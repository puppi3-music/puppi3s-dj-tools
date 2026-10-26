"""
Learn your Traktor colour scale from tracks you've already coloured.

  uv run dj-track-analyzer colors train      learn from your coloured tracks (reads Traktor's collection, read-only)
  uv run dj-track-analyzer colors review     tracks whose sound disagrees most with the colour you gave them
  uv run dj-track-analyzer colors explain    what each colour means in your collection (sound, playlists, sets)
  uv run dj-track-analyzer colors apply      scan new tracks in Traktor and colour the ones without a colour

Your colours are treated as a scale: Red (hardest, darkest) → Orange → Yellow → Green → Blue →
Violet → Magenta (prettiest). Two kinds of model are tried, one that predicts a position on that
scale and one that treats the 7 colours as separate classes, and the better one in cross-validation
is kept. After training, every scan predicts a colour for new tracks, and `traktor` writes it to
tracks that don't have a colour yet.
"""

from __future__ import annotations

import os
import re
import time
import warnings
from pathlib import Path

import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin

warnings.filterwarnings("ignore", category=FutureWarning)  # scikit-learn deprecation notices

from . import collection
from . import analysis
from . import traktor
from .analysis import COLOR_NAMES, COLOR_MODEL, log


def read_traktor_colors(nml: str | None = None) -> tuple[Path, dict[str, int], int]:
    """{file path: COLOR value 1-7} for every coloured track in Traktor's collection."""
    path = traktor.find_collection(nml)
    traktor.refuse_if_tempo_view(path)  # a loaded view's colours aren't yours: they'd skew training and explain
    text = path.read_text(encoding="utf-8")
    c0, c1 = text.find("<COLLECTION"), text.find("</COLLECTION>")
    out: dict[str, int] = {}
    n = 0
    for entry in re.findall(r"<ENTRY\b.*?</ENTRY>", text[c0:c1], flags=re.S):
        loc = re.search(r"<LOCATION\b[^>]*>", entry)
        info = re.search(r"<INFO\b[^>]*>", entry)
        if not (loc and info):
            continue
        color = traktor.attrs(info.group(0)).get("COLOR")
        if not color or not color.isdigit() or not (1 <= int(color) <= len(COLOR_NAMES)):
            continue
        n += 1
        for p in traktor.location_paths(traktor.attrs(loc.group(0))):
            out[p] = int(color)
    return path, out, n


INFO_COLS = ["bpm", "lufs", "loudness_range", "key", "key_strength"] + [f"mood_{m}" for m in analysis.MOODS]

# name: (feature set, kind, calibrate)
CANDIDATES = {
    "scale, MAEST": ("maest", "scale", False),
    "scale, MAEST, calibrated": ("maest", "scale", True),
    "scale, MAEST + sound": ("maest+sound", "scale", False),
    "scale, MAEST + sound, calibrated": ("maest+sound", "scale", True),
    "scale, MAEST + sound, descriptors weighted": ("maest+sound+descriptors", "scale", False),
    "scale, MAEST + sound, descriptors weighted, calibrated": ("maest+sound+descriptors", "scale", True),
    "scale, MAEST + sound + instruments": ("maest+sound+instruments", "scale", False),
    "scale, MAEST + instruments": ("maest+instruments", "scale", False),
    "scale, instruments": ("instruments", "scale", False),
    "colours as classes, MAEST + instruments": ("maest+instruments", "classes", False),
    "colours as classes, MAEST": ("maest", "classes", False),
    "scale, sound": ("sound", "scale", False),
    "scale, sound, calibrated": ("sound", "scale", True),
    "scale, styles, calibrated": ("styles", "scale", True),
    "scale, all, calibrated": ("all", "scale", True),
    "colours as classes, all": ("all", "classes", False),
    "nearest neighbours, all": ("all", "knn", False),
}


# Feature sets whose small blocks (moods, instruments, energy arc...) are weighted up. Otherwise their
# ~60 numbers are drowned out by the ~2000 MAEST/EffNet ones, since ridge treats every number alike.
# Tested on a ~470-track collection: x10 → 1.18 average error vs 1.21 unweighted.
WEIGHTED_SETS = {"maest+sound+descriptors": ({"maest", "sound"}, 10.0)}


class BlockWeights(TransformerMixin, BaseEstimator):
    """Multiplies standardised feature columns by fixed weights (a pipeline step; saved in the model,
    so it lives at module level)."""

    def __init__(self, weights=None):
        self.weights = weights

    def fit(self, X, y=None):
        return self

    def transform(self, X):
        return X * self.weights


def block_weights(feature_set: str, inputs: dict) -> np.ndarray | None:
    if feature_set not in WEIGHTED_SETS:
        return None
    plain, w = WEIGHTED_SETS[feature_set]
    return np.concatenate([np.full(len(inputs[b]), 1.0 if b in plain else w)
                           for b in analysis.FEATURE_SETS[feature_set]])


def _make(kind: str, weights: np.ndarray | None = None):
    from sklearn.decomposition import PCA
    from sklearn.linear_model import LogisticRegressionCV, RidgeCV
    from sklearn.neighbors import KNeighborsRegressor
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if kind == "scale" and weights is not None:
        return make_pipeline(StandardScaler(), BlockWeights(weights), RidgeCV(alphas=np.logspace(-1, 5, 25)))
    if kind == "scale":
        return make_pipeline(StandardScaler(), RidgeCV(alphas=np.logspace(0, 5, 11)))
    if kind == "classes":
        return make_pipeline(StandardScaler(), LogisticRegressionCV(
            Cs=[0.003, 0.01, 0.03, 0.1], cv=3, max_iter=3000, class_weight="balanced", n_jobs=-1))
    return make_pipeline(StandardScaler(), PCA(n_components=32, random_state=0),
                         KNeighborsRegressor(n_neighbors=15, weights="distance"))


