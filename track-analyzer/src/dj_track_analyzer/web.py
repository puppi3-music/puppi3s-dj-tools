"""`web` command: a local web page over the scanned collection, the colour model, the tempo views and saved
mix analyses. Its tabs call the same functions as the CLI and only read (hype.find(rescore=False) and
colors.explain(extract=False) keep those two from starting work). Changes made right here: the hold list
(collection.held), cue approvals, cue writes and saved loop samples. The Setup tab runs the CLI's own commands in
the background (jobs.py): setup, scans, training and, after a dry-run preview, the writers. `--export` / the Export
button save a snapshot: one self-contained, read-only HTML file with the data inside."""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import analysis, collection, jobs, traktor
from .analysis import COLOR_NAMES, HOME, log

PAGE = Path(__file__).with_name("web") / "index.html"
LOGS = Path(__file__).resolve().parents[2] / "logs"  # where the docs suggest saving run output
# collection.DB, the cache and the tempo-view state are module globals: one request at a time swaps them
_lock = threading.RLock()
TRACK_COLS = ["path", "artist", "title", "filename", "album", "folder", "type", "duration_sec", "bpm", "traktor_bpm",
              "key", "camelot", "traktor_camelot", "lufs", "loudness_range", "genre", "genre_score", "genre_2",
              "genre_2_score", "genre_3", "genre_3_score", "my_genre", "my_genre_confidence", "tag_genre", "styles",
              "traktor_color", "color", "color_position", "color_confidence", "mood_aggressive", "mood_happy",
              "mood_party", "mood_relaxed", "mood_sad", "instruments", "approachability", "hype", "error",
              "scanned_at"]


def libraries() -> dict:
    """The normal library plus every tempo view in data/, and which view Traktor has loaded."""
    from . import tempo

    libs = [{"id": "native", "label": "Normal (each track at its own tempo)", "bpm": None}]
    for d in sorted(HOME.glob("at-*")):
        if (d / "collection.db").exists():
            try:
                bpm = float(d.name[3:])
            except ValueError:
                continue
            libs.append({"id": d.name, "label": f"{bpm:g} BPM tempo view", "bpm": bpm})
    st = tempo.loaded()
    return {"libraries": libs, "loaded_view": st["bpm"] if st else None, "status": tempo.status(),
            "home": str(HOME), "colours": COLOR_NAMES}


@contextmanager
def library(lib: str):
    """Point collection.DB and the analysis cache at a library for one request, then put them back."""
    from . import tempo

    saved = (collection.DB, collection.DEFAULT_CSV, analysis.CACHE_DB, analysis.CACHE_VERSION)
    with _lock:
        try:
            if lib and lib != "native":
                tempo.view_db(float(lib.removeprefix("at-"))).close()
            yield
        finally:
            collection.DB, collection.DEFAULT_CSV, analysis.CACHE_DB, analysis.CACHE_VERSION = saved


def tracks(lib: str = "native") -> dict:
    with library(lib):
        con = collection.db()
        have = {r[1] for r in con.execute("PRAGMA table_info(tracks)")}
        cols = [c for c in TRACK_COLS + ["source_bpm", "native_color_position"] if c in have]
        rows = [{k: r[k] for k in cols if r[k] is not None} for r in
                con.execute(f"SELECT {', '.join(cols)} FROM tracks ORDER BY artist, title")]
    genre_held, colour_held = collection.held("genre"), collection.held("colour")
    from . import cues

    approved = cues.plans()
    fixes = correction_counts()
    try:
        g = cue_grids()
    except SystemExit:  # no Traktor collection found
        g = {}
    for r in rows:
        if r["path"] in approved:
            r["cues_approved"] = True
        if fixes and fixes.get(r["path"]):
            r["corrections"] = fixes[r["path"]]
        if r["path"] in g:  # how many cue points / loops you set in Traktor
            r["own_cues"] = len(g[r["path"]]["cues"])  # as the CLI shows them: whole BPMs, Traktor's value where it has one
        r["mix_bpm"] = analysis.round_bpm(r.get("traktor_bpm") or r.get("bpm"))
        r["mix_camelot"] = r.get("traktor_camelot") or r.get("camelot")
        if r["path"] in genre_held:
            r["held_genre"] = True
        if r["path"] in colour_held:
            r["held_colour"] = True
    return {"tracks": rows, **({"corrections_pending": True} if fixes is None else {})}


def stats(lib: str = "native") -> dict:
    with library(lib):
        return collection.summary()


def suggest(lib: str, path: str, tolerance: float = 4.0, same_genre: bool = True, limit: int = 30) -> dict:
    with library(lib):
        return collection.mix_suggestions(path, bpm_tolerance_pct=tolerance, same_genre=same_genre, limit=limit)


def hype(lib: str = "native", **filters) -> dict:
    from . import hype as h

    with library(lib):
        return h.find(rescore=False, **filters)


def colour_review(limit: int = 200) -> dict:
    from . import colors

    with library("native"):
        return colors.review(limit=limit)


def colour_explain() -> dict:
    from . import colors

    with library("native"):
        return colors.explain(extract=False)


def colour_kept() -> dict:
    from . import colors

    with library("native"):
        return colors.kept()


def tempo_report(bpm: float, limit: int = 25) -> dict:
    from . import tempo

    with library("native"):
        return tempo.report(bpm, limit=limit)


