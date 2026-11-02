"""
Across your recorded mixes (`mixes` command, the Mixes tab's All N mixes button): what the saved analyses of
`analyze <mix> --tracklist` add up to. Read-only.

- keys: tracks whose key heard in your mixes is our key (the CNN's, of the track alone) but not Traktor's: Traktor's
  is probably wrong. Mixes played with key lock on; heard by either model (mixes analysed before 2026-11-01 heard
  with Essentia), since agreeing with our key is the test.
- view: each track's mood (and genre) as heard in the mix against the track alone, at its own tempo and in the tempo
  view near the tempo you played: does the view predict what you played?
- shape: your sets stretched to the same length, in 5% slices: hype and Traktor colour of what you played (from the
  library, as the Mixes tab's timeline draws a mix) and loudness, per mix and the mean (each mix counts once).
- arc: the mood of your sets by fifth, measured in the recordings (each mix counts once).
- transitions: each change of track, key to key as heard, on the Camelot wheel.

A mix's heard values include the blends, the other deck and your effects, so single tracks are rough; the totals
over several mixes are the point.
"""

from __future__ import annotations

import sqlite3
import warnings
from collections import Counter
from pathlib import Path

import numpy as np

from .analysis import MOODS

PARTS = 5  # the arc: fifths of a set
SLICES = 20  # the shape: 5% of a set each


def load(dirs: list[Path]) -> list[dict]:
    """The saved mix analyses, the newest one per recording, oldest recording first."""
    from .web import _json_in

    best: dict[str, tuple[float, dict]] = {}
    for d in dirs:
        for f in sorted(Path(d).glob("*")):
            if f.suffix.lower() not in (".json", ".txt") or not f.is_file():
                continue
            data = _json_in(f)
            if not isinstance(data, dict) or data.get("type") != "dj_mix":
                continue
            rec = Path(data.get("file") or f.name).name
            mtime = f.stat().st_mtime
            if rec not in best or mtime > best[rec][0]:
                data["_saved"] = f.name
                best[rec] = (mtime, data)
    return sorted((d for _, d in best.values()), key=lambda d: Path(d.get("file") or "").name)


def _secs(t: str) -> float:
    s = 0.0
    for part in str(t).split(":"):
        s = s * 60 + float(part)
    return s


def _library_keys(paths: set[str]) -> dict[str, tuple]:
    """{path: (Traktor's Camelot, our CNN Camelot, artist, title)} from the normal library."""
    from .collection import NATIVE_DB, nfc

    if not paths or not NATIVE_DB.exists():
        return {}
    out = {}
    con = sqlite3.connect(f"file:{NATIVE_DB}?mode=ro", uri=True)
    try:
        for p in paths:
            row = con.execute("SELECT traktor_camelot, cnn_camelot, artist, title FROM tracks WHERE path=?",
                              (str(nfc(p)),)).fetchone()
            if row:
                out[p] = row
    finally:
        con.close()
    return out


def keys(mixes: list[dict]) -> dict:
    heard: dict[str, list[str]] = {}
    used = 0
    for d in mixes:
        tl = d.get("tracklist") or {}
        if (tl.get("keylock") or {}).get("verdict") != "on":
            continue
        used += 1
        for t in tl.get("tracks", []):
            if t.get("path") and t.get("heard_camelot"):
                heard.setdefault(t["path"], []).append(t["heard_camelot"])
    lib = _library_keys(set(heard))
    flags = []
    for path, hs in heard.items():
        if path not in lib:
            continue
        traktor, ours, artist, title = lib[path]
        top, n = Counter(hs).most_common(1)[0]
        if ours and top == ours and traktor and traktor != ours and n * 2 > len(hs):
            flags.append({"path": path, "artist": artist, "title": title, "traktor": traktor, "ours": ours,
                          "heard": hs, "agree": n})
    flags.sort(key=lambda f: (-f["agree"], f["title"] or ""))
    return {"mixes": used, "tracks": len(heard), "flags": flags}


