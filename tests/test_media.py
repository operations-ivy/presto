"""The media importer (presto#39): naming, device tags, the inbox, the sorter, the API."""

import contextlib
import json
import os
import struct
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

pytest.importorskip("brick_observe")
pytest.importorskip("mutagen")

from mutagen.easyid3 import EasyID3  # noqa: E402

from presto_photos import devices, media, sorter, web  # noqa: E402
from presto_photos.__main__ import default_devices  # noqa: E402
from presto_photos.inbox import Inbox, valid_upload  # noqa: E402
from presto_photos.store import Store  # noqa: E402


# --- naming ----------------------------------------------------------------
@pytest.mark.parametrize(
    "filename,kind,path",
    [
        # movies: a year, junk gone
        ("Akira (1988).mkv", "movie", "library/movies/Akira (1988).mkv"),
        ("Akira.1988.1080p.BluRay.x264-GROUP.mkv", "movie", "library/movies/Akira (1988).mkv"),
        ("[Grp] Perfect_Blue_(1997)_[BD 1080p][ABCD1234].mkv", "movie", "library/movies/Perfect Blue (1997).mkv"),
        ("The.Matrix.1999.REMASTERED.2160p.UHD.mkv", "movie", "library/movies/The Matrix (1999).mkv"),
        ("Blade Runner 2049 (2017).mp4", "movie", "library/movies/Blade Runner 2049 (2017).mp4"),
        ("2001.A.Space.Odyssey.1968.mp4", "movie", "library/movies/2001 A Space Odyssey (1968).mp4"),
        (
            "Star Wars Episode 4 A New Hope (1977).mkv",
            "movie",
            "library/movies/Star Wars Episode 4 A New Hope (1977).mkv",
        ),
        ("Mission: Impossible (1996).MKV", "movie", "library/movies/Mission - Impossible (1996).mkv"),
        ("Movie - 1999.mkv", "movie", "library/movies/Movie (1999).mkv"),  # a year, not episode 1999
        # shows: the anime cleanup's names, S01E02, 1x02, Ep 01
        (
            "Dragon Ball - 001 - Secret Of The DragonBall.mpg",
            "show",
            "library/dragon-ball/Dragon Ball - 001 - Secret Of The DragonBall.mpg",
        ),
        (
            "[SubsPlease] Ronin Warriors - 05 (1080p) [ABCDEF12].mkv",
            "show",
            "library/ronin-warriors/Ronin Warriors - 05.mkv",
        ),
        (
            "Cowboy.Bebop.S01E05.Ballad.of.Fallen.Angels.720p.WEB-DL.mkv",
            "show",
            "library/cowboy-bebop/Cowboy Bebop - S01E05 - Ballad of Fallen Angels.mkv",
        ),
        ("cowboy_bebop_1x05.avi", "show", "library/cowboy-bebop/cowboy bebop - S01E05.avi"),
        ("Ayane-chan High Kick! Ep 2.mp4", "show", "library/ayane-chan-high-kick/Ayane-chan High Kick! - 02.mp4"),
        ("Lupin III Episode 12 - The Heist.mkv", "show", "library/lupin-iii/Lupin III - 12 - The Heist.mkv"),
        ("Dororo (2019) - 01v2 [1080p].mkv", "show", "library/dororo-2019/Dororo (2019) - 01.mkv"),
        # not certain: review, never guessed into the library
        ("Ninja_Scroll.mp4", "review", "review/Ninja_Scroll.mp4"),
        ("1984.mkv", "review", "review/1984.mkv"),
        ("Blade Runner 2049.mkv", "review", "review/Blade Runner 2049.mkv"),
    ],
)
def test_video_names(filename, kind, path):
    p = media.place(filename)
    assert (p.kind, p.path) == (kind, path)
    assert p.share == "movies"


def test_review_says_why():
    assert "year" in media.place("Ninja_Scroll.mp4").reason
    assert media.place("Akira (1988).mkv").reason == ""


@pytest.mark.parametrize(
    "name,want",
    [
        ("Show - 01 [English Dub].mkv", "dub"),
        ("Show - 01 (Dubbed).mp4", "dub"),
        ("Show - 01 [Eng Sub].mkv", "sub"),
        ("Show - 01 [subbed].mkv", "sub"),
        ("Show - 01.mkv", None),
        ("Subway (1985).mkv", None),
        ("Show dub and sub.mkv", None),
    ],
)
def test_audio_hint(name, want):
    assert media.audio_hint(name) == want


