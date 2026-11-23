"""The picker: one list for every device (picks.py, devices.manifest/catalog_all, /api/media/picks)."""

import contextlib
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from presto_photos import devices, web
from presto_photos.inbox import Inbox
from presto_photos.picks import SHARED, Picks, valid_paths
from presto_photos.store import Store

TAGS = devices.parse(
    """devices:
  retroid:
    tags: [handheld, english_audio_only]
  frame:
    tags: [photos_240]
"""
)
ALL = devices.parse(
    """devices:
  retroid:
    tags: [handheld, english_audio_only]
  rg:
    tags: [handheld, high_graphics]
  board:
    tags: [pi4, medium_graphics, video_480p]
  frame:
    tags: [photos_240]
"""
)


def video(path, kind="movie", small=True, audio=None, series=None):
    item = {"kind": kind, "share": "movies", "path": path, "bytes": 1000, "mtime": 1, "audio": audio}
    if series:
        item["series"] = series
    if small:
        item["variants"] = {
            "480p": {"path": "480p/" + path.split("/", 1)[1].rsplit(".", 1)[0] + ".mp4", "bytes": 100, "mtime": 2}
        }
    return item


INDEX = {
    "updated": 1,
    "items": [
        video("library/movies/Akira (1988).mkv"),
        video("library/movies/New.mkv", small=False),
        video("library/movies/Subbed.mkv", audio="sub"),
        video("library/tv/Kite/Kite.mkv", kind="show", series="tv"),
        {"kind": "music", "share": "music", "path": "library/a.mp3", "bytes": 5, "mtime": 1},
    ],
}


def test_no_picks_means_everything_the_tags_allow():
    m = devices.manifest(TAGS["retroid"], INDEX)
    assert [f["path"] for f in m["files"]] == ["480p/movies/Akira (1988).mp4", "480p/tv/Kite/Kite.mp4"]
    assert m["waiting_for_480p"] == 1 and m["picked"] is False


def test_picks_narrow_the_manifest():
    m = devices.manifest(TAGS["retroid"], INDEX, picks={"library/tv/Kite/Kite.mkv", "library/movies/New.mkv"})
    assert [f["path"] for f in m["files"]] == ["480p/tv/Kite/Kite.mp4"]
    assert m["waiting_for_480p"] == 1 and m["picked"] is True


def test_the_catalog_lists_what_the_device_can_take():
    cat = devices.catalog(TAGS["retroid"], INDEX, picks={"library/movies/New.mkv"})
    by_path = {i["path"]: i for i in cat["items"]}
    assert set(by_path) == {"library/movies/Akira (1988).mkv", "library/movies/New.mkv", "library/tv/Kite/Kite.mkv"}
    assert by_path["library/movies/New.mkv"] == {
        "path": "library/movies/New.mkv",
        "kind": "movie",
        "series": None,
        "bytes": 1000,
        "waiting": True,
        "audio": None,
        "picked": True,
    }
    assert by_path["library/tv/Kite/Kite.mkv"]["series"] == "Kite"
    assert by_path["library/tv/Kite/Kite.mkv"]["bytes"] == 100 and not by_path["library/tv/Kite/Kite.mkv"]["picked"]
    assert all(i["picked"] for i in devices.catalog(TAGS["retroid"], INDEX)["items"])
    assert devices.catalog(TAGS["frame"], INDEX)["items"] == []


def test_one_catalog_for_every_device():
    cat = devices.catalog_all(ALL, INDEX, picks={"library/movies/New.mkv"})
    assert cat["devices"] == ["board", "retroid", "rg"] and cat["picked"] is True
    by_path = {i["path"]: i for i in cat["items"]}
    assert set(by_path) == {
        "library/movies/Akira (1988).mkv",
        "library/movies/New.mkv",
        "library/movies/Subbed.mkv",
        "library/tv/Kite/Kite.mkv",
    }
    assert by_path["library/movies/Subbed.mkv"]["devices"] == ["board", "rg"]  # the Retroid skips subtitled-only
    assert by_path["library/movies/Akira (1988).mkv"]["devices"] == ["board", "retroid", "rg"]
    assert by_path["library/movies/Akira (1988).mkv"]["bytes"] == 100  # the 480p copy's size
    assert by_path["library/movies/New.mkv"]["picked"] and not by_path["library/tv/Kite/Kite.mkv"]["picked"]
    assert devices.pickable_paths_all(ALL, INDEX) == set(by_path)


