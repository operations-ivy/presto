import calendar
import datetime as dt
import errno
import types

import pytest
from neon import card, conf, hue, nav, photosync, retry, sightings, sprites, tz, uplink, wx


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
    assert s["arm_down"] == "lights" and s["lights"] == "light.lamp"


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


# --- Home's arrows (presto#29) -----------------------------------------------


@pytest.mark.parametrize("pulse", nav.ARROW_PULSES)
@pytest.mark.parametrize("t", [0, 100, 450, 1234, 1799, 2599, 9999])
def test_arrow_levels_stay_in_range(pulse, t):
    assert all(0.0 <= nav.arrow_level(pulse, t, i) <= 1.0 for i in range(4))


def test_faint_is_hardly_a_pulse():
    levels = [nav.arrow_level("faint", t, 0) for t in range(0, 12000, 100)]
    assert max(levels) - min(levels) <= 0.2 and max(levels) <= 0.5
    assert len({nav.arrow_level("faint", 5000, i) for i in range(4)}) == 1  # all four together


def test_the_arrow_is_small():
    assert sprites.size(sprites.ARROW) == (9, 5)


def test_the_chase_runs_clockwise():
    step = nav.CHASE_STEP_MS
    brightest = [max(range(4), key=lambda i: nav.arrow_level("chase", t, i)) for t in (0, step, 2 * step, 3 * step)]
    assert [nav.ARROW_WAYS[i] for i in brightest] == ["up", "right", "down", "left"]


def test_the_chase_never_goes_dark():
    assert min(nav.arrow_level("chase", t, i) for t in range(0, 1800, 50) for i in range(4)) >= nav.ARROW_DIM


@pytest.mark.parametrize("t, lit", [(50, True), (180, False), (300, True), (500, False), (2000, False), (2530, True)])
def test_the_blink_is_twice_then_rest(t, lit):
    assert (nav.arrow_level("blink", t, 0) == 1.0) == lit


def test_vector_text_sizes_its_capitals_and_restores_the_pixel_font():
    from neon import fx

    calls = []

    class Display:
        def set_font(self, name):
            calls.append(("font", name))

        def text(self, s, x, y, wrap, scale):
            calls.append(("text", s, x, y, wrap, scale))

    fx.vector_text(Display(), "THU NOV 6", 14, 71, 10)
    font, (_, s, x, y, wrap, k), restore = calls
    assert font == ("font", "sans") and restore == ("font", "bitmap8")
    assert abs(k * fx.VECTOR_CAP - 10) < 1e-9  # capitals 10 px: 1.25x the pixel font's 8
    assert y == 71 + round(fx.VECTOR_TOP * k)  # the origin sits below the capitals' top


def test_vector_width_measures_in_the_vector_font_and_restores_the_pixel_font():
    from neon import fx

    fonts = []

    class Display:
        def set_font(self, name):
            fonts.append(name)

        def measure_text(self, s, scale):
            assert fonts[-1] == "sans"
            return int(len(s) * 20 * scale)

    assert fx.vector_width(Display(), "52F CLOUDY", 10) == int(10 * 20 * 10 / fx.VECTOR_CAP)
    assert fonts == ["sans", "bitmap8"]


def test_fit_px_sizes_text_to_a_width_within_its_bounds():
    from neon import fx

    class Display:  # 2 px of width per character per px of capital height
        def set_font(self, name):
            pass

        def measure_text(self, s, scale):
            return int(len(s) * 2 * scale * fx.VECTOR_CAP)

    d = Display()
    assert fx.fit_px(d, "ABCDE", 100, 6, 18) == 10  # 5 chars x 2 x 10 px = 100
    assert fx.fit_px(d, "AB", 100, 6, 18) == 18  # would be 25: held at the top
    assert fx.fit_px(d, "A" * 50, 100, 6, 18) == 6  # would be 1: held at the bottom


def test_arrow_pulse_setting():
    assert conf.load("device/settings.yaml", {})["arrow_pulse"] == "faint"


def test_taps_open_apps_and_come_home():
    p = nav.Plus(ARMS)
    assert p.tap(120, 120) is None  # the middle of Home: Home's own tap
    assert p.tap(120, 235, 0) == "fun"
    p.go(nav.HOME)
    assert p.tap(5, 120, 0) == "photos"
    assert p.home_tab() == "right"
    assert p.tap(50, 120, 100) is None and p.current == "photos"  # a photo tap, not navigation
    assert p.tap(5, 120, 200) is None  # the far edge is not the way Home


