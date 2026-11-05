"""The back LEDs mirror the plasma API: builds, deploys, checks (brick9000's buttons).

Whatever a Jenkins job or anything else signals plasma shows on the Presto's seven
LEDs too, the same effect and colour, from plasma's own GET / ("look": each
display's effect, color and seconds), so there's one set of displays, in
brick-cicd-config. With nothing showing, the LEDs go back to the screen's own.

Plain HTTP on the LAN to brick9000 by name, every POLL_MS: a ~50 ms request, not
the ~3 s TLS connect of the cluster's front door. The board's own idle signal
(source "board") is brick9000's health, slot by slot, so it isn't mirrored. Dark
room: off, as always. LEDs only: no sound, nothing on the screen.

frame() and pick() are pure (tested on the laptop); Mirror does the I/O.
"""

import time

N = 7
POLL_MS = 2000
TIMEOUT_S = 0.4
LEVEL = 1.0  # full: at a third the user could barely see it (2026-11-04); settings: mirror_level
IGNORE = ("board",)


def parse_colour(s):
    s = (s or "").lstrip("#")
    if len(s) != 6:
        return (255, 255, 255)
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def hsv(h, s, v):
    """h, s, v in 0..1 -> (r, g, b) 0..255."""
    h = (h % 1.0) * 6
    i = int(h)
    f = h - i
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    r, g, b = ((v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q))[i % 6]
    return (int(r * 255), int(g * 255), int(b * 255))


def _scale(c, k):
    k = max(0.0, min(1.0, k))
    return (int(c[0] * k), int(c[1] * k), int(c[2] * k))


def _noise(i, step):
    """0..1, the same for the same LED and step (a cheap hash: no random state)."""
    x = (i * 374761393 + step * 668265263) & 0xFFFFFFFF
    x = ((x ^ (x >> 13)) * 1274126177) & 0xFFFFFFFF
    return (x & 0xFFFF) / 65535


def frame(look, t_ms, n=N, level=LEVEL):
    """[(r, g, b)] for each LED at t_ms, playing plasma's look."""
    effect = look.get("effect", "solid")
    c = parse_colour(look.get("color"))
    period = max(200, int(float(look.get("seconds") or 2.0) * 1000))
    ph = (t_ms % period) / period  # 0..1 through one cycle
    out = []
    for i in range(n):
        if effect == "solid":
            k = 1.0
        elif effect == "pulse":
            low = float(look.get("low", 0.1))
            k = low + (1 - low) * (1 - abs(2 * ph - 1))
        elif effect == "flash":
            k = 1.0 if ph < 0.5 else 0.0
        elif effect in ("chase", "stream", "rush"):
            head = ph * n
            d = (head - i) % n  # how far behind the head
            k = max(0.05, 1 - d / (2.0 if effect == "rush" else 3.0))
        elif effect == "scanner":
            head = (n - 1) * (1 - abs(2 * ph - 1))  # back and forth
            k = max(0.03, 1 - abs(head - i) / 1.5)
        elif effect == "converge":
            mid = (n - 1) / 2
            head = mid * ph  # from both ends to the middle
            k = max(0.03, 1 - abs(abs(i - mid) - (mid - head)) / 1.2)
        elif effect == "fill":
            k = 1.0 if i < int(ph * (n + 1)) else 0.04
        elif effect in ("rainbow", "nightly"):
            out.append(_scale(hsv(i / n + ph, 1.0, 1.0), level * (0.6 if effect == "nightly" else 1.0)))
            continue
        elif effect == "boil":
            v = _noise(i, t_ms // 90)
            out.append(_scale(hsv(0.02 + 0.08 * v, 1.0, 1.0), level * (0.3 + 0.7 * v)))
            continue
        else:  # an effect added to plasma later: show its colour, breathing
            k = 0.4 + 0.6 * (1 - abs(2 * ph - 1))
        out.append(_scale(c, k * level))
    return out


def pick(state, ignore=IGNORE):
    """The look to mirror from plasma's GET /: the strongest signal that has one."""
    for sig in (state or {}).get("signals") or ():
        if sig.get("look") and sig.get("source") not in ignore:
            return sig["look"]
    return None


def parse_url(url):
    """'http://host:port/path' -> (host, port, path)."""
    rest = url.split("://", 1)[-1]
    hostport, _, path = rest.partition("/")
    host, _, port = hostport.partition(":")
    return host, int(port or 80), "/" + path


class Mirror:
    def __init__(self, url, level=LEVEL):
        self.url = url or ""
        self.level = max(0.0, min(1.0, level))
        self.look = None  # what to play, or None: the screen's own colours
        self.next_at = time.ticks_ms()
        self.addr = None  # resolved once: the router's DNS is slow
        self.fails = 0

    def step(self, net, now_ms):
        """Call once per pass. Returns True while the LEDs belong to plasma."""
        if not self.url or not net.up:
            self.look = None
            return False
        if time.ticks_diff(now_ms, self.next_at) >= 0:
            self.next_at = time.ticks_add(now_ms, POLL_MS * (1 if self.fails < 3 else 15))  # brick9000 off: 30 s
            self._poll()
        return self.look is not None

    def show(self, presto, now_ms):
        for i, (r, g, b) in enumerate(frame(self.look, now_ms, level=self.level)):
            presto.set_led_rgb(i, r, g, b)

    def _poll(self):
        import json
        import socket

        s = None
        try:
            host, port, path = parse_url(self.url)
            if self.addr is None:
                self.addr = socket.getaddrinfo(host, port)[0][-1]
            s = socket.socket()
            s.settimeout(TIMEOUT_S)
            s.connect(self.addr)
            s.send(b"GET %s HTTP/1.0\r\nHost: %s\r\n\r\n" % (path.encode(), host.encode()))
            buf = b""
            while len(buf) < 8192:
                chunk = s.recv(1024)
                if not chunk:
                    break
                buf += chunk
            head, _, body = buf.partition(b"\r\n\r\n")
            status = head.split(b"\r\n", 1)[0]
            if b" 200" not in status:
                raise OSError("plasma said %r" % status)
            self.look, self.fails = pick(json.loads(body)), 0
        except Exception as e:
            self.fails += 1
            if self.fails >= 3:
                self.addr = None  # it may have moved
            if self.fails == 3:
                print("[neon] mirror:", repr(e))
            self.look = None
        finally:
            if s is not None:
                s.close()
