"""Installing ffmpeg and Rubber Band from the Setup tab: Homebrew on a Mac, winget and Rubber Band's own build on Windows."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import TMP, WINDOWS, make_audio

from dj_track_analyzer import analysis, jobs


@pytest.mark.skipif(not WINDOWS, reason="Rubber Band's Windows build")
def test_install_rubberband_and_stretch():
    out = analysis.install_rubberband()
    exe = analysis.rubberband()
    assert out["installed"] == exe and str(analysis.TOOLS_HOME) in exe
    v = subprocess.run([exe, "--version"], capture_output=True, text=True)
    assert "4.0.0" in v.stdout + v.stderr  # the same as Homebrew's on the Mac
    src, dst = TMP / "stretch" / "in.wav", TMP / "stretch" / "out.wav"
    make_audio(src, 440)
    subprocess.run([exe, "-3", "-q", "-t", "1.1", str(src), str(dst)], check=True)  # the tempo view's engine
    assert dst.stat().st_size > src.stat().st_size


def test_rubberband_download_is_checked(monkeypatch):
    def fetch(url, dest):
        dest.write_bytes(b"not the zip")
        return True

    dest = analysis.TOOLS_HOME / "rubberband"
    before = sorted(p.name for p in dest.iterdir()) if dest.exists() else None  # a Rubber Band installed earlier
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(analysis, "_fetch", fetch)
    with pytest.raises(SystemExit, match="isn't the expected file"):
        analysis.install_rubberband()
    assert (sorted(p.name for p in dest.iterdir()) if dest.exists() else None) == before  # left as it was


@pytest.mark.parametrize("brew, winget, windows, tool, want, button", [
    ("/opt/homebrew/bin/brew", None, False, "ffmpeg", ["/opt/homebrew/bin/brew", "install", "ffmpeg"],
     "Install with Homebrew"),
    (None, "winget.exe", True, "ffmpeg", ["winget.exe", "install", "--id", "Gyan.FFmpeg"], "Install with winget"),
    (None, "winget.exe", True, "rubberband", ["install", "rubberband"], "Install"),
    (None, None, True, "rubberband", ["install", "rubberband"], "Install"),
    (None, None, True, "ffmpeg", None, None),  # no winget: the page shows the command instead
    (None, None, False, "ffmpeg", None, None),  # Linux without Homebrew: apt
])
def test_install_buttons(monkeypatch, brew, winget, windows, tool, want, button):
    monkeypatch.setattr(jobs, "BREW", brew)
    monkeypatch.setattr(jobs, "WINGET", winget)
    monkeypatch.setattr(jobs, "WINDOWS", windows)
    assert jobs._installer(tool) == button
    if want is None:
        with pytest.raises(ValueError):
            jobs._tool_install({"tool": tool})
    else:
        assert jobs._tool_install({"tool": tool})[:len(want)] == want


def test_page_shows_the_install_button():
    """The Setup tab draws the button from setup_status()'s `install`, not from whether Homebrew is there."""
    page = (Path(jobs.__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
    assert "st.install[name] ? jb(`su-install-${name}`, st.install[name])" in page
    assert 'go("install", { tool: "rubberband" })' in page


@pytest.mark.skipif(not WINDOWS, reason="Rubber Band's Windows build")
def test_install_button_installs_rubberband(monkeypatch, tmp_path):
    """What the Setup tab's Install button does on Windows: a job that downloads and installs Rubber Band."""
    monkeypatch.setattr(analysis, "TOOLS_HOME", tmp_path / "tools")  # a fresh install, not the earlier test's
    monkeypatch.setattr(analysis, "TOOL_DIRS", [str(tmp_path / "tools" / "rubberband")])
    monkeypatch.setattr(analysis, "_rubberband_exe", None)
    st = jobs.setup_status()
    assert st["install"]["rubberband"] == "Install"
    monkeypatch.setenv("TRACK_ANALYZER_HOME", str(tmp_path))  # the job process installs into tmp_path/tools
    jobs.start("install", {"tool": "rubberband"})
    job = jobs._job
    end = time.time() + 300
    while job["running"] and time.time() < end:
        time.sleep(0.5)
    assert job["returncode"] == 0, job["lines"]
    assert (tmp_path / "tools" / "rubberband" / "rubberband.exe").exists()
    assert jobs.setup_status()["tools"]["rubberband"]  # the step shows as installed


def test_undo_anyway_passes_force():
    build = jobs.ACTIONS["traktor_undo"][1]
    assert build({}) == ["traktor", "--undo"]
    assert build({"force": True}) == ["traktor", "--undo", "--force"]
    assert build({"force": "yes"}) == ["traktor", "--undo"]  # only a real true from the page's button
    page = (Path(jobs.__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
    assert "write(act, { ...opts, force: true })" in page


def test_model_download_reports_missing_required_models(tmp_path, monkeypatch):
    """model_files.download (also run alone by the Docker image): required models that fail are returned, optional ones skipped."""
    from dj_track_analyzer import model_files

    def fetch(url, dest):
        if "maest" in url or "mood_sad" in url:
            return False
        dest.write_bytes(b"model")
        return True

    monkeypatch.setattr(model_files, "_fetch", fetch)
    missing = model_files.download(tmp_path / "models")
    assert sorted(n for n, _ in missing) == sorted(model_files.MAEST_FILES)
    assert all((tmp_path / "models" / n).exists() for n in model_files.MODEL_FILES)
    assert not (tmp_path / "models" / "mood_sad-discogs-effnet-1.pb").exists()
    assert model_files.download(tmp_path / "models", maest=False) == []  # what's there is kept, not fetched again
