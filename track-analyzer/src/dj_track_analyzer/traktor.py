"""
Traktor integration: copy the genres from your files into Traktor's collection and create one
Smartlist per genre.

  uv run dj-track-analyzer traktor --dry-run     show what would change
  uv run dj-track-analyzer traktor               do it (Traktor must be closed)
  uv run dj-track-analyzer traktor --undo        put back the collection from before the last run

Traktor keeps its own copy of every track's tags in collection.nml and only re-reads files in
some situations, so freshly written genre tags may not show up there on their own. This command
updates the GENRE in collection.nml for every scanned track (taking the value from the file's own
genre tag, i.e. whatever `tag` wrote), and adds a "Genres (Track Analyzer)" folder of Smartlists
such as  Genre contains "Hard Trance".

collection.nml is edited in place with small, targeted text changes, so everything else in it
stays byte-for-byte the same. A timestamped backup is made next to it first.
"""

from __future__ import annotations

import contextlib
import contextvars
import glob
import html
import ntpath
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from . import collection
from .analysis import app_setting, log, save_app_setting

FOLDER_NAME = "Genres (Track Analyzer)"
BACKUP_PREFIX = "collection.nml.before-track-analyzer-"
UUID_NS = uuid.UUID("6f1c0a52-2d0e-4b4e-9a51-7a3c6f0d9e11")


_offsets: dict[str, int] = {}  # path -> audio_offset


def _mp3_info_frame(path: Path) -> bool:
    """Does the MP3 start with a Xing/Info frame (the header frame LAME and ffmpeg write before the audio)?"""
    with open(path, "rb") as f:
        h = f.read(10)
        i = 10 + ((h[6] << 21) | (h[7] << 14) | (h[8] << 7) | h[9]) + (10 if h[5] & 0x10 else 0) if h[:3] == b"ID3" else 0
        f.seek(i)
        b = f.read(4096)
    j = next((k for k in range(len(b) - 1) if b[k] == 0xFF and b[k + 1] & 0xE0 == 0xE0), None)
    return j is not None and (b.find(b"Xing", j, j + 200) != -1 or b.find(b"Info", j, j + 200) != -1)


def audio_offset(path: Path) -> int:
    """Samples (at the file's own rate) by which Traktor's timeline runs ahead of ffmpeg's and Essentia's for this file:
    Traktor time = decoded time + this. ffmpeg skips an MP3's Xing/Info frame and the encoder/decoder delay its LAME tag
    gives (ffmpeg's start_time); Traktor plays both. Checked to the sample against Traktor 4 Remix Deck captures for
    each kind of MP3 in a test library: 2257 (Info frame + 1105 delay), 1152 (Info frame, no delay), 1681 (Info frame +
    529), 0 (neither). Other formats: 0."""
    k = str(path)
    if k not in _offsets:
        n = 0
        if path.suffix.lower() == ".mp3":
            from .analysis import find_tool

            out = subprocess.run([find_tool("ffprobe") or "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=start_time,sample_rate",
                                  "-of", "csv=p=0", k], capture_output=True, text=True, timeout=30).stdout.strip().split(",")
            try:
                sr, st = int(out[0]), float(out[1])
            except (ValueError, IndexError):
                sr, st = 44100, 0.0
            n = round(st * sr) + (1152 if _mp3_info_frame(path) else 0)
        _offsets[k] = n
    return _offsets[k]


# ----------------------------------------------------------------------------
# Finding Traktor's collection
# ----------------------------------------------------------------------------


def _folder_version(folder: str) -> tuple[int, ...] | None:
    m = re.search(r"Traktor\D*(\d+(?:\.\d+)*)", folder)
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def _version_key(p: str):
    """Newest version first; at the same version Traktor's own "Traktor 4.5.0" folder beats a copy of it
    ("Traktor 4.5.0 copy"), then the most recently saved."""
    name = Path(p).parent.name
    return (_folder_version(name) or (0,), bool(re.fullmatch(r"Traktor\s*\d+(?:\.\d+)*", name)), os.path.getmtime(p))


# What the analyzer has been checked against: a real Traktor collection read and written (macOS 4.5.0, Windows
# 4.4.2). Raise these after checking a new Traktor version: Setup says "not verified yet" above them.
TESTED_TRAKTOR = (4, 5, 0)
TESTED_FORMATS = ("20",)  # <NML VERSION="…">: the collection file's own format number


def collection_version(path: Path) -> dict:
    """Traktor's version (from its folder's name) and the collection's format, and whether they're newer than tested:
    untested = None, "newer" (a newer Traktor, same file format: should work) or "format" (a file format not seen)."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096).decode("utf-8", "replace")
    except OSError:
        head = ""
    m = re.search(r'<NML\s+VERSION="([^"]*)"', head)
    fmt, app = (m.group(1) if m else None), _folder_version(path.parent.name)
    untested = "format" if fmt not in TESTED_FORMATS else "newer" if app and app > TESTED_TRAKTOR else None
    return {"traktor": ".".join(map(str, app)) if app else None, "format": fmt, "untested": untested,
            "tested_traktor": ".".join(map(str, TESTED_TRAKTOR)), "tested_formats": list(TESTED_FORMATS)}


def untested_note(path: Path) -> str | None:
    v = collection_version(path)
    if v["untested"] == "format":
        return (f"Note: this collection's file format (NML version {v['format']}) is newer than the analyzer has been "
                f"tested with ({', '.join(v['tested_formats'])}). Check the changes in Traktor; the backup and undo work as always.")
    if v["untested"] == "newer":
        return (f"Note: Traktor {v['traktor']} isn't verified with the analyzer yet (tested up to {v['tested_traktor']}). "
                "Its collection format is the same, so it should work; if anything looks wrong, undo and report it.")
    return None


KF_FLAG_DONT_VERIFY = 0x4000  # the path even if the folder isn't there (checking it is our glob's job)


def _known_documents() -> str:
    """Windows' Documents folder (FOLDERID_Documents); raises OSError with Windows' error code if it can't say."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

    folder = GUID(0xFDD39AD0, 0x238F, 0x46AF, (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
    out = ctypes.c_wchar_p()
    hr = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder), KF_FLAG_DONT_VERIFY, None, ctypes.byref(out))
    try:
        if hr != 0:
            raise OSError(f"SHGetKnownFolderPath failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")
        return out.value
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)  # freed even on failure (NULL is fine)


def _windows_documents() -> str | None:
    """Where Windows really keeps Documents (moved by OneDrive backup, or to another drive in its Properties →
    Location), which is where Traktor puts its folder; ~/Documents is only the default."""
    try:
        return _known_documents()
    except Exception:  # noqa: BLE001 - only a hint; ~/Documents is still searched
        return None


def documents_dirs() -> list[Path]:
    """Folders that may hold Native Instruments/Traktor x: ~/Documents, and on Windows the real Documents folder and
    OneDrive's."""
    dirs = [Path(os.path.expanduser("~/Documents"))]
    if sys.platform == "win32":
        dirs.append(_windows_documents())
        dirs += [Path(os.environ[k]) / "Documents" for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
                 if os.environ.get(k)]
    seen, out = set(), []
    for d in dirs:
        if d and os.path.normcase(str(d)) not in seen:
            seen.add(os.path.normcase(str(d)))
            out.append(Path(d))
    return out


def traktor_glob(rest: str) -> list[str]:
    """Matches of Native Instruments/Traktor*/<rest> in every Documents folder."""
    return [f for d in documents_dirs()
            for f in glob.glob(os.path.join(glob.escape(str(d)), "Native Instruments", "Traktor*", rest))]


