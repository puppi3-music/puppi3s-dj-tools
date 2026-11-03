"""Cues and beat grids with Rekordbox chosen: cues.grids() reads djmdCue rows (and the ANLZ grid, stood in for
here), cues.apply writes approved plans as djmdCue rows with a backup and undo, grid fixing refuses."""
from __future__ import annotations

import pytest
from conftest import make_master_db

from dj_track_analyzer import cues, gridfix, library, rekordbox


@pytest.fixture
def rb(library_files, monkeypatch, tmp_path):
    if rekordbox.running_state():
        pytest.skip("Rekordbox is running on this computer (pyrekordbox won't commit)")
    monkeypatch.setattr(rekordbox, "running_state", lambda: False)
    monkeypatch.setattr(rekordbox, "BACKUPS", tmp_path / "rekordbox-backups")
    monkeypatch.setattr(rekordbox, "first_downbeat", lambda master, c: 0.5)  # no ANLZ files in the test library
    files = library_files
    db = make_master_db(tmp_path / "rekordbox", files, bpm=12000, cues=[
        {"ContentID": "1", "InMsec": 2500, "OutMsec": -1, "Kind": 1, "Comment": "Drop"},       # hot cue A
        {"ContentID": "1", "InMsec": 4500, "OutMsec": 6500, "Kind": 0, "Comment": "Loop"},     # memory loop
    ])
    rekordbox.choose_database(str(db))
    library.choose_app("rekordbox")
    yield {"db": db, "path": rekordbox.norm_path(str(files[0]))}
    library.choose_app("traktor")


@pytest.fixture
def library_files(library):
    return library["files"]


def test_grids_in_traktor_shape(rb):
    d = cues.grids()[rb["path"]]
    assert d["bpm"] == 120 and d["grid_ms"] == 500
    assert [(c["ms"], c["type"], c["hotcue"], c["bars"]) for c in d["cues"]] == [(2500, "cue", 0, None), (4500, "loop", -1, 1.0)]


def test_mp3_times_move_one_frame():
    assert rekordbox.shift_ms("a.MP3", 44100) == pytest.approx(26.122, abs=1e-3)
    assert rekordbox.shift_ms("a.flac", 44100) == 0


def test_apply_adds_moves_and_undoes(rb):
    p, d = rb["path"], cues.grids()[rb["path"]]
    cues.set_plan(p, d["bpm"], d["grid_ms"], {
        "add": [{"ms": 8500, "name": "Break", "type": "cue"}],
        "move": [{"from_ms": 2500, "to_ms": 2500 + 2000, "hotcue": 0}]})
    before = rb["db"].read_bytes()
    dry = cues.apply(dry_run=True)
    assert dry["cues_added"] == 1 and dry["cues_moved"] == 1 and rb["db"].read_bytes() == before
    out = cues.apply()
    assert out["changed"] and out["backup"]
    got = sorted((q["in_ms"], q["kind"], q["name"]) for q in rekordbox.read()["cues"])
    assert got == [(4500, 0, "Loop"), (4500, 1, "Drop"), (8500, 2, "Break")]
    assert not cues.plans()
    rekordbox.undo()
    assert rb["db"].read_bytes() == before


def test_grid_fixing_refuses(rb):
    with pytest.raises(SystemExit, match="Rekordbox"):
        gridfix.apply(dry_run=True)


def test_setup_tab_traktor_side_with_rekordbox_chosen(rb, library):
    """The Setup tab counts Traktor's grids and labels from collection.nml even with Rekordbox chosen: a path
    given is Traktor's, never opened as master.db ("file is not a database")."""
    from dj_track_analyzer import jobs

    st = jobs.setup_status()
    assert "error" not in st.get("traktor", {}), st.get("traktor")
    assert isinstance(cues.grids(str(library["nml"])), dict)
