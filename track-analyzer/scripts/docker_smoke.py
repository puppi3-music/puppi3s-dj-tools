"""Smoke test in the Docker test image (`docker compose run --rm test`, and CI): the real models on the synthetic
library from make_fixtures.py, end to end.

  1. scan --traktor: every track analysed, BPM and key as made, Traktor's BPM and key read; CNN key (`keys`)
  2. traktor: refused without "Traktor is closed", written with it, undone byte for byte
  3. web: answers, asks the page to confirm a Traktor job, refuses a foreign Host header

Exits 1 on the first failure. Runs only in the container (it writes the fixtures' collection.nml).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MUSIC = Path("/fixtures/music")
NML = next(Path.home().glob("Documents/Native Instruments/Traktor*/collection.nml"))
HOME = Path(os.environ["TRACK_ANALYZER_HOME"])
PORT = 8799


def step(text: str) -> None:
    print(f"\n== {text}", flush=True)


def fail(text: str) -> None:
    print(f"FAIL: {text}", flush=True)
    sys.exit(1)


def cli(*args: str, closed: bool = False, ok: bool = True) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "TRACK_ANALYZER_TRAKTOR_CLOSED"}
    if closed:
        env["TRACK_ANALYZER_TRAKTOR_CLOSED"] = "1"
    p = subprocess.run(["dj-track-analyzer", *args], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if ok and p.returncode:
        fail(f"{' '.join(args)} exited {p.returncode}:\n{p.stderr[-3000:]}")
    return p


def request(path: str, body: dict | None = None, host: str = f"127.0.0.1:{PORT}") -> tuple[int, dict]:
    headers = {"Host": host}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw.decode(errors="replace")}


def main() -> None:
    expected = json.loads((MUSIC / "expected.json").read_text())

    step("scan --traktor")
    cli("scan", "--traktor")
    con = sqlite3.connect(HOME / "collection.db")
    rows = {r[0]: r[1:] for r in con.execute("SELECT path, bpm, key, traktor_bpm, error FROM tracks")}
    for path, want in expected.items():
        if path not in rows:
            fail(f"not scanned: {path}")
        bpm, key, traktor_bpm, error = rows[path]
        print(f"  {Path(path).name}: BPM {bpm} (made {want['bpm']}), key {key} (made {want['key']}), Traktor BPM {traktor_bpm}")
        if error:
            fail(f"{path}: {error}")
        if bpm != want["bpm"] or traktor_bpm != want["bpm"]:
            fail(f"{path}: BPM {bpm} / Traktor {traktor_bpm}, made at {want['bpm']}")
        if key != want["key"]:
            fail(f"{path}: key {key}, made in {want['key']}")

    step("keys: MusicalKeyCNN's key")
    cnn = {r[0]: r[1:] for r in con.execute("SELECT path, cnn_camelot, cnn_confidence FROM tracks")}
    for path in expected:
        cam, conf = cnn[path]
        print(f"  {Path(path).name}: CNN {cam} ({conf})")
        if not cam or conf is None:
            fail(f"{path}: scan left no CNN key ({cam}, {conf})")
    out = json.loads(cli("keys", "--json").stdout)
    if out["this_run"]["analyzed"] or out["tracks_with_cnn_key"] != len(expected):
        fail(f"keys: {json.dumps(out)[:400]}")
    print(f"  CNN vs Essentia: {out['cnn_against_essentia']}")

    step("traktor: refused until Traktor is said to be closed")
    before = NML.read_bytes()
    p = cli("traktor", ok=False)
    if p.returncode == 0 or "container" not in p.stderr:
        fail(f"traktor wrote without confirmation (exit {p.returncode}):\n{p.stderr[-1500:]}")
    if NML.read_bytes() != before:
        fail("collection.nml changed although the write was refused")
    print("  refused:", p.stderr.strip().splitlines()[-1][:200])

    step("traktor with TRACK_ANALYZER_TRAKTOR_CLOSED=1, then --undo")
    out = json.loads(cli("traktor", closed=True).stdout)
    print(f"  colours_set {out.get('colours_set')}, smartlists {len(out.get('smartlists') or [])}")
    if NML.read_bytes() == before:
        fail("traktor changed nothing in collection.nml")
    cli("traktor", "--undo", closed=True)
    if NML.read_bytes() != before:
        fail("traktor --undo didn't restore collection.nml byte for byte")
    print("  restored byte for byte")

    step("web")
    server = subprocess.Popen(["dj-track-analyzer", "web", "--host", "0.0.0.0", "--port", str(PORT), "--no-open"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(120):
            try:
                status, _d = request("/api/libraries")
                break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.5)
        else:
            fail("the web page didn't start")
        print(f"  /api/libraries {status}")
        if status != 200:
            fail(f"/api/libraries answered {status}")
        status, d = request("/api/jobs/start", {"action": "traktor"})
        print(f"  Traktor job without confirmation: {status} {d.get('confirm')}")
        if status != 409 or d.get("confirm") != "traktor_closed":
            fail(f"expected 409 + confirm, got {status} {d}")
        status, _d = request("/api/libraries", host="rebind.example")
        print(f"  foreign Host header: {status}")
        if status != 400:
            fail(f"a foreign Host header got {status}")
    finally:
        server.terminate()
        server.wait(30)
    print("\nAll passed.")


if __name__ == "__main__":
    main()
