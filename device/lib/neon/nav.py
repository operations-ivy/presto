"""The plus: Home in the middle, one app on each arm. Pure logic, tested on CPython.

Navigation is by taps (the user, 2026-11-03): on Home, the band along an edge
opens that arm's app; in an app, the HOME tab on the edge facing Home goes back.
A two-finger tap goes Home from anywhere. `swipe()` is kept for gestures later.
"""

HOME = "home"
OPPOSITE = {"up": "down", "down": "up", "left": "right", "right": "left"}

# Tunables, in screen pixels and milliseconds.
TAP_MAX_MOVE = 30
TAP_MAX_MS = 600
SWIPE_MIN_MOVE = 70


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


EDGE = 64  # px: the tap band along each edge (Home's arm buttons, an app's HOME tab)


def zone(x, y, size=480):
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

    def tap(self, x, y):
        """Taps navigate: on Home, an edge band opens that arm's app; in an app,
        the HOME tab (the band facing the centre) goes Home. Returns the new screen
        name, or None when the tap belongs to the current screen."""
        z = zone(x, y)
        if z is None:
            return None
        if self.current == HOME:
            target = self.arms.get(z)
        elif z == self.home_tab():
            target = HOME
        else:
            return None
        self.current = target
        return target

    def home_tab(self, name=None):
        """The edge an app's HOME tab sits on: the one facing back toward Home."""
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
