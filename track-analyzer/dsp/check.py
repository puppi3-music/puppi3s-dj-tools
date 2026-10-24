"""Check the Rust module and PyAV decoding against Essentia, bit for bit. Run after any change to dsp/ or decode:

    uv run python dsp/check.py                 # test signals: resampling (sinc, linear) at 8-192 kHz, loudness, mono mix,
                                               # decoding test files in each format (made with the ffmpeg command)
    uv run python dsp/check.py --collection    # also every scanned track and every file given with --files
    uv run python dsp/check.py --files-from mixes.txt   # files listed one per line (names with spaces are fine)
    uv run python dsp/check.py --collection --steps linear   # only the step you changed: much faster

Prints one line per difference and exits 1 if there is any. A Rust step only replaces Essentia's while this
passes on the whole test collection; run every step (the default) before a release. Most of the time goes
into Essentia's own slow steps (about a minute per hour-long mix), so check only what changed while working.
Tracks run --workers at a time; files over 12 minutes (mixes, ~1.3 GB each in memory) --mix-workers. Compare on a Mac: Essentia's arm64 build fuses multiply-adds that its
Linux x86_64 build doesn't, and the Rust loudness follows the Mac's (docs/WINDOWS.md).
"""

import argparse
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

import numpy as np

STEPS = ("loudness", "mono", "resample", "linear", "decode")
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
    """None if PyAV's decode matches Essentia's (ref) bit for bit, else what differs."""
    from dj_track_analyzer import analysis as a

    x, sr, nch = a._av_decode(a.pyav(), Path(path))
    if (sr, nch) != ref[1:]:
        return f"rate/channels {sr:g}/{nch} vs {ref[1]:g}/{ref[2]}"
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
    print(f"test signals: {'all identical' if not bad else f'{bad} different'}")
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
    if "decode" in steps and (diff := _decode_diff(ref, path)):
        out.append(f"decode: {diff}")
    if "loudness" in steps and not _same_loudness(e, dsp, stereo, sr):
        out.append("loudness")
    if "mono" in steps and not _same_mono(e, stereo, nch):
        out.append("mono mix")
    if not {"resample", "linear"} & set(steps):
        return f"DIFFERENT ({', '.join(out)}): {path}" if out else None
    mono = e.MonoMixer()(stereo, nch)  # Essentia's, so each resample check sees the same input
    if "resample" in steps and sr != 44100 and not _same_resample(e, dsp, mono, sr, 44100):
        out.append("resample")
    if "linear" in steps and not _same_resample(e, dsp, mono, sr, 16000, linear=True):
        out.append("linear resample")
    return f"DIFFERENT ({', '.join(out)}): {path}" if out else None


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
    bad = done = 0
    for name, group, n, every in groups:
        if not group:
            continue
        print(f"{name}: {len(group)} files, {n} at a time", flush=True)
        with ProcessPoolExecutor(n) as ex:
            for i, msg in enumerate(ex.map(partial(_check_file, steps), group), 1):
                if msg:
                    print(msg, flush=True)
                    bad += msg.startswith("DIFFERENT")
                if i % every == 0 or i == len(group):
                    print(f"[{done + i}/{len(paths)}] files checked", flush=True)
        done += len(group)
    print(f"files: {len(paths) - bad} of {len(paths)} identical")
    return bad


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--collection", action="store_true", help="also every track in collection.db")
    ap.add_argument("--files", nargs="*", default=[], help="more audio files (mixes at 48 kHz, ...)")
    ap.add_argument("--files-from", help="a text file with more audio files, one path per line")
    ap.add_argument("--steps", nargs="+", choices=STEPS, default=list(STEPS), help="only these steps (default: all)")
    ap.add_argument("--workers", type=int, default=6, help="processes for tracks")
    ap.add_argument("--mix-workers", type=int, default=2, help="processes for files over 12 minutes (~1.3 GB each)")
    args = ap.parse_args()
    print("steps:", " ".join(args.steps), flush=True)
    bad = test_signals(args.steps)
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
