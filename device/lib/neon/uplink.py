"""The Brick screen's uplink: presto-feed's JSON (presto#5) as two pages of lines.

CLUSTER: each brick (ready, CPU, memory, temperature), Argo CD, the pods, the
front door. ROOMS: every indoor temperature Home Assistant has online, plus the
Presto's own sensor, and this node. Passive only: a problem shows as a red line
and a stuttering column, never a sound or a takeover.

pages() and health() are pure (tested on the laptop); Uplink does the fetch.
"""

import time

EVERY_MS = 60000  # a fetch is a ~3 s TLS connect that stalls the screen: once a minute, on Brick only
FIRST_MS = 1500  # after entering, let the screen draw before the first fetch
STALE_S = 180  # older than this (here or at the feed): STALE
TABS = ("CLUSTER", "ROOMS")

REASONS = {
    "CrashLoopBackOff": "CRASHLOOP",
    "ImagePullBackOff": "NO IMAGE",
    "ErrImagePull": "NO IMAGE",
    "CreateContainerConfigError": "CONFIG",
}

# (text, kind): kind "h" a heading, "bad" a problem, "" plain
H, BAD, OK = "h", "bad", ""


def _deg(c, fahrenheit):
    return "%.1f%s" % ((c * 9 / 5 + 32, "F") if fahrenheit else (c, "C"))


def _short(s, n):
    s = str(s).upper()
    return s if len(s) <= n else s[: n - 1] + "~"


def cluster(data):
    if data is None:
        return [("> UPLINK", H), ("  NO FEED YET", BAD)]
    out = [("> NODES      CPU MEM TEMP", H)]
    for n in data.get("nodes", []):
        if not n.get("ok"):
            out.append(("  %-9s  %s" % (_short(n["n"], 9), "NOT READY" if n.get("k8s") else "DOWN"), BAD))
            continue
        cpu = "%3d%%" % n["cpu"] if "cpu" in n else "  --"
        mem = "%3d%%" % n["mem"] if "mem" in n else "  --"
        temp = "%3dC" % n["temp"] if "temp" in n else "  --"
        out.append(("  %-9s %s%s %s" % (_short(n["n"], 9), cpu, mem, temp), OK))
    apps = data.get("apps", {})
    nbad = apps.get("nbad", 0)
    out.append(("> ARGO  %d/%d SYNCED" % (apps.get("n", 0) - nbad, apps.get("n", 0)), H if not nbad else BAD))
    for name, sync, health in apps.get("bad", []):
        what = sync if sync != "Synced" else health
        out.append(("  %-13s %s" % (_short(name, 13), _short(what, 11)), BAD))
    pods = data.get("pods", {})
    if pods.get("nbad"):
        out.append(("> PODS  %d NOT RIGHT" % pods["nbad"], BAD))
        for name, why in pods.get("bad", []):
            out.append(("  %-19s %s" % (_short(name, 19), _short(REASONS.get(why, why), 9)), BAD))
    else:
        out.append(("> PODS  ALL READY", H))
    door = data.get("door", {})
    if door.get("ok"):
        e = door.get("e5xx", 0)
        out.append(("> DOOR  UP" + ("  %d ERR/10M" % e if e else ""), H if not e else BAD))
    else:
        out.append(("> DOOR  DOWN", BAD))
    return out


def rooms(data, fahrenheit, own_temp=None):
    """own_temp: the Presto's sensor, already in the display's unit."""
    out = [("> INDOOR", H)]
    for t in (data or {}).get("temps", []):
        out.append(("  %-14s %s" % (_short(t["n"], 14), _deg(t["c"], fahrenheit)), OK))
    if own_temp is not None:
        out.append(("  %-14s %.1f%s" % ("PRESTO", own_temp, "F" if fahrenheit else "C"), OK))
    if len(out) == 1:
        out.append(("  NO SENSORS ONLINE", OK))
    return out


def health(data):
    """One flag per check, for the rain's columns: nodes, then Argo, pods, the door."""
    if data is None:
        return []
    flags = [bool(n.get("ok")) for n in data.get("nodes", [])]
    flags.append(not data.get("apps", {}).get("nbad"))
    flags.append(not data.get("pods", {}).get("nbad"))
    door = data.get("door", {})
    flags.append(bool(door.get("ok")) and not door.get("e5xx"))
    return flags


def stale(data, fetched_age_s):
    """True when the feed is old: the last fetch here, or the feed's own answer."""
    if data is None or fetched_age_s is None:
        return False
    return fetched_age_s > STALE_S or data.get("age", 0) > STALE_S


class Uplink:
    def __init__(self, url):
        self.url = url or ""
        self.data = None
        self.got_at = None  # ticks_ms of the last good fetch
        self.next_at = None

    def age_s(self, now_ms):
        return None if self.got_at is None else time.ticks_diff(now_ms, self.got_at) // 1000

    def entered(self, now_ms):
        """On Brick: fetch soon if the last answer is a minute old (or there is none)."""
        if self.got_at is None or time.ticks_diff(now_ms, self.got_at) >= EVERY_MS:
            self.next_at = time.ticks_add(now_ms, FIRST_MS)

    def due(self, now_ms):
        return bool(self.url) and self.next_at is not None and time.ticks_diff(now_ms, self.next_at) >= 0

    def fetch(self, now_ms):
        import requests

        self.next_at = time.ticks_add(now_ms, EVERY_MS)
        r = None
        try:
            r = requests.get(self.url, timeout=10)
            if r.status_code != 200:
                raise OSError("HTTP %d" % r.status_code)
            self.data, self.got_at = r.json(), time.ticks_ms()
            return True
        except Exception as e:  # keep the last answer; the screen says STALE once it's old
            print("[neon] uplink:", repr(e))
            return False
        finally:
            if r is not None:
                r.close()
