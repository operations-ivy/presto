"""presto#8 (the room sensor on the LAN) and presto#23 (Weather's rooms and sky): the pure parts."""

import errno
import json
import types

import pytest
from neon import roomhttp, uplink, weatherfx, wx


def room(read_at=1000, temp_f=71.234, humidity=40.06, pressure=1012.34, lux=120.4, reads=None):
    return types.SimpleNamespace(
        read_at=read_at,
        temp_f=temp_f,
        humidity=humidity,
        pressure=pressure,
        lux=lux,
        reads=reads or {"ok": 5, "error": 1},
    )


def test_document_while_fresh_and_once_stale():
    assert roomhttp.document(room(), 2000) == {
        "up": True,
        "temperature_f": 71.2,
        "humidity": 40.1,
        "pressure_hpa": 1012.3,
        "lux": 120,
    }
    stale = roomhttp.document(room(), 1000 + roomhttp.FRESH_MS)
    assert stale == {"up": False, "temperature_f": None, "humidity": None, "pressure_hpa": None, "lux": None}
    assert roomhttp.document(room(read_at=None), 0)["up"] is False


def test_metrics_while_fresh():
    text = roomhttp.metrics(room(), 2000)
    lines = [ln for ln in text.splitlines() if not ln.startswith("#")]
    assert lines == [
        "presto_room_sensor_up 1",
        'presto_room_reads_total{result="error"} 1',
        'presto_room_reads_total{result="ok"} 5',
        "presto_room_temperature_fahrenheit 71.23",
        "presto_room_humidity_percent 40.1",
        "presto_room_pressure_hpa 1012.3",
        "presto_room_light_lux 120",
    ]
    assert "# TYPE presto_room_reads_total counter" in text and text.endswith("\n")


def test_metrics_drop_the_readings_when_stale_or_missing():
    lines = [ln for ln in roomhttp.metrics(room(read_at=None), 0).splitlines() if not ln.startswith("#")]
    assert lines[0] == "presto_room_sensor_up 0"
    assert not any("temperature" in ln for ln in lines)
    no_light = roomhttp.metrics(room(lux=None), 2000)
    assert "presto_room_light_lux" not in no_light and "presto_room_temperature_fahrenheit 71.23" in no_light


@pytest.mark.parametrize(
    "raw, want",
    [
        (b"GET /room.json?x=1 HTTP/1.1\r\nHost: presto\r\n\r\n", ("GET", "/room.json")),
        (b"GET /metr", None),
        (b"\xff\xfe\r\n\r\n", ("", "")),  # not text: a bad request, not a crash
    ],
)
def test_request_path(raw, want):
    assert roomhttp.request_path(raw) == want


@pytest.mark.parametrize(
    "method, path, status, ctype",
    [
        ("GET", "/room.json", "200 OK", "application/json"),
        ("GET", "/metrics", "200 OK", "text/plain; version=0.0.4"),
        ("HEAD", "/metrics", "200 OK", "text/plain; version=0.0.4"),
        ("GET", "/", "404 Not Found", "text/plain"),
        ("POST", "/metrics", "405 Method Not Allowed", "text/plain"),
        ("", "", "405 Method Not Allowed", "text/plain"),
    ],
)
def test_routes(method, path, status, ctype):
    assert roomhttp.respond(method, path, room(), 2000)[:2] == (status, ctype)


def test_head_has_no_body():
    head = roomhttp.http("200 OK", "text/plain", "abc", head=True)
    assert head.endswith(b"\r\n\r\n") and b"Content-Length: 3" in head


class FakeClient:
    def __init__(self, chunks):
        self.chunks = list(chunks)  # bytes, or None for "not yet"
        self.sent = b""
        self.closed = False

    def setblocking(self, flag):
        pass

    def settimeout(self, t):
        pass

    def recv(self, n):
        c = self.chunks.pop(0) if self.chunks else None
        if c is None:
            raise OSError(errno.EAGAIN)
        return c

    def sendall(self, b):
        self.sent += b

    def close(self):
        self.closed = True


class FakeListener:
    def __init__(self, pending):
        self.pending = list(pending)
        self.bound = None

    def setsockopt(self, *a):
        pass

    def bind(self, addr):
        self.bound = addr

    def listen(self, n):
        pass

    def setblocking(self, flag):
        pass

    def accept(self):
        if not self.pending:
            raise OSError(errno.EAGAIN)
        return self.pending.pop(0), ("192.168.1.10", 5000)

    def close(self):
        pass


def fake_sockets(listener):
    return types.SimpleNamespace(
        socket=lambda: listener,
        getaddrinfo=lambda host, port: [(0, 0, 0, "", (host, port))],
        SOL_SOCKET=1,
        SO_REUSEADDR=2,
    )


