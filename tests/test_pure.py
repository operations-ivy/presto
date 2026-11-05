import calendar
import datetime as dt

import pytest
from neon import conf, nav, photosync, retry, sprites, tz, wx


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
        (5, -6, 500, False, "tap"),
        (2, 2, 1500, False, None),  # long press: reserved
        (0, -75, 200, False, "up"),
        (0, 75, 200, False, "down"),
        (-75, 10, 200, False, "left"),
        (75, -10, 200, False, "right"),
        (25, 0, 200, False, None),  # between a tap and a swipe
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


# --- taps (navigation is by taps since 2026-11-03; 240x240 coordinates) ----
@pytest.mark.parametrize(
    "x,y,want",
    [(120, 5, "up"), (120, 235, "down"), (5, 120, "left"), (235, 120, "right"), (120, 120, None), (2, 2, "up")],
)
def test_zone(x, y, want):
    assert nav.zone(x, y) == want


def test_taps_open_apps_and_come_home():
    p = nav.Plus(ARMS)
    assert p.tap(120, 120) is None  # the middle of Home: Home's own tap
    assert p.tap(120, 235, 0) == "fun"
    p.go(nav.HOME)
    assert p.tap(5, 120, 0) == "photos"
    assert p.home_tab() == "right"
    assert p.tap(50, 120, 100) is None and p.current == "photos"  # a photo tap, not navigation
    assert p.tap(5, 120, 200) is None  # the far edge is not the HOME button's
    assert not p.revealed(300)


def test_hidden_home_button():
    p = nav.Plus(ARMS)
    p.tap(120, 5, 0)  # Weather; its HOME button hides on the bottom edge
    assert p.current == "weather" and p.home_tab() == "down"
    assert p.tap(120, 235, 1000) == nav.REVEAL and p.current == "weather"  # first tap raises it
    assert p.revealed(1500) and p.reveal_age(1500) == 500
    assert p.tap(120, 235, 2000) == nav.HOME  # second tap, while it's up: Home
    assert not p.revealed(2001)


def test_home_button_hides_again():
    p = nav.Plus(ARMS)
    p.tap(235, 120, 0)  # Brick
    assert p.tap(5, 120, 1000) == nav.REVEAL
    later = 1000 + nav.REVEAL_MS
    assert not p.revealed(later)
    assert p.tap(5, 120, later) == nav.REVEAL and p.current == "brick"  # raised again, not Home


def test_ticks_diff_wraps():
    top = (1 << 30) - 100
    assert nav.ticks_diff(50, top) == 150
    assert nav.ticks_diff(top, 50) == -150
    p = nav.Plus(ARMS)
    p.tap(235, 120, 0)
    assert p.tap(5, 120, top) == nav.REVEAL
    assert p.revealed(50)  # 150 ms later, across the wrap


# --- photosync -----------------------------------------------------------
def man(*photos):
    return {
        "version": 1,
        "photos": [{"id": i, "rev": r, "size": 240, "rgb565": "/photo/%s.rgb565?v=%d" % (i, r)} for i, r in photos],
    }


def test_sync_plan_fetches_new_and_keeps_order():
    order, fetch, delete = photosync.plan(man(("a", 1), ("b", 2), ("c", 3)), ["c-3.rgb565", "a-1.rgb565"])
    assert order == ["a-1.rgb565", "c-3.rgb565"]  # the manifest's order, not the card's
    assert [p["id"] for p in fetch] == ["b"] and delete == []


def test_sync_plan_drops_deleted_and_recropped():
    order, fetch, delete = photosync.plan(man(("a", 5)), ["a-1.rgb565", "gone-2.rgb565"])
    assert order == [] and [p["id"] for p in fetch] == ["a"]  # a re-crop is a new rev: fetched again
    assert delete == ["a-1.rgb565", "gone-2.rgb565"]


def test_sync_plan_skips_frames_of_another_size():
    m = man(("a", 1), ("b", 2))
    m["photos"][1]["size"] = 480  # not redone at 240 yet
    del m["photos"][0]["size"]  # a manifest from before sizes: 480
    assert photosync.plan(m, ["b-2.rgb565"]) == ([], [], ["b-2.rgb565"])


def test_sync_plan_empty_manifest_clears_the_card():
    assert photosync.plan({"photos": []}, ["a-1.rgb565"]) == ([], [], ["a-1.rgb565"])


def test_sync_settings_parse():
    s = conf.load("device/settings.yaml", {})
    assert s["photo_feed"] == "https://presto.brick.nozdormu.cloud" and s["feed_minutes"] == 10


# --- retry (Wi-Fi, presto#16) ---------------------------------------------
def retries(b, up_at, until_ms, step_ms=1000, start=0):
    """The times step() said retry, polling every step_ms; up_at(t) says whether the link is up."""
    return [t for t in range(start, until_ms, step_ms) if b.step(up_at(t), t)]


def test_backoff_waits_then_doubles_to_a_cap():
    b = retry.Backoff()
    got = retries(b, lambda t: False, 1_200_000)
    assert got[:5] == [20000, 50000, 110000, 230000, 470000]  # grace, then +30, +60, +120, +240 s
    assert got[5] - got[4] == 300000 and got[6] - got[5] == 300000  # capped at 5 min
    assert b.attempts == len(got)


