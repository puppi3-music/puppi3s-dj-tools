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
STILL_FPS = 2  # frames a second looked at when picking thumbnails
STILL_W = 160  # ... each shrunk to this width to score it (the saved still is cut from the full-size frame)
STILLS = 3  # suggested thumbnails per place each is posted
MIN_STILLS = 2  # ... and never fewer while the video has frames
# Where thumbnails go: (what the page calls it, saved size, file name ending added to the clip's or video's name)
MEDIA = {
    "post": ("Ghost post (and its Bluesky / Threads card)", (1200, 630), "-thumb.jpg"),
    "cover": ("Instagram / TikTok cover", (1080, 1920), "-cover.jpg"),
    "youtube": ("YouTube thumbnail", (1280, 720), "-thumb-youtube.jpg"),
}
CLIP_MEDIA = ("post", "cover")


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


def video_size(path: Path) -> tuple[int, int]:
    """A video's picture size as shown (width, height): a phone video's rotation turned, as ffmpeg decodes it."""
    res = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b", res.stderr)
    if not m:
        raise ValueError(f"{path.name} has no picture")
    w, h = int(m[1]), int(m[2])
    turned = re.search(r"rotat\w* of (-?\d+)|rotate\s*:\s*(-?\d+)", res.stderr)
    if turned and int(turned[1] or turned[2]) % 180:
        w, h = h, w
    return w, h


