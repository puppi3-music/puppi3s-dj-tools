"""The Tracks tab's note on a tempo view that's behind: tracks of the normal scan the view has no row for yet."""
from dj_track_analyzer import tempo, web


def test_view_missing_counts_what_a_view_scan_would_add(tmp_path, monkeypatch):
    files = {n: tmp_path / f"{n}.mp3" for n in ("in_view", "new", "no_bpm", "traktor_bpm_only")}
    for f in files.values():
        f.write_bytes(b"x")
    rows = {str(files["in_view"]): {"traktor_bpm": None, "bpm": 126.0},
            str(files["new"]): {"traktor_bpm": None, "bpm": 128.0},
            str(files["no_bpm"]): {"traktor_bpm": None, "bpm": None},  # the view's scan skips it: no tempo to stretch from
            str(files["traktor_bpm_only"]): {"traktor_bpm": 140.0, "bpm": None},
            str(tmp_path / "gone.mp3"): {"traktor_bpm": None, "bpm": 125.0}}  # not on disk: skipped too
    monkeypatch.setattr(tempo, "native_rows", lambda: rows)
    assert web._view_missing({str(files["in_view"])}) == 2
    assert web._view_missing(set(rows)) == 0
