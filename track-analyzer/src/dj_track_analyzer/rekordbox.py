"""Rekordbox's library (master.db), read through pyrekordbox: the same high-level calls as traktor.py, so
library.py can hand either one to the rest of the app. What the
database holds is in docs/notes/rekordbox.md. Writes (genre, colour, hype, the genre smart playlists) back up
master.db first and can be undone, like Traktor's.

The database is read once into plain rows (`load`), and everything else works on those rows, so the tests need
neither pyrekordbox nor an encrypted database."""
from __future__ import annotations

import hashlib
import json
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
# Hot cue colours (djmdCue.ColorTableIndex, Rekordbox's hot-cue palette; 0 = none, which Rekordbox shows in its own
# default colour): what a library's own hot cues use, matching Traktor's blue cue points and green loops
CUE_COLOR_POINT, CUE_COLOR_LOOP = 1, 21
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


def close_database(master) -> None:
    """Close the session and the engine: pyrekordbox's close() leaves the engine's connection open, and until that
    goes, a commit may not have reached master.db itself (so its sha1, the undo mark, would still be the old one)."""
    try:
        master.close()
    finally:
        master.engine.dispose()


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
            "color": color_id(c.ColorID), "comment": c.Commnt, "rating": c.Rating, "size": c.FileSize,
            "plays": int(p) if str(p := getattr(c, "DJPlayCount", None) or 0).isdigit() else 0,
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
        close_database(master)


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
    return path, sorted({Path(p) for p, _ in live.values() if not in_sampler(p)}), missing


SAMPLER = Path.home() / "Music" / "rekordbox" / "Sampler"  # its sampler's WAVs (bundled kits, our loops): not tracks


def in_sampler(p: str) -> bool:
    return Path(p).is_relative_to(SAMPLER)


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


# ---- beat grids and cues in Traktor's shape (cues.py, gridfix.py) ---------------------------------------------

MP3_FRAME = 1152  # samples: for MP3s Rekordbox's timeline runs about one frame behind Traktor's (notes/rekordbox.md)


def shift_ms(path: str, sample_rate) -> float:
    """Milliseconds to add to a Rekordbox time to get Traktor's (the timeline cues.py measures on)."""
    return MP3_FRAME * 1000 / (sample_rate or 44100) if path.lower().endswith(".mp3") else 0.0


def first_downbeat(master, content) -> float | None:
    """Seconds of the first beat 1 in the track's ANLZ grid (PQTZ in the .DAT file); None when there's none."""
    try:
        anlz = master.read_anlz_file(content, "DAT")
        beats, _, times = anlz.get("PQTZ") if anlz is not None else ((), (), ())
    except Exception:
        return None
    return next((float(t) for b, t in zip(beats, times) if int(b) == 1), None)


def _slot(q) -> int:
    return HOT_CUE_KINDS.index(q.Kind) if q.Kind in HOT_CUE_KINDS else -1


def _is_loop(q) -> bool:
    return q.OutMsec is not None and q.InMsec is not None and q.OutMsec > q.InMsec


def _grids(master, ungridded: bool = False) -> dict[str, tuple[object, dict]]:
    out: dict[str, tuple[object, dict]] = {}
    by_id = {}
    for c in master.get_content():
        if getattr(c, "rb_local_deleted", 0) or not c.FolderPath:
            continue
        t0 = first_downbeat(master, c) if c.BPM and c.BPM > 0 else None
        if t0 is None and not ungridded:
            continue
        p = norm_path(c.FolderPath)
        sh = shift_ms(p, c.SampleRate)
        if t0 is None:  # not analysed yet (e.g. just added by a sync): its cues still have a place in time
            d = {"bpm": (c.BPM or 0) / 100, "grid_ms": None, "nogrid": True, "cues": [], "all_cues": [], "marks": [], "shift_ms": sh}
        else:
            d = {"bpm": c.BPM / 100, "grid_ms": t0 * 1000 + sh, "cues": [], "all_cues": [], "marks": [], "shift_ms": sh}
        out[p] = by_id[str(c.ID)] = (c, d)
    for q in master.get_cue():
        hit = by_id.get(str(q.ContentID))
        if hit is None or q.InMsec is None:
            continue
        d = hit[1]
        ms, loop = q.InMsec + d["shift_ms"], _is_loop(q)
        kind = "loop" if loop else "cue"
        d["cues"].append({"ms": ms, "type": kind, "hotcue": _slot(q), "name": q.Comment or None,
                          "bars": round((q.OutMsec - q.InMsec) / (60000 / d["bpm"]) / 4, 2) if loop and d["bpm"] else None})
        d["all_cues"].append({"ms": ms, "type": kind, "hotcue": _slot(q)})
    for _, d in out.values():
        d["cues"].sort(key=lambda c: c["ms"])
    return out


