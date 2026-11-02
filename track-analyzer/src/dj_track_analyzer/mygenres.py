"""Your own genres: `train` (also the Setup tab's "Learn your genres") and the genre switch.

Training learns your genres from example folders plus what the rest of your scanned collection sounds like
(analysis.train). Every scanned track then gets `trained_genre` (one of yours where it clearly fits, else none)
and `nearest_genre` (the closest of yours, always). The switch (setting `my_genres_mode`; only once
analysis.my_genres_not_ready() is None) picks what goes into `my_genre`, the column the page, mix suggestions,
sets, the LLM, tags and Traktor read: "starter" nothing (the built-in genres), "both" the trained genre (tracks
none of yours fits keep the built-in one), "mine" the nearest (every track one of yours). Hand-set genres always stay."""
from __future__ import annotations

from . import analysis, collection
from .analysis import log


def _refresh_views(predict: bool, progress=log) -> list[str]:
    """refresh_my_genres in every tempo view (their trained genre from the stretched sound). The normal cache
    and database are restored afterwards."""
    from . import tempo

    saved = analysis.CACHE_DB, analysis.CACHE_VERSION, collection.DB, collection.DEFAULT_CSV, dict(analysis.STRETCH)
    done = []
    try:
        for d in sorted(analysis.HOME.glob("at-*")):
            if (d / "collection.db").exists():
                progress(f"Your genres in the tempo view {d.name}...")
                con = tempo.view_db(float(d.name[3:]))
                try:
                    collection.refresh_my_genres(con, predict)
                finally:
                    con.close()
                done.append(d.name)
    finally:
        analysis.CACHE_DB, analysis.CACHE_VERSION, collection.DB, collection.DEFAULT_CSV = saved[:4]
        analysis.STRETCH.clear()
        analysis.STRETCH.update(saved[4])
    return done


def refresh(predict: bool = True, progress=log) -> dict:
    """Every scanned track's trained genre again (predict) and my_genre per the switch, normal library + views."""
    con = collection.db()
    try:
        out = collection.refresh_my_genres(con, predict)
    finally:
        con.close()
    views = _refresh_views(predict, progress)
    if views:
        out["tempo_views"] = views
    return out


def train(folder: str, progress=log) -> dict:
    report = analysis.train(folder, progress)
    progress("Giving your scanned tracks their genre...")
    report["collection"] = refresh(True, progress)
    return report


def set_mode(mode: str) -> dict:
    """The genre switch: "starter", "both" or "mine". Changes only the analyzer's own data: files and Traktor get
    your genres the next time genres are written (tag / traktor)."""
    if mode not in analysis.MY_GENRE_MODES:
        raise ValueError(f"mode must be one of {', '.join(analysis.MY_GENRE_MODES)}")
    if mode != "starter":
        why = analysis.my_genres_not_ready()
        if why:
            raise SystemExit(why)
    analysis.save_app_setting("my_genres_mode", None if mode == "starter" else mode)
    return refresh(False)


def status() -> dict:
    """For the Setup tab: what was learned, how well, whether it can be used and whether it is."""
    import sqlite3

    b = analysis.my_genre_bundle()
    mode = analysis.my_genres_mode()
    out = {"trained": bool(b), "mode": mode, "chosen": analysis.app_setting("my_genres_mode") or "starter",
           "in_use": mode != "starter", "not_ready": analysis.my_genres_not_ready(b),
           "min_total": analysis.MY_GENRES_MIN_TOTAL, "min_each": analysis.MY_GENRES_MIN_EACH,
           "tracks_with_yours": 0}
    if b:
        out.update(trained_at=b.get("trained_at"), source=b.get("source"), counts=b.get("counts") or {},
                   report={k: v for k, v in (b.get("report") or {}).items() if k != "saved_to"})
    if b and collection.NATIVE_DB.exists():
        with sqlite3.connect(collection.NATIVE_DB) as con:
            have = {r[1] for r in con.execute("PRAGMA table_info(tracks)")}
            if "trained_genre" in have:
                out["tracks_with_yours"] = con.execute(
                    "SELECT COUNT(*) FROM tracks WHERE trained_genre IS NOT NULL").fetchone()[0]
    return out


def preview(mode: str = "both") -> dict:
    if mode not in ("both", "mine"):
        raise ValueError("preview mode must be both or mine")
    con = collection.db()
    try:
        return collection.my_genres_preview(con, mode)
    finally:
        con.close()
