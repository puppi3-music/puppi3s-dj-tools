"""Take the README's screenshots (docs/img/) from your own scanned library, with every track name, artist, album and
file path replaced by made-up ones in the browser first. Starts the page's app on a spare port (nothing opened,
nothing written), then drives headless Chrome (readme_shots.mjs; needs Chrome/Brave/Chromium and node 22+).

    uv run python scripts/readme_shots.py                   # the first track with your own cues
    uv run python scripts/readme_shots.py --track "Title"   # a track whose path contains this

Look at every picture before committing: the swap covers what the page shows today, not text added later.
"""

import argparse
import json
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from dj_track_analyzer import web

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "docs" / "img"


def start() -> str:
    """The page's app (web.build_app) under uvicorn on a free port, in a daemon thread."""
    import uvicorn

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(web.build_app([web.LOGS]), host="127.0.0.1", port=port, log_level="critical"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/api/jobs", timeout=1)
            return base
        except OSError:
            time.sleep(0.1)
    raise SystemExit("the app didn't start")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--track", help="text in the path of the track to show (default: the first with your own cues)")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    chrome = next((p for p in ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                               "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                               shutil.which("chromium") or "", shutil.which("google-chrome") or ""] if p and Path(p).exists()), None)
    node = shutil.which("node")
    if not chrome or not node:
        raise SystemExit("needs Chrome/Brave/Chromium and node 22+")
    base = start()
    rows = json.load(urllib.request.urlopen(base + "/api/tracks", timeout=60))["tracks"]
    tracks = [t["path"] for t in rows if t.get("type") == "track" and t.get("own_cues")]
    if a.track:
        tracks = [p for p in tracks if a.track.lower() in p.lower()]
    if not tracks:
        raise SystemExit("no scanned track with your own cues" + (f" matches {a.track!r}" if a.track else ""))
    a.out.mkdir(parents=True, exist_ok=True)
    subprocess.run([node, str(HERE / "readme_shots.mjs"), chrome, base, str(a.out), tracks[0]], check=True, timeout=600)


if __name__ == "__main__":
    main()
