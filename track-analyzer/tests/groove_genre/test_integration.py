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
