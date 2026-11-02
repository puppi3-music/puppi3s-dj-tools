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
# "[12/470  ~31m left] what" from scan, tempo scan, cues, colours, Lexicon loads; "[3/17] what" without a time
PROGRESS = re.compile(r"^\s*\[(\d+)/(\d+)(?:\s+~(.+?) left)?\]\s*(.*)")
STAGE = re.compile(r"^\[\s*[\d.]+s\] (.+)")  # "[ 25.1s] measuring loudness": analyze_file's stages
# A mix analysis has no [n/total] lines; its stages (in analyze_file's order, roughly even in time) stand in for them
STEPS = {"analyze_mix": ("decoding", "measuring loudness", "running the style model", "moods, instruments",
                         "building the timeline", "checking against the tracklist")}
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


MIX_DIR = Path(__file__).resolve().parents[2] / "logs"  # where analyze_mix saves (the web page's first --logs folder)


def _analyze_mix(o: dict) -> list[str]:
    if not str(o.get("file") or "").strip():
        raise ValueError("Pick a recording")
    p = Path(str(o["file"]).strip()).expanduser()
    if not p.is_absolute() or not p.is_file():
        raise ValueError(f"No such file: {o.get('file')} (give the full path)")
    if p.suffix.lower() not in analysis.AUDIO_EXTS:
        raise ValueError(f"{p.name} isn't an audio file")
    MIX_DIR.mkdir(parents=True, exist_ok=True)
    out = MIX_DIR / f"{p.stem}.json"  # analysed before: replaced by the new result
    start = str(o.get("start") or "").strip()  # when the recording began, for a name that doesn't say
    if start and not re.fullmatch(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d(:\d\d)?", start):
        raise ValueError(f"Start time as YYYY-MM-DD HH:MM(:SS), not {start!r}")
    tl = o.get("tracklist", True)
    return ["analyze", str(p.resolve()), *(["--tracklist"] if tl else []), *(["--start", start] if tl and start else []),
            *(["--highlights"] if o.get("highlights", True) else []), "--json-only", "--out", str(out)]


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
    "colors_train": ("Learn your colours", lambda o: ["colors", "train", *(["--refresh-views"] if o.get("views") else [])], False),
    "cues_train": ("Learn your cue points", lambda o: ["cues", "train"], False),
    "cues_scan": ("Listen to the other tracks for cues", lambda o: ["cues", "scan"], False),
    "grids_check": ("Check the beat grids", lambda o: ["grids", "check"], False),
    "tempo_scan": ("Scan the tempo view", lambda o: ["tempo", "scan", "--bpm", _bpm(o)], False),
    "regenre": ("Genres again from the saved scores", lambda o: ["regenre"], False),
    "analyze_mix": ("Analyse a mix", _analyze_mix, False),
    "install": ("Install a tool", _tool_install, False),
    "tag": ("Write genres into your files' tags", lambda o: ["tag", *(["--multi"] if o.get("multi", True) else [])], True),
    "untag": ("Put your files' original tags back", lambda o: ["untag"], True),
    "traktor": ("Genres, colours and Smartlists into Traktor",
                lambda o: ["traktor", *([] if o.get("colours", True) else ["--no-colors"])], True),
    "traktor_undo": ("Undo the last change to Traktor", lambda o: ["traktor", "--undo"], True),
    "hype_traktor": ("Hype into Traktor's Comment", lambda o: ["hype", "--traktor", "--native"], True),
    "hype_remove": ("Take hype out of Comment", lambda o: ["hype", "--traktor", "--remove", "--native"], True),
    "colors_apply": ("Colour new and uncoloured tracks in Traktor", lambda o: ["colors", "apply"], True),
    "colors_recolour": ("Recolour the clearest disagreements in Traktor", lambda o: ["colors", "review", "--apply", "--yes"], True),
    "colors_write_marked": ("Write the colours you marked", lambda o: ["colors", "review", "--apply", "--yes", "--planned"], True),
    "tempo_load": ("Switch Traktor to the tempo view",
                   lambda o: ["tempo", "load", "--bpm", _bpm(o), *(["--lexicon"] if o.get("lexicon") else [])], True),
    "tempo_native": ("Switch Traktor back to normal", lambda o: ["tempo", "native", *(["--lexicon"] if o.get("lexicon") else [])], True),
}
UNDO = {"tag": "untag", "traktor": "traktor_undo", "hype_traktor": "traktor_undo", "colors_apply": "traktor_undo",
        "colors_recolour": "traktor_undo", "colors_write_marked": "traktor_undo", "tempo_load": "tempo_native"}
