"""Rekordbox's library (master.db), read through pyrekordbox: the same high-level calls as traktor.py, so
library.py can hand either one to the rest of the app. Read-only for now (stage 1 of the Rekordbox plan); what the
database holds is in docs/notes/rekordbox.md.

The database is read once into plain rows (`load`), and everything else works on those rows, so the tests need
neither pyrekordbox nor an encrypted database."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

from . import collection
from .analysis import CAMELOT_MAJOR, CAMELOT_MINOR, PITCH, app_setting, save_app_setting

DB_SETTING = "rekordbox_database"  # in data/settings.json: chosen on the Setup tab
DB_NAME = "master.db"
CONTAINER = os.environ.get("TRACK_ANALYZER_CONTAINER") == "1"

# djmdPlaylist.Attribute
PLAYLIST, FOLDER, SMART = 0, 1, 4
# djmdCue.Kind: 0 = memory cue, hot cues A-H (4 is skipped)
HOT_CUE_KINDS = (1, 2, 3, 5, 6, 7, 8, 9)
# djmdColor IDs 1-8; 0 = none
COLORS = {1: "Pink", 2: "Red", 3: "Orange", 4: "Yellow", 5: "Green", 6: "Aqua", 7: "Blue", 8: "Purple"}
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
            "color": c.ColorID, "comment": c.Commnt, "rating": c.Rating,
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
