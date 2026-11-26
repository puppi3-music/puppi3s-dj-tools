"""`names`: copy the DJ app's artist and title into files whose own tags leave them empty, and undo it.

A track imported while the app's tag writing is off (Traktor: FileAccess.TagsSerialization=0) can have its name
only in the app's library; the Tracks tab then falls back to that name (`web.tracks`). This writes it into the
file, so every other app sees it too. Only empty fields are filled, never a name the file has. With Traktor,
each written entry is stamped with the file's new mtime afterwards (`traktor.stamp_if_unchanged`): otherwise
Traktor reads the file's tags again on deck load, and they'd replace its genre, colour, comment … with the
file's. That edit of collection.nml is backed up first, like every other.

Undo takes out exactly what was written (a field changed since is left alone) and stamps the entries again.
What was written is kept in data/names-written.json, across runs, until undone."""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from pathlib import Path

from . import analysis, collection, library, traktor
from .analysis import HOME, log

RECORD = HOME / "names-written.json"
FIELDS = ("artist", "title")
EXAMPLES = 15


def _record() -> dict[str, dict]:
    """{path: {"artist": …, "title": …, "at": …}} of what this command wrote and hasn't undone."""
    try:
        return json.loads(RECORD.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_record(rec: dict) -> None:
    if not rec:
        RECORD.unlink(missing_ok=True)
        return
    tmp = RECORD.with_name(RECORD.name + ".tmp")
    tmp.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, RECORD)


def usable(path: str, dj: dict) -> dict[str, str]:
    """The DJ app's artist/title worth showing or writing for a file. Traktor fills an untagged file's names by
    splitting its file name at " - ", so leave out a title that's only the file name, and a name the file name
    holds only cut short mid-word ("Ay" for "Ayū - …": that split drops letters outside ASCII)."""
    stem = unicodedata.normalize("NFC", Path(path).stem)
    out = {}
    for k in FIELDS:
        v = unicodedata.normalize("NFC", (dj.get(k) or "").strip())
        if not v or (k == "title" and v == stem):
            continue
        if v in stem and not re.search(rf"(?<!\w){re.escape(v)}(?!\w)", stem):
            continue
        out[k] = v
    return out


def plan(meta: dict[str, dict] | None = None) -> dict[str, dict]:
    """{path: {field: the DJ app's value}} for scanned files whose own artist or title tag is empty and whose DJ
    app has a usable one (`usable`)."""
    meta = library.track_meta() if meta is None else meta
    con = collection.db()
    rows = con.execute("SELECT path FROM tracks WHERE COALESCE(artist, '') = '' OR COALESCE(title, '') = ''").fetchall()
    out = {}
    for (path,) in rows:
        dj = meta.get(path) or {}
        if not any(dj.get(k) for k in FIELDS) or not os.path.isfile(path):
            continue
        tags = traktor.file_tags(path)
        if tags is None:  # unreadable or not a tagged format
            continue
        want = {k: v for k, v in usable(path, dj).items() if not (tags.get(k) or "").strip()}
        if want:
            out[path] = want
    return out


def _example(path: str, fields: dict) -> str:
    return f"{Path(path).name}: " + " – ".join(fields[k] for k in FIELDS if fields.get(k))


def _set_tags(path: str, fields: dict[str, str | None], con) -> None:
    """Set (None: remove) the fields in the file's tags; its cached analysis and collection row follow."""
    p = Path(path)
    old_key = analysis._cache_key(p)
    f, mode = collection._open_tags(p)
    for k, v in fields.items():
        collection._set_tag(f, mode, k, v)
    f.save()
    collection._rekey_cache(old_key, p)
    for k, v in fields.items():
        con.execute(f"UPDATE tracks SET {k}=? WHERE path=?", (v, path))
    if con.execute("SELECT 1 FROM tracks WHERE path=?", (path,)).fetchone():
        collection._refresh_stat(con, path)
    con.commit()


def _before_writing(dry_run: bool) -> Path | None:
    """Traktor: refuse in a tempo view and while it's open (it would re-read the files' tags on deck load and
    save over our stamps when it quits). Returns its collection, or None for another app."""
    if library.app() != "traktor":
        return None
    nml = traktor.find_collection(None)
    traktor.refuse_if_tempo_view(nml)
    traktor.check_closed(dry_run, "the marks that stop it reading these files' tags again")
    return nml


