"""Updates over Wi-Fi (presto#9): the server's release, the Presto's updater and rollback."""

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
    ota.read(Stream(rel.bundle()), rel.manifest(), {"lib/neon/app.py"}, lambda p, pieces: got.update({p: b"".join(pieces)}))
    assert got == {"lib/neon/app.py": dict(rel.files)["lib/neon/app.py"]}


def test_a_bad_hash_or_version_is_refused(tmp_path):
    rel = make_device(tmp_path, {"settings.yaml": "a: 1\n"})
    m = rel.manifest()
    bad = json.loads(json.dumps(m))
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


# --- a whole update, and its rollback, on a fake flash ------------------------
class Net:
    up = True


def flash_with(tmp_path, files, state):
    root = tmp_path / "flash"
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
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


def test_an_update_swaps_files_in_keeps_the_old_and_boots_on_trial(tmp_path, monkeypatch):
    root = flash_with(tmp_path, {"lib/neon/app.py": "old app", "settings.yaml": "same"}, {"version": "v1", "status": "ok"})
    rel = make_device(tmp_path, {"lib/neon/app.py": "new app", "settings.yaml": "same", "lib/neon/new.py": "added"})
    up, restarts = updater_for(root, rel, monkeypatch)
    assert up.check() is True and restarts == [1]
    assert Path(root, "lib/neon/app.py").read_text() == "new app"
    assert Path(root, "lib/neon/new.py").read_text() == "added"
    assert Path(root, "ota/prev/lib/neon/app.py").read_text() == "old app"
    assert not Path(root, "ota/prev/settings.yaml").exists()  # unchanged: not touched
    state = ota_boot.read(root)
    assert state == {"version": rel.version, "status": "trial", "boots": 0, "prev": "v1", "added": ["lib/neon/new.py"]}

    # two boots that never confirm, then the third rolls it back
    assert ota_boot.guard(root)["boots"] == 1
    assert ota_boot.guard(root)["boots"] == 2
    back = ota_boot.guard(root)
    assert back == {"version": "v1", "status": "ok", "bad": rel.version}
    assert Path(root, "lib/neon/app.py").read_text() == "old app"
    assert not Path(root, "lib/neon/new.py").exists() and not Path(root, "ota/prev").exists()

    # and the bad version isn't fetched again
    up2, restarts2 = updater_for(root, rel, monkeypatch)
    assert up2.check() is False and restarts2 == []


def test_a_confirmed_update_drops_its_backups(tmp_path, monkeypatch):
    root = flash_with(tmp_path, {"lib/neon/app.py": "old"}, {"version": "v1", "status": "ok"})
    rel = make_device(tmp_path, {"lib/neon/app.py": "new"})
    up, _ = updater_for(root, rel, monkeypatch)
    up.check()
    after = ota.Updater("https://presto.example/device", root=root)  # the next boot
    ota_boot.guard(root)
    after.confirm()
    assert ota_boot.read(root) == {"version": rel.version, "status": "ok", "prev": "v1"}
    assert not Path(root, "ota/prev").exists()


def test_same_files_new_version_just_records_it(tmp_path, monkeypatch):
    root = flash_with(tmp_path, {"lib/neon/app.py": "same"}, {"version": "usb", "status": "ok"})
    rel = make_device(tmp_path, {"lib/neon/app.py": "same"})
    up, restarts = updater_for(root, rel, monkeypatch)
    assert up.check() is False and restarts == []
    assert ota_boot.read(root)["version"] == rel.version


def test_no_check_while_a_trial_is_unconfirmed(tmp_path, monkeypatch):
    root = flash_with(tmp_path, {"lib/neon/app.py": "x"}, {"version": "v2", "status": "trial", "boots": 1})
    rel = make_device(tmp_path, {"lib/neon/app.py": "y"})
    up, restarts = updater_for(root, rel, monkeypatch)
    assert up.check() is False and restarts == []


def test_guard_without_state_is_harmless(tmp_path):
    assert ota_boot.guard(str(tmp_path)) is None
