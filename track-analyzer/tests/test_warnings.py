import warnings

from dj_track_analyzer._warnings import route_warnings


def test_warnings_go_to_log_once(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("PYTHONWARNINGS", raising=False)
    monkeypatch.setattr("sys.warnoptions", [])
    log = tmp_path / "logs" / "warnings.log"
    with warnings.catch_warnings():
        assert route_warnings(log)
        for _ in range(3):
            warnings.warn("old api", DeprecationWarning)
    assert capsys.readouterr().err == ""
    text = log.read_text()
    assert text.count("DeprecationWarning: old api") == 1


def test_user_settings_win(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONWARNINGS", "default")
    with warnings.catch_warnings():
        assert not route_warnings(tmp_path / "w.log")
