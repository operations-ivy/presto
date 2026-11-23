#!/usr/bin/env python3
"""Make a handheld's SD card match its picks (the picker at /media/devices), over adb.

    tools/handheld_sync.py rg476h            # what it would do
    tools/handheld_sync.py rg476h --apply    # do it

Reads the device's list from GET /api/media/sync?device=, copies what it
lacks from the movies share mounted on the laptop (/mnt/brick/movies), and
deletes what isn't listed any more. Only inside SD `Movies/movies` and
`Movies/tv`, which this tool owns: nothing else on the card is touched.
Pictures and subtitles beside a video on the share (`X.jpg`, `X.srt`, a
folder's `poster.jpg`) go with it. Then it rebuilds the TV playlists in
`Movies/tv-shows/` (one .m3u per show or season, for Daijisho's TV platform)
and asks Android to index the card.

Dry run by default. With --apply it deletes first (to make room), checks the
new files fit, then copies.
"""

import argparse
import json
import shlex
import subprocess
import sys
import tempfile
import urllib.request
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import NamedTuple

URL = "https://presto.brick.nozdormu.cloud"
SHARE = Path("/mnt/brick/movies")
OWNED = ("movies", "tv")  # the SD Movies/ folders this tool keeps in step
PLAYLISTS = "tv-shows"
SIDECAR = (".jpg", ".jpeg", ".png", ".webp", ".srt", ".ass", ".vtt")
POSTERS = ("poster.jpg", "poster.png", "poster.jpeg", "poster.webp")
MODELS = {"RG_476H": "rg476h"}  # adb's ro.product.model -> devices.yaml name
MERGE = {"Kite (Director's Cut)": "Kite"}  # Daijisho drops "(...)" and skipped the renamed list
SLACK = 512 << 20  # leave this free on the card
BATCH = 50  # files per `rm` on the device


class Entry(NamedTuple):
    src: Path  # on the share
    size: int


class Plan(NamedTuple):
    push: list[str]  # SD-relative paths to copy
    drop: list[str]  # and to delete
    need: int  # bytes the copies add (less what they replace)
    freed: int  # bytes the deletes free


