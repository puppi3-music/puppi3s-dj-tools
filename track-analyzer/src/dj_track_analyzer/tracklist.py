"""
Line a recorded DJ mix up with Traktor's history file of the same session (`analyze --tracklist`).

Traktor writes a history playlist for every session (Documents/Native Instruments/Traktor x/History/
history_YYYYyMMmDDd_HHhMMmSSs.nml): each played track with the deck, the time it started and how long
it played. Traktor's own recordings are named Recording_YYYY-MM-DD_HHhMMmSSs.wav after the time they
started, so each track's place in the recording follows. For other recordings pass --start.

With that, each track gets its stretch of the mix analysed on its own:
- key: Traktor's key for the track against the key heard (by the key CNN) in the middle of its main stretch (from its
  start to the next track's start, without the blends at either end). Key lock is assumed on; when it doesn't fit, the keys the tracks would
  have with key lock off (pitch moved with the tempo) are tried too, and the better fit is reported.
- tempo: the track's own BPM against the BPM it was played at.
- genre: how the mix sounds as played, at the BPM played: the speed-up and the mixing (stems, layers,
  effects) both really change a track. Only the models' habit of hearing psytrance in almost any sped-up
  four-on-the-floor is held back, as in the tempo view: psy counts up to 1.5x the track's own-tempo level.
  With a `tempo scan` view, `mixing` lists the styles the mixing pushed up or down against the track
  alone at that tempo. The uncorrected label and the library genre are kept next to it.

Overlaps, long blends and effects are normal in a mix, so a key mismatch can be the other deck: a track
whose main stretch is under 30 s gets no key verdict rather than a wrong one.
"""

from __future__ import annotations

import glob
import os
import re
import sqlite3
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from . import analysis
from .analysis import camelot, dj_genre, fmt_time, fold_bpm, label_scores, round_bpm

HISTORY_GLOB = "~/Documents/Native Instruments/Traktor*/History/history_*.nml"
RECORDING_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})h(\d{2})m(\d{2})s")
HISTORY_RE = re.compile(r"history_(\d{4})y(\d{1,2})m(\d{1,2})d")
PITCH_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

MAIN_TRIM = 0.2      # share of a track's main stretch left off each end (blending in, the next intro)
MIN_MAIN = 30.0      # a track needs this much main stretch for a key verdict
# How long after Traktor logs a track's start it takes over: depends on the DJ, so it's measured,
# as the lag at which most keys match (with key lock on and off).
LAGS = (0, 30, 60, 90, 120, 150)
SPEED_GENRE = 0.04   # tempo changed by more than this: judge the genre at the track's own BPM


# ----------------------------------------------------------------------------
# Reading the history
# ----------------------------------------------------------------------------


def recording_start(path: str | Path) -> datetime | None:
    """Start time from a Traktor recording's name (Recording_2026-10-12_16h27m17s.wav)."""
    m = RECORDING_RE.search(Path(path).name)
    return datetime(*map(int, m.groups())) if m else None


def parse_start(text: str, day: date | None) -> datetime:
    """--start: 'HH:MM:SS' (on the recording's date, or today) or 'YYYY-MM-DD HH:MM:SS'."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(text, fmt).time()
            return datetime.combine(day or date.today(), t)
        except ValueError:
            pass
    raise SystemExit(f"--start takes HH:MM:SS or 'YYYY-MM-DD HH:MM:SS', not {text!r}")


def _key_name(value: str | None) -> tuple[str, str] | None:
    """Traktor's MUSICAL_KEY VALUE (0-11 = C..B major, 12-23 = C..B minor) -> ('F', 'minor')."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return (PITCH_NAMES[v % 12], "minor" if v >= 12 else "major") if 0 <= v < 24 else None


def _location_key(loc: ET.Element) -> str:
    return loc.get("VOLUME", "") + loc.get("DIR", "") + loc.get("FILE", "")


def _local_path(loc: ET.Element) -> str:
    from .traktor import location_paths

    paths = location_paths(dict(loc.attrib))
    return next((p for p in paths if os.path.exists(p)), paths[0])


def read_history(path: str | Path) -> list[dict]:
    """Every play in a history file, in order: start time, seconds played, deck and the track's
    BPM and key as Traktor has them."""
    root = ET.parse(path).getroot()
    tracks = {}
    for e in root.iterfind("COLLECTION/ENTRY"):
        loc = e.find("LOCATION")
        if loc is None:
            continue
        tempo, mk = e.find("TEMPO"), e.find("MUSICAL_KEY")
        tracks[_location_key(loc)] = {
            "artist": e.get("ARTIST"), "title": e.get("TITLE"), "path": _local_path(loc),
            "bpm": float(tempo.get("BPM")) if tempo is not None and tempo.get("BPM") else None,
            "key": _key_name(mk.get("VALUE")) if mk is not None else None,
        }
    plays = []
    for e in root.iter("ENTRY"):
        pk, ext = e.find("PRIMARYKEY"), e.find("EXTENDEDDATA")
        if pk is None or ext is None or ext.get("STARTTIME") is None:
            continue
        d = int(ext.get("STARTDATE", 0))  # year << 16 | month << 8 | day
        try:
            start = datetime(d >> 16, (d >> 8) & 255, d & 255) + timedelta(seconds=int(ext.get("STARTTIME")))
        except ValueError:
            continue
        track = tracks.get(pk.get("KEY"), {"path": pk.get("KEY")})
        plays.append({**track, "start": start, "seconds": float(ext.get("DURATION", 0)),
                      "deck": "ABCD"[int(ext.get("DECK", 0)) % 4], "public": ext.get("PLAYEDPUBLIC") != "0"})
    return sorted(plays, key=lambda p: p["start"])


