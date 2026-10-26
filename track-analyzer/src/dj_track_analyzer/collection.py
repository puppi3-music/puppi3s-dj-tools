"""
Whole-collection scanning and search.

  uv run dj-track-analyzer scan ~/Music/Collection            analyze everything (resumable)
  uv run dj-track-analyzer scan ~/Music/Collection --workers 4
  uv run dj-track-analyzer export ~/Desktop/collection.csv    spreadsheet of the results

Results live in data/collection.db. Re-running `scan` only analyzes new or
changed files, and Ctrl-C is safe: everything finished so far is kept.
"""

from __future__ import annotations

import csv
import json
import os
import signal
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path

from . import analysis
from .analysis import AUDIO_EXTS, HOME, log

DB = HOME / "collection.db"
DEFAULT_CSV = HOME / "collection.csv"

COLUMNS = [
    # (name, sql type)
    ("path", "TEXT PRIMARY KEY"), ("folder", "TEXT"), ("filename", "TEXT"),
    ("artist", "TEXT"), ("title", "TEXT"), ("album", "TEXT"), ("tag_genre", "TEXT"),
    ("type", "TEXT"), ("duration_sec", "REAL"),
    ("bpm", "REAL"), ("bpm_confidence", "REAL"), ("key", "TEXT"), ("camelot", "TEXT"),
    ("key_strength", "REAL"), ("traktor_bpm", "REAL"), ("traktor_camelot", "TEXT"),
    ("lufs", "REAL"), ("loudness_range", "REAL"),
    ("genre", "TEXT"), ("genre_score", "REAL"), ("genre_2", "TEXT"), ("genre_3", "TEXT"),
    ("genre_2_score", "REAL"), ("genre_3_score", "REAL"),
    ("traktor_color", "TEXT"), ("color", "TEXT"), ("color_position", "REAL"), ("color_confidence", "REAL"),
    ("mood_aggressive", "REAL"), ("mood_happy", "REAL"), ("mood_party", "REAL"), ("mood_relaxed", "REAL"),
    ("mood_sad", "REAL"), ("instruments", "TEXT"), ("approachability", "REAL"), ("hype", "INTEGER"),
    ("styles", "TEXT"), ("my_genre", "TEXT"), ("my_genre_confidence", "REAL"),
    ("error", "TEXT"), ("size", "INTEGER"), ("mtime", "INTEGER"), ("scanned_at", "TEXT"),
]
NAMES = [c for c, _ in COLUMNS]

# What the user mixes with: Traktor's BPM/key where Traktor has one (possibly corrected by hand),
# else our own measurement. Used for search, mix suggestions, hype filters and tag writing.
BPM_SQL = "COALESCE(traktor_bpm, bpm)"
CAMELOT_SQL = "COALESCE(traktor_camelot, camelot)"


def mix_bpm(row) -> float | None:
    return row["traktor_bpm"] or row["bpm"]


def mix_camelot(row) -> str | None:
    return row["traktor_camelot"] or row["camelot"]


# Held tracks: the writers (tag, scan --write-tags, traktor, colors review --apply, tempo load, Lexicon) leave
# their genre / colour as it is. Kept in the normal library's database, also while a tempo view is in use.
NATIVE_DB = DB
HOLDS = {"genre": ("genre_keep", "genre"), "colour": ("color_keep", "colour")}  # kind: (table, value column)


def _hold_db() -> sqlite3.Connection:
    con = sqlite3.connect(NATIVE_DB)
    for table, col in HOLDS.values():
        con.execute(f"CREATE TABLE IF NOT EXISTS {table} (path TEXT PRIMARY KEY, {col} TEXT, at TEXT)")
    return con


def held(kind: str) -> set[str]:
    """Paths whose genre ("genre") or colour ("colour") is held."""
    if not NATIVE_DB.exists():
        return set()
    with _hold_db() as con:
        return {r[0] for r in con.execute(f"SELECT path FROM {HOLDS[kind][0]}")}


def set_hold(kind: str, values: dict[str, str | None], on: bool = True) -> int:
    """Hold (on) or release the genre/colour of these tracks. values: {path: the value held, for display}."""
    table, _col = HOLDS[kind]
    with _hold_db() as con:
        if on:
            now = time.strftime("%Y-%m-%d %H:%M")
            con.executemany(f"INSERT OR IGNORE INTO {table} VALUES (?, ?, ?)", [(p, v, now) for p, v in values.items()])
        else:
            con.executemany(f"DELETE FROM {table} WHERE path=?", [(p,) for p in values])
        return con.total_changes


def holds_list() -> dict:
    with _hold_db() as con:
        return {kind: [{"file": Path(r[0]).name, kind: r[1], "since": r[2], "path": r[0]}
                       for r in con.execute(f"SELECT path, {col}, at FROM {table} ORDER BY at, path")]
                for kind, (table, col) in HOLDS.items()}


def hold_matching(kind: str, text: str, on: bool = True) -> dict:
    """Hold (or release) the scanned tracks whose path contains text. At most 10, so a typo can't hold half
    the collection."""
    t = text.lower()
    if on:
        with sqlite3.connect(NATIVE_DB) as con:
            rows = [r for r in con.execute("SELECT path, tag_genre, traktor_color FROM tracks") if t in r[0].lower()]
        values = {r[0]: r[1] if kind == "genre" else r[2] for r in rows}
    else:
        values = {p: None for p in held(kind) if t in p.lower()}
    if not values:
        raise SystemExit(f'No {"scanned" if on else "held"} track has "{text}" in its path.')
    if len(values) > 10:
        raise SystemExit(f'"{text}" matches {len(values)} tracks; use more of the name.')
    set_hold(kind, values, on)
    return {"held" if on else "released": sorted(Path(p).name for p in values), "kind": kind}


