"""Updates over Wi-Fi (presto#9): the server's release, the Presto's updater and rollback."""

import copy
import io
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "device"))  # ota_boot lives at the flash's root

import ota_boot  # noqa: E402
from neon import ota  # noqa: E402

from presto_photos import device  # noqa: E402


@pytest.fixture(autouse=True)
def ticks(monkeypatch):
    """MicroPython's tick functions, for the Updater on CPython."""
    monkeypatch.setattr(time, "ticks_ms", lambda: 0, raising=False)
    monkeypatch.setattr(time, "ticks_add", lambda a, b: a + b, raising=False)
    monkeypatch.setattr(time, "ticks_diff", lambda a, b: a - b, raising=False)


def make_device(tmp_path, files):
    d = tmp_path / "src"
    for path, text in files.items():
        (d / path).parent.mkdir(parents=True, exist_ok=True)
        (d / path).write_text(text)
    return device.load(d)


# --- the server -------------------------------------------------------------
def test_release_skips_the_boot_files_and_caches(tmp_path):
    rel = make_device(tmp_path, {"main.py": "boot", "ota_boot.py": "guard", "settings.yaml": "a: 1",
                                 "secrets.py": "WIFI_PASSWORD = 'x'", "lib/secrets.py": "no",
                                 "lib/neon/app.py": "x = 1", "lib/neon/__pycache__/app.mpy": "junk"})
    assert [p for p, _ in rel.files] == ["lib/neon/app.py", "settings.yaml"]


def test_release_version_follows_the_contents(tmp_path):
    a = make_device(tmp_path / "a", {"lib/neon/app.py": "x = 1"})
    b = make_device(tmp_path / "b", {"lib/neon/app.py": "x = 1"})
    c = make_device(tmp_path / "c", {"lib/neon/app.py": "x = 2"})
    assert a.version == b.version != c.version and len(a.version) == 12


def test_the_real_device_dir_makes_a_release():
    rel = device.load(ROOT / "device")
    paths = [p for p, _ in rel.files]
    assert "lib/neon/ota.py" in paths and "settings.yaml" in paths
    assert "main.py" not in paths and "ota_boot.py" not in paths and not any("secrets" in p for p in paths)


# --- the Presto reads a bundle ----------------------------------------------
class Stream(io.BytesIO):
    """A TLS socket only has readinto; give it small reads like the real one."""

    def readinto(self, b):
        return super().readinto(memoryview(b)[:700])


def test_bundle_round_trip_writes_only_what_changed(tmp_path):
    rel = make_device(tmp_path, {"lib/neon/app.py": "x = 1\n" * 500, "settings.yaml": "a: 1\n"})
    got = {}
    keep = lambda p, pieces: got.update({p: b"".join(pieces)})  # noqa: E731
    ota.read(Stream(rel.bundle()), rel.manifest(), {"lib/neon/app.py"}, keep)
    assert got == {"lib/neon/app.py": dict(rel.files)["lib/neon/app.py"]}


def test_a_bad_hash_or_version_is_refused(tmp_path):
    rel = make_device(tmp_path, {"settings.yaml": "a: 1\n"})
    m = rel.manifest()
    bad = copy.deepcopy(m)
    bad["files"][0]["sha"] = "0" * 64
    with pytest.raises(ValueError, match="hash"):
        ota.read(Stream(rel.bundle()), bad, {"settings.yaml"}, lambda p, pieces: list(pieces))
    other = dict(m, version="something-else")
    with pytest.raises(ValueError, match="manifest says"):
        ota.read(Stream(rel.bundle()), other, set(), lambda p, pieces: list(pieces))
    with pytest.raises(OSError):  # cut short
        ota.read(Stream(rel.bundle()[:-3]), m, {"settings.yaml"}, lambda p, pieces: list(pieces))


def test_plan_lists_changed_and_added():
    m = {"files": [{"path": "a.py", "sha": "1"}, {"path": "b.py", "sha": "2"}, {"path": "c.py", "sha": "3"}]}
    local = {"a.py": "1", "b.py": "old"}
    assert ota.plan(m, local.get) == (["b.py", "c.py"], ["c.py"])


# --- a whole update, and its rollback, on a fake flash and SD card ------------
def flash_with(tmp_path, files, state, sd=True):
    root = tmp_path / "flash"
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    if sd:
        (root / "sd").mkdir(parents=True, exist_ok=True)
    ota_boot.write(state, str(root))
    return str(root)


def updater_for(root, rel, monkeypatch):
    up = ota.Updater("https://presto.example/device", root=root)
    responses = {"/manifest.json": rel.manifest(), "/bundle": rel.bundle()}

    class Resp:
        def __init__(self, body):
            self.body = body
            self.raw = Stream(body if isinstance(body, bytes) else b"")

        def json(self):
            return self.body

        def close(self):
            pass

    monkeypatch.setattr(up, "_get", lambda path: Resp(responses[path]))
    restarts = []
    monkeypatch.setattr(ota, "restart", lambda: restarts.append(1))
    return up, restarts