def frames(path: Path, start: float = 0.0, length: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """A video's frames, STILL_FPS a second from `start`, small (STILL_W wide, its own shape):
    (times in seconds, (n, height, width, 3) uint8 RGB)."""
    w, h = video_size(path)
    fw, fh = STILL_W, max(2, round(STILL_W * h / w / 2) * 2)
    cmd = [_ffmpeg(), "-v", "error", *(["-ss", f"{start:.3f}"] if start else []), "-i", str(path),
           *(["-t", f"{length:.3f}"] if length else []), "-map", "0:v:0",
           "-vf", f"fps={STILL_FPS},scale={fw}:{fh},format=rgb24", "-f", "rawvideo", "pipe:1"]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode:
        raise ValueError(f"can't read the picture of {path.name}: {res.stderr.decode(errors='replace').strip()[-200:]}")
    img = np.frombuffer(res.stdout, dtype=np.uint8)
    img = img[: len(img) // (fw * fh * 3) * fw * fh * 3].reshape(-1, fh, fw, 3)
    return start + np.arange(len(img)) / STILL_FPS, img


def _grey(img: np.ndarray) -> np.ndarray:
    return img.astype(np.float64).mean(axis=3) if img.ndim == 4 else img.astype(np.float64)


def _detail(g: np.ndarray) -> np.ndarray:
    """|Laplacian| of grey frames (n, h, w), edges padded: where the picture has detail."""
    lap = np.abs(4 * g[:, 1:-1, 1:-1] - g[:, :-2, 1:-1] - g[:, 2:, 1:-1] - g[:, 1:-1, :-2] - g[:, 1:-1, 2:])
    return np.pad(lap, ((0, 0), (1, 1), (1, 1)), mode="edge")


def motion(img: np.ndarray) -> np.ndarray:
    """How much each frame differs from the frames either side (the bigger): blur, or a cut."""
    g = _grey(img)
    d = np.abs(np.diff(g, axis=0)).reshape(len(g) - 1, -1).mean(axis=1) if len(g) > 1 else np.zeros(0)
    if not len(d):
        return np.zeros(len(g))
    return np.maximum(np.r_[d, d[-1:]], np.r_[d[:1], d])


def colourfulness(img: np.ndarray) -> np.ndarray:
    """Hasler & Suesstrunk's colourfulness of each RGB frame (0 grey, ~50 colourful, 100+ vivid)."""
    if img.ndim != 4:
        return np.zeros(len(img))
    f = img.astype(np.float64).reshape(len(img), -1, 3)
    rg, yb = f[..., 0] - f[..., 1], (f[..., 0] + f[..., 1]) / 2 - f[..., 2]
    return np.hypot(rg.std(axis=1), yb.std(axis=1)) + 0.3 * np.hypot(rg.mean(axis=1), yb.mean(axis=1))


def frame_scores(img: np.ndarray, strict: bool = True, moving: np.ndarray | None = None) -> np.ndarray:
    """How good each frame (or crop) is as a thumbnail, small among others in a feed: sharp, colourful, with
    contrast, not mid-cut (`moving`: motion() of the whole frames, else worked out here). strict: -inf for
    frames that can't be one (dark, blown out, a flat title card); otherwise only black is out."""
    g = _grey(img)
    sharp = np.log1p(_detail(g)[:, 1:-1, 1:-1].reshape(len(g), -1).var(axis=1))
    mean, contrast = g.reshape(len(g), -1).mean(axis=1), g.reshape(len(g), -1).std(axis=1)
    moving = motion(img) if moving is None else moving
    score = sharp + contrast / 50 + colourfulness(img) / 40 - moving / 20
    if not strict:
        return np.where(mean < 8, -np.inf, score)
    return np.where((mean < 30) | (mean > 225) | (contrast < 12), -np.inf, score)


def crop_boxes(img: np.ndarray, aspect: float) -> list[tuple[int, int, int, int]]:
    """Per frame, the window of shape `aspect` (width / height), as big as fits, slid to where the most detail is
    (a little in favour of the middle): (x, y, w, h) in the frame's pixels."""
    n, H, W = img.shape[:3]
    det = _detail(_grey(img))
    if W / H > aspect:  # wider than wanted: slide sideways
        cw, ch, along = max(2, round(H * aspect)), H, det.sum(axis=1)
    else:
        cw, ch, along = W, max(2, round(W / aspect)), det.sum(axis=2)
    size = cw if W / H > aspect else ch
    cs = np.concatenate((np.zeros((n, 1)), np.cumsum(along, axis=1)), axis=1)
    win = cs[:, size:] - cs[:, :-size]  # (n, positions)
    pos = np.arange(win.shape[1])
    mid = (win.shape[1] - 1) / 2
    win = win * (1 - 0.15 * np.abs(pos - mid) / max(mid, 1))
    best = win.argmax(axis=1)
    return [(int(b), 0, cw, ch) if W / H > aspect else (0, int(b), cw, ch) for b in best]


def pick_frames(times: np.ndarray, img: np.ndarray, aspect: float, k: int, apart: float,
                ends: float = 0.0) -> list[tuple[float, tuple[float, float, float, float]]]:
    """The k best thumbnails of shape `aspect`, `apart` seconds apart and `ends` seconds clear of the start and end
    (fades): [(time, crop as fractions of the frame: x, y, w, h)]. Fewer than MIN_STILLS: closer together, then the
    ends allowed, then dim or flat frames allowed."""
    if not len(times):
        return []
    n, H, W = img.shape[:3]
    boxes = crop_boxes(img, aspect)
    crops = np.stack([img[i, y:y + h, x:x + w] for i, (x, y, w, h) in enumerate(boxes)])
    moving = motion(img)
    got = []
    for strict, gap, edge in [(True, apart, ends), (True, apart / 3, ends), (True, apart / 3, 0.0),
                              (False, apart / 3, 0.0)]:
        score = frame_scores(crops, strict, moving)
        if edge and len(times) > 4:
            score[(times < times[0] + edge) | (times > times[-1] - edge)] = -np.inf
        got = best_frames(times, score, k, gap)
        if len(got) >= min(MIN_STILLS, k):
            break
    out = []
    for t in got:
        x, y, w, h = boxes[int(np.argmin(np.abs(times - t)))]
        out.append((t, (x / W, y / H, w / W, h / H)))
    return out


def best_frames(times: np.ndarray, score: np.ndarray, k: int = STILLS, apart: float = 3.0) -> list[float]:
    """The k best-scoring times at least `apart` seconds from each other, in time order."""
    out = []
    for i in np.argsort(-score, kind="stable"):
        if not np.isfinite(score[i]):
            break
        if all(abs(times[i] - t) >= apart for t in out):
            out.append(float(times[i]))
            if len(out) == k:
                break
    return sorted(out)


def save_still(video: Path, t: float, dest: Path, crop=(0.0, 0.0, 1.0, 1.0), size: tuple[int, int] | None = None) -> None:
    """The full-size frame at `t` seconds as a JPEG: cut to `crop` (fractions x, y, w, h), sized to `size`."""
    x, y, w, h = crop
    vf = f"crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f}"
    if size:
        vf += f",scale={size[0]}:{size[1]}:force_original_aspect_ratio=increase,crop={size[0]}:{size[1]}"
    res = subprocess.run([_ffmpeg(), "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video), "-map", "0:v:0",
                          "-vf", vf, "-frames:v", "1", "-q:v", "2", str(dest)], capture_output=True)
    if res.returncode or not dest.is_file():
        raise ValueError(f"can't save a still of {video.name} at {fmt_time(t)}: "
                         f"{res.stderr.decode(errors='replace').strip()[-200:]}")


def _aspect(media: str) -> float:
    w, h = MEDIA[media][1]
    return w / h


def clip_stills(clip: Path, folder: Path, n: int) -> dict[str, list[dict]]:
    """Suggested thumbnails for clip n, per place it's posted (CLIP_MEDIA): its best frames for that shape, spread
    through the clip (not its first or last half second: fades), saved in `folder`.
    {media: [{"t": seconds into the clip, "file": the still's name, "dest": where "Use this" copies it}]}."""
    times, img = frames(clip)
    if not len(times):
        raise ValueError(f"{clip.name} has no picture")
    apart = times[-1] / (STILLS + 1)  # different moments to pick from
    out = {}
    for media in CLIP_MEDIA:
        out[media] = []
        for i, (t, crop) in enumerate(pick_frames(times, img, _aspect(media), STILLS, apart, ends=0.5), 1):
            name = f"clip-{n}-{media}-{i}.jpg"
            save_still(clip, t, folder / name, crop, MEDIA[media][1])
            out[media].append({"t": round(t, 1), "file": name, "dest": str(clip.with_name(clip.stem + MEDIA[media][2]))})
    return out


def youtube_stills(video: Path, result: dict, folder: Path) -> list[dict]:
    """Suggested YouTube thumbnails from the finished video: the best frame in each of the top clip suggestions
    (the mix's biggest moments), then at even points through the video until there are STILLS.
    [{"t": seconds into the video, ...}]."""
    from . import tracklist as tl

    spans = []
    for h in sorted(result.get("highlights", []), key=lambda h: h.get("rank", 99)):
        if h.get("video"):
            a, b = (tl._seconds(x) for x in h["video"].split("-"))
            spans.append((a, max(b - a, 4.0)))
    length = video_length(video) or tl._seconds(str(result.get("duration") or "10:00"))
    spans += [(length * (i + 1) / 9, 8.0) for i in range(8)]  # fallbacks: 8 even points through the video
    gap = min(5.0, length / (STILLS + 1))  # different moments, even in a short video
    out = []
    for a, span in spans:
        if len(out) == STILLS:
            break
        times, img = frames(video, a, span)
        got = pick_frames(times, img, _aspect("youtube"), 1, 0.0)
        if not got or any(abs(got[0][0] - x["t"]) < gap for x in out):
            continue
        t, crop = got[0]
        name = f"youtube-{len(out) + 1}.jpg"
        save_still(video, t, folder / name, crop, MEDIA["youtube"][1])
        out.append({"t": round(t, 1), "file": name, "dest": str(video.with_name(f"{video.stem}{MEDIA['youtube'][2]}"))})
    return out


def video_length(video: Path) -> float | None:
    """A video's length in seconds, from ffmpeg's report on it (None if it doesn't say)."""
    res = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(video)], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", res.stderr)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None


def stills_folder(analysis: Path) -> Path:
    """Where an analysis' suggested thumbnails go: stills/<its name>/ beside it (emptied for each new set)."""
    folder = analysis.parent / "stills" / analysis.stem
    folder.mkdir(parents=True, exist_ok=True)
    for f in folder.glob("*.jpg"):
        f.unlink()
    return folder


def describe(analysis: Path, clips: list[Path], recording: Path | None = None, offset: float | None = None,
             at: dict[int, float] | None = None, video: Path | None = None, stills: bool = False,
             log=lambda msg: print(msg, file=sys.stderr)) -> dict:
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
    folder = stills_folder(analysis) if stills else None
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
        if folder:
            try:
                c["stills"] = clip_stills(path, folder, n)
            except ValueError as e:  # no picture (an audio-only clip): the rest of its line still stands
                c["stills_error"] = str(e)
        out.append(c)
    report = {"analysis": str(analysis), "recording": str(recording), "offset": offset, "clips": out,
              "media": {k: v[0] for k, v in MEDIA.items()}}
    if folder and video:
        log(f"thumbnails for YouTube from {video.name}")
        report["video"] = str(video)
        report["youtube_stills"] = youtube_stills(video, result, folder)
    return report


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
    video = Path(args.video).expanduser() if args.video else None
    if video and not video.is_file():
        raise SystemExit(f"No video at {video}")
    report = describe(analysis, clip_files(args.files),
                      Path(args.recording).expanduser() if args.recording else None,
                      tl.parse_offset(args.offset) if args.offset is not None else None, parse_at(args.at),
                      video, stills=bool(args.save or video))
    if args.save:
        save(analysis, report)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    where = "video" if report["offset"] is not None else "mix (add --offset for video)"
    print(f"Your clips ({where} times)")
    for c in report["clips"]:
        print("  " + line(c).replace("\n", "\n  "))
    if any(c.get("stills") for c in report["clips"]) or report.get("youtube_stills"):
        print(f"Thumbnails suggested (pick them on the page): {analysis.parent / 'stills' / analysis.stem}")
