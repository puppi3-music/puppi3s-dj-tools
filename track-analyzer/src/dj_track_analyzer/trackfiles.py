"""Tracks themselves, for the sync: find the DJ app's tracks whose file moved, and the analysed tracks the DJ app
doesn't list yet. Each backend (traktor.sync_tracks, rekordbox.sync_tracks) writes the plan in its own way.

A moved file is found by its file name: a missing track is pointed at the one analysed file with that name the DJ
app doesn't list (and the same size, when the app stores one). Two or more such files → left alone, listed as
ambiguous. Moves are planned first, so a moved file isn't also added as a new track."""
from __future__ import annotations

import os
import unicodedata
from collections import defaultdict

from . import collection

SIZE_SLACK = 1024  # bytes: Traktor stores the size in kB


def nfc(p: str) -> str:
    return unicodedata.normalize("NFC", p)


def known() -> list[dict]:
    """Analysed tracks (no error) whose file is on disk: [{path, size, artist, title}]."""
    con = collection.db()
    try:
        rows = con.execute("SELECT path, size, artist, title FROM tracks WHERE error IS NULL").fetchall()
    finally:
        con.close()
    return [dict(r) | {"path": nfc(r["path"])} for r in rows if os.path.isfile(r["path"])]


def plan(present: set[str], missing: list[tuple[object, str, int | None]], tracks: list[dict] | None = None) -> dict:
    """present: NFC paths the DJ app lists that exist. missing: (the app's key, its file name, size in bytes or None)
    for tracks whose file is gone. Returns {"moves": {key: new path}, "adds": [track rows], "ambiguous": [names]}."""
    tracks = known() if tracks is None else tracks
    free = [t for t in tracks if t["path"] not in present]
    by_name = defaultdict(list)
    for t in free:
        by_name[nfc(os.path.basename(t["path"])).casefold()].append(t)
    moves, used, ambiguous = {}, set(), []
    for key, name, size in missing:
        cands = [t for t in by_name.get(nfc(name).casefold(), []) if t["path"] not in used
                 and (size is None or t["size"] is None or abs(t["size"] - size) <= SIZE_SLACK)]
        if len(cands) == 1:
            moves[key] = cands[0]["path"]
            used.add(cands[0]["path"])
        elif len(cands) > 1:
            ambiguous.append(name)
    adds = sorted((t for t in free if t["path"] not in used), key=lambda t: t["path"])
    return {"moves": moves, "adds": adds, "ambiguous": sorted(set(ambiguous))}


def stats(p: dict, old_name) -> dict:
    """The step's report: counts and some examples ("old → new", "+ new")."""
    ex = [f"{old_name(k)} → {v}" for k, v in list(p["moves"].items())[:10]] + [f"+ {t['path']}" for t in p["adds"][:10]]
    out = {"relinked": len(p["moves"]), "added": len(p["adds"]), "examples": ex,
           "add_paths": [t["path"] for t in p["adds"]]}  # sync's preview: later steps see them (sync.run pops it)
    if p["ambiguous"]:
        out["moved_but_ambiguous"] = p["ambiguous"][:20]
    return out
