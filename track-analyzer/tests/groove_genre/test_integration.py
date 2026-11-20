"""groove_genre inside the analyzer: windows run through AudioPool, the evidence re-weights the model's scores and
names the mix (analysis.groove_measure / groove_headline, as analyze_file does for every mix)."""
import json

import numpy as np

from dj_track_analyzer import analysis
from dj_track_analyzer.groove_genre.groove_genre import _synth

# an offbeat-bass set at 145 the model hears as psy (the six Puppi3 sets' pattern)
MODEL = {"Electronic---Psy-Trance": 0.56, "Electronic---Goa Trance": 0.46, "Electronic---Tech Trance": 0.30,
         "Electronic---Trance": 0.28, "Rock---Indie Rock": 0.01}


def _set(seconds=90):
    y = np.concatenate([_synth("offbeat", seed=i) for i in range(int(seconds // 30))])
    return y.astype(np.float32)


def _measured(workers):
    y, result = _set(), {}
    with analysis.AudioPool(np.zeros(16, np.float32), workers=workers, extra={"mono22": y}) as pool:
        analysis.groove_measure(result, pool, y)
    return result


def test_pool_matches_in_process():
    assert _measured(2)["groove"] == _measured(1)["groove"]


def test_headline_from_evidence():
    result = _measured(1)
    analysis.groove_headline(result, dict(MODEL), 145)
    g = result["groove"]
    assert g["windows"] == 3 and g["gallop"] < 0.5 and abs(g["bpm"] - 145) < 1
    assert result["groove_label"]["category"] not in ("Psytrance", "Goa Trance")
    adj = {s["style"]: s["score"] for s in result["discogs_styles_adjusted"]}
    assert adj["Psy-Trance"] < 0.5 * MODEL["Electronic---Psy-Trance"]
    assert max(adj, key=adj.get) not in ("Psy-Trance", "Goa Trance")
    assert result["dj_genre"][0]["genre"] != "Psytrance"
    assert any(w.startswith("Psy-Trance demoted") and "gallop" in w for w in result["groove_why"])
    json.dumps(result)  # saved as the mix's JSON


def test_no_model_scores_still_labels():
    result = _measured(1)
    analysis.groove_headline(result, None, None)
    assert "groove_label" in result and "dj_genre" not in result and "discogs_styles_adjusted" not in result


def test_track_genres_from_the_mix_windows():
    """Each track's genre is checked against the windows the mix already measured in its stretch, without a scan
    of the track (no psy cap): the offbeat track loses its psy reading, the galloping one keeps it."""
    from dj_track_analyzer import tracklist as tl

    y = np.concatenate([_synth("offbeat", seed=i) for i in range(2)] + [_synth("psy", seed=i) for i in range(2)])
    y, result = y.astype(np.float32), {}
    with analysis.AudioPool(np.zeros(16, np.float32), workers=1, extra={"mono22": y}) as pool:
        wins = analysis.groove_measure(result, pool, y)
    assert [w["t"] for w in wins] == [0.0, 30.0, 60.0, 90.0]
    off, psy = {"genre": "Psytrance", "genre_basis": "as heard"}, {"genre": "Psytrance", "genre_basis": "as heard"}
    tl.track_groove(off, wins, 5.0, 55.0, dict(MODEL), 145)
    tl.track_groove(psy, wins, 65.0, 115.0, dict(MODEL), 145)
    assert off["genre"] != "Psytrance" and off["genre_before_groove"] == "Psytrance"
    assert any(w.startswith("Psy-Trance demoted") for w in off["groove_why"])
    assert "2 measured 30 s windows" in off["genre_basis"]
    assert psy["groove_label"]["category"] in ("Psytrance", "Goa Trance") and "genre_before_groove" not in psy
    json.dumps([off, psy])


def test_track_too_short_for_a_window_is_left_alone():
    from dj_track_analyzer import tracklist as tl

    t = {"genre": "Psytrance"}
    tl.track_groove(t, [{"t": 0.0}], 20.0, 40.0, dict(MODEL), 145)  # only 10 s of the window inside
    assert t == {"genre": "Psytrance"}


def test_breakdown_checks_left_out_without_audio():
    from dj_track_analyzer.groove_genre.groove_genre import genre_evidence

    f = {"bpm": 140.0, "gallop": 0.2, "hat_16ths": 0.1, "hat_offbeat": 2.5, "bass_swell": 1.2, "rumble": 0.1,
         "melodic_ratio": 0.5, "minor_share": 0.6, "chord_change": 0.2, "psy_windows": 0.0}
    ev = genre_evidence(f)
    assert not any("breakdowns" in r for g in ev["fits"].values() for r in g["reasons"])
    with_breaks = genre_evidence({**f, "long_breakdown_share": 0.0})
    assert any("breakdowns" in r for r in with_breaks["fits"]["Tech Trance"]["reasons"])
