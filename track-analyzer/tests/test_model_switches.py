"""The colour and cue switches (Starter / Both / Mine only): which applies (the starter until you've trained; then
your choice, or unset the lowest error measured at training), Both's mix, training's measurement (the starter only
on tracks it didn't learn from, else Mine only), and the anonymous track hashes the starters carry.

Models are tiny stand-ins written where the app looks for them; no audio, no Traktor."""
from __future__ import annotations

import joblib
import numpy as np
import pytest

from dj_track_analyzer import analysis, colors, cues


class Const:
    """A stand-in classifier: per-bar probabilities read from column 0 of X, scaled (a model's habit)."""

    def __init__(self, scale: float = 1.0, shift: int = 0):
        self.scale, self.shift = scale, shift

    def predict_proba(self, X):
        p = np.clip(np.roll(np.asarray(X)[:, 0], self.shift) * self.scale, 0, 1)
        return np.column_stack([1 - p, p])


@pytest.fixture
def home(monkeypatch, tmp_path):
    for mod, name, value in ((analysis, "HOME", tmp_path), (analysis, "SETTINGS", tmp_path / "settings.json"),
                             (analysis, "COLOR_MODEL", tmp_path / "my_colors.joblib"),
                             (analysis, "STARTER_COLOR_MODEL", tmp_path / "starter_colors.joblib"),
                             (cues, "MODEL", tmp_path / "my_cues.joblib"),
                             (cues, "STARTER_MODEL", tmp_path / "starter_cues.joblib")):
        monkeypatch.setattr(mod, name, value)
    analysis._color_bundle.clear()
    cues._bundle.clear()
    yield tmp_path
    analysis._color_bundle.clear()
    cues._bundle.clear()


def write(path, bundle: dict) -> None:
    joblib.dump(bundle, path)


# ---------------------------------------------------------------- which choice applies

def test_starter_until_trained_then_measured_auto_then_your_choice(home):
    write(analysis.STARTER_COLOR_MODEL, {"starter": True, "trained_at": "s"})
    assert analysis.color_model_mode() == "starter"
    analysis.save_app_setting("colour_model_mode", "mine")
    assert analysis.color_model_mode() == "starter"  # a choice can't apply before you've trained
    analysis.save_app_setting("colour_model_mode", None)
    write(analysis.COLOR_MODEL, {"trained_at": "t"})  # trained before the switch measured anything: as before
    assert analysis.color_model_mode() == "mine"
    write(analysis.COLOR_MODEL, {"trained_at": "t2", "switch": {"auto": "both", "both_weight": 0.25}})
    assert analysis.color_model_mode() == "both" and analysis.switch_weight(analysis.own_color_model()) == 0.25
    analysis.save_app_setting("colour_model_mode", "starter")
    assert analysis.color_model_mode() == "starter"
    analysis.STARTER_COLOR_MODEL.unlink()  # no starter in this copy: yours, whatever was chosen
    assert analysis.color_model_mode() == "mine"


def test_set_mode_refuses_what_cant_apply(home):
    write(cues.STARTER_MODEL, {"starter": True, "version": cues.BARS_VERSION, "trained_at": "s"})
    with pytest.raises(SystemExit, match="Learn your cue points first"):
        cues.set_mode("mine")
    with pytest.raises(ValueError):
        cues.set_mode("half")
    write(cues.MODEL, {"trained_at": "t", "switch": {"auto": "mine"}})
    assert cues.set_mode("both")["mode"] == "both"
    assert cues.set_mode("auto")["chosen"] == "auto" and cues.model_mode() == "mine"


def test_old_starter_cue_features_are_not_mixed_in(home):
    write(cues.MODEL, {"trained_at": "t", "switch": {"auto": "both"}})
    write(cues.STARTER_MODEL, {"starter": True, "version": "bars-1", "trained_at": "s"})
    assert cues.model_mode() == "mine"


# ---------------------------------------------------------------- Both

def test_both_mixes_colour_positions_by_the_measured_share(home, monkeypatch):
    write(analysis.COLOR_MODEL, {"who": "mine", "switch": {"auto": "both", "both_weight": 0.75}})
    write(analysis.STARTER_COLOR_MODEL, {"who": "starter", "starter": True})
    at = {"mine": 6.0, "starter": 2.0}
    monkeypatch.setattr(analysis, "color_position", lambda b, *a: (at[b["who"]], None, None) if b else None)
    monkeypatch.setattr(analysis, "color_confidence", lambda *a, **k: None)
    assert analysis.predict_color(None, None, {})["position"] == 5.0  # 0.75·6 + 0.25·2
    analysis.save_app_setting("colour_model_mode", "starter")
    assert analysis.predict_color(None, None, {})["position"] == 2.0
    analysis.save_app_setting("colour_model_mode", "mine")
    assert analysis.predict_color(None, None, {})["position"] == 6.0
    analysis.save_app_setting("colour_model_mode", "both")
    at["starter"] = None  # the starter can't place this track (a feature it needs is missing): yours alone
    monkeypatch.setattr(analysis, "color_position",
                        lambda b, *a: (at[b["who"]], None, None) if b and at[b["who"]] is not None else None)
    assert analysis.predict_color(None, None, {})["position"] == 6.0


