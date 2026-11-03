"""Rekordbox stage 4: set/crate playlists, History for tracklists and the learned arc, the tempo view's swap."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from conftest import make_master_db

from dj_track_analyzer import collection, library, rekordbox, setplan, tempo


@pytest.fixture
def rb(library_, monkeypatch, tmp_path):
    if rekordbox.running_state():
        pytest.skip("Rekordbox is running on this computer (pyrekordbox won't commit)")
    monkeypatch.setattr(rekordbox, "running_state", lambda: False)
    monkeypatch.setattr(rekordbox, "BACKUPS", tmp_path / "rekordbox-backups")
    db = make_master_db(tmp_path / "rekordbox", library_["files"], colours={0: 2})
    monkeypatch.setattr(rekordbox, "find_collection", lambda d=None: db)
    monkeypatch.setattr(library, "app", lambda: "rekordbox")
    return {"db": db, "files": [str(collection.nfc(f)) for f in library_["files"]]}


@pytest.fixture
def library_(library):
    return library


def _playlists(db):
    m = rekordbox.open_database(db)
    try:
        out = {}
        for p in m.get_playlist():
            parent = m.get_playlist(ID=p.ParentID) if str(p.ParentID) != "root" else None
            songs = sorted(m.get_playlist_songs(PlaylistID=p.ID), key=lambda s: s.TrackNo)
            out[p.Name] = (parent.Name if parent else None, [rekordbox.norm_path(s.Content.FolderPath) for s in songs])
        return out
    finally:
        rekordbox.close_database(m)


def test_set_playlist_write_replace_remove(rb):
    files = rb["files"]
    d = {"tracks": [{"path": files[2], "title": "c"}, {"path": files[0], "title": "a"},
                    {"path": "/nowhere.flac", "title": "x"}]}
    out = setplan.to_traktor(d, "Friday", dry_run=True)
    assert out["playlists"] == {"Friday": 2} and "Friday" not in _playlists(rb["db"])
    out = setplan.to_traktor(d, "Friday")
    assert out["not_in_rekordbox"] == 1 and out["backup"]
    # the summary line's keys (as Traktor's result has them), and the undo mark is master.db as it is now on disk
    assert (out["playlist"], out["tracks"], out["not_in_traktor"]) == (f"{setplan.FOLDER} / Friday", 2, ["x"])
    assert (Path(out["backup"]) / rekordbox.WRITTEN).read_text() == rekordbox._sha1(rb["db"])
    pl = _playlists(rb["db"])
    assert pl["Friday"] == (setplan.FOLDER, [files[2], files[0]])
    d["tracks"] = d["tracks"][1:2]
    assert setplan.to_traktor(d, "Friday")["replaced"] == ["Friday"]
    assert _playlists(rb["db"])["Friday"][1] == [files[0]]
    setplan.remove("Friday")
    pl = _playlists(rb["db"])
    assert "Friday" not in pl and setplan.FOLDER not in pl  # the empty folder goes too
    with pytest.raises(SystemExit, match="No playlist"):
        setplan.remove("Friday")


def test_crates(rb):
    d = {"crates": [{"crate": "Warm-up", "tracks": [{"path": rb["files"][1]}]}], "library": "normal"}
    setplan.crates_to_traktor(d)
    assert _playlists(rb["db"])["Warm-up"] == (setplan.CRATE_FOLDER, [rb["files"][1]])


def _add_history(db, ids, start):
    import sqlalchemy as sa
    import sqlcipher3
    from pyrekordbox.masterdb import models
    from pyrekordbox.masterdb.database import BLOB
    from pyrekordbox.utils import deobfuscate
    from sqlalchemy.orm import Session

    engine = sa.create_engine(f"sqlite+pysqlcipher://:{deobfuscate(BLOB)}@/{db}?", module=sqlcipher3)
    with Session(engine) as s:
        s.add(models.DjmdHistory(ID="h1", Name="2026-11-01", Attribute=0, ParentID="root", DateCreated="2026-11-01",
                                 created_at=start, updated_at=start, rb_local_usn=0))
        for n, cid in enumerate(ids):
            t = start + timedelta(minutes=5 * n)
            s.add(models.DjmdSongHistory(ID=f"s{n}", HistoryID="h1", ContentID=str(cid), TrackNo=n + 1,
                                         created_at=t, updated_at=t, rb_local_usn=0))
        s.commit()
    engine.dispose()


def test_history_plays(rb):
    start = datetime(2026, 11, 1, 22, 0)
    _add_history(rb["db"], [2, 1], start)
    (h,) = rekordbox.histories()
    a, b = h["plays"]
    assert a["path"] == rb["files"][1] and b["path"] == rb["files"][0]
    assert a["start"] == start and a["seconds"] == 300 and b["seconds"] > 0


def test_tempo_view_swap_and_back(rb, monkeypatch, tmp_path):
    files = rb["files"]
    monkeypatch.setattr(tempo, "STATE", tmp_path / "tempo-loaded.json")
    view = {files[0]: {"genre": "Techno", "colour": lambda yours: 2, "hype": 7},
            files[1]: {"genre": None, "colour": lambda yours: None, "hype": None}}
    stats, remembered = rekordbox.tempo_load(view)
    assert stats["genres_set"] == 1 and stats["colours_moved"] == 1 and stats["hype_set"] == 1
    r = {t["path"]: t for t in rekordbox.read()["tracks"]}[files[0]]
    assert r["genre"] == "Techno" and "Hype 07" in (r["comment"] or "")
    assert remembered[files[0]]["ColorID"] == ["2", str(rekordbox.OUR_TO_RB[2])]
    back = rekordbox.tempo_unload(remembered)
    assert back["restored"] == 3 and back["kept_your_changes"] == 0
    r = {t["path"]: t for t in rekordbox.read()["tracks"]}[files[0]]
    assert not r["genre"] and r["comment"] == "my note"