def auto_collection() -> Path | None:
    """The newest Traktor version's collection.nml in any Documents folder, or None."""
    candidates = traktor_glob("collection.nml")
    return Path(max(candidates, key=_version_key)) if candidates else None


COLLECTION_SETTING = "traktor_collection"  # in data/settings.json: chosen on the Setup tab


def chosen_collection() -> Path | None:
    p = app_setting(COLLECTION_SETTING)
    return Path(p) if p else None


def check_collection(path: str) -> Path:
    """A path typed, pasted or picked as Traktor's collection: a collection.nml (or the Traktor folder holding one)
    that Traktor wrote. Explorer's Copy as path adds quotes; they're dropped."""
    p = Path(os.path.expanduser(path.strip().strip('"').strip("'").strip()))
    if not str(path).strip():
        raise ValueError("Give the path of Traktor's collection.nml.")
    if p.is_dir():
        p = p / "collection.nml"
    if not p.is_file():
        raise ValueError(f"{p} isn't there.")
    if p.name.lower() != "collection.nml":
        raise ValueError(f"{p.name} isn't Traktor's collection: choose the file named collection.nml in the Traktor "
                         "folder (not a History playlist or a backup).")
    with open(p, "rb") as f:
        if b"<NML" not in f.read(4096):
            raise ValueError(f"{p} doesn't look like a Traktor collection (no <NML> at the start).")
    return p.resolve()


def choose_collection(path: str | None) -> Path | None:
    """Remember where Traktor's collection is (None: find it automatically again). Returns what's used now."""
    save_app_setting(COLLECTION_SETTING, str(check_collection(path)) if path else None)
    return chosen_collection() or auto_collection()


def find_collection(nml: str | None = None) -> Path:
    """--nml if given, else the one chosen on the Setup tab, else the newest Traktor version's."""
    if nml:
        p = Path(os.path.expanduser(nml))
        if not p.exists():
            raise SystemExit(f"{p} does not exist")
        return p
    chosen = chosen_collection()
    if chosen:
        if chosen.exists():
            return chosen
        raise SystemExit(f"The Traktor collection chosen in Setup, {chosen}, isn't there any more (moved, or on a drive "
                         "that isn't connected). Choose it again on the Setup tab, or let it be found automatically.")
    found = auto_collection()
    if found:
        return found
    if sys.platform.startswith("linux"):
        raise SystemExit("Traktor doesn't run on Linux, so the Traktor steps are skipped. Everything else works on "
                         "your music folders.")
    looked = ", ".join(str(d / "Native Instruments") for d in documents_dirs())
    raise SystemExit(f"Couldn't find Traktor's collection.nml (looked in {looked}). Choose it on the Setup tab "
                     "(Browse…), or pass --nml /path/to/collection.nml")


def newer_collection() -> Path | None:
    """A newer Traktor version's collection than the chosen one (Traktor makes a new folder when it updates, and
    the chosen one would stay on the old version), or None."""
    chosen, found = chosen_collection(), auto_collection()
    if not chosen or not found or not chosen.exists():
        return None
    return found if (_folder_version(found.parent.name) or (0,)) > (_folder_version(chosen.parent.name) or (0,)) else None


TEMPO_STATE = "tempo-loaded.json"  # in data while a `tempo load` view is in Traktor


def refuse_if_tempo_view(path: Path) -> None:
    """The normal genre/colour/hype writers would mix their values into a loaded tempo view, and
    `tempo native` could then no longer tell your values from the view's."""
    import json

    from .analysis import HOME

    state = HOME / TEMPO_STATE
    if state.exists() and json.loads(state.read_text(encoding="utf-8")).get("collection") == str(path):
        bpm = json.loads(state.read_text(encoding="utf-8")).get("bpm")
        raise SystemExit(f"The {bpm:g} BPM view is loaded in this Traktor collection. "
                         "Switch back first: uv run dj-track-analyzer tempo native")


def running_state() -> bool | None:
    """True/False: Traktor is or isn't running. None: this process can't tell (in a container, or the process list
    failed), and a write must not assume it's closed."""
    if CONTAINER:
        return None
    try:
        if sys.platform == "win32":
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace",
                                 timeout=30)
            names = [ln.split('","')[0].strip('"') for ln in out.stdout.splitlines() if ln.startswith('"')]
            if out.returncode or not names:
                return None
            return any(re.fullmatch(r"traktor.*\.exe", n, re.I) for n in names)
        out = subprocess.run(["pgrep", "-i", "-x", "traktor.*"], capture_output=True, text=True, timeout=30)
        return {0: True, 1: False}.get(out.returncode)  # 2, 3: pgrep failed
    except (OSError, subprocess.SubprocessError):
        return None


def traktor_running() -> bool:
    """For the page's warnings; writes go through check_closed, which also stops when it can't tell."""
    return running_state() is True


# When the process list can't be read (in a container, where the Dockerfile sets TRACK_ANALYZER_CONTAINER=1 and pgrep
# sees only the container; or pgrep/tasklist failing), a write asks instead: in a terminal once per run, otherwise
# TRACK_ANALYZER_TRAKTOR_CLOSED=1 (jobs started from the page get it) or the page's own answer (`page_answer`).
CONTAINER = os.environ.get("TRACK_ANALYZER_CONTAINER") == "1"
CLOSED_ENV = "TRACK_ANALYZER_TRAKTOR_CLOSED"
_page_closed: contextvars.ContextVar[bool | None] = contextvars.ContextVar("page_closed", default=None)


class ConfirmClosed(SystemExit):
    """A write nobody has said Traktor is closed for, where we can't see. The web page asks and sends it again."""

    def __init__(self, why: str):
        self.why = why
        where = "a terminal (docker compose run)" if CONTAINER else "a terminal"
        super().__init__(f"{why} Quit Traktor, then answer y when asked in {where}, or set {CLOSED_ENV}=1.")


@contextlib.contextmanager
def page_answer(closed: bool):
    """The page's answer to "Is Traktor closed?" while it handles one request (no terminal to ask in)."""
    token = _page_closed.set(closed)
    try:
        yield
    finally:
        _page_closed.reset(token)


def closed_confirmed() -> bool:
    return os.environ.get(CLOSED_ENV) == "1" or bool(_page_closed.get())


def check_closed(dry_run: bool = False, what: str = "these changes") -> None:
    """Refuse to write collection.nml while Traktor is open: it rewrites the file when it closes and would undo it.
    When we can't tell, ask rather than assume it's closed."""
    if dry_run:
        return
    state = running_state()
    if state:
        raise SystemExit(f"Traktor is running. Quit Traktor first: it rewrites collection.nml when it closes "
                         f"and would undo {what}.")
    if state is False or closed_confirmed():
        return
    why = ("This runs in a container, which can't see whether Traktor is open." if CONTAINER else
           f"Couldn't check whether Traktor is open ({'tasklist' if sys.platform == 'win32' else 'pgrep'} failed).")
    if _page_closed.get() is None and sys.stdin.isatty():
        sys.stderr.write(f"{why} Is Traktor closed? [y/N] ")
        sys.stderr.flush()
        if sys.stdin.readline().strip().lower() in ("y", "yes"):
            os.environ[CLOSED_ENV] = "1"  # once per run
            return
    raise ConfirmClosed(why)


# ----------------------------------------------------------------------------
# Small XML text helpers (no full re-serialisation, so Traktor's formatting is kept)
# ----------------------------------------------------------------------------

ATTR_RE = re.compile(r'([A-Za-z_][\w.-]*)="([^"]*)"')