# Colours the app guessed into Traktor (`traktor` / `colors apply` filling in or overwriting a colour) that nobody has
# checked yet. `colors train` leaves such a track out while Traktor still has that colour: changing it in Traktor or
# accepting it (`accept_guesses`, the Tracks tab) makes it yours. Recolours you said yes to (`colors review --apply`)
# are never guesses. In the normal library's database, as the holds.
def _guess_db() -> sqlite3.Connection:
    con = sqlite3.connect(NATIVE_DB)
    con.execute("CREATE TABLE IF NOT EXISTS color_guess (path TEXT PRIMARY KEY, color INTEGER, at TEXT)")
    return con


def add_guesses(guesses: dict[str, int]) -> None:
    """Remember colours just written into Traktor without anyone checking them: {path: COLOR 1-7}."""
    if not guesses:
        return
    now = time.strftime("%Y-%m-%d %H:%M")
    with _guess_db() as con:
        con.executemany("INSERT OR REPLACE INTO color_guess VALUES (?, ?, ?)", [(p, c, now) for p, c in guesses.items()])


def unchecked_guesses(traktor_colors: dict[str, int]) -> dict[str, int]:
    """The guesses Traktor still has, given {path: COLOR} as read from it now. One you changed since is yours."""
    if not NATIVE_DB.exists():
        return {}
    with _guess_db() as con:
        return {p: c for p, c in con.execute("SELECT path, color FROM color_guess") if traktor_colors.get(p) == c}


def accept_guesses(paths) -> int:
    """These tracks' colours are right (you checked them): they count as yours from now on."""
    paths = list(paths)
    if not paths or not NATIVE_DB.exists():
        return 0
    with _guess_db() as con:
        con.executemany("DELETE FROM color_guess WHERE path=?", [(p,) for p in paths])
        return con.total_changes


def db() -> sqlite3.Connection:
    HOME.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute(f"CREATE TABLE IF NOT EXISTS tracks ({', '.join(f'{c} {t}' for c, t in COLUMNS)})")
    have = {r[1] for r in con.execute("PRAGMA table_info(tracks)")}
    for c, t in COLUMNS:  # databases from older versions get new columns added
        if c not in have:
            con.execute(f"ALTER TABLE tracks ADD COLUMN {c} {t}")
    for idx in ("genre", "bpm", "camelot"):
        con.execute(f"CREATE INDEX IF NOT EXISTS ix_{idx} ON tracks({idx})")
    return con


# ----------------------------------------------------------------------------
# Tags
# ----------------------------------------------------------------------------


def read_tags(p: Path) -> dict:
    try:
        import mutagen

        f = mutagen.File(p, easy=True)
        if not f or not f.tags:
            return {}

        def first(k):
            v = f.tags.get(k)
            return str(v[0]) if v else None

        return {"artist": first("artist"), "title": first("title"),
                "album": first("album"), "tag_genre": first("genre")}
    except Exception:
        return {}


STD_KEY = {"major": "", "minor": "m"}


PITCH_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def camelot_to_key(code: str) -> str | None:
    """'8A' -> 'Am', '8B' -> 'C' (standard key tag spelling)."""
    try:
        num, letter = int(code[:-1]), code[-1].upper()
    except (ValueError, IndexError):
        return None
    table = analysis.CAMELOT_MINOR if letter == "A" else analysis.CAMELOT_MAJOR
    pc = next((pc for pc, n in table.items() if n == num), None)
    return None if pc is None else PITCH_NAMES[pc] + ("m" if letter == "A" else "")


def _wanted(row: dict, fields: list[str]) -> dict:
    wanted = {}
    bpm, cam = row.get("traktor_bpm") or row.get("bpm"), row.get("traktor_camelot") or row.get("camelot")
    if "genre" in fields and row.get("genre") and row.get("path") not in held("genre"):
        wanted["genre"] = row["genre"]
    if "bpm" in fields and bpm:
        wanted["bpm"] = str(analysis.round_bpm(bpm))
    if "key" in fields:
        if row.get("traktor_camelot"):
            k = camelot_to_key(row["traktor_camelot"])
            if k:
                wanted["initialkey"] = k
        elif row.get("key"):
            k, scale = row["key"].split(" ", 1)
            wanted["initialkey"] = k + STD_KEY.get(scale, "")
    if "camelot" in fields and cam:
        wanted["initialkey"] = cam
    return wanted


TAG_KEYS = ("genre", "bpm", "initialkey")


def _open_tags(p: Path):
    """Open a file's tags. Returns (file, mode) where mode is "easy" or "id3" (WAV/AIFF)."""
    import mutagen
    from mutagen import id3
    from mutagen.easyid3 import EasyID3
    from mutagen.easymp4 import EasyMP4Tags

    if "initialkey" not in EasyID3.valid_keys:
        EasyID3.RegisterTextKey("initialkey", "TKEY")
        EasyMP4Tags.RegisterFreeformKey("initialkey", "initialkey")
    f = mutagen.File(p, easy=True)
    if f is None:
        raise ValueError("unsupported file type for tags")
    if f.tags is None:
        f.add_tags()
    return f, ("id3" if isinstance(f.tags, id3.ID3) else "easy")


_FRAMES = {"genre": "TCON", "bpm": "TBPM", "initialkey": "TKEY"}


def _get_tag(f, mode, key):
    if mode == "id3":
        fr = f.tags.get(_FRAMES[key])
        return str(fr.text[0]) if fr and fr.text else None
    try:
        v = f.tags.get(key)
    except (KeyError, ValueError):
        return None
    return str(v[0]) if v else None


def _set_tag(f, mode, key, value):
    """Set (or with value None, remove) one tag. Raises KeyError if the format has no such field."""
    from mutagen import id3

    if mode == "id3":
        f.tags.delall(_FRAMES[key])
        if value is not None:
            f.tags.add(getattr(id3, _FRAMES[key])(encoding=3, text=[value]))
        return
    if value is None:
        if key in f.tags:
            del f.tags[key]
    else:
        f.tags[key] = value