def hold(paths: list[str], kind: str, on: bool) -> dict:
    """Hold or release the genre / colour of these tracks (the value at the time is kept for display)."""
    if kind not in collection.HOLDS:
        raise ValueError(f"kind is genre or colour, not {kind!r}")
    with library("native"):
        con = collection.db()
        rows = {r["path"]: r for r in con.execute(
            f"SELECT path, tag_genre, traktor_color FROM tracks WHERE path IN ({','.join('?' * len(paths))})", paths)}
    if len(rows) != len(set(paths)):
        raise ValueError("some of these tracks aren't scanned")
    values = {p: r["tag_genre"] if kind == "genre" else r["traktor_color"] for p, r in rows.items()}
    collection.set_hold(kind, values, on)
    return {"kind": kind, "on": on, "tracks": len(values), "held": len(collection.held(kind))}


_grids: dict = {"mtime": None, "data": {}}


def cue_grids() -> dict:
    """Traktor's beat grids and your cues, read again only when collection.nml changes."""
    from . import cues

    m = traktor_nml().stat().st_mtime
    if _grids["mtime"] != m:
        _grids.update(mtime=m, data=cues.grids())
    return _grids["data"]


def traktor_nml() -> Path:
    from . import traktor

    return traktor.find_collection(None)


_fixes: dict = {"key": None, "counts": None, "busy": False}
FIXES_FILE = HOME / "cue-corrections.json"  # the last count, reused while the model and collection are unchanged


def correction_counts() -> dict[str, int] | None:
    """{path: proposed corrections of your cues}, worked out in the background (≈10 s for 470 tracks) and again
    only when the cue model or Traktor's collection changes. None while counting."""
    from . import cues

    try:
        key = (cues.MODEL.stat().st_mtime, traktor_nml().stat().st_mtime)
    except (OSError, SystemExit):
        return {}
    if _fixes["key"] == key:
        return _fixes["counts"]
    try:  # counted before, by an earlier run of the server, with the same model and collection
        saved = json.loads(FIXES_FILE.read_text())
        if tuple(saved["key"]) == key:
            _fixes.update(key=key, counts=saved["counts"])
            return _fixes["counts"]
    except (OSError, ValueError, KeyError):
        pass
    if not _fixes["busy"]:
        _fixes["busy"] = True

        def work():
            counts = {}
            try:
                for p, d in cues._one_path_each(cue_grids()):
                    if not d["cues"]:
                        continue
                    with library("native"):  # one track at a time, so other requests aren't held up
                        try:
                            counts[p] = len(cues.pending_corrections(p, cues.suggest(p, d["bpm"], d["grid_ms"], own=d["cues"])["corrections"]))
                        except SystemExit:
                            pass
                _fixes.update(key=key, counts=counts)
                FIXES_FILE.write_text(json.dumps({"key": list(key), "counts": counts}))
            finally:
                _fixes["busy"] = False

        threading.Thread(target=work, daemon=True).start()
    return None


def recount(path: str) -> int:
    """One track's pending corrections again (after approving, ignoring or a rescan), kept in the saved counts."""
    from . import cues

    d = cue_grids().get(path)
    n = 0
    if d and d["cues"]:
        with library("native"):
            try:
                n = len(cues.pending_corrections(path, cues.suggest(path, d["bpm"], d["grid_ms"], own=d["cues"])["corrections"]))
            except SystemExit:
                n = 0
    if _fixes["counts"] is not None:
        _fixes["counts"][path] = n
        try:
            FIXES_FILE.write_text(json.dumps({"key": list(_fixes["key"]), "counts": _fixes["counts"]}))
        except (OSError, TypeError):
            pass
    return n


def ignore_correction(path: str, f: dict, on: bool) -> dict:
    from . import cues

    cues.set_ignore(path, int(f["hotcue"]), float(f["from_ms"]), float(f["to_ms"]), on)
    return {"corrections": recount(path)}


def rescan_track(path: str) -> dict:
    """Measure the track's audio again and re-read its grid and cues from Traktor's collection."""
    from . import cues

    playable(path)
    _grids["mtime"] = None  # re-read collection.nml
    d = cue_grids().get(path)
    if not d:
        raise ValueError("Traktor has no beat grid for this track")
    with library("native"):
        cues.forget_track(path)
        cues.bars(Path(path), d["bpm"], d["grid_ms"])
    _wave_src.pop(path, None)
    return {"rescanned": True, "corrections": recount(path)}


WRITTEN_PLANS = HOME / "cues-last-written.json"  # the approvals the last write used, so Undo can bring them back


def write_cues(dry_run: bool) -> dict:
    """`cues apply` from the page: a preview first (dry_run), then the write. Refuses while Traktor is open."""
    from . import cues, traktor

    if jobs.busy():
        raise ValueError(f"Busy with: {jobs.state()['job']['label']}. Wait for it to finish first.")
    with library("native"):
        before = cues.plans()
        if not before:
            return {"changed": False, "nothing_approved": True, "traktor_running": traktor.traktor_running()}
        try:
            out = cues.apply(dry_run=dry_run)
        except SystemExit as e:
            raise ValueError(str(e)) from None
    out["traktor_running"] = traktor.traktor_running()
    out["approved_tracks"] = len(before)
    if not dry_run and out.get("changed"):
        WRITTEN_PLANS.write_text(json.dumps({"backup": out["backup"], "plans": before}))
        _grids["mtime"] = None
        _wave_src.clear()
    return out


