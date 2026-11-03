"""Which DJ app's library the app works with (one at a time, chosen on the Setup tab), and the high-level calls the
rest of the app uses, routed to traktor.py or rekordbox.py. Callers move here from `traktor.x` as Rekordbox
support grows (stage 1b of the Rekordbox plan); Traktor's behaviour is unchanged."""
from __future__ import annotations

from types import ModuleType

from .analysis import app_setting, save_app_setting

APP_SETTING = "dj_app"  # in data/settings.json
APPS = {"traktor": "Traktor", "rekordbox": "Rekordbox"}
DEFAULT = "traktor"


def app() -> str:
    a = app_setting(APP_SETTING)
    return a if a in APPS else DEFAULT


def app_name() -> str:
    """For page and log wording: "Traktor" or "Rekordbox"."""
    return APPS[app()]


def choose_app(name: str) -> str:
    if name not in APPS:
        raise ValueError(f"Unknown DJ app {name!r}: choose one of {', '.join(APPS.values())}.")
    save_app_setting(APP_SETTING, None if name == DEFAULT else name)
    return app()


def backend() -> ModuleType:
    if app() == "rekordbox":
        from . import rekordbox
        return rekordbox
    from . import traktor
    return traktor


def find_collection(path: str | None = None):
    return backend().find_collection(path)


def collection_tracks(path: str | None = None):
    return backend().collection_tracks(path)


def track_info(path: str | None = None):
    return backend().track_info(path)


def playlists(path: str | None = None):
    return backend().playlists(path)


def genre_field(path: str | None = None):
    return backend().genre_field(path)


def running_state() -> bool | None:
    return backend().running_state()
