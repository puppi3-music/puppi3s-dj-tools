"""Setup and long work from the web page: what's done so far (`setup_status`), and the CLI's own commands run in the
background, one at a time (`start` / `stop` / `state`). A job is `python -m dj_track_analyzer.jobs <cli args>` in its
own process group, so it behaves exactly like the same command in Terminal (dry runs, backups, refusals while Traktor
is open), and Stop is Ctrl-C for it: scans save what they have and continue next time. The page can only start the
commands in ACTIONS, with options checked here, never arbitrary arguments."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import analysis, collection, traktor
from .analysis import HOME, log

BREW = analysis.find_tool("brew")
ERROR_MARK = "\x1eTRACK-ANALYZER-ERROR "  # the child's last line when it stops with a message (SystemExit)
PROGRESS = re.compile(r"^\s*\[(\d+)/(\d+)\s+~(.+?) left\]")  # "[12/470  ~31m left] …" from scan, tempo scan, cues, colours, Lexicon loads
KEEP_LINES = 3000


def _tool(name: str) -> str | None:
    return analysis.find_tool(name)


def _bpm(o: dict) -> str:
    bpm = float(o.get("bpm") or 145)
    if not 60 <= bpm <= 250:
        raise ValueError("BPM must be between 60 and 250")
    return f"{bpm:g}"


def _folders(o: dict) -> list[str]:
    out = []
    for f in o.get("folders") or []:
        p = Path(str(f).strip()).expanduser()
        if not str(f).strip():
            continue
        if not p.is_absolute() or not p.exists():
            raise ValueError(f"No such folder: {f} (give the full path, e.g. ~/Music/Tracks)")
        out.append(str(p.resolve()))
    return out


def _scan(o: dict) -> list[str]:
    args = ["scan", *(["--traktor"] if o.get("traktor") else []), *_folders(o)]
    if len(args) == 1:
        raise ValueError("Pick your Traktor collection, a folder, or both")
    return args


def _tool_install(o: dict) -> list[str]:
    if o.get("tool") not in ("ffmpeg", "rubberband"):
        raise ValueError("unknown tool")
    if not BREW:
        raise ValueError(f"Homebrew isn't installed. In a terminal: {analysis.install_hint(o['tool'])}")
    return [BREW, "install", o["tool"]]


# name: (label, args from options, writes = changes files / Traktor / Lexicon, so the page previews it first)
ACTIONS = {
    "setup": ("Download the models", lambda o: ["setup", *(["--no-maest"] if o.get("no_maest") else [])], False),
    "scan": ("Scan your music", _scan, False),
    "hype": ("Score hype", lambda o: ["hype", "--limit", "1", "--json", "--native"], False),
    "colors_train": ("Learn your colours", lambda o: ["colors", "train"], False),
    "cues_train": ("Learn your cue points", lambda o: ["cues", "train"], False),
    "cues_scan": ("Listen to the other tracks for cues", lambda o: ["cues", "scan"], False),
    "tempo_scan": ("Scan the tempo view", lambda o: ["tempo", "scan", "--bpm", _bpm(o)], False),
    "regenre": ("Genres again from the saved scores", lambda o: ["regenre"], False),
    "install": ("Install a tool", _tool_install, False),
    "tag": ("Write genres into your files' tags", lambda o: ["tag", *(["--multi"] if o.get("multi", True) else [])], True),
    "untag": ("Put your files' original tags back", lambda o: ["untag"], True),
    "traktor": ("Genres, colours and Smartlists into Traktor",
                lambda o: ["traktor", *([] if o.get("colours", True) else ["--no-colors"])], True),
    "traktor_undo": ("Undo the last change to Traktor", lambda o: ["traktor", "--undo"], True),
    "hype_traktor": ("Hype into Traktor's Comment 2", lambda o: ["hype", "--traktor", "--native"], True),
    "hype_remove": ("Take hype out of Comment 2", lambda o: ["hype", "--traktor", "--remove", "--native"], True),
    "colors_apply": ("Colour new and uncoloured tracks in Traktor", lambda o: ["colors", "apply"], True),
    "colors_recolour": ("Recolour the clearest disagreements in Traktor", lambda o: ["colors", "review", "--apply", "--yes"], True),
    "tempo_load": ("Switch Traktor to the tempo view",
                   lambda o: ["tempo", "load", "--bpm", _bpm(o), *(["--lexicon"] if o.get("lexicon") else [])], True),
    "tempo_native": ("Switch Traktor back to normal", lambda o: ["tempo", "native", *(["--lexicon"] if o.get("lexicon") else [])], True),
}
UNDO = {"tag": "untag", "traktor": "traktor_undo", "hype_traktor": "traktor_undo", "colors_apply": "traktor_undo",
        "colors_recolour": "traktor_undo", "tempo_load": "tempo_native"}
NO_DRY_RUN = {"traktor_undo", "install"}  # traktor --undo has no preview; the page asks first

_lock = threading.Lock()
_job: dict | None = None  # the running or last job
_proc: subprocess.Popen | None = None
_seq = 0
on_finish = []  # callbacks after a job ends (the web page's caches)


def command_line(args: list[str]) -> str:
    """The same command as you'd type it in Terminal (shown on the page, so it's no mystery what runs)."""
    import shlex

    if args and args[0] == BREW:
        return " ".join(["brew", *args[1:]])
    return "uv run dj-track-analyzer " + " ".join(shlex.quote(a) for a in args)


def busy() -> bool:
    return bool(_job and _job["running"])


def start(action: str, options: dict | None = None, dry_run: bool = False) -> dict:
    global _job, _proc, _seq
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    label, build, writes = ACTIONS[action]
    args = build(options or {})
    if dry_run:
        if not writes or action in NO_DRY_RUN:
            raise ValueError(f"{label} has no preview")
        args.append("--dry-run")
    with _lock:
        if busy():
            raise ValueError(f"Busy with: {_job['label']}. Wait for it or stop it first.")
        if args[0] == BREW:
            cmd = args
        else:
            cmd = [sys.executable, "-m", "dj_track_analyzer.jobs", *args]
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "HOMEBREW_NO_AUTO_UPDATE": "1"}
        _seq += 1
        _job = {"id": _seq, "action": action, "label": label, "command": command_line(args), "dry_run": dry_run,
                "writes": writes, "undo": UNDO.get(action), "started": time.time(), "ended": None, "running": True,
                "returncode": None, "lines": [], "dropped": 0, "progress": None, "live": None, "result": None, "error": None,
                "stopping": False}
        _proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env=env, cwd=str(Path(__file__).resolve().parents[2]), start_new_session=True)
        job, proc = _job, _proc
    out: list[str] = []
    readers = [threading.Thread(target=_read_err, args=(job, proc), daemon=True),
               threading.Thread(target=_read_out, args=(job, proc, out, args[0] == BREW), daemon=True)]
    for t in readers:
        t.start()
    threading.Thread(target=_wait, args=(job, proc, out, readers), daemon=True).start()
    log(f"Job started: {job['command']}")
    return state()


def _add(job: dict, line: str) -> None:
    job["lines"].append(line)
    if len(job["lines"]) > KEEP_LINES:
        del job["lines"][0]
        job["dropped"] += 1


def _read_err(job: dict, proc: subprocess.Popen) -> None:
    """Log lines; a line that ends in a carriage return (curl's download meter) is shown as `live` until replaced."""
    buf, after_cr = b"", False
    while chunk := proc.stderr.read1(65536):
        buf += chunk
        while (m := re.search(rb"[\r\n]", buf)):
            line, cr, buf = buf[:m.start()].decode("utf-8", "replace"), buf[m.start():m.end()] == b"\r", buf[m.end():]
            if cr:
                job["live"] = line.strip() or job.get("live")
            elif not (after_cr and not line):  # the newline after a meter's last update
                job["live"] = None
                line, _, err = line.partition(ERROR_MARK)  # the mark can follow a meter that ended without a newline
                if err:
                    job["error"] = json.loads(err)
                if (m2 := PROGRESS.match(line)):
                    job["progress"] = {"done": int(m2[1]), "total": int(m2[2]), "eta": m2[3]}
                if line or not err:
                    _add(job, line)
            after_cr = cr
    if buf.strip():
        _add(job, buf.decode("utf-8", "replace"))


def _read_out(job: dict, proc: subprocess.Popen, out: list[str], text: bool) -> None:
    """The CLI prints its JSON result on stdout; other programs' (brew's) output is just more log."""
    for raw in iter(proc.stdout.readline, b""):
        line = raw.decode("utf-8", "replace")
        if text:
            _add(job, line.rstrip("\n"))
        else:
            out.append(line)


def _wait(job: dict, proc: subprocess.Popen, out: list[str], readers: list[threading.Thread]) -> None:
    rc = proc.wait()
    for t in readers:
        t.join(timeout=5)
    text = "".join(out)
    i = text.find("{")
    if i >= 0:
        try:
            job["result"] = json.JSONDecoder().raw_decode(text[i:])[0]
        except ValueError:
            pass
    if rc and not job["error"]:
        job["error"] = "Stopped." if job["stopping"] else next(
            (ln for ln in reversed(job["lines"]) if ln.strip()), f"Ended with exit code {rc}")
    job.update(returncode=rc, ended=time.time(), running=False, live=None, traktor_running=traktor.traktor_running())
    log(f"Job ended ({'ok' if not rc else job['error']}): {job['command']}")
    for fn in on_finish:
        try:
            fn(job)
        except Exception as err:  # a cache refresh must never break the job runner
            log(f"  after-job refresh failed: {err}")


def stop() -> dict:
    """Ctrl-C for the job (scans save what they have); harder if it hasn't stopped after 20 s."""
    proc, job = _proc, _job
    if not (proc and job and job["running"]):
        return state()
    job["stopping"] = True
    try:
        os.killpg(proc.pid, signal.SIGINT)
    except ProcessLookupError:
        return state()

    def harder():
        time.sleep(20)
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    threading.Thread(target=harder, daemon=True).start()
    return state()


def stop_on_exit() -> None:
    """When the web server stops, stop its job as Ctrl-C would (it's resumable), rather than leave it running unseen."""
    proc = _proc
    if proc and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGINT)
            proc.wait(timeout=15)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass


