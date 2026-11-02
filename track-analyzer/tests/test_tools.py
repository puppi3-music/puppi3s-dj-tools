"""Installing ffmpeg and Rubber Band from the Setup tab: Homebrew on a Mac, winget and Rubber Band's own build on Windows."""
from __future__ import annotations

import subprocess
import sys

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

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(analysis, "_fetch", fetch)
    with pytest.raises(SystemExit, match="isn't the expected file"):
        analysis.install_rubberband()
    assert not (analysis.TOOLS_HOME / "rubberband").exists()


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
