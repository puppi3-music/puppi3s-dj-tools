"""Your own genres: `train` (also the Setup tab's "Learn your genres") and the genre switch.

Training learns your genres from example folders plus what the rest of your scanned collection sounds like
(analysis.train). Every scanned track then gets `trained_genre` (one of yours where it clearly fits, else none)
and `nearest_genre` (the closest of yours, always). The switch (setting `my_genres_mode`; only once
analysis.my_genres_not_ready() is None) picks what goes into `my_genre`, the column the page, mix suggestions,
sets, the LLM, tags and Traktor read: "starter" nothing (the built-in genres), "both" the trained genre (tracks
none of yours fits keep the built-in one), "mine" the nearest (every track one of yours). Hand-set genres always stay."""
from __future__ import annotations

from . import analysis, collection
from pathlib import Path
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


def _tempo_view_loaded() -> bool:
    import json

    from . import traktor

    state = analysis.HOME / traktor.TEMPO_STATE
    try:
        return bool(state.exists() and json.loads(state.read_text(encoding="utf-8")).get("collection"))
    except (OSError, ValueError):
        return False


def traktor_choices(nml: str | None = None) -> dict:
    """What the Setup tab offers to learn from in Traktor: its playlists (each ticked one = a genre) and the genres
    in Traktor's Genre field, with track counts. `built_in`: one of this app's genre names (maybe written by it)."""
    from . import library

    built = {g.casefold() for g in analysis.dj_rules()}
    field = library.genre_field(nml)
    return {
        "playlists": [{"path": p["path"], "name": p["name"], "tracks": len(p["files"])} for p in library.playlists(nml)],
        "genres": [{"genre": g, "tracks": len(v), "built_in": g.casefold() in built} for g, v in field["genres"].items()],
        "multi": field["multi"], "tempo_view_loaded": library.app() == "traktor" and _tempo_view_loaded(), "min_each": analysis.MY_GENRES_MIN_EACH,
    }


def traktor_sets(playlists: list[str] | None = None, genres: list[str] | None = None,
                 nml: str | None = None) -> tuple[dict[str, list[str]], dict]:
    """The examples from Traktor: the ticked `playlists` (paths as traktor.playlists gives them; each is a genre
    named after the playlist, two with the same name are one genre), or the tracks whose Genre field is one of
    `genres`. Also the source, to learn again from it later. A track in two different genres is left out."""
    from . import library

    if playlists:
        have = {p["path"]: p for p in library.playlists(nml)}
        missing = [p for p in playlists if p not in have]
        if missing:
            raise SystemExit(f"{library.app_name()} has no playlist {', '.join(missing)} (with tracks) any more.")
        sets: dict[str, list[str]] = {}
        for p in playlists:
            sets.setdefault(have[p]["name"], []).extend(have[p]["files"])
        sets = {g: list(dict.fromkeys(fs)) for g, fs in sets.items()}
        seen: dict[str, int] = {}
        for files in sets.values():
            for f in files:
                seen[f] = seen.get(f, 0) + 1
        sets = {g: [f for f in files if seen[f] == 1] for g, files in sets.items()}
        return sets, {"kind": "traktor_playlists", "playlists": list(playlists)}
    if genres:
        if library.app() == "traktor" and _tempo_view_loaded():
            raise SystemExit("A tempo view is loaded in Traktor, so its Genre field holds the view's genres. "
                             "Switch Traktor back to normal on the Tempo tab first.")
        field = library.genre_field(nml)["genres"]
        missing = [g for g in genres if g not in field]
        if missing:
            raise SystemExit(f"No tracks with the genre {', '.join(missing)} in {library.app_name()}'s collection.")
        return {g: field[g] for g in genres}, {"kind": "traktor_genres", "genres": list(genres)}
    raise ValueError("Tick some playlists or genres")


def train(folder: str | None = None, progress=log, playlists: list[str] | None = None,
          genres: list[str] | None = None, by_tag: bool = False) -> dict:
    """Learn your genres from a folder on disk (by subfolder/playlist, or with `by_tag` by each file's genre tag),
    Traktor playlists or genres in Traktor's Genre field."""
    if folder and by_tag:
        sets = analysis.collect_tagged_set(folder)
        progress("Genres found in the tags: " + (", ".join(f"{g} ({len(v)})" for g, v in sets.items()) or "none"))
        report = analysis.train(None, progress, sets=sets,
                                source={"kind": "folder_tags", "folder": str(Path(folder).expanduser())})
    elif folder:
        report = analysis.train(folder, progress)
    else:
        sets, source = traktor_sets(playlists, genres)
        report = analysis.train(None, progress, sets=sets, source=source)
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
                   source_kind=b.get("source_kind") or ({"kind": "folder", "folder": b["source"]} if b.get("source") else None),
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
