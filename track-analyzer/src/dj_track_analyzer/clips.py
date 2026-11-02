"""`clips` command: cut the clip suggestions of `analyze --highlights --offset` out of the finished video.

Without --pick: quick 720p previews of every suggestion in a temporary folder, to watch and choose from.
With --pick: the chosen clips at full quality (hardware H.264 on a Mac), next to the video.
Times come from the analysis JSON (video times, so it must have been run with --offset)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from functools import cache
from pathlib import Path

import numpy as np

from .tracklist import _seconds, fmt_time


def _tool(name: str) -> str:
    from .analysis import find_tool

    return find_tool(name) or name  # on PATH or Homebrew's / winget's folders (analysis.TOOL_DIRS)

PREVIEW_HEIGHT, PREVIEW_BITRATE = 720, "4M"
MOTION_W, MOTION_H, MOTION_FPS = 64, 36, 2  # movement: frame-to-frame change of a tiny greyscale copy


@cache
def _encoder() -> tuple[str, ...]:
    """Hardware H.264 (VideoToolbox) when this ffmpeg has it, else software x264."""
    out = subprocess.run([_tool("ffmpeg"), "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return ("-c:v", "h264_videotoolbox") if "h264_videotoolbox" in out else ("-c:v", "libx264", "-preset", "fast")


def _duration(video: Path) -> float:
    out = subprocess.run([_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        raise SystemExit(f"Couldn't read {video} with ffprobe: {out.stderr.strip() or 'no duration'}")


def suggestions(analysis: Path) -> list[dict]:
    """The clip suggestions in video time, numbered as in the summary."""
    try:
        result = json.loads(analysis.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"Couldn't read the analysis {analysis}: {e}")
    hl = result.get("highlights") or []
    if not hl:
        raise SystemExit(f"{analysis} has no clip suggestions: run analyze with --highlights 5 --offset M:SS")
    if not all("video" in h for h in hl):
        raise SystemExit(f"{analysis} has clip times for the recording, not the video: run analyze with --offset")
    if not all("rank" in h for h in hl):
        print(f"Note: {analysis.name} is from an older analyze without ranked suggestions; run analyze again "
              "with --highlights 5 for five ranked ideas", file=sys.stderr)
    out = []
    for i, h in enumerate(hl, 1):
        a, b = h["video"].split("-")
        out.append({"n": i, "start": _seconds(a), "end": _seconds(b), "rank": h.get("rank"), "track": h.get("track")})
    return out


def parse_pick(item: str, clips: list[dict]) -> dict:
    """--pick item: a suggestion number ('1') or an own range ('7:35-8:15')."""
    if item.isdigit():
        n = int(item)
        found = next((c for c in clips if c["n"] == n), None)
        if not found:
            raise SystemExit(f"--pick {item}: there are suggestions 1-{len(clips)}")
        return found
    m = re.fullmatch(r"(\d+(?::\d{1,2}){0,2})-(\d+(?::\d{1,2}){0,2})", item)
    if not m:
        raise SystemExit(f"--pick takes suggestion numbers (1 4) or ranges (7:35-8:15), not {item!r}")
    a, b = _seconds(m[1]), _seconds(m[2])
    if b <= a:
        raise SystemExit(f"--pick {item}: the end must come after the start")
    return {"n": None, "start": a, "end": b, "rank": None, "track": None}


def _cut(video: Path, start: float, end: float, dest: Path, height: int, bitrate: str, audio: str,
         measure: bool = False) -> float | None:
    """Cut [start, end) of the video to dest. With measure, the same decode also returns how much moves on screen:
    the mean change between frames of a tiny greyscale copy (camera footage of a DJ: higher = livelier)."""
    hwdec = ["-hwaccel", "videotoolbox"] if sys.platform == "darwin" else []
    clip = ["-c:a", "aac", "-b:a", audio, *_encoder(), "-b:v", bitrate, "-movflags", "+faststart", str(dest)]
    if measure:
        graph = (f"[0:v]split[a][b];[a]scale=-2:{height},fps=30[v];"
                 f"[b]fps={MOTION_FPS},scale={MOTION_W}:{MOTION_H}:flags=area,format=gray[m]")
        outs = ["-filter_complex", graph, "-map", "[v]", "-map", "0:a?", *clip,
                "-map", "[m]", "-f", "rawvideo", "pipe:1"]
    else:
        outs = ["-vf", f"scale=-2:{height},fps=30", *clip]
    cmd = [_tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", *hwdec, "-i", str(video),
           *outs]
    done = subprocess.run(cmd, capture_output=True)
    if done.returncode:
        dest.unlink(missing_ok=True)
        raise SystemExit(f"ffmpeg failed on {dest.name}:\n{done.stderr.decode(errors='replace').strip()}")
    if not measure:
        return None
    frames = np.frombuffer(done.stdout, np.uint8).reshape(-1, MOTION_H, MOTION_W).astype(np.float32)
    return float(np.abs(np.diff(frames, axis=0)).mean()) if len(frames) > 1 else 0.0


def rank_with_movement(clips: list[dict]) -> None:
    """Overall rank = mean of the audio rank and the movement rank among these clips (ties: audio first).
    On a test set the audio's #2 pick was one of the stillest stretches of the video; movement lifts the lively ones."""
    by_move = sorted(clips, key=lambda c: -c["movement"])
    for i, c in enumerate(by_move, 1):
        c["move_rank"] = i
    audio = lambda c: c.get("rank") or len(clips)  # noqa: E731
    for i, c in enumerate(sorted(clips, key=lambda c: ((audio(c) + c["move_rank"]) / 2, audio(c))), 1):
        c["overall"] = i


