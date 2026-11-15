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
