"""Check the Rust module, PyAV decoding, the EffNet input and the models on Python TensorFlow against Essentia.
Run after any change to dsp/, decode or the model graphs:

    uv run python dsp/check.py                 # test signals: resampling (sinc, linear) at 8-192 kHz, loudness, mono mix,
                                               # decoding test files in each format (made with the ffmpeg command),
                                               # the models' mel frames, EffNet's patches and batches,
                                               # every model graph on Python TensorFlow (uv sync --inexact --extra nets),
                                               # the key (KeyExtractor edma; mixes: every 2-minute segment too),
                                               # the BPM (RhythmExtractor2013 multifeature; the same segments)
    uv run python dsp/check.py --collection    # also every scanned track and every file given with --files
    uv run python dsp/check.py --files-from mixes.txt   # files listed one per line (names with spaces are fine)
    uv run python dsp/check.py --collection --steps linear   # only the step you changed: much faster

Each step is compared bit for bit, except where ours computes libm calls more exactly than Essentia's macOS build (the
mel's log10, the key's and the BPM's: math.rs): those may differ by a float step there, so they pass within a
tolerance (TOL; the BPM within the user's gate) and are counted as "close". Prints one line per difference and exits 1
if there is any. A Rust step only replaces Essentia's while this passes on the whole test collection; run every step (the default) before a release. Most of the time goes
into Essentia's own slow steps (about a minute per hour-long mix), so check only what changed while working.
Tracks run --workers at a time; files over 12 minutes (mixes, ~1.3 GB each in memory) --mix-workers. With nets each
process holds every model twice (~5 GB), so fewer run at once. Compare on a Mac: Essentia's arm64 build fuses multiply-adds that its
Linux x86_64 build doesn't, and the Rust loudness follows the Mac's (docs/WINDOWS.md).
"""

import argparse
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

import numpy as np