def test_the_app_only_writes_the_sd_card_and_boot_installs(tmp_path, monkeypatch):
    before = {"lib/neon/app.py": "old app", "settings.yaml": "same"}
    root = flash_with(tmp_path, before, {"version": "v1", "status": "ok"})
    rel = make_device(tmp_path, {"lib/neon/app.py": "new app", "settings.yaml": "same", "lib/neon/new.py": "added"})
    up, restarts = updater_for(root, rel, monkeypatch)
    assert up.check() is True and restarts == [1]
    # in the app: nothing on the flash changed (a flash write there deadlocks the firmware)
    assert Path(root, "lib/neon/app.py").read_text() == "old app"
    assert ota_boot.read(root) == {"version": "v1", "status": "ok"}
    assert Path(root, "sd/ota/new/lib/neon/app.py").read_text() == "new app"
    assert not Path(root, "sd/ota/new/settings.yaml").exists()  # unchanged: not downloaded to disk
    # the next boot, before the display: install
    state = ota_boot.install(root)
    assert state == {"version": rel.version, "status": "trial", "boots": 0, "prev": "v1", "added": ["lib/neon/new.py"]}
    assert Path(root, "lib/neon/app.py").read_text() == "new app"
    assert Path(root, "lib/neon/new.py").read_text() == "added"
    assert Path(root, "sd/ota/prev/lib/neon/app.py").read_text() == "old app"
    assert not Path(root, "sd/ota/pending.json").exists() and not Path(root, "sd/ota/new").exists()
    assert ota_boot.install(root) is None  # nothing pending any more

    # two boots that never confirm, then the third rolls it back
    assert ota_boot.guard(root)["boots"] == 1
    assert ota_boot.guard(root)["boots"] == 2
    back = ota_boot.guard(root)
    assert back == {"version": "v1", "status": "ok", "bad": rel.version}
    assert Path(root, "lib/neon/app.py").read_text() == "old app"
    assert not Path(root, "lib/neon/new.py").exists() and not Path(root, "sd/ota/prev").exists()

    # and the bad version isn't fetched again
    up2, restarts2 = updater_for(root, rel, monkeypatch)
    assert up2.check() is False and restarts2 == []


def test_a_confirmed_update_is_ok_at_the_next_boot(tmp_path, monkeypatch):
    root = flash_with(tmp_path, {"lib/neon/app.py": "old"}, {"version": "v1", "status": "ok"})
    rel = make_device(tmp_path, {"lib/neon/app.py": "new"})
    up, _ = updater_for(root, rel, monkeypatch)
    up.check()
    ota_boot.install(root)
    ota_boot.guard(root)  # the first trial boot
    running = ota.Updater("https://presto.example/device", root=root)
    assert not running.confirmed
    running.confirm()  # a minute in: written to the card, not the flash
    assert json.loads(Path(root, "sd/ota/confirmed").read_text()) == {"version": rel.version}
    assert ota_boot.read(root)["status"] == "trial"
    assert ota_boot.guard(root) == {"version": rel.version, "status": "ok", "prev": "v1"}  # the next boot
    assert not Path(root, "sd/ota/prev").exists() and not Path(root, "sd/ota/confirmed").exists()


def test_same_files_new_version_is_recorded_at_boot(tmp_path, monkeypatch):
    root = flash_with(tmp_path, {"lib/neon/app.py": "same"}, {"version": "usb", "status": "ok"})
    rel = make_device(tmp_path, {"lib/neon/app.py": "same"})
    up, restarts = updater_for(root, rel, monkeypatch)
    assert up.check() is False and restarts == []
    assert ota_boot.read(root)["version"] == "usb"  # not written from the app
    assert ota_boot.install(root) == {"version": rel.version, "status": "ok"}


def test_no_check_while_on_trial_or_with_one_pending(tmp_path, monkeypatch):
    root = flash_with(tmp_path, {"lib/neon/app.py": "x"}, {"version": "v2", "status": "trial", "boots": 1})
    rel = make_device(tmp_path, {"lib/neon/app.py": "y"})
    up, restarts = updater_for(root, rel, monkeypatch)
    assert up.check() is False and restarts == []
    root2 = flash_with(tmp_path / "b", {"lib/neon/app.py": "x"}, {"version": "v1", "status": "ok"})
    ota_boot.write_json(root2 + ota_boot.PENDING, {"version": "v2", "files": []})
    up2, restarts2 = updater_for(root2, rel, monkeypatch)
    assert up2.check() is False and restarts2 == []


def test_no_sd_card_no_updates(tmp_path, monkeypatch):
    root = flash_with(tmp_path, {"lib/neon/app.py": "x"}, {"version": "v1", "status": "ok"}, sd=False)
    rel = make_device(tmp_path, {"lib/neon/app.py": "y"})
    up, restarts = updater_for(root, rel, monkeypatch)
    assert up.check() is False and restarts == []


def test_guard_without_state_is_harmless(tmp_path):
    assert ota_boot.guard(str(tmp_path)) is None
