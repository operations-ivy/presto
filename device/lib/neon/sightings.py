"""Rare sightings over Home's city: a kaiju, the sky signal, fireworks, a UFO.

Every `sighting_minutes` on average (anywhere from half to one and a half of
that, never the same one twice running), one plays for a few seconds in the
right half of the sky, where the left-aligned Home leaves room. Each draws in
two layers: `back` before the skyline (so the city stands in front of it) and
`front` after it. Our own designs, nobody's character.

next_gap_ms() and pick() are pure (tested on the laptop); Sightings does the drawing.
"""

import math
import random
import time

from neon import sprites

KINDS = ("kaiju", "signal", "fireworks", "ufo")
LENGTH_MS = {"kaiju": 16000, "signal": 14000, "fireworks": 13000, "ufo": 15000}
GROUND = 226


def next_gap_ms(minutes, r):
    """The wait before the next sighting: minutes, give or take half. r is 0..1."""
    return int(minutes * 60000 * (0.5 + r))


def pick(last, r):
    """A kind other than the last one. r is 0..1."""
    choices = [k for k in KINDS if k != last]
    return choices[min(len(choices) - 1, int(r * len(choices)))]


def ease(t):
    """0..1 -> 0..1, slow at both ends."""
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, t)))


class Sightings:
    def __init__(self, ctx, minutes):
        self.ctx = ctx
        self.minutes = minutes
        self.kind = self.last = None
        self.started = 0
        self.next_at = time.ticks_add(time.ticks_ms(), next_gap_ms(minutes, random.random()))
        self.kaiju = sprites.Sprite(sprites.KAIJU)
        self.ufo = sprites.Sprite(sprites.UFO)
        self.emblem = sprites.Sprite(sprites.EMBLEM)
        self.sparks = []  # fireworks: [x, y, dx, dy, life, pen]
        self.rockets = []  # [x, y, dy, burst_y, pen]
        p = ctx.pens
        self.beam = p.rgb(46, 44, 30)  # a searchlight through haze
        self.beam_hi = p.rgb(90, 86, 52)
        self.spot = p.rgb(150, 140, 80)
        self.tractor = p.rgb(20, 70, 50)
        self.breath = (p.cyan, p.rgb(150, 255, 255), p.cyan_glow)
        self.firework_pens = (p.magenta, p.cyan, p.amber, p.phosphor, p.violet, p.text)

    def step(self, now_ms):
        """Start or end a sighting. Returns its 0..1 progress, or None when the sky is quiet."""
        if self.minutes <= 0:
            return None
        if self.kind is None:
            if time.ticks_diff(now_ms, self.next_at) < 0:
                return None
            self.kind = pick(self.last, random.random())
            self.started = now_ms
            self.sparks, self.rockets = [], []
            print("[neon] sighting:", self.kind)
        t = time.ticks_diff(now_ms, self.started) / LENGTH_MS[self.kind]
        if t >= 1:
            self.last, self.kind = self.kind, None
            self.next_at = time.ticks_add(now_ms, next_gap_ms(self.minutes, random.random()))
            return None
        return t

    def trigger(self, kind, now_ms):
        """Start this one now (for trying them out)."""
        self.kind, self.started = kind, now_ms
        self.sparks, self.rockets = [], []

    # --- drawing ---
    def back(self, d, now_ms):
        t = self.step(now_ms)
        if t is None:
            return
        getattr(self, "_" + self.kind)(d, now_ms, t, "back")

    def front(self, d, now_ms):
        if self.kind is not None:
            t = time.ticks_diff(now_ms, self.started) / LENGTH_MS[self.kind]
            getattr(self, "_" + self.kind)(d, now_ms, t, "front")

    def _kaiju(self, d, now_ms, t, layer):
        """It rises behind the city, roars a beam across the sky, and sinks again."""
        if layer != "back":
            return
        rise = ease(t / 0.2) if t < 0.8 else ease((1 - t) / 0.2)
        top = int(GROUND - rise * (GROUND - 132))
        x = 158 + int(3 * math.sin(now_ms / 400))  # it sways
        p = self.ctx.pens
        blink = {"R": "K"} if (now_ms // 1500) % 7 == 0 else None
        self.kaiju.draw(d, p.letters, x, top, 3, blink)
        if 0.35 < t < 0.65:  # the roar: a beam from its jaw out over the sky
            jaw_y = top + 7 * 3
            core, hot, glow = self.breath
            reach = int(min(1.0, (t - 0.35) / 0.08) * 120)
            d.set_pen(glow)
            d.rectangle(x - reach, jaw_y - 2, reach, 5)
            d.set_pen(core if (now_ms // 60) % 2 else hot)
            d.rectangle(x - reach, jaw_y, reach, 2)

    def _signal(self, d, now_ms, t, layer):
        """A searchlight comes on in the city and throws the emblem onto the clouds."""
        if layer != "back":
            return
        on = min(1.0, t / 0.15) if t < 0.85 else (1 - t) / 0.15
        if on <= 0 or (t < 0.15 and random.random() < 0.3):  # the lamp striking
            return
        sx, sy = 196, GROUND - 30  # the lamp, behind the buildings
        cx, cy = 168 + int(6 * math.sin(now_ms / 1700)), 52  # the spot on the clouds
        d.set_pen(self.beam)
        for k in range(-9, 10, 2):  # a fan of faint lines: a beam through haze
            d.line(sx, sy, cx + k * 3, cy + 6)
        d.set_pen(self.beam_hi)
        d.line(sx, sy, cx, cy + 6)
        d.set_pen(self.spot)
        d.circle(cx, cy, 24)
        self.emblem.draw(d, self.ctx.pens.letters, cx - 18, cy - 10, 3)

    def _fireworks(self, d, now_ms, t, layer):
        """Rockets from behind the city; each bursts into a falling ring of sparks."""
        if layer != "back":
            return
        if t < 0.8 and random.random() < 0.09:
            pen = random.choice(self.firework_pens)
            self.rockets.append([random.randint(140, 225), GROUND - 20, -random.randint(5, 7),
                                 random.randint(30, 95), pen])
        p = self.ctx.pens
        for r in self.rockets[:]:
            r[1] += r[2]
            d.set_pen(p.sodium)
            d.rectangle(r[0], r[1], 1, 3)
            if r[1] <= r[3]:
                self.rockets.remove(r)
                n = 22
                for k in range(n):
                    a = 2 * math.pi * k / n
                    v = random.uniform(2.0, 3.2)
                    self.sparks.append([r[0], r[1], v * math.cos(a), v * math.sin(a), random.randint(9, 15), r[4]])
        for s in self.sparks[:]:
            s[0] += s[2]
            s[1] += s[3]
            s[2] *= 0.92
            s[3] = s[3] * 0.92 + 0.18  # gravity
            s[4] -= 1
            if s[4] <= 0:
                self.sparks.remove(s)
                continue
            d.set_pen(s[5] if s[4] > 4 else p.faint)
            d.rectangle(int(s[0]), int(s[1]), 2 if s[4] > 6 else 1, 2 if s[4] > 6 else 1)

    def _ufo(self, d, now_ms, t, layer):
        """A saucer drifts in, beams something up from a rooftop, and darts off."""
        p = self.ctx.pens
        if t < 0.3:
            x = 250 - int(ease(t / 0.3) * 80)
        elif t < 0.8:
            x = 170
        else:
            x = 170 - int(((t - 0.8) / 0.2) ** 2 * 260)
        y = 70 + int(3 * math.sin(now_ms / 300))
        if layer == "back":
            return
        if 0.35 < t < 0.75:  # the tractor beam, and what it lifts
            d.set_pen(self.tractor)
            bx = x + 14
            for k in range(0, GROUND - y - 30, 3):
                w = 4 + k // 6
                d.rectangle(bx - w // 2, y + 12 + k, w, 1)
            lift = (t - 0.35) / 0.4
            d.set_pen(p.sodium)
            d.rectangle(bx - 2, int(GROUND - 34 - lift * (GROUND - y - 50)), 4, 3)
        blink = {"A": "R"} if (now_ms // 200) % 2 else None
        self.ufo.draw(d, p.letters, x, y, 2, blink)
