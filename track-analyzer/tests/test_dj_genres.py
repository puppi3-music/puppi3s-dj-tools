"""DJ genre rules (dj_genres.json + dj_genre): made-up Discogs scores shaped like real tracks from the
2026-11-11 genre audit, so a rule edit that brings back an old mistake fails here."""
from __future__ import annotations

from dj_track_analyzer import analysis


def names(styles: dict[str, float], bpm: float | None) -> list[str]:
    return [g["genre"] for g in analysis.dj_genre({f"Electronic---{s}": v for s, v in styles.items()}, bpm)]


def test_neo_trance_is_melodic_house_not_trance():
    # MAEST's Neo Trance on top of modern melodic house (Keinemusik, Adriatique): never Trance
    got = names({"Neo Trance": 0.72, "Deep Techno": 0.45, "Progressive House": 0.3}, 124)
    assert got[0] == "Melodic House & Techno" and "Trance" not in got
    got = names({"Neo Trance": 0.85, "Deep House": 0.63, "Progressive House": 0.47, "Deep Techno": 0.29}, 120)
    assert got[0] == "Deep House" and "Trance" not in got


def test_trance_style_still_makes_trance():
    assert names({"Trance": 0.6, "Progressive House": 0.2}, 138)[0] == "Trance"


def test_ambient_has_no_tempo():
    # a film score read at 124 BPM: Ambient, not left without a genre by a tempo range
    assert names({"Ambient": 0.07, "Modern Classical": 0.06}, 124) == ["Ambient"]
    assert names({"Ambient": 0.07, "Modern Classical": 0.06}, None) == ["Ambient"]


def test_downtempo_keeps_its_range():
    assert names({"Downtempo": 0.4, "Trip Hop": 0.2}, 90)[0] == "Downtempo"
    assert analysis.dj_genre({"Electronic---Downtempo": 0.4}, 130)[0]["note"] == "BPM outside usual range"


def test_psy_split_by_tempo():
    psy = {"Psy-Trance": 0.8, "Goa Trance": 0.3}
    assert names(psy, 142)[0] == "Psytrance"
    assert names(psy, 158)[0] == "Darkpsy"
    assert names(psy, 175)[0] == "Hi-Tech"


def test_fast_breakbeat_is_drum_and_bass():
    # the model's top style leads; tempo picks its form: breakbeat at 164 is DnB, not the runner-up Happy Hardcore
    assert names({"Breakbeat": 0.25, "Happy Hardcore": 0.20, "Drum n Bass": 0.19}, 164)[0] == "Drum and Bass"
    assert names({"Breakbeat": 0.25, "Happy Hardcore": 0.20}, 135)[0] == "Breaks"


def test_fast_techno_is_hard_techno():
    assert names({"Techno": 0.4, "Trance": 0.3}, 150)[0] == "Hard Techno"
    assert names({"Techno": 0.4, "Trance": 0.3}, 130)[0] == "Techno"
