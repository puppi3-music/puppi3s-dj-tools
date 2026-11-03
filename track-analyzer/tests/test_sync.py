"""Sync (all writers in one go) into a made-up collection.nml and master.db: preview writes nothing, a sync writes,
one undo puts the library back byte for byte."""
from __future__ import annotations

import pytest
from conftest import cli, make_master_db

from dj_track_analyzer import collection, library, rekordbox, sync, traktor


@pytest.fixture
def scanned(library, fake_analysis, monkeypatch):
    from dj_track_analyzer import hype

    # hype needs 20+ tracks; a fixed score so the sync makes two writes (two backups for one undo)
    monkeypatch.setattr(hype, "compute", lambda *a, **k: {str(collection.nfc(f)): 6 for f in library["files"]})
    collection.scan([str(library["music"])], workers=1, csv_out=None, traktor_nml=str(library["nml"]))
    cli("tag", "--multi")
    return library


def _check(path, nml=None):
    before = path.read_bytes()
    prev = sync.run(nml, dry_run=True)
    assert prev["dry_run"] and prev["steps"]["genres"]["changed"]
    assert "skipped" in prev["steps"]["cues"]  # nothing approved
    assert path.read_bytes() == before and "undo" not in prev

    out = sync.run(nml)
    assert out["changed"] and out["undo"]
    assert path.read_bytes() != before
    written = [s for s, r in out["steps"].items() if r.get("backup")]
    assert written == ["genres", "hype"]

    u = sync.undo(nml)
    assert u["undone"] == written[::-1]
    assert path.read_bytes() == before
    with pytest.raises(SystemExit, match="No sync"):
        sync.undo(nml)
    return out


def test_sync_traktor(scanned):
    if traktor.running_state():
        pytest.skip("Traktor is running on this computer")
    out = _check(scanned["nml"], str(scanned["nml"]))
    assert "skipped" in out["steps"]["grids"]  # nothing approved


def test_sync_rekordbox(scanned, monkeypatch, tmp_path):
    if rekordbox.running_state():
        pytest.skip("Rekordbox is running on this computer")
    monkeypatch.setattr(rekordbox, "running_state", lambda: False)
    monkeypatch.setattr(rekordbox, "BACKUPS", tmp_path / "rekordbox-backups")
    db = make_master_db(tmp_path / "rekordbox", scanned["files"])
    monkeypatch.setattr(rekordbox, "find_collection", lambda d=None: db)
    monkeypatch.setattr(library, "app", lambda: "rekordbox")
    out = _check(db)
    assert "Traktor only" in out["steps"]["grids"]["skipped"]


def test_undo_refuses_after_the_app_changed_it(scanned):
    nml = scanned["nml"]
    sync.run(str(nml))
    nml.write_text(nml.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="changed since"):
        sync.undo(str(nml))
