"""The page's Stop button: a running job gets Ctrl-C (Ctrl-Break on Windows) and stops cleanly, saving what it has."""
from __future__ import annotations

import sys
import time

from dj_track_analyzer import jobs

# Stands in for a scan: waits, and on Ctrl-C says so and exits as a stopped job does (as jobs.py's __main__ would)
JOB = """
import sys, time
from dj_track_analyzer import jobs
jobs.child_signals()
print("ready", file=sys.stderr, flush=True)
try:
    time.sleep(120)
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
