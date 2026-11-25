"""
groove_genre: structural genre evidence for EDM tracks and DJ mixes.

Measures what genre labels are built on (tempo, kick, bass rhythm, hi-hat pattern,
sidechain, harmony, breakdowns) and uses it to check and correct the scores of an
existing genre/style model, so that, for example, a melodic techno set played at
145 BPM is not called psytrance when it has no psytrance bassline.

Dependencies: numpy, scipy, librosa (>= 0.10), ffmpeg on PATH.

Library use (part of dj_track_analyzer; `analyze` runs it on every DJ mix):
    from dj_track_analyzer.groove_genre import analyze, genre_evidence
    result = analyze("set.mp3")                       # features + summary
    verdict = genre_evidence(result["summary"], model_scores={"Psy-Trance": 0.56, ...})

CLI (from track-analyzer/, `uv run`):
    python -m dj_track_analyzer.groove_genre set.mp3 [--model-scores scores.json] [--stride 2] [--json out.json]
    python -m dj_track_analyzer.groove_genre --self-test
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

import numpy as np

SR, HOP, NFFT = 22050, 256, 2048
FR = SR / HOP  # feature frames per second


# --------------------------------------------------------------------------- audio io
def load_audio(path: str, sr: int = SR) -> np.ndarray:
    """Decode any ffmpeg-readable file to mono float32 at `sr`."""
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


# --------------------------------------------------------------------------- helpers
def _band(S2, freqs, lo, hi):
    m = (freqs >= lo) & (freqs < hi)
    return S2[m].sum(0)


def _fold(env, period, phase, nb):
    t = ((np.arange(len(env)) - phase) % period) / period
    idx = np.minimum((t * nb).astype(int), nb - 1)
    cnt = np.bincount(idx, minlength=nb)
    return np.bincount(idx, weights=env, minlength=nb) / np.maximum(cnt, 1)


def _onsets(env):
    return np.maximum(np.diff(np.log1p(env * 1e3), prepend=0), 0)


def estimate_tempo(onset_env, lo=60.0, hi=200.0, prefer=(110.0, 180.0), hint=None):
    """Coarse autocorrelation estimate, then fine refinement by beat-fold contrast.
    Returns (bpm, contrast). `hint` (bpm) restricts the search to hint +/- 3 %."""
    o = onset_env - onset_env.mean()
    ac = np.correlate(o, o, "full")[len(o) - 1:]
    if hint:
        lo, hi = hint * 0.97, hint * 1.03
    lags = np.arange(1, len(ac))
    bpm = 60 * FR / lags
    m = (bpm >= lo) & (bpm <= hi)
    if not m.any():
        return float(hint or 0.0), 0.0
    w = ac[1:][m] * np.where((bpm[m] >= prefer[0]) & (bpm[m] <= prefer[1]), 1.0, 0.8)
    best = _refine(onset_env, bpm[m][np.argmax(w)])
    if not hint and best[1] < prefer[0] and 2 * best[1] <= hi:
        # A kick on every beat with a 2-beat pattern on top (accented kicks, a bassline in the kick band) can make
        # the 2-beat lag the stronger autocorrelation: 125 read as 62.5. Folded at that half tempo, a second kick
        # peak half a cycle after the first means a kick on every beat; a real half-time groove has none there.
        f = _fold(onset_env, 60 * FR / best[1], 0, 64)
        f = np.roll(f, -int(np.argmax(f)))
        if f[29:36].max() >= HALF_BEAT_PEAK * f[0]:
            best = _refine(onset_env, 2 * best[1])
    return float(best[1]), float(best[0])


HALF_BEAT_PEAK = 0.25  # estimate_tempo doubles a tempo below the preferred range when the kick fold has a peak of
                       # this share half a cycle after the main one (real tracks read half: 0.32-0.95; half-time: 0.07)


def _refine(onset_env, coarse):
    """(contrast, bpm) of the sharpest 32-bin beat fold within coarse +/- 1.5 BPM."""
    best = (-1.0, coarse)
    for b in np.arange(coarse - 1.5, coarse + 1.5001, 0.05):
        f = _fold(onset_env, 60 * FR / b, 0, 32)
        c = f.max() / (f.mean() + 1e-9)
        if c > best[0]:
            best = (c, b)
    return best


def gallop_index(bass_profile: np.ndarray) -> float:
    """Share of between-kick bass movement at the 16th-note rate (64-bin beat profile,
    kick onset at bin 0). Calibration: psytrance KBBB 1.00, sustained 0.33, offbeat 0.11."""
    seg = bass_profile[16:64] - bass_profile[16:64].mean()
    X = np.abs(np.fft.rfft(seg))
    return float(X[3] / (X[1] + X[2] + X[3] + 1e-9))


# Krumhansl-Kessler key profiles
_MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def _key_mode(chroma_mean):
    c = chroma_mean - chroma_mean.mean()
    best = (-9.0, "maj")
    for mode, prof in (("maj", _MAJ), ("min", _MIN)):
        p = prof - prof.mean()
        for r in range(12):
            v = np.corrcoef(c, np.roll(p, r))[0, 1]
            if v > best[0]:
                best = (v, mode)
    return best[1], float(best[0])


# --------------------------------------------------------------------------- per-window features
def analyze_window(y: np.ndarray, bpm_hint: float | None = None) -> dict:
    """Features for one excerpt (ideally 20-60 s of steady groove)."""
    import librosa

    S = np.abs(librosa.stft(y, n_fft=NFFT, hop_length=HOP))
    H, P = librosa.decompose.hpss(S, margin=1.0)
    S2, H2, P2 = S ** 2, H ** 2, P ** 2
    freqs = librosa.fft_frequencies(sr=SR, n_fft=NFFT)

    kick = _band(P2, freqs, 40, 110)
    kon = _onsets(kick)
    bpm, contrast = estimate_tempo(kon, hint=bpm_hint)
    per = 60 * FR / max(bpm, 1e-6)
    phase = np.argmax(_fold(kon, per, 0, 64)) / 64 * per

    def prof(env, nb=64):
        p = _fold(env, per, phase, nb)
        return p / (p.max() + 1e-12)

    pk = prof(kick)
    pb = prof(_band(S2, freqs, 60, 200))
    ph = prof(_band(P2, freqs, 7000, 11000))
    pm = prof(_band(H2, freqs, 300, 3000))

    s = int(np.argmax(pk))
    after = np.where(pk[s:] < 0.05)[0]
    kick_ms = (s + (after[0] if len(after) else 64 - s)) / 64 * per / FR * 1000
    rumble = float(pk[24:56].mean())                    # kick-band energy between kicks

    s2, s3, s4 = pb[18:28].mean(), pb[36:46].mean(), pb[50:60].mean()
    bass_swell = s3 / ((s2 + s4) / 2 + 1e-9)
    hoff, h2, h4 = ph[32:39].mean(), ph[16:23].mean(), ph[48:55].mean()
    hat_offbeat = hoff / ((h2 + h4) / 2 + 1e-9)
    hat_16ths = min(h2, h4) / (hoff + 1e-9)
    pump = 1 - pm[2:12].mean() / (pm[26:40].mean() + 1e-9)

    C = librosa.feature.chroma_stft(S=H2, sr=SR, tuning=0.0)
    blk = max(int(round(4 * per)), 1)
    cb = [C[:, i:i + blk].mean(1) for i in range(0, C.shape[1] - blk + 1, blk)]
    dists = [1 - (a @ b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9) for a, b in zip(cb, cb[1:])]
    mode, key_conf = _key_mode(C.mean(1))

    melodic = float(_band(H2, freqs, 300, 3000).mean() /
                    (_band(H2, freqs, 300, 3000).mean() + _band(P2, freqs, 300, 3000).mean() + 1e-12))

    return dict(
        bpm=bpm, kick_contrast=contrast, kick_level=float(kick.mean()),
        kick_ms=float(kick_ms), rumble=rumble, gallop=gallop_index(pb),
        bass_swell=float(bass_swell), hat_offbeat=float(hat_offbeat), hat_16ths=float(hat_16ths),
        pump=float(pump), chord_change=float(np.mean(np.array(dists) > 0.15)) if dists else 0.0,
        chroma_dist=float(np.mean(dists)) if dists else 0.0, minor=mode == "min", key_conf=key_conf,
        melodic_ratio=melodic, profiles=dict(kick=pk.tolist(), bass=pb.tolist(), hats=ph.tolist()),
    )


# --------------------------------------------------------------------------- breakdowns
def find_breakdowns(y: np.ndarray, bpm: float, min_bars: int = 4, thresh: float = 0.3) -> dict:
    """Stretches where kick-band energy drops below `thresh` x median, measured in bars."""
    from scipy import signal

    sr = SR // 2
    x = y[::2]
    sos = signal.butter(4, [40, 110], btype="band", fs=sr, output="sos")
    e = np.abs(signal.sosfilt(sos, x))
    bar = 4 * 60 / bpm
    F = int(bar * sr)
    kb = np.array([np.sqrt(np.mean(e[i:i + F] ** 2)) for i in range(0, len(e) - F, F)])
    if len(kb) == 0:
        return dict(bars=0, runs=[])
    out = kb < thresh * np.median(kb)
    runs, i = [], 0
    while i < len(out):
        if out[i]:
            j = i
            while j < len(out) and out[j]:
                j += 1
            if j - i >= min_bars and i * bar > 20:
                runs.append((round(i * bar, 1), int(j - i)))
            i = j
        else:
            i += 1
    return dict(bars=int(len(kb)), bar_seconds=bar, runs=runs)


# --------------------------------------------------------------------------- whole file
def window_starts(n_samples: int, window: float = 30.0, stride: int = 1) -> list[int]:
    """First sample of each analyzed window (at SR)."""
    n = int(window * SR)
    return list(range(0, max(n_samples - n, 0) + 1, n))[::stride] or [0]


def analyze(src, window: float = 30.0, stride: int = 1, bpm_hint: float | None = None, progress=None,
            run=None) -> dict:
    """Analyze a file path or mono float32 array at 22,050 Hz.
    stride=2 analyzes every 2nd window (about twice as fast, same conclusions on long mixes).
    run(starts, n) -> analyze_window results in the starts' order (windows y[s:s + n]), for running them
    elsewhere (the analyzer's process pool); default: one after another here."""
    y = load_audio(src) if isinstance(src, str) else np.asarray(src, dtype=np.float32)
    n = int(window * SR)
    starts = window_starts(len(y), window, stride)
    if run is not None:
        wins = run(starts, n)
    else:
        wins = []
        for k, w0 in enumerate(starts):
            wins.append(analyze_window(y[w0:w0 + n], bpm_hint=bpm_hint))
            if progress:
                progress(k + 1, len(starts))
    for f, w0 in zip(wins, starts):
        f["t"] = w0 / SR
    return dict(windows=wins, summary=summarize(wins, y))


def summarize(wins: list, y: np.ndarray | None = None) -> dict:
    kl = np.median([w["kick_level"] for w in wins])
    G = [w for w in wins if w["kick_level"] > 0.5 * kl and w["kick_contrast"] > 2.0] or wins
    med = lambda k, src=G: float(np.median([w[k] for w in src]))
    bpm = med("bpm")
    pb = np.mean([w["profiles"]["bass"] for w in G], 0)
    out = dict(
        duration_s=float(len(y) / SR) if y is not None else None,
        windows=len(wins), groove_windows=len(G),
        bpm=bpm, bpm_iqr=[float(np.percentile([w["bpm"] for w in G], q)) for q in (25, 75)],
        tempo_sections=_tempo_sections(wins),
        kick_ms=med("kick_ms"), rumble=med("rumble"),
        gallop=med("gallop"), gallop_profile=gallop_index(pb / pb.max()),
        psy_windows=float(np.mean([w["gallop"] > 0.5 for w in G])),
        bass_swell=med("bass_swell"), hat_offbeat=med("hat_offbeat"), hat_16ths=med("hat_16ths"),
        pump=med("pump"), chord_change=med("chord_change", wins), chroma_dist=med("chroma_dist", wins),
        minor_share=float(np.mean([w["minor"] for w in wins])), melodic_ratio=med("melodic_ratio", wins),
    )
    if y is not None:
        br = find_breakdowns(y, bpm)
        L = np.array([r[1] for r in br["runs"]]) if br["runs"] else np.array([], dtype=int)
        out.update(
            breakdowns=len(L), breakdown_median_bars=float(np.median(L)) if len(L) else 0.0,
            breakdowns_32plus=int(np.sum(L >= 32)), breakdown_longest_bars=int(L.max()) if len(L) else 0,
            breakdown_share=float(L.sum() / max(br["bars"], 1)),
            long_breakdown_share=float(np.sum(L >= 32) / len(L)) if len(L) else 0.0,
        )
    return out


def _tempo_sections(wins, tol=1.0):
    secs = []
    for w in wins:
        if w["kick_contrast"] <= 2.0:
            continue
        if secs and abs(secs[-1]["bpm"] - w["bpm"]) <= tol:
            secs[-1]["end"] = w["t"] + 30
            secs[-1]["_v"].append(w["bpm"])
            secs[-1]["bpm"] = float(np.median(secs[-1]["_v"]))
        else:
            secs.append(dict(start=w["t"], end=w["t"] + 30, bpm=w["bpm"], _v=[w["bpm"]]))
    for s in secs:
        s.pop("_v")
    return [s for s in secs if s["end"] - s["start"] >= 60]


# --------------------------------------------------------------------------- genre evidence
def _range_fit(x, lo, hi, soft):
    if lo <= x <= hi:
        return 1.0
    d = lo - x if x < lo else x - hi
    return float(max(0.0, 1 - d / soft))


def _ramp(x, a, b):
    """0 at a, 1 at b (works for a > b too)."""
    return float(np.clip((x - a) / (b - a), 0, 1))


def _breaks(f, check):
    """A breakdown check, or None (left out) when f has no breakdown counts: a track's slot in a mix is summarized
    from its windows alone (summarize(wins), no audio), too short to count 32-bar breakdowns."""
    v = f.get("long_breakdown_share")
    return None if v is None else check(v)


# Each genre: tempo range plus trait checks. Every check returns (fit 0..1, reason), or None to leave it out.
GENRES = {
    "Psytrance": dict(bpm=(136, 150), family="psy", gate=lambda f: max(_ramp(f["gallop"], 0.30, 0.55), _ramp(f.get("psy_windows", 0), 0.15, 0.50)), checks=[
        lambda f: (_ramp(f["gallop"], 0.30, 0.55), f"16th-note bass gallop {f['gallop']:.2f} (psy needs about 0.5+)"),
        lambda f: (_ramp(f["hat_16ths"], 0.15, 0.40), f"16th hats at {f['hat_16ths']:.0%} of the offbeat hat"),
    ]),
    "Goa Trance": dict(bpm=(135, 150), family="psy", gate=lambda f: max(_ramp(f["gallop"], 0.30, 0.55), _ramp(f.get("psy_windows", 0), 0.15, 0.50)), checks=[
        lambda f: (_ramp(f["gallop"], 0.30, 0.55), f"16th-note bass gallop {f['gallop']:.2f}"),
    ]),
    "Darkpsy": dict(bpm=(145, 165), family="psy", gate=lambda f: max(_ramp(f["gallop"], 0.30, 0.55), _ramp(f.get("psy_windows", 0), 0.15, 0.50)), checks=[
        lambda f: (_ramp(f["gallop"], 0.30, 0.55), f"16th-note bass gallop {f['gallop']:.2f}"),
    ]),
    "Hi-Tech": dict(bpm=(165, 200), family="psy", gate=lambda f: max(_ramp(f["gallop"], 0.30, 0.55), _ramp(f.get("psy_windows", 0), 0.15, 0.50)), checks=[
        lambda f: (_ramp(f["gallop"], 0.30, 0.55), f"16th-note bass gallop {f['gallop']:.2f}"),
    ]),
    "Tech Trance": dict(bpm=(136, 147), family="trance", checks=[
        lambda f: (_ramp(f["hat_offbeat"], 1.5, 3.0), f"offbeat open hat {f['hat_offbeat']:.1f}x the other 16ths"),
        lambda f: (1 - _ramp(f["gallop"], 0.40, 0.60), "no psytrance gallop" if f["gallop"] < 0.4 else "psy-style gallop present"),
        lambda f: _breaks(f, lambda v: (1 - _ramp(v, 0.25, 0.50), f"{v:.0%} of breakdowns are 32+ bars")),
    ]),
    "Uplifting Trance": dict(bpm=(134, 142), family="trance", checks=[
        lambda f: _breaks(f, lambda v: (_ramp(v, 0.10, 0.35), f"{v:.0%} of breakdowns are 32+ bars (uplifting needs long ones)")),
        lambda f: (_ramp(f["chord_change"], 0.10, 0.40), f"chords change in {f['chord_change']:.0%} of bars"),
        lambda f: (1 - _ramp(f["gallop"], 0.40, 0.60), "no psytrance gallop" if f["gallop"] < 0.4 else "psy-style gallop present"),
    ]),
    "Progressive Trance": dict(bpm=(126, 138), family="trance", checks=[
        lambda f: (_ramp(f["hat_offbeat"], 1.5, 3.0), f"offbeat open hat {f['hat_offbeat']:.1f}x"),
    ]),
    "Hard Trance": dict(bpm=(140, 155), family="trance", checks=[
        lambda f: (_ramp(f["bass_swell"], 1.1, 1.6), f"offbeat bass swell {f['bass_swell']:.2f}x"),
        lambda f: _breaks(f, lambda v: (_ramp(v, 0.05, 0.25), f"{v:.0%} of breakdowns are 32+ bars (hard trance builds to long euphoric breaks)")),
        lambda f: (_ramp(f["hat_offbeat"], 1.5, 3.0), f"offbeat open hat {f['hat_offbeat']:.1f}x"),
    ]),
    "Melodic Techno": dict(bpm=(118, 128), family="techno", checks=[
        lambda f: (_ramp(f["minor_share"], 0.3, 0.6), f"{f['minor_share']:.0%} of windows in minor keys"),
        lambda f: (1 - _ramp(f["chord_change"], 0.30, 0.60), f"chords change in {f['chord_change']:.0%} of bars"),
    ]),
    "Hard Techno": dict(bpm=(145, 165), family="techno",
                        gate=lambda f: max(_ramp(f["rumble"], 0.10, 0.30), 1 - _ramp(f["melodic_ratio"], 0.35, 0.50)), checks=[
        lambda f: (_ramp(f["rumble"], 0.10, 0.30), f"kick energy between kicks {f['rumble']:.2f} (rumble kick needs about 0.3)"),
        lambda f: (1 - _ramp(f["melodic_ratio"], 0.45, 0.70), f"melodic share {f['melodic_ratio']:.2f}"),
    ]),
    "Techno": dict(bpm=(125, 142), family="techno", checks=[
        lambda f: (1 - _ramp(f["melodic_ratio"], 0.50, 0.75), f"melodic share {f['melodic_ratio']:.2f}"),
    ]),
}

# Map common style-model labels (Discogs/Essentia style names and variants) onto the table above.
ALIASES = {
    "psy-trance": "Psytrance", "psytrance": "Psytrance", "goa trance": "Goa Trance", "dark psy": "Darkpsy",
    "darkpsy": "Darkpsy", "hi-tech": "Hi-Tech", "hitech": "Hi-Tech", "tech trance": "Tech Trance",
    "uplifting trance": "Uplifting Trance", "progressive trance": "Progressive Trance", "hard trance": "Hard Trance",
    "melodic techno": "Melodic Techno", "melodic house & techno": "Melodic Techno", "hard techno": "Hard Techno",
    "techno": "Techno", "trance": "Tech Trance",
}


def genre_evidence(f: dict, model_scores: dict | None = None, floor: float | None = None) -> dict:
    """Score each genre from measured structure, optionally re-weighting a model's scores.

    Returns {"fits": {genre: {fit, tempo_fit, reasons}}, "ranking": [...], "label": {...},
             "adjusted_model_scores": {...} (if model_scores given)}.
    A model score is multiplied by floor + (1 - floor) * fit, so a style the audio clearly
    contradicts (no gallop -> psytrance) is pushed down but never deleted outright."""
    floor = FLOOR if floor is None else floor
    fits = {}
    for g, spec in GENRES.items():
        lo, hi = spec["bpm"]
        tf = _range_fit(f["bpm"], lo, hi, soft=4.0)
        parts = [p for p in (c(f) for c in spec["checks"]) if p is not None]
        trait = float(np.mean([p[0] for p in parts])) if parts else 1.0
        gate = spec["gate"](f) if "gate" in spec else 1.0      # defining trait: absent -> genre ruled out
        fit = tf * (0.1 + 0.9 * gate) * (0.35 + 0.65 * trait)
        tempo_reason = f"tempo {f['bpm']:.1f} BPM vs {lo}-{hi}"
        fits[g] = dict(fit=round(fit, 3), tempo_fit=round(tf, 3),
                       reasons=[tempo_reason] + [p[1] for p in parts],
                       checks=[[round(tf, 3), tempo_reason]] + [[round(float(p[0]), 3), p[1]] for p in parts])
    ranking = sorted(fits, key=lambda g: -fits[g]["fit"])
    top = ranking[0]
    descriptor = top
    if top in ("Tech Trance", "Hard Trance") and f["minor_share"] >= 0.5 and f["chord_change"] < 0.3 and f["melodic_ratio"] >= 0.56:
        descriptor = "Melodic " + top
    tags = [g for g in ranking[1:4] if fits[g]["fit"] >= 0.6]
    if f.get("psy_windows", 0) >= 0.1 and "Psytrance" not in tags and top != "Psytrance":
        tags.append("Psytrance (moments)")
    out = dict(fits=fits, ranking=ranking, label=dict(category=top, descriptor=descriptor, secondary=tags))
    if model_scores:
        adj = {}
        from ..analysis import split_style  # model labels come as "Electronic---Psy-Trance"

        for name, s in model_scores.items():
            g = ALIASES.get(split_style(name)[0].lower().strip())
            factor = floor + (1 - floor) * fits[g]["fit"] if g else 1.0
            adj[name] = round(float(s) * factor, 4)
        out["adjusted_model_scores"] = dict(sorted(adj.items(), key=lambda kv: -kv[1]))
    return out


FLOOR = 0.15  # genre_evidence's default floor: the least share of a model score the evidence keeps

# ---------------------------------------------------------------------------- reference check (calibration hook)
# Features shown per genre in the reference check: the ones the GENRES checks read.
REFERENCE_FEATURES = ("bpm", "gallop", "psy_windows", "hat_offbeat", "hat_16ths", "bass_swell", "rumble",
                      "melodic_ratio", "minor_share", "chord_change", "long_breakdown_share")
REFERENCE_SKIP_ALIASES = {"trance"}
NO_RULE_FITS = "no rule fits"  # every genre's fit is 0 (e.g. the tempo read half-time): no label to speak of  # a set just called "Trance" may be any trance: no rules to check it against


def reference_genre(name: str) -> str | None:
    """The GENRES entry a set of labelled examples stands for: a folder, playlist or Genre-field name like
    "Psytrance", "tech_trance" or "DJ / Psy-Trance". None when no groove rules exist for it."""
    n = name.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    for cand in (n, n.replace("_", " "), n.replace("_", "-")):
        hit = {g.lower(): g for g in GENRES}.get(cand)
        if hit:
            return hit
        if cand in ALIASES and cand not in REFERENCE_SKIP_ALIASES:
            return ALIASES[cand]
    return None


def reference_report(examples: dict[str, list[tuple[str, dict]]], floor: float = FLOOR) -> dict:
    """Check the groove rules against labelled reference tracks. `examples`: {GENRES name: [(track, summary)]},
    each summary from analyze() on the whole track. Reports, per genre, the feature medians and ranges, how many
    examples the rules label as their own genre, the share of the model's score for their own genre the evidence
    would keep (floor + (1 - floor) * fit: what a wrong demotion costs), and each mislabelled example with the
    checks that failed it. Only reports: thresholds are changed by hand after looking at these numbers."""
    genres, total, wrong = {}, 0, 0
    for g, items in examples.items():
        rows = []
        for track, s in items:
            ev = genre_evidence(s)
            own = ev["fits"][g]
            rank = ev["ranking"].index(g) + 1
            top_fit = ev["fits"][ev["label"]["category"]]["fit"]
            rows.append(dict(track=track, label=ev["label"]["category"] if top_fit > 0 else NO_RULE_FITS, rank=rank,
                             own_fit=own["fit"], top_fit=top_fit,
                             kept=round(floor + (1 - floor) * own["fit"], 3),
                             failed=[r for f, r in sorted(own["checks"], key=lambda c: c[0]) if f < 0.5][:WHY_REASONS],
                             features={k: round(float(s[k]), 3) for k in REFERENCE_FEATURES if s.get(k) is not None}))
        if not rows:
            continue
        feats = {}
        for k in REFERENCE_FEATURES:
            v = [r["features"][k] for r in rows if k in r["features"]]
            if v:
                feats[k] = dict(median=round(float(np.median(v)), 3), lo=round(float(min(v)), 3), hi=round(float(max(v)), 3))
        bad = [r for r in rows if r["label"] != g]
        genres[g] = dict(examples=len(rows), agree=len(rows) - len(bad),
                         kept_median=round(float(np.median([r["kept"] for r in rows])), 3),
                         kept_min=min(r["kept"] for r in rows),
                         features=feats, mislabelled=bad, tracks=rows)
        total += len(rows)
        wrong += len(bad)
    return dict(examples=total, agree=total - wrong, floor=floor, genres=genres)


def measure_file(path: str) -> dict:
    """The whole-track summary of one reference file (every 30 s window, plus its breakdowns)."""
    return analyze(str(path))["summary"]


def reference_lines(rep: dict) -> list[str]:
    """The reference report as plain lines (CLI)."""
    out = [f"Groove rules vs your examples: {rep['agree']} of {rep['examples']} labelled as their own genre "
           f"(floor {rep['floor']})."]
    for g, d in rep["genres"].items():
        out.append(f"\n{g}: {d['agree']}/{d['examples']} agree; evidence keeps {d['kept_median']:.0%} of the model's "
                   f"{g} score (worst {d['kept_min']:.0%})")
        out += [f"  {k:22} {v['median']:7.3f}   ({v['lo']:.3f} - {v['hi']:.3f})" for k, v in d["features"].items()]
        for r in d["mislabelled"]:
            out.append(f"  ✗ {r['track']}: {r['label']} (own fit {r['own_fit']:.2f}, rank {r['rank']}): "
                       + ("; ".join(r["failed"]) or "other genre fits better"))
    return out


WHY_REASONS = 3        # reasons shown per genre
WHY_DEMOTED = 0.5      # a model style counts as demoted when the evidence keeps less than this share of its score
WHY_MIN_SCORE = 0.1    # ... and the model gave it at least this much


def why(ev: dict, model_scores: dict | None = None) -> list[str]:
    """Short explanation: the label's strongest reasons, then each model style the evidence pushed down with the
    checks that failed it most, e.g. "Psy-Trance demoted 0.56 -> 0.09: 16th-note bass gallop 0.24 (psy needs about 0.5+)"."""
    from ..analysis import split_style

    top = ev["label"]["category"]
    best = sorted(ev["fits"][top]["checks"], key=lambda c: -c[0])[:WHY_REASONS]
    out = [f"{ev['label']['descriptor']}: " + "; ".join(r for _f, r in best)]
    adj = ev.get("adjusted_model_scores") or {}
    for name, s in sorted((model_scores or {}).items(), key=lambda kv: -kv[1]):
        g = ALIASES.get(split_style(name)[0].lower().strip())
        if not g or s < WHY_MIN_SCORE or adj.get(name, s) >= WHY_DEMOTED * s:
            continue
        failed = sorted(ev["fits"][g]["checks"], key=lambda c: c[0])
        worst = [c for c in failed if c[0] < 1][:WHY_REASONS] or failed[:1]   # only what counted against it
        out.append(f"{split_style(name)[0]} demoted {s:.2f} -> {adj[name]:.2f}: " + "; ".join(r for _f, r in worst))
    return out


# --------------------------------------------------------------------------- self-test
def _synth(pattern: str, bpm=145.0, seconds=30.0, seed=0):
    """Kick + bass + offbeat hat test signal. pattern: 'psy' (KBBB), 'offbeat', 'sustained'."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    beat = 60 / bpm
    y = np.zeros(n, dtype=np.float64)
    kick_len = int(0.09 * SR)
    kt = np.arange(kick_len) / SR
    kick = np.sin(2 * np.pi * (50 + 120 * np.exp(-kt * 40)) * kt) * np.exp(-kt * 35)
    hat = rng.standard_normal(int(0.04 * SR)) * np.exp(-np.arange(int(0.04 * SR)) / SR * 80)
    from scipy import signal
    hat = signal.sosfilt(signal.butter(4, 7000, "hp", fs=SR, output="sos"), hat)
    bass_f = 98.0

    def note(L, decay):
        seg = np.arange(L) / SR
        env = np.minimum(seg / 0.004, 1.0) * np.exp(-seg * decay)   # 4 ms attack, like a real bass patch
        return np.sign(np.sin(2 * np.pi * bass_f * seg)) * env
    for b in range(int(seconds / beat)):
        s = int(b * beat * SR)
        y[s:s + kick_len] += kick[: max(0, min(kick_len, n - s))]
        o = int((b + 0.5) * beat * SR)
        y[o:o + len(hat)] += 0.3 * hat[: max(0, min(len(hat), n - o))]
        if pattern == "psy":
            for k in (1, 2, 3):
                s2 = int((b + k / 4) * beat * SR); L = int(beat / 4 * 0.6 * SR)
                y[s2:s2 + L] += 0.3 * note(L, 30)[: max(0, min(L, n - s2))]
        elif pattern == "offbeat":
            s2 = int((b + 0.5) * beat * SR); L = int(beat / 2 * 0.8 * SR)
            y[s2:s2 + L] += 0.3 * note(L, 8)[: max(0, min(L, n - s2))]
    if pattern == "sustained":
        duck = np.clip(((t % beat) / beat) * 3, 0, 1)
        y += 0.25 * np.sign(np.sin(2 * np.pi * bass_f * t)) * duck
    y = signal.sosfilt(signal.butter(2, 10000, "lp", fs=SR, output="sos"), y)
    return (y / np.abs(y).max() * 0.8).astype(np.float32)


def self_test(verbose=True) -> bool:
    res = {p: analyze_window(_synth(p)) for p in ("psy", "offbeat", "sustained")}
    ok = True
    for p, r in res.items():
        if verbose:
            print(f"  {p:10s} bpm {r['bpm']:.2f}  gallop {r['gallop']:.2f}  hat_offbeat {r['hat_offbeat']:.1f}")
        ok &= abs(r["bpm"] - 145) < 1.0
    ok &= res["psy"]["gallop"] > 0.5 > max(res["offbeat"]["gallop"], res["sustained"]["gallop"])
    ok &= all(r["hat_offbeat"] > 2 for r in res.values())
    if verbose:
        print("self-test", "PASSED" if ok else "FAILED")
    return bool(ok)


# --------------------------------------------------------------------------- cli
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio", nargs="?")
    ap.add_argument("--model-scores", help="JSON file {style: score} from your existing model")
    ap.add_argument("--stride", type=int, default=1, help="analyze every Nth 30 s window (2 = twice as fast)")
    ap.add_argument("--bpm-hint", type=float)
    ap.add_argument("--json", help="write full result (with per-window features) here")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        sys.exit(0 if self_test() else 1)
    if not a.audio:
        ap.error("audio file required")
    res = analyze(a.audio, stride=a.stride, bpm_hint=a.bpm_hint,
                  progress=lambda i, n: print(f"\r  window {i}/{n}", end="", file=sys.stderr))
    print(file=sys.stderr)
    ms = json.load(open(a.model_scores)) if a.model_scores else None
    ev = genre_evidence(res["summary"], ms)
    res["genre"] = ev
    s = res["summary"]
    print(json.dumps(dict(label=ev["label"], ranking=[(g, ev["fits"][g]["fit"]) for g in ev["ranking"][:6]],
                          adjusted_model_scores=ev.get("adjusted_model_scores"),
                          key_features={k: s[k] for k in ("bpm", "gallop", "psy_windows", "hat_offbeat", "bass_swell",
                                                          "kick_ms", "breakdown_median_bars", "long_breakdown_share")
                                        if k in s}), indent=2))
    if a.json:
        json.dump(res, open(a.json, "w"))


if __name__ == "__main__":
    main()