def grids(db: str | None = None, ungridded: bool = False) -> dict[str, dict]:
    """cues.grids() for Rekordbox: {file path: {bpm, grid_ms, cues, all_cues, marks}}, times on Traktor's
    timeline. grid_ms is the first downbeat of Rekordbox's grid, bpm its track BPM (a grid with several tempos is
    read as one). Memory cues have hotcue -1. With ungridded, also tracks with no grid yet (grid_ms None, nogrid)."""
    master = open_database(find_collection(db))
    try:
        return {p: d for p, (_, d) in _grids(master, ungridded).items()}
    finally:
        close_database(master)


def write_cues(paths, plan_for, db: str | None = None, dry_run: bool = False, stats: dict | None = None) -> dict:
    """Apply approved cue plans as djmdCue rows. plan_for(path, grid entry) returns (moves, adds, renumbers,
    removes) as cues.resolve gives them (Traktor time, slots 0-7), or None to leave the track alone."""
    import uuid

    from pyrekordbox.masterdb import models

    stats = {} if stats is None else stats
    want = set(paths)

    def change(master) -> bool:
        changed = False
        for p, (c, d) in _grids(master, ungridded=True).items():
            if p not in want:
                continue
            got = plan_for(p, d)
            if not got:
                continue
            moves, adds, renum, removes = got
            sh, rows = d["shift_ms"], list(master.get_cue(ContentID=c.ID))

            def find(slot, ms, kind=None):
                return next(q for q in rows if _slot(q) == slot and abs(q.InMsec + sh - ms) < 1
                            and (kind is None or ("loop" if _is_loop(q) else "cue") == kind))

            for r in removes:
                q = find(r["hotcue"], r["ms"], r["type"])
                rows.remove(q)
                master.delete(q)
            for mv in moves:
                q = find(mv["hotcue"], mv["from_ms"])
                length = q.OutMsec - q.InMsec if _is_loop(q) else None
                q.InMsec = round(mv["to_ms"] - sh)
                q.CueMicrosec = q.InMsec * 1000
                if length is not None:
                    q.OutMsec = q.InMsec + length
            for r, q in [(r, find(r["hotcue"], r["ms"])) for r in renum]:  # found all first: slots swap
                q.Kind = HOT_CUE_KINDS[r["to"]] if r["to"] >= 0 else 0
            for a, slot in adds:
                in_ms = round(a["ms"] - sh)
                loop = a["type"] == "loop" and a["len_ms"] > 0
                master.add(models.DjmdCue.create(
                    ID=str(master.generate_unused_id(models.DjmdCue)), ContentID=c.ID, ContentUUID=c.UUID,
                    UUID=str(uuid.uuid4()), InMsec=in_ms, OutMsec=in_ms + round(a["len_ms"]) if loop else -1,
                    Kind=HOT_CUE_KINDS[slot] if slot >= 0 else 0, Comment=a["name"] or "", Color=-1,
                    ColorTableIndex=CUE_COLOR_LOOP if loop else CUE_COLOR_POINT if slot >= 0 else 0, ActiveLoop=0, BeatLoopSize=-1, CueMicrosec=in_ms * 1000,
                    InFrame=0, InMpegFrame=0, InMpegAbs=0, OutFrame=0, OutMpegFrame=0, OutMpegAbs=0))
                master.flush()
            changed = changed or bool(moves or adds or renum or removes)
        return changed

    return _write(db, dry_run, change, stats, "the cues")


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
        close_database(master)
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
    anlz = latest / ANLZ_BACKUP  # beat grids written into ANLZ files (write_grids): put back with master.db
    for name, orig in (json.loads((anlz / "files.json").read_text()).items() if (anlz / "files.json").is_file() else ()):
        shutil.copy2(anlz / name, orig)
    for name in (DB_NAME, *SIDE_FILES):
        if (latest / name).is_file():
            shutil.copy2(latest / name, path.parent / name)
        elif name != "masterPlaylists6.xml" and (path.parent / name).exists():  # a journal that wasn't there before
            (path.parent / name).unlink()
    latest.rename(latest.with_name(latest.name + ".restored"))
    return {"restored_from": str(latest), "collection": str(path)}


