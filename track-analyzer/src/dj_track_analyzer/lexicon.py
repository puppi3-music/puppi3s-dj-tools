"""
Lexicon (lexicondj.com) side of the tempo view: `tempo load --lexicon` / `tempo native --lexicon`.

Talks to Lexicon's Local API (Lexicon running, Settings → Integrations → Local API on, localhost:48624).
Per track it switches the same things as in Traktor:
  genre      the genres heard at the view's tempo
  color      YOUR Lexicon colour moved by the view's shift (uncoloured tracks stay uncoloured)
  comment    its "Hype NN" part becomes the view's hype (your text is kept)
  energy     the view's hype, 1-10 (replaces Lexicon's own energy while loaded)
plus a smartlist folder "Genres @145 (Track Analyzer)" (deleted again on native).
Your values are remembered in data/tempo-loaded-lexicon.json; `native` restores a field
only if it still holds the view's value, so edits made in Lexicon while the view is loaded are kept.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

from . import collection
from . import traktor
from .analysis import COLOR_NAMES, HOME, fmt_time, log

API = "http://localhost:48624/v1"
STATE = HOME / "tempo-loaded-lexicon.json"
LIBRARY = Path.home() / "Library/Application Support/lexicon/main.db"
BACKUPS = HOME / "lexicon-backups"
KEEP_BACKUPS = 10
FIELDS = ("genre", "color", "comment", "energy")


def _cur(t: dict, f: str):
    """A track's value with empty as Lexicon stores it: 0 for energy (a number field), "" for text."""
    return t.get(f) or (0 if f == "energy" else "")
# Traktor's 7 colours as Lexicon names them. Lexicon's other shades (red_dark, teal, …) have no place on
# the scale and are left alone.
LEX_COLORS = [c.lower() for c in COLOR_NAMES]


class LexiconError(SystemExit):
    pass


def _call(method: str, path: str, body: dict | None = None, query: str = "") -> dict:
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(20):
        req = urllib.request.Request(f"{API}{path}{query}", data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read())
            except ValueError:
                err = {}
            if err.get("errorCode") == 2 and attempt < 19:  # busy: other library edits waiting their turn
                time.sleep(0.25 * (attempt + 1))
                continue
            raise LexiconError(f"Lexicon API {method} {path}: {err.get('message') or e} "
                               f"(code {err.get('errorCode')})")
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            raise LexiconError("Can't reach Lexicon's Local API on localhost:48624. Open Lexicon and turn on "
                               f"Settings → Integrations → Local API. ({e})")
    raise LexiconError(f"Lexicon stayed busy for {method} {path}")


def _each(items: list, label) -> object:
    """Yields the items, logging "[i/n  ~m:ss left] what" about twice a second: the web page's progress bar
    reads it (jobs.PROGRESS), and each line names the track being changed."""
    t0 = last = time.time()
    for i, x in enumerate(items, 1):
        yield x
        now = time.time()
        if now - last >= 0.5 or i == len(items):
            last = now
            log(f"[{i}/{len(items)}  ~{fmt_time((len(items) - i) * (now - t0) / i)} left] {label(x)}")


def tracks() -> list[dict]:
    out, offset = [], 0
    fields = "".join(f"&fields={f}" for f in ("id", "location", "title", *FIELDS))
    while True:
        d = _call("GET", "/tracks", query=f"?limit=1000&offset={offset}&source=non-archived{fields}")["data"]
        out += d["tracks"]
        offset += len(d["tracks"])
        if not d["tracks"] or offset >= d["total"]:
            return out


def _edit(track_id: int, edits: dict) -> None:
    _call("PATCH", "/track", {"id": track_id, "edits": edits})


def _paths(location: str) -> list[str]:
    """Lexicon writes the boot drive as /Volumes/Macintosh HD/…; our databases use /Users/…"""
    loc = unicodedata.normalize("NFC", location or "")
    out = [loc]
    m = re.match(r"/Volumes/[^/]+(/.*)$", loc)
    if m:
        out.append(m.group(1))
    return out


def _regex_escape(s: str) -> str:
    return re.sub(r"([.*+?^${}()|\[\]\\/])", r"\\\1", s)


def smartlist(genre: str) -> dict:
    """Matches the genre as one whole entry of 'A; B; C', so 'Trance' never catches 'Hard Trance' or
    'Progressive Trance' (Lexicon has regex rules, unlike Traktor's query language)."""
    sep = re.escape(collection.GENRE_SEP.strip())
    return {"matchAll": True, "rules": [{"field": "genre", "operator": "StringRegExpMatch",
                                        "values": [f"(^|{sep})\\s*{_regex_escape(genre)}\\s*({sep}|$)"],
                                        "or": False}]}


