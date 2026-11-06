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
POLL_TIMEOUT_S = 2.0  # a state read is Home Assistant's memory (stepped: it doesn't hold up the screen)

# The app's scene keys: light.turn_on data, like brick-status's lamp.yaml, and each
# key's colour on the screen.
SCENES = (
    ("BRT", {"brightness_pct": 100, "color_temp_kelvin": 4000}, (255, 228, 190)),
    ("RLX", {"brightness_pct": 55, "color_temp_kelvin": 2700}, (255, 150, 60)),
    ("HAX", {"brightness_pct": 70, "rgb_color": [0, 255, 65]}, (0, 255, 65)),
    ("NITE", {"brightness_pct": 5, "color_temp_kelvin": 2000}, (255, 80, 0)),
)


def scene_on(light, data):
    """Whether the light is in the scene now: on, at its level (within 3 points; Home
    Assistant reports 0..255), and its colour temperature (within 150 K) or colour
    (each channel within 40: the bulb's xy comes back as a nearby rgb)."""
    if not light or not light["on"]:
        return False
    if abs(light["bri"] * 100 - data.get("brightness_pct", 100)) > 3:
        return False
    if "color_temp_kelvin" in data:
        return light["kelvin"] is not None and abs(light["kelvin"] - data["color_temp_kelvin"]) <= 150
    if "rgb_color" in data:
        return light["rgb"] is not None and all(abs(a - b) <= 40 for a, b in zip(light["rgb"], data["rgb_color"]))
    return True


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


# errno values that mean "not yet" on a non-blocking socket: EAGAIN, EINPROGRESS,
# EALREADY, ENOTCONN (lwIP's and MicroPython's numbers)
WAIT = (11, 114, 115, 107, 128)
MAX_BODY = 16384


def _ticks_since(now_ms, then_ms):
    return time.ticks_diff(now_ms, then_ms) if hasattr(time, "ticks_diff") else now_ms - then_ms


def _ticks_add(t_ms, ms):
    return time.ticks_add(t_ms, ms) if hasattr(time, "ticks_add") else t_ms + ms


class Exchange:
    """One HTTP/1.0 request on a non-blocking socket, a step at a time, so the screen
    and the touch keep going while Home Assistant answers (the user, 2026-11-06: the
    blocking calls made the Lights app fiddly). step() does at most one send or recv."""

    def __init__(self, sock, addr, payload, now_ms, timeout_ms):
        self.sock = sock
        self.out = payload
        self.buf = bytearray()
        self.started = now_ms
        self.timeout_ms = timeout_ms
        sock.setblocking(False)
        try:
            sock.connect(addr)
        except OSError as e:
            if e.args[0] not in WAIT:
                raise

    def step(self, now_ms):
        """(ok, status, body) once it's done, else None. Never raises."""
        if _ticks_since(now_ms, self.started) > self.timeout_ms:
            return False, "TIMEOUT", b""
        try:
            if self.out:
                n = self.sock.send(self.out)
                if n:
                    self.out = self.out[n:]
                return None
            chunk = self.sock.recv(1024)
        except OSError as e:
            if e.args[0] in WAIT:
                return None
            return False, "NO ROUTE", b""
        if chunk is None:  # MicroPython: nothing yet
            return None
        if chunk and len(self.buf) < MAX_BODY:
            self.buf.extend(chunk)
            return None
        top, _, rest = bytes(self.buf).partition(b"\r\n\r\n")
        status = top.split(b"\r\n", 1)[0].decode().split(" ", 1)[-1] or "NO ANSWER"  # without HTTP/1.x
        return status.split(" ", 1)[0] == "200", status, rest


