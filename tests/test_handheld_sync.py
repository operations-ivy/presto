"""tools/handheld_sync.py's planning, without a device."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import handheld_sync as hs  # noqa: E402


def test_share_paths_land_under_sd_movies():
    assert hs.rel_of("480p/tv/Kite/Kite.mp4") == "tv/Kite/Kite.mp4"
    assert hs.rel_of("library/movies/Akira (1988).mkv") == "movies/Akira (1988).mkv"


def test_pictures_and_subtitles_go_with_their_video(tmp_path):
    show = tmp_path / "480p" / "tv" / "Show"
    show.mkdir(parents=True)
    for name in ("ep1.mp4", "ep1.srt", "ep1.jpg", "poster.jpg", "ep2.mp4", "notes.txt"):
        (show / name).write_bytes(b"x" * 3)
    files = [{"path": f"480p/tv/Show/ep{i}.mp4", "bytes": 3} for i in (1, 2)]
    want = hs.wanted(files, tmp_path)
    assert sorted(want) == [
        "tv/Show/ep1.jpg",
        "tv/Show/ep1.mp4",
        "tv/Show/ep1.srt",
        "tv/Show/ep2.mp4",
        "tv/Show/poster.jpg",
    ]
    assert want["tv/Show/ep1.mp4"] == hs.Entry(show / "ep1.mp4", 3)


def test_one_playlist_per_show_or_season():
    tv = [
        "tv/Bastard/Bastard - 01.mp4",
        "tv/Bastard/Bastard - 02.mp4",
        "tv/Gall Force/Gall Force 1/Gall Force 1 - 01.mp4",
        "tv/Gall Force/Gall Force 1/Gall Force 1 - 02.mp4",
        "tv/Gall Force/Rhea Gall Force.mp4",
        "tv/Kite/Kite.mp4",
        "tv/Kite (Director's Cut)/Kite DC.mp4",
    ]
    lists = hs.playlists(tv, "/storage/AB-CD")
    assert sorted(lists) == ["Bastard.m3u", "Gall Force 1.m3u", "Kite.m3u", "Rhea Gall Force.m3u"]
    assert lists["Bastard.m3u"] == (
        "#EXTM3U\n#EXTINF:-1,Bastard - 01\n/storage/AB-CD/Movies/tv/Bastard/Bastard - 01.mp4\n"
        "#EXTINF:-1,Bastard - 02\n/storage/AB-CD/Movies/tv/Bastard/Bastard - 02.mp4\n"
    )
    assert "Kite DC" in lists["Kite.m3u"]


def test_the_plan_copies_whats_missing_or_changed_and_deletes_the_rest(tmp_path):
    want = {"movies/A.mp4": hs.Entry(tmp_path / "A", 10), "movies/B.mp4": hs.Entry(tmp_path / "B", 20)}
    have = {"movies/A.mp4": 10, "movies/B.mp4": 5, "movies/Old.mp4": 7}
    assert hs.plan(want, have) == hs.Plan(push=["movies/B.mp4"], drop=["movies/Old.mp4"], need=15, freed=7)
