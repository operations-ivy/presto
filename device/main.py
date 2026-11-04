# presto: Home in the middle of a plus, four apps on its arms (operations-ivy/presto).
# Pimoroni's examples and menu live in /examples on the flash.
import network

network.hostname("presto")  # before Presto() makes the WLAN, so DHCP sends our name

import secrets  # noqa: E402  WIFI_SSID, WIFI_PASSWORD; optional LATITUDE, LONGITUDE, SIGNS

from presto import Presto  # noqa: E402

presto = Presto(full_res=True)

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
    d.text("NEON CRASHED", 20, 20, 440, 4)
    d.set_pen(d.create_pen(231, 227, 255))
    d.text(repr(e), 20, 80, 440, 2)
    d.text("REPL: Thonny or mpremote. Replug restarts.", 20, 440, 440, 2)
    presto.update()
    sys.print_exception(e)
    raise
