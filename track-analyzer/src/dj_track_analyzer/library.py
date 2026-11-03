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


def _path(path):
    """A path from --nml is Traktor's collection: Rekordbox uses the database chosen on the Setup tab instead."""
    return path if app() == DEFAULT else None


def find_collection(path: str | None = None):
    return backend().find_collection(_path(path))


def collection_tracks(path: str | None = None):
    return backend().collection_tracks(_path(path))


def track_info(path: str | None = None):
    return backend().track_info(_path(path))


def playlists(path: str | None = None):
    return backend().playlists(_path(path))


def genre_field(path: str | None = None):
    return backend().genre_field(_path(path))


def running_state() -> bool | None:
    return backend().running_state()


# ---- writes (each app backs up first and refuses while it's open) -----------------------------------------------

def check_closed(dry_run: bool = False, what: str = "these changes") -> None:
    return backend().check_closed(dry_run, what)


def sync(path: str | None = None, *args, **kw) -> dict:
    return backend().sync(_path(path), *args, **kw)


def set_colors(changes: dict, path: str | None = None, dry_run: bool = False) -> dict:
    return backend().set_colors(changes, _path(path), dry_run=dry_run)


def write_hype(hype: dict, path: str | None = None, **kw) -> dict:
    return backend().write_hype(hype, _path(path), **kw)


def undo(path: str | None = None, force: bool = False) -> dict:
    return backend().undo(_path(path), force=force)


def colors(path: str | None = None):
    """(collection, {file path: colour 1-7}, coloured tracks) from the chosen app."""
    if app() == "rekordbox":
        from . import rekordbox
        return rekordbox.colors(None)
    from . import colors as c
    return c.read_traktor_colors(path)