def _overlap(plays: list[dict], start: datetime, duration: float) -> float:
    end = start + timedelta(seconds=duration)
    return sum(max(0.0, (min(end, p["start"] + timedelta(seconds=p["seconds"])) - max(start, p["start"]))
                   .total_seconds()) for p in plays)


def find_history(start: datetime, duration: float) -> Path | None:
    """The history file covering most of the recording (sessions can run past midnight)."""
    days = {start.date(), (start - timedelta(days=1)).date()}
    best = None
    for f in glob.glob(os.path.expanduser(HISTORY_GLOB)):
        m = HISTORY_RE.search(Path(f).name)
        if not m or date(*map(int, m.groups())) not in days:
            continue
        try:
            covered = _overlap(read_history(f), start, duration)
        except ET.ParseError:
            continue
        rank = (covered, " copy" not in f, os.path.getmtime(f))  # backup copies of the Traktor folder lose ties
        if covered > 0 and (best is None or rank > best[0]):
            best = (rank, Path(f))
    return best[1] if best else None


def place(plays: list[dict], start: datetime, duration: float) -> list[dict]:
    """Each play's span in the recording (seconds), and the next track's start. Traktor's play time
    runs on while a faded-out deck keeps playing, so plays overlap most of a mix."""
    out = []
    for p in plays:
        a = (p["start"] - start).total_seconds()
        b = min(duration, a + p["seconds"])
        if b > max(0.0, a) and p["public"]:
            out.append({**p, "rec_start": max(0.0, a), "rec_end": b})
    for i, p in enumerate(out):
        p["next_start"] = out[i + 1]["rec_start"] if i + 1 < len(out) else p["rec_end"]
    return out


def slot(p: dict, lag: float) -> tuple[float, float]:
    """A track's main stretch: from its start to the next track's start (the usual tracklist cut),
    moved later by the time a blend takes, never past the end of its own play."""
    return p["rec_start"] + lag, max(p["rec_start"] + lag, min(p["rec_end"], p["next_start"] + lag))


def window(p: dict, lag: float) -> tuple[float, float]:
    """The middle of the main stretch, where the track is most likely the main sound."""
    a, b = slot(p, lag)
    cut = MAIN_TRIM * (b - a)
    return a + cut, b - cut


# ----------------------------------------------------------------------------
# Checking the analysis against it
# ----------------------------------------------------------------------------


def _shift_key(key: tuple[str, str], semitones: int) -> tuple[str, str]:
    return PITCH_NAMES[(analysis.PITCH[key[0]] + semitones) % 12], key[1]


def key_relation(expected: str | None, heard: str | None) -> str | None:
    """How a heard Camelot code relates to the expected one."""
    if not expected or not heard:
        return None
    if expected == heard:
        return "match"
    (n1, l1), (n2, l2) = (int(expected[:-1]), expected[-1]), (int(heard[:-1]), heard[-1])
    if n1 == n2:
        return "relative major/minor"
    if l1 == l2 and (n1 - n2) % 12 in (1, 11):
        return "neighbour"
    return "mismatch"


def _speed(played: float | None, own: float | None) -> float | None:
    """played / own BPM, with half/double-time readings folded back (0.7-1.4)."""
    if not played or not own:
        return None
    r = played / own
    while r > 1.4:
        r /= 2
    while r < 0.7:
        r *= 2
    return r