def undo_write() -> dict:
    """Put back the collection from before the last write (`traktor --undo`) and the approvals it used."""
    from . import cues, traktor

    if jobs.busy():
        raise ValueError(f"Busy with: {jobs.state()['job']['label']}. Wait for it to finish first.")
    try:
        out = traktor.undo()
    except SystemExit as e:
        raise ValueError(str(e)) from None
    try:
        saved = json.loads(WRITTEN_PLANS.read_text())
    except (OSError, ValueError):
        saved = None
    if saved and saved.get("backup") == out["restored_from"]:
        with library("native"), cues._plan_db() as con:
            con.executemany("INSERT OR REPLACE INTO cue_plan VALUES (?,?,?,?)",
                            [(p, v["grid"], json.dumps(v["plan"]), v["at"]) for p, v in saved["plans"].items()])
        WRITTEN_PLANS.unlink()
        out["approvals_restored"] = len(saved["plans"])
    _grids["mtime"] = None
    _wave_src.clear()
    return out


def track_cues(path: str) -> dict:
    """Your cues in Traktor and the suggested ones (when the cue model is trained and the track measured)."""
    from . import cues

    d = cue_grids().get(path)
    if not d:
        raise SystemExit("Traktor has no beat grid for this track: load it in Traktor once so it gets analysed.")
    out = {"path": path, "yours": d["cues"], "grid_ms": d["grid_ms"], "bpm": d["bpm"],
           "free_slots": 8 - len({c["hotcue"] for c in d["all_cues"]} - {-1}), "file_manager": FILE_MANAGER}
    pl = cues.plans().get(path)
    if pl:
        changed = not cues.same_grid(pl["grid"], d["bpm"], d["grid_ms"])
        out["approved"] = {**pl["plan"], "at": pl["at"], "grid_changed": changed}
        if not changed:  # your cues as they'll be in Traktor once written
            out["approved_view"] = cues.preview(d, pl["plan"])
    with library("native"):  # the measurements live in the normal analysis cache
        try:
            out["saved_samples"] = saved_samples(path)
        except OSError:
            out["saved_samples"] = []
        try:
            out.update(cues.suggest(path, d["bpm"], d["grid_ms"], own=d["cues"]))
        except SystemExit as e:
            out["note"] = str(e.code or e)
    return out


_wave_src: dict = {}  # path -> cues.WaveSource, the decoded audio of the last WAVE_KEEP tracks zoomed into
WAVE_KEEP = 2  # ≈65 MB each for a 6-minute track
_wave_lock = threading.Lock()


def playable(path: str) -> Path:
    """Only files in the scanned library can be played (never an arbitrary path)."""
    import sqlite3

    with sqlite3.connect(collection.NATIVE_DB) as con:
        ok = con.execute("SELECT 1 FROM tracks WHERE path=?", (path,)).fetchone()
    if not ok or not Path(path).is_file():
        raise ValueError("not a track in your scanned library")
    return Path(path)


DECK_AUDIO = HOME / "deck-audio"  # the page's player: tracks shifted onto Traktor's timeline, the last few played
DECK_KEEP = 6
_deck_locks: dict[str, threading.Lock] = {}


def deck_audio(path: str) -> Path:
    """The file the page's player plays: the track itself, or for an MP3 that Traktor times differently than decoders
    do (traktor.audio_offset), a lossless copy with that offset in front, so the player's time is Traktor's time
    whatever the browser's MP3 decoder does. Made once (≈5 s) and kept for the last DECK_KEEP tracks."""
    import hashlib
    import subprocess

    p = playable(path)
    off = traktor.audio_offset(p)
    if not off:
        return p
    st = p.stat()
    out = DECK_AUDIO / (hashlib.sha1(f"{p}|{st.st_size}|{st.st_mtime}|{off}".encode()).hexdigest() + ".flac")
    with _deck_locks.setdefault(out.name, threading.Lock()):
        if not out.exists():
            DECK_AUDIO.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp.flac")
            res = subprocess.run([_ffmpeg(), "-v", "error", "-y", "-i", str(p), "-map", "0:a:0", "-af", f"adelay=delays={off}S:all=1",
                                  "-c:a", "flac", "-compression_level", "0", "-sample_fmt", "s32", str(tmp)], capture_output=True, timeout=300)
            if res.returncode or not tmp.exists():
                raise ValueError(f"ffmpeg couldn't prepare the audio: {res.stderr.decode(errors='replace')[:200]}")
            tmp.replace(out)
            for old in sorted(DECK_AUDIO.glob("*.flac"), key=lambda f: f.stat().st_mtime, reverse=True)[DECK_KEEP:]:
                old.unlink(missing_ok=True)
        else:
            out.touch()
    return out


