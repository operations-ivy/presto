"""The Presto's room sensor on the LAN (presto#8), like brick1982's panel:

    GET /room.json   {"up", "temperature_f", "humidity", "pressure_hpa", "lux"}  (Home Assistant)
    GET /metrics     presto_room_* in Prometheus' text format                   (Prometheus)

on port 8767. A reading older than FRESH_MS counts as offline: "up" false, nulls in the
JSON, and only presto_room_sensor_up 0 in the metrics.

The server never blocks the screen: a non-blocking listening socket, stepped from the
main loop between frames like the Lights app's requests (hue.py). Each step takes at most
one new connection and reads what has arrived; a whole request gets its answer (a few
hundred bytes) and is closed. A client that hasn't sent its request in READ_MS is dropped.

document(), metrics() and respond() are pure (tested on the laptop); Server does the I/O.
"""

import json
import time

from neon.hue import WAIT

PORT = 8767
FRESH_MS = 300000  # 5 minutes, as on brick1982
READ_MS = 2000
MAX_CLIENTS = 3
MAX_REQUEST = 2048

try:  # MicroPython's wrapping ticks; plain arithmetic for the laptop's tests
    _ticks_since = time.ticks_diff
except AttributeError:

    def _ticks_since(a, b):
        return a - b


def fresh(room, now_ms):
    return room.read_at is not None and _ticks_since(now_ms, room.read_at) < FRESH_MS


def _round(v, places):
    """None stays None; 0 places is a whole number (120, not 120.0)."""
    if v is None:
        return None
    return round(v, places) if places else int(round(v))


def document(room, now_ms):
    """/room.json: the latest reading, or nulls and "up": false while the sensor is offline."""
    up = fresh(room, now_ms)
    return {
        "up": up,
        "temperature_f": _round(room.temp_f, 1) if up else None,
        "humidity": _round(room.humidity, 1) if up else None,
        "pressure_hpa": _round(room.pressure, 1) if up else None,
        "lux": _round(room.lux, 0) if up else None,
    }


# (name, type, help, the reading's attribute and places; None: written by hand)
METRICS = (
    ("presto_room_sensor_up", "gauge", "1 while the BME280 has read in the last 5 minutes.", None),
    ("presto_room_reads_total", "counter", "BME280 reads, by result.", None),
    ("presto_room_temperature_fahrenheit", "gauge", "The room's temperature, offset applied.", ("temp_f", 2)),
    ("presto_room_humidity_percent", "gauge", "The room's relative humidity.", ("humidity", 1)),
    ("presto_room_pressure_hpa", "gauge", "Air pressure.", ("pressure", 1)),
    ("presto_room_light_lux", "gauge", "Light at the screen (LTR-559).", ("lux", 0)),
)


def metrics(room, now_ms):
    """/metrics: the readings only while fresh, so a dead sensor's last value doesn't linger."""
    up = fresh(room, now_ms)
    out = []
    for name, kind, help_, field in METRICS:
        if name == "presto_room_sensor_up":
            values = [("", 1 if up else 0)]
        elif name == "presto_room_reads_total":
            values = [('{result="%s"}' % k, room.reads[k]) for k in sorted(room.reads)]
        else:
            v = getattr(room, field[0])
            if not up or v is None:
                continue
            values = [("", _round(v, field[1]))]
        out.append(f"# HELP {name} {help_}")
        out.append(f"# TYPE {name} {kind}")
        out.extend(f"{name}{labels} {v}" for labels, v in values)
    return "\n".join(out) + "\n"


def request_path(raw):
    """b"GET /room.json?x HTTP/1.1..." -> ("GET", "/room.json"), or None until the request line is in."""
    end = raw.find(b"\r\n")
    if end < 0:
        return None
    try:
        parts = raw[:end].decode().split(" ")
    except ValueError:  # not text (UnicodeError): a bad request, never a crash in the main loop
        return ("", "")
    if len(parts) < 2:
        return ("", "")
    return (parts[0], parts[1].split("?")[0])


def respond(method, path, room, now_ms):
    """(status line, content type, body) for one request."""
    if method not in ("GET", "HEAD"):
        return "405 Method Not Allowed", "text/plain", "GET only\n"
    if path == "/room.json":
        return "200 OK", "application/json", json.dumps(document(room, now_ms))
    if path == "/metrics":
        return "200 OK", "text/plain; version=0.0.4", metrics(room, now_ms)
    return "404 Not Found", "text/plain", "/room.json or /metrics\n"


def http(status, content_type, body, head=False):
    b = body.encode()
    hdr = "HTTP/1.0 %s\r\nContent-Type: %s\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % (
        status,
        content_type,
        len(b),
    )
    return hdr.encode() + (b"" if head else b)


def _errno(e):
    return e.args[0] if e.args else None


class Server:
    def __init__(self, room, port=PORT, sockets=None):
        if sockets is None:
            import socket as sockets
        self.sockets = sockets  # the module, or the tests' fakes
        self.room, self.port = room, port
        self.listener = None
        self.clients = []  # [socket, bytes so far, ticks_ms accepted]
        self.served = 0

    def _listen(self):
        s = self.sockets.socket()
        try:
            s.setsockopt(self.sockets.SOL_SOCKET, self.sockets.SO_REUSEADDR, 1)
            s.bind(self.sockets.getaddrinfo("0.0.0.0", self.port)[0][-1])
            s.listen(2)
            s.setblocking(False)
        except Exception:
            s.close()
            raise
        self.listener = s
        print("[neon] room sensor on :%d (/room.json, /metrics)" % self.port)

    def step(self, net_up, now_ms):
        """Between frames: listen once the Wi-Fi is up, take one connection, answer whole requests."""
        if self.listener is None:
            if not net_up:
                return
            try:
                self._listen()
            except Exception as e:
                print("[neon] room server:", repr(e))
                return
        if len(self.clients) < MAX_CLIENTS:
            try:
                c, _ = self.listener.accept()
                c.setblocking(False)
                self.clients.append([c, b"", now_ms])
            except OSError as e:
                if _errno(e) not in WAIT:
                    print("[neon] room accept:", repr(e))
        for cl in list(self.clients):
            self._serve(cl, now_ms)

    def _drop(self, cl):
        self.clients.remove(cl)
        try:
            cl[0].close()
        except OSError:
            pass

    def _serve(self, cl, now_ms):
        c = cl[0]
        if _ticks_since(now_ms, cl[2]) > READ_MS:  # silent or trickling: the request is late either way
            self._drop(cl)
            return
        try:
            chunk = c.recv(512)
        except OSError as e:
            if _errno(e) not in WAIT:
                self._drop(cl)
            return
        if not chunk:  # closed before asking
            self._drop(cl)
            return
        cl[1] += chunk
        if b"\r\n\r\n" not in cl[1] and len(cl[1]) < MAX_REQUEST:
            return
        method, path = request_path(cl[1]) or ("", "")
        status, ctype, body = respond(method, path, self.room, now_ms)
        try:
            c.setblocking(True)
            c.settimeout(1)  # a few hundred bytes into an empty send buffer: immediate
            c.sendall(http(status, ctype, body, method == "HEAD"))
            self.served += 1
        except OSError as e:
            print("[neon] room send:", repr(e))
        self._drop(cl)
