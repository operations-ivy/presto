"""Where a media upload goes: its kind and a clean name, from its file name (and
an audio file's tags). Pure: no files are touched here (sorter.py moves them).

    movie   movies/library/movies/Title (Year).ext
    show    movies/library/<series-slug>/Show - 01 - Title.ext   (Show - S01E02 - Title.ext with a season)
    music   music/library/<Artist>/<Album>/NN - Title.ext
    review  movies/review/<name>, music/review/<name>: anything not certain

The names follow the anime cleanup on brick2000 (2026-11-14). A guess never
goes into a library: a video with neither an episode number nor a year, or a
song without artist and title tags, goes to review for a person to name.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import NamedTuple

# The same list as brick-k8s-config's media/convert-480p.sh: what gets a 480p copy.
VIDEO_EXT = frozenset({".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".wmv", ".mpg", ".mpeg"})
AUDIO_EXT = frozenset(
    {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".wav", ".wma", ".aiff", ".aif", ".mka", ".alac"}
)
# What the photo converter takes (presto_photos/pipeline.py); the page sends these to /api/upload.
IMAGE_EXT = frozenset({".heic", ".heif", ".jpg", ".jpeg", ".png"})

# Tokens that end a title: resolution, codec, source, audio. Everything from the
# first of them on is the release's description, not its name. Words that can be
# in a real title ("web", "dvd", "proper") aren't here.
JUNK = re.compile(
    r"(?<![a-z0-9])(?:\d{3,4}[pi]|4k|uhd|hdr(?:10)?|x26[45]|h\.?26[45]|hevc|avc|xvid|divx|10-?bit|8-?bit|hi10p?"
    r"|blu-?ray|bdrip|brrip|bdremux|remux|webrip|web-?dl|hdtv|dvdrip|dvd-?r|aac(?:2\.0)?|e?ac3|ddp?5\.1|dts(?:-hd)?"
    r"|truehd|atmos|dual[ .-]?audio|multi-?sub)(?![a-z0-9])",
    re.I,
)
BRACKETS = re.compile(r"\[[^\]]*\]|\{[^}]*\}")
PARENS = re.compile(r"\(([^)]*)\)")
YEAR = re.compile(r"(?<!\d)(19\d\d|20\d\d)(?!\d)")
# (strong, pattern): a weak pattern followed by a "(year)" is a movie's title instead
# ("Star Wars Episode 4 A New Hope (1977)").
EPISODE_PATTERNS = (
    # Show S01E02, Show.S01E02E03, Show s1 e2
    (
        True,
        re.compile(
            r"^(?P<show>.+?)[\s\-]*\bS(?P<season>\d{1,2})\s?E(?P<ep>\d{1,4})(?:-?E\d{1,4})*\b(?P<rest>.*)$", re.I
        ),
    ),
    # Show 1x02
    (True, re.compile(r"^(?P<show>.+?)[\s\-]*\b(?P<season>\d{1,2})x(?P<ep>\d{2,3})\b(?P<rest>.*)$", re.I)),
    # Show - 01 - Title, Show - 001, Show - 01v2
    (False, re.compile(r"^(?P<show>.+?)\s+-\s+(?P<ep>\d{1,4})(?:v\d)?(?=\s|$)(?P<rest>.*)$", re.I)),
    # Show Ep 01, Show Ep. 01, Show Episode 01, Show E01, Show #01
    (False, re.compile(r"^(?P<show>.+?)\s+(?:Ep\.?|Episode|E|#)\s?(?P<ep>\d{1,4})\b(?P<rest>.*)$", re.I)),
)
YEAR_IN_PARENS = re.compile(r"\((?:19|20)\d\d\)")
DUB = re.compile(r"(?<![a-z])(?:dub|dubs|dubbed|eng(?:lish)?[ ._-]?dub)(?![a-z])", re.I)
SUB = re.compile(r"(?<![a-z])(?:sub|subs|subbed|subtitled|eng(?:lish)?[ ._-]?subs?|vostfr|raw)(?![a-z])", re.I)
UNSAFE = re.compile(r'[\x00-\x1f\x7f"*/<>?\\|]')  # exFAT and Samba refuse these (":" is handled first)
MAX_NAME = 150  # characters: exFAT allows 255 UTF-16 units, and a collision suffix needs room


@dataclass(frozen=True)
class Placement:
    kind: str  # movie | show | music | review
    share: str  # movies | music: the share (and drive folder) it's in
    folder: str  # relative to the share's root, e.g. "library/dragon-ball"
    name: str  # the file's clean name
    audio: str | None = None  # "dub" | "sub" | None (unknown): a video's audio, from its name
    reason: str = ""  # why it went to review

    @property
    def path(self) -> str:
        return f"{self.folder}/{self.name}"


class Episode(NamedTuple):
    show: str
    season: int | None
    number: str  # the digits as written ("001" stays three wide)
    title: str


class Title(NamedTuple):
    title: str
    year: int | None
    after: str  # what followed a bare year ("OVA 02" in "Birdy 1996 OVA 02")


def media_kind(filename: str) -> str | None:
    """ "video", "audio" or "image" by the file's extension (a browser's content
    type isn't trusted). None: not something we take."""
    ext = PurePosixPath(filename.lower()).suffix
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMAGE_EXT:
        return "image"
    return None


