"""Live checks for the track analyzer, run against the real models and data in data.
Read-only: never writes to the caches, databases, Traktor or files.

  uv run python .claude/skills/audit-analysis/scripts/checks.py            all checks
  uv run python .claude/skills/audit-analysis/scripts/checks.py rules bpm  only some

Checks: rules, heads, calibration, bpm, cache, timing.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np

from dj_track_analyzer import analysis, collection

ROOT = Path(__file__).resolve().parents[4]

M = analysis.MODELS_DIR


def _classes(name: str) -> list[str]:
    return json.loads((M / name).read_text())["classes"]


def rules() -> None:
    """Every style in dj_genres.json exists, and which ones only one model knows."""
    eff = _classes("genre_discogs400-discogs-effnet-1.json")
    mae = _classes("discogs-maest-30s-pw-519l-2.json") if analysis.maest_present() else []
    el = {c.split("---")[1] for c in eff if c.startswith("Electronic---")}
    ml = {c.split("---")[1] for c in mae if c.startswith("Electronic---")}
    for g, rule in analysis.dj_rules().items():
        for grp in rule["needs"]:
            for s in grp:
                where = ("EffNet " if s in el else "") + ("MAEST" if s in ml else "")
                if s not in el or s not in ml:
                    print(f"  {g:24} {s:20} only in: {where or 'NEITHER (ignored!)'}")
    print("  (a style only MAEST knows stands in for EffNet at the track's EffNet/MAEST ratio; see calibration)")


def heads() -> None:
    """Input/output node names and positive-class index of every head, from its metadata."""
    for js in sorted(M.glob("*.json")):
        meta = json.loads(js.read_text())
        sch = meta.get("schema", {})
        ins = [i["name"] for i in sch.get("inputs", [])]
        outs = [(o["name"], o.get("output_purpose")) for o in sch.get("outputs", [])]
        cls = meta.get("classes", [])
        print(f"  {js.stem:45} in={ins} out={[o for o, p in outs if p == 'predictions'] or outs[:2]} "
              f"classes={cls[:4]}{'…' if len(cls) > 4 else ''}")
    print("  compare with the hard-coded names in analysis.py (style_models, mood_models, _load_head, maest_model)")


def calibration() -> None:
    """Are EffNet and MAEST scores on the same scale? (label_scores blends them 40/60.)"""
    import sqlite3

    eff = _classes("genre_discogs400-discogs-effnet-1.json")
    mae = _classes("discogs-maest-30s-pw-519l-2.json")
    con = sqlite3.connect(analysis.CACHE_DB)
    rows = con.execute("SELECT e.styles, d.maest_styles FROM emb e JOIN deep d ON d.k=e.k "
                       "WHERE d.maest_styles IS NOT NULL").fetchall()
    E = np.array([np.frombuffer(a, np.float32) for a, _ in rows])
    Mm = np.array([np.frombuffer(b, np.float32) for _, b in rows])
    mi = {c: i for i, c in enumerate(mae)}
    print(f"  {len(rows)} tracks. top score per track: EffNet {E.max(1).mean():.3f}, MAEST {Mm.max(1).mean():.3f}; "
          f"sum: EffNet {E.sum(1).mean():.2f}, MAEST {Mm.sum(1).mean():.2f}")
    for s in ["Trance", "Progressive Trance", "Techno", "House", "Progressive House", "Neo Trance"]:
        c = "Electronic---" + s
        e = f"{E[:, eff.index(c)].mean():.3f}" if c in eff else "  -  "
        print(f"  {s:20} EffNet {e}  MAEST {Mm[:, mi[c]].mean():.3f}")


def bpm(target: float = 145.0) -> None:
    """How much the colour model moves when only the BPM input changes (the tempo view feeds it the
    stretched BPM). Anything far from 0 means BPM itself, not the sound, drives part of the shift."""
    b = analysis.color_model()
    if not b:
        print("  no colour model")
        return
    print(f"  model: {b['name']} ({b['trained_at']})")
    d = []
    for r in collection.db().execute("SELECT * FROM tracks WHERE type='track' AND error IS NULL"):
        p = Path(r["path"])
        hit, deep = (analysis.cache_get(p), analysis.deep_get(p)) if p.exists() else (None, None)
        if not hit or not deep:
            continue
        info = {c: r[c] for c in ("bpm", "lufs", "loudness_range", "key", "key_strength")}
        info.update({f"mood_{m}": v for m, v in deep[1].items()})
        a = analysis.predict_color(hit[1], hit[2], info, deep[0], deep[2], deep[3])
        c = analysis.predict_color(hit[1], hit[2], dict(info, bpm=target), deep[0], deep[2], deep[3])
        if a and c:
            d.append(c["position"] - a["position"])
    d = np.array(d)
    print(f"  {len(d)} tracks, BPM input → {target:g}: shift mean {d.mean():+.3f}, sd {d.std():.3f}, "
          f"range {d.min():+.2f} … {d.max():+.2f}")


def cache() -> None:
    """Scanned tracks missing cached pieces (those fall back to EffNet-only genres in regenre/tempo)."""
    miss = {}
    n = 0
    for r in collection.db().execute("SELECT path FROM tracks WHERE type='track' AND error IS NULL"):
        p = Path(r["path"])
        if not p.exists():
            miss.setdefault("file gone", []).append(p.name)
            continue
        n += 1
        if analysis.cache_get(p) is None:
            miss.setdefault("no EffNet cache", []).append(p.name)
        elif analysis.maest_cached(p) is None:
            miss.setdefault("no MAEST styles", []).append(p.name)
        if "sync-conflict" in p.name:
            miss.setdefault("Syncthing conflict copy scanned as a track", []).append(p.name)
    print(f"  {n} tracks checked")
    for k, v in miss.items():
        print(f"  {k}: {len(v)}  e.g. {v[:3]}")


def timing() -> None:
    """Seconds per analysis stage on one ~5-7 min track (first MAEST run includes loading it)."""
    r = collection.db().execute("SELECT path FROM tracks WHERE type='track' AND error IS NULL "
                                "AND duration_sec BETWEEN 300 AND 420 AND filename NOT LIKE '%.sync-conflict-%' "
                                "ORDER BY path LIMIT 1").fetchone()
    p = Path(r["path"])
    print(f"  {p.name}")

    def t(label, fn):
        t0 = time.time()
        out = fn()
        print(f"  {label:9} {time.time() - t0:5.1f}s")
        return out

    st, m44, m16, sr = t("decode", lambda: analysis.load_audio(p))
    t("rhythm", lambda: analysis.rhythm(m44))
    t("key", lambda: analysis.key_of(m44))
    t("loudness", lambda: analysis.loudness(st, sr))
    o = t("effnet", lambda: analysis.pooled_effnet(m16))
    t("heads", lambda: (analysis.run_moods(o[0], o[5]), analysis.run_instruments(o[0], o[5])))
    t("maest", lambda: analysis.run_maest(m16))
    b = collection.mix_bpm(collection.db().execute("SELECT * FROM tracks WHERE path=?", (str(p),)).fetchone())
    if b:
        t("stretch", lambda: analysis.stretch(st, sr, b, 145.0))  # tempo view, Rubber Band R2


CHECKS = {"rules": rules, "heads": heads, "calibration": calibration, "bpm": bpm, "cache": cache, "timing": timing}

if __name__ == "__main__":
    os.chdir(ROOT)
    for name in sys.argv[1:] or CHECKS:
        print(f"\n== {name}: {CHECKS[name].__doc__.splitlines()[0]}")
        try:
            CHECKS[name]()
        except Exception as err:
            print(f"  failed: {type(err).__name__}: {err}")