def test_one_tap_on_the_edge_facing_home_goes_home():
    p = nav.Plus(ARMS)
    p.tap(120, 5, 0)  # Weather; its arrow is on the bottom edge
    assert p.current == "weather" and p.home_tab() == "down"
    assert p.tap(120, 235, 1000) == nav.HOME and p.current == nav.HOME  # one tap
    p.tap(235, 120, 0)  # Brick: the arrow on the left
    assert p.tap(120, 235, 10) is None and p.current == "brick"  # the other edges are Brick's
    assert p.tap(5, 120, 20) == nav.HOME


def test_ticks_diff_wraps():
    top = (1 << 30) - 100
    assert nav.ticks_diff(50, top) == 150
    assert nav.ticks_diff(top, 50) == -150


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

FEED = {
    "at": 1,
    "age": 5,
    "nodes": [
        {"n": "brick420", "ok": 1, "k8s": 1, "cpu": 3, "mem": 23, "disk": 46, "temp": 47},
        {"n": "brick666", "ok": 0, "k8s": 1},
        {"n": "brick9000", "ok": 0, "k8s": 0},
    ],
    "apps": {"n": 21, "bad": [["chucks-wisdom", "OutOfSync", "Healthy"]], "nbad": 1},
    "pods": {"bad": [["chuck/reader-abc", "CrashLoopBackOff"]], "nbad": 1, "run": 54, "rs": 0},
    "door": {"ok": 1, "e5xx": 0, "rpm": 216},
    "jobs": {"n": 16, "bad": [], "nbad": 0, "last": ["build-branches", 175]},
    "net": {"ok": 1, "dn": 6, "up": 1536},
    "scrape": {"n": 56, "down": [], "ndown": 0},
    "temps": [{"n": "Bedroom", "c": 24.6}, {"n": "My ecobee", "c": 22.2}],
}


def test_uplink_cluster_page():
    lines = uplink.cluster(FEED)
    text = [t for t, _ in lines]
    assert text[0] == "> NODES     CPU MEM DSK TEMP"
    assert text[1] == "  BRICK420   3% 23% 46%  47C"
    assert ("  BRICK666   NOT READY", uplink.BAD) in lines
    assert ("  BRICK9000  DOWN", uplink.BAD) in lines
    assert ("> PODS  54 RUNNING  1 BAD", uplink.BAD) in lines  # one pod crash-looping
    assert ("  CHUCK/READER-ABC    CRASHLOOP", uplink.BAD) in lines
    assert ("> ARGO  20/21 SYNCED", uplink.BAD) in lines
    assert ("> JENKINS  16/16 PASSING", uplink.H) in lines
    assert ("  LAST BUILD-BRANCHES    3M AGO", uplink.OK) in lines
    assert ("> DOOR  UP  216 REQ/M", uplink.H) in lines
    assert ("> NET   DN 6KB/S  UP 1.5MB/S", uplink.H) in lines
    assert ("> SCRAPE  56/56 UP", uplink.H) in lines


def test_uplink_lines_fit_the_panel():
    worst = dict(FEED)
    worst["nodes"] = [
        {"n": "brick%d" % i, "ok": 1, "k8s": 1, "cpu": 100, "mem": 100, "disk": 100, "temp": 100} for i in range(5)
    ]
    worst["pods"] = {
        "bad": [["namespace/a-very-long-pod-name", "CreateContainerConfigError"]] * 4,
        "nbad": 9,
        "run": 999,
        "rs": 999,
    }
    worst["apps"] = {"n": 30, "bad": [["a-very-long-application-name", "OutOfSync", "Degraded"]] * 4, "nbad": 9}
    worst["jobs"] = {
        "n": 16,
        "bad": [["a-very-long-jenkins-job", "UNSTABLE"]] * 4,
        "nbad": 9,
        "last": ["a-very-long-jenkins-job", 999999],
    }
    worst["door"] = {"ok": 1, "e5xx": 12345, "rpm": 99999}
    worst["net"] = {"ok": 1, "dn": 99999, "up": 99999}
    worst["scrape"] = {"n": 99, "down": ["a-very-long-scrape-job-name-xyz"] * 4, "ndown": 9}
    lines = uplink.cluster(worst)
    assert len(lines) <= uplink.ROWS == (204 - 10) // 11  # the Brick panel's rows
    assert max(len(t) for t, _ in lines) * 6 <= 200 - 8, max(lines, key=lambda x: len(x[0]))  # 6 px a character
    assert ("> PODS  999 RUNNING  9 BAD", uplink.BAD) in lines  # trimmed, but counted
    assert all(s in " ".join(t for t, _ in lines) for s in ("PODS", "ARGO", "JENKINS", "DOOR", "NET", "SCRAPE"))


