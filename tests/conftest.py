import sys
from pathlib import Path

# The device's pure modules (neon.tz, nav, conf, wx, sprites) import under CPython.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "device" / "lib"))
