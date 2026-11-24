"""The back LEDs' kill switch (leds.py) and its /leds endpoints (roomhttp.py)."""

import json
import types

from neon import leds, roomhttp


class FakePresto:
    def __init__(self):
        self.writes = []
        self.display = "display"

    def set_led_rgb(self, i, r, g, b):
        self.writes.append(("rgb", i, r, g, b))

    def set_led_hsv(self, i, h, s, v):
        self.writes.append(("hsv", i, h, s, v))


def gate(tmp_path):
    presto = FakePresto()
    g = leds.Gate(presto, str(tmp_path / "leds_off"))
    g.load()
    return g, presto


def test_off_blanks_them_and_drops_every_write_until_on(tmp_path):
    g, presto = gate(tmp_path)
    g.set_led_hsv(0, 0.5, 1.0, 1.0)
    assert g.switch(True)
    assert presto.writes[1:] == [("rgb", i, 0, 0, 0) for i in range(leds.COUNT)]
    presto.writes.clear()
    g.set_led_rgb(0, 255, 0, 0)
    g.set_led_hsv(1, 0.2, 1.0, 1.0)
    assert presto.writes == []
    assert not g.switch(True)  # already off
    assert g.toggle() is False  # on again
    g.set_led_rgb(0, 255, 0, 0)
    assert presto.writes == [("rgb", 0, 255, 0, 0)]


def test_off_survives_a_restart(tmp_path):
    g, _ = gate(tmp_path)
    g.switch(True)
    again = leds.Gate(FakePresto(), g.marker)
    assert not again.off  # until the card is read
    again.load()
    assert again.off
    g.switch(False)
    again.load()
    assert not again.off


def test_the_loop_hears_of_each_change_once(tmp_path):
    g, _ = gate(tmp_path)
    assert not g.take_changed()
    g.toggle()
    assert g.take_changed()
    assert not g.take_changed()


def test_everything_else_passes_through(tmp_path):
    g, _ = gate(tmp_path)
    assert g.display == "display"


ROOM = types.SimpleNamespace(read_at=None, temp_f=None, humidity=None, pressure=None, lux=None, reads={})


def test_leds_over_the_lan(tmp_path):
    g, _ = gate(tmp_path)
    status, _, body = roomhttp.respond("GET", "/leds", ROOM, 0, g)
    assert (status, json.loads(body)) == ("200 OK", {"on": True})
    assert json.loads(roomhttp.respond("POST", "/leds/off", ROOM, 0, g)[2]) == {"on": False}
    assert g.off and g.take_changed()
    assert json.loads(roomhttp.respond("POST", "/leds/toggle", ROOM, 0, g)[2]) == {"on": True}
    assert json.loads(roomhttp.respond("POST", "/leds/on", ROOM, 0, g)[2]) == {"on": True}
    assert "presto_leds_on 1" in roomhttp.respond("GET", "/metrics", ROOM, 0, g)[2]


def test_leds_need_post_to_switch_and_a_gate_to_answer(tmp_path):
    g, _ = gate(tmp_path)
    assert roomhttp.respond("GET", "/leds/off", ROOM, 0, g)[0] == "404 Not Found"
    assert not g.off
    assert roomhttp.respond("POST", "/room.json", ROOM, 0, g)[0] == "405 Method Not Allowed"
    assert roomhttp.respond("GET", "/leds", ROOM, 0)[0] == "404 Not Found"  # no gate: no switch


def test_a_marker_that_cant_be_written_still_switches_them_off(tmp_path):
    presto = FakePresto()
    g = leds.Gate(presto, str(tmp_path / "no-card" / "leds_off"))
    assert g.switch(True)
    assert g.off
    assert presto.writes == [("rgb", i, 0, 0, 0) for i in range(leds.COUNT)]
