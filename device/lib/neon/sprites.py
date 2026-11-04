"""Pixel art as letter grids, like brick-status's tools/pixel_art.py.

Each sprite is rows of palette letters ('.' is transparent). `runs()` turns a
grid into horizontal runs once, so drawing is one rectangle per run instead
of one call per pixel (single pixels are slow on the Presto: ~14 us each).
All art here is our own.
"""


def runs(grid):
    """[(x, y, length, letter)] for each horizontal run of one letter, skipping '.'."""
    out = []
    for y, row in enumerate(grid):
        x = 0
        while x < len(row):
            ch = row[x]
            n = 1
            while x + n < len(row) and row[x + n] == ch:
                n += 1
            if ch != ".":
                out.append((x, y, n, ch))
            x += n
    return out


def size(grid):
    return max(len(r) for r in grid), len(grid)


class Sprite:
    def __init__(self, grid):
        self.w, self.h = size(grid)
        self.runs = runs(grid)

    def draw(self, display, pens, x, y, scale=3, recolor=None):
        """pens: {letter: pen}; recolor: optional {letter: letter} swap (blinking lights)."""
        for rx, ry, n, ch in self.runs:
            if recolor:
                ch = recolor.get(ch, ch)
            pen = pens.get(ch)
            if pen is None:
                continue
            display.set_pen(pen)
            display.rectangle(x + rx * scale, y + ry * scale, n * scale, scale)


# --- The art ------------------------------------------------------------
# Letters map to theme.PALETTE. C cyan, P magenta, A amber, O dust orange,
# G phosphor green, W white, K outline/dark, M mid grey-blue, L light grey-blue,
# R red light, Y yellow, B blue.

SPINNER = (  # our own flying car: wedge body, cockpit glow, tail and nose lights
    "......KKKKKK......",
    "....KKLLCCLLKK....",
    "..KKMMMMMMMMMMKKR.",
    "AKMMMMMMMMMMMMMMMK",
    "..KKKKMMMMMMKKKK..",
    "....P........P....",
)

SUN = (
    "....A....A....",
    ".....A..A.....",
    "..A.AAAAAA.A..",
    "...AAYYYYAA...",
    "...AYYYYYYA...",
    "AAAAYYYYYYAAAA",
    "...AYYYYYYA...",
    "...AAYYYYAA...",
    "..A.AAAAAA.A..",
    ".....A..A.....",
    "....A....A....",
)

MOON = (
    ".....LLLL.....",
    "...LLWW.......",
    "..LWW.........",
    ".LWW..........",
    ".LWW..........",
    "LWWW..........",
    "LWWW..........",
    ".LWWW.......L.",
    ".LWWWW....LL..",
    "..LLWWWWWLL...",
    "....LLLLLL....",
)

CLOUD = (
    "..............",
    ".....LLLL.....",
    "....LWWWWL....",
    "..LLWWWWWWLL..",
    ".LWWWWWWWWWWL.",
    "LWWWWWWWWWWWWL",
    "LMMMMMMMMMMMML",
    ".LLLLLLLLLLLL.",
)

RAIN_A = (  # two frames of drops under the cloud
    "..C...C...C...",
    "..............",
    "....C...C...C.",
)
RAIN_B = (
    "....C...C...C.",
    "..............",
    "..C...C...C...",
)

SNOW_A = (
    "..W...W...W...",
    "..............",
    "....W...W...W.",
)
SNOW_B = (
    "....W...W...W.",
    "..............",
    "..W...W...W...",
)

BOLT = (
    "......YY......",
    ".....YY.......",
    "....YYYY......",
    "......YY......",
    ".....YY.......",
)

FOG = (
    "LLLLLL..LLLL..",
    "..............",
    "..LLLLLLLL..LL",
    "..............",
    "LLLL..LLLLLLLL",
)
