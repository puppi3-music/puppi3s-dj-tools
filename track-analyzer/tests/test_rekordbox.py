"""Reading Rekordbox's library (rekordbox.py) from plain rows, as `load` makes them from master.db, and the DJ-app
switch (library.py). No pyrekordbox or encrypted database needed."""
from __future__ import annotations

from pathlib import Path

import pytest

from dj_track_analyzer import analysis, library, rekordbox, traktor


@pytest.mark.parametrize("name,cam", [
    ("Am", "8A"), ("C", "8B"), ("C#", "3B"), ("Dbm", "12A"), ("F#m", "11A"), ("Bb", "6B"),
    ("1m", "8A"), ("1M", "8A"), ("1d", "8B"), ("1D", "8B"), ("6m", "1A"), ("12D", "7B"),
    ("8A", "8A"), ("12b", "12B"), ("", None), (None, None), ("?", None),
])
def test_key_camelot(name, cam):
    assert rekordbox.key_camelot(name) == cam


def test_all_open_keys_match_classic():
    # Open Key nm/nd and the classic names Rekordbox writes itself must give the same Camelot
    for n, (minor, major) in enumerate(zip(["Am", "Em", "Bm", "F#m", "C#m", "G#m", "D#m", "A#m", "Fm", "Cm", "Gm", "Dm"],
                                           ["C", "G", "D", "A", "E", "B", "F#", "C#", "G#", "D#", "A#", "F"]), 1):
        assert rekordbox.key_camelot(f"{n}M") == rekordbox.key_camelot(minor)
        assert rekordbox.key_camelot(f"{n}D") == rekordbox.key_camelot(major)


@pytest.fixture
def rows(tmp_path):
    a, b, c = (tmp_path / f"{n}.flac" for n in "abc")
    for f in (a, b):
        f.write_bytes(b"")
    t = lambda i, p, **kw: {"id": i, "path": str(p), "bpm": None, "key": None, "genre": None, "color": 0,
                            "comment": None, "rating": 0, **kw}
    return {
        "tracks": [t("1", a, bpm=12800, key="1M", genre="Techno"), t("2", b, bpm=12600, key="C#", genre="A; B"),
                   t("3", c, bpm=14000)],
        "playlists": [
            {"id": "10", "name": "DJ", "attribute": rekordbox.FOLDER, "parent": "root", "seq": 1},
            {"id": "11", "name": "Peak", "attribute": rekordbox.PLAYLIST, "parent": "10", "seq": 1},
            {"id": "12", "name": "Smart", "attribute": rekordbox.SMART, "parent": "root", "seq": 2},
            {"id": "13", "name": "Gone", "attribute": rekordbox.PLAYLIST, "parent": "root", "seq": 3},
            {"id": "14", "name": "Genres (Track Analyzer)", "attribute": rekordbox.FOLDER, "parent": "root", "seq": 4},
            {"id": "15", "name": "Techno", "attribute": rekordbox.PLAYLIST, "parent": "14", "seq": 1},
        ],
        "songs": [{"playlist": "11", "track": "2", "no": 1}, {"playlist": "11", "track": "1", "no": 2},
                  {"playlist": "13", "track": "3", "no": 1}, {"playlist": "15", "track": "1", "no": 1}],
        "cues": [{"track": "1", "in_ms": 5000, "out_ms": -1, "kind": 5, "name": "Drop"},
                 {"track": "1", "in_ms": 1000, "out_ms": 9000, "kind": 0, "name": None},
                 {"track": "9", "in_ms": 1, "out_ms": -1, "kind": 1, "name": ""}],
        "files": (a, b, c),
    }


def test_tracks_and_info(rows):
    a, b, c = rows["files"]
    _, files, missing = rekordbox.collection_tracks("x/master.db", rows=rows)
    assert files == sorted([a, b]) and missing == 1
    info = rekordbox.track_info(rows=rows)
    assert info[str(a)] == (128.0, "8A") and info[str(b)] == (126.0, "3B") and info[str(c)] == (140.0, None)


def test_playlists_skip_smart_empty_and_own(rows):
    a, b, _ = rows["files"]
    assert rekordbox.playlists(rows=rows) == [{"path": "DJ / Peak", "name": "Peak", "files": [str(b), str(a)]}]


def test_genre_field(rows):
    a, _, _ = rows["files"]
    assert rekordbox.genre_field(rows=rows) == {"genres": {"Techno": [str(a)]}, "multi": 1}


def test_cues(rows):
    a, _, _ = rows["files"]
    assert rekordbox.cues(rows=rows) == {str(a): [
        {"ms": 1000, "loop_ms": 8000, "hot": None, "name": ""},
        {"ms": 5000, "loop_ms": None, "hot": 3, "name": "Drop"}]}


def test_check_database(tmp_path):
    with pytest.raises(ValueError):
        rekordbox.check_database(str(tmp_path))
    (tmp_path / "master.db").write_bytes(b"")
    assert rekordbox.check_database(f'"{tmp_path}"') == (tmp_path / "master.db").resolve()
    (tmp_path / "other.db").write_bytes(b"")
    with pytest.raises(ValueError):
        rekordbox.check_database(str(tmp_path / "other.db"))


def test_app_switch():
    try:
        assert library.app() == "traktor" and library.backend() is traktor
        library.choose_app("rekordbox")
        assert library.app_name() == "Rekordbox" and library.backend() is rekordbox
        with pytest.raises(ValueError):
            library.choose_app("serato")
    finally:
        analysis.save_app_setting(library.APP_SETTING, None)


def test_pyrekordbox_installed():
    from pyrekordbox import MasterDatabase  # noqa: F401  (and its SQLCipher, on every CI platform)
    import sqlcipher3  # noqa: F401


def test_page_switches_app_and_database(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from dj_track_analyzer import jobs, web

    client = TestClient(web.build_app([analysis.HOME / "logs"]), base_url="http://127.0.0.1:8766")
    try:
        assert client.post("/api/dj-app", json={"app": "serato"}).status_code != 200
        assert client.post("/api/dj-app", json={"app": "rekordbox"}).json() == {"app": "rekordbox"}
        (tmp_path / "master.db").write_bytes(b"")
        r = client.post("/api/rekordbox/database", json={"path": str(tmp_path)})
        assert r.json() == {"database": str((tmp_path / "master.db").resolve())}
        monkeypatch.setattr(rekordbox, "collection_tracks", lambda db=None, rows=None: (Path(db), [Path("a.flac")], 2))
        st = jobs.dj_app_status()
        assert st["app"] == "rekordbox" and st["rekordbox"]["tracks"] == 1 and st["rekordbox"]["missing"] == 2
        assert library._path("x/collection.nml") is None  # --nml is Traktor's: Rekordbox uses its own database
        assert client.post("/api/rekordbox/database", json={"auto": True}).status_code == 200
        assert rekordbox.chosen_database() is None
    finally:
        analysis.save_app_setting(library.APP_SETTING, None)
        analysis.save_app_setting(rekordbox.DB_SETTING, None)
    assert library._path("x/collection.nml") == "x/collection.nml" and jobs.dj_app_status()["app"] == "traktor"
