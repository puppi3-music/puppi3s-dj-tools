"""traktor.audio_offset keeps its ffprobe results in cache.db until the file changes."""
import os
from pathlib import Path

from dj_track_analyzer import traktor


def test_offset_kept_on_disk_until_file_changes(tmp_path, monkeypatch):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"\0" * 100)
    calls = []

    def fake_run(*a, **k):
        calls.append(a)
        return type("R", (), {"stdout": "44100,0.025057"})()

    monkeypatch.setattr(traktor.subprocess, "run", fake_run)
    monkeypatch.setattr(traktor, "_offsets", {})
    monkeypatch.setattr(traktor, "_offset_disk", None)
    n = traktor.audio_offset(f)
    assert n == 1105 and len(calls) == 1

    monkeypatch.setattr(traktor, "_offsets", {})  # a new server start: memory empty, disk kept
    monkeypatch.setattr(traktor, "_offset_disk", None)
    assert traktor.audio_offset(f) == 1105 and len(calls) == 1

    traktor._offsets.clear()
    os.utime(f, ns=(1, 1))  # the file changed: probed again
    assert traktor.audio_offset(f) == 1105 and len(calls) == 2


def test_warm_offsets_probes_each_once(tmp_path, monkeypatch):
    files = [tmp_path / f"{i}.mp3" for i in range(5)] + [tmp_path / "x.wav"]
    for f in files:
        f.write_bytes(b"\0" * 10)
    calls = []
    monkeypatch.setattr(traktor.subprocess, "run", lambda *a, **k: calls.append(a) or type("R", (), {"stdout": "44100,0"})())
    monkeypatch.setattr(traktor, "_offsets", {})
    monkeypatch.setattr(traktor, "_offset_disk", None)
    traktor.warm_offsets(str(f) for f in files)
    assert len(calls) == 5 and all(traktor._offsets[str(f)] == 0 for f in files[:5])