def _calibrator(kind: str, X, y, weights=None):
    """Stretch the scale back out: plain regression pulls everything toward the middle colours.
    Fitted on predictions for tracks the model didn't see (inner cross-validation)."""
    from sklearn.isotonic import IsotonicRegression
    from sklearn.model_selection import KFold, cross_val_predict

    inner = cross_val_predict(_make(kind, weights), X, y, cv=KFold(4, shuffle=True, random_state=1),
                              n_jobs=-1)
    return IsotonicRegression(y_min=1, y_max=len(COLOR_NAMES), out_of_bounds="clip").fit(inner, y)


def _fit(kind, calibrate, X, y, weights=None):
    m = _make(kind, weights).fit(X, y)
    cal = _calibrator(kind, X, y, weights) if calibrate else None
    return m, cal


def _predict(kind, m, cal, X):
    """(position on the scale, hard colour) for each row."""
    if kind == "classes":
        pr = m.predict_proba(X)
        return pr @ m.classes_.astype(float), m.classes_[pr.argmax(axis=1)]
    pos = m.predict(X)
    if cal is not None:
        pos = cal.predict(pos)
    pos = np.clip(pos, 1, len(COLOR_NAMES))
    return pos, np.clip(np.round(pos), 1, len(COLOR_NAMES)).astype(int)


def _cv(kind, calibrate, X, y, folds, weights=None):
    """Every track predicted by a model that never saw it."""
    from sklearn.model_selection import StratifiedKFold

    from joblib import Parallel, delayed

    def one(tr, te):
        m, cal = _fit(kind, calibrate, X[tr], y[tr], weights)
        return te, _predict(kind, m, cal, X[te])

    pos = np.zeros(len(y))
    hard = np.zeros(len(y), dtype=int)
    # folds in parallel; joblib runs the n_jobs=-1 inside each fold sequentially, so no oversubscription
    splits = StratifiedKFold(folds, shuffle=True, random_state=0).split(X, y)
    for te, (p, h) in Parallel(n_jobs=-1)(delayed(one)(tr, te) for tr, te in splits):
        pos[te], hard[te] = p, h
    return pos, hard


def _metrics(pos: np.ndarray, y: np.ndarray, hard: np.ndarray) -> dict:
    err = np.abs(pos - y)
    return {
        "exact": round(float((hard == y).mean()), 3),
        "within_one_colour": round(float((np.abs(hard - y) <= 1).mean()), 3),
        "average_error_in_colours": round(float(err.mean()), 2),
        # every colour counts the same, however many tracks it has: a model that plays safe with the
        # middle colours scores well on the plain average but badly here, because it misses Red/Magenta
        "average_error_per_colour": round(float(np.mean([err[y == c].mean() for c in np.unique(y)])), 2),
    }


def _deep_one(path: str):
    """Runs in a worker process."""
    try:
        maest = analysis.deep_features(Path(path))[0]
        return path, maest is not None, None
    except Exception as err:
        return path, False, f"{type(err).__name__}: {err}"


