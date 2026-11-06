# presto

The brick homelab's [Pimoroni Presto](https://shop.pimoroni.com/products/presto)
(RP2350, 4" 480x480 touchscreen, MicroPython): a desk display with a Home
screen in the middle of a plus and an app on each arm, in a Blade Runner /
Matrix neon style. Later, a photo pipeline and an upload page on the k3s
cluster feed its slideshow.

## The plus

```
              [ WEATHER ]
                   ^
  [ PHOTOS ] <  [ HOME ]  > [ BRICK ]
                   v
                [ ??? ]
```

Navigation is by taps (swipes do nothing): each edge has a 64 px tap band.

| Touch | Does |
| --- | --- |
| On Home, tap an edge (by its small green arrow) | opens that arm's app |
| In an app, tap the edge facing Home (its small arrow) | goes Home |
| Two-finger tap | Home, from anywhere |
| Any other tap | the app's own action (Photos: right half next, left half previous; Home: glitch) |
| No touch for `idle_seconds` | the `idle_screen` (Photos) |

Sound is off by default (`chirp: false`); turned on, it's soft two-note
chimes with a bell envelope at `chirp_volume`.

- **Home**: a rainy city at night. Skyline with flickering windows, a neon
  BRICK sign, a flying car now and then, a neon-tube clock (12- or 24-hour)
  that glitches every 20-30 s, the date and the forecast with an animated
  icon (the room's readings are on Weather). The scene follows the forecast: rain when it
  rains, stars on a clear night, 2049 dust on a hot day.
- **Weather** (up): outside temperature in a haze that's warm or cold, and the
  room's sensors on a flickering hologram panel.
- **Photos** (left): full-resolution JPEGs from `/sd/gallery`.
- **Brick** (right): a Matrix terminal. The cluster feed isn't built yet, so
  for now it shows the Presto itself. Passive only: it never beeps or takes
  over the screen.
- **Lights** (down): `root@presto`, the Hue lights through Home Assistant as a
  hacker's terminal. The big power key toggles (it glows in the lamp's own
  colour), the bar sets the level, the scene keys load BRT, RLX, HAX (phosphor
  green: ACCESS GRANTED) and NITE. Every command shows the bytes it sent and
  Home Assistant's answer with its round trip; the readout decrypts into place
  when the light changes. `lights` in settings lists them (tap the readout to
  cycle). It talks to `ha_url` (plain HTTP on the LAN: a TLS connect stalls the
  Presto ~3 s) as Home Assistant's Presto user, `HA_TOKEN` in `secrets.py`.
  (The old SIGNAL LOST screen is still `fun`.)
- **Transitions**: TV static. The old screen breaks up into noise bars and
  tear lines, and the new one tunes in as the static thins out. The backlight
  follows the light sensor.
- **Back LEDs**: they follow the Hue lamp (`leds_light`), its colour at full
  saturation and at full brightness (`leds_floor` 1.0, whatever the lamp's level), since they
  face the wall; dark when it's off. While Home Assistant can't be reached (or
  there's no `HA_TOKEN`) they change with the screen, at full brightness. A dark
  room only dims the backlight: the LEDs stay on, where they show best.

Style: overwatch's palette (night violet, neon magenta, cyan, amber) plus
Blade Runner's sodium amber, 2049's dust orange and the Matrix's phosphor
green. Inspiration only: all art and layouts are our own (letter-grid pixel
art in `device/lib/neon/sprites.py`, like brick-status's `tools/pixel_art.py`).

## Layout

| Path | What |
| --- | --- |
| `device/main.py` | runs on boot: sets the hostname, `Presto(full_res=True)`, the app |
| `device/lib/neon/` | the app. `app.py` loop, `home.py`, `screens.py`; pure modules (`nav`, `tz`, `conf`, `wx`, `sprites`) are tested on CPython |
| `device/settings.yaml` | settings (flat `key: value`; read by `neon/conf.py`) |
| `tools/deploy` | copy `device/` to the Presto over USB and restart it |
| `tools/presto-photos` | iPhone HEIC/JPEG to 480x480 baseline sRGB JPEGs (the pipeline's first draft) |
| `tests/` | pytest for the pure modules |

## On the device

- `secrets.py` (on the Presto only, never in git): `WIFI_SSID`,
  `WIFI_PASSWORD`, `HA_TOKEN` for the Lights app (home-assistant's
  `scripts/setup` leaves it in `~/.config/home-assistant/presto-token` on the
  laptop: `HA_TOKEN = "..."`), for the forecast `LATITUDE` / `LONGITUDE`, and `SIGNS`: up
  to four names for Home's neon tower signs (default `("BRICK",)`), kept off
  this public repo on purpose; optionally `EMBLEM`, the sky signal's emblem on
  Home (a letter grid like `sprites.EMBLEM`: `K` dark, `.` the light; up to 30
  wide, 20 tall), for a logo that isn't ours to publish. The forecast
  is [Open-Meteo](https://open-meteo.com) (no key); its UTC offset also sets
  the clock, with `utc_offset` + `dst` in settings as the fallback.
- Pimoroni's examples and menu are kept in `/examples` on the flash; move them
  back to `/` to get the menu again.
- Deploy: close Thonny (one program per port), then `tools/deploy`. Needs
  `mpremote`.

Things that have bitten (details in homelab-context `docs/hosts.md`):

- **No fake "long ago" ticks**: `time.ticks_diff(now, -10**9)` isn't a big
  positive number. Ticks wrap at 2^30 ms, so it reads as ~20 hours *ahead*,
  and the sensor stick went unread until the Presto had been up that long. Use
  `None` for "never".
- **No `[::-1]`**: MicroPython slices only with step 1 (`NotImplementedError`
  on the device, fine in the CPython tests). Use `reversed()`.
- **`/boot.py` never runs**: the firmware freezes its own `boot.py`, which
  shadows the flash's. Start-up code goes in `main.py`.
- **`Presto()` defaults to 240x240** (pixel-doubled). Full detail needs
  `full_res=True`; Pimoroni's Photo Frame shows only a 480x480 image's
  top-left quarter.
- **mpremote stops the running app**, and the screen keeps its last frame.
  Use plain `mpremote connect` (it soft-resets first): `resume` against a
  running app stalled the USB serial more than once.
- **Reset while joined, and the next boot's Wi-Fi join fails** (status -1): the
  router turns it away for a while. `tools/deploy` disconnects first; after an
  unplug the app's retry (`neon/retry.py`) has it back in ~20-30 s.
- **Speed** (full_res): a full clear is 45 ms and a full update 24 ms, so a
  full repaint runs at about 10 fps; Home measures 9-10 fps.

## Development

```bash
poetry install
poetry run pytest
pre-commit install
tools/deploy            # Thonny disconnected
```
