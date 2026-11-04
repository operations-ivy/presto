"""The four arms: Weather (up), Photos (left), Brick (right), and the fun one (down)."""

import gc
import os
import random
import time

from neon import fx, theme, wx

W = H = 480


class Screen:
    name = "?"
    label = "?"
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

    name, label = "weather", "WEATHER"
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
            d.rectangle(0, i * 48, W, 48)
        fx.centered(d, "OUTSIDE", 26, 3, p.text)
        if s and s.get("now") is not None:
            t = "%d" % round(s["now"])
            sc = 14
            x = (W - d.measure_text(t, sc)) // 2
            fx.neon_text(d, p, t, x, 64, sc, p.text, p.rgb(120, 50, 20) if warm else p.rgb(10, 60, 80))
            fx.centered(d, wx.line(s, "") + "   " + wx.LABELS.get(s["kind"], ""), 190, 2, p.text)
        else:
            fx.centered(d, "NO FORECAST" if ctx.weather.configured else "SET LATITUDE/LONGITUDE", 110, 3, p.dim)
            fx.centered(d, "in secrets.py on the Presto", 150, 2, p.faint)
        self._holo(d, p, now_ms)

    def _holo(self, d, p, now_ms):
        room = self.ctx.room
        x0, y0, w, h = 30, 240, 420, 200
        flick = random.random() < 0.06
        edge = p.cyan if not flick else p.magenta
        d.set_pen(p.rgb(8, 14, 30))
        d.rectangle(x0, y0, w, h)
        d.set_pen(edge)
        for (a, b, c, e) in ((x0, y0, w, 2), (x0, y0 + h - 2, w, 2), (x0, y0, 2, h), (x0 + w - 2, y0, 2, h)):
            d.rectangle(a, b, c, e)
        d.set_pen(p.rgb(20, 40, 70))
        off = (now_ms // 60) % 6
        for yy in range(y0 + 4 + off, y0 + h - 2, 6):  # drifting hologram scanlines
            d.line(x0 + 2, yy, x0 + w - 3, yy)
        d.set_pen(p.magenta)
        d.text("ROOM", x0 + 14, y0 + 12, 400, 2)
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
            yy = y0 + 42 + i * 36
            d.set_pen(p.dim)
            d.text(k, x0 + 14, yy + 6, 200, 2)
            d.set_pen(p.text if not flick else p.cyan)
            d.text(v, x0 + 200, yy, 220, 4)


class Photos(Screen):
    """The upload page's photos (raw RGB565 frames synced to /sd/feed), else the JPEGs
    in /sd/gallery. Tap right half: next; left half: previous."""

    name, label = "photos", "PHOTOS"
    frame_ms = None
    led_hue = None
    REPORT_AFTER_MS = 20000  # tell the page once a photo has stayed up this long

    def __init__(self, ctx):
        super().__init__(ctx)
        import jpegdec

        self.jpeg = jpegdec.JPEG(ctx.display)
        self.files = []  # (kind, path, name)
        self.i = 0
        self.shown_at = 0
        self.seen = -1  # the feed's version this list was made from
        self.reported = None
        self.frame = bytearray(480 * 480 * 2)  # the photo on screen (PSRAM has room)
        self.held = None
        self.rescan()

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

    def tap(self, x, y):
        if self.files:
            self.i = (self.i + (1 if x > W // 2 else -1)) % len(self.files)
            self.dirty = True

    def tick(self, now_ms):
        feed = self.ctx.feed
        if feed.version != self.seen:
            before = self.files[self.i][2] if self.files else None
            self.rescan()
            if (self.files[self.i][2] if self.files else None) != before or len(self.files) == 1:
                self.dirty = True
        if self.files and time.ticks_diff(now_ms, self.shown_at) > self.ctx.settings["photo_seconds"] * 1000:
            self.i = (self.i + 1) % len(self.files)
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

    def draw(self, d, now_ms):
        p = self.ctx.pens
        self.shown_at = now_ms
        if not self.files:
            d.set_pen(p.night)
            d.clear()
            fx.centered(d, "NO PHOTOS", 200, 4, p.dim)
            fx.centered(d, "add some on the upload page", 250, 2, p.faint)
            return
        kind, path, name = self.files[self.i]
        try:
            if kind == "raw":  # the frame as the screen holds it: no decoding
                if self.held != path:  # read from the card once per photo (it's ~0.5 s);
                    with open(path, "rb") as f:  # every redraw after that (transitions,
                        f.readinto(self.frame)  # the HOME tab) is a memory copy
                    self.held = path
                memoryview(d)[:] = self.frame
            else:
                d.set_pen(p.night)
                d.clear()
                import jpegdec

                self.jpeg.open_file(path)
                w, h = self.jpeg.get_width(), self.jpeg.get_height()
                self.jpeg.decode(max(0, (W - w) // 2), max(0, (H - h) // 2), jpegdec.JPEG_SCALE_FULL, dither=True)
        except Exception as e:
            self.held = None
            d.set_pen(p.night)
            d.clear()
            fx.centered(d, "CAN'T SHOW " + name[:20], 200, 2, p.red)
            fx.centered(d, repr(e)[:40], 230, 2, p.faint)
        if kind == "raw" and self.ctx.feed.stale:
            d.set_pen(p.night)
            d.rectangle(W - 104, H - 20, 104, 20)
            d.set_pen(p.faint)
            d.text("FEED STALE", W - 98, H - 16, 200, 2)


class Brick(Screen):
    """Matrix terminal. The cluster feed isn't built yet: the columns are decorative and
    the panel shows the Presto itself. Passive only: never beeps or takes over."""

    name, label = "brick", "BRICK"
    frame_ms = 90
    led_hue = 0.38

    def __init__(self, ctx):
        super().__init__(ctx)
        self.rain = fx.GlyphRain(cols=16, scale=2, top=0, bottom=H, length=(5, 10))
        self.typed = 0
        self.info, self.info_at = None, 0

    def enter(self):
        super().enter()
        self.typed = 0

    def draw(self, d, now_ms):
        ctx, p = self.ctx, self.ctx.pens
        d.set_pen(p.night)
        d.clear()
        self.rain.step_draw(d, p)
        x0, y0, w, h = 50, 110, 380, 260
        d.set_pen(p.rgb(2, 10, 6))
        d.rectangle(x0, y0, w, h)
        d.set_pen(p.phosphor)
        d.rectangle(x0, y0, w, 2)
        d.rectangle(x0, y0 + h - 2, w, 2)
        if self.info is None or time.ticks_diff(now_ms, self.info_at) > 3000:
            # gc.collect() and the SD card's statvfs are slow: once every 3 s, not every frame
            gc.collect()
            try:
                s = os.statvfs("/sd")
                sd = "%d MB FREE" % (s[0] * s[3] // 1048576)
            except OSError:
                sd = "NOT MOUNTED"
            self.info, self.info_at = (ctx.net.info(), gc.mem_free() // 1024, sd), now_ms
        net, ram, sd = self.info
        lines = [
            "> BRICK UPLINK",
            "  FEED  ..... NOT BUILT YET",
            "> THIS NODE",
            "  HOST  %s" % net["host"],
            "  IP    %s" % (net["ip"] or "NO SIGNAL"),
            "  RSSI  %s" % ("%d dBm" % net["rssi"] if net["rssi"] is not None else "--"),
            "  RAM   %d KB FREE" % ram,
            "  SD    %s" % sd,
            "  UP    %d MIN" % (time.ticks_ms() // 60000),
        ]
        self.typed = min(sum(len(x) for x in lines), self.typed + 14)  # text decodes into place
        left = self.typed
        for i, line in enumerate(lines):
            shown = line[: max(0, left)]
            left -= len(line)
            if left < 0 and len(shown) < len(line) and shown:
                shown = shown[:-1] + random.choice(fx.GLYPHS)
            d.set_pen(p.phosphor if line.startswith(">") else p.text)
            d.text(shown, x0 + 14, y0 + 14 + i * 27, w - 20, 2)
        if (now_ms // 500) % 2:
            d.set_pen(p.phosphor)
            d.rectangle(x0 + 14, y0 + h - 22, 10, 14)


class Fun(Screen):
    """Signal lost: the fun one isn't decided yet."""

    name, label = "fun", "???"
    frame_ms = 120
    led_hue = 0.78

    def draw(self, d, now_ms):
        p = self.ctx.pens
        d.set_pen(p.night)
        d.clear()
        for _ in range(26):  # static: random bars of noise
            y = random.randint(0, H - 4)
            d.set_pen(random.choice((p.line, p.faint, p.panel, p.violet)))
            d.rectangle(random.randint(-50, W - 40), y, random.randint(40, 260), random.randint(1, 4))
        jitter = random.randint(-3, 3) if random.random() < 0.3 else 0
        if random.random() < 0.15:
            fx.glitch_text(d, p, "SIGNAL LOST", 70 + jitter, 200, 5, 4)
        else:
            fx.centered(d, "SIGNAL LOST", 200, 5, p.violet)
        fx.centered(d, "the fun one: ideas wanted", 260, 2, p.dim)
