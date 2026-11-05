"""presto-photos: `python -m presto_photos web|convert|feed`.

    feed: presto-feed, the Brick screen's JSON from Prometheus (feed.py); no store.

    PHOTOS_ROOT        the store (default /data)
    PHOTOS_LIMIT_GB    the store's size cap (default 5)
    PORT               web: the page; convert: /metrics and /health; feed: the JSON (default 8080)
    DEVICE_DIR         web: the Presto's code to serve over Wi-Fi (default /code/device)
"""

import os
import sys

import brick_observe


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    mode = argv[0] if argv else ""
    if mode not in ("web", "convert", "feed"):
        sys.exit("usage: python -m presto_photos web|convert|feed")
    brick_observe.init("presto-feed" if mode == "feed" else f"presto-photos-{mode}")
    port = int(os.environ.get("PORT", "8080"))
    if mode == "feed":
        from . import feed

        feed.serve(port)
        return
    from .store import Store

    store = Store(os.environ.get("PHOTOS_ROOT", "/data"), int(float(os.environ.get("PHOTOS_LIMIT_GB", "5")) * (1 << 30)))
    if mode == "web":
        from . import web

        from pathlib import Path

        from . import device

        device_dir = Path(os.environ.get("DEVICE_DIR", "/code/device"))
        release = device.load(device_dir) if device_dir.is_dir() else None
        web.serve(store, port, release)
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
