#!/usr/bin/env python3
"""Draw the Presto's Home screen on the laptop, to a PNG: for design work without the device.

    poetry run python tools/render_home.py out.png [--signs '["BRICK", ["NEON", 12, "h"]]']
        [--alerts none|warning|critical|stale] [--at-ms 5000] [--frames 1] [--scale 2]

A stand-in for PicoGraphics: the bitmap8 pixel font is Pimoroni's own glyphs
(tools/bitmap8.py); the vector font ("sans") is approximated with DejaVu Sans.
The sign names on the real Presto are in its secrets.py, never here: pass
your own with --signs to see them (the PNG isn't committed). --frames N draws
N frames, 90 ms apart (rain, flicker), and keeps the last.
"""

import argparse
import ast
import functools
import json
import random
import sys
import time
import types
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device" / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

import bitmap8  # noqa: E402

SIZE = 240
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


@functools.cache
def _sans(scale):
    """DejaVu Sans with capitals as tall as the Hershey font's at scale (21 units)."""
    return ImageFont.truetype(SANS, max(1, round(21 * scale / 0.73)))


class Display:
    """The PicoGraphics calls Home makes, drawn into a Pillow image."""

    def __init__(self):
        self.img = Image.new("RGB", (SIZE, SIZE))
        self.draw = ImageDraw.Draw(self.img)
        self.pen = (0, 0, 0)
        self.font = "bitmap8"

    def create_pen(self, r, g, b):
        return (r, g, b)

    def set_pen(self, pen):
        self.pen = pen

    def set_font(self, name):
        self.font = name

    def clear(self):
        self.draw.rectangle((0, 0, SIZE, SIZE), fill=self.pen)

    def pixel(self, x, y):
        if 0 <= x < SIZE and 0 <= y < SIZE:
            self.img.putpixel((int(x), int(y)), self.pen)

    def rectangle(self, x, y, w, h):
        if w > 0 and h > 0:
            self.draw.rectangle((x, y, x + w - 1, y + h - 1), fill=self.pen)

    def line(self, x0, y0, x1, y1, *_):
        self.draw.line((x0, y0, x1, y1), fill=self.pen)

    def _glyph(self, ch):
        i = ord(ch) - 32
        return (bitmap8.WIDTHS[i], bitmap8.GLYPHS[i]) if 0 <= i < len(bitmap8.WIDTHS) else (0, [])

    def character(self, code, x, y, scale=2):
        width, cols = self._glyph(chr(code))
        for cx in range(width):
            for cy in range(8):
                if cols[cx] >> cy & 1:
                    self.rectangle(x + cx * scale, y + cy * scale, scale, scale)

    def measure_text(self, s, scale=2, spacing=1):
        if self.font == "sans":
            return int(_sans(scale).getlength(s))
        if not s:
            return 0
        return sum((self._glyph(c)[0] + spacing) * scale for c in s) - spacing * scale

    def text(self, s, x, y, wrap=SIZE, scale=2, *_):
        s = str(s)
        if self.font == "sans":  # y is the Hershey origin: capitals' baseline 9 units below
            self.draw.text((x, y + 9 * scale), s, fill=self.pen, font=_sans(scale), anchor="ls")
            return
        cx, cy = x, y
        for word in s.split(" "):
            ww = self.measure_text(word, scale)
            if cx != x and cx + ww - x > wrap:
                cx, cy = x, cy + 9 * scale
            for ch in word:
                self.character(ord(ch), cx, cy, scale)
                cx += (self._glyph(ch)[0] + 1) * scale
            cx += (self._glyph(" ")[0] + 1) * scale


def defaults():
    """app.DEFAULTS, read from the source: app.py imports device-only modules (machine)."""
    tree = ast.parse((ROOT / "device" / "lib" / "neon" / "app.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "DEFAULTS":
            return ast.literal_eval(node.value)
    raise SystemExit("no DEFAULTS in app.py")


def ctx_for(display, signs, alerts):
    from neon import pager, theme

    c = types.SimpleNamespace()
    c.display = display
    c.settings = dict(defaults(), sighting_minutes=0)  # no random kaiju in a design shot
    c.secrets = types.SimpleNamespace(SIGNS=tuple(tuple(s) if isinstance(s, list) else s for s in signs))
    c.pens = theme.Pens(display)
    c.net = types.SimpleNamespace(up=True)
    c.clock = types.SimpleNamespace(valid=True, now=lambda: (2026, 11, 22, 22, 47, 0, 6, 326))
    summary = {"hi": 52, "lo": 41, "rain_pct": 40, "sunrise": 420, "sunset": 1010, "is_day": 0}
    c.weather = types.SimpleNamespace(kind="rain", summary=summary, configured=True)
    p = pager.Pager("https://presto.example/alerts" if alerts != "off" else "", 120)
    if alerts in ("warning", "critical"):
        p.data = {
            "n": 2,
            "worst": alerts,
            "alerts": [{"id": "a", "name": "PagerTest", "sev": alerts, "for": 300, "sum": ""}],
        }
        p.got_at = time.ticks_ms()
    elif alerts == "none":
        p.data, p.got_at = {"n": 0, "worst": None, "alerts": []}, time.ticks_ms()
    # "stale": no answer yet, and long past due
    c.pager = p
    c.goto = None
    return c


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out")
    ap.add_argument("--signs", default='["BRICK"]', help="JSON, as SIGNS in secrets.py")
    ap.add_argument("--alerts", default="none", choices=("off", "none", "warning", "critical", "stale"))
    ap.add_argument("--at-ms", type=int, default=5000, help="ms since boot (the critical blink's phase)")
    ap.add_argument("--frames", type=int, default=1)
    ap.add_argument("--scale", type=int, default=2, help="PNG scale (the panel doubles 240 to 480)")
    args = ap.parse_args()

    clock = {"ms": 0}
    time.ticks_ms = lambda: clock["ms"]
    time.ticks_add = lambda a, b: a + b
    time.ticks_diff = lambda a, b: a - b
    time.ticks_us = lambda: 1
    random.seed(7)

    from neon import home

    d = Display()
    ctx = ctx_for(d, json.loads(args.signs), args.alerts)
    if args.alerts == "stale":
        clock["ms"] = 10**8
    screen = home.Home(ctx)
    screen.enter()
    start = max(args.at_ms, clock["ms"] + 2000)  # past the clock's striking
    for f in range(args.frames):
        clock["ms"] = start + f * home.Home.frame_ms
        screen.draw(d, clock["ms"])
    img = d.img.resize((SIZE * args.scale, SIZE * args.scale), Image.NEAREST)
    img.save(args.out)
    print(args.out)


if __name__ == "__main__":
    main()
