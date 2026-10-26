"""Live checks for the web page (web.py + web/index.html), run against the real data in data/.
Starts the page's own app on a spare port inside this process (no browser, nothing opened) and calls it.
Read-only: only GETs that read, and POSTs with input the server must refuse. The server's own caches may
fill as they do in normal use (data/cue-corrections.json, the saved_sample list).

  uv run python .claude/skills/audit-web/scripts/checks.py                 all but shots and export
  uv run python .claude/skills/audit-web/scripts/checks.py shots [DIR]     screenshots (headless Chrome)
  uv run python .claude/skills/audit-web/scripts/checks.py endpoints hosts only some

Checks: endpoints, tracks, errors, hosts, static, overflow, shots, export.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dj_track_analyzer import collection, web

PORT = 0
BASE = ""
SAMPLE = 8  # tracks whose panel (cues, suggestions, waveform) is timed


def _start() -> None:
    """The page's app (web.build_app) under uvicorn on a free port, in a daemon thread."""
    global PORT, BASE
    import uvicorn

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        PORT = s.getsockname()[1]
    BASE = f"http://127.0.0.1:{PORT}"
    server = uvicorn.Server(uvicorn.Config(web.build_app([web.LOGS]), host="127.0.0.1", port=PORT, log_level="critical"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            urllib.request.urlopen(BASE + "/api/jobs", timeout=1)
            return
        except OSError:
            time.sleep(0.1)
    raise SystemExit("the app didn't start")


def call(path: str, body: dict | None = None, headers: dict | None = None, raw: bytes | None = None):
    """(status, seconds, bytes, parsed JSON or None, content type)."""
    h = dict(headers or {})
    data = raw
    if body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), h.get("Content-Type", "application/json")
    req = urllib.request.Request(BASE + path, data=data, headers=h, method="POST" if data is not None else "GET")
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            status, out, ctype = r.status, r.read(), r.headers.get("content-type", "")
    except urllib.error.HTTPError as e:
        status, out, ctype = e.code, e.read(), e.headers.get("content-type", "")
    dt = time.perf_counter() - t
    try:
        js = json.loads(out)
    except ValueError:
        js = None
    return status, dt, len(out), js, ctype


def _row(name: str, r) -> None:
    status, dt, n, js, _ = r
    err = js.get("error") if isinstance(js, dict) else None
    flag = "" if status == 200 and not err else "  <-- " + (str(err)[:120] if err else f"HTTP {status}")
    print(f"  {name:52} {status} {dt * 1000:8.0f} ms {n / 1024:9.1f} KB{flag}")


def _libs() -> list[str]:
    return [x["id"] for x in call("/api/libraries")[3]["libraries"]]


def _sample_tracks(n: int = SAMPLE) -> list[str]:
    rows = call("/api/tracks")[3]["tracks"]
    tracks = [t["path"] for t in rows if t.get("type") == "track" and t.get("own_cues")]
    step = max(1, len(tracks) // n)
    return tracks[::step][:n]


def endpoints() -> None:
    """Every read endpoint the page calls, per library: status, time, size. Run twice: cold, then warm."""
    libs = _libs()
    for rnd in ("cold", "warm"):
        print(f" {rnd}:")
        _row("libraries", call("/api/libraries"))
        _row("setup", call("/api/setup"))
        _row("jobs", call("/api/jobs"))
        for lib in libs:
            q = urllib.parse.urlencode({"lib": lib})
            _row(f"stats {lib}", call(f"/api/stats?{q}"))
            _row(f"tracks {lib}", call(f"/api/tracks?{q}"))
            _row(f"hype {lib} (limit 500)", call(f"/api/hype?{q}&limit=500"))
        _row("colours/review", call("/api/colours/review?limit=200"))
        _row("colours/explain", call("/api/colours/explain"))
        _row("colours/kept", call("/api/colours/kept"))
        for lib in libs[1:]:
            _row(f"tempo/report {lib}", call(f"/api/tempo/report?bpm={lib[3:]}&limit=25"))
        _row("saved", call("/api/saved"))
        _row("samples/folders", call("/api/samples/folders"))


def tracks() -> None:
    """A track's panel: /api/cues (also after each approve/ignore/rescan), suggestions, the zoom waveform."""
    paths = _sample_tracks()
    for p in paths:
        q = urllib.parse.urlencode({"path": p})
        name = Path(p).name[:40]
        _row(f"cues      {name}", call(f"/api/cues?{q}"))
        _row(f"cues again", call(f"/api/cues?{q}"))
        _row(f"suggest   {name}", call(f"/api/suggest?{q}"))
        _row(f"wave      {name}", call(f"/api/wave?{q}"))
    t = time.perf_counter()
    web._saved_db().close()
    print(f"  _saved_db() alone (runs inside every /api/cues): {(time.perf_counter() - t) * 1000:.0f} ms; "
          f"sample root {web.sample_root()}")


def errors() -> None:
    """Bad input must come back as a JSON {error} with a 4xx, never a 500 / HTML page (the page reads .json())."""
    p = _sample_tracks(1)[0]
    cases = {
        "GET  cues, unknown path": ("/api/cues?path=/nope.mp3", None),
        "GET  cues, no path": ("/api/cues", None),
        "GET  wave, bad t0": ("/api/wave?" + urllib.parse.urlencode({"path": p, "t0": "x", "t1": "1"}), None),
        "GET  suggest, unknown path": ("/api/suggest?path=/nope.mp3", None),
        "GET  saved-file, bad id": ("/api/saved-file?id=0/..%5C..%5Cx.json", None),
        "GET  tempo/report, no view": ("/api/tempo/report?bpm=91", None),
        "GET  loop, unknown path": ("/api/loop?path=/nope.mp3&start=1&len=2", None),
        "POST hold, bad kind": ("/api/hold", {"paths": [p], "kind": "mood", "on": True}),
        "POST hold, paths not a list": ("/api/hold", {"paths": 5, "kind": "genre", "on": True}),
        "POST cues/rescan, unknown path": ("/api/cues/rescan", {"path": "/nope.mp3"}),
        "POST cues/slots, unknown path": ("/api/cues/slots", {"path": "/nope.mp3", "plan": {}}),
        "POST cues/slots, plan not a dict": ("/api/cues/slots", {"path": p, "plan": 5}),
        "POST cues/approve, unknown path": ("/api/cues/approve", {"path": "/nope.mp3", "plan": None}),
        "POST jobs/start, unknown action": ("/api/jobs/start", {"action": "nope"}),
        "POST jobs/start, bad option": ("/api/jobs/start", {"action": "tempo_scan", "options": {"bpm": "x"}, "dry_run": True}),
        "POST samples/save, unknown path": ("/api/samples/save", {"path": "/nope.mp3", "start": 1, "len": 2, "folder": "x"}),
        "POST samples/reveal, outside": ("/api/samples/reveal", {"file": "/etc/hosts"}),
    }
    for name, (path, body) in cases.items():
        status, dt, n, js, ctype = r = call(path, body)
        ok = 400 <= status < 500 and isinstance(js, dict) and "error" in js
        print(f"  {'ok ' if ok else 'BAD'} {name:38} {status} {ctype.split(';')[0]:18} "
              f"{(js or {}).get('error', '') if isinstance(js, dict) else '(not JSON)'}"[:170])
    st = call("/api/hold", raw=b"not json", headers={"Content-Type": "application/json"})
    print(f"  {'ok ' if 400 <= st[0] < 500 and st[3] else 'BAD'} POST hold, body not JSON{'':17} {st[0]} {st[4].split(';')[0]}")


def hosts() -> None:
    """DNS rebinding: a site whose name points at 127.0.0.1 is 'same origin' to the browser, so the Origin check
    passes. Only the Host header tells. Anything but 127.0.0.1/localhost should be refused."""
    evil = {"Host": f"evil.example:{PORT}", "Origin": f"http://evil.example:{PORT}"}
    for name, path, body in [("GET  /api/tracks", "/api/tracks", None), ("GET  /api/setup", "/api/setup", None),
                             ("POST /api/hold (bad kind)", "/api/hold", {"paths": [], "kind": "x", "on": True}),
                             ("POST /api/jobs/start (unknown)", "/api/jobs/start", {"action": "nope"})]:
        status = call(path, body, headers=evil)[0]
        verdict = "refused" if status in (400, 403, 421) else "ANSWERED (rebinding works)"
        print(f"  Host evil.example  {name:32} {status}  {verdict}")
    print(f"  Origin elsewhere   POST /api/hold                   {call('/api/hold', {'paths': [], 'kind': 'x', 'on': True}, headers={'Origin': 'http://evil.example'})[0]}  (403 expected)")


def static() -> None:
    """index.html: script syntax (node), colours hard-coded outside the theme tokens, listeners added on every redraw."""
    html = web.PAGE.read_text(encoding="utf-8")
    script = html[html.index("<script>") + 8:html.rindex("</script>")]
    node = shutil.which("node")
    if node:
        res = subprocess.run([node, "--check", "-"], input=script, capture_output=True, text=True)
        print(f"  node --check: {'ok' if not res.returncode else res.stderr.strip()[:400]}")
    else:
        print("  node not installed: syntax not checked")
    css = html[html.index("<style>"):html.index("</style>")]
    body = re.sub(r":root[^{]*\{[^}]*\}", "", css)  # theme tokens are fine
    rules = re.findall(r"([^{}]+)\{([^}]*)\}", body)
    hard = [(sel.strip().split("\n")[-1][:60], c) for sel, decl in rules for c in re.findall(r"#[0-9a-fA-F]{3,6}\b", decl)
            if not re.search(r"wave|playhead|pads|padx|#tip", sel)]
    print(f"  CSS hex colours outside :root and the (always dark) waveform/pads: {len(hard)}")
    for sel, c in hard:
        print(f"    {c:8} {sel}")
    inline = re.findall(r"(?:style=\"[^\"]*|color:)\s*(#[0-9a-fA-F]{3,6})", script)
    print(f"  hex colours in the script's markup (inline styles, legend swatches): {sorted(set(inline))}")
    for m in re.finditer(r"\b(window|document)\.addEventListener\(\"(\w+)\"|^\s*addEventListener\(\"(\w+)\"", script, re.M):
        if "signal" in script[m.start():script.find("\n", m.start())]:
            continue  # removable (AbortController): fine inside a per-render function
        line = script[:m.start()].count("\n") + html[:html.index("<script>")].count("\n") + 1
        print(f"  global listener at index.html:{line}: {m.group(0).strip()}")
    print("  (a global listener inside a function that runs per render, e.g. drawWave, piles up)")


def _chrome() -> str | None:
    return next((p for p in ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                             "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                             shutil.which("chromium") or "", shutil.which("google-chrome") or ""] if p and Path(p).exists()), None)


PAGES = ["overview", "tracks", "hype", "colours", "tempo", "mixes", "files", "setup"]


def overflow() -> None:
    """Phone width (390 px): pages that scroll sideways, and the elements sticking out (headless Chrome + node)."""
    chrome, node = _chrome(), shutil.which("node")
    if not chrome or not node:
        print("  needs Chrome/Brave/Chromium and node 22+")
        return
    track = urllib.parse.quote(_sample_tracks(1)[0], safe="")
    subprocess.run([node, str(Path(__file__).with_name("overflow.mjs")), chrome, BASE, "390", *PAGES, f"tracks/{track}"],
                   timeout=300)


def shots(out: str | None = None) -> None:
    """Screenshots of every tab (and one track's panel) in light and dark, desktop and phone width (390, emulated)."""
    chrome, node = _chrome(), shutil.which("node")
    if not chrome or not node:
        print("  needs Chrome/Brave/Chromium and node 22+")
        return
    d = Path(out or "/tmp/audit-web-shots")
    d.mkdir(parents=True, exist_ok=True)
    pages = [*PAGES, "tracks/" + urllib.parse.quote(_sample_tracks(1)[0], safe="")]
    for scheme in ("light", "dark"):
        for w in ("1400", "390"):  # whole pages: the panel's cues are far down
            subprocess.run([node, str(Path(__file__).with_name("overflow.mjs")), chrome, BASE, w, "--shots", str(d),
                            "--scheme", scheme, *pages], timeout=600)


def export() -> None:
    """The snapshot (Export button / --export): time and size."""
    t = time.perf_counter()
    html = web.snapshot([web.LOGS], progress=lambda m: None)
    print(f"  snapshot: {time.perf_counter() - t:.0f} s, {len(html) / 1e6:.1f} MB")


CHECKS = {"endpoints": endpoints, "tracks": tracks, "errors": errors, "hosts": hosts, "static": static, "overflow": overflow, "shots": shots, "export": export}

if __name__ == "__main__":
    args = sys.argv[1:]
    names = [a for a in args if a in CHECKS] or [k for k in CHECKS if k not in ("shots", "export")]
    extra = [a for a in args if a not in CHECKS]
    print(f"data: {collection.NATIVE_DB}")
    _start()
    for n in names:
        print(f"\n== {n}: {CHECKS[n].__doc__.splitlines()[0]}")
        try:
            CHECKS[n](*extra) if n == "shots" else CHECKS[n]()
        except Exception as e:  # one check failing shouldn't hide the others
            print(f"  FAILED: {type(e).__name__}: {e}")
