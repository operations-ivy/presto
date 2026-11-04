"""A tiny YAML subset for the device's settings: flat `key: value` lines.

MicroPython has no YAML library, and the homelab's config is YAML wherever
possible, so settings.yaml is read with this: comments, blank lines, strings
(bare or quoted), ints, floats, true/false/null. No nesting, no lists.
"""


def parse_value(raw):
    v = raw.strip()
    if not v or v in ("null", "~"):
        return None
    if v[0] in "\"'" and v[-1] == v[0] and len(v) >= 2:
        return v[1:-1]
    low = v.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def strip_comment(line):
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def parse(text):
    out = {}
    for n, line in enumerate(text.split("\n"), 1):
        line = strip_comment(line).rstrip()
        if not line.strip():
            continue
        if line[0] in " \t" or ":" not in line:
            raise ValueError("settings line %d: only flat 'key: value' lines are supported" % n)
        key, _, raw = line.partition(":")
        out[key.strip()] = parse_value(raw)
    return out


def load(path, defaults):
    """defaults overlaid with the file's values; a missing file means defaults."""
    cfg = dict(defaults)
    try:
        with open(path) as f:
            cfg.update(parse(f.read()))
    except OSError:
        pass
    return cfg
