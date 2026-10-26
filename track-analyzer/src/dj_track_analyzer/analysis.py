"""
Core analysis for Puppi3's Track Analyzer: configuration, Essentia models, the analysis cache, analyze_file()
and custom genre training. The command line is in cli.py, the MCP server in mcp_server.py.

Runs entirely on your machine with Essentia:
  * BPM (fast-tempo aware), key + Camelot, loudness, energy curve
  * Discogs style classification (400 styles: Hard Trance, Acid, Techno, ...)
  * YOUR OWN genre labels, learned from example tracks you sort into folders
"""

from __future__ import annotations

import os

# Keep TensorFlow quiet; must be set before essentia-tensorflow loads TF.
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

import hashlib
import json
import math
import sqlite3
import sys
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

# Data (databases, models, backups) lives in data/ at the repo root; TRACK_ANALYZER_HOME overrides it.
HOME = Path(os.environ.get("TRACK_ANALYZER_HOME", Path(__file__).resolve().parents[2] / "data")).expanduser()
MODELS_DIR = HOME / "models"
CACHE_DB = HOME / "cache.db"
GENRE_MODEL = HOME / "my_genres.joblib"

# Tempo window for a DJ library. Detected tempos are folded (x2 / ÷2) into it,
# so a 170 BPM hardcore track detected as 85 comes back as 170.
BPM_MIN = float(os.environ.get("TRACK_ANALYZER_BPM_MIN", "85"))
BPM_MAX = float(os.environ.get("TRACK_ANALYZER_BPM_MAX", "220"))

# Files longer than this are treated as DJ mixes and get a per-segment timeline.
MIX_THRESHOLD_SEC = float(os.environ.get("TRACK_ANALYZER_MIX_THRESHOLD", str(12 * 60)))

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aiff", ".aif", ".m4a", ".ogg", ".opus", ".wma"}

MODEL_BASE = "https://essentia.upf.edu/models"
MODEL_FILES = {
    "discogs-effnet-bs64-1.pb": f"{MODEL_BASE}/feature-extractors/discogs-effnet/discogs-effnet-bs64-1.pb",
    "genre_discogs400-discogs-effnet-1.pb": f"{MODEL_BASE}/classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1.pb",
    "genre_discogs400-discogs-effnet-1.json": f"{MODEL_BASE}/classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1.json",
}

# Second, larger genre model (MAEST, 519 Discogs styles, trained on 4M tracks). Optional but
# recommended: its opinion is blended with EffNet's. TRACK_ANALYZER_MAEST=0 turns it off.
MAEST_FILES = {
    "discogs-maest-30s-pw-519l-2.pb": f"{MODEL_BASE}/feature-extractors/maest/discogs-maest-30s-pw-519l-2.pb",
    "discogs-maest-30s-pw-519l-2.json": f"{MODEL_BASE}/feature-extractors/maest/discogs-maest-30s-pw-519l-2.json",
}
USE_MAEST = os.environ.get("TRACK_ANALYZER_MAEST", "1") not in ("0", "false", "no")
MAEST_BLOCKS = int(os.environ.get("TRACK_ANALYZER_MAEST_BLOCKS", "3"))  # 30 s blocks per track
MAEST_WEIGHT = 0.6  # share of MAEST in the blended style scores

# Small pretrained mood classifiers that reuse the EffNet embeddings (optional downloads).
MOODS = ["aggressive", "happy", "party", "relaxed", "sad"]
MOOD_FILES = {
    f"mood_{m}-discogs-effnet-1.{ext}": f"{MODEL_BASE}/classification-heads/mood_{m}/mood_{m}-discogs-effnet-1.{ext}"
    for m in MOODS for ext in ("pb", "json")
}

# Instrument tagger (40 instruments: voice, pad, piano, strings, synthesizer, ...), also on the
# EffNet embeddings (optional download). Trained on Jamendo music, so treat it as a rough guide.
INSTRUMENT_MODEL = "mtg_jamendo_instrument-discogs-effnet-1"
# Its "voice" class barely reacts to vocals in club tracks, so vocals come from a dedicated
# voice/instrumental head, measured over the whole track (vocals often sit in the breakdowns).
VOICE_MODEL = "voice_instrumental-discogs-effnet-1"
VOICE_FEATURES = ["vocals", "vocal_share"]  # average voice probability; share of moments with vocals
INSTRUMENT_FILES = {
    f"{name}.{ext}": f"{MODEL_BASE}/classification-heads/{head}/{name}.{ext}"
    for head, name in (("mtg_jamendo_instrument", INSTRUMENT_MODEL), ("voice_instrumental", VOICE_MODEL))
    for ext in ("pb", "json")
}

# Two regression heads used by `colors explain`: approachability (how accessible/easy a track is;
# the strongest single match with the user's colours) and engagement. Optional downloads.
EXTRA_HEADS = ["approachability", "engagement"]
EXTRA_HEAD_FILES = {
    f"{h}_regression-discogs-effnet-1.{ext}": f"{MODEL_BASE}/classification-heads/{h}/{h}_regression-discogs-effnet-1.{ext}"
    for h in EXTRA_HEADS for ext in ("pb", "json")
}
# Energy-arc numbers from the short-term loudness, also for `colors explain`.
ARC_FEATURES = ["drop_lift_db", "intro_sec", "breakdown_share", "longest_break_sec", "dynamics_db"]

# Traktor's 7 track colours, as stored in collection.nml (INFO COLOR="1".."7"), in menu order.
COLOR_NAMES = ["Red", "Orange", "Yellow", "Green", "Blue", "Violet", "Magenta"]
COLOR_MODEL = HOME / "my_colors.joblib"

# Genre is judged on the main section: style windows quieter than this many dB below the
# track's loud parts (intros, outros, breakdowns) are left out.
MAIN_SECTION_DB = float(os.environ.get("TRACK_ANALYZER_MAIN_SECTION_DB", "6"))

CACHE_VERSION = "effnet-bs64-1/v2-main"


def log(*args) -> None:
    print(*args, file=sys.stderr, flush=True)


# ----------------------------------------------------------------------------
# Essentia loading (lazy, so `--help` and `setup` stay fast)
# ----------------------------------------------------------------------------

_es = None


def es():
    global _es
    if _es is None:
        import essentia

        essentia.log.infoActive = False
        essentia.log.warningActive = False
        import essentia.standard as standard

        _es = standard
    return _es


_dsp = False


def dsp():
    """The Rust module (dsp/) or None. Its functions give Essentia's results bit for bit, faster; checked
    over the test collection (docs/WINDOWS.md). Without it Essentia does the same work."""
    global _dsp
    if _dsp is False:
        try:
            import track_analyzer_dsp as m
        except ImportError:
            m = None
        _dsp = m
    return _dsp


def models_present() -> bool:
    return all((MODELS_DIR / name).exists() for name in MODEL_FILES)


def _fetch(url: str, dest: Path) -> bool:
    """Download with resume and retries. essentia.upf.edu is a university server that is
    sometimes slow or unreachable over IPv6, so try curl (normal, then IPv4-only) before Python."""
    import shutil
    import subprocess

    tmp = dest.with_suffix(dest.suffix + ".part")
    curl = shutil.which("curl")
    attempts = []
    if curl:
        base = [curl, "-fL", "--retry", "3", "--retry-delay", "3", "--connect-timeout", "20",
                "-C", "-", "-o", str(tmp), url]
        attempts += [base, base[:1] + ["-4"] + base[1:]]
    for cmd in attempts:
        label = "curl -4" if "-4" in cmd else "curl"
        if subprocess.run(cmd).returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            tmp.rename(dest)
            return True
        log(f"    {label} failed, trying another way...")
    for i in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 16):
                    f.write(chunk)
            tmp.rename(dest)
            return True
        except Exception as err:
            log(f"    python download failed ({err}); retry {i + 1}/3")
            time.sleep(3 * (i + 1))
    return False


def download_models(maest: bool = True) -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    missing = []
    files = dict(MODEL_FILES)
    if maest:
        log("(includes the larger MAEST genre model, a few hundred MB; skip it with --no-maest)")
        files.update(MAEST_FILES)
    for name, url in files.items():
        dest = MODELS_DIR / name
        if dest.exists() and dest.stat().st_size > 0:
            log(f"  ok        {name}")
            continue
        log(f"  download  {name}")
        if not _fetch(url, dest):
            missing.append((name, url))
    for name, url in {**MOOD_FILES, **INSTRUMENT_FILES, **EXTRA_HEAD_FILES}.items():  # small and optional: a missing one is just skipped
        dest = MODELS_DIR / name
        if not (dest.exists() and dest.stat().st_size > 0):
            log(f"  download  {name}")
            if not _fetch(url, dest):
                log(f"    (optional model {name} unavailable; it will be skipped)")
    if not missing:
        log(f"Models ready in {MODELS_DIR}")
        return
    log("\nCouldn't reach essentia.upf.edu. Check that this opens in a browser:")
    log("  https://essentia.upf.edu/models/")
    log("If it does, download these files in the browser:")
    for name, url in missing:
        log(f"  {url}")
    log(f"and move them into this folder:\n  {MODELS_DIR}\nthen run `uv run dj-track-analyzer setup` again to check.")
    sys.exit(1)


_models = {}


