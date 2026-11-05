"""The upload page at presto.brick.nozdormu.cloud, and the Presto's files.

LAN only and no login (the user's call, 2026-11-03), so the guardrails are
here instead: a size limit per file and for the whole store, uploads and
changes only from the page itself (Origin must match, plus a custom header a
cross-site form can't send), and decoding happens in the converter pod, which
has no network.

    GET  /                     the page
    GET  /api/photos           the library: every photo's record, the store's usage
    POST /api/upload           one photo, raw body (X-Filename, Content-Type)
    POST /api/crop?id=         {"crop": [cx, cy, size]}: redo the photo with this crop
    POST /api/hide?id=         {"hidden": bool}: keep it, but off the frame
    POST /api/delete?id=       remove it
    GET  /preview?id=          PNG: exactly what the Presto shows
    GET  /master?id=           JPEG ~1600 px (the crop editor's picture)
    GET  /manifest.json        what the Presto shows, in order
    GET  /photo/<id>.jpg|.rgb565  the Presto's files
    GET  /device/manifest.json, /device/bundle  the Presto's own code (device.py, presto#9)
    GET  /health /ready /metrics
"""

from __future__ import annotations

import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import brick_observe
from brick_observe import metrics as observe_metrics
from prometheus_client import Counter

from .device import Release
from .store import Store, valid_id

log = brick_observe.get_logger(__name__)
PAGE = (Path(__file__).parent / "static" / "index.html").read_bytes()

MAX_UPLOAD = 60 << 20  # a 48 MP ProRAW-ish HEIC is ~25 MB; this leaves room
UPLOADS = Counter("photos_uploads_total", "Uploads, by result", ["result"])
PHOTO_FILE = re.compile(r"^/photo/([0-9a-f]{16})\.(jpg|rgb565)$")


def device_of(ua: str) -> str:
    """A short, human name for the uploading device, from its User-Agent."""
    for key, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Macintosh", "Mac"),
                      ("Windows", "Windows"), ("Linux", "Linux")):
        if key in ua:
            return name
    return "unknown"


def same_origin(origin: str | None, host: str | None) -> bool:
    if not origin or not host:
        return False
    return origin in (f"https://{host}", f"http://{host}")


SHOWING_FOR_S = 30 * 60  # a report older than this: the Presto isn't on Photos (or is off)