def extract_deep(paths: list[str], workers: int | None = None, progress=log) -> dict:
    """Make sure every track has MAEST, mood, instrument and extra features cached (parallel, resumable)."""
    if analysis.instrument_model() is None or analysis.voice_model() is None:
        progress(f"  instrument/vocals model not available ({analysis._instr.get('why', 'not downloaded; run setup')})")
    # Files deleted since the scan still have a row in collection.db; the cache lookup would stat them
    todo = [p for p in paths if Path(p).exists() and analysis.deep_missing(analysis.deep_get(Path(p)))]
    if not todo:
        return {"extracted": 0}
    if workers is None:
        workers = max(1, min(5, (os.cpu_count() or 4) // 2))
    workers = max(1, min(workers, len(todo)))
    progress(f"Deep listening pass: {len(todo)} tracks, {workers} workers. Runs once; Ctrl-C is safe, "
             "re-run to continue.")
    t0, done, errors = time.time(), 0, 0
    pool = None
    try:
        if workers == 1:
            results = map(_deep_one, todo)
        else:
            import multiprocessing as mp

            pool = mp.get_context("spawn").Pool(workers, initializer=collection._worker_init)
            results = pool.imap_unordered(_deep_one, todo, chunksize=1)
        for path, ok, err in results:
            done += 1
            if err:
                errors += 1
            if done % 10 == 0 or done == len(todo):
                rate = done / max(time.time() - t0, 1e-6)
                progress(f"  [{done}/{len(todo)}  ~{analysis.fmt_time((len(todo) - done) / rate)} left]")
    finally:
        if pool:
            pool.close()
            pool.join()
    return {"extracted": done, "errors": errors, "minutes": round((time.time() - t0) / 60, 1)}


def train(nml: str | None = None, progress=log, deep: bool = True, workers: int | None = None) -> dict:
    import joblib

    nml_path, colors, n_coloured = read_traktor_colors(nml)
    if not colors:
        raise SystemExit(f"No coloured tracks found in {nml_path}.")
    con = collection.db()
    rows = {r["path"]: dict(r) for r in con.execute(
        f"SELECT path, filename, {', '.join(INFO_COLS)} FROM tracks "
        "WHERE (error IS NULL OR error LIKE 'tags:%') AND type='track'")}
    guesses = collection.unchecked_guesses(colors)
    pairs = [(p, c) for p, c in colors.items() if p in rows and p not in guesses]
    progress(f"{n_coloured} coloured tracks in Traktor, {len(pairs)} of them scanned"
             + (f" and yours ({len(guesses)} guessed by the app, not checked yet, left out)" if guesses else ""))
    if len(pairs) < 30:
        raise SystemExit("Need at least 30 coloured tracks that are also scanned. Scan the folders your "
                         "Traktor tracks live in first: uv run dj-track-analyzer scan <folder>")

    deep_info = {}
    if deep:
        deep_info = extract_deep(sorted(rows), workers, progress)  # all scanned tracks, so new ones get a colour too
    maest_count = instr_count = extra_count = 0
    inputs, y, paths, instr_rows = [], [], [], []
    for i, (p, c) in enumerate(pairs, 1):
        try:
            _d, emb_mean, acts_mean = analysis.track_embedding(Path(p))  # cached from the scan
        except Exception as err:
            progress(f"  skip {Path(p).name}: {err}")
            continue
        info, maest, instruments, extra = dict(rows[p]), None, None, None
        if deep:
            hit = analysis.deep_get(Path(p))
            if hit:
                maest, instruments, extra = hit[0], hit[2], hit[3]
                info.update({f"mood_{m}": v for m, v in hit[1].items()})
                rows[p].update({f"mood_{m}": v for m, v in hit[1].items()})
        maest_count += maest is not None
        instr_count += bool(instruments) and not analysis.instruments_missing(instruments)
        extra_count += extra is not None
        inputs.append(analysis.color_inputs(emb_mean, acts_mean, info, maest, instruments, extra))
        instr_rows.append(instruments or {})
        y.append(c)
        paths.append(p)
        if i % 100 == 0:
            progress(f"  loaded {i}/{len(pairs)}")
    y = np.array(y)
    counts = {COLOR_NAMES[c - 1]: int((y == c).sum()) for c in range(1, len(COLOR_NAMES) + 1)}
    used = sorted(set(y.tolist()))
    min_count = min(int((y == c).sum()) for c in used)
    if len(used) < 2 or min_count < 2:
        raise SystemExit(f"Need at least 2 tracks in each colour you use. Counts: {counts}")
    folds = max(2, min(5, min_count))
    Xs = {fs: np.vstack([analysis.color_features(inp, fs) for inp in inputs]) for fs in analysis.FEATURE_SETS}

    # For context: always guessing the most typical (median) colour.
    med = float(np.median(y))
    baseline = _metrics(np.full(len(y), med), y, np.full(len(y), int(round(med))))
    progress(f"  baseline, always guess {COLOR_NAMES[int(round(med)) - 1]}: {baseline}")

    have_maest = maest_count >= 0.95 * len(y)
    if deep and not have_maest:
        why = analysis._maest.get("why", "missing for some tracks")
        progress(f"  MAEST features available for {maest_count}/{len(y)} tracks ({why}); skipping MAEST models")
    have_instr = deep and bool(analysis.instrument_features()) and instr_count >= 0.95 * len(y)
    if deep and not have_instr:
        progress(f"  instrument scores available for {instr_count}/{len(y)} tracks; skipping instrument models")
    have_extra = deep and extra_count >= 0.95 * len(y)
    results = {}
    for name, (fs, kind, cal) in CANDIDATES.items():
        if "maest" in fs and not have_maest:
            continue
        if "instruments" in analysis.FEATURE_SETS[fs] and not have_instr:
            continue
        if "arc" in analysis.FEATURE_SETS[fs] and not have_extra:
            continue
        try:
            pos, hard = _cv(kind, cal, Xs[fs], y, folds, block_weights(fs, inputs[0]))
        except Exception as err:
            progress(f"  {name}: failed ({err})")
            continue
        results[name] = (pos, hard, _metrics(pos, y, hard))
        progress(f"  {name}: {results[name][2]}")
    # Chosen by the per-colour average, so the ends of the scale (your favourites) count as much as the
    # crowded middle. Picking the best of several on the same folds flatters its numbers a little.
    best = min(results, key=lambda k: (results[k][2]["average_error_per_colour"],
                                       results[k][2]["average_error_in_colours"]))
    pos, hard, metrics = results[best]
    fs, kind, calibrate = CANDIDATES[best]

    per_colour = {COLOR_NAMES[c - 1]: {"exact": round(float((hard[y == c] == c).mean()), 2),
                                       "within_one": round(float((np.abs(hard[y == c] - c) <= 1).mean()), 2)}
                  for c in used}
    disagreements = sorted(
        ({"file": Path(p).name, "path": p, "your_colour": COLOR_NAMES[a - 1],
          "sounds_like": analysis.describe_position(ps)["color"], "position": round(float(ps), 2),
          "gap": round(abs(float(ps) - a), 2)}
         for p, a, ps in zip(paths, y, pos) if abs(ps - a) >= 1.5),
        key=lambda d: -d["gap"])

    # Check that Traktor's colour numbers run Red → Magenta the way we assume: these averages
    # should move steadily along the scale.
    sanity = {}
    for c in used:
        rs = [rows[p] for p, cc in zip(paths, y) if cc == c]

        def avg(k, rs=rs):
            v = [r[k] for r in rs if r[k] is not None]
            return round(float(np.mean(v)), 2) if v else None

        minor = [r["key"] for r in rs if r["key"]]
        sanity[COLOR_NAMES[c - 1]] = {
            "tracks": len(rs), "avg_bpm": avg("bpm"), "avg_lufs": avg("lufs"),
            "avg_aggressive": avg("mood_aggressive"), "avg_happy": avg("mood_happy"),
            "minor_key_share": round(sum(k.endswith("minor") for k in minor) / len(minor), 2) if minor else None,
        }

    instrument_check = _instrument_check(instr_rows, y, used) if have_instr else None

    progress(f"  best: {best}. Fitting it on all {len(y)} tracks...")
    model, cal = _fit(kind, calibrate, Xs[fs], y, block_weights(fs, inputs[0]))
    COLOR_MODEL.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "calibrator": cal, "kind": kind, "feature_set": fs, "name": best,
                 "instruments": analysis.instrument_features() if "instruments" in analysis.FEATURE_SETS[fs] else None,
                 "trained_at": time.strftime("%Y-%m-%d %H:%M"), "tracks": int(len(y)), "counts": counts,
                 "metrics": metrics, "baseline": baseline, "disagreements": disagreements[:100],
                 # cross-validated positions, for analysis.color_confidence
                 "cv": {"position": [round(float(v), 3) for v in pos], "truth": [int(v) for v in y]},
                 # the colours it learned from, so new_labels() can tell when there's something new
                 "labels": dict(pairs), "collection": str(nml_path)}, COLOR_MODEL)
    analysis._color_bundle.clear()

    updated = refresh_predictions(con, traktor_colors=colors, progress=progress)
    from . import tempo

    views = tempo.refresh_all_views(progress)  # their colours came from the previous model (train refuses while one is loaded)
    return {
        "trained_on": int(len(y)), "tracks_per_colour": counts,
        **({"guessed_colours_left_out": len(guesses)} if guesses else {}),
        "colour_scale_check": sanity,
        **({"instruments_vs_colour": instrument_check} if instrument_check else {}),
        "baseline_always_guess_middle": baseline,
        "chosen_by": "average_error_per_colour (each colour weighs the same)",
        "all_models": {k: v[2] for k, v in results.items()},
        "chosen_model": best, "accuracy_cross_validated": metrics, "accuracy_per_colour": per_colour,
        "maest_features": f"{maest_count}/{len(y)} tracks" if deep else "off (--no-deep)",
        "instrument_features": f"{instr_count}/{len(y)} tracks" if deep else "off (--no-deep)",
        "biggest_disagreements": [{k: d[k] for k in ("file", "your_colour", "sounds_like", "position")}
                                  for d in disagreements[:10]],
        "predictions_updated": updated, "saved_to": str(COLOR_MODEL),
        **({"tempo_views_refreshed": views} if views else {}),
    }


