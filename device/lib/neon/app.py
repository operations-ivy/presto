"""The loop: touch -> the plus, screens, transitions, LEDs, backlight, services."""

import time

from neon import card, conf, fx, nav, services, theme

DEFAULTS = {
    "idle_seconds": 14400,  # 4 h with no touch: go to idle_screen
    "idle_screen": "photos",
    "photo_seconds": 300,  # a new photo every 5 minutes
    "units": "imperial",
    "clock_24h": False,
    "home_sky": "night",  # Home's sky: night always, or "daylight" (2049 haze from sunrise to sunset)
    "home_align": "left",  # Home's clock, date and forecast: left (room for the sightings) or center
    "sighting_minutes": 15,  # left-aligned Home: a rare sighting this often, on average; 0: none
    "arrow_pulse": "faint",  # Home's arrows: faint, chase, blink or breathe
    "utc_offset": -5,  # standard time, hours (the forecast's own offset wins once it's fetched)
    "dst": "us",  # us, eu or none
    "temp_offset_c": 0.0,  # the sensor stick reads warm next to the board: correct it here
    "chirp": False,
    "chirp_volume": 0.005,  # 0..1 of the piezo's duty: 0.005 is a soft tick
    "dark_lux": 3,  # below this: the backlight at its floor, and the screen's LEDs off
    "photo_feed": "https://presto.brick.nozdormu.cloud",  # the upload page; empty: /sd/gallery only
    "feed_minutes": 10,  # how often to look for new photos
    "brick_feed": "https://presto.brick.nozdormu.cloud/brick.json",  # the Brick screen's uplink; empty: none
    "ota_url": "https://presto.brick.nozdormu.cloud/device",  # updates over Wi-Fi (presto#9); empty: USB only
    "ota_minutes": 10,  # how often to look for one
    "arm_up": "weather",
    "arm_left": "photos",
    "arm_right": "brick",
    "arm_down": "lights",
    "ha_url": "http://brick2000:8123",  # Home Assistant, plain HTTP on the LAN (hue.py); empty: no lights
    "lights": "light.lamp",  # the light app's lights, comma-separated
    "leds_light": "light.lamp",  # the back LEDs follow this light (hue.py); empty: the screen's own
    "leds_floor": 1.0,  # their brightness at the light's lowest: full, they face the wall
}


RISE_MS = 300  # the HOME button's rise


class Ctx:
    pass