def attrs(tag_text: str) -> dict:
    return {k: html.unescape(v) for k, v in ATTR_RE.findall(tag_text)}


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


WINDOWS_DRIVE = re.compile(r"[A-Za-z]:")


def location_paths(loc: dict) -> list[str]:
    """Possible paths for a Traktor LOCATION: on a Mac the boot volume or /Volumes/<name>; Windows Traktor's VOLUME is
    the drive letter ("C:"), the same DIR form ("/:Users/:me/:") then gives C:\\Users\\me\\."""
    vol = loc.get("VOLUME", "")
    if WINDOWS_DRIVE.fullmatch(vol):
        return [vol + loc.get("DIR", "").replace("/:", "\\") + loc.get("FILE", "")]
    d = loc.get("DIR", "").replace("/:", "/")
    rel = d + loc.get("FILE", "")
    out = [rel]
    if loc.get("VOLUME"):
        out.append(f"/Volumes/{loc['VOLUME']}{rel}")
    return out


def set_attr(tag: str, name: str, value: str) -> str:
    """Set an attribute inside one start tag like <INFO A="1" ...>."""
    pat = re.compile(rf'(\s{name}=")[^"]*(")')
    if pat.search(tag):
        return pat.sub(lambda m: m.group(1) + esc(value) + m.group(2), tag, count=1)
    m = re.match(r"<\w+", tag)
    return tag[:m.end()] + f' {name}="{esc(value)}"' + tag[m.end():]


# ----------------------------------------------------------------------------
# Smartlist queries
# ----------------------------------------------------------------------------


def query_term(genre: str) -> str:
    """Text to search for. Characters that mean something in Traktor's query language are avoided:
    'Melodic House & Techno' is searched as 'Melodic House'."""
    parts = [p.strip() for p in re.split(r'[&|"()!%$]', genre) if p.strip()]
    return max(parts, key=len) if parts else genre


def smartlist_query(genre: str, all_genres: list[str]) -> str:
    term = query_term(genre)
    q = f'$GENRE % "{term}"'
    # 'Trance' must not also catch 'Hard Trance': exclude other names that contain this one. Checked
    # against the full name, so 'Techno' also leaves out 'Melodic House & Techno' (searched as
    # 'Melodic House', since '&' can't go into a query).
    excluded = set()
    for other in sorted(all_genres):
        ot = query_term(other)
        if (other != genre and term.lower() in other.lower() and ot.lower() != term.lower()
                and ot.lower() not in excluded):
            excluded.add(ot.lower())
            q += f' & $GENRE !% "{ot}"'
    return q


def known_genres() -> set[str]:
    """Genre names this tool writes: the DJ genres in dj_genres.json, plus the user's trained genres while they're in use.
    Only these get Smartlists; a file's own genre tag ("melodic techno, indie dance", "Électronique")
    is the user's or the shop's, not ours."""
    from . import analysis

    names = set(analysis.dj_rules())
    if analysis.use_my_genres():  # "Use my genres" on (else nothing carries them)
        names |= set(analysis.my_genre_bundle().get("counts", {}))
    return names


def smartlist_folder(genres: list[str], folder: str = FOLDER_NAME) -> str:
    # Exclusions consider every known genre name, not just the ones with tracks right now, so
    # e.g. "Hardcore" never catches "Happy Hardcore" tracks tagged by hand.
    from . import analysis

    universe = sorted(set(genres) | set(analysis.dj_rules()))
    nodes = []
    for g in sorted(genres, key=str.lower):
        uid = uuid.uuid5(UUID_NS, g.lower()).hex
        nodes.append(
            f'<NODE TYPE="SMARTLIST" NAME="{esc(g)}"><SMARTLIST UUID="{uid}">'
            f'<SEARCH_EXPRESSION VERSION="1" QUERY="{esc(smartlist_query(g, universe))}"></SEARCH_EXPRESSION>\n'
            f'</SMARTLIST>\n</NODE>\n')
    return (f'<NODE TYPE="FOLDER" NAME="{esc(folder)}"><SUBNODES COUNT="{len(nodes)}">'
            + "".join(nodes) + "</SUBNODES>\n</NODE>\n")


def _node_span(text: str, start: int) -> int:
    """End index of the <NODE ...> element starting at `start` (handles nesting)."""
    depth, i = 0, start
    tok = re.compile(r"<NODE\b[^>]*?(/?)>|</NODE>")
    for m in tok.finditer(text, start):
        if m.group(0).startswith("</"):
            depth -= 1
        elif m.group(1) != "/":
            depth += 1
        if depth == 0:
            end = m.end()
            if text[end:end + 1] == "\n":
                end += 1
            return end
        i = m.end()
    raise ValueError("unbalanced NODE elements")


ROOT_RE = re.compile(r'<NODE TYPE="FOLDER" NAME="\$ROOT">\s*<SUBNODES COUNT="(\d+)">')
SUBNODES_RE = re.compile(r'<SUBNODES COUNT="(\d+)">')


def _parent_subnodes(text: str, i: int) -> re.Match | None:
    """The <SUBNODES COUNT=…> tag that directly holds the node starting at `i`: the root's, or that of a folder of
    yours it was moved into. None when it isn't in the playlist tree."""
    m = ROOT_RE.search(text)
    if not m:
        return None
    sub, folder_end = SUBNODES_RE.search(text, m.start()), _node_span(text, m.start())
    j = sub.end()
    while (j := text.find("<NODE ", j)) != -1 and j < folder_end:
        if j == i:
            return sub
        end = _node_span(text, j)
        if j < i < end:  # inside this folder: look among its children
            sub = SUBNODES_RE.search(text, j, end)
            if not sub:
                return None
            j, folder_end = sub.end(), end
            continue
        j = end
    return None


def remove_root_folder(text: str, folder: str) -> tuple[str, str | None]:
    """Take a folder out of the playlist tree (from under the root, or from the folder of yours you moved it into;
    that one's count goes down). Returns (text, the removed node's text or None)."""
    i = text.find(f'<NODE TYPE="FOLDER" NAME="{esc(folder)}">')
    if i == -1:
        return text, None
    end = _node_span(text, i)
    node = text[i:end]
    parent = _parent_subnodes(text, i)
    text = text[:i] + text[end:]
    if parent:  # it comes before the node, so its place didn't move
        head = f'<SUBNODES COUNT="{max(0, int(parent.group(1)) - 1)}">'
        text = text[:parent.start()] + head + text[parent.end():]
    return text, node


def swap_root_folder(text: str, folder: str, node: str) -> tuple[str, str | None]:
    """Put `node` exactly where the folder named `folder` is (same place in the playlist tree).
    Returns (text, the replaced node's text), or (text unchanged, None) if there's no such folder."""
    i = text.find(f'<NODE TYPE="FOLDER" NAME="{esc(folder)}">')
    if i == -1:
        return text, None
    end = _node_span(text, i)
    return text[:i] + node + text[end:], text[i:end]


def insert_root_node(text: str, node: str) -> str:
    """Put a node (folder or playlist text) first under the playlist root."""
    m = ROOT_RE.search(text)
    if m:
        head = m.group(0).replace(f'COUNT="{m.group(1)}"', f'COUNT="{int(m.group(1)) + 1}"')
        return text[:m.start()] + head + node + text[m.end():]
    # Collection without any playlists yet: create the playlist tree.
    block = (f'<PLAYLISTS><NODE TYPE="FOLDER" NAME="$ROOT"><SUBNODES COUNT="1">'
             f'{node}</SUBNODES>\n</NODE>\n</PLAYLISTS>\n')
    if "<PLAYLISTS></PLAYLISTS>" in text:
        return text.replace("<PLAYLISTS></PLAYLISTS>", block.rstrip("\n"), 1)
    if "<PLAYLISTS/>" in text or "<PLAYLISTS />" in text:
        return re.sub(r"<PLAYLISTS\s*/>", block.rstrip("\n"), text, count=1)
    idx = text.find("<INDEXING")
    idx = idx if idx != -1 else text.rfind("</NML>")
    return text[:idx] + block + text[idx:]