def wave_detail(path: str, t0: float | None = None, t1: float | None = None, n: int | None = None) -> dict:
    """Detailed waveform for zooming: the whole track at 100 slices a second, or with t0/t1 just that stretch in n
    slices (as fine as the screen needs, down to single samples). Cut from audio decoded once per server run."""
    import base64

    from . import cues

    if path not in cue_grids():
        raise ValueError("not a track in Traktor's collection")
    with _wave_lock:  # one decode per track, even when the page asks for several windows at once
        if path not in _wave_src:
            while len(_wave_src) >= WAVE_KEEP:
                _wave_src.pop(next(iter(_wave_src)))
            _wave_src[path] = cues.WaveSource(Path(path))
        src = _wave_src.pop(path)
        _wave_src[path] = src  # most recently used last
    if t0 is None or t1 is None:
        t0, t1, n = 0.0, src.dur, max(10, int(src.dur * 100))
    else:
        n = min(int(n or 1000), 20000)  # a window: a few screens' worth of pixels at most
    t0, t1 = max(0.0, t0), min(src.dur, t1)
    if t1 <= t0:
        raise ValueError("empty stretch")
    w = src.cut(t0, t1, n)
    return {"slices": len(w), "t0": round(t0, 6), "seconds": round(t1 - t0, 6), "data": base64.b64encode(w.tobytes()).decode()}


PRE_ROLL = 0.5  # seconds decoded before a loop and thrown away


def _cut(src: Path, start: float, length: float) -> list[str]:
    """ffmpeg input arguments for exactly the samples [start, start + length) of the decoded track. Seeking straight to
    `start` starts an MP3/AAC decoder cold, and its first ~15 ms come out wrong (a click each time the loop comes
    round), so decoding starts PRE_ROLL earlier and the extra is cut off sample-accurately after decoding."""
    a = max(0.0, start - PRE_ROLL)
    return ["-ss", f"{a:.6f}", "-i", str(src), "-ss", f"{start - a:.6f}", "-t", f"{length:.6f}"]


SMOOTH_MS = 5  # "smooth loop point": crossfade length
EDGE_FADE = 64  # samples faded at each end, as in Traktor's own captures (gain √(n/64)): Traktor reads a little past a
# sample's end when it loops, and a loop that ends at full level clicks there; one that ends at 0 doesn't
SAMPLE_FORMAT = 3  # saved samples: 2 = edge fades, 3 = also on Traktor's MP3 timeline (older ones can be saved again)


def _ffmpeg() -> str:
    exe = analysis.find_tool("ffmpeg")
    if not exe:
        raise ValueError(f"ffmpeg isn't installed ({analysis.install_hint('ffmpeg')})")
    return exe


def _render_loop(src: Path, start: float, length: float, smooth: bool, out_args: list[str], timeout: int = 60):
    """Run ffmpeg to write the loop [start, start + length) with out_args (codec, metadata, output): the track's exact
    samples, with Traktor's EDGE_FADE at both ends. smooth: also the loop's last SMOOTH_MS fade (raised cosine) into the
    audio just before its start, so the end runs into the start as it does in the track. start is in Traktor's time
    (its beat grid), converted to ffmpeg's with traktor_offset."""
    import subprocess

    import numpy as np

    exe = _ffmpeg()
    info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate,channels",
                           "-of", "csv=p=0", str(src)], capture_output=True, text=True, timeout=30).stdout.strip().split(",")
    sr, ch = int(info[0]), int(info[1])
    start = max(0.0, start - traktor.audio_offset(src) / sr)
    pre = min(round(SMOOTH_MS / 1000 * sr), int(start * sr)) if smooth else 0  # whole samples: the loop's samples stay the same
    raw = subprocess.run([exe, "-v", "error", *_cut(src, start - pre / sr, length + pre / sr), "-map", "0:a:0",
                          "-f", "f32le", "-acodec", "pcm_f32le", "-"], capture_output=True, timeout=timeout)
    if raw.returncode or not raw.stdout:
        return raw
    x = np.frombuffer(raw.stdout, np.float32).reshape(-1, ch)
    y = x[pre:].copy()
    n = min(pre, len(y) // 2)
    if n:
        fade = (0.5 - 0.5 * np.cos(np.linspace(0, np.pi, n, dtype=np.float32)))[:, None]  # 0 → 1, the two weights sum to 1
        y[-n:] = y[-n:] * (1 - fade) + x[pre - n:pre] * fade
    e = min(EDGE_FADE, len(y) // 4)
    g = np.sqrt(np.arange(e, dtype=np.float32) / EDGE_FADE)[:, None]
    y[:e] *= g
    y[len(y) - e:] *= g[::-1]
    return subprocess.run([exe, "-v", "error", "-f", "f32le", "-ar", str(sr), "-ac", str(ch), "-i", "pipe:0", *out_args],
                          input=y.astype(np.float32).tobytes(), capture_output=True, timeout=timeout)


def loop_clip(path: str, start: float, length: float, smooth: bool = False) -> bytes:
    """Just the loop, cut sample-accurately by ffmpeg as float WAV (~0.15 s for 4 bars), so the page can loop it
    gaplessly without decoding the whole track first."""
    p = playable(path)
    if not (0 <= start < 3600 and 0 < length <= 120):
        raise ValueError("start / length out of range")
    out = _render_loop(p, start, length, smooth, ["-f", "wav", "-acodec", "pcm_f32le", "-"], timeout=30)
    if out.returncode or not out.stdout:
        raise ValueError(f"ffmpeg couldn't cut the loop: {out.stderr.decode(errors='replace')[:200]}")
    return out.stdout


LOOPS_FOLDER = "Track Analyzer loops"  # made inside Traktor's sample folder on the first save there


def sample_root() -> Path:
    """Traktor's sample folder (Preferences → File Management; Browser.Dir.Loops in Traktor Settings.tsi)."""
    from . import traktor

    try:
        v = traktor.setting(traktor_nml(), "Browser.Dir.Loops")
        p = traktor.mac_path(v) if v else None
    except SystemExit:
        p = None
    return Path(p) if p else Path.home() / "Music" / "Traktor Samples"


def sample_folders() -> dict:
    root = sample_root()
    folders = sorted(d.name for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")) if root.is_dir() else []
    return {"root": str(root), "folders": sorted(set(folders) | {LOOPS_FOLDER}), "default": LOOPS_FOLDER,
            "file_manager": FILE_MANAGER}


FILE_MANAGER = {"darwin": "Finder", "win32": "Explorer"}.get(sys.platform, "file manager")


def _show(p: Path, select: bool) -> None:
    """Open the system's file manager on a folder, or on a file's folder with the file selected."""
    import subprocess

    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(p)] if select else ["open", str(p)], check=False)
    elif sys.platform == "win32":
        subprocess.run(["explorer", f"/select,{p}"] if select else ["explorer", str(p)], check=False)
    else:  # Linux: xdg-open can't select a file, so it opens the folder
        subprocess.run(["xdg-open", str(p.parent if select else p)], check=False)


def reveal_sample(file: str | None = None, folder: str | None = None) -> dict:
    """Show a saved sample (or a sample folder) in the file manager. Only inside Traktor's sample folder."""
    root = sample_root().resolve()
    if file:
        p = Path(file).resolve()
        if root not in p.parents or not p.is_file():
            raise ValueError("not a saved sample")
        _show(p, select=True)
    else:
        p = (root / (folder or LOOPS_FOLDER)).resolve()
        if p != root and root not in p.parents:
            raise ValueError("not a sample folder")
        _show(p if p.is_dir() else root, select=False)
    return {"shown": str(p)}


def _wav_codec(src: Path) -> str:
    """Keep the source's resolution: 16/24/32-bit for lossless files; 32-bit float for decoded lossy ones (MP3, AAC):
    loud MP3 masters decode above full scale (a test loop peaked at 1.049) and an integer WAV would clip those peaks,
    where Traktor, playing the MP3 in floating point, doesn't."""
    import subprocess

    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                          "stream=codec_name,sample_fmt,bits_per_raw_sample,bits_per_sample", "-of", "json", str(src)],
                         capture_output=True, text=True, timeout=30)
    st = (json.loads(out.stdout or "{}").get("streams") or [{}])[0]
    lossless = st.get("codec_name", "").startswith("pcm_") or st.get("codec_name") in ("flac", "alac", "wavpack", "ape")
    bits = int(st.get("bits_per_raw_sample") or st.get("bits_per_sample") or 0)
    if lossless and st.get("sample_fmt", "").startswith("flt"):
        return "pcm_f32le"
    if lossless and bits == 16:
        return "pcm_s16le"
    if lossless and bits == 32 and st.get("sample_fmt", "").startswith("s32") and not st.get("bits_per_raw_sample"):
        return "pcm_s32le"
    return "pcm_s24le" if lossless else "pcm_f32le"


