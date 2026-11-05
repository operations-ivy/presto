"""The Presto's own code, served to it over Wi-Fi (presto#9).

The image carries `device/` (the MicroPython app). The upload page's server
offers it as

    GET /device/manifest.json   {"version", "files": [{"path", "sha", "size"}]}
    GET /device/bundle          b"neon-bundle <version>\\n", then per file
                                b"<path>\\n<size>\\n" + its bytes

and the Presto (device/lib/neon/ota.py) checks the manifest every few minutes,
fetches the bundle when the version changed, checks every hash, and swaps the
files in; device/ota_boot.py rolls back a release that doesn't run.

BOOT_FILES (main.py, ota_boot.py) are never sent: they're what makes a bad
release recoverable, so they only change over USB (tools/deploy). secrets.py
isn't in the repo, so it's never sent either.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

BOOT_FILES = frozenset({"main.py", "ota_boot.py"})
NEVER = frozenset({"secrets.py"})  # Wi-Fi, coordinates, the signs: never in git, never served, even if a build has it
SUFFIXES = (".py", ".yaml")


@dataclass(frozen=True)
class Release:
    version: str
    files: tuple[tuple[str, bytes], ...]  # (path on the Presto, contents), sorted

    def manifest(self) -> dict:
        return {
            "version": self.version,
            "files": [{"path": p, "sha": hashlib.sha256(b).hexdigest(), "size": len(b)} for p, b in self.files],
        }

    def bundle(self) -> bytes:
        parts = [f"neon-bundle {self.version}\n".encode()]
        for path, body in self.files:
            parts += [f"{path}\n{len(body)}\n".encode(), body]
        return b"".join(parts)


def load(device_dir: Path) -> Release:
    """Every file the Presto should run from device_dir, and a version that changes with any of them."""
    files = tuple(
        (p.relative_to(device_dir).as_posix(), p.read_bytes())
        for p in sorted(device_dir.rglob("*"))
        if p.is_file() and p.suffix in SUFFIXES and "__pycache__" not in p.parts
        and p.relative_to(device_dir).as_posix() not in BOOT_FILES and p.name not in NEVER
    )
    return Release(version_of(files), files)


def version_of(files) -> str:
    """The first 12 hex of a hash over every path and its hash: the same files, the same version."""
    h = hashlib.sha256()
    for path, body in files:
        h.update(f"{path}:{hashlib.sha256(body).hexdigest()}\n".encode())
    return h.hexdigest()[:12]


if __name__ == "__main__":  # tools/deploy: the version a USB deploy installs
    import sys

    print(json.dumps({"version": load(Path(sys.argv[1])).version}))