ANLZ_BACKUP = "anlz"  # in a master.db backup: the ANLZ files write_grids changed, and files.json {copy: original}


def _grid_entries(t0: float, bpm: float, length_s: float) -> list[dict]:
    """PQTZ beats for one steady tempo: beat 1 at t0 (seconds on Rekordbox's timeline), from the first beat at or
    after the start to the end of the track."""
    from math import ceil

    beat = 60 / bpm
    k = ceil(-t0 / beat - 1e-9)
    out = []
    while (t := t0 + k * beat) <= length_s:
        out.append({"beat": k % 4 + 1, "tempo": round(bpm * 100), "time": round(t * 1000)})
        k += 1
    return out


def _same_grid(rb: dict, tk: dict) -> bool:
    """Rekordbox's grid (one tempo) is Traktor's: same BPM, and beat 1 lands within 1 ms of Traktor's (any bar)."""
    if abs(rb["bpm"] - tk["bpm"]) > 0.005:
        return False
    bar = 4 * 60000 / tk["bpm"]
    d = (rb["grid_ms"] - tk["grid_ms"]) % bar
    return min(d, bar - d) < 1


def write_grids(tk: dict[str, dict], db: str | None = None, dry_run: bool = False, stats: dict | None = None) -> dict:
    """Give Rekordbox Traktor's beat grids: tk = {path: {bpm, grid_ms}} (Traktor's timeline). Only for tracks
    Rekordbox has analysed (it has the ANLZ files): their PQTZ grid is rewritten as one steady tempo with beat 1
    on Traktor's grid marker, djmdContent.BPM set to match, and the extended PQT2 grid (.EXT) taken out so the two
    can't disagree (Rekordbox makes it again when it analyses or exports). The ANLZ files go into the master.db
    backup first, so undo puts both back. A track with no ANLZ files yet is listed, not made up."""
    stats = {} if stats is None else stats
    stats.update(tracks=0, not_analysed=[], changes=[])
    files: list[tuple[Path, bytes]] = []

    def change(master) -> bool:
        have = _grids(master, ungridded=True)
        for p, t in tk.items():
            hit = have.get(p)
            if hit is None or not t.get("bpm") or t.get("grid_ms") is None:
                continue
            c, d = hit
            if not d.get("nogrid") and _same_grid(d, t):
                continue
            got = _anlz_grid(master, c)
            if got is None:
                stats["not_analysed"].append(Path(p).name)
                continue
            _set_grid(got, (t["grid_ms"] - d["shift_ms"]) / 1000, t["bpm"], c.Length or 0, files)
            c.BPM = round(t["bpm"] * 100)
            stats["tracks"] += 1
            stats["changes"].append(f"{Path(p).name}: {d['bpm']:g} → {t['bpm']:g} BPM" if d.get("bpm") and abs(d["bpm"] - t["bpm"]) > 0.005
                                     else f"{Path(p).name}: beat 1 moved to Traktor's")
        return bool(files)

    out = _write(db, dry_run, change, stats, "the beat grids")
    if out["not_analysed"]:
        out["changes"].append(f"{len(out['not_analysed'])} track(s) not analysed in Rekordbox yet (analyse them there, "
                              f"then sync again): " + ", ".join(out["not_analysed"][:5]))
    if not dry_run:
        _save_anlz(out, files)
    return out


