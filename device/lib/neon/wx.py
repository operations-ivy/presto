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

# How hard it rains in the Home scene for each kind (streaks on screen).
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
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
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
        "utc_offset": data.get("utc_offset_seconds"),
    }


def line(s, unit="°"):
    """'52°/41°  RAIN 40%' (bitmap fonts have no degree sign: callers pass unit)."""
    if s is None or s.get("hi") is None:
        return "FORECAST OFFLINE"
    out = "%d%s/%d%s" % (round(s["hi"]), unit, round(s["lo"]), unit)
    if s.get("rain_pct") is not None:
        out += "  RAIN %d%%" % s["rain_pct"]
    return out
