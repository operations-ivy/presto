"""The plus: Home in the middle, one app on each arm. Pure logic, tested on CPython.

Navigation is by taps (the user, 2026-11-03): on Home, the band along an edge
opens that arm's app; in an app, the HOME button is hidden on the edge facing
Home: a tap there raises it, a second tap goes Home. A two-finger tap goes Home
from anywhere. `swipe()` is kept for gestures later.
"""

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


EDGE = 32  # px (of 240): the tap band along each edge (Home's arm buttons, an app's HOME tab)
REVEAL = "reveal"  # tap() result: the hidden HOME button was raised
REVEAL_MS = 4000  # how long a raised HOME button stays up
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
        self.revealed_at = None  # when the hidden HOME button was raised (ticks_ms)

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
        """Taps navigate. On Home, an edge band opens that arm's app. In an app the
        HOME button is hidden: a tap on its band (the edge facing Home) raises it
        for REVEAL_MS, and a second tap there while it's up goes Home.

        Returns the new screen name, REVEAL when the button was just raised, or
        None when the tap belongs to the current screen."""
        z = zone(x, y)
        if z is None:
            return None
        if self.current == HOME:
            target = self.arms.get(z)
            if target is None:
                return None
        elif z == self.home_tab():
            if not self.revealed(now_ms):
                self.revealed_at = now_ms
                return REVEAL
            target = HOME
        else:
            return None
        self.current = target
        self.revealed_at = None
        return target

    def revealed(self, now_ms):
        """Whether the HOME button is up (raised less than REVEAL_MS ago)."""
        return self.revealed_at is not None and 0 <= ticks_diff(now_ms, self.revealed_at) < REVEAL_MS

    def reveal_age(self, now_ms):
        """ms since the HOME button was raised (for its rise animation), or None."""
        return ticks_diff(now_ms, self.revealed_at) if self.revealed(now_ms) else None

    def home_tab(self, name=None):
        """The edge an app's HOME tab sits on: the one facing back toward Home."""
        name = name or self.current
        return None if name == HOME else OPPOSITE[self.where[name]]

    def go(self, name):
        """Jump straight to a screen (Home, or an app: the idle timeout)."""
        if name != HOME and name not in self.where:
            raise ValueError(name)
        self.current = name
        self.revealed_at = None
        return name

    def entry_direction(self, name):
        """Which way the screen slides in when moving to `name` from the current one."""
        if name == HOME:
            return OPPOSITE[self.where[self.current]] if self.current != HOME else None
        return self.where[name]