def _anlz_grid(master, c):
    """(.DAT path, parsed file, its PQTZ tag) for an analysed track, else None."""
    from pyrekordbox.anlz import AnlzFile

    try:  # pyrekordbox raises when the ANLZ folder isn't there
        dat = Path(master.get_anlz_path(c, "DAT") or "") if c.AnalysisDataPath else None
        a = AnlzFile.parse_file(dat) if dat and dat.is_file() else None
        q = a.get_tag("PQTZ") if a is not None and "PQTZ" in a.keys() else None
    except Exception:
        return None
    return None if q is None else (dat, a, q)


def _set_grid(got, t0: float, bpm: float, length_s: float, files: list) -> list[dict]:
    """Queue the .DAT with a steady grid (beat 1 at t0 s, Rekordbox's timeline) and the .EXT without its PQT2."""
    from pyrekordbox.anlz import AnlzFile

    dat, a, q = got
    q.content.entries = _grid_entries(t0, bpm, length_s)
    q.content.entry_count = len(q.content.entries)
    files.append((dat, a.build()))
    ext = dat.with_suffix(".EXT")
    if ext.is_file():
        e = AnlzFile.parse_file(ext)
        if "PQT2" in e.keys():
            e.tags = [x for x in e.tags if x.type != "PQT2"]
            files.append((ext, e.build()))
    return q.content.entries


def _save_anlz(out: dict, files: list) -> None:
    """After a committed write: copy the ANLZ files into its master.db backup (undo restores them), then write them."""
    if out.get("backup"):
        keep = Path(out["backup"]) / ANLZ_BACKUP
        keep.mkdir()
        names = {}
        for i, (f, data) in enumerate(files):
            names[f"{i}{f.suffix}"] = str(f)
            shutil.copy2(f, keep / f"{i}{f.suffix}")
        (keep / "files.json").write_text(json.dumps(names, indent=1))
        for f, data in files:
            f.write_bytes(data)


def _tempos(q) -> set[int]:
    return {int(e.tempo) for e in q.content.entries}


def shift_grids(todo: dict[str, dict], same, db: str | None = None, dry_run: bool = False) -> dict:
    """The grid fix for Rekordbox: todo = {path: approved plan {bpm, grid_ms, shift_ms}} (Traktor's timeline).
    Each track whose grid is still the approved one (same(plan, bpm, grid_ms)) gets its PQTZ grid moved by the
    shift (one steady tempo, so a grid with tempo changes is left alone and listed) and its hot cues, memory cues
    and loops moved with it in master.db. Returns stats with done = {path: (shift, bpm, new grid_ms)}."""
    stats = {"tracks": 0, "cues_moved": 0, "skipped": [], "tempo_changes": [], "changes": [], "done": {}}
    files: list[tuple[Path, bytes]] = []

    def change(master) -> bool:
        have = _grids(master)
        for p, plan in todo.items():
            name, hit = Path(p).name, have.get(p)
            if hit is None:
                stats["skipped"].append(f"{name}: not in Rekordbox's collection with a beat grid")
                continue
            c, d = hit
            if not same(plan, d["bpm"], d["grid_ms"]):
                stats["skipped"].append(f"{name}: beat grid changed since you approved; approve again")
                continue
            got = _anlz_grid(master, c)
            if got is None:
                stats["skipped"].append(f"{name}: no Rekordbox analysis file to write the grid into")
                continue
            if len(_tempos(got[2])) > 1:
                stats["tempo_changes"].append(name)
                stats["skipped"].append(f"{name}: its Rekordbox grid has tempo changes; fix it in Rekordbox")
                continue
            shift, bar = round(plan["shift_ms"]), 4 * 60000 / d["bpm"]  # PQTZ and cue times are whole ms
            rows = list(master.get_cue(ContentID=c.ID))
            if any(q.InMsec is not None and q.InMsec + shift < 0 for q in rows):
                stats["skipped"].append(f"{name}: a cue would move before the start")
                continue
            t0 = round(d["grid_ms"] - d["shift_ms"]) + shift
            while t0 < 0:
                t0 += bar  # the same grid, beat 1 a bar (or more) later
            t0 = round(t0)
            beats = _set_grid(got, t0 / 1000, d["bpm"], c.Length or 0, files)
            new_grid = next((e["time"] for e in beats if e["beat"] == 1), t0) + d["shift_ms"]  # as the check reads it
            for q in rows:
                if q.InMsec is None:
                    continue
                q.InMsec = round(q.InMsec + shift)
                q.CueMicrosec = q.InMsec * 1000
                if q.OutMsec is not None and q.OutMsec > 0:
                    q.OutMsec = round(q.OutMsec + shift)
            stats["tracks"] += 1
            stats["cues_moved"] += len(rows)
            stats["done"][p] = (shift, d["bpm"], new_grid)
            stats["changes"].append(f"{name}: grid {shift:+.1f} ms" + (f", with {len(rows)} cues/loops" if rows else ""))
        return bool(files)

    out = _write(db, dry_run, change, stats, "the grid fixes")
    if not dry_run:
        _save_anlz(out, files)
    return out


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
            examples.append(f"{Path(p).name}: {analysis.COLOR_NAMES[expect - 1] if expect else '(none)'} → {analysis.COLOR_NAMES[want - 1]}")
        return changed

    out = _write(db, dry_run, change, stats, "the colours")
    out["not_in_collection"] = len(changes) - len(done)
    out["changes"] = examples
    if out.get("changed") and not dry_run:
        collection.accept_guesses(changed)
    return out