# Within these, a step whose libm calls are more exact than Essentia's passes (it differs by up to ~3e-7 on test
# signals): the mels in float steps, EffNet's embeddings and the key's strength in absolute value.
TOL = {"mel": 1, "effnet": 1e-5, "key": 1e-5}
STEPS = ("loudness", "mono", "resample", "linear", "decode", "mel", "effnet", "nets", "key", "bpm")
# Test files for decoding: (name, ffmpeg output options, rate, channels). AAC, Vorbis and Opus come out of
# current FFmpeg's rewritten decoders about 1 in 10 million different (stage 0): printed, not counted.
CODECS = [(f"{c}.wav", ["-c:a", c], sr, 2) for c in ("pcm_u8", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_f64le")
          for sr in (44100, 48000)] + [
    ("s16.aiff", ["-c:a", "pcm_s16be"], 44100, 2), ("s24.aiff", ["-c:a", "pcm_s24be"], 96000, 2),
    ("s16.flac", ["-c:a", "flac", "-sample_fmt", "s16"], 44100, 2),
    ("s24.flac", ["-c:a", "flac", "-sample_fmt", "s32"], 48000, 2), ("s24-96k.flac", ["-c:a", "flac", "-sample_fmt", "s32"], 96000, 2),
    ("mono.flac", ["-c:a", "flac"], 44100, 1), ("mono.wav", ["-c:a", "pcm_s16le"], 48000, 1),
    ("5.1.flac", ["-c:a", "flac", "-sample_fmt", "s32"], 48000, 6),
    ("alac.m4a", ["-c:a", "alac"], 44100, 2),
    ("320k.mp3", ["-c:a", "libmp3lame", "-b:a", "320k"], 44100, 2), ("48k.mp3", ["-c:a", "libmp3lame", "-q:a", "2"], 48000, 2),
    ("mono.mp3", ["-c:a", "libmp3lame", "-b:a", "128k"], 44100, 1), ("cbr-noxing.mp3", ["-c:a", "libmp3lame", "-b:a", "192k", "-write_xing", "0"], 44100, 2),
]
LOSSY = [("aac.m4a", ["-c:a", "aac", "-b:a", "256k"], 44100, 2), ("vorbis.ogg", ["-c:a", "vorbis", "-strict", "-2"], 44100, 2),
         ("opus.opus", ["-c:a", "libopus", "-b:a", "160k"], 48000, 2)]

def _same_resample(e, dsp, x, sr, out_sr, linear=False) -> bool:
    ref = np.asarray(e.Resample(inputSampleRate=sr, outputSampleRate=out_sr, quality=4 if linear else 1)(x), np.float32)
    out = np.empty(dsp.resample_max_len(len(x), sr, out_sr), np.float32)
    k = (dsp.resample_linear if linear else dsp.resample_sinc_medium)(np.ascontiguousarray(x, np.float32), out, sr, out_sr)
    return k == len(ref) and np.array_equal(out[:k], ref)


def _same_mono(e, stereo, nch) -> bool:
    from dj_track_analyzer import analysis as a

    return np.array_equal(a.mono_mix(stereo, nch), np.asarray(e.MonoMixer()(stereo, nch), np.float32))


def _essentia_decode(e, path: str):
    """AudioLoader's (stereo, rate, channels); >2 channels through the ffmpeg command, as without PyAV."""
    from dj_track_analyzer import analysis as a

    try:
        stereo, sr, nch, *_ = e.AudioLoader(filename=path)()
    except RuntimeError as err:
        if "more than 2 channels" not in str(err):
            raise
        stereo, sr, nch, *_ = a._load_downmixed(Path(path))
    return np.asarray(stereo, np.float32), float(sr), int(nch)


def _decode_diff(ref, path: str) -> str | None:
    """None if PyAV's decode matches Essentia's (ref) bit for bit, "header" if Essentia's is PyAV's with one MP3 frame
    in front (FFmpeg 2.8 decodes a second LAME Info header frame as audio; ours is right: docs/WINDOWS.md stage 2),
    else what differs."""
    from dj_track_analyzer import analysis as a

    x, sr, nch = a._av_decode(a.pyav(), Path(path))
    if (sr, nch) != ref[1:]:
        return f"rate/channels {sr:g}/{nch} vs {ref[1]:g}/{ref[2]}"
    if len(ref[0]) == len(x) + 1152 and path.lower().endswith(".mp3") and np.array_equal(x, ref[0][1152:]):
        return "header"
    if x.shape != ref[0].shape:
        return f"length {len(x)} vs {len(ref[0])}"
    if not np.array_equal(x, ref[0]):
        d = np.abs(x.astype(np.float64) - ref[0])
        return f"{int((d > 0).sum())} values differ, at most {d.max():.2g}"
    return None


def test_files(e) -> int:
    """Encodes a noise + sine test signal in each format with the ffmpeg command, decodes both ways."""
    import shutil
    import subprocess
    import tempfile

    from dj_track_analyzer import analysis as a

    if not shutil.which("ffmpeg"):
        print("decode test files: skipped (no ffmpeg command to make them)")
        return 0
    rng = np.random.default_rng(2)
    bad = 0
    with tempfile.TemporaryDirectory(prefix="dsp-check-") as tmp:
        for name, opts, sr, ch in CODECS + LOSSY:
            n = int(sr * 20.3)
            t = np.arange(n) / sr
            x = (0.25 * rng.standard_normal((n, ch)) + 0.6 * np.sin(2 * np.pi * 440 * t)[:, None]).astype(np.float32)
            x[n // 2: n // 2 + 500] *= 2.2  # peaks over full scale: MP3's clipping, float formats' >1.0
            src, out = Path(tmp) / f"{name}.src.wav", Path(tmp) / name
            a._write_float_wav(src, x, sr)
            made = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), *opts, str(out)],
                                  capture_output=True, text=True)
            if made.returncode:  # an encoder this ffmpeg build lacks
                print(f"decode {name}: skipped (ffmpeg couldn't make it: {made.stderr.strip().splitlines()[-1]})")
                continue
            diff = _decode_diff(_essentia_decode(e, str(out)), str(out))
            if diff and (name, opts, sr, ch) in LOSSY:
                print(f"decode {name}: {diff} (newer decoder, expected)")
            elif diff:
                bad += 1
                print(f"decode DIFFERENT: {name} ({diff})")
    print(f"decode test files: {'all identical' if not bad else f'{bad} different'}")
    return bad


def _essentia_mels(e, x):
    tf = e.TensorflowInputMusiCNN()
    return np.array([tf(f) for f in e.FrameGenerator(x, frameSize=512, hopSize=256, startFromZero=False)],
                    np.float32).reshape(-1, 96)