def test_backoff_quiet_while_up_and_resets():
    b = retry.Backoff()
    assert retries(b, lambda t: True, 600_000) == [] and b.down_ms(5) is None
    retries(b, lambda t: False, 200_000)  # down a while: the wait has grown
    assert b.attempts == 3 and b.down_ms(200_000) == 200_000
    assert not b.step(True, 200_000) and b.attempts == 0  # back up: all reset
    assert retries(b, lambda t: False, 260_000, start=201_000) == [221_000, 251_000]  # grace and 30 s again


def test_backoff_a_short_drop_needs_no_retry():
    b = retry.Backoff()
    assert retries(b, lambda t: not (5000 <= t < 15000), 100_000) == []  # back within the grace


def test_backoff_across_the_ticks_wrap():
    top = (1 << 30) - 5000
    b = retry.Backoff()
    assert not b.step(False, top)
    assert not b.step(False, 10000)  # 15 s later, across the wrap: still in the grace
    assert b.step(False, 15001) and b.down_ms(15001) == 20001


# --- Home's arrows (presto#11) --------------------------------------------
def test_turn_points_each_way():
    up = (".A.", "ABA")
    assert sprites.turn(up, "up") == up
    assert sprites.turn(up, "down") == ("ABA", ".A.")
    assert sprites.turn(up, "left") == (".A", "AB", ".A")
    assert sprites.turn(up, "right") == ("A.", "BA", "A.")


def test_arrow_is_symmetric():
    for row in sprites.ARROW:
        assert row == row[::-1]


def test_arrows_sit_in_their_tap_bands():
    from neon import home

    s = home.ARROW_SCALE
    for way, (x, y) in home.ARROW_AT.items():
        w, h = sprites.size(sprites.turn(sprites.ARROW, way))
        x1, y1 = x + w * s - 1, y + h * s - 1
        assert 0 <= x and x1 < 240 and 0 <= y and y1 < 240, way
        # both corners tap through to the arrow's own arm, so all of it does
        assert nav.zone(x, y) == way and nav.zone(x1, y1) == way, way


# --- the Brick uplink (presto#5) --------------------------------------------
from neon import uplink  # noqa: E402

FEED = {
    "at": 1, "age": 5,
    "nodes": [{"n": "brick420", "ok": 1, "k8s": 1, "cpu": 3, "mem": 23, "temp": 47},
              {"n": "brick666", "ok": 0, "k8s": 1},
              {"n": "brick9000", "ok": 0, "k8s": 0}],
    "apps": {"n": 21, "bad": [["chucks-wisdom", "OutOfSync", "Healthy"]], "nbad": 1},
    "pods": {"bad": [["chuck/reader-abc", "CrashLoopBackOff"]], "nbad": 1},
    "door": {"ok": 1, "e5xx": 0},
    "temps": [{"n": "Bedroom", "c": 24.6}, {"n": "My ecobee", "c": 22.2}],
}


def test_uplink_cluster_page():
    lines = uplink.cluster(FEED)
    text = [t for t, _ in lines]
    assert text[1] == "  BRICK420    3% 23%  47C"
    assert ("  BRICK666   NOT READY", uplink.BAD) in lines
    assert ("  BRICK9000  DOWN", uplink.BAD) in lines
    assert ("> ARGO  20/21 SYNCED", uplink.BAD) in lines
    assert ("  CHUCK/READER-ABC    CRASHLOOP", uplink.BAD) in lines
    assert ("> DOOR  UP", uplink.H) in lines


def test_uplink_lines_fit_the_panel():
    worst = dict(FEED)
    worst["nodes"] = [{"n": "brick%d" % i, "ok": 1, "k8s": 1, "cpu": 100, "mem": 100, "temp": 100} for i in range(5)]
    worst["apps"] = {"n": 30, "bad": [["a-very-long-application-name", "OutOfSync", "Degraded"]] * 4, "nbad": 9}
    worst["pods"] = {"bad": [["namespace/a-very-long-pod-name", "CreateContainerConfigError"]] * 4, "nbad": 9}
    worst["door"] = {"ok": 1, "e5xx": 12345}
    lines = uplink.cluster(worst)
    assert len(lines) <= (204 - 10) // 11  # the Brick panel's rows
    assert max(len(t) for t, _ in lines) * 6 <= 200 - 8  # 6 px a character at scale 1


def test_uplink_rooms_page_converts_and_adds_the_presto():
    lines = uplink.rooms(FEED, True, 71.04)
    assert [t for t, _ in lines] == ["> INDOOR", "  BEDROOM        76.3F", "  MY ECOBEE      72.0F", "  PRESTO         71.0F"]
    assert uplink.rooms({"temps": []}, False)[1][0] == "  NO SENSORS ONLINE"


def test_uplink_health_flags_and_stale():
    assert uplink.health(FEED) == [True, False, False, False, False, True]
    assert uplink.health(None) == []
    assert not uplink.stale(FEED, 10) and uplink.stale(FEED, 999) and uplink.stale(dict(FEED, age=999), 10)
    assert uplink.cluster(None)[1] == ("  NO FEED YET", uplink.BAD)


def test_brick_feed_setting():
    assert conf.load("device/settings.yaml", {})["brick_feed"] == "https://presto.brick.nozdormu.cloud/brick.json"