def safe_component(s: str, fallback: str = "untitled") -> str:
    """One path component a share and exFAT both take: no separators or reserved
    characters, no leading dot (hidden, and the 480p converter skips dotfiles), no
    trailing dot or space, at most MAX_NAME characters."""
    s = unicodedata.normalize("NFC", s)
    s = re.sub(r"\s*:\s*", " - ", s)
    s = UNSAFE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip().lstrip(".").strip().rstrip(". ")
    if len(s) > MAX_NAME:
        s = s[:MAX_NAME].rstrip(". ")
    return s or fallback


def slug(s: str) -> str:
    """A series folder's name: dragon-ball, ronin-warriors."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower().replace("'", "")).strip("-")
    return s[:80].rstrip("-") or "unknown"


def audio_hint(name: str) -> str | None:
    """ "dub" or "sub" when the name says so (exactly one of them), else None."""
    dub, sub = bool(DUB.search(name)), bool(SUB.search(name))
    return "dub" if dub and not sub else "sub" if sub and not dub else None


def _spaced(stem: str) -> str:
    """Separators to spaces: underscores always, dots when there are no spaces
    (Show.Name.S01E02), so "Mr. Robot" keeps its dot."""
    s = stem.replace("_", " ")
    if " " not in s.strip():
        s = re.sub(r"\.(?!\d(?:\D|$))", " ", s)  # keep "5.1"-style numbers whole for JUNK
    return re.sub(r"\s+", " ", s).strip()


def _cut_junk(s: str) -> str:
    m = JUNK.search(s)
    if m:
        s = s[: m.start()].rstrip(" -._,+[(")
    return s.strip(" -._,")


def _tidy(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" -._,")


def clean_title(stem: str) -> Title:
    """A video's stem, tidied: (title, year or None, what followed a bare year).
    Bracket tags go ("[Group]", "[1080p]", "{hash}"), a "(1988)" becomes the
    year, and the rest is cut at the first release token (1080p, x264, BluRay...)."""
    s = BRACKETS.sub(" ", stem)
    year = None
    for m in PARENS.finditer(s):
        y = YEAR.fullmatch(m.group(1).strip())
        if y and _plausible_year(int(y.group(1))):
            year = int(y.group(1))
    s = PARENS.sub(lambda m: " " if YEAR.fullmatch(m.group(1).strip()) or JUNK.search(m.group(1)) else m.group(0), s)
    s = _cut_junk(_spaced(s))
    after = ""
    if year is None:
        # The last plausible year with a title before it: "Akira 1988", not "1984" alone.
        for m in reversed(list(YEAR.finditer(s))):
            y = int(m.group(1))
            if _plausible_year(y) and _tidy(s[: m.start()]):
                year, s, after = y, s[: m.start()], _tidy(s[m.end() :])
                break
    return Title(_tidy(s), year, after)


def _plausible_year(y: int) -> bool:
    return 1900 <= y <= time.gmtime().tm_year + 1  # "Blade Runner 2049" is a title, not a year


def episode_of(stem: str) -> Episode | None:
    """The show, season, episode number and title if the name has an episode
    number, else None."""
    s = BRACKETS.sub(" ", stem)
    s = PARENS.sub(lambda m: " " if JUNK.search(m.group(1)) else m.group(0), s)
    s = _spaced(s)
    for strong, pat in EPISODE_PATTERNS:
        m = pat.match(s)
        if not m or (not strong and YEAR_IN_PARENS.search(m.group("rest"))):
            continue
        show = _tidy(_cut_junk(m.group("show")))
        ep = m.group("ep")
        if not show or (len(ep) == 4 and YEAR.fullmatch(ep)):
            continue  # "Movie - 1999" is a year, not episode 1999
        season = m.groupdict().get("season")
        rest = _cut_junk(m.group("rest"))
        rest = re.sub(r"\s*\((?:19|20)\d\d\)$", "", rest)
        if rest.count("(") != rest.count(")"):  # a group cut in half by _cut_junk
            rest = rest.replace("(", " ").replace(")", " ")
        return Episode(show, int(season) if season else None, ep, _tidy(rest))
    return None


def _review(share: str, name: str, reason: str, audio: str | None = None) -> Placement:
    """Not certain: into the share's review folder under its own (safe) name."""
    return Placement("review", share, "review", name, audio, reason)


