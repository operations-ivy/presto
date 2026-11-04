"""The converter: turns finished uploads into Presto images, redoes re-crops,
and expires originals. A reconcile loop (rescan every POLL_S), so it catches
up by itself after a restart and never depends on missing an event.

It runs in its own pod with no network egress (k8s_config's NetworkPolicy):
image decoders have a history of holes, and a crafted file should find
nothing to reach.
"""

from __future__ import annotations

import time

import brick_observe
from prometheus_client import Counter, Gauge, Histogram

from . import pipeline
from .store import Store, photo_id

log = brick_observe.get_logger(__name__)

POLL_S = 2.0

CONVERTED = Counter("photos_converted_total", "Photos converted, by result", ["result"])
SECONDS = Histogram("photos_convert_seconds", "Time to convert one photo", buckets=(1, 2, 5, 10, 20, 40, 80))
STORED = Gauge("photos_stored", "Photos in the store")
BYTES = Gauge("photos_store_bytes", "Bytes used by the store")

EXT = {"HEIF": "heic", "JPEG": "jpg", "PNG": "png", "WEBP": "webp", "MPO": "jpg"}


def transcoded(fmt: str, info: dict) -> bool:
    """Whether iOS turned a HEIC into JPEG on the way (Safari does, even when the
    page asks for HEIC): a JPEG (Pillow says MPO when the iPhone's HDR gain map
    rides along) from an iPhone or iPad, or named like Safari names them
    (IMG_1234.jpeg; the camera's own JPEGs are .JPG)."""
    if fmt not in ("JPEG", "MPO"):
        return False
    name = info.get("filename") or ""
    return info.get("device") in ("iPhone", "iPad") or (name.startswith("IMG_") and name.endswith(".jpeg"))


def convert_one(store: Store, up: str) -> str | None:
    """Convert one finished upload; returns the photo id (or None if rejected)."""
    data, info = store.take_upload(up)
    pid = photo_id(data)
    existing = store.meta(pid)
    if existing and existing.get("status") == "ready":
        log.info("duplicate upload", photo=pid)
        store.drop_upload(up)
        CONVERTED.labels("duplicate").inc()
        return pid
    started = time.perf_counter()
    m = {
        "id": pid,
        "added": int(time.time()),
        "status": "converting",
        "upload": {k: info.get(k) for k in ("filename", "content_type", "bytes", "device", "from")},
    }
    store.save_meta(m)
    with brick_observe.span("photos convert", **{"photos.id": pid, "photos.bytes": len(data)}):
        try:
            lin, src = pipeline.decode(data)
            r = pipeline.render(lin)
        except pipeline.Rejected as e:
            m.update(status="failed", error=str(e))
            store.save_meta(m)
            store.drop_upload(up)
            CONVERTED.labels("rejected").inc()
            log.warning("upload rejected", photo=pid, error=str(e))
            return None
        except Exception as e:
            m.update(status="failed", error=f"conversion failed: {e!r}")
            store.save_meta(m)
            store.drop_upload(up)
            CONVERTED.labels("error").inc()
            log.exception("conversion failed", photo=pid)
            return None
    ext = EXT.get(src.format, "bin")
    store.p("originals", f"{pid}.{ext}").write_bytes(data)
    store.p("masters", pid + ".jpg").write_bytes(pipeline.master(lin))
    _write_outputs(store, pid, r)
    m.update(
        status="ready",
        converted=int(time.time()),
        original=f"{pid}.{ext}",
        source={"format": src.format, "width": src.width, "height": src.height, "colour": src.colour,
                "bit_depth": src.bit_depth},
        crop=list(r.crop),
        crop_auto=True,
        downgraded=transcoded(src.format, info),
    )
    store.save_meta(m)
    store.drop_upload(up)
    store.write_manifest()
    SECONDS.observe(time.perf_counter() - started)
    CONVERTED.labels("ok").inc()
    log.info("photo converted", photo=pid, format=src.format, colour=src.colour, seconds=round(time.perf_counter() - started, 2))
    return pid


def recrop(store: Store, m: dict) -> None:
    """Redo a photo with the user's crop: from the original while it's kept,
    else from the ~1600 px master."""
    pid = m["id"]
    src = store.p("originals", m["original"]) if m.get("original") else store.p("masters", pid + ".jpg")
    with brick_observe.span("photos recrop", **{"photos.id": pid}):
        try:
            lin, _ = pipeline.decode(src.read_bytes())
            r = pipeline.render(lin, tuple(m["crop"]))
        except Exception as e:
            m.update(status="ready", recrop_error=repr(e))
            store.save_meta(m)
            log.exception("recrop failed", photo=pid)
            return
    _write_outputs(store, pid, r)
    m.update(status="ready", crop=list(r.crop), crop_auto=False, recropped=int(time.time()))
    m.pop("recrop_error", None)
    store.save_meta(m)
    store.write_manifest()
    log.info("photo recropped", photo=pid)


def _write_outputs(store: Store, pid: str, r: pipeline.Rendered) -> None:
    store.p("out", pid + ".rgb565").write_bytes(r.rgb565)
    store.p("out", pid + ".jpg").write_bytes(r.jpeg)
    store.p("previews", pid + ".png").write_bytes(r.preview_png)


def tick(store: Store) -> int:
    """One pass: uploads, re-crops, housekeeping. Returns how much it did."""
    done = 0
    for up in store.pending_uploads():
        convert_one(store, up)
        done += 1
    for m in store.photos():
        if m.get("status") == "recrop":
            recrop(store, m)
            done += 1
    store.expire_originals()
    store.sweep_parts()
    photos = store.photos()
    STORED.set(len(photos))
    BYTES.set(store.usage())
    return done


def run(store: Store) -> None:
    log.info("converter started", root=str(store.root))
    store.write_manifest()
    while True:
        try:
            tick(store)
        except Exception:
            log.exception("converter pass failed")
        time.sleep(POLL_S)
