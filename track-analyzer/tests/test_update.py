"""Updating the app (update.py): a release zip installed over a made-up app folder writes only the app's own files,
keeps data/, backs up what it replaces, rolls back a failure, and `undo` puts the old version back byte for byte.
Also the launchers' restart loop, and why the Windows launcher must stay short."""
from __future__ import annotations

import os
import stat
import sys
import zipfile
from pathlib import Path

import pytest

from dj_track_analyzer import jobs, update

APP_ROOT = Path(update.APP)
OLD = {
    "pyproject.toml": 'name = "x"\nversion = "1.0.1"\n',
    "src/dj_track_analyzer/__init__.py": "",
    "src/dj_track_analyzer/a.py": "A = 1\n",
    "src/dj_track_analyzer/gone.py": "GONE = 1\n",
    "README.md": "old readme\n",
    "Start.command": "#!/bin/bash\necho old\n",
    "data/collection.db": "your scans",
    "logs/run.json": "{}",
    "notes-of-mine.txt": "mine",  # not the app's, not in the zip: stays
}
NEW = {
    "pyproject.toml": 'name = "x"\nversion = "1.0.2"\n',
    "src/dj_track_analyzer/__init__.py": "",
    "src/dj_track_analyzer/a.py": "A = 2\n",
    "src/dj_track_analyzer/b.py": "B = 1\n",
    "README.md": "old readme\n",
    "Start.command": "#!/bin/bash\necho new\n",
    "data/collection.db": "a zip must never write here",
    "logs/.gitkeep": "",
}


def make_app(root: Path) -> Path:
    app = root / "app"
    for rel, text in OLD.items():
        (app / rel).parent.mkdir(parents=True, exist_ok=True)
        (app / rel).write_bytes(text.encode())  # exact bytes, as unzipped (write_text gives \r\n on Windows)
    (app / "src/dj_track_analyzer/__pycache__").mkdir()
    (app / "src/dj_track_analyzer/__pycache__/a.cpython-311.pyc").write_bytes(b"cache")
    return app


def make_zip(root: Path, files: dict = NEW, top: str = "puppi3s-track-analyzer-1.0.2/") -> Path:
    z = root / "release.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for rel, text in files.items():
            info = zipfile.ZipInfo(top + rel)
            info.external_attr = (0o100755 if rel.endswith(".command") else 0o100644) << 16
            zf.writestr(info, text)
    return z


def snapshot(app: Path) -> dict:
    return {p.relative_to(app).as_posix(): p.read_bytes() for p in app.rglob("*") if p.is_file()}


def test_versions_compare_as_numbers():
    assert update.version_key("v1.10.0") > update.version_key("1.9.3") > update.version_key("1.9")
    assert update.version_key("v1.1") == update.version_key("1.1.0")  # a short tag isn't "older" than its own version
    assert update.installed_version(APP_ROOT)  # the real pyproject.toml has one


def test_a_failed_write_leaves_no_temporary_file(tmp_path, monkeypatch):
    def broken(*a):
        raise OSError("disk full")
    monkeypatch.setattr(update.os, "replace", broken)
    with pytest.raises(OSError):
        update._replace(tmp_path / "a.py", b"x", None)
    assert not list(tmp_path.iterdir())


def test_a_github_error_message_is_an_error(monkeypatch):
    monkeypatch.setattr(update, "_get", lambda url: b'{"message": "API rate limit exceeded"}')
    with pytest.raises(ValueError, match="rate limit"):
        update.releases()


def test_install_writes_only_the_apps_files_and_undo_puts_them_back(tmp_path):
    app, backups = make_app(tmp_path), tmp_path / "backups"
    before = snapshot(app)
    p = update.plan(make_zip(tmp_path), app)
    assert (p["from"], p["to"]) == ("1.0.1", "1.0.2")
    assert p["added"] == ["src/dj_track_analyzer/b.py"]
    assert sorted(p["changed"]) == ["Start.command", "pyproject.toml", "src/dj_track_analyzer/a.py"]
    assert p["removed"] == ["src/dj_track_analyzer/gone.py"]  # in src/ only; the cache isn't the app's

    res = update.install_zip(make_zip(tmp_path), app, backups)
    assert res["changed"] and Path(res["backup"]).is_dir()
    assert update.installed_version(app) == "1.0.2"
    assert (app / "src/dj_track_analyzer/a.py").read_text() == "A = 2\n"
    assert not (app / "src/dj_track_analyzer/gone.py").exists()
    assert (app / "data/collection.db").read_text() == "your scans"  # protected
    assert (app / "logs/run.json").exists() and not (app / "logs/.gitkeep").exists()
    assert (app / "notes-of-mine.txt").read_text() == "mine"
    if sys.platform != "win32":
        assert os.stat(app / "Start.command").st_mode & stat.S_IXUSR  # the launcher stays runnable
    assert not list(app.rglob("*.update-part"))

    assert update.undo(dry_run=True, app=app, backups=backups)["to"] == "1.0.1"
    assert update.installed_version(app) == "1.0.2"  # a dry run changes nothing
    update.undo(app=app, backups=backups)
    assert snapshot(app) == before
    with pytest.raises(SystemExit, match="no update to undo"):
        update.undo(app=app, backups=backups)