def _stamp(nml: Path, paths: set[str], stats: dict) -> dict:
    """Mark Traktor up to date with the files just changed (see the module doc), backing the collection up."""
    text = nml.read_text(encoding="utf-8")
    stats.update(stamped_in_traktor=0, not_stamped=0)

    def fix(m: re.Match) -> str:
        entry = m.group(0)
        loc = re.search(r"<LOCATION\b(?:[^>\x22]|\x22[^\x22]*\x22)*>", entry)
        found = traktor.location_paths(traktor.attrs(loc.group(0))) if loc else []
        if not paths.intersection(found):
            return entry
        entry, how = traktor.stamp_if_unchanged(entry, found)
        stats["stamped_in_traktor"] += how == "stamped"
        stats["not_stamped"] += how == "tags_changed"
        return entry

    c0, c1 = text.find("<COLLECTION"), text.find("</COLLECTION>")
    if c0 == -1 or c1 == -1:
        raise SystemExit(f"{nml} doesn't look like a Traktor collection (no COLLECTION section).")
    body = re.sub(r"<ENTRY\b.*?</ENTRY>", fix, text[c0:c1], flags=re.S)
    if stats["not_stamped"]:
        stats["warning"] = (f"{stats['not_stamped']} of these files had other tags (album or comment) changed since "
                            "Traktor last read them, so Traktor will read their tags again when you load them.")
    out = traktor._save(nml, text, text[:c0] + body + text[c1:], {}, False)
    if out.get("backup"):
        stats["backup"] = out["backup"]
    return stats


def write(dry_run: bool = False) -> dict:
    """Fill the files' empty artist/title tags from the DJ app (see the module doc)."""
    todo = plan()
    stats = {"dj_app": library.app_name(), "dry_run": dry_run}
    examples = [_example(p, f) for p, f in list(todo.items())[:EXAMPLES]]
    if dry_run or not todo:
        if todo:
            _before_writing(True)
        return {**stats, "would_write" if dry_run else "written": len(todo), "examples": examples}
    nml = _before_writing(False)
    con, rec, done, errors = collection.db(), _record(), set(), 0
    for path, fields in todo.items():
        try:
            _set_tags(path, fields, con)
        except Exception as err:
            errors += 1
            log(f"  {Path(path).name}: {err}")
            continue
        rec[path] = {**rec.get(path, {}), **fields, "at": time.strftime("%Y-%m-%d %H:%M")}
        done.add(path)
        _save_record(rec)  # after each file: a crash halfway still leaves undo knowing what was written
    stats.update(written=len(done), errors=errors, examples=examples)
    if nml and done:
        _stamp(nml, done, stats)
    return stats


def undo(dry_run: bool = False) -> dict:
    """Take out the names `write` put into files, where the file still has them."""
    rec = _record()
    con, back, changed, gone, done, errors, examples = collection.db(), {}, set(), 0, set(), 0, []
    for path, wrote in rec.items():
        tags = traktor.file_tags(path) if os.path.isfile(path) else None
        if tags is None:  # gone, or on a drive that isn't plugged in: stays in the record
            gone += 1
            continue
        clear = {k: None for k in FIELDS if wrote.get(k) and (tags.get(k) or "") == wrote[k]}
        if not clear:  # renamed since: nothing of ours left in it
            changed.add(path)
        else:
            back[path] = clear
            if len(examples) < EXAMPLES:
                examples.append(_example(path, wrote))
    stats = {"dj_app": library.app_name(), "dry_run": dry_run, "missing": gone, "changed_since": len(changed)}
    if dry_run or not back:
        if back:
            _before_writing(True)
        return {**stats, "would_restore" if dry_run else "restored": len(back), "examples": examples}
    nml = _before_writing(False)
    for path, clear in back.items():
        try:
            _set_tags(path, clear, con)
        except Exception as err:
            errors += 1
            log(f"  {Path(path).name}: {err}")
            continue
        done.add(path)
    _save_record({p: v for p, v in rec.items() if p not in done and p not in changed})  # failures and missing files stay
    stats.update(restored=len(done), errors=errors, examples=examples)
    if nml and done:
        _stamp(nml, done, stats)
    return stats
