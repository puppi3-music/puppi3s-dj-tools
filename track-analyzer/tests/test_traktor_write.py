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
    cli("tag")
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
    assert {"Deep House", "Tech House", "Trance"} <= set(lists)
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
