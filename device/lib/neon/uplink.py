"""The Brick screen's uplink: presto-feed's JSON (presto#5) as two pages of lines.

CLUSTER: each brick (ready, CPU, memory, disk, temperature), the pods, Argo CD,
Jenkins, the front door, the internet and Prometheus' scrapes. ROOMS: every
indoor temperature Home Assistant has online, plus the Presto's own sensor, and
this node. Passive only: a problem shows as a red line and a stuttering column,
never a sound or a takeover.

cluster(), rooms(), indoor() and health() are pure (tested on the laptop); Uplink does the fetch.
"""

import time

EVERY_MS = 60000  # a fetch is a ~3 s TLS connect that stalls the screen: once a minute, on Brick or Weather only
FIRST_MS = 1500  # after entering, let the screen draw before the first fetch
STALE_S = 180  # older than this (here or at the feed): STALE
TABS = ("CLUSTER", "ROOMS")
ROWS = 17  # the Brick panel's lines

REASONS = {
    "CrashLoopBackOff": "CRASHLOOP",
    "ImagePullBackOff": "NO IMAGE",
    "ErrImagePull": "NO IMAGE",
    "CreateContainerConfigError": "CONFIG",
}

# (text, kind): kind "h" a heading, "bad" a problem, "" plain
H, BAD, OK = "h", "bad", ""
OWN = "PRESTO"  # the Presto's own sensor; Home Assistant's copy of it is "Presto temperature"


def _short(s, n):
    s = str(s).upper()
    return s if len(s) <= n else s[: n - 1] + "~"


