"""The sorter: files finished media uploads on the media drive and keeps the
library index. A reconcile loop like the converter's: it rescans every POLL_S,
so it catches up by itself after a restart.

It runs as uid 1000 (the exFAT drive is mounted for that uid only) with the
drive's movies and music folders mounted, and no network but DNS and traces.

- Files go to the place media.place() names; a name already taken gets a
  " (2)" suffix, never an overwrite.
- A copy is written as a dotted ".<name>.part" beside its final name, then
  renamed: the 480p converter (brick-k8s-config media/) skips dotfiles, so it
  never converts half a film.
- Short on space on the drive (MIN_FREE), or the drive missing, and the upload
  waits in the inbox; nothing is dropped until it is filed.
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import brick_observe
import mutagen
from prometheus_client import Counter, Gauge

from . import media
from .inbox import Inbox

log = brick_observe.get_logger(__name__)

POLL_S = 5.0
INDEX_S = 300.0  # the index is rebuilt this often, and after every filing
RETRY_S = 300.0  # an upload whose filing failed waits this long before the next try
MIN_FREE = 10 << 30  # leave this much free on the drive
COPY_CHUNK = 8 << 20

FILED = Counter("media_filed_total", "Media uploads filed, by kind and result", ["kind", "result"])
LIBRARY = Gauge("media_library_files", "Files in the library index, by kind", ["kind"])
WAITING = Gauge("media_inbox_waiting", "Finished uploads waiting to be filed")
INBOX_BYTES = Gauge("media_inbox_bytes", "Bytes promised to uploads in the inbox")


@dataclass(frozen=True)
class Roots:
    movies: Path  # the drive's /extra/movies (the `movies` share)
    music: Path  # the drive's /extra/music (the `music` share)

    def of(self, share: str) -> Path:
        return {"movies": self.movies, "music": self.music}[share]

    def on_drive(self, share: str) -> bool:
        """The share's folder is there, and on the same filesystem as movies (which
        the Deployment mounts as an existing Directory): a music folder kubelet made
        on brick2000's root disk while the drive was away doesn't count."""
        root = self.of(share)
        try:
            return root.is_dir() and root.stat().st_dev == self.movies.stat().st_dev
        except FileNotFoundError:
            return False


def read_tags(path: Path) -> dict:
    """An audio file's tags (artist, album, title, tracknumber...), or {}."""
    try:
        f = mutagen.File(path, easy=True)
    except Exception:  # a broken or crafted file: no tags, so it goes to review
        log.warning("tags unreadable", file=path.name)
        return {}
    if f is None or not f.tags:
        return {}
    keys = ("artist", "albumartist", "album", "title", "tracknumber", "discnumber")
    return {k: str(f.tags[k][0]) for k in keys if k in f.tags and f.tags[k]}


def copy_into(src: Path, folder: Path, name: str) -> Path:
    """Copy src into folder as a name nobody has, through a dotted .part; returns
    the final path. Plain bytes only: exFAT has no owners or modes to copy."""
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / media.unique_name(name, [e.name for e in folder.iterdir()])
    part = folder / f".{final.name}.part"
    with src.open("rb") as fin, part.open("wb") as fout:
        shutil.copyfileobj(fin, fout, COPY_CHUNK)
        fout.flush()
        os.fsync(fout.fileno())
    if final.exists():  # taken while we copied (a person on the share): pick again
        final = folder / media.unique_name(name, [e.name for e in folder.iterdir() if e != part])
    part.rename(final)
    return final