def test_uplink_rooms_page_converts_and_adds_the_presto():
    lines = uplink.rooms(FEED, True, 71.04)
    assert [t for t, _ in lines] == [
        "> INDOOR",
        "  BEDROOM        76.3F",
        "  MY ECOBEE      72.0F",
        "  PRESTO         71.0F",
    ]
    assert uplink.rooms({"temps": []}, False)[1][0] == "  NO SENSORS ONLINE"


def test_uplink_health_flags_and_stale():
    assert uplink.health(FEED) == [True, False, False, False, False, True, True, True, True]
    assert uplink.health(None) == []
    assert not uplink.stale(FEED, 10) and uplink.stale(FEED, 999) and uplink.stale(dict(FEED, age=999), 10)
    assert uplink.cluster(None)[1] == ("  NO FEED YET", uplink.BAD)


def test_brick_feed_setting():
    assert conf.load("device/settings.yaml", {})["brick_feed"] == "https://presto.brick.nozdormu.cloud/brick.json"


# --- the back LEDs follow the lamp ------------------------------------------


def test_leds_follow_the_lamp_by_default():
    s = conf.load("device/settings.yaml", {})
    assert s["leds_light"] == "light.lamp" and s["leds_floor"] == 1.0
    assert "plasma_url" not in s


def test_led_rgb_is_none_until_the_light_is_known():
    h = hue.Hue("http://brick2000:8123", "tok", ["light.lamp"])
    assert h.led_rgb("light.lamp") is None
    h.lights["light.lamp"] = hue.parse_state("light.lamp", {"state": "off", "attributes": {}})
    assert h.led_rgb("light.lamp") == (0, 0, 0)
    h.lights["light.lamp"] = hue.parse_state(
        "light.lamp", {"state": "on", "attributes": {"brightness": 13, "rgb_color": [255, 137, 14]}}
    )
    r, g, b = h.led_rgb("light.lamp", floor=0.6)
    assert r >= int(0.6 * 255) and r > g > b  # a 5 % night light still shows, warm
    h.lights["light.lamp"] = None  # unavailable
    assert h.led_rgb("light.lamp") is None


# --- Home's rare sightings ---------------------------------------------------


def test_sightings_gap_is_the_average_give_or_take_half():
    assert sightings.next_gap_ms(15, 0.0) == 450000 and sightings.next_gap_ms(15, 1.0) == 1350000
    assert sightings.next_gap_ms(15, 0.5) == 900000


def test_sightings_never_the_same_twice_running():
    for last in sightings.KINDS:
        for r in (0.0, 0.3, 0.6, 0.999, 1.0):
            k = sightings.pick(last, r)
            assert k in sightings.KINDS and k != last
    assert {sightings.pick(None, r / 10) for r in range(10)} == set(sightings.KINDS)


def test_sightings_ease():
    assert sightings.ease(0) == 0 and sightings.ease(1) == 1 and abs(sightings.ease(0.5) - 0.5) < 1e-9
    assert sightings.ease(-1) == 0 and sightings.ease(2) == 1


def test_sky_signal_emblem_from_secrets_or_ours():
    mine = ("..K..", ".KKK.", "KKKKK")
    assert sightings.emblem_art(types.SimpleNamespace(EMBLEM=mine)) == mine
    assert sightings.emblem_art(types.SimpleNamespace()) == sprites.EMBLEM
    assert sightings.emblem_art(None) == sprites.EMBLEM
    assert sightings.emblem_art(types.SimpleNamespace(EMBLEM=7)) == sprites.EMBLEM  # not a grid
    assert sightings.emblem_art(types.SimpleNamespace(EMBLEM=("K" * 99,))) == sprites.EMBLEM  # too wide
    assert sightings.emblem_scale(12, 7) == 3 and sightings.emblem_scale(30, 12) == 2
    assert sightings.emblem_scale(30, 20) == 1


