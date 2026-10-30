"""
Beat grid check: does Traktor's grid sit on the kicks? For each track, where the kick's attack lands against the grid
lines, measured in several stretches where the kick plays (from the cue bars' kick_on), on Traktor's own timeline
(cues._on_traktor_time). Only a shift that every stretch agrees on is proposed; one that grows along the track is a
BPM that's off, which a shift can't fix (flagged, not proposed).

  uv run dj-track-analyzer grids check            measure every track (cached per grid; ~3 min the first time)
  uv run dj-track-analyzer grids show "name"      one track's measurement
  uv run dj-track-analyzer grids apply [--dry-run]  move the approved grids (and your cues with them) in Traktor

Approvals come from the web page (Tracks → a track → Beat grid). A shift moves the grid marker and every cue, loop
and marker of that track by the same amount, so your cues stay on the beat. Backup + `traktor --undo`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

import numpy as np

from . import analysis, traktor
from .analysis import log

VERSION = "grid-3"  # 2: kick runs bridge one-bar gaps (a fill every 8 bars left no stretch: the fallback hit a breakdown); 3: raw readings cached, classify() on reading
PROPOSE_MS = 10.0  # a grid this far off the kicks gets a proposed fix (on-grid tracks measure within ±3 ms)
AGREE_MS = 12.0  # two thirds of the stretches within this of their median, or no verdict
OUTLIER_MS = 25.0  # a stretch this far from the whole track's reading had no clear kick there: left out
DRIFT_MS = 15.0  # the offset changing this much from the first stretch to the last (a steady line fit): the BPM is off
SECTION_BEATS = 32  # 8 bars per stretch
MAX_SECTIONS = 6
ATTACK_SHARE = 0.3
SEARCH_MS = (-150, 150)  # where the attack is looked for around a grid line (off-beat hats sit at ±230 ms at 128 BPM)


def _envelope(x: np.ndarray, sr: int) -> np.ndarray:
    """Attack envelope in 1 ms bins: the kick's punch (100-400 Hz) plus its click (2-8 kHz), zero-phase filtered
    so the filters add no delay."""
    from scipy.signal import butter, sosfiltfilt

    e = sosfiltfilt(butter(4, [100, 400], "bandpass", fs=sr, output="sos"), x) ** 2
    e += 4 * sosfiltfilt(butter(4, [2000, 8000], "bandpass", fs=sr, output="sos"), x) ** 2
    edges = np.round(np.arange(int(len(e) * 1000 / sr)) * sr / 1000).astype(np.int64)  # 44.1 samples per ms: exact
    return np.add.reduceat(e, edges) if len(edges) else np.zeros(0)


def _attack(env: np.ndarray, lines_ms: np.ndarray, beat_ms: float) -> float | None:
    """Where the attack lands against these grid lines (ms, + = after the line): the envelope folded over the beats,
    where it rises past ATTACK_SHARE of the way from its floor to the peak near the line. (Its steepest rise read up to
    +20 ms on a track Traktor grids right; this reads -3 to +6 on those.)"""
    half = int(beat_ms / 2)
    folded = np.zeros(2 * half)
    n = 0
    for t in lines_ms:
        a = int(round(t)) - half
        if a < 0 or a + 2 * half > len(env):
            continue
        folded += env[a:a + 2 * half]
        n += 1
    if n < SECTION_BEATS // 2:
        return None
    sm = np.convolve(folded, np.ones(5) / 5, "same")
    lo, hi = half + SEARCH_MS[0], half + SEARCH_MS[1]
    pk = int(np.argmax(sm[lo:hi])) + lo  # the kick's peak near the line
    floor = np.percentile(sm, 10)
    th = floor + ATTACK_SHARE * (sm[pk] - floor)
    j = pk
    while j > lo and sm[j - 1] > th:  # back to where it rose past ATTACK_SHARE of the way up
        j -= 1
    return float(j - half)


def _sections(p: Path, bpm: float, grid_ms: float, dur_s: float) -> list[float]:
    """Start times (ms, on grid lines) of up to MAX_SECTIONS 8-bar stretches where the kick plays, spread over the
    track; without measured bars, fixed places between 20% and 80%."""
    from . import cues

    beat = 60000 / bpm
    hit = cues.bars_get(p, bpm, grid_ms)
    if hit is not None and len(hit[0]) >= 16:
        starts, F = hit
        _X, info = cues.features(starts, F, grid_ms)
        on = np.asarray(info["kick_on"], bool).copy()
        on[1:-1] |= on[:-2] & on[2:]  # a one-bar gap (a fill, a drop's first bar) inside a kick run is part of it
        runs, i = [], 0
        while i < len(on):
            if on[i]:
                j = i
                while j < len(on) and on[j]:
                    j += 1
                runs += [k for k in range(i, j - 7, 8)]  # 8-bar blocks inside each kick run
                i = j
            else:
                i += 1
        if len(runs) >= 3:
            pick = [runs[int(round(q))] for q in np.linspace(0, len(runs) - 1, min(MAX_SECTIONS, len(runs)))]
            return [float(starts[k]) * 1000 for k in sorted(set(pick))]
    return [grid_ms + round((dur_s * 1000 * f - grid_ms) / beat) * beat for f in np.linspace(0.2, 0.8, MAX_SECTIONS)]


def measure(p: Path, bpm: float, grid_ms: float) -> dict:
    """The raw readings, as cached: per stretch where its attack lands (ms after the grid line, None: unreadable),
    the stretches' start times, and `whole_ms`, all the stretches folded together. classify() makes them a verdict."""
    from . import cues

    stereo, sr, nch = analysis.decode(p)
    mono = analysis.mono_mix(stereo, nch)
    if sr != cues.SR:
        mono = analysis.resample_medium(mono, sr, cues.SR)
    x = cues._on_traktor_time(p, np.asarray(mono, dtype=np.float32), sr)  # the file's own rate: the offset is in its samples
    env = _envelope(x, cues.SR)
    beat = 60000 / bpm
    starts = _sections(p, bpm, grid_ms, len(x) / cues.SR)
    lines = [t0 + np.arange(SECTION_BEATS) * beat for t0 in starts]
    return {"sections": [_attack(env, ln, beat) for ln in lines], "section_starts_ms": [round(t, 3) for t in starts],
            "whole_ms": _attack(env, np.concatenate(lines), beat) if lines else None, "beat_ms": beat}


