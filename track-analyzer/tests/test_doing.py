"""The page's "Loading…" line: /api/doing names what the server works on, and nothing once it's done."""

from __future__ import annotations

from starlette.testclient import TestClient

from dj_track_analyzer import analysis, web


def client() -> TestClient:
    return TestClient(web.build_app([analysis.HOME / "logs"]), base_url="http://127.0.0.1:8766")


def test_steps_show_while_they_run_and_go_after():
    c = client()
    assert c.get("/api/doing").json() == {"doing": []}
    with web.doing("Reading your cues") as say:
        with web.doing("Reading your cues"):  # two requests waiting on the same step: named once
            assert c.get("/api/doing").json() == {"doing": ["Reading your cues"]}
        say("Counting proposed corrections of your cues: 3 of 10 tracks")
        assert c.get("/api/doing").json() == {"doing": ["Counting proposed corrections of your cues: 3 of 10 tracks"]}
    assert c.get("/api/doing").json() == {"doing": []}


def test_a_slow_request_is_named_while_it_runs(monkeypatch):
    seen = []
    monkeypatch.setattr(web, "libraries", lambda: seen.append(web.doing_now()) or {"libraries": []})
    client().get("/api/libraries")
    assert seen == [["Getting the library list"]]
    assert web.doing_now() == []


def test_a_failed_step_is_cleared():
    try:
        with web.doing("Reading your colours"):
            raise OSError("gone")
    except OSError:
        pass
    assert web.doing_now() == []


def test_the_setup_checks_name_each_part(monkeypatch):
    from dj_track_analyzer import jobs

    seen = []

    def fake(say):
        say("checking the beat grids")
        seen.append(web.doing_now())
        return {"traktor": {}}
    monkeypatch.setattr(jobs, "setup_status", fake)
    monkeypatch.setattr(web, "can_browse", lambda: False)
    client().get("/api/setup")
    assert seen == [["Setup checks: checking the beat grids"]]
    assert web.doing_now() == []
