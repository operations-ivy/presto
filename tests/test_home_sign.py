"""Home's pager sign (presto#52, the user 2026-11-22: part of the city, dark until an alert
fires), drawn off the device with tools/render_home.py's stand-in display."""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import render_home  # noqa: E402
from neon import home, nav, theme  # noqa: E402

SIGNS = ["NEON", "CITY", ["ARCADE", 9, "h"]]


@pytest.fixture
def clock(monkeypatch):
    now = {"ms": 0}
    monkeypatch.setattr(time, "ticks_ms", lambda: now["ms"], raising=False)
    monkeypatch.setattr(time, "ticks_add", lambda a, b: a + b, raising=False)
    monkeypatch.setattr(time, "ticks_diff", lambda a, b: a - b, raising=False)
    monkeypatch.setattr(time, "ticks_us", lambda: 1, raising=False)
    return now


def home_with(alerts, clock, at_ms=5000):
    d = render_home.Display()
    ctx = render_home.ctx_for(d, SIGNS, alerts)
    if alerts == "stale":
        clock["ms"] = 10**8
    screen = home.Home(ctx)
    clock["ms"] = max(at_ms, clock["ms"] + 2000)
    screen.draw(d, clock["ms"])
    return screen, d, ctx


def sign_colours(screen, d):
    x, y, w, _ = screen.alert_box
    return {d.img.getpixel((px, py)) for px in range(x + 1, x + w - 1) for py in range(y + 1, y + home.SIGN_H - 1)}


def test_the_sign_is_dark_when_all_is_clear(clock):
    screen, d, _ = home_with("none", clock)
    colours = sign_colours(screen, d)
    assert home.DEAD_TUBE in colours
    assert theme.PALETTE["amber"] not in colours and theme.PALETTE["red"] not in colours


@pytest.mark.parametrize("sev, colour", [("warning", "amber"), ("critical", "red")])
def test_it_lights_in_the_worst_colour(clock, sev, colour):
    screen, d, _ = home_with(sev, clock, at_ms=5000 + 110)  # not in a critical's dropout
    colours = sign_colours(screen, d)
    assert theme.PALETTE[colour] in colours and home.DEAD_TUBE not in colours


def test_stale_is_dark_with_a_question_mark(clock):
    screen, d, _ = home_with("stale", clock)
    colours = sign_colours(screen, d)
    assert home.DEAD_TUBE in colours and theme.PALETTE["faint"] in colours


def test_it_stands_in_the_city_clear_of_the_tap_bands(clock):
    screen, _, _ = home_with("none", clock)
    x, y, w, h = screen.alert_box
    assert nav.EDGE <= x and x + w <= home.W - nav.EDGE
    assert y + h <= home.GROUND
    for px, length in screen.alert_at[2]:
        assert screen._roof(px) == y + home.SIGN_H + length  # each post reaches its roof


def test_a_tap_on_it_opens_alerts_lit_or_not(clock):
    screen, _, ctx = home_with("none", clock)
    x, y, w, h = screen.alert_box
    screen.tap(x + w // 2, y + h // 2)
    assert ctx.goto == nav.ALERTS


def test_no_pager_no_sign(clock):
    screen, _, ctx = home_with("off", clock)
    assert screen.alert_box is None
    screen.tap(160, 150)
    assert ctx.goto is None


def test_a_horizontal_name_sign_stays_on_screen(clock):
    screen, _, _ = home_with("none", clock)
    [(name, sx, _sy, *_rest)] = [s for s in screen.signs if s[7] == "h"]
    assert name == "ARCADE" and 0 <= sx and sx + len(name) * 6 <= home.W