def _library(paths: list[str], db: Path | None = None) -> dict[str, dict]:
    """{path: {genre, camelot (our CNN key), mood}} from a library (the normal one: each track at its own tempo;
    or a tempo view's collection.db), where scanned."""
    from .collection import DB, nfc

    db = db or DB
    if not db.exists():
        return {}
    cols = ", ".join(f"mood_{m}" for m in analysis.MOODS)
    out = {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for p in paths:
            row = con.execute(f"SELECT genre, cnn_camelot, {cols} FROM tracks WHERE path=?", (str(nfc(p)),)).fetchone()
            if row:
                mood = {m: v for m, v in zip(analysis.MOODS, row[2:]) if v is not None}
                out[p] = {"genre": row[0], "camelot": row[1], "mood": mood}
    finally:
        con.close()
    return out


def camelot_step(a: str | None, b: str | None) -> str | None:
    """How a change of key from a to b sits on the Camelot wheel: "same key", "one step" (the relative key or a
    neighbour), "two steps" (a neighbour's relative, or two numbers along) or "clash"."""
    if not a or not b:
        return None
    (n1, l1), (n2, l2) = (int(a[:-1]), a[-1]), (int(b[:-1]), b[-1])
    d = min((n1 - n2) % 12, (n2 - n1) % 12) + (l1 != l2)
    return "same key" if d == 0 else "one step" if d == 1 else "two steps" if d == 2 else "clash"


def _patch_mean(patches: dict, duration: float, a: float, b: float) -> dict:
    """Mood scores (mood_patches) averaged over a stretch of the recording."""
    out = {}
    for m, p in patches.items():
        i0 = int(a / duration * len(p))
        out[m] = round(float(p[i0: max(i0 + 1, int(b / duration * len(p)))].mean()), 3)
    return out


def _off_key(p: dict) -> str | None:
    """The key a track has played with key lock off (pitch moves with the tempo); None when the
    pitch would sit between two keys."""
    if not p.get("key") or not p.get("speed"):
        return None
    semis = 12 * np.log2(p["speed"])
    if abs(semis - round(semis)) > 0.3:
        return None
    return camelot(*_shift_key(p["key"], int(round(semis))))


def _fit_keys(placed: list[dict], pool) -> dict:
    """Pick the blend lag and key lock setting under which most heard keys match Traktor's.
    Key lock on wins ties, and off has to win by two tracks: on is how most DJs play."""
    cells = [(i, lag) for i, p in enumerate(placed) for lag in LAGS
             if p.get("key") and window(p, lag)[1] - window(p, lag)[0] >= MIN_MAIN]
    # track by track (each at every blend lag), so the count can say how many tracks are done
    last = {i: k for k, (i, _lag) in enumerate(cells)}
    n_tracks = len(last)
    meter = lambda n: f"  matching keys: {sum(k < n for k in last.values())}/{n_tracks} tracks ({n}/{len(cells)} stretches)"  # noqa: E731
    # heard with the CNN, as the library's keys: Essentia's edma heard 4 of 20 tracks of a test set with major and
    # minor swapped (11A as 2B)
    measured = pool.measure([(*window(placed[i], lag), False, "cnn") for i, lag in cells], meter)
    heard = {cell: m["cnn_camelot"] for cell, m in zip(cells, measured)}
    best, per_lag = {}, {"on": {}, "off": {}}
    for lag in LAGS:
        on = sum(heard.get((i, lag)) == p["camelot"] for i, p in enumerate(placed) if (i, lag) in heard)
        off = sum(heard.get((i, lag)) == p.get("off_key") for i, p in enumerate(placed) if (i, lag) in heard)
        for mode, n in (("on", on), ("off", off)):
            per_lag[mode][str(lag)] = n
            if mode not in best or n > best[mode][0]:
                best[mode] = (n, lag)
    mode = "off" if best["off"][0] >= best["on"][0] + 2 else "on"
    lag = best[mode][1]
    for i, p in enumerate(placed):
        if (i, lag) in heard:
            p["heard"] = heard[i, lag]
    return {"verdict": mode, "matches_with_keylock_on": best["on"][0], "matches_with_keylock_off": best["off"][0],
            "blend_lag_seconds": lag, "matches_per_lag": per_lag[mode]}  # how clearly that lag won


def check(result: dict, pool, acts, scores: dict | None, duration: float,
          plays: list[dict], history: Path, start: datetime, moods: dict | None = None) -> None:
    """Add a per-track report to an analysed mix (result), and judge its genres at the tracks' own BPMs.
    moods: analysis.mood_patches of the mix, for each track's mood as heard."""
    placed = place(plays, start, duration)
    if not placed:
        raise SystemExit(f"None of the tracks in {history} play during this recording "
                         f"(taken to start at {start:%Y-%m-%d %H:%M:%S}; set it with --start)")
    library = _library([p["path"] for p in placed if p.get("path")])
    # the tempo each was played at: any stretch of it will do
    timed = [p for p in placed if p.get("bpm") and window(p, 0)[1] - window(p, 0)[0] >= MIN_MAIN]
    tempo_meter = lambda n: f"  tempo played: {n}/{len(timed)} tracks"  # noqa: E731
    for p, m in zip(timed, pool.measure([(*window(p, 0), True, False) for p in timed], tempo_meter)):
        if "bpm" in m:
            p["played_bpm"] = round_bpm(fold_bpm(m["bpm"]))
            p["speed"] = _speed(p["played_bpm"], p["bpm"])
    for p in placed:
        p["camelot"] = camelot(*p["key"]) if p.get("key") else None
        p["off_key"] = _off_key(p)
        if p.get("path") in library:
            p["own"] = library[p["path"]]
            if p["own"]["genre"]:
                p["library_genre"] = p["own"]["genre"]
    _sound_references(placed)
    keylock = _fit_keys(placed, pool)
    lag = keylock["blend_lag_seconds"]
    have_model = acts is not None and len(acts) > 0
    tracks = []
    for n, p in enumerate(placed, 1):
        p["main"] = window(p, lag)
        a, b = p["main"]
        t = {"n": n, "start": fmt_time(p["rec_start"]), "deck": p["deck"],
             "artist": p.get("artist"), "title": p.get("title"), "judged_on": f"{fmt_time(a)}-{fmt_time(b)}"}
        own = round_bpm(p.get("bpm"))
        if own:
            t["bpm"] = own
        if p.get("played_bpm"):
            t["played_bpm"] = p["played_bpm"]
        if p.get("speed"):
            t["speed"] = f"{(p['speed'] - 1) * 100:+.0f}%"
        if p.get("key"):
            t["key"] = f"{p['key'][0]} {p['key'][1]}"
            t["camelot"] = p["camelot"]
        if p.get("heard"):
            t["heard_camelot"] = p["heard"]
            expected = p["camelot"] if keylock["verdict"] == "on" else p.get("off_key")
            if expected != p["camelot"]:
                t["expected_camelot"] = expected
            t["key_check"] = key_relation(expected, p["heard"]) or "pitch between two keys"
            if keylock["verdict"] == "on" and t["key_check"] != "match" and p.get("off_key") == p["heard"]:
                t["key_check"] += " (fits key lock off for this track)"
        if p.get("library_genre"):
            t["library_genre"] = p["library_genre"]
        if (p.get("own") or {}).get("camelot"):
            t["library_camelot"] = p["own"]["camelot"]  # our CNN key of the track alone, to tell a wrong Traktor key
        # what you played against what the track sounds like alone: at its own tempo, and sped up in a tempo view
        if moods and b - a >= MIN_MAIN:
            t["heard_mood"] = _patch_mean(moods, duration, a, b)
        if (p.get("own") or {}).get("mood"):
            t["own_mood"] = p["own"]["mood"]
        if p.get("view"):
            t["view_bpm"] = p["view_bpm"]
            if p["view"]["mood"]:
                t["view_mood"] = p["view"]["mood"]
            if p["view"]["genre"]:
                t["view_genre"] = p["view"]["genre"]
        if p.get("path"):
            t["path"] = p["path"]  # the page looks up its hype and colour in the library by it
        if have_model and b - a >= MIN_MAIN:
            heard = _acts(acts, duration, a, b)
            bpm = p.get("played_bpm") or own
            g = dj_genre(label_scores(heard), bpm, top=1)
            if g:
                t["genre_uncorrected"] = g[0]["genre"]
            fixed, basis = corrected(heard, p)
            g = dj_genre(label_scores(fixed), bpm, top=1)
            if g:
                t["genre"] = g[0]["genre"]
                t["genre_basis"] = basis
            if p.get("view_acts") is not None:
                t["mixing"] = mixing_effect(heard - p["view_acts"])
        tracks.append(t)

    # each change of track as heard (with key lock off, the pitch moved with the tempo; heard has that)
    for prev, t in zip(tracks, tracks[1:]):
        step = camelot_step(prev.get("heard_camelot"), t.get("heard_camelot"))
        if step:
            t["transition"] = step
    steps = [t["transition"] for t in tracks if t.get("transition")]

    checks = [t.get("key_check", "") for t in tracks]
    judged = sum(bool(c) for c in checks)
    match = sum(c.startswith("match") for c in checks)
    close = sum(c.startswith(("relative", "neighbour")) for c in checks)
    keylock["basis"] = ("assumed (most DJs play with it on) and the keys agree" if keylock["verdict"] == "on"
                        else "the keys fit key lock off clearly better")
    keylock["summary"] = (f"{match} of {judged} tracks match"
                          + (f", {close} more one step away on the Camelot wheel" if close else "")
                          + (f"; {len(tracks) - judged} too short to judge" if len(tracks) > judged else ""))
    result["tracklist"] = {"history": str(history), "recording_start": f"{start:%Y-%m-%d %H:%M:%S}",
                           "keylock": keylock, "overlap_share": round(_overlap_share(placed, duration), 2),
                           "key_model": "cnn", "tracks": tracks,
                           "transitions": {k: steps.count(k) for k in ("same key", "one step", "two steps", "clash")
                                           if k in steps}}
    _rejudge_genres(result, placed, acts, scores, duration, lag)


def _overlap_share(placed: list[dict], duration: float) -> float:
    """Share of the recording with two or more tracks playing."""
    grid = np.zeros(int(duration) + 1, dtype=np.int8)
    for p in placed:
        grid[int(p["rec_start"]): int(p["rec_end"])] += 1
    return float((grid >= 2).mean())


def _acts(acts, duration: float, a: float, b: float) -> np.ndarray:
    i0 = int(a / duration * len(acts))
    return acts[i0: max(i0 + 1, int(b / duration * len(acts)))].mean(axis=0)


@contextmanager
def _in_view(bpm: float):
    """Read the tempo view's cache for a while, then put the normal library back."""
    from . import collection, tempo

    saved = (analysis.CACHE_DB, analysis.CACHE_VERSION, collection.DB, collection.DEFAULT_CSV)
    try:
        tempo.view_db(bpm).close()
        yield
    finally:
        analysis.CACHE_DB, analysis.CACHE_VERSION, collection.DB, collection.DEFAULT_CSV = saved


def _sound_references(placed: list[dict]) -> None:
    """Each track's style scores as analysed on its own: at its own tempo (the normal cache) and,
    where a tempo view near the mix's tempo was scanned, sped up alone (`tempo scan`)."""
    from . import tempo

    for p in placed:
        if p.get("path") and os.path.exists(p["path"]):
            hit = analysis.cache_get(Path(p["path"]))
            if hit:
                p["native_acts"] = hit[2]
    played = [p["played_bpm"] for p in placed if p.get("played_bpm")]
    if not played:
        return
    bpm = float(np.median(played))
    views = [float(m[1]) for d in analysis.HOME.glob("at-*") if (m := re.fullmatch(r"at-([\d.]+)", d.name))
             and (d / "cache.db").exists()]
    near = [v for v in views if abs(v - bpm) <= 3]
    if not near:
        return
    view = min(near, key=lambda v: abs(v - bpm))
    rows = _library([p["path"] for p in placed if p.get("path")], analysis.HOME / f"at-{view:g}" / "collection.db")
    for p in placed:
        if p.get("path") in rows and abs((p.get("played_bpm") or bpm) - view) <= 3:
            p["view"], p["view_bpm"] = rows[p["path"]], view
    with _in_view(view):
        for p in placed:
            if p.get("native_acts") is not None and abs((p.get("played_bpm") or bpm) - view) <= 3:
                hit = analysis.cache_get(Path(p["path"]))
                if hit:
                    p["view_acts"] = hit[2]
                    p["view_bpm"] = view


def corrected(heard: np.ndarray, p: dict) -> tuple[np.ndarray, str]:
    """The mix as heard, with only the models' tempo shortcut held back: sped up, they hear psytrance
    in almost any four-on-the-floor. As in the tempo view (tempo.PSY_GATE), psy counts only up to 1.5x
    what the track had at its own tempo. Everything else the speed-up and the mixing (stems, layers,
    effects) changed is kept: a house track pushed to 145 really does move toward trance."""
    if p.get("native_acts") is None:
        return heard, "as heard; scan the track to hold back the models' psy-at-speed habit"
    from .tempo import PSY_GATE, PSY_STYLES

    names = analysis.style_classes()
    out = heard.copy()
    cap = PSY_GATE * max(p["native_acts"][names.index(k)] for k in PSY_STYLES)
    for k in PSY_STYLES:
        out[names.index(k)] = min(out[names.index(k)], cap)
    return out, "as heard, psy held to 1.5x the track's own-tempo level"


def mixing_effect(diff: np.ndarray, n: int = 3, min_change: float = 0.05) -> dict:
    """Styles the mixing (stems, layers, effects) pushed up or down, against the track alone at that tempo."""
    names = [analysis.split_style(c)[0] for c in analysis.style_classes()]
    order = np.argsort(diff)
    up = [f"{names[i]} +{diff[i]:.2f}" for i in order[::-1][:n] if diff[i] >= min_change]
    down = [f"{names[i]} {diff[i]:.2f}" for i in order[:n] if diff[i] <= -min_change]
    return {k: v for k, v in (("up", up), ("down", down)) if v}


def _rejudge_genres(result: dict, placed: list[dict], acts, scores: dict | None, duration: float,
                    lag: float) -> None:
    """Tracks played well away from their own tempo: name the mix (and each timeline segment) by how
    it sounds as played, with the models' psy-at-speed habit held back (see corrected()). The
    uncorrected label is kept next to it."""
    speeds = [p["speed"] for p in placed if p.get("speed")]
    if acts is None or not len(acts) or not speeds or abs(float(np.median(speeds)) - 1) <= SPEED_GENRE:
        return
    span = lambda p: slot(p, lag)[1] - slot(p, lag)[0]  # noqa: E731  each track's share of the mix
    fixed, diffs, weights, bpms, scanned = [], [], [], [], 0.0
    for p in placed:
        a, b = slot(p, lag)
        if b - a < MIN_MAIN:
            continue
        heard = _acts(acts, duration, a, b)
        fixed.append(corrected(heard, p)[0])
        weights.append(span(p))
        scanned += span(p) if p.get("native_acts") is not None else 0.0
        if p.get("played_bpm") or p.get("bpm"):
            bpms.append((p.get("played_bpm") or p["bpm"], span(p)))
        if p.get("view_acts") is not None:
            diffs.append((heard - p["view_acts"], span(p)))
    if not fixed:
        return
    played_bpm = round_bpm(float(np.average([b for b, _ in bpms], weights=[w for _, w in bpms]))) if bpms else None
    result["dj_genre_uncorrected"] = result.get("dj_genre")
    result["dj_genre"] = dj_genre(label_scores(np.average(fixed, axis=0, weights=weights)), played_bpm)
    gated = scanned / sum(weights)
    result["genre_basis"] = {**result.get("genre_basis", {}),
                             "dj_genre": f"the mix as heard at {played_bpm} BPM, psy held to 1.5x each track's "
                                         f"own-tempo level ({gated:.0%} of the mix; the rest has no scan)"}
    if diffs:
        # averaged over the mix, the pushes partly cancel, so smaller changes count
        result["mixing"] = mixing_effect(np.average([d for d, _ in diffs], axis=0, weights=[w for _, w in diffs]),
                                         n=5, min_change=0.03)
    for seg in result.get("timeline", []):
        a, b = _seconds(seg["start"]), _seconds(seg["end"])
        here = [(min(b, slot(p, lag)[1]) - max(a, slot(p, lag)[0]), p) for p in placed]
        here = sorted(((t, p) for t, p in here if t > 0), key=lambda x: -x[0])
        if not here:
            continue
        seg["tracks"] = [p.get("title") or Path(p.get("path", "")).stem for _t, p in here]
        main = here[0][1]
        g = dj_genre(label_scores(corrected(_acts(acts, duration, a, b), main)[0]),
                     seg.get("bpm") or main.get("played_bpm"), top=1)
        if g and g[0]["genre"] != seg.get("dj_genre"):
            seg["dj_genre_uncorrected"] = seg.get("dj_genre")
            seg["dj_genre"] = g[0]["genre"]


def _seconds(text: str) -> float:
    parts = [int(x) for x in text.split(":")]
    return float(sum(v * 60 ** i for i, v in enumerate(reversed(parts))))


def prepare(recording: str, history: str, start_text: str | None) -> tuple[list[dict], Path, datetime]:
    """Everything `analyze --tracklist` needs before decoding: the plays, which history file, and when
    the recording started. Stops with what to do when something can't be found."""
    rec = analysis.resolve_audio(recording)
    named = recording_start(rec)
    if start_text:
        start = parse_start(start_text, named.date() if named else None)
    elif named:
        start = named
    else:
        raise SystemExit(f"Can't tell when {rec.name} started: its name isn't a Traktor recording name "
                         "(Recording_YYYY-MM-DD_HHhMMmSSs). Add --start HH:MM:SS (or 'YYYY-MM-DD HH:MM:SS'): "
                         "the time of day the recording began.")
    if history == "auto":
        found = find_history(start, analysis._audio_length(rec) or 6 * 3600)
        if not found:
            raise SystemExit(f"No Traktor history file covers a recording starting {start:%Y-%m-%d %H:%M:%S} "
                             f"(looked in {HISTORY_GLOB}). Traktor saves one per session when it closes: quit "
                             "Traktor once after the set, or pass the file: --tracklist /path/to/history_….nml")
        path = found
    else:
        path = Path(os.path.expanduser(history))
        if not path.exists():
            raise SystemExit(f"{path} does not exist")
    try:
        plays = read_history(path)
    except ET.ParseError as err:
        raise SystemExit(f"{path} isn't a readable Traktor history file ({err})")
    if not plays:
        raise SystemExit(f"{path} has no played tracks with start times. Export the session's history from "
                         "Traktor (Explorer > History > right-click > Export) and pass that file.")
    missing = [p for p in plays if not p.get("key") or not p.get("bpm")]
    if missing:
        analysis.log(f"  {len(missing)} track(s) have no key or BPM in the history file; analyse them in Traktor "
                     "(right-click > Analyze) and save the history again to check those too")
    analysis.log(f"Tracklist: {path} ({len(plays)} plays, recording taken to start {start:%H:%M:%S})")
    return plays, path, start


# ----------------------------------------------------------------------------
# Printed summary of a mix
# ----------------------------------------------------------------------------


def _genres(g: list[dict] | None) -> str:
    return " / ".join(x["genre"] for x in g or []) or "?"


def _cut(text: str | None, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1] + "…"


def _runs(timeline: list[dict], field: str) -> list[str]:
    """Neighbouring segments with the same value merged: '0:00-8:00 Trance'."""
    out = []
    for seg in timeline:
        v = seg.get(field)
        if out and out[-1][2] == v:
            out[-1][1] = seg["end"]
        else:
            out.append([seg["start"], seg["end"], v])
    return [f"{a}-{b} {v}" for a, b, v in out if v]


def summary(result: dict) -> str:
    """A readable report of an analysed DJ mix (with or without --tracklist), for the terminal."""
    lines = [f"DJ mix: {Path(result['file']).name} ({result['duration']})", ""]
    lines.append(f"  Genre      {_genres(result.get('dj_genre'))}")
    if result.get("dj_genre_uncorrected"):
        lines.append(f"             without the psy limit: {_genres(result['dj_genre_uncorrected'])}")
    styles = ", ".join(f"{s['style']} {s['score']:.2f}" for s in result.get("discogs_styles", [])[:4])
    if styles:
        lines.append(f"  Styles     {styles}  (as heard, no limit)")
    tl = result.get("timeline", [])
    bpms = [s["bpm"] for s in tl if s.get("bpm")]
    if bpms:
        lo, hi = min(bpms), max(bpms)
        lines.append(f"  Tempo      {int(np.median(bpms))} BPM" + (f" (segments {lo}-{hi})" if hi - lo > 1 else ""))
    loud = result.get("loudness", {})
    if loud:
        lines.append(f"  Loudness   {loud['integrated_lufs']} LUFS, range {loud['loudness_range_lu']} LU")
    moods = sorted(result.get("mood", {}).items(), key=lambda x: -x[1])
    if moods:
        lines.append("  Mood       " + ", ".join(f"{m} {v:.2f}" for m, v in moods[:3])
                     + (f"; vocals in {result['vocal_share']:.0%}" if "vocal_share" in result else ""))
    mixing = result.get("mixing")
    if mixing:
        lines.append("  Mixing     " + "; ".join(f"{k}: {', '.join(v)}" for k, v in mixing.items())
                     + "  (vs the tracks alone at that tempo)")

    report = result.get("tracklist")
    if report:
        k = report["keylock"]
        lines += ["", f"  Tracklist  {len(report['tracks'])} tracks from {Path(report['history']).name}",
                  f"             recording started {report['recording_start']}; two decks playing "
                  f"{report['overlap_share']:.0%} of the time",
                  f"  Key lock   {k['verdict']} ({k['matches_with_keylock_on']} keys fit on, "
                  f"{k['matches_with_keylock_off']} off); blends take ~{k['blend_lag_seconds']} s"
                  + (f" (keys matching per lag: {', '.join(f'{lag} s {n}' for lag, n in k['matches_per_lag'].items())})"
                     if k.get("matches_per_lag") else ""),
                  f"  Keys       {k['summary']}"]
        if report.get("transitions"):
            lines.append("  Changes    " + ", ".join(f"{n} {k}" for k, n in report["transitions"].items())
                         + "  (key to key as heard, on the Camelot wheel)")
        lines.append("")
        lines.append(f"  {'#':>2}  {'start':>7}  {'track':<34} {'bpm':>9}  {'key':>3} {'heard':>5}  "
                     f"{'library genre':<22} genre in the mix")
        mark = {"match": "✓", "neighbour": "~", "relative major/minor": "~"}
        for t in report["tracks"]:
            name = " - ".join(x for x in (t.get("artist"), t.get("title")) if x)
            bpm = f"{t.get('bpm', '?')}>{t['played_bpm']}" if t.get("played_bpm") else str(t.get("bpm", "?"))
            check = t.get("key_check", "")
            sign = next((v for c, v in mark.items() if check.startswith(c)), "✗" if check else " ")
            lines.append(f"  {t['n']:>2}  {t['start']:>7}  {_cut(name, 34):<34} {bpm:>9}  {t.get('camelot', ''):>3} "
                         f"{t.get('heard_camelot', ''):>5}{sign} {_cut(t.get('library_genre'), 22):<22} "
                         f"{t.get('genre', '')}")
        lines.append("  key: ✓ match, ~ one step on the Camelot wheel, ✗ different (may be the other deck)")

    if result.get("chapters"):
        lines += ["", "  YouTube chapters (paste into the description; Intro = the video before the music)"]
        lines += [f"    {c}" for c in result["chapters"]]
    if result.get("highlights"):
        where = "video" if any("video" in h for h in result["highlights"]) else "recording (add --offset for video)"
        lines += ["", f"  Clip suggestions ({where} times; nudge to the nearest bar in your editor)"]
        for i, h in enumerate(result["highlights"], 1):
            span = h.get("video") or f"{h['start']}-{h['end']}"
            rise = f", rises {h['rise_lu']:+.1f} LU" if h["rise_lu"] > 1 else ""
            best = f"  #{h['rank']} best" if len(result["highlights"]) > 1 and h.get("rank") else ""
            energy = f", energy {h['energy']:.2f}" if h.get("energy") is not None else ""
            lines.append(f"    Clip {i}: {span}{best}  {h['lufs']} LUFS{rise}{energy}"
                         + (f"  ({h['track']})" if h.get("track") else ""))
    if tl:
        lines += ["", "  Timeline (genre)"]
        lines += [f"    {r}" for r in _runs(tl, "dj_genre")]
        if not report:
            lines += ["", "  Timeline (key)"]
            lines += [f"    {r}" for r in _runs(tl, "camelot")]
            lines += ["", "  Add --tracklist to check this against Traktor's history of the session."]
    return "\n".join(lines)



# ----------------------------------------------------------------------------
# YouTube chapters and clip suggestions
# ----------------------------------------------------------------------------

SILENCE = 10 ** (-60 / 20)  # -60 dBFS, a usual threshold for trimming silence off a recording


def first_sound(mono: np.ndarray, sr: int = 44100) -> float:
    """Seconds of silence before the first sound (a trimmed copy of the recording starts here)."""
    loud = np.flatnonzero(np.abs(mono) > SILENCE)
    return float(loud[0]) / sr if len(loud) else 0.0


def parse_offset(text: str | None) -> float:
    """--offset: '14', '14.5' or '0:14' -> seconds."""
    if not text:
        return 0.0
    try:
        parts = [float(x) for x in str(text).split(":")]
    except ValueError:
        raise SystemExit(f"--offset takes seconds (14) or m:ss (0:14), not {text!r}")
    return sum(v * 60 ** i for i, v in enumerate(reversed(parts)))


def anchor(result: dict) -> float:
    """The moment in the recording that --offset (read off the finished video) refers to: the first
    track's start, or the first sound without a tracklist."""
    tracks = (result.get("tracklist") or {}).get("tracks")
    return _seconds(tracks[0]["start"]) if tracks else result.get("first_sound_seconds", 0.0)


def to_video(rec_seconds: float, start: float, offset: float) -> float:
    """A time in the recording -> the same moment in the video, where the music starts `offset` s in.
    Trimming the silence off the recording (as transcoding scripts often do) doesn't matter: both are counted from
    the same musical moment."""
    return max(0.0, rec_seconds - start + offset)


def chapters(result: dict, offset: float) -> list[str]:
    """YouTube chapter lines from the tracklist, for a video where the first track starts `offset`
    seconds in (the user reads that off the finished video). YouTube wants the first at 0:00, at least
    three, and each at least 10 s long, so very short plays are folded into the next chapter, and the
    video's own opening before the music becomes an "Intro" chapter."""
    report = result.get("tracklist")
    if not report:
        return []
    lead = anchor(result)
    marks = []
    for t in report["tracks"]:
        name = " - ".join(x for x in (t.get("artist"), t.get("title")) if x) or "?"
        at = to_video(_seconds(t["start"]), lead, offset)
        if marks and at - marks[-1][0] < 10:
            marks[-1][1] += f" / {name}"
            continue
        marks.append([at, name])
    if not marks:
        return []
    if marks[0][0] >= 10:
        marks.insert(0, [0.0, "Intro"])
    else:
        marks[0][0] = 0.0
    return [f"{fmt_time(at)} {name}" for at, name in marks] if len(marks) >= 3 else []


def mood_energy(moods: dict | None, duration: float) -> np.ndarray | None:
    """Per second of the recording, how party / aggressive it sounds (mean of the two mood heads, 0-1)."""
    if not moods or not {"party", "aggressive"} <= set(moods):
        return None
    e = (moods["party"] + moods["aggressive"]) / 2
    return np.interp(np.arange(int(duration)) + 0.5, (np.arange(len(e)) + 0.5) / len(e) * duration, e)


def highlights(short: np.ndarray, duration: float, n: int = 2, length: float = 40.0,
               hop: float = 0.1, energy: np.ndarray | None = None, energy_weight: float = 0.0) -> list[dict]:
    """The n best stretches of `length` seconds for a social clip: loud, and starting where the music
    rises out of something quieter (a drop after a build or breakdown). From EBU short-term loudness.
    energy (mood_energy): each clip says how party / aggressive it sounds; energy_weight (LU for a clip that
    sounds fully so, default 0 = loudness only) adds it to the score."""
    loud = np.where(np.isfinite(short), short, -70.0).astype(float)
    per_s = int(round(1 / hop))
    sec = loud[: len(loud) // per_s * per_s].reshape(-1, per_s).mean(axis=1)  # one value per second
    win, before, hit = int(length), 20, 8
    if len(sec) < win + before:
        return []
    cands = []
    for s in range(before, len(sec) - win):
        if sec[s - before: s + win].min() < -45:
            continue  # next to silence: the start or end of the set, not a moment in it
        level = sec[s: s + win].mean()
        rise = sec[s: s + hit].mean() - sec[s - before: s].mean()
        e = float(energy[s: s + win].mean()) if energy is not None and len(energy[s: s + win]) else None
        bonus = energy_weight * e if e is not None and energy_weight else 0.0
        cands.append((level + 0.5 * min(max(rise, 0.0), 8.0) + bonus, s, level, rise, e))
    cands.sort(reverse=True)
    picked = []
    for score, s, level, rise, e in cands:
        if all(abs(s - p["_s"]) >= 2 * win for p in picked):
            picked.append({"_s": s, "start": fmt_time(s), "end": fmt_time(min(duration, s + win)),
                           "lufs": round(float(level), 1), "rise_lu": round(float(rise), 1),
                           **({"energy": round(e, 2)} if e is not None else {}),
                           "rank": len(picked) + 1})  # 1 = best score; the list itself is in time order
        if len(picked) == n:
            break
    return [{k: v for k, v in p.items() if k != "_s"} for p in sorted(picked, key=lambda p: p["_s"])]


def describe_highlights(result: dict, offset: float | None) -> None:
    """Add video times and the tracks playing to result["highlights"] (in place)."""
    lead = anchor(result)
    tracks = (result.get("tracklist") or {}).get("tracks", [])
    for h in result.get("highlights", []):
        s = _seconds(h["start"])
        if offset is not None:
            e = _seconds(h["end"])
            h["video"] = f"{fmt_time(to_video(s, lead, offset))}-{fmt_time(to_video(e, lead, offset))}"
        playing = [t for t in tracks if _seconds(t["start"]) <= s]
        if playing:
            t = playing[-1]
            h["track"] = " - ".join(x for x in (t.get("artist"), t.get("title")) if x)
