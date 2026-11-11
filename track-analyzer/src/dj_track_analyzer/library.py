"""Which DJ app's library the app works with (one at a time, chosen on the Setup tab), and the high-level calls the
rest of the app uses, routed to traktor.py or rekordbox.py. Callers move here from `traktor.x` as Rekordbox
support grows (stage 1b of the Rekordbox plan); Traktor's behaviour is unchanged."""
from __future__ import annotations

from pathlib import Path
from types import ModuleType

from .analysis import app_setting, log, save_app_setting

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


def changed_at(path) -> tuple:
    """When the library file last changed, for cache keys: Rekordbox's master.db may only change in its -wal file."""
    from pathlib import Path

    wal = Path(str(path) + "-wal")
    return Path(path).stat().st_mtime_ns, wal.stat().st_mtime_ns if wal.exists() else None


def collection_tracks(path: str | None = None):
    return backend().collection_tracks(_path(path))


def all_collection_tracks(path: str | None = None) -> tuple[list[str], list[Path], int]:
    """Every audio file in every DJ app found here (Traktor and Rekordbox, whichever app is chosen), so a track added
    in either gets scanned and sync can carry it to the other. An app that isn't installed or can't be read is left
    out. Returns ([\"Traktor <collection path>\", ...], files, number listed but missing)."""
    from . import rekordbox, traktor

    seen, files, missing = [], set(), 0
    for name, mod, arg in (("Traktor", traktor, path), ("Rekordbox", rekordbox, None)):
        try:
            where, f, m = mod.collection_tracks(arg)
        except (SystemExit, OSError, ValueError, ImportError) as err:
            if app() == name.lower():
                raise
            log(f"Scan: {name} left out ({err})")
            continue
        seen.append(f"{name} {where}")
        files.update(f)
        missing += m
    return seen, sorted(files), missing


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


def sync_tracks(path: str | None = None, **kw) -> dict:
    return backend().sync_tracks(_path(path), **kw)


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
