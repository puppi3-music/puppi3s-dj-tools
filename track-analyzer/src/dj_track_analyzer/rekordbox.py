"""Rekordbox's library (master.db), read through pyrekordbox: the same high-level calls as traktor.py, so
library.py can hand either one to the rest of the app. What the
database holds is in docs/notes/rekordbox.md. Writes (genre, colour, hype, the genre smart playlists) back up
master.db first and can be undone, like Traktor's.

The database is read once into plain rows (`load`), and everything else works on those rows, so the tests need
neither pyrekordbox nor an encrypted database."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
import sys
import unicodedata
from pathlib import Path

from . import collection
from .analysis import CAMELOT_MAJOR, CAMELOT_MINOR, HOME, PITCH, app_setting, log, save_app_setting
from .comments import HYPE_RE, with_hype

DB_SETTING = "rekordbox_database"  # in data/settings.json: chosen on the Setup tab
DB_NAME = "master.db"
CONTAINER = os.environ.get("TRACK_ANALYZER_CONTAINER") == "1"

# djmdPlaylist.Attribute
PLAYLIST, FOLDER, SMART = 0, 1, 4
# djmdCue.Kind: 0 = memory cue, hot cues A-H (4 is skipped)
HOT_CUE_KINDS = (1, 2, 3, 5, 6, 7, 8, 9)
# djmdColor IDs 1-8; 0 = none
COLORS = {1: "Pink", 2: "Red", 3: "Orange", 4: "Yellow", 5: "Green", 6: "Aqua", 7: "Blue", 8: "Purple"}
# Our 7-step scale (analysis.COLOR_NAMES: Red, Orange, Yellow, Green, Blue, Violet, Magenta) onto Rekordbox's
# colours: Violet = Purple, Magenta = Pink. Aqua is never written; read back, it counts as Blue.
OUR_TO_RB = {1: 2, 2: 3, 3: 4, 4: 5, 5: 7, 6: 8, 7: 1}
RB_TO_OUR = {rb: ours for ours, rb in OUR_TO_RB.items()} | {6: 5}
GENRE_FOLDER = "Genres (Track Analyzer)"
OWN_FOLDERS = {"Genres (Track Analyzer)", "Sets (Track Analyzer)", "Crates (Track Analyzer)"}


# ---- where the database is ------------------------------------------------------------------------------------

def default_dirs() -> list[Path]:
    """Rekordbox's own folder (holding master.db and share/) on this computer."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        return [Path(appdata) / "Pioneer" / "rekordbox"] if appdata else []
    return [Path.home() / "Library" / "Pioneer" / "rekordbox"]


def auto_database() -> Path | None:
    return next((d / DB_NAME for d in default_dirs() if (d / DB_NAME).is_file()), None)


def chosen_database() -> Path | None:
    p = app_setting(DB_SETTING)
    return Path(p) if p else None


def check_database(path: str) -> Path:
    """A path typed, pasted or picked as Rekordbox's database: master.db (or the rekordbox folder holding it)."""
    if not str(path or "").strip():
        raise ValueError("Give the path of Rekordbox's master.db.")
    p = Path(os.path.expanduser(path.strip().strip('"').strip("'").strip()))
    if p.is_dir():
        p = p / DB_NAME
    if not p.is_file():
        raise ValueError(f"{p} isn't there.")
    if p.name.lower() != DB_NAME:
        raise ValueError(f"{p.name} isn't Rekordbox's database: choose the file named master.db in the rekordbox "
                         "folder (not a backup).")
    return p.resolve()


def choose_database(path: str | None) -> Path | None:
    """Remember where Rekordbox's database is (None: find it automatically again). Returns what's used now."""
    save_app_setting(DB_SETTING, str(check_database(path)) if path else None)
    return chosen_database() or auto_database()


def find_collection(db: str | None = None) -> Path:
    """--db if given, else the one chosen on the Setup tab, else Rekordbox's default folder."""
    if db:
        p = Path(os.path.expanduser(db))
        if not p.exists():
            raise SystemExit(f"{p} does not exist")
        return p
    chosen = chosen_database()
    if chosen:
        if chosen.exists():
            return chosen
        raise SystemExit(f"The Rekordbox database chosen in Setup, {chosen}, isn't there any more. Choose it again on "
                         "the Setup tab, or let it be found automatically.")
    found = auto_database()
    if found:
        return found
    if sys.platform.startswith("linux"):
        raise SystemExit("Rekordbox doesn't run on Linux, so the Rekordbox steps are skipped. Everything else works "
                         "on your music folders.")
    looked = ", ".join(str(d) for d in default_dirs())
    raise SystemExit(f"Couldn't find Rekordbox's master.db (looked in {looked}). Choose it on the Setup tab (Browse…).")


