"""`cues` command: cue points and a mix loop for each track, learned from the cues you placed yourself.

Everything sits on Traktor's own beat grid (AutoGrid marker + BPM in collection.nml), so suggestions land on
bars Traktor shows. Per bar the audio is measured once (loudness in 6 bands, a 40-band timbre profile, how
strongly the kick and the off-beat hats pulse) and cached for that grid. Two models learn from your hand-set
cues: which bars you cue (where the music changes, at phrase boundaries) and where you put your loop (a
stretch that loops cleanly under whatever plays on the other deck). Read-only for Traktor.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path

import numpy as np

from . import analysis, traktor
from .analysis import HOME, log

MODEL = HOME / "my_cues.joblib"
STARTER_MODEL = analysis.STARTER_DIR / "cues.joblib"  # used until you train your own
SR, N, HOP = 44100, 2048, 441  # 10 ms hop
BANDS = [(30, 100), (100, 250), (250, 800), (800, 2500), (2500, 6000), (6000, 16000)]
N_MELS = 40
BARS_VERSION = "bars-3"  # 2: measured on Traktor's timeline (traktor.audio_offset); 3: + kick pulse at 100-250 Hz
KICK_BODY_DB = 4.0  # a 100-250 Hz pulse this strong is a kick, whatever the bass does below 100 Hz
KICK_SUB_DB = 15.0  # ... if the bar has sub-bass: 30-100 Hz within this of the track's loud level (its 90th percentile)
KICK_BARE_DB = 10.0  # or both pulses this strong (a kick with no bassline under it, as in a DJ intro) ...
KICK_BARE_SUB_DB = 30.0  # ... with 30-100 Hz within this of the loud level, for two bars running
WAVE_VERSION = "wave-2"
PARTS_VERSION = "parts-3"  # what plays in a loop idea (loop_parts); 2: + the kick, from the drum stem; 3: per-beat levels
PART_DB = -20.0  # a stem this close to the loop's level (per beat) plays there; without it the stem sits at -40 to -65 dB
PART_BG_DB = -33.0  # ... and a synth down to this is in the background (Soft's pad: -25 dB; no synth: -38 to -45)
KICK_STEM_DB = -25.0  # the drum stem's 30-150 Hz on the beat this close to the loop's level, and pulsing: a kick
WAVE_SLICES = 1500  # waveform overview resolution (fewer for short files: at least 0.1 s per slice)
WS = (1, 2, 4, 8, 16)  # context windows, bars
MIN_CUES = 3  # tracks with fewer of your own cues aren't used for learning
LOOP_LENGTHS = (1, 2, 4, 8)  # what the loop length model picks from (16-bar loops: 3 of 439 on a test collection, too few to learn)
CUE_TYPES = {"0": "cue", "1": "fade-in", "2": "fade-out", "3": "load", "4": "grid", "5": "loop"}


# ----------------------------------------------------------------------------
# Traktor's grid and your cues (read-only)
# ----------------------------------------------------------------------------

def grids(nml: str | None = None) -> dict[str, dict]:
    """{file path: {bpm, grid_ms, cues: [{ms, type, bars, hotcue, name}]}} for every track Traktor has a beat
    grid for. Cues are your own (cue points and loops); marks your fade-in, fade-out and load markers (for the
    page only); the AutoGrid marker is grid_ms."""
    text = traktor.find_collection(nml).read_text(encoding="utf-8")
    c0, c1 = text.find("<COLLECTION"), text.find("</COLLECTION>")
    out = {}
    for e in re.findall(r"<ENTRY\b.*?</ENTRY>", text[c0:c1], flags=re.S):
        loc, tm = re.search(r"<LOCATION\b[^>]*>", e), re.search(r"<TEMPO\b[^>]*>", e)
        if not (loc and tm):
            continue
        cues = [traktor.attrs(c) for c in re.findall(r"<CUE_V2\b[^>]*>", e)]
        g = [c for c in cues if c.get("TYPE") == "4"]
        try:
            bpm = float(traktor.attrs(tm.group(0)).get("BPM") or 0)
        except ValueError:
            continue
        if not g or bpm <= 0:
            continue
        beat_ms = 60000 / bpm
        own = sorted(({"ms": float(c["START"]), "type": CUE_TYPES.get(c.get("TYPE"), c.get("TYPE")),
                       "bars": round(float(c.get("LEN") or 0) / beat_ms / 4, 2) if c.get("TYPE") == "5" else None,
                       "hotcue": int(c.get("HOTCUE", -1)), "name": c.get("NAME")}
                      for c in cues if c.get("TYPE") in ("0", "5")), key=lambda c: c["ms"])
        every = [{"ms": float(c["START"]), "type": CUE_TYPES.get(c.get("TYPE"), c.get("TYPE")), "hotcue": int(c.get("HOTCUE", -1))}
                 for c in cues if c.get("TYPE") != "4"]  # fade / load markers can take hot-cue slots too
        marks = sorted(({"ms": float(c["START"]), "type": CUE_TYPES[c["TYPE"]], "bars": None,
                         "hotcue": int(c.get("HOTCUE", -1)), "name": c.get("NAME")}
                        for c in cues if c.get("TYPE") in ("1", "2", "3")), key=lambda c: c["ms"])  # shown, never learned from
        d = {"bpm": bpm, "grid_ms": float(g[0]["START"]), "cues": own, "all_cues": every, "marks": marks}
        for p in traktor.location_paths(traktor.attrs(loc.group(0))):
            out[p] = d
    return out


def _one_path_each(g: dict[str, dict]) -> list[tuple[str, dict]]:
    """Each Traktor track once, under the first of its paths that exists. A track on the startup disk is
    listed as /Users/… and /Volumes/<disk>/Users/…, and both exist."""
    seen, out = set(), []
    for p, d in g.items():
        if id(d) not in seen and os.path.exists(p):
            seen.add(id(d))
            out.append((p, d))
    return out


# ----------------------------------------------------------------------------
# Per-bar measurements (cached per grid)
# ----------------------------------------------------------------------------

def _mel_fb() -> np.ndarray:
    f = np.fft.rfftfreq(N, 1 / SR)
    mel = lambda x: 2595 * np.log10(1 + x / 700)  # noqa: E731
    hz = 700 * (10 ** (np.linspace(mel(30), mel(16000), N_MELS + 2) / 2595) - 1)
    fb = np.zeros((N_MELS, len(f)))
    for i in range(N_MELS):
        lo, c, hi = hz[i], hz[i + 1], hz[i + 2]
        fb[i] = np.clip(np.minimum((f - lo) / (c - lo), (hi - f) / (hi - c)), 0, None)
    return fb


def _db(x):
    return 10 * np.log10(np.maximum(x, 1e-10))


def _on_traktor_time(p: Path, x: np.ndarray, sr: int) -> np.ndarray:
    """The decoded audio (at SR) shifted onto Traktor's timeline, so that time t here is time t on Traktor's beat grid:
    for most MP3s Traktor plays a header frame and encoder delay that decoders skip (51 ms; traktor.audio_offset)."""
    n = round(traktor.audio_offset(p) * SR / sr)
    return np.concatenate([np.zeros(n, np.float32), x]) if n else x


def _spectra(p: Path) -> tuple[np.ndarray, np.ndarray, float, np.ndarray]:
    """10 ms frames: power in the 6 bands, 40 mel bands; the length in seconds; and the waveform overview."""
    stereo, sr, nch = analysis.decode(p)
    mono = analysis.mono_mix(stereo, nch)
    if sr != SR:
        mono = analysis.resample_medium(mono, sr, SR)
    x = _on_traktor_time(p, np.asarray(mono, dtype=np.float32), sr)
    nfr = max(0, 1 + (len(x) - N) // HOP)
    win = np.hanning(N).astype(np.float32)
    f = np.fft.rfftfreq(N, 1 / SR)
    fb = _mel_fb()
    bands, mels = np.zeros((nfr, len(BANDS)), np.float32), np.zeros((nfr, N_MELS), np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(x, N)
    for s in range(0, nfr, 4096):
        idx = np.arange(s, min(nfr, s + 4096))
        pw = np.abs(np.fft.rfft(frames[idx * HOP] * win, axis=1)) ** 2
        for b, (lo, hi) in enumerate(BANDS):
            bands[idx, b] = pw[:, (f >= lo) & (f < hi)].sum(1)
        mels[idx] = pw @ fb.T
    return bands, mels, len(x) / SR, _wave(x, bands)


def _wave(x: np.ndarray, bands: np.ndarray, n: int | None = None) -> np.ndarray:
    """Waveform overview, one row per slice (uint8): peak, RMS (both against the track's loudest peak), and how
    strong the lows (30-250 Hz), mids (250-2500) and highs (2.5-16k) are against their own loud level (95th
    percentile), the way DJ software colours waveforms by frequency."""
    n = n or int(min(WAVE_SLICES, max(10, len(x) / SR * 10)))
    edges = np.linspace(0, len(x), n + 1).astype(int)
    peak = np.maximum.reduceat(np.abs(x), edges[:-1])
    rms = np.sqrt(np.add.reduceat(x.astype(np.float64) ** 2, edges[:-1]) / np.diff(edges))
    top = max(float(peak.max()), 1e-9)
    centre = (np.arange(len(bands)) * HOP + N / 2) / len(x)  # each frame's position in the track, 0..1
    slot = np.minimum(n - 1, (centre * n).astype(int))
    out = np.zeros((n, 5), np.uint8)
    out[:, 0] = np.clip(peak / top * 255, 0, 255)
    out[:, 1] = np.clip(rms / top * 255, 0, 255)
    for j, cols in enumerate(([0, 1], [2, 3], [4, 5])):
        e = np.zeros(n)
        np.add.at(e, slot, bands[:, cols].sum(1))
        e = np.sqrt(e / np.maximum(1, np.bincount(slot, minlength=n)))  # amplitude, not power
        out[:, 2 + j] = np.clip(e / max(np.percentile(e, 95), 1e-12) * 255, 0, 255)
    return out


def _bars_from_spectra(bands, mels, dur: float, bpm: float, grid_ms: float):
    """Bar start times (s) on Traktor's grid and per bar: 6 band levels, 40 mel levels (dB), the kick's body at
    100-250 Hz (its first 3/16 of the beat vs the rest), kick pulse (30-100 Hz) and off-beat hat pulse (dB, on-beat
    vs between beats). Below 100 Hz a trance kick's long tail and a rolling or off-beat bassline fill the gaps between
    beats, so the 30-100 Hz pulse misses the kick under a full drop on about a quarter of tracks; 100-250 Hz doesn't."""
    beat, g0 = 60 / bpm, grid_ms / 1000
    bar = 4 * beat
    k0 = -int(g0 // bar)  # whole bars before the grid marker
    starts = g0 + bar * np.arange(k0, int((dur - g0) / bar) + 1)
    starts = starts[(starts >= 0) & (starts + bar <= dur)]
    nfr = len(bands)
    fr = lambda t: min(nfr - 1, max(0, int(round(t / (HOP / SR)))))  # noqa: E731
    fc = lambda t: fr(t - N / 2 / SR)  # noqa: E731  # the frame centred on t
    F = np.zeros((len(starts), len(BANDS) + N_MELS + 3), np.float32)
    for i, s in enumerate(starts):
        a, b = fr(s), fr(s + bar)
        on, off, hon, hoff, bon, boff = [], [], [], [], [], []
        for j in range(4):
            t = s + j * beat
            bon.append(bands[fc(t):fc(t + 3 / 16 * beat), 1].mean())
            boff.append(bands[fc(t + 5 / 16 * beat):fc(t + 15 / 16 * beat), 1].mean())
            on.append(bands[fr(t):fr(t + 0.06), 0].mean())
            off.append(bands[fr(t + 0.25 * beat):fr(t + 0.75 * beat), 0].mean())
            hon.append(bands[fr(t + 0.45 * beat):fr(t + 0.55 * beat), 5].mean())
            hoff.append(bands[fr(t - 0.05 * beat):fr(t + 0.05 * beat), 5].mean())
        F[i, :len(BANDS)] = _db(bands[a:b].mean(0))
        F[i, len(BANDS):len(BANDS) + N_MELS] = _db(mels[a:b].mean(0))
        F[i, -3] = _db(np.mean(bon)) - _db(np.mean(boff))
        F[i, -2] = _db(np.mean(on)) - _db(np.mean(off))
        F[i, -1] = _db(np.mean(hon)) - _db(np.mean(hoff))
    return starts, F


def _grid_key(bpm: float, grid_ms: float) -> str:
    return f"{BARS_VERSION}|{bpm:.4f}|{grid_ms:.3f}"


def same_grid(key: str, bpm: float, grid_ms: float) -> bool:
    """Is a stored grid key (from any BARS_VERSION) Traktor's grid as it is now? For approvals: they depend on
    Traktor's beat grid, not on how the bars were measured."""
    return key.split("|", 1)[-1] == _grid_key(bpm, grid_ms).split("|", 1)[-1]


def _bars_db():
    con = analysis._db()
    con.execute("CREATE TABLE IF NOT EXISTS bars (k TEXT PRIMARY KEY, path TEXT, grid TEXT, duration REAL,"
                " starts BLOB, feats BLOB)")
    con.execute("CREATE TABLE IF NOT EXISTS wave (k TEXT PRIMARY KEY, path TEXT, duration REAL, data BLOB)")
    return con


def bars_get(p: Path, bpm: float, grid_ms: float):
    """Cached (bar starts, per-bar features) for this file and grid, or None. A tag edit doesn't lose it: the
    entry is matched by path and grid, and kept while the audio length is unchanged."""
    k = hashlib.sha1(f"{p}|{_grid_key(bpm, grid_ms)}".encode()).hexdigest()
    with _bars_db() as con:
        row = con.execute("SELECT duration, starts, feats FROM bars WHERE k=?", (k,)).fetchone()
    if not row:
        return None
    length = analysis._audio_length(p)
    if length is not None and abs(length - row[0]) > 0.25:
        return None  # different audio now
    starts = np.frombuffer(row[1], dtype=np.float64)
    return starts, np.frombuffer(row[2], dtype=np.float32).reshape(len(starts), -1)


class WaveSource:
    """A track's decoded audio (on Traktor's timeline) and its 10 ms band powers, kept so any stretch of it can be cut
    into a waveform as fine as the zoom needs, down to single samples. Peaks are scaled against the whole track's
    loudest peak and band strengths against the whole track's loud level (as in _wave), so every zoom level matches."""

    def __init__(self, p: Path):
        stereo, sr, nch = analysis.decode(p)
        mono = analysis.mono_mix(stereo, nch)
        if sr != SR:
            mono = analysis.resample_medium(mono, sr, SR)
        x = self.x = _on_traktor_time(p, np.asarray(mono, dtype=np.float32), sr)
        nfr = max(0, 1 + (len(x) - N) // HOP)
        win = np.hanning(N).astype(np.float32)
        f = np.fft.rfftfreq(N, 1 / SR)
        bands = np.zeros((nfr, len(BANDS)), np.float32)
        frames = np.lib.stride_tricks.sliding_window_view(x, N)
        for s0 in range(0, nfr, 4096):
            idx = np.arange(s0, min(nfr, s0 + 4096))
            pw = np.abs(np.fft.rfft(frames[idx * HOP] * win, axis=1)) ** 2
            for b, (lo, hi) in enumerate(BANDS):
                bands[idx, b] = pw[:, (f >= lo) & (f < hi)].sum(1)
        g = np.stack([bands[:, c].sum(1) for c in ([0, 1], [2, 3], [4, 5])], 1)  # lows, mids, highs
        self.cum = np.vstack([np.zeros((1, 3)), np.cumsum(g, 0, dtype=np.float64)])
        self.dur = len(x) / SR
        self.top = max(float(np.abs(x).max()), 1e-9)
        self.ref = None
        self.ref = self._levels(self._edges(0, self.dur, max(10, int(self.dur * 100)))[0])[1]  # loud level at 100 slices/s

    def _edges(self, t0: float, t1: float, n: int) -> tuple[np.ndarray, np.ndarray]:
        s0, s1 = max(0, int(t0 * SR)), min(len(self.x), max(int(t0 * SR) + 1, int(np.ceil(t1 * SR))))
        n = max(1, min(n, s1 - s0))  # never finer than one sample a slice
        return np.linspace(s0, s1, n + 1).astype(int), self.x[s0:s1]

    def _levels(self, edges: np.ndarray):
        """Band strengths per slice (the frames centred in it, or the nearest one when slices are finer than the
        10 ms hop) and, unless already known, the loud level (95th percentile) they're scaled against."""
        nfr = len(self.cum) - 1
        a = np.clip(np.ceil((edges[:-1] - N / 2) / HOP), 0, nfr).astype(int)
        z = np.clip(np.ceil((edges[1:] - N / 2) / HOP), 0, nfr).astype(int)
        near = np.clip(np.round(((edges[:-1] + edges[1:]) / 2 - N / 2) / HOP), 0, max(0, nfr - 1)).astype(int)
        empty = z <= a
        a, z = np.where(empty, near, a), np.where(empty, np.minimum(near + 1, nfr), z)
        e = np.sqrt((self.cum[z] - self.cum[a]) / np.maximum(1, z - a)[:, None])  # amplitude, not power
        return e, self.ref if self.ref is not None else np.maximum(np.percentile(e, 95, axis=0), 1e-12)

    def cut(self, t0: float, t1: float, n: int) -> np.ndarray:
        """Slices x [peak, rms, low, mid, high] (uint8) for [t0, t1), n slices (fewer if that's finer than a sample)."""
        edges, seg = self._edges(t0, t1, n)
        rel = edges[:-1] - edges[0]
        peak = np.maximum.reduceat(np.abs(seg), rel)
        rms = np.sqrt(np.add.reduceat(seg.astype(np.float64) ** 2, rel) / np.diff(edges))
        e, ref = self._levels(edges)
        out = np.zeros((len(rel), 5), np.uint8)
        out[:, 0] = np.clip(peak / self.top * 255, 0, 255)
        out[:, 1] = np.clip(rms / self.top * 255, 0, 255)
        out[:, 2:] = np.clip(e / ref * 255, 0, 255)
        return out


def wave_get(p: Path) -> tuple[np.ndarray, float] | None:
    """Cached waveform overview (slices x [peak, rms, low, mid, high], uint8) and the length in seconds."""
    k = hashlib.sha1(f"{p}|{WAVE_VERSION}".encode()).hexdigest()
    with _bars_db() as con:
        row = con.execute("SELECT duration, data FROM wave WHERE k=?", (k,)).fetchone()
    if not row:
        return None
    length = analysis._audio_length(p)
    if length is not None and abs(length - row[0]) > 0.25:
        return None
    return np.frombuffer(row[1], dtype=np.uint8).reshape(-1, 5), row[0]


def bars(p: Path, bpm: float, grid_ms: float):
    """(bar starts, per-bar features), measured once per grid; the waveform overview is kept at the same time."""
    hit = bars_get(p, bpm, grid_ms)
    if hit is not None and wave_get(p) is not None:
        return hit
    bands, mels, dur, wave = _spectra(p)
    starts, F = _bars_from_spectra(bands, mels, dur, bpm, grid_ms)
    k = hashlib.sha1(f"{p}|{_grid_key(bpm, grid_ms)}".encode()).hexdigest()
    with _bars_db() as con:
        con.execute("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?)",
                    (k, str(p), _grid_key(bpm, grid_ms), dur, starts.astype(np.float64).tobytes(), F.tobytes()))
        con.execute("INSERT OR REPLACE INTO wave VALUES (?,?,?,?)",
                    (hashlib.sha1(f"{p}|{WAVE_VERSION}".encode()).hexdigest(), str(p), dur, wave.tobytes()))
    return starts, F


def _measure_one(job):
    """Runs in a worker process."""
    path, bpm, grid_ms = job
    try:
        bars(Path(path), bpm, grid_ms)
        return path, None
    except Exception as err:
        return path, f"{type(err).__name__}: {err}"


def measure(jobs: list[tuple[str, float, float]], workers: int | None = None, progress=log) -> dict:
    """Make sure every (path, bpm, grid_ms) has its bars cached (parallel, resumable)."""
    todo = [j for j in jobs if Path(j[0]).exists()
            and (bars_get(Path(j[0]), j[1], j[2]) is None or wave_get(Path(j[0])) is None)]
    if not todo:
        return {"measured": 0}
    workers = max(1, min(workers or max(1, min(5, (os.cpu_count() or 4) // 2)), len(todo)))
    progress(f"Listening for phrases: {len(todo)} tracks, {workers} workers. Runs once per beat grid; "
             "Ctrl-C is safe, run again to continue.")
    t0, done, errors = time.time(), 0, []
    pool = None
    try:
        if workers == 1:
            results = map(_measure_one, todo)
        else:
            import multiprocessing as mp

            from . import collection

            pool = mp.get_context("spawn").Pool(workers, initializer=collection._worker_init)
            results = pool.imap_unordered(_measure_one, todo, chunksize=1)
        for path, err in results:
            done += 1
            if err:
                errors.append(f"{Path(path).name}: {err}")
            if done % max(1, min(25, len(todo) // 20)) == 0 or done == len(todo):  # ~every 5%, so few tracks still move the bar
                rate = done / max(time.time() - t0, 1e-6)
                progress(f"  [{done}/{len(todo)}  ~{analysis.fmt_time((len(todo) - done) / rate)} left]")
    finally:
        if pool:
            pool.close()
            pool.join()
    return {"measured": done, "errors": errors[:20], "minutes": round((time.time() - t0) / 60, 1)}


# ----------------------------------------------------------------------------
# Features per bar (what the models see)
# ----------------------------------------------------------------------------

def features(starts: np.ndarray, F: np.ndarray, grid_ms: float) -> tuple[np.ndarray, dict]:
    """One row per bar: the bar's own sound, how it changes from before to after (1-16 bars), timbre novelty,
    how well the next bars repeat (loopability), and where the bar sits in the phrase pattern."""
    nb = len(starts)
    B, M, body, kick, hat = F[:, :6], F[:, 6:6 + N_MELS], F[:, -3], F[:, -2], F[:, -1]
    loud = _db((10 ** (B / 10)).sum(1))
    base = np.column_stack([B - loud.max(), loud - loud.max(), kick, hat])
    Mz = (M - M.mean(0)) / (M.std(0) + 1e-6)
    U = Mz / (np.linalg.norm(Mz, axis=1, keepdims=True) + 1e-9)
    S = U @ U.T
    nov = []
    for w in (2, 4, 8, 16):  # Foote novelty on the timbre self-similarity
        n = np.zeros(nb)
        for i in range(1, nb):
            a0, b1 = max(0, i - w), min(nb, i + w)
            n[i] = S[a0:i, a0:i].mean() + S[i:b1, i:b1].mean() - 2 * S[a0:i, i:b1].mean()
        nov.append(n)
    cs = np.vstack([np.zeros((1, base.shape[1])), np.cumsum(base, 0)])
    ctx = []
    for w in WS:
        idx = np.arange(nb)
        hi, lo = np.minimum(nb, idx + w), np.maximum(0, idx - w)
        aft = (cs[hi] - cs[idx]) / np.maximum(1, hi - idx)[:, None]
        bef = (cs[idx] - cs[lo]) / np.maximum(1, idx - lo)[:, None]
        ctx += [aft - bef, aft]
    loop = []
    for L in (2, 4, 8):  # how alike the next L bars are, and how steady their level is
        sim, steady = np.zeros(nb), np.zeros(nb)
        for i in range(nb):
            j = min(nb, i + L)
            sim[i] = S[i:j, i:j].mean() if j - i > 1 else 0
            steady[i] = -loud[i:j].std() if j - i > 1 else 0
        loop += [sim, steady]
    idx = np.arange(nb)
    zero = int(np.argmin(np.abs(starts - grid_ms / 1000)))  # the grid marker's bar
    pos = np.column_stack([((idx - zero) % m == 0) for m in (2, 4, 8, 16, 32)] + [idx / nb, idx, nb - idx, idx - zero])
    X = np.column_stack([base] + nov + ctx + loop + [pos]).astype(np.float32)
    return X, {"loud": loud - loud.max(), "kick": kick, "kick_on": kick_on(kick, body, B[:, 0]),
               "bass_on": B[:, 0] - np.percentile(B[:, 0], 90) > -KICK_SUB_DB}


def kick_on(kick: np.ndarray, body: np.ndarray, low: np.ndarray) -> np.ndarray:
    """Bars where the kick plays: its 30-100 Hz pulse at least half of the track's usual strong pulse, or its body
    (100-250 Hz) pulsing clearly; either only with sub-bass in the bar (`low`: 30-100 Hz level, dB), since in a
    breakdown without it both pulses come from noise or an echoing synth. On a ~450-track collection the loud half
    of a track read as kick-free on 108 tracks with the first test alone, 15 with all three; bars without sub-bass
    read as kick 30% of the time with the first test alone, 0% now. A bare kick before the bassline comes in (a DJ
    intro) has little sub-bass but pulses hard in both bands, which an echo or noise doesn't (in one at a time), so
    both pulses at KICK_BARE_DB for two bars running count too: 806 bars on 70 tracks, 42 of them intros; the five
    mid-track cue names it changed were all right by ear (London's on Fire, Crush, Paper Walls, WE ARE FREE, The Less I
    Know the Better)."""
    rel = low - np.percentile(low, 90)
    with_sub = ((kick > max(np.percentile(kick, 80), 2.0) * 0.5) | (body > KICK_BODY_DB)) & (rel > -KICK_SUB_DB)
    bare = (kick > KICK_BARE_DB) & (body > KICK_BARE_DB) & (rel > -KICK_BARE_SUB_DB)
    bare &= np.convolve(bare, [1, 1, 1], "same") >= 2  # with a neighbour: a lone hit isn't a beat
    return with_sub | bare


def labels(starts: np.ndarray, bpm: float, cues: list[dict]) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """Bars you put a cue point on / start a loop on (within a beat of the bar start), and your loop lengths."""
    beat = 60 / bpm
    yc, yl, lens = np.zeros(len(starts), int), np.zeros(len(starts), int), []
    for c in cues:
        t = c["ms"] / 1000
        i = int(np.argmin(np.abs(starts - t)))
        if abs(starts[i] - t) <= beat * 1.01:
            (yl if c["type"] == "loop" else yc)[i] = 1
            if c["type"] == "loop" and c["bars"]:
                lens.append(c["bars"])
    return yc, yl, lens


# ----------------------------------------------------------------------------
# Training
# ----------------------------------------------------------------------------

def _pick(p: np.ndarray, k_max: int, thr: float, gap: int = 4) -> list[int]:
    out = []
    for i in np.argsort(-p):
        if p[i] < thr or len(out) >= k_max:
            break
        if all(abs(i - j) >= gap for j in out):
            out.append(int(i))
    return sorted(out)


def _prf(pred_truth: list[tuple[list[int], np.ndarray]], tol: int = 0) -> dict:
    hp = np_ = ht = nt = 0
    for pred, y in pred_truth:
        truth = np.flatnonzero(y)
        hp += sum(any(abs(t - p) <= tol for t in truth) for p in pred)
        ht += sum(any(abs(t - p) <= tol for p in pred) for t in truth)
        np_ += len(pred)
        nt += len(truth)
    P, R = hp / max(np_, 1), ht / max(nt, 1)
    return {"precision": round(P, 3), "recall": round(R, 3), "f1": round(2 * P * R / max(P + R, 1e-9), 3),
            "cues_per_track": round(np_ / max(len(pred_truth), 1), 1)}


def _model():
    from sklearn.ensemble import HistGradientBoostingClassifier

    return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, l2_regularization=1.0,
                                          random_state=0)


def train(nml: str | None = None, workers: int | None = None, progress=log, threshold: float = 0.25,
          max_cues: int = 7) -> dict:
    """Learn your cue points and loops from the tracks you cued yourself (Traktor's collection). Reports
    cross-validated accuracy: each track's cues predicted by models that never saw that track."""
    import joblib
    from collections import Counter

    from sklearn.model_selection import GroupKFold

    g = grids(nml)
    tracks = [(p, d) for p, d in _one_path_each(g) if sum(c["type"] == "cue" for c in d["cues"]) >= MIN_CUES]
    if len(tracks) < 30:
        raise SystemExit(f"Need at least 30 tracks with {MIN_CUES}+ of your own cue points in Traktor "
                         f"(found {len(tracks)}).")
    out = {"measuring": measure([(p, d["bpm"], d["grid_ms"]) for p, d in tracks], workers, progress)}
    rows = []
    for p, d in tracks:
        hit = bars_get(Path(p), d["bpm"], d["grid_ms"])
        if hit is None or len(hit[0]) < 24:
            continue
        X, info = features(hit[0], hit[1], d["grid_ms"])
        yc, yl, lens = labels(hit[0], d["bpm"], d["cues"])
        rows.append((p, X, yc, yl, lens, d["grid_ms"], hit[0], d["bpm"], hit[1], info["kick_on"], d["cues"]))
    progress(f"Learning from {len(rows)} tracks: {sum(r[2].sum() for r in rows)} cue points, "
             f"{sum(r[3].sum() for r in rows)} loops")
    X = np.vstack([r[1] for r in rows])
    yc, yl = np.concatenate([r[2] for r in rows]), np.concatenate([r[3] for r in rows])
    grp = np.concatenate([[i] * len(r[2]) for i, r in enumerate(rows)])
    off = np.cumsum([0] + [len(r[2]) for r in rows])
    t_learn, steps, done = time.time(), 10, 0  # 5 folds, 3 loop grids, loop lengths, the final models

    def step(what: str) -> None:  # "[k/10  ~t left]" lines: the web page's progress bar
        nonlocal done
        done += 1
        rate = done / max(time.time() - t_learn, 1e-6)
        progress(f"  [{done}/{steps}  ~{analysis.fmt_time((steps - done) / rate)} left] {what}")

    Pc, Pl = np.zeros(len(yc)), np.zeros(len(yl))
    for k, (tr, te) in enumerate(GroupKFold(5).split(X, yc, grp)):
        Pc[te] = _model().fit(X[tr], yc[tr]).predict_proba(X[te])[:, 1]
        Pl[te] = _model().fit(X[tr], yl[tr]).predict_proba(X[te])[:, 1]
        step(f"cross-checking on tracks it hasn't seen, {k + 1} of 5")
    cue_pt = [(_pick(Pc[off[i]:off[i + 1]], max_cues, threshold), r[2]) for i, r in enumerate(rows)]
    grid16 = []
    for r in rows:
        zero = int(np.argmin(np.abs(r[6] - r[5] / 1000)))
        grid16.append((list(range(zero, len(r[2]), 16))[:max_cues], r[2]))
    with_loop = [i for i, r in enumerate(rows) if r[3].any()]
    loop_hits = Counter()
    for i in with_loop:
        pl = Pl[off[i]:off[i + 1]].copy()
        for j in cue_pt[i][0]:  # as suggest(): not on a suggested cue point
            pl[max(0, j - 1):j + 2] = 0
        guess, truth = int(np.argmax(pl)), np.flatnonzero(rows[i][3])
        dist = int(min(abs(truth - guess)))
        loop_hits["exact"] += dist == 0
        loop_hits["within_4_bars"] += dist <= 4
    def on_phrase(pairs):  # share of cues on the 8-bar phrase grid counted from the grid marker
        hits = n = 0
        for bars_, r in pairs:
            zero = int(np.argmin(np.abs(r[6] - r[5] / 1000)))
            hits += sum((b - zero) % 8 == 0 for b in bars_)
            n += len(bars_)
        return round(hits / max(n, 1), 3)

    phrase = {"suggested": on_phrase([(p, rows[i]) for i, (p, _y) in enumerate(cue_pt)]),
              "yours": on_phrase([(list(np.flatnonzero(r[2])), r) for r in rows])}
    lens = Counter(round(x) for r in rows for x in r[4])
    loop_bars = lens.most_common(1)[0][0] if lens else 4
    by_align = {}
    for align in (1, 2, 4):  # which grid the clean-loop ideas start on: chosen by how often they find your loops
        found = Counter()
        for i in with_loop:  # your loop among the (up to 5) loop ideas, made without seeing this track's cues
            zero = int(np.argmin(np.abs(rows[i][6] - rows[i][5] / 1000)))  # without the peak ideas, as measured first
            idx = [k for k, _w in loop_ideas(Pl[off[i]:off[i + 1]], rows[i][8], rows[i][9], cue_pt[i][0], loop_bars,
                                             n=5, peak=0, zero=zero, align=align)]
            truth = np.flatnonzero(rows[i][3])
            dist = min((abs(int(t) - k) for t in truth for k in idx), default=99)
            found["exact"] += dist == 0
            found["within_4_bars"] += dist <= 4
        by_align[align] = {k: round(found[k] / max(len(with_loop), 1), 3) for k in ("exact", "within_4_bars")}
        step(f"loop ideas on a {align}-bar grid")
    loop_align = max(by_align, key=lambda a: (by_align[a]["exact"] + by_align[a]["within_4_bars"], a))
    length_model, length_cv = train_lengths(rows)
    step("your loop lengths")
    out.update({
        "trained_on": len(rows),
        "cue_points_cross_validated": {"exact_bar": _prf(cue_pt), "within_1_bar": _prf(cue_pt, 1)},
        "on_8_bar_phrase_grid": phrase,
        "baseline_every_16_bars": {"exact_bar": _prf(grid16), "within_1_bar": _prf(grid16, 1)},
        "loop_cross_validated": {"tracks_with_loop": len(with_loop),
                                 "exact_bar": round(loop_hits["exact"] / max(len(with_loop), 1), 3),
                                 "within_4_bars": round(loop_hits["within_4_bars"] / max(len(with_loop), 1), 3),
                                 "your_loop_lengths_bars": dict(lens.most_common()),
                                 "suggested_length_bars": loop_bars,
                                 "yours_among_5_ideas": by_align[loop_align],
                                 "clean_loop_grid_bars": {"chosen": loop_align,
                                                          "tried": {str(a): v for a, v in by_align.items()}},
                                 "length_cross_validated": length_cv},
    })
    oof = {r[0]: {"grid": _grid_key(r[7], r[5]), "p": Pc[off[i]:off[i + 1]].astype(np.float16)}
           for i, r in enumerate(rows)}  # for correcting your own cues fairly: the model never saw them
    cue_model, loop_model = _model().fit(X, yc), _model().fit(X, yl) if yl.any() else None
    step("the final models")
    bundle = {"oof": oof, "cue_model": cue_model, "loop_model": loop_model,
              "threshold": threshold, "loop_align": loop_align, "max_cues": max_cues, "loop_bars": loop_bars,
              "length_model": length_model,
              "trained_at": time.strftime("%Y-%m-%d %H:%M"), "metrics": {k: v for k, v in out.items() if k != "measuring"},
              "version": BARS_VERSION,
              "labels": {p: _label_key(d) for p, d in tracks}}  # what it learned from, for new_labels()
    joblib.dump(bundle, MODEL)
    out["saved_to"] = str(MODEL)
    out["note"] = ("exact_bar: share of suggested cues on a bar you cued (precision) and of your cues that were "
                   "suggested (recall), for tracks the model never saw. Many misses are 8 or 16 bars off: a phrase "
                   "boundary, but not the one you chose.")
    return out


def _label_key(d: dict) -> tuple:
    """A track's grid and your cue points and loops, as training reads them: changes when any of them does."""
    return _grid_key(d["bpm"], d["grid_ms"]), tuple((c["type"], round(c["ms"]), c["bars"]) for c in d["cues"])


def new_labels(g: dict[str, dict]) -> dict | None:
    """What `train` would learn that your own model hasn't, given grids(): tracks with MIN_CUES+ of your cues that
    weren't trained on (added), whose cues or grid changed since (changed), or that no longer qualify (removed).
    None with the starter or a model from before this."""
    b = model()
    if not b or b.get("starter") or "labels" not in b:
        return None
    then = b["labels"]
    now = {p: _label_key(d) for p, d in _one_path_each(g) if sum(c["type"] == "cue" for c in d["cues"]) >= MIN_CUES}
    return {"added": sum(p not in then for p in now), "changed": sum(p in then and then[p] != k for p, k in now.items()),
            "removed": sum(p not in now for p in then), "trained_at": b.get("trained_at")}


_bundle: dict = {}


def model():
    """Your cue model, else the starter one; reloaded when the file changes (a `cues train` from the web page)."""
    path = analysis.own_or_starter(MODEL, STARTER_MODEL)
    if path is None:
        return None
    key = (path, path.stat().st_mtime)
    if _bundle.get("key") != key:
        import joblib

        _bundle.update(key=key, bundle=joblib.load(path))
    return _bundle["bundle"]


# ----------------------------------------------------------------------------
# Suggestions
# ----------------------------------------------------------------------------

def loopability(F: np.ndarray, L: int) -> np.ndarray:
    """How cleanly L bars from each bar loop: the bars sound alike (timbre similarity) and keep a steady level.
    Higher is better; -inf where the loop doesn't fit."""
    nb = len(F)
    M = F[:, 6:6 + N_MELS]
    Mz = (M - M.mean(0)) / (M.std(0) + 1e-6)
    U = Mz / (np.linalg.norm(Mz, axis=1, keepdims=True) + 1e-9)
    S = U @ U.T
    loud = _db((10 ** (F[:, :6] / 10)).sum(1))
    out = np.full(nb, -np.inf)
    for i in range(nb - L + 1):
        out[i] = S[i:i + L, i:i + L].mean() - loud[i:i + L].std() / 6
    return out


def length_features(F: np.ndarray, starts: list[int], bpm: float) -> np.ndarray:
    """Per loop start bar, for the loop length model: how cleanly 1/2/4/8 bars loop from there (as loopability())
    and how closely the next L bars repeat those L (an L-bar pattern), plus the BPM. -1 where it doesn't fit."""
    nb = len(F)
    M = F[:, 6:6 + N_MELS]
    Mz = (M - M.mean(0)) / (M.std(0) + 1e-6)
    U = Mz / (np.linalg.norm(Mz, axis=1, keepdims=True) + 1e-9)
    S = U @ U.T
    loud = _db((10 ** (F[:, :6] / 10)).sum(1))
    rows = []
    for i in starts:
        r = []
        for L in LOOP_LENGTHS:
            r.append(S[i:i + L, i:i + L].mean() - loud[i:i + L].std() / 6 if i + L <= nb else -1.0)
            r.append(float(np.mean([S[i + j, i + L + j] for j in range(L)])) if i + 2 * L <= nb else -1.0)
        rows.append(r + [bpm])
    return np.array(rows, dtype=float)


def _length_model():
    from sklearn.ensemble import HistGradientBoostingClassifier

    return HistGradientBoostingClassifier(max_depth=3, max_iter=100, learning_rate=0.05, random_state=0)


def train_lengths(rows: list) -> tuple:
    """(model or None, metrics): your loop lengths from the audio where each of your loops starts. Kept only when,
    cross-validated by track, it picks your length more often than always using your most common one."""
    from collections import Counter

    from sklearn.model_selection import StratifiedGroupKFold

    X, y, grp = [], [], []
    for k, r in enumerate(rows):
        starts, bpm, F, own = r[6], r[7], r[8], r[10]
        for c in own:
            if c["type"] != "loop" or not c.get("bars") or round(c["bars"]) not in LOOP_LENGTHS:
                continue
            t = c["ms"] / 1000
            i = int(np.argmin(np.abs(starts - t)))
            if abs(starts[i] - t) <= 60 / bpm * 1.01:
                X.append(length_features(F, [i], bpm)[0])
                y.append(round(c["bars"]))
                grp.append(k)
    y = np.array(y)
    n = Counter(y.tolist())
    if len(y) < 50 or len(n) < 2 or min(n.values()) < 5:
        return None, {"loops": len(y), "used": False, "why": "too few loops of more than one length"}
    X = np.array(X)
    common, base = n.most_common(1)[0][0], n.most_common(1)[0][1] / len(y)
    pred = np.zeros(len(y), int)
    for tr, te in StratifiedGroupKFold(5, shuffle=True, random_state=0).split(X, y, grp):
        pred[te] = _length_model().fit(X[tr], y[tr]).predict(X[te])
    acc = float((pred == y).mean())
    used = acc > base
    return (_length_model().fit(X, y) if used else None), {
        "loops": len(y), "exact": round(acc, 3), f"always_{common}_bars": round(base, 3), "used": used,
        "per_length_found": {str(L): f"{int(((pred == L) & (y == L)).sum())}/{n[L]}" for L in LOOP_LENGTHS if n[L]}}


def loop_ideas(pl: np.ndarray, F: np.ndarray, on: np.ndarray, picks: list[int], L: int, n: int = 6,
               like_yours: int = 2, peak: int = 2, zero: int = 0, align: int = 1) -> list[tuple[int, str]]:
    """Up to n loop starts: the `like_yours` best by the loop model (where you put your loops), then `peak` that
    loop most cleanly at full energy (kick on, within 1.5 dB of the loudest bar, as _idea's "Peak groove"; the
    loudest kick loops if too few are), then the ones that loop most cleanly anywhere in the track. Never on a
    suggested cue point, at least 8 bars apart."""
    nb = len(pl)
    ok = np.ones(nb, bool)
    ok[max(0, nb - L + 1):] = False
    for j in picks:
        ok[max(0, j - 1):j + 2] = False
    out: list[tuple[int, str]] = []

    def take(order, upto, why):
        for i in order:
            if len(out) >= upto:
                return
            if ok[i] and all(abs(i - k) >= max(L, 8) for k, _w in out):
                out.append((int(i), why))

    take(np.argsort(-np.where(ok, pl, -1)), like_yours, "like your loops")
    lp = loopability(F, L)
    lp[(np.arange(nb) - zero) % align != 0] = -np.inf  # clean loops start on the align-bar grid from the grid marker
    if peak:
        loud = _db((10 ** (F[:, :6] / 10)).sum(1))
        level, kicks = np.full(nb, -np.inf), np.zeros(nb, bool)
        for i in range(nb - L + 1):
            level[i] = loud[i:i + L].mean() - loud.max()
            kicks[i] = on[i:i + L].mean() > 0.5
        fits, upto = np.isfinite(lp), len(out) + peak
        body = fits.copy()  # not in the DJ intro / outro: drums mastered loud, but not the peak
        body[:int(nb * 0.15)] = body[int(nb * 0.85):] = False
        # in the body before anywhere; with the kick before without (kick_on still misses it on ~4% of tracks)
        for where, kick_needed in ((body, True), (body, False), (fits, True), (fits, False)):
            take([i for i in np.argsort(-lp) if where[i] and (kicks[i] or not kick_needed) and level[i] > -1.5],
                 upto, "peak energy, loops cleanly")
        take([i for i in np.argsort(-level) if fits[i]], upto, "peak energy")
    take(np.argsort(-lp), n, "loops cleanly")
    return out


def _idea(i: int, why: str, starts, F, on: np.ndarray, L: int, bpm: float, lp: np.ndarray, pl: np.ndarray) -> dict:
    loud = _db((10 ** (F[:, :6] / 10)).sum(1))
    level = float(loud[i:i + L].mean() - loud.max())
    k = on[i:i + L].mean() > 0.5
    fin = lp[np.isfinite(lp)]
    steady = bool(len(fin) and lp[i] >= np.percentile(fin, 90))
    return {"bar": i, "ms": round(float(starts[i]) * 1000, 3), "time": _clock(starts[i]), "bars": L,
            "len_ms": round(L * 4 * 60000 / bpm, 3), "kind": _kind(level, k, steady), "level_db": round(level, 2),
            "steady": steady, "why": why, "score": round(float(pl[i]), 2)}


def _kind(level: float, kick: bool, steady: bool) -> str:
    kind = ("Peak groove" if level > -1.5 else "Lighter groove") if kick else "No kick: pads / melody"
    return kind + (" · very steady" if steady else "")


def _clock(s: float) -> str:
    return f"{int(s // 60)}:{s % 60:04.1f}"


# ----------------------------------------------------------------------------
# What plays in a loop: Demucs (the optional `stems` extra) splits the loop's audio into drums, bass, other (synths,
# keys, pads) and vocals. Only the loops, with 4 s either side for context (Demucs works in 7.8 s chunks anyway), so a
# track's six loop ideas take ~8 s on Apple Silicon. On the instrument tagger's "voice" and the voice/instrumental head,
# Pjanoo's synth lead was a voice half the track; its separated vocals stay at -62 dB. Neither those heads, the gender
# head nor the vocal stem's pitch told male from female on 8 known singers, so no gender. Checked by ear: vocals in
# Blackberries' intro (0:03.8), vocal bits in Beautiful People (0:13.3) and Time's a Fantasy (1:09.8).
# ----------------------------------------------------------------------------

PARTS = {"drums": "drums", "bass": "bass", "other": "synth", "vocals": "vocals"}
_sep: dict = {}


def parts_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("demucs") is not None


def _separator():
    """(Demucs htdemucs, device, lock), loaded once."""
    if "model" not in _sep:
        import threading

        import torch
        from demucs.pretrained import get_model

        dev = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
        m = get_model("htdemucs")
        m.eval()
        _sep.update(model=m.to(dev), device=dev, lock=threading.Lock())
    return _sep["model"], _sep["device"], _sep["lock"]


def _parts_key(p: Path, ms: float, len_ms: float) -> str:
    return hashlib.sha1(f"{p}|{ms:.0f}|{len_ms:.0f}|{PARTS_VERSION}".encode()).hexdigest()


def _parts_shares(m: dict) -> dict:
    """From a loop's per-beat measurements (levels in dB against the loop, per stem; the drum stem's 30-150 Hz on the
    beat and its pulse), the share of beats where each part plays."""
    on = lambda xs, t: round(float(np.mean(np.asarray(xs) > t)), 3)  # noqa: E731
    sh = {k: on(v, PART_DB) for k, v in m["db"].items()}
    sh["synth_bg"] = on(m["db"]["synth"], PART_BG_DB)
    sh["kick"] = round(float(np.mean((np.asarray(m["kick_db"]) > KICK_STEM_DB) & (np.asarray(m["kick_pulse"]) > 0))), 3)
    return sh


def _parts_label(sh: dict) -> str:
    """What the loop has, from each part's share of beats: vocals through it or only bits, synth (and "no bass" with
    them when there's no bassline, for layering over another track's), else which of drums and bass ("drums only",
    "drums + bass only")."""
    tags = []
    if sh["vocals"] >= 0.5:
        tags.append("vocals")
    elif sh["vocals"] >= 0.125:  # 2 of 16 beats: a word or a vocal shot
        tags.append("vocal bits")
    if sh["synth"] >= 0.5:
        tags.append("synth")
    elif sh["synth_bg"] >= 0.5:
        tags.append("background synth")
    if tags and sh["bass"] < 0.5:
        tags.append("no bass")
    rest = [k for k in ("drums", "bass") if sh[k] >= 0.5]
    if not tags and rest:
        tags.append(" + ".join(rest) + " only")
    return " · ".join(tags)


def loop_parts(p: Path, ideas: list[dict], bpm: float, compute: bool = True) -> list[dict | None]:
    """For each loop idea ({ms, len_ms}, on Traktor's timeline): {drums, bass, synth, vocals: share of its beats where
    that stem plays (within PART_DB of the loop), synth_bg (within PART_BG_DB), kick: share of beats with a kick in the
    drum stem, label (_parts_label)}; None where not cached and compute is False. The per-beat levels are cached, so
    the thresholds can change without separating again. The kick is measured on the drum
    stem, where no bassline fills the gaps between beats: on 4 loops that kick_on (the full mix) read as kick-free,
    it heard a kick, and all 4 had one by ear."""
    got = parts_cached(p, ideas)
    todo = [l for l in ideas if l["ms"] not in got]
    if todo and compute:
        for l, m in separate_parts(p, todo, bpm):
            got[l["ms"]] = m
            store_parts(p, l, m)
    out = []
    for l in ideas:
        sh = _parts_shares(got[l["ms"]]) if l["ms"] in got else None
        out.append({**sh, "label": _parts_label(sh)} if sh else None)
    return out


def parts_cached(p: Path, ideas: list[dict]) -> dict[float, dict]:
    """{idea ms: its cached per-beat measurements} for the ideas separated before."""
    with _bars_db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS parts (k TEXT PRIMARY KEY, path TEXT, data TEXT)")
        got = {}
        for l in ideas:
            row = con.execute("SELECT data FROM parts WHERE k=?", (_parts_key(p, l["ms"], l["len_ms"]),)).fetchone()
            if row:
                got[l["ms"]] = json.loads(row[0])
    return got


def store_parts(p: Path, l: dict, m: dict) -> None:
    with _bars_db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS parts (k TEXT PRIMARY KEY, path TEXT, data TEXT)")
        con.execute("INSERT OR REPLACE INTO parts VALUES (?,?,?)", (_parts_key(p, l["ms"], l["len_ms"]), str(p), json.dumps(m)))


def separate_parts(p: Path, todo: list[dict], bpm: float, cancel=None):
    """Yields (idea, its per-beat measurements) one idea at a time, separated by Demucs. Reads and writes no cache,
    so the web page runs it outside its library lock. cancel (threading.Event): stops before the next idea
    (analysis.Cancelled); the ones yielded so far can still be stored."""
    if todo:
        import julius
        import torch
        from demucs.apply import apply_model
        from scipy.signal import butter, sosfilt

        analysis.check_cancel(cancel)
        model, dev, lock = _separator()
        stereo, sr, _nch = analysis.decode(p)
        x = np.ascontiguousarray(np.asarray(stereo, np.float32).T)
        off = traktor.audio_offset(p) / sr  # Traktor time = decoded time + off
        db = lambda y: 10 * np.log10(np.mean(y ** 2) + 1e-12)  # noqa: E731
        for l in todo:
            analysis.check_cancel(cancel)
            a = l["ms"] / 1000 - off
            b = a + l["len_ms"] / 1000
            a0, b0 = max(0.0, a - 4), min(x.shape[1] / sr, b + 4)
            seg = torch.from_numpy(x[:, int(a0 * sr):int(b0 * sr)].copy())
            if sr != model.samplerate:
                seg = julius.resample_frac(seg, int(sr), model.samplerate)
            ref = seg.mean(0)
            mu, sd = ref.mean(), ref.std() + 1e-8
            with lock, torch.no_grad():  # as the demucs command does: normalized, shifts=1, overlap 0.25
                analysis.check_cancel(cancel)  # given up while another track's separation had the model
                st = apply_model(model, ((seg - mu) / sd)[None].to(dev), device=dev, shifts=1, split=True,
                                 overlap=0.25)[0].cpu() * sd + mu
            k0 = int((a - a0) * model.samplerate)
            st = st[:, :, k0:k0 + int((b - a) * model.samplerate)].mean(1).numpy()  # (stems, samples), mono
            mix = st.sum(0)
            nb = max(1, int(round((b - a) * bpm / 60)))
            hop = len(mix) // nb
            beats = range(nb)
            mixb = [db(mix[k * hop:(k + 1) * hop]) for k in beats]
            m = {"db": {PARTS[src]: [round(db(st[j, k * hop:(k + 1) * hop]) - mixb[k], 1) for k in beats]
                        for j, src in enumerate(model.sources)}}
            low = sosfilt(butter(4, [30, 150], "bandpass", fs=model.samplerate, output="sos"),
                          st[model.sources.index("drums")])
            hit = int(0.06 * model.samplerate)  # the kick's first 60 ms, against the middle of the beat
            m["kick_db"] = [round(db(low[k * hop:k * hop + hit]) - mixb[k], 1) for k in beats]
            m["kick_pulse"] = [round(db(low[k * hop:k * hop + hit]) - db(low[k * hop + int(0.3 * hop):k * hop + int(0.75 * hop)]), 1)
                               for k in beats]
            yield l, m


def _name(i: int, first: bool, loud: np.ndarray, on: np.ndarray, nb: int, bass: np.ndarray) -> str:
    """What happens at bar i: the kick over the next 8 bars against the 16 before (so a 2-bar fill isn't a
    breakdown), the same for the sub-bass (`bass`: bars with it), and the level over 4 bars either side. Bass in: the
    bassline arrives over a kick already playing, where you'd swap basslines in a mix."""
    k_bef = on[max(0, i - 16):i].mean() > 0.5 if i else False
    k_aft = on[i:min(nb, i + 8)].mean() > 0.5
    b_bef = bass[max(0, i - 16):i].mean() > 0.5 if i else False
    b_aft = bass[i:min(nb, i + 8)].mean() > 0.5
    b, a = slice(max(0, i - 4), i), slice(i, min(nb, i + 4))
    l_bef = loud[b].mean() if i else -60.0
    l_aft = loud[a].mean()
    if first and i < nb * 0.15:
        return "Intro" if not k_aft or l_aft < -6 else "Start"
    if k_bef and not k_aft:
        return "Breakdown"
    if not k_bef and k_aft:
        return "Drop" if l_aft > -4 else "Kick in"
    if k_bef and k_aft and not b_bef and b_aft:
        return "Bass in"
    if i > nb * 0.7 and l_aft < l_bef - 2:
        return "Outro"
    if l_aft > l_bef + 2:
        return "Lift"
    return "Phrase"


def _own_bars(starts: np.ndarray, bpm: float, own: list[dict]) -> list[tuple[dict, int, float]]:
    """(cue, nearest bar, distance in beats) for each of your cue points."""
    beat = 60 / bpm
    out = []
    for c in own:
        t = c["ms"] / 1000
        i = int(np.argmin(np.abs(starts - t)))
        out.append((c, i, abs(starts[i] - t) / beat))
    return out


def corrections(starts: np.ndarray, bpm: float, own: list[dict], p: np.ndarray, zero: int, thr: float,
                window: int = 2, margin: float = 0.2) -> list[dict]:
    """Your cue points that the model would put 1-2 bars away: the bar it prefers scores at least `thr` and
    `margin` more than your bar. p must come from a model that never saw this track's cues."""
    out = []
    taken = {i for _c, i, _d in _own_bars(starts, bpm, own)}
    for c, i, dist in _own_bars(starts, bpm, own):
        if c["type"] != "cue":
            continue
        lo, hi = max(0, i - window), min(len(p), i + window + 1)
        j = lo + int(np.argmax(p[lo:hi]))
        here = p[i] if dist <= 1.01 else 0.0  # a cue that isn't near a bar start scores nothing where it is
        if (j != i or dist > 1.01) and j not in taken - {i} and p[j] >= thr and p[j] - here >= margin:
            out.append({"hotcue": c["hotcue"], "name": c["name"], "from_ms": c["ms"],
                        "to_ms": round(float(starts[j]) * 1000, 3), "from_bar": i - zero + 1, "to_bar": j - zero + 1,
                        "to_time": _clock(starts[j]), "score_here": round(float(here), 2),
                        "score_there": round(float(p[j]), 2), "phrase_grid": (j - zero) % 8 == 0})
    return out


def alternatives(starts: np.ndarray, bpm: float, mine: list[tuple[dict, int, float]], cues: list[dict], p: np.ndarray,
                 zero: int, floor: float, window: int = 4) -> list[dict]:
    """Where each of your cue points could go instead, for choosing by hand: the bar before and after, the bars up to
    `window` away that score at least `floor`, every suggested cue, and its own bar's downbeat when it's off it;
    never onto a bar another of your cues is on."""
    taken = {i for _c, i, _d in mine}
    by_bar = {q["bar"]: q for q in cues}
    out = []
    for c, i, dist in mine:
        if c["type"] != "cue":
            continue
        near = {j for j in range(max(0, i - window), min(len(starts), i + window + 1)) if p[j] >= floor or abs(j - i) == 1}
        off = abs(starts[i] - c["ms"] / 1000) / (60 / bpm) > 0.05  # more than 1/20 beat off its bar's downbeat
        opts = []
        for j in sorted((near | set(by_bar) | ({i} if off else set())) - (taken - {i})):
            if j == i and not off:
                continue  # already there
            q = by_bar.get(j)
            opts.append({"to_ms": round(float(starts[j]) * 1000, 3), "to_bar": j - zero + 1, "to_time": _clock(starts[j]),
                         "score": round(float(p[j]), 2), "phrase_grid": (j - zero) % 8 == 0, **({"name": q["name"]} if q else {})})
        if opts:  # none only when the bars either side hold your other cues
            out.append({"hotcue": c["hotcue"], "name": c["name"], "from_ms": c["ms"], "from_bar": i - zero + 1,
                        "score_here": round(float(p[i]) if dist <= 1.01 else 0.0, 2), "options": opts})
    return out


def suggest(path: str, bpm: float, grid_ms: float, measure_if_needed: bool = False,
            own: list[dict] | None = None) -> dict:
    """Suggested cue points (named by what happens there) and a mix loop for one track, plus the per-bar
    loudness and kick for drawing. Needs `cues train` once, and the track's bars measured."""
    b = model()
    if b is None:
        raise SystemExit("No cue model yet. Run: uv run dj-track-analyzer cues train")
    hit = bars(Path(path), bpm, grid_ms) if measure_if_needed else bars_get(Path(path), bpm, grid_ms)
    if hit is None:
        raise SystemExit("This track hasn't been listened to for phrases yet. Run: uv run dj-track-analyzer cues scan")
    starts, F = hit
    X, info = features(starts, F, grid_ms)
    nb = len(starts)
    from threadpoolctl import threadpool_limits

    with threadpool_limits(1):  # one track is too small to split over cores: 16 vs ~100 ms, same result
        pc = b["cue_model"].predict_proba(X)[:, 1]
        pl_all = b["loop_model"].predict_proba(X)[:, 1] if b.get("loop_model") is not None else None
    picks = _pick(pc, b["max_cues"], b["threshold"])
    own = own or []
    mine = _own_bars(starts, bpm, own)
    cues = [{"bar": i, "ms": round(float(starts[i]) * 1000, 3), "time": _clock(starts[i]),
             "name": _name(i, n == 0, info["loud"], info["kick_on"], nb, info["bass_on"]), "score": round(float(pc[i]), 2)}
            for n, i in enumerate(picks)]
    held, more = {i for _c, i, _d in mine}, []
    for i in map(int, np.argsort(-pc)):  # the six next-best bars, for a free slot when every pick is at one of your cues
        if len(more) >= 6:
            break
        if all(abs(i - j) >= 2 for j in held) and all(abs(i - j) >= 4 for j in picks + more):
            more.append(i)
    first = min([*picks, *held, *more], default=-1)
    more = [{"bar": i, "ms": round(float(starts[i]) * 1000, 3), "time": _clock(starts[i]),
             "name": _name(i, i == first, info["loud"], info["kick_on"], nb, info["bass_on"]), "score": round(float(pc[i]), 2)} for i in sorted(more)]
    moved_by_suggestion = []
    for q in cues:  # next to one of your cues, a suggestion is agreement (same bar) or a correction (1-2 bars)
        c, j, dist = min(mine, key=lambda m: abs(m[1] - q["bar"]), default=(None, None, None))
        if c is None or abs(j - q["bar"]) > 2:
            continue
        if c["type"] == "loop":
            if abs(j - q["bar"]) <= 1:
                q["at_your_loop"] = True
            continue
        if j == q["bar"] and dist <= 1.01:
            q["matches"] = c["hotcue"]
        elif not b.get("starter") and not any(m[1] == q["bar"] for m in mine):
            q["moves"] = c["hotcue"]
            moved_by_suggestion.append((c, j, dist, q))
    zero_ = int(np.argmin(np.abs(starts - grid_ms / 1000)))
    o = b.get("oof", {}).get(path)
    if o is not None and o["grid"] == _grid_key(bpm, grid_ms) and len(o["p"]) == nb:
        fair = np.asarray(o["p"], dtype=float)  # your cues were in training: use the scores made without them
    elif o is None:
        fair = pc  # not in training (fewer than 3 cues of yours): the model never saw them
    else:
        fair = None  # grid changed since training
    if b.get("starter"):  # another DJ's habits don't correct your cues
        fair = None
    fixes = corrections(starts, bpm, own, fair, zero_, b["threshold"]) if fair is not None and own else []
    for c, j, dist, q in moved_by_suggestion:  # the suggestions' moves, unless already proposed
        if any(f["hotcue"] == c["hotcue"] and f["to_bar"] == q["bar"] - zero_ + 1 for f in fixes):
            continue
        sc = fair if fair is not None else pc
        fixes.append({"hotcue": c["hotcue"], "name": c["name"], "from_ms": c["ms"], "to_ms": q["ms"],
                      "from_bar": j - zero_ + 1, "to_bar": q["bar"] - zero_ + 1, "to_time": q["time"],
                      "score_here": round(float(sc[j]) if dist <= 1.01 else 0.0, 2), "score_there": round(float(sc[q["bar"]]), 2),
                      "phrase_grid": (q["bar"] - zero_) % 8 == 0, "suggested_as": q["name"]})
    alts = alternatives(starts, bpm, mine, cues, fair if fair is not None else pc, zero_, b["threshold"] / 2)
    loop, ideas = None, []
    if pl_all is not None:
        L = b["loop_bars"]
        lp = loopability(F, L)
        zero = int(np.argmin(np.abs(starts - grid_ms / 1000)))
        ideas = [_idea(i, why, starts, F, info["kick_on"], L, bpm, lp, pl_all)
                 for i, why in loop_ideas(pl_all, F, info["kick_on"], picks, L, zero=zero, align=b.get("loop_align", 1))]
        parts = loop_parts(Path(path), ideas, bpm, compute=False) if ideas else []
        for l, pt in zip(ideas, parts):
            if pt:
                l["parts"] = pt
                l["kind"] = _kind(l["level_db"], pt["kick"] >= 0.5, l["steady"])  # the drum stem's kick over kick_on's
                if pt["label"]:
                    l["kind"] += " · " + pt["label"]
        if ideas and b.get("length_model") is not None:  # the length you'd likely give each (the page preselects it)
            with threadpool_limits(1):
                guess = b["length_model"].predict(length_features(F, [l["bar"] for l in ideas], bpm))
            for l, L in zip(ideas, guess):
                if l["bar"] + int(L) <= nb:
                    l["suggested_bars"] = int(L)
        loop = ideas[0] if ideas else None  # the one most like yours
    zero = int(np.argmin(np.abs(starts - grid_ms / 1000)))
    w = wave_get(Path(path))
    wave = {"slices": len(w[0]), "seconds": round(w[1], 3),
            "data": base64.b64encode(w[0].tobytes()).decode()} if w is not None else None
    if own:  # mark the ones you chose to ignore (the page folds them away)
        ign = ignored(path)
        for f in fixes:
            if any(_same_fix(f, g) for g in ign):
                f["ignored"] = True
    return {"loop_ideas": ideas, "parts_pending": bool(ideas) and parts_available() and any("parts" not in l for l in ideas),
            "corrections": fixes, "alternatives": alts, "wave": wave, "bpm": bpm, "bar_seconds": round(4 * 60 / bpm, 4), "first_bar_s": round(float(starts[0]), 4),
            "grid_bar": zero, "bars": nb, "cues": cues, "more_cues": more, "loop": loop,
            "loudness": [round(float(x), 1) for x in info["loud"]],
            "kick": [round(float(x), 1) for x in info["kick"]],
            "trained_at": b["trained_at"], "starter": bool(b.get("starter"))}


def scan(nml: str | None = None, workers: int | None = None, rescan: bool = False, progress=log) -> dict:
    """Measure the bars of every track in Traktor's collection that has a beat grid (once per grid)."""
    jobs = [(p, d["bpm"], d["grid_ms"]) for p, d in _one_path_each(grids(nml))]
    if rescan:
        with _bars_db() as con:
            con.execute("DELETE FROM bars")
    out = measure(jobs, workers, progress)
    out["tracks_with_grid"] = len(jobs)
    return out


def show(text: str, nml: str | None = None) -> dict:
    """Suggestions for the track whose path contains text, next to your own cues."""
    g = grids(nml)
    hits = sorted(p for p, _d in _one_path_each(g) if text.lower() in p.lower())
    if not hits:
        raise SystemExit(f'No track in Traktor with a beat grid has "{text}" in its path.')
    if len(hits) > 1:
        raise SystemExit(f'"{text}" matches {len(hits)} tracks, e.g. {Path(hits[0]).name}; use more of the name.')
    p = hits[0]
    d = g[p]
    s = suggest(p, d["bpm"], d["grid_ms"], measure_if_needed=True, own=d["cues"])
    bar_s, first = s["bar_seconds"], s["first_bar_s"]
    yours = [{"time": _clock(c["ms"] / 1000), "bar": round((c["ms"] / 1000 - first) / bar_s, 2), "type": c["type"],
              **({"bars": c["bars"]} if c["bars"] else {}), "name": c["name"]} for c in d["cues"]]
    return {"track": Path(p).name, "bpm": d["bpm"], "suggested": s["cues"], "suggested_loop": s["loop"],
            "yours": yours, "corrections": s["corrections"]}


# ----------------------------------------------------------------------------
# Approving and writing into Traktor
# ----------------------------------------------------------------------------

def _plan_db():
    import sqlite3

    from . import collection

    con = sqlite3.connect(collection.NATIVE_DB)
    con.execute("CREATE TABLE IF NOT EXISTS cue_plan (path TEXT PRIMARY KEY, grid TEXT, plan TEXT, at TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS cue_ignore (path TEXT, hotcue INTEGER, from_ms REAL, to_ms REAL, at TEXT)")
    return con


def ignored(path: str) -> list[dict]:
    """Corrections you chose to ignore for this track (your cue is right where it is)."""
    with _plan_db() as con:
        return [{"hotcue": r[0], "from_ms": r[1], "to_ms": r[2]} for r in
                con.execute("SELECT hotcue, from_ms, to_ms FROM cue_ignore WHERE path=?", (path,))]


def set_ignore(path: str, hotcue: int, from_ms: float, to_ms: float, on: bool = True) -> None:
    with _plan_db() as con:
        con.execute("DELETE FROM cue_ignore WHERE path=? AND hotcue=? AND ABS(from_ms-?)<1 AND ABS(to_ms-?)<1",
                    (path, hotcue, from_ms, to_ms))
        if on:
            con.execute("INSERT INTO cue_ignore VALUES (?,?,?,?,?)", (path, hotcue, from_ms, to_ms, time.strftime("%Y-%m-%d %H:%M")))
    from . import collection

    collection.log_feedback("cue_ignore", [(path, {"hotcue": hotcue, "from_ms": from_ms, "to_ms": to_ms, "on": on})])


def _unticked(todo: dict, g: dict) -> list[tuple[str, dict]]:
    """For each track about to be written: the suggested cue points and loop you didn't tick (not within half a bar
    of a cue you approved or already have), with the free hot-cue slots (none free: not much of a no)."""
    b, out = model(), []
    for p, pl in todo.items():
        d = g.get(p)
        if not d or not same_grid(pl["grid"], d["bpm"], d["grid_ms"]):
            continue
        try:
            s = suggest(p, d["bpm"], d["grid_ms"], own=d["cues"])
        except (SystemExit, OSError):
            continue
        half = 2 * 60000 / d["bpm"]
        taken = [a["ms"] for a in pl["plan"].get("add", [])] + [c["ms"] for c in d["cues"]]
        sug = s["cues"] + ([dict(s["loop"], type="loop")] if s.get("loop") else [])
        left = [{"ms": c["ms"], "type": c.get("type", "cue"), "name": c.get("name"), "score": c.get("score")}
                for c in sug if all(abs(c["ms"] - t) >= half for t in taken)]
        out.append((p, {"grid": pl["grid"], "free_slots": 8 - len({c["hotcue"] for c in d["all_cues"]} - {-1}),
                        "starter": bool(b and b.get("starter")), "unticked": left,
                        "ticked": len(pl["plan"].get("add", []))}))
    return out


def _same_fix(f: dict, g: dict) -> bool:
    return f["hotcue"] == g["hotcue"] and abs(f["from_ms"] - g["from_ms"]) < 1 and abs(f["to_ms"] - g["to_ms"]) < 1


def pending_corrections(path: str, fixes: list[dict]) -> list[dict]:
    """Corrections neither ignored nor already approved (an approved move of that cue to any bar settles them)."""
    ign = ignored(path)
    pl = plans().get(path)
    moves = pl["plan"]["move"] if pl else []
    gone = pl["plan"].get("remove", []) if pl else []  # a cue you approved deleting has nothing left to correct
    return [f for f in fixes if not any(_same_fix(f, g) for g in ign)
            and not any(f["hotcue"] == m["hotcue"] and abs(f["from_ms"] - m["from_ms"]) < 1 for m in moves)
            and not any(f["hotcue"] == r["hotcue"] and abs(f["from_ms"] - r["ms"]) < 1 for r in gone)]


def forget_track(path: str) -> None:
    """Drop a track's measured bars and waveform so the next look measures it again."""
    with _bars_db() as con:
        con.execute("DELETE FROM bars WHERE path=?", (path,))
        con.execute("DELETE FROM wave WHERE path=?", (path,))


def plans() -> dict[str, dict]:
    """{path: {grid, plan: {add: [...], move: [...], remove: [...]}, at}}: what you approved and `cues apply` will write."""
    with _plan_db() as con:
        return {r[0]: {"grid": r[1], "plan": json.loads(r[2]), "at": r[3]} for r in con.execute("SELECT * FROM cue_plan")}


def clean_plan(plan: dict | None) -> dict:
    """An approval from the page, as it's saved: only the fields resolve() and apply use."""
    add, move, remove = (plan or {}).get("add", []), (plan or {}).get("move", []), (plan or {}).get("remove", [])
    return {"add": [{"ms": float(a["ms"]), "name": str(a["name"])[:40], "type": "loop" if a.get("type") == "loop" else "cue",
                     "len_ms": float(a.get("len_ms") or 0), **({"hand": True} if a.get("hand") else {}),
                     **({"slot": int(a["slot"])} if a.get("slot") is not None and 0 <= int(a["slot"]) <= 7 else {})} for a in add],
            "move": [{"from_ms": float(m["from_ms"]), "to_ms": float(m["to_ms"]), "hotcue": int(m["hotcue"]),
                      "name": str(m.get("name") or "")} for m in move],
            "remove": [{"ms": float(r["ms"]), "hotcue": int(r["hotcue"]), "type": str(r["type"]), "name": str(r.get("name") or "")}
                       for r in remove if str(r.get("type")) in CUE_TYPES.values() and r.get("type") != "grid"],
            **({"renumber": True} if (plan or {}).get("renumber") else {})}


def set_plan(path: str, bpm: float, grid_ms: float, plan: dict | None) -> dict:
    """Save (or with None / an empty plan, clear) your approval for one track. Writes nothing to Traktor."""
    add, move, remove = (plan or {}).get("add", []), (plan or {}).get("move", []), (plan or {}).get("remove", [])
    with _plan_db() as con:
        if not add and not move and not remove and not (plan or {}).get("renumber"):
            con.execute("DELETE FROM cue_plan WHERE path=?", (path,))
            return {"approved": 0}
        clean = clean_plan(plan)
        con.execute("INSERT OR REPLACE INTO cue_plan VALUES (?,?,?,?)",
                    (path, _grid_key(bpm, grid_ms), json.dumps(clean), time.strftime("%Y-%m-%d %H:%M")))
    return {"approved": len(add) + len(move) + len(remove) + bool((plan or {}).get("renumber"))}


def same_cue(a: dict, b: dict) -> bool:
    """The same cue of yours: its slot, kind and place (to the millisecond)."""
    return a["hotcue"] == b["hotcue"] and a["type"] == b["type"] and abs(a["ms"] - b["ms"]) < 1


def resolve(every: list[dict], plan: dict, bpm: float) -> tuple[list[dict], list[tuple[dict, int]], list[dict], list[str], list[dict]]:
    """What an approval does to a track's cues, the same for the preview in the web page and for `apply`:
    (moves that still apply, [(cue to add, hot-cue slot)], renumbered cues, reasons for what's skipped). every = all
    the track's cues except the grid marker: {ms, type, hotcue}. Moves need your cue still where it was; new cues go
    into free slots (cue points 1-7, a loop slot 8 first), never within a bar of a cue or another new one (one you set
    by hand, "hand": only never on the same spot, since you put it there on purpose). When your
    hot cue points were numbered in time order, they stay so: a new one between 3 and 4 becomes 4 and your 4, 5, 6
    become 5, 6, 7 ([{hotcue, ms (after any move), to}]); loops and other kinds keep their slots. A new cue with a
    "slot" (0-7) you chose goes there: if a cue point holds it, that one and the ones after it move up a slot into the
    next free one (never past a loop or another kind); with a slot chosen, the rest isn't put in time order.
    Deleted cues (the 5th item: those still where they were) go first: your hot cues after a deleted one in slots 1-7
    slide back to close the gap (deleting cue 1 makes 2-7 into 1-6); slot 8 keeps its cue. With "renumber", the cue
    points in slots 1-7 (yours and new) end up in time order, filling the slots no loop or marker holds."""
    bar_ms = 4 * 60000 / bpm
    moves, skipped, removes = [], [], []
    for r in plan.get("remove", []):
        if any(same_cue(c, r) for c in every):
            removes.append(r)
        else:
            skipped.append(f"{'cue ' + str(r['hotcue'] + 1) if r['hotcue'] >= 0 else r['type']} at {_clock(r['ms'] / 1000)} "
                           "isn't where it was; not deleted")
    every = [c for c in every if not any(same_cue(c, r) for r in removes)]
    cues = [dict(c, h0=c["hotcue"]) for c in every]
    for mv in plan.get("move", []):
        hit = next((c for c in cues if c["hotcue"] == mv["hotcue"] and abs(c["ms"] - mv["from_ms"]) < 1), None)
        if hit is None:
            skipped.append(f"cue {mv['hotcue'] + 1} isn't where it was; not moved")
            continue
        hit["ms"] = mv["to_ms"]
        moves.append(mv)
    gone = [r["hotcue"] for r in removes if 0 <= r["hotcue"] < 7]
    for c in cues:  # after the moves: they find your cue by its slot as it is now
        if 0 <= c["hotcue"] < 7:
            c["hotcue"] -= sum(g < c["hotcue"] for g in gone)
    starts = [c["ms"] for c in cues if c["type"] in ("cue", "loop")]
    held = [c for c in cues if c["hotcue"] >= 0]  # your hot cues and, as they're placed, the new ones
    new: list[dict] = []
    picked = any(a.get("slot") is not None for a in plan.get("add", []))
    for a in sorted(plan.get("add", []), key=lambda a: (a.get("slot") is None, a["type"] == "loop", a["ms"])):
        near = 1 if a.get("hand") else bar_ms
        if any(abs(a["ms"] - s) < near for s in starts):
            skipped.append(f"one of your cues is already {'at' if a.get('hand') else 'within a bar of'} {_clock(a['ms'] / 1000)}")
            continue
        if any(abs(a["ms"] - n["ms"]) < near for n in new):
            skipped.append(f"{a['name']} at {_clock(a['ms'] / 1000)} is {'at the same spot as' if a.get('hand') else 'within a bar of'} another approved cue")
            continue
        at = {c["hotcue"]: c for c in held}
        if a.get("slot") is not None:
            slot, chain = a["slot"], []
            while slot + len(chain) in at:
                chain.append(at[slot + len(chain)])
            if slot + len(chain) > 7 or any(c["type"] != "cue" for c in chain):
                skipped.append(f"no room to put {a['name']} at {_clock(a['ms'] / 1000)} in slot {slot + 1}: the slots after it "
                               "are full or hold a loop" if chain else f"slot {slot + 1} isn't a hot-cue slot")
                continue
            for c in chain:
                c["hotcue"] += 1
        else:
            prefer = [7] + list(range(7)) if a["type"] == "loop" else list(range(7)) + [7]
            slot = next((s for s in prefer if s not in at), None)
            if slot is None:
                skipped.append(f"no free hot-cue slot for {a['name']} at {_clock(a['ms'] / 1000)}")
                continue
        n = {"ms": a["ms"], "type": a["type"], "hotcue": slot, "add": a}
        new.append(n)
        held.append(n)
    pts = sorted((c for c in every if c["type"] == "cue" and c["hotcue"] >= 0), key=lambda c: c["ms"])
    in_order = [c["hotcue"] for c in pts] == sorted(c["hotcue"] for c in pts)
    if in_order and not picked and any(n["type"] == "cue" for n in new):  # yours were in time order: keep them so
        mine = sorted((c for c in held if c["type"] == "cue"), key=lambda c: c["ms"])
        for c, slot in zip(mine, sorted(c["hotcue"] for c in mine)):
            c["hotcue"] = slot
    if plan.get("renumber"):  # "number in time order": cue points in slots 1-7 by time, into the slots no loop or marker holds
        free = [s for s in range(7) if s not in {c["hotcue"] for c in held if not (c["type"] == "cue" and c["hotcue"] < 7)}]
        for c, slot in zip(sorted((c for c in held if c["type"] == "cue" and c["hotcue"] < 7), key=lambda c: c["ms"]), free):
            c["hotcue"] = slot
    renum = [{"hotcue": c["h0"], "ms": c["ms"], "to": c["hotcue"]} for c in cues if c["hotcue"] != c["h0"]]
    return moves, [(n["add"], n["hotcue"]) for n in new], renum, skipped, removes


def preview(d: dict, plan: dict) -> dict:
    """Your cues and markers as they'll be once the approval is written: moved ones at their new place, new ones in
    their slot, deleted ones still listed (status "deleted") so the page can show what goes."""
    moves, adds, renum, skipped, removes = resolve(d["all_cues"], plan, d["bpm"])
    out = []
    for c in d["cues"] + d.get("marks", []):
        if any(same_cue(c, r) for r in removes):
            out.append({**c, "status": "deleted"})
            continue
        mv = next((m for m in moves if m["hotcue"] == c["hotcue"] and abs(m["from_ms"] - c["ms"]) < 1), None)
        y = {**c, "ms": mv["to_ms"], "status": "moved", "from_ms": c["ms"]} if mv else {**c, "status": "yours"}
        rn = next((r for r in renum if r["hotcue"] == c["hotcue"] and abs(r["ms"] - y["ms"]) < 1), None)
        out.append({**y, "hotcue": rn["to"], "was_hotcue": c["hotcue"]} if rn else y)  # was_hotcue: which cue it is
    beat_ms = 60000 / d["bpm"]
    for a, slot in adds:
        out.append({"ms": a["ms"], "type": a["type"], "bars": round(a["len_ms"] / beat_ms / 4, 2) if a["type"] == "loop" else None,
                    "hotcue": slot, "name": a["name"], "status": "new"})
    placed = [a["ms"] for a, _s in adds]
    left_out = [a["ms"] for a in plan.get("add", []) if not any(abs(a["ms"] - m) < 1 for m in placed)]
    left_out += [m["from_ms"] for m in plan.get("move", []) if m not in moves]
    left_out += [r["ms"] for r in plan.get("remove", []) if r not in removes]
    return {"cues": sorted(out, key=lambda c: c["ms"]), "skipped": skipped, "skipped_ms": left_out}


def _cue_tag(name: str, kind: str, ms: float, len_ms: float, slot: int) -> str:
    return (f'<CUE_V2 NAME="{traktor.esc(name)}" DISPL_ORDER="0" TYPE="{5 if kind == "loop" else 0}" START="{ms:.6f}" '
            f'LEN="{len_ms:.6f}" REPEATS="-1" HOTCUE="{slot}"></CUE_V2>\n')


def _drop_tag(entry: str, hit: re.Match) -> str:
    """The entry without one CUE_V2: self-closing or with its </CUE_V2>, and its line."""
    a, b = hit.start(), hit.end()
    if not hit.group(0).endswith("/>"):
        b = entry.index("</CUE_V2>", b) + len("</CUE_V2>")
    while a > 0 and entry[a - 1] in " \t":
        a -= 1
    if entry.startswith("\r\n", b):
        b += 2
    elif entry.startswith("\n", b):
        b += 1
    return entry[:a] + entry[b:]


def apply(nml: str | None = None, dry_run: bool = False) -> dict:
    """Write the approved cues into collection.nml: new ones into free hot-cue slots (cue points in slots 1-7 in
    time order, the loop in slot 8 when free), corrections by moving your cue, deleted ones taken out. A track whose beat grid changed
    since you approved, or whose cue was moved since, is left alone. Backup + `traktor --undo`."""
    path = traktor.find_collection(nml)
    traktor.refuse_if_tempo_view(path)
    if not dry_run and traktor.traktor_running():
        raise SystemExit("Traktor is running. Quit Traktor first: it rewrites collection.nml when it closes "
                         "and would undo these changes.")
    todo = plans()
    if not todo:
        raise SystemExit("Nothing approved yet: approve cues in the web page (uv run dj-track-analyzer web).")
    text = path.read_text(encoding="utf-8")
    stats = {"collection": str(path), "tracks": 0, "cues_added": 0, "cues_moved": 0, "cues_renumbered": 0, "cues_deleted": 0,
             "skipped": []}
    examples, done = [], set()

    def fix_entry(m: re.Match) -> str:
        entry = m.group(0)
        loc = re.search(r"<LOCATION\b[^>]*>", entry)
        p = next((c for c in traktor.location_paths(traktor.attrs(loc.group(0))) if c in todo), None) if loc else None
        if not p:
            return entry
        plan, name = todo[p], Path(p).name
        tm = re.search(r"<TEMPO\b[^>]*>", entry)
        cue_tags = list(re.finditer(r"<CUE_V2\b[^>]*>", entry))
        grid = [traktor.attrs(c.group(0)) for c in cue_tags if traktor.attrs(c.group(0)).get("TYPE") == "4"]
        bpm = float(traktor.attrs(tm.group(0)).get("BPM") or 0) if tm else 0
        if not grid or not bpm or not same_grid(plan["grid"], bpm, float(grid[0]["START"])):
            stats["skipped"].append(f"{name}: beat grid changed since you approved; approve again")
            return entry
        done.add(p)
        old = entry
        every = [{"ms": float(t["START"]), "type": CUE_TYPES.get(t.get("TYPE"), t.get("TYPE")), "hotcue": int(t.get("HOTCUE", -1))}
                 for t in (traktor.attrs(c.group(0)) for c in cue_tags) if t.get("TYPE") != "4"]
        moves, adds, renum, skipped, removes = resolve(every, plan["plan"], bpm)
        stats["skipped"] += [f"{name}: {why}" for why in skipped]
        code = {v: k for k, v in CUE_TYPES.items()}
        for r in removes:  # first: a renumbered cue may take the slot it frees
            hit = next(c for c in re.finditer(r"<CUE_V2\b[^>]*>", entry)
                       if traktor.attrs(c.group(0)).get("HOTCUE", "-1") == str(r["hotcue"])
                       and traktor.attrs(c.group(0)).get("TYPE") == code[r["type"]]
                       and abs(float(traktor.attrs(c.group(0)).get("START", -1e9)) - r["ms"]) < 1)
            entry = _drop_tag(entry, hit)
            stats["cues_deleted"] += 1
            what = f"cue {r['hotcue'] + 1}" if r["hotcue"] >= 0 else r["type"]
            examples.append(f"{name}: − {what}{' ' + r['name'] if r['name'] else ''} at {_clock(r['ms'] / 1000)} deleted")
        for mv in moves:
            hit = next(c for c in re.finditer(r"<CUE_V2\b[^>]*>", entry)
                       if traktor.attrs(c.group(0)).get("HOTCUE") == str(mv["hotcue"])
                       and abs(float(traktor.attrs(c.group(0)).get("START", -1e9)) - mv["from_ms"]) < 1)
            entry = entry[:hit.start()] + traktor.set_attr(hit.group(0), "START", f"{mv['to_ms']:.6f}") + entry[hit.end():]
            stats["cues_moved"] += 1
            examples.append(f"{name}: cue {mv['hotcue'] + 1} {_clock(mv['from_ms'] / 1000)} → {_clock(mv['to_ms'] / 1000)}")
        hits = [(r, next(c for c in re.finditer(r"<CUE_V2\b[^>]*>", entry)  # found all first: the slots swap among themselves
                         if traktor.attrs(c.group(0)).get("HOTCUE") == str(r["hotcue"])
                         and abs(float(traktor.attrs(c.group(0)).get("START", -1e9)) - r["ms"]) < 1)) for r in renum]
        for r, hit in sorted(hits, key=lambda x: -x[1].start()):  # from the end, so earlier offsets still hold
            entry = entry[:hit.start()] + traktor.set_attr(hit.group(0), "HOTCUE", str(r["to"])) + entry[hit.end():]
            stats["cues_renumbered"] += 1
            examples.append(f"{name}: cue {r['hotcue'] + 1} → slot {r['to'] + 1}")
        new = ""
        for a_, slot in adds:
            new += _cue_tag(a_["name"], a_["type"], a_["ms"], a_["len_ms"], slot)
            stats["cues_added"] += 1
            examples.append(f"{name}: + {a_['name']} at {_clock(a_['ms'] / 1000)} in slot {slot + 1}")
        if new:
            k = entry.rfind("</ENTRY>")
            entry = entry[:k] + new + entry[k:]
        if entry != old:
            stats["tracks"] += 1
            # so Traktor doesn't read the file's own tags again on load (they could carry older cues)
            stamp = next((s for s in map(traktor.file_stamp, traktor.location_paths(traktor.attrs(loc.group(0)))) if s), None)
            if stamp:
                entry = traktor.set_entry_stamp(entry, stamp)
        return entry

    c0 = text.find("<COLLECTION")
    c1 = text.find("</COLLECTION>", c0)
    if c0 == -1 or c1 == -1:
        raise SystemExit(f"{path} doesn't look like a Traktor collection (no COLLECTION section).")
    body = re.sub(r"<ENTRY\b.*?</ENTRY>", fix_entry, text[c0:c1], flags=re.S)
    stats["not_in_collection"] = len(todo) - len(done) - sum("beat grid changed" in s for s in stats["skipped"])
    stats["changes"] = examples
    unticked = []
    if not dry_run and done:  # what you left unticked, from the cues as they are before this write
        try:
            unticked = _unticked({p: todo[p] for p in done}, grids(str(path)))
        except Exception as err:  # never in the way of the write
            log(f"Unticked suggestions not recorded: {err}")
    out = traktor._save(path, text, text[:c0] + body + text[c1:], stats, dry_run)
    if not dry_run and out.get("changed"):
        with _plan_db() as con:  # written: these approvals are done
            con.executemany("DELETE FROM cue_plan WHERE path=?", [(p,) for p in done])
        from . import collection

        collection.log_feedback("cue_unticked", unticked)
        out["undo"] = "uv run dj-track-analyzer traktor --undo"
    return out


def main(args) -> None:
    if args.action == "train":
        out = train(args.nml, workers=args.workers)
    elif args.action == "scan":
        out = scan(args.nml, workers=args.workers, rescan=args.rescan)
    elif args.action == "apply":
        out = apply(args.nml, dry_run=args.dry_run)
    else:
        if not args.track:
            raise SystemExit('Which track? uv run dj-track-analyzer cues show "part of the name"')
        out = show(args.track, args.nml)
    print(json.dumps(out, indent=2, ensure_ascii=False))
