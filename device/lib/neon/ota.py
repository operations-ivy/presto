"""Updates over Wi-Fi (presto#9): the Presto pulls its own code from the cluster.

Every EVERY_S (when nobody's touching the screen) it fetches the upload page's
/device/manifest.json. A new version means: fetch /device/bundle (one TLS
connect for every file), check each file's sha256 against the manifest, and
write the changed ones to the SD card (/sd/ota/new) with /sd/ota/pending.json;
then leave Wi-Fi cleanly and restart. ota_boot copies them into the flash at the
next boot, before the display starts: with the display running, a flash write
deadlocks the firmware (2026-11-05), so this module never writes the flash.
Once the app has run CONFIRM_MS on new code it writes /sd/ota/confirmed; if it
doesn't, ota_boot rolls the update back. main.py and ota_boot.py never come this way.

While it downloads and restarts, the back LEDs put on a show (leds(): a rainbow
filling up with the download, then a fast sparkling spin until the reset), through
the app's show(frac) callback: frac 0..1 downloading, None restarting.

Reader, read(), plan() and leds() are pure (tested on the laptop); Updater does the I/O.
"""

import binascii
import hashlib
import time

import ota_boot

CONFIRM_MS = 60000
N_LEDS = 7
QUIET_MS = 15000  # no TLS connect within this long of a touch (it stalls the screen)


def hexdigest(h):
    return binascii.hexlify(h.digest()).decode()


