"""Traktor's paths and the folder checks, with Windows' rules simulated, so they're checked on every computer."""
from __future__ import annotations

import ntpath
import subprocess
import sys
import types
from unittest import mock

import pytest

from dj_track_analyzer import collection, colors, traktor

# As Windows Traktor Pro 4 writes them (seen 2026-11-02)
WIN_LOC = {"DIR": "/:Users/:me/:Music/:Traktor/:Tracks/:Beatport/:", "FILE": "Café - Été & Co.mp3",
           "VOLUME": "C:", "VOLUMEID": "f21b7ddb"}
WIN_KEY = "C:/:Users/:me/:Music/:Traktor/:Tracks/:Beatport/:Café - Été & Co.mp3"


def test_windows_location():
    assert traktor.location_paths(WIN_LOC) == [r"C:\Users\me\Music\Traktor\Tracks\Beatport\Café - Été & Co.mp3"]


def test_windows_playlist_key_is_the_same_track():
    assert traktor.location_paths(colors._key_location(WIN_KEY)) == traktor.location_paths(WIN_LOC)


def test_mac_location_unchanged():
    loc = {"DIR": "/:Users/:me/:Music/:", "FILE": "x.mp3", "VOLUME": "Macintosh HD"}
    assert traktor.location_paths(loc) == ["/Users/me/Music/x.mp3", "/Volumes/Macintosh HD/Users/me/Music/x.mp3"]
    loc = {"DIR": "/:DJ/:", "FILE": "x.mp3", "VOLUME": "USB"}
    assert traktor.location_paths(loc) == ["/DJ/x.mp3", "/Volumes/USB/DJ/x.mp3"]


@pytest.mark.parametrize("value, want", [
    ("C:\\Users\\me\\Music\\Traktor\\Samples", "C:\\Users\\me\\Music\\Traktor\\Samples"),
    ("D:\\DJing\\Recordings\\Audio-Recordings\\", "D:\\DJing\\Recordings\\Audio-Recordings"),
    ("C:/Users/x/", "C:\\Users\\x"),
    ("\\\\nas\\music\\DJ\\", "\\\\nas\\music\\DJ"),
    ("D:\\", "D:\\"),
    ("Macintosh HD:Users:Shared:Traktor:Samples:", "/Users/Shared/Traktor/Samples"),
    ("/Users/x/", "/Users/x"),
    ("", None),
])
def test_settings_paths(value, want):
    assert traktor.mac_path(value) == want


@pytest.fixture
def windows_rules():
    """collection's os.path as Windows has it, with C: and one music folder present."""
    present = {"C:\\", "C:\\Users\\me\\Music\\Traktor\\Tracks"}
    path = types.SimpleNamespace(**{k: getattr(ntpath, k) for k in dir(ntpath) if not k.startswith("__")})
    path.isdir = lambda p: p in present
    path.exists = lambda p: False
    with mock.patch.object(collection, "os", types.SimpleNamespace(path=path, sep="\\")), \
            mock.patch.object(sys, "platform", "win32"):
        yield


TRACK = "C:\\Users\\me\\Music\\Traktor\\Tracks\\a.mp3"


@pytest.mark.parametrize("folder, want", [
    ("C:\\Users\\me\\Music\\Traktor\\Tracks", True),
    ("C:\\Users\\me\\Music\\Traktor\\Tracks\\", True),
    ("c:\\users\\me\\music\\TRAKTOR\\tracks", True),  # Windows ignores case
    ("C:/Users/me/Music/Traktor/Tracks", True),
    ("C:\\Users\\me\\Music\\Traktor\\Track", False),  # a longer name is another folder
    ("D:\\Users\\me", False),
    ("C:\\", True),
])
def test_under_windows(windows_rules, folder, want):
    assert collection.under(TRACK, folder) is want


def test_unplugged_drive_windows(windows_rules):
    assert collection._missing_for_good(TRACK) is True  # C: is there, the file isn't: gone
    assert collection._missing_for_good("E:\\Music\\a.mp3") is False  # E: isn't plugged in
    assert collection._missing_for_good("\\\\nas\\music\\a.mp3") is False  # the share isn't reachable


@pytest.mark.parametrize("path, folder, want", [
    ("/Users/x/Music/a.mp3", "/Users/x/Music/", True),
    ("/Users/x/Music/a.mp3", "/users/x/music", False),  # case matters on macOS/Linux paths as stored
    ("/Users/x/Music2/a.mp3", "/Users/x/Music", False),
    ("/Users/x/a.mp3", "/", True),
])
def test_under_posix(path, folder, want):
    if sys.platform == "win32":
        pytest.skip("POSIX paths")
    assert collection.under(path, folder) is want


def ran(stdout="", returncode=0, exc=None):
    def run(*a, **k):
        if exc:
            raise exc
        return types.SimpleNamespace(stdout=stdout, returncode=returncode)
    return mock.patch.object(subprocess, "run", run)


TASKLIST = '"System Idle Process","0","Services","0","8 K"\n"explorer.exe","12","Console","1","9 K"\n'


@pytest.mark.parametrize("platform, out, want", [
    ("win32", ran(TASKLIST + '"Traktor Pro 4.exe","13344","Console","1","1,730,992 K"\n'), True),
    ("win32", ran(TASKLIST), False),
    ("win32", ran("", returncode=1), None),
    ("win32", ran("ERROR: Access denied\n"), None),
    ("win32", ran(exc=FileNotFoundError()), None),
    ("darwin", ran("123\n", 0), True),
    ("darwin", ran("", 1), False),
    ("darwin", ran("", 3), None),  # pgrep itself failed
    ("darwin", ran(exc=FileNotFoundError()), None),
])
def test_running_state(platform, out, want):
    with mock.patch.object(sys, "platform", platform), out:
        assert traktor.running_state() is want


def test_cant_tell_means_ask_not_write(monkeypatch):
    monkeypatch.setattr(traktor, "running_state", lambda: None)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    with pytest.raises(traktor.ConfirmClosed):
        traktor.check_closed()
    with traktor.page_answer(True):
        traktor.check_closed()  # the page's "yes, it's closed"
    traktor.check_closed(dry_run=True)


def test_running_refuses_even_when_told_closed(monkeypatch):
    monkeypatch.setattr(traktor, "running_state", lambda: True)
    with traktor.page_answer(True), pytest.raises(SystemExit, match="Traktor is running"):
        traktor.check_closed()


def test_lexicon_locations():
    from dj_track_analyzer import lexicon

    # Windows Lexicon (seen 2026-11-02) and the Mac's boot-drive form
    assert lexicon._paths("C:/Users/me/Music/Traktor/Tracks/Café - Été.mp3") == [
        "C:\\Users\\me\\Music\\Traktor\\Tracks\\Café - Été.mp3"]
    assert lexicon._paths("/Volumes/Macintosh HD/Users/me/x.mp3") == ["/Volumes/Macintosh HD/Users/me/x.mp3",
                                                                      "/Users/me/x.mp3"]
