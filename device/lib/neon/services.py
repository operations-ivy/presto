"""Wi-Fi, time, forecast and the room's sensors. Nothing here ever raises into the UI."""

import time

import machine
import network

from neon import retry, tz, wx


def log(*a):
    print("[neon]", *a)


class Net:
    """Wi-Fi. start() at boot; tick() every pass brings it back when it's down
    (presto#16: a failed boot connect used to mean offline until a restart)."""

    def __init__(self, secrets):
        self.secrets = secrets
        self.wlan = network.WLAN(network.STA_IF)
        self.started = False
        self.backoff = retry.Backoff()
        self.was_up = False

    def start(self):
        try:
            self.wlan.active(True)
            if not self.wlan.isconnected():
                self.wlan.connect(self.secrets.WIFI_SSID, self.secrets.WIFI_PASSWORD)
            self.started = True
        except Exception as e:
            log("wifi:", repr(e))

    def _status(self):
        try:
            return self.wlan.status()
        except Exception as e:
            return repr(e)

    def tick(self, now_ms):
        """Call once per pass of the main loop. connect() doesn't block (the radio
        joins in the background), so the screen keeps running."""
        up = self.up
        down_ms = self.backoff.down_ms(now_ms)
        if up and not self.was_up and down_ms is not None:
            log("wifi: up after %d s down, %d retries" % (down_ms // 1000, self.backoff.attempts))
        self.was_up = up
        if not self.backoff.step(up, now_ms):
            return
        # status: 1 joining, -1 failed, -2 no AP found, -3 wrong password, 0 idle
        log("wifi: down %d s (status %s), retry %d" % (self.backoff.down_ms(now_ms) // 1000, self._status(),
                                                      self.backoff.attempts))
        try:
            # Power the radio down and up: after a failed boot join it answers every
            # connect() with status -1 (seen 2026-11-04); disconnect() alone didn't clear it.
            self.wlan.active(False)
            time.sleep_ms(200)
            self.wlan.active(True)
            self.wlan.connect(self.secrets.WIFI_SSID, self.secrets.WIFI_PASSWORD)
            self.started = True
        except Exception as e:
            log("wifi:", repr(e))

    @property
    def up(self):
        try:
            return self.wlan.isconnected()
        except Exception:
            return False

    def info(self):
        if not self.up:
            return {"host": network.hostname(), "ip": None, "rssi": None, "status": self.wlan.status()}
        return {"host": network.hostname(), "ip": self.wlan.ifconfig()[0], "rssi": self.wlan.status("rssi")}


class Clock:
    """UTC from NTP; local time from the forecast's offset, else settings' offset + DST rule."""

    RESYNC_S = 6 * 3600

    def __init__(self, std_hours, rule):
        self.std_hours, self.rule = std_hours, rule
        self.override = None  # seconds, from Open-Meteo
        self.synced_at = None

    def sync(self, net):
        if not net.up:
            return
        if self.synced_at is not None and time.time() - self.synced_at < self.RESYNC_S:
            return
        try:
            import ntptime

            ntptime.settime()
            self.synced_at = time.time()
            log("ntp ok")
        except Exception as e:
            log("ntp:", repr(e))

    @property
    def valid(self):
        return self.synced_at is not None

    def now(self):
        utc = time.time()
        return tz.local(utc, tz.offset_seconds(utc, self.std_hours, self.rule, self.override))


class Weather:
    EVERY_S = 10 * 60  # a fresher temperature and sky on the desk (was 30 min; the user, 2026-11-04)
    RETRY_S = 5 * 60

    def __init__(self, secrets, fahrenheit=True):
        self.lat = getattr(secrets, "LATITUDE", None)
        self.lon = getattr(secrets, "LONGITUDE", None)
        self.fahrenheit = fahrenheit
        self.summary = None
        self.next_at = 0

    @property
    def configured(self):
        return self.lat is not None and self.lon is not None

    def due(self, net):
        return self.configured and net.up and time.ticks_diff(time.ticks_ms(), self.next_at) >= 0

    def fetch(self, clock):
        """Blocking for a second or two, once per EVERY_S."""
        try:
            import requests

            r = requests.get(wx.url(self.lat, self.lon, self.fahrenheit), timeout=8)
            try:
                self.summary = wx.summarize(r.json())
            finally:
                r.close()
            clock.override = self.summary.get("utc_offset")
            self.next_at = time.ticks_add(time.ticks_ms(), self.EVERY_S * 1000)
            log("weather", self.summary)
        except Exception as e:
            log("weather:", repr(e))
            self.next_at = time.ticks_add(time.ticks_ms(), self.RETRY_S * 1000)

    @property
    def kind(self):
        return self.summary["kind"] if self.summary else "unknown"


class Room:
    """BME280 (temp, humidity, pressure) and LTR-559 (lux, proximity) on the sensor stick."""

    def __init__(self, fahrenheit=True, temp_offset=0.0):
        self.fahrenheit = fahrenheit
        self.temp_offset = temp_offset
        self.bme = self.ltr = None
        i2c = machine.I2C()
        try:
            from breakout_bme280 import BreakoutBME280

            self.bme = BreakoutBME280(i2c)
        except Exception as e:
            log("bme280:", repr(e))
        try:
            from breakout_ltr559 import BreakoutLTR559

            self.ltr = BreakoutLTR559(i2c)
            self._LUX, self._PROX = BreakoutLTR559.LUX, BreakoutLTR559.PROXIMITY
        except Exception as e:
            log("ltr559:", repr(e))
        self.temp = self.humidity = self.pressure = self.lux = self.prox = None
        self.temp_f = None  # always Fahrenheit, for /room.json and /metrics (roomhttp.py)
        self.read_at = None  # ticks_ms of the last good BME280 read
        self.reads = {"ok": 0, "error": 0}
        self._last = None  # never read yet (not a fake old tick: ticks wrap, README)

    def read(self, every_ms=3000):
        now = time.ticks_ms()
        if self._last is not None and time.ticks_diff(now, self._last) < every_ms:
            return
        self._last = now
        if self.bme is not None:
            try:
                c, p, rh = self.bme.read()
                c += self.temp_offset
                self.temp_f = c * 9 / 5 + 32
                self.temp = self.temp_f if self.fahrenheit else c
                self.humidity, self.pressure = rh, p / 100
                self.read_at = now
                self.reads["ok"] += 1
            except Exception as e:
                self.reads["error"] += 1
                log("bme read:", repr(e))
        self.read_light()

    def read_light(self):
        if self.ltr is None:
            return
        try:
            r = self.ltr.get_reading()
            if r is not None:
                self.lux, self.prox = r[self._LUX], r[self._PROX]
        except Exception as e:
            log("ltr read:", repr(e))


class Chirp:
    """Soft chimes on the piezo (pin 43).

    A piezo's loudness is its PWM duty, so volume is a tiny duty, and each note
    has a bell envelope: a quick fade in, then a long fade out. A hardware timer
    steps the envelope every 4 ms, so it stays smooth while the screen draws.
    """

    STEP_MS = 4
    ATTACK_MS = 12

    def __init__(self, enabled=True, volume=0.005):
        self.peak = max(0, min(65535, int(65535 * volume)))
        self.pwm = None
        self.notes = []  # [(freq, ms)] still to play
        self.t = 0
        self.freq = self.ms = 0
        self.timer = None
        if enabled and self.peak:
            try:
                self.pwm = machine.PWM(machine.Pin(43))
                self.pwm.duty_u16(0)
                self.timer = machine.Timer()
            except Exception as e:
                log("buzzer:", repr(e))

    def play(self, *notes):
        """play((freq, ms), (freq, ms), ...): queued, back to back."""
        if self.pwm is None:
            return
        self.notes = list(notes)
        self.freq = 0
        self.timer.init(period=self.STEP_MS, mode=machine.Timer.PERIODIC, callback=self._step)

    def _step(self, _t):
        if not self.freq:
            if not self.notes:
                self.pwm.duty_u16(0)
                self.timer.deinit()
                return
            self.freq, self.ms = self.notes.pop(0)
            self.t = 0
            self.pwm.freq(self.freq)
        self.t += self.STEP_MS
        if self.t < self.ATTACK_MS:
            level = self.t / self.ATTACK_MS
        else:
            left = 1 - (self.t - self.ATTACK_MS) / max(1, self.ms - self.ATTACK_MS)
            level = left * left  # a bell's decay: fast, then a long tail
        if self.t >= self.ms or level <= 0:
            self.pwm.duty_u16(0)
            self.freq = 0
            return
        self.pwm.duty_u16(int(self.peak * level))

    def tick(self):
        pass  # the timer does the work

    # The house sounds: soft, short, never urgent.
    def enter_app(self):
        self.play((1047, 140), (1568, 220))  # C6 -> G6, a rising fifth

    def go_home(self):
        self.play((1568, 140), (1047, 240))  # G6 -> C6

    def bump(self):
        self.play((523, 160))  # one low C5, barely there
