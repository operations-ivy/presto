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

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path, PurePosixPath

URL = "https://presto.brick.nozdormu.cloud"
SHARE = Path("/mnt/brick/movies")
OWNED = ("movies", "tv")  # the SD Movies/ folders this tool keeps in step
PLAYLISTS = "tv-shows"
SIDECAR = (".jpg", ".jpeg", ".png", ".webp", ".srt", ".ass", ".vtt")
POSTERS = ("poster.jpg", "poster.png", "poster.jpeg", "poster.webp")
MODELS = {"RG_476H": "rg476h"}  # adb's ro.product.model -> devices.yaml name
MERGE = {"Kite (Director's Cut)": "Kite"}  # Daijisho drops "(...)" and skipped the renamed list
SLACK = 512 << 20  # leave this free on the card


def adb(serial: str, *args: str, check=True) -> str:
    out = subprocess.run(["adb", "-s", serial, *args], capture_output=True, text=True)
    if check and out.returncode:
        raise SystemExit(f"adb {' '.join(args)}: {out.stderr.strip() or out.stdout.strip()}")
    return out.stdout


def sh(serial: str, cmd: str, check=True) -> str:
    return adb(serial, "shell", cmd, check=check)


def pick_serial(device: str, serial: str | None) -> str:
    lines = subprocess.run(["adb", "devices"], capture_output=True, text=True).stdout.splitlines()[1:]
    serials = [line.split()[0] for line in lines if line.endswith("\tdevice")]
    if serial:
        if serial not in serials:
            raise SystemExit(f"{serial} isn't connected (connected: {serials or 'none'})")
        return serial
    for s in serials:
        model = sh(s, "getprop ro.product.model").strip()
        if MODELS.get(model) == device:
            return s
    others = [s for s in serials if MODELS.get(sh(s, "getprop ro.product.model").strip()) is None]
    if len(others) == 1 and device not in MODELS.values():
        return others[0]
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


def wanted(files: list[dict], share: Path) -> dict[str, tuple[Path, int]]:
    """SD-relative path -> (file on the share, bytes): the videos, and what sits beside them."""
    out = {}
    for f in files:
        src = share / f["path"]
        rel = rel_of(f["path"])
        out[rel] = (src, f["bytes"])
        folder = src.parent
        names = [PurePosixPath(src.stem + ext) for ext in SIDECAR] + [PurePosixPath(p) for p in POSTERS]
        for name in names:
            side = folder / name
            if side.is_file():
                out[str(PurePosixPath(rel).parent / name)] = (side, side.stat().st_size)
    return out


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
    """One .m3u per show or season folder; loose one-offs beside season folders get their own."""
    by_dir = defaultdict(list)
    for f in tv:
        by_dir[str(PurePosixPath(f).parent)].append(f)
    has_sub = {str(PurePosixPath(d).parent) for d in by_dir if len(PurePosixPath(d).parts) > 2}
    lists = {}
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


def main():
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
    want = wanted(m["files"], args.share)
    have = on_card(serial, root)
    missing = [r for r in want if not want[r][0].is_file()]
    if missing:
        print(f"not on the share (skipped): {len(missing)}, e.g. {missing[0]}")
        for r in missing:
            del want[r]
    push = sorted(r for r, (_, n) in want.items() if have.get(r) != n)
    drop = sorted(r for r in have if r not in want)
    need = sum(want[r][1] for r in push) - sum(have[r] for r in push if r in have)
    freed = sum(have[r] for r in drop)
    free = free_bytes(serial, root)
    tv = sorted(r for r in want if r.startswith("tv/") and not r.endswith(SIDECAR))

    print(
        f"{args.device} ({serial}), card {root}: {len(m['files'])} videos listed"
        f" ({'its picks' if m.get('picked') else 'no picks saved: everything'}),"
        f" {m.get('waiting_for_480p', 0)} waiting for a 480p copy"
    )
    print(f"  copy:   {len(push)} files, {gb(sum(want[r][1] for r in push))}")
    for r in push[:15]:
        print(f"    + {r}")
    if len(push) > 15:
        print(f"    ... and {len(push) - 15} more")
    print(f"  delete: {len(drop)} files, {gb(freed)}")
    for r in drop[:15]:
        print(f"    - {r}")
    if len(drop) > 15:
        print(f"    ... and {len(drop) - 15} more")
    print(f"  card free {gb(free)}; after: {gb(free + freed - need)}")
    if free + freed - need < SLACK:
        raise SystemExit("won't fit: unpick some videos on the page first")
    if not args.apply:
        print("dry run: nothing changed. Run again with --apply to do it.")
        return

    base = f"{root}/Movies"
    for start in range(0, len(drop), 50):
        sh(serial, "rm -f " + " ".join(shlex.quote(f"{base}/{r}") for r in drop[start:][:50]))
    for d in OWNED:
        sh(
            serial,
            f"[ -d {shlex.quote(f'{base}/{d}')} ] && find {shlex.quote(f'{base}/{d}')} -mindepth 1 -type d -empty"
            " -delete",
            check=False,
        )
    for n, r in enumerate(push, 1):
        src = want[r][0]
        print(f"  [{n}/{len(push)}] {r}", flush=True)
        adb(serial, "push", str(src), f"{base}/{r}")

    lists = playlists(tv, root)
    out = Path.home() / ".cache" / "handheld-sync" / args.device
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.m3u"):
        old.unlink()
    for name, text in lists.items():
        (out / name).write_text(text)
    pl = f"{base}/{PLAYLISTS}"
    sh(serial, f"mkdir -p {shlex.quote(pl)} && rm -f {shlex.quote(pl)}/*.m3u")
    if lists:
        adb(serial, "push", *[str(out / n) for n in lists], pl + "/")
    sh(
        serial,
        "content call --uri content://media --method scan_volume --arg " + root.rsplit("/", 1)[1].lower(),
        check=False,
    )
    print(
        f"done: {len(push)} copied, {len(drop)} deleted, {len(lists)} TV playlists."
        " In Daijisho, sync the Movies and TV platforms."
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