def _stamp(sec: float) -> str:
    """7:35 -> '7m35s', 1:07:35 -> '1h07m35s' (file names can't hold ':' on every system)."""
    *h, m, s = fmt_time(sec).split(":")
    return (f"{h[0]}h" if h else "") + f"{m}m{s}s"


def _check_fits(clips: list[dict], length: float) -> None:
    """All clips start inside the video (checked before cutting any, so nothing is left half done)."""
    late = [c for c in clips if c["start"] >= length]
    if late:
        raise SystemExit(f"{', '.join(_label(c) for c in late)}: starts after the video ends ({fmt_time(length)}). "
                         "Wrong video or offset?")


PREVIEW_NAME = re.compile(r"(#\d+ )?\.?clip-\d+.*\.mp4")  # "#2 clip-4 7m35s.mp4", and ".clip-4.mp4" left by a stopped run


def _label(c: dict) -> str:
    span = f"{fmt_time(c['start'])}-{fmt_time(c['end'])}"
    rank = f"  #{c['rank']} best" if c.get("rank") else ""
    return f"{span}{rank}" + (f"  ({c['track']})" if c.get("track") else "")


def main(args) -> None:
    from .analysis import find_tool, install_hint

    if not find_tool("ffmpeg") or not find_tool("ffprobe"):

        raise SystemExit(f"clips needs ffmpeg: {install_hint('ffmpeg')}")
    video, analysis = Path(args.video).expanduser(), Path(args.analysis).expanduser()
    if not video.is_file():
        raise SystemExit(f"No video at {video}")
    clips = suggestions(analysis)
    length = _duration(video)

    if not args.pick:
        tmp = Path("/tmp") if Path("/tmp").is_dir() else Path(tempfile.gettempdir())  # /tmp: easy to find, emptied on restart
        folder = Path(args.previews or tmp / "clip-previews").expanduser() / analysis.stem
        _check_fits(clips, length)
        print(f"Previews ({PREVIEW_HEIGHT}p) in {folder}")
        if args.dry_run:
            for c in clips:
                print(f"  clip-{c['n']}: {_label(c)}")
            print("(dry run: nothing written)")
            return
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.iterdir():
            if PREVIEW_NAME.fullmatch(old.name):
                old.unlink()  # earlier previews of this analysis
        for c in clips:
            c["end"] = min(c["end"], length)
            print(f"  cutting clip-{c['n']} {fmt_time(c['start'])}", file=sys.stderr)
            c["file"] = folder / f".clip-{c['n']}.mp4"
            c["movement"] = _cut(video, c["start"], c["end"], c["file"], PREVIEW_HEIGHT, PREVIEW_BITRATE, "160k",
                                 measure=True)
        rank_with_movement(clips)
        print(f"  {'file':<26} {'video':<13} {'audio':>5} {'movement':>12} {'overall':>7}  track")
        for c in sorted(clips, key=lambda c: c["overall"]):
            name = f"#{c['overall']} clip-{c['n']} {_stamp(c['start'])}.mp4"
            os.replace(c["file"], folder / name)
            span = f"{fmt_time(c['start'])}-{fmt_time(c['end'])}"
            print(f"  {name:<26} {span:<13} {'#' + str(c.get('rank') or '-'):>5} "
                  f"{c['movement']:>6.2f} (#{c['move_rank']}) {'#' + str(c['overall']):>7}  {c.get('track') or ''}")
        print("Watch them, then: clips … --pick N [N] (clip numbers, or own ranges like 7:35-8:15)")
        return
    folder = Path(args.out).expanduser() if args.out else video.parent
    picks = [parse_pick(p, clips) for p in args.pick]
    todo = []
    for c in picks:  # named by suggestion number (or range), so later --pick runs don't collide
        tag = c["n"] if c["n"] else f"{_stamp(c['start'])}-{_stamp(c['end'])}"
        todo.append((c, folder / f"{video.stem}-clip-{tag}.mp4"))
    if len({d for _, d in todo}) < len(todo):
        raise SystemExit("--pick names the same clip twice")
    height, bitrate, audio = args.height, args.bitrate, "320k"
    print(f"Clips ({args.height}p, {args.bitrate}) in {folder}")
    _check_fits(picks, length)
    clash = [d for _, d in todo if d.exists()]
    if clash and not args.force:
        raise SystemExit("Already there (--force to replace): " + ", ".join(d.name for d in clash))

    for c, dest in todo:
        end = min(c["end"], length)
        print(f"  {dest.name}: {_label({**c, 'end': end})}")
        if not args.dry_run:
            folder.mkdir(parents=True, exist_ok=True)
            _cut(video, c["start"], end, dest, height, bitrate, audio)
    if args.dry_run:
        print("(dry run: nothing written)")
