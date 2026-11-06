"""Effects: neon text, glitch, glyph drops (Home's rain), glyph rain, scanlines. All cheap by design.

The app runs at 240x240 (the panel doubles it): a clear is 2 ms, an update 9 ms,
a frame copy 7 ms (at 480x480 they were 45, 24 and 70 ms; measured 2026-11-04).
"""

import random

GLYPHS = "0123456789ABCDEF<>=+*:;#%$&@"  # our own set, drawn with the bitmap font


def text_w(display, s, scale):
    return display.measure_text(s, scale)


W = 240


def centered(display, s, y, scale, pen, W=W):
    display.set_pen(pen)
    display.text(s, (W - text_w(display, s, scale)) // 2, y, W, scale)


# The vector (Hershey) font, for text between the pixel font's whole steps (8, 16 px).
# Its capitals run from VECTOR_TOP units above the glyph's origin down to the baseline,
# VECTOR_CAP units in all (classic Hershey metrics; tune here if the device disagrees).
VECTOR_CAP, VECTOR_TOP = 21, 12


def vector_text(display, s, x, y, px, wrap=W):
    """s with capitals px tall, their top at y, in the vector font; the pixel font
    (bitmap8) is set again after."""
    k = px / VECTOR_CAP
    display.set_font("sans")
    display.text(s, x, y + int(VECTOR_TOP * k + 0.5), wrap, k)
    display.set_font("bitmap8")


def vector_width(display, s, px):
    """vector_text()'s width for s, in px."""
    display.set_font("sans")
    w = display.measure_text(s, px / VECTOR_CAP)
    display.set_font("bitmap8")
    return w


def neon_text(display, pens, s, x, y, scale, core, glow, flicker=1.0):
    """A neon tube: a halo offset around the core. flicker < 1 dims the halo (a stutter)."""
    if flicker > 0.3:
        display.set_pen(glow)
        for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            display.text(s, x + ox, y + oy, W, scale)
    display.set_pen(core if flicker > 0.15 else glow)
    display.text(s, x, y, W, scale)


def glitch_text(display, pens, s, x, y, scale, amount):
    """RGB split and a jitter, for a moment: magenta left, cyan right, white core."""
    display.set_pen(pens.magenta)
    display.text(s, x - amount, y + random.randint(-1, 1), W, scale)
    display.set_pen(pens.cyan)
    display.text(s, x + amount, y + random.randint(-1, 1), W, scale)
    display.set_pen(pens.text)
    display.text(s, x + random.randint(-1, 1), y, W, scale)


class GlyphDrops:
    """Home's rain as single green glyphs falling from the sky (the user, 2026-11-04);
    how many fall still follows the forecast. A drop changes glyph now and then, and drags
    the glyph it was before one line behind it, darker: a short trail."""

    MAX_X = W - 6  # a glyph is 6 px wide

    def __init__(self, n=60, top=0, bottom=W):
        self.top, self.bottom = top, bottom
        g = len(GLYPHS)
        # [x, y, speed, glyph, the glyph before it (the trail's)]
        self.drops = [
            [
                random.randint(0, self.MAX_X),
                random.randint(top, bottom),
                random.randint(3, 7),
                random.randrange(g),
                random.randrange(g),
            ]
            for _ in range(200)
        ]
        self.n = n

    def step_draw(self, display, pens):
        """pens: (glyph, trail) pairs for far, near and the fastest drops."""
        g = len(GLYPHS)
        for i in range(min(self.n, len(self.drops))):
            d = self.drops[i]
            d[1] += d[2]
            if d[1] > self.bottom:
                d[0], d[1] = random.randint(0, self.MAX_X), self.top - random.randint(0, 30)
            elif random.random() < 0.06:
                d[4], d[3] = d[3], random.randrange(g)
            glyph, trail = pens[2 if d[2] >= 7 else 1 if d[2] >= 5 else 0]
            display.set_pen(trail)
            display.character(ord(GLYPHS[d[4]]), d[0], d[1] - 9, 1)  # one line behind
            display.set_pen(glyph)
            display.character(ord(GLYPHS[d[3]]), d[0], d[1], 1)


class GlyphRain:
    """Matrix-style falling columns of our own glyphs."""

    def __init__(self, cols=24, scale=1, top=0, bottom=W, speed=(3, 7), length=(6, 16)):
        self.scale = scale
        self.top, self.bottom = top, bottom
        self.cw = W // cols
        self.cols = [
            [random.randint(top - 200, bottom), random.randint(*speed), random.randint(*length), True]
            for _ in range(cols)
        ]

    def set_health(self, i, ok):
        if 0 <= i < len(self.cols):
            self.cols[i][3] = ok

    def step_draw(self, display, pens):
        step = 8 * self.scale + 1
        for i, c in enumerate(self.cols):
            c[0] += c[1] if c[3] else random.choice((0, 0, c[1] * 2))  # a sick column stutters
            if c[0] - c[2] * step > self.bottom:
                c[0] = self.top - random.randint(0, 100)
            x = i * self.cw + 2
            for k in range(c[2]):
                y = c[0] - k * step
                if y < self.top or y > self.bottom:
                    continue
                if k == 0:
                    display.set_pen(pens.text if c[3] else pens.red)
                elif c[3]:
                    display.set_pen(pens.phosphor if k < 4 else pens.phosphor_dim)
                else:
                    display.set_pen(pens.red if k < 3 else pens.line)
                display.character(
                    ord(random.choice(GLYPHS)) if k < 2 else ord(GLYPHS[(i * 7 + k) % len(GLYPHS)]), x, y, self.scale
                )


def scanlines(display, pen, top=0, bottom=W, every=2):
    display.set_pen(pen)
    for y in range(top, bottom, every):
        display.line(0, y, W - 1, y)


def static_frames():
    """How much static covers the screen on each frame of a switch (1 = all noise)."""
    return (1.0, 0.8, 0.55, 0.35, 0.18, 0.07)


def draw_static(display, pens, cover):
    """TV static over the new screen, like the ??? screen's: random noise bars, plus a
    bright tear line now and then. At cover 1 the screen is all static."""
    if cover <= 0:
        return
    if cover >= 1.0:
        display.set_pen(pens.night)
        display.clear()
    for _ in range(int(160 * cover)):
        y = random.randint(0, W - 2)
        display.set_pen(random.choice((pens.line, pens.faint, pens.panel, pens.violet)))
        display.rectangle(random.randint(-30, 220), y, random.randint(15, 140), random.randint(1, 2))
    for _ in range(random.randint(0, 2)):  # a tear: the picture losing its hold
        display.set_pen(pens.text if random.random() < 0.5 else pens.dim)
        display.rectangle(0, random.randint(0, W - 1), W, 1)