def style_models():
    """Discogs-EffNet embedding model + 400-style head, loaded once."""
    if not models_present():
        raise RuntimeError(
            "Style models are not downloaded yet. Run:  uv run dj-track-analyzer setup"
        )
    if not _models:
        e = es()
        _models["embed"] = e.TensorflowPredictEffnetDiscogs(
            graphFilename=str(MODELS_DIR / "discogs-effnet-bs64-1.pb"),
            output="PartitionedCall:1",
        )
        _models["styles"] = e.TensorflowPredict2D(
            graphFilename=str(MODELS_DIR / "genre_discogs400-discogs-effnet-1.pb"),
            input="serving_default_model_Placeholder",
            output="PartitionedCall:0",
        )
        meta = json.loads((MODELS_DIR / "genre_discogs400-discogs-effnet-1.json").read_text())
        _models["classes"] = meta["classes"]
    return _models


_maest: dict = {}


def maest_present() -> bool:
    return all((MODELS_DIR / n).exists() for n in MAEST_FILES)


def warn_if_maest_missing() -> None:
    """Genres quietly fall back to EffNet only without MAEST, so say so before a long scan."""
    if USE_MAEST and not maest_present():
        log("MAEST isn't downloaded, so genres will use the EffNet model only (less accurate). "
            "Run `uv run dj-track-analyzer setup` to add it, or set TRACK_ANALYZER_MAEST=0 to silence this.")


def maest_input_name(meta: dict) -> str:
    """The graph's input node. This MAEST file calls it "melspectrogram"; Essentia's default
    ("serving_default_melspectrogram") doesn't exist in it, so it must be passed explicitly."""
    try:
        return meta["schema"]["inputs"][0]["name"]
    except (KeyError, IndexError, TypeError):
        return "melspectrogram"


def maest_available() -> bool:
    """MAEST is turned on, downloaded and hasn't failed in this process (doesn't load it)."""
    return USE_MAEST and maest_present() and not _maest.get("disabled")


def maest_model():
    """MAEST-519, loaded once as a plain TensorflowPredict so a single run gives both the style
    predictions and the layer-7 embeddings. None when it's off, not downloaded, or fails to load.
    Any failure disables it for this process and is reported once in results."""
    if _maest.get("disabled"):
        return None
    if not (USE_MAEST and maest_present()):
        _maest["disabled"] = True
        _maest["why"] = "not installed" if USE_MAEST else "turned off"
        return None
    if "model" not in _maest:
        try:
            meta = json.loads((MODELS_DIR / "discogs-maest-30s-pw-519l-2.json").read_text())
            outs = {o.get("output_purpose"): o["name"] for o in meta["schema"]["outputs"]}
            pred = outs.get("predictions", "PartitionedCall/Identity_13")
            emb = outs.get("embeddings", "PartitionedCall/Identity_7")
            _maest["model"] = es().TensorflowPredict(
                graphFilename=str(MODELS_DIR / "discogs-maest-30s-pw-519l-2.pb"),
                inputs=[maest_input_name(meta)], outputs=[pred, emb])
            _maest.update(classes=meta["classes"], input=maest_input_name(meta), pred=pred, emb=emb)
        except Exception as err:
            _maest.update(disabled=True, why=f"failed to load: {err}")
            log(f"MAEST model unavailable ({err}); using EffNet only")
            return None
    return _maest


# EffNet makes one prediction per ~2 s window, stepping ~1 s (62 frames of 256 samples at 16 kHz).
EFFNET_HOP = 62 * 256
EFFNET_WIN = 128 * 256


def window_energy_db(mono16: np.ndarray, n: int, hop: int = EFFNET_HOP, win: int = EFFNET_WIN) -> np.ndarray:
    """Mean power (dB) of each EffNet window; windows past the end are -120, the last ones may be partial."""
    x = np.asarray(mono16, dtype=np.float64)
    csum = np.concatenate([[0.0], np.cumsum(x * x)])
    start = np.arange(n) * hop
    end = np.minimum(start + win, len(x))
    out = np.full(n, -120.0, dtype=np.float32)
    ok = end > start
    out[ok] = 10 * np.log10((csum[end[ok]] - csum[start[ok]]) / (end[ok] - start[ok]) + 1e-12)
    return out


def main_section_mask(energy_db: np.ndarray) -> np.ndarray:
    """True for windows in the track's main (loud) part; quiet intros/breakdowns are False."""
    if len(energy_db) < 8:
        return np.ones(len(energy_db), dtype=bool)
    ref = np.percentile(energy_db, 90)
    mask = energy_db >= ref - MAIN_SECTION_DB
    if mask.mean() < 0.3:  # very dynamic track: keep the louder half instead
        mask = energy_db >= np.percentile(energy_db, 50)
    return mask


def pooled_effnet(mono16: np.ndarray):
    """EffNet embeddings/styles averaged over the main section.
    Returns (emb, acts, emb_mean, acts_mean, coverage, mask)."""
    emb, acts = run_style_model(mono16)
    mask = main_section_mask(window_energy_db(mono16, len(acts)))
    if not mask.any():
        mask[:] = True
    return emb, acts, emb[mask].mean(axis=0), acts[mask].mean(axis=0), float(mask.mean()), mask


_moods: dict = {}


def mood_models() -> dict:
    """{mood: (model, index of the positive class)} for every mood head that's downloaded."""
    if "loaded" not in _moods:
        _moods["loaded"] = {}
        for m in MOODS:
            pb, js = MODELS_DIR / f"mood_{m}-discogs-effnet-1.pb", MODELS_DIR / f"mood_{m}-discogs-effnet-1.json"
            if not (pb.exists() and js.exists()):
                continue
            try:
                classes = json.loads(js.read_text())["classes"]
                idx = classes.index(m) if m in classes else 0
                model = es().TensorflowPredict2D(graphFilename=str(pb), output="model/Softmax")
                _moods["loaded"][m] = (model, idx)
            except Exception as err:
                log(f"mood model {m} unavailable ({err})")
    return _moods["loaded"]


def run_moods(emb: np.ndarray, mask: np.ndarray) -> dict:
    """Mood scores 0-1 (share of the main section that sounds aggressive, happy, ...). A head that fails
    on this track is left out for this track only (the cache then counts it as missing, see deep_missing)."""
    out = {}
    for m, (model, idx) in mood_models().items():
        try:
            p = np.asarray(model(emb), dtype=np.float32).reshape(len(emb), -1)
            out[m] = round(float(p[mask, idx].mean()), 3)
        except Exception as err:
            log(f"mood model {m} failed on this track ({err})")
    return out


def moods_missing(cached: dict | None) -> bool:
    """True if a cached moods dict lacks a mood the loaded heads can measure."""
    return not set(mood_models()) <= set(cached or {})


def mood_heads_present() -> list[str]:
    """Moods whose head is downloaded (a file check: doesn't load TensorFlow)."""
    return [m for m in MOODS if _head_present(f"mood_{m}-discogs-effnet-1")]


_instr: dict = {}


def instrument_classes() -> list[str]:
    """The tagger's 40 instrument names (empty if it isn't downloaded)."""
    if "classes" not in _instr:
        js = MODELS_DIR / f"{INSTRUMENT_MODEL}.json"
        try:
            _instr["classes"] = list(json.loads(js.read_text())["classes"]) if js.exists() else []
        except Exception as err:
            log(f"instrument model metadata unreadable ({err})")
            _instr["classes"] = []
    return _instr["classes"]


def _load_head(name: str, default_output: str):
    """A classification head on the EffNet embeddings, with the graph's input/output names taken
    from its metadata rather than guessed (see MAEST). Returns (model, classes)."""
    pb, js = MODELS_DIR / f"{name}.pb", MODELS_DIR / f"{name}.json"
    meta = json.loads(js.read_text())
    schema = meta.get("schema", {})
    ins = [i["name"] for i in schema.get("inputs", [])]
    outs = [o["name"] for o in schema.get("outputs", []) if o.get("output_purpose") == "predictions"]
    model = es().TensorflowPredict2D(graphFilename=str(pb), input=ins[0] if ins else "model/Placeholder",
                                     output=outs[0] if outs else default_output)
    return model, list(meta["classes"])


def _head_present(name: str) -> bool:
    return (MODELS_DIR / f"{name}.pb").exists() and (MODELS_DIR / f"{name}.json").exists()


def instrument_features() -> list[str]:
    """Names of everything run_instruments measures, in a fixed order (the colour model's input)."""
    return instrument_classes() + (VOICE_FEATURES if _head_present(VOICE_MODEL) else [])


def instrument_model():
    """The instrument tagger, or None if it isn't downloaded or won't load."""
    if "model" not in _instr:
        _instr["model"] = None
        if _head_present(INSTRUMENT_MODEL) and instrument_classes():
            try:
                _instr["model"] = _load_head(INSTRUMENT_MODEL, "model/Sigmoid")[0]
            except Exception as err:
                _instr["why"] = f"{type(err).__name__}: {err}"
                log(f"instrument model unavailable ({err})")
    return _instr["model"]


def voice_model():
    """(voice/instrumental head, index of the 'voice' class), or None."""
    if "voice" not in _instr:
        _instr["voice"] = None
        if _head_present(VOICE_MODEL):
            try:
                model, classes = _load_head(VOICE_MODEL, "model/Softmax")
                _instr["voice"] = (model, classes.index("voice"))
            except Exception as err:
                _instr["why"] = f"{type(err).__name__}: {err}"
                log(f"vocals model unavailable ({err})")
    return _instr["voice"]