# ---- is Rekordbox open ------------------------------------------------------------------------------------------

def running_state() -> bool | None:
    """True/False: Rekordbox is or isn't running. None: can't tell. Only `rekordbox` itself counts:
    `rekordboxAgent` runs on its own."""
    if CONTAINER:
        return None
    try:
        if sys.platform == "win32":
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace",
                                 timeout=30)
            names = [ln.split('","')[0].strip('"') for ln in out.stdout.splitlines() if ln.startswith('"')]
            if out.returncode or not names:
                return None
            return any(n.lower() == "rekordbox.exe" for n in names)
        out = subprocess.run(["pgrep", "-x", "rekordbox"], capture_output=True, text=True, timeout=30)
        return {0: True, 1: False}.get(out.returncode)
    except (OSError, subprocess.SubprocessError):
        return None


# ---- reading ----------------------------------------------------------------------------------------------------

def open_database(path: Path):
    from pyrekordbox import MasterDatabase  # only needed when Rekordbox is the chosen app

    return MasterDatabase(path=str(path), db_dir=str(path.parent))


def color_id(v) -> int:
    """djmdContent.ColorID (text in the database) as 0-8; 0 = no colour."""
    try:
        n = int(v or 0)
    except (TypeError, ValueError):
        return 0
    return n if n in COLORS else 0


def load(db) -> dict:
    """The rows this app uses, as plain dicts: {"tracks": [...], "playlists": [...], "songs": [...], "cues": [...]}.
    Deleted tracks (rb_local_deleted) are left out."""
    tracks = []
    for c in db.get_content():
        if getattr(c, "rb_local_deleted", 0):
            continue
        tracks.append({
            "id": str(c.ID), "path": c.FolderPath or "", "bpm": c.BPM,
            "key": c.Key.ScaleName if c.Key is not None else None,
            "genre": c.Genre.Name if c.Genre is not None else None,
            "color": color_id(c.ColorID), "comment": c.Commnt, "rating": c.Rating,
        })
    playlists = [{"id": str(p.ID), "name": p.Name or "", "attribute": p.Attribute, "parent": str(p.ParentID),
                  "seq": p.Seq or 0} for p in db.get_playlist()]
    songs = [{"playlist": str(s.PlaylistID), "track": str(s.ContentID), "no": s.TrackNo or 0}
             for s in db.get_playlist_songs()]
    cues = [{"track": str(q.ContentID), "in_ms": q.InMsec, "out_ms": q.OutMsec, "kind": q.Kind, "name": q.Comment}
            for q in db.get_cue()]
    return {"tracks": tracks, "playlists": playlists, "songs": songs, "cues": cues}


def read(db: str | None = None) -> dict:
    path = find_collection(db)
    master = open_database(path)
    try:
        return load(master)
    finally:
        master.close()


def norm_path(p: str) -> str:
    """Rekordbox's FolderPath as a path on this computer: NFC (Traktor's paths match after that), and on Windows
    with backslashes."""
    p = unicodedata.normalize("NFC", p or "")
    return p.replace("/", "\\") if sys.platform == "win32" and re.match(r"[A-Za-z]:/", p) else p


OPEN_KEY = re.compile(r"(1[0-2]|[1-9])\s*([mMdD])")
CAMELOT = re.compile(r"(1[0-2]|[1-9])\s*([aAbB])")
CLASSIC = re.compile(r"([A-Ga-g])\s*([#b♯♭]?)\s*(m|min|minor|maj|major)?", re.I)