def install_smartlists(text: str, genres: list[str], folder: str = FOLDER_NAME) -> tuple[str, bool]:
    """Replace (or add) our Smartlist folder directly under Traktor's playlist root."""
    text, old = remove_root_folder(text, folder)
    return insert_root_node(text, smartlist_folder(genres, folder)), old is not None


# ----------------------------------------------------------------------------
# Main entry
# ----------------------------------------------------------------------------


def mac_path(value: str) -> str | None:
    """Convert Traktor's settings-file paths ("Macintosh HD:Users:me:Music:Recordings:") to
    normal ones ("/Users/me/Music/Recordings"). Plain "/..." paths are returned as they are, and so are
    Windows Traktor's ("C:\\Users\\me\\Music\\Traktor\\Samples\\", without the last "\\")."""
    v = html.unescape(value or "").strip()
    if not v:
        return None
    if v.startswith("/"):
        return v.rstrip("/") or "/"
    if WINDOWS_DRIVE.match(v[:2]) and v[2:3] in ("\\", "/") or v.startswith("\\\\"):
        return ntpath.normpath(v)
    parts = [x for x in v.split(":") if x != ""]
    if len(parts) < 2:
        return None
    volume, rest = parts[0], "/".join(parts[1:])
    on_boot = "/" + rest
    other = f"/Volumes/{volume}/{rest}"
    # The startup disk ("Macintosh HD") is mounted at "/" (its /Volumes entry links back to "/");
    # any other drive lives under /Volumes/<name>.
    vol = f"/Volumes/{volume}"
    if os.path.exists(vol):
        return on_boot if os.path.realpath(vol) == "/" else other
    return on_boot if volume == "Macintosh HD" else other


# Traktor settings that point at folders holding things that aren't your tracks.
SKIP_SETTINGS = ("Browser.Dir.Recordings", "Browser.Dir.Loops")


def recording_dirs(nml: str | None = None, settings: tuple[str, ...] = SKIP_SETTINGS) -> list[str]:
    """Folders to leave out of scans: Traktor's mix recordings (its default Recordings folder and
    the one set in Preferences, Browser.Dir.Recordings in Traktor Settings.tsi) and its sample/loop
    folder (Browser.Dir.Loops). `settings=("Browser.Dir.Recordings",)`: the recordings only."""
    dirs = set(traktor_glob("Recordings"))
    tsis = set(traktor_glob("*.tsi"))
    for c in (Path(os.path.expanduser(nml)) if nml else None, chosen_collection()):
        if c:
            dirs.update(glob.glob(os.path.join(glob.escape(str(c.parent)), "Recordings")))
            tsis.update(glob.glob(os.path.join(glob.escape(str(c.parent)), "*.tsi")))
    for tsi in tsis:
        try:
            text = Path(tsi).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name in settings:
            m = re.search(rf'<Entry\s+Name="{re.escape(name)}"[^>]*\sValue="([^"]*)"', text)
            d = mac_path(m.group(1)) if m else None
            if d and d != "/" and not re.fullmatch(r"[A-Za-z]:\\?", d):  # never a whole drive
                dirs.add(d)
    return sorted(os.path.normpath(d) for d in dirs)  # on Windows glob keeps the "/" of "~/Documents/…"; scans use "\\"


def setting(collection: Path, name: str) -> str | None:
    """One value from Traktor Settings.tsi next to the collection (None if missing)."""
    tsi = collection.with_name("Traktor Settings.tsi")
    try:
        text = tsi.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(rf'<Entry\s+Name="{re.escape(name)}"[^>]*\sValue="([^"]*)"', text)
    return html.unescape(m.group(1)) if m else None


def set_setting(collection: Path, name: str, value: str, backup_tag: str = "before-track-analyzer-") -> str | None:
    """Change one existing value in Traktor Settings.tsi (text edit, backup first). Returns the old value,
    or None if the entry isn't there (nothing is written then)."""
    tsi = collection.with_name("Traktor Settings.tsi")
    text = tsi.read_text(encoding="utf-8")
    pat = re.compile(rf'(<Entry\s+Name="{re.escape(name)}"[^>]*\sValue=")([^"]*)(")')
    m = pat.search(text)
    if not m:
        return None
    if m.group(2) != esc(value):
        backup = tsi.with_name(f"{tsi.name}.{backup_tag}{time.strftime('%Y%m%d-%H%M%S')}")
        n = 1
        while backup.exists():  # two changes within the same second
            n += 1
            backup = tsi.with_name(f"{tsi.name}.{backup_tag}{time.strftime('%Y%m%d-%H%M%S')}-{n}")
        shutil.copy2(tsi, backup)
        tmp = tsi.with_name(tsi.name + ".track-analyzer-tmp")
        tmp.write_text(text[:m.start(2)] + esc(value) + text[m.end(2):], encoding="utf-8")
        os.replace(tmp, tsi)
    return html.unescape(m.group(2))


# The clock panel's tempo; Traktor saves it on quit and starts with it.
MASTER_TEMPO = "Status.Master Tempo"


# Preferences → File Management, writing tags to the audio files. The user's settings backups show
# 2 → 1 → 0, and 0 when they had turned it off (2026-10-22).
TAG_WRITING = "FileAccess.TagsSerialization"


def tag_writing_on(collection: Path) -> bool:
    v = setting(collection, TAG_WRITING)
    return v is not None and v != "0"


# ENTRY MODIFIED_DATE / MODIFIED_TIME: after reading a file's tags Traktor sets them to the file's mtime
# (UTC date, seconds into that day; verified 2026-10-22). When the file's mtime differs, Traktor reads the
# tags again on deck load and they replace INFO GENRE, COLOR etc.

def entry_stamp(entry: str) -> str | None:
    """'DATE TIME' of an ENTRY, e.g. '2026/10/22 38471'."""
    m = re.match(r"<ENTRY\b[^>]*>", entry)
    a = attrs(m.group(0)) if m else {}
    if "MODIFIED_DATE" not in a or "MODIFIED_TIME" not in a:
        return None
    return f"{a['MODIFIED_DATE']} {a['MODIFIED_TIME']}"


def file_stamp(path: str) -> str | None:
    """A file's mtime the way Traktor writes it into MODIFIED_DATE / MODIFIED_TIME."""
    try:
        t = time.gmtime(os.path.getmtime(path))
    except OSError:
        return None
    return f"{t.tm_year}/{t.tm_mon}/{t.tm_mday} {t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec}"


def set_entry_stamp(entry: str, stamp: str | None) -> str:
    """Set (or, for None, remove) MODIFIED_DATE / MODIFIED_TIME on an ENTRY."""
    m = re.match(r"<ENTRY\b[^>]*>", entry)
    if not m:
        return entry
    tag = m.group(0)
    if stamp is None:
        tag = re.sub(r'\sMODIFIED_(?:DATE|TIME)="[^"]*"', "", tag)
    else:
        date, secs = stamp.split(" ")
        tag = set_attr(set_attr(tag, "MODIFIED_DATE", date), "MODIFIED_TIME", secs)
    return tag + entry[m.end():]


