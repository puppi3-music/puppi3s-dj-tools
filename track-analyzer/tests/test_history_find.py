"""find_history: Traktor names a history file when it closes, so a set's file can carry a later day."""
from datetime import datetime

from dj_track_analyzer import tracklist, traktor


def _history(folder, name, date, seconds):
    d = date.year << 16 | date.month << 8 | date.day
    entries = "".join(
        f'<ENTRY><PRIMARYKEY KEY="t{i}"/><EXTENDEDDATA STARTDATE="{d}" STARTTIME="{t}" DURATION="300" DECK="{i % 2}"/></ENTRY>'
        for i, t in enumerate(seconds))
    (folder / name).write_text(f'<NML><COLLECTION></COLLECTION><PLAYLISTS><NODE>{entries}</NODE></PLAYLISTS></NML>')


def _folder(tmp_path, monkeypatch):
    hist = tmp_path / "Documents" / "Native Instruments" / "Traktor 4.5.0" / "History"
    hist.mkdir(parents=True)
    monkeypatch.setattr(traktor, "documents_dirs", lambda: [tmp_path / "Documents"])
    return hist


def test_file_closed_after_midnight(tmp_path, monkeypatch):
    hist = _folder(tmp_path, monkeypatch)
    set_day = datetime(2026, 11, 7)
    _history(hist, "history_2026y11m07d_01h24m26s.nml", datetime(2026, 11, 6), [82380, 84000])  # the night before
    _history(hist, "history_2026y11m08d_00h11m26s.nml", set_day, [71100, 71400, 72000, 73000])  # 19:45 on
    found = tracklist.find_history(datetime(2026, 11, 7, 19, 44, 59), 3884)
    assert found and found.name == "history_2026y11m08d_00h11m26s.nml"


def test_file_closed_days_later(tmp_path, monkeypatch):
    hist = _folder(tmp_path, monkeypatch)
    _history(hist, "history_2026y11m10d_09h00m00s.nml", datetime(2026, 11, 7), [71100, 72000])
    assert tracklist.find_history(datetime(2026, 11, 7, 19, 44, 59), 3600).name.startswith("history_2026y11m10d")


def test_no_overlap_no_file(tmp_path, monkeypatch):
    hist = _folder(tmp_path, monkeypatch)
    _history(hist, "history_2026y11m08d_00h11m26s.nml", datetime(2026, 11, 7), [3600])  # 1:00, long before the set
    assert tracklist.find_history(datetime(2026, 11, 7, 19, 44, 59), 3600) is None
