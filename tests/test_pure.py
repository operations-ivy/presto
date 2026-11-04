import calendar
import datetime as dt

import pytest
from neon import conf, nav, sprites, tz, wx


# --- tz ------------------------------------------------------------------
@pytest.mark.parametrize("day", [0, 1, 59, 365, 10957, 20000, 20760, -1, -719468])
def test_civil_round_trip(day):
    assert tz.days_from_civil(*tz.civil_from_days(day)) == day


def test_civil_matches_python():
    for y, m, d in ((1970, 1, 1), (2000, 2, 29), (2026, 11, 3), (2099, 12, 31)):
        assert tz.days_from_civil(y, m, d) == (dt.date(y, m, d) - dt.date(1970, 1, 1)).days
        assert tz.weekday(y, m, d) == dt.date(y, m, d).weekday()


def utc(*a):
    return calendar.timegm(dt.datetime(*a).timetuple())


def test_us_dst_2026():
    # 2026: DST from Sun 8 March 07:00 UTC (02:00 EST) to Sun 1 November 06:00 UTC (02:00 EDT)
    assert tz.nth_sunday(2026, 3, 2) == 8 and tz.nth_sunday(2026, 11, 1) == 1
    assert not tz.is_dst(utc(2026, 3, 8, 6, 59), -5, "us")
    assert tz.is_dst(utc(2026, 3, 8, 7, 0), -5, "us")
    assert tz.is_dst(utc(2026, 11, 1, 5, 59), -5, "us")
    assert not tz.is_dst(utc(2026, 11, 1, 6, 0), -5, "us")


def test_eu_dst_2026():
    assert tz.is_dst(utc(2026, 7, 1), 0, "eu")
    assert not tz.is_dst(utc(2026, 3, 29, 0, 59), 0, "eu")  # last Sunday of March 2026 is the 29th
    assert tz.is_dst(utc(2026, 3, 29, 1, 0), 0, "eu")


def test_local_and_override():
    t = utc(2026, 11, 3, 3, 47)  # 22:47 EST on Monday 2 November
    lt = tz.local(t, tz.offset_seconds(t, -5, "us"))
    assert lt[:5] == (2026, 11, 2, 22, 47) and tz.DAYS[lt[6]] == "MON"
    assert tz.offset_seconds(t, -5, "us", override=-14400) == -14400
    assert tz.date_line(lt) == "MON  02 NOV 2026"


# --- nav -----------------------------------------------------------------
ARMS = {"up": "weather", "left": "photos", "right": "brick", "down": "fun"}


@pytest.mark.parametrize(
    "dx,dy,ms,two,want",
    [
        (0, 0, 100, False, "tap"),
        (10, -12, 500, False, "tap"),
        (5, 5, 1500, False, None),  # long press: reserved
        (0, -150, 200, False, "up"),
        (0, 150, 200, False, "down"),
        (-150, 20, 200, False, "left"),
        (150, -20, 200, False, "right"),
        (50, 0, 200, False, None),  # between a tap and a swipe
        (0, 0, 100, True, "two"),
    ],
)
def test_classify(dx, dy, ms, two, want):
    assert nav.classify(dx, dy, ms, two) == want


def test_plus_round_trips():
    p = nav.Plus(ARMS)
    for direction, app in ARMS.items():
        assert p.swipe(direction) == app
        for other in ARMS:  # only the opposite way leads home
            if other != nav.OPPOSITE[direction]:
                assert p.swipe(other) is None and p.current == app
        assert p.swipe(nav.OPPOSITE[direction]) == nav.HOME


def test_entry_direction_and_go():
    p = nav.Plus(ARMS)
    assert p.entry_direction("weather") == "up"
    p.go("photos")
    assert p.entry_direction(nav.HOME) == "right"
    with pytest.raises(ValueError):
        p.go("nope")


# --- conf ----------------------------------------------------------------
def test_conf_parse():
    text = (
        "# comment\n"
        "idle_seconds: 300   # five minutes\n"
        "units: imperial\n"
        "offset: -5\n"
        "temp: 0.5\n"
        "chirp: true\n"
        "name: 'a # not a comment'\n"
        "empty:\n"
        "\n"
    )
    assert conf.parse(text) == {
        "idle_seconds": 300,
        "units": "imperial",
        "offset": -5,
        "temp": 0.5,
        "chirp": True,
        "name": "a # not a comment",
        "empty": None,
    }