def view(mixes: list[dict]) -> dict:
    """Mood as heard vs the track alone (own tempo; tempo view), and the genre as heard vs each."""
    rows = [t for d in mixes for t in (d.get("tracklist") or {}).get("tracks", []) if t.get("heard_mood")]
    moods = {}
    for m in MOODS:
        both = [t for t in rows if m in t["heard_mood"] and m in t.get("own_mood", {}) and m in t.get("view_mood", {})]
        if not both:
            continue
        h = np.array([t["heard_mood"][m] for t in both])
        o = np.array([t["own_mood"][m] for t in both])
        v = np.array([t["view_mood"][m] for t in both])
        moods[m] = {"tracks": len(both), "heard": round(float(h.mean()), 3), "own": round(float(o.mean()), 3),
                    "view": round(float(v.mean()), 3), "own_error": round(float(np.abs(h - o).mean()), 3),
                    "view_error": round(float(np.abs(h - v).mean()), 3)}
    g = [t for d in mixes for t in (d.get("tracklist") or {}).get("tracks", [])
         if t.get("genre") and t.get("view_genre") and t.get("library_genre")]
    genre = {"tracks": len(g), "same_as_view": sum(t["genre"] == t["view_genre"] for t in g),
             "same_as_own": sum(t["genre"] == t["library_genre"] for t in g)}
    return {"tracks": len(rows), "moods": moods, "genre": genre}


def shape(mixes: list[dict]) -> dict:
    """Each set in SLICES equal slices of its length: per slice the hype (weighted by time, over the tracks playing:
    a track from its start + the blend lag to the next one's), the colour played longest and the loudness of the
    timeline segments in it; per mix and the mean over the mixes that have it."""
    from .web import played_tracks

    per_mix = []
    for d in mixes:
        segs = d.get("timeline") or []
        if not segs:
            continue
        end = max(_secs(s["end"]) for s in segs)
        tracks = (d.get("tracklist") or {}).get("tracks") or []
        lib = played_tracks(tracks) if tracks else {}
        lag = ((d.get("tracklist") or {}).get("keylock") or {}).get("blend_lag_seconds") or 0
        spans = [(_secs(t["start"]) + lag, _secs(tracks[i + 1]["start"]) + lag if i + 1 < len(tracks) else np.inf,
                  lib.get(t["n"])) for i, t in enumerate(tracks)]
        hype, colour, lufs = [], [], []
        for i in range(SLICES):
            a, b = end * i / SLICES, end * (i + 1) / SLICES
            here = [(min(b, y) - max(a, x), L) for x, y, L in spans if L and min(b, y) > max(a, x)]
            rated = [(o, L["hype"]) for o, L in here if L["hype"] is not None]
            w = sum(o for o, _ in rated)
            hype.append(round(sum(o * h for o, h in rated) / w, 2) if w else None)
            by = Counter()
            for o, L in here:
                if L["colour"]:
                    by[L["colour"]] += o
            colour.append(by.most_common(1)[0][0] if by else None)
            ov = [(min(b, _secs(s["end"])) - max(a, _secs(s["start"])), s["lufs"]) for s in segs
                  if s.get("lufs") is not None and min(b, _secs(s["end"])) > max(a, _secs(s["start"]))]
            w = sum(o for o, _ in ov)
            lufs.append(round(sum(o * v for o, v in ov) / w, 2) if w else None)
        per_mix.append({"file": Path(d.get("file") or "").name, "duration": d.get("duration"),
                        "hype": hype if any(h is not None for h in hype) else None, "colour": colour, "lufs": lufs})

    def mean(key):
        out = []
        for i in range(SLICES):
            xs = [m[key][i] for m in per_mix if m[key] and m[key][i] is not None]
            out.append({"value": round(float(np.mean(xs)), 2) if xs else None, "mixes": len(xs)})
        return out

    colours = []
    for i in range(SLICES):
        c = Counter(m["colour"][i] for m in per_mix if m["colour"][i])
        colours.append(c.most_common(1)[0][0] if c else None)
    return {"slices": SLICES, "mixes": per_mix, "hype": mean("hype"), "lufs": mean("lufs"), "colour": colours}


def arc(mixes: list[dict]) -> dict:
    """Per fifth of the set: the mean mood and loudness of its timeline segments; each mix counts once."""
    per = [[] for _ in range(PARTS)]
    used = 0
    for d in mixes:
        tl = [s for s in d.get("timeline", []) if s.get("mood")]
        if not tl:
            continue
        used += 1
        end = max(_secs(s["end"]) for s in tl)
        parts = [[] for _ in range(PARTS)]
        for s in tl:
            mid = (_secs(s["start"]) + _secs(s["end"])) / 2
            parts[min(PARTS - 1, int(mid / end * PARTS))].append(s)
        for i, segs in enumerate(parts):
            if segs:
                per[i].append({**{m: np.mean([s["mood"][m] for s in segs if m in s["mood"]] or [np.nan]) for m in MOODS},
                               "lufs": np.mean([s["lufs"] for s in segs if s.get("lufs") is not None] or [np.nan])})
    out = []
    for i, xs in enumerate(per):
        if not xs:
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # nanmean of a mood no mix measured
            mean = {k: np.nanmean([x[k] for x in xs]) for k in [*MOODS, "lufs"]}
        r = {k: None if np.isnan(v) else round(float(v), 3) for k, v in mean.items()}
        out.append({"part": i + 1, "mixes": len(xs), "mood": {m: r[m] for m in MOODS if r[m] is not None},
                    "lufs": r["lufs"]})
    return {"mixes": used, "parts": out}