def file_tags(path: str) -> dict | None:
    """Title, artist, album and comment from the file's own tags (None when it can't be read)."""
    import mutagen
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4Tags

    try:
        f = mutagen.File(path)
    except Exception:
        return None
    if f is None:
        return None
    t = f.tags
    if t is None:
        return {}

    def first(*keys):
        for k in keys:
            try:
                v = t.get(k)
            except (KeyError, ValueError):
                v = None
            v = getattr(v, "text", v)
            if v:
                return str(v[0] if isinstance(v, list) else v)
        return None

    if isinstance(t, ID3):
        comm = next((fr for fr in t.getall("COMM") if not fr.desc and fr.text), None)  # not iTunNORM etc.
        return {"title": first("TIT2"), "artist": first("TPE1"), "album": first("TALB"),
                "comment": str(comm.text[0]) if comm else None}
    if isinstance(t, MP4Tags):
        return {"title": first("\xa9nam"), "artist": first("\xa9ART"), "album": first("\xa9alb"),
                "comment": first("\xa9cmt")}
    return {"title": first("title"), "artist": first("artist"), "album": first("album"),
            "comment": first("comment", "description")}


def tags_match(entry: str, path: str) -> bool:
    """Does Traktor still show the file's title, artist, album and comment? False means the file's tags were
    edited since Traktor read them. Only fields the file has are compared (Traktor fills a missing title from
    the file name, and a comment typed in Traktor needn't be in the file); genre isn't, since `traktor` puts
    genres into Traktor that aren't in the files. The Hype part of a comment is ignored on both sides."""
    tags = file_tags(path)
    if tags is None:
        return True  # unreadable: nothing for Traktor to pick up either
    m = re.match(r"<ENTRY\b[^>]*>", entry)
    e = attrs(m.group(0)) if m else {}
    album_m = re.search(r"<ALBUM\b[^>]*>", entry)
    info_m = re.search(r"<INFO\b[^>]*>", entry)
    ours = {"title": e.get("TITLE"), "artist": e.get("ARTIST"),
            "album": attrs(album_m.group(0)).get("TITLE") if album_m else None,
            "comment": attrs(info_m.group(0)).get("COMMENT") if info_m else None}

    def norm(s):
        return unicodedata.normalize("NFC", HYPE_RE.sub("", s or "")).strip()

    return all(norm(v) == norm(ours[k]) for k, v in tags.items() if norm(v))


def stamp_if_unchanged(entry: str, paths: list[str]) -> tuple[str, str]:
    """Mark Traktor as up to date with the file (MODIFIED_DATE/TIME = its mtime), so a tag re-read on deck load
    doesn't replace what we wrote, but only when the file's tags still match the entry: tags edited elsewhere
    are left for Traktor to read. Returns (entry, "" | "stamped" | "tags_changed")."""
    p = next((p for p in paths if file_stamp(p)), None)
    if p is None:
        return entry, ""
    stamp = file_stamp(p)
    if stamp == entry_stamp(entry):
        return entry, ""
    if not tags_match(entry, p):
        return entry, "tags_changed"
    return set_entry_stamp(entry, stamp), "stamped"


def collection_tracks(nml: str | None = None) -> tuple[Path, list[Path], int]:
    """Every audio file in Traktor's collection that exists on disk.
    Returns (collection path, files, number of tracks Traktor lists but that are missing)."""
    path = find_collection(nml)
    text = path.read_text(encoding="utf-8")
    c0, c1 = text.find("<COLLECTION"), text.find("</COLLECTION>")
    files, missing, seen = [], 0, set()
    for loc in re.findall(r"<LOCATION\b[^>]*>", text[c0:c1]):
        cands = location_paths(attrs(loc))
        if any("/TraktorContent/" in c for c in cands):
            continue  # Traktor's bundled loops and samples, not your tracks
        found = next((c for c in cands if os.path.isfile(c)), None)
        if not found:
            missing += 1
            continue
        if os.path.splitext(found)[1].lower() in collection.AUDIO_EXTS and found not in seen:
            seen.add(found)
            files.append(Path(found))
    return path, sorted(files), missing


def track_info(nml: str | None = None) -> dict[str, tuple[float | None, str | None]]:
    """{file path: (Traktor's BPM, Traktor's key as Camelot)} from collection.nml. These are what the
    user sees and mixes with in Traktor, and may have been corrected by hand.
    MUSICAL_KEY VALUE: 0-11 = C..B major, 12-23 = C..B minor (checked against INFO KEY)."""
    from . import analysis

    text = find_collection(nml).read_text(encoding="utf-8")
    c0, c1 = text.find("<COLLECTION"), text.find("</COLLECTION>")
    out = {}
    for entry in re.findall(r"<ENTRY\b.*?</ENTRY>", text[c0:c1], flags=re.S):
        loc = re.search(r"<LOCATION\b[^>]*>", entry)
        if not loc:
            continue
        tempo = re.search(r"<TEMPO\b[^>]*>", entry)
        key = re.search(r"<MUSICAL_KEY\b[^>]*>", entry)
        bpm = attrs(tempo.group(0)).get("BPM") if tempo else None
        kv = attrs(key.group(0)).get("VALUE") if key else None
        try:
            bpm = float(bpm) if bpm else None
        except ValueError:
            bpm = None
        cam = None
        if kv and kv.isdigit() and int(kv) < 24:
            v = int(kv)
            cam = f"{analysis.CAMELOT_MINOR[v - 12]}A" if v >= 12 else f"{analysis.CAMELOT_MAJOR[v]}B"
        if bpm or cam:
            for p in location_paths(attrs(loc.group(0))):
                out[p] = (bpm if bpm and bpm > 0 else None, cam)
    return out


def file_genre(path: str) -> str | None:
    try:
        f, mode = collection._open_tags(Path(path))
        return collection._get_tag(f, mode, "genre")
    except Exception:
        return None


def _set_info_attr(entry: str, name: str, value: str) -> tuple[str, str | None]:
    """Set one attribute on the entry's <INFO> element (creating it if needed). Returns (entry, old value)."""
    info_m = re.search(r"<INFO\b[^>]*>", entry)
    if info_m:
        old = attrs(info_m.group(0)).get(name)
        return entry[:info_m.start()] + set_attr(info_m.group(0), name, value) + entry[info_m.end():], old
    loc_m = re.search(r"<LOCATION\b[^>]*>", entry)
    loc_end = entry.find("</LOCATION>", loc_m.end())
    at = loc_end + len("</LOCATION>") if loc_end != -1 else loc_m.end()
    return entry[:at] + f'\n<INFO {name}="{esc(value)}"></INFO>' + entry[at:], None


