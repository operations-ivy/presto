"""The Weather screen's sky (presto#23): the conditions across the whole screen.

Rain as falling streaks, snow drifting, fog banks rolling by, storms with lightning, clouds
on the wind, and the sun's turning rays by day or the moon and stars at night. Each kind
draws only its own layers, and every layer is a few dozen lines or pixels: cheap enough
for the ~10 fps budget at 240x240.

layers() and page() are pure (tested on the laptop); Sky draws.
"""

import math
import random
import time

W = H = 240

try:  # MicroPython's wrapping ticks (README: no fake old ticks); plain arithmetic on the laptop
    _since, _add = time.ticks_diff, time.ticks_add
except AttributeError:

    def _since(a, b):
        return a - b

    def _add(a, b):
        return a + b


# What each kind shows: rain streaks (how many), snow (how many), fog banks, clouds
# (how many), lightning, and whether the sun or moon shows through.
SCENES = {
    "clear": {"rain": 0, "snow": 0, "fog": 0, "clouds": 0, "bolts": False, "sun": True},
    "partly": {"rain": 0, "snow": 0, "fog": 0, "clouds": 2, "bolts": False, "sun": True},
    "cloudy": {"rain": 0, "snow": 0, "fog": 0, "clouds": 4, "bolts": False, "sun": False},
    "fog": {"rain": 0, "snow": 0, "fog": 4, "clouds": 1, "bolts": False, "sun": False},
    "rain": {"rain": 70, "snow": 0, "fog": 0, "clouds": 3, "bolts": False, "sun": False},
    "storm": {"rain": 110, "snow": 0, "fog": 0, "clouds": 4, "bolts": True, "sun": False},
    "snow": {"rain": 0, "snow": 55, "fog": 0, "clouds": 3, "bolts": False, "sun": False},
    "unknown": {"rain": 0, "snow": 0, "fog": 0, "clouds": 0, "bolts": False, "sun": False},
}


def layers(kind):
    return SCENES.get(kind, SCENES["unknown"])


