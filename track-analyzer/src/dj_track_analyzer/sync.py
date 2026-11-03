"""Sync: everything the app has for your DJ app in one go, for the app chosen on the Setup tab (App → DJ app only).

Steps, each through its own writer: genres + predicted colours + genre smart playlists (library.sync), hype in the
Comment (library.write_hype), approved cues (cues.apply), approved grid fixes (gridfix.apply, Traktor only) and the
warm-up/peak/closing crates (setplan.crates_to_traktor). Set playlists aren't rebuilt: each needs its own arc and
name (`set --traktor`). A step with nothing to do is skipped with its reason. Every write makes its own backup;
they're listed in data/sync-last.json so one undo puts back the library from before the whole sync."""
from __future__ import annotations

import json

from . import analysis, library
from .analysis import log

LAST = "sync-last.json"  # in data/
STEPS = ("genres", "hype", "cues", "grids", "crates")


def _genres(nml, dry_run):
    return library.sync(nml, dry_run=dry_run, update_genres=True, make_smartlists=True, colors=True,
                        overwrite_colors=False)


def _hype(nml, dry_run):
    from . import hype

    return library.write_hype(hype.compute(), nml, dry_run=dry_run)


def _cues(nml, dry_run, other=False):
    from . import cues

    return cues.apply(nml, dry_run=dry_run, restore=True, other=other)  # also puts back approved cues deleted since


def _grids(nml, dry_run):
    from . import gridfix

    if library.app() != "traktor":
        raise SystemExit("Fixing grids is Traktor only.")
    return gridfix.apply(nml, dry_run=dry_run)


def _crates(nml, dry_run):
    from . import setplan

    return setplan.crates_to_traktor(setplan.crates(nml=nml), nml, dry_run=dry_run)


WRITERS = {"genres": _genres, "hype": _hype, "cues": _cues, "grids": _grids, "crates": _crates}


def run(nml: str | None = None, dry_run: bool = False, steps: list[str] | None = None, cues_from_other: bool = False) -> dict:
    """Preview (dry_run) or apply each step in order. Stops at the first error; what was written before it stays
    listed for the undo."""
    steps = list(steps or STEPS)
    if bad := [s for s in steps if s not in WRITERS]:
        raise SystemExit(f"Unknown step(s) {', '.join(bad)}: choose from {', '.join(STEPS)}.")
    library.check_closed(dry_run, "the sync")  # once up front: a SystemExit from a step below means "nothing to do"
    out = {"app": library.app_name(), "dry_run": dry_run, "steps": {}, "changed": False}
    written = []
    try:
        for s in steps:
            log(f"Sync: {s}" + (" (preview)" if dry_run else ""))
            try:
                r = _cues(nml, dry_run, cues_from_other) if s == "cues" else WRITERS[s](nml, dry_run)
            except SystemExit as err:
                out["steps"][s] = {"skipped": str(err)}
                continue
            r.pop("undo", None)  # the sync's own undo replaces each step's
            out["steps"][s] = r
            out["changed"] |= bool(r.get("changed"))
            if r.get("backup"):
                written.append({"step": s, "backup": r["backup"]})
    finally:
        if written:
            (analysis.HOME / LAST).write_text(json.dumps({"app": library.app(), "steps": written}, indent=1))
            out["undo"] = "uv run dj-track-analyzer sync --undo"
    return out


def undo(nml: str | None = None, force: bool = False) -> dict:
    """Put back the library from before the last sync: each of its backups, newest first. Refuses (unless force) when
    the DJ app changed the library since the sync wrote it, as the plain undo does."""
    from . import gridfix

    f = analysis.HOME / LAST
    try:
        last = json.loads(f.read_text())
    except (OSError, ValueError):
        raise SystemExit("No sync to undo.")
    if last.get("app") != library.app():
        raise SystemExit(f"The last sync wrote to {library.APPS.get(last.get('app'), last.get('app'))}; "
                         f"choose it on the Setup tab to undo it.")
    restored = []
    for i, w in enumerate(reversed(last["steps"])):
        # only the newest needs force: each older backup's fingerprint is of the file the next one copied
        r = gridfix.undo_write(nml) if w["step"] == "grids" else library.undo(nml, force=force and i == 0)
        if r.get("restored_from") != w["backup"]:
            raise SystemExit(f"Undo stopped: restored {r.get('restored_from')}, expected the sync's {w['backup']} "
                             f"(another write happened since). Restored so far: {len(restored) + 1}.")
        restored.append(w["step"])
    f.unlink()
    return {"undone": restored, "app": library.app_name()}


def text(d: dict) -> str:
    lines = [f"Sync to {d['app']}" + (" (preview, nothing written)" if d["dry_run"] else "") + ":"]
    for s, r in d["steps"].items():
        lines.append(f"  {s}: " + (f"skipped ({r['skipped']})" if "skipped" in r
                                   else ("changes" if r.get("changed") else "nothing to change")))
    if d.get("undo"):
        lines.append(f"Undo all of it: {d['undo']}")
    return "\n".join(lines)


def main(args) -> None:
    if args.undo:
        print(json.dumps(undo(args.nml, force=args.force), indent=2, ensure_ascii=False))
        return
    d = run(args.nml, dry_run=args.dry_run, steps=args.only.split(",") if args.only else None,
          cues_from_other=args.cues_from_other_app)
    print(json.dumps(d, indent=2, ensure_ascii=False) if args.json else text(d))