def instruments_missing(cached: dict | None) -> bool:
    """True if a cached instruments dict lacks something the downloaded models can measure."""
    if instrument_model() is None and voice_model() is None:
        return False
    return not set(instrument_features()) <= set(cached or {})


def run_instruments(emb: np.ndarray, mask: np.ndarray) -> dict | None:
    """Instrument scores 0-1 over the main section, for all 40 instruments, plus vocals over the
    whole track. None if neither model is available."""
    out = {}
    model = instrument_model()
    if model is not None:
        try:
            p = np.asarray(model(emb), dtype=np.float32).reshape(len(emb), -1)
            out.update({c: round(float(v), 3) for c, v in zip(instrument_classes(), p[mask].mean(axis=0))})
        except Exception as err:  # this track only; a load failure (instrument_model) is what turns it off
            log(f"instrument model failed on this track ({err})")
    voice = voice_model()
    if voice is not None:
        try:
            v = np.asarray(voice[0](emb), dtype=np.float32).reshape(len(emb), -1)[:, voice[1]]
            out.update(vocals=round(float(v.mean()), 3), vocal_share=round(float((v > 0.5).mean()), 3))
        except Exception as err:  # this track only
            log(f"vocals model failed on this track ({err})")
    return out or None


def extra_heads() -> dict:
    """{name: model} for the approachability/engagement heads that are downloaded."""
    if "extra" not in _instr:
        _instr["extra"] = {}
        for h in EXTRA_HEADS:
            name = f"{h}_regression-discogs-effnet-1"
            if _head_present(name):
                try:
                    _instr["extra"][h] = _load_head(name, "model/Identity")[0]
                except Exception as err:
                    log(f"{h} model unavailable ({err})")
    return _instr["extra"]


def energy_arc(short_term: np.ndarray) -> dict:
    """How the track's energy moves, from the short-term loudness (10 values per second):
    drop_lift_db: loudest moments above the typical level; intro_sec: time to reach full energy;
    breakdown_share / longest_break_sec: time spent 6 dB+ below the loud parts (not counting the
    intro/outro); dynamics_db: loud parts minus quiet parts. Silence (below -70 LUFS) counts as quiet where it
    is part of the track: silence before the music adds to intro_sec, a silent gap is a break. Silence after the
    music is file padding, left out (all the silence in a test collection of ~470 tracks was at the end)."""
    s_all = np.asarray(short_term, dtype=np.float32)
    sound = np.isfinite(s_all) & (s_all > -70)
    s = s_all[sound]
    if len(s) < 50:
        return {k: None for k in ARC_FEATURES}  # too short to say; counts as measured
    end = int(np.flatnonzero(sound)[-1]) + 1
    s_all, sound = s_all[:end], sound[:end]
    hop = 0.1
    p90 = float(np.percentile(s, 90))
    quiet = ~sound | (s_all < p90 - 6)
    longest, i = 0, 0
    while i < len(quiet):
        if quiet[i]:
            j = i
            while j < len(quiet) and quiet[j]:
                j += 1
            if i > 0 and j < len(quiet):  # a breakdown, not the intro or outro
                longest = max(longest, j - i)
            i = j
        else:
            i += 1
    return {
        "drop_lift_db": round(float(np.percentile(s, 95) - np.median(s)), 2),
        "intro_sec": round(next((k for k, v in enumerate(s_all) if sound[k] and v >= p90 - 3), 0) * hop, 1),
        "breakdown_share": round(float(quiet.mean()), 3),
        "longest_break_sec": round(longest * hop, 1),
        "dynamics_db": round(p90 - float(np.percentile(s, 10)), 2),
    }


def run_extras(emb: np.ndarray, short_term: np.ndarray) -> dict:
    """Approachability/engagement (whole track) + energy arc."""
    out = energy_arc(short_term)
    for h, model in extra_heads().items():
        try:
            out[h] = round(float(np.asarray(model(emb), dtype=np.float32).reshape(len(emb), -1)[:, 0].mean()), 3)
        except Exception as err:  # this track only; extras_missing then asks for it again
            log(f"{h} model failed on this track ({err})")
    return out


def extras_missing(cached: dict | None) -> bool:
    return not (set(ARC_FEATURES) | set(extra_heads())) <= set(cached or {})


def top_instruments(scores: dict | None, n: int = 5, min_score: float = 0.1) -> dict:
    """The strongest instruments (vocals are reported separately, as 'vocal_share')."""
    inst = ((k, v) for k, v in (scores or {}).items() if k not in VOICE_FEATURES and v >= min_score)
    return dict(sorted(inst, key=lambda kv: -kv[1])[:n])


def color_inputs(emb_mean: np.ndarray, acts_mean: np.ndarray | None, info: dict,
                 maest: np.ndarray | None = None, instruments: dict | None = None,
                 extra: dict | None = None) -> dict:
    """Everything the colour model may look at, as named blocks.
    info: bpm, lufs, loudness_range, key ("A minor"), key_strength, mood_<name> (any may be missing).
    instruments: {instrument: score} from run_instruments. extra: from run_extras (any may be missing;
    defaults are typical values in a club collection)."""
    def num(v, default, scale):
        return (default if v is None else float(v)) / scale
    key = info.get("key") or ""
    extras = [
        num(info.get("bpm"), 130.0, 100.0),
        num(info.get("lufs"), -9.0, 10.0),
        num(info.get("loudness_range"), 6.0, 10.0),
        1.0 if key.endswith("minor") else 0.0 if key.endswith("major") else 0.5,
        num(info.get("key_strength"), 0.6, 1.0),
    ] + [num(info.get(f"mood_{m}"), 0.5, 1.0) for m in MOODS]
    ex = extra or {}
    approach = [num(ex.get("approachability"), 0.19, 1.0), num(ex.get("engagement"), 0.95, 1.0)]
    arc = [num(ex.get("drop_lift_db"), 1.5, 1.0), num(ex.get("intro_sec"), 13.0, 10.0),
           num(ex.get("breakdown_share"), 0.09, 1.0), num(ex.get("longest_break_sec"), 11.0, 10.0),
           num(ex.get("dynamics_db"), 5.6, 1.0)]
    return {"sound": np.asarray(emb_mean, dtype=np.float32),
            "styles": np.asarray(acts_mean if acts_mean is not None else np.zeros(400), dtype=np.float32),
            "maest": np.asarray(maest if maest is not None else np.zeros(768), dtype=np.float32),
            "instruments": np.asarray([(instruments or {}).get(c, 0.0) for c in instrument_features()],
                                      dtype=np.float32),
            "extras": np.asarray(extras, dtype=np.float32),
            "approach": np.asarray(approach, dtype=np.float32),
            "arc": np.asarray(arc, dtype=np.float32)}


FEATURE_SETS = {"sound": ["sound", "extras"], "styles": ["styles", "extras"], "all": ["sound", "styles", "extras"],
                "maest": ["maest", "extras"], "maest+sound": ["maest", "sound", "extras"],
                "instruments": ["instruments", "extras"], "maest+instruments": ["maest", "instruments", "extras"],
                "maest+sound+instruments": ["maest", "sound", "instruments", "extras"],
                # the small descriptor blocks get extra weight in the model (colors.WEIGHTED_SETS)
                "maest+sound+descriptors": ["maest", "sound", "extras", "instruments", "approach", "arc"]}


def color_features(inputs: dict, feature_set: str) -> np.ndarray:
    return np.concatenate([inputs[k] for k in FEATURE_SETS[feature_set]])


_color_bundle: dict = {}


def color_model():
    if not COLOR_MODEL.exists():
        return None
    mtime = COLOR_MODEL.stat().st_mtime
    if _color_bundle.get("mtime") != mtime:
        import joblib

        from . import colors

        # Models trained before the move into the dj_track_analyzer package pickled colors.BlockWeights
        # under the plain module name `colors`.
        sys.modules.setdefault("colors", colors)
        _color_bundle.update(mtime=mtime, bundle=joblib.load(COLOR_MODEL))
    return _color_bundle["bundle"]


def describe_position(pos: float) -> dict:
    """1.0 = Red ... 7.0 = Magenta on the user's colour scale."""
    pos = float(np.clip(pos, 1, len(COLOR_NAMES)))
    value = int(round(pos))
    out = {"color": COLOR_NAMES[value - 1], "value": value, "position": round(pos, 2)}
    lean = pos - value
    if abs(lean) >= 0.25:
        nb = value + (1 if lean > 0 else -1)
        out["leaning"] = COLOR_NAMES[nb - 1]
    return out


