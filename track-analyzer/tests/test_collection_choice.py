"""Choosing where Traktor's collection is on the Setup tab (no command line): pasted or picked paths, saved for the
CLI and the jobs too, checked, and back to automatic."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest
from starlette.testclient import TestClient

from dj_track_analyzer import analysis, traktor, web

NML = '<?xml version="1.0" encoding="UTF-8" standalone="no" ?><NML VERSION="20"><COLLECTION ENTRIES="0"></COLLECTION></NML>'


@pytest.fixture
def chosen_cleared():
    analysis.save_app_setting(traktor.COLLECTION_SETTING, None)
    yield
    analysis.save_app_setting(traktor.COLLECTION_SETTING, None)


def traktor_folder(root, version: str):
    d = root / "Native Instruments" / f"Traktor {version}"
    d.mkdir(parents=True)
    (d / "collection.nml").write_text(NML, encoding="utf-8")
    return d


def post(body: dict, **headers):
    client = TestClient(web.build_app([analysis.HOME / "logs"]), base_url="http://127.0.0.1:8766")
    return client.post("/api/traktor/collection", json=body, headers=headers)


def test_pasted_path_is_used_everywhere(tmp_path, chosen_cleared):
    d = traktor_folder(tmp_path / "Elsewhere", "4.5.0")
    r = post({"path": f'"{d / "collection.nml"}"'})  # Explorer's Copy as path adds the quotes
    assert r.status_code == 200, r.text
    assert traktor.find_collection(None) == (d / "collection.nml").resolve()
    assert analysis.app_setting(traktor.COLLECTION_SETTING) == str((d / "collection.nml").resolve())
    assert post({"path": str(d)}).status_code == 200  # the Traktor folder itself is fine too
    assert post({"auto": True}).json() == {"collection": str(traktor.auto_collection()) if traktor.auto_collection() else None}
    assert traktor.chosen_collection() is None


def test_wrong_files_are_refused(tmp_path, chosen_cleared):
    d = traktor_folder(tmp_path, "4.5.0")
    (d / "History").mkdir()
    (d / "History" / "history_2026y11m02d_20h00m00s.nml").write_text(NML, encoding="utf-8")
    (tmp_path / "collection.nml").write_text("not traktor", encoding="utf-8")
    for bad in (d / "History" / "history_2026y11m02d_20h00m00s.nml", tmp_path / "collection.nml", tmp_path / "nope.nml", ""):
        r = post({"path": str(bad)})
        assert r.status_code == 400 and r.json()["error"], (bad, r.text)
    assert traktor.chosen_collection() is None


def test_only_this_page_may_choose(tmp_path, chosen_cleared):
    d = traktor_folder(tmp_path, "4.5.0")
    assert post({"path": str(d)}, origin="http://evil.example").status_code == 403
    assert traktor.chosen_collection() is None


def test_chosen_collection_gone_says_so(tmp_path, chosen_cleared):
    d = traktor_folder(tmp_path, "4.5.0")
    traktor.choose_collection(str(d))
    (d / "collection.nml").unlink()
    with pytest.raises(SystemExit, match="isn't there any more"):
        traktor.find_collection(None)
    assert traktor.find_collection(str(tmp_path / "Native Instruments")).exists()  # --nml still wins


def test_newer_traktor_version_is_offered(tmp_path, monkeypatch, chosen_cleared):
    old, new = traktor_folder(tmp_path, "4.5.0"), traktor_folder(tmp_path, "4.10.0")
    traktor.choose_collection(str(old))
    monkeypatch.setattr(traktor, "auto_collection", lambda: new / "collection.nml")
    assert traktor.newer_collection() == new / "collection.nml"
    traktor.choose_collection(str(new))
    assert traktor.newer_collection() is None


def test_cancelled_dialog_changes_nothing(tmp_path, monkeypatch, chosen_cleared):
    monkeypatch.setattr(web, "pick_file", lambda: None)
    assert post({"browse": True}).json() == {"cancelled": True}
    d = traktor_folder(tmp_path, "4.5.0")
    monkeypatch.setattr(web, "pick_file", lambda: str(d / "collection.nml"))
    assert post({"browse": True}).json()["collection"] == str((d / "collection.nml").resolve())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' file dialog is a PowerShell script")
def test_windows_dialog_script_parses():
    """CI can't click a dialog, but PowerShell can check the script without running it."""
    check = ("$e = $null; [System.Management.Automation.Language.Parser]::ParseInput($env:TA_SCRIPT, [ref]$null, [ref]$e)"
             " | Out-Null; if ($e.Count) { $e | ForEach-Object { $_.Message }; exit 1 }")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", check],
                       env={**os.environ, "TA_SCRIPT": web._PS_PICK}, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert web.can_browse()


def test_untested_traktor_versions_are_flagged(tmp_path):
    tested = ".".join(map(str, traktor.TESTED_TRAKTOR))
    assert traktor.collection_version(traktor_folder(tmp_path / "a", tested) / "collection.nml")["untested"] is None
    assert traktor.collection_version(traktor_folder(tmp_path / "b", "4.4.2") / "collection.nml")["untested"] is None
    newer = traktor_folder(tmp_path / "c", "9.0.0") / "collection.nml"
    assert traktor.collection_version(newer)["untested"] == "newer"
    assert "should work" in traktor.untested_note(newer)
    fmt = traktor_folder(tmp_path / "d", tested) / "collection.nml"
    fmt.write_text(NML.replace('VERSION="20"', 'VERSION="99"'), encoding="utf-8")
    assert traktor.collection_version(fmt)["untested"] == "format"
    stats = traktor._save(fmt, NML, NML + "\n", {}, dry_run=True)  # previews say so too
    assert "NML version 99" in stats["untested"]


def test_traktor_folder_beats_its_copy(tmp_path, monkeypatch):
    real = traktor_folder(tmp_path / "Documents", "4.5.0") / "collection.nml"
    copy = tmp_path / "Documents" / "Native Instruments" / "Traktor 4.5.0 copy"
    copy.mkdir()
    (copy / "collection.nml").write_text(NML, encoding="utf-8")  # saved later than the real one
    os.utime(real, (1_000_000_000, 1_000_000_000))
    monkeypatch.setattr(traktor, "documents_dirs", lambda: [tmp_path / "Documents"])
    assert traktor.auto_collection() == real
