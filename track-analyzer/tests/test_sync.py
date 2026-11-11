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
    assert "Nothing approved" in out["steps"]["grids"]["skipped"]


def test_undo_refuses_after_the_app_changed_it(scanned):
    nml = scanned["nml"]
    sync.run(str(nml))
    nml.write_text(nml.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="changed since"):
        sync.undo(str(nml))


def _move_and_add(lib):
    """One track's file moved to a new folder, one new analysed file the DJ app doesn't list."""
    import shutil

    moved = lib["files"][0]
    new_dir = lib["music"] / "Moved"
    new_dir.mkdir()
    dest = new_dir / moved.name
    shutil.move(moved, dest)
    extra = lib["music"] / "Extra" / "brand new.flac"
    extra.parent.mkdir()
    shutil.copy(lib["files"][1], extra)
    collection.scan([str(lib["music"])], workers=1, csv_out=None, traktor_nml=str(lib["nml"]))
    return dest, extra


def test_tracks_traktor(scanned):
    nml = str(scanned["nml"])
    kb = scanned["files"][0].stat().st_size // 1024  # the made-up NML's FILESIZE is a stand-in; Traktor's is real
    scanned["nml"].write_text(scanned["nml"].read_text(encoding="utf-8").replace('FILESIZE="200"', f'FILESIZE="{kb}"'),
                              encoding="utf-8")
    dest, extra = _move_and_add(scanned)
    before = scanned["nml"].read_bytes()
    prev = sync.run(nml, dry_run=True, steps=["tracks"])["steps"]["tracks"]
    assert prev["relinked"] == 1 and prev["added"] == 1 and scanned["nml"].read_bytes() == before
    sync.run(nml, steps=["tracks"])
    _, files, missing = traktor.collection_tracks(nml)
    assert missing == 0 and {str(collection.nfc(dest)), str(collection.nfc(extra))} <= {str(collection.nfc(f)) for f in files}
    text = scanned["nml"].read_text(encoding="utf-8")
    import re
    n = lambda t: int(re.search(r'<COLLECTION ENTRIES="(\d+)"', t).group(1))
    assert n(text) == n(before.decode("utf-8")) + 1
    assert "skipped" in sync.run(nml, dry_run=True, steps=["tracks"])["steps"]["tracks"]  # nothing left to do
    sync.undo(nml)
    assert scanned["nml"].read_bytes() == before


def test_tracks_rekordbox(scanned, monkeypatch, tmp_path):
    db = make_master_db(tmp_path / "rekordbox", scanned["files"])
    dest, extra = _move_and_add(scanned)  # while Traktor is the app: its Recordings folder stays out of the scan
    monkeypatch.setattr(rekordbox, "running_state", lambda: False)
    monkeypatch.setattr(rekordbox, "BACKUPS", tmp_path / "rekordbox-backups")
    monkeypatch.setattr(rekordbox, "find_collection", lambda d=None: db)
    monkeypatch.setattr(library, "app", lambda: "rekordbox")
    out = sync.run(None, steps=["tracks"])["steps"]["tracks"]
    assert out["relinked"] == 1 and out["added"] == 1, out["examples"]
    _, files, missing = rekordbox.collection_tracks()
    assert missing == 0 and len(files) == len(scanned["files"]) + 1
    sync.undo()
    assert rekordbox.collection_tracks()[2] == 1