def predict_color(emb_mean: np.ndarray, acts_mean: np.ndarray | None, info: dict,
                  maest: np.ndarray | None = None, instruments: dict | None = None,
                  extra: dict | None = None) -> dict | None:
    b = color_model()
    if b is None or "feature_set" not in b:  # no model, or one from an older version: retrain
        return None
    if "maest" in b["feature_set"] and maest is None:
        return None  # this model needs MAEST features we don't have for this track
    if "instruments" in FEATURE_SETS[b["feature_set"]] and (not instruments or b.get("instruments") != instrument_features()):
        return None  # needs instrument scores we don't have (or the tagger changed since training)
    if "arc" in FEATURE_SETS[b["feature_set"]] and extra is None:
        return None  # needs the energy arc / approachability we don't have for this track
    if any(info.get(f"mood_{m}") is None for m in mood_heads_present()):
        return None  # a mood wasn't measured for this track; color_inputs would put a made-up 0.5 there
    x = color_features(color_inputs(emb_mean, acts_mean, info, maest, instruments, extra),
                       b["feature_set"]).reshape(1, -1)
    if b["kind"] == "classes":
        probs = b["model"].predict_proba(x)[0]
        classes = list(b["model"].classes_)
        pos = float(sum(p * c for p, c in zip(probs, classes)))
        out = describe_position(pos)
        best = classes[int(np.argmax(probs))]
        out.update(color=COLOR_NAMES[best - 1], value=int(best), confidence=round(float(probs.max()), 2))
        return out
    pos = float(b["model"].predict(x)[0])
    if b.get("calibrator") is not None:
        pos = float(b["calibrator"].predict([pos])[0])
    out = describe_position(pos)
    out["confidence"] = color_confidence(b, pos, out["value"])
    return out


def color_confidence(b: dict, pos: float, colour: int, within: int = 0) -> float | None:
    """How often tracks the model placed near `pos` in cross-validation (so without having seen
    them) really had `colour` (within=1: that colour or a neighbour). Smoothed over nearby positions
    and pulled toward the overall rate where few tracks landed. None for models trained before
    this existed (retrain with `colors train`)."""
    cv = b.get("cv")
    if not cv:
        return None
    p, y = np.asarray(cv["position"], dtype=float), np.asarray(cv["truth"], dtype=float)
    hit = (np.abs(y - colour) <= within).astype(float)
    prior = float((np.abs(y - np.clip(np.round(p), 1, len(COLOR_NAMES))) <= within).mean())
    w = np.exp(-0.5 * ((p - float(np.clip(pos, 1, len(COLOR_NAMES)))) / 0.3) ** 2)
    k = 5.0  # pseudo-tracks of prior
    return round(float((w @ hit + k * prior) / (w.sum() + k)), 2)


def _maest_audio(mono16: np.ndarray) -> np.ndarray:
    """The most energetic 30 s blocks of the track, joined (short tracks are looped to 30 s)."""
    block = 30 * 16000
    x = np.asarray(mono16, dtype=np.float32)
    if len(x) < block:
        x = np.tile(x, int(np.ceil(block / max(len(x), 1))))[:block]
    n = len(x) // block
    energy = [float(np.mean(x[i * block:(i + 1) * block] ** 2)) for i in range(n)]
    chosen = sorted(np.argsort(energy)[::-1][:max(1, MAEST_BLOCKS)])
    return np.concatenate([x[i * block:(i + 1) * block] for i in chosen])


# TensorflowPredictMAEST's own input pipeline, reproduced so one graph run can return two outputs:
# MusiCNN mel bands (512/256 frames at 16 kHz), normalised with MAEST's training statistics, cut into
# 30 s patches. Checked on the Mac 2026-10-21: identical to TensorflowPredictMAEST (max diff 2e-7).
MAEST_MEL_MEAN, MAEST_MEL_STD = 2.06755686098554, 1.268292820667291
MAEST_PATCH, MAEST_PATCH_HOP = 1876, 1875


def _maest_patches(audio: np.ndarray) -> np.ndarray:
    e = es()
    mel = e.TensorflowInputMusiCNN()
    frames = np.array([mel(fr) for fr in e.FrameGenerator(audio, frameSize=512, hopSize=256, startFromZero=False)],
                      dtype=np.float32)
    frames = (frames - MAEST_MEL_MEAN) / (2 * MAEST_MEL_STD)
    n = max(0, (len(frames) - MAEST_PATCH) // MAEST_PATCH_HOP + 1)
    return np.stack([frames[i * MAEST_PATCH_HOP: i * MAEST_PATCH_HOP + MAEST_PATCH] for i in range(n)]) if n else \
        np.zeros((0, MAEST_PATCH, frames.shape[1] if frames.ndim == 2 else 96), dtype=np.float32)


def run_maest(mono16: np.ndarray) -> tuple[np.ndarray | None, np.ndarray | None]:
    """(style scores, layer-7 embedding) from the most energetic 30 s blocks of the track, in one
    pass; (None, None) when MAEST isn't available."""
    m = maest_model()
    if m is None:
        return None, None
    from essentia import Pool

    try:
        preds, embs = [], []
        for patch in _maest_patches(_maest_audio(mono16)):  # the graph takes one patch per run
            pool = Pool()
            pool.set(m["input"], patch[None, None, :, :].copy())
            out = m["model"](pool)
            preds.append(np.asarray(out[m["pred"]], dtype=np.float32).reshape(-1))
            emb = np.asarray(out[m["emb"]], dtype=np.float32)
            embs.append(emb.reshape(-1, emb.shape[-1]).mean(axis=0))  # average over tokens
        if not preds:
            raise RuntimeError("audio too short for a MAEST patch")
    except Exception as err:  # this track only; a load failure (maest_model) is what turns MAEST off
        _maest["why"] = f"failed on this track: {err}"
        log(f"MAEST failed on this track ({err}); its genre uses EffNet only")
        return None, None
    return np.mean(preds, axis=0), np.mean(embs, axis=0)


def maest_classes() -> list[str]:
    if "classes" not in _maest:
        _maest["classes"] = json.loads((MODELS_DIR / "discogs-maest-30s-pw-519l-2.json").read_text())["classes"]
    return _maest["classes"]


def label_scores(effnet_mean: np.ndarray, maest_mean: np.ndarray | None = None) -> dict[str, float]:
    """Blend both models into one {"Electronic---Hard Trance": score} map."""
    e = dict(zip(style_models()["classes"], map(float, effnet_mean)))
    if maest_mean is None:
        return e
    m = dict(zip(maest_classes(), map(float, maest_mean)))
    # MAEST scores run higher than EffNet's (~1.3x on this collection), so a style only one model
    # knows (Neo Trance, UK Funky, ...) stands in for the missing one at this track's EffNet/MAEST
    # ratio over the shared styles, instead of counting unblended and scoring a bit high.
    shared = set(e) & set(m)
    m_sum = sum(m[lab] for lab in shared)
    ratio = sum(e[lab] for lab in shared) / m_sum if m_sum > 0 else 1.0
    out = {}
    for lab in set(e) | set(m):
        if lab in shared:
            out[lab] = (1 - MAEST_WEIGHT) * e[lab] + MAEST_WEIGHT * m[lab]
        elif lab in m:
            out[lab] = (1 - MAEST_WEIGHT) * m[lab] * ratio + MAEST_WEIGHT * m[lab]
        else:  # none today: all 400 EffNet styles are in MAEST
            out[lab] = e[lab]
    return out


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------

PITCH = {
    "C": 0, "B#": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "Fb": 4,
    "F": 5, "E#": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
    "A#": 10, "Bb": 10, "B": 11, "Cb": 11,
}
# Camelot number by pitch class
CAMELOT_MINOR = {8: 1, 3: 2, 10: 3, 5: 4, 0: 5, 7: 6, 2: 7, 9: 8, 4: 9, 11: 10, 6: 11, 1: 12}
CAMELOT_MAJOR = {11: 1, 6: 2, 1: 3, 8: 4, 3: 5, 10: 6, 5: 7, 0: 8, 7: 9, 2: 10, 9: 11, 4: 12}


def camelot(key: str, scale: str) -> str | None:
    pc = PITCH.get(key)
    if pc is None:
        return None
    if scale == "minor":
        return f"{CAMELOT_MINOR[pc]}A"
    return f"{CAMELOT_MAJOR[pc]}B"


def fold_bpm(bpm: float) -> float:
    if bpm <= 0:
        return bpm
    while bpm < BPM_MIN:
        bpm *= 2
    while bpm >= BPM_MAX:
        bpm /= 2
    return bpm


def round_bpm(bpm: float | None) -> int | None:
    """BPMs are whole numbers everywhere (stored, shown, judged); the user's choice 2026-10-22."""
    return int(math.floor(float(bpm) + 0.5)) if bpm else None  # halves up, as SQLite's ROUND (Python's round() goes to even)


def fmt_time(sec: float) -> str:
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def resolve_audio(path: str) -> Path:
    p = Path(os.path.expanduser(path.strip().strip('"').strip("'"))).resolve()
    if not p.exists():
        raise FileNotFoundError(f"No such file: {p}")
    if p.is_dir():
        raise IsADirectoryError(f"{p} is a folder; use analyze_folder for folders")
    if p.suffix.lower() not in AUDIO_EXTS:
        raise ValueError(f"{p.name} is not an audio file this tool reads ({', '.join(sorted(AUDIO_EXTS))})")
    return p


def split_style(label: str) -> tuple[str, str]:
    # Discogs labels look like "Electronic---Hard Trance"
    if "---" in label:
        parent, style = label.split("---", 1)
        return style, parent
    return label, ""


# ----------------------------------------------------------------------------
# Embedding cache (so training and re-analysis don't redo the slow part)
# ----------------------------------------------------------------------------


_db_ready: dict = {}
_db_local = threading.local()  # one open connection per thread and cache file (sqlite3 connections are per thread)


def _db() -> sqlite3.Connection:
    cons = _db_local.__dict__.setdefault("cons", {})
    con = cons.get(str(CACHE_DB))
    if con is not None and CACHE_DB.exists():
        return con
    if not CACHE_DB.exists():
        _db_ready.pop(str(CACHE_DB), None)  # deleted meanwhile: create the tables again
    HOME.mkdir(parents=True, exist_ok=True)
    con = cons[str(CACHE_DB)] = sqlite3.connect(CACHE_DB, timeout=60)  # several scan workers may write at once
    if _db_ready.get(str(CACHE_DB)):
        return con
    con.execute(
        "CREATE TABLE IF NOT EXISTS emb (k TEXT PRIMARY KEY, path TEXT, duration REAL,"
        " embedding BLOB, styles BLOB, ver TEXT)"
    )
    con.execute("CREATE TABLE IF NOT EXISTS deep (k TEXT PRIMARY KEY, path TEXT, maest BLOB, moods TEXT,"
                " instruments TEXT, extra TEXT, maest_styles BLOB, ver TEXT)")
    for table, cols in (("emb", {"ver": "TEXT"}),
                        ("deep", {"instruments": "TEXT", "extra": "TEXT", "maest_styles": "BLOB", "ver": "TEXT"})):
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for col, typ in cols.items():
            if col not in have:
                try:  # caches from older versions
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
                except sqlite3.OperationalError:
                    pass  # another worker added it first
    con.execute("CREATE INDEX IF NOT EXISTS ix_emb_path ON emb(path)")
    if con.execute("PRAGMA user_version").fetchone()[0] < 1:
        # Rows from before the `ver` column: the ones whose key still matches their file were
        # written by this version (the key includes CACHE_VERSION).
        for table in ("emb", "deep"):
            for k, path in con.execute(f"SELECT k, path FROM {table} WHERE ver IS NULL").fetchall():
                try:
                    if os.path.exists(path) and _cache_key(Path(path)) == k:
                        con.execute(f"UPDATE {table} SET ver=? WHERE k=?", (CACHE_VERSION, k))
                except OSError:
                    pass
        con.execute("PRAGMA user_version = 1")
    con.commit()
    _db_ready[str(CACHE_DB)] = True
    return con


def _audio_length(p: Path) -> float | None:
    try:
        import mutagen

        f = mutagen.File(p)
        return float(f.info.length) if f is not None and f.info else None
    except Exception:
        return None


# {path: target BPM / source BPM} while a tempo view is in use (tempo.use_view): its cache stores the
# stretched audio's length, so the file's own length must be scaled to compare.
STRETCH: dict[str, float] = {}


def _recover_key(con: sqlite3.Connection, p: Path, key: str) -> bool:
    """A tag edit (ours, Traktor's, any tagger's) changes a file's size and date, and with them the
    cache key, although the audio is the same. If this path has a cached analysis from this version
    whose duration matches the file's audio length, move it to the new key. True if recovered."""
    rows = con.execute("SELECT k, duration FROM emb WHERE path=? AND ver=? ORDER BY rowid DESC",
                       (str(p), CACHE_VERSION)).fetchall()
    if not rows:
        return False
    length = _audio_length(p)
    if length is None:
        return False
    length /= STRETCH.get(str(p), 1.0)
    for old, duration in rows:
        if duration is not None and abs(duration - length) < 0.25:  # decoders agree within ~0.05 s
            con.execute("UPDATE OR REPLACE emb SET k=? WHERE k=?", (key, old))
            con.execute("UPDATE OR REPLACE deep SET k=? WHERE k=?", (key, old))
            con.commit()
            return True
    return False


def _get(sql: str, p: Path):
    try:
        key = _cache_key(p)
    except FileNotFoundError:
        return None  # the file is gone: nothing cached for it counts
    with _db() as con:
        row = con.execute(sql, (key,)).fetchone()
        if row is None and _recover_key(con, p, key):
            row = con.execute(sql, (key,)).fetchone()
    return row


def deep_get(p: Path):
    """Cached (MAEST embedding or None, moods dict, instruments dict or None, extras dict or None)
    for a file, or None if not cached. Extras: approachability, engagement, energy arc."""
    row = _get("SELECT maest, moods, instruments, extra FROM deep WHERE k=?", p)
    if not row:
        return None
    maest = np.frombuffer(row[0], dtype=np.float32) if row[0] else None
    return (maest, json.loads(row[1] or "{}"), json.loads(row[2]) if row[2] else None,
            json.loads(row[3]) if row[3] else None)


def maest_cached(p: Path) -> tuple[np.ndarray, np.ndarray] | None:
    """Cached (MAEST style scores, MAEST embedding), so a rescan doesn't run MAEST again."""
    row = _get("SELECT maest_styles, maest FROM deep WHERE k=?", p)
    if not row or not row[0] or not row[1]:
        return None
    return np.frombuffer(row[0], dtype=np.float32), np.frombuffer(row[1], dtype=np.float32)


def deep_put(p: Path, maest: np.ndarray | None, moods: dict, instruments: dict | None = None,
             extra: dict | None = None, maest_styles: np.ndarray | None = None) -> None:
    def blob(a):
        return a.astype(np.float32).tobytes() if a is not None else None

    with _db() as con:
        if maest is None or maest_styles is None:  # keep MAEST results cached earlier for the same audio
            old = con.execute("SELECT maest, maest_styles FROM deep WHERE k=?", (_cache_key(p),)).fetchone()
            if old and maest is None and old[0]:
                maest = np.frombuffer(old[0], dtype=np.float32)
            if old and maest_styles is None and old[1]:
                maest_styles = np.frombuffer(old[1], dtype=np.float32)
        con.execute("INSERT OR REPLACE INTO deep (k, path, maest, moods, instruments, extra, maest_styles, ver)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (_cache_key(p), str(p), blob(maest), json.dumps(moods),
                     json.dumps(instruments) if instruments else None, json.dumps(extra) if extra else None,
                     blob(maest_styles), CACHE_VERSION))


