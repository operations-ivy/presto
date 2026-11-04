"""presto-photos: the pipeline, the store, and the web server over real HTTP."""

import io
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

pytest.importorskip("pillow_heif")
pytest.importorskip("brick_observe")

from presto_photos import pipeline, web, worker  # noqa: E402
from presto_photos.store import ORIGINAL_DAYS, Store, photo_id  # noqa: E402

ADOBE = Path("/usr/share/color/icc/colord/AdobeRGB1998.icc")


def sideways(fmt="JPEG", size=(1200, 900)):
    """A landscape image stored sideways (EXIF orientation 6), left edge green:
    upright, the green edge is on top."""
    im = Image.new("RGB", size, (100, 150, 200))
    im.paste((0, 200, 0), (0, 0, size[0] // 10, size[1]))
    ex = Image.Exif()
    ex[0x0112] = 6
    b = io.BytesIO()
    im.save(b, fmt, exif=ex.tobytes(), quality=92)
    return b.getvalue()


def jpeg(size=(800, 600), colour=(120, 120, 120), icc=None):
    b = io.BytesIO()
    kw = {"icc_profile": icc} if icc else {}
    Image.new("RGB", size, colour).save(b, "JPEG", quality=95, **kw)
    return b.getvalue()


# --- pipeline ------------------------------------------------------------
@pytest.mark.parametrize("fmt", ["JPEG", "HEIF"])
def test_rotation_applied_once(fmt):
    lin, src = pipeline.decode(sideways(fmt))
    assert (src.width, src.height) == (900, 1200)  # portrait now
    top = pipeline.linear_to_srgb(lin[10, 450])
    assert top[1] > 0.6 and top[0] < 0.2  # green along the top, not the side


@pytest.mark.skipif(not ADOBE.exists(), reason="no AdobeRGB profile on this machine")
def test_icc_converted_to_srgb():
    lin, src = pipeline.decode(jpeg(colour=(100, 150, 200), icc=ADOBE.read_bytes()))
    assert "Adobe" in src.colour
    got = pipeline.linear_to_srgb(lin[300, 400]) * 255
    assert np.allclose(got, (66, 151, 203), atol=3)  # ImageMagick/LCMS reference


def test_p3_matrix_keeps_white_white():
    assert np.allclose(np.ones(3) @ pipeline.P3_TO_SRGB.T, 1.0, atol=1e-4)


def test_render_outputs():
    lin, _ = pipeline.decode(jpeg((1600, 1200)))
    r = pipeline.render(lin)
    assert len(r.rgb565) == 480 * 480 * 2
    j = Image.open(io.BytesIO(r.jpeg))
    assert j.size == (480, 480) and not j.info.get("progressive") and not j.info.get("progression")
    pv = np.asarray(Image.open(io.BytesIO(r.preview_png)))
    frame = np.frombuffer(r.rgb565, dtype=">u2").reshape(480, 480)
    assert (pv == pipeline.rgb565_to_rgb8(frame)).all()  # the preview is exactly the frame


def test_dither_keeps_brightness():
    img = np.full((64, 64, 3), 0.5, dtype=np.float32)
    rgb = pipeline.rgb565_to_rgb8(pipeline.dither_rgb565(img)).astype(float) / 255
    assert abs(rgb.mean() - 0.5) < 0.01  # error diffusion: right on average, not banded


def test_auto_crop_follows_detail():
    rng = np.random.default_rng(1)
    lin = np.full((300, 900, 3), 0.2, dtype=np.float32)
    lin[:, 650:900] = rng.random((300, 250, 3))  # all the detail on the right
    cx, cy, size = pipeline.auto_crop(lin)
    assert cx > 0.6 and cy == 0.5 and size == 1.0


@pytest.mark.parametrize("crop", [(0.0, 0.0, 1.0), (1.0, 1.0, 0.3), (0.5, 0.5, 2.0)])
def test_crop_box_stays_inside(crop):
    left, top, right, bottom = pipeline.crop_box(900, 600, crop)
    assert 0 <= left < right <= 900 and 0 <= top < bottom <= 600
    assert abs((right - left) - (bottom - top)) < 1e-6


def test_garbage_rejected():
    with pytest.raises(pipeline.Rejected):
        pipeline.decode(b"not an image at all")


# --- store + worker ------------------------------------------------------
def queue(store, data, **info):
    up = store.begin_upload()
    store.upload_part(up).write_bytes(data)
    store.finish_upload(up, {"filename": "x.jpg", "bytes": len(data), **info})
    return up


def test_convert_dedupe_delete(tmp_path):
    s = Store(tmp_path)
    data = jpeg((1000, 700))
    queue(s, data)
    assert worker.tick(s) == 1
    pid = photo_id(data)
    m = s.meta(pid)
    assert m["status"] == "ready" and m["source"]["format"] == "JPEG"
    for f in (("out", pid + ".rgb565"), ("out", pid + ".jpg"), ("previews", pid + ".png"), ("masters", pid + ".jpg")):
        assert s.p(*f).exists()
    assert [p["id"] for p in json.loads(s.p("manifest.json").read_text())["photos"]] == [pid]
    queue(s, data)  # the same photo again: stored once
    worker.tick(s)
    assert len(s.photos()) == 1 and not s.pending_uploads()
    assert s.delete(pid) and not s.photos()
    assert json.loads(s.p("manifest.json").read_text())["photos"] == []


def test_rejected_upload_recorded(tmp_path):
    s = Store(tmp_path)
    queue(s, b"garbage")
    worker.tick(s)
    (m,) = s.photos()
    assert m["status"] == "failed" and "not an image" in m["error"]


def test_recrop_and_expiry(tmp_path):
    s = Store(tmp_path)
    data = jpeg((1200, 600))
    queue(s, data)
    worker.tick(s)
    pid = photo_id(data)
    m = s.meta(pid)
    m.update(crop=[0.2, 0.5, 0.6], status="recrop")
    s.save_meta(m)
    worker.tick(s)
    m = s.meta(pid)
    assert m["status"] == "ready" and m["crop_auto"] is False
    s.expire_originals(now=time.time() + ORIGINAL_DAYS * 86400 + 60)
    m = s.meta(pid)
    assert m["original"] is None and not list(s.p("originals").iterdir())
    m.update(status="recrop")  # without the original, the master is used
    s.save_meta(m)
    worker.tick(s)
    assert s.meta(pid)["status"] == "ready" and "recrop_error" not in s.meta(pid)


# --- web -----------------------------------------------------------------
@pytest.fixture
def site(tmp_path):
    s = Store(tmp_path, limit_bytes=50 << 20)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(s))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host = "127.0.0.1:%d" % httpd.server_address[1]
    yield s, host
    httpd.shutdown()


def req(host, path, data=None, headers=None, method=None):
    r = urllib.request.Request("http://" + host + path, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def page_headers(host, **extra):
    return {"X-Presto": "1", "Origin": "http://" + host, **extra}


def test_page_and_health(site):
    _, host = site
    code, body, _ = req(host, "/")
    assert code == 200 and b"PRESTO" in body
    assert req(host, "/health")[0] == 200 and req(host, "/ready")[0] == 200
    assert b"http_server_requests_total" in req(host, "/metrics")[1]


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-Presto": "1"}, {"Origin": "http://evil.example", "X-Presto": "1"}, {"Origin": "same"}],
)
def test_changes_only_from_the_page(site, headers):
    _, host = site
    if headers.get("Origin") == "same":
        headers = {"Origin": "http://" + host}  # right origin, but no X-Presto (a cross-site form can't add it)
    code, _, _ = req(host, "/api/upload", data=jpeg(), headers=headers)
    assert code == 403


def test_upload_to_presto_files(site):
    s, host = site
    data = jpeg((900, 900), (200, 40, 90))
    code, body, _ = req(host, "/api/upload", data=data,
                        headers=page_headers(host, **{"X-Filename": "IMG_0001.JPG", "Content-Type": "image/jpeg",
                                                      "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)"}))
    assert code == 202
    worker.tick(s)
    pid = photo_id(data)
    lib = json.loads(req(host, "/api/photos")[1])
    (p,) = lib["photos"]
    assert p["id"] == pid and p["upload"]["device"] == "iPhone" and p["downgraded"] is True
    man = json.loads(req(host, "/manifest.json")[1])
    assert man["photos"][0]["rgb565"] == f"/photo/{pid}.rgb565"
    code, frame, h = req(host, f"/photo/{pid}.rgb565")
    assert code == 200 and len(frame) == 460800 and "immutable" in h["Cache-Control"]
    assert req(host, f"/preview?id={pid}")[2]["Content-Type"] == "image/png"
    assert req(host, f"/photo/{'0' * 16}.jpg")[0] == 404
    assert req(host, "/preview?id=../../etc/passwd")[0] == 400


def test_crop_hide_delete(site):
    s, host = site
    data = jpeg((1000, 600))
    req(host, "/api/upload", data=data, headers=page_headers(host))
    worker.tick(s)
    pid = photo_id(data)
    post = lambda path, obj: req(host, path, data=json.dumps(obj).encode(), headers=page_headers(host))  # noqa: E731
    assert post(f"/api/crop?id={pid}", {"crop": [0.5, 0.5, 7]})[0] == 400
    assert post(f"/api/crop?id={pid}", {"crop": [0.3, 0.5, 0.8]})[0] == 202
    worker.tick(s)
    assert s.meta(pid)["crop_auto"] is False
    assert post(f"/api/hide?id={pid}", {"hidden": True})[0] == 200
    assert json.loads(req(host, "/manifest.json")[1])["photos"] == []
    assert post(f"/api/delete?id={pid}", {})[0] == 200
    assert s.meta(pid) is None


def test_limits(site, monkeypatch):
    s, host = site
    monkeypatch.setattr(web, "MAX_UPLOAD", 1000)
    assert req(host, "/api/upload", data=b"x" * 2000, headers=page_headers(host))[0] == 413
    monkeypatch.setattr(web, "MAX_UPLOAD", 60 << 20)
    s.limit = 10
    assert req(host, "/api/upload", data=b"x" * 2000, headers=page_headers(host))[0] == 507


@pytest.mark.parametrize(
    "fmt,info,want",
    [
        ("MPO", {"device": "iPhone", "filename": "IMG_1189.jpeg"}, True),  # what Safari really sent
        ("JPEG", {"device": "unknown", "filename": "IMG_0001.jpeg"}, True),
        ("JPEG", {"device": "Mac", "filename": "IMG_0001.JPG"}, False),  # a camera JPEG
        ("HEIF", {"device": "iPhone", "filename": "IMG_0001.HEIC"}, False),
    ],
)
def test_transcoded(fmt, info, want):
    assert worker.transcoded(fmt, info) is want