def key_camelot(name: str | None) -> str | None:
    """djmdKey.ScaleName is free text in whatever notation was imported: classic (Am, C#, Dbm), Open Key (1m, 1d;
    also 1M / 1D) or Camelot (8A). As Camelot, or None."""
    s = (name or "").strip()
    if m := OPEN_KEY.fullmatch(s):
        n = (int(m.group(1)) + 6) % 12 + 1  # Open Key 1 = Camelot 8
        return f"{n}{'A' if m.group(2).lower() == 'm' else 'B'}"
    if m := CAMELOT.fullmatch(s):
        return f"{int(m.group(1))}{m.group(2).upper()}"
    if m := CLASSIC.fullmatch(s):
        acc = {"♯": "#", "♭": "b"}.get(m.group(2), m.group(2))
        pc = PITCH.get(m.group(1).upper() + acc)
        if pc is None:
            return None
        minor = (m.group(3) or "").lower() in ("m", "min", "minor")
        return f"{CAMELOT_MINOR[pc]}A" if minor else f"{CAMELOT_MAJOR[pc]}B"
    return None


def _live(rows: dict) -> dict[str, tuple[str, dict]]:
    """{track ID: (audio file on disk, its row)} for tracks whose file exists."""
    out = {}
    for t in rows["tracks"]:
        p = norm_path(t["path"])
        if p and os.path.isfile(p) and os.path.splitext(p)[1].lower() in collection.AUDIO_EXTS:
            out[t["id"]] = (p, t)
    return out


def collection_tracks(db: str | None = None, rows: dict | None = None) -> tuple[Path, list[Path], int]:
    """Every audio file in Rekordbox's library that exists on disk: (database path, files, number missing)."""
    path = find_collection(db) if rows is None else Path(db or "")
    rows = rows if rows is not None else read(db)
    live = _live(rows)
    missing = sum(1 for t in rows["tracks"] if t["id"] not in live and t["path"])
    return path, sorted({Path(p) for p, _ in live.values()}), missing


def track_info(db: str | None = None, rows: dict | None = None) -> dict[str, tuple[float | None, str | None]]:
    """{file path: (Rekordbox's BPM, its key as Camelot)}. BPM is stored × 100."""
    rows = rows if rows is not None else read(db)
    out = {}
    for t in rows["tracks"]:
        bpm = t["bpm"] / 100 if t["bpm"] and t["bpm"] > 0 else None
        cam = key_camelot(t["key"])
        if t["path"] and (bpm or cam):
            out[norm_path(t["path"])] = (bpm, cam)
    return out


def playlists(db: str | None = None, rows: dict | None = None) -> list[dict]:
    """Every playlist, for learning genres: [{"path": "A / B / Name", "name": "Name", "files": [...]}] in
    Rekordbox's order. This app's own folders, smart playlists and empty playlists are left out."""
    rows = rows if rows is not None else read(db)
    live = _live(rows)
    children: dict[str, list[dict]] = {}
    for p in rows["playlists"]:
        children.setdefault(p["parent"], []).append(p)
    songs: dict[str, list[dict]] = {}
    for s in rows["songs"]:
        songs.setdefault(s["playlist"], []).append(s)
    out = []

    def walk(parent: str, path: list[str]):
        for p in sorted(children.get(parent, []), key=lambda p: p["seq"]):
            if p["attribute"] == FOLDER and p["name"] not in OWN_FOLDERS:
                walk(p["id"], path + [p["name"]])
            elif p["attribute"] == PLAYLIST:
                ids = [s["track"] for s in sorted(songs.get(p["id"], []), key=lambda s: s["no"])]
                found = list(dict.fromkeys(live[i][0] for i in ids if i in live))
                if found:
                    out.append({"path": " / ".join(path + [p["name"]]), "name": p["name"], "files": found})

    walk("root", [])
    return out


def genre_field(db: str | None = None, rows: dict | None = None) -> dict:
    """Rekordbox's Genre field: {"genres": {genre: [files]}, "multi": n}; several genres in one ("A; B") skipped."""
    rows = rows if rows is not None else read(db)
    genres: dict[str, list[str]] = {}
    multi = 0
    for path, t in _live(rows).values():
        g = (t["genre"] or "").strip()
        if not g:
            continue
        if re.search(r"[;,]| / ", g):
            multi += 1
            continue
        genres.setdefault(g, []).append(path)
    return {"genres": {g: sorted(set(v)) for g, v in sorted(genres.items())}, "multi": multi}


