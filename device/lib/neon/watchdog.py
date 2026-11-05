"""Restart the Presto if the main loop stops (2026-11-04: a Wi-Fi update froze it for minutes).

A hard machine.Timer fires every CHECK_MS in interrupt context, so it still runs
when Python is stuck inside the firmware (a network read on bad Wi-Fi, which
Ctrl-C couldn't break). If the loop hasn't called beat() for TIMEOUT_MS, it
resets. Nothing the app does blocks anywhere near that long (a TLS connect is
~3 s). Not the chip's own WDT: that one can't be stopped, and would reset the
Presto in the middle of a USB deploy; this one stops with the app (stop()).

A reset from here doesn't leave Wi-Fi first (no network calls in an interrupt),
so the next join may fail once: neon.retry brings it back in ~25 s. An update
that froze mid-swap is on trial, and ota_boot rolls it back if it doesn't run.
"""

import time

CHECK_MS = 5000
TIMEOUT_MS = 45000


class Watchdog:
    def __init__(self, timeout_ms=TIMEOUT_MS):
        self.timeout_ms = timeout_ms
        self.last = time.ticks_ms()
        self.timer = None

    def start(self):
        import machine

        self.last = time.ticks_ms()
        self._reset = machine.reset  # looked up now: an interrupt can't import or allocate
        self._ticks, self._diff = time.ticks_ms, time.ticks_diff
        self.timer = machine.Timer(period=CHECK_MS, callback=self._check, hard=True)

    def beat(self):
        self.last = time.ticks_ms()

    def stop(self):
        if self.timer is not None:
            self.timer.deinit()
            self.timer = None

    def stale(self, now_ms):
        return time.ticks_diff(now_ms, self.last) > self.timeout_ms

    def _check(self, _timer):
        # interrupt context: no allocation (so no method calls or imports), no printing
        if self._diff(self._ticks(), self.last) > self.timeout_ms:
            self._reset()
