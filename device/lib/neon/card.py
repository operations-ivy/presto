"""The SD card: mount it, and mount it again after an I/O error.

2026-11-05: after an OSError 5 (EIO) the card kept failing every read
(Photos: CAN'T SHOW, for hours) and photo sync crashed the app, yet a restart
read the same files fine. The driver's state was stuck, not the files. So an
EIO from the card remounts it (a fresh SPI and SDCard, as at boot), at most
once every GAP_MS.
"""

import os
import time

SD = "/sd"
EIO = 5
GAP_MS = 30000

remounts = 0
_last = None  # ticks_ms of the last remount


def mount():
    import machine
    import sdcard

    try:
        spi = machine.SPI(0, sck=machine.Pin(34), mosi=machine.Pin(35), miso=machine.Pin(36))
        # 24 MHz: the driver's default 1.3 MHz read a 460 KB file in 4.7 s, 24 MHz in 1.3 s
        os.mount(sdcard.SDCard(spi, machine.Pin(39), baudrate=24_000_000), SD)
        return True
    except Exception as e:
        print("[neon] sd:", repr(e))
        return False


def is_eio(e):
    return isinstance(e, OSError) and bool(e.args) and e.args[0] == EIO


def failed(e):
    """Call with any exception from a card operation. An EIO remounts the card
    (not twice within GAP_MS); returns True if it did, so the caller can retry."""
    global remounts, _last
    if not is_eio(e):
        return False
    now = time.ticks_ms()
    if _last is not None and time.ticks_diff(now, _last) < GAP_MS:
        return False
    _last = now
    try:
        os.umount(SD)
    except OSError:
        pass
    ok = mount()
    remounts += 1
    print("[neon] sd: I/O error, remounted (%s), %d so far" % ("ok" if ok else "failed", remounts))
    return ok