def cues(db: str | None = None, rows: dict | None = None) -> dict[str, list[dict]]:
    """{file path: [{"ms", "loop_ms", "hot" (0-7 for A-H, None for a memory cue), "name"}]}, by position."""
    rows = rows if rows is not None else read(db)
    paths = {t["id"]: norm_path(t["path"]) for t in rows["tracks"] if t["path"]}
    out: dict[str, list[dict]] = {}
    for q in rows["cues"]:
        if q["track"] not in paths or q["in_ms"] is None:
            continue
        loop = q["out_ms"] - q["in_ms"] if q["out_ms"] is not None and q["out_ms"] > q["in_ms"] else None
        hot = HOT_CUE_KINDS.index(q["kind"]) if q["kind"] in HOT_CUE_KINDS else None
        out.setdefault(paths[q["track"]], []).append(
            {"ms": q["in_ms"], "loop_ms": loop, "hot": hot, "name": q["name"] or ""})
    for v in out.values():
        v.sort(key=lambda c: (c["ms"], c["hot"] is None))
    return out


def colors(db: str | None = None, rows: dict | None = None) -> tuple[Path, dict[str, int], int]:
    """Like colors.read_traktor_colors: (database, {file path: colour 1-7 on our scale}, coloured tracks)."""
    path = find_collection(db) if rows is None else Path(db or "")
    rows = rows if rows is not None else read(db)
    out, n = {}, 0
    for t in rows["tracks"]:
        c = RB_TO_OUR.get(t["color"] or 0)
        if c and t["path"]:
            n += 1
            out[norm_path(t["path"])] = c
    return path, out, n


# ---- writing: Rekordbox closed, a backup first, undo --------------------------------------------------------------

BACKUPS = HOME / "rekordbox-backups"
KEEP_BACKUPS = 10
# Saved with master.db when there: SQLite's journal files, and the playlist file pyrekordbox keeps in step.
SIDE_FILES = ("master.db-wal", "master.db-shm", "masterPlaylists6.xml")
WRITTEN = "written-sha1"  # in each backup: master.db as we left it, so undo can tell whether Rekordbox changed it
SOURCE = "source"  # in each backup: which master.db it's of


def check_closed(dry_run: bool = False, what: str = "these changes") -> None:
    """Refuse to write master.db while Rekordbox is open (it holds the database and would overwrite or clash with
    our changes). When we can't tell, ask, the same way as for Traktor."""
    from . import traktor

    if dry_run:
        return
    state = running_state()
    if state:
        raise SystemExit(f"Rekordbox is running. Quit Rekordbox first, then try {what} again.")
    if state is False or traktor.closed_confirmed():
        return
    why = ("This runs in a container, which can't see whether Rekordbox is open." if CONTAINER else
           f"Couldn't check whether Rekordbox is open ({'tasklist' if sys.platform == 'win32' else 'pgrep'} failed).")
    raise traktor.ConfirmClosed(why, "Rekordbox")


def _sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _backups(path: Path) -> list[Path]:
    """Backups of this master.db, oldest first (undone ones left out)."""
    if not BACKUPS.is_dir():
        return []
    out = [b for b in BACKUPS.iterdir() if b.is_dir() and not b.name.endswith(".restored")
           and (b / SOURCE).is_file() and (b / SOURCE).read_text().strip() == str(path.resolve())]
    return sorted(out, key=lambda b: b.name)


def backup(path: Path) -> Path:
    """Copy master.db (and its side files) into data/rekordbox-backups/<time>/, keeping the last 10."""
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest, n = BACKUPS / stamp, 1
    while dest.exists() or dest.with_name(dest.name + ".restored").exists():  # two writes within a second
        n += 1
        dest = BACKUPS / f"{stamp}-{n}"
    dest.mkdir()
    for name in (DB_NAME, *SIDE_FILES):
        if (path.parent / name).is_file():
            shutil.copy2(path.parent / name, dest / name)
    (dest / SOURCE).write_text(str(path.resolve()))
    for old in sorted((b for b in BACKUPS.iterdir() if b.is_dir()), key=lambda b: b.name)[:-KEEP_BACKUPS]:
        shutil.rmtree(old)
    return dest