def test_installing_the_same_release_again_changes_nothing(tmp_path):
    app, backups = make_app(tmp_path), tmp_path / "backups"
    update.install_zip(make_zip(tmp_path), app, backups)
    again = update.install_zip(make_zip(tmp_path), app, backups)
    assert not again["changed"]


def test_a_failure_halfway_puts_everything_back(tmp_path, monkeypatch):
    app, backups = make_app(tmp_path), tmp_path / "backups"
    before = snapshot(app)
    real, calls = update._replace, []

    def flaky(dest, data, mode):
        calls.append(dest)
        if len(calls) == 3:
            raise OSError("disk full")
        real(dest, data, mode)
    monkeypatch.setattr(update, "_replace", flaky)
    with pytest.raises(SystemExit, match="nothing was changed"):
        update.install_zip(make_zip(tmp_path), app, backups)
    monkeypatch.setattr(update, "_replace", real)
    assert snapshot(app) == before
    assert not update.last_backup(backups)


@pytest.mark.parametrize("files, top, why", [
    ({**NEW, "../escape.py": "x"}, "puppi3s-track-analyzer-1.0.2/", "unsafe path"),
    ({**NEW, "src/a.py:hidden": "x"}, "puppi3s-track-analyzer-1.0.2/", "unsafe path"),  # an NTFS stream
    ({"pyproject.toml": "x"}, "puppi3s-track-analyzer-1.0.2/", "isn't Puppi3"),
])
def test_a_wrong_zip_is_refused(tmp_path, files, top, why):
    app = make_app(tmp_path)
    before = snapshot(app)
    with pytest.raises(SystemExit, match=why):
        update.install_zip(make_zip(tmp_path, files, top), app, tmp_path / "backups")
    assert snapshot(app) == before


def test_steps_after_an_update_cover_every_skipped_release(monkeypatch):
    monkeypatch.setattr(update, "AFTER", {"1.0.2": ["regenre"], "1.1.0": ["train", "regenre"], "2.0.0": ["rescan"]})
    assert update.steps_between("1.0.1", "1.1.0") == ["regenre", "train"]
    assert update.steps_between("1.0.2", "1.1.0") == ["regenre", "train"]
    assert update.steps_between("1.1.0", "1.9.0") == []


def test_every_after_step_has_a_label():
    assert set(update.STEPS) >= {s for ss in update.AFTER.values() for s in ss}


def test_update_says_so_when_already_newest(monkeypatch):
    monkeypatch.setattr(update, "install_kind", lambda app=None: "zip")
    monkeypatch.setattr(update, "releases", lambda: [{"version": update.installed_version(), "tag": "v0", "notes": "",
                                                      "url": "", "published": "", "zip": None}])
    res = update.update()
    assert not res["changed"] and "Already the newest" in res["note"]


def test_the_page_runs_update_as_a_previewed_writer():
    _label, build, writes = jobs.ACTIONS["update"]
    assert build({}) == ["update"] and writes and jobs.UNDO["update"] == "update_undo"
    assert jobs.ACTIONS["update_undo"][1]({}) == ["update", "--undo"]


def test_launchers_restart_after_an_update():
    mac = (APP_ROOT / "Start Puppi3's Track Analyzer.command").read_text()
    assert "TRACK_ANALYZER_LAUNCHER=1" in mac and '= 75 ]' in mac and "exec uv run" not in mac
    win = (APP_ROOT / "Start Puppi3's Track Analyzer.bat").read_bytes()
    assert b"TRACK_ANALYZER_LAUNCHER=1" in win and b"errorlevel 75" in win
    assert update.RESTART_CODE == 75


def test_windows_launcher_stays_short():
    """cmd.exe reads a running batch file line by line from its byte offset, also after the file was replaced. The
    launchers of 1.0.1 and before were still running `uv run … web` when an update replaced them, and read on at
    byte 1817 (1863 with CRLF) once it ended. A new launcher shorter than that ends there instead of running half a
    line of itself."""
    assert len((APP_ROOT / "Start Puppi3's Track Analyzer.bat").read_bytes().replace(b"\r\n", b"\n")) < 1800
    assert len((APP_ROOT / "Start Puppi3's Track Analyzer.bat").read_bytes()) < 1800
