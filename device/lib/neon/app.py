"""The loop: touch -> the plus, screens, transitions, LEDs, backlight, services."""

import time

from neon import conf, fx, nav, services, theme

DEFAULTS = {
    "idle_seconds": 300,  # no touch for this long: go to idle_screen
    "idle_screen": "photos",
    "photo_seconds": 300,  # a new photo every 5 minutes
    "units": "imperial",
    "clock_24h": False,
    "utc_offset": -5,  # standard time, hours (the forecast's own offset wins once it's fetched)
    "dst": "us",  # us, eu or none
    "temp_offset_c": 0.0,  # the sensor stick reads warm next to the board: correct it here
    "chirp": False,
    "chirp_volume": 0.005,  # 0..1 of the piezo's duty: 0.005 is a soft tick
    "dark_lux": 3,  # below this: LEDs off, backlight at its floor
    "arm_up": "weather",
    "arm_left": "photos",
    "arm_right": "brick",
    "arm_down": "fun",
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
    _mount_sd()

    apps = {c.name: c(ctx) for c in (screens.Weather, screens.Photos, screens.Brick, screens.Fun)}
    ctx.arms = {"up": s["arm_up"], "left": s["arm_left"], "right": s["arm_right"], "down": s["arm_down"]}
    ctx.labels = {n: a.label for n, a in apps.items()}
    screens_by_name = dict(apps)
    screens_by_name[nav.HOME] = home.Home(ctx)
    plus = nav.Plus(ctx.arms)

    touch = presto.touch
    cur = screens_by_name[nav.HOME]
    cur.enter()
    last_touch = time.ticks_ms()
    down = None  # (x0, y0, t0, last_x, last_y, two)
    last_frame = -10**9
    last_light = 0
    frames, fps_at = 0, time.ticks_ms()
    tab_up = False  # the hidden HOME button is showing

    def switch(name):
        nonlocal cur
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
        cur.leds(presto, time.ticks_ms())

    def draw(now_ms):
        cur.draw(d, now_ms)
        tab, age = plus.home_tab(), plus.reveal_age(now_ms)
        if tab is not None and age is not None:  # the hidden HOME button, raised by a tap
            # it rises out of its edge; a screen that only redraws on change shows it up at once
            rise = 1.0 if cur.frame_ms is None else min(1.0, age / RISE_MS)
            fx.home_tab(d, ctx.pens, tab, now_ms, rise)

    while True:
        now = time.ticks_ms()
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
            last_touch = now
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
        ctx.clock.sync(ctx.net)
        if ctx.weather.due(ctx.net):
            ctx.weather.fetch(ctx.clock)
            cur.dirty = True
        ctx.room.read()
        if time.ticks_diff(now, last_light) > 1000:
            last_light = now
            _light(ctx, presto, cur, now)

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
            if cur.name == "home":
                cur.leds(presto, now)
        if time.ticks_diff(now, fps_at) > 10000:
            print("[neon] %s %.1f fps" % (cur.name, frames * 1000 / time.ticks_diff(now, fps_at)))
            frames, fps_at = 0, now
        time.sleep_ms(5)


def _light(ctx, presto, cur, now):
    lux = ctx.room.lux
    if lux is None:
        return
    dark = lux < ctx.settings["dark_lux"]
    presto.set_backlight(0.12 if dark else max(0.25, min(1.0, lux / 150)))
    if dark:
        for i in range(7):
            presto.set_led_rgb(i, 0, 0, 0)
    elif cur.name != "home":
        cur.leds(presto, now)


def _mount_sd():
    import os

    import machine
    import sdcard

    try:
        spi = machine.SPI(0, sck=machine.Pin(34), mosi=machine.Pin(35), miso=machine.Pin(36))
        os.mount(sdcard.SDCard(spi, machine.Pin(39)), "/sd")
    except Exception as e:
        services.log("sd:", repr(e))
