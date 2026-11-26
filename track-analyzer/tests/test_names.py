"""`names`: the DJ app's artist/title into files without those tags, Traktor stamped, and undo (see conftest.py)."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import mutagen
from dj_track_analyzer import collection, names, traktor


def entry_of(nml, path) -> ET.Element:
    root = ET.parse(nml).getroot()
    return next(e for e in root.find("COLLECTION").iter("ENTRY")
                if str(path) in traktor.location_paths(e.find("LOCATION").attrib))


def cli(*args):  # in-process with Traktor taken as closed, so it runs while a real Traktor is open too
    undo, dry = "--undo" in args, "--dry-run" in args
    return (names.undo if undo else names.write)(dry)


def test_names_written_stamped_and_undone(library, fake_analysis, monkeypatch):
    monkeypatch.setattr(traktor, "running_state", lambda: False)
    nml, (a, b, *rest) = library["nml"], library["files"]
    f = mutagen.File(b)
    f["artist"] = "Own Artist"  # a name the file has is never replaced
    f.save()
    # The made-up collection says ARTIST="Test" and the file name as TITLE (Traktor's filler for no tag)
    nml.write_text(nml.read_text(encoding="utf-8").replace(f'TITLE="{a.stem}"', 'TITLE="Real Name"', 1),
                   encoding="utf-8", newline="\n")
    c = rest[0]  # Traktor's split of "Ayū - Song" dropped the ū: "Ay" isn't a name, the title is
    c = c.rename(c.with_name("Ayū - Song" + c.suffix))
    library["files"][library["files"].index(rest[0])] = c
    nml.write_text(nml.read_text(encoding="utf-8").replace(f'TITLE="{rest[0].stem}" ARTIST="Test"', 'TITLE="Song" ARTIST="Ay"')
                   .replace(f'FILE="{rest[0].name}"', f'FILE="{c.name}"'), encoding="utf-8", newline="\n")
    collection.scan([str(library["music"])], workers=1, csv_out=None, traktor_nml=str(nml))
    before = nml.read_bytes()

    # the Tracks tab shows Traktor's names where the file has none (not a title that's only the file name)
    from dj_track_analyzer import web

    rows = {r["path"]: r for r in web.tracks()["tracks"]}
    assert (rows[str(a)]["artist"], rows[str(a)]["title"], rows[str(a)]["names_from_dj"]) == ("Test", "Real Name", ["artist", "title"])
    assert rows[str(b)]["artist"] == "Own Artist" and "title" not in rows[str(b)] and "names_from_dj" not in rows[str(b)]

    assert rows[str(c)]["title"] == "Song" and rows[str(c)]["names_from_dj"] == ["title"] and "artist" not in rows[str(c)]

    out = cli("names", "--dry-run")
    assert out["would_write"] == len(library["files"]) - 1  # b keeps its own artist and has no title but its name
    assert any("Real Name" in x for x in out["examples"])
    assert nml.read_bytes() == before and not mutagen.File(a).get("artist")

    out = cli("names")
    assert out["written"] == len(library["files"]) - 1 and out["stamped_in_traktor"] == out["written"]
    tags = mutagen.File(a)
    assert tags["artist"] == ["Test"] and tags["title"] == ["Real Name"]
    assert mutagen.File(b)["artist"] == ["Own Artist"]
    assert "title" not in mutagen.File(b)  # its Traktor title is only the file name
    assert mutagen.File(c)["title"] == ["Song"] and "artist" not in mutagen.File(c)
    e = entry_of(nml, a)
    assert f'{e.get("MODIFIED_DATE")} {e.get("MODIFIED_TIME")}' == traktor.file_stamp(str(a))
    assert entry_of(nml, b).get("MODIFIED_TIME") == "40000"  # untouched
    row = collection.db().execute("SELECT artist, title FROM tracks WHERE path=?", (str(a),)).fetchone()
    assert tuple(row) == ("Test", "Real Name")
    assert out.get("backup") and cli("names", "--dry-run")["would_write"] == 0

    out = cli("names", "--undo")
    assert out["restored"] == len(library["files"]) - 1
    assert "artist" not in mutagen.File(a) and "title" not in mutagen.File(a)
    assert mutagen.File(b)["artist"] == ["Own Artist"]
    e = entry_of(nml, a)
    assert f'{e.get("MODIFIED_DATE")} {e.get("MODIFIED_TIME")}' == traktor.file_stamp(str(a))
    assert cli("names", "--undo", "--dry-run")["would_restore"] == 0