def _float_steps(mine, ref) -> int:
    """How many floats apart two float32 arrays of one shape are, at most (0: the same bits)."""
    def order(v):
        i = np.asarray(v, np.float32).view(np.int32).astype(np.int64)
        return np.where(i < 0, -(i & 0x7FFFFFFF), i)
    return int(np.abs(order(mine) - order(ref)).max()) if np.size(mine) else 0


def _mels_diff(e, dsp, x) -> str | None:
    """track_analyzer_dsp.musicnn_mels vs TensorflowInputMusiCNN on every FrameGenerator frame: None if the same bits,
    "close" within TOL, else what differs."""
    x = np.ascontiguousarray(x, np.float32)
    out = np.empty((dsp.musicnn_frame_count(len(x)), 96), np.float32)
    ref = _essentia_mels(e, x)
    if dsp.musicnn_mels(x, out) != len(out) or out.shape != ref.shape:
        return f"{len(out)} vs {len(ref)} frames"
    n = _float_steps(out, ref)
    return None if n == 0 else "close" if n <= TOL["mel"] else f"up to {n} float steps apart"


_effnet = {}


def _effnet_ref(e):
    from dj_track_analyzer import analysis as a

    if not _effnet:
        _effnet["ref"] = e.TensorflowPredictEffnetDiscogs(graphFilename=str(a.MODELS_DIR / "discogs-effnet-bs64-1.pb"),
                                                          output=a.EFFNET_OUTPUT)
    return _effnet["ref"]


