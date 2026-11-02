"""The analysis models to download (Essentia's, from essentia.upf.edu) and the downloader.

Nothing here imports the rest of the package, so the Docker image can fetch the models before the code is copied in
(python model_files.py <folder>): a code change then doesn't download them again. analysis.py re-exports all of it.
"""

from __future__ import annotations

import sys
import time
import urllib.request
from pathlib import Path

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


def log(*args) -> None:
    print(*args, file=sys.stderr, flush=True)


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


def download(folder: Path, maest: bool = True) -> list[tuple[str, str]]:
    """Fetch every model missing from folder; returns the required ones that couldn't be fetched (name, url)."""
    folder.mkdir(parents=True, exist_ok=True)
    missing = []
    files = dict(MODEL_FILES)
    if maest:
        log("(includes the larger MAEST genre model, a few hundred MB; skip it with --no-maest)")
        files.update(MAEST_FILES)
    for name, url in files.items():
        dest = folder / name
        if dest.exists() and dest.stat().st_size > 0:
            log(f"  ok        {name}")
            continue
        log(f"  download  {name}")
        if not _fetch(url, dest):
            missing.append((name, url))
    for name, url in {**MOOD_FILES, **INSTRUMENT_FILES, **EXTRA_HEAD_FILES}.items():  # small and optional: a missing one is just skipped
        dest = folder / name
        if not (dest.exists() and dest.stat().st_size > 0):
            log(f"  download  {name}")
            if not _fetch(url, dest):
                log(f"    (optional model {name} unavailable; it will be skipped)")
    return missing


if __name__ == "__main__":  # the Docker test image: python model_files.py /data/models
    if len(sys.argv) != 2:
        sys.exit("usage: python model_files.py <folder for the models>")
    gone = download(Path(sys.argv[1]))
    for name, url in gone:
        log(f"couldn't download {name} from {url}")
    sys.exit(1 if gone else 0)
