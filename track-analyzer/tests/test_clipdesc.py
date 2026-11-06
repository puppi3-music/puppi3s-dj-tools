"""`clips describe`: finding an editor's clip in the recording by its sound, and the line it gets."""
from __future__ import annotations

import json
import shutil
import subprocess

import numpy as np
import pytest

from dj_track_analyzer import clipdesc

SR = clipdesc.MATCH_SR


def _music(seconds: float, seed: int = 1) -> np.ndarray:
    """Noise with a changing tone colour: no two stretches alike, like a mix (and unlike a loop)."""
    rng = np.random.default_rng(seed)
    y = rng.standard_normal(int(seconds * SR))
    return (np.convolve(y, np.hanning(9), "same") * 0.05).astype(np.float32)


def test_finds_a_clip_through_volume_and_noise():
    rec = _music(600)
    start = 321.37
    clip = rec[int(start * SR): int((start + 40) * SR)] * 0.4  # the editor turned it down...
    clip = clip + np.random.default_rng(2).standard_normal(len(clip)).astype(np.float32) * 0.003  # ...and re-encoded
    m = clipdesc.Recording(rec).match(clip)
    assert m["at"] == pytest.approx(start, abs=1 / SR)
    assert clipdesc.confident(m) and m["score"] > 0.9


def test_a_foreign_clip_is_not_placed():
    m = clipdesc.Recording(_music(600)).match(_music(40, seed=9))
    assert not clipdesc.confident(m) and m["score"] < 0.2


def test_a_repeated_stretch_is_not_confident():
    part = _music(40)
    rec = np.concatenate([_music(100, 3), part, _music(100, 4), part, _music(100, 5)])
    m = clipdesc.Recording(rec).match(part)
    assert m["score"] > 0.9 and m["next"] > 0.9 and not clipdesc.confident(m)


def test_silence_and_short_clips():
    rec = np.concatenate([np.zeros(60 * SR, np.float32), _music(120)])
    assert not clipdesc.confident(clipdesc.Recording(rec).match(np.zeros(40 * SR, np.float32)))
    assert clipdesc.Recording(rec).match(_music(0.5))["at"] is None


def test_parse_at():
    assert clipdesc.parse_at(["2=41:20", " 3 = 1:02:05 "]) == {2: 2480.0, 3: 3725.0}
    assert clipdesc.parse_at(None) == {}
    with pytest.raises(SystemExit):
        clipdesc.parse_at(["41:20"])


def test_video_offset_from_the_suggestions():
    res = {"tracklist": {"tracks": [{"start": "0:30"}]},
           "highlights": [{"start": "0:40", "video": "0:00-0:30"}, {"start": "10:30", "video": "10:14-10:54"}]}
    assert clipdesc.video_offset(res) == 14.0  # the first is clamped at 0:00, so the second tells
    assert clipdesc.video_offset({"highlights": [{"start": "1:00"}]}) is None


def test_clip_files(tmp_path):
    for name in ("b.mp4", "a.MOV", ".hidden.mp4", "notes.txt"):
        (tmp_path / name).write_bytes(b"")
    assert [p.name for p in clipdesc.clip_files([str(tmp_path)])] == ["a.MOV", "b.mp4"]
    with pytest.raises(SystemExit):
        clipdesc.clip_files([str(tmp_path / "missing.mp4")])


