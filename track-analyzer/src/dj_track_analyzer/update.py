"""Updating the app from the web page (Setup tab) or `update`: is a newer release out, install it, go back.

An install from a release zip gets the newer release's zip from GitHub, checked by its SHA-256, and writes only the
app's own files: `data/` (scans, models, training, settings), `logs/` and `.venv` are never touched. Every file it
replaces or removes is copied to data/app-backups/<from>-to-<to>-<time>/ first, with a list of what changed, so
`update --undo` puts the old version back; a failure halfway puts it back at once. Files are replaced through a
temporary file and a rename, so a launcher that is running keeps reading its old copy. In src/ (only the app's code
lives there) files the new release no longer has are removed; elsewhere nothing is deleted.

A git clone is updated with `git pull --ff-only` on main, only without local changes; undo goes back to the commit
it was on. In Docker the image is rebuilt instead.

Some releases need a step after updating (genres again, a rescan, new models): AFTER lists them by version, and the
Setup tab offers each as a button once, until you've done it or said it's fine (`pending_steps`).

After an update the web page restarts itself (`web.restart`): the launchers start it again when it stops with
RESTART_CODE, and install the new release's packages first."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from . import analysis
from .analysis import HOME, log

APP = Path(__file__).resolve().parents[2]  # the track-analyzer folder: pyproject.toml, launchers, src/
REPO = "puppi3-music/puppi3s-dj-tools"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
BACKUPS = HOME / "app-backups"
DOWNLOADS = HOME / "updates"
RESTART_CODE = 75  # the web page stopped to be started again (the launchers loop on it)
# never written or removed by an update, whatever a zip holds: your data, run output, Python's environment
PROTECTED = {"data", "data-docker", "logs", ".venv", ".git"}
# Steps a release needs after updating to it, for anyone coming from an older version: version → step keys (STEPS).
# Add a line when a release changes the genre list, the analysis or the models.
AFTER: dict[str, list[str]] = {}
STEPS = {
    "models": "Download the new analysis models",
    "regenre": "Work out the genres again (the genre list changed; seconds)",
    "rescan": "Analyse every track again (the analysis changed; about half an hour per 500 tracks)",
    "train": "Update the training of your colour scale",
}
FIRST_SEEN = "1.0.1"  # the last release before updates were tracked: an existing install without a record is this one


def version_key(v: str) -> tuple[int, ...]:
    """'1.10.2' → (1, 10, 2), for comparing; a leading 'v' is fine."""
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3]) or (0,)


def installed_version(app: Path = APP) -> str:
    text = (app / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise SystemExit("Can't tell which version this is: pyproject.toml has no version.")
    return m.group(1)


def install_kind(app: Path = APP) -> str:
    """'container' (Docker), 'git' (a clone) or 'zip' (a release zip, unpacked)."""
    from . import traktor

    if traktor.CONTAINER:
        return "container"
    if any((d / ".git").exists() for d in (app, app.parent)) and shutil.which("git"):
        return "git"
    return "zip"


# ----------------------------------------------------------------------------
# Is there a newer release
# ----------------------------------------------------------------------------

def _get(url: str, timeout: float = 15) -> bytes:
    """GitHub's answer; with Python, else curl (Python's certificates can be missing on a fresh Mac)."""
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "puppi3s-track-analyzer",
                                               "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as err:
        curl = shutil.which("curl")
        if not curl:
            raise
        p = subprocess.run([curl, "-fsSL", "--max-time", str(int(timeout)), "-H", "User-Agent: puppi3s-track-analyzer",
                            url], capture_output=True, check=False)
        if p.returncode:
            raise OSError(f"couldn't reach GitHub ({err})") from None
        return p.stdout


def releases() -> list[dict]:
    """The app's published releases (tags v…), newest first; the dsp-v… builds and drafts left out."""
    out = []
    for r in json.loads(_get(RELEASES_API)):
        tag = r.get("tag_name") or ""
        if r.get("draft") or r.get("prerelease") or not re.fullmatch(r"v\d+(\.\d+)*", tag):
            continue
        zips = [a for a in r.get("assets") or [] if a.get("name", "").endswith(".zip")]
        out.append({"version": tag[1:], "tag": tag, "notes": r.get("body") or "", "url": r.get("html_url"),
                    "published": (r.get("published_at") or "")[:10],
                    "zip": {"name": zips[0]["name"], "url": zips[0]["browser_download_url"], "size": zips[0].get("size"),
                            "sha256": (zips[0].get("digest") or "").removeprefix("sha256:") or None} if zips else None})
    return sorted(out, key=lambda r: version_key(r["version"]), reverse=True)


