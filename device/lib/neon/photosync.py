"""Photos from the upload page (presto-photos on the cluster) onto the SD card.

Every few minutes: fetch manifest.json, delete what's gone (deleted or hidden on
the page, or re-cropped: a new rev), and download what's new. Each frame is the
raw 240x240 RGB565 the screen holds (big-endian, as the framebuffer is), so
showing one is a single read from the card, no decoding.

A download goes a chunk per pass of the main loop, so the screen keeps running;
the only blocking parts are the TLS connects (~3 s each), held off while
someone is touching the screen. When the cluster or Wi-Fi is down, the card's
copy keeps playing, in the last manifest's order.

plan() is pure (tested on the laptop); Feed does the I/O.
"""

import json
import os
import time

DIR = "/sd/feed"
SIZE = 240  # the app runs at 240x240
FRAME_BYTES = SIZE * SIZE * 2
EXT = ".rgb565"


def log(*a):
    print("[neon] feed:", *a)


def name_of(photo):
    """A photo's file on the card: one per id and rev, so a re-crop is a new file."""
    return "%s-%d%s" % (photo["id"], int(photo.get("rev") or 0), EXT)


def plan(manifest, local):
    """(order, fetch, delete): the card's files in the manifest's order, the photos
    still to download (manifest entries), and the local files to remove.

    Only frames of this screen's size count: a 480 frame (the manifest says 480 for
    photos the converter hasn't redone yet) would read as a wrong 240 one."""
    wanted = [(name_of(p), p) for p in manifest.get("photos", []) if p.get("size", 480) == SIZE]
    names = set(n for n, _ in wanted)
    have = set(local)
    order = [n for n, _ in wanted if n in have]
    fetch = [p for n, p in wanted if n not in have]
    delete = sorted(n for n in have if n not in names)
    return order, fetch, delete