def deep_missing(hit) -> bool:
    """True if a deep_get() result lacks something the downloaded models can add."""
    return (hit is None or (hit[0] is None and maest_available()) or moods_missing(hit[1])
            or instruments_missing(hit[2]) or extras_missing(hit[3]))


def deep_features(p: Path):
    """MAEST embedding + mood, instrument and extra scores for a file (cached). Decodes the audio
    only when needed, and reuses a cached MAEST embedding when only the newer scores are missing."""
    hit = deep_get(p)
    if not deep_missing(hit):
        return hit
    stereo, _m44, mono16, sr = load_audio(p)
    emb, _acts, emb_mean, acts_mean, _cov, mask = pooled_effnet(mono16)
    cache_put(p, len(mono16) / 16000.0, emb_mean, acts_mean)
    moods = run_moods(emb, mask)
    instruments = run_instruments(emb, mask)
    extra = run_extras(emb, loudness(stereo, sr)[0])
    maest_styles = None
    if hit is None or (hit[0] is None and maest_available()):
        maest_styles, maest = run_maest(mono16)
    else:
        maest = hit[0]
    deep_put(p, maest, moods, instruments, extra, maest_styles)
    return maest, moods, instruments, extra


def _cache_key(p: Path) -> str:
    st = p.stat()
    raw = f"{CACHE_VERSION}|{p}|{st.st_size}|{int(st.st_mtime)}"
    return hashlib.sha1(raw.encode()).hexdigest()


def cache_get(p: Path):
    row = _get("SELECT duration, embedding, styles FROM emb WHERE k=?", p)
    if not row:
        return None
    return row[0], np.frombuffer(row[1], dtype=np.float32), np.frombuffer(row[2], dtype=np.float32)


def cache_put(p: Path, duration: float, emb: np.ndarray, styles: np.ndarray) -> None:
    with _db() as con:
        con.execute(
            "INSERT OR REPLACE INTO emb (k, path, duration, embedding, styles, ver) VALUES (?,?,?,?,?,?)",
            (_cache_key(p), str(p), duration, emb.astype(np.float32).tobytes(),
             styles.astype(np.float32).tobytes(), CACHE_VERSION),
        )


# ----------------------------------------------------------------------------
# Core analysis
# ----------------------------------------------------------------------------


TOOL_DIRS = "/opt/homebrew/bin:/usr/local/bin:/home/linuxbrew/.linuxbrew/bin"  # Homebrew's, when PATH lacks them
APT_NAMES = {"rubberband": "rubberband-cli"}  # Debian/Ubuntu packages named differently from the command


def find_tool(name: str) -> str | None:
    """ffmpeg / ffprobe / rubberband / brew: on PATH or in Homebrew's folders."""
    import shutil

    return shutil.which(name) or shutil.which(name, path=TOOL_DIRS)


def install_hint(name: str) -> str:
    """How to install ffmpeg or rubberband on this system."""
    if sys.platform == "darwin":
        return f"brew install {name}"
    return f"sudo apt install {APT_NAMES.get(name, name)} (or your distribution's package manager)"


_rubberband_exe: str | None = None


def rubberband() -> str:
    """The Rubber Band command, version 3 or newer (the R3 engine, `-3`; Ubuntu 22.04 still ships 2.0)."""
    global _rubberband_exe
    if _rubberband_exe:
        return _rubberband_exe
    import re
    import subprocess

    exe = find_tool("rubberband")
    if not exe:
        raise SystemExit(f"Rubber Band isn't installed: {install_hint('rubberband')}")
    v = subprocess.run([exe, "--version"], capture_output=True, text=True)
    m = re.match(r"(\d+)\.[\d.]*", (v.stdout or v.stderr).strip())
    if m and int(m.group(1)) < 3:
        raise SystemExit(f"Rubber Band {m.group(0)} is too old: the tempo view needs 3.0 or newer (its R3 engine). "
                         "Newer distributions have it, or get it from breakfastquay.com/rubberband.")
    _rubberband_exe = exe
    return exe