@pytest.mark.parametrize(
    "tags,path",
    [
        (
            {"artist": "Daft Punk", "album": "Discovery", "title": "One More Time", "tracknumber": "1/14"},
            "library/Daft Punk/Discovery/01 - One More Time.flac",
        ),
        (
            {
                "artist": "A",
                "albumartist": "Various Artists",
                "album": "Mix: Vol. 2",
                "title": "Song?",
                "tracknumber": "7",
                "discnumber": "2/2",
            },
            "library/Various Artists/Mix - Vol. 2/2-07 - Song.flac",
        ),
        ({"artist": "AC/DC", "title": "Thunderstruck"}, "library/AC DC/Singles/Thunderstruck.flac"),
    ],
)
def test_music_names(tags, path):
    p = media.place("whatever.flac", tags)
    assert (p.kind, p.share, p.path) == ("music", "music", path)


def test_music_without_tags_goes_to_review():
    p = media.place("track01.mp3", {"title": "x"})
    assert (p.kind, p.share, p.path) == ("review", "music", "review/track01.mp3")


def test_not_media_goes_to_review():
    assert media.place("notes.txt").kind == "review"


@pytest.mark.parametrize(
    "raw,want",
    [
        ("a/b\\c", "a b c"),
        ("..hidden", "hidden"),
        ("dots...", "dots"),
        ('x"*<>?|y', "x y"),
        ("", "untitled"),
        ("  spaced   out  ", "spaced out"),
        ("x" * 400, "x" * media.MAX_NAME),
    ],
)
def test_safe_component(raw, want):
    assert media.safe_component(raw) == want


def test_slug():
    assert media.slug("Dragon Ball") == "dragon-ball"
    assert media.slug("Ayane-chan High Kick!") == "ayane-chan-high-kick"
    assert media.slug("Pokémon: The Series") == "pokemon-the-series"
    assert media.slug("!!!") == "unknown"


def test_unique_name():
    assert media.unique_name("Akira (1988).mkv", []) == "Akira (1988).mkv"
    assert media.unique_name("Akira (1988).mkv", ["akira (1988).MKV"]) == "Akira (1988) (2).mkv"  # exFAT: no case
    # a .mp4 of the same name: their 480p copies would collide (brick-k8s-config#112)
    assert media.unique_name("Akira (1988).mkv", ["Akira (1988).mp4", "Akira (1988) (2).avi"]) == "Akira (1988) (3).mkv"
    assert media.unique_name("01 - Song.flac", ["01 - Song.mp3"]) == "01 - Song.flac"  # audio: only the same name


@pytest.mark.parametrize(
    "name,kind",
    [("a.MKV", "video"), ("a.flac", "audio"), ("a.HEIC", "image"), ("a.txt", None), ("noext", None)],
)
def test_media_kind(name, kind):
    assert media.media_kind(name) == kind


# --- device tags -------------------------------------------------------------
def item(kind="movie", path="library/movies/A (2000).mkv", audio=None, small=True):
    it = {
        "kind": kind,
        "share": "music" if kind == "music" else "movies",
        "path": path,
        "bytes": 100,
        "mtime": 1,
        "audio": audio,
    }
    if small and kind != "music":
        stem = path.split("/", 1)[1].rsplit(".", 1)[0]
        it["variants"] = {"480p": {"path": f"480p/{stem}.mp4", "bytes": 10, "mtime": 2}}
    return it


def test_shipped_devices_yaml_is_valid():
    tags = devices.load(default_devices())
    assert set(tags) >= {"brick9000", "rg476h", "retroid", "presto"}
    assert "english_audio_only" in tags["retroid"].tags


@pytest.mark.parametrize(
    "text,err",
    [
        ("devices:\n  x:\n    tags: [hanheld]\n", "unknown tags"),
        ("nope: 1\n", "devices"),
        ("devices:\n  Bad Name:\n    tags: []\n", "device name"),
        ("devices:\n  x:\n    tags: handheld\n", "list"),
    ],
)
def test_devices_yaml_mistakes(text, err):
    with pytest.raises(ValueError, match=err):
        devices.parse(text)


def test_variants_by_tags():
    tags = devices.load(default_devices())
    movie = item()
    assert devices.wants(tags["brick9000"], movie)["path"] == "library/movies/A (2000).mkv"
    assert devices.wants(tags["rg476h"], movie) == {
        "kind": "movie",
        "share": "movies",
        "path": "480p/movies/A (2000).mp4",
        "bytes": 10,
        "mtime": 2,
        "variant": "480p",
    }
    assert devices.wants(tags["presto"], movie) is None
    assert devices.wants(tags["retroid"], item(audio="sub")) is None  # subtitled-only: too small to read
    assert devices.wants(tags["rg476h"], item(audio="sub"))["variant"] == "480p"
    assert devices.wants(tags["retroid"], item(small=False)) == devices.WAITING


