"""The upload page at presto.brick.nozdormu.cloud, and the Presto's files.

LAN only and no login (the user's call, 2026-11-03), so the guardrails are
here instead: a size limit per file and for the whole store, uploads and
changes only from the page itself (Origin must match, plus a custom header a
cross-site form can't send), and decoding happens in the converter pod, which
has no network.

    GET  /                     the page
    GET  /api/photos           the library: every photo's record, the store's usage
    GET  /api/photos/known?sha=   whether a photo (its SHA-256, 64 hex) is in the
                               store already: the page asks before it uploads (presto#19)
    POST /api/upload           one photo, raw body (X-Filename, Content-Type); 200
                               {"status": "duplicate"} when it's in the store already
    POST /api/crop?id=         {"crop": [cx, cy, size]}: redo the photo with this crop
    POST /api/hide?id=         {"hidden": bool}: keep it, but off the frame
    POST /api/delete?id=       remove it
    GET  /preview?id=          PNG: exactly what the Presto shows
    GET  /master?id=           JPEG ~1600 px (the crop editor's picture)
    GET  /manifest.json        what the Presto shows, in order
    GET  /photo/<id>.jpg|.rgb565  the Presto's files
    GET  /device/manifest.json, /device/bundle  the Presto's own code (device.py, presto#9)
    GET  /health /ready /metrics

Media (presto#39): audio and video go to their own inbox (inbox.py), in
chunks, so a film is many short requests (each well inside the proxies'
timeouts) and a dropped one resumes where it stopped. The sorter files them.

    POST /api/media/begin      {"filename", "bytes", "type"}: {"upload", "chunk"}
    POST /api/media/chunk?upload=&offset=   the next bytes (raw body, at most MAX_CHUNK);
                               409 {"offset"} if the server has a different count
    GET  /api/media/status?upload=   receiving (offset), queued, or where it was filed
    GET  /api/media/recent     the last uploads filed, the inbox's usage
    GET  /api/media/devices    every device, its tags, and the tag vocabulary
    GET  /api/media/sync?device=   what that device should hold (devices.py)
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import brick_observe
from brick_observe import metrics as observe_metrics
from prometheus_client import Counter

from . import devices as media_devices
from . import media
from .device import Release
from .inbox import Inbox, valid_upload
from .store import Store, valid_id

log = brick_observe.get_logger(__name__)
PAGE = (Path(__file__).parent / "static" / "index.html").read_bytes()

MAX_UPLOAD = 60 << 20  # a 48 MP ProRAW-ish HEIC is ~25 MB; this leaves room
UPLOADS = Counter("photos_uploads_total", "Uploads, by result", ["result"])
MAX_MEDIA_UPLOAD = 20 << 30  # one file; the inbox's own cap (MEDIA_LIMIT_GB) is for all of them together
CHUNK = 16 << 20  # what the page sends per request: ~10 s on slow Wi-Fi, well inside Traefik's 60 s
MAX_CHUNK = 64 << 20
MEDIA_UPLOADS = Counter("media_uploads_total", "Media uploads, by kind and result", ["kind", "result"])
SYNCS = Counter("media_sync_requests_total", "Sync manifests served, by device", ["device"])
READ_CHUNK = 1 << 20  # request bodies are copied to disk this much at a time
SHA256 = re.compile(r"[0-9a-f]{64}")
PHOTO_FILE = re.compile(r"^/photo/([0-9a-f]{16})\.(jpg|rgb565)$")
SHOWING_FOR_S = 30 * 60  # a report older than this: the Presto isn't on Photos (or is off)
MAX_DELETE = 500  # photos per /api/delete-many (the library's ticked ones)
ID_JSON_BYTES = 24  # one id in a JSON list: 16 hex digits, quotes, a comma, a space and slack
# (User-Agent fragment, the name we show), first match wins
_DEVICE_KEYS = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Macintosh", "Mac"),
    ("Windows", "Windows"),
    ("Linux", "Linux"),
)


def device_of(ua: str) -> str:
    """A short, human name for the uploading device, from its User-Agent."""
    for key, name in _DEVICE_KEYS:
        if key in ua:
            return name
    return "unknown"


def same_origin(origin: str | None, host: str | None) -> bool:
    if not origin or not host:
        return False
    return origin in (f"https://{host}", f"http://{host}")


def make_handler(
    store: Store,
    release: Release | None = None,
    inbox: Inbox | None = None,
    devices: dict[str, media_devices.Device] | None = None,
):
    showing = {}  # what the Presto last said it shows: {"id", "at"} (in memory: one web pod)
    devices = devices or {}
    locks: dict[str, threading.Lock] = {}  # one chunk at a time per upload (the page sends them in order anyway)
    locks_lock = threading.Lock()

    def lock_for(up: str) -> threading.Lock:
        with locks_lock:
            if len(locks) > 1000:  # forget finished uploads' locks
                for k in [k for k, v in locks.items() if not v.locked()]:
                    del locks[k]
            return locks.setdefault(up, threading.Lock())

    class Handler(brick_observe.httpserver.Observed, BaseHTTPRequestHandler):
        routes = {
            "/",
            "/api/photos",
            "/api/photos/known",
            "/api/upload",
            "/api/crop",
            "/api/hide",
            "/api/delete",
            "/api/delete-many",
            "/preview",
            "/master",
            "/manifest.json",
            "/photo",
            "/api/showing",
            "/device/manifest.json",
            "/device/bundle",
            "/api/media/begin",
            "/api/media/chunk",
            "/api/media/status",
            "/api/media/recent",
            "/api/media/devices",
            "/api/media/sync",
        }
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

        def query(self, key: str) -> str:
            return (parse_qs(urlsplit(self.path).query).get(key) or [""])[0]

        def query_id(self) -> str | None:
            pid = self.query("id")
            return pid if valid_id(pid) else None

        def client_ip(self) -> str:
            return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

        def origin_info(self, **extra) -> dict:
            """Who sent an upload, for its record: the device, the address, when."""
            return {
                **extra,
                "device": device_of(self.headers.get("User-Agent", "")),
                "from": self.client_ip(),
                "received": int(time.time()),
            }

        def _read_body_to(self, f, n: int, digest=None) -> int:
            """Copy the next n bytes of the body into f, READ_CHUNK at a time,
            feeding digest (a hashlib object) when given; returns how many
            didn't arrive (0: all of them)."""
            left = n
            while left:
                chunk = self.rfile.read(min(left, READ_CHUNK))
                if not chunk:
                    break
                f.write(chunk)
                if digest is not None:
                    digest.update(chunk)
                left -= len(chunk)
            return left

        def known(self, pid: str) -> dict:
            m = store.known(pid)
            if m is None:
                return {"known": False}
            return {"known": True, "id": pid, "status": m["status"], "hidden": bool(m.get("hidden"))}

        def body_json(self, limit=4096) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                raise ValueError("body too large")
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):  # a list or a number: a 400, not a crash on .get()
                raise ValueError("body must be a JSON object")
            return body

        def guarded(self) -> bool:
            """Changes only from the page itself: same Origin and our header."""
            if self.headers.get("X-Presto") != "1" or not same_origin(
                self.headers.get("Origin"), self.headers.get("Host")
            ):
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
                return self.json(
                    200,
                    {
                        "photos": store.photos(),
                        "usage": store.usage(),
                        "limit": store.limit,
                        "pending": len(store.pending_uploads()),
                        "now": now,
                        "showing": on,
                    },
                )
            if path == "/api/photos/known":
                sha = self.query("sha")
                if not SHA256.fullmatch(sha):
                    return self.json(400, {"error": "needs sha: the photo's SHA-256, 64 hex digits"})
                return self.json(200, self.known(sha[:16]))
            if path.startswith("/api/media/"):
                return self.media_get(path)
            if path == "/manifest.json":
                return self.file(store.p("manifest.json"), "application/json")
            if path in ("/device/manifest.json", "/device/bundle"):
                if release is None:
                    return self.json(404, {"error": "no device code in this image"})
                if path == "/device/manifest.json":
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
            if path == "/api/media/begin":
                return self.media_begin()
            if path == "/api/media/chunk":
                return self.media_chunk()
            if path == "/api/delete-many":
                return self.delete_many()
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
                if (
                    not isinstance(crop, list)
                    or len(crop) != 3
                    or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 for v in crop)
                ):
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

        def delete_many(self):
            """The library's DELETE (presto#25): {"ids": [...]}, all or nothing
            on a bad id; ids no longer here are skipped."""
            try:
                ids = self.body_json(limit=MAX_DELETE * ID_JSON_BYTES).get("ids")
            except ValueError as e:
                return self.json(400, {"error": str(e)})
            if (
                not isinstance(ids, list)
                or not 0 < len(ids) <= MAX_DELETE
                or not all(isinstance(i, str) and valid_id(i) for i in ids)
            ):
                return self.json(400, {"error": f"ids is a list of 1-{MAX_DELETE} photo ids"})
            gone = store.delete_many(ids)
            log.info("photos deleted", count=len(gone), device=device_of(self.headers.get("User-Agent", "")))
            return self.json(200, {"deleted": gone})

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
            digest = hashlib.sha256()
            with part.open("wb") as f:
                left = self._read_body_to(f, n, digest)
            if left:
                part.unlink(missing_ok=True)
                UPLOADS.labels("cut_short").inc()
                return self.json(400, {"error": "upload cut short"})
            # The page asks first (/api/photos/known); this catches a page that
            # couldn't hash (no crypto.subtle, an old tab) and a script.
            pid = digest.hexdigest()[:16]  # store.photo_id, without reading the file again
            if (dup := self.known(pid))["known"]:
                part.unlink(missing_ok=True)
                UPLOADS.labels("duplicate").inc()
                log.info("duplicate upload skipped", photo=pid, bytes=n)
                return self.json(200, {"id": pid, "status": "duplicate", "hidden": dup["hidden"]})
            info = self.origin_info(
                filename=(self.headers.get("X-Filename") or "photo")[:200],
                content_type=(self.headers.get("Content-Type") or "")[:100],
                bytes=n,
            )
            store.finish_upload(up, info)
            UPLOADS.labels("ok").inc()
            log.info("upload received", upload=up, bytes=n, device=info["device"], content_type=info["content_type"])
            self.json(202, {"upload": up, "status": "queued"})

        # --- media ---
        def media_get(self, path: str):
            if inbox is None:
                return self.json(404, {"error": "no media inbox here"})
            if path == "/api/media/status":
                up = self.query("upload")
                st = inbox.status(up) if valid_upload(up) else None
                return self.json(200, st) if st else self.json(404, {"error": "no such upload"})
            if path == "/api/media/recent":
                return self.json(
                    200,
                    {
                        "recent": inbox.recent(),
                        "usage": inbox.usage(),
                        "limit": inbox.limit,
                        "waiting": len(inbox.pending()),
                    },
                )
            if path == "/api/media/devices":
                return self.json(
                    200,
                    {
                        "devices": [{"name": d.name, "tags": sorted(d.tags), "note": d.note} for d in devices.values()],
                        "vocabulary": media_devices.VOCABULARY,
                    },
                )
            if path == "/api/media/sync":
                dev = devices.get(self.query("device"))
                if dev is None:
                    return self.json(404, {"error": "no such device", "devices": sorted(devices)})
                SYNCS.labels(dev.name).inc()
                return self.json(200, media_devices.manifest(dev, inbox.index()))
            self.json(404, {"error": "not found"})

        def media_begin(self):
            if inbox is None:
                return self.json(404, {"error": "no media inbox here"})
            try:
                body = self.body_json()
                filename = str(body.get("filename") or "")[:200]
                n = int(body.get("bytes"))
            except (ValueError, TypeError):
                return self.json(400, {"error": "needs filename and bytes"})
            kind = media.media_kind(filename) or "other"
            if kind not in ("audio", "video"):
                MEDIA_UPLOADS.labels("other", "bad_type").inc()
                return self.json(415, {"error": "audio or video only here (photos go to /api/upload)"})
            if n <= 0:
                MEDIA_UPLOADS.labels(kind, "empty").inc()
                return self.json(400, {"error": "empty file"})
            if n > MAX_MEDIA_UPLOAD:
                MEDIA_UPLOADS.labels(kind, "too_big").inc()
                return self.json(413, {"error": f"over {MAX_MEDIA_UPLOAD >> 30} GB"})
            if not inbox.room_for(n):
                MEDIA_UPLOADS.labels(kind, "full").inc()
                return self.json(507, {"error": "the media inbox is full: try again once the sorter catches up"})
            info = self.origin_info(
                filename=filename, content_type=str(body.get("type") or "")[:100], bytes=n, kind=kind
            )
            up = inbox.begin(info)
            log.info("media upload started", upload=up, kind=kind, bytes=n, device=info["device"])
            self.json(201, {"upload": up, "chunk": CHUNK, "offset": 0})

        def media_chunk(self):
            if inbox is None:
                return self.json(404, {"error": "no media inbox here"})
            up = self.query("upload")
            info = inbox.info(up) if valid_upload(up) else None
            if not info:
                return self.json(404, {"error": "no such upload"})
            kind = info.get("kind", "other")
            try:
                offset = int(self.query("offset"))
                n = int(self.headers.get("Content-Length") or -1)
            except ValueError:
                return self.json(400, {"error": "needs offset and Content-Length"})
            if n <= 0 or n > MAX_CHUNK or offset + n > info["bytes"]:
                return self.json(413 if n > MAX_CHUNK else 400, {"error": "bad chunk size"})
            with lock_for(up):
                have = inbox.received(up)
                if have is None:  # complete already, or swept
                    st = inbox.status(up)
                    if st is None:
                        return self.json(409, {"error": "upload gone"})
                    return self.json(200, st)
                if have != offset:
                    MEDIA_UPLOADS.labels(kind, "resumed").inc()
                    return self.json(409, {"error": "offset", "offset": have})
                with inbox.part(up).open("ab") as f:
                    left = self._read_body_to(f, n)
                if left:  # cut short: keep what came, the page resumes from it
                    return self.json(400, {"error": "chunk cut short", "offset": inbox.received(up)})
                have += n
                if have < info["bytes"]:
                    return self.json(200, {"upload": up, "status": "receiving", "offset": have})
                inbox.finish(up)
            MEDIA_UPLOADS.labels(kind, "ok").inc()
            log.info("media upload received", upload=up, kind=kind, bytes=have, device=info.get("device"))
            self.json(202, {"upload": up, "status": "queued", "offset": have})

    return Handler


def serve(
    store: Store,
    port: int = 8080,
    release: Release | None = None,
    inbox: Inbox | None = None,
    devices: dict | None = None,
) -> None:
    httpd = ThreadingHTTPServer(("", port), make_handler(store, release, inbox, devices))
    httpd.daemon_threads = True
    log.info("web listening", port=port)
    httpd.serve_forever()
