"""The page's Stop button: a running job gets Ctrl-C (Ctrl-Break on Windows) and stops cleanly, saving what it has."""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

from dj_track_analyzer import jobs

# Stands in for a scan: works in short steps (Windows delivers Ctrl-Break between Python steps, not inside one long
# sleep), and on Ctrl-C says so and exits as a stopped job does (as jobs.py's __main__ would)
JOB = """
import sys, time
from dj_track_analyzer import jobs
jobs.child_signals()
print("ready", file=sys.stderr, flush=True)
try:
    for _ in range(1200):
        time.sleep(0.1)
except KeyboardInterrupt:
    print("stopped cleanly, saved what it had", file=sys.stderr, flush=True)
    sys.exit(130)
"""


def wait_for(cond, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_stop_button_stops_a_job_cleanly(monkeypatch):
    monkeypatch.setitem(jobs.ACTIONS, "wait", ("Wait for a stop", lambda o: ["wait"], False))
    monkeypatch.setattr(jobs, "_command", lambda args: [sys.executable, "-c", JOB])
    jobs.start("wait")
    job = jobs._job
    assert wait_for(lambda: "ready" in "".join(job["lines"]), 60), job["lines"]
    t0 = time.time()
    jobs.stop()
    assert wait_for(lambda: not job["running"], 15), "the job didn't stop"
    assert time.time() - t0 < 15  # by its own Ctrl-C handling, not the hard stop after 20 s
    assert job["returncode"] == 130, (job["returncode"], job["lines"])
    assert "stopped cleanly" in "".join(job["lines"])
    assert job["error"] == "Stopped."


class Stream:
    def __init__(self, chunks: list[bytes]):
        self.chunks = list(chunks)

    def read1(self, n: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""


def read_log(chunks: list[bytes]) -> dict:
    job = {"lines": [], "dropped": 0, "live": None, "error": None, "progress": None, "stage": None, "steps": []}
    jobs._read_err(job, type("Proc", (), {"stderr": Stream(chunks)})())
    return job


def test_log_lines_with_windows_line_ends(monkeypatch):
    monkeypatch.setattr(jobs, "WINDOWS", True)
    # "\r\n" split across two reads; a meter's "\r" updates; the meter's last value ends in "\r\n"
    job = read_log([b"ready\r\n", b"one\r", b"\ntwo\r\n", b"10%\r50%\r100%\r\n", b"last"])
    assert job["lines"] == ["ready", "one", "two", "100%", "last"]
    assert job["live"] is None


def test_log_meter_on_mac_and_linux(monkeypatch):
    monkeypatch.setattr(jobs, "WINDOWS", False)
    job = read_log([b"start\n", b"10%\r50%\r", b"100%\r", b"\ndone\n"])
    assert job["lines"] == ["start", "done"] and job["live"] is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows priority classes")
def test_commands_run_below_normal_priority():
    """A scan's process and the workers it starts (they inherit it); the web page itself isn't lowered."""
    child = ("import ctypes; from ctypes import wintypes as w; k = ctypes.WinDLL('kernel32'); "
             "k.GetCurrentProcess.restype = w.HANDLE; k.GetPriorityClass.argtypes = (w.HANDLE,); "
             "print(k.GetPriorityClass(k.GetCurrentProcess()))")
    code = ("import subprocess, sys; from dj_track_analyzer import cli; cli.lower_priority(); "
            f"exec({child!r}); print(subprocess.run([sys.executable, '-c', {child!r}], capture_output=True, "
            "text=True).stdout.strip())")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.stdout.split() == ["16384", "16384"], out  # BELOW_NORMAL_PRIORITY_CLASS
