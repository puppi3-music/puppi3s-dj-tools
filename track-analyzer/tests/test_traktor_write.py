"""Scan → tag → write into Traktor → undo, on a made-up library, as you'd run them (see conftest.py)."""
from __future__ import annotations

import json
import os
import re
import string
import xml.etree.ElementTree as ET

import pytest
from conftest import RECORDING, TRACKS, WINDOWS, cli

from dj_track_analyzer import collection, traktor


def scan(library) -> dict:
    return collection.scan([str(library["music"])], workers=1, csv_out=None, traktor_nml=str(library["nml"]))


def entries(nml) -> dict[str, dict]:
    """{track's path: its INFO attributes} from a collection.nml."""
    root = ET.parse(nml).getroot()
    return {traktor.location_paths(e.find("LOCATION").attrib)[0]: e.find("INFO").attrib
            for e in root.find("COLLECTION").iter("ENTRY")}


def test_scan_finds_the_tracks_traktor_has(library, fake_analysis):
    scan(library)
    scanned = set(collection.known_all(collection.db()))
    assert scanned == {str(f) for f in library["files"]}  # the recording is left out (Traktor Settings.tsi)
    assert str(library["recording"]) not in scanned
    # Traktor's paths and the scan's are the same strings, so every track matches
    assert scanned <= set(entries(library["nml"]))


def test_write_into_traktor_and_undo(library, fake_analysis, fake_traktor):
    if traktor.running_state():
        pytest.skip("Traktor is running on this computer")
    scan(library)
    cli("tag", "--multi")  # as the page's "Write genres into your files' tags"
    nml = library["nml"]
    before = nml.read_bytes()

    out = json.loads(cli("traktor", "--dry-run").stdout)
    assert out["matched_scanned_tracks"] == len(TRACKS)
    assert out["genres_updated"] == len(TRACKS) and out["colours_set"] == len(TRACKS)
    assert nml.read_bytes() == before

    out = json.loads(cli("traktor").stdout)
    assert out["changed"] is True
    after = entries(nml)
    for f, (_folder, _name, genre, colour) in zip(library["files"], TRACKS):
        assert after[str(f)]["GENRE"] == genre
        assert after[str(f)]["COLOR"] == str(traktor_colour(colour))
    assert "GENRE" not in after[str(library["recording"])]
    text = nml.read_text(encoding="utf-8")
    assert "東京 Night - ナイト.flac" in text and "Été" in text  # names kept, in UTF-8
    assert "\r\n" not in text  # Traktor writes LF; so do we
    root = ET.parse(nml).getroot()
    lists = [n.get("NAME") for n in root.iter("NODE") if n.get("TYPE") == "SMARTLIST"]
    assert {"Deep House", "Progressive House", "Tech House", "Trance"} <= set(lists)
    assert any(nml.parent.glob(traktor.BACKUP_PREFIX + "*"))

    # Traktor open: every write and the undo refuse, and nothing changes
    written = nml.read_bytes()
    fake_traktor.start()
    for args in (("traktor",), ("traktor", "--undo")):
        r = cli(*args, check=False)
        assert r.returncode and "Traktor is running" in r.stdout + r.stderr, args
    assert nml.read_bytes() == written

    # closed again: the undo puts back the collection byte for byte
    fake_traktor.stop()
    cli("traktor", "--undo")
    assert nml.read_bytes() == before

    # Traktor saved the collection after our write: the undo refuses, --force (the page's "Undo anyway") restores
    cli("traktor")
    nml.write_bytes(nml.read_bytes().replace(b'MODIFIED_TIME="40000"', b'MODIFIED_TIME="40001"', 1))
    r = cli("traktor", "--undo", check=False)
    assert r.returncode and "--force" in r.stdout + r.stderr
    cli("traktor", "--undo", "--force")
    assert nml.read_bytes() == before


def traktor_colour(name: str) -> int:
    from dj_track_analyzer import analysis

    return analysis.COLOR_NAMES.index(name) + 1