def run(*cmd: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise SystemExit(f"{cmd[0]} isn't installed") from None


def adb(serial: str, *args: str, check: bool = True) -> str:
    out = run("adb", "-s", serial, *args)
    if check and out.returncode:
        raise SystemExit(f"adb {' '.join(args)}: {out.stderr.strip() or out.stdout.strip()}")
    return out.stdout


def sh(serial: str, cmd: str, check: bool = True) -> str:
    return adb(serial, "shell", cmd, check=check)


def pick_serial(device: str, serial: str | None) -> str:
    lines = run("adb", "devices").stdout.splitlines()[1:]
    serials = [line.split()[0] for line in lines if line.endswith("\tdevice")]
    if serial:
        if serial not in serials:
            raise SystemExit(f"{serial} isn't connected (connected: {serials or 'none'})")
        return serial
    models = {s: MODELS.get(sh(s, "getprop ro.product.model").strip()) for s in serials}
    if found := [s for s, name in models.items() if name == device]:
        return found[0]
    unknown = [s for s, name in models.items() if name is None]
    if len(unknown) == 1 and device not in MODELS.values():
        return unknown[0]
    raise SystemExit(f"no {device} on adb (connected: {serials or 'none'}); plug it in, or pass --serial")


def sd_root(serial: str) -> str:
    """The SD card's mount, /storage/XXXX-XXXX, the one holding Movies/."""
    vols = [v for v in sh(serial, "ls /storage").split() if v not in ("emulated", "self")]
    for v in vols:
        if sh(serial, f"[ -d /storage/{v}/Movies ] && echo yes", check=False).strip() == "yes":
            return f"/storage/{v}"
    raise SystemExit(f"no SD card with a Movies folder (volumes: {vols})")


def manifest(device: str, url: str) -> dict:
    with urllib.request.urlopen(f"{url}/api/media/sync?device={device}", timeout=30) as r:
        return json.load(r)


def rel_of(path: str) -> str:
    """Where a share path goes under SD Movies/: 480p/tv/X.mp4 or library/tv/X.mkv -> tv/X.*"""
    first, _, rest = path.partition("/")
    return rest if first in ("480p", "library") else path


def wanted(files: list[dict], share: Path) -> dict[str, Entry]:
    """SD-relative path -> what to copy there: the videos, and what sits beside them."""
    out = {}
    for f in files:
        src = share / f["path"]
        rel = rel_of(f["path"])
        out[rel] = Entry(src, f["bytes"])
        for name in [src.stem + ext for ext in SIDECAR] + list(POSTERS):
            side = src.parent / name
            if side.is_file():
                out[f"{PurePosixPath(rel).parent}/{name}"] = Entry(side, side.stat().st_size)
    return out


def plan(want: dict[str, Entry], have: dict[str, int]) -> Plan:
    """Copy what's missing or a different size; delete what isn't wanted."""
    push = sorted(r for r, e in want.items() if have.get(r) != e.size)
    drop = sorted(r for r in have if r not in want)
    need = sum(want[r].size for r in push) - sum(have.get(r, 0) for r in push)
    return Plan(push, drop, need, sum(have[r] for r in drop))


def on_card(serial: str, root: str) -> dict[str, int]:
    """SD-relative path -> bytes, for every file in the folders this tool owns."""
    dirs = " ".join(shlex.quote(f"{root}/Movies/{d}") for d in OWNED)
    out = sh(
        serial,
        f'for d in {dirs}; do [ -d "$d" ] && find "$d" -type f -exec stat -c \'%s|%n\' {{}} +; done',
        check=False,
    )
    have = {}
    prefix = f"{root}/Movies/"
    for line in out.splitlines():
        size, _, path = line.partition("|")
        if path.startswith(prefix) and size.isdigit():
            rel = path.removeprefix(prefix)
            if not PurePosixPath(rel).name.startswith("."):
                have[rel] = int(size)
    return have


def free_bytes(serial: str, root: str) -> int:
    line = sh(serial, f"df -k {shlex.quote(root)}").splitlines()[-1].split()
    return int(line[3]) * 1024


def playlists(tv: list[str], root: str) -> dict[str, str]:
    """One .m3u per show or season folder. A show with season folders can also
    have a loose one-off or two beside them (Rhea Gall Force): each gets its own."""
    by_dir: dict[str, list[str]] = defaultdict(list)
    for f in tv:
        by_dir[str(PurePosixPath(f).parent)].append(f)
    # folders that hold season folders: tv/<Show>/<Season> has 3 parts, so its parent is tv/<Show>
    has_sub = {str(PurePosixPath(d).parent) for d in by_dir if len(PurePosixPath(d).parts) > 2}
    lists: dict[str, list[str]] = {}
    for d, eps in by_dir.items():
        if d in has_sub and len(eps) <= 2:
            for e in eps:
                lists[PurePosixPath(e).stem] = [e]
        else:
            lists[PurePosixPath(d).name] = sorted(eps)
    for src, dst in MERGE.items():
        if src in lists and dst in lists:
            lists[dst] += lists.pop(src)
    return {
        f"{name}.m3u": "#EXTM3U\n" + "".join(f"#EXTINF:-1,{PurePosixPath(e).stem}\n{root}/Movies/{e}\n" for e in eps)
        for name, eps in sorted(lists.items())
    }


def gb(n: int) -> str:
    return f"{n / 2**30:.1f} GB"


def show(sign: str, items: list[str], limit: int = 15) -> None:
    for r in items[:limit]:
        print(f"    {sign} {r}")
    if len(items) > limit:
        print(f"    ... and {len(items) - limit} more")


def apply(serial: str, root: str, want: dict[str, Entry], p: Plan, tv: list[str]) -> int:
    """Delete, copy, then the playlists and a media scan; returns how many playlists."""
    base = f"{root}/Movies"
    for start in range(0, len(p.drop), BATCH):
        batch = p.drop[start : start + BATCH]  # noqa: E203 (black's slice spacing)
        sh(serial, "rm -f " + " ".join(shlex.quote(f"{base}/{r}") for r in batch))
    for d in OWNED:
        q = shlex.quote(f"{base}/{d}")
        sh(serial, f"[ -d {q} ] && find {q} -mindepth 1 -type d -empty -delete", check=False)
    for n, r in enumerate(p.push, 1):
        print(f"  [{n}/{len(p.push)}] {r}", flush=True)
        adb(serial, "push", str(want[r].src), f"{base}/{r}")

    lists = playlists(tv, root)
    pl = shlex.quote(f"{base}/{PLAYLISTS}")
    sh(serial, f"mkdir -p {pl} && rm -f {pl}/*.m3u")
    if lists:
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in lists.items():
                (Path(tmp) / name).write_text(text)
            adb(serial, "push", *[str(Path(tmp) / n) for n in lists], f"{base}/{PLAYLISTS}/")
    volume = root.rsplit("/", 1)[1].lower()
    sh(serial, f"content call --uri content://media --method scan_volume --arg {volume}", check=False)
    return len(lists)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("device", help="its name in devices.yaml: rg476h, retroid")
    ap.add_argument("--apply", action="store_true", help="make the changes (default: only say what they'd be)")
    ap.add_argument("--serial", help="adb serial, when it can't tell which device is which")
    ap.add_argument("--share", type=Path, default=SHARE, help=f"the movies share on this laptop ({SHARE})")
    ap.add_argument("--url", default=URL)
    args = ap.parse_args()

    if not (args.share / "480p").is_dir():
        raise SystemExit(f"{args.share} isn't mounted (scripts/brick-shares-mount.sh in homelab-context)")
    serial = pick_serial(args.device, args.serial)
    root = sd_root(serial)
    m = manifest(args.device, args.url)
    listed = wanted(m["files"], args.share)
    want = {r: e for r, e in listed.items() if e.src.is_file()}
    if skipped := sorted(set(listed) - set(want)):
        print(f"not on the share (skipped): {len(skipped)}, e.g. {skipped[0]}")
    p = plan(want, on_card(serial, root))
    free = free_bytes(serial, root)
    tv = sorted(r for r in want if r.startswith("tv/") and not r.endswith(SIDECAR))

    picked = "its picks" if m.get("picked") else "no picks saved: everything"
    print(
        f"{args.device} ({serial}), card {root}: {len(m['files'])} videos listed ({picked}),"
        f" {m.get('waiting_for_480p', 0)} waiting for a 480p copy"
    )
    print(f"  copy:   {len(p.push)} files, {gb(sum(want[r].size for r in p.push))}")
    show("+", p.push)
    print(f"  delete: {len(p.drop)} files, {gb(p.freed)}")
    show("-", p.drop)
    after = free + p.freed - p.need
    print(f"  card free {gb(free)}; after: {gb(after)}")
    if after < SLACK:
        raise SystemExit("won't fit: unpick some videos on the page first")
    if not args.apply:
        print("dry run: nothing changed. Run again with --apply to do it.")
        return
    n = apply(serial, root, want, p, tv)
    print(f"done: {len(p.push)} copied, {len(p.drop)} deleted, {n} TV playlists. In Daijisho, sync Movies and TV.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