class Feed:
    EVERY_S = 10 * 60
    RETRY_S = 2 * 60
    CHUNK = 16384
    QUIET_MS = 15000  # no TLS connect within this long of a touch (it stalls the screen)

    def __init__(self, url, every_s=None):
        self.url = (url or "").rstrip("/")
        self.every_s = every_s or self.EVERY_S
        self.order = []  # files on the card, in the manifest's order
        self.queue = []  # manifest entries still to download
        self.version = 0  # bumped when order changes (the Photos app rescans)
        self.ok_at = None  # time.time() of the last good manifest
        self.next_at = 0
        self.dl = None  # (response, file, name, got)
        self.buf = bytearray(self.CHUNK)
        self.mv = memoryview(self.buf)
        self.ready = False
        if self.url:
            self._load()

    # --- the card ---
    def _load(self):
        try:
            os.mkdir(DIR)
        except OSError:
            pass
        try:
            local = self._local()
        except OSError as e:  # no card
            log("no", DIR, repr(e))
            return
        self.ready = True
        for f in os.listdir(DIR):
            if f.endswith(".part"):
                self._rm(f)
        try:
            with open(DIR + "/manifest.json") as f:
                man = json.load(f)
        except (OSError, ValueError):
            man = None
        if man:
            self.order, _, _ = plan(man, local)
        else:
            self.order = sorted(local)
        self.version += 1

    def _local(self):
        return [f for f in os.listdir(DIR) if f.endswith(EXT)]

    def _rm(self, name):
        try:
            os.remove(DIR + "/" + name)
        except OSError:
            pass

    def path(self, name):
        return DIR + "/" + name

    @staticmethod
    def id_of(name):
        return name.split("-")[0]

    @property
    def stale(self):
        """No good manifest for three intervals: the frames may be out of date."""
        return self.ready and (self.ok_at is None or time.time() - self.ok_at > 3 * self.every_s)

    # --- the loop ---
    def step(self, net, last_touch_ms):
        """Call once per pass of the main loop. Returns True if the photo list changed.

        Never raises: a card error (OSError 5, EIO, 2026-11-05: it took the app
        down after a good manifest) skips this sync, and it tries again later."""
        if not (self.ready and net.up):
            return False
        try:
            return self._step(last_touch_ms)
        except Exception as e:
            log("sync:", repr(e))
            self._close()
            self.queue = []
            self.next_at = time.ticks_add(time.ticks_ms(), self.RETRY_S * 1000)
            return False

    def _step(self, last_touch_ms):
        if self.dl:
            return self._read()
        quiet = time.ticks_diff(time.ticks_ms(), last_touch_ms) > self.QUIET_MS
        if not quiet:
            return False
        if self.queue:
            self._start(self.queue.pop(0))
            return False
        if time.ticks_diff(time.ticks_ms(), self.next_at) >= 0:
            return self._sync()
        return False

    def _get(self, path):
        import requests

        return requests.get(self.url + path, timeout=10)

    def _sync(self):
        try:
            r = self._get("/manifest.json")
            try:
                if r.status_code != 200:
                    raise OSError("manifest: HTTP %d" % r.status_code)
                raw = r.content
            finally:
                r.close()
            man = json.loads(raw)
        except Exception as e:
            log("sync:", repr(e))
            self.next_at = time.ticks_add(time.ticks_ms(), self.RETRY_S * 1000)
            return False
        self.ok_at = time.time()
        self.next_at = time.ticks_add(time.ticks_ms(), self.every_s * 1000)
        with open(DIR + "/manifest.json.part", "wb") as f:
            f.write(raw)
        os.rename(DIR + "/manifest.json.part", DIR + "/manifest.json")
        order, fetch, delete = plan(man, self._local())
        for n in delete:
            self._rm(n)
        self.queue = fetch
        changed = order != self.order
        self.order = order
        if changed:
            self.version += 1
        if fetch or delete:
            log("%d to fetch, %d removed, %d on the card" % (len(fetch), len(delete), len(order)))
        return changed

    def _start(self, photo):
        name = name_of(photo)
        try:
            r = self._get(photo["rgb565"])
            if r.status_code != 200:
                r.close()
                raise OSError("HTTP %d" % r.status_code)
            f = open(self.path(name) + ".part", "wb")
        except Exception as e:
            log("fetch %s: %r" % (name, e))
            self.next_at = time.ticks_add(time.ticks_ms(), self.RETRY_S * 1000)  # re-plan later
            self.queue = []
            return
        self.dl = [r, f, name, 0]

    def _read(self):
        r, f, name, got = self.dl
        try:
            # MicroPython's readinto takes only the buffer: a slice of it caps the read
            k = r.raw.readinto(self.mv[: min(self.CHUNK, FRAME_BYTES - got)])
            if k:
                f.write(self.mv[:k])
                got = self.dl[3] = got + k
            if k and got < FRAME_BYTES:
                return False  # more next pass
            self._close()
            if got != FRAME_BYTES:
                raise OSError("%d bytes, not %d" % (got, FRAME_BYTES))
            os.rename(self.path(name) + ".part", self.path(name))
        except Exception as e:
            log("fetch %s: %r" % (name, e))
            self._close()
            self._rm(name + ".part")
            self.queue = []
            self.next_at = time.ticks_add(time.ticks_ms(), self.RETRY_S * 1000)
            return False
        # the new file goes where the manifest puts it
        try:
            with open(DIR + "/manifest.json") as fm:
                self.order, _, _ = plan(json.load(fm), self._local())
        except (OSError, ValueError):
            self.order.append(name)
        self.version += 1
        log("got", name)
        return True

    def _close(self):
        if self.dl:
            for h in self.dl[:2]:
                try:
                    h.close()
                except Exception:
                    pass
            self.dl = None

    def report(self, name):
        """Tell the page which photo is on the screen (a POST; blocks ~3 s)."""
        try:
            import requests

            r = requests.post(
                self.url + "/api/showing?id=" + self.id_of(name),
                headers={"X-Presto": "1", "Origin": self.url, "Content-Type": "application/json"},
                data="{}",
                timeout=10,
            )
            r.close()
        except Exception as e:
            log("report:", repr(e))
