"""Home: a Blade Runner night. Skyline, rain, a neon-sign clock, the forecast, and the four arms."""

import math
import random
import time

from neon import fx, sprites, theme, tz, wx

W = H = 480
SKY_BOTTOM = 330
GROUND = 452


class Home:
    name = "home"
    frame_ms = 90  # ~11 fps: a full repaint costs ~70 ms on the Presto

    def __init__(self, ctx):
        self.ctx = ctx
        p = ctx.pens
        random.seed(1982)  # the same skyline every boot (MicroPython has no random.Random)
        r = random.randint
        # The skyline: fixed buildings, windows that come and go.
        self.buildings = []
        x = -10
        while x < W:
            w = r(28, 70)
            h = r(40, 125)
            self.buildings.append([x, GROUND - h, w, h])
            x += w + r(0, 6)
        self.signs = self._place_signs(ctx, p, r)
        self.windows = []
        for bx, by, bw, bh in self.buildings:
            for wy in range(by + 6, GROUND - 6, 9):
                for wx_ in range(bx + 4, bx + bw - 5, 7):
                    if any(x0 - 4 <= wx_ <= x1 + 2 and y0 - 4 <= wy <= y1 + 2 for x0, y0, x1, y1 in self.sign_boxes):
                        continue  # keep the wall behind a sign dark
                    if r(0, 9) < 4:
                        self.windows.append([wx_, wy, r(0, 9) < 6, r(0, 2)])
        print("[neon] skyline", [(i, b[0], b[2], b[3]) for i, b in enumerate(self.buildings)])
        print("[neon] signs", [(s[0], s[6], s[7]) for s in self.signs])
        self.stars = [(r(0, W - 2), r(0, SKY_BOTTOM - 140), r(0, 99)) for _ in range(45)]
        random.seed(time.ticks_us())  # everything after this (rain, glitches) differs each boot
        self.rain = fx.Rain(n=60, top=-20, bottom=GROUND)
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
        self.sky_day = [p.rgb(*theme.mix(theme.PALETTE["dust_dark"], theme.PALETTE["dust"], i / 7)) for i in range(8)]
        self.sky_night = [p.rgb(*theme.mix(theme.PALETTE["night"], (30, 14, 52), i / 7)) for i in range(8)]
        self.building_pen = p.rgb(10, 8, 20)
        self.building_edge = p.rgb(24, 18, 44)
        self.win_pens = (p.sodium, p.amber, p.cyan)
        self.rain_far = p.rgb(40, 60, 90)
        self.rain_near = p.rgb(90, 130, 170)
        self.label_bg = p.rgb(10, 6, 22)
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
                target = int(W * (i + 1) / (len(entries) + 1))
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
                b[3], b[1] = h, GROUND - h
                sx, sy = b[0] + (b[2] - tw) // 2, b[1] + 10
                box = (sx, sy, sx + tw, sy + 16)
            else:
                need = len(name) * 18 + 22
                b[2] = max(b[2], 30)
                if b[3] < need:
                    b[3], b[1] = need, GROUND - need
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
        self.glitch_at = time.ticks_ms()  # tap the sky: make it glitch

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
                d.rectangle(sx, sy, 2 if tw < 3 else 1, 2 if tw < 3 else 1)

        # the flying car
        self._car(d, p, now_ms)

        # skyline and windows
        for bx, by, bw, bh_ in self.buildings:
            d.set_pen(self.building_pen)
            d.rectangle(bx, by, bw, bh_)
            d.set_pen(self.building_edge)
            d.rectangle(bx, by, bw, 2)
        if self.frame % 6 == 0:
            for _ in range(3):
                w = random.choice(self.windows)
                w[2] = not w[2]
        for wx_, wy, on, c in self.windows:
            if on:
                d.set_pen(self.win_pens[c])
                d.rectangle(wx_, wy, 3, 4)
        self._sign(d, p, now_ms)
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
                self.car_x, self.car_y = -60, random.randint(30, 110)
                self.car_dx = random.choice((5, 7))
            return
        blink = {"R": "K", "P": "K"} if (self.frame // 3) % 2 else None
        self.car.draw(d, p.letters, self.car_x, self.car_y, 3, blink)
        self.car_x += self.car_dx
        if self.car_x > W + 10:
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
                d.text(name, sx - 1, sy - 1, 480, 2)
                d.rectangle(sx - 3, sy + 18, len(name) * 12 + 4, 2)
                d.set_pen(tube)
                d.text(name, sx, sy, 480, 2)
                d.rectangle(sx - 2, sy + 18, len(name) * 12 + 2, 1)  # the tube's underline
                continue
            for i, ch in enumerate(name):
                d.set_pen(halo)
                d.character(ord(ch), sx - 1, sy + i * 18 - 1, 2)
                d.set_pen(tube)
                d.character(ord(ch), sx, sy + i * 18, 2)

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
        scale = 11
        x = (W - d.measure_text(s, scale) - (34 if ampm else 0)) // 2
        y = 60
        if ampm:
            d.set_pen(p.magenta)
            d.text(ampm, x + d.measure_text(s, scale) + 8, y + 52, 480, 3)
        since = time.ticks_diff(now_ms, self.entered_at)
        # a neon tube striking for the first second, then a faint hum
        if since < 1100:
            flicker = (0.0, 1.0, 0.1, 0.0, 1.0, 0.4, 1.0)[min(6, since // 160)]
        else:
            flicker = 0.7 if random.random() < 0.03 else 1.0
        if time.ticks_diff(now_ms, self.glitch_at) >= 0:
            fx.glitch_text(d, p, s, x, y, scale, random.randint(3, 7))
            if time.ticks_diff(now_ms, self.glitch_at) > 350:
                self.glitch_at = time.ticks_add(now_ms, random.randint(18000, 32000))
        else:
            fx.neon_text(d, p, s, x, y, scale, p.cyan, p.cyan_glow, flicker)
        fx.centered(d, date, y + 100, 3, p.amber)

    def _forecast(self, d, p):
        ctx = self.ctx
        s = ctx.weather.summary
        kind = ctx.weather.kind
        y = 205
        # the icon: animated, two frames
        alt = (self.frame // 5) % 2
        ix = 96
        if kind in ("clear", "partly"):
            self.icons["sun" if s and s.get("is_day") else "moon"].draw(d, p.letters, ix, y, 3)
        else:
            self.icons["cloud"].draw(d, p.letters, ix, y, 3)
            if kind in ("rain", "storm"):
                self.icons["rain_b" if alt else "rain_a"].draw(d, p.letters, ix, y + 26, 3)
            if kind == "storm" and alt:
                self.icons["bolt"].draw(d, p.letters, ix, y + 18, 3)
            if kind == "snow":
                self.icons["snow_b" if alt else "snow_a"].draw(d, p.letters, ix, y + 26, 3)
            if kind == "fog":
                self.icons["fog"].draw(d, p.letters, ix, y + 20, 3)
        tx = 154
        d.set_pen(p.text)
        if not ctx.weather.configured:
            d.text("SET LATITUDE/", tx, y + 2, 300, 2)
            d.text("LONGITUDE", tx, y + 20, 300, 2)
        else:
            d.text(wx.line(s, ""), tx, y + 2, 320, 3)
            d.set_pen(p.dim)
            d.text(wx.LABELS.get(kind, "NO DATA"), tx, y + 30, 320, 2)
        room = ctx.room
        if room.temp is not None:
            d.set_pen(p.sodium)
            d.text("IN %.1f  %d%%RH" % (room.temp, round(room.humidity or 0)), tx, y + 50, 320, 2)

    def _arms(self, d, p, now_ms):
        pulse = (now_ms // 450) % 2
        chev = p.magenta if pulse else p.rgb(110, 18, 50)
        arms = self.ctx.arms
        labels = self.ctx.labels

        def tag(s, x, y):  # a button: tap it to open that app
            w = d.measure_text(s, 2)
            d.set_pen(self.label_bg)
            d.rectangle(x - 6, y - 6, w + 12, 26)
            d.set_pen(chev)
            d.rectangle(x - 6, y - 6, w + 12, 1)
            d.rectangle(x - 6, y + 19, w + 12, 1)
            d.rectangle(x - 6, y - 6, 1, 26)
            d.rectangle(x + w + 5, y - 6, 1, 26)
            d.set_pen(p.text)
            d.text(s, x, y, 480, 2)
            return w

        up = labels[arms["up"]]
        w = d.measure_text(up, 2)
        d.set_pen(chev)
        d.text("^", (W - 12) // 2, 4, 480, 2)
        tag(up, (W - w) // 2, 22)
        dn = labels[arms["down"]]
        w = d.measure_text(dn, 2)
        tag(dn, (W - w) // 2, 438)
        d.set_pen(chev)
        d.text("v", (W - 12) // 2, 460, 480, 2)
        d.set_pen(chev)
        d.text("<", 4, 290, 480, 3)
        tag(labels[arms["left"]], 24, 293)
        rt = labels[arms["right"]]
        w = d.measure_text(rt, 2)
        tag(rt, W - 28 - w, 293)
        d.set_pen(chev)
        d.text(">", W - 20, 290, 480, 3)
