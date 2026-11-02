"""`edit` command and the page's Edit: a track's colour, key, genre, hype or BPM set by hand.

Colour, key and genre go into Traktor (traktor.set_track: backup, `traktor --undo`) and the analyzer's database;
Traktor stays where colour and key are read from, so a later change in Traktor still wins. A hand colour is kept
always (colour hold) and counts as yours for training; a hand genre is held. Genre, hype and BPM are also kept in
collection.db `manual` and put back after every scan, regenre and hype score. BPM never goes into Traktor (its
BPM goes with its beat grid, which the analyzer doesn't write). Hype goes into Traktor's Comment only where
Comment already carries one."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import analysis, collection, traktor

FIELDS = ("colour", "key", "genre", "hype", "bpm")
CLEARABLE = ("colour", "genre", "hype", "bpm")  # colour: off the keep-always list; the others: back to the analysis


def parse(field: str, value) -> str | int | float:
    """A value as typed (colour name or 1-7, Camelot or a key like 'F#m', ...) in the form it's stored in."""
    s = str(value).strip()
    if field == "colour":
        names = {n.lower(): n for n in analysis.COLOR_NAMES}
        if s.isdigit() and 1 <= int(s) <= 7:
            return analysis.COLOR_NAMES[int(s) - 1]
        if s.lower() in names:
            return names[s.lower()]
        raise ValueError(f"colour is one of {', '.join(analysis.COLOR_NAMES)} (or 1-7), not {s!r}")
    if field == "key":
        u = s.upper().replace(" ", "")
        if u[:-1].isdigit() and 1 <= int(u[:-1]) <= 12 and u[-1:] in ("A", "B"):
            return f"{int(u[:-1])}{u[-1]}"
        minor = s.endswith("m") or s.lower().endswith("minor")
        root = s.removesuffix("minor").removesuffix("major").strip().removesuffix("m").strip()
        cam = analysis.camelot(root[:1].upper() + root[1:], "minor" if minor else "major") if root else None
        if cam:
            return cam
        raise ValueError(f"key is Camelot (8A, 11B) or a key like Am, F#, Eb minor; not {s!r}")
    if field == "genre":
        if not s:
            raise ValueError("genre can't be empty (clear it instead)")
        return s
    if field == "hype":
        if not s.isdigit() or not 1 <= int(s) <= 10:
            raise ValueError(f"hype is a whole number 1-10, not {s!r}")
        return int(s)
    if field == "bpm":
        try:
            b = float(s)
        except ValueError:
            b = 0
        if not 40 <= b <= 300:
            raise ValueError(f"BPM is a number between 40 and 300, not {s!r}")
        return analysis.round_bpm(b)
    raise ValueError(f"field is one of {', '.join(FIELDS)}, not {field!r}")


def find_track(text: str) -> str:
    """A scanned track by its path, or the one whose path contains text."""
    with sqlite3.connect(collection.NATIVE_DB) as con:
        paths = [r[0] for r in con.execute("SELECT path FROM tracks")]
    if text in paths:
        return text
    t = text.lower()
    hits = [p for p in paths if t in p.lower()]
    if len(hits) != 1:
        raise SystemExit(f'No scanned track has "{text}" in its path.' if not hits else
                         f'"{text}" matches {len(hits)} tracks; use more of the name: '
                         + ", ".join(Path(p).name for p in hits[:5]) + ("…" if len(hits) > 5 else ""))
    return hits[0]


def current(path: str) -> dict:
    """The track's values as the analyzer uses them now, and which were set by hand."""
    with sqlite3.connect(collection.NATIVE_DB) as con:
        con.row_factory = sqlite3.Row
        r = con.execute("SELECT * FROM tracks WHERE path=?", (path,)).fetchone()
    if r is None:
        raise ValueError(f"{path} isn't scanned")
    bpm = collection.mix_bpm(r)
    return {"path": path, "colour": r["traktor_color"], "key": collection.mix_camelot(r),
            "genre": r["my_genre"] or r["genre"], "hype": r["hype"], "bpm": analysis.round_bpm(bpm) if bpm else None,
            "by_hand": collection.manual().get(path, {}),
            "held": sorted(k for k in collection.HOLDS if path in collection.held(k))}


def _traktor_file() -> bool:
    try:
        traktor.find_collection()
        return True
    except SystemExit:
        return False