def test_manifest():
    tags = devices.parse("devices:\n  r:\n    tags: [handheld, music]\n  p:\n    tags: [photos_240]\n")
    index = {
        "updated": 5,
        "items": [
            item(),
            item(path="library/movies/B (2001).mkv", small=False),
            item("show", "library/x/X - 01.mkv"),
            item("music", "library/A/B/01 - C.flac"),
        ],
    }
    m = devices.manifest(tags["r"], index, now=9)
    assert [f["path"] for f in m["files"]] == [
        "480p/movies/A (2000).mp4",
        "480p/x/X - 01.mp4",
        "library/A/B/01 - C.flac",
    ]
    assert m["waiting_for_480p"] == 1 and m["bytes"] == 120 and m["generated"] == 9 and "photos" not in m
    assert m["tags"] == ["handheld", "music"]
    p = devices.manifest(tags["p"], index)
    assert p["files"] == [] and p["photos"] == {"manifest": "/manifest.json", "size": 240}


# --- the sorter --------------------------------------------------------------
def mp3_bytes() -> bytes:
    """A tiny real MP3: silent MPEG-1 Layer III frames (128 kbps, 44.1 kHz, 417 bytes each)."""
    return (struct.pack(">I", 0xFFFB9004) + bytes(413)) * 8


@pytest.fixture
def drive(tmp_path, monkeypatch):
    monkeypatch.setattr(sorter, "MIN_FREE", 0)
    movies, music = tmp_path / "extra" / "movies", tmp_path / "extra" / "music"
    (movies / "library" / "movies").mkdir(parents=True)
    music.mkdir(parents=True)
    inbox = Inbox(tmp_path / "media", limit_bytes=1 << 30, min_free_bytes=0, create=True)
    return inbox, sorter.Roots(movies, music)


def queue(inbox, filename, data):
    up = inbox.begin({"filename": filename, "bytes": len(data), "device": "Mac"})
    inbox.part(up).write_bytes(data)
    inbox.finish(up)
    return up


def test_sorter_never_overwrites(drive):
    inbox, roots = drive
    (roots.movies / "library/movies/Akira (1988).mp4").write_bytes(b"old")
    up = queue(inbox, "Akira.1988.1080p.mkv", b"new film")
    assert sorter.Sorter(inbox, roots).tick() == 1
    assert inbox.status(up)["path"] == "library/movies/Akira (1988) (2).mkv"  # the .mp4 is kept
    assert (roots.movies / "library/movies/Akira (1988).mp4").read_bytes() == b"old"
    assert (roots.movies / "library/movies/Akira (1988) (2).mkv").read_bytes() == b"new film"


def test_sorter_files_shows_and_review(drive):
    inbox, roots = drive
    show = queue(inbox, "Dragon Ball - 002 - The Emperor's Quest.avi", b"ep")
    unsure = queue(inbox, "Ninja_Scroll.mp4", b"?")
    assert sorter.Sorter(inbox, roots).tick() == 2
    assert inbox.status(show)["kind"] == "show"
    assert (roots.movies / "library/dragon-ball/Dragon Ball - 002 - The Emperor's Quest.avi").read_bytes() == b"ep"
    assert inbox.status(unsure)["status"] == "review" and (roots.movies / "review/Ninja_Scroll.mp4").exists()


def test_sorter_cleans_up(drive):
    inbox, roots = drive
    queue(inbox, "Akira (1988).mkv", b"x")
    sorter.Sorter(inbox, roots).tick()
    assert inbox.pending() == [] and list(inbox.p("inbox").iterdir()) == []
    assert not list(roots.movies.rglob(".*"))  # no .part left behind