class Hue:
    """The lights' state (polled while something wants it) and commands to them, one
    request at a time, each stepped between frames (Exchange); commands go first."""

    def __init__(self, url, token, entities, sockets=None):
        self.url = url or ""
        self.token = token or ""
        self.entities = [e for e in entities if e]
        self.lights = {}  # entity -> parse_state(), or None while unavailable
        self.next_at = None  # poll at the first step
        self.addr = None  # resolved once: the router's DNS is slow
        self.fails = 0
        self.calls = []  # (service, entity, body, label) waiting their turn
        self.to_poll = []  # entities left in this poll
        self.job = None  # what the exchange is: ("poll", entity) or ("call", ..., started)
        self.exchange = None
        self.sockets = sockets  # a socket factory, for the tests; None: the socket module
        self.last = None  # (ok, status, ms, body sent, label) of the last call
        self.seq = 0  # bumped as each call finishes

    @property
    def configured(self):
        return bool(self.url and self.token and self.entities)

    @property
    def busy(self):
        """A command waiting or on the wire."""
        return bool(self.calls) or (self.job is not None and self.job[0] == "call")

    def led_rgb(self, entity, floor=0.6):
        """led_colour() of entity, or None while its state isn't known."""
        light = self.lights.get(entity)
        return None if light is None else led_colour(light, floor)

    def send(self, service, entity, data=None, label=None):
        """Queue light.<service> on entity; its result lands in last (seq goes up)."""
        payload = {"entity_id": entity}
        payload.update(data or {})
        self.calls.append((service, entity, json.dumps(payload).encode(), label or "light." + service))

    def step(self, net, now_ms):
        """Call every pass while the app or the LEDs want the state: one small step."""
        if not self.configured or not net.up:
            return
        if self.exchange is not None:
            result = self.exchange.step(now_ms)
            if result is None:
                return
            self._finish(result, now_ms)
        if self.calls:
            service, entity, body, label = self.calls.pop(0)
            self._start(
                ("call", service, entity, body, label, now_ms),
                "POST",
                "/api/services/light/" + service,
                body,
                now_ms,
                TIMEOUT_S,
            )
        elif self.to_poll:
            e = self.to_poll.pop(0)
            self._start(("poll", e), "GET", "/api/states/" + e, b"", now_ms, POLL_TIMEOUT_S)
        elif self.next_at is None or _ticks_since(now_ms, self.next_at) >= 0:
            self.next_at = _ticks_add(now_ms, POLL_MS * (1 if self.fails < 3 else 10))
            self.to_poll = list(self.entities)

    def _start(self, job, method, path, body, now_ms, timeout_s):
        self.job = job
        host, port = parse_url(self.url)
        head = "{} {} HTTP/1.0\r\nHost: {}\r\nAuthorization: Bearer {}\r\n".format(method, path, host, self.token)
        if body:
            head += "Content-Type: application/json\r\nContent-Length: %d\r\n" % len(body)
        try:
            if self.sockets is None:
                import socket

                self.sockets = socket
            if self.addr is None:
                self.addr = self.sockets.getaddrinfo(host, port)[0][-1]
            self.exchange = Exchange(
                self.sockets.socket(), self.addr, head.encode() + b"\r\n" + body, now_ms, int(timeout_s * 1000)
            )
        except Exception as e:  # deliberately wide: OSError, gaierror, a bad port's ValueError...
            self.exchange = None
            self._finish((False, "NO ROUTE", b""), now_ms, e)

    def _finish(self, result, now_ms, error=None):
        ok, status, body = result
        if self.exchange is not None:
            try:
                self.exchange.sock.close()
            except OSError:
                pass
            self.exchange = None
        self.fails = 0 if ok else self.fails + 1
        if self.fails >= 3:
            self.addr = None  # it may have moved
        if self.fails == 3:
            print("[neon] hue:", status, repr(error) if error else "")
        job, self.job = self.job, None
        if job[0] == "poll":
            try:
                self.lights[job[1]] = parse_state(job[1], json.loads(body)) if ok else None
            except ValueError:
                self.lights[job[1]] = None
            return
        _, service, entity, sent, label, started = job
        ms = _ticks_since(now_ms, started)
        self.last = (ok, status, ms, sent, label)
        self.seq += 1
        print("[neon] hue: %s %s -> %s, %d ms" % (label, entity, status, ms))
        self.next_at = None  # see what it did