# --- Home's sky (2026-11-04: haze by day, grey when the weather is, night after sunset) ---
def test_sunrise_and_sunset_from_the_forecast():
    data = {
        "current": {"weather_code": 0, "is_day": 1},
        "daily": {"sunrise": ["2026-11-04T06:41"], "sunset": ["2026-11-04T17:03"]},
    }
    s = wx.summarize(data)
    assert (s["sunrise"], s["sunset"]) == (401, 1023)
    assert wx.minute_of(None) is None and wx.minute_of("garbage") is None


@pytest.mark.parametrize("minute,day", [(400, False), (401, True), (720, True), (1022, True), (1023, False)])
def test_daytime_switches_at_sunrise_and_sunset(minute, day):
    assert wx.daytime({"sunrise": 401, "sunset": 1023, "is_day": not day}, minute) is day


def test_daytime_falls_back_to_the_forecasts_is_day():
    assert wx.daytime({"is_day": True}, 720) is True  # no sunrise/sunset in an older answer
    assert wx.daytime({"sunrise": 401, "sunset": 1023, "is_day": True}, None) is True  # clock not set
    assert wx.daytime(None, 720) is False


@pytest.mark.parametrize(
    "kind,day,sky",
    [
        ("clear", True, "haze"),
        ("partly", True, "grey_haze"),
        ("cloudy", True, "overcast"),
        ("rain", True, "overcast"),
        ("fog", True, "overcast"),
        ("snow", True, "overcast"),
        ("storm", True, "overcast"),
        ("unknown", True, "overcast"),
        ("clear", False, "night"),
        ("rain", False, "night"),
    ],
)
def test_sky_by_weather_and_time(kind, day, sky):
    assert wx.sky(kind, day) == sky


def test_glyph_rain_draws_n_drops_each_with_its_trail_and_respawns_above():
    from neon import fx

    class Display:
        def __init__(self):
            self.chars = []

        def set_pen(self, pen):
            self.pen = pen

        def character(self, code, x, y, scale):
            self.chars.append((chr(code), x, y, self.pen))

    rain = fx.GlyphDrops(n=30, top=-10, bottom=226)
    pens = (("far", "far-trail"), ("near", "near-trail"), ("head", "head-trail"))
    for _ in range(200):
        d = Display()
        rain.step_draw(d, pens)
        assert len(d.chars) == 60  # 30 drops, a trail glyph behind each
        trails, glyphs = d.chars[0::2], d.chars[1::2]
        for (tc, tx, ty, tpen), (gc, gx, gy, gpen) in zip(trails, glyphs):
            assert tc in fx.GLYPHS and gc in fx.GLYPHS
            assert (tx, ty + 9) == (gx, gy) and tpen == gpen + "-trail"  # one line behind, darker
            assert 0 <= gx <= fx.W - 6 and gy <= 226


def test_sync_survives_a_card_error(monkeypatch):
    """2026-11-05: OSError 5 (EIO) writing the manifest to the card crashed the app."""
    feed = photosync.Feed("")  # no url: nothing touches the card here
    feed.ready = True

    def eio(_last_touch_ms):
        raise OSError(5)

    monkeypatch.setattr(feed, "_step", eio)
    monkeypatch.setattr(photosync.time, "ticks_ms", lambda: 0, raising=False)  # MicroPython's
    monkeypatch.setattr(photosync.time, "ticks_add", lambda a, b: a + b, raising=False)
    remounted = []
    monkeypatch.setattr(card, "failed", remounted.append)
    assert feed.step(types.SimpleNamespace(up=True), -(10**6)) is False
    assert [e.args for e in remounted] == [(5,)]  # the card is remounted for the retry
    assert feed.queue == [] and feed.dl is None


def test_uplink_maintenance_is_calm():
    data = dict(
        FEED,
        nodes=[{"n": "brick666", "ok": 0, "k8s": 1, "maint": 7200}, {"n": "brick1982", "ok": 0, "k8s": 0, "maint": -1}],
    )
    lines = uplink.cluster(data)
    assert ("  BRICK666   MAINT 2H", uplink.OK) in lines
    assert ("  BRICK1982  MAINT NO END", uplink.OK) in lines
    assert uplink.health(data)[:2] == [True, True]  # their rain columns flow