def report(dirs: list[Path]) -> dict:
    mixes = load(dirs)
    steps = Counter()
    per_mix = []
    for d in mixes:
        tl = d.get("tracklist") or {}
        if tl.get("key_model") == "cnn":
            steps.update(tl.get("transitions") or {})
        kl = tl.get("keylock") or {}
        per_mix.append({"saved": d["_saved"], "file": Path(d.get("file") or "").name, "duration": d.get("duration"),
                        "tracks": len(tl.get("tracks", [])), "key_model": tl.get("key_model"),
                        "keylock": kl.get("verdict"), "blend_lag_seconds": kl.get("blend_lag_seconds"),
                        "matches_per_lag": kl.get("matches_per_lag"), "transitions": tl.get("transitions"),
                        "mood_timeline": any(s.get("mood") for s in d.get("timeline", []))})
    return {"mixes": per_mix, "keys": keys(mixes), "view": view(mixes), "shape": shape(mixes), "arc": arc(mixes),
            "transitions": {k: steps[k] for k in ("same key", "one step", "two steps", "clash") if steps[k]}}


def text(r: dict) -> str:
    """The report for the terminal."""
    old = [m for m in r["mixes"] if not m["mood_timeline"]]
    lines = [f"{len(r['mixes'])} recorded mixes analysed" + (
        f"; {len(old)} before the CNN key / per-segment mood (analyse them again to count them in)" if old else "")]
    for m in r["mixes"]:
        lag = m["matches_per_lag"]
        lines.append(f"  {m['file']}  {m['duration'] or ''}  {m['tracks']} tracks"
                     + (f", key lock {m['keylock']}, blends ~{m['blend_lag_seconds']} s" if m["keylock"] else "")
                     + (f" (keys per lag: {', '.join(f'{k} s {n}' for k, n in lag.items())})" if lag else ""))
    k = r["keys"]
    lines += ["", f"Keys Traktor may have wrong ({k['tracks']} tracks heard in {k['mixes']} mixes; "
                  "heard = our key, not Traktor's)"]
    lines += [f"  {f['traktor']:>3} in Traktor, {f['ours']:>3} ours and heard {f['agree']}/{len(f['heard'])}x  "
              f"{' - '.join(x for x in (f['artist'], f['title']) if x)}" for f in k["flags"]] or ["  none"]
    v = r["view"]
    lines += ["", f"Mood as played vs the track alone ({v['tracks']} tracks; mean, and mean distance from as played)"]
    for m, x in v["moods"].items():
        lines.append(f"  {m:<10} played {x['heard']:.2f}  tempo view {x['view']:.2f} (off by {x['view_error']:.2f})  "
                     f"own tempo {x['own']:.2f} (off by {x['own_error']:.2f})  [{x['tracks']} tracks]")
    if not v["moods"]:
        lines.append("  nothing yet: needs mixes analysed with this version and a tempo view near the tempo played")
    g = v["genre"]
    if g["tracks"]:
        lines.append(f"  genre as played = the view's on {g['same_as_view']}/{g['tracks']}, "
                     f"= the track's own on {g['same_as_own']}/{g['tracks']}")
    sh = r["shape"]
    lines += ["", f"Your sets start to end ({len(sh['mixes'])} mixes stretched to the same length; "
                  f"per {100 // sh['slices']}% of the set)"]
    for name, key, f in (("hype", "hype", "{:.1f}"), ("LUFS", "lufs", "{:.0f}")):
        if any(x["value"] is not None for x in sh[key]):
            lines.append(f"  {name:<5} " + " ".join("–" if x["value"] is None else f.format(x["value"]) for x in sh[key]))
    if not sh["mixes"]:
        lines.append("  nothing yet")
    a = r["arc"]
    lines += ["", f"Your sets by fifth ({a['mixes']} mixes; mood measured in the recordings)"]
    for p in a["parts"]:
        lines.append(f"  part {p['part']}  " + "  ".join(f"{m} {v:.2f}" for m, v in p["mood"].items())
                     + (f"  {p['lufs']:.1f} LUFS" if p["lufs"] is not None else ""))
    if not a["parts"]:
        lines.append("  nothing yet: needs mixes analysed with this version")
    t = r["transitions"]
    lines += ["", "Changes of track (key to key as heard)",
              "  " + (", ".join(f"{n} {k}" for k, n in t.items()) if t else "nothing yet")]
    return "\n".join(lines)
