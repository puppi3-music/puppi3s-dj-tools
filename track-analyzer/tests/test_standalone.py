"""Standalone pieces: a long blend qualifies, a quick handover doesn't, a file that isn't what was played can't be
measured, a live stem cut is still the track playing when it has stems. Made-up audio: noise whose spectrum changes
every quarter second, so each "track" (and each stem) lines up in one place."""

import numpy as np
import pytest

from dj_track_analyzer import standalone as sa

SR = sa.SR
BPM = 240  # one bar a second keeps the audio short



@pytest.fixture(autouse=True)
def short_pieces(monkeypatch):
    """The made-up blends are 40-50 bars (one bar a second keeps them short): the rule is checked at 32 bars."""
    monkeypatch.setattr(sa, "MIN_BARS", 32)
    monkeypatch.setattr(sa, "MIN_SECONDS", 32.0)  # one bar a second: the track rule at 32 bars too

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
    assert c["title"] == "Artist A vs Artist B – Song A x Song B (Live Mashup)"
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


def test_export_cuts_the_piece_as_adjusted_with_tags(tmp_path):
    """Export: the run moved by whole bars, a WAV in '<recording> pieces/' with the title as file name and tags;
    a piece shorter than 8 bars is refused; Show only finds files in that folder."""
    import json
    import shutil
    import subprocess

    import pytest
    import soundfile as sf

    from dj_track_analyzer import web

    if not shutil.which("ffmpeg"):
        pytest.skip("no ffmpeg")
    rec = tmp_path / "Recording.wav"
    sf.write(rec, np.zeros(120 * 8000, np.float32) + 0.1, 8000, subtype="PCM_16")
    bars = [[10.0 + 2 * i, 0.0, 0.0] for i in range(40)]  # 2 s bars from 10 s
    c = {"at": "1:00", "bars": bars, "run": {"bars": 16, "from": "0:20", "to": "0:52"}}
    (tmp_path / "mix.json").write_text(json.dumps({"type": "dj_mix", "file": str(rec), "standalone": {"changes": [c]}}))
    assert web.piece_bounds(c) == (20.0, 52.0, 2.0)
    assert web.piece_bounds(c, 8, -4) == (4.0, 44.0, 2.0)
    with pytest.raises(ValueError, match="8 bars"):
        web.piece_bounds(c, 0, -9)
    d = web.export_piece([tmp_path], "0/mix.json", "1:00", lead=4, tail=8, fade=False, title="A / B (Edit)",
                         credits="Originals:\n• A")
    out = tmp_path / "Recording pieces" / "A _ B (Edit).wav"
    assert d["saved"] == str(out) and (d["from"], d["to"], d["bars"]) == ("0:12", "1:08", 28)
    assert abs(sf.info(out).duration - 56.0) < 0.01
    tags = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(out)],
                          capture_output=True, text=True).stdout
    assert "A / B (Edit)" in tags
    with pytest.raises(ValueError, match="isn't exported"):
        web.reveal_piece([tmp_path], "0/mix.json", "../mix.json")


def long_blend(tmp_path):
    """A plays 0-150 s; B comes in at 60 s, its judged-on window starts at 65 s (the old measuring stopped at
    85 s), and both play together until A ends."""
    a, b = track(20, 150), track(21, 120)
    mix = np.zeros(180 * SR, np.float32)
    mix[: 150 * SR] += a
    mix[60 * SR: 180 * SR] += b
    paths = {}
    for k in "ab":
        (tmp_path / f"{k}.flac").write_bytes(b"")
        paths[k] = str(tmp_path / f"{k}.flac")
    tracks = [{"n": 1, "start": "0:00", "artist": "A", "title": "A", "judged_on": "0:05-0:45", "bpm": BPM,
               "played_bpm": BPM, "path": paths["a"]},
              {"n": 2, "start": "1:00", "artist": "B", "title": "B", "judged_on": "1:05-2:55", "bpm": BPM,
               "played_bpm": BPM, "path": paths["b"]}]
    by_path = {paths["a"]: a, paths["b"]: b}
    return {"tracklist": {"tracks": tracks}}, mix, by_path, paths


def test_run_is_measured_until_it_ends_on_the_incoming_grid(tmp_path):
    """The run isn't cut off where the next track's window starts; the bars sit on B's downbeats and the piece is
    widened to whole 16-bar phrases of B."""
    result, mix, by_path, paths = long_blend(tmp_path)
    out = sa.find(result, mix, load=by_path.get, load_stems=lambda p: None,
                  grid=lambda p: (BPM, 300.0) if p == paths["b"] else None, say=lambda m: None)
    (c,) = out["changes"]
    assert c["status"] == "measured" and c["qualifies"]
    assert c["run"]["bars"] >= 85  # 60-150 s, one bar a second; was cut at 85 s before
    assert abs(sa.seconds(c["run"]["to"]) - 150) <= 2
    assert c["bars"][-1][0] < 170  # stops measuring once the run is over (+ CONTEXT bars)
    assert all(abs((x - 60.3) - round(x - 60.3)) < 0.06 for x, *_ in c["bars"])  # on B's downbeats
    t0 = next(x for x, *_ in c["bars"] if abs(x - sa.seconds(c["run"]["from"])) < 0.6)
    k0 = round(t0 - 60.3) - c["run"]["lead"]
    assert k0 % 16 == 0 and (k0 + c["run"]["lead"] + c["run"]["bars"] + c["run"]["tail"]) % 16 == 0
    assert c["bar"] == 1.0


