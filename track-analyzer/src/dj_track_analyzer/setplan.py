"""
Set planner: a set that follows an arc on two axes, colour (Red = hard … Magenta = pretty) and hype (1 chill …
10 peak), with keys that mix.

  uv run dj-track-analyzer set                              20 tracks, warm-up arc
  uv run dj-track-analyzer set --arc journey --tracks 30
  uv run dj-track-analyzer set --arc mine                   the arc your Traktor History shows
  uv run dj-track-analyzer set --arc Magenta/3,Red/9,Violet/6
  uv run dj-track-analyzer set --tempo 145                  colours and hype as heard at 145 (its tempo view)
  uv run dj-track-analyzer set --bpm-step 3 --bpm 124-132   neighbours within 3%, BPM rising 124 → 132
  uv run dj-track-analyzer set --traktor [--dry-run]        also write it as a playlist (Traktor closed)
  uv run dj-track-analyzer set --remove "Set …"             take that playlist out again

Each slot of the set has a target colour and hype from the arc. A beam search picks the order with the lowest
total cost: distance from the targets, key clashes between neighbours (same key, ±1 on the Camelot wheel and the
relative major/minor mix), the same artist back to back, BPM jumps beyond --bpm-step; more stars cost a little less.
With key lock the key stays the track's own, so keys are checked as they are at any tempo.
"""

from __future__ import annotations

import re
import sqlite3
import time
import uuid
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import numpy as np

from . import analysis
from . import collection
from . import hype as hype_mod
from . import tempo
from . import traktor
from .analysis import COLOR_NAMES, log

# Arc points: (position in the set 0..1, colour 1 Red .. 7 Magenta, hype 1..10)
ARCS = {
    "warmup": [(0.0, 6.8, 2.0), (1.0, 3.5, 6.5)],
    "peak": [(0.0, 3.0, 7.0), (0.5, 1.5, 9.5), (0.75, 2.5, 8.0), (1.0, 1.2, 10.0)],
    "journey": [(0.0, 6.8, 2.5), (0.35, 4.5, 5.5), (0.75, 1.5, 9.5), (1.0, 5.5, 6.0)],
    "closing": [(0.0, 2.0, 9.0), (0.6, 4.0, 7.0), (1.0, 6.5, 4.0)],
}
LEARNED = "mine"
FOLDER = "Sets (Track Analyzer)"
BACKUP_PREFIX = "collection.nml.before-set-"

HYPE_STEP = 1.5      # this many hype steps cost as much as one colour step
KEY_CLASH = 4.0      # neighbours whose keys don't mix (= two colours off)
KEY_UNKNOWN = 1.5    # a neighbour without a key
SAME_ARTIST = 2.0    # same artist back to back
BPM_JUMP = 25.0      # with --bpm-step: a jump beyond it (nearly a hard rule)
STAR_BONUS = 0.25    # per star above 3
BEAM = 256
PER_STATE = 24


def _colour(name: str | None) -> int | None:
    return COLOR_NAMES.index(name) + 1 if name in COLOR_NAMES else None


def colour_name(pos: float) -> str:
    return COLOR_NAMES[int(np.clip(round(pos), 1, len(COLOR_NAMES))) - 1]


def parse_arc(text: str) -> list[tuple[float, float, float]]:
    """'Magenta/3,Red/9,Violet/6' → evenly spaced points. A colour can also be a number 1-7."""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) < 2:
        raise SystemExit(f"--arc: a name ({', '.join([*ARCS, LEARNED])}) or at least two points like Magenta/3,Red/9")
    pts = []
    for i, p in enumerate(parts):
        m = re.fullmatch(r"([A-Za-z]+|\d(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", p)
        if not m:
            raise SystemExit(f"--arc: '{p}' isn't colour/hype, e.g. Magenta/3")
        c = m.group(1)
        col = _colour(c.capitalize()) if c.isalpha() else float(c)
        if col is None or not 1 <= col <= 7:
            raise SystemExit(f"--arc: '{c}' isn't one of {', '.join(COLOR_NAMES)} (or 1-7)")
        h = float(m.group(2))
        if not 1 <= h <= 10:
            raise SystemExit(f"--arc: hype {h:g} isn't 1-10")
        pts.append((i / (len(parts) - 1), float(col), h))
    return pts