def set_genres(changes: dict[str, tuple[str | None, str]], db: str | None = None, dry_run: bool = False) -> dict:
    """traktor.set_genres for Rekordbox: {path: (genre it must have now or None, new genre)}."""
    stats = {"genres_changed": 0, "skipped_changed_since": 0}
    examples, done = [], set()

    def change(master):
        made: dict = {}
        n = 0
        for p, c in _content_by_path(master).items():
            if p not in changes:
                continue
            expect, want = changes[p]
            done.add(p)
            cur = ((c.Genre.Name if c.Genre is not None else "") or "").strip() or None
            if cur != expect:
                stats["skipped_changed_since"] += 1
                continue
            c.Genre = _genre_row(master, want, made)
            n += 1
            examples.append(f"{Path(p).name}: {expect or '(none)'} → {want}")
        stats["genres_changed"] = n
        return n

    out = _write(db, dry_run, change, stats, "the genres")
    out["not_in_collection"] = len(changes) - len(done)
    out["changes"] = examples
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


# ----------------------------------------------------------------------------
# Set and crate playlists, History, tempo view
# ----------------------------------------------------------------------------

SET_FOLDER = "Sets (Track Analyzer)"


def _root_folder(master, name: str, make: bool):
    found = [p for p in master.get_playlist() if p.Name == name and p.Attribute == FOLDER and str(p.ParentID) == "root"]
    if found or not make:
        return found[0] if found else None
    return master.create_playlist_folder(name)


def write_playlists(lists: dict[str, list[str] | None], folder: str = SET_FOLDER, db: str | None = None,
                    dry_run: bool = False, first: bool = True, what: str = "the set playlist") -> dict:
    """{name: [file paths] in order, or None to remove} as plain playlists in `folder` (under the root); a playlist of
    the same name is replaced. The folder goes when it ends up empty."""
    stats = {"folder": folder, "playlists": {}, "removed": [], "replaced": [], "not_in_rekordbox": 0, "missing": []}

    def change(master):
        rows = _content_by_path(master)
        parent = _root_folder(master, folder, make=any(v is not None for v in lists.values()))
        if parent is None:
            return False
        kids = {p.Name: p for p in master.get_playlist(ParentID=parent.ID)}
        for name, paths in lists.items():
            if name in kids:
                master.delete_playlist(kids.pop(name))
                stats["removed" if paths is None else "replaced"].append(name)
            if paths is None:
                continue
            pl = master.create_playlist(name, parent=parent, seq=1 if first else None)
            n = 0
            for p in paths:
                c = rows.get(norm_path(p))
                if c is None:
                    stats["not_in_rekordbox"] += 1
                    stats["missing"].append(p)
                    continue
                n += 1
                master.add_to_playlist(pl, c, track_no=n)
            stats["playlists"][name] = n
        if not kids and not stats["playlists"]:
            master.delete_playlist(parent)
        return bool(stats["playlists"] or stats["removed"])

    return _write(db, dry_run, change, stats, what)


