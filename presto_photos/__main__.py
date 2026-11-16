"""presto-photos: `python -m presto_photos web|convert|sort|feed`.

feed: presto-feed, the Brick screen's JSON from Prometheus (feed.py); no store.
sort: the media sorter (sorter.py): files audio and video uploads on the media drive.

PHOTOS_ROOT        the store (default /data)
PHOTOS_LIMIT_GB    the store's size cap (default 5)
PORT               web: the page; convert, sort: /metrics and /health; feed: the JSON (default 8080)
DEVICE_DIR         web: the Presto's code to serve over Wi-Fi (default /code/device)
MEDIA_ROOT         web, sort: the media inbox (inbox.py); web: unset, no media uploads
MEDIA_LIMIT_GB     web: the inbox's cap, all uploads together (default 20)
MEDIA_MIN_FREE_GB  web: free space to leave on the inbox's disk (default 50)
MEDIA_DEVICES      web: devices.yaml (default: the copy in the image)
MOVIES_ROOT        sort: the drive's movies folder (default /movies)
MUSIC_ROOT         sort: the drive's music folder (default /music)
"""

import os
import sys
from pathlib import Path

import brick_observe

GB = 1 << 30


def default_devices() -> Path:
    """devices.yaml: the image's copy, else the repo's (k8s_config/, the ConfigMap's source)."""
    here = Path(__file__).resolve().parent
    image = here / "devices.yaml"
    return image if image.exists() else here.parent / "k8s_config" / "devices.yaml"


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    mode = argv[0] if argv else ""
    if mode not in ("web", "convert", "sort", "feed"):
        sys.exit("usage: python -m presto_photos web|convert|sort|feed")
    brick_observe.init("presto-feed" if mode == "feed" else f"presto-photos-{mode}")
    port = int(os.environ.get("PORT", "8080"))
    if mode == "feed":
        from . import feed

        feed.serve(port)
        return
    if mode == "sort":
        from brick_observe import metrics

        from . import sorter
        from .inbox import Inbox

        metrics.serve(port)  # /metrics, and /health for the kubelet
        roots = sorter.Roots(
            Path(os.environ.get("MOVIES_ROOT", "/movies")), Path(os.environ.get("MUSIC_ROOT", "/music"))
        )
        sorter.run(Inbox(os.environ.get("MEDIA_ROOT", "/media")), roots)
        return
    from .store import Store

    store = Store(os.environ.get("PHOTOS_ROOT", "/data"), int(float(os.environ.get("PHOTOS_LIMIT_GB", "5")) * GB))
    if mode == "web":
        from . import device, devices, web
        from .inbox import Inbox

        device_dir = Path(os.environ.get("DEVICE_DIR", "/code/device"))
        release = device.load(device_dir) if device_dir.is_dir() else None
        inbox = None
        if os.environ.get("MEDIA_ROOT"):
            inbox = Inbox(
                os.environ["MEDIA_ROOT"],
                int(float(os.environ.get("MEDIA_LIMIT_GB", "20")) * GB),
                int(float(os.environ.get("MEDIA_MIN_FREE_GB", "50")) * GB),
                create=True,
            )
        tags = devices.load(Path(os.environ.get("MEDIA_DEVICES") or default_devices()))
        web.serve(store, port, release, inbox, tags)
    else:
        from brick_observe import metrics

        from . import worker

        # The converter serves only metrics (prometheus-client answers every
        # path, so the kubelet's /health probe works too). Nothing else reaches
        # it: NetworkPolicy.
        metrics.serve(port)
        worker.run(store)


if __name__ == "__main__":
    main()
