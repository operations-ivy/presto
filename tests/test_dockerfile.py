"""The image copies an explicit file list (Dockerfile): every module and page must be on it.
0.14.0 shipped without picks.py and crash-looped."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_module_and_page_is_copied_into_the_image():
    dockerfile = (ROOT / "Dockerfile").read_text()
    files = [*(ROOT / "presto_photos").glob("*.py"), *(ROOT / "presto_photos" / "static").glob("*.html")]
    missing = [str(f.relative_to(ROOT)) for f in files if str(f.relative_to(ROOT)) not in dockerfile]
    assert not missing, f"add to the Dockerfile's COPY: {missing}"