def stretch(stereo: np.ndarray, sr: float, src_bpm: float, target_bpm: float) -> np.ndarray:
    """Change the tempo from src_bpm to target_bpm keeping the pitch (like Traktor's key lock), with
    Rubber Band's R3 engine. Traktor uses its own stretcher (élastique), so this sounds close, not identical.
    At the track's own BPM the audio comes back as it is: R3 at ratio 1 isn't a passthrough (up to -55 dBFS off)."""
    import subprocess
    import tempfile

    if src_bpm == target_bpm:
        return stereo
    exe = rubberband()
    with tempfile.TemporaryDirectory(prefix="track-analyzer-") as tmp:
        src, out = Path(tmp) / "in.wav", Path(tmp) / "out.wav"
        _write_float_wav(src, stereo, int(sr))  # float in, float out: Rubber Band keeps the input's format
        subprocess.run([exe, "-3", "-q", "--tempo", f"{src_bpm:.4f}:{target_bpm:.4f}", str(src), str(out)],
                       check=True, capture_output=True)
        audio, _sr, *_ = es().AudioLoader(filename=str(out))()
    return audio


def _write_float_wav(path: Path, audio: np.ndarray, sr: int) -> None:
    """32-bit float WAV (format 3): no rounding to 16 bits and no clipping of peaks above full scale."""
    import struct

    data = np.ascontiguousarray(audio, dtype="<f4")
    ch = data.shape[1] if data.ndim == 2 else 1
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 4 + 26 + 12 + 8 + data.nbytes) + b"WAVE")
        f.write(b"fmt " + struct.pack("<IHHIIHHH", 18, 3, ch, sr, sr * ch * 4, ch * 4, 32, 0))
        f.write(b"fact" + struct.pack("<II", 4, len(data)))
        f.write(b"data" + struct.pack("<I", data.nbytes))
        f.write(data.tobytes())


def _load_downmixed(p: Path):
    """Files with more than 2 channels (5.1 FLACs): Essentia's AudioLoader refuses them, so ffmpeg mixes
    them down to stereo with its standard downmix, at the file's own rate, as float (no rounding)."""
    import shutil
    import subprocess
    import tempfile

    if not shutil.which("ffmpeg"):
        raise RuntimeError(f"{p.name} has more than 2 channels; install ffmpeg ({install_hint('ffmpeg')}) to read it")
    with tempfile.TemporaryDirectory(prefix="track-analyzer-") as tmp:
        out = Path(tmp) / "stereo.wav"
        done = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-map", "0:a:0", "-ac", "2",
                               "-c:a", "pcm_f32le", str(out)], capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError(f"ffmpeg couldn't mix {p.name} down to stereo: {done.stderr.strip()}")
        return es().AudioLoader(filename=str(out))()


def resample_medium(mono: np.ndarray, sr: float, out_sr: float) -> np.ndarray:
    """Essentia's Resample(quality=1) (libsamplerate's medium sinc), bit for bit; the Rust copy (~5x faster) if
    installed. Checked on 48 kHz mixes and on 8-192 kHz test signals (docs/WINDOWS.md)."""
    if m := dsp():
        x = np.ascontiguousarray(mono, dtype=np.float32)
        out = np.empty(m.resample_max_len(len(x), sr, out_sr), dtype=np.float32)
        return out[: m.resample_sinc_medium(x, out, sr, out_sr)]
    return es().Resample(inputSampleRate=sr, outputSampleRate=out_sr, quality=1)(mono)


def resample_linear(mono: np.ndarray, sr: float, out_sr: float) -> np.ndarray:
    """Essentia's Resample(quality=4) (libsamplerate's linear converter), bit for bit; the Rust copy if installed.
    Used for the models' 16 kHz input, as Essentia's model docs do."""
    if (m := dsp()) and hasattr(m, "resample_linear"):  # dsp 0.1.0 doesn't have it
        x = np.ascontiguousarray(mono, dtype=np.float32)
        out = np.empty(m.resample_max_len(len(x), sr, out_sr), dtype=np.float32)
        return out[: m.resample_linear(x, out, sr, out_sr)]
    return es().Resample(inputSampleRate=sr, outputSampleRate=out_sr, quality=4)(mono)


def mono_mix(stereo: np.ndarray, nch: int) -> np.ndarray:
    """Essentia's MonoMixer, bit for bit: (L+R)*0.5 in float32 (its 0.5*(L+R) in double rounds the same), or the
    left channel for a mono file (AudioLoader leaves the right one silent)."""
    st = np.asarray(stereo, dtype=np.float32)
    return st[:, 0].copy() if nch == 1 else (st[:, 0] + st[:, 1]) * np.float32(0.5)


def load_audio(p: Path, tempo: tuple[float, float] | None = None):
    """Decode once; return stereo (native rate), mono 44.1k, mono 16k, rate.
    tempo: (track's BPM, BPM to play it at) to hear it sped up / slowed down with key lock."""
    e = es()
    try:
        stereo, sr, nch, *_ = e.AudioLoader(filename=str(p))()
    except RuntimeError as err:
        if "more than 2 channels" not in str(err):
            raise
        stereo, sr, nch, *_ = _load_downmixed(p)
    if tempo:
        stereo = stretch(stereo, sr, *tempo)
    mono = mono_mix(stereo, nch)
    mono44 = mono if sr == 44100 else resample_medium(mono, sr, 44100)
    mono16 = resample_linear(mono, sr, 16000)
    return stereo, mono44, mono16, float(sr)


def run_style_model(mono16: np.ndarray):
    """Per-patch embeddings (n,1280) and style activations (n,400)."""
    m = style_models()
    emb = np.asarray(m["embed"](mono16), dtype=np.float32)
    acts = np.asarray(m["styles"](emb), dtype=np.float32)
    return emb, acts


def top_styles(scores: dict[str, float], n: int = 5, min_score: float = 0.05):
    out = []
    for label, score in sorted(scores.items(), key=lambda kv: -kv[1])[:n]:
        if out and score < min_score:
            break
        style, parent = split_style(label)
        out.append({"style": style, "parent_genre": parent, "score": round(score, 3)})
    return out


def rhythm(mono44: np.ndarray, method: str = "multifeature"):
    """(BPM, confidence, beat count). The extractor's own BPM is used as it is: a line fit through
    its beat grid was tried and made things worse (stray off-beat hits in breakdowns counted as
    beats; checked 2026-10-21 against Traktor's BPMs: 96% within 1 BPM raw vs 50% after the fit)."""
    e = es()
    lo = max(40, min(180, int(BPM_MIN)))
    hi = max(60, min(250, int(BPM_MAX)))
    bpm, beats, conf, _est, _int = e.RhythmExtractor2013(method=method, minTempo=lo, maxTempo=hi)(mono44)
    return float(bpm), float(conf), len(beats)


def key_of(mono44: np.ndarray):
    key, scale, strength = es().KeyExtractor(profileType="edma", sampleRate=44100)(mono44)
    return key, scale, float(strength)


def loudness(stereo: np.ndarray, sr: float):
    if m := dsp():  # Essentia's LoudnessEBUR128, bit for bit, ~7x faster
        short, integrated, lra = m.loudness_ebur128(np.ascontiguousarray(stereo, dtype=np.float32), sr)
        return np.frombuffer(short, dtype=np.float32).copy(), float(integrated), float(lra)
    _mom, short, integrated, lra = es().LoudnessEBUR128(sampleRate=sr)(stereo)
    return np.asarray(short, dtype=np.float32), float(integrated), float(lra)


def energy_curve(short_term: np.ndarray, duration: float, points: int = 16):
    """Coarse short-term loudness curve: shows intro / build / peak / breakdown."""
    if len(short_term) == 0:
        return []
    chunks = np.array_split(short_term, min(points, len(short_term)))
    step = duration / len(chunks)
    curve = []
    for i, c in enumerate(chunks):
        c = c[np.isfinite(c) & (c > -70)]
        curve.append({"t": fmt_time(i * step), "lufs": round(float(c.mean()), 1) if len(c) else None})
    return curve


_rules_cache: dict = {}


def dj_rules() -> dict:
    """DJ genre rules: data/dj_genres.json if present, else the one shipped here."""
    user = HOME / "dj_genres.json"
    path = user if user.exists() else Path(__file__).with_name("dj_genres.json")
    mtime = path.stat().st_mtime if path.exists() else 0
    if _rules_cache.get("key") != (str(path), mtime):
        rules = json.loads(path.read_text())["genres"] if path.exists() else {}
        _rules_cache.update(key=(str(path), mtime), rules=rules)
    return _rules_cache["rules"]


# A genre's usual BPM range is a guide, not a wall: outside it the score fades down to 60% over
# this many BPM, so 127.9 vs 128.0 doesn't flip a genre (most of a club collection sits at 124-130).
BPM_MARGIN = 3.0