def test_video_480p_takes_the_480p_copies():
    m = devices.manifest(ALL["board"], INDEX)
    assert [f["path"] for f in m["files"]] == [
        "480p/movies/Akira (1988).mp4",
        "480p/movies/Subbed.mp4",
        "480p/tv/Kite/Kite.mp4",
    ]


def test_one_devices_picks_become_the_shared_list(tmp_path):
    p = Picks(tmp_path / "picks.json")
    assert p.adopt() is None  # nothing saved
    p.set("rg476h", {"a", "b"}, now=5)
    assert p.adopt() == "rg476h"
    assert p.get(SHARED) == {"a", "b"} and p.updated(SHARED) == 5 and p.get("rg476h") is None
    assert p.adopt() is None  # once
    q = Picks(tmp_path / "two.json")
    q.set("rg476h", {"a"})
    q.set("retroid", {"b"})
    assert q.adopt() is None and q.get(SHARED) is None  # two lists: no guessing which


def test_picks_are_saved_and_forgotten(tmp_path):
    p = Picks(tmp_path / "picks.json")
    assert p.get("retroid") is None
    p.set("retroid", {"b", "a"}, now=5)
    assert p.get("retroid") == {"a", "b"} and p.updated("retroid") == 5
    assert Picks(tmp_path / "picks.json").get("retroid") == {"a", "b"}
    p.set("retroid", None)
    assert p.get("retroid") is None


@pytest.mark.parametrize("raw", [None, "a", [1], ["nope"], ["a"] * 20001])
def test_bad_picks_are_refused(raw):
    with pytest.raises(ValueError):
        valid_paths(raw, {"a"})


@contextlib.contextmanager
def serve(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def req(host, path, body=None, guarded=True):
    headers = {"X-Presto": "1", "Origin": f"http://{host}"} if guarded else {}
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(f"http://{host}{path}", data=data, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture
def host(tmp_path):
    inbox = Inbox(tmp_path / "media", limit_bytes=1 << 20, min_free_bytes=0, create=True)
    inbox.write_index(INDEX)
    with serve(web.make_handler(Store(tmp_path / "photos"), None, inbox, ALL)) as h:
        yield h


def test_the_page_picks_and_every_device_follows(host):
    with urllib.request.urlopen(f"http://{host}/media/devices", timeout=10) as resp:
        assert b"MEDIA PICKS" in resp.read()
    status, cat = req(host, "/api/media/catalog")
    assert status == 200 and cat["picked"] is False and len(cat["items"]) == 4
    picks = ["library/tv/Kite/Kite.mkv", "library/movies/Subbed.mkv"]
    assert req(host, "/api/media/picks", {"paths": picks}) == (200, {"picked": 2})
    _, m = req(host, "/api/media/sync?device=retroid")
    assert [f["path"] for f in m["files"]] == ["480p/tv/Kite/Kite.mp4"]  # not Subbed: its tags
    _, m = req(host, "/api/media/sync?device=board")
    assert [f["path"] for f in m["files"]] == ["480p/movies/Subbed.mp4", "480p/tv/Kite/Kite.mp4"]
    _, cat = req(host, "/api/media/catalog")
    assert cat["picked"] is True and cat["updated"]
    assert req(host, "/api/media/picks", {"reset": True})[1]["picked"] is None
    _, m = req(host, "/api/media/sync?device=retroid")
    assert len(m["files"]) == 2


def test_picks_refuse_strangers_and_unknown_paths(host):
    assert req(host, "/api/media/picks", {"paths": []}, guarded=False)[0] == 403
    assert req(host, "/api/media/picks", {"paths": ["library/a.mp3"]})[0] == 400
    assert req(host, "/api/media/picks", {"paths": "x"})[0] == 400
    assert req(host, "/api/media/sync?device=nobody")[0] == 404
