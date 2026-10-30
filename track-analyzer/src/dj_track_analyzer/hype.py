"""
Hype: how hype each track is, as 1-10 ranked against your own collection (10 = top 10%).

  uv run dj-track-analyzer hype                         the most hype tracks
  uv run dj-track-analyzer hype --least                 the least hype (chill) tracks
  uv run dj-track-analyzer hype --colour Yellow --bpm 126 --key 8A
  uv run dj-track-analyzer hype --gems                  very hype, but 3-4 stars or hardly played
  uv run dj-track-analyzer hype --traktor [--dry-run]   write "Hype NN" into Comment in Traktor

Hype = half "rave sound" (how much the style model hears Trance / Hard Trance / Hands Up / Hardstyle /
Tech Trance, minus Deep Techno / Tech House / Deep House / Downtempo) and half energy (loudness, the
party and aggressive moods, minus relaxed). Each part is ranked within the collection, so the score
says "more hype than N% of my tracks". Found 2026-10-21 by comparing with the user's star ratings
(Spearman +0.34; the most hype quarter is 63% five-star vs 22% for the least hype quarter) and their
own "Hype!" comments (7 of 8 in the top third). It sorts well but can't predict exact stars.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from scipy.stats import rankdata

from . import collection
from . import analysis
from . import traktor
from .analysis import log

RAVE = ["Trance", "Hard Trance", "Hands Up", "Hardstyle", "Tech Trance"]
DEEP = ["Deep Techno", "Tech House", "Deep House", "Downtempo"]
STARS = {51: 1, 102: 2, 153: 3, 204: 4, 255: 5}  # Traktor RANKING → stars


def _rank(v: np.ndarray) -> np.ndarray:
    return rankdata(v) / len(v) - 0.5


def compute(con=None, progress=log) -> dict[str, int]:
    """Hype 1-10 for every scanned track, saved in collection.db (column `hype`)."""
    con = con or collection.db()
    rows = con.execute(
        "SELECT path, lufs, mood_party, mood_aggressive, mood_relaxed FROM tracks WHERE type='track' "
        "AND (error IS NULL OR error LIKE 'tags:%') AND lufs IS NOT NULL AND mood_party IS NOT NULL "
        "AND mood_aggressive IS NOT NULL AND mood_relaxed IS NOT NULL").fetchall()
    names = [analysis.split_style(c)[0] for c in analysis.style_classes()]
    rave_i, deep_i = [names.index(s) for s in RAVE], [names.index(s) for s in DEEP]
    paths, vals = [], []
    for r in rows:
        hit = analysis.cache_get(Path(r["path"]))  # style activations from the scan; never re-analyses
        if not hit:
            continue
        acts = hit[2]
        paths.append(r["path"])
        vals.append([acts[rave_i].sum(), acts[deep_i].sum(), r["lufs"], r["mood_party"],
                     r["mood_aggressive"], r["mood_relaxed"]])
    if len(paths) < 20:
        raise SystemExit("Need at least 20 scanned tracks: uv run dj-track-analyzer scan --traktor")
    v = np.array(vals, dtype=float)
    rave = (_rank(v[:, 0]) - _rank(v[:, 1])) / 2
    energy = (_rank(v[:, 2]) + _rank(v[:, 3]) + _rank(v[:, 4]) - _rank(v[:, 5])) / 4
    pct = rankdata(rave + energy) / len(paths)
    hype = {p: max(1, min(10, math.ceil(x * 10))) for p, x in zip(paths, pct)}
    con.execute("UPDATE tracks SET hype = NULL")
    con.executemany("UPDATE tracks SET hype=? WHERE path=?", [(h, p) for p, h in hype.items()])
    con.commit()
    progress(f"Hype scored for {len(hype)} tracks")
    return hype


def _traktor_info(nml: str | None) -> dict[str, dict]:
    """{path: {stars, plays, colour}} from Traktor's collection (read-only)."""
    try:
        root = ET.parse(traktor.find_collection(nml)).getroot()
    except (SystemExit, ET.ParseError, OSError):
        return {}
    out = {}
    for e in root.find("COLLECTION").iter("ENTRY"):
        loc, info = e.find("LOCATION"), e.find("INFO")
        if loc is None or info is None:
            continue
        c = info.get("COLOR")
        d = {"stars": STARS.get(int(info.get("RANKING") or 0)),
             "plays": int(info.get("PLAYCOUNT") or 0),
             "colour": analysis.COLOR_NAMES[int(c) - 1] if c and c.isdigit() and 1 <= int(c) <= 7 else None}
        for p in traktor.location_paths(loc.attrib):
            out[p] = d
    return out