def _backup_originals(con: sqlite3.Connection, p: Path, f, mode) -> None:
    """Remember the file's tags before we first change them, so `untag` can put them back."""
    con.execute("CREATE TABLE IF NOT EXISTS original_tags (path TEXT PRIMARY KEY, genre TEXT, bpm TEXT, "
                "initialkey TEXT, saved_at TEXT)")
    if con.execute("SELECT 1 FROM original_tags WHERE path=?", (str(p),)).fetchone():
        return  # keep the very first backup, never overwrite it with our own values
    con.execute("INSERT INTO original_tags VALUES (?,?,?,?,?)",
                (str(p), *(_get_tag(f, mode, k) for k in TAG_KEYS), time.strftime("%Y-%m-%d %H:%M")))
    con.commit()


def _rekey_cache(old_key: str, p: Path) -> None:
    """Tag edits change a file's size/date; keep its cached analysis (the audio didn't change)."""
    try:
        new_key = analysis._cache_key(p)
        if new_key != old_key:
            with analysis._db() as c:
                c.execute("UPDATE OR REPLACE emb SET k=? WHERE k=?", (new_key, old_key))
                c.execute("UPDATE OR REPLACE deep SET k=? WHERE k=?", (new_key, old_key))
    except Exception:
        pass


def write_tags(p: Path, row: dict, fields: list[str], con: sqlite3.Connection | None = None) -> str | None:
    """Write chosen results into the file's own tags (backing up the originals first).
    Returns an error string or None."""
    try:
        old_key = analysis._cache_key(p)
        f, mode = _open_tags(p)
        _backup_originals(con or db(), p, f, mode)
        skipped = []
        for k, v in _wanted(row, fields).items():
            try:
                _set_tag(f, mode, k, v)
            except (KeyError, ValueError):
                skipped.append(k)  # e.g. some formats have no standard key field
        f.save()
        _rekey_cache(old_key, p)
        return f"no {', '.join(skipped)} field in this format" if skipped else None
    except Exception as err:
        return str(err)


def _under(path: str, roots: list[str] | None) -> bool:
    if not roots:
        return True
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)


def _refresh_stat(con: sqlite3.Connection, path: str) -> None:
    st = Path(path).stat()
    con.execute("UPDATE tracks SET size=?, mtime=? WHERE path=?", (st.st_size, int(st.st_mtime), path))


GENRE_SEP = "; "


