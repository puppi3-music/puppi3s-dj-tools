"""Make the test environment's library: a few synthetic club tracks (kick, hats, bass, chords, a breakdown) at known
BPMs and keys, and a Traktor collection.nml that lists them with grids, cue points and a loop, as Traktor 4 writes it.

  python scripts/make_fixtures.py --music /fixtures/music --nml "~/Documents/Native Instruments/Traktor 4.0.0/collection.nml"

Synthetic audio needs no licence and comes out the same every time. BPM, key, grids and cues are meaningful; genre,
mood and colour scores are not (the models never heard music like this). Writes expected.json next to the tracks
with what each track was made as. Needs numpy; ffmpeg for the MP3 and FLAC copies (WAV only without it).
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

SR = 44100
LEAD_IN = 0.1  # seconds of silence before the first downbeat (Traktor's AutoGrid START)
BARS = 40
BREAKDOWN = (16, 24)  # bars without kick
NOTES = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5, "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
# title, BPM, root, scale, format, Traktor colour (None = uncoloured)
TRACKS = [
    ("Fixture One", 128, "A", "minor", "mp3", 1),
    ("Fixture Two", 140, "F#", "minor", "flac", 7),
    ("Fixture Three", 124, "C", "major", "wav", None),
    ("Fixture Four", 145, "D", "minor", "wav", None),
]


def hz(semitone: int, octave: int) -> float:
    return 440.0 * 2 ** ((semitone - 9) / 12 + octave - 4)


def track(bpm: float, root: str, scale: str, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    n = int(SR * (LEAD_IN + BARS * 4 * beat + 1.0))
    out = np.zeros(n)

    def put(at: float, sound: np.ndarray, gain: float) -> None:
        i = int(round(at * SR))
        j = min(n, i + len(sound))
        out[i:j] += gain * sound[: j - i]

    t = np.arange(int(0.35 * SR)) / SR
    kick = np.sin(2 * np.pi * np.cumsum(50 + 100 * np.exp(-t * 30)) / SR) * np.exp(-t * 9)
    th = np.arange(int(0.04 * SR)) / SR
    hat = np.diff(rng.standard_normal(len(th) + 1)) * np.exp(-th * 90)
    r = NOTES[root]
    third = 3 if scale == "minor" else 4
    tb = np.arange(int(beat / 2 * SR)) / SR
    bass = np.sin(2 * np.pi * hz(r, 2) * tb) * np.minimum(1, (len(tb) - np.arange(len(tb))) / 400) * np.exp(-tb * 4)
    for b in range(BARS * 4):
        at = LEAD_IN + b * beat
        bar = b // 4
        if not BREAKDOWN[0] <= bar < BREAKDOWN[1]:
            put(at, kick, 0.9)
            put(at + beat / 2, bass, 0.35)
        if bar >= 4:
            put(at + beat / 2, hat, 0.12)
    tp = np.arange(n) / SR
    pad = sum(np.sin(2 * np.pi * hz(r + s, 3) * k * tp) / k for s in (0, third, 7) for k in (1, 2, 3))
    level = np.full(n, 0.06)
    bd = (tp >= LEAD_IN + BREAKDOWN[0] * 4 * beat) & (tp < LEAD_IN + BREAKDOWN[1] * 4 * beat)
    level[bd] = 0.14
    level[tp < LEAD_IN] = 0
    out += level * pad
    out *= 0.8 / np.max(np.abs(out))
    return np.stack([out, out * 0.98], axis=1)


def write_wav(path: Path, audio: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())


def nml_dir(folder: Path) -> str:
    return "".join(f"/:{p}" for p in folder.parts[1:]) + "/:"


def entry(path: Path, title: str, bpm: float, key: int, colour: int | None) -> str:
    beat_ms = 60000.0 / bpm
    stamp = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
    secs = stamp.hour * 3600 + stamp.minute * 60 + stamp.second
    length = BARS * 4 * beat_ms / 1000 + LEAD_IN + 1.0
    cues = [f'<CUE_V2 NAME="AutoGrid" DISPL_ORDER="0" TYPE="4" START="{LEAD_IN * 1000:.6f}" LEN="0.000000" '
            f'REPEATS="-1" HOTCUE="-1"><GRID BPM="{bpm:.6f}"></GRID>\n</CUE_V2>']
    for slot, bar in enumerate((0, 8, 16, 24, 32)):
        cues.append(f'<CUE_V2 NAME="Cue {slot + 1}" DISPL_ORDER="0" TYPE="0" START="{LEAD_IN * 1000 + bar * 4 * beat_ms:.6f}" '
                    f'LEN="0.000000" REPEATS="-1" HOTCUE="{slot}"></CUE_V2>')
    cues.append(f'<CUE_V2 NAME="Loop" DISPL_ORDER="0" TYPE="5" START="{LEAD_IN * 1000 + 32 * 4 * beat_ms:.6f}" '
                f'LEN="{16 * beat_ms:.6f}" REPEATS="-1" HOTCUE="7"></CUE_V2>')
    colour_attr = f' COLOR="{colour}"' if colour else ""
    return (f'<ENTRY MODIFIED_DATE="{stamp.year}/{stamp.month}/{stamp.day}" MODIFIED_TIME="{secs}" LOCK="0" '
            f'TITLE="{html.escape(title)}" ARTIST="Track Analyzer"><LOCATION DIR="{html.escape(nml_dir(path.parent))}" '
            f'FILE="{html.escape(path.name)}" VOLUME="" VOLUMEID=""></LOCATION>\n'
            f'<MODIFICATION_INFO AUTHOR_TYPE="user"></MODIFICATION_INFO>\n'
            f'<INFO BITRATE="1411200" PLAYTIME="{int(length)}" PLAYTIME_FLOAT="{length:.6f}" '
            f'IMPORT_DATE="2026/10/1" FLAGS="12"{colour_attr}></INFO>\n'
            f'<TEMPO BPM="{bpm:.6f}" BPM_QUALITY="100.000000"></TEMPO>\n'
            f'<MUSICAL_KEY VALUE="{key}"></MUSICAL_KEY>\n' + "\n".join(cues) + "\n</ENTRY>\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--music", required=True, help="folder for the tracks (made if missing)")
    ap.add_argument("--nml", required=True, help="where to write collection.nml")
    args = ap.parse_args()
    music = Path(args.music).expanduser().resolve()
    nml = Path(args.nml).expanduser()
    music.mkdir(parents=True, exist_ok=True)
    nml.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    entries, expected = [], {}
    for i, (title, bpm, root, scale, fmt, colour) in enumerate(TRACKS):
        fmt = fmt if ffmpeg else "wav"
        path = music / f"Track Analyzer - {title}.{fmt}"
        audio = track(bpm, root, scale, seed=i)
        if fmt == "wav":
            write_wav(path, audio)
        else:
            tmp = path.with_suffix(".tmp.wav")
            write_wav(tmp, audio)
            codec = ["-c:a", "libmp3lame", "-b:a", "320k"] if fmt == "mp3" else ["-c:a", "flac"]
            subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(tmp), *codec,
                            "-metadata", f"title={title}", "-metadata", "artist=Track Analyzer", str(path)], check=True)
            tmp.unlink()
        key = NOTES[root] + (12 if scale == "minor" else 0)
        entries.append(entry(path, title, bpm, key, colour))
        expected[str(path)] = {"bpm": bpm, "key": f"{root} {scale}", "traktor_key": key, "colour": colour}
    nml.write_text('<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n'
                   '<NML VERSION="20"><HEAD COMPANY="www.native-instruments.com" PROGRAM="Traktor Pro 4"></HEAD>\n'
                   f'<COLLECTION ENTRIES="{len(entries)}">' + "".join(entries) + "</COLLECTION>\n"
                   '<SETS ENTRIES="0"></SETS>\n'
                   '<PLAYLISTS><NODE TYPE="FOLDER" NAME="$ROOT"><SUBNODES COUNT="0"></SUBNODES>\n</NODE>\n</PLAYLISTS>\n'
                   "</NML>\n", encoding="utf-8")
    (music / "expected.json").write_text(json.dumps(expected, indent=2) + "\n")
    print(f"{len(entries)} tracks in {music}\ncollection: {nml}")


if __name__ == "__main__":
    os.umask(0o022)
    main()