def targets(arc: list[tuple[float, float, float]], n: int) -> tuple[np.ndarray, np.ndarray]:
    xs = np.linspace(0, 1, n) if n > 1 else np.zeros(1)
    px, pc, ph = (np.array(v) for v in zip(*arc))
    return np.interp(xs, px, pc), np.interp(xs, px, ph)


# ----------------------------------------------------------------------------
# The library
# ----------------------------------------------------------------------------


def _view_rows(bpm: float) -> tuple[dict[str, float], dict[str, int], dict[str, float]]:
    """Colour (yours shifted, as `tempo load` writes it; else the view's predicted position), hype and
    stretch ratio per path in the tempo view at `bpm`. Read-only: the process's library stays as it is."""
    path = tempo.view_home(bpm) / "collection.db"
    if not path.exists():
        raise SystemExit(f"No {bpm:g} BPM tempo view yet. Make one with: uv run dj-track-analyzer tempo scan --bpm {bpm:g}"
                         "\n  (or plan without --tempo: colours and hype at each track's own tempo)")
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        yours = tempo.view_colours(con)
        colour, hype, ratio = {}, {}, {}
        for r in con.execute("SELECT path, color_position, hype, source_bpm FROM tracks WHERE type='track'"):
            c = _colour(yours.get(r["path"])) or r["color_position"]
            if c is not None:
                colour[r["path"]] = float(c)
            if r["hype"] is not None:
                hype[r["path"]] = r["hype"]
            if r["source_bpm"]:
                ratio[r["path"]] = bpm / r["source_bpm"]
    finally:
        con.close()
    return colour, hype, ratio


def library(tempo_bpm: float | None = None, nml: str | None = None) -> list[dict]:
    """Every scanned track with what the planner needs. Colour: yours (Traktor's, as the last scan read it),
    else the model's position (`colour_is_yours` False). Key and BPM as you mix with them (Traktor's first)."""
    with tempo.native_db() as con:
        rows = con.execute(
            f"SELECT path, artist, title, filename, {collection.BPM_SQL} AS mix_bpm, {collection.CAMELOT_SQL} AS mix_key, "
            "traktor_color, color_position, hype, approachability, duration_sec, my_genre, genre, genre_2 FROM tracks "
            "WHERE type='track' AND (error IS NULL OR error LIKE 'tags:%')").fetchall()
    view = _view_rows(tempo_bpm) if tempo_bpm else None
    tk = hype_mod._traktor_info(nml)
    out = []
    for r in rows:
        p = r["path"]
        yours = _colour(r["traktor_color"])
        if view:
            col, hy = view[0].get(p), view[1].get(p)
            yours_here = yours is not None
        else:
            col, hy, yours_here = (yours or r["color_position"]), r["hype"], yours is not None
        if col is None or hy is None:
            continue
        dur = r["duration_sec"]
        if view and dur and p in view[2]:
            dur /= view[2][p]
        t = tk.get(p, {})
        out.append({"path": p, "artist": r["artist"], "title": r["title"] or r["filename"],
                    "bpm": analysis.round_bpm(r["mix_bpm"]), "camelot": r["mix_key"],
                    "colour": float(col), "colour_is_yours": yours_here, "hype": int(hy),
                    "stars": t.get("stars"), "plays": t.get("plays"), "seconds": dur,
                    "approachability": r["approachability"],
                    "genres": [g for g in (r["my_genre"], r["genre"], r["genre_2"]) if g]})
    return out


def _entry_paths(root: ET.Element) -> dict[str, list[str]]:
    """Traktor's playlist key (VOLUME + DIR + FILE) → the paths it can be on this computer."""
    out = {}
    for e in root.find("COLLECTION").iter("ENTRY"):
        loc = e.find("LOCATION")
        if loc is not None:
            out[loc.get("VOLUME", "") + loc.get("DIR", "") + loc.get("FILE", "")] = [
                str(collection.nfc(p)) for p in traktor.location_paths(loc.attrib)]
    return out