_SAMPLE_NOTE = re.compile(r"Loop from (.+) at ([\d.]+) s, ([\d.]+) s(?:, (\d+) ms crossfade)?(, edge fades)?(, Traktor time)?")
_samples_read: set[Path] = set()  # sample files whose comment was read this server run


def _saved_db():
    """{source file name, start, length, saved file} for every loop saved as a sample. Samples saved before this
    list existed are found by the comment each one carries ("Loop from <file> at <s> s, <len> s")."""
    import sqlite3
    import subprocess

    con = sqlite3.connect(collection.NATIVE_DB)
    con.execute("CREATE TABLE IF NOT EXISTS saved_sample (file TEXT PRIMARY KEY, src_name TEXT, start REAL, len REAL)")
    cols = {r[1] for r in con.execute("PRAGMA table_info(saved_sample)")}
    if "smooth" not in cols:
        con.execute("ALTER TABLE saved_sample ADD COLUMN smooth INTEGER DEFAULT 0")
    if "format" not in cols:
        con.execute("ALTER TABLE saved_sample ADD COLUMN format INTEGER DEFAULT 1")
    root = sample_root()
    known = {r[0] for r in con.execute("SELECT file FROM saved_sample")}
    for f in root.rglob("* from bar *.wav") if root.is_dir() else []:
        if str(f) in known or f in _samples_read:
            continue
        _samples_read.add(f)
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=comment", "-of", "default=nw=1:nk=1", str(f)],
                             capture_output=True, text=True, timeout=30)
        m = _SAMPLE_NOTE.match(out.stdout.strip())
        if m:
            con.execute("INSERT OR REPLACE INTO saved_sample VALUES (?,?,?,?,?,?)",
                        (str(f), m[1], float(m[2]), float(m[3]), int(bool(m[4])), 3 if m[6] else 2 if m[5] else 1))
    con.commit()
    return con


def saved_samples(path: str) -> list[dict]:
    """This track's loops already saved as samples (files that still exist)."""
    con = _saved_db()
    try:
        rows = con.execute("SELECT file, start, len, smooth FROM saved_sample WHERE src_name=? AND format=?",
                           (Path(path).name, SAMPLE_FORMAT)).fetchall()
    finally:
        con.close()
    return [{"file": f, "folder": Path(f).parent.name, "start": a, "len": n, "smooth": bool(sm)}
            for f, a, n, sm in rows if Path(f).is_file()]