def make_handler(store: Store, release: Release | None = None):
    showing = {}  # what the Presto last said it shows: {"id", "at"} (in memory: one web pod)

    class Handler(brick_observe.httpserver.Observed, BaseHTTPRequestHandler):
        routes = {"/", "/api/photos", "/api/upload", "/api/crop", "/api/hide", "/api/delete", "/preview",
                  "/master", "/manifest.json", "/photo", "/api/showing", "/device/manifest.json", "/device/bundle"}
        server_version = "presto-photos"
        protocol_version = "HTTP/1.1"

        def _brick_observe_route(self):  # /photo/<id>.<ext> is one route, not one per photo
            path = urlsplit(self.path).path
            if PHOTO_FILE.match(path):
                return "/photo"
            return super()._brick_observe_route()

        # --- helpers ---
        def send(self, code: int, body: bytes, ctype: str, cache: str = "no-store") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def json(self, code: int, obj) -> None:
            self.send(code, json.dumps(obj).encode(), "application/json")

        def file(self, path: Path, ctype: str, cache="no-cache") -> None:
            try:
                self.send(200, path.read_bytes(), ctype, cache)
            except FileNotFoundError:
                self.json(404, {"error": "not found"})

        def query_id(self):
            pid = (parse_qs(urlsplit(self.path).query).get("id") or [""])[0]
            return pid if valid_id(pid) else None

        def body_json(self, limit=4096):
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                raise ValueError("body too large")
            return json.loads(self.rfile.read(n) or b"{}")

        def guarded(self) -> bool:
            """Changes only from the page itself: same Origin and our header."""
            if self.headers.get("X-Presto") != "1" or not same_origin(self.headers.get("Origin"),
                                                                       self.headers.get("Host")):
                self.json(403, {"error": "uploads and changes only from the Presto page"})
                return False
            return True

        # --- GET ---
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/":
                return self.send(200, PAGE, "text/html; charset=utf-8", "no-cache")
            if path == "/health":
                return self.send(200, b"ok\n", "text/plain")
            if path == "/ready":
                ok = store.p("meta").is_dir()
                return self.send(200 if ok else 503, b"ok\n" if ok else b"no store\n", "text/plain")
            if path == "/metrics":
                body, ctype = observe_metrics.exposition()
                return self.send(200, body, ctype)
            if path == "/api/photos":
                now = int(time.time())
                on = showing.get("id") if now - showing.get("at", 0) < SHOWING_FOR_S else None
                return self.json(200, {"photos": store.photos(), "usage": store.usage(), "limit": store.limit,
                                       "pending": len(store.pending_uploads()), "now": now, "showing": on})
            if path == "/manifest.json":
                return self.file(store.p("manifest.json"), "application/json")
            if path in ("/device/manifest.json", "/device/bundle"):
                if release is None:
                    return self.json(404, {"error": "no device code in this image"})
                if path.endswith(".json"):
                    return self.json(200, release.manifest())
                return self.send(200, release.bundle(), "application/octet-stream")
            if path in ("/preview", "/master"):
                pid = self.query_id()
                if not pid:
                    return self.json(400, {"error": "bad id"})
                if path == "/preview":
                    return self.file(store.p("previews", pid + ".png"), "image/png")
                return self.file(store.p("masters", pid + ".jpg"), "image/jpeg")
            m = PHOTO_FILE.match(path)
            if m:
                pid, ext = m.groups()
                ctype = "image/jpeg" if ext == "jpg" else "application/octet-stream"
                return self.file(store.p("out", f"{pid}.{ext}"), ctype, "public, max-age=31536000, immutable")
            self.json(404, {"error": "not found"})

        # --- POST ---
        def do_POST(self):
            path = urlsplit(self.path).path
            if not self.guarded():
                return
            if path == "/api/upload":
                return self.upload()
            pid = self.query_id()
            if not pid:
                return self.json(400, {"error": "bad id"})
            m = store.meta(pid)
            if m is None:
                return self.json(404, {"error": "no such photo"})
            try:
                body = self.body_json()
            except ValueError as e:
                return self.json(400, {"error": str(e)})
            if path == "/api/crop":
                crop = body.get("crop")
                if (not isinstance(crop, list) or len(crop) != 3
                        or not all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in crop)):
                    return self.json(400, {"error": "crop is [cx, cy, size], each 0..1"})
                if m.get("status") not in ("ready", "recrop"):
                    return self.json(409, {"error": "not ready yet"})
                m.update(crop=[float(v) for v in crop], status="recrop")
                store.save_meta(m)
                return self.json(202, {"id": pid, "status": "recrop"})
            if path == "/api/hide":
                m["hidden"] = bool(body.get("hidden"))
                store.save_meta(m)
                store.write_manifest()
                return self.json(200, {"id": pid, "hidden": m["hidden"]})
            if path == "/api/showing":  # from the Presto itself (it sends the page's headers)
                showing.update(id=pid, at=int(time.time()))
                return self.json(200, {"id": pid, "showing": True})
            if path == "/api/delete":
                store.delete(pid)
                log.info("photo deleted", photo=pid, device=device_of(self.headers.get("User-Agent", "")))
                return self.json(200, {"id": pid, "deleted": True})
            self.json(404, {"error": "not found"})

        def upload(self):
            try:
                n = int(self.headers.get("Content-Length") or -1)
            except ValueError:
                n = -1
            if n <= 0:
                UPLOADS.labels("empty").inc()
                return self.json(411, {"error": "needs Content-Length"})
            if n > MAX_UPLOAD:
                UPLOADS.labels("too_big").inc()
                return self.json(413, {"error": f"over {MAX_UPLOAD >> 20} MB"})
            if not store.room_for(n):
                UPLOADS.labels("full").inc()
                return self.json(507, {"error": "the photo store is full: delete some photos first"})
            up = store.begin_upload()
            part = store.upload_part(up)
            left = n
            with open(part, "wb") as f:
                while left:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            if left:
                part.unlink(missing_ok=True)
                UPLOADS.labels("cut_short").inc()
                return self.json(400, {"error": "upload cut short"})
            ua = self.headers.get("User-Agent", "")
            info = {
                "filename": (self.headers.get("X-Filename") or "photo")[:200],
                "content_type": (self.headers.get("Content-Type") or "")[:100],
                "bytes": n,
                "device": device_of(ua),
                "from": (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip(),
                "received": int(time.time()),
            }
            store.finish_upload(up, info)
            UPLOADS.labels("ok").inc()
            log.info("upload received", upload=up, bytes=n, device=info["device"], content_type=info["content_type"])
            self.json(202, {"upload": up, "status": "queued"})

    return Handler


def serve(store: Store, port: int = 8080, release: Release | None = None) -> None:
    httpd = ThreadingHTTPServer(("", port), make_handler(store, release))
    httpd.daemon_threads = True
    log.info("web listening", port=port)
    httpd.serve_forever()