class Sorter:
    def __init__(self, inbox: Inbox, roots: Roots):
        self.inbox, self.roots = inbox, roots
        self.failed_at: dict[str, float] = {}
        self.indexed_at = 0.0

    def _fail(self, up: str, kind: str, result: str, event: str, **fields) -> None:
        """An upload that can't be filed now: counted, logged, retried after RETRY_S."""
        self.failed_at[up] = time.time()
        FILED.labels(kind, result).inc()
        log.error(event, upload=up, **fields)

    def file_one(self, up: str) -> dict | None:
        """File one finished upload; returns its done record, or None if it waits."""
        if time.time() - self.failed_at.get(up, 0) < RETRY_S:
            return None
        info = self.inbox.info(up) or {}
        src = self.inbox.upload_path(up)
        filename = info.get("filename") or src.name
        tags = read_tags(src) if media.media_kind(filename) == "audio" else {}
        place = media.place(filename, tags)
        if not self.roots.on_drive(place.share):
            self._fail(up, place.kind, "no_drive", "media drive missing", share=place.share)
            return None
        size = src.stat().st_size
        root = self.roots.of(place.share)
        if shutil.disk_usage(root).free - size < MIN_FREE:
            self._fail(up, place.kind, "no_space", "media drive full", share=place.share, bytes=size)
            return None
        started = time.perf_counter()
        with brick_observe.span("media file", **{"media.kind": place.kind, "media.bytes": size}):
            try:
                final = copy_into(src, root / place.folder, place.name)
            except OSError as e:
                self._fail(up, place.kind, "error", "media filing failed", error=repr(e))
                return None
        rel = final.relative_to(root).as_posix()
        done = {
            "status": "review" if place.kind == "review" else "filed",
            "kind": place.kind,
            "share": place.share,
            "path": rel,
            "bytes": size,
            "filename": filename,
            "audio": place.audio,
            "reason": place.reason,
            "device": info.get("device"),
            "at": int(time.time()),
        }
        self.inbox.record(up, done)
        self.inbox.drop(up)
        self.failed_at.pop(up, None)
        FILED.labels(place.kind, done["status"]).inc()
        log.info(
            "media filed",
            upload=up,
            kind=place.kind,
            share=place.share,
            path=rel,
            bytes=size,
            seconds=round(time.perf_counter() - started, 1),
        )
        return done

    def tick(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        pending = self.inbox.pending()
        filed = sum(1 for up in pending if self.file_one(up))
        self.inbox.sweep(now)
        if filed or now - self.indexed_at >= INDEX_S:
            index = build_index(self.roots, now)
            self.inbox.write_index(index)
            self.indexed_at = now
            for kind in ("movie", "show", "music"):
                LIBRARY.labels(kind).set(sum(1 for i in index["items"] if i["kind"] == kind))
        WAITING.set(len(pending) - filed)
        INBOX_BYTES.set(self.inbox.usage())
        return filed


def _files(top: Path, exts: frozenset[str]) -> Iterator[Path]:
    """Every visible file under top with one of exts (no dotfiles, no dot folders);
    nothing if top isn't there (os.walk yields nothing)."""
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fn in sorted(filenames):
            if not fn.startswith(".") and Path(fn).suffix.lower() in exts:
                yield Path(dirpath, fn)


def _stat(path: Path, root: Path) -> dict | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return {"path": path.relative_to(root).as_posix(), "bytes": st.st_size, "mtime": int(st.st_mtime)}


def build_index(roots: Roots, now: float | None = None) -> dict:
    """Every file in the libraries, by share-relative path: a video's kind is its
    folder's (library/movies and the library's top level: movies; any other
    folder: a show), with its 480p copy if the converter has made one."""
    items = []
    lib, p480 = roots.movies / "library", roots.movies / "480p"
    for f in _files(lib, media.VIDEO_EXT):
        entry = _stat(f, roots.movies)
        if not entry:
            continue
        sub = f.relative_to(lib)
        kind = "movie" if len(sub.parts) == 1 or sub.parts[0] == "movies" else "show"
        item = {"kind": kind, "share": "movies", **entry, "audio": media.audio_hint(f.stem)}
        if kind == "show":
            item["series"] = sub.parts[0]
        small = _stat(p480 / sub.with_suffix(".mp4"), roots.movies)
        if small:
            item["variants"] = {"480p": small}
        items.append(item)
    if roots.on_drive("music"):
        for f in _files(roots.music / "library", media.AUDIO_EXT):
            entry = _stat(f, roots.music)
            if entry:
                items.append({"kind": "music", "share": "music", **entry})
    return {"version": 1, "updated": int(time.time() if now is None else now), "items": items}


def run(inbox: Inbox, roots: Roots) -> None:
    log.info("sorter started", movies=str(roots.movies), music=str(roots.music))
    sorter = Sorter(inbox, roots)
    while True:
        try:
            sorter.tick()
        except Exception:
            log.exception("sorter pass failed")
        time.sleep(POLL_S)
