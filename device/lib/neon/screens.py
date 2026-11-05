"""The four arms: Weather (up), Photos (left), Brick (right), and the fun one (down)."""

import gc
import os
import random
import time

from neon import fx, theme, uplink, wx

W = H = 240


class Screen:
    name = "?"
    frame_ms = 200  # how often draw() runs; None = only when dirty
    led_hue = None  # hue for the back LEDs, or None for off

    def __init__(self, ctx):
        self.ctx = ctx
        self.dirty = True

    def enter(self):
        self.dirty = True

    def exit(self):
        pass

    def tap(self, x, y):
        pass

    def leds(self, presto, t):
        for i in range(7):
            if self.led_hue is None:
                presto.set_led_rgb(i, 0, 0, 0)
            else:
                presto.set_led_hsv(i, self.led_hue, 1.0, 0.12)

    def draw(self, d, now_ms):
        raise NotImplementedError


class Weather(Screen):
    """2049 haze: warm orange when it's hot, cold teal when it's wet; the room as holograms."""

    name = "weather"
    frame_ms = 160
    led_hue = 0.88

    def leds(self, presto, t):
        for i in range(7):  # hologram shimmer: pink and blue chasing
            presto.set_led_hsv(i, 0.6 + 0.3 * (((t // 120) + i) % 2), 0.8, 0.1)

    def draw(self, d, now_ms):
        ctx, p = self.ctx, self.ctx.pens
        s = ctx.weather.summary
        warm = s is not None and (s.get("now") or 0) >= (68 if ctx.fahrenheit else 20)
        top = theme.PALETTE["dust"] if warm else (6, 70, 90)
        for i in range(10):
            d.set_pen(p.rgb(*theme.mix(theme.PALETTE["night"], top, (9 - i) / 14)))
            d.rectangle(0, i * 24, W, 24)
        fx.centered(d, "OUTSIDE", 12, 2, p.text)
        if s and s.get("now") is not None:
            t = "%d" % round(s["now"])
            sc = 7
            x = (W - d.measure_text(t, sc)) // 2
            fx.neon_text(d, p, t, x, 32, sc, p.text, p.rgb(120, 50, 20) if warm else p.rgb(10, 60, 80))
            fx.centered(d, wx.line(s, "") + "   " + wx.LABELS.get(s["kind"], ""), 95, 1, p.text)
        else:
            fx.centered(d, "NO FORECAST" if ctx.weather.configured else "SET LATITUDE/LONGITUDE", 55, 2, p.dim)
            fx.centered(d, "in secrets.py on the Presto", 75, 1, p.faint)
        self._holo(d, p, now_ms)

    def _holo(self, d, p, now_ms):
        room = self.ctx.room
        x0, y0, w, h = 15, 120, 210, 100
        flick = random.random() < 0.06
        edge = p.cyan if not flick else p.magenta
        d.set_pen(p.rgb(8, 14, 30))
        d.rectangle(x0, y0, w, h)
        d.set_pen(edge)
        for (a, b, c, e) in ((x0, y0, w, 1), (x0, y0 + h - 1, w, 1), (x0, y0, 1, h), (x0 + w - 1, y0, 1, h)):
            d.rectangle(a, b, c, e)
        d.set_pen(p.rgb(20, 40, 70))
        off = (now_ms // 60) % 3
        for yy in range(y0 + 2 + off, y0 + h - 1, 3):  # drifting hologram scanlines
            d.line(x0 + 1, yy, x0 + w - 2, yy)
        d.set_pen(p.magenta)
        d.text("ROOM", x0 + 7, y0 + 6, 200, 1)
        rows = []
        if room.temp is not None:
            rows.append(("TEMP", "%.1f" % room.temp))
            rows.append(("HUMIDITY", "%d%%" % round(room.humidity)))
            rows.append(("PRESSURE", "%d hPa" % round(room.pressure)))
        if room.lux is not None:
            rows.append(("LIGHT", "%d lux" % round(room.lux)))
        if not rows:
            rows.append(("SENSORS", "OFFLINE"))
        for i, (k, v) in enumerate(rows):
            yy = y0 + 21 + i * 18
            d.set_pen(p.dim)
            d.text(k, x0 + 7, yy + 3, 100, 1)
            d.set_pen(p.text if not flick else p.cyan)
            d.text(v, x0 + 100, yy, 110, 2)


class Photos(Screen):
    """The upload page's photos (240x240 RGB565 frames synced to /sd/feed), else the
    JPEGs in /sd/gallery. Tap right half: next; left half: previous.

    The photo on screen is held in RAM: every redraw (the switch's static, the HOME
    button) is a 7 ms copy, and the card is read only when the photo changes
    (a raw frame ~0.25 s; a JPEG decode ~2 s, once)."""

    name = "photos"
    frame_ms = None
    led_hue = None
    REPORT_AFTER_MS = 20000  # tell the page once a photo has stayed up this long
    FRAME = W * H * 2

    def __init__(self, ctx):
        super().__init__(ctx)
        import jpegdec

        self.jpeg = jpegdec.JPEG(ctx.display)
        self.files = []  # (kind, path, name)
        self.i = 0
        self.shown_at = 0
        self.seen = -1  # the feed's version this list was made from
        self.reported = None
        self.frame = bytearray(self.FRAME)
        self.held = None  # the path whose picture is in self.frame
        self.error = None
        self.rescan()
        self._hold()  # at boot, so the first visit is instant

    def rescan(self):
        feed = self.ctx.feed
        cur = self.files[self.i][2] if self.files else None
        self.seen = feed.version
        if feed.order:
            self.files = [("raw", feed.path(n), n) for n in feed.order]
        else:
            try:
                names = sorted(f for f in os.listdir("/sd/gallery") if f.lower().endswith((".jpg", ".jpeg")))
            except OSError:
                names = []
            self.files = [("jpeg", "/sd/gallery/" + n, n) for n in names]
        names = [f[2] for f in self.files]
        self.i = names.index(cur) if cur in names else 0  # stay on the same photo if it's still there

    def _hold(self):
        """Load the current photo into self.frame (the slow part, once per photo)."""
        if not self.files:
            self.held = None
            return
        kind, path, name = self.files[self.i]
        if self.held == path:
            return
        d = self.ctx.display
        t0 = time.ticks_ms()
        try:
            if kind == "raw":
                with open(path, "rb") as f:
                    if f.readinto(self.frame) != self.FRAME:
                        raise OSError("short frame")
            else:  # decode into the screen buffer once, then keep a copy
                d.set_pen(self.ctx.pens.night)
                d.clear()
                import jpegdec

                self.jpeg.open_file(path)
                w = self.jpeg.get_width()
                scale = jpegdec.JPEG_SCALE_HALF if w > W else jpegdec.JPEG_SCALE_FULL
                sw = w // 2 if w > W else w
                sh = self.jpeg.get_height() // 2 if w > W else self.jpeg.get_height()
                self.jpeg.decode(max(0, (W - sw) // 2), max(0, (H - sh) // 2), scale, dither=True)
                self.frame[:] = memoryview(d)
            self.held, self.error = path, None
        except Exception as e:
            self.held, self.error = None, repr(e)[:40]
        print("[neon] photo %s in %d ms" % (name, time.ticks_diff(time.ticks_ms(), t0)))

    def tap(self, x, y):
        if self.files:
            self.i = (self.i + (1 if x > W // 2 else -1)) % len(self.files)
            self.shown_at = time.ticks_ms()
            self._hold()
            self.dirty = True

    def tick(self, now_ms):
        feed = self.ctx.feed
        if feed.version != self.seen:
            before = self.files[self.i][1] if self.files else None
            self.rescan()
            if (self.files[self.i][1] if self.files else None) != before:
                self._hold()
                self.dirty = True
        if self.files and time.ticks_diff(now_ms, self.shown_at) > self.ctx.settings["photo_seconds"] * 1000:
            self.shown_at = now_ms
            if len(self.files) > 1:
                self.i = (self.i + 1) % len(self.files)
                self._hold()
                self.dirty = True
        if (
            self.files
            and self.files[self.i][0] == "raw"
            and self.reported != self.files[self.i][2]
            and time.ticks_diff(now_ms, self.shown_at) > self.REPORT_AFTER_MS
            and time.ticks_diff(now_ms, self.ctx.last_touch) > self.REPORT_AFTER_MS
            and self.ctx.net.up
        ):
            self.reported = self.files[self.i][2]
            feed.report(self.reported)

    def enter(self):
        super().enter()
        self.shown_at = time.ticks_ms()

    def draw(self, d, now_ms):
        p = self.ctx.pens
        if not self.files:
            d.set_pen(p.night)
            d.clear()
            fx.centered(d, "NO PHOTOS", 100, 2, p.dim)
            fx.centered(d, "add some on the upload page", 125, 1, p.faint)
            return
        if self.held is None:
            self._hold()
        if self.held is None:
            d.set_pen(p.night)
            d.clear()
            fx.centered(d, "CAN'T SHOW " + self.files[self.i][2][:20], 100, 1, p.red)
            fx.centered(d, self.error or "", 115, 1, p.faint)
            return
        memoryview(d)[:] = self.frame
        if self.files[self.i][0] == "raw" and self.ctx.feed.stale:
            d.set_pen(p.night)
            d.rectangle(W - 64, H - 11, 64, 11)
            d.set_pen(p.faint)
            d.text("FEED STALE", W - 61, H - 9, 100, 1)


class Brick(Screen):
    """Matrix terminal: the cluster's uplink (presto-feed, presto#5) on two tabs, CLUSTER and
    ROOMS; tap the screen to switch. A rain column per check stutters red while that check
    is unhappy. Passive only: never beeps, flashes or takes over."""

    name = "brick"
    frame_ms = 90
    led_hue = 0.38
    X0, Y0, PW, PH = 20, 22, 200, 204  # the panel; the rain shows either side
    PITCH = 11  # px per line

    def __init__(self, ctx):
        super().__init__(ctx)
        self.rain = fx.GlyphRain(cols=16, scale=1, top=0, bottom=H, length=(5, 10))
        self.uplink = uplink.Uplink(ctx.settings.get("brick_feed"))
        self.tab = 0
        self.typed = 0
        self.receiving = False
        self.info, self.info_at = None, 0

    def enter(self):
        super().enter()
        self.typed = 0
        self.uplink.entered(time.ticks_ms())

    def tap(self, x, y):
        self.tab = (self.tab + 1) % len(uplink.TABS)
        self.typed = 0  # the new page decodes into place

    def tick(self, now_ms):
        """The fetch and gc.collect() + statvfs (~200 ms) run here, in the main loop between
        frames, never in draw(). A fetch first shows RECEIVING for a frame: it stalls ~3 s."""
        if self.uplink.due(now_ms) and self.ctx.net.up:
            if not self.receiving:
                self.receiving = True  # draw RECEIVING first, fetch on the next pass
                return
            if self.uplink.fetch(now_ms):
                flags = uplink.health(self.uplink.data)
                for i in range(len(self.rain.cols)):
                    self.rain.set_health(i, flags[i % len(flags)] if flags else True)
            self.receiving = False
        if self.info is not None and time.ticks_diff(now_ms, self.info_at) <= 3000:
            return
        gc.collect()
        try:
            st = os.statvfs("/sd")
            sd = "%d MB FREE" % (st[0] * st[3] // 1048576)
        except OSError:
            sd = "NOT MOUNTED"
        self.info, self.info_at = (self.ctx.net.info(), gc.mem_free() // 1024, sd), now_ms

    def _this_node(self):
        if self.info is None:  # the first visit, before tick() has run
            net, ram, sd = {"host": "...", "ip": "...", "rssi": None}, 0, "..."
        else:
            net, ram, sd = self.info
        return [
            ("> THIS NODE", uplink.H),
            ("  HOST  %s" % net["host"], ""),
            ("  IP    %s" % (net["ip"] or "NO SIGNAL"), ""),
            ("  RSSI  %s" % ("%d dBm" % net["rssi"] if net["rssi"] is not None else "--"), ""),
            ("  RAM   %d KB FREE   SD %s" % (ram, sd), ""),
            ("  UP    %d MIN" % (time.ticks_ms() // 60000), ""),
        ]

    def draw(self, d, now_ms):
        ctx, p = self.ctx, self.ctx.pens
        d.set_pen(p.night)
        d.clear()
        self.rain.step_draw(d, p)
        x0, y0, w, h = self.X0, self.Y0, self.PW, self.PH
        d.set_pen(p.rgb(2, 10, 6))
        d.rectangle(x0, y0, w, h)
        d.set_pen(p.phosphor)
        d.rectangle(x0, y0, w, 1)
        d.rectangle(x0, y0 + h - 1, w, 1)
        # the tabs, above the panel: the current one lit
        tx = x0
        for i, t in enumerate(uplink.TABS):
            d.set_pen(p.phosphor if i == self.tab else p.phosphor_dim)
            d.text(("[%s]" if i == self.tab else " %s ") % t, tx, 8, W, 1)
            tx += (len(t) + 3) * 6
        data, age = self.uplink.data, self.uplink.age_s(now_ms)
        if self.receiving:
            d.set_pen(p.amber)
            d.text("RECEIVING", x0 + w - 54, 8, W, 1)
        elif uplink.stale(data, age):
            d.set_pen(p.red)
            d.text("STALE", x0 + w - 30, 8, W, 1)
        if self.tab == 0:
            lines = uplink.cluster(data)
        else:
            temp = ctx.room.temp
            lines = uplink.rooms(data, ctx.fahrenheit, temp) + [("", "")] + self._this_node()
        lines = lines[: (h - 10) // self.PITCH]
        self.typed = min(sum(len(t) for t, _ in lines), self.typed + 18)  # text decodes into place
        left = self.typed
        for i, (line, kind) in enumerate(lines):
            shown = line[: max(0, left)]
            left -= len(line)
            if left < 0 and len(shown) < len(line) and shown:
                shown = shown[:-1] + random.choice(fx.GLYPHS)
            d.set_pen(p.red if kind == uplink.BAD else p.phosphor if kind == uplink.H else p.text)
            d.text(shown, x0 + 6, y0 + 6 + i * self.PITCH, w - 8, 1)
        if (now_ms // 500) % 2:
            d.set_pen(p.phosphor)
            d.rectangle(x0 + 6, y0 + h - 10, 5, 7)


class Fun(Screen):
    """Signal lost: the fun one isn't decided yet."""

    name = "fun"
    frame_ms = 120
    led_hue = 0.78

    def draw(self, d, now_ms):
        p = self.ctx.pens
        d.set_pen(p.night)
        d.clear()
        for _ in range(26):  # static: random bars of noise
            y = random.randint(0, H - 2)
            d.set_pen(random.choice((p.line, p.faint, p.panel, p.violet)))
            d.rectangle(random.randint(-25, W - 20), y, random.randint(20, 130), random.randint(1, 2))
        jitter = random.randint(-2, 2) if random.random() < 0.3 else 0
        if random.random() < 0.15:
            fx.glitch_text(d, p, "SIGNAL LOST", (W - d.measure_text("SIGNAL LOST", 3)) // 2 + jitter, 100, 3, 2)
        else:
            fx.centered(d, "SIGNAL LOST", 100, 3, p.violet)
        fx.centered(d, "the fun one: ideas wanted", 130, 1, p.dim)
