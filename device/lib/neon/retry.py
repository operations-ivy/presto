"""When to try Wi-Fi again (presto#16). Pure logic, tested on CPython.

The Presto used to connect once, at boot: when that failed (2 boots of ~6 on
2026-11-04), it stayed offline until a restart. Now, once the link has been
down for GRACE_MS (time for a connect already under way, or a short drop, to
finish by itself), it says "retry", then waits 30 s, 60 s, 120 s and every
5 min after that. Coming back up resets it.
"""

from neon.nav import ticks_diff

GRACE_MS = 20000
FIRST_MS = 30000
MAX_MS = 300000


class Backoff:
    def __init__(self, grace_ms=GRACE_MS, first_ms=FIRST_MS, max_ms=MAX_MS):
        self.grace_ms, self.first_ms, self.max_ms = grace_ms, first_ms, max_ms
        self.down_since = None  # ticks_ms when the link was first seen down
        self.next_at = None  # ticks_ms of the next retry
        self.wait_ms = first_ms
        self.attempts = 0  # retries since the link went down

    def step(self, up, now_ms):
        """Call once per pass. True means: call connect() now."""
        if up:
            self.down_since = self.next_at = None
            self.wait_ms, self.attempts = self.first_ms, 0
            return False
        if self.down_since is None:
            self.down_since = now_ms
            self.next_at = now_ms + self.grace_ms
            return False
        if ticks_diff(now_ms, self.next_at) < 0:
            return False
        self.next_at = now_ms + self.wait_ms
        self.wait_ms = min(self.wait_ms * 2, self.max_ms)
        self.attempts += 1
        return True

    def down_ms(self, now_ms):
        """How long the link has been down, or None while it's up."""
        return None if self.down_since is None else ticks_diff(now_ms, self.down_since)
