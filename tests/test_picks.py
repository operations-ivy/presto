"""The handheld picker (picks.py, devices.manifest/catalog, /api/media/picks)."""

import contextlib
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from presto_photos import devices, web
from presto_photos.inbox import Inbox
from presto_photos.picks import Picks, valid_paths
from presto_photos.store import Store

TAGS = devices.parse(
    """devices:
  retroid:
    tags: [handheld, english_audio_only]
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
    with serve(web.make_handler(Store(tmp_path / "photos"), None, inbox, TAGS)) as h:
        yield h


def test_the_page_picks_and_the_sync_follows(host):
    with urllib.request.urlopen(f"http://{host}/media/devices", timeout=10) as resp:
        assert b"HANDHELD PICKS" in resp.read()
    status, cat = req(host, "/api/media/catalog?device=retroid")
    assert status == 200 and cat["picked"] is False and len(cat["items"]) == 3
    assert req(host, "/api/media/picks?device=retroid", {"paths": ["library/tv/Kite/Kite.mkv"]}) == (
        200,
        {"device": "retroid", "picked": 1},
    )
    _, m = req(host, "/api/media/sync?device=retroid")
    assert [f["path"] for f in m["files"]] == ["480p/tv/Kite/Kite.mp4"]
    _, cat = req(host, "/api/media/catalog?device=retroid")
    assert cat["picked"] is True and cat["updated"]
    assert req(host, "/api/media/picks?device=retroid", {"reset": True})[1]["picked"] is None
    _, m = req(host, "/api/media/sync?device=retroid")
    assert len(m["files"]) == 2


def test_picks_refuse_strangers_and_unknown_paths(host):
    assert req(host, "/api/media/picks?device=retroid", {"paths": []}, guarded=False)[0] == 403
    assert req(host, "/api/media/picks?device=nobody", {"paths": []})[0] == 404
    assert req(host, "/api/media/picks?device=retroid", {"paths": ["library/a.mp3"]})[0] == 400
    assert req(host, "/api/media/picks?device=retroid", {"paths": "x"})[0] == 400
    assert req(host, "/api/media/catalog?device=nobody")[0] == 404