def save_loop(path: str, start: float, length: float, folder: str, bar: int, kind: str, smooth: bool = False) -> dict:
    """Save a loop as a WAV sample in Traktor's sample folder: the exact samples (cut by ffmpeg, as for playing),
    at the source's sample rate; smooth = the loop point crossfaded (`_render_loop`). A loop already saved (same file,
    start, length and smoothing, in any sample folder) isn't saved again. Never overwrites: a name that exists gets (2)…"""
    import sqlite3

    src = playable(path)
    if not (0 <= start < 3600 and 0 < length <= 120):
        raise ValueError("start / length out of range")
    for had in saved_samples(path):
        if abs(had["start"] - start) < 0.002 and abs(had["len"] - length) < 0.002 and had["smooth"] == smooth:
            return {"saved": had["file"], "folder": had["folder"], "file": Path(had["file"]).name, "already": True}
    if folder not in sample_folders()["folders"]:
        raise ValueError("unknown sample folder")
    _ffmpeg()
    with sqlite3.connect(collection.NATIVE_DB) as con:
        artist, title, bpm = con.execute(
            f"SELECT artist, COALESCE(title, filename), {collection.BPM_SQL} FROM tracks WHERE path=?", (path,)).fetchone()
    bars = round(length / (4 * 60 / bpm)) if bpm else None
    clean = lambda t: re.sub(r'[/:\\\x00-\x1f]+', "-", str(t)).strip()  # noqa: E731
    tail = clean(f" - loop {bars or ''} bars from bar {bar} ({kind}{', smooth' if smooth else ''}){f' {analysis.round_bpm(bpm)} BPM' if bpm else ''}")
    name = clean(f"{artist + ' - ' if artist else ''}{title}")[:150 - len(tail)].rstrip(" -") + " " + tail.lstrip()
    out_dir = sample_root() / folder
    out_dir.mkdir(parents=True, exist_ok=True)
    out, n = out_dir / f"{name}.wav", 1
    while out.exists():
        n += 1
        out = out_dir / f"{name} ({n}).wav"
    note = (f"Loop from {src.name} at {start:.3f} s, {length:.3f} s{f', {SMOOTH_MS} ms crossfade' if smooth else ''}, edge fades, "
            "Traktor time, "
            "saved by Track Analyzer")
    res = _render_loop(src, start, length, smooth,
                       ["-map", "0:a:0", "-map_metadata", "-1", "-c:a", _wav_codec(src),
                        "-metadata", f"title={clean(title)} (loop, bar {bar})", "-metadata", f"artist={clean(artist or '')}",
                        "-metadata", f"comment={note}", "-n", str(out)])
    if res.returncode or not out.exists():
        raise ValueError(f"ffmpeg couldn't save the loop: {res.stderr.decode(errors='replace')[:200]}")
    with sqlite3.connect(collection.NATIVE_DB) as con:
        con.execute("INSERT OR REPLACE INTO saved_sample VALUES (?,?,?,?,?,?)",
                    (str(out), src.name, round(start, 3), round(length, 3), int(smooth), SAMPLE_FORMAT))
    log(f"Saved loop: {out}")
    return {"saved": str(out), "folder": folder, "file": out.name}


def approve_cues(path: str, plan: dict | None) -> dict:
    from . import cues

    d = cue_grids().get(path)
    if not d:
        raise ValueError("Traktor has no beat grid for this track")
    out = cues.set_plan(path, d["bpm"], d["grid_ms"], plan)
    out["corrections"] = recount(path)
    return out


def setup_status() -> dict:
    with library("native"):
        return jobs.setup_status()


def start_job(b: dict) -> dict:
    with library("native"):  # the options are checked against the normal library
        return jobs.start(str(b["action"]), b.get("options") or {}, bool(b.get("dry_run", False)))


def _after_job(job: dict) -> None:
    """A job may have changed collection.nml, the scans or the models: read them again next time."""
    _grids["mtime"] = None
    _wave_src.clear()


jobs.on_finish.append(_after_job)


def _json_in(path: Path):
    """The JSON object in a saved run output (also when stderr was teed into the same file)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    i = text.find("{")
    if i < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text[i:])[0]
    except ValueError:
        return None


def saved_files(dirs: list[Path]) -> list[dict]:
    out = []
    for n, d in enumerate(dirs):
        for f in sorted(d.glob("*")):
            if f.suffix.lower() not in (".json", ".txt") or not f.is_file():
                continue
            data = _json_in(f)
            if not isinstance(data, dict):
                continue
            kind = "mix" if data.get("type") == "dj_mix" else "track" if "bpm" in data and "dj_genre" in data \
                else "colour model" if "trained_on" in data else "other"
            out.append({"id": f"{n}/{f.name}", "name": f.name, "folder": str(d), "kind": kind,
                        "modified": f.stat().st_mtime, "duration": data.get("duration"),
                        "recording": Path(data["file"]).name if data.get("file") else None})
    return sorted(out, key=lambda x: -x["modified"])


def saved_file(dirs: list[Path], ident: str):
    i, _, name = ident.partition("/")
    if not i.isdigit() or int(i) >= len(dirs) or "/" in name or name.startswith("."):
        raise ValueError("unknown file")
    f = dirs[int(i)] / name
    if not f.is_file():
        raise ValueError("unknown file")
    return _json_in(f)


def _try(fn, *a, **kw):
    """A tab's data, or the advice it would show (e.g. no colour model yet)."""
    try:
        return fn(*a, **kw)
    except SystemExit as e:
        return {"error": str(e.code or e)}
    except (ValueError, OSError, KeyError) as e:
        return {"error": f"{type(e).__name__}: {e}"}


