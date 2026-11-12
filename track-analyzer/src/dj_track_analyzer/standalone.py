"""Standalone pieces: long blends in a recorded live mix that work as their own track (for SoundCloud).

Each track's original file is lined up with the recording (speed and position), then every bar through each
change is unmixed into the two originals (non-negative least squares on a band spectrogram). A track with a stem
file is unmixed per stem, so a live stem cut or bass swap doesn't read as a fade: it plays in a bar when any of
its stems is near that stem's own level. A track without one is fit whole (or, as an option, separated by Demucs
over the stretch where it plays). A change qualifies when both tracks stay near their own level (or lower but
steady, see STEADY_DB) for a long run
of bars: 2:30 of run makes a track candidate, 64 bars (with phrase padding) a short edit. These are recommendations with a fixed
bar, never a quota: no qualifying change is a valid answer, and then the closest miss is shown.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path

import numpy as np

SR = 44100
HOP = 2048
NFFT = 4096
FPS = SR / HOP
EDGES = np.geomspace(40, 16000, 97)  # 96 log bands

NEAR_DB = 6.0  # both tracks within this of their solo level ... (null test: a track that isn't playing reads
# within it in 32% of bars; one playing at 0 dB in 100%, at -6 dB in 91%, drums only in 73%)
MIN_SECONDS = 150.0  # ... for this long (the run itself, not phrase padding): long enough to release as a track ...
MIN_BARS = 64  # ... or, as a short edit (a moment from the set), this many bars once widened to whole phrases
GAP = 1  # ... where a run may hold single off bars (a fill, a filter sweep, a misfit)
# A track also counts as near when it sits lower but steady: at STEADY_DB or more, and its level changes by at most
# STEADY dB from bar to bar (median over the bar and 2 on each side, STEADY_READINGS+ of them measured). A track
# played bass-down or filtered holds a level; a track that isn't playing jumps around its -8 dB floor. Null test
# (4058 bars, 3 mixes): runs of 32+ bars for a track that isn't playing stay at 1%; a track at -6 dB gets them in
# 76% of stretches (64% without). GAP 2 would raise the fake runs to 6%.
STEADY_DB = -16.0
STEADY = 1.0
STEADY_READINGS = 4
PHRASE = 16  # a piece is widened to whole phrases of this many bars of the incoming track's grid
CONTEXT = 16  # bars measured after a run ends, so the page's strip shows how it ends
PROBE = 24.0  # seconds of the mix matched against the original at each lineup point
AGREE = 1.0  # lineup points must agree on the original's position to this many seconds ...
AGREE_SHARE = 0.6  # ... this share of them
STEP = 12.0  # seconds between lineup points
SPEED_SPAN = 0.015  # speed searched around played/own BPM (BPMs are rounded)
SPEED_STEPS = 13
SOLO = 20.0  # solo level: the first seconds of the track's judged-on window
BLOCKS = 8  # blocks per bar in the fit: smearing from the stretch averages out
NAMES = ["Live Mashup", "Live Rework", "Live Edit", "Bootleg", "Remix"]
# Solo reworks: one track alone (no other within NEAR_DB) for MIN_SECONDS / MIN_BARS, played changed in at least
# REWORK_SHARE of its bars. Cut-offs from 22.8k measured bars of 11 real mixes and an ear check.
REWORK_SHARE = 0.5
REWORK_EQ = 15.0  # a coarse band (of EQ_BANDS) this far from the others, against the original: EQ'd or filtered
EQ_BANDS = 8
REWORK_STEM = 20.0  # a stem this far from its own level (muted or pushed)...
REWORK_STEM2 = 6.5  # ... or a second stem this far: the stems rebalanced
STEM_HEARD = 0.25  # a stem counts there when its original plays at 25%+ of its usual level (under 10%: 23% of bars read 20 dB+ off by noise)
REWORK_OFF = 1.0  # seconds the mix sits off the original's position: looped, cut or re-arranged
# No "another track faintly over it": the fit reads a track that isn't playing at about -8 dB (a spectral floor),
# so a faint layer can't be told from none.
LEVEL_HEARD = 0.05  # a part counts in a bar's level when its original there is 5%+ of the track's loudest part


def seconds(text: str) -> float:
    return sum(float(v) * 60 ** i for i, v in enumerate(reversed(text.split(":"))))


def clock(t: float) -> str:
    t = max(0, int(round(t)))
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def bands(mono44: np.ndarray) -> np.ndarray:
    """(frames, 96) mean power per log band, hop 2048 at 44.1 kHz."""
    freqs = np.fft.rfftfreq(NFFT, 1 / SR)
    band = np.digitize(freqs, EDGES) - 1
    inside = (band >= 0) & (band < 96)
    counts = np.bincount(band[inside], minlength=96).astype(np.float32)
    win = np.hanning(NFFT).astype(np.float32)
    n = max(0, (len(mono44) - NFFT) // HOP + 1)
    out = np.zeros((n, 96), np.float32)
    for i in range(0, n, 2000):
        starts = np.arange(i, min(n, i + 2000)) * HOP
        frames = np.asarray(mono44, np.float32)[starts[:, None] + np.arange(NFFT)] * win
        power = np.abs(np.fft.rfft(frames, axis=1)) ** 2
        summed = np.zeros((len(starts), 96), np.float32)
        np.add.at(summed.T, band[inside], power[:, inside].T)
        out[i:i + len(starts)] = summed / np.maximum(counts, 1)
    return out


def _stretch(O: np.ndarray, speed: float) -> np.ndarray:
    """An original played at `speed`, as frames at the mix's rate (nearest frame)."""
    idx = np.round(np.arange(0, len(O) / speed) * speed).astype(int)
    return O[np.minimum(idx, len(O) - 1)]