def test_line():
    c = {"n": 1, "file": "c1.mp4", "start": "41:20", "end": "42:00", "video": "41:34-42:14", "lufs": -8.9,
         "rise_lu": 3.1, "energy": 0.64, "track": "A - B", "genre": ["Tech House", "House"],
         "moods": {"party": 0.71, "aggressive": 0.58, "happy": 0.4, "sad": 0.01}, "bpm": 128.0}
    assert clipdesc.line(c) == ("Clip 1 (c1.mp4): 41:34-42:14  -8.9 LUFS, rises +3.1 LU, energy 0.64  (A - B)\n"
                                "    genre Tech House / House · moods party 0.71, aggressive 0.58, happy 0.40 · 128.0 BPM")
    miss = clipdesc.line({"n": 2, "file": "c2.mp4", "match": {"score": 0.31, "next": 0.29}})
    assert "no confident match" in miss and "--at 2=M:SS" in miss


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_describe_end_to_end(tmp_path, monkeypatch):
    """A real recording and a clip cut from it as AAC video sound: placed, timed, named, saved."""
    rng = np.random.default_rng(5)
    sr = 44100
    n = 180 * sr
    y = np.convolve(rng.standard_normal(n), np.hanning(15), "same") * 0.03
    y[: 60 * sr] *= 0.3  # quieter first minute: the clip at 1:00 rises out of it
    rec = tmp_path / "mix.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "f64le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0",
                    "-ac", "2", str(rec)], input=y.astype("<f8").tobytes(), check=True)
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-ss", "62.5", "-t", "30", "-i", str(rec), "-af", "volume=0.5",
                    "-c:a", "aac", "-b:a", "192k", str(clip)], check=True)
    analysis = tmp_path / "mix.json"
    analysis.write_text(json.dumps({
        "type": "dj_mix", "file": str(rec), "first_sound_seconds": 0.0,
        "tracklist": {"tracks": [{"start": "0:10", "artist": "One", "title": "First"},
                                 {"start": "1:15", "artist": "Two", "title": "Second"}]},
        "highlights": [{"start": "1:00", "end": "1:40", "video": "1:05-1:45"}]}))
    monkeypatch.setattr(clipdesc, "own_sound", lambda p: {"lufs": -20.0, "length": 30.0, "energy": 0.5,
                                                          "genre": ["Techno"], "moods": {"party": 0.6}})
    report = clipdesc.describe(analysis, [clip], log=lambda m: None)
    c = report["clips"][0]
    assert report["offset"] == 15.0
    assert c["placed"] == "by sound" and c["start_s"] == pytest.approx(62.5, abs=0.05)
    assert c["video"] == "1:08-1:38" and c["track"] == "Two - Second"  # the one playing at the clip's middle
    assert c["rise_lu"] > 5

    hand = clipdesc.describe(analysis, [clip], at={1: 100.0}, log=lambda m: None)["clips"][0]
    assert hand["placed"] == "by hand" and hand["start"] == "1:35"  # video 1:40 = mix 1:35 (offset 15, lead 10)
    clipdesc.save(analysis, report)
    saved = json.loads(analysis.read_text())
    assert saved["described_clips"]["clips"][0]["video"] == "1:08-1:38" and saved["highlights"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_rise_as_the_suggestions_measure_it(tmp_path):
    from dj_track_analyzer.analysis import loudness

    sr = 48000
    gain = np.repeat(np.linspace(0.01, 0.2, 120), sr)[:, None]
    y = (np.random.default_rng(3).standard_normal((120 * sr, 2)) * gain).astype(np.float32)
    rec = tmp_path / "r.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(sr), "-ac", "2", "-i", "pipe:0", str(rec)],
                   input=y.tobytes(), check=True)
    sec = clipdesc.per_second(loudness(y, sr)[0])  # what tracklist.highlights works on
    for s in (30, 61):
        assert clipdesc._rise(rec, float(s)) == round(float(sec[s: s + 8].mean() - sec[s - 20: s].mean()), 1)
    assert clipdesc._rise(rec, 10.0) is None


def test_page_job_arguments(tmp_path):
    from dj_track_analyzer import jobs

    a = tmp_path / "mix.json"
    a.write_text("{}")
    (tmp_path / "clips").mkdir()
    args = jobs._clips_describe({"analysis": str(a), "clips": f' "{tmp_path / "clips"}" \n\n', "offset": "0:14",
                                 "at": {"2": "41:20", "3": ""}})
    assert args == ["clips", "describe", str(a), str(tmp_path / "clips"), "--save", "--offset", "0:14", "--at", "2=41:20"]
    for bad in ({"analysis": str(a), "clips": ""}, {"analysis": str(a), "clips": "clips"},
                {"analysis": str(a), "clips": str(tmp_path), "offset": "soon"}, {"analysis": "mix.json", "clips": str(tmp_path)}):
        with pytest.raises(ValueError):
            jobs._clips_describe(bad)


