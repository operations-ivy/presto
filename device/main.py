# presto: Home in the middle of a plus, four apps on its arms (operations-ivy/presto).
# Pimoroni's examples and menu live in /examples on the flash.
import network

try:  # install, or roll back, a Wi-Fi update (presto#9); never fatal
    import ota_boot

    _ota = ota_boot.boot()  # install a downloaded update, guard a trial: before the display
except Exception as e:
    print("[ota] guard:", repr(e))
    _ota = None

network.hostname("presto")  # before Presto() makes the WLAN, so DHCP sends our name

import secrets  # noqa: E402  WIFI_SSID, WIFI_PASSWORD; optional LATITUDE, LONGITUDE, SIGNS

from presto import Presto  # noqa: E402

# 240x240, doubled to the panel: 4x fewer pixels to push than full_res, so screens
# switch in ~0.2 s instead of ~0.8 s (measured 2026-11-04). One layer: no compositing.
presto = Presto(full_res=False, layers=1)

try:
    from neon import app

    app.run(presto, secrets)
except Exception as e:  # show the crash on screen instead of a frozen frame
    import sys

    d = presto.display
    d.set_pen(d.create_pen(7, 5, 15))
    d.clear()
    d.set_pen(d.create_pen(255, 56, 96))
    d.set_font("bitmap8")
    d.text("NEON CRASHED", 10, 10, 220, 2)
    d.set_pen(d.create_pen(231, 227, 255))
    d.text(repr(e), 10, 40, 220, 1)
    d.text("REPL: Thonny or mpremote. Replug restarts.", 10, 220, 220, 1)
    presto.update()
    sys.print_exception(e)
    if _ota and _ota.get("status") == "trial":  # a new update crashed: restart, so ota_boot can roll it back
        import time

        import machine

        d.text("NEW UPDATE: RESTARTING TO ROLL BACK", 10, 200, 220, 1)
        presto.update()
        time.sleep(8)
        try:
            w = network.WLAN(network.STA_IF)
            w.disconnect()
            time.sleep(2)
            w.active(False)
        except Exception:  # best effort: it resets either way
            pass
        machine.reset()
    raise