def _same_effnet(e, x):
    """analysis.effnet_embeddings (Rust mels, own patches and batches, plain TensorflowPredict) vs
    TensorflowPredictEffnetDiscogs: True, False, or "noise". Essentia's streaming FrameCutter adds unseeded noise
    (NoiseAdder, seeded from the clock) to frames below isSilent's cutoff; usually it vanishes in the mel's log, but on
    some tracks it tips roundings, and Essentia's own output then changes from run to run (19 different results in
    20 runs on one track), only in patches holding such frames. Ours is the noise-free result. "noise": every
    differing patch holds a near-silent frame and differs by under 1e-5; "close": differs by under TOL (our mels' log10
    is more exact); anything else is a difference."""
    from dj_track_analyzer import analysis as a

    x = np.ascontiguousarray(x, np.float32)
    mine = a.effnet_embeddings(x)
    ref = np.asarray(_effnet_ref(e)(x), np.float32)
    if mine.shape != ref.shape:
        return False
    d = np.abs(mine - ref).max(axis=1) if mine.ndim == 2 else np.abs(mine - ref)
    if not d.any():
        return True
    frames = np.array(list(e.FrameGenerator(x, frameSize=512, hopSize=256, startFromZero=False)), np.float32)
    silent = np.flatnonzero((frames.astype(np.float64) ** 2).mean(axis=1) < 1e-10)
    noisy = {k for f in silent for k in range(max(0, (f - a.EFFNET_PATCH) // a.EFFNET_PATCH_HOP), f // a.EFFNET_PATCH_HOP + 1)
             if k * a.EFFNET_PATCH_HOP <= f < k * a.EFFNET_PATCH_HOP + a.EFFNET_PATCH}
    bad = set(np.flatnonzero(d > 0).tolist())
    return "noise" if bad <= noisy and d.max() < 1e-5 else "close" if d.max() < TOL["effnet"] else False

_nets = {}


def _net_pairs():
    """(name, the analyzer's model as it loads it, Essentia's copy) for every downloaded graph; the analyzer's run on
    Python TensorFlow (analysis.Graph / Head)."""
    from dj_track_analyzer import analysis as a

    if not _nets:
        ours = [("effnet", a.style_models()["embed"]), ("styles", a.style_models()["styles"])]
        ours += [(f"mood {m}", h) for m, (h, _i) in a.mood_models().items()]
        ours += [("instruments", a.instrument_model()), ("vocals", (a.voice_model() or (None,))[0])]
        ours += [(h, m) for h, m in a.extra_heads().items()]
        ours.append(("maest", (a.maest_model() or {}).get("model")))
        _nets["pairs"] = [(n, m, type(m)(*m.args, essentia=True)) for n, m in ours if m is not None]
    return _nets["pairs"]


def _diff(name, mine, ref) -> str | None:
    mine, ref = np.asarray(mine, np.float32), np.asarray(ref, np.float32)
    if mine.shape != ref.shape:
        return f"{name} shape {mine.shape} vs {ref.shape}"
    if not np.array_equal(mine.view(np.uint32), ref.view(np.uint32)):
        return f"{name} {int((mine != ref).sum())} values differ, at most {np.abs(mine - ref).max():.2g}"
    return None


def _nets_diff(mono16, emb=None) -> list[str]:
    """Each graph on Python TensorFlow vs Essentia's TensorFlow, on the same input: EffNet on mono16's patches, the
    heads on Essentia's embeddings (or emb), MAEST on its 30 s patches. Lists what differs."""
    from dj_track_analyzer import analysis as a

    out = []
    for name, mine, ref in _net_pairs():
        if name == "effnet":
            if mono16 is None:
                continue
            emb = a.effnet_embeddings(mono16, model=ref)
            out.append(_diff(name, a.effnet_embeddings(mono16, model=mine), emb))
        elif name == "maest":
            if mono16 is None:
                continue
            m = a.maest_model()
            for k, patch in enumerate(a._maest_patches(a._maest_audio(mono16))):
                feed = {m["input"]: patch[None, None, :, :].copy()}
                got, want = mine.run(feed), ref.run(feed)
                out += [_diff(f"maest patch {k} {o}", got[o], want[o]) for o in mine.outputs]
        elif emb is not None and len(emb):
            out.append(_diff(name, mine(emb), ref(emb)))
    return [d for d in out if d]


def test_nets() -> int:
    """Every graph on random embeddings around the heads' batch of 64 rows, and EffNet and MAEST on noise."""
    from dj_track_analyzer import analysis as a

    if a.tensorflow() is None:
        print(f"nets: skipped (Python TensorFlow {a.TF_VERSION} isn't installed: uv sync --inexact --extra nets)")
        return 0
    rng = np.random.default_rng(3)
    bad = 0
    for n in (1, 2, 63, 64, 65, 128, 129, 300):
        for d in _nets_diff(None, (0.05 * rng.standard_normal((n, 1280))).astype(np.float32)):
            bad += 1
            print(f"nets DIFFERENT: {n} rows, {d}")
    for sec in (5, 31, 95):
        for d in _nets_diff((0.1 * rng.standard_normal(16000 * sec)).astype(np.float32)):
            bad += 1
            print(f"nets DIFFERENT: {sec} s of noise, {d}")
    print(f"nets: {'all identical' if not bad else f'{bad} different'} ({len(_net_pairs())} graphs)")
    return bad


_ke = {}


def _key_diff(e, dsp, x) -> tuple[str | None, str | None]:
    """track_analyzer_dsp.key_edma vs KeyExtractor(profileType="edma"): (failure, note). A failure is another key or
    scale, or a strength more than TOL off; a note, a strength in other bits."""
    if "ref" not in _ke:
        _ke["ref"] = e.KeyExtractor(profileType="edma", sampleRate=44100)
    x = np.ascontiguousarray(x, np.float32)
    got, want = dsp.key_edma(x), _ke["ref"](x)
    if np.float32(got[2]).view(np.uint32) == np.float32(want[2]).view(np.uint32) and got[:2] == tuple(want[:2]):
        return None, None
    what = f"{got[0]} {got[1]} {got[2]!r} vs {want[0]} {want[1]} {float(want[2])!r}"
    return (None, what) if got[:2] == tuple(want[:2]) and abs(got[2] - float(want[2])) <= TOL["key"] else (what, None)


def _srand1():
    """TempoTapDegara adds rand() noise and Essentia never seeds it: start Essentia's draw from seed 1, as the Rust code does."""
    import ctypes
    import ctypes.util

    ctypes.CDLL(ctypes.util.find_library("c")).srand(1)


def _bpm_diff(e, dsp, x, lo=85, hi=220) -> tuple[str | None, str | None]:
    """rhythm_multifeature vs RhythmExtractor2013(multifeature) as analysis.rhythm calls it: (failure, note). A failure
    is a different whole-number BPM or a confidence more than 0.01 off (the user's gate); a note says what else differs
    (the beats, the bits): expected only where our libm calls are more exact than Essentia's."""
    x = np.ascontiguousarray(x, np.float32)
    _srand1()
    bpm, ticks, conf, _e, _i = e.RhythmExtractor2013(method="multifeature", minTempo=lo, maxTempo=hi)(x)
    b2, t2, c2 = dsp.rhythm_multifeature(x, lo, hi)
    from dj_track_analyzer import analysis as a

    ticks, t2 = np.asarray(ticks, np.float32), np.asarray(t2, np.float32)
    whole = a.round_bpm(a.fold_bpm(float(bpm))) == a.round_bpm(a.fold_bpm(float(b2)))
    fail = None if whole and abs(float(conf) - c2) <= 0.01 else f"BPM {b2:.4f} vs {float(bpm):.4f}, confidence {c2:.4f} vs {float(conf):.4f}"
    if np.float32(bpm) == np.float32(b2) and np.float32(conf) == np.float32(c2) and np.array_equal(ticks, t2):
        return fail, None
    beats = "same beats" if np.array_equal(ticks, t2) else f"{len(t2)} vs {len(ticks)} beats"
    return fail, f"{beats}, BPM {b2:.6f} vs {float(bpm):.6f}, confidence {c2:.6f} vs {float(conf):.6f}"


def _segments(diff, step, e, dsp, mono44, whole=True) -> tuple[list[str], list[str]]:
    """diff (_key_diff or _bpm_diff) on the whole signal and, over 12 minutes, on every 2-minute segment as
    segment_timeline cuts them, as analyze_file measures them (the BPM never on a whole mix: Essentia can't on long
    ones, its onset buffer overflows): (failures, notes)."""
    duration = len(mono44) / 44100.0
    parts = [("", mono44)] if whole or duration <= 12 * 60 else []
    if duration > 12 * 60:
        for i in range(int(np.ceil(duration / 120))):
            a, b = i * 120, min(duration, (i + 1) * 120)
            if b - a >= 20:
                parts.append((f" segment {i}", mono44[int(a * 44100): int(b * 44100)]))
    fails, notes = [], []
    for name, x in parts:
        f, n = diff(e, dsp, x)
        if f:
            fails.append(f"{step}{name}: {f}")
        if n:
            notes.append(f"{step}{name}: {n}")
    return fails, notes


def test_key(e, dsp, rng) -> int:
    """Chords of 1-5 sines with noise, quiet starts, 0.02-20 s; silence; one sample; one frame."""
    bad = 0
    sr = 44100
    signals = []
    for trial in range(60):
        t = np.arange(int(sr * rng.uniform(0.02, 20))) / sr
        x = np.zeros(len(t))
        for f0 in rng.uniform(40, 2000, rng.integers(1, 6)):
            x += rng.uniform(0.05, 0.4) * np.sin(2 * np.pi * f0 * t + rng.uniform(0, 6))
        x += 10 ** rng.uniform(-5, -1) * rng.standard_normal(len(t))
        if trial % 3 == 0:
            x[: rng.integers(0, len(x))] *= 1e-7
        signals.append((f"{len(t)} samples", x))
    signals += [("silence", np.zeros(50000)), ("one sample", np.full(1, 0.3)), ("one frame", 0.1 * rng.standard_normal(4096))]
    close = 0
    for name, x in signals:
        fail, note = _key_diff(e, dsp, x)
        bad += bool(fail)
        close += bool(note)
        if fail:
            print(f"key DIFFERENT: {name}: {fail}")
    print(f"key: {len(signals) - bad - close} of {len(signals)} signals identical, {close} close" + (f", {bad} different" if bad else ""))
    return bad


def _fft_diffs(dsp, rng) -> list[str]:
    """fftw.rs (the BPM's autocorrelations) against the FFTW in Essentia's wheel, forward and inverse, every power of
    two up to its largest plan, on a few random inputs. Only on macOS: the Rust follows that build's FFTW."""
    import ctypes

    import essentia

    lib = next((Path(essentia.__file__).parent / ".dylibs").glob("libfftw3f*.dylib"), None) if sys.platform == "darwin" else None
    if lib is None or not hasattr(dsp, "_rfft_max_p"):
        return []
    lib = ctypes.CDLL(str(lib))
    lib.fftwf_malloc.restype = ctypes.c_void_p
    lib.fftwf_malloc.argtypes = [ctypes.c_size_t]
    lib.fftwf_execute.argtypes = [ctypes.c_void_p]
    for f in ("fftwf_plan_dft_r2c_1d", "fftwf_plan_dft_c2r_1d"):
        getattr(lib, f).restype = ctypes.c_void_p
        getattr(lib, f).argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    bad = []
    for p in range(1, dsp._rfft_max_p() + 1):
        n = 1 << p
        for kind in ("r2c", "c2r"):
            nin, nout = (n, n + 2) if kind == "r2c" else (n + 2, n)
            ip, op = lib.fftwf_malloc(4 * n + 8), lib.fftwf_malloc(4 * n + 8)  # as Essentia's (I)FFTW: aligned, apart
            plan = getattr(lib, f"fftwf_plan_dft_{kind}_1d")(n, ip, op, 1 << 6)  # FFTW_ESTIMATE
            xin = np.ctypeslib.as_array((ctypes.c_float * nin).from_address(ip))
            xout = np.ctypeslib.as_array((ctypes.c_float * nout).from_address(op))
            for k in range(3):
                x = (rng.standard_normal(nin) * 10.0 ** rng.uniform(-6, 2)).astype(np.float32)
                if k == 1:
                    x[rng.integers(0, nin):] = 0
                xin[:] = x
                lib.fftwf_execute(plan)
                out = np.empty(nout, np.float32)
                dsp._rfft(kind, x, out)
                if not np.array_equal(out.view(np.uint32), xout.view(np.uint32)):
                    bad.append(f"{kind} 2^{p}")
                    break
    return bad


def test_bpm(e, dsp, rng) -> int:
    """fftw.rs against FFTW, then kick + hat patterns at 85-175 BPM, noise, sparse clicks, a held tone, silence, very
    short signals."""
    fft = _fft_diffs(dsp, rng)
    for what in fft:
        print(f"bpm DIFFERENT: FFT {what}")
    sr = 44100
    signals = []
    for t in range(12):
        n = int(sr * rng.uniform(8, 45))
        tt = np.arange(n) / sr
        kind = t % 4
        if kind == 0:
            bpm = rng.uniform(85, 175)
            x = 0.5 * (np.sin(2 * np.pi * bpm / 60 * tt) > 0.85) * np.sin(2 * np.pi * 55 * tt)
            x += (np.sin(2 * np.pi * 2 * bpm / 60 * tt + 1) > 0.95) * rng.standard_normal(n) * 0.2
        elif kind == 1:
            x = rng.standard_normal(n) * 0.1
        elif kind == 2:
            x = np.zeros(n)
            x[rng.integers(0, n, rng.integers(5, 60))] = 1.0
        else:
            x = 0.2 * np.sin(2 * np.pi * rng.uniform(100, 900) * tt)
        signals.append((f"{n} samples, kind {kind}", x + 0.01 * rng.standard_normal(n)))
    signals += [("silence", np.zeros(sr * 10)), ("one frame", 0.1 * rng.standard_normal(2048)),
                ("three seconds", 0.1 * rng.standard_normal(sr * 3))]
    bad, close = len(fft), 0
    for name, x in signals:
        fail, note = _bpm_diff(e, dsp, x.astype(np.float32))
        bad += bool(fail)
        close += bool(note) and not fail
        if fail:
            print(f"bpm DIFFERENT: {name}: {fail}")
        elif note:
            print(f"bpm close: {name}: {note}")
    print(f"bpm: {len(signals) - bad + len(fft) - close} of {len(signals)} signals identical, {close} within the gate"
          + (f", {bad} different" if bad else ""))
    return bad


def _same_loudness(e, dsp, stereo, sr) -> bool:
    stereo = np.ascontiguousarray(stereo, np.float32)
    _m, short, integ, lra = e.LoudnessEBUR128(sampleRate=sr)(stereo)
    b, i2, l2 = dsp.loudness_ebur128(stereo, sr)
    st = np.frombuffer(b, np.float32)
    return (np.array_equal(st, np.asarray(short, np.float32))
            and np.float32(i2) == np.float32(integ) and np.float32(l2) == np.float32(lra))


def test_signals(steps) -> int:
    from dj_track_analyzer import analysis as a
    import track_analyzer_dsp as dsp

    e = a.es()
    rng = np.random.default_rng(1)
    bad = 0
    for sr in (8000, 11025, 16000, 22050, 32000, 44100, 48000, 88200, 96000, 176400, 192000):
        for n in (1, 7, 100, 1000, 4096, 50000, int(sr * 37.3)):
            t = np.arange(n) / sr
            x = (0.3 * rng.standard_normal(n) + 0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
            for out_sr in (44100, 16000):
                if "resample" in steps and not _same_resample(e, dsp, x, sr, out_sr):
                    bad += 1
                    print(f"resample DIFFERENT: {sr} -> {out_sr} Hz, {n} samples")
                # 1 sample: libsamplerate's linear converter reads before the array (garbage), can't be matched
                if "linear" in steps and n > 1 and not _same_resample(e, dsp, x, sr, out_sr, linear=True):
                    bad += 1
                    print(f"linear resample DIFFERENT: {sr} -> {out_sr} Hz, {n} samples")
    for sr in (44100.0, 48000.0):
        for sec in (0.5, 3.0, 3.05, 61.7):
            n = int(sr * sec)
            st = (0.2 * rng.standard_normal((n, 2))).astype(np.float32)
            st[: n // 3] *= 0.01  # a quiet start, so the gates have something to drop
            if "loudness" in steps and not _same_loudness(e, dsp, st, sr):
                bad += 1
                print(f"loudness DIFFERENT: {sr:.0f} Hz, {sec} s")
            for nch in (1, 2) if "mono" in steps else ():
                if not _same_mono(e, st, nch):
                    bad += 1
                    print(f"mono mix DIFFERENT: {nch} channel(s), {sec} s")
    if "mel" in steps:
        for n in (1, 255, 256, 257, 511, 512, 513, 16000, 16000 * 30 + 77):
            for scale in (1e-6, 1e-3, 0.3, 1.5):
                d = _mels_diff(e, dsp, (scale * rng.standard_normal(n)).astype(np.float32))
                if d and d != "close":
                    bad += 1
                    print(f"mel DIFFERENT: {n} samples, scale {scale}: {d}")
    if "effnet" in steps:  # lengths around patch (128 frames, every 62) and batch (64 patches) boundaries
        for k in (-1, 0, 1, 2, 63, 64, 65):
            for d in (-300, -1, 0, 1, 256, 300):
                n = 256 * (128 + 62 * k) + d
                if n > 0 and _same_effnet(e, (0.1 * rng.standard_normal(n)).astype(np.float32)) is False:
                    bad += 1
                    print(f"effnet DIFFERENT: {n} samples")
        # a first frame quiet enough to count as silent but not zero: the streaming FrameCutter blanks it
        x = (0.1 * rng.standard_normal(16000 * 5)).astype(np.float32)
        x[:400] = 0
        x[100:120] = 1e-5
        if _same_effnet(e, x) is False:
            bad += 1
            print("effnet DIFFERENT: near-silent first frame")
    if "key" in steps:
        bad += test_key(e, dsp, rng)
    if "bpm" in steps:
        bad += test_bpm(e, dsp, rng)
    print(f"test signals: {'all identical or close' if not bad else f'{bad} different'}")
    if "nets" in steps:
        bad += test_nets()
    if "decode" in steps:
        bad += test_files(e)
    return bad


def _check_file(steps, path: str) -> str | None:
    from dj_track_analyzer import analysis as a
    import track_analyzer_dsp as dsp

    e = a.es()
    try:
        ref = stereo, sr, nch = _essentia_decode(e, path)
    except Exception as err:  # unreadable here = not a difference; say so
        return f"skipped ({err}): {path}"
    out = []
    notes = []
    if "decode" in steps and (diff := _decode_diff(ref, path)):
        if diff == "header":
            notes.append("decode: Essentia decodes the Info header frame as audio, 26 ms; the rest the same")
        else:
            out.append(f"decode: {diff}")
    if "loudness" in steps and not _same_loudness(e, dsp, stereo, sr):
        out.append("loudness")
    if "mono" in steps and not _same_mono(e, stereo, nch):
        out.append("mono mix")
    if not {"resample", "linear", "mel", "effnet", "nets", "key", "bpm"} & set(steps):
        return f"DIFFERENT ({', '.join(out)}): {path}" if out else f"close ({'; '.join(notes)}): {path}" if notes else None
    mono = e.MonoMixer()(stereo, nch)  # Essentia's, so each resample check sees the same input
    if "resample" in steps and sr != 44100 and not _same_resample(e, dsp, mono, sr, 44100):
        out.append("resample")
    if {"key", "bpm"} & set(steps):  # on Essentia's 44.1 kHz mono, so only the key / BPM step is compared
        mono44 = np.asarray(mono if sr == 44100 else e.Resample(inputSampleRate=sr, outputSampleRate=44100, quality=1)(mono),
                            np.float32)
        for step, diff in (("key", _key_diff), ("bpm", _bpm_diff)):
            if step in steps:
                fails, n = _segments(diff, step, e, dsp, mono44, whole=step == "key")
                out += fails
                notes += n
    if "linear" in steps and not _same_resample(e, dsp, mono, sr, 16000, linear=True):
        out.append("linear resample")
    varies = None
    if {"mel", "effnet", "nets"} & set(steps):
        mono16 = np.asarray(e.Resample(inputSampleRate=sr, outputSampleRate=16000, quality=4)(mono), np.float32)
        if "mel" in steps and (d := _mels_diff(e, dsp, mono16)):
            if d == "close":
                notes.append("mel")
            else:
                out.append(f"mel: {d}")
        if "effnet" in steps:
            r = _same_effnet(e, mono16)
            if r == "noise":
                varies = f"effnet: differs only where Essentia adds random noise (near-silent frames; its own output varies from run to run there): {path}"
            elif r == "close":
                notes.append("effnet")
            elif not r:
                out.append("effnet")
        if "nets" in steps and a.tensorflow() is not None:
            out += _nets_diff(mono16)
    if out:
        return f"DIFFERENT ({', '.join(out)}): {path}"
    if notes:  # other bits, within TOL or the BPM gate
        return f"close ({'; '.join(notes)}): {path}"
    return varies


def _is_long(path: str) -> bool:
    """Over 12 minutes (a mix): read from the file's header, no decoding."""
    try:
        import mutagen

        return mutagen.File(path).info.length > 12 * 60
    except Exception:
        return Path(path).stat().st_size > 100_000_000


def files(paths: list[str], steps, workers: int, mix_workers: int) -> int:
    long = [p for p in paths if _is_long(p)]
    groups = [(f"tracks", [p for p in paths if p not in set(long)], workers, 25), ("mixes", long, mix_workers, 1)]
    bad = done = varies = close = 0
    for name, group, n, every in groups:
        if not group:
            continue
        print(f"{name}: {len(group)} files, {n} at a time", flush=True)
        with ProcessPoolExecutor(n) as ex:
            for i, msg in enumerate(ex.map(partial(_check_file, steps), group), 1):
                if msg:
                    print(msg, flush=True)
                    bad += msg.startswith("DIFFERENT")
                    varies += msg.startswith("effnet: differs only where")
                    close += msg.startswith("close")
                if i % every == 0 or i == len(group):
                    print(f"[{done + i}/{len(paths)}] files checked", flush=True)
        done += len(group)
    print(f"files: {len(paths) - bad - varies - close} of {len(paths)} identical"
          + (f", {varies} differ in EffNet only where Essentia's own output is random (near-silent frames)" if varies else "")
          + (f", {close} close (where ours is more exact: other bits within the tolerances, the BPM within the gate, or an MP3 header frame not decoded)" if close else "")
          + (f", {bad} different" if bad else ""))
    return bad


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--collection", action="store_true", help="also every track in collection.db")
    ap.add_argument("--files", nargs="*", default=[], help="more audio files (mixes at 48 kHz, ...)")
    ap.add_argument("--files-from", help="a text file with more audio files, one path per line")
    ap.add_argument("--steps", nargs="+", choices=STEPS, default=list(STEPS), help="only these steps (default: all)")
    ap.add_argument("--workers", type=int, help="processes for tracks (default 6; 2 with nets: ~5 GB each)")
    ap.add_argument("--mix-workers", type=int, help="processes for files over 12 minutes (default 2, ~1.3 GB each; 1 with nets)")
    args = ap.parse_args()
    nets = "nets" in args.steps  # every model twice per process, one copy per TensorFlow
    args.workers = args.workers or (2 if nets else 6)
    args.mix_workers = args.mix_workers or (1 if nets else 2)
    print("steps:", " ".join(args.steps), f"({args.workers} processes for tracks, {args.mix_workers} for mixes)", flush=True)
    with ProcessPoolExecutor(1) as ex:  # its own process, so the models it loads don't stay in this one
        bad = ex.submit(test_signals, args.steps).result()
    paths = list(args.files)
    if args.files_from:
        paths += [l.strip() for l in Path(args.files_from).read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.collection:
        from dj_track_analyzer import collection

        con = sqlite3.connect(collection.DB)
        paths += [p for (p,) in con.execute("SELECT path FROM tracks") if Path(p).exists()]
    if paths:
        bad += files(paths, args.steps, args.workers, args.mix_workers)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":  # spawn workers re-import this file
    main()