def state(since: int = 0) -> dict:
    """The running or last job, with its log lines from line number `since` on."""
    j = _job
    if not j:
        return {"job": None}
    first = j["dropped"]
    lines = j["lines"][max(0, since - first):]
    return {"job": {**{k: v for k, v in j.items() if k != "lines"}, "line_from": max(since, first),
                    "line_to": first + len(j["lines"]), "lines": lines,
                    "elapsed": (j["ended"] or time.time()) - j["started"]}}


# ----------------------------------------------------------------------------
# What's set up so far
# ----------------------------------------------------------------------------

_tk_cache: dict = {"key": None, "data": None}


def _traktor_side(nml: Path) -> dict:
    """Tracks, colours and cues in Traktor's collection (read once per change of collection.nml)."""
    from . import cues

    key = (str(nml), nml.stat().st_mtime)
    if _tk_cache["key"] == key:
        return _tk_cache["data"]
    _, files, missing = traktor.collection_tracks(str(nml))
    skip = traktor.recording_dirs(str(nml))
    files = [str(f) for f in files if not collection.sync_conflict(f)
             and not any(str(f).startswith(d + "/") for d in skip)]
    text = nml.read_text(encoding="utf-8")
    coloured = len(re.findall(r'<INFO\b[^>]*\sCOLOR="[1-7]"', text))
    g = cues.grids(str(nml))
    each = cues._one_path_each(g)
    cued = sum(sum(c["type"] == "cue" for c in d["cues"]) >= cues.MIN_CUES for _p, d in each)
    data = {"files": files, "missing": missing, "coloured": coloured, "with_grid": len(each), "cued": cued}
    _tk_cache.update(key=key, data=data)
    return data


