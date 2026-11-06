"""The plus: Home in the middle, one app on each arm. Pure logic, tested on CPython.

Navigation is by taps (the user, 2026-11-03): on Home, the band along an edge
opens that arm's app; in an app, a small arrow on the edge facing Home points
back to it, and one tap on that edge goes Home (the user, 2026-11-06; it was a
hidden button raised by a first tap). A two-finger tap goes Home from anywhere.
`swipe()` is kept for gestures later.
"""

import math

HOME = "home"
OPPOSITE = {"up": "down", "down": "up", "left": "right", "right": "left"}

# Tunables, in screen pixels (240x240, as touch reports them) and milliseconds.
TAP_MAX_MOVE = 15
TAP_MAX_MS = 600
SWIPE_MIN_MOVE = 35


def classify(dx, dy, ms, two_fingers=False):
    """What a finished touch was: 'two', 'tap', 'up', 'down', 'left', 'right' or None."""
    if two_fingers:
        return "two"
    adx, ady = abs(dx), abs(dy)
    if adx <= TAP_MAX_MOVE and ady <= TAP_MAX_MOVE:
        return "tap" if ms <= TAP_MAX_MS else None  # a long still press: reserved (enhance)
    if max(adx, ady) < SWIPE_MIN_MOVE:
        return None
    if adx >= ady:
        return "right" if dx > 0 else "left"
    return "down" if dy > 0 else "up"


EDGE = 32  # px (of 240): the tap band along each edge (Home's arm buttons, an app's way Home)
_TICKS_PERIOD = 1 << 30  # MicroPython's ticks_ms wraps here


def ticks_diff(a, b):
    """a - b for ticks_ms values, across the wrap (like time.ticks_diff, but pure)."""
    return ((a - b + _TICKS_PERIOD // 2) % _TICKS_PERIOD) - _TICKS_PERIOD // 2


def zone(x, y, size=240):
    """Which edge band a tap is in: 'up', 'down', 'left', 'right' or None (the middle).
    Corners belong to the top and bottom bands."""
    if y < EDGE:
        return "up"
    if y >= size - EDGE:
        return "down"
    if x < EDGE:
        return "left"
    if x >= size - EDGE:
        return "right"
    return None


class Plus:
    def __init__(self, arms):
        """arms: {'up': name, 'down': name, 'left': name, 'right': name}."""
        self.arms = dict(arms)
        self.where = {v: k for k, v in self.arms.items()}  # app name -> arm
        self.current = HOME

    def swipe(self, direction):
        """Apply a swipe; returns the new screen name, or None for a bump."""
        if self.current == HOME:
            target = self.arms.get(direction)
        elif direction == OPPOSITE[self.where[self.current]]:
            target = HOME
        else:
            target = None
        if target is not None:
            self.current = target
        return target

    def tap(self, x, y, now_ms=0):
        """Taps navigate. On Home, an edge band opens that arm's app; in an app, a tap
        on the band facing Home (its arrow's edge) goes Home.

        Returns the new screen name, or None when the tap belongs to the current
        screen. (now_ms is kept for callers; one tap needs no timing.)"""
        z = zone(x, y)
        if z is None:
            return None
        if self.current == HOME:
            target = self.arms.get(z)
            if target is None:
                return None
        elif z == self.home_tab():
            target = HOME
        else:
            return None
        self.current = target
        return target

    def home_tab(self, name=None):
        """The edge an app's way Home sits on: the one facing back toward Home."""
        name = name or self.current
        return None if name == HOME else OPPOSITE[self.where[name]]

    def go(self, name):
        """Jump straight to a screen (Home, or an app: the idle timeout)."""
        if name != HOME and name not in self.where:
            raise ValueError(name)
        self.current = name
        return name

    def entry_direction(self, name):
        """Which way the screen slides in when moving to `name` from the current one."""
        if name == HOME:
            return OPPOSITE[self.where[self.current]] if self.current != HOME else None
        return self.where[name]


# --- Home's arrows (presto#29) ---
ARROW_WAYS = ("up", "right", "down", "left")  # clockwise: the chase's order
ARROW_DIM = 0.25  # between pulses: still there, over the city
ARROW_PULSES = ("faint", "chase", "blink", "breathe")
CHASE_STEP_MS = 450  # the light's time on each arrow
CHASE_TAIL = 1.5  # in arrows: how far behind the light its glow fades out
BLINK_MS, BLINK_REST_MS = 120, 2000  # on, off, on, off, then rest
BREATHE_MS = 480  # the old pulse's sine: ~3 s a breath
FAINT_LEVEL, FAINT_DEPTH, FAINT_MS = 0.4, 0.08, 1600  # a slow ~10 s breath, a few shades deep


def arrow_level(pulse, t, i):
    """Arrow i's brightness (ARROW_WAYS order), 0..1, at t ms. faint (the default): a
    barely-there ~10 s breath around 40 %, so the arrows don't pull the eye from Home; chase: a light runs
    clockwise round the four with a fading tail; blink: all four blink twice, then
    rest; breathe: all four fade together (the old pulse). One float, no allocation:
    it runs four times a frame."""
    if pulse == "chase":
        behind = ((t % (4 * CHASE_STEP_MS)) / CHASE_STEP_MS - i) % 4
        return ARROW_DIM + (1 - ARROW_DIM) * max(0.0, 1 - behind / CHASE_TAIL)
    if pulse == "faint":
        return FAINT_LEVEL + FAINT_DEPTH * math.sin(t / FAINT_MS)
    if pulse == "blink":
        p = t % (4 * BLINK_MS + BLINK_REST_MS)
        return 1.0 if p < 3 * BLINK_MS and (p // BLINK_MS) % 2 == 0 else ARROW_DIM
    return (1 + math.sin(t / BREATHE_MS)) / 2