def sync(nml: str | None = None, dry_run: bool = False, update_genres: bool = True,
         make_smartlists: bool = True, colors: bool = True, overwrite_colors: bool = False,
         color_min_confidence: float = 0.0, genres_from_analysis: bool = False) -> dict:
    """Genres from the files' tags (written by `tag`), or with genres_from_analysis straight from the analysis
    (as `tag --multi` would pick them; a genre set by hand as typed), leaving the files as they are."""
    from . import analysis

    path = find_collection(nml)
    refuse_if_tempo_view(path)
    check_closed(dry_run)
    text = path.read_text(encoding="utf-8")
    con = collection.db()
    scanned = {r["path"] for r in con.execute("SELECT path FROM tracks")}
    if not scanned:
        raise SystemExit("Nothing scanned yet. Run `uv run dj-track-analyzer scan <music folder>` and `tag` first.")
    analysed: dict[str, str] = {}
    if genres_from_analysis:
        hand = {p: f["genre"] for p, f in collection.manual().items() if "genre" in f}
        for r in con.execute("SELECT * FROM tracks WHERE error IS NULL OR error LIKE 'tags:%'"):
            # with "Use my genres" on, my_genre is your trained genre where one fits (else None: ours)
            if (g := hand.get(r["path"]) or r["my_genre"] or collection.multi_genre(dict(r))):
                analysed[r["path"]] = g
    genre_of = analysed.get if genres_from_analysis else file_genre
    predicted = {}
    if colors:
        for r in con.execute("SELECT path, color, color_confidence FROM tracks WHERE color IS NOT NULL"):
            if r["color"] in analysis.COLOR_NAMES and (r["color_confidence"] or 0) >= color_min_confidence:
                predicted[r["path"]] = analysis.COLOR_NAMES.index(r["color"]) + 1

    stats = {"collection": str(path), "traktor_tracks": 0, "matched_scanned_tracks": 0}
    if update_genres:
        stats.update(genres_updated=0, already_up_to_date=0, no_genre_tag_in_file=0)
        if genres_from_analysis:
            stats["genres_from"] = "the analysis (files' tags left as they are)"
            stats["no_genre_from_analysis"] = stats.pop("no_genre_tag_in_file")
    if colors:
        stats.update(colours_set=0, colours_kept=0, no_colour_prediction=0)
    examples, genres_seen, traktor_colors = [], set(), {}
    guessed = {}  # colours written without anyone checking them: training leaves them out until you do
    genre_held, colour_held = collection.held("genre"), collection.held("colour")

    def fix_entry(m: re.Match) -> str:
        entry = m.group(0)
        stats["traktor_tracks"] += 1
        loc_m = re.search(r"<LOCATION\b[^>]*>", entry)
        if not loc_m:
            return entry
        p = next((c for c in location_paths(attrs(loc_m.group(0))) if c in scanned), None)
        if not p:
            return entry
        stats["matched_scanned_tracks"] += 1
        if update_genres and p in genre_held:
            stats["genre_held"] = stats.get("genre_held", 0) + 1  # Traktor's genre stays as it is
            info_m = re.search(r"<INFO\b[^>]*>", entry)
            g = attrs(info_m.group(0)).get("GENRE") if info_m else None
            if g:  # still gets its Smartlist
                genres_seen.update(x.strip() for x in g.split(";") if x.strip())
        elif update_genres:
            g = genre_of(p)
            if not g:
                stats["no_genre_from_analysis" if genres_from_analysis else "no_genre_tag_in_file"] += 1
            else:
                genres_seen.update(x.strip() for x in g.split(";") if x.strip())
                info_m = re.search(r"<INFO\b[^>]*>", entry)
                if info_m and attrs(info_m.group(0)).get("GENRE") == g:
                    stats["already_up_to_date"] += 1
                else:
                    entry, old = _set_info_attr(entry, "GENRE", g)
                    stats["genres_updated"] += 1
                    if len(examples) < 15:
                        examples.append(f"{Path(p).name}: {old or '(empty)'} → {g}")
        if colors:
            info_m = re.search(r"<INFO\b[^>]*>", entry)
            cur = attrs(info_m.group(0)).get("COLOR") if info_m else None
            has = bool(cur and cur.isdigit() and 1 <= int(cur) <= len(analysis.COLOR_NAMES))
            if has:
                traktor_colors[p] = int(cur)
            want = predicted.get(p)
            if p in colour_held:
                stats["colour_held"] = stats.get("colour_held", 0) + 1
            elif want is None:
                if not has:
                    stats["no_colour_prediction"] += 1
            elif has and not overwrite_colors:
                stats["colours_kept"] += 1  # your own colour always wins
            elif not has or int(cur) != want:
                entry, _old = _set_info_attr(entry, "COLOR", str(want))
                stats["colours_set"] += 1
                traktor_colors[p] = want
                guessed[p] = want
                if len(examples) < 30:
                    was = analysis.COLOR_NAMES[int(cur) - 1] if has else "no colour"
                    examples.append(f"{Path(p).name}: {was} → {analysis.COLOR_NAMES[want - 1]}")
        return entry

    if update_genres or colors:  # only the track list itself; playlists also contain <ENTRY> elements
        c0 = text.find("<COLLECTION")
        c1 = text.find("</COLLECTION>", c0)
        if c0 == -1 or c1 == -1:
            raise SystemExit(f"{path} doesn't look like a Traktor collection (no COLLECTION section).")
        body = re.sub(r"<ENTRY\b.*?</ENTRY>", fix_entry, text[c0:c1], flags=re.S)
        new_text = text[:c0] + body + text[c1:]
    else:
        new_text = text
    if not update_genres and make_smartlists:  # still need the genre list for the Smartlists
        for p in scanned:
            g = analysed.get(p) if genres_from_analysis else file_genre(p) if os.path.exists(p) else None
            if g:
                genres_seen.update(x.strip() for x in g.split(";") if x.strip())
    if colors and not dry_run:  # remember which tracks carry which colour in Traktor
        con.executemany("UPDATE tracks SET traktor_color=? WHERE path=?",
                        [(analysis.COLOR_NAMES[c - 1], p) for p, c in traktor_colors.items()])
        con.commit()

    if make_smartlists:
        known = {g.lower(): g for g in known_genres()}
        other = sorted(g for g in genres_seen if g.lower() not in known)
        genres_seen = {known[g.lower()] for g in genres_seen if g.lower() in known}
        if other:
            stats["genre_tags_without_smartlist"] = len(other)  # the files' own tags, not from `tag`
        if genres_seen:
            new_text, replaced = install_smartlists(new_text, sorted(genres_seen))
            stats["smartlists"] = sorted(genres_seen, key=str.lower)
            stats["smartlist_folder"] = f"{FOLDER_NAME} ({'replaced' if replaced else 'added'})"
        else:
            stats["smartlists"] = []
            stats["note"] = "No genre tags found in the scanned files yet. Run `uv run dj-track-analyzer tag` first."
    stats["examples"] = examples
    out = _save(path, text, new_text, stats, dry_run)
    if out.get("changed") and not dry_run:
        collection.add_guesses(guessed)
    return out


def _save(path: Path, text: str, new_text: str, stats: dict, dry_run: bool, prefix: str = BACKUP_PREFIX) -> dict:
    """Validate, back up and write the edited collection (or only report, for a dry run)."""
    stats["dry_run"] = dry_run
    note = untested_note(path)
    if note:
        stats["untested"] = note
        log(note)
    if new_text == text:
        stats["changed"] = False
        return stats
    try:  # never write a file Traktor couldn't parse
        ET.fromstring(new_text.encode("utf-8"))
    except ET.ParseError as err:
        raise SystemExit(f"Aborted, the edited collection would not be valid XML ({err}). Nothing was changed.")
    stats["changed"] = True
    if dry_run:
        return stats
    backup = path.with_name(prefix + time.strftime("%Y%m%d-%H%M%S"))
    n = 1
    while backup.exists():  # two changes within the same second
        n += 1
        backup = path.with_name(prefix + time.strftime("%Y%m%d-%H%M%S") + f"-{n}")
    shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".track-analyzer-tmp")
    tmp.write_text(new_text, encoding="utf-8")
    os.replace(tmp, path)
    # Fingerprint of what we wrote, so --undo can tell whether Traktor changed the file since.
    _written(backup).write_text(_sha1(path))
    stats["backup"] = str(backup)
    log(f"Updated {path}\nBackup: {backup}")
    return stats