def test_both_mixes_cue_and_loop_scores_per_bar():
    X = np.array([[0.2], [0.8], [0.4]])
    mine = {"cue_model": Const(1.0), "loop_model": None}
    starter = {"cue_model": Const(0.5), "loop_model": Const(1.0)}
    pc, pl = cues.scores(mine, starter, 0.5, X)
    assert np.allclose(pc, 0.5 * X[:, 0] + 0.5 * 0.5 * X[:, 0])
    assert np.allclose(pl, X[:, 0])  # yours has no loop model: the starter's loop scores alone
    pc, pl = cues.scores(mine, None, 1.0, X)
    assert np.allclose(pc, X[:, 0]) and pl is None


# ---------------------------------------------------------------- measured at training

def _colour_case(monkeypatch, n: int, starter_at, learned: set[str]):
    paths = [f"/music/t{i}.mp3" for i in range(n)]
    y = np.array([1 + i % 7 for i in range(n)])
    monkeypatch.setattr(analysis, "starter_color_model", lambda: {"starter": True, "trained_at": "s",
                                                                   "learned_from": sorted(learned)})
    monkeypatch.setattr(analysis, "track_hash", lambda p: p)
    raws = [(i,) for i in range(n)]
    monkeypatch.setattr(analysis, "color_position", lambda b, i: (float(starter_at(y[i])), None, None))
    return paths, y, raws


def test_colour_measurement_picks_the_starter_when_it_knows_your_habits(monkeypatch):
    paths, y, raws = _colour_case(monkeypatch, 70, lambda c: c, {"/music/t0.mp3"})
    mine = np.clip(y + np.where(np.arange(70) % 2, 1.2, -1.2), 1, 7)
    sw = colors.measure_switch(paths, y, mine, raws, progress=lambda *a: None)
    assert sw["measured_on"] == 69 and sw["starter_learned_from_these"] == 1  # the track it learned from: left out
    assert sw["auto"] == "starter" and sw["errors"]["starter"]["average_error_per_colour"] == 0
    assert sw["both_weight"] == 0.25 and len(sw["starter_position"]) == 70


def test_colour_measurement_picks_yours_when_the_starter_reads_the_scale_backwards(monkeypatch):
    paths, y, raws = _colour_case(monkeypatch, 70, lambda c: 8 - c, set())
    sw = colors.measure_switch(paths, y, y.astype(float), raws, progress=lambda *a: None)
    assert sw["errors"] is None and sw["auto"] == "mine"  # a starter without its list can't be judged fairly
    assert "doesn't list" in sw["note"]
    paths, y, raws = _colour_case(monkeypatch, 70, lambda c: 8 - c, {"x"})
    sw = colors.measure_switch(paths, y, np.clip(y + 0.4, 1, 7), raws, progress=lambda *a: None)
    assert sw["auto"] == "mine" and sw["both_weight"] == 0.75


def test_colour_measurement_falls_back_to_yours_when_the_starter_learned_from_your_tracks(monkeypatch):
    paths, y, raws = _colour_case(monkeypatch, 70, lambda c: c, {f"/music/t{i}.mp3" for i in range(50)})
    sw = colors.measure_switch(paths, y, y.astype(float), raws, progress=lambda *a: None)
    assert sw["measured_on"] == 20 and sw["errors"] is None and sw["auto"] == "mine"
    assert "learned from 50 of your 70" in sw["note"] and "needs 30" in sw["note"]


def _cue_rows(n: int, bars: int = 32, at: int = 8):
    rows, X = [], []
    for i in range(n):
        yc, yl = np.zeros(bars, int), np.zeros(bars, int)
        yc[[at, at + 16]] = 1
        yl[at + 8] = 1
        rows.append((f"/music/c{i}.mp3", None, yc, yl))
        X.append(np.column_stack([yc * 0.9 + 0.01]))
    off = np.cumsum([0] + [bars] * n)
    return rows, np.vstack(X), off


