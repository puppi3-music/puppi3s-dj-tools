"""The groove rules' calibration hook: reference sets matched by name, reported per genre."""
import json
import os

from dj_track_analyzer.groove_genre import groove_genre as gg

FIX = json.load(open(os.path.join(os.path.dirname(__file__), "puppi3_sets.json")))


def test_reference_genre_names():
    assert gg.reference_genre("Psytrance") == "Psytrance"
    assert gg.reference_genre("tech_trance") == "Tech Trance"
    assert gg.reference_genre("DJ / Psy-Trance") == "Psytrance"
    assert gg.reference_genre("hard_techno") == "Hard Techno"
    assert gg.reference_genre("uplifting trance") == "Uplifting Trance"
    assert gg.reference_genre("Trance") is None  # any trance: nothing to check it against
    assert gg.reference_genre("Hyperpop") is None


def test_reference_report_flags_mislabelled():
    sets = [(name, d["summary"]) for name, d in FIX.items()]  # all six label Tech Trance
    rep = gg.reference_report({"Tech Trance": sets, "Psytrance": sets[:2]})
    tt, psy = rep["genres"]["Tech Trance"], rep["genres"]["Psytrance"]
    assert (tt["agree"], tt["examples"], tt["mislabelled"]) == (6, 6, [])
    assert (psy["agree"], len(psy["mislabelled"])) == (0, 2)
    bad = psy["mislabelled"][0]
    assert bad["label"] == "Tech Trance" and bad["kept"] < 0.5 and any("gallop" in r for r in bad["failed"])
    assert rep["examples"] == 8 and rep["agree"] == 6 and rep["floor"] == gg.FLOOR
    assert set(tt["features"]["gallop"]) == {"median", "lo", "hi"}
    assert any("✗" in line for line in gg.reference_lines(rep))