def test_frame_scores_prefer_sharp_lit_still_frames():
    rng = np.random.default_rng(3)
    side = 32
    sharp = rng.integers(40, 220, (side, side)).astype(np.uint8)  # detailed and lit
    blurred = np.full((side, side), 128, np.uint8)
    blurred[:, : side // 2] = 90  # one soft edge: lit, a little contrast, no detail
    black = np.full((side, side), 5, np.uint8)
    img = np.stack([sharp, sharp, blurred, blurred, black, sharp])
    score = clipdesc.frame_scores(img)
    assert np.isneginf(score[4])  # black: never a thumbnail
    assert score[0] > score[2]  # detail beats flat
    assert score[0] > score[5]  # frame 5 comes straight after a cut (from black): motion


def test_best_frames_keep_apart_and_skip_bad():
    times = np.arange(10, dtype=float)
    score = np.array([1, 9, 8.5, 1, 1, 7, 1, -np.inf, 1, 1], float)
    assert clipdesc.best_frames(times, score, 3, apart=3.0) == [1.0, 5.0, 8.0]
    assert clipdesc.best_frames(times, np.full(10, -np.inf), 3) == []


def test_crop_follows_the_detail():
    side = 64
    img = np.full((1, side, side * 2, 3), 120, np.uint8)  # a wide frame, flat...
    img[0, :, 100:120] = np.random.default_rng(4).integers(0, 255, (side, 20, 3))  # ...with the action on the right
    (x, y, w, h), = clipdesc.crop_boxes(img, 9 / 16)
    assert (y, h) == (0, side) and w == 36 and x <= 100 and x + w >= 120  # a tall window around it


def test_at_least_two_stills_even_from_a_dull_clip():
    rng = np.random.default_rng(5)
    img = rng.integers(20, 30, (8, 36, 64, 3)).astype(np.uint8)  # dim and flat: strict scoring rejects every frame
    times = np.arange(8) / clipdesc.STILL_FPS
    assert np.isneginf(clipdesc.frame_scores(img)).all()
    got = clipdesc.pick_frames(times, img, 16 / 9, 3, 1.0)
    assert len(got) >= clipdesc.MIN_STILLS


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_stills_from_a_clip_and_the_video(tmp_path):
    clip = tmp_path / "2026-11-06-clip-1.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=6",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    folder = tmp_path / "stills"
    folder.mkdir()
    got = clipdesc.clip_stills(clip, folder, 1)
    assert set(got) == set(clipdesc.CLIP_MEDIA)
    for media, stills in got.items():
        assert len(stills) == clipdesc.STILLS
        assert all(0.5 <= x["t"] <= 5.5 for x in stills)  # not the fade-prone ends
        assert all(clipdesc.video_size(folder / x["file"]) == clipdesc.MEDIA[media][1] for x in stills)
        assert stills[0]["dest"] == str(tmp_path / ("2026-11-06-clip-1" + clipdesc.MEDIA[media][2]))
    result = {"highlights": [{"rank": 1, "video": "0:01-0:04"}]}
    yt = clipdesc.youtube_stills(clip, result, folder)  # one from the top moment, the rest spread through
    assert len(yt) >= clipdesc.MIN_STILLS and 1 <= yt[0]["t"] <= 4
    assert clipdesc.video_size(folder / yt[0]["file"]) == (1280, 720)
    assert yt[0]["dest"] == str(tmp_path / "2026-11-06-clip-1-thumb-youtube.jpg")


def test_page_still_routes(tmp_path):
    from dj_track_analyzer import web

    a = tmp_path / "mix.json"
    folder = tmp_path / "stills" / "mix"
    folder.mkdir(parents=True)
    (folder / "clip-1-1.jpg").write_bytes(b"jpg")
    (folder / "other.jpg").write_bytes(b"x")
    dest = tmp_path / "clip-1-thumb.jpg"
    a.write_text(json.dumps({"type": "dj_mix", "described_clips": {"clips": [
        {"n": 1, "stills": {"post": [{"t": 1.0, "file": "clip-1-1.jpg", "dest": str(dest)}]}}]}}))
    assert web.use_still([tmp_path], "0/mix.json", "clip-1-1.jpg") == {"saved": str(dest)}
    assert dest.read_bytes() == b"jpg"
    for name in ("other.jpg", "../mix.json", "missing.jpg"):  # only the stills the analysis lists
        with pytest.raises(ValueError):
            web._still([tmp_path], "0/mix.json", name)