def _write(db: str | None, dry_run: bool, change, stats: dict, what: str = "these changes") -> dict:
    """Open master.db, let change(database) edit rows, fill stats and return whether it changed anything, then
    commit after a backup (or roll back, for a dry run)."""
    path = find_collection(db)
    check_closed(dry_run, what)
    stats.update(collection=str(path), dry_run=dry_run)
    saved = None if dry_run else backup(path)  # before opening: the copy is of master.db as it is on disk
    master = open_database(path)
    master.registry.clear_buffer()  # pyrekordbox keeps it per class, not per database
    try:
        stats["changed"] = bool(change(master))
        if dry_run or not stats["changed"]:
            master.session.rollback()
        else:
            master.commit()
    finally:
        master.registry.clear_buffer()
        master.close()
    if saved is None:
        return stats
    if not stats["changed"]:
        shutil.rmtree(saved)
        return stats
    (saved / WRITTEN).write_text(_sha1(path))
    stats["backup"] = str(saved)
    log(f"Updated {path}\nBackup: {saved}")
    return stats


def undo(db: str | None = None, force: bool = False) -> dict:
    """Put back master.db from before our last change. Refuses (unless force) when Rekordbox changed it since we
    wrote it: restoring would also throw away what Rekordbox saved since (plays, cues, new tracks, your edits)."""
    path = find_collection(db)
    check_closed(what="the undo")
    backups = _backups(path)
    if not backups:
        raise SystemExit(f"No Puppi3's Track Analyzer backups of {path} in {BACKUPS}")
    latest = backups[-1]
    mark = latest / WRITTEN
    if not force:
        if not mark.exists():
            raise SystemExit(f"Can't check whether Rekordbox changed {path.name} since backup {latest.name} was made. "
                             "Restoring would lose anything Rekordbox saved since then. Run again with --force to "
                             "restore anyway.")
        if mark.read_text().strip() != _sha1(path):
            raise SystemExit(f"{path.name} has changed since Puppi3's Track Analyzer wrote it (Rekordbox saved plays, "
                             f"cues or edits). Restoring backup {latest.name} would lose those. Run again with --force "
                             "to restore anyway.")
    for name in (DB_NAME, *SIDE_FILES):
        if (latest / name).is_file():
            shutil.copy2(latest / name, path.parent / name)
        elif name != "masterPlaylists6.xml" and (path.parent / name).exists():  # a journal that wasn't there before
            (path.parent / name).unlink()
    latest.rename(latest.with_name(latest.name + ".restored"))
    return {"restored_from": str(latest), "collection": str(path)}


def latest_backup(db: str | None = None) -> str | None:
    """The Setup tab's "last backup" name."""
    try:
        b = _backups(find_collection(db))
    except SystemExit:
        return None
    return b[-1].name if b else None


def _content_by_path(master) -> dict[str, object]:
    """{file path on this computer: djmdContent row} for tracks not deleted."""
    return {norm_path(c.FolderPath): c for c in master.get_content()
            if c.FolderPath and not getattr(c, "rb_local_deleted", 0)}


def _genre_row(master, name: str, made: dict):
    if name not in made:
        made[name] = master.get_genre(Name=name).first() or master.add_genre(name)
    return made[name]


def genre_conditions(genre: str, universe: list[str]) -> list[tuple[int, str]]:
    """The smart playlist's rules: Genre contains this name and none of the longer names that contain it ('Trance'
    leaves out 'Hard Trance'). Rekordbox takes any character, so no query_term shortening as for Traktor."""
    from pyrekordbox.masterdb.smartlist import Operator

    out = [(int(Operator.CONTAINS), genre)]
    out += [(int(Operator.NOT_CONTAINS), o) for o in sorted(universe, key=str.lower)
            if o.lower() != genre.lower() and genre.lower() in o.lower()]
    return out


def _smart_folder(master, genres: list[str]) -> str | None:
    """Make the 'Genres (Track Analyzer)' folder hold one smart playlist per genre. None: already so."""
    from pyrekordbox.masterdb.smartlist import LogicalOperator, Property, SmartList

    from . import analysis

    universe = sorted(set(genres) | set(analysis.dj_rules()))
    want = {g: genre_conditions(g, universe) for g in genres}
    old = [p for p in master.get_playlist() if p.Name == GENRE_FOLDER and p.Attribute == FOLDER
           and str(p.ParentID) == "root"]
    if len(old) == 1:
        have = {}
        for p in master.get_playlist(ParentID=old[0].ID):
            sl = SmartList()
            try:
                sl.parse(p.SmartList or "")
                have[p.Name] = [(int(c.operator), str(c.value_left)) for c in sl.conditions]
            except Exception:
                have[p.Name] = None
        if have == want:
            return None
    for p in old:
        master.delete_playlist(p)
    folder = master.create_playlist_folder(GENRE_FOLDER)
    for g in sorted(genres, key=str.lower):
        sl = SmartList(logical_operator=LogicalOperator.ALL)
        for op, value in want[g]:
            sl.add_condition(Property.GENRE, op, value)
        master.create_smart_playlist(g, sl, parent=folder)
    return "replaced" if old else "added"


