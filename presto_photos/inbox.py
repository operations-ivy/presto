"""The media inbox: a volume of its own (presto-media-inbox), so a film never
lands in the 5 GB photo store. The page's server writes uploads here in chunks;
the sorter (sorter.py) files them on the drive and reports back.

    inbox/<upload>.json      the upload's name, type, size, who sent it   (web)
    inbox/<upload>.part      arriving, chunk by chunk                      (web)
    inbox/<upload>.upload    complete, waiting for the sorter              (web)
    done/<upload>.json       where the sorter filed it                     (sorter)
    library.json             the sorter's index of the drive's library     (sorter)

Two users share the volume: the web pod (uid 65532) and the sorter (uid 1000,
the exFAT drive's owner, with 65532 as a supplementary group). So the web pod
makes inbox/ group-writable and setgid, and the sorter can remove what it has
filed. The sorter never creates inbox/ itself: owned by uid 1000, the web pod
couldn't write to it.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

UPLOAD_ID = re.compile(r"[0-9a-f]{32}")
DONE_KEEP_S = 14 * 86400  # how long the page can show where an upload went


def valid_upload(s: str) -> bool:
    """An upload id: 32 hex digits and nothing else (it's a path component)."""
    return bool(UPLOAD_ID.fullmatch(s or ""))


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name("." + path.name + ".tmp")
    with tmp.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path: Path) -> dict | None:
    """A JSON object from path, or None (missing, unreadable, or not an object)."""
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return None


def _empty_index() -> dict:
    return {"version": 1, "updated": None, "items": []}


class Inbox:
    def __init__(
        self, root: str | Path, limit_bytes: int = 20 << 30, min_free_bytes: int = 50 << 30, create: bool = False
    ):
        self.root = Path(root)
        self.limit = limit_bytes
        self.min_free = min_free_bytes
        self._index: tuple[float, dict] | None = None
        if create:  # the web pod
            d = self.root / "inbox"
            d.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(PermissionError):  # made by someone else: leave it
                d.chmod(0o2775)

    def p(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def _files(self, sub: str, pattern: str) -> list[Path]:
        """sub's files matching pattern, sorted; [] if sub isn't there."""
        d = self.p(sub)
        return sorted(d.glob(pattern)) if d.is_dir() else []

    # --- the web pod: receiving ---
    def usage(self) -> int:
        """Bytes promised to uploads in the inbox: each one's full size from its
        start, so two big uploads can't both start under the cap. A malformed
        record counts as nothing rather than stopping every upload."""
        total = 0
        for f in self._files("inbox", "*.json"):
            with contextlib.suppress(TypeError, ValueError):
                total += int((read_json(f) or {}).get("bytes") or 0)
        return total

    def room_for(self, n: int) -> bool:
        if self.usage() + n > self.limit:
            return False
        return shutil.disk_usage(self.root).free - n >= self.min_free

    def begin(self, info: dict) -> str:
        up = uuid.uuid4().hex
        write_atomic(self.p("inbox", up + ".json"), json.dumps(info).encode())
        self.p("inbox", up + ".part").touch()
        return up

    def info(self, up: str) -> dict | None:
        return read_json(self.p("inbox", up + ".json"))

    def part(self, up: str) -> Path:
        return self.p("inbox", up + ".part")

    def received(self, up: str) -> int | None:
        try:
            return self.part(up).stat().st_size
        except FileNotFoundError:
            return None

    def finish(self, up: str) -> None:
        os.replace(self.part(up), self.p("inbox", up + ".upload"))

    def status(self, up: str) -> dict | None:
        """Where an upload is: receiving, queued, or what the sorter did with it."""
        if done := read_json(self.p("done", up + ".json")):
            return done
        if self.p("inbox", up + ".upload").exists():
            return {"upload": up, "status": "queued"}
        n = self.received(up)
        if n is not None:
            return {"upload": up, "status": "receiving", "offset": n}
        return None

    def recent(self, n: int = 12) -> list[dict]:
        out = [d for f in self._files("done", "*.json") if (d := read_json(f))]
        return sorted(out, key=lambda d: d.get("at", 0), reverse=True)[:n]

    def index(self) -> dict:
        """The sorter's library index, re-read when it changes."""
        path = self.p("library.json")
        mtime = _mtime(path)
        if mtime is None:
            return _empty_index()
        if not self._index or self._index[0] != mtime:
            self._index = (mtime, read_json(path) or _empty_index())
        return self._index[1]

    # --- the sorter: filing ---
    def pending(self) -> list[str]:
        """Finished uploads, oldest first."""
        ups = [(t, f.stem) for f in self._files("inbox", "*.upload") if (t := _mtime(f)) is not None]
        return [u for _, u in sorted(ups)]

    def upload_path(self, up: str) -> Path:
        return self.p("inbox", up + ".upload")

    def record(self, up: str, done: dict) -> None:
        self.p("done").mkdir(exist_ok=True)
        write_atomic(self.p("done", up + ".json"), json.dumps({"upload": up, **done}).encode())

    def drop(self, up: str) -> None:
        for ext in (".upload", ".part", ".json"):
            self.p("inbox", up + ext).unlink(missing_ok=True)

    def sweep(self, now: float | None = None, part_idle_s: float = 86400) -> int:
        """Remove uploads abandoned for a day (the page can resume one until then)
        and done records older than DONE_KEEP_S."""
        now = time.time() if now is None else now
        n = 0
        for f in self._files("inbox", "*.part"):
            t = _mtime(f)
            if t is not None and now - t > part_idle_s:
                self.drop(f.stem)
                n += 1
        for f in self._files("done", "*.json"):
            if now - (read_json(f) or {}).get("at", 0) > DONE_KEEP_S:
                f.unlink(missing_ok=True)
        return n

    def write_index(self, index: dict) -> None:
        write_atomic(self.p("library.json"), json.dumps(index).encode())