def snapshot(dirs: list[Path], progress=log) -> str:
    """The page with every tab's data inside: a self-contained, read-only file to keep or send."""
    libs = libraries()
    data = {"made": time.strftime("%Y-%m-%d %H:%M"), "libraries": libs, "stats": {}, "tracks": {}, "hype": {},
            "suggest": {}, "tempo": {}, "saved": [], "files": {}}
    for lib in [x["id"] for x in libs["libraries"]]:
        progress(f"  snapshot: {lib}")
        data["stats"][lib] = _try(stats, lib)
        data["tracks"][lib] = _try(tracks, lib)
        # hype in each order the page offers; the page filters these lists as hype.find does
        data["hype"][lib] = {k: _try(hype, lib, limit=100000, **kw) for k, kw in
                             {"most": {}, "least": {"least": True}, "gems": {"gems": True}}.items()}
        sugg = {}
        with library(lib):  # mix suggestions with the default rule for every track
            for t in data["tracks"][lib].get("tracks", []):
                if t.get("type") == "track":
                    r = _try(collection.mix_suggestions, t["path"], limit=30)
                    sugg[t["path"]] = r if "error" in r else \
                        {"rule": r["rule"], "matches": r["matches"], "paths": [x["path"] for x in r["tracks"]]}
        data["suggest"][lib] = sugg
    for x in libs["libraries"]:
        if x["bpm"]:
            data["tempo"][f'{x["bpm"]:g}'] = _try(tempo_report, x["bpm"])
    data["review"], data["explain"], data["kept"] = _try(colour_review), _try(colour_explain), _try(colour_kept)
    data["cues"] = {}
    for t in data["tracks"].get("native", {}).get("tracks", []):
        if t.get("type") == "track":
            r = _try(track_cues, t["path"])
            if "error" not in r:
                data["cues"][t["path"]] = r
    for f in saved_files(dirs):  # recorded mixes only, not other run output
        if f["kind"] == "mix":
            data["saved"].append(f)
            data["files"][f["id"]] = saved_file(dirs, f["id"])
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = PAGE.read_text(encoding="utf-8")
    return page.replace("<script>\n\"use strict\";", f"<script>window.SNAPSHOT = {blob};</script>\n<script>\n\"use strict\";", 1)