def bpm_fit(bpm: float | None, lo: float, hi: float) -> float:
    """1.0 inside [lo, hi], falling linearly to 0.6 at BPM_MARGIN outside it."""
    if not bpm or lo <= bpm <= hi:
        return 1.0
    off = lo - bpm if bpm < lo else bpm - hi
    return 1.0 - 0.4 * min(1.0, off / BPM_MARGIN)


def dj_genre(scores: dict[str, float], bpm: float | None, top: int = 3):
    """Name the track in DJ terms using the pretrained Discogs scores + BPM. No training."""
    score_of = {split_style(c)[0]: v for c, v in scores.items() if c.startswith("Electronic---")}
    out = []
    for name, rule in dj_rules().items():
        groups = [g for g in rule.get("needs", []) if g]
        if not groups:
            continue
        best = [max(g, key=lambda s: score_of.get(s, 0.0)) for g in groups]  # strongest style per group
        score = min(score_of.get(s, 0.0) for s in best)
        if len(set(best)) > 1:
            score *= 1.35  # reward the more specific combined genre (e.g. Acid + Trance), but only when
            # different styles back it: one style sitting in every group (Acid House) earns nothing extra
        lo, hi = rule.get("bpm", [0, 1000])
        fit = bpm_fit(bpm, lo, hi)
        out.append((score * fit, name, fit))
    out.sort(reverse=True)
    best_score = out[0][0] if out else 0
    return [
        {"genre": n, "score": round(s, 3), **({"note": "BPM outside usual range"} if fit < 0.85 else {})}
        for s, n, fit in out[:top]
        if s >= 0.05 and s >= best_score * 0.25
    ]


def style_classes() -> list[str]:
    return json.loads((MODELS_DIR / "genre_discogs400-discogs-effnet-1.json").read_text())["classes"]


def check_rules() -> None:
    """Warn about style names in dj_genres.json that don't exist in the Discogs model."""
    if not models_present():
        return
    labels = list(style_classes())
    if maest_present():
        labels += json.loads((MODELS_DIR / "discogs-maest-30s-pw-519l-2.json").read_text())["classes"]
    known = {split_style(c)[0] for c in labels if c.startswith("Electronic---")}
    bad = sorted({s for r in dj_rules().values() for g in r.get("needs", []) for s in g if s not in known})
    if bad:
        log(f"dj_genres.json: these style names aren't Discogs styles and are ignored: {', '.join(bad)}")
    else:
        log(f"dj_genres.json: {len(dj_rules())} DJ genres, all style names valid")


_genre_bundle: dict = {}


def my_genre_model():
    """The model trained by `train`, loaded once per process (again after a retrain), or None."""
    if not GENRE_MODEL.exists():
        return None
    mtime = GENRE_MODEL.stat().st_mtime
    if _genre_bundle.get("mtime") != mtime:
        import joblib

        _genre_bundle.update(mtime=mtime, model=joblib.load(GENRE_MODEL)["model"])
    return _genre_bundle["model"]


def predict_my_genre(emb_mean: np.ndarray, top: int = 3):
    clf = my_genre_model()
    if clf is None:
        return None
    probs = clf.predict_proba(emb_mean.reshape(1, -1))[0]
    order = np.argsort(probs)[::-1][:top]
    return [{"genre": clf.classes_[i], "confidence": round(float(probs[i]), 3)} for i in order]


def track_embedding(p: Path):
    """Mean embedding + mean style activations for a file (cached)."""
    hit = cache_get(p)
    if hit:
        return hit
    _stereo, _m44, mono16, _sr = load_audio(p)
    duration = len(mono16) / 16000.0
    _emb, _acts, emb_mean, acts_mean, _cov, _mask = pooled_effnet(mono16)
    cache_put(p, duration, emb_mean, acts_mean)
    return duration, emb_mean, acts_mean


def analyze_file(path: str, segment_seconds: float | None = None, timeline: bool = True,
                 genre_bpm: float | None = None, tempo: tuple[float, float] | None = None,
                 progress: bool = False, tracklist: tuple | None = None, highlights: int = 0,
                 clip_seconds: float = 40.0, own_bpm: float | None = None) -> dict:
    """tracklist: (plays, history path, recording start) from tracklist.py, for a mix: adds a per-track
    report and judges genres at the tracks' own BPMs. genre_bpm: a BPM to judge the genre rules by instead of the measured one (scan passes
    Traktor's, which the user may have corrected by hand). tempo: (track's BPM, BPM to play it at)
    analyses the track sped up / slowed down with key lock (`tempo scan`). own_bpm: with tempo, the BPM we
    measured at the track's own tempo, for the colour model (trained on those; defaults to tempo[0]). progress: print the stage
    being worked on to stderr (the `analyze` command)."""
    def say(msg: str) -> None:
        if progress:
            log(f"[{time.time() - t0:5.1f}s] {msg}")

    p = resolve_audio(path)
    t0 = time.time()
    say(f"decoding {p.name}")
    stereo, mono44, mono16, sr = load_audio(p, tempo)
    duration = len(mono44) / 44100.0
    is_mix = duration >= MIX_THRESHOLD_SEC
    if segment_seconds is None and is_mix and timeline:
        segment_seconds = 120.0

    result: dict = {
        "file": str(p),
        "duration": fmt_time(duration),
        "type": "dj_mix" if is_mix else "track",
    }

    say("measuring loudness")
    short, lufs, lra = loudness(stereo, sr)
    result["loudness"] = {"integrated_lufs": round(lufs, 1), "loudness_range_lu": round(lra, 1)}
    if is_mix:
        from . import tracklist as tl

        # where a silence-trimmed copy (FLAC/MP3) starts; only a fallback anchor for video times without a
        # tracklist. Where the music starts in the video is the user's --offset, read off the finished video.
        result["first_sound_seconds"] = round(tl.first_sound(mono44), 2)
        if highlights:
            result["highlights"] = tl.highlights(short, duration, highlights, clip_seconds)

    if not is_mix and tempo:
        # Stretched to a known tempo: no need to measure it. The colour model below gets the track's
        # own BPM (it was trained on native tempos; 145 would move colours by itself, ~-0.3 on average).
        result["bpm"] = round_bpm(tempo[1])
        k, s, strength = key_of(mono44)
        result["key"] = f"{k} {s}"
        result["camelot"] = camelot(k, s)
        result["key_strength"] = round(strength, 2)
        result["energy_curve"] = energy_curve(short, duration)
    elif not is_mix:
        say("detecting BPM and key")
        raw_bpm, conf, nbeats = rhythm(mono44)
        result["bpm"] = round_bpm(fold_bpm(raw_bpm))
        if abs(fold_bpm(raw_bpm) - raw_bpm) > 0.5:
            result["bpm_note"] = f"detected {raw_bpm:.1f}, folded into {BPM_MIN:g}-{BPM_MAX:g} range"
        result["bpm_confidence"] = round(conf, 2)  # multifeature scale: >3.5 is very reliable
        k, s, strength = key_of(mono44)
        result["key"] = f"{k} {s}"
        result["camelot"] = camelot(k, s)
        result["key_strength"] = round(strength, 2)
        result["energy_curve"] = energy_curve(short, duration)

    # Style model (needs the downloaded models)
    try:
        say("running the style model (EffNet)")
        emb, acts, emb_mean, acts_mean, coverage, mask = pooled_effnet(mono16)
        cache_put(p, len(mono16) / 16000.0, emb_mean, acts_mean)
        maest_mean = maest_emb = None
        if not is_mix:  # one MAEST pass gives genre scores and the embedding; reused from the cache if there
            cached = maest_cached(p) if USE_MAEST else None
            if not cached and USE_MAEST:
                say("running MAEST (the slowest step)")
            maest_mean, maest_emb = cached if cached else run_maest(mono16)
        scores = label_scores(acts_mean, maest_mean)
        if genre_bpm:
            genre_bpm = round_bpm(fold_bpm(float(genre_bpm)))
            if result.get("bpm") and abs(genre_bpm - result["bpm"]) > 0.5:
                result["genre_bpm"] = genre_bpm
        result["dj_genre"] = dj_genre(scores, genre_bpm or result.get("bpm"))
        result["discogs_styles"] = top_styles(scores)
        result["genre_basis"] = {
            "models": "EffNet + MAEST" if maest_mean is not None else "EffNet",
            "main_section_share": round(coverage, 2),
        }
        if maest_mean is None and not is_mix and _maest.get("why"):
            result["genre_basis"]["maest"] = _maest["why"]
        say("moods, instruments and colour")
        mine = predict_my_genre(emb_mean)
        if mine:
            result["my_genre"] = mine
        moods = run_moods(emb, mask)
        if moods:
            result["mood"] = moods
        instruments = run_instruments(emb, mask)
        if instruments:
            result["instruments"] = top_instruments(instruments)
            if "vocal_share" in instruments:
                result["vocal_share"] = instruments["vocal_share"]
        extra = run_extras(emb, short)
        if "approachability" in extra:
            result["approachability"] = extra["approachability"]
        if not is_mix:
            # exactly the values collection.db stores, which `colors train` learns from and refresh_predictions
            # reads back: the calibrator is a step function, so even rounding differences can move a colour
            info = {"bpm": round_bpm(own_bpm or tempo[0]) if tempo else result.get("bpm"),
                    "lufs": result["loudness"]["integrated_lufs"], "loudness_range": result["loudness"]["loudness_range_lu"],
                    "key": result.get("key"), "key_strength": result.get("key_strength"),
                    **{f"mood_{m}": v for m, v in moods.items()}}
            deep_put(p, maest_emb, moods, instruments, extra, maest_mean)  # so `colors` needn't decode again
            col = predict_color(emb_mean, acts_mean, info, maest_emb, instruments, extra)
            if col:
                result["color"] = col
    except RuntimeError as err:
        emb = acts = scores = None
        result["styles_error"] = str(err)

    if segment_seconds or (tracklist and is_mix):
        with AudioPool(mono44) as pool:
            if segment_seconds:
                say(f"building the timeline ({pool.workers} processes)")
                result["timeline"] = segment_timeline(mono44, short, emb, acts, duration, segment_seconds, pool=pool)
            if tracklist and is_mix:
                from . import tracklist as tl

                say("checking against the tracklist")
                tl.check(result, pool, acts, scores, duration, *tracklist)

    result["analysis_seconds"] = round(time.time() - t0, 1)
    return result


