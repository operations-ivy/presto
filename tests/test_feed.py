"""presto-feed: building the Brick screen's JSON from Prometheus answers (no network)."""

import json

from presto_photos import feed


def answers(**over):
    r = {k: [] for k in feed.QUERIES}
    r.update(
        ready=[({"node": "brick2000"}, 1), ({"node": "brick420"}, 1), ({"node": "brick666"}, 0)],
        host_up=[({"instance": "brick9000"}, 1)],
        uname=[
            ({"instance": "10.0.0.1:9100", "nodename": "brick2000"}, 1),
            ({"instance": "brick9000", "nodename": "brick9000"}, 1),
        ],
        cpu=[({"instance": "10.0.0.1:9100"}, 12.4), ({"instance": "brick9000"}, 29.6)],
        mem=[({"instance": "10.0.0.1:9100"}, 45.0)],
        temp=[({"instance": "brick9000"}, 66.2)],
        apps=[
            ({"name": "presto", "sync_status": "Synced", "health_status": "Healthy"}, 1),
            ({"name": "chucks-wisdom", "sync_status": "OutOfSync", "health_status": "Healthy"}, 1),
        ],
        door_up=[({"job": "front-door"}, 1)],
        temps=[
            (
                {"entity": "sensor.my_ecobee_current_temperature", "friendly_name": "My ecobee Current Temperature"},
                22.19,
            ),
            ({"entity": "sensor.bedroom_temperature", "friendly_name": "Bedroom temperature"}, 24.55),
        ],
    )
    r.update(over)
    return r


def test_nodes_in_number_order_with_vitals():
    out = feed.build(answers(), 100)
    assert [n["n"] for n in out["nodes"]] == ["brick420", "brick666", "brick2000", "brick9000"]
    b2000 = out["nodes"][2]
    assert b2000 == {"n": "brick2000", "ok": 1, "k8s": 1, "cpu": 12, "mem": 45}
    assert out["nodes"][1]["ok"] == 0  # NotReady
    assert out["nodes"][3] == {"n": "brick9000", "ok": 1, "k8s": 0, "cpu": 30, "temp": 66}


def test_apps_list_only_the_unhappy_ones():
    out = feed.build(answers(), 100)
    assert out["apps"] == {"n": 2, "bad": [["chucks-wisdom", "OutOfSync", "Healthy"]], "nbad": 1}


def test_pod_problems_are_capped_but_counted():
    stuck = [({"namespace": "x", "pod": f"p{i}", "reason": "CrashLoopBackOff"}, 1) for i in range(6)]
    out = feed.build(answers(stuck=stuck, unavailable=[({"namespace": "x", "deployment": "web"}, 1)]), 100)
    assert out["pods"]["nbad"] == 7
    assert out["pods"]["bad"][0] == ["x/web", "1 NOT READY"]
    assert len(out["pods"]["bad"]) == feed.MAX_ROWS


def test_indoor_temperatures_short_names_sorted():
    out = feed.build(answers(), 100)
    assert out["temps"] == [{"n": "Bedroom", "c": 24.6}, {"n": "My ecobee", "c": 22.2}]


def test_offline_or_outdoor_sensors_are_not_shown():
    # offline ones never arrive (the query keeps only hass_entity_available == 1); outdoor ones are dropped
    out = feed.build(answers(temps=[({"friendly_name": "Outdoor temperature"}, 9.0)]), 100)
    assert out["temps"] == []


def test_front_door_and_its_errors():
    out = feed.build(answers(door_up=[({}, 0)], door_5xx=[({}, 3.4)]), 100)
    assert out["door"] == {"ok": 0, "e5xx": 3, "rpm": 0}


def test_the_whole_feed_stays_small():
    stuck = [({"namespace": "namespace", "pod": "a-long-pod-name-abcde", "reason": "CrashLoopBackOff"}, 1)] * 10
    body = json.dumps(feed.build(answers(stuck=stuck), 100), separators=(",", ":"))
    assert len(body) < 1500  # the Presto parses it in a few ms


class FailingProm:
    def query(self, q):
        raise OSError("connection refused")


def test_a_failed_refresh_keeps_the_last_answer():
    f = feed.Feed(FailingProm())
    f.snapshot = {"at": 5}
    f.refresh()
    assert f.snapshot == {"at": 5}
    body = json.loads(f.body())
    assert body["at"] == 5 and body["age"] > 0


def test_jenkins_the_internet_and_the_scrapes():
    out = feed.build(
        answers(
            jobs=[({"jenkins_job": "build-image"}, 0), ({"jenkins_job": "test-all"}, 2), ({"jenkins_job": "x"}, 3)],
            job_start=[({"jenkins_job": "build-image"}, 90_000), ({"jenkins_job": "test-all"}, 40_000)],
            wan=[({"entity": "binary_sensor.router_wan_status"}, 1)],
            rates=[
                ({"entity": "sensor.router_download_speed"}, 812.4),
                ({"entity": "sensor.router_upload_speed"}, 40.2),
            ],
            targets=[({}, 56)],
            targets_down=[({"job": "lcars"}, 0)],
            running=[({}, 54)],
            restarts=[({}, 2.0)],
        ),
        100,
    )
    assert out["jobs"] == {"n": 3, "bad": [["test-all", "FAILED"]], "nbad": 1, "last": ["build-image", 10]}
    assert out["net"] == {"ok": 1, "dn": 812, "up": 40}
    assert out["scrape"] == {"n": 56, "down": ["lcars"], "ndown": 1}
    assert (out["pods"]["run"], out["pods"]["rs"]) == (54, 2)
    assert feed.build(answers(), 100)["net"] is None  # no router data: no NET line


def test_a_node_in_maintenance_and_what_runs_on_it_are_calm():
    out = feed.build(answers(
        maint=[({"node": "brick666"}, 100 + 3600), ({"node": "brick1982"}, float("inf"))],
        pod_nodes=[({"namespace": "chuck", "pod": "reader-5d9-abc", "node": "brick666"}, 1),
                   ({"namespace": "x", "pod": "p0", "node": "brick666"}, 1)],
        unavailable=[({"namespace": "chuck", "deployment": "reader"}, 1), ({"namespace": "wigle", "deployment": "console"}, 1)],
        stuck=[({"namespace": "x", "pod": "p0", "reason": "CrashLoopBackOff"}, 1)],
        uname=[({"instance": "brick1982", "nodename": "brick1982"}, 1)],
        targets_down=[({"job": "brick1982-node", "instance": "brick1982"}, 0), ({"job": "lcars", "instance": "brick1982:8767"}, 0),
                      ({"job": "overwatch", "instance": "10.0.0.9:80"}, 0)],
    ), 100)
    nodes = {n["n"]: n for n in out["nodes"]}
    assert nodes["brick666"]["maint"] == 3600 and nodes["brick1982"]["maint"] == -1  # off, but listed
    assert out["pods"]["bad"] == [["wigle/console", "1 NOT READY"]]  # reader and p0 are on brick666
    assert out["scrape"]["down"] == ["overwatch"]