def check(app: Path = APP) -> dict:
    """What's installed, the newest release, whether it's newer, its notes (and those of any skipped in between),
    and whether this install can update itself (or why not)."""
    now = installed_version(app)
    kind = install_kind(app)
    st: dict = {"installed": now, "kind": kind, "checked": time.strftime("%Y-%m-%d %H:%M"), "page": RELEASES_PAGE}
    rel = releases()
    if not rel:
        return {**st, "latest": None, "newer": False}
    newest = rel[0]
    newer = [r for r in rel if version_key(r["version"]) > version_key(now)]
    st.update(latest=newest["version"], newer=bool(newer), url=newest["url"], published=newest["published"],
              notes=[{"version": r["version"], "notes": r["notes"], "url": r["url"]} for r in newer])
    st["can_update"], st["why_not"] = True, None
    if kind == "container":
        st["can_update"], st["why_not"] = False, "In Docker: build the image again (docker compose build) to update."
    elif kind == "git":
        why = _git_blocker(app)
        if why:
            st["can_update"], st["why_not"] = False, why
    elif not newest["zip"]:
        st["can_update"], st["why_not"] = False, f"Release {newest['tag']} has no zip to install."
    return st


# ----------------------------------------------------------------------------
# A release zip
# ----------------------------------------------------------------------------

