"""Colours: overwatch's neon palette plus Blade Runner sodium/dust and Matrix phosphor."""

PALETTE = {
    "night": (7, 5, 15),  # overwatch --bg
    "panel": (14, 10, 30),
    "line": (36, 26, 68),
    "text": (231, 227, 255),
    "dim": (163, 157, 208),
    "faint": (111, 104, 160),
    "magenta": (255, 42, 109),
    "cyan": (5, 217, 232),
    "cyan_glow": (2, 70, 90),
    "amber": (249, 200, 14),
    "sodium": (255, 159, 28),  # Blade Runner street light
    "dust": (232, 106, 23),  # 2049 haze
    "dust_dark": (60, 24, 8),
    "violet": (157, 78, 221),
    "phosphor": (57, 255, 136),  # the Matrix
    "phosphor_dim": (8, 70, 34),
    "red": (255, 56, 96),
    "teal_dark": (4, 30, 40),
}

# Sprite letters (sprites.py) -> palette names.
LETTERS = {
    "C": "cyan",
    "P": "magenta",
    "A": "amber",
    "O": "dust",
    "G": "phosphor",
    "W": "text",
    "K": "night",
    "M": "line",
    "L": "faint",
    "R": "red",
    "Y": "amber",
    "B": "violet",
    "g": "phosphor_dim",
}


class Pens:
    """Creates each pen once (create_pen is not free) and maps sprite letters."""

    def __init__(self, display):
        self.d = display
        self.by_name = {k: display.create_pen(*v) for k, v in PALETTE.items()}
        self.letters = {k: self.by_name[v] for k, v in LETTERS.items()}
        self._extra = {}

    def __getattr__(self, name):
        try:
            return self.by_name[name]
        except KeyError:
            raise AttributeError(name)

    def rgb(self, r, g, b):
        key = (r, g, b)
        pen = self._extra.get(key)
        if pen is None:
            pen = self._extra[key] = self.d.create_pen(r, g, b)
        return pen


def mix(a, b, t):
    """Blend two RGB tuples, t in 0..1."""
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
