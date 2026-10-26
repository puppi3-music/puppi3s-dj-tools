"""
Tempo view: your catalogue as it sounds when every track is played at one tempo with key lock
(default 145 BPM). Each track is stretched with Rubber Band (pitch kept, like Traktor's key lock) and
analysed again: genre, moods, colour, hype. Your normal analysis is never touched.

  uv run dj-track-analyzer tempo scan                 analyse everything at 145 (cached, resumable, ~1 h the first time)
  uv run dj-track-analyzer tempo report               what changes at 145: genres, colours, hype
  uv run dj-track-analyzer tempo export               spreadsheet data/at-145/collection.csv
  uv run dj-track-analyzer tempo nml                  write data/at-145/collection.nml (Traktor untouched)
  uv run dj-track-analyzer tempo load                 switch Traktor to the 145 view (Traktor closed)
  uv run dj-track-analyzer tempo native               switch Traktor back to your normal genres/colours/hype
  uv run dj-track-analyzer tempo load --lexicon       the same in Lexicon too (Lexicon open, Local API on; see lexicon.py)
  uv run dj-track-analyzer tempo native --lexicon     switch both back
  uv run dj-track-analyzer tempo status

Everything for a view lives in data/at-<bpm>/ (cache.db, collection.db, collection.csv,
collection.nml). Models and your colour model are shared with the normal analysis.

`load` / `native` only change three fields per track plus one Smartlist folder:
  Genre      the genres heard at 145
  Colour     YOUR colour, moved by how much the colour model thinks the track shifts at 145
             (tracks you haven't coloured stay uncoloured)
  Comment 2  its "Hype NN" part becomes the hype at 145 (your own text in it is kept)
  Smartlists "Genres (Track Analyzer)" is swapped for "Genres @145 (Track Analyzer)"
The values they replace are remembered in data/tempo-loaded.json. Everything else Traktor
saves while the view is loaded (cue points, plays, History, new tracks) carries over both ways. If you
change one of those three fields yourself while the view is loaded, `native` keeps your change and lists it.
"""

from __future__ import annotations

import json
import os
import re
import signal
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from . import collection
from . import analysis
from . import traktor
from .analysis import COLOR_NAMES, HOME, log

DEFAULT_BPM = 145.0
BASE_CACHE_DB = analysis.CACHE_DB
BASE_CACHE_VERSION = analysis.CACHE_VERSION
BASE_DB = collection.DB
STATE = HOME / traktor.TEMPO_STATE
BACKUP_PREFIX = "collection.nml.before-tempo-view-"
FIELDS = ("GENRE", "COLOR", "RATING")  # Genre, Colour, Comment 2
VIEW_EXTRA = [("source_bpm", "REAL"), ("native_color_position", "REAL")]

# Sped up to ~145, the Discogs-trained models hear psytrance in almost any rolling four-on-the-floor
# (Psy-Trance 0.6-0.9 on house tracks that score 0.0-0.1 at their own tempo): tempo is a shortcut for
# them. So in a view, psy only counts as far as the track already had it at its own tempo (x PSY_GATE).
# Tested on 29 tracks 2026-10-21: Psytrance top genre 17 → 1 (with Progressive Trance as its own genre).
PSY_STYLES = ["Electronic---Psy-Trance", "Electronic---Goa Trance"]
PSY_GATE = 1.5


def view_genres(path: str, bpm: float) -> list[dict] | None:
    """Top 3 genres at the view's tempo, psy gated by the track's own-tempo psy (see PSY_GATE)."""
    p = Path(path)
    hit, m = analysis.cache_get(p), analysis.maest_cached(p)
    if not hit:
        return None
    scores = analysis.label_scores(hit[2], m[0] if m else None)
    with base_cache():
        h0, m0 = analysis.cache_get(p), analysis.maest_cached(p)
    if h0:
        s0 = analysis.label_scores(h0[2], m0[0] if m0 else None)
        cap = PSY_GATE * max(s0.get(k, 0.0) for k in PSY_STYLES)
        for k in PSY_STYLES:
            if k in scores:
                scores[k] = min(scores[k], cap)
    return analysis.dj_genre(scores, bpm)


def _genre_cols(g: list[dict]) -> dict:
    return {"genre": g[0]["genre"] if g else None, "genre_score": g[0]["score"] if g else None,
            "genre_2": g[1]["genre"] if len(g) > 1 else None, "genre_3": g[2]["genre"] if len(g) > 2 else None,
            "genre_2_score": g[1]["score"] if len(g) > 1 else None,
            "genre_3_score": g[2]["score"] if len(g) > 2 else None}


def view_home(bpm: float) -> Path:
    return HOME / f"at-{bpm:g}"


def folder_name(bpm: float) -> str:
    return f"Genres @{bpm:g} (Track Analyzer)"


def use_view(bpm: float) -> None:
    """Point the cache and collection database of this process (and of scan workers) at the view."""
    vh = view_home(bpm)
    vh.mkdir(parents=True, exist_ok=True)
    analysis.CACHE_DB = vh / "cache.db"
    analysis.CACHE_VERSION = f"{BASE_CACHE_VERSION}|tempo-{bpm:g}"
    collection.DB = vh / "collection.db"
    collection.DEFAULT_CSV = vh / "collection.csv"