def histories(db: str | None = None) -> list[dict]:
    """Rekordbox's History playlists (djmdHistory / djmdSongHistory), oldest first: {name, plays}, each play like
    tracklist.read_history's. Rekordbox keeps no start time or deck per play, only the order and when the row was
    written (at load), so 'start' is that time and 'seconds' runs to the next play's start."""
    from datetime import datetime

    master = open_database(find_collection(db))
    try:
        out = []
        for h in master.get_history():
            if h.Attribute == FOLDER:
                continue
            songs = sorted((s for s in master.get_history_songs(HistoryID=h.ID) if s.Content is not None),
                           key=lambda s: s.TrackNo or 0)
            plays = []
            for s in songs:
                c, start = s.Content, s.created_at
                if isinstance(start, str):
                    try:
                        start = datetime.fromisoformat(start[:19])
                    except ValueError:
                        start = None
                if start is not None and start.tzinfo is not None:
                    start = start.astimezone().replace(tzinfo=None)
                plays.append({"artist": c.Artist.Name if c.Artist is not None else None, "title": c.Title,
                              "path": norm_path(c.FolderPath) if c.FolderPath else None,
                              "bpm": c.BPM / 100 if c.BPM else None,
                              "key": _key_name(c.Key.ScaleName if c.Key is not None else None),
                              "start": start, "seconds": 0.0, "deck": "?", "public": True})
            for a, b in zip(plays, plays[1:]):
                if a["start"] and b["start"]:
                    a["seconds"] = max(0.0, (b["start"] - a["start"]).total_seconds())
            if plays and plays[-1]["start"]:
                plays[-1]["seconds"] = 420.0  # the last play: no next one to end it; a typical track's length
            out.append({"name": h.Name, "date": h.DateCreated, "plays": [p for p in plays if p["start"]]})
        return sorted(out, key=lambda h: h["plays"][0]["start"] if h["plays"] else datetime.min)
    finally:
        close_database(master)


def _key_name(name: str | None) -> tuple[str, str] | None:
    """Rekordbox's key name → tracklist's (tonic, 'major'/'minor')."""
    from .tracklist import PITCH_NAMES

    cam = key_camelot(name)
    if not cam:
        return None
    n, minor = int(cam[:-1]), cam[-1] == "A"
    pc = next(k for k, v in (CAMELOT_MINOR if minor else CAMELOT_MAJOR).items() if v == n)
    return PITCH_NAMES[pc], "minor" if minor else "major"


def tempo_load(view: dict[str, dict], db: str | None = None, dry_run: bool = False) -> tuple[dict, dict]:
    """tempo.load for Rekordbox. view: {path: {genre, colour (our 1-7, or None to keep), hype}}, colour built from
    the track's colour now (colour_of(ours) callable allowed). Returns (stats, remembered {path: {field: [yours,
    view]}}) with fields Genre (name), ColorID, Commnt, so tempo_unload can put back exactly what was there."""
    from . import analysis

    stats = {"tracks_in_view": 0, "genres_set": 0, "colours_moved": 0, "hype_set": 0}
    remembered, examples = {}, []

    def change(master):
        made: dict = {}
        for p, c in _content_by_path(master).items():
            v = view.get(p)
            if v is None:
                continue
            stats["tracks_in_view"] += 1
            cur = {"Genre": c.Genre.Name if c.Genre is not None else None, "ColorID": str(c.ColorID or "0"),
                   "Commnt": c.Commnt or ""}
            want = {}
            if v.get("genre"):
                want["Genre"] = v["genre"]
            col = v.get("colour")
            if callable(col):
                col = col(RB_TO_OUR.get(color_id(c.ColorID)))
            if col:
                want["ColorID"] = str(OUR_TO_RB[col])
            if v.get("hype") is not None:
                want["Commnt"] = with_hype(cur["Commnt"], v["hype"])
            changed = {f: [cur[f], w] for f, w in want.items() if w != cur[f]}
            if not changed:
                continue
            _set_fields(master, c, {f: w for f, (_, w) in changed.items()}, made)
            remembered[p] = changed
            stats["genres_set"] += "Genre" in changed
            stats["colours_moved"] += "ColorID" in changed
            stats["hype_set"] += "Commnt" in changed
            if len(examples) < 12 and ("Genre" in changed or "ColorID" in changed):
                def show(f, x):
                    if f == "ColorID":
                        o = RB_TO_OUR.get(color_id(x))
                        return analysis.COLOR_NAMES[o - 1] if o else "no colour"
                    return x or "(empty)"
                examples.append(f"{Path(p).name}: " + "; ".join(f"{show(f, a)} → {show(f, b)}"
                                                               for f, (a, b) in changed.items() if f != "Commnt"))
        return bool(remembered)

    out = _write(db, dry_run, change, stats, "the tempo view")
    out["examples"] = examples
    return out, remembered


