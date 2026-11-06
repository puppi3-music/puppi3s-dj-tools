"""`clips describe`: clips someone else cut from the finished video (an editor), placed in the mix by their sound
and described like the analysis' clip suggestions, plus their own genre and mood, for the posts that go with them."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from .analysis import fmt_time

MATCH_SR = 2000  # Hz: plenty to line a clip's waveform up with the recording, and a 2-hour mix stays ~58 MB
MIN_SCORE = 0.5  # normalised correlation at the match (the same audio, re-encoded, scores ~0.9+)
MIN_MARGIN = 0.15  # ... and this far above the best spot elsewhere in the mix (a repeated loop can score high twice)
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}


def _ffmpeg() -> str:
    from .analysis import find_tool, install_hint

    exe = find_tool("ffmpeg")
    if not exe:
        raise SystemExit(f"clips describe needs ffmpeg: {install_hint('ffmpeg')}")
    return exe


def decode(path: Path, sr: int = MATCH_SR, channels: int = 1, start: float | None = None,
           length: float | None = None) -> np.ndarray:
    """The audio of a file (video or audio) as float32 at `sr`, (n,) for mono or (n, channels)."""
    cmd = [_ffmpeg(), "-v", "error", *(["-ss", f"{start:.3f}"] if start else []), "-i", str(path),
           *(["-t", f"{length:.3f}"] if length else []), "-map", "0:a:0", "-ac", str(channels), "-ar", str(sr),
           "-f", "f32le", "pipe:1"]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode:
        raise ValueError(f"can't read the sound of {path.name}: {res.stderr.decode(errors='replace').strip()[-200:]}")
    y = np.frombuffer(res.stdout, dtype=np.float32)
    return y.reshape(-1, channels) if channels > 1 else y


class Recording:
    """A recording's sound (mono at `sr`) to find clips in; its FFT and running sums are kept for the next clip."""

    def __init__(self, y: np.ndarray, sr: int = MATCH_SR):
        self.r, self.sr, self._fft = y.astype(np.float64), sr, {}
        self.cs = np.concatenate(([0.0], np.cumsum(self.r)))
        self.cs2 = np.concatenate(([0.0], np.cumsum(self.r * self.r)))

    def match(self, clip: np.ndarray) -> dict:
        """Where `clip` (mono at the same rate) sits: normalised cross-correlation at every offset, by FFT.
        {"at": seconds in, "score": 0-1 there, "next": the best score more than a second away}."""
        r, sr, m = self.r, self.sr, len(clip)
        none = {"at": None, "score": 0.0, "next": 0.0}
        if m < sr or len(r) < m:
            return none
        c = clip.astype(np.float64) - clip.mean()
        c_norm = np.sqrt((c * c).sum())
        if c_norm == 0:
            return none
        n = 1 << int(np.ceil(np.log2(len(r) + m)))
        if n not in self._fft:
            self._fft = {n: np.fft.rfft(r, n)}  # one size kept: clips of similar length share it
        corr = np.fft.irfft(self._fft[n] * np.conj(np.fft.rfft(c, n)), n)[: len(r) - m + 1]
        # each window's own energy (around its mean), so the editor's volume doesn't change the score
        s, s2 = self.cs[m:] - self.cs[:-m], self.cs2[m:] - self.cs2[:-m]
        energy = np.maximum(s2 - s * s / m, 0.0)
        quiet = energy < 1e-6 * m  # near silence: nothing to compare
        score = np.where(quiet, 0.0, corr / (c_norm * np.sqrt(np.where(quiet, 1.0, energy))))
        best = int(np.argmax(score))
        away = np.ones(len(score), bool)
        away[max(0, best - sr): best + sr] = False
        nxt = float(score[away].max()) if away.any() else 0.0
        return {"at": best / sr, "score": round(float(score[best]), 3), "next": round(max(nxt, 0.0), 3)}


def confident(m: dict) -> bool:
    return m["at"] is not None and m["score"] >= MIN_SCORE and m["score"] - m["next"] >= MIN_MARGIN


