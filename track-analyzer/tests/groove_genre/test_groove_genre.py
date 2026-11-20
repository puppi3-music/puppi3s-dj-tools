"""groove_genre regression tests: synthetic bass patterns and the six Puppi3 sets (puppi3_sets.json)."""
import json
import os

from dj_track_analyzer.groove_genre.groove_genre import _synth, analyze_window, genre_evidence

FIX = json.load(open(os.path.join(os.path.dirname(__file__), "puppi3_sets.json")))


def test_synthetic_gallop_separates_psy():
    psy = analyze_window(_synth("psy"))
    off = analyze_window(_synth("offbeat"))
    sus = analyze_window(_synth("sustained"))
    assert psy["gallop"] > 0.5 > max(off["gallop"], sus["gallop"])
    assert all(abs(r["bpm"] - 145) < 1 for r in (psy, off, sus))


def test_synthetic_psy_is_labeled_psy():
    w = analyze_window(_synth("psy"))
    f = dict(w, psy_windows=1.0, minor_share=1.0, long_breakdown_share=0.0)
    ev = genre_evidence(f)
    assert ev["ranking"][0] in ("Psytrance", "Goa Trance")


def test_puppi3_sets_are_tech_trance_not_psy():
    for name, d in FIX.items():
        ev = genre_evidence(d["summary"], d["model_scores"])
        assert ev["label"]["category"] == "Tech Trance", name
        assert ev["fits"]["Psytrance"]["fit"] < 0.3, name
        if d["model_scores"]:
            adj = ev["adjusted_model_scores"]
            assert max(adj, key=adj.get) not in ("Psy-Trance", "Goa Trance"), name


def test_discogs_labels_match_aliases():
    """The style model's labels carry the parent genre ("Electronic---Psy-Trance"); they must still be re-weighted."""
    d = next(iter(FIX.values()))
    plain = genre_evidence(d["summary"], {"Psy-Trance": 0.5, "Tech Trance": 0.3})["adjusted_model_scores"]
    full = genre_evidence(d["summary"], {"Electronic---Psy-Trance": 0.5, "Electronic---Tech Trance": 0.3})
    assert full["adjusted_model_scores"] == {f"Electronic---{k}": v for k, v in plain.items()}
    assert full["adjusted_model_scores"]["Electronic---Psy-Trance"] < 0.5
