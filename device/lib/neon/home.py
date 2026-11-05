"""Home: a Blade Runner night. Skyline, rain, a neon-sign clock, the forecast, and the four arms."""

import math
import random
import time

from neon import fx, sightings, sprites, theme, tz, wx

W = H = 240  # the Presto at its native speed: 240x240, doubled to the 480 panel
SKY_BOTTOM = 165
GROUND = 226
LEFT = 14  # home_align: left: the text's margin, clear of the left arrow (y 137+)
ARROW_SCALE = 2
_AW, _AH = 15 * ARROW_SCALE, 10 * ARROW_SCALE  # sprites.ARROW pointing up: 30x20
ARROW_AT = {  # top-left corners; left and right sit below the forecast
    "up": ((W - _AW) // 2, 1),
    "down": ((W - _AW) // 2, H - 1 - _AH),
    "left": (1, 152 - _AW // 2),
    "right": (W - 1 - _AH, 152 - _AW // 2),
}


class Home:
    name = "home"
    frame_ms = 90  # ~11 fps (a full repaint costs ~10 ms at 240x240)

    def __init__(self, ctx):
        self.ctx = ctx
        p = ctx.pens
        random.seed(1982)  # the same skyline every boot (MicroPython has no random.Random)
        r = random.randint
        # The skyline: fixed buildings, windows that come and go.
        # Laid out in 480 space with the same seed as at full resolution (so the
        # skyline, and SIGNS' building numbers, stay as they were), then halved.
        self.buildings = []
        x = -10
        while x < 480:
            w = r(28, 70)
            h = r(40, 125)
            self.buildings.append([x, 452 - h, w, h])
            x += w + r(0, 6)
        self.signs = self._place_signs(ctx, p, r)
        self.buildings = [[b[0] // 2, b[1] // 2, (b[2] + 1) // 2, (b[3] + 1) // 2] for b in self.buildings]
        self.sign_boxes = [tuple(v // 2 for v in box) for box in self.sign_boxes]
        self.signs = [(sg[0], sg[1] // 2, sg[2] // 2) + tuple(sg[3:]) for sg in self.signs]
        self.windows = []
        for bx, by, bw, bh in self.buildings:
            for wy in range(by + 3, GROUND - 3, 5):
                for wx_ in range(bx + 2, bx + bw - 3, 4):
                    if any(x0 - 2 <= wx_ <= x1 + 1 and y0 - 2 <= wy <= y1 + 1 for x0, y0, x1, y1 in self.sign_boxes):
                        continue  # keep the wall behind a sign dark
                    if r(0, 9) < 4:
                        self.windows.append([wx_, wy, r(0, 9) < 6, r(0, 2)])
        print("[neon] skyline", [(i, b[0], b[2], b[3]) for i, b in enumerate(self.buildings)])
        print("[neon] signs", [(s[0], s[6], s[7]) for s in self.signs])
        self.stars = [(r(0, W - 1), r(0, SKY_BOTTOM - 70), r(0, 99)) for _ in range(45)]
        random.seed(time.ticks_us())  # everything after this (rain, glitches) differs each boot
        self.rain = fx.Rain(n=60, top=-10, bottom=GROUND)
        self.car = sprites.Sprite(sprites.SPINNER)
        self.car_x = None
        self.car_next = time.ticks_add(time.ticks_ms(), 8000)
        self.icons = {
            "sun": sprites.Sprite(sprites.SUN),
            "moon": sprites.Sprite(sprites.MOON),
            "cloud": sprites.Sprite(sprites.CLOUD),
            "rain_a": sprites.Sprite(sprites.RAIN_A),
            "rain_b": sprites.Sprite(sprites.RAIN_B),
            "snow_a": sprites.Sprite(sprites.SNOW_A),
            "snow_b": sprites.Sprite(sprites.SNOW_B),
            "bolt": sprites.Sprite(sprites.BOLT),
            "fog": sprites.Sprite(sprites.FOG),
        }
        self.arrows = {way: sprites.Sprite(sprites.turn(sprites.ARROW, way)) for way in ARROW_AT}
        night, green = theme.PALETTE["night"], theme.PALETTE["phosphor"]
        glow, light = theme.PALETTE["phosphor_dim"], theme.mix(green, (255, 255, 255), 0.6)
        self.arrow_pens = [  # 16 shades of the breath; the outline stays dark so it reads over the city
            {
                "K": p.night,
                "G": p.rgb(*theme.mix(night, green, t / 15)),
                "W": p.rgb(*theme.mix(night, light, t / 15)),
                "g": p.rgb(*theme.mix(night, glow, t / 15)),
            }
            for t in range(16)
        ]
        # rare sightings in the right half of the sky: only with the left-aligned layout
        minutes = ctx.settings.get("sighting_minutes", 15) if ctx.settings.get("home_align") == "left" else 0
        self.sights = sightings.Sightings(ctx, minutes)
        self.sky_day = [p.rgb(*theme.mix(theme.PALETTE["dust_dark"], theme.PALETTE["dust"], i / 7)) for i in range(8)]
        self.sky_night = [p.rgb(*theme.mix(theme.PALETTE["night"], (30, 14, 52), i / 7)) for i in range(8)]
        self.building_pen = p.rgb(10, 8, 20)
        self.building_edge = p.rgb(24, 18, 44)
        self.win_pens = (p.sodium, p.amber, p.cyan)
        self.rain_far = p.rgb(40, 60, 90)
        self.rain_near = p.rgb(90, 130, 170)
        self.entered_at = 0
        self.glitch_at = time.ticks_add(time.ticks_ms(), 20000)
        self.frame = 0
        self.dirty = True

    def _place_signs(self, ctx, p, r):
        """Neon signs from SIGNS in secrets.py (device only: names stay off the public repo).

        Each entry is a name (placed automatically, vertical) or a tuple
        (name, building, "v" or "h"): building is an index into the skyline,
        counted from the left (the startup log prints it). A vertical sign's
        tower grows to fit its letters; a horizontal one goes across a
        slightly shorter, wider building.
        """
        entries = []
        for e in getattr(ctx.secrets, "SIGNS", ("BRICK",))[:4]:
            if isinstance(e, str):
                entries.append((e, None, "v"))
            else:
                entries.append((e[0], e[1] if len(e) > 1 else None, e[2] if len(e) > 2 else "v"))
        tubes = (p.magenta, p.cyan, p.sodium, p.violet)
        halos = (p.rgb(90, 10, 40), p.rgb(2, 60, 70), p.rgb(80, 45, 6), p.rgb(45, 20, 70))
        n_b = len(self.buildings)
        taken = {e[1] for e in entries if e[1] is not None}
        signs, self.sign_boxes = [], []
        tallest_v = 0
        for i, (name, slot, orient) in enumerate(entries):
            name = str(name).upper()[:7]
            if slot is None:
                target = int(480 * (i + 1) / (len(entries) + 1))
                slot = min(
                    (k for k in range(1, n_b - 1) if k not in taken),
                    key=lambda k: abs(self.buildings[k][0] + self.buildings[k][2] // 2 - target),
                )
                taken.add(slot)
            slot = max(0, min(n_b - 1, slot))
            if orient == "v":
                tallest_v = max(tallest_v, len(name) * 18 + 22, self.buildings[slot][3])
            entries[i] = (name, slot, orient)
        for i, (name, slot, orient) in enumerate(entries):
            b = self.buildings[slot]
            if orient == "h":
                tw = len(name) * 12  # bitmap8 at scale 2
                if b[2] < tw + 16:  # widen it, keeping its centre
                    b[0] -= (tw + 16 - b[2]) // 2
                    b[2] = tw + 16
                h = max(70, (tallest_v or 120) - 28)  # slightly shorter than the towers
                b[3], b[1] = h, 452 - h
                sx, sy = b[0] + (b[2] - tw) // 2, b[1] + 10
                box = (sx, sy, sx + tw, sy + 16)
            else:
                need = len(name) * 18 + 22
                b[2] = max(b[2], 30)
                if b[3] < need:
                    b[3], b[1] = need, 452 - need
                sx, sy = b[0] + b[2] // 2 - 6, b[1] + 10
                box = (sx, sy, sx + 12, sy + len(name) * 18)
            self.sign_boxes.append(box)
            signs.append((name, sx, sy, tubes[i % 4], halos[i % 4], r(0, 52), slot, orient))
        return signs

    # --- lifecycle ---
    def enter(self):
        self.entered_at = time.ticks_ms()

    def exit(self):
        pass

    def tap(self, x, y):
        now = time.ticks_ms()
        if self.sights.minutes and x >= 130 and y < 150 and self.sights.kind is None:
            self.sights.trigger(sightings.pick(self.sights.last, random.random()), now)  # summon one
        else:
            self.glitch_at = now  # tap the sky: make it glitch

    # --- leds ---
    def leds(self, presto, t):
        v = 0.08 + 0.07 * (1 + math.sin(t / 900)) / 2
        for i in range(7):
            # amber at the ends, magenta in the middle, breathing
            presto.set_led_hsv(i, 0.95 if 2 <= i <= 4 else 0.09, 1.0, v)

    # --- drawing ---
    def draw(self, d, now_ms):
        ctx, p = self.ctx, self.ctx.pens
        self.frame += 1
        kind = ctx.weather.kind
        day = bool(ctx.weather.summary and ctx.weather.summary.get("is_day"))
        hot = ctx.weather.summary and (ctx.weather.summary.get("hi") or 0) >= (85 if ctx.fahrenheit else 29)

        # sky: night violet, or 2049 dust when it's a hot day
        bands = self.sky_day if (day and hot) else self.sky_night
        bh = SKY_BOTTOM // len(bands) + 1
        for i, pen in enumerate(bands):
            d.set_pen(pen)
            d.rectangle(0, i * bh, W, bh)
        d.set_pen(bands[-1])
        d.rectangle(0, SKY_BOTTOM, W, H - SKY_BOTTOM)

        # stars on a clear night
        if kind in ("clear", "partly", "unknown") and not (day and hot):
            for sx, sy, ph in self.stars:
                tw = (self.frame + ph) % 40
                d.set_pen(p.text if tw < 3 else p.faint)
                d.pixel(sx, sy)

        # the flying car
        self._car(d, p, now_ms)
        self.sights.back(d, now_ms)  # behind the city

        # skyline and windows
        for bx, by, bw, bh_ in self.buildings:
            d.set_pen(self.building_pen)
            d.rectangle(bx, by, bw, bh_)
            d.set_pen(self.building_edge)
            d.rectangle(bx, by, bw, 1)
        if self.frame % 6 == 0:
            for _ in range(3):
                w = random.choice(self.windows)
                w[2] = not w[2]
        for wx_, wy, on, c in self.windows:
            if on:
                d.set_pen(self.win_pens[c])
                d.rectangle(wx_, wy, 2, 2)
        self._sign(d, p, now_ms)
        self.sights.front(d, now_ms)  # over the rooftops
        d.set_pen(p.night)
        d.rectangle(0, GROUND, W, H - GROUND)

        # rain, as hard as the forecast says
        self.rain.n = wx.RAIN.get(kind, 15)
        if self.rain.n:
            self.rain.step_draw(d, self.rain_far, self.rain_near)

        self._clock(d, p, now_ms)
        self._forecast(d, p)
        self._arms(d, p, now_ms)

    def _car(self, d, p, now_ms):
        if self.car_x is None:
            if time.ticks_diff(now_ms, self.car_next) >= 0:
                self.car_x, self.car_y = -30, random.randint(15, 55)
                self.car_dx = random.choice((2, 3))
            return
        blink = {"R": "K", "P": "K"} if (self.frame // 3) % 2 else None
        self.car.draw(d, p.letters, self.car_x, self.car_y, 2, blink)
        self.car_x += self.car_dx
        if self.car_x > W + 5:
            self.car_x = None
            self.car_next = time.ticks_add(now_ms, random.randint(30000, 70000))

    def _sign(self, d, p, now_ms):
        # vertical neon tubes; each buzzes off now and then, on its own beat
        tick = now_ms // 97
        for name, sx, sy, tube, halo, phase, _slot, orient in self.signs:
            if (tick + phase) % 53 in (0, 2):
                continue
            if orient == "h":
                d.set_pen(halo)
                d.text(name, sx - 1, sy - 1, W, 1)
                d.rectangle(sx - 2, sy + 9, len(name) * 6 + 2, 1)
                d.set_pen(tube)
                d.text(name, sx, sy, W, 1)
                d.rectangle(sx - 1, sy + 9, len(name) * 6, 1)  # the tube's underline
                continue
            for i, ch in enumerate(name):
                d.set_pen(halo)
                d.character(ord(ch), sx - 1, sy + i * 9 - 1, 1)
                d.set_pen(tube)
                d.character(ord(ch), sx, sy + i * 9, 1)

    def _clock(self, d, p, now_ms):
        ctx = self.ctx
        ampm = None
        if ctx.clock.valid:
            lt = ctx.clock.now()
            if ctx.settings["clock_24h"]:
                s = "%02d:%02d" % (lt[3], lt[4])
            else:
                s = "%d:%02d" % (lt[3] % 12 or 12, lt[4])
                ampm = "AM" if lt[3] < 12 else "PM"
            date = tz.date_line(lt)
        else:
            s, date = "--:--", "SYNCING TIME" if ctx.net.up else "NO SIGNAL"
        left = ctx.settings.get("home_align") == "left"
        # centered: 48 px tall, clear of the top arrow and the forecast; left: 32 px, leaving
        # the right half of the sky to the sightings
        scale = 4 if left else 6
        x = LEFT if left else (W - d.measure_text(s, scale) - (26 if ampm else 0)) // 2
        y = 26
        if ampm:
            d.set_pen(p.magenta)
            d.text(ampm, x + d.measure_text(s, scale) + 4, y + 8 * scale - 16, W, 2)  # on the digits' baseline
        since = time.ticks_diff(now_ms, self.entered_at)
        # a neon tube striking for the first second, then a faint hum
        if since < 1100:
            flicker = (0.0, 1.0, 0.1, 0.0, 1.0, 0.4, 1.0)[min(6, since // 160)]
        else:
            flicker = 0.7 if random.random() < 0.03 else 1.0
        if time.ticks_diff(now_ms, self.glitch_at) >= 0:
            fx.glitch_text(d, p, s, x, y, scale, random.randint(2, 4))
            if time.ticks_diff(now_ms, self.glitch_at) > 350:
                self.glitch_at = time.ticks_add(now_ms, random.randint(18000, 32000))
        else:
            fx.neon_text(d, p, s, x, y, scale, p.cyan, p.cyan_glow, flicker)
        if left:
            d.set_pen(p.amber)
            d.text(date, LEFT, y + 8 * scale + 5, W, 1)  # 63..71
        else:
            fx.centered(d, date, y + 8 * scale + 6, 2, p.amber)  # 80..96, above the forecast at 102

    def _forecast(self, d, p):
        ctx = self.ctx
        s = ctx.weather.summary
        kind = ctx.weather.kind
        left = ctx.settings.get("home_align") == "left"
        y = 78 if left else 102
        # the icon: animated, two frames
        alt = (self.frame // 5) % 2
        ix = LEFT if left else 44
        if kind in ("clear", "partly"):
            self.icons["sun" if s and s.get("is_day") else "moon"].draw(d, p.letters, ix, y, 2)
        else:
            self.icons["cloud"].draw(d, p.letters, ix, y, 2)
            if kind in ("rain", "storm"):
                self.icons["rain_b" if alt else "rain_a"].draw(d, p.letters, ix, y + 17, 2)
            if kind == "storm" and alt:
                self.icons["bolt"].draw(d, p.letters, ix, y + 12, 2)
            if kind == "snow":
                self.icons["snow_b" if alt else "snow_a"].draw(d, p.letters, ix, y + 17, 2)
            if kind == "fog":
                self.icons["fog"].draw(d, p.letters, ix, y + 13, 2)
        tx = ix + 34 if left else ix + 38
        big, step, wrap = (1, 10, 100) if left else (2, 18, 158)  # left: the small font, in ~100 px
        d.set_pen(p.text)
        if not ctx.weather.configured:
            d.text("SET LATITUDE/", tx, y + 1, 150, 1)
            d.text("LONGITUDE", tx, y + 10, 150, 1)
        else:
            d.text(wx.line(s, ""), tx, y + 1, wrap, big)
            d.set_pen(p.dim)
            d.text(wx.LABELS.get(kind, "NO DATA"), tx, y + 1 + step, wrap, 1)
        room = ctx.room
        if room.temp is not None:
            d.set_pen(p.sodium)
            d.text("IN %.1f  %d%%RH" % (room.temp, round(room.humidity or 0)), tx, y + 1 + step + (10 if left else 11), wrap, 1)

    def _arms(self, d, p, now_ms):
        # no app names (presto#11): just the four chevrons, fading slowly green <-> black
        # (a ~3 s breath). Each sits inside its edge's tap band (nav.EDGE).
        level = (1 + math.sin(now_ms / 480)) / 2
        pens = self.arrow_pens[int(level * 15)]
        for way, (x, y) in ARROW_AT.items():
            self.arrows[way].draw(d, pens, x, y, ARROW_SCALE)