def test_index_kinds_and_480p_variants(drive):
    _, roots = drive
    lib, small = roots.movies / "library", roots.movies / "480p"
    (lib / "dragon-ball").mkdir()
    (lib / "dragon-ball/Dragon Ball - 002 - Quest.avi").write_bytes(b"ep")
    (lib / "movies/Akira (1988).mkv").write_bytes(b"film")
    (lib / "Ninja_Scroll.mp4").write_bytes(b"top level: a movie")
    (roots.movies / "review").mkdir()
    (roots.movies / "review/Unsure.mp4").write_bytes(b"?")
    (small / "dragon-ball").mkdir(parents=True)
    (small / "dragon-ball/Dragon Ball - 002 - Quest.mp4").write_bytes(b"s")
    by_path = {i["path"]: i for i in sorter.build_index(roots)["items"]}
    show = by_path["library/dragon-ball/Dragon Ball - 002 - Quest.avi"]
    assert show["kind"] == "show" and show["series"] == "dragon-ball"
    assert show["variants"]["480p"]["path"] == "480p/dragon-ball/Dragon Ball - 002 - Quest.mp4"
    assert by_path["library/movies/Akira (1988).mkv"]["kind"] == "movie"
    assert "variants" not in by_path["library/movies/Akira (1988).mkv"]
    assert by_path["library/Ninja_Scroll.mp4"]["kind"] == "movie"
    assert not any(p.startswith("review/") for p in by_path)  # review isn't in the library


def test_sorter_reads_music_tags(drive):
    inbox, roots = drive
    tmp = inbox.root / "t.mp3"
    tmp.write_bytes(mp3_bytes())
    tags = EasyID3()
    tags.update({"artist": "Daft Punk", "album": "Discovery", "title": "One More Time", "tracknumber": "1/14"})
    tags.save(tmp)
    up = queue(inbox, "01_track.mp3", tmp.read_bytes())
    sorter.Sorter(inbox, roots).tick()
    assert inbox.status(up)["path"] == "library/Daft Punk/Discovery/01 - One More Time.mp3"
    items = sorter.build_index(roots)["items"]
    assert [(i["kind"], i["path"]) for i in items] == [("music", "library/Daft Punk/Discovery/01 - One More Time.mp3")]


def test_sorter_waits_for_the_drive(drive, tmp_path):
    inbox, roots = drive
    up = queue(inbox, "song.flac", b"x")
    assert sorter.Sorter(inbox, sorter.Roots(roots.movies, tmp_path / "nowhere")).tick() == 0
    assert inbox.pending() == [up]  # kept, not dropped


def test_sorter_waits_for_space(drive, monkeypatch):
    inbox, roots = drive
    monkeypatch.setattr(sorter, "MIN_FREE", 1 << 62)
    up = queue(inbox, "Akira (1988).mkv", b"x")
    assert sorter.Sorter(inbox, roots).tick() == 0 and inbox.pending() == [up]


def test_sorter_retries_a_failed_copy(drive, monkeypatch):
    inbox, roots = drive
    up = queue(inbox, "Akira (1988).mkv", b"x")
    s = sorter.Sorter(inbox, roots)
    real = sorter.copy_into

    def boom(*a):
        raise OSError("I/O error")

    monkeypatch.setattr(sorter, "copy_into", boom)
    assert s.tick() == 0 and inbox.pending() == [up]
    monkeypatch.setattr(sorter, "copy_into", real)
    assert s.tick() == 0  # waits RETRY_S
    s.failed_at[up] = 0
    assert s.tick() == 1


def test_sweep(drive):
    inbox, _ = drive
    up = inbox.begin({"filename": "a.mkv", "bytes": 10})
    old = time.time() - 2 * 86400
    os.utime(inbox.part(up), (old, old))
    inbox.record("f" * 32, {"at": int(old) - 30 * 86400})
    assert inbox.sweep() == 1 and inbox.status(up) is None and inbox.recent() == []


def test_a_bad_record_does_not_block_uploads(drive):
    inbox, _ = drive
    inbox.p("inbox", "a" * 32 + ".json").write_text('{"bytes": "lots"}')
    inbox.p("inbox", "b" * 32 + ".json").write_text("[1, 2]")
    inbox.begin({"filename": "a.mkv", "bytes": 10})
    assert inbox.usage() == 10 and inbox.room_for(5)


@pytest.mark.parametrize("up,ok", [("a" * 32, True), ("a" * 32 + "\n", False), ("A" * 32, False), ("../x", False)])
def test_valid_upload(up, ok):
    assert valid_upload(up) is ok


def test_inbox_dir_is_shared_with_the_sorter(tmp_path):
    Inbox(tmp_path, create=True)
    assert (tmp_path / "inbox").stat().st_mode & 0o7777 == 0o2775


