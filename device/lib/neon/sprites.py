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


def turn(grid, way):
    """An up-pointing grid turned to point "up", "down", "left" or "right"."""
    if way == "up":
        return tuple(grid)
    if way == "down":
        return tuple(reversed(grid))
    cols = tuple("".join(row[x] for row in grid) for x in range(len(grid[0])))  # transpose: apex left
    return cols if way == "left" else tuple("".join(reversed(c)) for c in cols)  # MicroPython: no [::-1]


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
# R red light, Y yellow, B blue, g phosphor glow.

SPINNER = (  # our own flying car: wedge body, cockpit glow, tail and nose lights
    "......KKKKKK......",
    "....KKLLCCLLKK....",
    "..KKMMMMMMMMMMKKR.",
    "AKMMMMMMMMMMMMMMMK",
    "..KKKKMMMMMMKKKK..",
    "....P........P....",
)

ARROW = (  # Home's arms (presto#11, #29): a small chevron, one pixel of green outlined in dark
    "....K....",
    "...KGK...",
    "..KGKGK..",
    ".KGK.KGK.",
    "KGK...KGK",
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

# --- Home's rare sightings (sightings.py). Our own designs, nobody's character. ---

KAIJU = (  # a giant lizard of our own, facing left: glowing dorsal spines, red eye, open jaw
    ".............C...........",
    "............CC..C........",
    "......KKKK..CMC.CC.......",
    ".....KMMMMKKCMMCMC.......",
    "....KMRMMMMMKMMMMKC......",
    "...KMMMMMMMMMMMMMMKC.....",
    "..KMMMKKKMMMMMMMMMMK.....",
    "..KMMK...KMMMMMMMMMK.....",
    "...KMK..KMMMMMMMMMMK.....",
    "....KKKKMMMMLLMMMMMK.....",
    "......KMMMMLLLLMMMMK.....",
    ".....KMKMMLLLLLMMMMK.....",
    "....KMK.KMLLLLLMMMMK.....",
    "....KK..KMMLLLLMMMMMK....",
    "........KMMMLLMMMMMMK....",
    "........KMMMMMMMMMMMMK...",
    ".......KMMMKKKKMMMMMMMK..",
    "......KMMMK....KMMMMMMMK.",
    "......KMMK......KMMKKMMMK",
    ".....KMMMK......KMMK.KKMK",
)

UFO = (  # a saucer: a lit dome, a rim of running lights (A, swapped to blink)
    ".....CCCC.....",
    "....CWWWWC....",
    "..KKKKKKKKKK..",
    ".KMMMMMMMMMMK.",
    "KAMMAMMAMMAMMK",
    ".KKKKKKKKKKKK.",
)

EMBLEM = (  # the homelab's own sky signal: a brick
    "KKKKKKKKKKKK",
    "KKK.KKKKK.KK",
    "KKKKKKKKKKKK",
    "K.KKKKK.KKKK",
    "KKKKKKKKKKKK",
    "KKK.KKKKK.KK",
    "KKKKKKKKKKKK",
)

FIREBALL = (  # the kaiju's blue fireball, flying left: a white-hot core, a flame trail (L)
    "..BBBB.....",
    ".BCCCCBB.L.",
    "BCCWWWCCBLL",
    "BCWWWWWCBBL",
    "BCCWWWCCBLL",
    ".BCCCCBB.L.",
    "..BBBB.....",
)