def find(nml: str | None = None, least: bool = False, colour: str | None = None, bpm: float | None = None,
         bpm_tolerance_pct: float = 4.0, key: str | None = None, gems: bool = False, limit: int = 25,
         progress=log, rescore: bool = True, colours: dict[str, str] | None = None) -> dict:
    """rescore=False uses the hype stored by the last scan instead of recomputing it (writes nothing).
    colours: your colour per path, used before Traktor's (the web page passes the library's own, so a tempo view
    shows its colours, and the normal library its own even while a view is loaded in Traktor)."""
    con = collection.db()
    hype = compute(con, progress) if rescore else dict(
        con.execute("SELECT path, hype FROM tracks WHERE hype IS NOT NULL").fetchall())
    tk = _traktor_info(nml)
    rows = {r["path"]: r for r in con.execute(
        f"SELECT path, artist, title, filename, {collection.BPM_SQL} AS bpm, {collection.CAMELOT_SQL} AS camelot, "
        "traktor_color, color FROM tracks WHERE hype IS NOT NULL")}
    keys = collection.camelot_neighbors(key) if key else None
    picked = []
    for p, h in hype.items():
        r, t = rows[p], tk.get(p, {})
        mine = colours.get(p) if colours is not None else t.get("colour") or r["traktor_color"]
        col = mine or r["color"]
        if colour and (col or "").lower() != colour.strip().lower():
            continue
        if bpm and not (r["bpm"] and abs(r["bpm"] - bpm) <= bpm * bpm_tolerance_pct / 100):
            continue
        if keys and r["camelot"] not in keys:
            continue
        if gems and not (h >= 8 and ((t.get("stars") or 0) <= 4 or t.get("plays", 0) <= 1)):
            continue
        picked.append({"hype": h, "artist": r["artist"], "title": r["title"] or r["filename"],
                       "bpm": analysis.round_bpm(r["bpm"]), "camelot": r["camelot"],
                       "colour": col, "colour_is_yours": bool(mine),
                       "stars": t.get("stars"), "plays": t.get("plays"), "path": p})
    if gems:  # the most underrated first: hype high, stars and plays low
        picked.sort(key=lambda d: (-d["hype"], d["stars"] or 0, d["plays"] or 0))
    else:
        picked.sort(key=lambda d: (d["hype"], -(d["stars"] or 0)) if least else (-d["hype"], -(d["stars"] or 0)))
    rule = [f"colour {colour}" if colour else None,
            f"BPM {bpm:g} ±{bpm_tolerance_pct:g}%" if bpm else None,
            f"Camelot {', '.join(keys)}" if keys else None,
            "gems: hype 8+ but 4 stars or fewer, or played at most once" if gems else None]
    return {"scored": len(hype), "matches": len(picked), "showing": min(limit, len(picked)),
            "order": "least hype first" if least else "most hype first",
            "filters": [x for x in rule if x] or ["none"], "tracks": picked[:limit]}


def find_text(d: dict) -> str:
    out = [f"{d['matches']} of {d['scored']} tracks match ({', '.join(d['filters'])}); {d['order']}", ""]
    out.append(f"  {'hype':>4}  {'stars':5} {'plays':>5}  {'colour':8} {'bpm':>5} {'key':>4}  track")
    for t in d["tracks"]:
        stars = "★" * t["stars"] if t["stars"] else "-"
        col = (t["colour"] or "-") + ("" if t["colour_is_yours"] or not t["colour"] else "?")
        name = f"{t['artist']} - {t['title']}" if t["artist"] else t["title"]
        out.append(f"  {t['hype']:>4}  {stars:5} {t['plays'] if t['plays'] is not None else '-':>5}  {col:8} "
                   f"{t['bpm'] or '-':>5} {t['camelot'] or '-':>4}  {name[:70]}")
    out += ["", "hype: 10 = more hype than 90% of your tracks. colour '?' = predicted, not set by you."]
    return "\n".join(out)


def to_traktor(nml: str | None = None, dry_run: bool = False, remove: bool = False,
               clean_comment2: bool = False, force: bool = False, progress=log) -> dict:
    """Write 'Hype NN' into Comment (after your own text there), or take it out again.
    clean_comment2 only takes an old 'Hype NN' out of Comment 2."""
    if clean_comment2:
        return traktor.write_hype({}, nml, dry_run=dry_run, comment2_only=True)
    hype = {} if remove else compute(progress=progress)
    out = traktor.write_hype(hype, nml, dry_run=dry_run, remove=remove, force=force)
    if not remove:
        collection.export_csv(collection.DEFAULT_CSV)
    out["undo"] = "uv run dj-track-analyzer traktor --undo   (or: uv run dj-track-analyzer hype --traktor --remove)"
    return out