def file_sha(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(2048)
                if not chunk:
                    break
                h.update(chunk)
    except OSError:
        return None
    return hexdigest(h)


class Reader:
    """Lines and exact byte counts from a stream that only has readinto (a TLS socket)."""

    def __init__(self, stream, chunk=2048):
        self.stream = stream
        self.buf = bytearray(chunk)
        self.mv = memoryview(self.buf)
        self.have = b""

    def _fill(self):
        n = self.stream.readinto(self.mv)
        if not n:
            raise OSError("bundle ended early")
        self.have += bytes(self.mv[:n])

    def line(self, limit=200):
        while b"\n" not in self.have:
            if len(self.have) > limit:
                raise ValueError("bundle: no line break")
            self._fill()
        i = self.have.index(b"\n")
        out, self.have = self.have[:i], self.have[i + 1 :]
        return out.decode()

    def exactly(self, n):
        """Yield n bytes in pieces (a file is never held in RAM whole)."""
        while n > 0:
            if not self.have:
                self._fill()
            piece, self.have = self.have[:n], self.have[n:]
            n -= len(piece)
            yield piece


def plan(manifest, local_sha):
    """(changed, added): manifest paths whose contents differ from the Presto's, and
    those of them that don't exist there yet. local_sha(path) -> hex or None."""
    changed, added = [], []
    for f in manifest["files"]:
        have = local_sha(f["path"])
        if have != f["sha"]:
            changed.append(f["path"])
            if have is None:
                added.append(f["path"])
    return changed, added


def read(stream, manifest, wanted, write):
    """Read a bundle, hand each wanted file to write(path, pieces) and check its hash.
    Raises ValueError on a wrong version, size or hash: nothing is applied then."""
    r = Reader(stream)
    head = r.line()
    if head != "neon-bundle %s" % manifest["version"]:
        raise ValueError("bundle is {!r}, manifest says {}".format(head, manifest["version"]))
    expect = {f["path"]: f for f in manifest["files"]}
    seen = set()
    while len(seen) < len(expect):
        path, size = r.line(), int(r.line())
        f = expect.get(path)
        if f is None or f["size"] != size:
            raise ValueError("bundle: unexpected %s (%d bytes)" % (path, size))
        h = hashlib.sha256()

        def pieces():
            for piece in r.exactly(size):
                h.update(piece)
                yield piece

        if path in wanted:
            write(path, pieces())
        else:
            for _ in pieces():
                pass
        if hexdigest(h) != f["sha"]:
            raise ValueError("bundle: %s doesn't match its hash" % path)
        seen.add(path)


class Updater:
    def __init__(self, url, every_s=600, root="", show=None):
        self.url = (url or "").rstrip("/")
        self.show = show or (lambda frac: None)  # the LED show: frac 0..1, None while restarting
        self.got, self.size = 0, 1
        self.every_s = every_s
        self.root = root
        self.state = ota_boot.read(root) or {}
        self.version = self.state.get("version")
        self.started = time.ticks_ms()
        self.next_at = time.ticks_add(self.started, 90000)  # not in the first minute and a half
        self.confirmed = self.state.get("status") != "trial"
        self.warned = False

    def step(self, net, last_touch_ms, now_ms):
        if not self.confirmed and time.ticks_diff(now_ms, self.started) > CONFIRM_MS:
            self.confirm()
        if not self.url or not net.up or time.ticks_diff(now_ms, self.next_at) < 0:
            return
        if time.ticks_diff(now_ms, last_touch_ms) < QUIET_MS:
            return
        self.next_at = time.ticks_add(now_ms, self.every_s * 1000)
        try:
            self.check()
        except Exception as e:  # never let an update take the app down
            print("[neon] ota:", repr(e))
            ota_boot.remove_tree(self.root + ota_boot.NEW)

    def confirm(self):
        """The new code has run a minute: tell the next boot it's the good one (on the SD card)."""
        self.confirmed = True
        if ota_boot.is_dir(self.root + ota_boot.SD):
            ota_boot.write_json(self.root + ota_boot.CONFIRMED, {"version": self.version})
            print("[neon] ota: %s confirmed" % self.version)

    def _get(self, path):
        import gc

        import requests

        gc.collect()
        r = requests.get(self.url + path, timeout=15)
        if r.status_code != 200:
            r.close()
            raise OSError("%s: HTTP %d" % (path, r.status_code))
        return r

    def check(self):
        if not ota_boot.is_dir(self.root + ota_boot.SD):  # updates are staged on the card
            if not self.warned:
                print("[neon] ota: no SD card, no Wi-Fi updates")
                self.warned = True
            return False
        if not self.confirmed or ota_boot.read_json(self.root + ota_boot.PENDING):
            return False  # on trial, or one already waiting for the next boot
        r = self._get("/manifest.json")
        try:
            manifest = r.json()
        finally:
            r.close()
        v = manifest["version"]
        if v == self.version or v == self.state.get("bad"):
            return False
        changed, added = plan(manifest, lambda p: file_sha(self.root + "/" + p))
        print("[neon] ota: %s -> %s, %d files" % (self.version, v, len(changed)))
        pending = {"version": v, "prev": self.version, "files": changed, "added": added}
        if not changed:  # the same files under a new version (a USB deploy, say): the next boot notes it
            ota_boot.write_json(self.root + ota_boot.PENDING, pending)
            self.version = v
            return False
        ota_boot.remove_tree(self.root + ota_boot.NEW)
        self.got, self.size = 0, max(1, sum(f["size"] for f in manifest["files"] if f["path"] in changed))
        self.show(0.0)
        r = self._get("/bundle")
        try:
            read(r.raw, manifest, set(changed), self._stage)
        finally:
            r.close()
        ota_boot.write_json(self.root + ota_boot.PENDING, pending)
        print("[neon] ota: %s downloaded, restarting to install it" % v)
        restart(self.show)
        return True

    def _stage(self, path, pieces):
        dst = self.root + ota_boot.NEW + "/" + path
        ota_boot.makedirs(dst.rsplit("/", 1)[0])
        with open(dst, "wb") as f:
            for piece in pieces:
                f.write(piece)
                self.got += len(piece)
                self.show(min(1.0, self.got / self.size))


def restart(show=None):
    """Leave Wi-Fi cleanly first (presto#18), then reset; show(None) keeps the LEDs
    going meanwhile."""
    import machine
    import network

    w = network.WLAN(network.STA_IF)
    w.disconnect()
    _wait(2000, show)
    w.active(False)
    _wait(1000, show)
    machine.reset()


def _wait(ms, show):
    end = time.ticks_add(time.ticks_ms(), ms)
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        if show:
            show(None)
        time.sleep_ms(30)


def _hsv(h, v):
    """A fully saturated hue h (0..1) at value v (0..1) -> (r, g, b) 0..255."""
    h = (h % 1.0) * 6
    i, f = int(h), h - int(h)
    q, t = 1 - f, f
    r, g, b = ((1, t, 0), (q, 1, 0), (0, 1, t), (0, q, 1), (t, 0, 1), (1, 0, q))[i % 6]
    return (int(r * v * 255), int(g * v * 255), int(b * v * 255))


def leds(frac, t):
    """The update's LED show, N_LEDS colours at t ms. Downloading (frac 0..1): a rainbow
    spins round them, the LEDs the download has reached at full brightness, the rest
    faint. Restarting (frac None): all of them, spinning fast, with white sparks."""
    out = []
    for i in range(N_LEDS):
        if frac is None:
            hue = i / N_LEDS - t / 400
            spark = (t // 60 + i * 3) % 11 == 0
            out.append((255, 255, 255) if spark else _hsv(hue, 1.0))
        else:
            hue = i / N_LEDS - t / 1200
            out.append(_hsv(hue, 1.0 if i < 1 + frac * (N_LEDS - 1) else 0.15))
    return out