# Hype goes into Traktor's Comment (INFO COMMENT). It used to go into Comment 2 (INFO RATING; stars are
# RANKING), so a "Hype NN" still found there is taken out on the next write.
HYPE_RE = re.compile(r"\s*(?:·\s*)?Hype \d{2}\s*$")


def with_hype(comment: str | None, hype: int | None) -> str:
    """The comment with its 'Hype NN' part replaced (or removed when hype is None); other text is kept."""
    rest = HYPE_RE.sub("", comment or "").strip()
    if hype is None:
        return rest
    return f"{rest} · Hype {hype:02d}" if rest else f"Hype {hype:02d}"


def _set_or_drop_info(entry: str, name: str, value: str) -> str:
    """Set an INFO attribute, or drop it when value is empty instead of leaving NAME=""."""
    if value:
        return _set_info_attr(entry, name, value)[0]
    info_m = re.search(r"<INFO\b[^>]*>", entry)
    if not info_m:
        return entry
    return entry[:info_m.start()] + re.sub(rf'\s{name}="[^"]*"', "", info_m.group(0)) + entry[info_m.end():]


def write_hype(hype: dict[str, int], nml: str | None = None, dry_run: bool = False, remove: bool = False,
               comment2_only: bool = False, force: bool = False) -> dict:
    """Put 'Hype NN' into the Comment of scanned tracks, after any text already there, and take any
    old 'Hype NN' out of Comment 2. remove=True takes the Hype part out again everywhere;
    comment2_only=True only takes the old one out of Comment 2."""
    path = find_collection(nml)
    refuse_if_tempo_view(path)
    check_closed(dry_run)
    tag_warning = None
    if not comment2_only and tag_writing_on(path):
        tag_warning = ("Traktor is set to write tags to your files (Preferences → File Management), so it would "
                       "write 'Hype NN' into your files' comment tags. Turn that off first, or pass --force.")
        if not dry_run and not force:
            raise SystemExit(tag_warning)
    text = path.read_text(encoding="utf-8")
    stats = {"collection": str(path), "comment_updated": 0, "already_up_to_date": 0, "kept_your_text": 0,
             "moved_out_of_comment2": 0, "restamped": 0, "skipped_file_tags_changed": 0}
    examples, skipped = [], []

    def fix_entry(m: re.Match) -> str:
        entry = m.group(0)
        loc_m = re.search(r"<LOCATION\b[^>]*>", entry)
        if not loc_m:
            return entry
        paths = location_paths(attrs(loc_m.group(0)))
        if remove:
            want_hype = None
        else:
            want_hype = next((hype[p] for p in paths if p in hype), None)
        info_m = re.search(r"<INFO\b[^>]*>", entry)
        info = attrs(info_m.group(0)) if info_m else {}
        old2 = info.get("RATING")
        if old2 and HYPE_RE.search(old2):  # left over from when hype went into Comment 2
            entry = _set_or_drop_info(entry, "RATING", with_hype(old2, None))
            stats["moved_out_of_comment2"] += 1
        if comment2_only or (want_hype is None and not remove):
            return entry
        cur = info.get("COMMENT")
        new = with_hype(cur, want_hype)
        if new == (cur or "") and want_hype is None:
            return entry
        # Mark Traktor as up to date with the file, or a tag re-read replaces Comment with the file's comment.
        # Also for a Hype that's already right: its stamp may have gone stale since (a sync or tagger touched it).
        stamped, how = stamp_if_unchanged(entry, paths)
        if how == "tags_changed" and not remove:  # Traktor should read those tags first; Hype would be lost
            stats["skipped_file_tags_changed"] += 1
            if len(skipped) < 15:
                skipped.append(Path(paths[0]).name)
            return entry
        if new == (cur or ""):
            stats["already_up_to_date"] += 1
            stats["restamped"] += how == "stamped"
            return stamped
        entry = _set_or_drop_info(stamped, "COMMENT", new)
        stats["comment_updated"] += 1
        stats["kept_your_text"] += bool(HYPE_RE.sub("", cur or "").strip())
        if len(examples) < 15:
            examples.append(f"{Path(paths[0]).name}: {cur or '(empty)'} → {new or '(empty)'}")
        return entry

    c0 = text.find("<COLLECTION")
    c1 = text.find("</COLLECTION>", c0)
    if c0 == -1 or c1 == -1:
        raise SystemExit(f"{path} doesn't look like a Traktor collection (no COLLECTION section).")
    body = re.sub(r"<ENTRY\b.*?</ENTRY>", fix_entry, text[c0:c1], flags=re.S)
    stats["examples"] = examples
    if skipped:
        stats["skipped_examples"] = skipped
        stats["skipped_why"] = ("These files' tags (title, artist, album or comment) were edited since Traktor read "
                                "them. Open Traktor, load them or run Check Consistency so it reads the new tags, "
                                "quit, and run this again.")
    if tag_warning:
        stats["warning"] = tag_warning
    return _save(path, text, text[:c0] + body + text[c1:], stats, dry_run)


def set_colors(changes: dict[str, tuple[int, int]], nml: str | None = None, dry_run: bool = False) -> dict:
    """Recolour specific tracks: {path: (colour it must have now, new colour)}. A track whose colour
    in Traktor is no longer the expected one (you changed it since) is left alone."""
    from . import analysis

    path = find_collection(nml)
    refuse_if_tempo_view(path)
    check_closed(dry_run)
    text = path.read_text(encoding="utf-8")
    stats = {"collection": str(path), "colours_changed": 0, "skipped_changed_since": 0}
    examples, done, changed = [], set(), []

    def fix_entry(m: re.Match) -> str:
        entry = m.group(0)
        loc_m = re.search(r"<LOCATION\b[^>]*>", entry)
        if not loc_m:
            return entry
        p = next((c for c in location_paths(attrs(loc_m.group(0))) if c in changes), None)
        if not p:
            return entry
        expect, want = changes[p]
        done.add(p)
        info_m = re.search(r"<INFO\b[^>]*>", entry)
        cur = attrs(info_m.group(0)).get("COLOR") if info_m else None
        if cur != str(expect):
            stats["skipped_changed_since"] += 1
            return entry
        entry, _old = _set_info_attr(entry, "COLOR", str(want))
        stats["colours_changed"] += 1
        changed.append(p)
        examples.append(f"{Path(p).name}: {analysis.COLOR_NAMES[expect - 1]} → {analysis.COLOR_NAMES[want - 1]}")
        return entry

    c0 = text.find("<COLLECTION")
    c1 = text.find("</COLLECTION>", c0)
    if c0 == -1 or c1 == -1:
        raise SystemExit(f"{path} doesn't look like a Traktor collection (no COLLECTION section).")
    body = re.sub(r"<ENTRY\b.*?</ENTRY>", fix_entry, text[c0:c1], flags=re.S)
    stats["not_in_collection"] = len(changes) - len(done)
    stats["changes"] = examples
    out = _save(path, text, text[:c0] + body + text[c1:], stats, dry_run)
    if out.get("changed") and not dry_run:
        collection.accept_guesses(changed)  # you said yes to these: yours now, even where the app had guessed
    return out


def camelot_value(cam: str) -> int:
    """'6A' → MUSICAL_KEY VALUE 19 (0-11 = C..B major, 12-23 = C..B minor)."""
    from . import analysis

    n, minor = int(cam[:-1]), cam[-1].upper() == "A"
    pc = next(pc for pc, c in (analysis.CAMELOT_MINOR if minor else analysis.CAMELOT_MAJOR).items() if c == n)
    return pc + 12 if minor else pc