def _download(rel: dict) -> Path:
    z = rel["zip"]
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    dest = DOWNLOADS / z["name"]
    if dest.exists() and z["sha256"] and _sha256(dest) == z["sha256"]:
        log(f"Already downloaded: {dest.name}")
        return dest
    dest.unlink(missing_ok=True)
    log(f"Downloading {z['url']}")
    if not analysis._fetch(z["url"], dest):
        raise SystemExit("Couldn't download the update. Check the internet connection and try again.")
    if z["sha256"]:
        got = _sha256(dest)
        if got != z["sha256"]:
            dest.unlink(missing_ok=True)
            raise SystemExit(f"The download isn't the file GitHub lists (SHA-256 {got}); nothing was changed.")
        log("Download checked (SHA-256 matches).")
    return dest


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def read_zip(path: Path) -> dict[str, tuple[zipfile.ZipInfo, str]]:
    """The release's files: {path inside the app folder: (zip entry, version it says)}. Refuses a zip that isn't one
    app folder, or that would write outside it."""
    with zipfile.ZipFile(path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
    tops = {i.filename.split("/", 1)[0] for i in infos}
    if len(tops) != 1:
        raise SystemExit("The update zip isn't one app folder; nothing was changed.")
    top = tops.pop() + "/"
    files: dict = {}
    for i in infos:
        rel = i.filename[len(top):]
        parts = rel.split("/")
        if (not rel or rel.startswith("/") or "\\" in rel or ".." in parts or ":" in parts[0]):
            raise SystemExit(f"The update zip has an unsafe path ({i.filename}); nothing was changed.")
        files[rel] = i
    if "pyproject.toml" not in files or not any(r.startswith("src/dj_track_analyzer/") for r in files):
        raise SystemExit("The update zip isn't Puppi3's Track Analyzer; nothing was changed.")
    return files


def _mode(info: zipfile.ZipInfo) -> int | None:
    m = (info.external_attr >> 16) & 0o777
    return m or None


def _app_files(app: Path, under: str) -> list[str]:
    """The app's own files under a folder (relative, '/'), without Python's caches."""
    root = app / under
    if not root.exists():
        return []
    return sorted(p.relative_to(app).as_posix() for p in root.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc" and ".egg-info" not in p.as_posix())


def plan(zip_path: Path, app: Path = APP) -> dict:
    """What installing this zip would do: {added, changed, removed} (relative paths) and the versions."""
    files = read_zip(zip_path)
    added, changed = [], []
    with zipfile.ZipFile(zip_path) as zf:
        new_version = re.search(r'^version\s*=\s*"([^"]+)"', zf.read(files["pyproject.toml"]).decode("utf-8"), re.MULTILINE)
        for rel, info in sorted(files.items()):
            if rel.split("/", 1)[0] in PROTECTED:
                continue
            dest = app / rel
            if not dest.exists():
                added.append(rel)
            elif dest.read_bytes() != zf.read(info):
                changed.append(rel)
    removed = [r for r in _app_files(app, "src") if r not in files]
    return {"from": installed_version(app), "to": new_version.group(1) if new_version else "?",
            "added": added, "changed": changed, "removed": removed}


def _replace(dest: Path, data: bytes, mode: int | None) -> None:
    """Write through a temporary file and a rename: a script running from the old file (a launcher) keeps it."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".update-part")
    tmp.write_bytes(data)
    if mode and sys.platform != "win32":
        os.chmod(tmp, mode)
    os.replace(tmp, dest)


def install_zip(zip_path: Path, app: Path = APP, backups: Path | None = None) -> dict:
    """Install a release zip over the app (see the module's notes). Returns what changed and where the backup is."""
    p = plan(zip_path, app)
    backups = backups or BACKUPS
    if not (p["added"] or p["changed"] or p["removed"]):
        return {**_counts(p), "changed": False, "note": "Every file is already the new release's."}
    bdir = backups / f"{p['from']}-to-{p['to']}-{time.strftime('%Y%m%d-%H%M%S')}"
    for rel in p["changed"] + p["removed"]:  # the old copies first: nothing is changed before they're all saved
        (bdir / "files" / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(app / rel, bdir / "files" / rel)
    record = {"kind": "zip", "from": p["from"], "to": p["to"], "at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "added": p["added"], "changed": p["changed"], "removed": p["removed"]}
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "update.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    files = read_zip(zip_path)
    done: list[str] = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for rel in p["added"] + p["changed"]:
                _replace(app / rel, zf.read(files[rel]), _mode(files[rel]))
                done.append(rel)
        for rel in p["removed"]:
            (app / rel).unlink()
            done.append(rel)
    except BaseException as err:  # Ctrl-C too: never leave half an update
        log(f"Update failed ({err}); putting the old version back.")
        _restore(record, bdir, app, only=set(done))
        shutil.rmtree(bdir, ignore_errors=True)
        raise SystemExit(f"The update failed and nothing was changed: {err}") from None
    _prune(backups)
    log(f"Updated {p['from']} → {p['to']}: {len(p['added'])} new, {len(p['changed'])} changed, "
        f"{len(p['removed'])} removed files. Backup: {bdir}")
    return {**_counts(p), "changed": True, "backup": str(bdir)}


def _counts(p: dict) -> dict:
    return {"from": p["from"], "to": p["to"], "new_files": len(p["added"]), "changed_files": len(p["changed"]),
            "removed_files": len(p["removed"]),
            "examples": (p["changed"] + p["added"] + [f"{r} (removed)" for r in p["removed"]])[:15]}


def _restore(record: dict, bdir: Path, app: Path, only: set[str] | None = None) -> None:
    """Put the files of a zip update back as they were (only those in `only`, when given: a half-done update)."""
    want = (lambda r: r in only) if only is not None else (lambda r: True)
    for rel in record["added"]:
        if want(rel):
            (app / rel).unlink(missing_ok=True)
    for rel in record["changed"] + record["removed"]:
        if want(rel):
            src = bdir / "files" / rel
            _replace(app / rel, src.read_bytes(), stat.S_IMODE(src.stat().st_mode))


def _prune(backups: Path, keep: int = 3) -> None:
    olds = sorted((d for d in backups.glob("*") if (d / "update.json").exists()), key=lambda d: d.stat().st_mtime)
    for d in olds[:-keep]:
        shutil.rmtree(d, ignore_errors=True)


def last_backup(backups: Path | None = None) -> tuple[Path, dict] | None:
    backups = backups or BACKUPS
    found = sorted((d for d in backups.glob("*") if (d / "update.json").exists()), key=lambda d: d.stat().st_mtime)
    if not found:
        return None
    return found[-1], json.loads((found[-1] / "update.json").read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------
# A git clone
# ----------------------------------------------------------------------------

def _git(app: Path, *args: str, check: bool = True) -> str:
    p = subprocess.run(["git", "-C", str(app), *args], capture_output=True, text=True, check=False)
    if check and p.returncode:
        raise SystemExit(f"git {' '.join(args)} failed: {(p.stderr or p.stdout).strip()}")
    return p.stdout.strip()


def _git_blocker(app: Path) -> str | None:
    """Why `git pull` isn't safe here, or None."""
    branch = _git(app, "rev-parse", "--abbrev-ref", "HEAD", check=False)
    if branch != "main":
        return f"This clone is on branch {branch or '?'}, not main: switch to main (or pull) yourself."
    if _git(app, "status", "--porcelain", "--untracked-files=no", check=False):
        return "This clone has changes of its own: commit or stash them, then update (or git pull yourself)."
    return None


def _git_update(app: Path, dry_run: bool) -> dict:
    why = _git_blocker(app)
    if why:
        raise SystemExit(why)
    before = installed_version(app)
    log("Fetching from GitHub (git fetch)…")
    _git(app, "fetch", "--tags", "origin", "main")
    head, theirs = _git(app, "rev-parse", "HEAD"), _git(app, "rev-parse", "FETCH_HEAD")
    if subprocess.run(["git", "-C", str(app), "merge-base", "--is-ancestor", head, theirs], check=False).returncode:
        raise SystemExit("This clone has commits GitHub's main doesn't: update it yourself (git pull).")
    commits = _git(app, "log", "--format=%h %s", f"{head}..{theirs}").splitlines()
    files = _git(app, "diff", "--name-only", head, theirs).splitlines()
    out = {"from": before, "to": "main", "commits": len(commits), "changed_files": len(files), "examples": commits[:15],
           "dry_run": dry_run, "changed": bool(commits)}
    if dry_run or not commits:
        return out if commits else {**out, "note": "Already up to date with GitHub's main."}
    _git(app, "merge", "--ff-only", theirs)
    BACKUPS.mkdir(parents=True, exist_ok=True)
    bdir = BACKUPS / f"{before}-git-{time.strftime('%Y%m%d-%H%M%S')}"
    bdir.mkdir()
    (bdir / "update.json").write_text(json.dumps({"kind": "git", "from": before, "to": installed_version(app),
                                                  "from_commit": head, "to_commit": theirs,
                                                  "at": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=1), encoding="utf-8")
    _prune(BACKUPS)
    return {**out, "to": installed_version(app), "backup": str(bdir)}


# ----------------------------------------------------------------------------
# The command
# ----------------------------------------------------------------------------

def update(dry_run: bool = False, app: Path = APP) -> dict:
    """Install the newest release (a clone: pull main). Dry run: what would change, nothing written."""
    kind = install_kind(app)
    if kind == "container":
        raise SystemExit("In Docker the app is updated by building the image again (docker compose build).")
    if kind == "git":
        res = _git_update(app, dry_run)
    else:
        st = check(app)
        if not st["newer"]:
            return {"from": st["installed"], "to": st["installed"], "changed": False, "dry_run": dry_run,
                    "note": f"Already the newest release ({st['installed']})."}
        if not st["can_update"]:
            raise SystemExit(st["why_not"])
        rel = releases()[0]
        z = _download(rel)
        if dry_run:
            res = {**_counts(plan(z, app)), "dry_run": True, "changed": True}
        else:
            res = install_zip(z, app)
    if not dry_run and res.get("changed"):
        res["restart"] = True
        res["note"] = "Updated. The page starts again with the new version (it installs new packages first)."
    if res.get("changed"):
        res["after_steps"] = [STEPS[s] for s in steps_between(res["from"], res["to"] if kind == "zip" else installed_version(app))]
    return res


def undo(dry_run: bool = False, app: Path = APP, backups: Path | None = None) -> dict:
    """Go back to the version before the last update."""
    found = last_backup(backups)
    if not found:
        raise SystemExit("There's no update to undo.")
    bdir, rec = found
    now = installed_version(app)
    if now != rec["to"]:
        raise SystemExit(f"This is version {now}, not the {rec['to']} the last update installed: nothing to undo.")
    if rec["kind"] == "git":
        if _git_blocker(app):
            raise SystemExit(_git_blocker(app))
        if _git(app, "rev-parse", "HEAD") != rec["to_commit"]:
            raise SystemExit("The clone moved on since the update: go back with git yourself.")
        out = {"from": now, "to": rec["from"], "changed": True, "note": f"Back to commit {rec['from_commit'][:9]}."}
        if not dry_run:
            _git(app, "reset", "--keep", rec["from_commit"])
    else:
        out = {"from": now, "to": rec["from"], "changed": True,
               "files_put_back": len(rec["changed"]) + len(rec["removed"]), "files_removed": len(rec["added"])}
        if not dry_run:
            _restore(rec, bdir, app)
    if not dry_run:
        shutil.rmtree(bdir, ignore_errors=True)
        out["restart"] = True
        log(f"Back to version {rec['from']}.")
    return {**out, "dry_run": dry_run}


# ----------------------------------------------------------------------------
# Steps after an update
# ----------------------------------------------------------------------------

def steps_between(old: str, new: str) -> list[str]:
    """The AFTER steps of every release after `old` up to `new`, each once, in STEPS' order."""
    want = {s for v, ss in AFTER.items() if version_key(old) < version_key(v) <= version_key(new) for s in ss}
    return [s for s in STEPS if s in want]


def pending_steps(app: Path = APP) -> dict:
    """{since, version, steps: [{key, label}]}: what updating from the version you last confirmed still asks for.
    A fresh install, or an update that needs nothing, is noted as seen."""
    from . import collection

    now = installed_version(app)
    seen = analysis.app_setting("app_version_seen")
    if seen is None:
        seen = FIRST_SEEN if collection.NATIVE_DB.exists() else now  # nothing scanned yet = a new install
    steps = steps_between(seen, now) if version_key(seen) < version_key(now) else []
    if not steps and seen != now:
        mark_seen(app)
    return {"since": seen, "version": now, "steps": [{"key": s, "label": STEPS[s]} for s in steps]}


def mark_seen(app: Path = APP) -> dict:
    analysis.save_app_setting("app_version_seen", installed_version(app))
    return {"seen": installed_version(app)}