def _overlaps(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    return a in b or b in a


def multi_genre(r: dict, ratio: float = 0.7, max_genres: int = 3) -> str | None:
    """Top genre plus runner-ups scoring at least `ratio` of it, joined with "; ".
    A runner-up that overlaps an already chosen name (Trance vs Hard Trance) is skipped,
    so Smartlists can tell them apart."""
    if not r.get("genre"):
        return None
    top = r.get("genre_score") or 0
    chosen = [r["genre"]]
    for g, sc in ((r.get("genre_2"), r.get("genre_2_score")), (r.get("genre_3"), r.get("genre_3_score"))):
        if len(chosen) >= max_genres or not g or sc is None or top <= 0:
            continue
        if sc >= ratio * top and not any(_overlaps(g, c) for c in chosen):
            chosen.append(g)
    return GENRE_SEP.join(chosen)


def tag_from_db(roots: list[str] | None, fields: list[str], min_score: float = 0.0,
                use_my_genre: bool = False, dry_run: bool = False,
                multi: bool = False, multi_ratio: float = 0.7, max_genres: int = 3) -> dict:
    """Write already-scanned results into the files. No re-analysis."""
    con = db()
    roots_abs = [str(Path(os.path.expanduser(r)).resolve()) for r in roots] if roots else None
    rows = [dict(r) for r in con.execute("SELECT * FROM tracks WHERE error IS NULL OR error LIKE 'tags:%' ORDER BY path")]
    rows = [r for r in rows if _under(r["path"], roots_abs)]
    stats = {"written": 0, "unchanged": 0, "skipped_low_score": 0, "skipped_changed_since_scan": 0,
             "missing": 0, "errors": 0}
    examples = []
    genre_held = held("genre") if "genre" in fields else set()
    if genre_held:
        stats["genre_held"] = sum(r["path"] in genre_held for r in rows)  # their genre tag stays as it is
    for r in rows:
        p = Path(r["path"])
        if not p.exists():
            stats["missing"] += 1
            continue
        st = p.stat()
        if (st.st_size, int(st.st_mtime)) != (r["size"], r["mtime"]):
            stats["skipped_changed_since_scan"] += 1  # results may be stale; scan again first
            continue
        if use_my_genre and r.get("my_genre"):
            r["genre"] = r["my_genre"]
        elif "genre" in fields and (r["genre_score"] or 0) < min_score:
            stats["skipped_low_score"] += 1
            continue
        elif multi:
            if r.get("genre_2") and r.get("genre_2_score") is None:
                stats["single_genre_needs_rescan"] = stats.get("single_genre_needs_rescan", 0) + 1
            r["genre"] = multi_genre(r, multi_ratio, max_genres)
            if r["genre"] and GENRE_SEP in r["genre"]:
                stats["multi_genre_tracks"] = stats.get("multi_genre_tracks", 0) + 1
        wanted = _wanted(r, fields)
        if not wanted:
            stats["unchanged"] += 1
            continue
        try:
            f, mode = _open_tags(p)
            current = {k: _get_tag(f, mode, k) for k in wanted}
        except Exception:
            current = {}
        if all(current.get(k) == v for k, v in wanted.items()):
            stats["unchanged"] += 1
            continue
        if len(examples) < 15:
            examples.append({"file": p.name, **{k: f"{current.get(k) or '(empty)'} → {v}" for k, v in wanted.items()}})
        if dry_run:
            stats["written"] += 1
            continue
        err = write_tags(p, r, fields, con)
        if err and not err.startswith("no "):
            stats["errors"] += 1
            log(f"  {p.name}: {err}")
            continue
        _refresh_stat(con, r["path"])  # so the next scan doesn't treat the file as changed
        con.commit()
        stats["written"] += 1
        if stats["written"] % 100 == 0:
            log(f"  {stats['written']} files tagged...")
    if dry_run:
        stats["would_write"] = stats.pop("written")
    return {"dry_run": dry_run, "fields": fields, **stats, "examples": examples}


def untag(roots: list[str] | None, dry_run: bool = False) -> dict:
    """Put back the genre/BPM/key tags files had before this tool first changed them."""
    con = db()
    con.execute("CREATE TABLE IF NOT EXISTS original_tags (path TEXT PRIMARY KEY, genre TEXT, bpm TEXT, "
                "initialkey TEXT, saved_at TEXT)")
    roots_abs = [str(Path(os.path.expanduser(r)).resolve()) for r in roots] if roots else None
    rows = [dict(r) for r in con.execute("SELECT * FROM original_tags ORDER BY path") if _under(r["path"], roots_abs)]
    done, missing, errors, examples = 0, 0, 0, []
    for r in rows:
        p = Path(r["path"])
        if not p.exists():
            missing += 1
            continue
        if len(examples) < 15:
            examples.append({"file": p.name, "genre": r["genre"] or "(empty)"})
        if dry_run:
            done += 1
            continue
        try:
            old_key = analysis._cache_key(p)
            f, mode = _open_tags(p)
            for k in TAG_KEYS:
                try:
                    _set_tag(f, mode, k, r[k])
                except (KeyError, ValueError):
                    pass
            f.save()
            _rekey_cache(old_key, p)
            con.execute("DELETE FROM original_tags WHERE path=?", (r["path"],))
            if con.execute("SELECT 1 FROM tracks WHERE path=?", (r["path"],)).fetchone():
                _refresh_stat(con, r["path"])
            con.commit()
            done += 1
        except Exception as err:
            errors += 1
            log(f"  {p.name}: {err}")
    return {"dry_run": dry_run, ("would_restore" if dry_run else "restored"): done,
            "missing": missing, "errors": errors, "examples": examples}


# ----------------------------------------------------------------------------
# Scanning
# ----------------------------------------------------------------------------


def nfc(path: str | Path) -> Path:
    """macOS hands out accented file names decomposed (é = e + accent), Traktor stores them
    composed. Use the composed form everywhere so both refer to the same track (the Mac's file
    system opens either spelling). Linux file systems don't: there the composed form is used only
    when a file has that name."""
    composed = unicodedata.normalize("NFC", str(path))
    return Path(composed if sys.platform == "darwin" or os.path.exists(composed) else path)


def _drop_decomposed_duplicates(con: sqlite3.Connection) -> None:
    """Rows saved under the decomposed spelling of a path that also exists composed (older scans)."""
    paths = {r[0] for r in con.execute("SELECT path FROM tracks")}
    dupes = [p for p in paths if unicodedata.normalize("NFC", p) != p and unicodedata.normalize("NFC", p) in paths]
    if dupes:
        con.executemany("DELETE FROM tracks WHERE path = ?", [(p,) for p in dupes])
        con.commit()
        log(f"Removed {len(dupes)} duplicate entries (same file, accented name spelled differently)")


def sync_conflict(path: str | Path) -> bool:
    """Syncthing's '<name>.sync-conflict-<date>-<time>-<device>.mp3' copies: the same track twice."""
    return ".sync-conflict-" in Path(path).name


def _missing_for_good(path: str) -> bool:
    """The file is gone, and not just on a drive that isn't plugged in right now."""
    if os.path.exists(path):
        return False
    parts = Path(path).parts
    if len(parts) > 2 and parts[1] == "Volumes":  # macOS: another drive
        return os.path.isdir(os.path.join(*parts[:3]))
    # Linux: /media/<user>/<disk>, /run/media/<user>/<disk>, /mnt/<disk> (an unplugged disk's folder may stay, empty)
    n = {"media": 4, "run": 5, "mnt": 3}.get(parts[1] if len(parts) > 1 else "", 0)
    if n and sys.platform.startswith("linux") and len(parts) > n and (parts[1] != "run" or parts[2] == "media"):
        return os.path.ismount(os.path.join(*parts[:n]))
    return True


def find_audio(roots: list[str]) -> list[Path]:
    files = []
    for r in roots:
        root = Path(os.path.expanduser(r)).resolve()
        if root.is_file():
            files.append(nfc(root))
            continue
        if not root.is_dir():
            log(f"Skipping {root}: not found")
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.startswith("._") or name.startswith("."):
                    continue
                if os.path.splitext(name)[1].lower() in AUDIO_EXTS:
                    files.append(nfc(Path(dirpath) / name))
    return sorted(files)


def instruments_text(scores: dict | None) -> str | None:
    """Top 3 instruments plus vocals, as 'synthesizer 0.81; bass 0.55; piano 0.16; vocals 0.43'
    (vocals = share of the track with a voice)."""
    parts = [f"{k} {v:.2f}" for k, v in analysis.top_instruments(scores, n=3).items()]
    if scores and "vocal_share" in scores:
        parts.append(f"vocals {scores['vocal_share']:.2f}")
    return "; ".join(parts) or None


def to_row(p: Path, r: dict | None, err: str | None) -> dict:
    st = p.stat()
    row = {c: None for c in NAMES}
    row.update(path=str(p), folder=str(p.parent), filename=p.name, size=st.st_size,
               mtime=int(st.st_mtime), scanned_at=time.strftime("%Y-%m-%d %H:%M"), error=err)
    row.update(read_tags(p))
    if r:
        g = r.get("dj_genre") or []
        mine = r.get("my_genre") or []
        dur = r.get("duration", "0:00").split(":")
        row.update(
            type=r.get("type"),
            duration_sec=sum(int(x) * 60 ** i for i, x in enumerate(reversed(dur))),
            bpm=r.get("bpm"), bpm_confidence=r.get("bpm_confidence"),
            key=r.get("key"), camelot=r.get("camelot"), key_strength=r.get("key_strength"),
            lufs=r["loudness"]["integrated_lufs"], loudness_range=r["loudness"]["loudness_range_lu"],
            genre=g[0]["genre"] if g else None, genre_score=g[0]["score"] if g else None,
            genre_2=g[1]["genre"] if len(g) > 1 else None, genre_3=g[2]["genre"] if len(g) > 2 else None,
            genre_2_score=g[1]["score"] if len(g) > 1 else None, genre_3_score=g[2]["score"] if len(g) > 2 else None,
            color=(r.get("color") or {}).get("color"), color_position=(r.get("color") or {}).get("position"),
            color_confidence=(r.get("color") or {}).get("confidence"),
            **{f"mood_{m}": (r.get("mood") or {}).get(m) for m in analysis.MOODS},
            instruments=instruments_text({**(r.get("instruments") or {}),
                                          **({"vocal_share": r["vocal_share"]} if "vocal_share" in r else {})}),
            approachability=r.get("approachability"),
            styles="; ".join(f'{s["style"]} {s["score"]:.2f}' for s in (r.get("discogs_styles") or [])[:3]),
            my_genre=mine[0]["genre"] if mine else None,
            my_genre_confidence=mine[0]["confidence"] if mine else None,
        )
        if r.get("styles_error"):
            row["error"] = r["styles_error"]
    return row


def known_all(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute("SELECT path FROM tracks")]


def _scan_one(job):
    """Runs in a worker process. job: (path, Traktor's BPM or None)."""
    path, genre_bpm = job
    p = Path(path)
    try:
        return to_row(p, analysis.analyze_file(path, timeline=False, genre_bpm=genre_bpm), None)
    except Exception as err:
        try:
            return to_row(p, None, f"{type(err).__name__}: {err}")
        except Exception:
            return {"path": path, "error": str(err)}


def _worker_init():
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # let the parent handle Ctrl-C
    analysis.es()


# Come from Traktor, not the analysis; hype is a rank within the collection, recomputed after a scan
# (kept meanwhile so a stopped scan doesn't leave tracks without one).
KEEP_ON_RESCAN = ("traktor_color", "traktor_bpm", "traktor_camelot", "hype")


def save_row(con: sqlite3.Connection, row: dict) -> None:
    old = con.execute(f"SELECT {', '.join(KEEP_ON_RESCAN)} FROM tracks WHERE path=?", (row.get("path"),)).fetchone()
    if old:
        for c in KEEP_ON_RESCAN:
            if row.get(c) is None:
                row[c] = old[c]
    con.execute(
        f"INSERT OR REPLACE INTO tracks ({', '.join(NAMES)}) VALUES ({', '.join('?' * len(NAMES))})",
        [row.get(c) for c in NAMES],
    )
    con.commit()


def load_traktor_info(nml: str | None = None) -> dict[str, tuple[float | None, str | None]]:
    """{path: (BPM folded into our range, Camelot)} from Traktor, or {} without a Traktor collection."""
    from . import traktor

    try:
        info = traktor.track_info(nml)
    except (SystemExit, OSError) as err:
        log(f"(Traktor's BPM/key not used: {err})")
        return {}
    return {str(nfc(p)): (analysis.round_bpm(analysis.fold_bpm(b)) if b else None, c) for p, (b, c) in info.items()}


def store_traktor_info(con: sqlite3.Connection, info: dict) -> None:
    """Traktor's BPM/key on every row; tracks Traktor no longer lists (or lists without either) lose theirs,
    so a stale value doesn't keep winning in BPM_SQL / CAMELOT_SQL. Without a Traktor collection ({}),
    nothing changes."""
    if not info:
        return
    con.executemany("UPDATE tracks SET traktor_bpm=?, traktor_camelot=? WHERE path=?",
                    [(b, c, p) for p, (b, c) in info.items()])
    stale = [p for (p,) in con.execute("SELECT path FROM tracks WHERE traktor_bpm IS NOT NULL "
                                       "OR traktor_camelot IS NOT NULL") if p not in info]
    con.executemany("UPDATE tracks SET traktor_bpm=NULL, traktor_camelot=NULL WHERE path=?", [(p,) for p in stale])
    con.commit()


def scan(roots: list[str], workers: int | None = None, rescan: bool = False,
         tag_fields: list[str] | None = None, csv_out: Path | None = DEFAULT_CSV,
         traktor_nml: str | None = None, use_traktor: bool = False,
         exclude: list[str] | None = None, include_recordings: bool = False) -> dict:
    if not analysis.models_present():
        raise SystemExit("Style models missing. Run `uv run dj-track-analyzer setup` first.")
    analysis.warn_if_maest_missing()
    con = db()
    _drop_decomposed_duplicates(con)
    files = find_audio(roots) if roots else []
    if use_traktor:
        from . import traktor

        nml_path, tk_files, tk_missing = traktor.collection_tracks(traktor_nml)
        log(f"Traktor collection {nml_path}: {len(tk_files)} tracks found on disk"
            + (f", {tk_missing} listed but missing (moved or on a disconnected drive)" if tk_missing else ""))
        files = sorted(set(files) | set(tk_files))
    conflicts = [f for f in files if sync_conflict(f)]
    if conflicts:
        files = [f for f in files if not sync_conflict(f)]
        log(f"Skipping {len(conflicts)} Syncthing conflict copies (*.sync-conflict-*): the same tracks twice")
    old = [p for p in known_all(con) if sync_conflict(p)]
    if old:
        con.executemany("DELETE FROM tracks WHERE path = ?", [(x,) for x in old])
        con.commit()
        log(f"Removed {len(old)} earlier results for Syncthing conflict copies")
    # Leave out Traktor's own mix recordings, and any folders the user excluded.
    skip_dirs = [str(Path(os.path.expanduser(d)).resolve()) for d in (exclude or [])]
    if not include_recordings:
        from . import traktor

        skip_dirs += traktor.recording_dirs(traktor_nml)
    if skip_dirs:
        before = len(files)
        files = [f for f in files if not any(str(f) == d or str(f).startswith(d.rstrip("/") + "/") for d in skip_dirs)]
        if before - len(files):
            log(f"Skipping {before - len(files)} files in excluded folders (recordings, samples, --exclude): "
                + ", ".join(skip_dirs))
        # and drop results for those folders from earlier scans, so they don't show up in searches
        old = [p for p in known_all(con) if any(p.startswith(d.rstrip("/") + "/") for d in skip_dirs)]
        if old:
            con.executemany("DELETE FROM tracks WHERE path = ?", [(x,) for x in old])
            con.commit()
            log(f"Removed {len(old)} earlier results from excluded folders")
    # Files that failed last time are retried; everything else is skipped unless it changed.
    known = {r["path"]: (r["size"], r["mtime"]) for r in con.execute(
        "SELECT path, size, mtime FROM tracks WHERE error IS NULL OR error LIKE 'tags:%'")}
    todo = []
    for f in files:
        try:
            st = f.stat()
        except OSError:
            continue
        if rescan or known.get(str(f)) != (st.st_size, int(st.st_mtime)):
            todo.append(str(f))
    # Forget files that were deleted or moved out of the scanned folders.
    roots_abs = [str(Path(os.path.expanduser(r)).resolve()) for r in roots]
    present = {str(f) for f in files}
    gone = [path for path in known_all(con) if any(path.startswith(r.rstrip("/") + "/") or path == r for r in roots_abs)
            and path not in present]
    # tracks scanned before that no longer exist anywhere (a drive that isn't plugged in doesn't count)
    gone += [path for path in known_all(con) if path not in present and path not in gone and _missing_for_good(path)]
    if gone:
        con.executemany("DELETE FROM tracks WHERE path = ?", [(g,) for g in gone])
        con.commit()
        log(f"Removed {len(gone)} entries for files that no longer exist")
    log(f"{len(files)} audio files found, {len(files) - len(todo)} already scanned, {len(todo)} to analyze")
    tk_info = load_traktor_info(traktor_nml)
    store_traktor_info(con, tk_info)
    pruned = analysis.prune_cache(_missing_for_good, sync_conflict)  # before the workers start writing to the cache
    if pruned:
        log(f"Dropped {pruned} cache entries nothing can use any more (old versions, gone files, replaced keys)")
    if not todo:
        if csv_out:
            export_csv(csv_out)
        return summary(con)

    if workers is None:
        workers = max(1, min(5, (os.cpu_count() or 4) // 2))  # e.g. 5 on a 10-core M1 Pro
    workers = max(1, min(workers, len(todo)))
    log(f"Using {workers} worker{'s' if workers > 1 else ''}. Ctrl-C stops safely; run the same command to resume.\n")

    jobs = [(p, (tk_info.get(p) or (None, None))[0]) for p in todo]  # genres judged by Traktor's BPM if it has one
    t0, done, errors = time.time(), 0, 0
    pool = None
    try:
        if workers == 1:
            analysis.es()
            results = map(_scan_one, jobs)
        else:
            import multiprocessing as mp

            pool = mp.get_context("spawn").Pool(workers, initializer=_worker_init)
            results = pool.imap_unordered(_scan_one, jobs, chunksize=1)
        for row in results:
            done += 1
            if row.get("path") in tk_info:  # before write_tags: BPM/key tags use Traktor's values
                row["traktor_bpm"], row["traktor_camelot"] = tk_info[row["path"]]
            if tag_fields and not row.get("error") and row.get("genre"):
                terr = write_tags(Path(row["path"]), row, tag_fields, con)
                if terr:
                    row["error"] = f"tags: {terr}"
                st = Path(row["path"]).stat()  # tag writes change size/mtime; record the new ones
                row["size"], row["mtime"] = st.st_size, int(st.st_mtime)
            save_row(con, row)
            if row.get("error") and not str(row["error"]).startswith("tags:"):
                errors += 1
            rate = done / max(time.time() - t0, 1e-6)
            eta = (len(todo) - done) / rate if rate else 0
            name = Path(row["path"]).name
            genres = multi_genre(row) if row.get("genre") else None  # what `tag --multi` would write
            colour = row.get("color") or "-"
            if row.get("color_confidence") is not None:
                colour += f' ({row["color_confidence"]:.2f})'
            info = (f'{row.get("bpm") or "-":>6} {row.get("camelot") or "-":>3}  {colour:14s} '
                    f'{genres or row.get("error") or ""}')
            log(f"[{done}/{len(todo)}  ~{analysis.fmt_time(eta)} left] {name[:44]:44s} {info[:80]}")
    except KeyboardInterrupt:
        log(f"\nStopped. {done} tracks saved; run the same command to continue.")
        if pool:
            pool.terminate()
            pool = None
    finally:
        if pool:
            pool.close()
            pool.join()

    if done and con.execute("SELECT 1 FROM tracks WHERE hype IS NOT NULL LIMIT 1").fetchone():
        from . import hype

        try:  # ranks within the collection: new and changed tracks move everyone's
            hype.compute(con)
        except SystemExit as err:
            log(f"(hype not scored: {err})")
    if csv_out:
        export_csv(csv_out)
    out = summary(con)
    out["this_run"] = {"analyzed": done, "errors": errors, "minutes": round((time.time() - t0) / 60, 1)}
    return out


# ----------------------------------------------------------------------------
# Export, stats, search
# ----------------------------------------------------------------------------


def regenre(csv_out: Path | None = DEFAULT_CSV) -> dict:
    """Genres of every scanned track again from the cached style scores and the current dj_genres.json,
    judged by the BPM you mix with (as `scan` does). Seconds, no audio decoding. Files and Traktor
    only change when you run `tag` / `traktor` afterwards."""
    from collections import Counter

    con = db()
    rows = con.execute(f"SELECT path, genre, {BPM_SQL} AS b FROM tracks WHERE type='track' "
                       "AND (error IS NULL OR error LIKE 'tags:%')").fetchall()
    changed, missing = Counter(), 0
    for r in rows:
        p = Path(r["path"])
        hit = analysis.cache_get(p) if p.exists() else None
        if not hit:
            missing += 1
            continue
        m = analysis.maest_cached(p)
        g = analysis.dj_genre(analysis.label_scores(hit[2], m[0] if m else None), r["b"])
        new = g[0]["genre"] if g else None
        if new != r["genre"]:
            changed[f"{r['genre']} → {new}"] += 1
        con.execute("UPDATE tracks SET genre=?, genre_score=?, genre_2=?, genre_3=?, genre_2_score=?, genre_3_score=? "
                    "WHERE path=?", (new, g[0]["score"] if g else None, g[1]["genre"] if len(g) > 1 else None,
                                     g[2]["genre"] if len(g) > 2 else None, g[1]["score"] if len(g) > 1 else None,
                                     g[2]["score"] if len(g) > 2 else None, r["path"]))
    con.commit()
    if csv_out:
        export_csv(csv_out)
    return {"tracks": len(rows) - missing, "not_in_cache": missing, "top_genre_changed": sum(changed.values()),
            "changes": dict(changed.most_common())}


BPM_COLUMNS = {"bpm", "traktor_bpm", "source_bpm"}


def csv_value(col: str, v):
    """BPMs as whole numbers (SQLite's REAL columns would print 126.0)."""
    return analysis.round_bpm(v) if col in BPM_COLUMNS and v is not None else v


def export_csv(path: Path) -> Path:
    path = Path(os.path.expanduser(str(path)))
    con = db()
    rows = con.execute("SELECT * FROM tracks ORDER BY folder, filename").fetchall()
    cols = [c for c in NAMES if c not in ("size", "mtime")]
    gi = cols.index("genre")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols[:gi] + ["genres_multi"] + cols[gi:])  # what `tag --multi` would write
        for r in rows:
            vals = [csv_value(c, r[c]) for c in cols]
            w.writerow(vals[:gi] + [multi_genre(dict(r))] + vals[gi:])
    log(f"Spreadsheet: {path}")
    return path


def summary(con: sqlite3.Connection | None = None) -> dict:
    con = con or db()
    total = con.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    errors = con.execute("SELECT COUNT(*) FROM tracks WHERE error IS NOT NULL AND error NOT LIKE 'tags:%'").fetchone()[0]
    genres = con.execute(
        f"SELECT genre, COUNT(*) n, CAST(ROUND(AVG({BPM_SQL})) AS INTEGER) avg_bpm FROM tracks WHERE genre IS NOT NULL "
        "GROUP BY genre ORDER BY n DESC").fetchall()
    keys = con.execute(
        f"SELECT {CAMELOT_SQL} camelot, COUNT(*) n FROM tracks WHERE {CAMELOT_SQL} IS NOT NULL "
        "GROUP BY 1 ORDER BY n DESC LIMIT 8").fetchall()
    bpm = con.execute(
        f"SELECT CAST({BPM_SQL}/5 AS INT)*5 b, COUNT(*) n FROM tracks WHERE {BPM_SQL} IS NOT NULL "
        "GROUP BY b ORDER BY b").fetchall()
    last = con.execute("SELECT MAX(scanned_at) FROM tracks").fetchone()[0]
    cols = con.execute(
        "SELECT COALESCE(traktor_color, color) c, COUNT(*) n FROM tracks WHERE COALESCE(traktor_color, color) "
        "IS NOT NULL GROUP BY c").fetchall()
    order = {n: i for i, n in enumerate(analysis.COLOR_NAMES)}
    return {
        "tracks": total, "errors": errors, "last_scan": last,
        "genres": {r["genre"]: {"tracks": r["n"], "avg_bpm": r["avg_bpm"]} for r in genres},
        "top_camelot_keys": {r["camelot"]: r["n"] for r in keys},
        "bpm_histogram": {f'{r["b"]}-{r["b"] + 4}': r["n"] for r in bpm},
        "colours": {r["c"]: r["n"] for r in sorted(cols, key=lambda r: order.get(r["c"], 99))},
    }


def camelot_neighbors(code: str) -> list[str]:
    """Harmonic mixing matches: same key, ±1 on the wheel, relative major/minor."""
    code = code.strip().upper()
    num, letter = int(code[:-1]), code[-1]
    other = "B" if letter == "A" else "A"
    wrap = lambda n: (n - 1) % 12 + 1  # noqa: E731
    return [f"{num}{letter}", f"{wrap(num - 1)}{letter}", f"{wrap(num + 1)}{letter}", f"{num}{other}"]


SHOW = ["artist", "title", "filename", "bpm", "camelot", "key", "genre", "genre_2", "my_genre",
        "traktor_color", "color", "lufs", "duration_sec", "path"]
# bpm/camelot are shown as the user mixes with them (Traktor's where it has them)
SHOW_SQL = [f"CAST(ROUND({BPM_SQL}) AS INTEGER) AS bpm" if c == "bpm" else f"{CAMELOT_SQL} AS camelot" if c == "camelot" else c
            for c in SHOW]
# Traktor colour number for ordering: the user's own colour first, else the predicted position
COLOR_ORDER_SQL = ("COALESCE(CASE traktor_color " + " ".join(f"WHEN '{n}' THEN {i}" for i, n in
                   enumerate(analysis.COLOR_NAMES, 1)) + " END, color_position)")


def search(genre: str | None = None, bpm_min: float | None = None, bpm_max: float | None = None,
           camelot: str | None = None, harmonic: bool = False, text: str | None = None,
           track_type: str | None = "track", limit: int = 50, order: str = "bpm",
           color: str | None = None, exact_genre: str | None = None) -> dict:
    """genre: text anywhere in the genres, styles or genre tag ("trance" finds Hard Trance too).
    exact_genre: one of the track's genres is exactly this (as the genre Smartlists match)."""
    con = db()
    where, args = ["error IS NULL OR error LIKE 'tags:%'"], []
    if exact_genre:
        where.append("LOWER(?) IN (LOWER(genre), LOWER(genre_2), LOWER(my_genre))")
        args.append(exact_genre)
    if genre:
        where.append("(genre LIKE ? OR genre_2 LIKE ? OR my_genre LIKE ? OR styles LIKE ? OR tag_genre LIKE ?)")
        args += [f"%{genre}%"] * 5
    if bpm_min is not None:
        where.append(f"{BPM_SQL} >= ?"); args.append(bpm_min)
    if bpm_max is not None:
        where.append(f"{BPM_SQL} <= ?"); args.append(bpm_max)
    if camelot:
        keys = camelot_neighbors(camelot) if harmonic else [camelot.strip().upper()]
        where.append(f"{CAMELOT_SQL} IN ({','.join('?' * len(keys))})"); args += keys
    if text:
        where.append("(artist LIKE ? OR title LIKE ? OR filename LIKE ? OR folder LIKE ? OR album LIKE ?)")
        args += [f"%{text}%"] * 5
    if track_type:
        where.append("type = ?"); args.append(track_type)
    if color:  # the user's own Traktor colour when set, otherwise the predicted one
        where.append("LOWER(COALESCE(traktor_color, color)) = LOWER(?)"); args.append(color.strip())
    order_sql = {"bpm": BPM_SQL, "genre": f"genre, {BPM_SQL}", "key": f"{CAMELOT_SQL}, {BPM_SQL}",
                 "loudness": "lufs DESC", "artist": "artist, title", "recent": "scanned_at DESC",
                 "color": f"{COLOR_ORDER_SQL} IS NULL, {COLOR_ORDER_SQL}, {BPM_SQL}"}.get(order, BPM_SQL)
    sql = f"SELECT {', '.join(SHOW_SQL)} FROM tracks WHERE ({') AND ('.join(where)}) ORDER BY {order_sql}"
    count = con.execute(f"SELECT COUNT(*) FROM ({sql})", args).fetchone()[0]
    rows = con.execute(sql + " LIMIT ?", args + [limit]).fetchall()
    return {"matches": count, "showing": len(rows), "tracks": [{k: r[k] for k in SHOW if r[k] is not None} for r in rows]}


def find_one(query: str):
    con = db()
    q = os.path.expanduser(query)
    r = con.execute("SELECT * FROM tracks WHERE path = ?", (q,)).fetchone()
    if r:
        return r
    like = f"%{query}%"
    return con.execute(
        "SELECT * FROM tracks WHERE title LIKE ? OR filename LIKE ? OR (artist || ' - ' || title) LIKE ? "
        "ORDER BY LENGTH(filename) LIMIT 1", (like, like, like)).fetchone()


def mix_suggestions(query: str, bpm_tolerance_pct: float = 4.0, same_genre: bool = True, limit: int = 20) -> dict:
    seed = find_one(query)
    if not seed:
        raise ValueError(f"No scanned track matches '{query}'. Use search_collection to find it first.")
    seed_bpm, seed_cam = mix_bpm(seed), mix_camelot(seed)
    if seed_bpm is None or seed_cam is None:
        raise ValueError(f"{seed['filename']} has no BPM/key (maybe a mix or a failed analysis).")
    tol = seed_bpm * bpm_tolerance_pct / 100
    res = search(
        exact_genre=(seed["my_genre"] or seed["genre"]) if same_genre else None,
        bpm_min=seed_bpm - tol, bpm_max=seed_bpm + tol,
        camelot=seed_cam, harmonic=True, limit=limit + 1,
    )
    before = len(res["tracks"])
    res["tracks"] = [t for t in res["tracks"] if t["path"] != seed["path"]][:limit]
    if len(res["tracks"]) < before:
        res["matches"] -= 1
    res["showing"] = len(res["tracks"])
    res["seed"] = {k: seed[k] for k in SHOW if seed[k] is not None}
    res["seed"].update(bpm=analysis.round_bpm(seed_bpm), camelot=seed_cam)
    res["rule"] = (f"BPM within ±{bpm_tolerance_pct:g}%, Camelot {', '.join(camelot_neighbors(seed_cam))}"
                   + (f", genre {seed['my_genre'] or seed['genre']}" if same_genre else ""))
    return res


def main_scan(args) -> None:
    fields = [f.strip().lower() for f in args.write_tags.split(",")] if args.write_tags else None
    if fields:
        bad = set(fields) - {"genre", "bpm", "key", "camelot"}
        if bad:
            raise SystemExit(f"--write-tags accepts genre,bpm,key,camelot (got {', '.join(bad)})")
        if "key" in fields and "camelot" in fields:
            raise SystemExit("Pick either key or camelot for the key tag, not both.")
    use_traktor = args.traktor
    if not args.folders and not use_traktor:
        raise SystemExit("Tell scan what to analyze: one or more folders, and/or --traktor for every track "
                         "in your Traktor collection.\n  uv run dj-track-analyzer scan ~/Music/Collection\n"
                         "  uv run dj-track-analyzer scan --traktor")
    out = scan(args.folders, workers=args.workers, rescan=args.rescan, tag_fields=fields,
               csv_out=Path(args.csv) if args.csv else DEFAULT_CSV,
               traktor_nml=args.nml, use_traktor=use_traktor,
               exclude=args.exclude, include_recordings=args.include_recordings)
    print(json.dumps(out, indent=2, ensure_ascii=False))
