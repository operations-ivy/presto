"""Which library videos the devices hold: picked on /media/devices.

One list for every device (SHARED): each device holds the picked videos its
tags allow (devices.py), so the Retroid still skips subtitled-only ones. With
no picks saved, every device holds everything its tags allow, which is what
they held before there was a picker. Picks are the original's path in the
movies share (`library/tv/Kite/Kite.mkv`), so a pick holds across the 480p
copy being made.

Picks were per device at first (presto#56): adopt() turns the one device's
saved list into the shared one, once.

Stored as one JSON file in the media inbox's volume (picks.json), written
atomically; the web pod is the only writer.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .inbox import read_json, write_atomic

MAX_PICKS = 20000
SHARED = "all"  # the key of the one list every device follows


class Picks:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def _all(self) -> dict:
        doc = read_json(self.path) or {}
        devices = doc.get("devices") if isinstance(doc, dict) else None
        return devices if isinstance(devices, dict) else {}

    def _write(self, devices: dict) -> None:
        write_atomic(self.path, json.dumps({"version": 1, "devices": devices}).encode())

    def get(self, device: str) -> set[str] | None:
        """The device's picked paths, or None: never picked, so everything."""
        entry = self._all().get(device)
        if not isinstance(entry, dict) or not isinstance(entry.get("paths"), list):
            return None
        return {p for p in entry["paths"] if isinstance(p, str)}

    def updated(self, device: str) -> int | None:
        entry = self._all().get(device)
        return entry.get("updated") if isinstance(entry, dict) else None

    def adopt(self) -> str | None:
        """Per-device picks from before the shared list: if there's no shared list
        and exactly one device has picks, they become the shared list. Returns
        that device's name, or None when there was nothing to do."""
        with self.lock:
            devices = self._all()
            if SHARED in devices:
                return None
            saved = [name for name, entry in devices.items() if isinstance(entry, dict) and "paths" in entry]
            if len(saved) != 1:
                return None
            (name,) = saved
            self._write({SHARED: devices[name]})
            return name

    def set(self, device: str, paths: set[str] | None, now: float | None = None) -> None:
        """Save the device's picks; None forgets them (back to everything)."""
        with self.lock:
            devices = self._all()
            if paths is None:
                devices.pop(device, None)
            else:
                devices[device] = {"paths": sorted(paths), "updated": int(time.time() if now is None else now)}
            self._write(devices)


def valid_paths(raw, known: set[str]) -> set[str]:
    """The picks a page sent: a list of known library paths, nothing else."""
    if not isinstance(raw, list) or len(raw) > MAX_PICKS:
        raise ValueError(f"paths must be a list of at most {MAX_PICKS} library paths")
    if any(not isinstance(p, str) for p in raw):
        raise ValueError("paths must be strings")
    if unknown := set(raw) - known:
        raise ValueError(f"not in the library: {sorted(unknown)[:3]}")
    return set(raw)