def backup(tag: str) -> str:
    """Consistent copy of Lexicon's library database, taken while Lexicon runs (SQLite's backup API on a
    read-only connection, so pending WAL writes are included). To restore: quit Lexicon, copy it over
    main.db and delete main.db-wal and main.db-shm next to it."""
    if not LIBRARY.exists():
        raise SystemExit(f"Lexicon's library isn't at {LIBRARY}; not changing anything without a backup.")
    BACKUPS.mkdir(parents=True, exist_ok=True)
    dest = BACKUPS / f"main.db.{tag}-{time.strftime('%Y%m%d-%H%M%S')}"
    src = sqlite3.connect(f"file:{LIBRARY}?mode=ro", uri=True)
    try:
        with sqlite3.connect(dest) as out:
            src.backup(out)
            ok = out.execute("PRAGMA integrity_check").fetchone()[0]
            n = out.execute("SELECT COUNT(*) FROM Track").fetchone()[0]
    finally:
        src.close()
    if ok != "ok":
        raise SystemExit(f"Backup of Lexicon's library failed its integrity check ({ok}); nothing changed.")
    for old in sorted(BACKUPS.glob("main.db.*"), key=lambda f: f.name[-15:])[:-KEEP_BACKUPS]:  # by timestamp
        old.unlink()
    return f"{dest} ({n} tracks)"


def loaded() -> dict | None:
    return json.loads(STATE.read_text()) if STATE.exists() else None


def _show(field: str, v):
    return v if v not in (None, "") else "(empty)"


def build(bpm: float, view_db: sqlite3.Connection, shifted_colour) -> tuple[dict, dict, list]:
    """What would change: ({lexicon id: {field: [yours, view]}}, stats, genres used)."""
    view = {}
    for r in view_db.execute("SELECT * FROM tracks WHERE type='track' AND (error IS NULL OR error LIKE 'tags:%')"):
        view[unicodedata.normalize("NFC", r["path"])] = r
    if not view:
        raise SystemExit(f"No {bpm:g} BPM analysis yet. Run: uv run dj-track-analyzer tempo scan --bpm {bpm:g}")
    changes, genres, examples = {}, set(), []
    stats = {"lexicon_tracks": 0, "in_view": 0, "genres_set": 0, "colours_moved": 0, "colours_same": 0,
             "hype_set": 0, "energy_set": 0, "not_in_view": 0, "genre_held": 0, "colour_held": 0}
    genre_held, colour_held = collection.held("genre"), collection.held("colour")
    log("Lexicon: reading your tracks…")
    for t in tracks():
        stats["lexicon_tracks"] += 1
        r = next((view[p] for p in _paths(t.get("location")) if p in view), None)
        if r is None:
            stats["not_in_view"] += 1
            continue
        stats["in_view"] += 1
        want = {}
        if r["path"] in genre_held:  # held in the analyzer: your genre stays, with its Smartlist
            stats["genre_held"] += 1
            genres.update(g.strip() for g in (t.get("genre") or "").split(";") if g.strip())
        elif r["genre"]:
            want["genre"] = collection.multi_genre(dict(r))
            genres.update(g.strip() for g in want["genre"].split(";"))
        cur_col = (t.get("color") or "").lower()
        yours = LEX_COLORS.index(cur_col) + 1 if cur_col in LEX_COLORS else None
        at = shifted_colour(yours, r["color_position"], r["native_color_position"])
        if r["path"] in colour_held:
            stats["colour_held"] += 1
        elif at is not None:
            want["color"] = LEX_COLORS[at - 1]
            stats["colours_moved" if at != yours else "colours_same"] += 1
        if r["hype"] is not None:
            want["comment"] = traktor.with_hype(t.get("comment"), r["hype"])
            want["energy"] = int(r["hype"])
        changed = {f: [_cur(t, f), v] for f, v in want.items() if v != _cur(t, f)}
        if changed:
            changes[str(t["id"])] = {"title": t.get("title") or Path(t.get("location") or "").name, **changed}
            stats["genres_set"] += "genre" in changed
            stats["hype_set"] += "comment" in changed
            stats["energy_set"] += "energy" in changed
            if len(examples) < 12 and ("genre" in changed or "color" in changed):
                bits = [f"{_show(f, changed[f][0])} → {changed[f][1]}" for f in ("genre", "color") if f in changed]
                examples.append(f"{changes[str(t['id'])]['title']}: " + "; ".join(bits))
    stats["examples"] = examples
    return changes, stats, sorted(genres, key=str.lower)


