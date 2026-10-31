"""
`keys` command: MusicalKeyCNN's key (keycnn.py) for scanned tracks that don't have one yet (scanned before it came
in), and how the CNN, Essentia and Traktor keys compare.
"""

from __future__ import annotations

import os
import time
from collections import Counter
from pathlib import Path

from . import analysis, collection
from .analysis import log

# MIREX key score weights (www.music-ir.org/mirex/wiki/2025:Audio_Key_Detection)
WEIGHTS = {"same": 1.0, "fifth": 0.5, "relative": 0.3, "parallel": 0.2, "other": 0.0}


def _one(path: str) -> dict:
    """Runs in a worker process."""
    try:
        _stereo, mono44, _mono16, _sr = analysis.load_audio(analysis.resolve_audio(path))
        return {"path": path, **analysis.key_cnn(mono44)}
    except Exception as err:
        return {"path": path, "error": f"{type(err).__name__}: {err}"}


def backfill(con, rescan: bool = False, workers: int | None = None) -> dict:
    """CNN key for scanned tracks without one (all of them with rescan)."""
    rows = con.execute("SELECT path FROM tracks WHERE type='track' AND (error IS NULL OR error LIKE 'tags:%')"
                       + ("" if rescan else " AND cnn_camelot IS NULL")
                       + " ORDER BY path").fetchall()
    todo = [r["path"] for r in rows if Path(r["path"]).exists()]
    missing = len(rows) - len(todo)
    log(f"{len(todo)} tracks need the CNN key" + (f" ({missing} more aren't on disk)" if missing else ""))
    if not todo:
        return {"analyzed": 0, "errors": 0, "not_on_disk": missing}
    if workers is None:  # no TensorFlow models in these workers, so more of them fit than in `scan`
        workers = max(1, (os.cpu_count() or 4) - 2)
    workers = max(1, min(workers, len(todo)))
    t0, done, errors, pool = time.time(), 0, 0, None
    try:
        if workers == 1:
            results = map(_one, todo)
        else:
            import multiprocessing as mp

            pool = mp.get_context("spawn").Pool(workers, initializer=collection._worker_init)
            results = pool.imap_unordered(_one, todo, chunksize=1)
        for r in results:
            done += 1
            if r.get("error"):
                errors += 1
                info = r["error"]
            else:
                con.execute("UPDATE tracks SET cnn_key=?, cnn_camelot=?, cnn_confidence=? WHERE path=?",
                            (r["cnn_key"], r["cnn_camelot"], r["cnn_confidence"], r["path"]))
                con.commit()
                info = f'{r["cnn_camelot"]:>3} {r["cnn_confidence"]:.2f}'
            rate = done / max(time.time() - t0, 1e-6)
            log(f"[{done}/{len(todo)}  ~{analysis.fmt_time((len(todo) - done) / rate)} left] "
                f"{Path(r['path']).name[:50]:50s} {info}")
    except KeyboardInterrupt:
        log(f"\nStopped. {done} tracks saved; run the same command to continue.")
        if pool:
            pool.terminate()
            pool = None
    finally:
        if pool:
            pool.close()
            pool.join()
    return {"analyzed": done - errors, "errors": errors, "not_on_disk": missing,
            "minutes": round((time.time() - t0) / 60, 1)}


def index_of(camelot: str | None) -> int | None:
    """Key index of a Camelot code (0-11 = 1A-12A, 12-23 = 1B-12B, as the CNN's classes)."""
    if not camelot or camelot[-1] not in "AB":
        return None
    return int(camelot[:-1]) - 1 + (0 if camelot[-1] == "A" else 12)


def _tonic(index: int) -> tuple[int, bool]:
    """(pitch class, minor) of a key index."""
    table = analysis.CAMELOT_MINOR if index < 12 else analysis.CAMELOT_MAJOR
    return next(p for p, n in table.items() if n == index % 12 + 1), index < 12


def relation(est: int, ref: int) -> str:
    """MIREX category of an estimated key against a reference key."""
    if est == ref:
        return "same"
    if (est < 12) == (ref < 12) and (est - ref) % 12 in (1, 11):
        return "fifth"
    if est % 12 == ref % 12:
        return "relative"
    if _tonic(est)[0] == _tonic(ref)[0]:
        return "parallel"
    return "other"


def compare(pairs: list[tuple[int, int]]) -> dict:
    """{"tracks", "same" %, "weighted" MIREX %, and each category's %} for (estimate, reference) pairs."""
    n = len(pairs)
    if not n:
        return {"tracks": 0}
    c = Counter(relation(e, r) for e, r in pairs)
    out = {"tracks": n, "weighted": round(100 * sum(WEIGHTS[k] * v for k, v in c.items()) / n, 1)}
    out |= {k: round(100 * c[k] / n, 1) for k in WEIGHTS}
    return out


def report(con, show: int = 20) -> dict:
    rows = [dict(r) for r in con.execute(
        "SELECT path, camelot, traktor_camelot, cnn_camelot, cnn_confidence FROM tracks "
        "WHERE cnn_camelot IS NOT NULL ORDER BY path")]
    idx = index_of
    tk = [r for r in rows if idx(r["traktor_camelot"]) is not None]
    out = {
        "tracks_with_cnn_key": len(rows),
        "against_traktor": {
            "essentia": compare([(idx(r["camelot"]), idx(r["traktor_camelot"])) for r in tk if idx(r["camelot"]) is not None]),
            "cnn": compare([(idx(r["cnn_camelot"]), idx(r["traktor_camelot"])) for r in tk]),
        },
        "cnn_against_essentia": compare([(idx(r["cnn_camelot"]), idx(r["camelot"])) for r in rows
                                         if idx(r["camelot"]) is not None]),
    }
    least = sorted(rows, key=lambda r: (r["cnn_confidence"], r["path"]))
    out["least_confident"] = [{"track": Path(r["path"]).name, "cnn": r["cnn_camelot"],
                               "cnn_confidence": r["cnn_confidence"], "essentia": r["camelot"],
                               "traktor": r["traktor_camelot"]} for r in least[:show]]
    return out


def run(rescan: bool = False, workers: int | None = None, show: int = 20) -> dict:
    con = collection.db()
    filled = backfill(con, rescan, workers)
    return {"this_run": filled, **report(con, show)}


def report_text(out: dict) -> str:
    def line(name: str, c: dict) -> str:
        if not c.get("tracks"):
            return f"  {name:24s} -"
        return (f"  {name:24s} {c['same']:5.1f}% same  {c['weighted']:5.1f} MIREX   fifth {c['fifth']:.1f}  "
                f"relative {c['relative']:.1f}  parallel {c['parallel']:.1f}  other {c['other']:.1f}  "
                f"({c['tracks']} tracks)")

    at = out["against_traktor"]
    lines = [f"Tracks with a CNN key: {out['tracks_with_cnn_key']}", "",
             "Against Traktor's key (not ground truth):",
             line("Essentia (edma)", at["essentia"]), line("CNN", at["cnn"]), "",
             "CNN against Essentia:", line("CNN vs Essentia", out["cnn_against_essentia"])]
    if out["least_confident"]:
        lines += ["", "Least confident CNN keys:",
                  f"  {'track':48s} {'CNN':>4} {'conf':>5} {'Essentia':>8} {'Traktor':>7}"]
        for d in out["least_confident"]:
            lines.append(f"  {d['track'][:48]:48s} {d['cnn']:>4} {d['cnn_confidence']:5.2f} "
                         f"{d['essentia'] or '-':>8} {d['traktor'] or '-':>7}")
    return "\n".join(lines)