@pytest.mark.skipif(not WINDOWS, reason="drive letters")
def test_unplugged_drive_keeps_its_tracks(library, fake_analysis):
    free = next(d for d in "QRSTUVWXYZ" if not os.path.exists(f"{d}:\\"))
    unplugged = f"{free}:\\DJ\\Track on a USB stick.flac"
    con = collection.db()
    deleted = str(library["music"].parent / "elsewhere" / "deleted.flac")  # outside the scanned folder
    for p in (unplugged, deleted):
        collection.save_row(con, {"path": p, "type": "track"})
    scan(library)
    left = set(collection.known_all(collection.db()))
    assert unplugged in left  # its drive isn't there: unplugged, kept
    assert deleted not in left  # its drive is there, the file isn't: gone


def test_genres_only_in_traktor(library, fake_analysis):
    """`traktor --genres-from-analysis` (the page's "only in Traktor"): the files' tags stay as they were."""
    if traktor.running_state():
        pytest.skip("Traktor is running on this computer")
    scan(library)
    collection.set_manual(str(library["files"][2]), "genre", "My Own Genre")  # set by hand: goes in as typed
    nml = library["nml"]
    before, files = nml.read_bytes(), {f: f.read_bytes() for f in library["files"]}

    out = json.loads(cli("traktor", "--genres-from-analysis").stdout)
    assert out["genres_updated"] == len(TRACKS)
    after = entries(nml)
    assert after[str(library["files"][0])]["GENRE"] == TRACKS[0][2]  # both genres, as tag --multi picks them
    assert after[str(library["files"][1])]["GENRE"] == TRACKS[1][2]
    assert after[str(library["files"][2])]["GENRE"] == "My Own Genre"
    assert {f: f.read_bytes() for f in library["files"]} == files  # no file touched
    cli("traktor", "--undo")
    assert nml.read_bytes() == before


def test_collection_found_in_moved_documents(tmp_path, monkeypatch):
    """Windows' Documents moved (OneDrive backup, another drive): Traktor's folder is there, not in ~/Documents."""
    moved = tmp_path / "OneDrive" / "Documents"
    for v in ("4.1.0", "4.10.2"):
        d = moved / "Native Instruments" / f"Traktor {v}"
        d.mkdir(parents=True)
        (d / "collection.nml").write_text("<NML/>", encoding="utf-8")
    monkeypatch.setattr(traktor.sys, "platform", "win32")
    monkeypatch.setattr(traktor, "_windows_documents", lambda: str(moved))
    monkeypatch.setattr(traktor.os.path, "expanduser", lambda p: p.replace("~", str(tmp_path / "nobody"), 1))
    assert traktor.find_collection() == moved / "Native Instruments" / "Traktor 4.10.2" / "collection.nml"
    assert moved in traktor.documents_dirs()


@pytest.mark.skipif(not WINDOWS, reason="asks Windows itself")
def test_windows_knows_its_documents_folder():
    assert traktor._known_documents()  # the raising version: CI shows Windows' error if it fails


def test_first_scan_scores_hype(library, fake_analysis, monkeypatch):
    """A fresh library's first scan scores hype (it used to wait for `hype`, leaving the column empty)."""
    from dj_track_analyzer import hype

    calls = []

    def compute(con, progress=None):
        calls.append(1)
        con.execute("UPDATE tracks SET hype = 5")
        con.commit()
        return {}

    monkeypatch.setattr(hype, "compute", compute)
    scan(library)
    assert len(calls) == 1
    scan(library)  # nothing new and hype already there: left as it is
    assert len(calls) == 1
    collection.db().execute("UPDATE tracks SET hype = NULL").connection.commit()
    scan(library)  # nothing new, but no hype yet: scored
    assert len(calls) == 2


def test_info_attr_with_raw_gt_in_comment():
    """A '>' inside an attribute value is legal XML; it must not cut the <INFO> tag short (duplicate GENRE)."""
    import xml.etree.ElementTree as ET
    from dj_track_analyzer.traktor import _set_info_attr
    entry = '<ENTRY><INFO COMMENT="&lt;p>text&lt;/p>" GENRE="Old"></INFO></ENTRY>'
    new, old = _set_info_attr(entry, "GENRE", "House")
    assert old == "Old"
    assert ET.fromstring(new).find("INFO").get("GENRE") == "House"