NO_DRY_RUN = {"traktor_undo", "install"}  # traktor --undo has no preview; the page asks first
TOUCHES_TRAKTOR = {"traktor", "traktor_undo", "hype_traktor", "hype_remove", "colors_apply", "colors_recolour",
                   "colors_write_marked", "tempo_load", "tempo_native"}  # = the page's: in a container these need "Traktor is closed" first

_lock = threading.Lock()
_job: dict | None = None  # the running or last job
_proc: subprocess.Popen | None = None
_seq = 0
_queue: list[tuple[str, str, list[str]]] = []  # mixes to analyse after the running one: (action, label, args)
_ended: list[dict] = []  # the last jobs that ended, without their log: a queued mix starts at once, so the page
ENDED_KEEP = 10          # may never see the one before it as ended
on_finish = []  # callbacks after a job ends (the web page's caches)


def command_line(args: list[str]) -> str:
    """The same command as you'd type it in Terminal (shown on the page, so it's no mystery what runs)."""
    import shlex

    if args and args[0] == BREW:
        return " ".join(["brew", *args[1:]])
    return "uv run dj-track-analyzer " + " ".join(shlex.quote(a) for a in args)


def busy() -> bool:
    return bool(_job and _job["running"] or _queue)


def start(action: str, options: dict | None = None, dry_run: bool = False) -> dict:
    """Run a job. analyze_mix takes several recordings (`files`): the first runs now, the rest one after another;
    a mix asked for while another is analysed waits its turn too."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    label, build, writes = ACTIONS[action]
    options = options or {}
    many = action == "analyze_mix" and isinstance(options.get("files"), list)
    runs = [build({**options, "file": f}) for f in options["files"]] if many else [build(options)]
    if not runs:
        raise ValueError("Pick a recording")
    if action == "analyze_mix":
        runs = [(f"Analysing {Path(a[1]).name}", a) for a in runs]
    else:
        runs = [(label, runs[0])]
    if dry_run:
        if not writes or action in NO_DRY_RUN:
            raise ValueError(f"{label} has no preview")
        runs[0][1].append("--dry-run")
    with _lock:
        if busy():
            if action != "analyze_mix" or _job["action"] != "analyze_mix":
                now = _job["label"] if _job["running"] else _queue[0][1]  # between two queued mixes: the next one
                raise ValueError(f"Busy with: {now}. Wait for it or stop it first.")
            _queue.extend((action, lb, a) for lb, a in runs)
            log(f"Job queued: {', '.join(lb for lb, _ in runs)}")
            return state()
        _queue.extend((action, lb, a) for lb, a in runs[1:])
        _launch(action, *runs[0], dry_run)
    return state()


def _launch(action: str, label: str, args: list[str], dry_run: bool = False) -> None:
    """Start the job's process (holding _lock, nothing running)."""
    global _job, _proc, _seq
    writes = ACTIONS[action][2]
    if args[0] == BREW:
        cmd = args
    else:
        cmd = [sys.executable, "-m", "dj_track_analyzer.jobs", *args]
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "HOMEBREW_NO_AUTO_UPDATE": "1",
           "PYTHONUTF8": "1"}  # Windows: the job's output and files in UTF-8, as read here (not the ANSI code page)
    if action in TOUCHES_TRAKTOR and not dry_run and traktor.running_state() is None:
        traktor.check_closed()  # ask the page now, not the job later
        env[traktor.CLOSED_ENV] = "1"
    _seq += 1
    _job = {"id": _seq, "action": action, "label": label, "command": command_line(args), "dry_run": dry_run,
            "writes": writes, "undo": UNDO.get(action), "started": time.time(), "ended": None, "running": True,
            "returncode": None, "lines": [], "dropped": 0, "progress": None, "live": None, "result": None, "error": None,
            "stopping": False, "stage": None,
            "steps": [x for x in STEPS.get(action, ()) if "--tracklist" in args or "tracklist" not in x]}
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
                    job["progress"] = {"done": int(m2[1]), "total": int(m2[2]), "eta": m2[3], "what": " ".join(m2[4].split()) or None}
                elif (m2 := STAGE.match(line)):
                    job["stage"] = m2[1]
                    if (i := next((i for i, x in enumerate(job["steps"]) if m2[1].startswith(x)), None)) is not None:
                        job["progress"] = {"done": i, "total": len(job["steps"]), "eta": None, "steps": True}
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
    _ended.append({k: job[k] for k in ("id", "action", "label", "command", "dry_run", "writes", "undo", "returncode",
                                       "error", "traktor_running")} | {"running": False, "elapsed": job["ended"] - job["started"]})
    del _ended[:-ENDED_KEEP]
    for fn in on_finish:
        try:
            fn(job)
        except Exception as err:  # a cache refresh must never break the job runner
            log(f"  after-job refresh failed: {err}")
    with _lock:  # the next queued mix, also after one failed; Stop empties the queue
        while _queue and not (_job and _job["running"]):
            action, label, args = _queue.pop(0)
            try:
                _launch(action, label, args)
            except Exception as err:  # e.g. the recording was moved meanwhile: on to the next
                log(f"  queued job didn't start ({label}): {err}")


