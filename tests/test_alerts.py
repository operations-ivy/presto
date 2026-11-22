"""presto-alerts (presto#52): trimming Alertmanager's alerts for the Presto, and
the silence, against a stub Alertmanager."""

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from presto_photos import alerts

NOW = 1_795_000_000.0


def alert(name, sev="warning", fp=None, started=NOW - 600, summary="", **labels):
    return {
        "fingerprint": fp or f"fp-{name}",
        "labels": {"alertname": name, "severity": sev, **labels},
        "annotations": {"summary": summary} if summary else {},
        "startsAt": alerts._iso(started),
        "status": {"state": "active"},
    }


def test_worst_first_then_oldest_without_the_always_firing_ones():
    out = alerts.trim(
        [
            alert("Watchdog", "none"),
            alert("InfoInhibitor", "none"),
            alert("DiskFilling", "warning", started=NOW - 60),
            alert("NodeDown", "critical"),
            alert("OldWarning", "warning", started=NOW - 7200),
        ],
        NOW,
    )
    assert [a["name"] for a in out["alerts"]] == ["NodeDown", "OldWarning", "DiskFilling"]
    assert (out["n"], out["worst"], out["at"]) == (3, "critical", int(NOW))
    assert out["alerts"][1]["for"] == 7200


def test_nothing_firing_is_all_clear():
    assert alerts.trim([alert("Watchdog", "none")], NOW) == {"at": int(NOW), "n": 0, "worst": None, "alerts": []}


def test_summaries_are_one_short_line_and_odd_severities_are_info():
    long = "Disk  is\nfilling " + "x" * 400
    [row] = alerts.trim([alert("A", "page-me", summary=long)], NOW)["alerts"]
    assert row["sev"] == "info" and row["sum"].startswith("Disk is filling x") and len(row["sum"]) == 160


def test_at_most_eight_are_listed_but_all_are_counted():
    out = alerts.trim([alert(f"A{i}") for i in range(12)], NOW)
    assert out["n"] == 12 and len(out["alerts"]) == alerts.MAX_ALERTS


@pytest.mark.parametrize(
    "raw, expected",
    [(b'{"id": "fp-1"}', "fp-1"), (b"not json", None), (b'{"id": 3}', None), (b"[1]", None), (b"{}", None)],
)
def test_the_silence_body_is_just_an_id(raw, expected):
    assert alerts.parse_silence_id(raw) == expected


def test_a_silence_matches_exactly_that_alert_for_an_hour():
    body = alerts.silence_for(alert("NodeDown", "critical", node="brick666"), NOW)
    assert {m["name"]: m["value"] for m in body["matchers"]} == {
        "alertname": "NodeDown",
        "severity": "critical",
        "node": "brick666",
    }
    assert all(m["isEqual"] and not m["isRegex"] for m in body["matchers"])
    assert alerts._time(body["endsAt"]) - alerts._time(body["startsAt"]) == 3600
    assert body["createdBy"] == "presto"


class StubAlertmanager(BaseHTTPRequestHandler):
    firing: list = []
    silences: list = []

    def log_message(self, *args):
        pass

    def reply(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        assert self.path.startswith("/api/v2/alerts?active=true&silenced=false&inhibited=false")
        self.reply(self.firing)

    def do_POST(self):
        assert self.path == "/api/v2/silences"
        self.silences.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.reply({"silenceID": "s-1"})


def _serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


@pytest.fixture
def relay():
    StubAlertmanager.firing = [alert("NodeDown", "critical", node="brick666"), alert("Watchdog", "none")]
    StubAlertmanager.silences = []
    upstream, am_url = _serve(StubAlertmanager)
    server, url = _serve(alerts.make_handler(alerts.Alertmanager(am_url)))
    yield url
    server.shutdown()
    upstream.shutdown()


def call(url, body=None):
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_the_relay_lists_whats_firing(relay):
    status, body = call(f"{relay}/alerts")
    assert status == 200 and [a["name"] for a in body["alerts"]] == ["NodeDown"]


def test_silence_silences_one_firing_alert(relay):
    status, body = call(f"{relay}/silence", {"id": "fp-NodeDown"})
    assert status == 200 and body["silence"] == "s-1"
    [made] = StubAlertmanager.silences
    assert {"name": "node", "value": "brick666", "isRegex": False, "isEqual": True} in made["matchers"]


def test_silence_refuses_what_isnt_firing_or_isnt_asked_properly(relay):
    assert call(f"{relay}/silence", {"id": "fp-Unknown"})[0] == 404
    assert call(f"{relay}/silence", b"not json")[0] == 400
    assert call(f"{relay}/silence", {"id": 3})[0] == 400
    assert call(f"{relay}/silence", b"x" * 2000)[0] == 400
    assert StubAlertmanager.silences == []


def test_alertmanager_down_is_a_502():
    server, url = _serve(alerts.make_handler(alerts.Alertmanager("http://127.0.0.1:9", timeout=1)))
    try:
        assert call(f"{url}/alerts")[0] == 502
    finally:
        server.shutdown()