def playlist_paths(name: str, nml: str | None = None) -> set[str]:
    """Paths in the Traktor playlist called `name` (any folder)."""
    root = ET.parse(traktor.find_collection(nml)).getroot()
    keys = _entry_paths(root)
    found = [n for n in root.iter("NODE") if n.get("TYPE") == "PLAYLIST" and n.get("NAME", "").lower() == name.lower()]
    if not found:
        raise SystemExit(f"No Traktor playlist called '{name}'")
    return {p for n in found for pk in n.iter("PRIMARYKEY") for p in keys.get(pk.get("KEY"), [])}


# ----------------------------------------------------------------------------
# Learned arc: where your sets put colour and hype (Traktor's History)
# ----------------------------------------------------------------------------


def learned_arc(tracks: list[dict], nml: str | None = None, bins: int = 5) -> tuple[list[tuple[float, float, float]], int]:
    """Mean colour and hype of what you played in each fifth of your sessions (8+ tracks)."""
    from .tracklist import read_history

    hist = traktor.find_collection(nml).parent / "History"
    by_path = {t["path"]: t for t in tracks}
    col, hy = defaultdict(list), defaultdict(list)
    sessions = 0
    for f in sorted(hist.glob("*.nml")):
        try:
            plays = [p for p in read_history(f) if p["public"]]
        except (ET.ParseError, OSError):
            continue
        seq = []
        for p in plays:
            t = by_path.get(str(collection.nfc(p["path"])))
            if t and (not seq or seq[-1] is not t):
                seq.append(t)
        if len(seq) < 8:
            continue
        sessions += 1
        for i, t in enumerate(seq):
            b = min(bins - 1, int(i / len(seq) * bins))
            col[b].append(t["colour"])
            hy[b].append(t["hype"])
    if sessions < 3:
        raise SystemExit(f"--arc {LEARNED} needs at least 3 sessions of 8+ known tracks in {hist} (found {sessions}).")
    return [((b + 0.5) / bins, float(np.mean(col[b])), float(np.mean(hy[b]))) for b in range(bins)], sessions


# ----------------------------------------------------------------------------
# Planning
# ----------------------------------------------------------------------------


def _artist(a: str | None) -> str:
    return re.split(r"\s+(?:feat\.?|ft\.?|&|x|vs\.?)\s+|,", (a or "").lower())[0].strip()


def transition_costs(tracks: list[dict], bpm_step: float | None) -> np.ndarray:
    n = len(tracks)
    cost = np.zeros((n, n))
    keys = [t["camelot"] for t in tracks]
    mixes = {k: set(collection.camelot_neighbors(k)) for k in set(keys) if k}
    artists = [_artist(t["artist"]) for t in tracks]
    bpms = np.array([t["bpm"] or np.nan for t in tracks], dtype=float)
    for a in range(n):
        ka = keys[a]
        for b in range(n):
            kb = keys[b]
            if not ka or not kb:
                cost[a, b] += KEY_UNKNOWN
            elif kb not in mixes[ka]:
                cost[a, b] += KEY_CLASH
            if artists[a] and artists[a] == artists[b]:
                cost[a, b] += SAME_ARTIST
    if bpm_step:
        jump = np.abs(bpms[None, :] - bpms[:, None]) / bpms[:, None] * 100
        cost += np.where(np.nan_to_num(jump, nan=0.0) > bpm_step, BPM_JUMP, 0.0)
    np.fill_diagonal(cost, np.inf)
    return cost


