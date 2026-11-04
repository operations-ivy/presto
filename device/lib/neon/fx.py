"""Effects: neon text, glitch, rain, glyph rain, scanlines. All cheap by design.

Budget (measured on the Presto, full_res): full clear 45 ms, full update 24 ms,
150 rain lines 4.6 ms, a big text 1.3 ms, single pixels ~14 us each.
"""

import random

GLYPHS = "0123456789ABCDEF<>=+*:;#%$&@"  # our own set, drawn with the bitmap font


def text_w(display, s, scale):
    return display.measure_text(s, scale)


def centered(display, s, y, scale, pen, W=480):
    display.set_pen(pen)
    display.text(s, (W - text_w(display, s, scale)) // 2, y, W, scale)


def neon_text(display, pens, s, x, y, scale, core, glow, flicker=1.0):
    """A neon tube: a halo offset around the core. flicker < 1 dims the halo (a stutter)."""
    if flicker > 0.3:
        display.set_pen(glow)
        for ox, oy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, 1)):
            display.text(s, x + ox, y + oy, 480, scale)
    display.set_pen(core if flicker > 0.15 else glow)
    display.text(s, x, y, 480, scale)


def glitch_text(display, pens, s, x, y, scale, amount):
    """RGB split and a jitter, for a moment: magenta left, cyan right, white core."""
    display.set_pen(pens.magenta)
    display.text(s, x - amount, y + random.randint(-1, 1), 480, scale)
    display.set_pen(pens.cyan)
    display.text(s, x + amount, y + random.randint(-1, 1), 480, scale)
    display.set_pen(pens.text)
    display.text(s, x + random.randint(-1, 1), y, 480, scale)


class Rain:
    """Slanted streaks falling through the scene; density changes with the forecast."""

    def __init__(self, n=120, top=0, bottom=480):
        self.top, self.bottom = top, bottom
        self.drops = [[random.randint(0, 520), random.randint(top, bottom), random.randint(9, 17)] for _ in range(200)]
        self.n = n

    def step_draw(self, display, pen_far, pen_near):
        for i in range(min(self.n, len(self.drops))):
            d = self.drops[i]
            d[1] += d[2]
            d[0] -= d[2] // 5
            if d[1] > self.bottom:
                d[0], d[1] = random.randint(0, 540), self.top - random.randint(0, 40)
            display.set_pen(pen_near if d[2] > 14 else pen_far)
            display.line(d[0], d[1], d[0] - d[2] // 5, d[1] + d[2] // 2 + 3)


class GlyphRain:
    """Matrix-style falling columns of our own glyphs."""

    def __init__(self, cols=24, scale=2, top=0, bottom=480, speed=(6, 14), length=(6, 16)):
        self.scale = scale
        self.top, self.bottom = top, bottom
        self.cw = 480 // cols
        self.cols = [
            [random.randint(top - 400, bottom), random.randint(*speed), random.randint(*length), True] for _ in range(cols)
        ]

    def set_health(self, i, ok):
        if 0 <= i < len(self.cols):
            self.cols[i][3] = ok

    def step_draw(self, display, pens):
        step = 8 * self.scale + 2
        for i, c in enumerate(self.cols):
            c[0] += c[1] if c[3] else random.choice((0, 0, c[1] * 2))  # a sick column stutters
            if c[0] - c[2] * step > self.bottom:
                c[0] = self.top - random.randint(0, 200)
            x = i * self.cw + 4
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
                display.character(ord(random.choice(GLYPHS)) if k < 2 else ord(GLYPHS[(i * 7 + k) % len(GLYPHS)]),
                                  x, y, self.scale)


def scanlines(display, pen, top=0, bottom=480, every=4):
    display.set_pen(pen)
    for y in range(top, bottom, every):
        display.line(0, y, 479, y)


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
        y = random.randint(0, 476)
        display.set_pen(random.choice((pens.line, pens.faint, pens.panel, pens.violet)))
        display.rectangle(random.randint(-60, 440), y, random.randint(30, 280), random.randint(1, 4))
    for _ in range(random.randint(0, 2)):  # a tear: the picture losing its hold
        display.set_pen(pens.text if random.random() < 0.5 else pens.dim)
        display.rectangle(0, random.randint(0, 478), 480, 1)


def home_tab(display, pens, edge, now_ms):
    """The HOME tab an app shows on the edge facing Home (tap it to go back).
    It fills the middle of that edge's tap band (nav.EDGE) and pulses gently."""
    pulse = (now_ms // 600) % 2
    ring = pens.cyan if pulse else pens.cyan_glow
    if edge in ("up", "down"):
        w, h = 150, 40
        x = (480 - w) // 2
        y = 8 if edge == "up" else 480 - 8 - h
        label = "^ HOME" if edge == "up" else "v HOME"
    else:
        w, h = 44, 150
        x = 6 if edge == "left" else 480 - 6 - w
        y = (480 - h) // 2
        label = None
    display.set_pen(pens.night)
    display.rectangle(x, y, w, h)
    display.set_pen(ring)
    display.rectangle(x, y, w, 2)
    display.rectangle(x, y + h - 2, w, 2)
    display.rectangle(x, y, 2, h)
    display.rectangle(x + w - 2, y, 2, h)
    display.set_pen(pens.text)
    if label:
        display.text(label, x + (w - display.measure_text(label, 3)) // 2, y + 10, 480, 3)
    else:  # vertical: an arrow, then H O M E down the tab
        display.text("<" if edge == "left" else ">", x + 15, y + 8, 480, 3)
        for i, ch in enumerate("HOME"):
            display.character(ord(ch), x + 15, y + 40 + i * 26, 3)