class Mix:
    def __init__(self, mono44: np.ndarray):
        M = bands(mono44)
        self.keep = M.mean(0) > 0
        self.M = M[:, self.keep]
        self.W = 1 / self.M.mean(0)  # whitened: every band counts, not just the bass

    def logz(self, X: np.ndarray) -> np.ndarray:
        L = np.log(X * self.W + 1e-9)
        return L - L.mean(1, keepdims=True)  # spectral shape per frame, level removed

    def locate(self, S: np.ndarray, points: list[float]) -> list[tuple[float, float]]:
        """[(offset s, match 0-1)] per point: where mix seconds t..t+PROBE sit in the stretched original S, as
        original time minus mix time (log-spectrum shape cross-correlation, summed over bands)."""
        o = self.logz(S)
        L = int(PROBE * FPS)
        if len(o) < L:
            return [(0.0, 0.0) for _ in points]
        n = 1 << int(np.ceil(np.log2(len(o) + L)))
        Fo = np.fft.rfft(o, n, axis=0)  # once per speed, not once per point
        energy = np.sqrt(np.maximum(np.convolve((o ** 2).sum(1), np.ones(L), "valid"), 0))
        out = []
        for t in points:
            a = int(t * FPS)
            q = self.logz(self.M[a:a + L])
            if len(q) < L:
                out.append((0.0, 0.0))
                continue
            q = q - q.mean(0)
            num = np.fft.irfft((Fo * np.fft.rfft(q[::-1], n, axis=0)).sum(1), n)[L - 1:len(o)]
            num = num / (np.linalg.norm(q) * energy + 1e-9)
            i = int(np.argmax(num))
            out.append((i / FPS - t, float(num[i])))
        return out

    def line_up(self, O: np.ndarray, own_bpm: float, played_bpm: float, window: tuple[float, float]) -> dict:
        """Search the speed; position checked every STEP seconds through the judged-on window. Lined up when
        most points agree on the position and the agreeing ones span half the window (that checks the speed).
        Points may disagree where the DJ looped, cut or blended near the window's edges."""
        a, b = window
        points = list(np.arange(a, max(a + 1, b - PROBE), STEP))
        best = None
        for s in played_bpm / own_bpm * np.linspace(1 - SPEED_SPAN, 1 + SPEED_SPAN, SPEED_STEPS):
            S = _stretch(O, s)
            found = self.locate(S, points)
            offs = np.array([f[0] for f in found])
            med = float(np.median(offs))
            good = np.abs(offs - med) < AGREE
            span = float(np.ptp(np.array(points)[good])) if good.any() else 0.0
            score = (good.mean(), float(np.mean([f[1] for f, g in zip(found, good) if g] or [0])))
            if best is None or score > best["score"]:
                best = {"score": score, "speed": float(s), "offset": float(np.median(offs[good])) if good.any() else med,
                        "agree": float(good.mean()), "span": span, "S": S}
        need_span = min(0.5 * (b - a - PROBE), 60.0)
        best["need_span"] = need_span
        best["ok"] = best["agree"] >= AGREE_SHARE and best["span"] >= need_span
        return best

    def _frames(self, S: np.ndarray, off: float, x0: float, x1: float) -> np.ndarray:
        idx = np.arange(int(x0 * FPS), int(x1 * FPS)) + int(round(off * FPS))
        out = np.zeros((len(idx), S.shape[1]), np.float32)
        good = (idx >= 0) & (idx < len(S))
        out[good] = S[idx[good]]
        return out

    def gains(self, x0: float, x1: float, parts: list[tuple[np.ndarray, float]]) -> np.ndarray:
        """NNLS gains of the lined-up originals that best rebuild the mix over x0..x1 (blocks of a bar)."""
        from scipy.optimize import nnls

        m = self.M[int(x0 * FPS):int(x1 * FPS)]
        n = len(m)
        k = max(1, n // BLOCKS)

        def blk(X):
            return X[: n // k * k].reshape(n // k, k, -1).mean(1) * self.W

        A = np.stack([blk(self._frames(S, off, x0, x1)[:n]).ravel() for S, off in parts], 1)
        w, _ = nnls(A.astype(np.float64), blk(m).ravel().astype(np.float64))
        return w


def _db(g: float, ref: float) -> float:
    return 10 * np.log10(max(g, 1e-9) / ref)


def _longest(flags: list[bool], gap: int = GAP) -> tuple[int, int]:
    """(start index, length) of the longest run of True that may hold up to `gap` False in a row inside it;
    a run starts and ends on True."""
    best = (0, 0)
    start = last = None
    for i, f in enumerate(flags):
        if f:
            if start is None or i - last - 1 > gap:
                start = i
            last = i
            if last - start + 1 > best[1]:
                best = (start, last - start + 1)
    return best


def _near(col: list[float | None]) -> list[bool]:
    """Per bar of one track's levels (dB vs its own level, None: no part sounds): near its own level, or lower
    but steady (see STEADY_DB)."""
    out = []
    for i, v in enumerate(col):
        ok = v is not None and abs(v) <= NEAR_DB
        if not ok and v is not None and v >= STEADY_DB:
            w = [x for x in col[max(0, i - 2):i + 3] if x is not None]
            ok = len(w) >= STEADY_READINGS and float(np.median(np.abs(np.diff(w)))) <= STEADY
        out.append(ok)
    return out


def _near_bars(levels: list[tuple]) -> list[list[bool]]:
    """_near for each track's column of `levels` rows (mix seconds, dB, dB, ...): [track][bar]."""
    return [_near([r[1 + q] for r in levels]) for q in range(len(levels[0]) - 1)] if levels else []


def _plain(title: str) -> str:
    """'Desire (Deviu, MARTMAD Remix)' -> 'Desire': the mashup title keeps the bare song name."""
    return re.sub(r"\s*[(\[][^)\]]*[)\]]\s*$", "", title or "").strip() or (title or "?").strip()


def _credit(t: dict) -> str:
    return f"{(t.get('artist') or '?').strip()} - {(t.get('title') or '?').strip()}"


def _date(report: dict) -> str:
    try:
        d = datetime.strptime(report["recording_start"], "%Y-%m-%d %H:%M:%S")
        return f"{d.day} {d:%b %Y}"
    except (KeyError, ValueError):
        return ""


def piece(A: dict, B: dict, run: dict, report: dict, name: str = NAMES[0], link: str = "<link>",
          more: list[dict] | None = None) -> dict:
    """Title, one-line Why and credits for one change (more: other tracks up in the run, in set order)."""
    ts = sorted([A, B] + list(more or []), key=lambda t: t.get("n", 0))
    title = " vs ".join((t.get('artist') or '?').strip() for t in ts) + " – " + \
        " x ".join(_plain(t.get('title')) for t in ts) + f" ({name})"
    why = f"{run['bars']} bars ({run['from']}–{run['to']}) with " + \
        ("both tracks" if len(ts) == 2 else "2 or more tracks at a time") + " near full level"
    if B.get("transition") == "clash":
        why += f"; keys clash ({A.get('heard_camelot') or A.get('camelot')} into " \
               f"{B.get('heard_camelot') or B.get('camelot')})"
    elif B.get("transition"):
        why += f"; keys: {B['transition']}"
    when = _date(report)
    credits = "\n".join(["Originals:", *(f"• {_credit(t)}" for t in ts), "",
                         f"Recorded live{' ' + when if when else ''} · full set: {link}"])
    return {"title": title, "why": why, "credits": credits}


def decode_stems(f: Path) -> list[np.ndarray] | None:
    """The 4 stems of a stem file (streams 1-4: drums, bass, other, vocals) as mono 44.1 kHz, read in one pass."""
    from .analysis import pyav

    av = pyav()
    with av.open(str(f), metadata_errors="replace") as c:
        ss = list(c.streams.audio)[1:5]
        if len(ss) < 4:
            return None
        ids = [s.index for s in ss]  # read while the file is open: a closed stream's fields are gone
        rs = {i: av.AudioResampler(format="flt", layout="mono", rate=SR) for i in ids}
        out: dict[int, list] = {i: [] for i in ids}
        for pk in c.demux(*ss):
            i = pk.stream.index
            for fr in pk.decode():
                out[i] += [o.to_ndarray().ravel() for o in rs[i].resample(fr)]
        for i, r in rs.items():
            out[i] += [o.to_ndarray().ravel() for o in r.resample(None)]
    return [np.concatenate(out[i]) if out[i] else np.zeros(0, np.float32) for i in ids]


def separate(path: Path, t0: float, t1: float) -> list[np.ndarray]:
    """Demucs (htdemucs) stems of the original over its seconds t0..t1, mono 44.1 kHz from t0 (4 s of context
    each side, as the loop parts use)."""
    import julius
    import torch
    from demucs.apply import apply_model

    from .analysis import decode
    from .cues import _separator

    model, dev, lock = _separator()
    stereo, sr, _nch = decode(Path(path))
    x = np.ascontiguousarray(np.asarray(stereo, np.float32).T)
    a0, b0 = max(0.0, t0 - 4), min(x.shape[1] / sr, t1 + 4)
    seg = torch.from_numpy(x[:, int(a0 * sr):int(b0 * sr)].copy())
    if sr != model.samplerate:
        seg = julius.resample_frac(seg, int(sr), model.samplerate)
    ref = seg.mean(0)
    mu, sd = ref.mean(), ref.std() + 1e-8
    with lock, torch.no_grad():
        st = apply_model(model, ((seg - mu) / sd)[None].to(dev), device=dev, shifts=1, split=True,
                         overlap=0.25)[0].cpu() * sd + mu
    st = st.mean(1)  # (stems, samples), mono
    if model.samplerate != SR:
        st = julius.resample_frac(st, model.samplerate, SR)
    k0 = int((max(0.0, t0) - a0) * SR)
    return [st[j, k0:k0 + int((t1 - max(0.0, t0)) * SR)].numpy() for j in range(st.shape[0])]


HOW = {"stems": "its stem file", "separated": "stems separated here", "whole": "the whole track (no stem file)"}


def find(result: dict, mono44: np.ndarray, load=None, load_stems=None, split=None, separate_missing: bool = False,
         grid=None, say=print, workers: int | None = None, multi: bool = True,
         solo: bool = True) -> dict | None:
    """Standalone pieces of a mix whose tracklist has been checked (`result["tracklist"]`).
    load(path) -> mono 44.1 kHz audio of an original (default: decoded like any track).
    load_stems(path) -> its 4 stems from a stem file, or None (default: the Traktor/NUO stem file, by tags).
    split(path, t0, t1) -> 4 stems of the original's seconds t0..t1 (default: Demucs), used only for a track
    without a stem file and only when separate_missing is on.
    grid(path) -> (bpm, first downbeat ms) of the original's beat grid in the DJ app, or None (default: the DJ app's
    grids). The bars are counted on the incoming track's grid, so a piece starts and ends on its downbeats.
    multi: also unmix the track before the outgoing one and the one after the incoming one, so a run of 3+
    tracks is seen; a bar counts when any 2+ tracks are near their own level.
    solo: also look for solo reworks, one track alone and played changed (EQ, stems or re-arranged)."""
    report = result.get("tracklist")
    if not report or len(report.get("tracks", [])) < 2:
        return None
    if load is None:
        from .analysis import decode, mono_mix, resample_medium

        def load(path):
            stereo, sr, nch = decode(Path(path))
            mono = mono_mix(stereo, nch)
            return mono if sr == SR else resample_medium(mono, sr, SR)
    if load_stems is None:
        def load_stems(path):
            from .gridfix import find_stem

            f = find_stem(Path(path))
            return decode_stems(f) if f else None
    if split is None:
        split = separate
    if grid is None:
        grids: dict = {}

        def grid(path):
            if not grids:
                try:
                    from .cues import grids as read

                    grids.update(read() or {"": None})
                except (Exception, SystemExit) as err:  # no DJ app collection: bars counted from the change instead
                    say(f"standalone pieces: no beat grids ({err}); bars won't sit on the downbeats")
                    grids[""] = None
            d = grids.get(str(path))
            return (d["bpm"], d["grid_ms"]) if d and d.get("grid_ms") is not None and d.get("bpm") else None
    if separate_missing:
        from .cues import parts_available

        separate_missing = split is not separate or parts_available()

    tracks = report["tracks"]
    say(f"standalone pieces: reading the mix, then lining up {len(tracks)} original files")
    mix = Mix(mono44)
    placed: dict[int, dict] = {}

    # The originals and their stem files are read and turned into bands in parallel threads (decoding and the FFTs
    # release the GIL); the same functions on the same input, so the result is the same as reading them in turn.
    def _orig(path):
        return bands(load(path))[:, mix.keep]

    def _stems(path):
        stems = load_stems(path)
        return None if stems is None else [bands(x)[:, mix.keep] for x in stems]

    from concurrent.futures import ThreadPoolExecutor

    readers = ThreadPoolExecutor(max(1, min(5, (os.cpu_count() or 4) // 2)))  # as AudioPool / `scan`
    ahead: dict[int, tuple] = {}
    for i, t in enumerate(tracks):
        if t.get("path") and Path(t["path"]).exists() and t.get("bpm") and t.get("judged_on"):
            ahead[i] = (readers.submit(_orig, t["path"]), readers.submit(_stems, t["path"]))

    def bar(t: dict) -> float:
        return 240 / (t.get("played_bpm") or 140)

    def win(t: dict) -> tuple[float, float]:
        return tuple(map(seconds, t["judged_on"].split("-")))

    def span(i: int) -> tuple[float, float]:
        """Mix seconds where track i is measured: its own window and the changes into and out of it."""
        t = tracks[i]
        lo, hi = win(t)
        if i > 0 and tracks[i - 1].get("judged_on"):
            lo = min(lo, win(tracks[i - 1])[0] + SOLO)
        if i + 1 < len(tracks) and tracks[i + 1].get("judged_on"):
            hi = max(hi, win(tracks[i + 1])[1])  # a blend may run on into the next track's window
        return lo, hi

    def reference(p: dict, t: dict) -> list[float]:
        """Each part's own level: its median gain over the track's judged-on window, fit with only this track,
        counting the bars where that part has sound."""
        w0, w1 = win(t)
        L = bar(t)
        G, Lv = [], []
        for x in np.arange(w0, w1 - L, L):
            g, lv = fit(x, x + L, [p])
            G.append(g)
            Lv.append(lv)
        G, Lv = np.array(G), np.array(Lv)
        ref = []
        for j in range(len(p["parts"])):
            heard = Lv[:, j][Lv[:, j] > 0] if len(Lv) else Lv
            on = Lv[:, j] > 0.05 * np.median(heard) if len(heard) else np.zeros(len(Lv), bool)
            ref.append(float(np.median(G[on, j])) if on.any() else 0.0)
        # how loud each part's original usually is: a part far under that plays only a tail (a breakdown)
        p["usual"] = [float(np.median(Lv[:, j][Lv[:, j] > 0])) if len(Lv) and (Lv[:, j] > 0).any() else 0.0
                      for j in range(len(p["parts"]))]
        return ref

    import threading

    locks = {t["n"]: threading.Lock() for t in tracks}
    split_lock = threading.Lock()  # Demucs already uses every core: one separation at a time

    def place(i: int) -> dict:
        """Track i lined up, once: two changes share each track, the second waits for the first."""
        with locks[tracks[i]["n"]]:
            if tracks[i]["n"] not in placed:
                placed[tracks[i]["n"]] = _place(i)
            return placed[tracks[i]["n"]]

    def _place(i: int) -> dict:
        t = tracks[i]
        p = {"ok": False}
        path = t.get("path")
        t0 = time.monotonic()
        name = f"track {i + 1}/{len(tracks)} {_plain(t.get('title'))}"
        say(f"standalone pieces: {name}: reading and lining up the original")
        if not path or not Path(path).exists():
            p["why"] = "file not found"
        elif not t.get("bpm") or not t.get("judged_on"):
            p["why"] = "no BPM or judged-on window"
        else:
            try:
                O = ahead[i][0].result()
            except Exception as err:  # an unreadable original only costs that change
                O, p["why"] = None, f"couldn't read the file ({err})"
            if O is not None:
                window = win(t)
                p = mix.line_up(O, t["bpm"], t.get("played_bpm") or t["bpm"], window)
                if not p["ok"]:
                    p["why"] = (f"the file doesn't match what was played (only {p['agree']:.0%} of lineup points agree)"
                                if p["agree"] < AGREE_SHARE else
                                f"couldn't confirm the speed (the agreeing lineup points cover {p['span']:.0f} s, "
                                f"{p['need_span']:.0f} s needed)")
                else:
                    p["solo"] = mix.gains(window[0], window[0] + SOLO, [(p["S"], p["offset"])])[0]
                    if p["solo"] <= 0:
                        p.update(ok=False, why="no solo level")
            if p["ok"]:
                p["how"], p["parts"] = "whole", [p["S"]]
                stems = None
                try:
                    stems = ahead[i][1].result()  # bands of each stem, not yet stretched
                    how = "stems"
                    if stems is None and separate_missing:
                        lo, hi = span(i)
                        o0, o1 = max(0.0, (lo + p["offset"]) * p["speed"]), (hi + p["offset"]) * p["speed"]
                        say(f"standalone pieces: {name}: no stem file, separating {clock(o1 - o0)} of it (slow)")
                        with split_lock:
                            cut = split(path, o0, o1)
                        stems = [bands(np.concatenate([np.zeros(int(o0 * SR), np.float32), np.asarray(x, np.float32)]))
                                 [:, mix.keep] for x in cut]
                        how = "separated"
                except Exception as err:  # no stems: the whole-track fit still measures it
                    say(f"standalone pieces: {name}: no stems ({err})")
                    stems = None
                if stems:
                    say(f"standalone pieces: {name}: measuring the own level of each of its {len(stems)} stems")
                    p["parts"] = [_stretch(x, p["speed"]) for x in stems]
                    p["how"] = how
                    p["ref"] = reference(p, t)
                    if not any(r > 0 for r in p["ref"]):
                        p["how"], p["parts"] = "whole", [p["S"]]
                if p["how"] == "whole":
                    p["ref"] = [p["solo"]]
        took = f"{time.monotonic() - t0:.0f} s"
        if p["ok"]:
            say(f"standalone pieces: {name}: lined up at {p['speed']:.3f}x, {p['agree']:.0%} of points agree, "
                f"measured with {HOW[p['how']]} ({took})")
        else:
            say(f"standalone pieces: {name}: skipped, {p['why']} ({took})")
        return p

    def fit(x0: float, x1: float, ps: list[dict]) -> tuple[np.ndarray, list[float]]:
        """NNLS gains of every part of the given tracks over x0..x1, and each part's own level there."""
        from scipy.optimize import nnls

        m = mix.M[int(x0 * FPS):int(x1 * FPS)]
        n = len(m)
        k = max(1, n // BLOCKS)

        def blk(X):
            return X[: n // k * k].reshape(n // k, k, -1).mean(1) * mix.W

        cols, live = [], []
        for p in ps:
            for S in p["parts"]:
                X = mix._frames(S, p["offset"], x0, x1)[:n]
                cols.append(blk(X).ravel())
                live.append(float((X * mix.W).sum(1).mean()) if n else 0.0)
        w, _ = nnls(np.stack(cols, 1).astype(np.float64), blk(m).ravel().astype(np.float64))
        return w, live

    def level(p: dict, g: np.ndarray, lv: list[float]) -> float | None:
        """A track's level in one bar, in dB against its own: the gain of the parts that sound in the original
        there (LEVEL_HEARD of the track's loudest part) and have a level of their own, weighted by how loud each
        is: sum(g * lv) / sum(ref * lv). A cut stem lowers it by its share of the sound, not to silence."""
        top = max(lv)
        if top <= 0:  # its original has no sound here (not started, or over)
            return None
        on = [(gj, r, l) for gj, r, l in zip(g, p["ref"], lv) if r > 0 and (len(lv) == 1 or l >= LEVEL_HEARD * top)]
        if not on:
            return None
        return round(_db(sum(gj * l for gj, _, l in on), sum(r * l for _, r, l in on)), 1)

    def change(i: int) -> dict:
        t_start = time.monotonic()
        A, B = tracks[i], tracks[i + 1]
        c = {"from": A["n"], "to": B["n"], "at": B["start"]}
        pa, pb = place(i), place(i + 1)
        extra = [j for j in ((i - 1, i + 2) if multi else ()) if 0 <= j < len(tracks)]
        if not (pa["ok"] and pb["ok"]):
            bad = [(t, p) for t, p in ((A, pa), (B, pb)) if not p["ok"]]
            c["status"] = "couldn't measure: " + "; ".join(f"{_plain(t.get('title'))}: {p['why']}" for t, p in bad)
            return c
        extra = [(j, p) for j in extra for p in [place(j)] if p["ok"]]
        c["others"] = [tracks[j]["n"] for j, _ in extra]  # bars rows carry their levels after A and B
        lo, hi = win(A)[0] + SOLO, win(B)[0] + SOLO
        # measured on until B's window ends, as long as the run lasts (then CONTEXT bars more); once A's original
        # has run out it counts as silent, so the page's strip shows the run end instead of stopping at it
        a_end = len(pa["S"]) / FPS - pa["offset"]
        # no cap at B's window end: a run still going there was cut off at the strip's edge
        limit = min(len(pb["S"]) / FPS - pb["offset"], len(mix.M) / FPS)
        L = 240 / (B.get("played_bpm") or A.get("played_bpm") or 140)
        gb, phase = grid(B["path"]), None
        if gb:  # bars on B's downbeats: original seconds o play at mix seconds o / speed - offset
            L = 240 / (gb[0] * pb["speed"])
            phase = gb[1] / 1000 / pb["speed"] - pb["offset"]
            lo = phase + np.ceil((lo - phase) / L) * L
        say(f"standalone pieces: change {i + 1}/{len(tracks) - 1} at {B['start']}: measuring "
            f"{max(0, int((hi - lo) / L))} bars"
            f"{'' if gb else ' (no beat grid for ' + _plain(B.get('title')) + ')'}")
        levels, near, after = [], [], 0

        def is_near(*dbs):  # while measuring, to know when the run is over: the plain test (steadiness needs bars ahead)
            return sum(d is not None and abs(d) <= NEAR_DB for d in dbs) >= 2

        x = lo
        while x + L <= limit:
            if x + L > hi:  # past the usual end: on while the run lasts, then CONTEXT bars more
                alive = any(near[-(GAP + 1):]) and after == 0
                after = 0 if alive else after + 1
                if after > CONTEXT:
                    break
            ps = ([] if x + L > a_end else [pa]) + [pb] + [p for _, p in extra]  # A over: counts as silent
            g, lv = fit(x, x + L, ps)
            row, k = [], 0
            for p in ps:
                m = len(p["parts"])
                row.append(level(p, g[k:k + m], lv[k:k + m]))
                k += m
            if x + L > a_end:
                row.insert(0, None)
            levels.append((round(float(x), 2), *row))
            near.append(is_near(*levels[-1][1:]))
            x += L
        c["bars"] = levels  # [mix seconds, A dB, B dB, dB of each of c["others"]] vs each track's own level (None: no part sounds)
        c["bar"] = round(L, 4)  # bar length in mix seconds
        c["measured_with"] = {"from": pa["how"], "to": pb["how"]}
        per = _near_bars(levels)  # [track][bar]: near or lower but steady
        near = [sum(col[j] for col in per) >= 2 for j in range(len(levels))]
        i0, n = _longest(near)
        c["status"] = "measured"
        t0 = levels[i0][0] if n else 0.0
        c["run"] = {"bars": n, "from": clock(t0) if n else "", "to": clock(t0 + n * L) if n else ""}
        if n and gb:  # widened to whole phrases of B: bars added before and after (the page's −/+ start there)
            k = int(round((t0 - phase) / L))
            c["run"]["lead"], c["run"]["tail"] = k % PHRASE, -(k + n) % PHRASE
        # long enough as a piece once widened to whole phrases (64 bars: about 1:46 at 145 BPM)
        c["qualifies"] = n * L >= MIN_SECONDS  # a track of its own
        c["short"] = not c["qualifies"] and n + c["run"].get("lead", 0) + c["run"].get("tail", 0) >= MIN_BARS
        if n:  # a track is named when it is up in at least a quarter of the run
            who = [A, B] + [tracks[j] for j, _ in extra]
            up = [t for q, t in enumerate(who) if sum(per[q][i0:i0 + n]) >= n / 4]
            c["with"] = [t["n"] for t in who[2:] if t in up]
            c["names"] = [t["n"] for t in sorted(up if len(up) >= 2 else who[:2], key=lambda t: t["n"])]
            ts = [t for t in tracks if t["n"] in c["names"]]
            c.update(piece(ts[0], ts[1], c["run"], report, more=ts[2:]))
            for how, words in (("whole", "measured without stems"), ("separated", "stems separated here")):
                ts = [_plain(t.get("title")) for t, p in ((A, pa), (B, pb)) if p["how"] == how]
                if ts:
                    c["why"] += f"; {words}: " + ", ".join(ts)
        say(f"standalone pieces: change {i + 1}/{len(tracks) - 1} at {B['start']}: done "
            f"({time.monotonic() - t_start:.0f} s)")
        return c

    def changed(p: dict, x0: float, x1: float, g: np.ndarray, lv: list[float]) -> list[str]:
        """Why track p is played changed in bar x0..x1 (empty: as the original). g, lv: its parts' fit."""
        why = []
        Mb = mix.M[int(x0 * FPS):int(x1 * FPS)]
        Ob = mix._frames(p["S"], p["offset"], x0, x1)[:len(Mb)]
        if len(Mb) and Ob.sum() > 0:
            gb = []
            for gr in np.array_split(np.arange(Mb.shape[1]), EQ_BANDS):
                o = Ob[:, gr].sum()
                gb.append(10 * np.log10(max(Mb[:, gr].sum(), 1e-12) / o) if o > 0 else np.nan)
            gb = np.array(gb)
            if np.isfinite(gb).sum() >= EQ_BANDS * 3 // 4 and np.nanmax(np.abs(gb - np.nanmedian(gb))) > REWORK_EQ:
                why.append("EQ")
        if p["how"] != "whole":
            top = max(max(lv), 1e-12)
            st = sorted((abs(_db(gj, r)) for gj, r, l, u in zip(g, p["ref"], lv, p["usual"])
                         if r > 0 and l >= 0.05 * top and l >= STEM_HEARD * u), reverse=True)
            if st and (st[0] > REWORK_STEM or (len(st) > 1 and st[1] > REWORK_STEM2)):
                why.append("stems")
        return why

    def solo_run(i: int) -> dict | None:
        """Track i alone (no other track within NEAR_DB) through its stretch of the mix, and how much of that is
        played changed: a solo rework when it is long enough and changed in REWORK_SHARE of its bars."""
        t, p = tracks[i], placed.get(tracks[i]["n"])
        if not p or not p["ok"]:
            return None
        nb = [placed[tracks[j]["n"]] for j in (i - 1, i + 1) if 0 <= j < len(tracks)
              and placed.get(tracks[j]["n"], {}).get("ok")]
        a = seconds(t["start"])
        b = seconds(tracks[i + 1]["start"]) if i + 1 < len(tracks) else len(mix.M) / FPS
        b = min(b, len(p["S"]) / FPS - p["offset"], len(mix.M) / FPS)
        L, phase = bar(t), None
        gt = grid(t["path"])
        if gt:
            L = 240 / (gt[0] * p["speed"])
            phase = gt[1] / 1000 / p["speed"] - p["offset"]
            a = phase + np.ceil((a - phase) / L) * L
        starts = list(np.arange(a, b - L, L))
        if not starts:
            return None
        pos = mix.locate(p["S"], [max(0.0, x - PROBE / 2 + L / 2) for x in starts])
        rows, alone, why = [], [], []
        for x, (off, _) in zip(starts, pos):
            ps = [p] + nb
            g, lv = fit(x, x + L, ps)
            m = len(p["parts"])
            own, others, k = level(p, g[:m], lv[:m]), [], m
            for q in nb:
                others.append(level(q, g[k:k + len(q["parts"])], lv[k:k + len(q["parts"])]))
                k += len(q["parts"])
            ok = own is not None and abs(own) <= NEAR_DB and all(o is None or o < -NEAR_DB for o in others)
            w = changed(p, x, x + L, g[:m], lv[:m]) if ok else []
            if ok and abs(off - p["offset"]) >= REWORK_OFF:
                w.append("re-arranged")
            rows.append((round(float(x), 2), own, *others))
            alone.append(ok)
            why.append(w)
        i0, n = _longest(alone)
        if not n:
            return None
        hit = [w for w, ok in zip(why[i0:i0 + n], alone[i0:i0 + n]) if ok and w]
        t0 = rows[i0][0]
        run = {"bars": n, "from": clock(t0), "to": clock(t0 + n * L)}
        if gt:
            k0 = int(round((t0 - phase) / L))
            run["lead"], run["tail"] = k0 % PHRASE, -(k0 + n) % PHRASE
        share = len(hit) / n
        c = {"at": f"{t['start']} solo", "solo": t["n"], "from": t["n"], "to": t["n"], "status": "measured",
             "others": [tracks[j]["n"] for j in (i - 1, i + 1) if 0 <= j < len(tracks)
                        and placed.get(tracks[j]["n"], {}).get("ok")],
             "bars": rows, "bar": round(L, 4), "run": run, "reworked": round(share, 2),
             "reworked_bars": [rows[i0 + q][0] for q in range(n) if alone[i0 + q] and why[i0 + q]]}
        c["qualifies"] = share >= REWORK_SHARE and n * L >= MIN_SECONDS
        c["short"] = share >= REWORK_SHARE and not c["qualifies"] and n + run.get("lead", 0) + run.get("tail", 0) >= MIN_BARS
        kinds = sorted({k for w in hit for k in w})
        name = "Live Rework"
        c["title"] = f"{(t.get('artist') or '?').strip()} – {_plain(t.get('title'))} ({name})"
        c["why"] = (f"{n} bars ({run['from']}–{run['to']}) with {_plain(t.get('title'))} alone, played changed in "
                    f"{share:.0%} of them" + (f" ({', '.join(kinds)})" if kinds else ""))
        when = _date(report)
        c["credits"] = "\n".join(["Original:", f"• {_credit(t)}", "",
                                  f"Recorded live{' ' + when if when else ''} · full set: <link>"])
        return c

    from concurrent.futures import ThreadPoolExecutor as Pool

    pool = Pool(max(1, workers or min(5, (os.cpu_count() or 4) // 2)))
    try:
        futures = [pool.submit(change, i) for i in range(len(tracks) - 1)]
        changes = [f.result() for f in futures]  # in order; an error or a stop re-raises here
        solos = [c for c in pool.map(solo_run, range(len(tracks))) if c] if solo else []
    finally:  # an error or a stop from the page: drop the reads and changes not started yet
        pool.shutdown(wait=False, cancel_futures=True)
        readers.shutdown(wait=False, cancel_futures=True)

    measured = [c for c in changes if c["status"] == "measured"]
    picks = [c for c in measured if c["qualifies"]]
    shorts = [c for c in measured if c.get("short")]
    out = {"multi": multi, "rule": ("2 or more of the tracks around a change" if multi else "both tracks") + f" within {NEAR_DB:g} dB of their own level (with a stem file: its stems that play there, by loudness) "
                   f"or lower down to {STEADY_DB:g} dB but steady (bar to bar within {STEADY:g} dB) "
                   f"for {clock(MIN_SECONDS)}+ (short edits: {MIN_BARS}+ bars in whole phrases)"
                   + (", single off bars allowed" if GAP == 1 else f", up to {GAP} off bars in a row allowed" if GAP else ""),
           "near_db": NEAR_DB, "steady_db": STEADY_DB, "steady": STEADY, "min_bars": MIN_BARS, "min_seconds": MIN_SECONDS, "gap": GAP,  # the page words the rule from these
           "names": NAMES, "changes": changes, "picks": [c["at"] for c in picks],
           "shorts": [c["at"] for c in shorts],
           "solo": solo, "solos": solos, "rework_share": REWORK_SHARE,
           "solo_picks": [c["at"] for c in solos if c["qualifies"]], "solo_shorts": [c["at"] for c in solos if c["short"]],
           "measured_with": {str(t["n"]): placed[t["n"]]["how"] for t in tracks if placed.get(t["n"], {}).get("ok")}}
    say(f"standalone pieces: done, {len(picks)} of {len(measured)} measured changes qualify as tracks, "
        f"{len(shorts)} as short edits"
        + (f" ({len(changes) - len(measured)} couldn't be measured)" if len(changes) > len(measured) else "")
        + (f"; {len(out['solo_picks'])} solo reworks, {len(out['solo_shorts'])} short" if solo else ""))
    if not picks and not shorts and measured:
        miss = max(measured, key=lambda c: c["run"]["bars"])
        if miss["run"]["bars"]:
            out["closest_miss"] = miss["at"]
    return out


def summary_lines(result: dict, chapters: list[str] | None = None) -> list[str]:
    sa = result.get("standalone")
    if not sa:
        return []
    by_at = {c["at"]: c for c in sa["changes"]}
    names = {t["n"]: t for t in result["tracklist"]["tracks"]}
    lines = ["", f"  Standalone pieces ({sa['rule']})"]
    if not sa["picks"] and not sa.get("shorts") and not sa.get("solo_picks") and not sa.get("solo_shorts"):
        lines.append("    Nothing qualifies.")
        if sa.get("closest_miss"):
            c = by_at[sa["closest_miss"]]
            lines.append(f"    Closest miss: the change at {c['at']}, {c['why']}")
    for at in sa["picks"] + sa.get("shorts", []):
        c = by_at[at]
        short = " (short edit)" if at in sa.get("shorts", []) else ""
        lines += ["", f"    {c['title']}{short}", f"      Why: {c['why']}",
                  "      Name: " + " / ".join(sa["names"]) + " (your call)", "      Credits:"]
        lines += [f"        {x}" for x in c["credits"].splitlines()]
    by_solo = {c["at"]: c for c in sa.get("solos", [])}
    for at in sa.get("solo_picks", []) + sa.get("solo_shorts", []):
        c = by_solo[at]
        short = " (short edit)" if at in sa.get("solo_shorts", []) else ""
        lines += ["", f"    {c['title']} (solo rework){short}", f"      Why: {c['why']}", "      Credits:"]
        lines += [f"        {x}" for x in c["credits"].splitlines()]
    without = [names[int(n)].get("title", "") for n, how in sa.get("measured_with", {}).items() if how == "whole"]
    if without and len(without) < len(sa.get("measured_with", {})):
        lines += ["", "    Measured without stems (no stem file): " + ", ".join(without)]
    skipped = [c for c in sa["changes"] if c["status"] != "measured"]
    if skipped:
        lines.append("")
        lines += [f"    {c['at']} {names[c['to']].get('title', '')}: {c['status']}" for c in skipped]
    if (sa["picks"] or sa.get("shorts")) and chapters:
        lines += ["", "  SoundCloud timestamps (for the full set)"] + [f"    {x}" for x in chapters]
    return lines