def build_app(log_dirs: list[Path]):
    from starlette.applications import Starlette
    from starlette.concurrency import run_in_threadpool
    from starlette.requests import Request
    from starlette.responses import FileResponse, HTMLResponse, JSONResponse
    from starlette.routing import Route

    def respond(fn):
        async def endpoint(request: Request):
            try:
                return JSONResponse(await run_in_threadpool(fn, request.query_params))
            except SystemExit as e:  # expected problems come with advice
                return JSONResponse({"error": str(e.code or e)}, status_code=409)
            except (ValueError, OSError, KeyError) as e:
                return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=400)
        return endpoint

    def num(q, k, default=None):
        return float(q[k]) if q.get(k) not in (None, "") else default

    async def audio(request: Request):
        try:
            p = await run_in_threadpool(deck_audio, request.query_params.get("path", ""))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=404)
        return FileResponse(p)  # supports Range requests, so jumping around doesn't load the whole file

    async def loop_endpoint(request: Request):
        from starlette.responses import Response

        q = request.query_params
        try:
            data = await run_in_threadpool(loop_clip, q.get("path", ""), float(q.get("start", "nan")), float(q.get("len", "nan")),
                                           q.get("smooth") == "1")
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return Response(data, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    async def page(request):
        return FileResponse(PAGE, headers={"Cache-Control": "no-store"})

    async def export(request):
        html = await run_in_threadpool(snapshot, log_dirs)
        name = f"track-analyzer-{time.strftime('%Y-%m-%d')}.html"
        return HTMLResponse(html, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    def from_this_page(request: Request) -> bool:
        # Only this page may change anything: a JSON body can't be sent by another site without a CORS preflight,
        # which this server never allows, and a browser always says where a request comes from.
        origin = request.headers.get("origin")
        return request.headers.get("content-type", "").split(";")[0] == "application/json" and \
            not (origin and origin.split("://", 1)[-1] != request.headers.get("host"))

    async def save_loop_endpoint(request: Request):
        if not from_this_page(request):
            return JSONResponse({"error": "not allowed"}, status_code=403)
        b = await request.json()
        try:
            return JSONResponse(await run_in_threadpool(save_loop, str(b["path"]), float(b["start"]), float(b["len"]),
                                                        str(b["folder"]), int(b.get("bar", 0)), str(b.get("kind", "loop"))[:40],
                                                        bool(b.get("smooth", False))))
        except (ValueError, KeyError, TypeError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    def post(fn):
        """A change from this page: JSON body, checked origin, run in a worker thread."""
        async def endpoint(request: Request):
            if not from_this_page(request):
                return JSONResponse({"error": "not allowed"}, status_code=403)
            try:
                return JSONResponse(await run_in_threadpool(fn, await request.json()))
            except (ValueError, KeyError, TypeError) as e:
                return JSONResponse({"error": str(e)}, status_code=400)
        return endpoint

    async def approve_endpoint(request: Request):
        if not from_this_page(request):
            return JSONResponse({"error": "not allowed"}, status_code=403)
        body = await request.json()
        try:
            return JSONResponse(await run_in_threadpool(approve_cues, str(body["path"]), body.get("plan")))
        except (ValueError, KeyError, TypeError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    async def hold_endpoint(request: Request):
        if not from_this_page(request):
            return JSONResponse({"error": "not allowed"}, status_code=403)
        body = await request.json()
        try:
            return JSONResponse(await run_in_threadpool(hold, list(body["paths"]), body["kind"], bool(body["on"])))
        except (ValueError, KeyError, TypeError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    q_lib = lambda q: q.get("lib", "native")  # noqa: E731
    routes = [
        Route("/", page),
        Route("/api/libraries", respond(lambda q: libraries())),
        Route("/api/stats", respond(lambda q: stats(q_lib(q)))),
        Route("/api/tracks", respond(lambda q: tracks(q_lib(q)))),
        Route("/api/suggest", respond(lambda q: suggest(q_lib(q), q["path"], num(q, "tolerance", 4.0),
                                                       q.get("same_genre", "1") == "1", int(num(q, "limit", 30))))),
        Route("/api/hype", respond(lambda q: hype(
            q_lib(q), least=q.get("least") == "1", colour=q.get("colour") or None, bpm=num(q, "bpm"),
            bpm_tolerance_pct=num(q, "tolerance", 4.0), key=q.get("key") or None, gems=q.get("gems") == "1",
            limit=int(num(q, "limit", 100))))),
        Route("/api/colours/review", respond(lambda q: colour_review(int(num(q, "limit", 200))))),
        Route("/api/colours/explain", respond(lambda q: colour_explain())),
        Route("/api/colours/kept", respond(lambda q: colour_kept())),
        Route("/api/tempo/report", respond(lambda q: tempo_report(num(q, "bpm", 145.0), int(num(q, "limit", 25))))),
        Route("/api/saved", respond(lambda q: {"files": saved_files(log_dirs), "folders": [str(d) for d in log_dirs]})),
        Route("/api/saved-file", respond(lambda q: saved_file(log_dirs, q["id"]))),
        Route("/api/cues", respond(lambda q: track_cues(q["path"]))),
        Route("/api/wave", respond(lambda q: wave_detail(q["path"], num(q, "t0"), num(q, "t1"), int(num(q, "n", 1000))))),
        Route("/api/audio", audio),
        Route("/api/loop", loop_endpoint),
        Route("/api/hold", hold_endpoint, methods=["POST"]),
        Route("/api/cues/approve", approve_endpoint, methods=["POST"]),
        Route("/api/cues/ignore", post(lambda b: ignore_correction(str(b["path"]), b["fix"], bool(b.get("on", True)))), methods=["POST"]),
        Route("/api/cues/rescan", post(lambda b: rescan_track(str(b["path"]))), methods=["POST"]),
        Route("/api/cues/write", post(lambda b: write_cues(bool(b.get("dry_run", True)))), methods=["POST"]),
        Route("/api/cues/undo", post(lambda b: undo_write()), methods=["POST"]),
        Route("/api/setup", respond(lambda q: setup_status())),
        Route("/api/jobs", respond(lambda q: jobs.state(int(num(q, "since", 0))))),
        Route("/api/jobs/start", post(start_job), methods=["POST"]),
        Route("/api/jobs/stop", post(lambda b: jobs.stop()), methods=["POST"]),
        Route("/api/samples/folders", respond(lambda q: sample_folders())),
        Route("/api/samples/save", save_loop_endpoint, methods=["POST"]),
        Route("/api/samples/reveal", post(lambda b: reveal_sample(b.get("file"), b.get("folder"))), methods=["POST"]),
        Route("/export", export),
    ]
    return Starlette(routes=routes)


def export_file(out: str, logs: list[str] | None = None) -> Path:
    dirs = [Path(d).expanduser().resolve() for d in (logs or [])] or [LOGS]
    path = Path(out).expanduser()
    path.write_text(snapshot(dirs), encoding="utf-8")
    log(f"Snapshot saved: {path} ({path.stat().st_size / 1e6:.1f} MB). Opens in any browser, read-only.")
    return path


def serve(host: str = "127.0.0.1", port: int = 8766, logs: list[str] | None = None, open_browser: bool = True) -> None:
    import uvicorn

    dirs = [Path(d).expanduser().resolve() for d in (logs or [])] or [LOGS]
    url = f"http://{host}:{port}/"
    log(f"Puppi3's Track Analyzer web page on {url}  (Ctrl-C to stop)")
    correction_counts()  # start counting proposed cue corrections now, before the page asks
    if open_browser:
        import webbrowser

        threading.Timer(1.0, webbrowser.open, (url,)).start()
    class Server(uvicorn.Server):
        def handle_exit(self, sig, frame):
            # Ctrl-C: an open track keeps its audio stream open, so don't wait for it (1 s), and don't print the
            # requests cut off on the way out as errors
            logging.getLogger("uvicorn.error").setLevel(logging.CRITICAL)
            super().handle_exit(sig, frame)

    server = Server(uvicorn.Config(build_app(dirs), host=host, port=port, log_level="warning", timeout_graceful_shutdown=1))
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    jobs.stop_on_exit()  # a scan the page started stops as Ctrl-C would stop it (it continues next time)
    log("Stopped.")