def usable_labels(colors: dict[str, int]) -> dict[str, int]:
    """The colours `train` learns from, given Traktor's {path: COLOR}: scanned tracks, unchecked guesses left out."""
    if not collection.NATIVE_DB.exists():
        return {}
    con = collection.db()
    rows = {r[0] for r in con.execute("SELECT path FROM tracks WHERE (error IS NULL OR error LIKE 'tags:%') AND type='track'")}
    guesses = collection.unchecked_guesses(colors)
    return {p: c for p, c in colors.items() if p in rows and p not in guesses}


def guessed(accept: str | None = None, nml: str | None = None) -> dict:
    """The colours the app wrote into Traktor that nobody has checked (`train` leaves them out). accept: mark the
    ones whose path contains this text right (at most 10, so a typo can't accept half the collection), or "all"."""
    _path, colors, _n = read_traktor_colors(nml)
    g = collection.unchecked_guesses(colors)
    out = {}
    if accept:
        hit = list(g) if accept.strip().lower() == "all" else [p for p in g if accept.lower() in p.lower()]
        if not hit:
            raise SystemExit(f'No guessed colour has "{accept}" in its path.')
        if len(hit) > 10 and accept.strip().lower() != "all":
            raise SystemExit(f'"{accept}" matches {len(hit)} guessed colours; use more of the name, or `all`.')
        collection.accept_guesses(hit)
        out["accepted"] = sorted(Path(p).name for p in hit)
        g = {p: c for p, c in g.items() if p not in hit}
    out["guessed_not_checked"] = [{"file": Path(p).name, "colour": COLOR_NAMES[c - 1]} for p, c in sorted(g.items())]
    out["note"] = ("Change a guessed colour in Traktor, or accept it (colors guessed --accept TEXT, or the Tracks tab), "
                   "and colors train learns from it.")
    return out


def new_labels(colors: dict[str, int]) -> dict | None:
    """What `train` would learn that your own model hasn't: tracks coloured since (added), recoloured (changed) or
    no longer coloured (removed), given Traktor's {path: COLOR}. None with the starter or a model from before this."""
    b = analysis.color_model()
    if not b or b.get("starter") or "labels" not in b:
        return None
    now, then = usable_labels(colors), b["labels"]
    return {"added": sum(p not in then for p in now), "changed": sum(p in then and then[p] != c for p, c in now.items()),
            "removed": sum(p not in now for p in then), "trained_at": b.get("trained_at")}


def _instrument_check(instr_rows: list[dict], y: np.ndarray, used: list[int], top: int = 10) -> dict:
    """The instruments (and vocals) that move most with your colours: rank correlation with the
    Red (1) → Magenta (7) scale (negative = more on the Red end), and the average score per colour."""
    from scipy.stats import spearmanr

    out = []
    for name in analysis.instrument_features():
        v = np.array([r.get(name, 0.0) for r in instr_rows])
        if v.std() < 1e-6:
            continue
        rho = float(spearmanr(v, y)[0])
        if np.isnan(rho):
            continue
        out.append((name, rho, {COLOR_NAMES[c - 1]: round(float(v[y == c].mean()), 3) for c in used}))
    out.sort(key=lambda t: -abs(t[1]))
    return {name: {"correlation": round(rho, 2), **avgs} for name, rho, avgs in out[:top]}


