import sys
from pathlib import Path

# The device's pure modules (neon.tz, nav, conf, wx, sprites) import under CPython.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device" / "lib"))
sys.path.insert(0, str(ROOT))  # presto_photos
