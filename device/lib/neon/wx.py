"""Weather: WMO codes to our scene kinds, and Open-Meteo's response to a summary.

Pure (tested on CPython); the HTTP fetch lives in app code.
"""

# WMO weather interpretation codes (Open-Meteo `weather_code`).
KINDS = (
    ((0,), "clear"),
    ((1, 2), "partly"),
    ((3,), "cloudy"),
    ((45, 48), "fog"),
    ((51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82), "rain"),
    ((71, 73, 75, 77, 85, 86), "snow"),
    ((95, 96, 99), "storm"),
)

LABELS = {
    "clear": "CLEAR",
    "partly": "PARTLY CLOUDY",
    "cloudy": "OVERCAST",
    "fog": "FOG",
    "rain": "RAIN",
    "snow": "SNOW",
    "storm": "STORM",
    "unknown": "NO DATA",
}

# How hard it rains in the Home scene for each kind (falling glyphs on screen).
RAIN = {"rain": 110, "storm": 170, "fog": 20, "cloudy": 25, "partly": 0, "clear": 0, "snow": 0, "unknown": 15}


def kind(code):
    if code is None:
        return "unknown"
    for codes, name in KINDS:
        if code in codes:
            return name
    return "unknown"


def url(lat, lon, fahrenheit=True):
    return (
        "https://api.open-meteo.com/v1/forecast?latitude=%.3f&longitude=%.3f"
        "&current=temperature_2m,weather_code,is_day"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset"
        "&timezone=auto&forecast_days=1%s" % (lat, lon, "&temperature_unit=fahrenheit" if fahrenheit else "")
    )


def summarize(data):
    """The bits the screens use, from Open-Meteo's JSON (already parsed)."""
    cur = data.get("current", {})
    day = data.get("daily", {})

    def first(key):
        v = day.get(key) or [None]
        return v[0]

    code = cur.get("weather_code", first("weather_code"))
    return {
        "now": cur.get("temperature_2m"),
        "hi": first("temperature_2m_max"),
        "lo": first("temperature_2m_min"),
        "rain_pct": first("precipitation_probability_max"),
        "kind": kind(code),
        "is_day": bool(cur.get("is_day", 0)),
        "sunrise": minute_of(first("sunrise")),  # local minutes past midnight, at the coordinates
        "sunset": minute_of(first("sunset")),
        "utc_offset": data.get("utc_offset_seconds"),
    }


def minute_of(iso):
    """'2026-11-04T06:41' (Open-Meteo, local time) -> 401, or None."""
    try:
        hh, mm = iso.split("T")[1].split(":")[:2]
        return int(hh) * 60 + int(mm)
    except (AttributeError, IndexError, ValueError):
        return None


def daytime(s, minute):
    """Day or night now: between today's sunrise and sunset when the forecast has them and
    the clock is set (on the minute), else the forecast's own is_day (refreshed every 30 min)."""
    if not s:
        return False
    rise, down = s.get("sunrise"), s.get("sunset")
    if minute is not None and rise is not None and down is not None:
        return rise <= minute < down
    return bool(s.get("is_day"))


# Home's sky (the user, 2026-11-04): 2049 haze on a sunny day, haze under grey when partly
# cloudy, grey when it's overcast, wet, foggy or snowing; violet night after sunset.
DAY_SKIES = {"clear": "haze", "partly": "grey_haze"}


def sky(kind, day):
    return DAY_SKIES.get(kind, "overcast") if day else "night"


def line(s, unit="°"):
    """'52°/41°  RAIN 40%' (bitmap fonts have no degree sign: callers pass unit)."""
    if s is None or s.get("hi") is None:
        return "FORECAST OFFLINE"
    out = "%d%s/%d%s" % (round(s["hi"]), unit, round(s["lo"]), unit)
    if s.get("rain_pct") is not None:
        out += "  RAIN %d%%" % s["rain_pct"]
    return out
