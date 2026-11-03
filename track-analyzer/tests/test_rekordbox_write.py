"""Writing into Rekordbox's master.db (genres, colours, hype, genre smart playlists), the backup and the undo, on
a made-up SQLCipher database built with pyrekordbox (conftest.make_master_db)."""
from __future__ import annotations

import json

import pytest
from conftest import TRACKS, cli, make_master_db

from dj_track_analyzer import collection, rekordbox, traktor


@pytest.fixture
def rb(library, fake_analysis, monkeypatch, tmp_path):
    """A scanned, tagged library and a Rekordbox database holding its tracks (the first one coloured Aqua)."""
    if rekordbox.running_state():
        pytest.skip("Rekordbox is running on this computer (pyrekordbox won't commit)")
    monkeypatch.setattr(rekordbox, "running_state", lambda: False)
    monkeypatch.setattr(rekordbox, "BACKUPS", tmp_path / "rekordbox-backups")
    collection.scan([str(library["music"])], workers=1, csv_out=None, traktor_nml=str(library["nml"]))
    cli("tag", "--multi")
    db = make_master_db(tmp_path / "rekordbox", library["files"], colours={0: 6})
    return {"db": db, "files": library["files"]}


def rows(db):
    return {t["path"]: t for t in rekordbox.read(str(db))["tracks"]}


def test_colour_mapping():
    assert [rekordbox.COLORS[rekordbox.OUR_TO_RB[i]] for i in range(1, 8)] == [
        "Red", "Orange", "Yellow", "Green", "Blue", "Purple", "Pink"]
    assert rekordbox.RB_TO_OUR[6] == 5  # Aqua reads as Blue; never written
    assert 6 not in rekordbox.OUR_TO_RB.values()


def test_dry_run_changes_nothing(rb):
    before = rb["db"].read_bytes()
    out = rekordbox.sync(str(rb["db"]), dry_run=True)
    assert out["changed"] and out["dry_run"]
    assert out["genres_updated"] == len(TRACKS)
    assert out["colours_kept"] == 1 and out["colours_set"] == len(TRACKS) - 1  # Aqua is the user's own
    assert rb["db"].read_bytes() == before
    assert not rekordbox.BACKUPS.exists() or not any(rekordbox.BACKUPS.iterdir())


def test_write_then_undo_byte_for_byte(rb):
    db = rb["db"]
    before = db.read_bytes()
    out = rekordbox.sync(str(db))
    assert out["changed"] and out["backup"]
    after = rows(db)
    for f, (_folder, _name, genre, _colour) in zip(rb["files"], TRACKS):
        assert after[str(f)]["genre"] == traktor.file_genre(str(f)) or after[str(f)]["genre"] == genre
    assert after[str(rb["files"][0])]["color"] == 6  # the user's Aqua kept
    names = {p["name"]: p for p in rekordbox.read(str(db))["playlists"]}
    assert rekordbox.GENRE_FOLDER in names
    assert set(out["smartlists"]) <= set(names)
    # the same again: nothing to do, no new backup
    again = rekordbox.sync(str(db))
    assert again["changed"] is False and "backup" not in again
    assert len(rekordbox._backups(db)) == 1

    out = rekordbox.write_hype({str(rb["files"][0]): 7, str(rb["files"][1]): 3}, str(db))
    assert out["comment_updated"] == 2 and out["kept_your_text"] == 1
    assert rows(db)[str(rb["files"][0])]["comment"] == "my note · Hype 07"
    p0 = str(rb["files"][1])
    want = rekordbox.RB_TO_OUR[rows(db)[p0]["color"]]
    out = rekordbox.set_colors({p0: (want, 6)}, str(db))
    assert out["colours_changed"] == 1 and rows(db)[p0]["color"] == rekordbox.OUR_TO_RB[6]

    for _ in range(3):  # each write undone in turn, back to the very first file
        rekordbox.undo(str(db))
    assert db.read_bytes() == before
    with pytest.raises(SystemExit, match="No Puppi3"):
        rekordbox.undo(str(db))


def test_undo_refuses_after_rekordbox_changed_the_database(rb):
    db = rb["db"]
    rekordbox.write_hype({str(rb["files"][0]): 5}, str(db))
    with db.open("ab") as f:  # as if Rekordbox saved something since
        f.write(b"\0")
    with pytest.raises(SystemExit, match="has changed since"):
        rekordbox.undo(str(db))
    rekordbox.undo(str(db), force=True)
    assert rows(db)[str(rb["files"][0])]["comment"] == "my note"


def test_refuses_while_rekordbox_runs(rb, monkeypatch):
    db = rb["db"]
    before = db.read_bytes()
    monkeypatch.setattr(rekordbox, "running_state", lambda: True)
    for write in (lambda: rekordbox.sync(str(db)), lambda: rekordbox.write_hype({}, str(db), remove=True),
                  lambda: rekordbox.undo(str(db))):
        with pytest.raises(SystemExit, match="Rekordbox is running"):
            write()
    assert db.read_bytes() == before
    assert rekordbox.sync(str(db), dry_run=True)["dry_run"]  # previews still work
    monkeypatch.setattr(rekordbox, "running_state", lambda: None)  # can't tell: the page asks
    monkeypatch.delenv(traktor.CLOSED_ENV, raising=False)
    with pytest.raises(traktor.ConfirmClosed, match="Quit Rekordbox"):
        rekordbox.sync(str(db))


def test_backups_keep_ten(rb, monkeypatch):
    monkeypatch.setattr(rekordbox, "KEEP_BACKUPS", 2)
    for h in (1, 2, 3):
        rekordbox.write_hype({str(rb["files"][0]): h}, str(db := rb["db"]))
    assert len(rekordbox._backups(db)) == 2


def test_jobs_say_the_app(monkeypatch):
    from dj_track_analyzer import jobs, library

    monkeypatch.setattr(library, "app", lambda: "rekordbox")
    assert jobs.app_label("hype_traktor", jobs.ACTIONS["hype_traktor"][0]) == "Hype into Rekordbox's Comment"
    assert jobs.app_label("tempo_native", jobs.ACTIONS["tempo_native"][0]) == "Switch Rekordbox back to normal"


def test_hand_edit_goes_to_rekordbox_when_chosen(rb, monkeypatch):
    """A colour/genre edit in a track's panel writes master.db (one backup), not Traktor's collection.nml; the key
    stays in the analyzer."""
    from pathlib import Path

    from dj_track_analyzer import edit, library

    db, p = rb["db"], str(rb["files"][1])
    monkeypatch.setattr(library, "app", lambda: "rekordbox")
    real = rekordbox.find_collection
    monkeypatch.setattr(rekordbox, "find_collection", lambda d=None: real(d or str(db)))
    nml = Path(traktor.find_collection(None)).read_bytes()
    out = edit.edit(p, {"colour": "Blue", "genre": "Techno", "key": "8A"})
    assert out["traktor"]["changed"] and out["traktor"]["backup"]
    assert out["traktor"]["changed_fields"] == ["ColorID", "Genre"] and "key" in out["traktor"]
    assert rows(db)[p]["color"] == rekordbox.OUR_TO_RB[5] and rows(db)[p]["genre"] == "Techno"
    assert Path(traktor.find_collection(None)).read_bytes() == nml  # Traktor untouched
    again = edit.edit(p, {"colour": "Blue"})  # already so: no write, no backup
    assert again["traktor"]["changed"] is False and len(rekordbox._backups(db)) == 1
