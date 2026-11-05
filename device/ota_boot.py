# Installs and rolls back Wi-Fi updates at boot (presto#9). main.py calls boot() first,
# before Presto() starts the display. This file and main.py only change over USB
# (tools/deploy): an update must never be able to break its own way back.
#
# Why at boot: once the display runs (on the chip's second core), any write to the
# flash deadlocks in the firmware (found 2026-11-05: the watchdog had to reset it).
# So the app only writes the SD card (neon.ota): it downloads an update into
# /sd/ota/new and leaves /sd/ota/pending.json; this copies it into the flash, keeping
# the old files in /sd/ota/prev, and boots the new code on trial.
#
# Flash: /ota/state.json  {"version", "status": "ok" | "trial", "boots", "prev", "added", "bad"}
# SD:    /sd/ota/pending.json, new/, prev/, confirmed (the version the app confirmed)
#
# A trial is confirmed once the app has run a minute on it (it writes /sd/ota/confirmed;
# the next boot makes it "ok"). A trial that crashes restarts (main.py); after
# MAX_TRIES trial boots with no confirm, the old files go back and the version is
# marked bad (not fetched again).
#
# Runs on MicroPython and on the laptop (tests): plain os and json, no ilistdir.
import json
import os

STATE = "/ota/state.json"  # flash
SD = "/sd"
PENDING = "/sd/ota/pending.json"
NEW = "/sd/ota/new"
PREV = "/sd/ota/prev"
CONFIRMED = "/sd/ota/confirmed"
MAX_TRIES = 2


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def read(root=""):
    return read_json(root + STATE)


def write_json(path, value):
    makedirs(path.rsplit("/", 1)[0])
    tmp = path + ".new"
    with open(tmp, "w") as f:
        json.dump(value, f)
    remove(path)
    os.rename(tmp, path)


def write(state, root=""):
    write_json(root + STATE, state)


def remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def is_dir(path):
    try:
        return os.stat(path)[0] & 0x4000 != 0
    except OSError:
        return False


def makedirs(path):
    part = ""
    for name in path.strip("/").split("/"):
        part += "/" + name
        if not is_dir(part):
            try:
                os.mkdir(part)
            except OSError:
                pass


def files_under(top, rel=""):
    """Every file below top, as paths relative to it."""
    out = []
    for name in os.listdir(top + "/" + rel if rel else top):
        sub = rel + "/" + name if rel else name
        if is_dir(top + "/" + sub):
            out += files_under(top, sub)
        else:
            out.append(sub)
    return out


def copy(src, dst):
    makedirs(dst.rsplit("/", 1)[0])
    with open(src, "rb") as a, open(dst, "wb") as b:
        while True:
            chunk = a.read(2048)
            if not chunk:
                break
            b.write(chunk)


def remove_tree(path):
    if not is_dir(path):
        return
    rels = files_under(path)
    for rel in rels:
        os.remove(path + "/" + rel)
    dirs = set()
    for rel in rels:  # every directory on the way down, deepest first
        parts = rel.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    for d in sorted(dirs, key=lambda x: -x.count("/")):
        try:
            os.rmdir(path + "/" + d)
        except OSError:
            pass
    try:
        os.rmdir(path)
    except OSError:
        pass


def install(root=""):
    """Copy a downloaded update (pending on the SD card) into the flash, keeping the
    files it replaces. Returns the new state, or None if nothing was pending."""
    pending = read_json(root + PENDING)
    if not pending:
        return None
    files, added = pending.get("files") or [], pending.get("added") or []
    if not files:  # the same files under a new version: just record it
        state = {"version": pending.get("version"), "status": "ok"}
    else:
        remove_tree(root + PREV)
        for path in files:
            if path not in added:
                copy(root + "/" + path, root + PREV + "/" + path)
        # trial first: if the copy is cut short, the next boots roll it back
        state = {"version": pending.get("version"), "status": "trial", "boots": 0,
                 "prev": pending.get("prev"), "added": added}
        write(state, root)
        for path in files:
            copy(root + NEW + "/" + path, root + "/" + path)
    write(state, root)
    remove(root + PENDING)
    remove(root + CONFIRMED)
    remove_tree(root + NEW)
    print("[ota] installed %s (%d files)" % (state["version"], len(files)))
    return state


def rollback(state, root=""):
    """Put back the files the update replaced, drop the ones it added, mark it bad."""
    prev = root + PREV
    if is_dir(prev):
        for rel in files_under(prev):
            copy(prev + "/" + rel, root + "/" + rel)
    for rel in state.get("added") or ():
        remove(root + "/" + rel)
    remove_tree(prev)
    new = {"version": state.get("prev"), "status": "ok", "bad": state.get("version")}
    write(new, root)
    print("[ota] rolled back %s to %s" % (state.get("version"), state.get("prev")))
    return new


def guard(root=""):
    """A trial the app confirmed becomes "ok"; otherwise count the boot, and roll back
    after MAX_TRIES. Returns the state, or None."""
    state = read(root)
    if not state or state.get("status") != "trial":
        return state
    confirmed = read_json(root + CONFIRMED)
    if confirmed and confirmed.get("version") == state.get("version"):
        state = {k: v for k, v in state.items() if k not in ("boots", "added")}
        state["status"] = "ok"
        write(state, root)
        remove(root + CONFIRMED)
        remove_tree(root + PREV)
        print("[ota] %s confirmed" % state.get("version"))
        return state
    state["boots"] = state.get("boots", 0) + 1
    if state["boots"] > MAX_TRIES:
        return rollback(state, root)
    write(state, root)
    print("[ota] trial boot %d of %s" % (state["boots"], state.get("version")))
    return state


def mount_sd():
    """The SD card, as the app mounts it (neon.app). False if there's none."""
    import machine
    import sdcard

    try:
        spi = machine.SPI(0, sck=machine.Pin(34), mosi=machine.Pin(35), miso=machine.Pin(36))
        os.mount(sdcard.SDCard(spi, machine.Pin(39), baudrate=24_000_000), SD)
        return True
    except Exception as e:
        print("[ota] no SD card:", repr(e))
        return False


def boot():
    """main.py: install a pending update, then guard the trial; before the display starts."""
    mounted = mount_sd()
    try:
        if mounted:
            install()
        return guard()
    finally:
        if mounted:
            try:
                os.umount(SD)  # the app mounts it again its own way
            except OSError:
                pass