def refresh_predictions(con, traktor_colors: dict[str, int] | None = None, progress=log,
                        csv_out: Path | None = collection.DEFAULT_CSV, own_bpm: dict[str, float] | None = None) -> int:
    """Recompute the predicted colour for every scanned track (uses cached fingerprints) and
    record each track's current Traktor colour in the database. A track the model can't place now
    (not in the cache, or missing a feature) loses its old prediction rather than keep a stale one.
    own_bpm: {path: BPM} for the colour model instead of the bpm column (a tempo view passes the BPM
    we measured at each track's own tempo, as in the normal analysis the model was trained on)."""
    rows = [dict(r) for r in con.execute(
        f"SELECT path, {', '.join(INFO_COLS)} FROM tracks WHERE (error IS NULL OR error LIKE 'tags:%') AND type='track'")
        ] if analysis.color_model() is not None else []  # no model: leave the predictions alone
    n = 0
    for r in rows:
        p = Path(r["path"])
        if own_bpm is not None:
            r["bpm"] = own_bpm.get(r["path"])
        hit = analysis.cache_get(p) if p.exists() else None
        if not hit:  # gone, or not analysed with the current models (the next scan fills it in)
            con.execute("UPDATE tracks SET color=NULL, color_position=NULL, color_confidence=NULL WHERE path=?",
                        (r["path"],))
            continue
        maest = instruments = extra = None
        deep = analysis.deep_get(p)
        if deep:
            maest, instruments, extra = deep[0], deep[2], deep[3]
            r.update({f"mood_{m}": v for m, v in deep[1].items()})
            if deep[1]:
                con.execute(f"UPDATE tracks SET {', '.join(f'mood_{m}=?' for m in analysis.MOODS)} WHERE path=?",
                            [deep[1].get(m) for m in analysis.MOODS] + [r["path"]])
            if instruments:
                con.execute("UPDATE tracks SET instruments=? WHERE path=?",
                            (collection.instruments_text(instruments), r["path"]))
            if deep[3] and deep[3].get("approachability") is not None:
                con.execute("UPDATE tracks SET approachability=? WHERE path=?",
                            (deep[3]["approachability"], r["path"]))
        col = analysis.predict_color(hit[1], hit[2], r, maest, instruments, extra) or {}
        con.execute("UPDATE tracks SET color=?, color_position=?, color_confidence=? WHERE path=?",
                    (col.get("color"), col.get("position"), col.get("confidence"), r["path"]))
        n += bool(col)
    if traktor_colors is not None:
        con.execute("UPDATE tracks SET traktor_color=NULL")
        con.executemany("UPDATE tracks SET traktor_color=? WHERE path=?",
                        [(COLOR_NAMES[c - 1], p) for p, c in traktor_colors.items()])
    con.commit()
    if csv_out:
        collection.export_csv(csv_out)  # so the spreadsheet shows the new predictions
    return n


def review(limit: int = 25, apply: bool = False, dry_run: bool = False, min_gap: float = 2.5,
           min_confidence: float = 0.75, nml: str | None = None, ask: bool = False) -> dict:
    """apply=True recolours, in Traktor, the reviewed tracks with gap >= min_gap and confidence >=
    min_confidence to the colour they sound like. Uses the cross-validated predictions (made without
    seeing the track's own colour), not the final model's. ask=True asks about each track first."""
    b = analysis.color_model()
    if b is None:
        raise SystemExit("No colour model yet. Run: uv run dj-track-analyzer colors train")
    if b.get("starter"):
        raise SystemExit("Review compares your colours with a model trained on them; the starter model learned "
                         "another DJ's. Train your own first: uv run dj-track-analyzer colors train")
    scale = b.get("kind") != "classes"
    tracks, changes = [], {}
    if scale and not b.get("cv"):
        raise SystemExit("This colour model is from before confidence was measured properly. "
                         "Retrain it: uv run dj-track-analyzer colors train")
    keep_forever = _kept_forever()
    for i, d in enumerate(b["disagreements"]):
        if apply and d["path"] in keep_forever:
            continue  # plain review still lists them, marked "kept"
        pos = d["position"]
        # how often tracks placed here really were this colour or a neighbour of it
        conf = analysis.color_confidence(b, pos, COLOR_NAMES.index(d["sounds_like"]) + 1, within=1) if scale else None
        chosen = bool(scale and d["gap"] >= min_gap and conf >= min_confidence)  # numpy bool isn't JSON
        if chosen:
            changes[d["path"]] = (COLOR_NAMES.index(d["your_colour"]) + 1, COLOR_NAMES.index(d["sounds_like"]) + 1)
        if (chosen if apply else i < limit):  # --apply lists only the tracks it would recolour
            t = {k: d[k] for k in ("file", "your_colour", "sounds_like", "position")}
            t["gap"] = d["gap"]
            if scale:
                t["confidence"] = conf
            t["meets_apply_threshold"] = chosen
            if d["path"] in keep_forever:
                t["kept"] = True  # you answered "keep always": --apply leaves it alone
            t["path"] = d["path"]
            tracks.append(t)
    out = {"trained_at": b["trained_at"], "model": b.get("name"), "accuracy": b["metrics"],
           "note": "Tracks whose sound (in cross-validation, i.e. without having seen them) points to a "
                   "colour at least 1.5 steps from the one you gave them, biggest gap first. Some will "
                   "be the model's mistakes, some may be worth recolouring. gap = distance in colours "
                   "between yours and the prediction. confidence = how often tracks the model placed at this "
                   "position (in cross-validation) really were that colour or a neighbour of it.",
           "apply_threshold": {"min_gap": min_gap, "min_confidence": min_confidence,
                               "tracks_meeting_it": len(changes)},
           "tracks": tracks}
    if keep_forever:
        out["always_kept"] = len(keep_forever)
    if apply:
        if not scale:
            raise SystemExit("--apply needs a scale model (this one treats colours as classes).")
        if ask and changes and not dry_run:
            if traktor.traktor_running():  # say so before the questions, not after them
                raise SystemExit("Traktor is running. Quit Traktor first: it rewrites collection.nml when it "
                                 "closes and would undo these changes.")
            names = {t["path"]: t["file"] for t in tracks}
            asked = changes
            changes, out["kept_your_colour"], forever = _ask_each(changes, names)
            _keep_forever(forever, {p: old for p, (old, _new) in asked.items()})
            said = lambda p: {"yours": COLOR_NAMES[asked[p][0] - 1], "model": COLOR_NAMES[asked[p][1] - 1]}  # noqa: E731
            collection.log_feedback("colour_keep", [(p, said(p)) for p in forever])
            collection.log_feedback("colour_no", [(p, said(p)) for p in asked if p not in changes and p not in forever])
        out["traktor"] = traktor.set_colors(changes, nml, dry_run=dry_run)
        if not dry_run and out["traktor"].get("changed"):
            out["undo"] = "uv run dj-track-analyzer traktor --undo"
    else:
        out["apply_hint"] = (f"uv run dj-track-analyzer colors review --apply --dry-run  (previews recolouring the "
                             f"{len(changes)} tracks with gap >= {min_gap} and confidence >= {min_confidence})")
    return out


def _kept_forever() -> set[str]:
    """Tracks you answered "always keep" for: review --apply never suggests recolouring them again."""
    return collection.held("colour")


