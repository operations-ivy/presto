"""The device's pager (neon/pager.py, presto#52) and the plus's off-arm screen, on CPython."""

import sys
import time
import types

import pytest
from neon import nav, pager


class Clock:
    now = 0


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """MicroPython's ticks_*, on a clock the test moves."""
    c = Clock()
    monkeypatch.setattr(time, "ticks_ms", lambda: c.now, raising=False)
    monkeypatch.setattr(time, "ticks_add", lambda a, b: a + b, raising=False)
    monkeypatch.setattr(time, "ticks_diff", lambda a, b: a - b, raising=False)
    return c


class Net:
    up = True


class Reply:
    def __init__(self, status, body=None):
        self.status_code, self.body = status, body

    def json(self):
        return self.body

    def close(self):
        pass


@pytest.fixture
def requests(monkeypatch):
    """A stand-in for MicroPython's requests: answers queued by the test, calls recorded."""
    fake = types.SimpleNamespace(answers=[], calls=[])

    def call(method):
        def go(url, **kw):
            fake.calls.append((method, url, kw))
            answer = fake.answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        return go

    fake.get, fake.post = call("GET"), call("POST")
    monkeypatch.setitem(sys.modules, "requests", fake)
    return fake


FIRING = {
    "at": 1,
    "n": 2,
    "worst": "critical",
    "alerts": [
        {"id": "a", "name": "NodeDown", "sev": "critical", "for": 60, "sum": "brick666 is down"},
        {"id": "b", "name": "DiskFilling", "sev": "warning", "for": 600, "sum": ""},
    ],
}


def test_the_badge_counts_in_the_worst_colour():
    assert pager.badge(FIRING, stale=False) == ("2", "critical")
    assert pager.badge({"n": 0, "alerts": []}, stale=False) is None
    assert pager.badge(None, stale=False) is None
    assert pager.badge(FIRING, stale=True) == ("?", None)
    assert pager.badge({"n": 150, "worst": "info"}, stale=False) == ("99+", "info")


def test_ago_and_wrap():
    assert [pager.ago(s) for s in (5, 125, 7300, 400000)] == ["5S", "2M", "2H", "4D"]
    assert pager.wrap("brick666 has been down for a while now", 12) == [
        "brick666 has",
        "been down",
        "for a while",
        "now",
    ]
    assert pager.wrap("x" * 20, 8) == ["xxxxxxxx"]


def test_it_asks_only_when_due_and_nobody_is_touching(requests, clock):
    p = pager.Pager("https://presto.example/alerts", 120)
    requests.answers = [Reply(200, FIRING)]
    clock.now = pager.FIRST_MS
    assert not p.step(Net, last_touch_ms=clock.now - 1000, now_ms=clock.now)  # just touched
    assert p.step(Net, last_touch_ms=0, now_ms=clock.now)
    assert p.data == FIRING and requests.calls[0][1] == "https://presto.example/alerts"
    assert not p.step(Net, last_touch_ms=0, now_ms=clock.now + 1000)  # not due again yet


def test_off_without_a_url(requests, clock):
    p = pager.Pager("", 120)
    clock.now = 10**7
    assert not p.step(Net, 0, clock.now) and not p.stale(clock.now) and requests.calls == []


def test_a_failed_fetch_keeps_the_last_answer_until_its_stale(requests, clock):
    p = pager.Pager("https://presto.example/alerts", 120)
    requests.answers = [Reply(200, FIRING), OSError("no route"), Reply(502)]
    clock.now = pager.FIRST_MS
    p.step(Net, 0, clock.now)
    clock.now += 120_000
    assert not p.step(Net, 0, clock.now) and p.data == FIRING and not p.stale(clock.now)
    clock.now += pager.STALE_S * 1000
    assert not p.step(Net, 0, clock.now) and p.stale(clock.now)


def test_a_critical_alert_opens_alerts_once(requests):
    p = pager.Pager("https://presto.example/alerts", 120)
    p.data = FIRING
    assert p.take_critical()
    assert not p.take_critical()  # still the same one
    p.data = {"n": 0, "alerts": []}
    assert not p.take_critical()
    p.data = FIRING  # it came back
    assert p.take_critical()


def test_silence_posts_the_id_and_drops_it_from_the_list(requests):
    p = pager.Pager("https://presto.example/alerts", 120)
    p.data = dict(FIRING)
    requests.answers = [Reply(200, {"id": "a"})]
    assert p.silence("a")
    method, url, kw = requests.calls[0]
    assert (method, url, kw["data"]) == ("POST", "https://presto.example/silence", '{"id": "a"}')
    assert [a["id"] for a in p.data["alerts"]] == ["b"] and p.data["n"] == 1 and p.data["worst"] == "warning"


def test_a_failed_silence_changes_nothing(requests):
    p = pager.Pager("https://presto.example/alerts", 120)
    p.data = dict(FIRING)
    requests.answers = [Reply(404)]
    assert not p.silence("a") and p.data["n"] == 2


def test_an_extra_screen_is_reached_by_go_and_left_by_the_bottom_edge():
    plus = nav.Plus({"up": "weather", "left": "photos", "right": "brick", "down": "lights"}, extras=("alerts",))
    assert plus.go("alerts") == "alerts" and plus.home_tab() == nav.EXTRA_HOME
    assert plus.tap(120, 120) is None  # the screen's own tap
    assert plus.tap(120, 230) == nav.HOME
    with pytest.raises(ValueError):
        plus.go("nowhere")


def test_extras_dont_change_the_arms():
    plus = nav.Plus({"up": "weather", "left": "photos", "right": "brick", "down": "lights"}, extras=("alerts",))
    plus.go("weather")
    assert plus.home_tab() == "down" and plus.swipe("down") == nav.HOME


def test_a_silenced_alert_leaves_the_count_only_if_it_was_listed():
    data = dict(FIRING, n=10)  # ten firing, two listed
    after = pager.drop(data, "a")
    assert [a["id"] for a in after["alerts"]] == ["b"] and after["n"] == 9 and after["worst"] == "warning"
    assert pager.drop(data, "not-listed")["n"] == 10
    assert pager.drop(dict(FIRING, n=1, alerts=FIRING["alerts"][:1]), "a")["worst"] is None
    assert pager.drop(None, "a") is None