@contextmanager
def base_cache():
    """Temporarily read the normal (native tempo) analysis cache."""
    saved = analysis.CACHE_DB, analysis.CACHE_VERSION
    analysis.CACHE_DB, analysis.CACHE_VERSION = BASE_CACHE_DB, BASE_CACHE_VERSION
    try:
        yield
    finally:
        analysis.CACHE_DB, analysis.CACHE_VERSION = saved


def view_db(bpm: float) -> sqlite3.Connection:
    use_view(bpm)
    con = collection.db()
    have = {r[1] for r in con.execute("PRAGMA table_info(tracks)")}
    for c, t in VIEW_EXTRA:
        if c not in have:
            con.execute(f"ALTER TABLE tracks ADD COLUMN {c} {t}")
    # so a cached view analysis survives tag edits / file syncs (analysis._recover_key); this view's ratios only
    analysis.STRETCH.clear()
    analysis.STRETCH.update({p: bpm / b for p, b in con.execute(
        "SELECT path, source_bpm FROM tracks WHERE source_bpm > 0")})
    return con


def native_db() -> sqlite3.Connection:
    if not BASE_DB.exists():
        raise SystemExit("Nothing scanned yet. Run `uv run dj-track-analyzer scan --traktor` first.")
    con = sqlite3.connect(BASE_DB)
    con.row_factory = sqlite3.Row
    return con


def native_rows() -> dict[str, sqlite3.Row]:
    with native_db() as con:
        return {r["path"]: r for r in con.execute(
            "SELECT * FROM tracks WHERE type='track' AND (error IS NULL OR error LIKE 'tags:%')")}


# ----------------------------------------------------------------------------
# Scan
# ----------------------------------------------------------------------------


def _scan_one(job):
    """Runs in a worker process. job: (path, track's BPM, BPM to play it at, our own-tempo BPM measurement).
    The stretch goes from the track's BPM (Traktor's where it has one); the colour model gets our own
    measurement, as in the normal analysis it was trained on (see refresh)."""
    path, src, bpm, own = job
    use_view(bpm)
    p = Path(path)
    try:
        row = collection.to_row(p, analysis.analyze_file(path, timeline=False, genre_bpm=bpm, tempo=(src, bpm),
                                                         own_bpm=own), None)
    except SystemExit as err:  # e.g. Rubber Band missing: the same for every track
        return {"path": path, "fatal": str(err)}
    except Exception as err:
        try:
            row = collection.to_row(p, None, f"{type(err).__name__}: {err}")
        except Exception:
            row = {"path": path, "error": str(err)}
    row["source_bpm"] = src
    return row


def _forget_cached(paths: list[str]) -> None:
    """Drop view cache rows for these files (their tempo changed, so the stretched audio did)."""
    with analysis._db() as con:
        for table in ("emb", "deep"):
            con.executemany(f"DELETE FROM {table} WHERE path=?", [(p,) for p in paths])