def test_card_remounts_after_an_io_error_not_twice_in_a_row(monkeypatch):
    """2026-11-05: after an EIO every read failed until a restart; a remount fixes it."""
    clock = [100_000]
    calls = []
    monkeypatch.setattr(card.time, "ticks_ms", lambda: clock[0], raising=False)
    monkeypatch.setattr(card.time, "ticks_diff", lambda a, b: a - b, raising=False)
    monkeypatch.setattr(card.os, "umount", lambda path: calls.append(("umount", path)), raising=False)

    def fake_mount():
        calls.append(("mount",))
        return True

    monkeypatch.setattr(card, "mount", fake_mount)
    monkeypatch.setattr(card, "_last", None)
    monkeypatch.setattr(card, "remounts", 0)
    assert not card.failed(ValueError("not the card"))
    assert not card.failed(OSError(2))  # ENOENT: a missing file, not a stuck card
    assert card.failed(OSError(5)) and calls == [("umount", "/sd"), ("mount",)]
    clock[0] += card.GAP_MS - 1
    assert not card.failed(OSError(5))  # too soon
    clock[0] += 2
    assert card.failed(OSError(5)) and card.remounts == 2


# --- hue -----------------------------------------------------------------
LAMP_ON = {
    "state": "on",
    "attributes": {
        "friendly_name": "Lamp",
        "brightness": 128,
        "color_mode": "color_temp",
        "color_temp_kelvin": 2700,
        "rgb_color": [255, 167, 87],
    },
}


def test_hue_parses_on_off_and_unavailable():
    on = hue.parse_state("light.lamp", LAMP_ON)
    assert on["name"] == "Lamp" and on["on"] and abs(on["bri"] - 128 / 255) < 1e-9
    assert on["rgb"] == (255, 167, 87) and on["kelvin"] == 2700
    off = hue.parse_state("light.lamp", {"state": "off", "attributes": {"friendly_name": "Lamp", "brightness": None}})
    assert not off["on"] and off["bri"] == 0.0 and off["rgb"] is None
    assert hue.parse_state("light.lamp", {"state": "unavailable", "attributes": {}}) is None
    assert hue.parse_state("light.lamp", None) is None


def test_hue_leds_stay_bright_even_for_the_night_light():
    on = hue.parse_state("light.lamp", LAMP_ON)
    r, g, b = hue.led_colour(on)
    assert max(r, g, b) >= 0.6 * 255 and r > g > b  # the lamp's warm hue, pushed bright
    night = dict(on, bri=0.05)
    assert max(hue.led_colour(night)) >= int(0.6 * 255)
    full = dict(on, bri=1.0, rgb=(0, 255, 65))
    assert hue.led_colour(full) == (0, 255, 65)
    assert hue.led_colour(dict(on, on=False)) == (0, 0, 0) and hue.led_colour(None) == (0, 0, 0)
    assert hue.led_colour(dict(on, rgb=None)) != (0, 0, 0)  # on without a colour: warm white


def test_hue_bar_hexline_and_url():
    assert hue.bar(0.5, 10) == "[#####.....]" and hue.bar(2, 4) == "[####]" and hue.bar(-1, 3) == "[...]"
    assert hue.hexline(b'{"entity_id"', 4) == "7B 22 65 6E"
    assert hue.parse_url("http://brick2000:8123") == ("brick2000", 8123)
    assert hue.parse_url("http://ha/") == ("ha", 80)


def test_hue_scenes_are_turn_on_data():
    names = [n for n, _, _ in hue.SCENES]
    assert names == ["BRT", "RLX", "HAX", "NITE"]
    for _, data, rgb in hue.SCENES:
        assert 1 <= data["brightness_pct"] <= 100
        assert ("color_temp_kelvin" in data) != ("rgb_color" in data)
        assert len(rgb) == 3 and max(rgb) == 255  # the key's colour, bright


def lamp_state(on=True, brightness=255, **attrs):
    return hue.parse_state(
        "light.lamp", {"state": "on" if on else "off", "attributes": {"brightness": brightness, **attrs}}
    )


def test_scene_on_follows_the_lamps_state():
    scenes = {n: data for n, data, _ in hue.SCENES}
    nite = lamp_state(brightness=13, color_temp_kelvin=2000, color_mode="color_temp")  # 13/255 = 5 %
    assert hue.scene_on(nite, scenes["NITE"])
    assert not hue.scene_on(nite, scenes["BRT"]) and not hue.scene_on(nite, scenes["RLX"])
    hax = lamp_state(brightness=179, rgb_color=[10, 255, 80], color_mode="xy")  # 70 %, a nearby rgb
    assert hue.scene_on(hax, scenes["HAX"]) and not hue.scene_on(hax, scenes["NITE"])
    assert not hue.scene_on(lamp_state(on=False), scenes["BRT"])
    assert not hue.scene_on(None, scenes["BRT"])
    brt_dim = lamp_state(brightness=200, color_temp_kelvin=4000)  # 78 %: not BRT's 100
    assert not hue.scene_on(brt_dim, scenes["BRT"])