def plan(tracks: list[dict], tc: np.ndarray, th: np.ndarray, bpm_step: float | None = None,
         bpm_target: np.ndarray | None = None, first: int | None = None, last: int | None = None) -> tuple[list[int], float]:
    """Beam search over the slots. Returns (track indices in order, total cost)."""
    n, k = len(tc), len(tracks)
    col = np.array([t["colour"] for t in tracks])
    hyp = np.array([t["hype"] for t in tracks], dtype=float)
    stars = np.array([t["stars"] or 3 for t in tracks], dtype=float)
    node = (col[None, :] - tc[:, None]) ** 2 + ((hyp[None, :] - th[:, None]) / HYPE_STEP) ** 2
    node -= STAR_BONUS * (stars - 3)[None, :]
    if bpm_target is not None:
        bpm = np.array([t["bpm"] or np.nan for t in tracks], dtype=float)
        node += np.nan_to_num(((bpm[None, :] - bpm_target[:, None]) / 2) ** 2 * 0.25, nan=1.0)
    for i, j in ((0, first), (n - 1, last)):
        if j is not None:
            node[:, j] = np.inf
            node[i] = np.inf
            node[i, j] = 0.0
    trans = transition_costs(tracks, bpm_step)

    beam = [(c, [j]) for j, c in enumerate(node[0]) if np.isfinite(c)]
    beam = sorted(beam, key=lambda s: s[0])[:BEAM]
    for i in range(1, n):
        cands, seen = [], set()
        for cost, seq in beam:
            v = cost + trans[seq[-1]] + node[i]
            v[seq] = np.inf
            top = np.argpartition(v, min(PER_STATE, k - 1))[:PER_STATE] if k > PER_STATE else np.arange(k)
            cands += [(v[j], seq + [int(j)]) for j in top if np.isfinite(v[j])]
        cands.sort(key=lambda s: s[0])
        beam = []
        for c, seq in cands:
            sig = (seq[-1], frozenset(seq))
            if sig not in seen:
                seen.add(sig)
                beam.append((c, seq))
                if len(beam) == BEAM:
                    break
        if not beam:
            raise SystemExit("No set fits these rules (check --start and --end).")
    cost, seq = beam[0]
    return seq, float(cost)


def _find(tracks: list[dict], text: str, what: str) -> int:
    t = text.lower()
    hits = [i for i, x in enumerate(tracks) if t in f"{x['artist'] or ''} - {x['title'] or ''} {x['path']}".lower()]
    if not hits:
        raise SystemExit(f"{what}: no track matches '{text}'")
    if len(hits) > 1:
        names = "\n  ".join(f"{tracks[i]['artist']} - {tracks[i]['title']}" for i in hits[:8])
        raise SystemExit(f"{what}: '{text}' matches {len(hits)} tracks, be more specific:\n  {names}")
    return hits[0]


def bpm_bounds(bpm_range: str | None) -> tuple[float | None, float | None]:
    if not bpm_range:
        return None, None
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:-\s*(\d+(?:\.\d+)?))?\s*", bpm_range)
    if not m:
        raise SystemExit("--bpm: a range like 124-132 (or one BPM)")
    lo, hi = float(m.group(1)), float(m.group(2) or m.group(1))
    return min(lo, hi), max(lo, hi)


def filtered(tracks: list[dict], bpm_range: str | None = None, genre: str | None = None, playlist: str | None = None,
             min_stars: int | None = None, exclude: list[str] | None = None, nml: str | None = None,
             pinned: set[str] = frozenset()) -> list[dict]:
    """The tracks that pass the pool filters (pinned paths always do)."""
    lo, hi = bpm_bounds(bpm_range)
    keep = playlist_paths(playlist, nml) if playlist else None

    def wanted(t):
        if t["path"] in pinned:
            return True
        if keep is not None and t["path"] not in keep:
            return False
        if lo is not None and not (t["bpm"] and lo - 0.5 <= t["bpm"] <= hi + 0.5):
            return False
        if genre and not any(genre.lower() == g.lower() for g in t["genres"]):
            return False
        if min_stars and (t["stars"] or 0) < min_stars:
            return False
        name = f"{t['artist'] or ''} - {t['title'] or ''} {t['path']}".lower()
        return not any(x.lower() in name for x in exclude or [])

    return [t for t in tracks if wanted(t)]