def test_conf_rejects_nesting():
    with pytest.raises(ValueError):
        conf.parse("a:\n  b: 1\n")


def test_repo_settings_parse():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "device" / "settings.yaml").read_text()
    s = conf.parse(text)
    assert s["arm_left"] == "photos" and s["utc_offset"] == -5 and s["dst"] == "us"


# --- wx ------------------------------------------------------------------
def test_wx_kinds():
    assert wx.kind(0) == "clear" and wx.kind(63) == "rain" and wx.kind(95) == "storm"
    assert wx.kind(None) == "unknown" and wx.kind(1234) == "unknown"


def test_wx_summarize_and_line():
    data = {
        "utc_offset_seconds": -18000,
        "current": {"temperature_2m": 47.3, "weather_code": 61, "is_day": 0},
        "daily": {
            "temperature_2m_max": [52.4],
            "temperature_2m_min": [40.6],
            "precipitation_probability_max": [40],
            "weather_code": [61],
        },
    }
    s = wx.summarize(data)
    assert s["kind"] == "rain" and s["utc_offset"] == -18000 and not s["is_day"]
    assert wx.line(s, "") == "52/41  RAIN 40%"
    assert wx.line(None) == "FORECAST OFFLINE"
    assert "latitude=51.507&longitude=-0.128" in wx.url(51.5072, -0.1276)  # London: a neutral example


# --- sprites -------------------------------------------------------------
def test_runs():
    assert sprites.runs(("..AAB.", "C.....")) == [(2, 0, 2, "A"), (4, 0, 1, "B"), (0, 1, 1, "C")]


def test_art_uses_known_letters():
    from neon import theme

    for name in dir(sprites):
        art = getattr(sprites, name)
        if name.isupper() and isinstance(art, tuple):
            letters = {ch for row in art for ch in row} - {"."}
            assert letters <= set(theme.LETTERS), (name, letters - set(theme.LETTERS))


# --- taps (navigation is by taps since 2026-11-03) ------------------------
@pytest.mark.parametrize(
    "x,y,want",
    [(240, 10, "up"), (240, 470, "down"), (10, 240, "left"), (470, 240, "right"), (240, 240, None), (5, 5, "up")],
)
def test_zone(x, y, want):
    assert nav.zone(x, y) == want


def test_taps_open_apps_and_come_home():
    p = nav.Plus(ARMS)
    assert p.tap(240, 240) is None  # the middle of Home: Home's own tap
    assert p.tap(240, 470, 0) == "fun"
    p.go(nav.HOME)
    assert p.tap(10, 240, 0) == "photos"
    assert p.home_tab() == "right"
    assert p.tap(100, 240, 100) is None and p.current == "photos"  # a photo tap, not navigation
    assert p.tap(10, 240, 200) is None  # the far edge is not the HOME button's
    assert not p.revealed(300)


def test_hidden_home_button():
    p = nav.Plus(ARMS)
    p.tap(240, 10, 0)  # Weather; its HOME button hides on the bottom edge
    assert p.current == "weather" and p.home_tab() == "down"
    assert p.tap(240, 470, 1000) == nav.REVEAL and p.current == "weather"  # first tap raises it
    assert p.revealed(1500) and p.reveal_age(1500) == 500
    assert p.tap(240, 470, 2000) == nav.HOME  # second tap, while it's up: Home
    assert not p.revealed(2001)


def test_home_button_hides_again():
    p = nav.Plus(ARMS)
    p.tap(470, 240, 0)  # Brick
    assert p.tap(10, 240, 1000) == nav.REVEAL
    later = 1000 + nav.REVEAL_MS
    assert not p.revealed(later)
    assert p.tap(10, 240, later) == nav.REVEAL and p.current == "brick"  # raised again, not Home


def test_ticks_diff_wraps():
    top = (1 << 30) - 100
    assert nav.ticks_diff(50, top) == 150
    assert nav.ticks_diff(top, 50) == -150
    p = nav.Plus(ARMS)
    p.tap(470, 240, 0)
    assert p.tap(10, 240, top) == nav.REVEAL
    assert p.revealed(50)  # 150 ms later, across the wrap
