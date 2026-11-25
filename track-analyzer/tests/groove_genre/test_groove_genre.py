"""groove_genre regression tests: synthetic bass patterns and the six Puppi3 sets (puppi3_sets.json)."""
import json
import os

import numpy as np

from dj_track_analyzer.groove_genre.groove_genre import FR, _synth, analyze_window, estimate_tempo, genre_evidence

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


def _kick_env(bpm, every=1, accent=1.0, seconds=30):
    """Onset envelope with a kick spike on every `every`-th beat; every 2nd kick at `accent` level."""
    e = np.zeros(int(seconds * FR))
    beat = 60 * FR / bpm
    for k in range(0, int(len(e) / beat), every):
        e[int(round(k * beat))] += 1.0 if k % 2 == 0 else accent
    return e


def test_accented_kicks_are_not_read_half_time():
    # every other kick quieter: the 2-beat lag wins the autocorrelation (read 62.5 before the half-beat check)
    assert abs(estimate_tempo(_kick_env(125, accent=0.5))[0] - 125) < 0.5


def test_real_half_time_stays_half():
    assert abs(estimate_tempo(_kick_env(125, every=2))[0] - 62.5) < 0.5
    assert abs(estimate_tempo(_kick_env(62.5))[0] - 62.5) < 0.5


def test_equal_fits_go_to_the_nearest_tempo_range():
    """A 126 BPM track with an offbeat hat and few minor keys fits Melodic Techno, Progressive Trance and Techno
    all at 1.0: the label goes to the range centred nearest (Melodic Techno 118-128), not to table order."""
    f = dict(bpm=126.0, gallop=0.2, psy_windows=0.0, hat_offbeat=10.0, hat_16ths=0.05, bass_swell=1.0, rumble=0.01,
             melodic_ratio=0.45, minor_share=0.8, chord_change=0.0, long_breakdown_share=0.0)
    ev = genre_evidence(f)
    assert ev["fits"]["Progressive Trance"]["fit"] == ev["fits"]["Melodic Techno"]["fit"] == 1.0
    assert ev["label"]["category"] == "Melodic Techno"
    assert genre_evidence(dict(f, bpm=131.0))["label"]["category"] == "Progressive Trance"  # 126-138 centres nearest


def test_tech_trance_reaches_down_to_132():
    """Tracks tagged tech trance ran 130-135 BPM in the 2026-11-20 groove check."""
    f = dict(bpm=132.0, gallop=0.2, psy_windows=0.0, hat_offbeat=5.0, hat_16ths=0.1, bass_swell=1.0, rumble=0.1,
             melodic_ratio=0.5, minor_share=0.7, chord_change=0.0, long_breakdown_share=0.0)
    assert genre_evidence(f)["fits"]["Tech Trance"]["tempo_fit"] == 1.0