def build(arc: str = "warmup", n: int = 20, tempo_bpm: float | None = None, bpm_step: float | None = None,
          bpm_range: str | None = None, genre: str | None = None, playlist: str | None = None,
          min_stars: int | None = None, start: str | None = None, end: str | None = None,
          exclude: list[str] | None = None, nml: str | None = None) -> dict:
    if n < 1:
        raise SystemExit("--tracks: at least 1")
    tracks = library(tempo_bpm, nml)
    if not tracks:
        raise SystemExit("Nothing scanned with colour and hype yet. Run `uv run dj-track-analyzer scan --traktor`.")
    if arc == LEARNED:
        points, sessions = learned_arc(tracks, nml)
    else:
        points, sessions = (ARCS[arc] if arc in ARCS else parse_arc(arc)), None
    lo, hi = bpm_bounds(bpm_range)
    pinned = {tracks[_find(tracks, p, "--start" if p == start else "--end")]["path"] for p in (start, end) if p}
    tracks = filtered(tracks, bpm_range, genre, playlist, min_stars, exclude, nml, pinned)
    if len(tracks) < n:
        raise SystemExit(f"Only {len(tracks)} tracks fit the filters, fewer than --tracks {n}.")
    tc, th = targets(points, n)
    bt = np.linspace(lo, hi, n) if (bpm_step and lo is not None and hi > lo) else None
    first = _find(tracks, start, "--start") if start else None
    last = _find(tracks, end, "--end") if end else None
    if first is not None and last is not None and (first == last or n == 1):
        raise SystemExit("--start and --end need two different tracks and --tracks 2 or more.")
    t0 = time.time()
    seq, cost = plan(tracks, tc, th, bpm_step, bt, first, last)
    log(f"Planned {n} tracks from {len(tracks)} in {time.time() - t0:.1f} s")

    rows, clashes = [], 0
    for i, j in enumerate(seq):
        t = tracks[j]
        prev = tracks[seq[i - 1]] if i else None
        mix = None
        if prev:
            mix = (t["camelot"] in collection.camelot_neighbors(prev["camelot"])
                   if t["camelot"] and prev["camelot"] else None)
            clashes += mix is False
        rows.append({"slot": i + 1, "target_colour": round(float(tc[i]), 2), "target_hype": round(float(th[i]), 1),
                     "colour": colour_name(t["colour"]), "colour_position": round(t["colour"], 2),
                     "colour_is_yours": t["colour_is_yours"], "hype": t["hype"], "camelot": t["camelot"],
                     "key_mixes": mix, "bpm": t["bpm"], "stars": t["stars"], "artist": t["artist"],
                     "title": t["title"], "path": t["path"]})
    secs = [tracks[j]["seconds"] for j in seq if tracks[j]["seconds"]]
    return {
        "arc": arc, "arc_points": [{"at": round(x, 2), "colour": round(c, 2), "hype": round(h, 1)} for x, c, h in points],
        "learned_from_sessions": sessions,
        "library": f"{tempo_bpm:g} BPM tempo view" if tempo_bpm else "each track at its own tempo",
        "pool": len(tracks), "tracks": rows, "key_clashes": clashes, "cost": round(cost, 2),
        "colour_off": round(float(np.mean([abs(r["colour_position"] - r["target_colour"]) for r in rows])), 2),
        "hype_off": round(float(np.mean([abs(r["hype"] - r["target_hype"]) for r in rows])), 2),
        "full_length_minutes": round(sum(secs) / 60) if secs else None,
    }


def text(d: dict) -> str:
    arc = " → ".join(f"{colour_name(p['colour'])}/{p['hype']:g}" for p in d["arc_points"])
    out = [f"Set: {d['arc']} ({arc}), {d['library']}"
           + (f", learned from {d['learned_from_sessions']} sessions" if d["learned_from_sessions"] else ""),
           f"{len(d['tracks'])} tracks chosen from {d['pool']}", "",
           f"  {'#':>2}  {'aim':12} {'colour':8} {'hype':>4}  {'key':>4}   {'bpm':>4} {'stars':5}  track"]
    for r in d["tracks"]:
        aim = f"{colour_name(r['target_colour'])[:7]}/{r['target_hype']:.0f}"
        col = r["colour"] + ("" if r["colour_is_yours"] else "?")
        mark = {True: " ", False: "✗", None: "·"}[r["key_mixes"]] if r["slot"] > 1 else " "
        stars = "★" * r["stars"] if r["stars"] else "-"
        name = f"{r['artist']} - {r['title']}" if r["artist"] else r["title"]
        out.append(f"  {r['slot']:>2}  {aim:12} {col:8} {r['hype']:>4}  {r['camelot'] or '-':>4} {mark} {r['bpm'] or '-':>4} "
                   f"{stars:5}  {name[:64]}")
    out += ["", f"Key clashes: {d['key_clashes']}. Off the arc on average: {d['colour_off']:g} colours, "
                f"{d['hype_off']:g} hype."
            + (f" Played in full: {d['full_length_minutes']} min." if d["full_length_minutes"] else ""),
            "aim = the arc's colour/hype for that slot. ✗ = key doesn't mix with the track before, · = key unknown, "
            "colour ? = predicted, not set by you."]
    return "\n".join(out)