def load(bpm: float, folder: str, view_db: sqlite3.Connection, shifted_colour, dry_run: bool = False) -> dict:
    st = loaded()
    if st:
        raise SystemExit(f"The {st['bpm']:g} BPM view is already loaded in Lexicon (since {st['loaded_at']}). "
                         "Switch back first: uv run dj-track-analyzer tempo native --lexicon")
    changes, stats, genres = build(bpm, view_db, shifted_colour)
    known = {g.lower(): g for g in traktor.known_genres()}
    lists = [known[g.lower()] for g in genres if g.lower() in known]
    stats["smartlists"] = f"{folder} ({len(lists)} genres)"
    log(f"Lexicon: {len(changes)} of {stats['in_view']} tracks get the view's values")
    if dry_run:
        return stats
    log("Lexicon: backing up its library…")
    stats["backup"] = backup("before-tempo-view")
    # Remember before writing, so an interrupted load can still be switched back.
    state = {"bpm": bpm, "loaded_at": time.strftime("%Y-%m-%d %H:%M"), "folder_id": None, "tracks": changes}
    STATE.write_text(json.dumps(state, ensure_ascii=False))
    log(f"Lexicon: changing {len(changes)} tracks, one at a time…")
    for tid, ch in _each(list(changes.items()), lambda x: x[1]["title"][:60]):
        _edit(int(tid), {f: v[1] for f, v in ch.items() if f in FIELDS})
    log(f"Lexicon: adding the smartlist folder {folder} ({len(lists)} genres)…")
    folder_id = _call("POST", "/playlist", {"name": folder, "type": "1"})["data"]["id"]
    state["folder_id"] = folder_id
    STATE.write_text(json.dumps(state, ensure_ascii=False))
    for g in lists:
        _call("POST", "/playlist", {"name": g, "type": "3", "parentId": folder_id, "smartlist": smartlist(g)})
    stats["now_loaded"] = (f"{bpm:g} BPM view in Lexicon. Back to normal: uv run dj-track-analyzer tempo native --lexicon")
    stats["note"] = ("While it's loaded, don't sync Lexicon → Traktor or import from Traktor into Lexicon. "
                     "Syncing to your USB is fine; sync again after `tempo native --lexicon` to put your "
                     "normal values back on the USB.")
    return stats


def unload(dry_run: bool = False) -> dict:
    st = loaded()
    if not st:
        raise SystemExit("No tempo view is loaded in Lexicon.")
    log("Lexicon: reading your tracks…")
    now = {str(t["id"]): t for t in tracks()}
    stats = {"bpm": st["bpm"], "restored": 0, "kept_your_changes": 0, "gone_from_lexicon": 0}
    if not dry_run:
        log("Lexicon: backing up its library…")
        stats["backup"] = backup("before-tempo-native")
        log(f"Lexicon: putting your values back on {len(st['tracks'])} tracks, one at a time…")
    kept = []
    for tid, ch in (st["tracks"].items() if dry_run else _each(list(st["tracks"].items()), lambda x: x[1]["title"][:60])):
        t = now.get(tid)
        if t is None:
            stats["gone_from_lexicon"] += 1
            continue
        edits = {}
        for f in FIELDS:
            if f not in ch:
                continue
            yours, view = ch[f]
            cur = _cur(t, f)
            if cur == view:
                edits[f] = yours
            elif cur != yours:  # changed in Lexicon while the view was loaded: keep it
                stats["kept_your_changes"] += 1
                if len(kept) < 30:
                    kept.append(f"{ch['title']}: {f} {_show(f, cur)} (view had {view}, normally {_show(f, yours)})")
        stats["restored"] += len(edits)
        if edits and not dry_run:
            _edit(int(tid), edits)
    stats["kept"] = kept
    fid = st.get("folder_id")
    if fid is not None:
        stats["smartlists"] = "folder removed"
        if not dry_run:
            log("Lexicon: removing the smartlist folder…")
            try:
                _call("DELETE", "/playlists", {"ids": [fid]})
            except LexiconError as e:
                if "code 101" not in str(e):  # already deleted by hand
                    raise
    if not dry_run:
        STATE.unlink()
        stats["now_loaded"] = "your normal genres, colours, comments and energy in Lexicon"
    return stats


def status() -> str:
    st = loaded()
    return (f"{st['bpm']:g} BPM view since {st['loaded_at']} ({len(st['tracks'])} tracks changed)"
            if st else "normal (no tempo view)")