def kept(forget: str | None = None, keep: str | None = None, nml: str | None = None) -> dict:
    """The tracks you answered "always keep" for. forget: take the ones whose path contains this text
    off the list, so review --apply can suggest them again. keep: add the coloured tracks whose path
    contains this text, with their colour in Traktor now."""
    _kept_forever()  # creates the table
    out = {}
    if keep:
        _nml, colors, _n = read_traktor_colors(nml)
        scanned = {r[0] for r in collection.db().execute("SELECT path FROM tracks")}  # one path per track
        hits = {p: c for p, c in colors.items() if keep.lower() in p.lower() and p in scanned}
        if not hits:
            raise SystemExit(f'No coloured track in Traktor has "{keep}" in its path.')
        if len(hits) > 10:  # a typo like "e" shouldn't quietly keep half the collection
            raise SystemExit(f'"{keep}" matches {len(hits)} tracks; use more of the name.')
        _keep_forever(list(hits), hits)
        out["added"] = [{"file": Path(p).name, "your_colour": COLOR_NAMES[c - 1]} for p, c in hits.items()]
    con = collection.db()
    rows = [dict(r) for r in con.execute("SELECT path, colour, at FROM color_keep ORDER BY at, path")]
    if forget:
        gone = [r for r in rows if forget.lower() in r["path"].lower()]
        con.executemany("DELETE FROM color_keep WHERE path=?", [(r["path"],) for r in gone])
        con.commit()
        out["forgotten"] = [Path(r["path"]).name for r in gone]
        rows = [r for r in rows if r not in gone]
    out["always_kept"] = [{"file": Path(r["path"]).name, "your_colour": r["colour"], "kept_since": r["at"],
                           "path": r["path"]} for r in rows]
    return out


def _keep_forever(paths: list[str], olds: dict[str, int]) -> None:
    if paths:
        collection.set_hold("colour", {p: COLOR_NAMES[olds[p] - 1] for p in paths})


def _ask_each(changes: dict[str, tuple[int, int]], names: dict[str, str]):
    """Ask about each recolour on the terminal: yes, no, keep always (never ask again), all the rest,
    or cancel (nothing written). Questions go to stderr so the JSON on stdout stays clean."""
    import sys

    keep, skipped, forever, take_all = {}, [], [], False
    for n, (p, (old, new)) in enumerate(changes.items(), 1):
        if not take_all:
            q = (f"[{n}/{len(changes)}] {names.get(p, Path(p).name)}\n"
                 f"  {COLOR_NAMES[old - 1]} -> {COLOR_NAMES[new - 1]}?  [y]es / [n]o / [k]eep always / [a]ll the rest / [c]ancel: ")
            while True:
                print(q, end="", file=sys.stderr, flush=True)
                a = (sys.stdin.readline() or "c").strip().lower()[:1]
                if a in ("y", "n", "k", "a", "c"):
                    break
            if a == "c":
                raise SystemExit("Cancelled, nothing changed.")
            if a in ("n", "k"):
                skipped.append(names.get(p, Path(p).name))
                if a == "k":
                    forever.append(p)
                continue
            take_all = a == "a"
        keep[p] = (old, new)
    return keep, skipped, forever


# ----------------------------------------------------------------------------
# explain: what the colours mean in this collection
# ----------------------------------------------------------------------------

# (label, where the value comes from, key). Sources: db = collection.db row, mood = deep moods,
# inst = deep instruments, extra = deep extras, nml = Traktor's own data.
PROFILE = [
    ("approachability", "extra", "approachability"), ("engagement", "extra", "engagement"),
    ("aggressive", "mood", "aggressive"), ("happy", "mood", "happy"), ("party", "mood", "party"),
    ("relaxed", "mood", "relaxed"), ("sad", "mood", "sad"),
    ("piano", "inst", "piano"), ("bell", "inst", "bell"), ("strings", "inst", "strings"),
    ("guitar", "inst", "guitar"), ("bass", "inst", "bass"), ("computer", "inst", "computer"),
    ("synthesizer", "inst", "synthesizer"), ("pad", "inst", "pad"), ("vocal share", "inst", "vocal_share"),
    ("drop lift dB", "extra", "drop_lift_db"), ("intro s", "extra", "intro_sec"),
    ("breakdown share", "extra", "breakdown_share"), ("longest break s", "extra", "longest_break_sec"),
    ("bpm", "db", "bpm"), ("lufs", "db", "lufs"), ("minor key", "db", "minor"),
    ("length min", "db", "minutes"), ("plays (median)", "nml", "plays"), ("purchased", "nml", "purchased"),
]


def _key_location(key: str) -> dict:
    """A playlist PRIMARYKEY like 'Macintosh HD/:Users/:me/:x.mp3' as LOCATION attributes."""
    i = key.find("/:")
    if i == -1:
        return {}
    vol, rest = key[:i], key[i:]
    j = rest.rfind("/:")
    return {"VOLUME": vol, "DIR": rest[:j + 2], "FILE": rest[j + 2:]}


def _artists(credit: str | None) -> list[str]:
    parts = re.split(r",|&| feat\.? | ft\.? | x | vs\.? ", credit or "", flags=re.I)
    return [a.strip().lower() for a in parts if a.strip()]