def _n(x):
    """A count in at most 4 characters: 216, 9999, 12K, 1.2M."""
    x = int(x)
    return "%d" % x if x < 10000 else "%dK" % (x // 1000) if x < 1000000 else "%.1fM" % (x / 1000000)


def _ago(s):
    if s < 60:
        return "%dS" % s
    if s < 3600:
        return "%dM" % (s // 60)
    if s < 172800:
        return "%dH" % (s // 3600)
    return "%dD" % (s // 86400)


def _left(seconds):
    """A maintenance window's time left: presto-feed sends -1 for no end."""
    return _ago(seconds) if seconds >= 0 else "NO END"


def _pct(n, key):
    return "%3d%%" % n[key] if key in n else "  --"


def _rate(kib):
    return "%dK" % kib if kib < 1024 else "%.1fM" % (kib / 1024) if kib < 10240 else "%dM" % (kib // 1024)


def cluster(data, rows=ROWS):
    """The CLUSTER page: always the summaries, and as many problem lines as fit."""
    if data is None:
        return [("> UPLINK", H), ("  NO FEED YET", BAD)]
    # fewer problem lines until it fits: the "+N MORE" lines, then Jenkins' LAST line go first
    for cap, more, last in ((4, 1, 1), (3, 1, 1), (2, 1, 1), (1, 1, 1), (1, 0, 1), (1, 0, 0), (0, 0, 0)):
        out = _cluster(data, cap, more, last)
        if len(out) <= rows:
            return out
    return out[:rows]


def _more(out, shown, total, more):
    if more and total > shown:
        out.append(("  +%d MORE" % (total - shown), BAD))


def _cluster(data, cap, more=1, last=1):
    out = [("> NODES     CPU MEM DSK TEMP", H)]
    for n in data.get("nodes", []):
        if "maint" in n:  # a maintenance window: calm, with how long it has left
            out.append(("  %-9s  MAINT %s" % (_short(n["n"], 9), _left(n["maint"])), OK))
            continue
        if not n.get("ok"):
            out.append(("  %-9s  %s" % (_short(n["n"], 9), "NOT READY" if n.get("k8s") else "DOWN"), BAD))
            continue
        temp = "%3dC" % n["temp"] if "temp" in n else "  --"
        out.append(("  %-9s%s%s%s %s" % (_short(n["n"], 9), _pct(n, "cpu"), _pct(n, "mem"), _pct(n, "disk"), temp), OK))

    pods = data.get("pods", {})
    rs = pods.get("rs", 0)
    line = "> PODS  %s RUNNING" % _n(pods.get("run", 0))
    if pods.get("nbad"):
        line += "  %s BAD" % _n(pods["nbad"])
    elif rs:
        line += "  %s RST/H" % _n(rs)
    out.append((line, BAD if pods.get("nbad") or rs else H))
    bad = pods.get("bad", [])[:cap]
    for name, why in bad:
        out.append(("  %-19s %s" % (_short(name, 19), _short(REASONS.get(why, why), 9)), BAD))
    if cap:
        _more(out, len(bad), pods.get("nbad", 0), more)

    apps = data.get("apps", {})
    nbad = apps.get("nbad", 0)
    out.append(("> ARGO  %d/%d SYNCED" % (apps.get("n", 0) - nbad, apps.get("n", 0)), BAD if nbad else H))
    bad = apps.get("bad", [])[:cap]
    for name, sync, health in bad:
        out.append(("  %-17s %s" % (_short(name, 17), _short(sync if sync != "Synced" else health, 11)), BAD))
    if cap:
        _more(out, len(bad), nbad, more)

    jobs = data.get("jobs")
    if jobs:
        nbad = jobs.get("nbad", 0)
        out.append(("> JENKINS  %d/%d PASSING" % (jobs.get("n", 0) - nbad, jobs.get("n", 0)), BAD if nbad else H))
        bad = jobs.get("bad", [])[:cap]
        for name, result in bad:
            out.append(("  %-19s %s" % (_short(name, 19), result), BAD))
        if cap:
            _more(out, len(bad), nbad, more)
        if jobs.get("last") and last:
            name, ago = jobs["last"]
            out.append(("  LAST %-17s %s AGO" % (_short(name, 17), _ago(ago + data.get("age", 0))), OK))

    door = data.get("door", {})
    if door.get("ok"):
        e = door.get("e5xx", 0)
        line = "> DOOR  UP  %s REQ/M" % _n(door.get("rpm", 0)) + ("  %s 5XX" % _n(e) if e else "")
        out.append((line, BAD if e else H))
    else:
        out.append(("> DOOR  DOWN", BAD))

    net = data.get("net")
    if net is not None:
        if net.get("ok"):
            out.append(("> NET   DN %sB/S  UP %sB/S" % (_rate(net.get("dn", 0)), _rate(net.get("up", 0))), H))
        else:
            out.append(("> NET   INTERNET DOWN", BAD))

    sc = data.get("scrape")
    if sc:
        nd = sc.get("ndown", 0)
        out.append(("> SCRAPE  %d/%d UP" % (sc.get("n", 0) - nd, sc.get("n", 0)), BAD if nd else H))
        if cap:
            down = sc.get("down", [])[:cap]
            for job in down:
                out.append(("  %s" % _short(job, 30), BAD))
            _more(out, len(down), nd, more)
    return out


def indoor(data, fahrenheit, own_temp=None):
    """Every indoor sensor online, as (NAME, temperature in the display's unit): the feed's,
    by name, then the Presto's own (own_temp, already in that unit). While the Presto reads,
    the feed's copy of it (through Home Assistant, a minute or more behind) is left out."""
    out = []
    for t in (data or {}).get("temps", []):
        name = str(t["n"]).upper()
        if own_temp is not None and name == OWN:
            continue
        out.append((name, t["c"] * 9 / 5 + 32 if fahrenheit else t["c"]))
    if own_temp is not None:
        out.append((OWN, own_temp))
    return out


def rooms(data, fahrenheit, own_temp=None):
    """own_temp: the Presto's sensor, already in the display's unit."""
    unit = "F" if fahrenheit else "C"
    out = [("> INDOOR", H)]
    out.extend(("  %-14s %.1f%s" % (_short(n, 14), t, unit), OK) for n, t in indoor(data, fahrenheit, own_temp))
    if len(out) == 1:
        out.append(("  NO SENSORS ONLINE", OK))
    return out


def health(data):
    """One flag per check, for the rain's columns: nodes, then Argo, pods, the door, Jenkins,
    the internet and the scrapes."""
    if data is None:
        return []
    flags = [bool(n.get("ok")) or "maint" in n for n in data.get("nodes", [])]
    flags.append(not data.get("apps", {}).get("nbad"))
    flags.append(not data.get("pods", {}).get("nbad"))
    door = data.get("door", {})
    flags.append(bool(door.get("ok")) and not door.get("e5xx"))
    if data.get("jobs"):
        flags.append(not data["jobs"].get("nbad"))
    if data.get("net") is not None:
        flags.append(bool(data["net"].get("ok")))
    if data.get("scrape"):
        flags.append(not data["scrape"].get("ndown"))
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
        """On Brick or Weather: fetch soon if the last answer is a minute old (or there is none)."""
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
            self.data, self.got_at = r.json(), time.ticks_ms()  # now, not now_ms: the fetch took ~3 s
            return True
        except Exception as e:  # keep the last answer; the screen says STALE once it's old
            print("[neon] uplink:", repr(e))
            return False
        finally:
            if r is not None:
                r.close()