def _saved_samples(folder):
    """Two loops saved as samples (tiny WAVs) and one whose file is gone, as the page's saved_sample table lists them."""
    import wave

    from dj_track_analyzer import web

    folder.mkdir(parents=True, exist_ok=True)
    files = [folder / "A - Loop 1.wav", folder / "B - Loop 2.wav"]
    for f in files:
        with wave.open(str(f), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(44100), w.writeframes(b"\0\0" * 441)
    con = web._saved_db()
    con.execute("DELETE FROM saved_sample")  # rows left by another test would be added too
    con.executemany("INSERT OR REPLACE INTO saved_sample (file, src_name, start, len) VALUES (?,?,?,?)",
                    [(str(f), "x.flac", 1.0, 2.0) for f in files + [folder / "Gone.wav"]])
    con.commit()
    con.close()
    return files


def _sample_rows():
    from dj_track_analyzer import web

    con = web._saved_db()
    try:
        return {r[0] for r in con.execute("SELECT file FROM saved_sample")}
    finally:
        con.close()


def test_samples_traktor(scanned, tmp_path):
    files = _saved_samples(tmp_path / "Samples")
    nml = scanned["nml"]
    before = nml.read_bytes()
    prev = sync.run(None, dry_run=True, steps=["samples"])["steps"]["samples"]
    assert prev["added"] == 2 and prev["forgotten"] == 1 and nml.read_bytes() == before
    assert len(_sample_rows()) == 3  # a preview forgets nothing
    out = sync.run(None, steps=["samples"])["steps"]["samples"]
    assert out["added"] == 2 and out["relinked"] == 0 and out["backup"]
    assert _sample_rows() == {str(f) for f in files}
    assert "A - Loop 1.wav" in nml.read_text(encoding="utf-8")
    again = sync.run(None, dry_run=True, steps=["samples"])["steps"]["samples"]
    assert "already in" in again["skipped"]
    sync.undo()
    assert nml.read_bytes() == before


def test_samples_rekordbox(scanned, monkeypatch, tmp_path):
    db = make_master_db(tmp_path / "rekordbox", scanned["files"])
    monkeypatch.setattr(rekordbox, "running_state", lambda: False)
    monkeypatch.setattr(rekordbox, "BACKUPS", tmp_path / "rekordbox-backups")
    monkeypatch.setattr(rekordbox, "find_collection", lambda d=None: db)
    monkeypatch.setattr(rekordbox, "SAMPLER", tmp_path / "Sampler")
    monkeypatch.setattr(library, "app", lambda: "rekordbox")
    _saved_samples(tmp_path / "Samples")
    prev = sync.run(None, dry_run=True, steps=["samples"])["steps"]["samples"]
    assert prev["added"] == 2 and not (tmp_path / "Sampler").exists()
    out = sync.run(None, steps=["samples"])["steps"]["samples"]
    copies = sorted((tmp_path / "Sampler" / sync.RB_SAMPLES).iterdir())
    assert out["added"] == 2 and [c.name for c in copies] == ["A - Loop 1.wav", "B - Loop 2.wav"]
    _, tracks, _ = rekordbox.collection_tracks()
    assert len(tracks) == len(scanned["files"])  # the sampler's WAVs aren't tracks to scan
    sync.undo()


def test_scan_reads_every_dj_app(monkeypatch):
    """A track added in Traktor while Rekordbox is chosen (or the other way) still gets scanned; a missing app is
    left out unless it's the chosen one."""
    from pathlib import Path

    monkeypatch.setattr(library, "app", lambda: "rekordbox")
    monkeypatch.setattr(traktor, "collection_tracks", lambda nml=None: (Path("c.nml"), [Path("/m/a.flac"), Path("/m/b.flac")], 1))
    monkeypatch.setattr(rekordbox, "collection_tracks", lambda db=None: (Path("master.db"), [Path("/m/b.flac"), Path("/m/c.flac")], 0))
    apps, files, missing = library.all_collection_tracks()
    assert files == [Path("/m/a.flac"), Path("/m/b.flac"), Path("/m/c.flac")] and missing == 1 and len(apps) == 2

    def gone(nml=None):
        raise SystemExit("no Traktor")
    monkeypatch.setattr(traktor, "collection_tracks", gone)
    assert library.all_collection_tracks()[1] == [Path("/m/b.flac"), Path("/m/c.flac")]
    monkeypatch.setattr(library, "app", lambda: "traktor")
    with pytest.raises(SystemExit):
        library.all_collection_tracks()


def test_copy_cues_into_ungridded_rekordbox_track(scanned, monkeypatch, tmp_path):
    """Traktor's hot cues go into a Rekordbox track that has no beat grid yet (no ANLZ files, e.g. just added by a
    sync): cues are times, so they're written at once, at the same time (no MP3 shift for these FLACs)."""
    nml = scanned["nml"]
    t = nml.read_text(encoding="utf-8")
    k = t.find("</ENTRY>")
    t = t[:k] + ('<CUE_V2 NAME="AutoGrid" TYPE="4" START="100.0" LEN="0" REPEATS="-1" HOTCUE="-1"></CUE_V2>'
                 '<CUE_V2 NAME="Drop" TYPE="0" START="2500.0" LEN="0" REPEATS="-1" HOTCUE="2"></CUE_V2>') + t[k:]
    nml.write_text(t, encoding="utf-8")
    monkeypatch.setattr(traktor, "find_collection", lambda n=None: nml)
    db = make_master_db(tmp_path / "rekordbox", scanned["files"])
    monkeypatch.setattr(rekordbox, "running_state", lambda: False)
    monkeypatch.setattr(rekordbox, "BACKUPS", tmp_path / "rekordbox-backups")
    monkeypatch.setattr(rekordbox, "find_collection", lambda d=None: db)
    monkeypatch.setattr(library, "app", lambda: "rekordbox")
    assert not rekordbox.grids()  # nothing analysed in Rekordbox yet
    out = sync.run(None, steps=["cues"], copy=True)["steps"]["cues"]
    assert out.get("cues_added") == 1, out
    got = [c for v in rekordbox.cues().values() for c in v]
    assert [(c["ms"], c["hot"], c["name"]) for c in got] == [(2500, 2, "Drop")]



def test_copy_cues_preview_counts_tracks_the_sync_adds(scanned, monkeypatch, tmp_path):
    """A preview writes nothing, so a track the tracks step would add isn't in Rekordbox yet: the cue step's preview
    still lists the Traktor cues it will copy into it."""
    from pathlib import Path

    from dj_track_analyzer import cues

    nml = scanned["nml"]
    t = nml.read_text(encoding="utf-8")
    k = t.find("</ENTRY>")
    t = t[:k] + ('<CUE_V2 NAME="AutoGrid" TYPE="4" START="100.0" LEN="0" REPEATS="-1" HOTCUE="-1"></CUE_V2>'
                 '<CUE_V2 NAME="Drop" TYPE="0" START="2500.0" LEN="0" REPEATS="-1" HOTCUE="2"></CUE_V2>'
                 '<CUE_V2 NAME="n.n." TYPE="0" START="4000.0" LEN="0" REPEATS="-1" HOTCUE="3"></CUE_V2>') + t[k:]
    nml.write_text(t, encoding="utf-8")
    monkeypatch.setattr(traktor, "find_collection", lambda n=None: nml)
    cued = next(p for p, d in cues.grids(str(nml)).items() if d["cues"])
    files = [f for f in scanned["files"] if Path(f).name != Path(cued).name]
    assert len(files) == len(scanned["files"]) - 1
    db = make_master_db(tmp_path / "rekordbox", files)
    monkeypatch.setattr(rekordbox, "running_state", lambda: False)
    monkeypatch.setattr(rekordbox, "BACKUPS", tmp_path / "rekordbox-backups")
    monkeypatch.setattr(rekordbox, "find_collection", lambda d=None: db)
    monkeypatch.setattr(library, "app", lambda: "rekordbox")
    out = sync.run(None, dry_run=True, steps=["tracks", "cues"], copy=True)["steps"]
    assert out["tracks"]["added"] >= 1 and "add_paths" not in out["tracks"]
    assert "+  at" in out["cues"]["changes"][1]  # Traktor's "n.n." (unnamed) isn't copied as a name
    assert out["cues"].get("cues_added") == 2 and out["cues"]["changed"], out["cues"]
    assert "added by this sync" in out["cues"]["changes"][0]
    assert not cues.PENDING_TRACKS  # reset after the run

def _anlz_dat(path, t0: float, bpm: float, n: int) -> bytes:
    """A minimal ANLZ .DAT: just a PQTZ beat grid (beat 1 at t0 s)."""
    from pyrekordbox.anlz.structs import AnlzFileHeader, AnlzTag

    beat = 60 / bpm
    tag = AnlzTag.build({"type": "PQTZ", "len_header": 24, "len_tag": 24 + 8 * n, "content": {
        "u1": None, "u2": 0x80000, "entry_count": n,
        "entries": [{"beat": k % 4 + 1, "tempo": round(bpm * 100), "time": round((t0 + k * beat) * 1000)} for k in range(n)]}})
    data = AnlzFileHeader.build({"type": "PMAI", "len_header": 28, "len_file": 28 + len(tag), "u1": 1, "u2": 0x10000,
                                 "u3": 0x10000, "u4": 0}) + tag
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def test_copy_traktor_grid_into_rekordbox(scanned, monkeypatch, tmp_path):
    """Traktor's grid replaces Rekordbox's in an analysed track (PQTZ in its .DAT, BPM in master.db); a track with
    no ANLZ files is only listed; the sync's undo puts the .DAT back byte for byte."""
    nml = scanned["nml"]
    t = nml.read_text(encoding="utf-8")
    k = t.find("</ENTRY>")
    t = t[:k] + '<CUE_V2 NAME="AutoGrid" TYPE="4" START="250.0" LEN="0" REPEATS="-1" HOTCUE="-1"></CUE_V2>' + t[k:]
    k = t.find("</ENTRY>", k + 100)
    t = t[:k] + '<CUE_V2 NAME="AutoGrid" TYPE="4" START="80.0" LEN="0" REPEATS="-1" HOTCUE="-1"></CUE_V2>' + t[k:]
    nml.write_text(t, encoding="utf-8")
    monkeypatch.setattr(traktor, "find_collection", lambda n=None: nml)
    db = make_master_db(tmp_path / "rekordbox", scanned["files"], bpm=12500)
    monkeypatch.setattr(rekordbox, "running_state", lambda: False)
    monkeypatch.setattr(rekordbox, "BACKUPS", tmp_path / "rekordbox-backups")
    monkeypatch.setattr(rekordbox, "find_collection", lambda d=None: db)
    monkeypatch.setattr(library, "app", lambda: "rekordbox")
    dat = db.parent / "share/PIONEER/USBANLZ/abc/123/ANLZ0000.DAT"
    before = _anlz_dat(dat, 0.3, 125, 60)
    m = rekordbox.open_database(db)
    c = m.get_content(ID="1")
    c.AnalysisDataPath, c.Length = "/PIONEER/USBANLZ/abc/123/ANLZ0000.DAT", 30
    m.commit()
    rekordbox.close_database(m)
    p = next(iter(rekordbox.grids()))
    assert rekordbox.grids()[p]["bpm"] == 125

    prev = sync.run(None, dry_run=True, steps=["grids"], copy=True)["steps"]["grids"]
    assert prev["tracks"] == 1 and dat.read_bytes() == before, prev
    out = sync.run(None, steps=["grids"], copy=True)["steps"]["grids"]
    assert out["tracks"] == 1 and len(out["not_analysed"]) == 1, out
    g = rekordbox.grids()[p]
    assert g["bpm"] == 124 and abs(g["grid_ms"] - 250) < 1
    from pyrekordbox.anlz import AnlzFile

    q = AnlzFile.parse_file(dat).get_tag("PQTZ")
    assert q.get_times()[0] < 60 / 124 and q.get_times()[-1] <= 30
    assert "skipped" in sync.run(None, dry_run=True, steps=["grids"], copy=True)["steps"]["grids"]  # already the same
    sync.undo(copy=True)
    assert dat.read_bytes() == before and rekordbox.grids()[p]["bpm"] == 125


def _rekordbox(scanned, monkeypatch, tmp_path, files=None, colours=None):
    db = make_master_db(tmp_path / "rekordbox", files or scanned["files"], colours=colours)
    monkeypatch.setattr(traktor, "find_collection", lambda n=None: scanned["nml"])
    monkeypatch.setattr(traktor, "running_state", lambda: False)
    monkeypatch.setattr(rekordbox, "running_state", lambda: False)
    monkeypatch.setattr(rekordbox, "BACKUPS", tmp_path / "rekordbox-backups")
    monkeypatch.setattr(rekordbox, "find_collection", lambda d=None: db)
    return db


def _traktor_values(nml, genre: str, colour: int):
    """Genre and colour on Traktor's first entry."""
    t = nml.read_text(encoding="utf-8")
    k = t.find('FILESIZE="200"')
    nml.write_text(t[:k] + f'GENRE="{genre}" COLOR="{colour}" ' + t[k:], encoding="utf-8")


def test_copy_traktor_into_rekordbox(scanned, monkeypatch, tmp_path):
    """Tracks Rekordbox lacks, Traktor's genre and colour, its playlist in a "From Traktor" folder; one undo for all
    of it, separate from the sync's."""
    _traktor_values(scanned["nml"], "Techno", 3)
    db = _rekordbox(scanned, monkeypatch, tmp_path, files=scanned["files"][:1])
    monkeypatch.setattr(library, "app", lambda: "rekordbox")
    before = db.read_bytes()
    prev = sync.run(None, dry_run=True, copy=True)
    assert prev["from"] == "Traktor" and db.read_bytes() == before
    assert prev["steps"]["tracks"]["added"] == len(scanned["files"]) - 1, prev["steps"]["tracks"]  # not the recording
    assert prev["steps"]["tracks"]["recordings_left_out"] == 1
    assert prev["steps"]["playlists"]["changes"] == ["From Traktor (Track Analyzer) / Chill: 2 tracks"]
    out = sync.run(None, copy=True)["steps"]
    assert out["genres"]["genres_changed"] == 1 and out["colours"]["colours_changed"] == 1, out
    assert out["playlists"]["playlists"] == {"Chill": 2}, out["playlists"]
    rows = rekordbox.read()
    first = next(t for t in rows["tracks"] if t["path"] == str(scanned["files"][0]))
    assert first["genre"] == "Techno" and rekordbox.RB_TO_OUR[first["color"]] == 3
    assert [p["path"] for p in rekordbox.playlists()] == ["From Traktor (Track Analyzer) / Chill"]
    again = sync.run(None, dry_run=True, copy=True)["steps"]
    assert all("skipped" in again[s] for s in ("tracks", "genres", "colours")), again
    with pytest.raises(SystemExit, match="No sync"):
        sync.undo()  # the copy has its own undo
    sync.undo(copy=True)
    assert len(rekordbox.read()["tracks"]) == 1
    with pytest.raises(SystemExit, match="No copy"):
        sync.undo(copy=True)


def test_copy_rekordbox_into_traktor(scanned, monkeypatch, tmp_path):
    """The other way: Rekordbox's colour into Traktor (an empty Rekordbox genre erases nothing); undo is byte for
    byte."""
    nml = scanned["nml"]
    _traktor_values(nml, "House", 2)
    _rekordbox(scanned, monkeypatch, tmp_path, colours={0: rekordbox.OUR_TO_RB[5]})
    monkeypatch.setattr(library, "app", lambda: "traktor")
    before = nml.read_bytes()
    out = sync.run(str(nml), steps=["genres", "colours"], copy=True)
    assert "skipped" in out["steps"]["genres"] and out["from"] == "Rekordbox"
    assert out["steps"]["colours"]["colours_changed"] == 1
    assert 'GENRE="House" COLOR="5"' in nml.read_text(encoding="utf-8")
    sync.undo(str(nml), copy=True)
    assert nml.read_bytes() == before


def test_copy_samples_rekordbox_into_traktor(scanned, monkeypatch, tmp_path):
    """A WAV in Rekordbox's sampler folder goes into Traktor (as it is) by the samples step, never the tracks step."""
    sampler = tmp_path / "Sampler"
    wav = sampler / "OSC_SAMPLER" / "Kick.wav"
    wav.parent.mkdir(parents=True)
    wav.write_bytes(b"RIFF0000WAVE")
    monkeypatch.setattr(rekordbox, "SAMPLER", sampler)
    _rekordbox(scanned, monkeypatch, tmp_path, files=[*scanned["files"], wav])
    monkeypatch.setattr(library, "app", lambda: "traktor")
    prev = sync.run(str(scanned["nml"]), dry_run=True, steps=["tracks", "samples"], copy=True)["steps"]
    assert "skipped" in prev["tracks"], prev["tracks"]
    assert prev["samples"]["added"] == 1, prev["samples"]
