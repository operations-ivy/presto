"""The pager (presto#52): what's firing in Alertmanager, from presto-alerts on the cluster.

The Presto polls GET /alerts every alerts_minutes, between frames, and only when
nobody has touched it for QUIET_MS: a fetch is a ~3 s TLS connect that stalls
the screen. Home shows a badge (the count, in the worst one's colour); a
critical alert opens ALERTS by itself, once per alert; SILENCE asks the relay
for a 1 hour silence. Nothing here makes a sound: the app's chirp does, only
when chirp is on.

badge(), criticals(), worst(), drop(), wrap() and ago() are pure (tested on the laptop); Pager does the fetch.
"""

import time

FIRST_MS = 20000  # after boot: let Wi-Fi and the first screens settle
QUIET_MS = 15000  # no touch for this long before a fetch (it stalls the screen ~3 s)
STALE_S = 900  # older than this: the badge shows "?" and ALERTS says STALE
SEVERITY_PENS = {"critical": "red", "warning": "amber", "info": "cyan"}
SEVERITY_HUES = {"critical": 0.97, "warning": 0.12, "info": 0.52}  # ALERTS' back LEDs


def badge(data, stale):
    """Home's badge: (text, severity) or None for none. Stale: ("?", None)."""
    if stale:
        return ("?", None)
    if not data or not data.get("n"):
        return None
    n = data["n"]
    return ("%d" % n if n < 100 else "99+", data.get("worst"))


def criticals(data):
    """The ids of the critical alerts firing."""
    return [a["id"] for a in (data or {}).get("alerts", ()) if a.get("sev") == "critical"]


def worst(alerts):
    """The worst severity among alerts, or None."""
    for sev in ("critical", "warning", "info"):
        if any(a.get("sev") == sev for a in alerts):
            return sev
    return None


def drop(data, alert_id):
    """The relay's answer without one alert (just silenced). n counts every alert firing,
    the list only the first few, so n goes down only if the alert was in the list."""
    if not data:
        return data
    shown = data.get("alerts", [])
    kept = [a for a in shown if a.get("id") != alert_id]
    n = max(0, data.get("n", 0) - (len(shown) - len(kept)))
    return dict(data, alerts=kept, n=n, worst=worst(kept) if n else None)


def ago(seconds):
    """A firing time in at most 3 characters: 45S, 12M, 5H, 3D."""
    s = max(0, int(seconds))
    if s < 60:
        return "%dS" % s
    if s < 3600:
        return "%dM" % (s // 60)
    if s < 172800:
        return "%dH" % (s // 3600)
    return "%dD" % (s // 86400)


def wrap(text, width):
    """Words into lines of at most width characters (longer words are cut)."""
    lines, line = [], ""
    for word in str(text).split():
        word = word[:width]
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = (line + " " + word) if line else word
    if line:
        lines.append(line)
    return lines


class Pager:
    def __init__(self, url, every_s):
        self.url = url or ""
        self.silence_url = self.url.rsplit("/", 1)[0] + "/silence" if self.url else ""
        self.every_ms = max(30, int(every_s)) * 1000
        self.data = None
        self.got_at = None  # ticks_ms of the last good fetch
        self.next_at = time.ticks_add(time.ticks_ms(), FIRST_MS)
        self.seen = set()  # critical alerts that have opened ALERTS already

    def age_s(self, now_ms):
        return None if self.got_at is None else time.ticks_diff(now_ms, self.got_at) // 1000

    def stale(self, now_ms):
        """No answer for STALE_S (or none since boot, once the first fetch was due)."""
        if not self.url:
            return False
        age = self.age_s(now_ms)
        if age is None:
            return time.ticks_diff(now_ms, self.next_at) > STALE_S * 1000
        return age > STALE_S

    def due(self, now_ms, last_touch_ms):
        return (
            bool(self.url)
            and time.ticks_diff(now_ms, self.next_at) >= 0
            and time.ticks_diff(now_ms, last_touch_ms) > QUIET_MS
        )

    def step(self, net, last_touch_ms, now_ms):
        """Fetch if due; True when there's a new answer."""
        if not (net.up and self.due(now_ms, last_touch_ms)):
            return False
        return self.fetch(now_ms)

    def fetch(self, now_ms):
        import requests  # here, not at the top: MicroPython's RAM, as uplink and ota do

        self.next_at = time.ticks_add(now_ms, self.every_ms)
        r = None
        try:
            r = requests.get(self.url, timeout=10)
            if r.status_code != 200:
                raise OSError("HTTP %d" % r.status_code)
            self.data, self.got_at = r.json(), time.ticks_ms()
            return True
        except Exception as e:  # keep the last answer; the badge shows "?" once it's old
            print("[neon] pager:", repr(e))
            return False
        finally:
            if r is not None:
                r.close()

    def take_critical(self):
        """True once for each critical alert that starts firing (it opens ALERTS)."""
        firing = set(criticals(self.data))
        new = firing - self.seen
        self.seen = firing  # one that stops firing can open it again if it comes back
        return bool(new)

    def silence(self, alert_id):
        """Ask the relay to silence one alert for an hour; True when it did."""
        import json

        import requests

        r = None
        try:
            r = requests.post(
                self.silence_url,
                data=json.dumps({"id": alert_id}),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if r.status_code != 200:
                raise OSError("HTTP %d" % r.status_code)
        except Exception as e:
            print("[neon] pager silence:", repr(e))
            return False
        finally:
            if r is not None:
                r.close()
        self.data = drop(self.data, alert_id)  # gone from the list now; the next fetch agrees
        return True