def edit(path: str, changes: dict | None = None, clear: list[str] | tuple = (), dry_run: bool = False,
         to_traktor: bool = True, traktor_genre: str | None = None) -> dict:
    """Set (changes {field: value}) or clear fields of one scanned track. Returns what changed and `undo`:
    the changes + clear (+ traktor_genre: Traktor's genre before) that put it back (the page's Undo sends them).
    traktor_genre: what Traktor gets for a cleared genre (default: the file's genre tag, as `traktor` writes)."""
    changes = {f: parse(f, v) for f, v in (changes or {}).items() if v is not None and v != ""}
    clear = [f for f in clear if f not in changes or f == "colour"]  # colour: set, and off the keep-always list
    for f in clear:
        if f not in CLEARABLE:
            raise ValueError(f"{f} can't be cleared, only set (clearable: {', '.join(CLEARABLE)})")
    if not changes and not clear:
        raise ValueError("nothing to change")
    now = current(path)
    by_hand, held = now["by_hand"], now["held"]

    undo: dict = {"changes": {}, "clear": []}
    for f in changes.keys() | set(clear):
        if f == "colour":
            if now["colour"] and now["colour"] != changes.get("colour"):
                undo["changes"]["colour"] = now["colour"]
            if "colour" not in held:
                undo["clear"].append("colour")
        elif f == "key":
            if now["key"] and now["key"] != changes["key"]:
                undo["changes"]["key"] = now["key"]
        elif f in by_hand:
            undo["changes"][f] = by_hand[f]
        else:
            undo["clear"].append(f)
    if "genre" in changes and "genre" not in by_hand and to_traktor and _traktor_file():
        undo["traktor_genre"] = (traktor.read_track(path) or {}).get("genre") or ""

    # what Traktor gets: the hand values, and for a cleared genre / hype what the analyzer goes back to
    tk: dict = {}
    if "colour" in changes:
        tk["colour"] = analysis.COLOR_NAMES.index(changes["colour"]) + 1
    if "key" in changes:
        tk["camelot"] = changes["key"]
    if "genre" in changes:
        tk["genre"] = changes["genre"]
    elif "genre" in clear and "genre" in by_hand:  # the genre before (an undo), else the file's tag as `traktor` writes
        g = traktor_genre if traktor_genre is not None else traktor.file_genre(path)
        if g is not None:  # no tag: Traktor keeps the genre it has
            tk["genre"] = g
    if "hype" in changes:
        tk["hype"] = changes["hype"]
    elif "hype" in clear and "hype" in by_hand:
        tk["hype"] = _was(path, "hype")

    out: dict = {"path": path, "set": changes, "cleared": clear, "dry_run": dry_run, "undo": undo}
    needs = any(k in tk for k in ("colour", "camelot", "genre"))
    if not to_traktor:
        out["traktor"] = "not written (--no-traktor)"
    elif not tk:
        out["traktor"] = "nothing for Traktor (BPM stays in the analyzer: Traktor's BPM goes with its beat grid)"
    elif not _traktor_file():
        out["traktor"] = "no Traktor collection found: saved in the analyzer only"
    else:
        hype_only = not needs
        cur = traktor.read_track(path) if hype_only else None
        if hype_only and not (cur and traktor.HYPE_RE.search(cur.get("comment") or "")):
            out["traktor"] = "Traktor's Comment has no Hype (hype --traktor puts one there): not written"
        else:
            from . import tempo

            if hype_only and tempo.loaded():
                out["traktor"] = "a tempo view is loaded: Traktor's Comment holds the view's hype, not written"
            else:
                out["traktor"] = traktor.set_track(path, colour=tk.get("colour"), camelot=tk.get("camelot"),
                                                   genre=tk.get("genre"), hype=tk.get("hype"), dry_run=dry_run)
    if dry_run:
        return out

    with sqlite3.connect(collection.NATIVE_DB) as con:
        if "colour" in changes:
            con.execute("UPDATE tracks SET traktor_color=? WHERE path=?", (changes["colour"], path))
        if "key" in changes:
            con.execute("UPDATE tracks SET traktor_camelot=? WHERE path=?", (changes["key"], path))
    if "colour" in changes:  # yours (training learns from it), and kept always unless cleared too (an undo)
        collection.accept_guesses([path])
        if "colour" not in clear:
            collection.set_hold("colour", {path: changes["colour"]}, True)
    if "colour" in clear:
        collection.set_hold("colour", {path: None}, False)
    for f in ("genre", "hype", "bpm"):
        if f in changes:
            collection.set_manual(path, f, str(changes[f]))
        elif f in clear:
            collection.set_manual(path, f, None)
    if "genre" in changes:
        collection.set_hold("genre", {path: changes["genre"]}, True)
    elif "genre" in clear:
        collection.set_hold("genre", {path: None}, False)
    if "key" in changes:
        collection.log_feedback("manual", [(path, {"field": "key", "value": changes["key"]})])
    if "colour" in changes:
        collection.log_feedback("manual", [(path, {"field": "colour", "value": changes["colour"]})])
    if "bpm" in clear:  # Traktor's BPM again, as it is now
        info = collection.load_traktor_info()
        if path in info:
            with sqlite3.connect(collection.NATIVE_DB) as con:
                con.execute("UPDATE tracks SET traktor_bpm=? WHERE path=?", (info[path][0], path))
    out["now"] = current(path)
    return out


def _was(path: str, field: str):
    """The value the analysis gave before it was set by hand (first column)."""
    import json

    with sqlite3.connect(collection.NATIVE_DB) as con:
        collection._manual_table(con)
        r = con.execute("SELECT was FROM manual WHERE path=? AND field=?", (path, field)).fetchone()
    return json.loads(r[0])[0] if r and r[0] else None


def listing() -> list[dict]:
    """Every track with a value set by hand."""
    return [{"file": Path(p).name, "path": p, **f} for p, f in sorted(collection.manual().items())]