def _changed(path: str, size, mtime) -> bool:
    """A scanned file that's different on disk now (edited by another tagger, Traktor or a sync): `tag` skips it
    until it's scanned again. A file that's gone doesn't count."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    return (st.st_size, int(st.st_mtime)) != (size, mtime)


def setup_status() -> dict:
    """Each step from a fresh install to everything in Traktor: done or not, and what it needs."""
    import sqlite3

    from . import cues, tempo

    st: dict = {"home": str(HOME), "busy": busy(), "brew": bool(BREW), "traktor_running": traktor.traktor_running(),
                "actions": {k: v[0] for k, v in ACTIONS.items()}, "undo": UNDO}
    st["models"] = {"ready": analysis.models_present(), "maest": analysis.maest_present(),
                    "folder": str(analysis.MODELS_DIR)}
    st["tools"] = {"ffmpeg": bool(_tool("ffmpeg")), "rubberband": bool(_tool("rubberband"))}
    st["mac"] = sys.platform == "darwin"
    st["install_hint"] = {t: analysis.install_hint(t) for t in st["tools"]}  # shown when there's no Homebrew
    try:
        nml = traktor.find_collection(None)
        st["traktor"] = {"collection": str(nml), **{k: v for k, v in _traktor_side(nml).items() if k != "files"}}
        tk_files = set(_traktor_side(nml)["files"])
    except SystemExit as e:
        st["traktor"] = {"collection": None, "why": str(e.code or e)}
        tk_files = set()
    except (OSError, ValueError) as e:
        st["traktor"] = {"collection": None, "why": f"Couldn't read Traktor's collection: {e}"}
        tk_files = set()
    scanned, hyped, errors, tagged, predicted, changed = set(), 0, 0, 0, 0, 0
    if collection.NATIVE_DB.exists():
        with sqlite3.connect(collection.NATIVE_DB) as con:
            have = {r[1] for r in con.execute("PRAGMA table_info(tracks)")}
            if "path" in have:
                rows = con.execute("SELECT path, size, mtime FROM tracks").fetchall()
                scanned = {r[0] for r in rows}
                changed = sum(_changed(*r) for r in rows)
                hyped = con.execute("SELECT COUNT(*) FROM tracks WHERE hype IS NOT NULL").fetchone()[0]
                errors = con.execute("SELECT COUNT(*) FROM tracks WHERE error IS NOT NULL AND error NOT LIKE 'tags:%'").fetchone()[0]
                predicted = con.execute("SELECT COUNT(*) FROM tracks WHERE color IS NOT NULL").fetchone()[0]
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='original_tags'").fetchone():
                tagged = con.execute("SELECT COUNT(*) FROM original_tags").fetchone()[0]
    st["scan"] = {"tracks": len(scanned), "errors": errors, "hype": hyped, "traktor_not_scanned": len(tk_files - scanned),
                  "changed_since_scan": changed,
                  "traktor_tracks": len(tk_files)}
    stamp = lambda p: time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)) if p.exists() else None  # noqa: E731
    st["colours"] = {"model": stamp(analysis.COLOR_MODEL), "predicted": predicted}
    st["cues"] = {"model": stamp(cues.MODEL), "approved": len(cues.plans()) if collection.NATIVE_DB.exists() else 0}
    st["tags"] = {"tagged_files": tagged}
    backups = []
    if st["traktor"].get("collection"):
        backups = sorted(Path(st["traktor"]["collection"]).parent.glob(traktor.BACKUP_PREFIX + "*"))
        backups = [b for b in backups if not b.name.endswith((".restored", traktor.WRITTEN_SUFFIX))]
    st["traktor_backup"] = backups[-1].name if backups else None
    loaded = tempo.loaded()
    st["tempo"] = {"views": [float(d.name[3:]) for d in sorted(HOME.glob("at-*")) if (d / "collection.db").exists()
                             and re.fullmatch(r"at-[\d.]+", d.name)],
                   "loaded": loaded["bpm"] if loaded else None}
    from . import lexicon

    st["tempo"]["lexicon_loaded"] = bool(lexicon.loaded())
    return st


if __name__ == "__main__":
    # A job: the CLI with these arguments; its message on SystemExit goes to the page as one marked line.
    sys.argv = ["dj-track-analyzer", *sys.argv[1:]]
    from .cli import main

    try:
        main()
    except SystemExit as e:
        if isinstance(e.code, str):
            print(ERROR_MARK + json.dumps(e.code), file=sys.stderr, flush=True)
            sys.exit(1)
        raise
    except KeyboardInterrupt:
        print(ERROR_MARK + json.dumps("Stopped."), file=sys.stderr, flush=True)
        sys.exit(130)
