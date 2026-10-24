"""Check the Rust module against Essentia, bit for bit. Run after any change to dsp/:

    uv run python dsp/check.py                 # test signals: resampling (sinc, linear) at 8-192 kHz, loudness, mono mix
    uv run python dsp/check.py --collection    # also every scanned track and every file given with --files
    uv run python dsp/check.py --files-from mixes.txt   # files listed one per line (names with spaces are fine)

Prints one line per difference and exits 1 if there is any. A Rust step only replaces Essentia's while this
passes on the whole test collection. Compare on a Mac: Essentia's arm64 build fuses multiply-adds that its
Linux x86_64 build doesn't, and the Rust loudness follows the Mac's (docs/WINDOWS.md).
"""

import argparse
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np


def _same_resample(e, dsp, x, sr, out_sr, linear=False) -> bool:
    ref = np.asarray(e.Resample(inputSampleRate=sr, outputSampleRate=out_sr, quality=4 if linear else 1)(x), np.float32)
    out = np.empty(dsp.resample_max_len(len(x), sr, out_sr), np.float32)
    k = (dsp.resample_linear if linear else dsp.resample_sinc_medium)(np.ascontiguousarray(x, np.float32), out, sr, out_sr)
    return k == len(ref) and np.array_equal(out[:k], ref)


def _same_mono(e, stereo, nch) -> bool:
    from dj_track_analyzer import analysis as a

    return np.array_equal(a.mono_mix(stereo, nch), np.asarray(e.MonoMixer()(stereo, nch), np.float32))


def _same_loudness(e, dsp, stereo, sr) -> bool:
    stereo = np.ascontiguousarray(stereo, np.float32)
    _m, short, integ, lra = e.LoudnessEBUR128(sampleRate=sr)(stereo)
    b, i2, l2 = dsp.loudness_ebur128(stereo, sr)
    st = np.frombuffer(b, np.float32)
    return (np.array_equal(st, np.asarray(short, np.float32))
            and np.float32(i2) == np.float32(integ) and np.float32(l2) == np.float32(lra))


def test_signals() -> int:
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
                if not _same_resample(e, dsp, x, sr, out_sr):
                    bad += 1
                    print(f"resample DIFFERENT: {sr} -> {out_sr} Hz, {n} samples")
                # 1 sample: libsamplerate's linear converter reads before the array (garbage), can't be matched
                if n > 1 and not _same_resample(e, dsp, x, sr, out_sr, linear=True):
                    bad += 1
                    print(f"linear resample DIFFERENT: {sr} -> {out_sr} Hz, {n} samples")
    for sr in (44100.0, 48000.0):
        for sec in (0.5, 3.0, 3.05, 61.7):
            n = int(sr * sec)
            st = (0.2 * rng.standard_normal((n, 2))).astype(np.float32)
            st[: n // 3] *= 0.01  # a quiet start, so the gates have something to drop
            if not _same_loudness(e, dsp, st, sr):
                bad += 1
                print(f"loudness DIFFERENT: {sr:.0f} Hz, {sec} s")
            for nch in (1, 2):
                if not _same_mono(e, st, nch):
                    bad += 1
                    print(f"mono mix DIFFERENT: {nch} channel(s), {sec} s")
    print(f"test signals: {'all identical' if not bad else f'{bad} different'}")
    return bad


def _check_file(path: str) -> str | None:
    from dj_track_analyzer import analysis as a
    import track_analyzer_dsp as dsp

    e = a.es()
    try:
        try:
            stereo, sr, nch, *_ = e.AudioLoader(filename=path)()
        except RuntimeError as err:
            if "more than 2 channels" not in str(err):
                raise
            stereo, sr, nch, *_ = a._load_downmixed(Path(path))
    except Exception as err:  # unreadable here = not a difference; say so
        return f"skipped ({err}): {path}"
    out = []
    if not _same_loudness(e, dsp, stereo, sr):
        out.append("loudness")
    if not _same_mono(e, stereo, nch):
        out.append("mono mix")
    mono = e.MonoMixer()(stereo, nch)
    if sr != 44100 and not _same_resample(e, dsp, mono, sr, 44100):
        out.append("resample")
    if not _same_resample(e, dsp, mono, sr, 16000, linear=True):
        out.append("linear resample")
    return f"DIFFERENT ({', '.join(out)}): {path}" if out else None


def files(paths: list[str], workers: int) -> int:
    bad = 0
    with ProcessPoolExecutor(workers) as ex:
        for i, msg in enumerate(ex.map(_check_file, paths), 1):
            if msg:
                print(msg, flush=True)
                bad += msg.startswith("DIFFERENT")
            if i % 50 == 0 or i == len(paths):
                print(f"[{i}/{len(paths)}] files checked", file=sys.stderr, flush=True)
    print(f"files: {len(paths) - bad} of {len(paths)} identical")
    return bad


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--collection", action="store_true", help="also every track in collection.db")
    ap.add_argument("--files", nargs="*", default=[], help="more audio files (mixes at 48 kHz, ...)")
    ap.add_argument("--files-from", help="a text file with more audio files, one path per line")
    ap.add_argument("--workers", type=int, default=4, help="processes for the file check (long mixes: 1)")
    args = ap.parse_args()
    bad = test_signals()
    paths = list(args.files)
    if args.files_from:
        paths += [l.strip() for l in Path(args.files_from).read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.collection:
        from dj_track_analyzer import collection

        con = sqlite3.connect(collection.DB)
        paths += [p for (p,) in con.execute("SELECT path FROM tracks") if Path(p).exists()]
    if paths:
        bad += files(paths, args.workers)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":  # spawn workers re-import this file
    main()
