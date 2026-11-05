# Rolls back a Wi-Fi update that doesn't run (presto#9). main.py calls guard() before
# anything else. This file and main.py only change over USB (tools/deploy): an update
# must never be able to break its own way back.
#
# /ota/state.json: {"version", "status": "ok" | "trial", "boots", "prev", "added", "bad"}
# /ota/prev/: the files an update replaced, as they were.
#
# An update boots as "trial". neon.ota confirms it once the app has run a minute;
# a crash on trial restarts the Presto (main.py). After MAX_TRIES trial boots with
# no confirm, the old files go back and the version is marked bad (not fetched again).
#
# Runs on MicroPython and on the laptop (tests): plain os, json, no ilistdir.
import json
import os

STATE = "/ota/state.json"
PREV = "/ota/prev"
MAX_TRIES = 2


def read(root=""):
    try:
        with open(root + STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write(state, root=""):
    makedirs(root + "/ota")
    tmp = root + STATE + ".new"
    with open(tmp, "w") as f:
        json.dump(state, f)
    try:
        os.remove(root + STATE)
    except OSError:
        pass
    os.rename(tmp, root + STATE)


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
        d = path + "/" + d
        try:
            os.rmdir(d)
        except OSError:
            pass
    try:
        os.rmdir(path)
    except OSError:
        pass


def rollback(state, root=""):
    """Put back the files the update replaced, drop the ones it added, mark it bad."""
    prev = root + PREV
    if is_dir(prev):
        for rel in files_under(prev):
            copy(prev + "/" + rel, root + "/" + rel)
    for rel in state.get("added") or ():
        try:
            os.remove(root + "/" + rel)
        except OSError:
            pass
    remove_tree(prev)
    new = {"version": state.get("prev"), "status": "ok", "bad": state.get("version")}
    write(new, root)
    print("[ota] rolled back %s to %s" % (state.get("version"), state.get("prev")))
    return new


def guard(root=""):
    """Count a trial boot; roll back after MAX_TRIES. Returns the state, or None."""
    state = read(root)
    if not state or state.get("status") != "trial":
        return state
    state["boots"] = state.get("boots", 0) + 1
    if state["boots"] > MAX_TRIES:
        return rollback(state, root)
    write(state, root)
    print("[ota] trial boot %d of %s" % (state["boots"], state.get("version")))
    return state