def classify(r: dict) -> dict:
    """The readings plus {state: on | off | drift | unclear, offset_ms, drift_ms, preview_ms}. offset_ms: how far
    after the grid line the kick lands, from all the stretches folded together (move the grid that much later to fix
    it). The stretches one by one only check it: a stretch can read the click or the punch of a two-part attack (47
    or 60 ms on one track), so two thirds must lie within AGREE_MS of their median; one with no clear kick (more than
    OUTLIER_MS from the whole reading) is left out. preview_ms: a stretch whose reading agrees, for the click preview."""
    out = {**r, "state": "unclear", "offset_ms": None, "preview_ms": None}
    whole, secs, starts = r.get("whole_ms"), r.get("sections") or [], r.get("section_starts_ms") or []
    if whole is None:
        return out
    ok = [(t, x) for t, x in zip(starts, secs) if x is not None and abs(x - whole) <= OUTLIER_MS]
    if len(ok) < 3 or len(ok) * 2 <= len(secs):
        return out
    t, x = np.array(ok).T
    med = float(np.median(x))
    fit = np.polyfit(t, x, 1)
    change = fit[0] * (t[-1] - t[0]) if t[-1] > t[0] else 0.0
    r2 = 1 - np.var(x - np.polyval(fit, t)) / max(np.var(x), 1e-9)
    if abs(change) >= DRIFT_MS and r2 >= 0.7:
        out["state"], out["drift_ms"] = "drift", round(float(change), 1)  # steadily later or earlier: the BPM is off
    elif np.mean(np.abs(x - med) <= AGREE_MS) >= 2 / 3:
        out["offset_ms"] = round(float(whole), 1)
        out["state"] = "off" if abs(whole) >= PROPOSE_MS else "on"
        good = [tt for tt, xx in ok if abs(xx - med) <= AGREE_MS]
        out["preview_ms"] = round(float(good[len(good) // 2]), 3)
    return out


# ----------------------------------------------------------------------------
# Cache (the normal cache.db, keyed by path + grid; kept while the audio length is unchanged, as the cue bars)
# ----------------------------------------------------------------------------

def _cache():
    """The normal library's cache.db, also while the web page (or a command) has a tempo view's in place: the grids
    are Traktor's, whatever library is shown."""
    import sqlite3

    analysis.HOME.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(analysis.HOME / "cache.db", timeout=60)
    con.execute("CREATE TABLE IF NOT EXISTS gridcheck (k TEXT PRIMARY KEY, path TEXT, duration REAL, data TEXT)")
    return con


def _key(p, bpm: float, grid_ms: float) -> str:
    return hashlib.sha1(f"{p}|{bpm:.4f}|{grid_ms:.3f}|{VERSION}".encode()).hexdigest()


def cached(p: Path, bpm: float, grid_ms: float, check_length: bool = True) -> dict | None:
    con = _cache()
    try:
        row = con.execute("SELECT duration, data FROM gridcheck WHERE k=?", (_key(p, bpm, grid_ms),)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    if check_length:
        length = analysis._audio_length(p)
        if length is not None and abs(length - row[0]) > 0.25:
            return None  # different audio now
    return classify(json.loads(row[1]))


def check_one(p: Path, bpm: float, grid_ms: float, rescan: bool = False) -> dict:
    """The measurement for this file and grid, measured now if it isn't cached (~1-2 s)."""
    hit = None if rescan else cached(p, bpm, grid_ms)
    if hit is not None:
        return hit
    r = measure(p, bpm, grid_ms)
    con = _cache()
    try:
        with con:
            con.execute("INSERT OR REPLACE INTO gridcheck VALUES (?,?,?,?)",
                        (_key(p, bpm, grid_ms), str(p), analysis._audio_length(p) or 0.0, json.dumps(r)))
    finally:
        con.close()
    return classify(r)


def _check_job(job):
    p, bpm, grid_ms, rescan = job
    try:
        return p, check_one(Path(p), bpm, grid_ms, rescan), None
    except Exception as e:  # one unreadable file: the rest still get checked
        return p, None, f"{type(e).__name__}: {e}"


def _worker_init():
    import signal

    signal.signal(signal.SIGINT, signal.SIG_IGN)  # let the parent handle Ctrl-C


def check(nml: str | None = None, workers: int | None = None, rescan: bool = False, progress=log) -> dict:
    """Measure every Traktor track with a beat grid (cached ones are skipped). Parallel; Ctrl-C keeps what's done."""
    from . import cues

    tracks = cues._one_path_each(cues.grids(nml))
    todo = [(p, d["bpm"], d["grid_ms"], rescan) for p, d in tracks
            if rescan or cached(Path(p), d["bpm"], d["grid_ms"]) is None]
    errors = []
    if todo:
        workers = max(1, min(workers or max(1, min(6, (os.cpu_count() or 4) // 2)), len(todo)))
        progress(f"Checking beat grids: {len(todo)} tracks, {workers} workers. Ctrl-C is safe, run again to continue.")
        t0, done = time.time(), 0
        import multiprocessing as mp

        with mp.get_context("spawn").Pool(workers, initializer=_worker_init) as pool:
            for p, _r, err in pool.imap_unordered(_check_job, todo, chunksize=2):
                done += 1
                if err:
                    errors.append(f"{Path(p).name}: {err}")
                if done % max(1, min(10, len(todo) // 20)) == 0 or done == len(todo):
                    rate = done / max(time.time() - t0, 1e-6)
                    progress(f"  [{done}/{len(todo)}  ~{analysis.fmt_time((len(todo) - done) / rate)} left]")
    res = results(nml)
    states = {}
    for r in res.values():
        states[r["state"]] = states.get(r["state"], 0) + 1
    off = sorted(((Path(p).name, r["offset_ms"]) for p, r in res.items() if r["state"] == "off"), key=lambda x: -abs(x[1]))
    return {"tracks": len(tracks), "measured_now": len(todo), "errors": errors[:20], "states": states,
            "off": [f"{n}: kick {o:+.0f} ms after the grid line" for n, o in off],
            "drift": [Path(p).name for p, r in res.items() if r["state"] == "drift"],
            "next": "Review them on the web page (Tracks → a track → Beat grid), then grids apply."}


_classified: dict[str, tuple[str, dict]] = {}  # cache key -> (stored reading, its verdict): classify() is ~75% of a list


def results(nml: str | None = None, g: dict | None = None) -> dict[str, dict]:
    """{path: cached measurement} for Traktor's tracks as their grids are now (no audio-length check: for lists).
    g: cues.grids() if already read. Read only: the verdicts are shared between calls."""
    from . import cues

    con = _cache()
    try:
        out = {}
        if len(_classified) > 5000:
            _classified.clear()
        for p, d in cues._one_path_each(g if g is not None else cues.grids(nml)):
            k = _key(p, d["bpm"], d["grid_ms"])
            row = con.execute("SELECT data FROM gridcheck WHERE k=?", (k,)).fetchone()
            if row:
                hit = _classified.get(k)
                if not hit or hit[0] != row[0]:
                    hit = _classified[k] = (row[0], classify(json.loads(row[0])))
                out[p] = hit[1]
        return out
    finally:
        con.close()


# ----------------------------------------------------------------------------
# Stems: one grid serves both the track and its stem file, so they must line up (checked against NUO Stems files
# on a test collection: 423 of 469 matched to the sample; 42 made from LAME 3.100 MP3s started 2257 samples early,
# the MP3's lead-in left out, and their grids had been set on the stem)
# ----------------------------------------------------------------------------

AAC_PRIMING = 2112  # samples of encoder lead-in in front of a stem's audio when ffmpeg decodes it (Traktor skips them)
_stem_index: dict = {}


def _norm(s) -> str:
    import unicodedata

    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", str(s or "")).lower())


def stems_dir() -> Path | None:
    """Traktor's stem files folder (Browser.Dir.GeneratedStems in Traktor Settings.tsi)."""
    try:
        v = traktor.setting(traktor.find_collection(None), "Browser.Dir.GeneratedStems")
    except SystemExit:
        return None
    d = traktor.mac_path(v) if v else None
    return Path(d) if d and os.path.isdir(d) else None


def find_stem(p: Path) -> Path | None:
    """The stem file made from this track, matched by artist and title tags (the stem file's name is an ID Traktor
    keeps outside the collection)."""
    import mutagen

    root = stems_dir()
    if root is None:
        return None
    files = sorted(root.glob("*/*.stem.mp4"))
    sig = (str(root), len(files), max((f.stat().st_mtime for f in files), default=0))
    if _stem_index.get("sig") != sig:
        idx = {}
        for f in files:
            try:
                t = mutagen.File(f) or {}
                idx[(_norm((t.get("\xa9ART") or [""])[0]), _norm((t.get("\xa9nam") or [""])[0]))] = f
            except Exception:
                continue
        _stem_index.update(sig=sig, idx=idx)
    try:
        t = mutagen.File(p, easy=True) or {}
    except Exception:
        return None
    return _stem_index["idx"].get((_norm((t.get("artist") or [""])[0]), _norm((t.get("title") or [""])[0])))


def stem_check(p: Path) -> dict | None:
    """{stem, shift_ms, matches}: how far the stem file's audio sits from the track's on Traktor's timeline
    (+ = later). None when there's no stem or it can't be compared."""
    import subprocess

    stem = find_stem(p)
    if stem is None:
        return None
    stereo, sr, nch = analysis.decode(p)
    if int(sr) != 44100:
        return {"stem": str(stem), "shift_ms": None, "matches": None, "why": f"{sr:g} Hz track: not compared"}
    m = analysis.mono_mix(stereo, nch)
    raw = subprocess.run([analysis.find_tool("ffmpeg") or "ffmpeg", "-v", "error", "-i", str(stem), "-map", "0:a:0",
                          "-ac", "1", "-ar", "44100", "-f", "f32le", "-"], capture_output=True, timeout=120).stdout
    s = np.frombuffer(raw, np.float32)
    lags = []
    for a_s in (60, 120):
        a, n, w = int(a_s * sr), int(10 * sr), 12000
        if a + n + w > min(len(m), len(s)):
            continue
        c = np.correlate(s[a - w:a + n + w], m[a:a + n], "valid")
        lags.append(int(np.argmax(c)) - w)
    if not lags or max(lags) - min(lags) > 2:
        return {"stem": str(stem), "shift_ms": None, "matches": None, "why": "couldn't line the stem up with the track"}
    d = lags[0] - AAC_PRIMING - traktor.audio_offset(p)
    return {"stem": str(stem), "shift_ms": round(d * 1000 / sr, 1), "matches": abs(d) <= 2}


# ----------------------------------------------------------------------------
# Approvals (collection.db, the native one) and writing them into Traktor
# ----------------------------------------------------------------------------

WRITTEN = "grids-last-written.json"  # in data/: what the last write changed, so an undo can put the approvals back


def _plan_db():
    import sqlite3

    from . import collection

    con = sqlite3.connect(collection.NATIVE_DB)
    con.execute("CREATE TABLE IF NOT EXISTS grid_plan (path TEXT PRIMARY KEY, bpm REAL, grid_ms REAL, shift_ms REAL, at TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS grid_ignore (path TEXT PRIMARY KEY, bpm REAL, grid_ms REAL, at TEXT)")
    return con


def plans() -> dict[str, dict]:
    with _plan_db() as con:
        return {r[0]: {"bpm": r[1], "grid_ms": r[2], "shift_ms": r[3], "at": r[4]} for r in con.execute("SELECT * FROM grid_plan")}


def set_plan(path: str, bpm: float, grid_ms: float, shift_ms: float | None) -> None:
    """Approve moving this track's grid (and cues) shift_ms later (negative: earlier); None takes it back."""
    with _plan_db() as con:
        con.execute("DELETE FROM grid_plan WHERE path=?", (path,))
        if shift_ms is not None:
            if not -500 <= shift_ms <= 500 or shift_ms == 0:
                raise ValueError("The shift must be between -500 and 500 ms, and not 0")
            con.execute("INSERT INTO grid_plan VALUES (?,?,?,?,?)", (path, bpm, grid_ms, float(shift_ms), time.strftime("%Y-%m-%d %H:%M")))


def ignored() -> dict[str, dict]:
    with _plan_db() as con:
        return {r[0]: {"bpm": r[1], "grid_ms": r[2]} for r in con.execute("SELECT * FROM grid_ignore")}


def set_ignore(path: str, bpm: float, grid_ms: float, on: bool, reading: dict | None = None) -> None:
    """The grid is right as it is (kept while the grid is unchanged). reading: the check's result, for the log."""
    from . import collection

    with _plan_db() as con:
        con.execute("DELETE FROM grid_ignore WHERE path=?", (path,))
        if on:
            con.execute("INSERT INTO grid_ignore VALUES (?,?,?,?)", (path, bpm, grid_ms, time.strftime("%Y-%m-%d %H:%M")))
    r = {k: reading.get(k) for k in ("state", "offset_ms", "drift_ms", "stretches")} if reading else {}
    collection.log_feedback("grid_ignore", [(path, {"bpm": bpm, "grid_ms": grid_ms, "on": on, **r})])


def same(a: dict, bpm: float, grid_ms: float) -> bool:
    return abs(a["bpm"] - bpm) < 1e-3 and abs(a["grid_ms"] - grid_ms) < 1e-3


def _shift_cue_rows(con, path: str, shift: float, new_bpm: float, new_grid_ms: float) -> None:
    """Your approved cues and ignored corrections for this track, moved with its grid."""
    from . import cues

    row = con.execute("SELECT grid, plan FROM cue_plan WHERE path=?", (path,)).fetchone()
    if row:
        pl = json.loads(row[1])
        for a in pl.get("add", []) + pl.get("remove", []):
            a["ms"] = round(a["ms"] + shift, 3)
        for m in pl.get("move", []):
            m["from_ms"], m["to_ms"] = round(m["from_ms"] + shift, 3), round(m["to_ms"] + shift, 3)
        con.execute("UPDATE cue_plan SET grid=?, plan=? WHERE path=?", (cues._grid_key(new_bpm, new_grid_ms), json.dumps(pl), path))
    con.execute("UPDATE cue_ignore SET from_ms=from_ms+?, to_ms=to_ms+? WHERE path=?", (shift, shift, path))


def apply(nml: str | None = None, dry_run: bool = False) -> dict:
    """Move each approved track's grid marker, cues, loops and markers by its shift (Traktor closed). A track whose
    grid changed since you approved is left alone. Backup + `traktor --undo`."""
    from . import cues

    path = traktor.find_collection(nml)
    traktor.refuse_if_tempo_view(path)
    traktor.check_closed(dry_run)
    todo = plans()
    if not todo:
        raise SystemExit("Nothing approved yet: approve grid fixes in the web page (uv run dj-track-analyzer web).")
    text = path.read_text(encoding="utf-8")
    stats = {"collection": str(path), "tracks": 0, "cues_moved": 0, "skipped": []}
    examples, done = [], {}

    def fix_entry(m: re.Match) -> str:
        entry = m.group(0)
        loc = re.search(r"<LOCATION\b[^>]*>", entry)
        p = next((c for c in traktor.location_paths(traktor.attrs(loc.group(0))) if c in todo), None) if loc else None
        if not p:
            return entry
        plan, name = todo[p], Path(p).name
        tm = re.search(r"<TEMPO\b[^>]*>", entry)
        tags = list(re.finditer(r"<CUE_V2\b[^>]*>", entry))
        grid = [traktor.attrs(c.group(0)) for c in tags if traktor.attrs(c.group(0)).get("TYPE") == "4"]
        bpm = float(traktor.attrs(tm.group(0)).get("BPM") or 0) if tm else 0
        if not grid or not bpm or not same(plan, bpm, float(grid[0]["START"])):
            stats["skipped"].append(f"{name}: beat grid changed since you approved; approve again")
            return entry
        shift, bar = plan["shift_ms"], 4 * 60000 / bpm
        new = []
        for c in tags:
            a = traktor.attrs(c.group(0))
            t = float(a.get("START", 0)) + shift
            if t < 0 and a.get("TYPE") == "4":
                t += bar * np.ceil(-t / bar)  # the same grid, its marker a bar (or more) later
            elif t < 0:
                stats["skipped"].append(f"{name}: a cue at {cues._clock(float(a.get('START', 0)) / 1000)} would move before the start")
                return entry
            new.append((c, t))
        for c, t in reversed(new):  # from the end, so earlier offsets still hold
            entry = entry[:c.start()] + traktor.set_attr(c.group(0), "START", f"{t:.6f}") + entry[c.end():]
        moved = len(new) - len(grid)
        stats["tracks"] += 1
        stats["cues_moved"] += moved
        examples.append(f"{name}: grid {shift:+.1f} ms" + (f", with {moved} cue{'s' if moved != 1 else ''}/loop{'s' if moved != 1 else ''}/marker{'s' if moved != 1 else ''}" if moved else ""))
        new_grid = next(t for c, t in new if traktor.attrs(c.group(0)).get("TYPE") == "4")
        done[p] = (shift, bpm, new_grid)
        stamp = next((s for s in map(traktor.file_stamp, traktor.location_paths(traktor.attrs(loc.group(0)))) if s), None)
        return traktor.set_entry_stamp(entry, stamp) if stamp else entry

    c0 = text.find("<COLLECTION")
    c1 = text.find("</COLLECTION>", c0)
    if c0 == -1 or c1 == -1:
        raise SystemExit(f"{path} doesn't look like a Traktor collection (no COLLECTION section).")
    body = re.sub(r"<ENTRY\b.*?</ENTRY>", fix_entry, text[c0:c1], flags=re.S)
    stats["changes"] = examples
    out = traktor._save(path, text, text[:c0] + body + text[c1:], stats, dry_run)
    if not dry_run and out.get("changed"):
        with cues._plan_db() as con:  # remembered for an undo first, then your cue approvals moved along
            before = {"backup": out["backup"], "plans": {p: todo[p] for p in done},
                      "cue_plan": [list(r) for r in con.execute(f"SELECT * FROM cue_plan WHERE path IN ({','.join('?' * len(done))})", list(done))],
                      "cue_ignore": [list(r) for r in con.execute(f"SELECT * FROM cue_ignore WHERE path IN ({','.join('?' * len(done))})", list(done))]}
            (analysis.HOME / WRITTEN).write_text(json.dumps(before))
            for p, (shift, bpm, new_grid) in done.items():
                _shift_cue_rows(con, p, shift, bpm, new_grid)
        with _plan_db() as con:
            con.executemany("DELETE FROM grid_plan WHERE path=?", [(p,) for p in done])
        from . import collection

        collection.log_feedback("grid_written", [(p, {"bpm": todo[p]["bpm"], "grid_ms": todo[p]["grid_ms"], "shift_ms": shift})
                                                 for p, (shift, _bpm, _g) in done.items()])
        out["undo"] = "uv run dj-track-analyzer traktor --undo"
    return out


def undo_write(nml: str | None = None) -> dict:
    """Put back the collection from before the last write and, if that was a grid write, its approvals and your
    cue approvals as they were."""
    from . import cues

    out = traktor.undo(nml)
    f = analysis.HOME / WRITTEN
    try:
        saved = json.loads(f.read_text())
    except (OSError, ValueError):
        return out
    if saved.get("backup") == out.get("restored_from"):
        paths = list(saved["plans"])
        with _plan_db() as con:
            for p, v in saved["plans"].items():
                con.execute("INSERT OR REPLACE INTO grid_plan VALUES (?,?,?,?,?)", (p, v["bpm"], v["grid_ms"], v["shift_ms"], v["at"]))
        with cues._plan_db() as con:
            q = ",".join("?" * len(paths))
            con.execute(f"DELETE FROM cue_plan WHERE path IN ({q})", paths)
            con.execute(f"DELETE FROM cue_ignore WHERE path IN ({q})", paths)
            con.executemany("INSERT INTO cue_plan VALUES (?,?,?,?)", [tuple(r) for r in saved["cue_plan"]])
            con.executemany("INSERT INTO cue_ignore VALUES (?,?,?,?,?)", [tuple(r) for r in saved["cue_ignore"]])
        f.unlink()
        out["approvals_restored"] = len(paths)
    return out


def show(track: str, nml: str | None = None) -> dict:
    from . import cues

    hits = [(p, d) for p, d in cues._one_path_each(cues.grids(nml)) if track.lower() in Path(p).name.lower()]
    if not hits:
        raise SystemExit(f"No Traktor track with a beat grid matches {track!r}")
    if len(hits) > 10:
        raise SystemExit(f"{len(hits)} tracks match {track!r}: be more specific")
    out = []
    for p, d in hits:
        r = check_one(Path(p), d["bpm"], d["grid_ms"])
        out.append({"track": Path(p).name, **r, "stem": stem_check(Path(p)) if r["state"] == "off" else None,
                    "approved": plans().get(p)})
    return {"tracks": out}


def main(args) -> None:
    if args.action == "check":
        out = check(args.nml, workers=args.workers, rescan=args.rescan)
    elif args.action == "apply":
        out = apply(args.nml, dry_run=args.dry_run)
    else:
        if not args.track:
            raise SystemExit('Which track? uv run dj-track-analyzer grids show "part of the name"')
        out = show(args.track, args.nml)
    print(json.dumps(out, indent=2, ensure_ascii=False))
