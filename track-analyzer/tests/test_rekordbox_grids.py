"""Grid fixes written into Rekordbox: the ANLZ grid and master.db cues moved by the approved shift, cue approvals
moved along, a grid with tempo changes left alone and listed, and the undo putting everything back."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import make_master_db

from dj_track_analyzer import cues, gridfix, library, rekordbox, web


def _dat(path: Path, t0: float, tempos: list[float], n: int) -> bytes:
    """A minimal ANLZ .DAT with only a PQTZ grid: beat 1 at t0 s, beat k at tempos[k * len // n]."""
    from pyrekordbox.anlz.structs import AnlzFileHeader, AnlzTag

    entries, t = [], t0
    for k in range(n):
        bpm = tempos[k * len(tempos) // n]
        entries.append({"beat": k % 4 + 1, "tempo": round(bpm * 100), "time": round(t * 1000)})
        t += 60 / bpm
    tag = AnlzTag.build({"type": "PQTZ", "len_header": 24, "len_tag": 24 + 8 * n,
                         "content": {"u1": None, "u2": 0x80000, "entry_count": n, "entries": entries}})
    data = AnlzFileHeader.build({"type": "PMAI", "len_header": 28, "len_file": 28 + len(tag), "u1": 1, "u2": 0x10000,
                                 "u3": 0x10000, "u4": 0}) + tag
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


@pytest.fixture
def rb(library, monkeypatch, tmp_path):
    from dj_track_analyzer import collection
    from dj_track_analyzer import library as lib

    collection.NATIVE_DB.parent.mkdir(parents=True, exist_ok=True)  # the approvals live there (no scan here)
    db = make_master_db(tmp_path / "rekordbox", library["files"], bpm=12000, cues=[
        {"ContentID": "1", "InMsec": 2500, "OutMsec": -1, "Kind": 1, "Comment": "Drop"},    # hot cue A
        {"ContentID": "1", "InMsec": 4500, "OutMsec": 6500, "Kind": 0, "Comment": "Loop"},  # memory loop
    ])
    monkeypatch.setattr(rekordbox, "running_state", lambda: False)
    monkeypatch.setattr(rekordbox, "BACKUPS", tmp_path / "rekordbox-backups")
    monkeypatch.setattr(rekordbox, "find_collection", lambda d=None: db)
    monkeypatch.setattr(lib, "app", lambda: "rekordbox")
    root = db.parent / "share/PIONEER/USBANLZ"
    dats = [root / "a/1/ANLZ0000.DAT", root / "b/2/ANLZ0000.DAT"]
    before = [_dat(dats[0], 0.3, [120], 60), _dat(dats[1], 0.3, [120, 124], 60)]  # the 2nd has a tempo change
    m = rekordbox.open_database(db)
    for i, d in enumerate(dats):
        c = m.get_content(ID=str(i + 1))
        c.AnalysisDataPath, c.Length = "/" + str(d.relative_to(db.parent / "share")), 30
    m.commit()
    rekordbox.close_database(m)
    g = rekordbox.grids()
    by_id = {Path(p).stem: p for p in g}
    paths = [rekordbox.norm_path(str(f)) for f in library["files"][:2]]
    assert all(p in g for p in paths), (paths, by_id)
    return {"db": db, "dats": dats, "before": before, "paths": paths, "grids": g}


def _cue_rows(db) -> list[tuple]:
    m = rekordbox.open_database(db)
    try:
        return sorted((q.InMsec, q.OutMsec) for q in m.get_cue(ContentID="1"))
    finally:
        rekordbox.close_database(m)


def test_grid_fix_into_rekordbox_and_undo(rb):
    p1, p2 = rb["paths"]
    g1, g2 = rb["grids"][p1], rb["grids"][p2]
    assert abs(g1["grid_ms"] - 300) < 1e-6
    gridfix.set_plan(p1, g1["bpm"], g1["grid_ms"], 20)
    gridfix.set_plan(p2, g2["bpm"], g2["grid_ms"], 20)
    with cues._plan_db() as con:  # an approved cue plan on the first track, which must move with its grid
        con.execute("INSERT INTO cue_plan VALUES (?,?,?,?)", (p1, cues._grid_key(g1["bpm"], g1["grid_ms"]),
                    json.dumps({"add": [{"ms": 1000.0, "type": "cue", "hotcue": 1, "name": None}], "move": []}), "now"))

    prev = gridfix.apply(dry_run=True)
    assert prev["tracks"] == 1 and prev["cues_moved"] == 2, prev
    assert prev["tempo_changes"] == [Path(p2).name] and any("tempo changes" in s for s in prev["skipped"])
    assert [d.read_bytes() for d in rb["dats"]] == rb["before"] and _cue_rows(rb["db"]) == [(2500, -1), (4500, 6500)]

    out = gridfix.apply()
    assert out["changed"] and out["tracks"] == 1 and "sync --undo" in out["undo"], out
    assert abs(rekordbox.grids()[p1]["grid_ms"] - 320) < 1e-6
    assert rb["dats"][1].read_bytes() == rb["before"][1]  # the tempo-change track is untouched
    assert _cue_rows(rb["db"]) == [(2520, -1), (4520, 6520)]
    assert set(gridfix.plans()) == {p2}  # written; the skipped one stays approved
    with cues._plan_db() as con:
        grid, plan = con.execute("SELECT grid, plan FROM cue_plan WHERE path=?", (p1,)).fetchone()
    assert grid == cues._grid_key(120, 320) and json.loads(plan)["add"][0]["ms"] == 1020

    u = gridfix.undo_write()
    assert u["approvals_restored"] == 1
    assert [d.read_bytes() for d in rb["dats"]] == rb["before"] and _cue_rows(rb["db"]) == [(2500, -1), (4500, 6500)]
    assert set(gridfix.plans()) == {p1, p2}
    with cues._plan_db() as con:
        assert con.execute("SELECT grid FROM cue_plan WHERE path=?", (p1,)).fetchone()[0] == cues._grid_key(120, 300)


def test_grid_fix_skips_a_grid_changed_since_approval(rb):
    p1 = rb["paths"][0]
    gridfix.set_plan(p1, 120, 310, 20)  # approved on a grid that isn't Rekordbox's now
    out = gridfix.apply(dry_run=True)
    assert out["tracks"] == 0 and any("changed since you approved" in s for s in out["skipped"])


def test_grid_fix_page_side_for_rekordbox(rb, monkeypatch):
    """The page's preview: the chosen app's running state, and the tempo-change list for the dialog."""
    p1, p2 = rb["paths"]
    for p in (p1, p2):
        gridfix.set_plan(p, rb["grids"][p]["bpm"], rb["grids"][p]["grid_ms"], -10)
    monkeypatch.setattr(library, "running_state", lambda: True)
    out = web.write_grids(dry_run=True)
    assert out["traktor_running"] is True and out["tracks"] == 1 and out["tempo_changes"] == [Path(p2).name]
    js = (Path(web.__file__).parent / "web/js/tracks.js").read_text(encoding="utf-8")
    assert "Writing grid fixes isn't available for Rekordbox" not in js  # the button is on for Rekordbox