def page(rows, per_page, now_ms, every_ms=6000):
    """The rows on screen now: all of them if they fit, else per_page at a time, turning
    every every_ms. Returns (rows, page number, pages)."""
    if len(rows) <= per_page:
        return rows, 0, 1
    pages = (len(rows) + per_page - 1) // per_page
    n = (now_ms // every_ms) % pages
    return rows[n * per_page : (n + 1) * per_page], n, pages


class Sky:
    def __init__(self, pens):
        p = self.p = pens
        r = random.randint
        self.streaks = [[r(0, W + 40), r(-H, H), r(5, 9)] for _ in range(120)]  # x, y, speed
        self.flakes = [[r(0, W - 1), r(-H, H), r(1, 2), r(0, 628)] for _ in range(60)]  # x, y, speed, phase
        self.clouds = [[r(-40, W), r(4, 70), r(10, 18), r(1, 3) / 10] for _ in range(4)]  # x, y, size, speed
        self.banks = [[r(-120, W), 30 + i * 52, r(2, 5) / 10] for i in range(4)]  # x, y, speed
        self.stars = [(r(0, W - 1), r(0, 110), r(0, 39)) for _ in range(40)]
        self.bolt_at = None  # ticks of the next lightning (set on the first storm frame)
        self.bolt = None  # (points, frames left) while a flash is on screen
        self.frame = 0
        self.rain_pens = (p.rgb(40, 90, 130), p.rgb(70, 160, 210), p.cyan)
        self.cloud_pens = {
            "day": (p.rgb(96, 94, 104), p.rgb(128, 126, 136)),
            "night": (p.rgb(30, 24, 52), p.rgb(46, 36, 76)),
        }
        self.fog_pen = {"day": p.rgb(120, 116, 126), "night": p.rgb(44, 38, 70)}
        self.sun_core, self.sun_ray = p.amber, p.sodium

    def draw(self, d, kind, day, sky_pen, now_ms):
        """sky_pen: the sky's colour behind the moon (its crescent is cut out with it)."""
        scene = layers(kind)
        self.frame += 1
        tone = "day" if day else "night"
        if scene["sun"] and day:
            self._sun(d, now_ms)
        elif scene["sun"]:
            self._moon(d, sky_pen)
        if scene["clouds"]:
            self._clouds(d, scene["clouds"], tone)
        if scene["fog"]:
            self._fog(d, scene["fog"], tone)
        if scene["rain"]:
            self._rain(d, scene["rain"])
        if scene["snow"]:
            self._snow(d, scene["snow"])
        if scene["bolts"]:
            self._lightning(d, now_ms)

    def _sun(self, d, now_ms):
        cx, cy = 196, 40
        a0 = (now_ms % 24000) / 24000 * 2 * math.pi  # a slow turn, a full one in 24 s
        d.set_pen(self.sun_ray)
        for i in range(12):
            a = a0 + i * math.pi / 6
            r1, r2 = 22, 30 + (4 if i % 2 else 0)
            d.line(
                int(cx + r1 * math.cos(a)),
                int(cy + r1 * math.sin(a)),
                int(cx + r2 * math.cos(a)),
                int(cy + r2 * math.sin(a)),
            )
        d.set_pen(self.sun_core)
        d.circle(cx, cy, 17)

    def _moon(self, d, sky_pen):
        p = self.p
        for sx, sy, ph in self.stars:
            d.set_pen(p.text if (self.frame + ph) % 40 < 3 else p.faint)
            d.pixel(sx, sy)
        d.set_pen(p.text)
        d.circle(198, 36, 14)
        d.set_pen(sky_pen)  # a crescent: the sky cut out of it
        d.circle(206, 31, 12)

    def _clouds(self, d, n, tone):
        under, over = self.cloud_pens[tone]
        for c in self.clouds[:n]:
            c[0] += c[3]
            if c[0] > W + 30:
                c[0] = -60
            x, y, s = int(c[0]), c[1], c[2]
            d.set_pen(under)
            d.circle(x, y + s // 2, s)
            d.circle(x + s + 4, y + s // 2 + 2, s - 2)
            d.rectangle(x - 4, y + s // 2, 2 * s + 6, s // 2 + 2)
            d.set_pen(over)
            d.circle(x + s // 2 + 2, y + 2, s - 3)

    def _fog(self, d, n, tone):
        d.set_pen(self.fog_pen[tone])
        for b in self.banks[:n]:
            b[0] += b[2]
            if b[0] > W:
                b[0] = -140
            x = int(b[0])
            for k, yy in enumerate(range(b[1], b[1] + 18, 3)):  # a bank: dashed lines, thinner at the edges
                inset = abs(k - 3) * 12
                d.line(max(0, x + inset), yy, min(W - 1, x + 140 - inset), yy)

    def _rain(self, d, n):
        for i in range(min(n, len(self.streaks))):
            s = self.streaks[i]
            s[0] -= s[2] // 3  # the wind leans it left
            s[1] += s[2] * 2
            if s[1] > H or s[0] < -10:
                s[0], s[1] = random.randint(0, W + 40), -random.randint(0, 40)
            d.set_pen(self.rain_pens[0 if s[2] < 7 else 1 if s[2] < 9 else 2])
            d.line(s[0], s[1], s[0] + s[2] // 3, s[1] - s[2])

    def _snow(self, d, n):
        p = self.p
        for i in range(min(n, len(self.flakes))):
            f = self.flakes[i]
            f[1] += f[2]
            f[3] = (f[3] + 7) % 628
            if f[1] > H:
                f[0], f[1] = random.randint(0, W - 1), -random.randint(0, 30)
            x = int(f[0] + 3 * math.sin(f[3] / 100))
            d.set_pen(p.text if f[2] > 1 else p.dim)
            d.rectangle(x, int(f[1]), f[2] + 1, f[2] + 1)

    def _lightning(self, d, now_ms):
        if self.bolt_at is None:
            self.bolt_at = _add(now_ms, random.randint(2000, 6000))
        if self.bolt is None and _since(now_ms, self.bolt_at) >= 0:
            x, y, pts = random.randint(30, W - 30), 0, []
            while y < 150:
                pts.append((x, y))
                x += random.randint(-14, 14)
                y += random.randint(12, 24)
            pts.append((x, y))
            self.bolt = [pts, 3]
            self.bolt_at = _add(now_ms, random.randint(4000, 11000))
        if self.bolt is None:
            return
        pts, left = self.bolt
        p = self.p
        if left == 3:  # the flash: the sky lights up for one frame
            d.set_pen(p.rgb(70, 64, 110))
            d.rectangle(0, 0, W, H)
        d.set_pen(p.text if left > 1 else p.violet)
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            d.line(x1, y1, x2, y2)
            d.line(x1 + 1, y1, x2 + 1, y2)
        self.bolt[1] -= 1
        if self.bolt[1] <= 0:
            self.bolt = None