def explain(nml: str | None = None, workers: int | None = None, progress=log, extract: bool = True) -> dict:
    """Per-colour profile of the user's coloured tracks: sound, energy, Traktor usage, playlists,
    position in sets and artists. Read-only for Traktor's files. extract=False skips the deep listening
    pass for tracks without deep features (they're left out of the profile instead)."""
    import xml.etree.ElementTree as ET
    from collections import Counter, defaultdict

    from scipy.stats import spearmanr

    nml_path, colors, _n = read_traktor_colors(nml)
    con = collection.db()
    rows = {r["path"]: dict(r) for r in con.execute(
        "SELECT path, artist, bpm, lufs, key, duration_sec FROM tracks "
        "WHERE (error IS NULL OR error LIKE 'tags:%') AND type='track'")}
    # Only scanned tracks, so recordings, samples and other excluded folders never count anywhere
    # below (playlists and History included).
    colors = {p: c for p, c in colors.items() if p in rows}
    paths = sorted(colors)
    if len(paths) < 30:
        raise SystemExit("Need at least 30 coloured tracks that are also scanned: uv run dj-track-analyzer scan --traktor")
    if extract:
        extract_deep(paths, workers, progress)

    # Traktor's own data: play counts, comments, playlists, history.
    root = ET.parse(nml_path).getroot()
    nml_info = {}
    for e in root.find("COLLECTION").iter("ENTRY"):
        loc, info = e.find("LOCATION"), e.find("INFO")
        if loc is None or info is None:
            continue
        for p in traktor.location_paths(loc.attrib):
            nml_info[p] = info.attrib

    # History and playlists can point to where a file used to be; fall back to the file name
    # when only one coloured track has it.
    by_name = defaultdict(set)
    for p, c in colors.items():
        by_name[Path(p).name.lower()].add(c)

    def colour_of(loc: dict):
        c = next((colors[p] for p in traktor.location_paths(loc) if p in colors), None)
        if c is None:
            cs = by_name.get((loc.get("FILE") or "").lower(), set())
            c = next(iter(cs)) if len(cs) == 1 else None
        return c

    # One value table per track.
    y, values = [], defaultdict(list)
    used_paths = []
    for p in paths:
        hit = analysis.deep_get(Path(p))
        if not hit:
            continue
        r, info = rows[p], nml_info.get(p, {})
        src = {"mood": hit[1] or {}, "inst": hit[2] or {}, "extra": hit[3] or {},
               "db": {"bpm": r["bpm"], "lufs": r["lufs"], "minutes": (r["duration_sec"] or 0) / 60 or None,
                      "minor": (1.0 if (r["key"] or "").endswith("minor") else 0.0) if r["key"] else None},
               "nml": {"plays": float(info.get("PLAYCOUNT") or 0),
                       "purchased": 1.0 if "purchased" in (info.get("COMMENT") or "").lower() else 0.0}}
        for label, where, key in PROFILE:
            v = src[where].get(key)
            values[label].append(np.nan if v is None else float(v))
        y.append(colors[p])
        used_paths.append(p)
    y = np.array(y)
    used = sorted(set(y.tolist()))
    names = [COLOR_NAMES[c - 1] for c in used]

    profile, correlations = {}, []
    for label, _w, _k in PROFILE:
        v = np.array(values[label])
        ok = ~np.isnan(v)
        if ok.sum() < 20:
            continue
        agg = np.nanmedian if label.startswith("plays") else np.nanmean
        profile[label] = {COLOR_NAMES[c - 1]: (round(float(agg(v[y == c])), 3) if np.any(ok & (y == c)) else None)
                          for c in used}
        if label.startswith("plays") or label == "purchased":
            continue  # how you use tracks, not how they sound; shown in the table only
        if np.nanstd(v) > 1e-9:
            rho = float(spearmanr(v[ok], y[ok])[0])
            if not np.isnan(rho):
                correlations.append((label, round(rho, 2)))
    correlations.sort(key=lambda t: -abs(t[1]))

    # Playlists (your own crates), only those with 3+ coloured tracks.
    share = {c: float((y == c).mean()) for c in used}
    playlists = []
    for node in root.iter("NODE"):
        if node.get("TYPE") != "PLAYLIST":
            continue
        cs = [c for pk in node.iter("PRIMARYKEY") if (c := colour_of(_key_location(pk.get("KEY", ""))))]
        if len(cs) < 3:
            continue
        cnt = Counter(cs)
        playlists.append({
            "playlist": node.get("NAME"), "tracks": len(cs), "average": round(float(np.mean(cs)), 1),
            "counts": {COLOR_NAMES[c - 1]: cnt.get(c, 0) for c in used},
            "stands_out": [COLOR_NAMES[c - 1] for c in used if cnt.get(c, 0) >= 2 and cnt[c] / len(cs) > 2 * share[c]],
        })
    playlists.sort(key=lambda d: d["average"])

    # Sets: where in a session each colour gets played (Traktor's History folder).
    pos = defaultdict(list)
    sessions = 0
    for f in sorted(nml_path.parent.glob("History/*.nml")):
        try:
            hroot = ET.parse(f).getroot()
        except ET.ParseError:
            continue
        coll = hroot.find("COLLECTION")
        if coll is None:
            continue
        seq = []
        for n, e in enumerate(coll.iter("ENTRY")):
            loc, ext = e.find("LOCATION"), e.find("EXTENDEDDATA")
            c = colour_of(loc.attrib) if loc is not None else None
            if c:
                t = (float(ext.get("STARTDATE", 0) or 0) * 86400 + float(ext.get("STARTTIME", 0) or 0)
                     if ext is not None else n)
                seq.append((t, c))
        if len(seq) < 8:
            continue
        sessions += 1
        seq.sort()
        for i, (_t, c) in enumerate(seq):
            pos[c].append(i / (len(seq) - 1))
    sets = {}
    for c in used:
        v = pos.get(c, [])
        if v:
            thirds = Counter(min(2, int(x * 3)) for x in v)
            sets[COLOR_NAMES[c - 1]] = {"plays": len(v), "first_third": round(thirds[0] / len(v), 2),
                                        "middle_third": round(thirds[1] / len(v), 2),
                                        "last_third": round(thirds[2] / len(v), 2)}

    # Artists: are an artist's tracks closer in colour than random tracks?
    by_artist = defaultdict(list)
    for p, c in zip(used_paths, y):
        for a in _artists(rows[p]["artist"]):
            by_artist[a].append(int(c))
    multi = {a: v for a, v in by_artist.items() if len(v) >= 3}
    artists = None
    if multi:
        def spread(groups):
            return float(np.mean([abs(x - np.mean(g)) for g in groups for x in g]))
        rng = np.random.default_rng(0)
        pool = np.concatenate([np.array(v) for v in multi.values()])
        rand = []
        for _ in range(200):
            sh = rng.permutation(pool)
            cuts = np.cumsum([len(v) for v in multi.values()])[:-1]
            rand.append(spread(np.split(sh, cuts)))
        artists = {
            "artists_with_3_plus": len(multi),
            "spread_within_artist": round(spread(multi.values()), 2),
            "spread_random_groups": round(float(np.mean(rand)), 2),
            "top": [{"artist": a, "tracks": len(v), "average": round(float(np.mean(v)), 1),
                     "average_colour": COLOR_NAMES[int(round(np.mean(v))) - 1]}
                    for a, v in sorted(multi.items(), key=lambda kv: -len(kv[1]))[:15]],
        }

    return {
        "tracks": int(len(y)), "colours": names,
        "tracks_per_colour": {COLOR_NAMES[c - 1]: int((y == c).sum()) for c in used},
        "profile": profile,
        "correlations": dict(correlations),
        "strongest_links": [{"feature": f, "correlation": r} for f, r in correlations[:10]],
        "playlists": playlists,
        "sets": {"sessions": sessions, "by_colour": sets},
        "artists": artists,
        "notes": [
            "profile: averages per colour (plays: median). correlation: Spearman with Red=1 … Magenta=7, "
            "so + means more of it toward Magenta.",
            "instrument scores come from a tagger trained on non-club music: compare colours, don't read "
            "them as absolute amounts.",
        ],
    }


