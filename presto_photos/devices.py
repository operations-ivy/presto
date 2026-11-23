"""Device tags, and what each device should hold: its sync manifest.

devices.yaml (k8s_config/devices.yaml, a ConfigMap; the image carries a copy)
tags each device with what it can use. Rules here turn tags into the kinds a
device gets and which copy of each:

    handheld, medium_graphics, high_graphics   plays video: movies and shows
    handheld, 640x480                          a small screen: the 480p copy of each video
    english_audio_only                         no video known to be subtitled-only
    music                                      the music library
    photos_240                                 the photo frame's 240x240 frames (/manifest.json)
    pi4                                        what the device is; no rule of its own

The manifest names each file by its path inside a Samba share (`movies` is
the drive's /extra/movies, `music` its /extra/music), so a device pulls what
it lacks and removes what is no longer listed.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

VOCABULARY = {
    "handheld": "a handheld: plays video, on a small screen (the 480p copies)",
    "640x480": "a 640x480 screen: the 480p copies",
    "medium_graphics": "plays video (originals unless the screen is small)",
    "high_graphics": "plays video at full size (originals)",
    "english_audio_only": "skips video known to be subtitled-only: too small to read",
    "music": "gets the music library",
    "photos_240": "a photo frame: the Presto's 240x240 frames (its own /manifest.json)",
    "pi4": "a Raspberry Pi 4 (descriptive)",
}
PLAYS_VIDEO = frozenset({"handheld", "medium_graphics", "high_graphics"})
SMALL_SCREEN = frozenset({"handheld", "640x480"})
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
VIDEO_KINDS = ("movie", "show")
WAITING = "waiting"  # wants() for a small screen whose 480p copy isn't made yet


@dataclass(frozen=True)
class Device:
    name: str
    tags: frozenset[str]
    note: str = ""

    @property
    def plays_video(self) -> bool:
        return not self.tags.isdisjoint(PLAYS_VIDEO)

    @property
    def small_screen(self) -> bool:
        return not self.tags.isdisjoint(SMALL_SCREEN)


def parse(text: str) -> dict[str, Device]:
    """devices.yaml's devices, checked: a typo in a tag is an error, not a device
    that silently gets nothing."""
    doc = yaml.safe_load(text) or {}
    raw = doc.get("devices") if isinstance(doc, dict) else None
    if not isinstance(raw, dict):
        raise ValueError("devices.yaml needs a `devices:` mapping")
    out = {}
    for name, spec in raw.items():
        name = str(name)
        if not NAME.match(name):
            raise ValueError(f"device name {name!r}: lowercase letters, digits, - and _ only")
        spec = spec or {}
        raw_tags = spec.get("tags") or []
        if not isinstance(raw_tags, list):
            raise ValueError(f"{name}: tags must be a list")
        tags = frozenset(str(t) for t in raw_tags)
        if unknown := sorted(tags - VOCABULARY.keys()):
            raise ValueError(f"{name}: unknown tags {unknown}; known: {sorted(VOCABULARY)}")
        out[name] = Device(name, tags, str(spec.get("note") or ""))
    return out


def load(path: str | Path) -> dict[str, Device]:
    return parse(Path(path).read_text())


def wants(dev: Device, item: dict) -> dict | str | None:
    """The copy of a library item this device should hold (a file entry), None,
    or WAITING: a small screen whose 480p copy isn't made yet gets it later."""
    kind = item.get("kind")
    if kind in VIDEO_KINDS:
        if not dev.plays_video:
            return None
        if "english_audio_only" in dev.tags and item.get("audio") == "sub":
            return None
        if dev.small_screen:
            v = (item.get("variants") or {}).get("480p")
            return {**_entry(item, v), "variant": "480p"} if v else WAITING
        return {**_entry(item, item), "variant": "original"}
    if kind == "music" and "music" in dev.tags:
        return {**_entry(item, item), "variant": "original"}
    return None


def _entry(item: dict, f: dict) -> dict:
    return {"kind": item["kind"], "share": item["share"], "path": f["path"], "bytes": f["bytes"], "mtime": f["mtime"]}


def manifest(dev: Device, index: dict, now: float | None = None, picks: set[str] | None = None) -> dict:
    """Everything this device should hold, from the sorter's library index:
    what its tags allow, narrowed to its picks when it has any (picks.py).
    picks None: no picks saved, everything; an empty set: nothing."""
    files, waiting = [], 0
    for item in index.get("items", []):
        if picks is not None and item["path"] not in picks:
            continue
        f = wants(dev, item)
        if f == WAITING:
            waiting += 1
        elif f:
            files.append(f)
    files.sort(key=lambda f: (f["share"], f["path"]))
    out = {
        "version": 1,
        "device": dev.name,
        "tags": sorted(dev.tags),
        "generated": int(time.time() if now is None else now),
        "index_updated": index.get("updated"),
        "files": files,
        "bytes": sum(f["bytes"] for f in files),
        "waiting_for_480p": waiting,
        "picked": picks is not None,
    }
    if "photos_240" in dev.tags:
        out["photos"] = {"manifest": "/manifest.json", "size": 240}
    return out


def _show(item: dict) -> str | None:
    """A show's name: its folder under library/tv/ (the index's `series` is
    "tv" for every show until presto#46), else the index's own."""
    parts = item["path"].split("/")
    if item.get("kind") == "show" and len(parts) > 3 and parts[:2] == ["library", "tv"]:
        return parts[2]
    return item.get("series")


def pickable_paths(dev: Device, index: dict) -> set[str]:
    """The library paths of every video this device can take: what a pick may name."""
    return {i["path"] for i in index.get("items", []) if i.get("kind") in VIDEO_KINDS and wants(dev, i) is not None}


def catalog(dev: Device, index: dict, picks: set[str] | None = None) -> dict:
    """The picker page's list for this device: every video its tags allow, with
    whether it's picked (no picks saved: all of them) and the size of the copy
    it would get (the original's until a small screen's 480p copy is made)."""
    items = []
    for item in index.get("items", []):
        if item.get("kind") not in VIDEO_KINDS:
            continue
        f = wants(dev, item)
        if f is None:
            continue
        waiting = f == WAITING  # no 480p copy yet: show the original's size
        items.append(
            {
                "path": item["path"],
                "kind": item["kind"],
                "series": _show(item),
                "bytes": item["bytes"] if waiting else f["bytes"],
                "waiting": waiting,
                "audio": item.get("audio"),
                "picked": picks is None or item["path"] in picks,
            }
        )
    items.sort(key=lambda i: i["path"].casefold())
    return {"device": dev.name, "note": dev.note, "picked": picks is not None, "items": items}