# --- the API -----------------------------------------------------------------
@contextlib.contextmanager
def serve(handler):
    """Serve handler on a free port; yields "host:port"."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def site(tmp_path):
    store = Store(tmp_path / "photos", limit_bytes=50 << 20)
    inbox = Inbox(tmp_path / "media", limit_bytes=1 << 20, min_free_bytes=0, create=True)
    tags = devices.parse("devices:\n  retroid:\n    tags: [handheld, english_audio_only]\n")
    with serve(web.make_handler(store, None, inbox, tags)) as host:
        yield inbox, host


@pytest.fixture
def bare_site(tmp_path):
    with serve(web.make_handler(Store(tmp_path))) as host:
        yield host


def req(host, path, data=None, headers=None):
    r = urllib.request.Request(f"http://{host}{path}", data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def page(host):
    return {"X-Presto": "1", "Origin": f"http://{host}"}


def begin(host, filename, n):
    body = json.dumps({"filename": filename, "bytes": n, "type": "video/x-matroska"}).encode()
    return req(host, "/api/media/begin", body, page(host))


def test_chunked_upload_and_resume(site):
    inbox, host = site
    data = os.urandom(3000)
    code, b = begin(host, "Akira (1988).mkv", len(data))
    assert code == 201 and b["offset"] == 0
    up = b["upload"]

    def chunk(off, body):
        return req(host, f"/api/media/chunk?upload={up}&offset={off}", body, page(host))

    assert chunk(0, data[:1000]) == (200, {"upload": up, "status": "receiving", "offset": 1000})
    # a retried chunk the server already has: told where it really is
    assert chunk(0, data[:1000]) == (409, {"error": "offset", "offset": 1000})
    assert req(host, f"/api/media/status?upload={up}") == (200, {"upload": up, "status": "receiving", "offset": 1000})
    assert chunk(1000, data[1000:4000])[0] == 202
    assert inbox.upload_path(up).read_bytes() == data
    assert req(host, f"/api/media/status?upload={up}")[1]["status"] == "queued"
    assert chunk(3000, b"x")[0] == 400  # past the end


def test_media_upload_guards(site):
    _, host = site
    assert req(host, "/api/media/begin", b'{"filename": "a.mkv", "bytes": 5}')[0] == 403  # not from the page
    assert begin(host, "a.jpg", 5)[0] == 415  # photos: the photo flow
    assert begin(host, "a.exe", 5)[0] == 415
    assert begin(host, "a.mkv", 2 << 20)[0] == 507  # over the inbox's cap (1 MB here)
    assert begin(host, "a.mkv", 0)[0] == 400
    assert req(host, "/api/media/chunk?upload=../../x&offset=0", b"x", page(host))[0] == 404
    assert req(host, "/api/media/status?upload=nope")[0] == 404
    assert begin(host, "a.mkv", 600 << 10)[0] == 201
    assert begin(host, "b.mkv", 600 << 10)[0] == 507  # the first one's size is promised already


def test_sync_api(site):
    inbox, host = site
    inbox.write_index(
        {"version": 1, "updated": 7, "items": [item(), item(audio="sub"), item("music", "library/a.flac")]}
    )
    code, m = req(host, "/api/media/sync?device=retroid")
    assert code == 200 and [f["path"] for f in m["files"]] == ["480p/movies/A (2000).mp4"] and m["index_updated"] == 7
    assert req(host, "/api/media/sync?device=nope") == (404, {"error": "no such device", "devices": ["retroid"]})
    _, d = req(host, "/api/media/devices")
    assert d["devices"] == [{"name": "retroid", "tags": ["english_audio_only", "handheld"], "note": ""}]
    assert "photos_240" in d["vocabulary"]
    inbox.record("a" * 32, {"status": "filed", "kind": "movie", "path": "library/movies/A (2000).mkv", "at": 5})
    _, r = req(host, "/api/media/recent")
    assert r["recent"][0]["path"] == "library/movies/A (2000).mkv" and r["limit"] == 1 << 20


def test_no_inbox_no_media(bare_site):
    assert req(bare_site, "/api/media/recent")[0] == 404
    assert begin(bare_site, "a.mkv", 5)[0] == 404


@pytest.mark.parametrize(
    "filename,path",
    [
        ("Kite - 01 (Director's Cut).mkv", "library/kite/Kite - 01 (Director's Cut).mkv"),
        ("Dream Hunter Rem - 01 (OnDeed v2).mkv", "library/dream-hunter-rem/Dream Hunter Rem - 01 (OnDeed v2).mkv"),
        (
            "Tetsuwan.Birdy.1996.OVA.02.[DVD.h264.AC3].mkv",
            "review/Tetsuwan.Birdy.1996.OVA.02.[DVD.h264.AC3].mkv",
        ),  # review keeps the name
    ],
)
def test_names_from_the_real_library(filename, path):
    assert media.place(filename).path == path