def run(presto, secrets):
    from neon import home, screens

    d = presto.display
    ctx = Ctx()
    ctx.presto, ctx.display = presto, d
    ctx.secrets = secrets
    ctx.settings = s = conf.load("/settings.yaml", DEFAULTS)
    ctx.fahrenheit = s["units"] == "imperial"
    ctx.pens = theme.Pens(d)
    d.set_font("bitmap8")
    ctx.net = services.Net(secrets)
    ctx.net.start()
    ctx.clock = services.Clock(s["utc_offset"], None if s["dst"] == "none" else s["dst"])
    ctx.weather = services.Weather(secrets, ctx.fahrenheit)
    ctx.room = services.Room(ctx.fahrenheit, s["temp_offset_c"])
    chirp = services.Chirp(s["chirp"], s["chirp_volume"])
    card.mount()
    from neon import photosync

    ctx.feed = photosync.Feed(s["photo_feed"], s["feed_minutes"] * 60)
    ctx.last_touch = time.ticks_ms()
    from neon import hue, ota

    entities = [e.strip() for e in str(s["lights"] or "").split(",") if e.strip()]
    leds_light = str(s["leds_light"] or "").strip()
    if leds_light and leds_light not in entities:
        entities.append(leds_light)
    ctx.hue = hue.Hue(s["ha_url"], getattr(secrets, "HA_TOKEN", ""), entities)
    ctx.lamp_rgb = None  # the back LEDs' colour while they follow the light; None: the screen's own
    leds_floor = s["leds_floor"]
    ctx.ota_shown = False

    def ota_show(frac):
        """The update's LED show (ota.leds), bright; not in a dark room: updates come at night too."""
        ctx.ota_shown = True
        if not ctx.dark:
            for i, rgb in enumerate(ota.leds(frac, time.ticks_ms())):
                presto.set_led_rgb(i, *rgb)

    ctx.ota = ota.Updater(s["ota_url"], s["ota_minutes"] * 60, show=ota_show)
    services.log("running {} ({})".format(ctx.ota.version or "unknown", ctx.ota.state.get("status", "no update yet")))
    ctx.dark = False

    apps = {c.name: c(ctx) for c in (screens.Weather, screens.Photos, screens.Brick, screens.Fun, screens.Lights)}
    ctx.arms = {"up": s["arm_up"], "left": s["arm_left"], "right": s["arm_right"], "down": s["arm_down"]}
    screens_by_name = dict(apps)
    screens_by_name[nav.HOME] = home.Home(ctx)
    plus = nav.Plus(ctx.arms)

    touch = presto.touch
    cur = screens_by_name[nav.HOME]
    cur.enter()
    last_touch = time.ticks_ms()
    down = None  # (x0, y0, t0, last_x, last_y, two)
    last_frame = time.ticks_ms()  # the first frame is drawn anyway: cur starts dirty
    last_light = 0
    frames, fps_at = 0, time.ticks_ms()
    tab_up = False  # the hidden HOME button is showing

    def switch(name):
        nonlocal cur
        t0 = time.ticks_ms()
        plus.go(name)
        cur.exit()
        cur = screens_by_name[name]
        cur.enter()
        for cover in fx.static_frames():  # the old screen dissolves into static; the new one tunes in
            if cover < 1.0:
                draw(time.ticks_ms())
            fx.draw_static(d, ctx.pens, cover)
            presto.update()
        draw(time.ticks_ms())
        presto.update()
        cur.dirty = False
        if ctx.lamp_rgb is None and not ctx.dark:
            cur.leds(presto, time.ticks_ms())
        services.log("to %s in %d ms" % (name, time.ticks_diff(time.ticks_ms(), t0)))

    def draw(now_ms):
        cur.draw(d, now_ms)
        tab, age = plus.home_tab(), plus.reveal_age(now_ms)
        if tab is not None and age is not None:  # the hidden HOME button, raised by a tap
            # it rises out of its edge; a screen that only redraws on change shows it up at once
            rise = 1.0 if cur.frame_ms is None else min(1.0, age / RISE_MS)
            fx.home_tab(d, ctx.pens, tab, now_ms, rise)

    from neon import watchdog

    dog = watchdog.Watchdog()
    dog.start()  # restarts the Presto if this loop stops for 45 s
    try:
        while True:
            now = time.ticks_ms()
            dog.beat()
            chirp.tick()
            touch.poll()

            # --- touch ---
            if touch.state:
                if down is None:
                    down = [touch.x, touch.y, now, touch.x, touch.y, False]
                else:
                    down[3], down[4] = touch.x, touch.y
                if touch.state2:
                    down[5] = True
                last_touch = ctx.last_touch = now
            elif down is not None:
                g = nav.classify(down[3] - down[0], down[4] - down[1], time.ticks_diff(now, down[2]), down[5])
                x0, y0 = down[0], down[1]
                down = None
                if g == "two":
                    if plus.current != nav.HOME:
                        chirp.go_home()
                        switch(nav.HOME)
                elif g == "tap":
                    before = plus.current
                    target = plus.tap(x0, y0, now)
                    if target is None:
                        cur.tap(x0, y0)  # not a navigation band: the app's own tap
                    elif target == nav.REVEAL:
                        tab_up = True  # the HOME button rises; a second tap there goes Home
                        cur.dirty = True
                    else:
                        plus.current = before  # switch() moves it
                        chirp.go_home() if target == nav.HOME else chirp.enter_app()
                        switch(target)
                # swipes do nothing: navigation is by taps (the user, 2026-11-03)
                continue  # react to touches before drawing

            # --- idle: back to the resting screen ---
            idle = s["idle_screen"]
            if (
                time.ticks_diff(now, last_touch) > s["idle_seconds"] * 1000
                and plus.current != idle
                and idle in screens_by_name
            ):
                switch(idle)
                last_touch = now

            # --- services (between frames) ---
            ctx.net.tick(now)
            ctx.clock.sync(ctx.net)
            if ctx.weather.due(ctx.net):
                ctx.weather.fetch(ctx.clock)
                cur.dirty = True
            ctx.room.read()
            ctx.feed.step(ctx.net, last_touch)
            ctx.ota.step(ctx.net, last_touch, now)
            if ctx.ota_shown:  # a download that didn't end in a restart: the LEDs come back
                ctx.ota_shown, ctx.lamp_rgb = False, None
                if not ctx.dark:
                    cur.leds(presto, now)
            if time.ticks_diff(now, last_light) > 1000:
                last_light = now
                _light(ctx, presto, cur, now)
            if leds_light:
                _follow_lamp(ctx, presto, cur, now, leds_light, leds_floor)

            # --- frame ---
            if tab_up and not plus.revealed(now):  # the HOME button has hidden again
                tab_up = False
                cur.dirty = True
            if hasattr(cur, "tick"):
                cur.tick(now)
            due = cur.frame_ms is not None and time.ticks_diff(now, last_frame) >= cur.frame_ms
            if due or cur.dirty:
                draw(now)
                presto.update()
                cur.dirty = False
                last_frame = now
                frames += 1
                if cur.name == "home" and ctx.lamp_rgb is None and not ctx.dark:
                    cur.leds(presto, now)
            if time.ticks_diff(now, fps_at) > 10000:
                print(f"[neon] {cur.name} {frames * 1000 / time.ticks_diff(now, fps_at):.1f} fps")
                frames, fps_at = 0, now
            time.sleep_ms(5)
    finally:
        dog.stop()  # a crash screen, a REPL or a USB deploy: no restarts behind its back


def _light(ctx, presto, cur, now):
    lux = ctx.room.lux
    if lux is None:
        return
    dark = ctx.dark = lux < ctx.settings["dark_lux"]
    presto.set_backlight(0.12 if dark else max(0.25, min(1.0, lux / 150)))
    if ctx.lamp_rgb is not None:
        return  # the LEDs follow the lamp
    if dark:
        _fill(presto, (0, 0, 0))
    elif cur.name != "home":
        cur.leds(presto, now)


def _follow_lamp(ctx, presto, cur, now, light, floor):
    """The back LEDs: the lamp's colour (dark with it) whenever its state is known; not
    dimmed in a dark room, which its night light would make one. Unknown: the screen's own."""
    ctx.hue.step(ctx.net, now)
    rgb = ctx.hue.led_rgb(light, floor)
    if rgb == ctx.lamp_rgb:
        return
    ctx.lamp_rgb = rgb
    if rgb is not None:
        _fill(presto, rgb)
    elif not ctx.dark:
        cur.leds(presto, now)


def _fill(presto, rgb):
    for i in range(7):
        presto.set_led_rgb(i, *rgb)
