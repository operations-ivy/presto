"""The Hue lights, through Home Assistant: their state, and the light app's commands.

The Presto calls Home Assistant itself, as its Presto user (HA_TOKEN in secrets.py,
from home-assistant's scripts/setup). Plain HTTP on the LAN to brick2000:8123, where
Home Assistant listens on the host's network: a ~50 ms request, not the ~3 s TLS
connect of the cluster's front door.
The lamp is a Bluetooth bulb Home Assistant drives through brick2000's radio, so a
command can take a second or so.

The Presto's back LEDs follow one of them (settings: leds_light): its colour, bright,
since they face away from the user; off with it. The pure parts (parse_state,
led_colour, bar, hexline, the scenes) are tested on the laptop; Hue does the I/O.
"""

import json
import time

POLL_MS = 3000
TIMEOUT_S = 4.0  # a service call waits for the bulb's Bluetooth write
POLL_TIMEOUT_S = 1.0  # a state read is Home Assistant's memory: it holds up the screen meanwhile

# The app's scene keys: light.turn_on data, like brick-status's lamp.yaml.
SCENES = (
    ("BRT", {"brightness_pct": 100, "color_temp_kelvin": 4000}),
    ("RLX", {"brightness_pct": 55, "color_temp_kelvin": 2700}),
    ("HAX", {"brightness_pct": 70, "rgb_color": [0, 255, 65]}),
    ("NITE", {"brightness_pct": 5, "color_temp_kelvin": 2000}),
)


def parse_state(entity, js):
    """Home Assistant's /api/states/<entity> -> {entity, name, on, bri 0..1, rgb, kelvin},
    or None when it's unavailable."""
    state = (js or {}).get("state")
    if state not in ("on", "off"):
        return None
    a = js.get("attributes") or {}
    rgb = a.get("rgb_color")
    on = state == "on"
    return {
        "entity": entity,
        "name": a.get("friendly_name") or entity.split(".", 1)[-1],
        "on": on,
        "bri": (a.get("brightness") or 0) / 255 if on else 0.0,
        "rgb": tuple(int(c) for c in rgb) if on and rgb and len(rgb) == 3 else None,
        "kelvin": a.get("color_temp_kelvin") if on else None,
        "mode": a.get("color_mode"),
    }


def led_colour(light, floor=0.6):
    """The lamp's colour for the Presto's LEDs, bright: they face away from the user, so
    the colour is pushed to full and its brightness runs floor..1 (a 5 % night light still
    shows). Off or unknown: dark."""
    if not light or not light["on"]:
        return (0, 0, 0)
    r, g, b = light["rgb"] or (255, 180, 107)  # no colour reported: warm white
    peak = max(r, g, b) or 1
    k = (floor + (1 - floor) * max(0.0, min(1.0, light["bri"]))) * 255 / peak
    return (min(255, int(r * k)), min(255, int(g * k)), min(255, int(b * k)))


def bar(frac, width=12):
    """[#######.....]"""
    n = int(round(max(0.0, min(1.0, frac)) * width))
    return "[" + "#" * n + "." * (width - n) + "]"


def hexline(data, n=8):
    """The first n bytes of what went over the wire, as hex: 7B 22 65 6E ..."""
    return " ".join("%02X" % b for b in bytes(data)[:n])


def parse_url(url):
    """'http://host:port' -> (host, port)."""
    rest = url.split("://", 1)[-1].rstrip("/")
    host, _, port = rest.partition(":")
    return host, int(port or 80)


class Hue:
    """The lights' state (polled while something wants it) and commands to them."""

    def __init__(self, url, token, entities):
        self.url = url or ""
        self.token = token or ""
        self.entities = [e for e in entities if e]
        self.lights = {}  # entity -> parse_state(), or None while unavailable
        self.next_at = None  # poll at the first step
        self.addr = None  # resolved once: the router's DNS is slow
        self.fails = 0
        self.last = None  # (ok, status line, ms, the request's first bytes) of the last call

    @property
    def configured(self):
        return bool(self.url and self.token and self.entities)

    def step(self, net, now_ms):
        """Call once per pass while the app or the LEDs want the state."""
        if not self.configured or not net.up:
            return
        if self.next_at is None or time.ticks_diff(now_ms, self.next_at) >= 0:
            self.next_at = time.ticks_add(now_ms, POLL_MS * (1 if self.fails < 3 else 10))
            self.poll()

    def led_rgb(self, entity, floor=0.6):
        """led_colour() of entity, or None while its state isn't known."""
        light = self.lights.get(entity)
        return None if light is None else led_colour(light, floor)

    def poll(self):
        for e in self.entities:
            ok, _, body = self._request("GET", "/api/states/" + e, timeout=POLL_TIMEOUT_S)
            if not ok:
                self.lights[e] = None
                continue
            try:
                self.lights[e] = parse_state(e, json.loads(body))
            except ValueError:
                self.lights[e] = None

    def call(self, service, entity, data=None):
        """light.<service> on entity; True if Home Assistant took it. The new state
        arrives with the next poll (forced now)."""
        payload = {"entity_id": entity}
        payload.update(data or {})
        body = json.dumps(payload).encode()
        t0 = time.ticks_ms()
        ok, status, _ = self._request("POST", "/api/services/light/" + service, body)
        self.last = (ok, status, time.ticks_diff(time.ticks_ms(), t0), body)
        self.next_at = None  # see what it did
        return ok

    def _request(self, method, path, body=b"", timeout=TIMEOUT_S):
        """(ok, status: "200 OK", "401 Unauthorized", "NO ROUTE"..., body). Never raises."""
        import socket

        s = None
        try:
            host, port = parse_url(self.url)
            if self.addr is None:
                self.addr = socket.getaddrinfo(host, port)[0][-1]
            s = socket.socket()
            s.settimeout(timeout)
            s.connect(self.addr)
            head = f"{method} {path} HTTP/1.0\r\nHost: {host}\r\nAuthorization: Bearer {self.token}\r\n"
            if body:
                head += "Content-Type: application/json\r\nContent-Length: %d\r\n" % len(body)
            s.send(head.encode() + b"\r\n" + body)
            buf = bytearray()  # grown in place: no new bytes per chunk on the device's heap
            while len(buf) < 16384:
                chunk = s.recv(1024)
                if not chunk:
                    break
                buf.extend(chunk)
            top, _, rest = bytes(buf).partition(b"\r\n\r\n")
            status = top.split(b"\r\n", 1)[0].decode().split(" ", 1)[-1]  # without HTTP/1.x
            ok = status.split(" ", 1)[0] == "200"
            self.fails = 0 if ok else self.fails + 1
            return ok, status, rest
        except Exception as e:  # deliberately wide: OSError, gaierror, a bad port's ValueError...
            self.fails += 1
            if self.fails >= 3:
                self.addr = None  # it may have moved
            if self.fails == 3:
                print("[neon] hue:", repr(e))
            return False, "NO ROUTE", b""
        finally:
            if s is not None:
                s.close()