# BPM and key of stretches of one long recording (a mix's timeline segments, its tracks), measured in
# parallel processes. The audio goes to the workers as a memory-mapped file, not one copy each.
_AUDIO: dict[str, np.ndarray] = {}


def _audio_pool_init(npy: str) -> None:
    import signal

    signal.signal(signal.SIGINT, signal.SIG_IGN)  # let the parent handle Ctrl-C
    os.dup2(2, 1)  # stray library output to stderr: stdout may be the MCP channel
    es()
    _AUDIO["mono44"] = np.load(npy, mmap_mode="r")


def _measure(job: tuple[float, float, bool, bool]) -> dict:
    """job: (start s, end s, want BPM, want key) -> {"bpm": raw BPM, "key": (key, scale, strength)}."""
    a, b, want_bpm, want_key = job
    x = np.ascontiguousarray(_AUDIO["mono44"][int(a * 44100): int(b * 44100)], dtype=np.float32)
    out = {}
    if want_bpm:
        try:
            out["bpm"] = rhythm(x)[0]
        except Exception:
            pass
    if want_key:
        out["key"] = key_of(x)
    return out


class AudioPool:
    """`with AudioPool(mono44) as pool: pool.measure(jobs)`. One process when parallel wouldn't pay."""

    def __init__(self, mono44: np.ndarray, workers: int | None = None):
        self.mono44 = mono44
        self.workers = workers or max(1, min(5, (os.cpu_count() or 4) // 2))  # as `scan`
        self._pool = self._dir = None

    def __enter__(self):
        if self.workers > 1:
            import multiprocessing as mp
            import tempfile

            self._dir = tempfile.mkdtemp(prefix="track-analyzer-")
            npy = os.path.join(self._dir, "mono44.npy")
            np.save(npy, self.mono44.astype(np.float32, copy=False))
            self._pool = mp.get_context("spawn").Pool(self.workers, initializer=_audio_pool_init, initargs=(npy,))
        return self

    def measure(self, jobs: list[tuple[float, float, bool, bool]]) -> list[dict]:
        if self._pool is None or len(jobs) < 2:
            _AUDIO["mono44"] = self.mono44
            return [_measure(j) for j in jobs]
        return self._pool.map(_measure, jobs, chunksize=1)

    def __exit__(self, exc_type, *_):
        if self._pool is not None:
            (self._pool.terminate if exc_type else self._pool.close)()
            self._pool.join()
        if self._dir:
            import shutil

            shutil.rmtree(self._dir, ignore_errors=True)
        _AUDIO.pop("mono44", None)


def segment_timeline(mono44, short, emb, acts, duration, seg_sec, pool: AudioPool | None = None):
    """Per-segment BPM, key, loudness and style: useful for DJ mixes."""
    timeline = []
    n_seg = max(1, int(np.ceil(duration / seg_sec)))
    have_model = emb is not None and len(emb) > 0
    model = my_genre_model() if have_model else None
    spans = [(i * seg_sec, min(duration, (i + 1) * seg_sec)) for i in range(n_seg)]
    spans = [(a, b) for a, b in spans if b - a >= 20]
    jobs = [(a, b, True, True) for a, b in spans]
    measured = pool.measure(jobs) if pool else AudioPool(mono44, workers=1).measure(jobs)
    for (start, end), m in zip(spans, measured):
        seg = {"start": fmt_time(start), "end": fmt_time(end)}
        if "bpm" in m:
            seg["bpm"] = round_bpm(fold_bpm(m["bpm"]))
        k, s, _st = m["key"]
        seg["key"] = f"{k} {s}"
        seg["camelot"] = camelot(k, s)
        if len(short):
            sl = short[int(start / duration * len(short)): int(end / duration * len(short))]
            sl = sl[np.isfinite(sl) & (sl > -70)]
            if len(sl):
                seg["lufs"] = round(float(sl.mean()), 1)
        if have_model:
            i0 = int(start / duration * len(acts))
            i1 = max(i0 + 1, int(end / duration * len(acts)))
            seg_acts = label_scores(acts[i0:i1].mean(axis=0))
            g = dj_genre(seg_acts, seg.get("bpm"), top=1)
            if g:
                seg["dj_genre"] = g[0]["genre"]
            seg["styles"] = [f'{s["style"]} {s["score"]:.2f}' for s in top_styles(seg_acts, n=3, min_score=0.08)]
            if model is not None:
                probs = model.predict_proba(emb[i0:i1].mean(axis=0).reshape(1, -1))[0]
                j = int(np.argmax(probs))
                seg["my_genre"] = f"{model.classes_[j]} {probs[j]:.2f}"
        timeline.append(seg)
    return timeline


# ----------------------------------------------------------------------------
# Custom genre training
# ----------------------------------------------------------------------------


def collect_training_set(folder: str) -> dict[str, list[Path]]:
    """
    Two layouts are accepted (mixed is fine):
      folder/<Genre Name>/*.mp3               one subfolder per genre
      folder/<Genre Name>.m3u8 (or .m3u)      one playlist per genre (e.g. exported from Traktor)
    """
    root = Path(os.path.expanduser(folder)).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a folder")
    sets: dict[str, list[Path]] = {}
    for sub in sorted(root.iterdir()):
        if sub.is_dir() and not sub.name.startswith("."):
            files = [f for f in sorted(sub.rglob("*")) if f.suffix.lower() in AUDIO_EXTS and not f.name.startswith("._")]
            if files:
                sets.setdefault(sub.name, []).extend(files)
        elif sub.suffix.lower() in {".m3u", ".m3u8"}:
            files = []
            for line in sub.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                f = Path(line) if os.path.isabs(line) else (sub.parent / line)
                f = f.expanduser()
                if f.exists() and f.suffix.lower() in AUDIO_EXTS:
                    files.append(f.resolve())
            if files:
                sets.setdefault(sub.stem, []).extend(files)
    return sets


def train(folder: str, progress=log) -> dict:
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    import joblib

    sets = collect_training_set(folder)
    if len(sets) < 2:
        raise ValueError(
            "Need at least 2 genres. Make one subfolder per genre (or one .m3u8 playlist per genre) "
            f"inside {folder}. Found: {list(sets) or 'nothing'}"
        )

    X, y, failed = [], [], []
    total = sum(len(v) for v in sets.values())
    done = 0
    for genre, files in sets.items():
        for f in files:
            done += 1
            try:
                _d, emb_mean, _acts = track_embedding(f)
                X.append(emb_mean)
                y.append(genre)
            except Exception as err:  # unreadable file etc.
                failed.append(f"{f.name}: {err}")
            if done % 10 == 0 or done == total:
                progress(f"  embedded {done}/{total}")
    X, y = np.vstack(X), np.array(y)

    counts = {g: int((y == g).sum()) for g in sets}
    clf = make_pipeline(
        StandardScaler(),
        LogisticRegression(max_iter=5000, C=0.5, class_weight="balanced"),
    )

    report: dict = {"genres": counts, "tracks_used": int(len(y))}
    min_count = min(counts.values())
    if min_count >= 3:
        folds = min(5, min_count)
        pred = cross_val_predict(clf, X, y, cv=StratifiedKFold(folds, shuffle=True, random_state=0))
        acc = float((pred == y).mean())
        report["cross_validated_accuracy"] = round(acc, 3)
        per = {g: round(float((pred[y == g] == g).mean()), 3) for g in counts}
        report["accuracy_per_genre"] = per
        confusions: dict[str, int] = {}
        for t, pr in zip(y, pred):
            if t != pr:
                confusions[f"{t} → {pr}"] = confusions.get(f"{t} → {pr}", 0) + 1
        report["most_confused"] = dict(sorted(confusions.items(), key=lambda kv: -kv[1])[:6])
    else:
        report["cross_validated_accuracy"] = None
        report["note"] = "Add at least 3 tracks per genre to get an accuracy estimate (20+ is better)."

    clf.fit(X, y)
    HOME.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": clf, "counts": counts, "trained_at": time.strftime("%Y-%m-%d %H:%M"),
                 "source": str(Path(folder).expanduser())}, GENRE_MODEL)
    report["saved_to"] = str(GENRE_MODEL)
    if failed:
        report["skipped_files"] = failed[:20]
    small = [g for g, c in counts.items() if c < 15]
    if small:
        report["tip"] = f"These genres have few examples and will be less reliable: {', '.join(small)}"
    return report