def stop() -> dict:
    """Ctrl-C for the job (scans save what they have); harder if it hasn't stopped after 20 s."""
    proc, job = _proc, _job
    _queue.clear()
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
    return {"job": {**{k: v for k, v in j.items() if k != "lines"}, "queued": [lb for _, lb, _ in _queue],
                    "ended": _ended, "line_from": max(since, first),
                    "line_to": first + len(j["lines"]), "lines": lines,
                    "elapsed": (j["ended"] or time.time()) - j["started"],
                    # what it's doing now, for the page's bar when there's no progress line to show
                    "last": next((ln.strip() for ln in reversed(j["lines"]) if ln.strip()), None)}}


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
             and not any(collection.under(str(f), d) for d in skip)]
    text = nml.read_text(encoding="utf-8")
    coloured = len(re.findall(r'<INFO\b[^>]*\sCOLOR="[1-7]"', text))
    g = cues.grids(str(nml))
    each = cues._one_path_each(g)
    cued = sum(sum(c["type"] == "cue" for c in d["cues"]) >= cues.MIN_CUES for _p, d in each)
    data = {"files": files, "missing": missing, "coloured": coloured, "with_grid": len(each), "cued": cued}
    _tk_cache.update(key=key, data=data)
    return data


_new_cache: dict = {"key": None, "data": None}


def _new_labels(nml: Path) -> tuple:
    """(colours, cues): what retraining your own model would learn that it hasn't (colors/cues.new_labels), or None
    each with a starter or an older model. Read again when the collection, a model or the database changes."""
    from . import colors, cues

    mt = lambda p: p.stat().st_mtime if p.exists() else None  # noqa: E731
    key = (str(nml), mt(nml), mt(analysis.COLOR_MODEL), mt(cues.MODEL), mt(collection.NATIVE_DB))
    if _new_cache["key"] != key:
        _nml, tk_colours, _n = colors.read_traktor_colors(str(nml))
        _new_cache.update(key=key, data=(colors.new_labels(tk_colours), cues.new_labels(cues.grids(str(nml)))))
    return _new_cache["data"]


def to_learn() -> dict:
    """What retraining your colour and cue models would learn that they haven't (the Colours and Tracks tabs offer it):
    {colours, cues}, each {added, changed, removed}, or None (no Traktor, a starter or an older model, a tempo view loaded)."""
    try:
        return dict(zip(("colours", "cues"), _new_labels(Path(traktor.find_collection(None)))))
    except (SystemExit, OSError, ValueError) as e:
        log(f"New-labels count skipped: {e}")
        return {"colours": None, "cues": None}


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
    st["container"] = traktor.CONTAINER  # the page says Docker is experimental
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
    st["colours"] = {"model": stamp(analysis.COLOR_MODEL), "starter": analysis.STARTER_COLOR_MODEL.exists(),
                     "predicted": predicted}
    st["cues"] = {"model": stamp(cues.MODEL), "starter": cues.STARTER_MODEL.exists(),
                  "approved": len(cues.plans()) if collection.NATIVE_DB.exists() else 0}
    st["grids"] = {"checked": 0, "off": 0, "drift": 0, "approved": 0}
    if st["traktor"].get("collection"):
        from . import cues, gridfix

        try:
            st["colours"]["new"], st["cues"]["new"] = _new_labels(Path(st["traktor"]["collection"]))
        except (SystemExit, OSError) as e:  # e.g. a tempo view is loaded: its colours aren't yours
            log(f"New-labels count skipped: {e}")
        try:
            g = cues.grids()
            res = gridfix.results(g=g)
            st["grids"] = {"tracks": len(cues._one_path_each(g)), "checked": len(res),
                           "off": sum(r["state"] == "off" for r in res.values()),
                           "drift": sum(r["state"] == "drift" for r in res.values()),
                           "approved": len(gridfix.plans()) if collection.NATIVE_DB.exists() else 0}
        except (SystemExit, OSError) as e:
            log(f"Beat grid check status skipped: {e}")
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
