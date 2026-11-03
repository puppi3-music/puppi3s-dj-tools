"""The page side of Sync everything: the jobs' commands, the undo row's flag and the app's name in the labels."""
from __future__ import annotations

import json

from dj_track_analyzer import jobs, library, sync


def test_sync_job_commands():
    sync_cmd = jobs.ACTIONS["sync"][1]
    undo_cmd = jobs.ACTIONS["sync_undo"][1]
    assert sync_cmd({}) == ["sync", "--json"]
    assert sync_cmd({"only": ["cues", "grids"]}) == ["sync", "--json", "--only", "cues,grids"]
    assert undo_cmd({}) == ["sync", "--undo"]
    assert undo_cmd({"force": True}) == ["sync", "--undo", "--force"]
    assert undo_cmd({"force": "yes"}) == ["sync", "--undo"]  # only a real true forces
    assert jobs.UNDO["sync"] == "sync_undo"
    assert "sync_undo" in jobs.NO_DRY_RUN
    assert {"sync", "sync_undo"} <= jobs.EITHER_APP & jobs.TOUCHES_TRAKTOR


def test_sync_undoable_follows_the_chosen_app(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "HOME", tmp_path)
    monkeypatch.setattr(library, "app", lambda: "traktor")
    assert jobs._sync_undoable() is False  # no sync yet
    (tmp_path / sync.LAST).write_text("not json")
    assert jobs._sync_undoable() is False
    (tmp_path / sync.LAST).write_text(json.dumps({"app": "traktor", "steps": []}))
    assert jobs._sync_undoable() is True
    monkeypatch.setattr(library, "app", lambda: "rekordbox")
    assert jobs._sync_undoable() is False  # that sync was Traktor's


def test_sync_labels_name_the_chosen_app(monkeypatch):
    monkeypatch.setattr(library, "app", lambda: "rekordbox")
    monkeypatch.setattr(library, "app_name", lambda: "Rekordbox")
    assert jobs.app_label("sync", jobs.ACTIONS["sync"][0]) == "Sync everything into Rekordbox"
    assert jobs.app_label("sync_undo", jobs.ACTIONS["sync_undo"][0]) == "Undo the last sync to Rekordbox"