# ----------------------------------------------------------------------------
# Traktor playlist
# ----------------------------------------------------------------------------


def _playlist_node(name: str, keys: list[str], folder: str = FOLDER) -> str:
    # The folder is part of the UUID: a set and a crate of the same name must not share one
    uid = uuid.uuid5(traktor.UUID_NS, ("set:" if folder == FOLDER else folder + ":") + name).hex
    entries = "".join(f'<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="{traktor.esc(k)}"></PRIMARYKEY>\n</ENTRY>\n' for k in keys)
    return (f'<NODE TYPE="PLAYLIST" NAME="{traktor.esc(name)}"><PLAYLIST ENTRIES="{len(keys)}" TYPE="LIST" '
            f'UUID="{uid}">{entries}</PLAYLIST>\n</NODE>\n')


def _folder_children(folder: str) -> list[str]:
    """The child NODEs of our folder's text."""
    body_start = folder.index(">", folder.index("<SUBNODES")) + 1
    kids, i = [], body_start
    while True:
        j = folder.find("<NODE ", i)
        if j == -1:
            return kids
        end = traktor._node_span(folder, j)
        kids.append(folder[j:end])
        i = end


def _with_playlist(text: str, name: str, node: str | None, folder: str = FOLDER, first: bool = True) -> tuple[str, bool]:
    """`folder` (under the root) with the playlist `name` replaced by `node` in its place, or added (first, or last
    with first=False) when new; removed when node is None. Returns (text, whether a playlist of that name was there)."""
    i = text.find(f'<NODE TYPE="FOLDER" NAME="{traktor.esc(folder)}">')
    kids = _folder_children(text[i:traktor._node_span(text, i)]) if i != -1 else []
    mine = f'<NODE TYPE="PLAYLIST" NAME="{traktor.esc(name)}">'
    at = next((n for n, k in enumerate(kids) if k.startswith(mine)), None)
    if at is not None:
        kids = kids[:at] + ([node] if node else []) + kids[at + 1:]
    elif node:
        kids = [node] + kids if first else kids + [node]
    node_text = (f'<NODE TYPE="FOLDER" NAME="{traktor.esc(folder)}"><SUBNODES COUNT="{len(kids)}">'
                 + "".join(kids) + "</SUBNODES>\n</NODE>\n")
    if i != -1 and not kids:
        return traktor.remove_root_folder(text, folder)[0], at is not None
    if i != -1:
        return traktor.swap_root_folder(text, folder, node_text)[0], at is not None
    return (traktor.insert_root_node(text, node_text) if kids else text), at is not None


def to_traktor(d: dict, name: str, nml: str | None = None, dry_run: bool = False) -> dict:
    path = traktor.find_collection(nml)
    traktor.check_closed(dry_run, "the set playlist")
    text_ = path.read_text(encoding="utf-8")
    by_path = {p: k for k, ps in _entry_paths(ET.fromstring(text_.encode("utf-8"))).items() for p in ps}
    keys = [by_path.get(r["path"]) for r in d["tracks"]]
    missing = [r["title"] for r, k in zip(d["tracks"], keys) if k is None]
    new, replaced = _with_playlist(text_, name, _playlist_node(name, [k for k in keys if k]))
    out = traktor._save(path, text_, new, {"playlist": f"{FOLDER} / {name}", "tracks": len(keys) - len(missing),
                                           "replaced": replaced, "not_in_traktor": missing}, dry_run, BACKUP_PREFIX)
    out["undo"] = f'uv run dj-track-analyzer set --remove "{name}"'
    return out


def remove(name: str, nml: str | None = None, dry_run: bool = False) -> dict:
    path = traktor.find_collection(nml)
    traktor.check_closed(dry_run, "removing the playlist")
    text_ = path.read_text(encoding="utf-8")
    new, had = _with_playlist(text_, name, None)
    if not had:
        raise SystemExit(f"No playlist '{name}' in Traktor's '{FOLDER}' folder")
    return traktor._save(path, text_, new, {"removed": f"{FOLDER} / {name}"}, dry_run, BACKUP_PREFIX)


# ----------------------------------------------------------------------------
# Crates: warm-up / peak / closing by rule (a track can be in more than one)
# ----------------------------------------------------------------------------