def test_hue_without_a_token_or_lights_is_off():
    assert not hue.Hue("http://brick2000:8123", "", ["light.lamp"]).configured
    assert not hue.Hue("http://brick2000:8123", "t", [""]).configured
    assert hue.Hue("http://brick2000:8123", "t", ["light.lamp"]).configured


# --- hue: requests stepped between frames ------------------------------------


class FakeSock:
    """A non-blocking socket: connect is 'in progress', each recv hands out one chunk
    (or 'not yet' first), and what was sent is kept."""

    def __init__(self, reply, waits=1):
        self.reply = [reply[i : i + 7] for i in range(0, len(reply), 7)]
        self.waits = waits
        self.sent = b""
        self.closed = False

    def setblocking(self, flag):
        assert flag is False

    def connect(self, addr):
        raise OSError(errno.EINPROGRESS)

    def send(self, data):
        n = min(5, len(data))
        self.sent += bytes(data[:n])
        return n

    def recv(self, n):
        if self.waits:
            self.waits -= 1
            raise OSError(errno.EAGAIN)
        return self.reply.pop(0) if self.reply else b""

    def close(self):
        self.closed = True


class FakeSockets:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.made = []

    def getaddrinfo(self, host, port):
        return [(2, 1, 0, "", (host, port))]

    def socket(self):
        self.made.append(FakeSock(self.replies.pop(0)))
        return self.made[-1]


NET_UP = types.SimpleNamespace(up=True)


def run_hue(h, passes=200):
    for t in range(passes):
        h.step(NET_UP, t)


def test_exchange_steps_through_connect_send_and_a_chunked_answer():
    sock = FakeSock(b"HTTP/1.0 200 OK\r\nX: y\r\n\r\n{}", waits=2)
    ex = hue.Exchange(sock, ("h", 1), b"GET / HTTP/1.0\r\n\r\n", 0, 1000)
    results = [ex.step(t) for t in range(40)]
    done = [r for r in results if r is not None]
    assert done[0] == (True, "200 OK", b"{}")
    assert sock.sent == b"GET / HTTP/1.0\r\n\r\n"
    assert results.index(done[0]) > 5  # it took many small steps, none of them blocking


def test_exchange_times_out_and_refuses_quietly():
    ex = hue.Exchange(FakeSock(b"", waits=10**6), ("h", 1), b"", 0, 100)
    assert ex.step(50) is None and ex.step(101) == (False, "TIMEOUT", b"")


def test_hue_polls_then_sends_a_call_and_reports_it():
    state = b'HTTP/1.0 200 OK\r\n\r\n{"state": "on", "attributes": {"brightness": 255, "friendly_name": "Lamp"}}'
    socks = FakeSockets(state, b"HTTP/1.0 200 OK\r\n\r\n[]", state)
    h = hue.Hue("http://brick2000:8123", "tok", ["light.lamp"], sockets=socks)
    run_hue(h)
    assert h.lights["light.lamp"]["on"] and h.lights["light.lamp"]["name"] == "Lamp"
    h.send("turn_on", "light.lamp", {"brightness_pct": 40}, "scene.rlx")
    assert h.busy
    run_hue(h)
    assert not h.busy and h.seq == 1
    ok, status, _, body, label = h.last
    assert ok and status == "200 OK" and label == "scene.rlx"
    assert b'"brightness_pct": 40' in body
    call = socks.made[1].sent
    assert call.startswith(b"POST /api/services/light/turn_on HTTP/1.0\r\n") and b"Bearer tok" in call
    assert all(s.closed for s in socks.made)


def test_hue_marks_a_light_unavailable_when_home_assistant_refuses():
    h = hue.Hue(
        "http://brick2000:8123", "bad", ["light.lamp"], sockets=FakeSockets(b"HTTP/1.0 401 Unauthorized\r\n\r\n")
    )
    run_hue(h, 60)
    assert "light.lamp" in h.lights and h.lights["light.lamp"] is None
