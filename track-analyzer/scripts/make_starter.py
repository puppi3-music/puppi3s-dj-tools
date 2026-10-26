"""Copy your trained colour and cue models into the package as starter models, without anything personal.

The starters are used by anyone who hasn't trained their own (`colors train`, `cues train`), which replaces them.
Kept: the fitted models, their settings and accuracy numbers, and the colour model's cross-validated positions
(numbers only, for its confidence). Dropped: every track path (colour disagreements, the cue model's per-track
scores) and the path to your Traktor collection. Refuses to write a file that still holds a path or a file name.

    uv run python scripts/make_starter.py
"""

import pickle
import re
import sys
from pathlib import Path

import joblib

from dj_track_analyzer import analysis, colors, cues

sys.modules.setdefault("colors", colors)  # see analysis.color_model()

COLOUR_KEEP = ["model", "calibrator", "kind", "feature_set", "name", "instruments", "trained_at", "tracks", "counts",
               "metrics", "baseline", "cv"]
CUE_KEEP = ["cue_model", "loop_model", "threshold", "loop_align", "max_cues", "loop_bars", "trained_at", "metrics",
            "version"]
PERSONAL = re.compile(rb"/Users/|/home/|[A-Za-z]:\\\\|\.(mp3|flac|wav|aiff?|m4a|nml)\b", re.I)


def clean(src: Path, dst: Path, keep: list[str], empty: dict) -> None:
    b = joblib.load(src)
    out = {k: b[k] for k in keep if k in b} | empty | {"starter": True}
    if "cv" in out:
        out["cv"] = {"position": list(out["cv"]["position"]), "truth": list(out["cv"]["truth"])}
    raw = pickle.dumps(out)  # checked before compression, which would hide the text
    found = PERSONAL.search(raw)
    if found:
        raise SystemExit(f"{dst.name}: still holds {raw[max(0, found.start() - 40):found.end() + 40]!r}; not written")
    dst.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(out, dst, compress=3)
    print(f"{dst}  ({dst.stat().st_size // 1024} KB, trained {out['trained_at']})")


clean(analysis.COLOR_MODEL, analysis.STARTER_COLOR_MODEL, COLOUR_KEEP, {"disagreements": []})
clean(cues.MODEL, cues.STARTER_MODEL, CUE_KEEP, {"oof": {}})