def sync(db: str | None = None, dry_run: bool = False, update_genres: bool = True, make_smartlists: bool = True,
         colors: bool = True, overwrite_colors: bool = False, color_min_confidence: float = 0.0,
         genres_from_analysis: bool = False) -> dict:
    """traktor.sync for Rekordbox: genres (from the files' tags, or the analysis), predicted colours for tracks
    without one, and a smart playlist per genre."""
    from . import analysis, collection, traktor

    con = collection.db()
    scanned = {r["path"] for r in con.execute("SELECT path FROM tracks")}
    if not scanned:
        raise SystemExit("Nothing scanned yet. Run `uv run dj-track-analyzer scan <music folder>` and `tag` first.")
    analysed: dict[str, str] = {}
    if genres_from_analysis:
        hand = {p: f["genre"] for p, f in collection.manual().items() if "genre" in f}
        for r in con.execute("SELECT * FROM tracks WHERE error IS NULL OR error LIKE 'tags:%'"):
            if (g := hand.get(r["path"]) or r["my_genre"] or collection.multi_genre(dict(r))):
                analysed[r["path"]] = g
    genre_of = analysed.get if genres_from_analysis else traktor.file_genre
    predicted = {}
    if colors:
        for r in con.execute("SELECT path, color, color_confidence FROM tracks WHERE color IS NOT NULL"):
            if r["color"] in analysis.COLOR_NAMES and (r["color_confidence"] or 0) >= color_min_confidence:
                predicted[r["path"]] = analysis.COLOR_NAMES.index(r["color"]) + 1
    stats = {"rekordbox_tracks": 0, "matched_scanned_tracks": 0}
    if update_genres:
        stats.update(genres_updated=0, already_up_to_date=0)
        stats["no_genre_from_analysis" if genres_from_analysis else "no_genre_tag_in_file"] = 0
        if genres_from_analysis:
            stats["genres_from"] = "the analysis (files' tags left as they are)"
    if colors:
        stats.update(colours_set=0, colours_kept=0, no_colour_prediction=0)
    examples, genres_seen, app_colors, guessed = [], set(), {}, {}
    genre_held, colour_held = collection.held("genre"), collection.held("colour")

    def change(master):
        made: dict = {}
        how = None
        rows = _content_by_path(master)
        stats["rekordbox_tracks"] = len(rows)
        for p, c in rows.items():
            if p not in scanned:
                continue
            stats["matched_scanned_tracks"] += 1
            cur_g = c.Genre.Name if c.Genre is not None else None
            if update_genres and p in genre_held:
                stats["genre_held"] = stats.get("genre_held", 0) + 1
                if cur_g:
                    genres_seen.update(x.strip() for x in cur_g.split(";") if x.strip())
            elif update_genres:
                g = genre_of(p)
                if not g:
                    stats["no_genre_from_analysis" if genres_from_analysis else "no_genre_tag_in_file"] += 1
                else:
                    genres_seen.update(x.strip() for x in g.split(";") if x.strip())
                    if cur_g == g:
                        stats["already_up_to_date"] += 1
                    else:
                        c.Genre = _genre_row(master, g, made)
                        stats["genres_updated"] += 1
                        if len(examples) < 15:
                            examples.append(f"{Path(p).name}: {cur_g or '(empty)'} → {g}")
            elif make_smartlists:
                g = genre_of(p) if genres_from_analysis or os.path.exists(p) else None
                if g:
                    genres_seen.update(x.strip() for x in g.split(";") if x.strip())
            if colors:
                cur = RB_TO_OUR.get(color_id(c.ColorID))
                if cur:
                    app_colors[p] = cur
                want = predicted.get(p)
                if p in colour_held:
                    stats["colour_held"] = stats.get("colour_held", 0) + 1
                elif want is None:
                    stats["no_colour_prediction"] += not cur
                elif cur and not overwrite_colors:
                    stats["colours_kept"] += 1  # your own colour always wins
                elif cur != want:
                    c.ColorID = str(OUR_TO_RB[want])
                    stats["colours_set"] += 1
                    app_colors[p] = guessed[p] = want
                    if len(examples) < 30:
                        was = analysis.COLOR_NAMES[cur - 1] if cur else "no colour"
                        examples.append(f"{Path(p).name}: {was} → {analysis.COLOR_NAMES[want - 1]}")
        if make_smartlists:
            known = {g.lower(): g for g in traktor.known_genres()}
            other = sorted(g for g in genres_seen if g.lower() not in known)
            found = sorted({known[g.lower()] for g in genres_seen if g.lower() in known}, key=str.lower)
            if other:
                stats["genre_tags_without_smartlist"] = len(other)
            stats["smartlists"] = found
            if found:
                how = _smart_folder(master, found)
                stats["smartlist_folder"] = f"{GENRE_FOLDER} ({how or 'already up to date'})"
            else:
                stats["note"] = "No genre tags found in the scanned files yet. Run `uv run dj-track-analyzer tag` first."
        return bool(stats.get("genres_updated") or stats.get("colours_set") or how)

    out = _write(db, dry_run, change, stats, "the genres and colours")
    out["examples"] = examples
    if colors and not dry_run:  # the colours each track carries in the DJ app (training reads these)
        con.executemany("UPDATE tracks SET traktor_color=? WHERE path=?",
                        [(analysis.COLOR_NAMES[c - 1], p) for p, c in app_colors.items()])
        con.commit()
    if out.get("changed") and not dry_run:
        collection.add_guesses(guessed)
    return out


