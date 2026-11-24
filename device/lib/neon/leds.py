"""The back LEDs' kill switch: Home's LED button, or http://presto:8767/leds.

Off means off until it's switched on again, by the button or over the LAN, and a restart
keeps it off (a marker file on the flash). Gate stands in for the Presto: every LED write
goes through it, so each screen's lights, the lamp follower and the update's show all go
dark without knowing about it; everything else passes through to the Presto.
"""

COUNT = 7  # the Presto's back LEDs
MARKER = "/leds_off"  # present: off


def _exists(path):
    try:
        open(path).close()
        return True
    except OSError:
        return False


class Gate:
    def __init__(self, presto, marker=MARKER):
        self._presto = presto
        self.marker = marker
        self.off = _exists(marker)
        self.changed = False  # switched since the loop last looked (take_changed)

    def __getattr__(self, name):  # display, touch, update, set_backlight...
        return getattr(self._presto, name)

    def set_led_rgb(self, i, r, g, b):
        if not self.off:
            self._presto.set_led_rgb(i, r, g, b)

    def set_led_hsv(self, i, h, s, v):
        if not self.off:
            self._presto.set_led_hsv(i, h, s, v)

    def switch(self, off):
        """Off or on, kept across restarts. Off blanks them at once; on, the loop repaints
        them (take_changed). True when that changed anything."""
        off = bool(off)
        if off == self.off:
            return False
        self.off = off
        try:
            if off:
                open(self.marker, "w").close()
            else:
                import os

                os.remove(self.marker)
        except OSError as e:  # the switch still holds until a restart
            print("[neon] leds marker:", repr(e))
        if off:
            for i in range(COUNT):
                self._presto.set_led_rgb(i, 0, 0, 0)
        self.changed = True
        return True

    def toggle(self):
        self.switch(not self.off)
        return self.off

    def take_changed(self):
        changed, self.changed = self.changed, False
        return changed