def test_server_waits_for_wifi_then_answers_a_request_in_pieces():
    client = FakeClient([b"GET /room.json HTTP/1.1\r\n", None, b"Host: presto\r\n\r\n"])
    listener = FakeListener([client])
    srv = roomhttp.Server(room(), sockets=fake_sockets(listener))
    srv.step(False, 1000)
    assert srv.listener is None  # no Wi-Fi: not listening yet
    for t in (1000, 1010, 1020, 1030):
        srv.step(True, t)
    assert listener.bound == ("0.0.0.0", 8767)
    head, body = client.sent.split(b"\r\n\r\n", 1)
    assert head.startswith(b"HTTP/1.0 200 OK") and json.loads(body)["temperature_f"] == 71.2
    assert client.closed and srv.clients == [] and srv.served == 1


def test_server_drops_a_trickling_client():
    client = FakeClient([b"G", b"E", b"T", b" "] * 10)
    srv = roomhttp.Server(room(), sockets=fake_sockets(FakeListener([client])))
    for t in range(0, roomhttp.READ_MS + 500, 250):
        srv.step(True, t)
    assert client.closed and client.sent == b"" and srv.clients == []


def test_server_answers_binary_garbage_without_raising():
    client = FakeClient([b"\xff\xfe\x00\r\n\r\n"])
    srv = roomhttp.Server(room(), sockets=fake_sockets(FakeListener([client])))
    srv.step(True, 0)
    assert client.sent.startswith(b"HTTP/1.0 405") and client.closed


def test_server_drops_a_silent_client():
    client = FakeClient([])
    srv = roomhttp.Server(room(), sockets=fake_sockets(FakeListener([client])))
    srv.step(True, 0)
    srv.step(True, roomhttp.READ_MS + 1)
    assert client.closed and client.sent == b"" and srv.clients == []


FEED = {"temps": [{"n": "My ecobee", "c": 22.2}, {"n": "Presto", "c": 21.0}]}


def test_indoor_while_brick1982_is_offline_is_the_ecobee_and_the_presto():
    # The feed lists only sensors Home Assistant has available: no Bedroom while brick1982 is off.
    rows = uplink.indoor(FEED, True, 70.5)
    assert [(n, round(t, 1)) for n, t in rows] == [("MY ECOBEE", 72.0), ("PRESTO", 70.5)]


def test_indoor_shows_the_bedroom_once_brick1982_is_back():
    feed = {"temps": [{"n": "Bedroom", "c": 24.6}, *FEED["temps"]]}
    assert [n for n, _ in uplink.indoor(feed, False, 21.4)] == ["BEDROOM", "MY ECOBEE", "PRESTO"]


def test_indoor_keeps_home_assistants_copy_while_the_presto_cant_read():
    assert uplink.indoor(FEED, False, None) == [("MY ECOBEE", 22.2), ("PRESTO", 21.0)]
    assert uplink.indoor(None, True, None) == []
    assert uplink.indoor(None, True, 70.0) == [("PRESTO", 70.0)]


def test_rooms_page_has_one_presto():
    lines = [t for t, _ in uplink.rooms(FEED, True, 70.5)]
    assert lines == ["> INDOOR", "  MY ECOBEE      72.0F", "  PRESTO         70.5F"]


def test_page_turns_when_rows_overflow():
    rows = list("abcdefghij")
    assert weatherfx.page(rows[:4], 4, 99999) == (rows[:4], 0, 1)
    assert weatherfx.page(rows, 4, 0) == (rows[:4], 0, 3)
    assert weatherfx.page(rows, 4, 6000) == (rows[4:8], 1, 3)
    assert weatherfx.page(rows, 4, 12000) == (rows[8:], 2, 3)
    assert weatherfx.page(rows, 4, 18000)[1] == 0


def test_every_weather_kind_has_a_scene():
    for _, kind in wx.KINDS:
        assert weatherfx.layers(kind) is weatherfx.SCENES[kind]
    assert weatherfx.layers("nonsense") is weatherfx.SCENES["unknown"]
    assert weatherfx.layers("storm")["bolts"] and weatherfx.layers("clear")["sun"]


class _Pens:
    def rgb(self, *rgb):
        return rgb

    def __getattr__(self, name):
        return name


def test_the_effect_pools_cover_the_busiest_scene():
    sky = weatherfx.Sky(_Pens())
    scenes = weatherfx.SCENES.values()
    assert len(sky.streaks) >= max(s["rain"] for s in scenes)
    assert len(sky.flakes) >= max(s["snow"] for s in scenes)
    assert len(sky.clouds) >= max(s["clouds"] for s in scenes)
    assert len(sky.banks) >= max(s["fog"] for s in scenes)