CRATE_FOLDER = "Crates (Track Analyzer)"
# colour zone (1 Red .. 7 Magenta), hype range, order inside the crate
CRATES = {
    "Warm-up": {"colours": (4, 7), "hype": (1, 5), "order": "most approachable first"},
    "Peak": {"colours": (1, 3), "hype": (8, 10), "order": "most hype first, hardest first"},
    "Closing": {"colours": (5, 7), "hype": (6, 10), "order": "most hype first, prettiest first"},
}


def _crate_order(name: str):
    if name == "Warm-up":
        return lambda t: (-(t["approachability"] if t["approachability"] is not None else -9), t["hype"])
    if name == "Peak":
        return lambda t: (-t["hype"], t["colour"], -(t["stars"] or 0))
    return lambda t: (-t["hype"], -t["colour"], -(t["stars"] or 0))


def crate_name(name: str, tempo_bpm: float | None) -> str:
    return f"{name} @{tempo_bpm:g}" if tempo_bpm else name


def crates(tempo_bpm: float | None = None, warmup_max_hype: int = 5, peak_min_hype: int = 8,
           closing_min_hype: int = 6, bpm_range: str | None = None, genre: str | None = None,
           playlist: str | None = None, min_stars: int | None = None, exclude: list[str] | None = None,
           nml: str | None = None) -> dict:
    tracks = filtered(library(tempo_bpm, nml), bpm_range, genre, playlist, min_stars, exclude, nml)
    if not tracks:
        raise SystemExit("No scanned tracks with colour and hype fit. Run `uv run dj-track-analyzer scan --traktor`.")
    rules = {k: dict(v) for k, v in CRATES.items()}
    rules["Warm-up"]["hype"] = (1, warmup_max_hype)
    rules["Peak"]["hype"] = (peak_min_hype, 10)
    rules["Closing"]["hype"] = (closing_min_hype, 10)
    out = []
    for name, r in rules.items():
        (c0, c1), (h0, h1) = r["colours"], r["hype"]
        got = [t for t in tracks if c0 <= round(t["colour"]) <= c1 and h0 <= t["hype"] <= h1]
        got.sort(key=_crate_order(name))
        out.append({
            "crate": crate_name(name, tempo_bpm),
            "rule": f"{COLOR_NAMES[c0 - 1]}…{COLOR_NAMES[c1 - 1]}, hype {h0}-{h1}",
            "order": r["order"], "count": len(got),
            "tracks": [{"colour": colour_name(t["colour"]), "colour_is_yours": t["colour_is_yours"], "hype": t["hype"],
                        "approachability": None if t["approachability"] is None else round(t["approachability"], 3),
                        "camelot": t["camelot"], "bpm": t["bpm"], "stars": t["stars"], "artist": t["artist"],
                        "title": t["title"], "path": t["path"]} for t in got]})
    in_any = {t["path"] for c in out for t in c["tracks"]}
    return {"library": f"{tempo_bpm:g} BPM tempo view" if tempo_bpm else "each track at its own tempo",
            "pool": len(tracks), "in_a_crate": len(in_any), "crates": out}


def crates_text(d: dict, limit: int = 15) -> str:
    out = [f"Crates from {d['pool']} tracks ({d['library']}); {d['in_a_crate']} are in at least one"]
    for c in d["crates"]:
        out += ["", f"{c['crate']}: {c['count']} tracks ({c['rule']}; {c['order']})",
                f"  {'colour':8} {'hype':>4} {'appr':>5}  {'key':>4} {'bpm':>4} {'stars':5}  track"]
        for t in c["tracks"][:limit]:
            col = t["colour"] + ("" if t["colour_is_yours"] else "?")
            ap = f"{t['approachability']:.2f}" if t["approachability"] is not None else "-"
            stars = "★" * t["stars"] if t["stars"] else "-"
            name = f"{t['artist']} - {t['title']}" if t["artist"] else t["title"]
            out.append(f"  {col:8} {t['hype']:>4} {ap:>5}  {t['camelot'] or '-':>4} {t['bpm'] or '-':>4} {stars:5}  {name[:64]}")
        if c["count"] > limit:
            out.append(f"  … and {c['count'] - limit} more")
    out += ["", "appr = approachability (higher = easier to get into). colour ? = predicted, not set by you."]
    return "\n".join(out)