def set_colors(changes: dict[str, tuple[int, int]], db: str | None = None, dry_run: bool = False) -> dict:
    """traktor.set_colors for Rekordbox: {path: (colour it must have now, new colour)}, on our 1-7 scale."""
    from . import analysis, collection

    stats = {"colours_changed": 0, "skipped_changed_since": 0}
    examples, done, changed = [], set(), []

    def change(master):
        for p, c in _content_by_path(master).items():
            if p not in changes:
                continue
            expect, want = changes[p]
            done.add(p)
            if RB_TO_OUR.get(color_id(c.ColorID)) != expect:
                stats["skipped_changed_since"] += 1
                continue
            c.ColorID = str(OUR_TO_RB[want])
            stats["colours_changed"] += 1
            changed.append(p)
            examples.append(f"{Path(p).name}: {analysis.COLOR_NAMES[expect - 1]} → {analysis.COLOR_NAMES[want - 1]}")
        return changed

    out = _write(db, dry_run, change, stats, "the colours")
    out["not_in_collection"] = len(changes) - len(done)
    out["changes"] = examples
    if out.get("changed") and not dry_run:
        collection.accept_guesses(changed)
    return out


def write_hype(hype: dict[str, int], db: str | None = None, dry_run: bool = False, remove: bool = False,
               comment2_only: bool = False, force: bool = False) -> dict:
    """traktor.write_hype for Rekordbox: 'Hype NN' at the end of Comments, after any text already there
    (remove=True takes it out again). Rekordbox has no Comment 2, so comment2_only changes nothing."""
    stats = {"comment_updated": 0, "already_up_to_date": 0, "kept_your_text": 0}
    examples = []

    def change(master):
        if comment2_only:
            return False
        for p, c in _content_by_path(master).items():
            want = None if remove else hype.get(p)
            if want is None and not remove:
                continue
            cur = c.Commnt or ""
            new = with_hype(cur, want)
            if new == cur:
                stats["already_up_to_date"] += want is not None
                continue
            c.Commnt = new
            stats["comment_updated"] += 1
            stats["kept_your_text"] += bool(HYPE_RE.sub("", cur).strip())
            if len(examples) < 15:
                examples.append(f"{Path(p).name}: {cur or '(empty)'} → {new or '(empty)'}")
        return stats["comment_updated"]

    out = _write(db, dry_run, change, stats, "the hype")
    out["examples"] = examples
    return out