def _set_fields(master, c, fields: dict, made: dict) -> None:
    for f, w in fields.items():
        if f == "Genre":
            c.Genre = _genre_row(master, w, made) if w else None
        else:
            setattr(c, f, w)


def tempo_unload(tracks: dict[str, dict], db: str | None = None, dry_run: bool = False) -> dict:
    """Put back what tempo_load remembered; a field changed in Rekordbox while the view was loaded is kept."""
    stats = {"restored": 0, "kept_your_changes": 0}
    kept = []

    def change(master):
        made: dict = {}
        for p, c in _content_by_path(master).items():
            if p not in tracks:
                continue
            cur = {"Genre": c.Genre.Name if c.Genre is not None else None, "ColorID": str(c.ColorID or "0"),
                   "Commnt": c.Commnt or ""}
            back = {}
            for f, (yours, view) in tracks[p].items():
                if cur.get(f) == view:
                    back[f] = yours
                    stats["restored"] += 1
                elif cur.get(f) != yours:
                    stats["kept_your_changes"] += 1
                    if len(kept) < 30:
                        kept.append(f"{Path(p).name}: {f} {cur.get(f) or '(empty)'} (view had {view or '(empty)'})")
            _set_fields(master, c, back, made)
        return stats["restored"]

    out = _write(db, dry_run, change, stats, "switching back from the tempo view")
    out["kept"] = kept
    return out


def sync_tracks(db: str | None = None, dry_run: bool = False, tracks: list[dict] | None = None,
                relink: bool = True) -> dict:
    """Relink tracks whose file moved (FolderPath; the ANLZ files' own path tag is left as it is, so nothing outside
    master.db changes and the backup covers it all) and add analysed tracks Rekordbox doesn't list (trackfiles.plan).
    Added tracks get the title only; Rekordbox reads the rest and analyses them when it loads them."""
    from . import trackfiles

    rows = read(db)
    live = _live(rows)
    present = {trackfiles.nfc(p) for p, _ in live.values()}
    missing = [(t["id"], os.path.basename(norm_path(t["path"])), t.get("size")) for t in rows["tracks"]
               if t["id"] not in live and t["path"]]
    p = trackfiles.plan(present, missing if relink else [], tracks)
    if not p["moves"] and not p["adds"]:
        raise SystemExit("every analysed track is in Rekordbox and no moved files were found")
    old = {t["id"]: t["path"] for t in rows["tracks"]}
    stats = trackfiles.stats(p, lambda k: old.get(k, k))

    def change(master) -> bool:
        for cid, new in p["moves"].items():
            # as pyrekordbox's update_content_path, without it reading ANLZ files (missing ones raise)
            c, new = master.get_content(ID=cid), Path(new).as_posix()
            if c.OrgFolderPath and c.OrgFolderPath == c.FolderPath:
                c.OrgFolderPath = new
            c.FolderPath, c.FileNameL = new, new.rsplit("/", 1)[-1]
        gen = master.generate_unused_id  # new IDs as strings, like the loaded ones: SQLAlchemy can't sort a mix
        master.generate_unused_id = lambda *a, **k: str(gen(*a, **k))
        for t in p["adds"]:
            title = t.get("title") or os.path.splitext(os.path.basename(t["path"]))[0]
            try:
                master.add_content(t["path"], Title=title)
            except ValueError as err:  # a file type Rekordbox doesn't take
                stats.setdefault("not_added", []).append(f"{t['path']}: {err}")
        return True

    return _write(db, dry_run, change, stats, "adding and relinking tracks")