def per_second(short: np.ndarray, hop: float = 0.1) -> np.ndarray:
    """Short-term loudness (one value per `hop`) -> one value per second, as tracklist.highlights averages it."""
    loud = np.where(np.isfinite(short), short, -70.0).astype(float)
    k = int(round(1 / hop))
    return loud[: len(loud) // k * k].reshape(-1, k).mean(axis=1)


def video_offset(result: dict) -> float | None:
    """The --offset the analysis was run with, worked back from a clip suggestion's video time (not saved itself)."""
    from . import tracklist as tl

    lead = tl.anchor(result)
    for h in result.get("highlights", []):
        if h.get("video"):
            v = tl._seconds(h["video"].split("-")[0])
            if v > 0:  # a start clamped at 0:00 says nothing
                return v - (tl._seconds(h["start"]) - lead)
    return None


def parse_at(items: list[str] | None) -> dict[int, float]:
    """--at 2=41:20 3=1:02:05 -> {2: 2480.0, 3: 3725.0} (clip number: its start in the video)."""
    from . import tracklist as tl

    out = {}
    for item in items or []:
        m = re.fullmatch(r"\s*(\d+)\s*=\s*([\d:.]+)\s*", item)
        if not m:
            raise SystemExit(f"--at wants CLIP=TIME, e.g. 2=41:20, not {item!r}")
        out[int(m[1])] = tl.parse_offset(m[2])
    return out


def clip_files(items: list[str]) -> list[Path]:
    """The clips given: video files, or folders of them (in name order)."""
    out = []
    for item in items:
        p = Path(item).expanduser()
        if p.is_dir():
            out += sorted(f for f in p.iterdir() if f.suffix.lower() in VIDEO_EXTS and not f.name.startswith("."))
        elif p.is_file():
            out.append(p)
        else:
            raise SystemExit(f"No clip at {p}")
    if not out:
        raise SystemExit("No clips found (video files: " + ", ".join(sorted(VIDEO_EXTS)) + ")")
    return out


def _rise(recording: Path, at: float) -> float | None:
    """How far the clip's first 8 s are above the 20 s before it in the recording (LU), as for the suggestions."""
    from .analysis import loudness

    if at < 20:
        return None  # the clip starts in the mix's first 20 s: nothing before it to compare
    # short-term loudness: one 3 s window every 0.1 s, counted from where the window starts (as in the full analysis)
    y = decode(recording, 48000, 2, at - 20, 31)
    sec = per_second(loudness(y, 48000)[0])
    if len(sec) < 28:
        return None  # the recording ends within the clip
    return round(float(sec[20:28].mean() - sec[:20].mean()), 1)


def own_sound(path: Path) -> dict:
    """The clip's own LUFS (as the suggestions measure it), energy, genre and moods."""
    from .analysis import analyze_file, loudness

    y = decode(path, 48000, 2)
    sec = per_second(loudness(y, 48000)[0])
    out = {"lufs": round(float(sec.mean()), 1) if len(sec) else None, "length": round(len(y) / 48000, 1)}
    with tempfile.TemporaryDirectory() as tmp:  # the models read audio files, not videos: its sound as a WAV
        wav = Path(tmp) / f"{path.stem}.wav"
        subprocess.run([_ffmpeg(), "-v", "error", "-i", str(path), "-map", "0:a:0", "-c:a", "pcm_s16le", str(wav)],
                       check=True, capture_output=True)
        res = analyze_file(str(wav), timeline=False)
    moods = res.get("mood") or {}
    if {"party", "aggressive"} <= set(moods):
        out["energy"] = round((moods["party"] + moods["aggressive"]) / 2, 2)  # as tracklist.mood_energy
    out["genre"] = [g["genre"] for g in res.get("dj_genre") or []]
    out["moods"] = dict(sorted(moods.items(), key=lambda kv: -kv[1]))
    if res.get("bpm"):
        out["bpm"] = res["bpm"]
    if res.get("styles_error"):
        out["error"] = res["styles_error"]
    return out


def describe(analysis: Path, clips: list[Path], recording: Path | None = None, offset: float | None = None,
             at: dict[int, float] | None = None, log=lambda msg: print(msg, file=sys.stderr)) -> dict:
    from . import tracklist as tl

    result = json.loads(analysis.read_text(encoding="utf-8"))
    if result.get("type") != "dj_mix":
        raise SystemExit(f"{analysis.name} isn't a mix's analysis")
    recording = recording or Path(result.get("file") or "")
    if not recording.is_file():
        raise SystemExit(f"No recording at {recording} (give it with --recording)")
    offset = video_offset(result) if offset is None else offset
    lead, at = tl.anchor(result), at or {}
    tracks = (result.get("tracklist") or {}).get("tracks", [])
    log(f"reading the sound of {recording.name}")
    rec = Recording(decode(recording))
    out = []
    for n, path in enumerate(clips, 1):
        log(f"clip {n}: {path.name}")
        own = own_sound(path)
        c = {"n": n, "file": path.name, **own}
        if n in at:
            v = at[n]
            c["start_s"] = v - offset + lead if offset is not None else v  # no offset: the time is the mix's
            c["placed"] = "by hand"
        else:
            m = rec.match(decode(path))
            c["match"] = {"score": m["score"], "next": m["next"]}
            if confident(m):
                c["start_s"], c["placed"] = m["at"], "by sound"
        if "start_s" in c:
            s = c["start_s"]
            c["start"], c["end"] = fmt_time(s), fmt_time(s + own["length"])
            if offset is not None:
                c["video"] = f"{fmt_time(tl.to_video(s, lead, offset))}-{fmt_time(tl.to_video(s + own['length'], lead, offset))}"
            c["rise_lu"] = _rise(recording, s)
            playing = [t for t in tracks if tl._seconds(t["start"]) <= s + own["length"] / 2]
            if playing:
                t = playing[-1]
                c["track"] = " - ".join(x for x in (t.get("artist"), t.get("title")) if x)
            c["start_s"] = round(s, 2)
        out.append(c)
    return {"analysis": str(analysis), "recording": str(recording), "offset": offset, "clips": out}


def line(c: dict, cli: bool = True) -> str:
    """One clip as a "Clip suggestions" line, then its own genre and moods. cli: how to place an unfound clip
    (--at, else the page's time box)."""
    head = f"Clip {c['n']} ({c['file']}): "
    if "start" not in c:
        m = c.get("match") or {}
        how = f"give its start in the video with --at {c['n']}=M:SS" if cli else "place it by hand on the page"
        return (head + f"no confident match in the recording (best {m.get('score', 0):.2f}, elsewhere "
                f"{m.get('next', 0):.2f}): {how}")
    span = c.get("video") or f"{c['start']}-{c['end']} (mix time; no video offset)"
    rise = f", rises {c['rise_lu']:+.1f} LU" if c.get("rise_lu") is not None and c["rise_lu"] > 1 else ""
    energy = f", energy {c['energy']:.2f}" if c.get("energy") is not None else ""
    lufs = f"  {c['lufs']} LUFS" if c.get("lufs") is not None else ""
    text = head + span + lufs + rise + energy + (f"  ({c['track']})" if c.get("track") else "")
    if c.get("placed") == "by hand":
        text += "  [placed by hand]"
    sound = []
    if c.get("genre"):
        sound.append("genre " + " / ".join(c["genre"]))
    if c.get("moods"):
        sound.append("moods " + ", ".join(f"{k} {v:.2f}" for k, v in list(c["moods"].items())[:3]))
    if c.get("bpm"):
        sound.append(f"{c['bpm']} BPM")
    if c.get("error"):
        sound.append(f"no genre or moods: {c['error']}")
    return text + ("\n    " + " · ".join(sound) if sound else "")


def save(analysis: Path, report: dict) -> None:
    """Keep the report in the analysis' JSON ("described_clips"), where the page shows it with the mix."""
    data = json.loads(analysis.read_text(encoding="utf-8"))
    data["described_clips"] = {**{k: v for k, v in report.items() if k != "analysis"},
                               "lines": [line(c, cli=False) for c in report["clips"]]}  # the page's "Copy for posts"
    tmp = analysis.with_name(analysis.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, analysis)


def main(args) -> None:
    from . import tracklist as tl

    analysis = Path(args.analysis).expanduser()
    if not analysis.is_file():
        raise SystemExit(f"No analysis at {analysis}")
    report = describe(analysis, clip_files(args.files),
                      Path(args.recording).expanduser() if args.recording else None,
                      tl.parse_offset(args.offset) if args.offset is not None else None, parse_at(args.at))
    if args.save:
        save(analysis, report)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    where = "video" if report["offset"] is not None else "mix (add --offset for video)"
    print(f"Your clips ({where} times)")
    for c in report["clips"]:
        print("  " + line(c).replace("\n", "\n  "))