def open_key(cam: str) -> str:
    """'6A' → '11m', Traktor's INFO KEY text (Open Key = Camelot + 5, d for major)."""
    return f"{(int(cam[:-1]) + 4) % 12 + 1}{'m' if cam[-1].upper() == 'A' else 'd'}"


def read_track(path: str, nml: str | None = None) -> dict | None:
    """What Traktor has for one track now: {colour 1-7, camelot, genre, comment}, or None if it isn't listed."""
    text = find_collection(nml).read_text(encoding="utf-8")
    c0, c1 = text.find("<COLLECTION"), text.find("</COLLECTION>")
    for entry in re.findall(r"<ENTRY\b.*?</ENTRY>", text[c0:c1], flags=re.S):
        loc = re.search(r"<LOCATION\b[^>]*>", entry)
        if not loc or path not in location_paths(attrs(loc.group(0))):
            continue
        info_m, key_m = re.search(r"<INFO\b[^>]*>", entry), re.search(r"<MUSICAL_KEY\b[^>]*>", entry)
        info = attrs(info_m.group(0)) if info_m else {}
        kv = attrs(key_m.group(0)).get("VALUE", "") if key_m else ""
        cam = None
        if kv.isdigit() and int(kv) < 24:
            from . import analysis

            v = int(kv)
            cam = f"{analysis.CAMELOT_MINOR[v - 12]}A" if v >= 12 else f"{analysis.CAMELOT_MAJOR[v]}B"
        c = info.get("COLOR", "")
        return {"colour": int(c) if c.isdigit() and 1 <= int(c) <= 7 else None, "camelot": cam,
                "genre": info.get("GENRE"), "comment": info.get("COMMENT")}
    return None


def set_track(path: str, colour: int | None = None, camelot: str | None = None, genre: str | None = None,
              hype: int | None = None, nml: str | None = None, dry_run: bool = False) -> dict:
    """One track's values set by hand: INFO COLOR, the key (MUSICAL_KEY VALUE + INFO KEY as Open Key), INFO
    GENRE, and the Hype in Comment only where Comment already carries one (from `hype --traktor`) and Traktor
    doesn't write tags into the files. None = leave as it is. Backup + `traktor --undo` as every write."""
    p = find_collection(nml)
    refuse_if_tempo_view(p)
    check_closed(dry_run, "this edit")
    text = p.read_text(encoding="utf-8")
    stats = {"collection": str(p), "changed_fields": []}
    found = []

    def fix_entry(m: re.Match) -> str:
        entry = m.group(0)
        loc_m = re.search(r"<LOCATION\b[^>]*>", entry)
        if not loc_m or path not in (paths := location_paths(attrs(loc_m.group(0)))):
            return entry
        found.append(1)
        info_m = re.search(r"<INFO\b[^>]*>", entry)
        info = attrs(info_m.group(0)) if info_m else {}
        if colour is not None and info.get("COLOR") != str(colour):
            entry = _set_info_attr(entry, "COLOR", str(colour))[0]
            stats["changed_fields"].append("colour")
        if genre is not None and (info.get("GENRE") or "") != genre:  # "" takes it off
            entry = _set_or_drop_info(entry, "GENRE", genre)
            stats["changed_fields"].append("genre")
        if camelot is not None:
            v = str(camelot_value(camelot))
            key_m = re.search(r"<MUSICAL_KEY\b[^>]*>", entry)
            if not key_m or attrs(key_m.group(0)).get("VALUE") != v:
                if key_m:
                    entry = entry[:key_m.start()] + set_attr(key_m.group(0), "VALUE", v) + entry[key_m.end():]
                else:  # where Traktor puts it: after LOUDNESS, else TEMPO, else INFO
                    after = next((x for x in (re.search(rf"<{t}\b[^>]*>.*?</{t}>", entry, re.S)
                                              for t in ("LOUDNESS", "TEMPO", "INFO")) if x), None)
                    at = after.end() if after else entry.rfind("</ENTRY>")
                    entry = entry[:at] + f'\n<MUSICAL_KEY VALUE="{v}"></MUSICAL_KEY>' + entry[at:]
                stats["changed_fields"].append("key")
            if info.get("KEY") != open_key(camelot):
                entry = _set_info_attr(entry, "KEY", open_key(camelot))[0]
                if "key" not in stats["changed_fields"]:
                    stats["changed_fields"].append("key")
        new = with_hype(info.get("COMMENT"), hype) if hype is not None else None
        if new is not None and HYPE_RE.search(info.get("COMMENT") or "") and new != info.get("COMMENT"):
            if tag_writing_on(p):
                stats["hype_comment_skipped"] = ("Traktor writes tags into your files, so the Hype in Comment "
                                                 "wasn't changed (it would go into the file's comment)")
            else:
                entry = _set_info_attr(entry, "COMMENT", new)[0]
                stats["changed_fields"].append("hype comment")
        if stats["changed_fields"]:  # or Traktor reads the file's tags on deck load and they replace these
            entry, how = stamp_if_unchanged(entry, paths)
            if how == "tags_changed":
                stats["warning"] = ("This file's tags changed since Traktor read them: when Traktor reads them "
                                    "again, the file's genre/colour/comment may replace what was set here")
        return entry

    c0 = text.find("<COLLECTION")
    c1 = text.find("</COLLECTION>", c0)
    if c0 == -1 or c1 == -1:
        raise SystemExit(f"{p} doesn't look like a Traktor collection (no COLLECTION section).")
    body = re.sub(r"<ENTRY\b.*?</ENTRY>", fix_entry, text[c0:c1], flags=re.S)
    if not found:
        stats["not_in_collection"] = True
        stats["changed"] = False
        return stats
    return _save(p, text, text[:c0] + body + text[c1:], stats, dry_run)


WRITTEN_SUFFIX = ".written-sha1"


def _sha1(path: Path) -> str:
    import hashlib

    return hashlib.sha1(path.read_bytes()).hexdigest()


def _written(backup: Path) -> Path:
    return backup.with_name(backup.name + WRITTEN_SUFFIX)


def undo(nml: str | None = None, force: bool = False) -> dict:
    """Put back the collection from before our last change. Refuses (unless force) when the file
    changed after we wrote it, since restoring would also throw away what Traktor saved since
    (plays, cue points, new tracks, your own edits)."""
    path = find_collection(nml)
    refuse_if_tempo_view(path)
    check_closed()
    backups = sorted(b for b in path.parent.glob(BACKUP_PREFIX + "*")
                     if not b.name.endswith(".restored") and not b.name.endswith(WRITTEN_SUFFIX))
    if not backups:
        raise SystemExit(f"No Puppi3's Track Analyzer backups next to {path}")
    latest = backups[-1]
    mark = _written(latest)
    if not force:
        if not mark.exists():
            raise SystemExit(f"Can't check whether Traktor changed {path.name} since {latest.name} was made "
                             "(that backup is from an older version). Restoring would lose anything Traktor saved "
                             "since then. Run again with --force to restore anyway.")
        if mark.read_text().strip() != _sha1(path):
            raise SystemExit(f"{path.name} has changed since Puppi3's Track Analyzer wrote it (Traktor saved plays, cues or "
                             f"edits). Restoring {latest.name} would lose those. Run again with --force to restore "
                             "anyway.")
    shutil.copy2(latest, path)
    latest.rename(latest.with_name(latest.name + ".restored"))
    if mark.exists():
        mark.unlink()
    return {"restored_from": str(latest), "collection": str(path)}
