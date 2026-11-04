"""The photo store: one volume, plain files, every write atomic (write then rename).

    inbox/<upload>.part        an upload arriving
    inbox/<upload>.upload      a finished upload, waiting for the converter
    inbox/<upload>.json        who sent it, its name and type
    originals/<id>.<ext>       the original, kept ORIGINAL_DAYS after conversion
    masters/<id>.jpg           ~1600 px, kept while the photo is on the frame (re-crops)
    out/<id>.rgb565            the Presto's frame (480x480 RGB565, big-endian)
    out/<id>.jpg               480x480 JPEG
    previews/<id>.png          exactly what the frame looks like
    meta/<id>.json             the photo's record
    manifest.json              what the Presto shows, in order

A photo's id is the first 16 hex digits of its original's SHA-256, so the
same photo uploaded twice is stored once.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

ORIGINAL_DAYS = 7
DIRS = ("inbox", "originals", "masters", "out", "previews", "meta")


def photo_id(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def valid_id(s: str) -> bool:
    return len(s) == 16 and all(c in "0123456789abcdef" for c in s)


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Store:
    def __init__(self, root: str | Path, limit_bytes: int = 5 << 30):
        self.root = Path(root)
        self.limit = limit_bytes
        for d in DIRS:
            (self.root / d).mkdir(parents=True, exist_ok=True)

    def p(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    # --- uploads ---
    def usage(self) -> int:
        total = 0
        for d in DIRS:
            for f in (self.root / d).iterdir():
                try:
                    total += f.stat().st_size
                except FileNotFoundError:
                    pass
        return total

    def room_for(self, n: int) -> bool:
        return self.usage() + n <= self.limit

    def begin_upload(self) -> str:
        return uuid.uuid4().hex

    def upload_part(self, up: str) -> Path:
        return self.p("inbox", up + ".part")

    def finish_upload(self, up: str, info: dict) -> None:
        write_atomic(self.p("inbox", up + ".json"), json.dumps(info).encode())
        os.replace(self.upload_part(up), self.p("inbox", up + ".upload"))

    def pending_uploads(self) -> list[str]:
        ups = [f.stem for f in self.p("inbox").glob("*.upload")]
        return sorted(ups, key=lambda u: self.p("inbox", u + ".upload").stat().st_mtime)

    def take_upload(self, up: str) -> tuple[bytes, dict]:
        return self.p("inbox", up + ".upload").read_bytes(), self._upload_info(up)

    def _upload_info(self, up: str) -> dict:
        try:
            return json.loads(self.p("inbox", up + ".json").read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def note_attempt(self, up: str) -> int:
        """Count a conversion attempt on an upload (saved before decoding, so an
        attempt that kills the process still counts); returns the count so far."""
        info = self._upload_info(up)
        info["attempts"] = int(info.get("attempts", 0)) + 1
        write_atomic(self.p("inbox", up + ".json"), json.dumps(info).encode())
        return info["attempts"]

    def drop_upload(self, up: str) -> None:
        for ext in (".upload", ".json", ".part"):
            self.p("inbox", up + ext).unlink(missing_ok=True)

    def sweep_parts(self, older_than_s: float = 3600) -> int:
        """Remove uploads that never finished (a dropped connection)."""
        n, now = 0, time.time()
        for f in self.p("inbox").glob("*.part"):
            if now - f.stat().st_mtime > older_than_s:
                f.unlink(missing_ok=True)
                n += 1
        return n

    # --- photos ---
    def meta(self, pid: str) -> dict | None:
        try:
            return json.loads(self.p("meta", pid + ".json").read_text())
        except (FileNotFoundError, ValueError):
            return None

    def save_meta(self, m: dict) -> None:
        write_atomic(self.p("meta", m["id"] + ".json"), json.dumps(m, indent=1).encode())

    def photos(self) -> list[dict]:
        out = []
        for f in self.p("meta").glob("*.json"):
            m = self.meta(f.stem)
            if m:
                out.append(m)
        return sorted(out, key=lambda m: m.get("added", 0), reverse=True)

    def delete(self, pid: str) -> bool:
        m = self.meta(pid)
        if m is None:
            return False
        for d, ext in (("masters", ".jpg"), ("out", ".rgb565"), ("out", ".jpg"), ("previews", ".png")):
            self.p(d, pid + ext).unlink(missing_ok=True)
        for f in self.p("originals").glob(pid + ".*"):
            f.unlink(missing_ok=True)
        self.p("meta", pid + ".json").unlink(missing_ok=True)
        self.write_manifest()
        return True

    def expire_originals(self, now: float | None = None) -> int:
        """Delete originals ORIGINAL_DAYS after conversion (the re-crop window);
        the ~1600 px master stays for later re-crops."""
        now = now or time.time()
        n = 0
        for m in self.photos():
            kept = m.get("original")
            if kept and now - m.get("converted", now) > ORIGINAL_DAYS * 86400:
                self.p("originals", kept).unlink(missing_ok=True)
                m["original"] = None
                self.save_meta(m)
                n += 1
        return n

    def write_manifest(self) -> dict:
        """What the Presto shows: ready photos, oldest first, not hidden."""
        ready = [m for m in self.photos() if m.get("status") == "ready" and not m.get("hidden")]
        ready.sort(key=lambda m: m.get("added", 0))
        man = {
            "version": 1,
            "updated": int(time.time()),
            "photos": [
                {"id": m["id"], "added": m.get("added"), "jpg": f"/photo/{m['id']}.jpg",
                 "rgb565": f"/photo/{m['id']}.rgb565"}
                for m in ready
            ],
        }
        write_atomic(self.p("manifest.json"), json.dumps(man).encode())
        return man