def scan(bpm: float = DEFAULT_BPM, workers: int | None = None, rescan: bool = False,
         nml: str | None = None) -> dict:
    if not analysis.models_present():
        raise SystemExit("Style models missing. Run `uv run dj-track-analyzer setup` first.")
    analysis.warn_if_maest_missing()
    analysis.rubberband()  # installed and new enough, before any work
    native = native_rows()
    tk = collection.load_traktor_info(nml)  # Traktor's BPM (maybe corrected by hand) wins, as in `scan`
    src = {}
    for p, r in native.items():
        b = (tk.get(p) or (None, None))[0] or r["traktor_bpm"] or r["bpm"]
        if b:
            src[p] = analysis.round_bpm(b)
    con = view_db(bpm)
    known = {r["path"]: r for r in con.execute(
        "SELECT path, size, mtime, source_bpm FROM tracks WHERE error IS NULL OR error LIKE 'tags:%'")}
    gone = [p for (p,) in con.execute("SELECT path FROM tracks") if p not in src]
    if gone:
        con.executemany("DELETE FROM tracks WHERE path=?", [(p,) for p in gone])
        con.commit()
        log(f"Removed {len(gone)} tracks that are no longer in your normal scan")
    todo, retempo, recovered = [], [], 0
    for p, b in sorted(src.items()):
        try:
            st = os.stat(p)
        except OSError:
            continue
        k = known.get(p)
        stat_changed = k is not None and (k["size"], k["mtime"]) != (st.st_size, int(st.st_mtime))
        if stat_changed and not rescan and k["source_bpm"] and abs(k["source_bpm"] - b) <= 0.05 \
                and analysis.cache_get(Path(p)) is not None:  # same audio, only tags/sync touched the file
            con.execute("UPDATE tracks SET size=?, mtime=? WHERE path=?", (st.st_size, int(st.st_mtime), p))
            recovered += 1
        elif rescan or k is None or stat_changed:
            todo.append(p)
        elif k["source_bpm"] is None or abs(k["source_bpm"] - b) > 0.05:
            todo.append(p)
            retempo.append(p)
    con.commit()
    if recovered:
        log(f"{recovered} files changed on disk (tags or sync) but have the same audio; kept their analysis")
    pruned = analysis.prune_cache(collection._missing_for_good, collection.sync_conflict)  # the view's cache; before the workers start
    if pruned:
        log(f"Dropped {pruned} view cache entries nothing can use any more (old versions, gone files, replaced keys)")
    if rescan:
        retempo = todo
    if retempo:
        _forget_cached(retempo)
    pct = [100 * (bpm / b - 1) for b in src.values()]
    log(f"{len(src)} tracks, played at {bpm:g} BPM: tempo change {min(pct):+.0f}% to {max(pct):+.0f}% "
        f"(median {np.median(pct):+.0f}%). {len(src) - len(todo)} already analysed, {len(todo)} to go"
        + (f" ({len(retempo)} because their BPM changed)" if retempo and not rescan else ""))
    t0, done, errors = time.time(), 0, 0
    if todo:
        if workers is None:
            workers = max(1, min(5, (os.cpu_count() or 4) // 2))
        workers = max(1, min(workers, len(todo)))
        log(f"Using {workers} worker{'s' if workers > 1 else ''}. Ctrl-C stops safely; run the same command to resume.\n")
        # Longest first: a long track started last would leave the other workers idle at the end
        todo.sort(key=lambda p: -(native[p]["duration_sec"] or 0))
        jobs = [(p, src[p], bpm, native[p]["bpm"]) for p in todo]
        pool = None
        try:
            if workers == 1:
                analysis.es()
                results = map(_scan_one, jobs)
            else:
                import multiprocessing as mp

                pool = mp.get_context("spawn").Pool(workers, initializer=collection._worker_init)
                results = pool.imap_unordered(_scan_one, jobs, chunksize=1)
            for row in results:
                if row.get("fatal"):
                    raise SystemExit(row["fatal"])
                done += 1
                if not row.get("error"):
                    g = view_genres(row["path"], bpm)
                    if g is not None:
                        row.update(_genre_cols(g))
                n = native.get(row["path"])
                row["traktor_bpm"] = bpm  # what you mix at; the key stays (key lock)
                row["traktor_camelot"] = ((tk.get(row["path"]) or (None, None))[1]
                                          or (n["traktor_camelot"] if n else None))
                row["traktor_color"] = n["traktor_color"] if n else None
                collection.save_row(con, row)
                con.execute("UPDATE tracks SET source_bpm=? WHERE path=?", (row["source_bpm"], row["path"]))
                con.commit()
                if row.get("error"):
                    errors += 1
                rate = done / max(time.time() - t0, 1e-6)
                eta = (len(todo) - done) / rate if rate else 0
                was = collection.multi_genre(dict(n)) if n and n["genre"] else "-"
                now = collection.multi_genre(row) if row.get("genre") else (row.get("error") or "-")
                log(f"[{done}/{len(todo)}  ~{analysis.fmt_time(eta)} left] {Path(row['path']).name[:40]:40s} "
                    f"{row['source_bpm']:>6} → {row.get('bpm') or '-':>5}  {was[:24]:24s} → {now[:40]}")
        except KeyboardInterrupt:
            log(f"\nStopped. {done} tracks saved; run the same command to continue.")
            if pool:
                pool.terminate()
                pool = None
        finally:
            if pool:
                pool.close()
                pool.join()
    refresh(bpm, con)
    out = {"bpm": bpm, "tracks": con.execute("SELECT COUNT(*) FROM tracks").fetchone()[0],
           "data": str(view_home(bpm))}
    if todo:
        out["this_run"] = {"analysed": done, "errors": errors, "minutes": round((time.time() - t0) / 60, 1)}
    return out


def _native_position(path: str, r: sqlite3.Row) -> float | None:
    """The colour model's position for the track at its own tempo, with the current model (so it
    compares fairly with the view's position)."""
    p = Path(path)
    with base_cache():
        hit = analysis.cache_get(p)
        if not hit:
            return None
        deep = analysis.deep_get(p)
    info = {c: r[c] for c in ("bpm", "lufs", "loudness_range", "key", "key_strength")}
    info.update({f"mood_{m}": r[f"mood_{m}"] for m in analysis.MOODS})
    maest = instruments = extra = None
    if deep:
        maest, instruments, extra = deep[0], deep[2], deep[3]
        info.update({f"mood_{m}": v for m, v in deep[1].items()})
    col = analysis.predict_color(hit[1], hit[2], info, maest, instruments, extra)
    return col["position"] if col else None


def refresh(bpm: float, con: sqlite3.Connection | None = None) -> None:
    """Colour predictions (view and native, same model), hype and the spreadsheet for a view."""
    from . import colors
    from . import hype

    con = con or view_db(bpm)
    gone = [p for (p,) in con.execute("SELECT path FROM tracks") if collection._missing_for_good(p)]
    if gone:
        con.executemany("DELETE FROM tracks WHERE path=?", [(p,) for p in gone])
        log(f"Removed {len(gone)} tracks whose files no longer exist")
    for (p,) in con.execute("SELECT path FROM tracks WHERE type='track' "
                            "AND (error IS NULL OR error LIKE 'tags:%')").fetchall():
        g = view_genres(p, bpm)  # cheap (cached scores); picks up changes to the rules
        if g is not None:
            cols = _genre_cols(g)
            con.execute(f"UPDATE tracks SET {', '.join(f'{c}=?' for c in cols)} WHERE path=?", [*cols.values(), p])
    con.commit()
    if analysis.color_model() is not None:
        native = native_rows()
        # the BPM we measured at each track's own tempo, as _native_position uses: the colour shift is the tempo's alone
        colors.refresh_predictions(con, csv_out=None, own_bpm={p: r["bpm"] for p, r in native.items()})
        con.executemany("UPDATE tracks SET native_color_position=? WHERE path=?",
                        [(_native_position(p, native[p]) if p in native else None, p)
                         for (p,) in con.execute("SELECT path FROM tracks").fetchall()])
        con.commit()
    try:
        hype.compute(con)
    except SystemExit as err:
        log(f"(hype not scored: {err})")
    export(bpm, con=con)


def refresh_all_views(progress=log) -> list[str]:
    """Every tempo view again with the current colour model (after `colors train`, so a view never keeps
    the previous model's colours). Only the views' own databases change, not Traktor. This process's normal
    cache and database are restored afterwards."""
    saved = analysis.CACHE_DB, analysis.CACHE_VERSION, collection.DB, collection.DEFAULT_CSV, dict(analysis.STRETCH)
    done = []
    try:
        for d in sorted(HOME.glob("at-*")):
            if (d / "collection.db").exists():
                progress(f"Refreshing the tempo view {d.name} with the new model...")
                refresh(float(d.name[3:]))
                done.append(d.name)
    finally:
        analysis.CACHE_DB, analysis.CACHE_VERSION, collection.DB, collection.DEFAULT_CSV = saved[:4]
        analysis.STRETCH.clear()
        analysis.STRETCH.update(saved[4])
    return done


# ----------------------------------------------------------------------------
# Comparing the view with your normal analysis
# ----------------------------------------------------------------------------


def _colour_index(name: str | None) -> int | None:
    return COLOR_NAMES.index(name) + 1 if name in COLOR_NAMES else None


def shifted_colour(yours: int | None, view_pos: float | None, native_pos: float | None) -> int | None:
    """Your colour moved by the model's shift between native tempo and the view."""
    if yours is None or view_pos is None or native_pos is None:
        return None
    return int(np.clip(round(yours + (view_pos - native_pos)), 1, len(COLOR_NAMES)))


def view_colours(con: sqlite3.Connection) -> dict[str, str]:
    """Each coloured track's colour in a view, as `load` puts it in Traktor: your colour (read from the normal
    library now, not the copy the view scan took) moved by the model's shift; a held colour stays yours."""
    with native_db() as n:
        yours = {r[0]: r[1] for r in n.execute("SELECT path, traktor_color FROM tracks WHERE traktor_color IS NOT NULL")}
    held = collection.held("colour")
    out = {}
    for r in con.execute("SELECT path, color_position, native_color_position FROM tracks"):
        y = yours.get(r["path"])
        if y is None:
            continue
        at = None if r["path"] in held else shifted_colour(_colour_index(y), r["color_position"], r["native_color_position"])
        out[r["path"]] = COLOR_NAMES[at - 1] if at else y
    return out


def compare_rows(bpm: float, con: sqlite3.Connection | None = None) -> list[dict]:
    con = con or view_db(bpm)
    native = native_rows()
    out = []
    for r in con.execute("SELECT * FROM tracks WHERE type='track' AND (error IS NULL OR error LIKE 'tags:%')"):
        n = native.get(r["path"])
        if n is None:
            continue
        yours = _colour_index(n["traktor_color"])
        shift = (r["color_position"] - r["native_color_position"]
                 if r["color_position"] is not None and r["native_color_position"] is not None else None)
        at = shifted_colour(yours, r["color_position"], r["native_color_position"])
        out.append({
            "path": r["path"], "artist": r["artist"], "title": r["title"], "filename": r["filename"],
            "source_bpm": analysis.round_bpm(r["source_bpm"]), "tempo_change_pct": round(100 * (bpm / r["source_bpm"] - 1), 1)
            if r["source_bpm"] else None,
            "native_genres": collection.multi_genre(dict(n)) if n["genre"] else None,
            "genres": collection.multi_genre(dict(r)) if r["genre"] else None,
            "native_genre": n["genre"], "genre": r["genre"],
            "your_colour": n["traktor_color"], "colour_shift": round(shift, 2) if shift is not None else None,
            "colour_at_bpm": COLOR_NAMES[at - 1] if at else None,
            "native_hype": n["hype"], "hype": r["hype"],
        })
    return out


def export(bpm: float = DEFAULT_BPM, out: Path | None = None, con: sqlite3.Connection | None = None) -> Path:
    """The view's spreadsheet: every normal column (measured at the new tempo), plus the comparison."""
    import csv

    con = con or view_db(bpm)
    path = Path(os.path.expanduser(str(out))) if out else view_home(bpm) / "collection.csv"
    cmp = {c["path"]: c for c in compare_rows(bpm, con)}
    extra = ["source_bpm", "tempo_change_pct", "native_genres", "your_colour", "colour_shift",
             "colour_at_bpm", "native_hype"]
    cols = [c for c in collection.NAMES if c not in ("size", "mtime")]
    gi = cols.index("genre")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols[:gi] + ["genres_multi"] + cols[gi:] + extra)
        for r in con.execute("SELECT * FROM tracks ORDER BY folder, filename").fetchall():
            vals = [collection.csv_value(c, r[c]) for c in cols]
            c = cmp.get(r["path"], {})
            w.writerow(vals[:gi] + [collection.multi_genre(dict(r)) if r["genre"] else None] + vals[gi:]
                       + [collection.csv_value(k, r["source_bpm"]) if k == "source_bpm" else c.get(k) for k in extra])
    log(f"Spreadsheet: {path}")
    return path


def report(bpm: float = DEFAULT_BPM, limit: int = 15) -> dict:
    rows = compare_rows(bpm)
    if not rows:
        raise SystemExit(f"No {bpm:g} BPM analysis yet. Run: uv run dj-track-analyzer tempo scan --bpm {bpm:g}")
    from collections import Counter

    def count(key):
        return Counter(r[key] or "(none)" for r in rows)

    flips = Counter((r["native_genre"] or "(none)", r["genre"] or "(none)") for r in rows
                    if r["native_genre"] != r["genre"])
    shifts = [r for r in rows if r["colour_shift"] is not None]
    coloured = [r for r in rows if r["your_colour"] and r["colour_at_bpm"]]
    pct = [r["tempo_change_pct"] for r in rows if r["tempo_change_pct"] is not None]
    by_colour = {}
    for name in COLOR_NAMES:
        s = [r["colour_shift"] for r in shifts if r["your_colour"] == name]
        if s:
            by_colour[name] = {"tracks": len(s), "mean_shift": round(float(np.mean(s)), 2),
                               "now": dict(Counter(r["colour_at_bpm"] for r in coloured
                                                   if r["your_colour"] == name).most_common())}

    def brief(r):
        return {"track": f"{r['artist'] or ''} - {r['title'] or r['filename']}".strip(" -"),
                "bpm": r["source_bpm"], "genres": f"{r['native_genres']} → {r['genres']}",
                "colour": f"{r['your_colour'] or '-'} → {r['colour_at_bpm'] or '-'} ({r['colour_shift']:+.2f})"
                if r["colour_shift"] is not None else None,
                "hype": f"{r['native_hype'] or '-'} → {r['hype'] or '-'}"}

    return {
        "bpm": bpm, "tracks": len(rows),
        "tempo_change": {"min_pct": min(pct), "median_pct": float(np.median(pct)), "max_pct": max(pct)},
        "genres_native": dict(count("native_genre").most_common()),
        "genres_at_bpm": dict(count("genre").most_common()),
        "genre_changed": sum(flips.values()),
        "genre_flips": [{"from": a, "to": b, "tracks": n} for (a, b), n in flips.most_common(limit)],
        "colour_mean_shift": round(float(np.mean([r["colour_shift"] for r in shifts])), 2) if shifts else None,
        "colour_changed": sum(r["your_colour"] != r["colour_at_bpm"] for r in coloured),
        "colours_coloured_tracks": len(coloured),
        "colours_native": dict(Counter(r["your_colour"] for r in coloured)),
        "colours_at_bpm": dict(Counter(r["colour_at_bpm"] for r in coloured)),
        "by_your_colour": by_colour,
        "hardest_turns": [brief(r) for r in sorted(shifts, key=lambda r: r["colour_shift"])[:limit]],
        "prettiest_turns": [brief(r) for r in sorted(shifts, key=lambda r: -r["colour_shift"])[:limit]],
        "most_hype": [brief(r) for r in sorted(rows, key=lambda r: -(r["hype"] or 0))[:limit]],
        "hype_risers": [brief(r) for r in sorted(rows, key=lambda r: -((r["hype"] or 0) - (r["native_hype"] or 0)))[:limit]],
    }


def report_text(d: dict) -> str:
    def counts(c: dict, order=None) -> str:
        keys = order or list(c)
        return ", ".join(f"{k} {c[k]}" for k in keys if c.get(k))

    tc = d["tempo_change"]
    lines = [f"Your catalogue at {d['bpm']:g} BPM (key lock): {d['tracks']} tracks, tempo change "
             f"{tc['min_pct']:+.0f}% to {tc['max_pct']:+.0f}% (median {tc['median_pct']:+.0f}%)", "",
             "Top genre", f"  normally: {counts(d['genres_native'])}", f"  at {d['bpm']:g}:   {counts(d['genres_at_bpm'])}",
             f"  {d['genre_changed']} of {d['tracks']} tracks change top genre. Most common changes:"]
    lines += [f"    {f['tracks']:4d}  {f['from']} → {f['to']}" for f in d["genre_flips"]]
    if d["colour_mean_shift"] is not None:
        lines += ["", f"Colour (your colour, moved by the model's shift; - = harder, + = prettier)",
                  f"  average shift {d['colour_mean_shift']:+.2f} colours; {d['colour_changed']} of "
                  f"{d['colours_coloured_tracks']} coloured tracks land on another colour",
                  f"  normally: {counts(d['colours_native'], COLOR_NAMES)}",
                  f"  at {d['bpm']:g}:   {counts(d['colours_at_bpm'], COLOR_NAMES)}"]
        for name, c in d["by_your_colour"].items():
            lines.append(f"    {name:8s} ({c['tracks']:3d})  shift {c['mean_shift']:+.2f}  → "
                         + ", ".join(f"{k} {v}" for k, v in c["now"].items()))
    for key, title in (("hardest_turns", "Turn hardest"), ("prettiest_turns", "Stay prettiest / turn prettier"),
                       ("most_hype", f"Most hype at {d['bpm']:g}"), ("hype_risers", "Biggest hype risers")):
        lines += ["", title]
        for r in d[key]:
            lines.append(f"  {r['track'][:46]:46s} {r['bpm'] or '':>6}  {r['colour'] or '':28s} hype {r['hype']:7s} "
                         f"{r['genres']}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# Traktor: building, loading and unloading the view
# ----------------------------------------------------------------------------


def _loc_key(entry: str) -> tuple[str | None, list[str]]:
    loc_m = re.search(r"<LOCATION\b[^>]*>", entry)
    if not loc_m:
        return None, []
    a = traktor.attrs(loc_m.group(0))
    return f"{a.get('VOLUME', '')}|{a.get('DIR', '')}|{a.get('FILE', '')}", traktor.location_paths(a)


def _info(entry: str) -> dict:
    m = re.search(r"<INFO\b[^>]*>", entry)
    return traktor.attrs(m.group(0)) if m else {}


def _set_or_drop(entry: str, name: str, value: str | None) -> str:
    if value is not None:
        return traktor._set_info_attr(entry, name, value)[0]
    m = re.search(r"<INFO\b[^>]*>", entry)
    if not m:
        return entry
    return entry[:m.start()] + re.sub(rf'\s{name}="[^"]*"', "", m.group(0)) + entry[m.end():]


def _collection_span(text: str, path: Path) -> tuple[int, int]:
    c0 = text.find("<COLLECTION")
    c1 = text.find("</COLLECTION>", c0)
    if c0 == -1 or c1 == -1:
        raise SystemExit(f"{path} doesn't look like a Traktor collection (no COLLECTION section).")
    return c0, c1


def build_view(text: str, bpm: float, path: Path) -> tuple[str, dict, dict]:
    """The collection text with the view's genre, colour and hype. Returns (text, remembered
    {track: {field: [your value, view value]}} , stats)."""
    con = view_db(bpm)
    view = {r["path"]: r for r in con.execute(
        "SELECT * FROM tracks WHERE type='track' AND (error IS NULL OR error LIKE 'tags:%')")}
    if not view:
        raise SystemExit(f"No {bpm:g} BPM analysis yet. Run: uv run dj-track-analyzer tempo scan --bpm {bpm:g}")
    remembered, genres_seen = {}, set()
    stats = {"collection": str(path), "bpm": bpm, "tracks_in_view": 0, "genres_set": 0, "colours_moved": 0,
             "colours_same": 0, "hype_set": 0, "genre_held": 0, "colour_held": 0}
    examples = []
    genre_held, colour_held = collection.held("genre"), collection.held("colour")

    def fix_entry(m: re.Match) -> str:
        entry = m.group(0)
        key, paths = _loc_key(entry)
        r = next((view[p] for p in paths if p in view), None)
        if key is None or r is None:
            return entry
        stats["tracks_in_view"] += 1
        cur = _info(entry)
        want = {}
        if r["path"] in genre_held:  # your genre stays, and keeps its Smartlist in the view's folder
            stats["genre_held"] += 1
            genres_seen.update(g.strip() for g in (cur.get("GENRE") or "").split(";") if g.strip())
        elif r["genre"]:
            want["GENRE"] = collection.multi_genre(dict(r))
            genres_seen.update(g.strip() for g in want["GENRE"].split(";"))
        yours = int(cur["COLOR"]) if (cur.get("COLOR") or "").isdigit() and 1 <= int(cur["COLOR"]) <= 7 else None
        at = shifted_colour(yours, r["color_position"], r["native_color_position"])
        if r["path"] in colour_held:
            stats["colour_held"] += 1
        elif at is not None:
            want["COLOR"] = str(at)
            stats["colours_moved" if at != yours else "colours_same"] += 1
        if r["hype"] is not None:
            want["RATING"] = traktor.with_hype(cur.get("RATING"), r["hype"])
        changed = {}
        for field, value in want.items():
            if value != cur.get(field):
                entry = _set_or_drop(entry, field, value)
                changed[field] = [cur.get(field), value]
        if changed:
            # Mark Traktor as up to date with the file, or it reads the file's tags again on deck load
            # and they replace the view's genre/colour (seen 2026-10-22).
            stamp = next((s for s in map(traktor.file_stamp, paths) if s), None)
            if stamp and stamp != traktor.entry_stamp(entry):
                changed["MODIFIED"] = [traktor.entry_stamp(entry), stamp]
                entry = traktor.set_entry_stamp(entry, stamp)
            remembered[key] = changed
            stats["genres_set"] += "GENRE" in changed
            stats["hype_set"] += "RATING" in changed
            if len(examples) < 12 and ("GENRE" in changed or "COLOR" in changed):
                bits = [f"{(changed[f][0] if f != 'COLOR' else COLOR_NAMES[int(changed[f][0]) - 1]) or '(empty)'} → "
                        f"{changed[f][1] if f != 'COLOR' else COLOR_NAMES[int(changed[f][1]) - 1]}"
                        for f in ("GENRE", "COLOR") if f in changed]
                examples.append(f"{Path(paths[0]).name}: " + "; ".join(bits))
        return entry

    c0, c1 = _collection_span(text, path)
    body = re.sub(r"<ENTRY\b.*?</ENTRY>", fix_entry, text[c0:c1], flags=re.S)
    new_text = text[:c0] + body + text[c1:]
    known = {g.lower(): g for g in traktor.known_genres()}
    lists = sorted({known[g.lower()] for g in genres_seen if g.lower() in known}, key=str.lower)
    # The view's folder takes the place of your normal genre folder, so the playlist tree keeps its order.
    new_text, native_folder = traktor.swap_root_folder(new_text, traktor.FOLDER_NAME,
                                                       traktor.smartlist_folder(lists, folder_name(bpm)))
    if native_folder is None:
        new_text, _ = traktor.install_smartlists(new_text, lists, folder_name(bpm))
    stats["smartlists"] = f"{folder_name(bpm)} ({len(lists)} genres)" + (
        f", replacing {traktor.FOLDER_NAME} while loaded" if native_folder else "")
    stats["examples"] = examples
    return new_text, {"tracks": remembered, "native_folder": native_folder}, stats


def loaded() -> dict | None:
    return json.loads(STATE.read_text()) if STATE.exists() else None


def _check_closed(dry_run: bool) -> None:
    if not dry_run and traktor.traktor_running():
        raise SystemExit("Traktor is running. Quit Traktor first: it rewrites collection.nml when it closes "
                         "and would undo these changes.")


def write_nml(bpm: float = DEFAULT_BPM, nml: str | None = None, out: str | None = None) -> dict:
    """A standalone copy of your collection with the view's values. Traktor itself is not touched."""
    path = traktor.find_collection(nml)
    st = loaded()
    if st and st["collection"] == str(path):
        raise SystemExit(f"The {st['bpm']:g} BPM view is loaded in Traktor right now; its collection.nml already "
                         "is the view.")
    new_text, _remembered, stats = build_view(path.read_text(encoding="utf-8"), bpm, path)
    dest = Path(os.path.expanduser(out)) if out else view_home(bpm) / "collection.nml"
    dest.write_text(new_text, encoding="utf-8")
    stats["written"] = str(dest)
    return stats


def load(bpm: float = DEFAULT_BPM, nml: str | None = None, dry_run: bool = False, force: bool = False) -> dict:
    path = traktor.find_collection(nml)
    _check_closed(dry_run)
    tag_warning = None
    if traktor.tag_writing_on(path):
        tag_warning = ("Traktor is set to write tags to your files (Preferences → File Management). While the view "
                       "is loaded it could write the view's genres into the files, and `tempo native` only "
                       "restores collection.nml. Turn that off first, or pass --force.")
        if not dry_run and not force:
            raise SystemExit(tag_warning)
    st = loaded()
    if st:
        raise SystemExit(f"The {st['bpm']:g} BPM view is already loaded ({st['collection']}). "
                         "Switch back first: uv run dj-track-analyzer tempo native")
    log(f"Traktor: putting the {bpm:g} BPM view's genres, colours and hype into {path.name}…")
    text = path.read_text(encoding="utf-8")
    new_text, remembered, stats = build_view(text, bpm, path)
    if tag_warning:
        stats["warning"] = tag_warning
    stats = traktor._save(path, text, new_text, stats, dry_run, prefix=BACKUP_PREFIX)
    clock = f"{bpm:g}"
    yours = traktor.setting(path, traktor.MASTER_TEMPO)
    if yours is not None:
        stats["clock"] = f"{yours} → {clock} BPM"
    if not dry_run and stats.get("changed"):
        if yours is not None:
            traktor.set_setting(path, traktor.MASTER_TEMPO, clock, backup_tag="before-tempo-view-")
            remembered["clock"] = [yours, clock]
        STATE.write_text(json.dumps({"bpm": bpm, "collection": str(path), "loaded_at": time.strftime("%Y-%m-%d %H:%M"),
                                     **remembered}, ensure_ascii=False))
        (view_home(bpm) / "collection.nml").write_text(new_text, encoding="utf-8")
        stats["now_loaded"] = f"{bpm:g} BPM view. Back to normal: uv run dj-track-analyzer tempo native"
    return stats


def unload(nml: str | None = None, dry_run: bool = False) -> dict:
    st = loaded()
    if not st:
        raise SystemExit("No tempo view is loaded; Traktor already has your normal values.")
    path = traktor.find_collection(nml or st["collection"])
    _check_closed(dry_run)
    bpm, tracks = st["bpm"], st["tracks"]
    text = path.read_text(encoding="utf-8")
    stats = {"collection": str(path), "bpm": bpm, "restored": 0, "kept_your_changes": 0}
    kept = []

    def fix_entry(m: re.Match) -> str:
        entry = m.group(0)
        key, paths = _loc_key(entry)
        if key not in tracks:
            return entry
        cur = _info(entry)
        for field, (yours, view) in tracks[key].items():
            if field == "MODIFIED":  # Traktor's stamp, not a value of yours: put back unless Traktor moved it
                if traktor.entry_stamp(entry) == view:
                    entry = traktor.set_entry_stamp(entry, yours)
                continue
            if cur.get(field) == view:
                entry = _set_or_drop(entry, field, yours)
                stats["restored"] += 1
            elif cur.get(field) != yours:  # changed in Traktor while the view was loaded: keep it
                stats["kept_your_changes"] += 1
                if len(kept) < 30:
                    def show(v):
                        return COLOR_NAMES[int(v) - 1] if field == "COLOR" and v and v.isdigit() else v or "(empty)"
                    kept.append(f"{Path(paths[0]).name}: {field.lower()} {show(cur.get(field))} "
                                f"(view had {show(view)}, normally {show(yours)})")
        return entry

    c0, c1 = _collection_span(text, path)
    body = re.sub(r"<ENTRY\b.*?</ENTRY>", fix_entry, text[c0:c1], flags=re.S)
    new_text = text[:c0] + body + text[c1:]
    if st.get("native_folder") and f'NAME="{traktor.esc(traktor.FOLDER_NAME)}"' not in new_text:
        new_text, swapped = traktor.swap_root_folder(new_text, folder_name(bpm), st["native_folder"])
        if swapped is None:  # the view's folder was deleted in Traktor
            new_text = traktor.insert_root_node(new_text, st["native_folder"])
    else:
        new_text, _ = traktor.remove_root_folder(new_text, folder_name(bpm))
    stats["kept"] = kept
    if not dry_run:
        (view_home(bpm) / "collection.nml").write_text(text, encoding="utf-8")  # the view as you left it
    stats = traktor._save(path, text, new_text, stats, dry_run, prefix=BACKUP_PREFIX)
    if st.get("clock"):  # always back to your clock, even if it was changed while the view was loaded
        yours = st["clock"][0]
        stats["clock"] = f"{traktor.setting(path, traktor.MASTER_TEMPO)} → {yours} BPM"
        if not dry_run:
            traktor.set_setting(path, traktor.MASTER_TEMPO, yours, backup_tag="before-tempo-view-")
    if not dry_run:
        STATE.unlink()
        stats["now_loaded"] = "your normal genres, colours and hype"
    return stats


def load_with_lexicon(bpm: float = DEFAULT_BPM, nml: str | None = None, dry_run: bool = False,
                      force: bool = False) -> dict:
    """Traktor and Lexicon together. If Traktor already has this view, only Lexicon is switched."""
    from . import lexicon

    lexicon.tracks()  # reachable? Check before Traktor is touched
    st = loaded()
    if st and st["bpm"] != bpm:
        raise SystemExit(f"Traktor has the {st['bpm']:g} BPM view loaded. Use --bpm {st['bpm']:g}, or switch "
                         "back first: uv run dj-track-analyzer tempo native")
    out = {"traktor": f"{bpm:g} BPM view already loaded, left as is" if st else load(bpm, nml, dry_run, force)}
    if not dry_run:
        log("Traktor: done")
    out["lexicon"] = lexicon.load(bpm, folder_name(bpm), view_db(bpm), shifted_colour, dry_run)
    return out


def unload_with_lexicon(nml: str | None = None, dry_run: bool = False) -> dict:
    from . import lexicon

    lexicon.tracks()
    if loaded():
        log("Traktor: putting your own genres, colours and hype back…")
    return {"traktor": unload(nml, dry_run) if loaded() else "already normal",
            "lexicon": lexicon.unload(dry_run) if lexicon.loaded() else "already normal"}


def status() -> dict:
    from . import lexicon

    st = loaded()
    out = {"loaded_in_traktor": f"{st['bpm']:g} BPM view since {st['loaded_at']} ({len(st['tracks'])} tracks changed)"
           if st else "normal (no tempo view)", "loaded_in_lexicon": lexicon.status()}
    views = {}
    for d in sorted(HOME.glob("at-*")):
        db = d / "collection.db"
        if db.exists():
            with sqlite3.connect(db) as con:
                views[d.name] = {"tracks": con.execute("SELECT COUNT(*) FROM tracks").fetchone()[0],
                                 "folder": str(d)}
    out["views"] = views
    return out


def main(args) -> None:
    bpm = args.bpm
    log(f"Library: tempo view, every track stretched to {bpm:g} BPM ({view_home(bpm)})")
    if args.action == "refresh":
        refresh(bpm)
        out = {"bpm": bpm, "refreshed": str(view_home(bpm))}
    elif args.action == "scan":
        out = scan(bpm, workers=args.workers, rescan=args.rescan, nml=args.nml)
    elif args.action == "report":
        out = report(bpm, limit=args.limit)
        if not args.json:
            print(report_text(out))
            return
    elif args.action == "export":
        out = {"csv": str(export(bpm, Path(args.out) if args.out else None))}
    elif args.action == "nml":
        out = write_nml(bpm, args.nml, args.out)
    elif args.action == "load":
        out = (load_with_lexicon if args.lexicon else load)(bpm, args.nml, args.dry_run, args.force)
    elif args.action == "native":
        out = (unload_with_lexicon if args.lexicon else unload)(args.nml, args.dry_run)
        from . import lexicon
        if not args.lexicon and lexicon.loaded():
            out["note"] = "Lexicon still has the view. Switch it back: uv run dj-track-analyzer tempo native --lexicon"
    else:
        out = status()
    print(json.dumps(out, indent=2, ensure_ascii=False))
    if args.action in ("load", "native"):
        from .cli import say_if_nothing
        say_if_nothing(out, args.dry_run)
