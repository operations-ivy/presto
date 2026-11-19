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
- **Weather** (up): the weather at a glance. The conditions fill the screen (rain
  streaks, drifting snow, fog banks, storms with lightning, clouds on the wind, the
  sun's turning rays by day or the moon and stars at night) under Home's sky, with
  the temperature outside, a forecast panel (high, low, chance of rain) and every
  room on a flickering hologram panel: each indoor sensor Home Assistant has online
  (presto-feed's, as on Brick's ROOMS) plus the Presto's own. An offline sensor
  isn't listed; more than four turn in pages.
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
- **Room sensor on the LAN** (presto#8): `http://presto:8767/room.json` for Home
  Assistant (`up`, `temperature_f`, `humidity`, `pressure_hpa`, `lux`; nulls and
  `up: false` once the last reading is 5 minutes old) and `/metrics` for Prometheus
  (`presto_room_*`). Served between frames without blocking; `room_port: 0` turns it off.
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
| `device/lib/neon/roomhttp.py` | `/room.json` and `/metrics` on port 8767 (presto#8) |
| `device/lib/neon/weatherfx.py` | the Weather screen's animated sky (presto#23) |
| `device/settings.yaml` | settings (flat `key: value`; read by `neon/conf.py`) |
| `tools/deploy` | copy `device/` to the Presto over USB and restart it |
| `tools/presto-photos` | iPhone HEIC/JPEG to 480x480 baseline sRGB JPEGs (the pipeline's first draft) |
| `presto_photos/` | the cluster side: upload page (`web.py`), photo converter (`worker.py`), media sorter (`sorter.py`), presto-feed |
| `presto_photos/media.py` | media kinds and clean names (pure) |
| `presto_photos/devices.py` | device tags and each device's sync manifest (pure) |
| `k8s_config/` | the `presto` namespace for Argo CD; `devices.yaml` is the media device tags |
| `tests/` | pytest for the pure modules and the services |

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

## Duplicate photos (presto#19)

A photo's id is the first 16 hex digits of its SHA-256, so its record in the
store's `meta/` (on the `presto-photos` volume) is also the record of what's
been uploaded. Before it sends a photo, the upload page hashes the file in the
browser and asks `GET /api/photos/known?sha=<64 hex>`; a photo already in the
store (on the frame, hidden, or converting) isn't sent, and the queue says
ALREADY ON THE FRAME (or IN THE STORE (HIDDEN)). The page hashes one file at a
time, so a big pick doesn't fill a phone's memory.

The server checks too, while the upload arrives: a duplicate is answered
`200 {"status": "duplicate"}` and never queued (a page that can't hash, an old
tab, a script). Counted as `photos_uploads_total{result="duplicate"}`.

- A deleted photo leaves the record, so it can be uploaded again.
- A failed one can be retried: a newer converter may manage it.
- It's the exact file: the same picture re-saved or resized is a new photo.
  Audio and video aren't checked yet (presto#47).

## Media importer (presto#39)

The upload page (presto.brick.nozdormu.cloud) takes audio and video as well as
photos. Photos go through the photo converter as before. Audio and video go
to their own volume, the media inbox (`presto-media-inbox`, 20 GB for all
uploads together, 20 GB a file), in 16 MB chunks: a film is many short
requests, each well inside the proxies' timeouts, and a dropped connection
resumes where it stopped. The sorter (`presto-media-sort`) then files each
one on brick2000's media drive and the page shows where it went.

### Kinds and names

| Kind | When | Where (on the drive) |
| --- | --- | --- |
| show | a video with an episode number: `S01E02`, `1x02`, `Show - 01`, `Show Ep 01`, `Episode 01` | `/extra/movies/library/<series-slug>/Show - 01 - Title.ext` (`Show - S01E02 - Title.ext` with a season) |
| movie | a video with a year: `Title (1988)`, `Title.1988.1080p...` | `/extra/movies/library/movies/Title (Year).ext` |
| music | audio with artist and title tags (mutagen) | `/extra/music/library/<Artist>/<Album>/NN - Title.ext` (`D-NN` on multi-disc albums; `Singles` without an album; album artist first) |
| review | anything else: no episode number and no year, a number after the year (`Birdy 1996 OVA 02`), audio without tags | `/extra/movies/review/` or `/extra/music/review/`, under its own name |
| photo | `.heic .heif .jpg .jpeg .png` | the photo store, as before |

Names follow the anime cleanup (2026-11-14). Release noise goes: `[Group]`
and `[hash]` tags, and everything from the first release token (`1080p`,
`x264`, `BluRay`, `WEB-DL`, `AAC`...); underscores, and dots in names without
spaces, become spaces. Characters exFAT or Samba refuse go (`:` becomes ` - `),
as do leading dots (the 480p converter skips dotfiles). Nothing is ever
overwritten: a taken name gets ` (2)`, without regard to case (exFAT ignores
it), and two videos never share a name with different extensions (their 480p
copies would both be `<name>.mp4`: brick-k8s-config#112). A copy lands as a
dotted `.part` and is renamed when complete, so the 480p converter never sees
half a file. Videos in the library get their 480p copy from brick-k8s-config's
`movies-480p` CronJob as before; review doesn't.

A video's `audio` is `dub` or `sub` when its name says so (`[English Dub]`,
`[Eng Sub]`, `subbed`), else unknown (`null`).

### Device tags

`k8s_config/devices.yaml` (the `presto-media-devices` ConfigMap; the image
carries a copy) tags each device. An unknown tag stops the page from starting.

| Tag | Means |
| --- | --- |
| `handheld` | plays video on a small screen: movies and shows, as the 480p copies |
| `640x480` | a 640x480 screen: the 480p copies |
| `medium_graphics` | plays video: movies and shows, originals |
| `high_graphics` | plays video at full size: originals (unless also `handheld`) |
| `english_audio_only` | skips videos known to be subtitled-only (`audio: sub`) |
| `music` | the music library |
| `photos_240` | the Presto's 240x240 photo frames (its own `/manifest.json`) |
| `pi4` | a Raspberry Pi 4; descriptive, no rule |

A small screen gets a video once its 480p copy exists (`waiting_for_480p`
counts the rest).

### API

| Call | Answers |
| --- | --- |
| `POST /api/media/begin` `{"filename", "bytes", "type"}` | `{"upload", "chunk", "offset": 0}`; 415 for anything but audio and video, 413 over 20 GB, 507 when the inbox is full |
| `POST /api/media/chunk?upload=&offset=` (raw body) | `{"offset"}`; 202 when complete; 409 `{"offset"}` when the server has a different count (resume from it) |
| `GET /api/media/status?upload=` | `receiving` (offset), `queued`, or the sorter's record: `filed`/`review`, kind, share, path |
| `GET /api/media/recent` | the last uploads filed, the inbox's usage and cap |
| `GET /api/media/devices` | every device, its tags, the tag vocabulary |
| `GET /api/media/sync?device=<name>` | the device's manifest: each file's `kind`, `share`, `path`, `bytes`, `mtime`, `variant` |

POSTs need the page's guard (same Origin and `X-Presto: 1`), like the photo
calls. A manifest's paths are relative to a Samba share's root: `movies` is
`/extra/movies` (`library/...` or `480p/...`), `music` is `/extra/music`. A
device pulls what it lacks or what changed (size, mtime) and removes what's
no longer listed. The sorter rebuilds the index (`library.json` in the inbox)
every 5 minutes and after each filing, so files added over Samba show up too.

Metrics: `media_uploads_total{kind,result}`, `media_sync_requests_total{device}`
(page); `media_filed_total{kind,result}`, `media_library_files{kind}`,
`media_inbox_waiting`, `media_inbox_bytes` (sorter).

## Development

```bash
poetry install
poetry run pytest
pre-commit install
tools/deploy            # Thonny disconnected
```