def explain_text(d: dict) -> str:
    names = d["colours"]
    short = [n[:3] for n in names]
    out = [f"{d['tracks']} coloured tracks: " + ", ".join(f"{n} {d['tracks_per_colour'][n]}" for n in names), ""]

    def fmt(v):
        if v is None:
            return "-"
        a = abs(v)
        return f"{v:.0f}" if a >= 100 else f"{v:.1f}" if a >= 10 else f"{v:.2f}"

    corr = d["correlations"]
    out.append("PROFILE PER COLOUR (corr: + = more toward Magenta)")
    out.append(f"  {'':16} {'corr':>5}  " + " ".join(f"{s:>6}" for s in short))
    for label, per in d["profile"].items():
        c = corr.get(label)
        out.append(f"  {label:16} {'' if c is None else f'{c:+.2f}':>5}  "
                   + " ".join(f"{fmt(per.get(n)):>6}" for n in names))
    out += ["", "STRONGEST LINKS WITH YOUR SCALE"]
    out += [f"  {r['feature']:16} {r['correlation']:+.2f}" for r in d["strongest_links"][:10]]
    if d["playlists"]:
        out += ["", "PLAYLISTS (sorted hard → pretty; * = colour stands out in this list)"]
        out.append(f"  {'':30} {'n':>3} {'avg':>4}  " + " ".join(f"{s:>4}" for s in short))
        for p in d["playlists"]:
            cells = " ".join(f"{p['counts'][n]:>3}{'*' if n in p['stands_out'] else ' '}" for n in names)
            out.append(f"  {p['playlist'][:30]:30} {p['tracks']:>3} {p['average']:>4}  {cells}")
    s = d["sets"]
    if s["by_colour"]:
        out += ["", f"WHERE IN A SET ({s['sessions']} sessions from Traktor's History)"]
        out.append(f"  {'':8} {'plays':>5}  {'first third':>11} {'middle':>7} {'last third':>10}")
        for n in names:
            v = s["by_colour"].get(n)
            if v:
                out.append(f"  {n:8} {v['plays']:>5}  {v['first_third']:>11.0%} {v['middle_third']:>7.0%} "
                           f"{v['last_third']:>10.0%}")
    a = d["artists"]
    if a:
        out += ["", f"ARTISTS ({a['artists_with_3_plus']} with 3+ coloured tracks): colours within one artist "
                    f"differ by {a['spread_within_artist']} on average, vs {a['spread_random_groups']} for random tracks"]
        out += [f"  {t['artist'][:28]:28} {t['tracks']:>3}  avg {t['average']:.1f} ({t['average_colour']})"
                for t in a["top"]]
    out += [""] + [f"Note: {n}" for n in d["notes"]]
    return "\n".join(out)


# ----------------------------------------------------------------------------
# apply: colour new tracks in one step
# ----------------------------------------------------------------------------


def apply(nml: str | None = None, dry_run: bool = False, min_confidence: float = 0.0,
          workers: int | None = None, progress=log) -> dict:
    """Scan tracks that are new in Traktor's collection, predict their colours, and write a colour to
    tracks that don't have one. Colours you set are never changed. Genres and Smartlists are left alone."""
    if analysis.color_model() is None:
        raise SystemExit("No colour model yet. Run: uv run dj-track-analyzer colors train")
    if not dry_run and traktor.traktor_running():
        raise SystemExit("Traktor is running. Quit Traktor first: it rewrites collection.nml when it closes "
                         "and would undo the colours. (Or use --dry-run to preview.)")
    scan = collection.scan([], workers=workers, traktor_nml=nml, use_traktor=True)
    nml_path, colors, _n = read_traktor_colors(nml)
    con = collection.db()
    # Tracks without a colour that were scanned earlier (e.g. before training) may still lack the
    # deep features the colour model needs.
    uncoloured = [r["path"] for r in con.execute(
        "SELECT path FROM tracks WHERE (error IS NULL OR error LIKE 'tags:%') AND type='track'")
                  if r["path"] not in colors]
    if uncoloured:
        extract_deep(uncoloured, workers, progress)
    refresh_predictions(con, traktor_colors=colors, progress=progress)
    out = traktor.sync(nml, dry_run=dry_run, update_genres=False, make_smartlists=False, colors=True,
                       overwrite_colors=False, color_min_confidence=min_confidence)
    out["new_tracks_analyzed"] = (scan.get("this_run") or {}).get("analyzed", 0)
    out["uncoloured_tracks"] = len(uncoloured)
    if not dry_run and out.get("changed"):
        out["undo"] = "uv run dj-track-analyzer traktor --undo"
    return out