def crates_to_traktor(d: dict, nml: str | None = None, dry_run: bool = False) -> dict:
    """Each crate as a playlist in the crates folder; a playlist of the same name is replaced in place."""
    path = traktor.find_collection(nml)
    traktor.check_closed(dry_run, "the crate playlists")
    text_ = path.read_text(encoding="utf-8")
    by_path = {p: k for k, ps in _entry_paths(ET.fromstring(text_.encode("utf-8"))).items() for p in ps}
    new, written, missing = text_, {}, 0
    for c in d["crates"]:
        keys = [by_path.get(t["path"]) for t in c["tracks"]]
        missing += keys.count(None)
        new = _with_playlist(new, c["crate"], _playlist_node(c["crate"], [k for k in keys if k], CRATE_FOLDER),
                             CRATE_FOLDER, first=False)[0]
        written[c["crate"]] = len(keys) - keys.count(None)
    out = traktor._save(path, text_, new, {"folder": CRATE_FOLDER, "playlists": written,
                                           "not_in_traktor": missing}, dry_run, BACKUP_PREFIX)
    out["undo"] = "uv run dj-track-analyzer crates --remove" + (
        f" --tempo {d['library'].split()[0]}" if d["library"].endswith("view") else "")
    return out


def remove_crates(tempo_bpm: float | None = None, nml: str | None = None, dry_run: bool = False) -> dict:
    """Take this library's crate playlists out again (the folder goes when it's empty)."""
    path = traktor.find_collection(nml)
    traktor.check_closed(dry_run, "removing the crates")
    text_ = path.read_text(encoding="utf-8")
    new, gone = text_, []
    for name in CRATES:
        new, had = _with_playlist(new, crate_name(name, tempo_bpm), None, CRATE_FOLDER)
        if had:
            gone.append(crate_name(name, tempo_bpm))
    if not gone:
        raise SystemExit(f"No crate playlists for this library in Traktor's '{CRATE_FOLDER}' folder")
    return traktor._save(path, text_, new, {"removed": gone}, dry_run, BACKUP_PREFIX)


def main_crates(args) -> None:
    import json

    if args.remove:
        print(json.dumps(remove_crates(args.tempo, args.nml, args.dry_run), indent=2, ensure_ascii=False))
        return
    d = crates(args.tempo, args.warmup_max_hype, args.peak_min_hype, args.closing_min_hype, args.bpm, args.genre,
               args.playlist, args.min_stars, args.exclude, args.nml)
    if args.traktor:
        d["traktor"] = crates_to_traktor(d, args.nml, args.dry_run)
    print(json.dumps(d, indent=2, ensure_ascii=False) if args.json else crates_text(d, args.limit))
    if args.traktor and not args.json:
        t = d["traktor"]
        lists = ", ".join(f"{k} ({v})" for k, v in t["playlists"].items())
        log(f"{'Would write' if t['dry_run'] else 'Wrote'} into '{CRATE_FOLDER}': {lists}"
            + (f"; {t['not_in_traktor']} tracks not in Traktor's collection" if t["not_in_traktor"] else "")
            + ("" if t["dry_run"] or not t.get("changed") else f". Undo: {t['undo']}"))


def main(args) -> None:
    import json

    if args.remove:
        out = remove(args.remove, args.nml, args.dry_run)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return
    d = build(args.arc, args.tracks, args.tempo, args.bpm_step, args.bpm, args.genre, args.playlist,
              args.min_stars, args.start, args.end, args.exclude, args.nml)
    if args.traktor:
        name = args.name or f"Set {time.strftime('%Y-%m-%d %H.%M')} {d['arc']}"
        d["traktor"] = to_traktor(d, name, args.nml, args.dry_run)
    print(json.dumps(d, indent=2, ensure_ascii=False) if args.json else text(d))
    if args.traktor and not args.json:
        t = d["traktor"]
        verb = "Would write" if t["dry_run"] else "Wrote"
        log(f"{verb} playlist '{t['playlist']}' ({t['tracks']} tracks"
            + (f", {len(t['not_in_traktor'])} not in Traktor's collection" if t["not_in_traktor"] else "")
            + ")" + ("" if t["dry_run"] or not t.get("changed") else f". Undo: {t['undo']}"))
