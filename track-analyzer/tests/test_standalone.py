"""Standalone pieces: a long blend qualifies, a quick handover doesn't, a file that isn't what was played can't be
measured, a live stem cut is still the track playing when it has stems. Made-up audio: noise whose spectrum changes
every quarter second, so each "track" (and each stem) lines up in one place."""

import numpy as np

from dj_track_analyzer import standalone as sa

SR = sa.SR
BPM = 240  # one bar a second keeps the audio short


def track(seed: int, seconds: float) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = SR // 4
    out = []
    for _ in range(int(seconds * 4)):
        shape = np.exp(np.convolve(rng.normal(0, 1.5, n // 2 + 1), np.ones(200) / 200, "same"))
        out.append(np.fft.irfft(shape * np.exp(2j * np.pi * rng.random(n // 2 + 1)), n))
    x = np.concatenate(out).astype(np.float32)
    return x / np.abs(x).max() * 0.3


def setup(tmp_path, overlap: float, wrong_b: bool = False, cut_a: bool = False):
    """A plays 0-90 s, B comes in under it and plays on alone. A is 4 stems; cut_a: through the blend only its
    quietest stem plays (about -15 dB of the whole track), as a live stem cut leaves it."""
    stems_a = [track(10 + i, 100) * (0.3 if i == 0 else 1) for i in range(4)]
    a, b = sum(stems_a), track(2, 120)
    b_in = 90 - overlap
    mix = np.zeros(int((b_in + 120) * SR), np.float32)
    mix[: 90 * SR] += a[: 90 * SR]
    if cut_a:
        mix[int(b_in * SR): 90 * SR] -= sum(stems_a[1:])[int(b_in * SR): 90 * SR]
    mix[int(b_in * SR): int(b_in * SR) + len(b)] += b
    files = {"a": a, "b": track(3, 120) if wrong_b else b}
    paths = {}
    for k in files:
        (tmp_path / f"{k}.flac").write_bytes(b"")
        paths[k] = str(tmp_path / f"{k}.flac")
    solo_b = sa.clock(95 if overlap < 30 else 92)
    tracks = [
        {"n": 1, "start": "0:00", "artist": "Artist A", "title": "Song A (Extended Mix)", "judged_on": "0:05-0:45",
         "bpm": BPM, "played_bpm": BPM, "path": paths["a"]},
        {"n": 2, "start": sa.clock(b_in), "artist": "Artist B", "title": "Song B (Someone Remix)",
         "judged_on": f"{solo_b}-2:40", "bpm": BPM, "played_bpm": BPM, "path": paths["b"], "transition": "clash",
         "camelot": "8A", "heard_camelot": "8A"},
    ]
    tracks[0]["camelot"] = tracks[0]["heard_camelot"] = "3B"
    result = {"tracklist": {"recording_start": "2026-10-27 14:57:24", "tracks": tracks}}
    by_path = {v: files[k] for k, v in paths.items()}
    stems = {paths["a"]: stems_a}
    return result, mix, lambda p: by_path[p], stems


def run(tmp_path, stems_on=False, **kw):
    """find() on a made-up mix; stems_on: A has a stem file (B never does)."""
    result, mix, load, stems = setup(tmp_path, **kw)
    out = sa.find(result, mix, load=load, load_stems=(lambda p: stems.get(p)) if stems_on else (lambda p: None),
                  say=lambda m: None)
    return result, out


def test_long_blend_qualifies(tmp_path):
    _result, out = run(tmp_path, overlap=45)
    (c,) = out["changes"]
    assert c["status"] == "measured" and c["qualifies"], c.get("run")
    assert c["run"]["bars"] >= 40
    assert out["picks"] == [c["at"]]
    assert c["title"] == "Artist A vs Artist B – Song A x Song B (puppi3 Live Mashup)"
    assert "keys clash (3B into 8A)" in c["why"]  # noted, never a reason to leave it out
    assert "• Artist B - Song B (Someone Remix)" in c["credits"]
    assert "Recorded live 27 Oct 2026 · full set: <link>" in c["credits"]


def test_quick_handover_does_not(tmp_path):
    result, out = run(tmp_path, overlap=8)
    (c,) = out["changes"]
    assert c["status"] == "measured" and not c["qualifies"]
    assert out["picks"] == [] and out["closest_miss"] == c["at"]  # nothing qualifies is a valid answer
    result["standalone"] = out
    text = "\n".join(sa.summary_lines(result))
    assert "Nothing qualifies." in text and "Closest miss" in text


def test_wrong_file_cannot_be_measured(tmp_path):
    _result, out = run(tmp_path, overlap=45, wrong_b=True)
    (c,) = out["changes"]
    assert c["status"].startswith("couldn't measure") and "doesn't match what was played" in c["status"]
    assert out["picks"] == [] and "closest_miss" not in out


def test_stem_cut_still_counts_with_stems(tmp_path):
    _result, whole = run(tmp_path, overlap=45, cut_a=True)
    (c,) = whole["changes"]
    assert c["measured_with"] == {"from": "whole", "to": "whole"} and not c["qualifies"]  # read as a fade
    _result, out = run(tmp_path, stems_on=True, overlap=45, cut_a=True)
    (c,) = out["changes"]
    assert c["measured_with"] == {"from": "stems", "to": "whole"}
    assert c["qualifies"] and c["run"]["bars"] >= 40, c["run"]
    assert c["why"].endswith("; measured without stems: Song B")
    assert out["measured_with"] == {"1": "stems", "2": "whole"}


def test_separation_only_when_asked(tmp_path):
    result, mix, load, stems = setup(tmp_path, overlap=45, cut_a=True)
    asked = []

    def split(path, t0, t1):
        asked.append(path)
        if path in stems:
            return [x[int(t0 * SR):int(t1 * SR)] for x in stems[path]]
        return [load(path)[int(t0 * SR):int(t1 * SR)]] + [np.zeros(int((t1 - t0) * SR), np.float32)] * 3

    out = sa.find(result, mix, load=load, load_stems=lambda p: None, split=split, say=lambda m: None)
    assert asked == [] and out["changes"][0]["measured_with"]["from"] == "whole"  # off by default
    out = sa.find(result, mix, load=load, load_stems=lambda p: None, split=split, separate_missing=True,
                  say=lambda m: None)
    (c,) = out["changes"]
    assert len(asked) == 2 and c["measured_with"] == {"from": "separated", "to": "separated"}
    assert c["qualifies"] and "stems separated here: Song A, Song B" in c["why"]


def test_longest_run_allows_single_off_bars():
    T, F = True, False
    assert sa._longest([T, T, F, T, T, F, F, T]) == (0, 5)
    assert sa._longest([F, T, F, F, T, T, T], gap=1) == (4, 3)
    assert sa._longest([T, F, T], gap=0) == (0, 1)
    assert sa._longest([F, F]) == (0, 0)


def test_mix_job_passes_the_standalone_ticks(tmp_path):
    from dj_track_analyzer import jobs

    f = tmp_path / "mix.wav"
    f.write_bytes(b"")
    base = {"file": str(f), "tracklist": True}
    assert "--no-standalone" not in jobs._analyze_mix(base) and "--standalone-separate" not in jobs._analyze_mix(base)
    assert "--no-standalone" in jobs._analyze_mix({**base, "standalone": False})
    assert "--standalone-separate" in jobs._analyze_mix({**base, "standalone_separate": True})
    off = jobs._analyze_mix({**base, "standalone": False, "standalone_separate": True})
    assert "--standalone-separate" not in off
    assert "--no-standalone" not in jobs._analyze_mix({**base, "tracklist": False, "standalone": False})


def test_preview_plays_only_the_recording_a_saved_mix_names(tmp_path):
    import json

    import pytest

    from dj_track_analyzer import web

    rec = tmp_path / "Recording_2026-10-27_14h57m24s.wav"
    rec.write_bytes(b"RIFF")
    (tmp_path / "mix.json").write_text(json.dumps({"type": "dj_mix", "file": str(rec)}))
    (tmp_path / "other.json").write_text(json.dumps({"type": "track", "file": str(rec)}))
    assert web.mix_audio([tmp_path], "0/mix.json") == rec
    with pytest.raises(ValueError, match="not a mix"):
        web.mix_audio([tmp_path], "0/other.json")
    with pytest.raises(ValueError, match="unknown file"):
        web.mix_audio([tmp_path], "0/../mix.json")
    rec.unlink()
    with pytest.raises(ValueError, match="Reanalyse"):
        web.mix_audio([tmp_path], "0/mix.json")
