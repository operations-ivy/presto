import calendar
import datetime as dt
import types

import pytest
from neon import conf, mirror, nav, photosync, retry, sightings, sprites, tz, uplink, wx


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


# --- the back LEDs mirror plasma ------------------------------------------

PLASMA = {  # plasma's GET /, as brick-cicd-config's plasma-look serves it
    "showing": {"source": "board", "pattern": "calm", "priority": 90, "look": {"effect": "pulse", "seconds": 4}},
    "signals": [
        {"source": "board", "pattern": "calm", "priority": 90, "look": {"effect": "pulse", "seconds": 4}},
        {
            "source": "jenkins-build-image",
            "pattern": "stage-build",
            "priority": 50,
            "look": {"effect": "rainbow", "seconds": 0.75},
        },
        {"source": "board-buttons", "pattern": "buttons", "priority": 10, "look": None},
    ],
}


def test_mirror_picks_the_strongest_signal_with_a_look_but_not_the_boards():
    assert mirror.pick(PLASMA) == {"effect": "rainbow", "seconds": 0.75}
    assert mirror.pick({"signals": [PLASMA["signals"][0]]}) is None
    assert mirror.pick({"signals": []}) is None and mirror.pick(None) is None
    assert mirror.pick({"signals": [{"source": "old-plasma", "pattern": "x"}]}) is None  # before plasma-look


@pytest.mark.parametrize(
    "effect",
    [
        "solid",
        "pulse",
        "flash",
        "chase",
        "stream",
        "rush",
        "scanner",
        "converge",
        "fill",
        "rainbow",
        "nightly",
        "boil",
        "something-new",
    ],
)
@pytest.mark.parametrize("t", [0, 137, 500, 1999])
def test_mirror_every_effect_gives_seven_dim_colours(effect, t):
    leds = mirror.frame({"effect": effect, "color": "#00ff00", "seconds": 2}, t, level=0.5)
    assert len(leds) == 7
    assert all(0 <= v <= 127 for c in leds for v in c), leds


def test_mirror_effects_move():
    look = {"effect": "chase", "color": "#ffffff", "seconds": 0.7}
    brightest = [max(range(7), key=lambda i: mirror.frame(look, t)[i][0]) for t in (0, 100, 200)]
    assert brightest == [0, 1, 2]
    solid = mirror.frame({"effect": "solid", "color": "#ff8000"}, 0)
    assert solid[0] == (int(255 * mirror.LEVEL), int(128 * mirror.LEVEL), 0) and len(set(solid)) == 1
    assert mirror.frame({"effect": "flash", "color": "#ff0000", "seconds": 1}, 600)[0] == (0, 0, 0)


def test_mirror_url_and_colours():
    assert mirror.parse_url("http://brick9000:8766/") == ("brick9000", 8766, "/")
    assert mirror.parse_url("http://plasma/x") == ("plasma", 80, "/x")
    assert mirror.parse_colour("#ff8000") == (255, 128, 0) and mirror.parse_colour(None) == (255, 255, 255)
    assert mirror.hsv(0, 1, 1) == (255, 0, 0) and mirror.hsv(1 / 3, 1, 1)[1] == 255


def test_plasma_url_setting():
    assert conf.load("device/settings.yaml", {})["plasma_url"] == "http://brick9000:8766/"


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
    data = {"current": {"weather_code": 0, "is_day": 1},
            "daily": {"sunrise": ["2026-11-04T06:41"], "sunset": ["2026-11-04T17:03"]}}
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
    [("clear", True, "haze"), ("partly", True, "grey_haze"), ("cloudy", True, "overcast"),
     ("rain", True, "overcast"), ("fog", True, "overcast"), ("snow", True, "overcast"),
     ("storm", True, "overcast"), ("unknown", True, "overcast"), ("clear", False, "night"),
     ("rain", False, "night")],
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
    assert feed.step(types.SimpleNamespace(up=True), -10**6) is False
    assert feed.queue == [] and feed.dl is None