def _episode_name(ep: Episode) -> str:
    """Show - 01 - Title, Show - S01E02 - Title, Show - 01 (Director's Cut)."""
    num = ep.number.zfill(2)
    name = f"{ep.show} - " + (f"S{ep.season:02d}E{num}" if ep.season is not None else num)
    if ep.title.startswith("("):
        return f"{name} {ep.title}"
    if ep.title:
        return f"{name} - {ep.title}"
    return name


def place(filename: str, tags: dict | None = None) -> Placement:
    """Where an upload goes. tags: an audio file's (artist, album, title,
    tracknumber, discnumber, albumartist), as read by sorter.read_tags."""
    p = PurePosixPath(filename.replace("\\", "/")).name
    stem, ext = PurePosixPath(p).stem, PurePosixPath(p).suffix.lower()
    kind = media_kind(p)
    if kind == "audio":
        return _place_music(stem, ext, tags or {})
    if kind != "video":
        return _review("movies", safe_component(p), "not a video or audio file")
    audio = audio_hint(stem)
    if ep := episode_of(stem):
        ep = ep._replace(show=safe_component(ep.show))
        return Placement("show", "movies", f"library/{slug(ep.show)}", safe_component(_episode_name(ep)) + ext, audio)
    t = clean_title(stem)
    if t.title and t.year and re.search(r"\d", t.after):  # "Birdy 1996 OVA 02": a part of something, not a film
        return _review(
            "movies", safe_component(stem) + ext, "a number after the year: an episode or part, not sure which", audio
        )
    if t.title and t.year:
        return Placement("movie", "movies", "library/movies", safe_component(f"{t.title} ({t.year})") + ext, audio)
    return _review(
        "movies", safe_component(stem) + ext, "no episode number or year: can't tell a movie from a show", audio
    )


def _tag(tags: dict, key: str) -> str:
    v = tags.get(key)
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    return str(v or "").strip()


def _number(v: str) -> int | None:
    m = re.match(r"\s*(\d{1,3})", v)  # "3", "03", "3/12"
    return int(m.group(1)) if m else None


def _place_music(stem: str, ext: str, tags: dict) -> Placement:
    artist = _tag(tags, "albumartist") or _tag(tags, "artist")
    title = _tag(tags, "title")
    if not (artist and title):
        return _review("music", safe_component(stem) + ext, "no artist and title tags")
    album = _tag(tags, "album") or "Singles"
    track, disc = _number(_tag(tags, "tracknumber")), _number(_tag(tags, "discnumber"))
    discs = _number(_tag(tags, "discnumber").partition("/")[2])
    name = safe_component(title)
    if track:
        lead = f"{disc}-{track:02d}" if disc and (disc > 1 or (discs or 0) > 1) else f"{track:02d}"
        name = f"{lead} - {name}"
    return Placement(
        "music", "music", f"library/{safe_component(artist)}/{safe_component(album)}", safe_component(name) + ext
    )


def unique_name(name: str, existing: Iterable[str]) -> str:
    """name, or "name (2).ext", "name (3).ext"... so nothing is overwritten.
    Compared without case (exFAT ignores it), and two videos may not share a
    stem at all: their 480p copies would both be <stem>.mp4 (brick-k8s-config#112)."""
    taken = {e.casefold() for e in existing}
    video_stems = {PurePosixPath(e).stem for e in taken if PurePosixPath(e).suffix in VIDEO_EXT}
    stem, ext = PurePosixPath(name).stem, PurePosixPath(name).suffix
    is_video = ext.lower() in VIDEO_EXT

    def free(s: str) -> bool:
        return (s + ext).casefold() not in taken and not (is_video and s.casefold() in video_stems)

    if free(stem):
        return name
    n = 2
    while not free(f"{stem} ({n})"):
        n += 1
    return f"{stem} ({n}){ext}"