def test_cue_measurement_judges_the_starter_only_on_tracks_it_didnt_learn_from(home, monkeypatch):
    rows, X, off = _cue_rows(40)
    write(cues.STARTER_MODEL, {"starter": True, "version": cues.BARS_VERSION, "trained_at": "s", "threshold": 0.25,
                               "max_cues": 7, "cue_model": Const(1.0, shift=1), "loop_model": None,
                               "learned_from": ["/music/c0.mp3", "/music/c1.mp3"]})
    monkeypatch.setattr(analysis, "track_hash", lambda p: p)
    Pc = X[:, 0].copy()  # yours: right on your bars
    Pl = np.concatenate([r[3] * 0.9 for r in rows]).astype(float)
    sw = cues.measure_switch(rows, X, off, Pc, Pl, 0.25, 7, progress=lambda *a: None)
    assert sw["measured_on"] == 38 and sw["starter_learned_from_these"] == 2
    assert sw["errors"]["starter"]["cue_points_exact_bar"]["f1"] == 0  # one bar late, every time
    assert sw["errors"]["mine"]["cue_points_exact_bar"]["f1"] == 1 and sw["auto"] == "mine"
    assert sw["errors"]["mine"]["loop_within_4_bars"] == 1


def test_cue_measurement_too_few_fair_tracks(home, monkeypatch):
    rows, X, off = _cue_rows(40)
    write(cues.STARTER_MODEL, {"starter": True, "version": cues.BARS_VERSION, "trained_at": "s",
                               "cue_model": Const(), "loop_model": None,
                               "learned_from": [r[0] for r in rows[:35]]})
    monkeypatch.setattr(analysis, "track_hash", lambda p: p)
    sw = cues.measure_switch(rows, X, off, X[:, 0], X[:, 0], 0.25, 7, progress=lambda *a: None)
    assert sw["auto"] == "mine" and sw["errors"] is None and sw["measured_on"] == 5


# ---------------------------------------------------------------- the starters' anonymous list

def test_track_hash_same_release_same_hash(monkeypatch, tmp_path):
    from dj_track_analyzer import collection

    tags = {"a.mp3": {"artist": "Beyoncé", "title": "Halo (Original Mix)"},
            "b.flac": {"artist": "BEYONCE", "title": "Halo"},
            "c.mp3": {"artist": "Beyonce", "title": "Halo (Dub)"}}
    for name in (*tags, "d.mp3"):
        (tmp_path / name).write_bytes(b"x")
    monkeypatch.setattr(collection, "read_tags", lambda p: tags.get(p.name, {}))
    h = {n: analysis.track_hash(tmp_path / n) for n in (*tags, "d.mp3")}
    assert h["a.mp3"] == h["b.flac"] != h["c.mp3"]
    assert len(h["a.mp3"]) == 16 and "halo" not in h["a.mp3"]
    assert h["d.mp3"] == analysis.track_hash(tmp_path / "elsewhere" / "d.mp3")  # no tags: by file name


# ---------------------------------------------------------------- the command line, as the page's switch

def test_cli_switch_shows_and_refuses_like_the_page():
    import json

    from conftest import cli

    r = cli("cues", "switch")
    st = json.loads(r.stdout[r.stdout.index("{"):])
    assert st["trained"] is False and st["mode"] == "starter" and st["chosen"] == "auto"
    r = cli("colors", "switch", "--use", "mine", check=False)
    assert r.returncode and "Learn your colours first" in r.stderr + r.stdout
    r = cli("cues", "show", "x", "--use", "both", check=False)
    assert r.returncode and "--use goes with switch or train" in r.stderr + r.stdout


def test_page_switches_colours_as_a_job_and_cues_at_once(home, monkeypatch):
    from dj_track_analyzer import jobs, web

    build = jobs.ACTIONS["colors_switch"][1]
    assert build({"mode": "both"}) == ["colors", "switch", "--use", "both"]
    assert build({"mode": "auto"})[-1] == "auto"
    with pytest.raises(ValueError):
        build({"mode": "mine; rm -rf"})
    from starlette.testclient import TestClient

    monkeypatch.setattr(jobs, "MIX_DIR", jobs.MIX_DIR)  # build_app sets it
    client = TestClient(web.build_app([analysis.HOME / "logs"]), base_url="http://127.0.0.1:8766")
    write(cues.STARTER_MODEL, {"starter": True, "version": cues.BARS_VERSION, "trained_at": "s"})
    monkeypatch.setattr(jobs, "busy", lambda: True)
    r = client.post("/api/cues/use", json={"mode": "starter"})
    assert r.status_code >= 400 and "job is running" in r.text
    monkeypatch.setattr(jobs, "busy", lambda: False)
    assert client.post("/api/cues/use", json={"mode": "starter"}).json()["chosen"] == "starter"  # needs no training of yours
    assert client.post("/api/cues/use", json={"mode": "mine"}).status_code >= 400  # nothing of yours to use yet