def test_no_grid_still_measures_to_the_end(tmp_path):
    result, mix, by_path, _paths = long_blend(tmp_path)
    out = sa.find(result, mix, load=by_path.get, load_stems=lambda p: None, grid=lambda p: None, say=lambda m: None)
    (c,) = out["changes"]
    assert c["run"]["bars"] >= 85 and "lead" not in c["run"]


def test_threads_give_the_same_result(tmp_path):
    """Changes measured in parallel threads come out exactly as one after the other; each track is lined up once."""
    n, step = 4, 60
    audio = [track(30 + k, 120) for k in range(n)]
    mix = np.zeros(((n - 1) * step + 120) * SR, np.float32)
    tracks, by_path = [], {}
    for k, a in enumerate(audio):
        mix[k * step * SR: (k * step + 120) * SR] += a
        path = tmp_path / f"{k}.flac"
        path.write_bytes(b"")
        by_path[str(path)] = a
        s = k * step
        tracks.append({"n": k + 1, "start": sa.clock(s), "artist": "X", "title": f"T{k}", "bpm": BPM,
                       "played_bpm": BPM, "path": str(path),
                       "judged_on": f"{sa.clock(s + 5)}-{sa.clock(s + 55)}"})
    loads = []

    def load(p):
        loads.append(p)
        return by_path[p]

    outs = [sa.find({"tracklist": {"tracks": tracks}}, mix, load=load, load_stems=lambda p: None,
                    grid=lambda p: None, say=lambda m: None, workers=w) for w in (1, 4)]
    assert len(outs[0]["changes"]) == n - 1
    assert outs[0] == outs[1]
    assert sorted(loads) == sorted(list(by_path) * 2)  # each original read once per run


def test_long_blend_short_of_a_track_is_a_short_edit(tmp_path, monkeypatch):
    monkeypatch.setattr(sa, "MIN_SECONDS", 1000.0)  # no run is this long: the blend falls back to a short edit
    result, out = run(tmp_path, overlap=45)
    (c,) = out["changes"]
    assert not c["qualifies"] and c["short"], c.get("run")
    assert out["picks"] == [] and out["shorts"] == [c["at"]] and "closest_miss" not in out
    result["standalone"] = out
    assert "(short edit)" in "\n".join(sa.summary_lines(result))


def setup_three(tmp_path):
    """A plays 0-130 s; B is an insert 40-90 s; C comes in at 85 s under A: A and C overlap 45 s, around the
    change from B into C, where B and C overlap only 5 s."""
    srcs = {k: track(s, 200) for k, s in (("a", 21), ("b", 22), ("c", 23))}
    mix = np.zeros(220 * SR, np.float32)
    for k, (t0, t1) in {"a": (0, 130), "b": (40, 90), "c": (85, 215)}.items():
        mix[t0 * SR: t1 * SR] += srcs[k][: (t1 - t0) * SR]
    paths = {}
    for k in srcs:
        (tmp_path / f"{k}.flac").write_bytes(b"")
        paths[k] = str(tmp_path / f"{k}.flac")
    tracks = [{"n": n + 1, "start": sa.clock(s), "artist": f"Artist {k.upper()}", "title": f"Song {k.upper()}",
               "judged_on": w, "bpm": BPM, "played_bpm": BPM, "path": paths[k]}
              for n, (k, s, w) in enumerate((("a", 0, "0:05-0:45"), ("b", 40, "0:42-1:28"), ("c", 85, "2:11-3:30")))]
    by_path = {paths[k]: srcs[k] for k in srcs}
    return {"tracklist": {"tracks": tracks}}, mix, lambda p: by_path[p]


@pytest.mark.parametrize("multi", [True, False])
def test_overlap_of_tracks_around_a_change(tmp_path, multi):
    result, mix, load = setup_three(tmp_path)
    out = sa.find(result, mix, load=load, load_stems=lambda p: None, say=lambda m: None, multi=multi)
    c = out["changes"][1]  # B into C, with A still up
    assert c["status"] == "measured"
    if multi:
        assert c["others"] == [1] and c["with"] == [1] and c["qualifies"], c["run"]
        assert c["names"] == [1, 2, 3] and c["title"].startswith("Artist A vs Artist B vs Artist C – ")
        assert len(c["bars"][0]) == 4
        assert np.median([r[1] if r[1] is not None else -99 for r in c["bars"] if 95 <= r[0] < 125]) < -sa.NEAR_DB
    else:  # A unfitted: its sound is taken for B's after B ends, a run that isn't there
        assert len(c["bars"][0]) == 3
        assert np.median([r[1] if r[1] is not None else -99 for r in c["bars"] if 95 <= r[0] < 125]) > -sa.NEAR_DB
