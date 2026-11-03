"""Sync: everything the app has for your DJ app in one go, for the app chosen on the Setup tab (App → DJ app only).

Steps, each through its own writer: tracks themselves (relink moved files, add analysed tracks the app lacks: trackfiles), genres + predicted colours + genre smart playlists (library.sync), hype in the
Comment (library.write_hype), approved cues (cues.apply), the other DJ app's hot cues into free slots (cues.apply other="only"), approved grid fixes (gridfix.apply, Traktor only) and the
warm-up/peak/closing crates (setplan.crates_to_traktor) and the loops saved as samples (_samples). Set playlists aren't rebuilt: each needs its own arc and
name (`set --traktor`). A step with nothing to do is skipped with its reason. Every write makes its own backup;
they're listed in data/sync-last.json so one undo puts back the library from before the whole sync."""
from __future__ import annotations

import json
from pathlib import Path

from . import analysis, library
from .analysis import log

LAST = "sync-last.json"  # in data/
STEPS = ("tracks", "genres", "hype", "cues", "othercues", "grids", "othergrids", "crates", "samples")


def _tracks(nml, dry_run):
    return library.sync_tracks(nml, dry_run=dry_run)  # first: the steps after it then reach new tracks too


def _genres(nml, dry_run):
    return library.sync(nml, dry_run=dry_run, update_genres=True, make_smartlists=True, colors=True,
                        overwrite_colors=False)


def _hype(nml, dry_run):
    from . import hype

    return library.write_hype(hype.compute(), nml, dry_run=dry_run)


def _cues(nml, dry_run):
    from . import cues

    return cues.apply(nml, dry_run=dry_run, restore=True)  # also puts back approved cues deleted since


def _othercues(nml, dry_run):
    from . import cues

    return cues.apply(nml, dry_run=dry_run, other="only")  # the other DJ app's hot cues, into free slots only


def _grids(nml, dry_run):
    from . import gridfix

    if library.app() != "traktor":
        raise SystemExit("Fixing grids is Traktor only.")
    return gridfix.apply(nml, dry_run=dry_run)


def _othergrids(nml, dry_run):
    from . import cues, rekordbox, traktor

    if library.app() != "rekordbox":
        raise SystemExit("Rekordbox only: Traktor's grids into Rekordbox")
    try:
        tk = cues.grids(str(traktor.find_collection(None)))
    except (SystemExit, OSError, ValueError) as err:
        raise SystemExit(f"Couldn't read Traktor's grids: {err}") from None
    out = rekordbox.write_grids(tk, nml, dry_run)
    if not out.get("changed"):
        raise SystemExit("every analysed track already has Traktor's grid" + (
            f"; {len(out['not_analysed'])} not analysed in Rekordbox yet" if out["not_analysed"] else ""))
    return out


def _crates(nml, dry_run):
    from . import setplan

    return setplan.crates_to_traktor(setplan.crates(nml=nml), nml, dry_run=dry_run)


RB_SAMPLES = "Track Analyzer loops"  # in Rekordbox's sampler folder, ~/Music/rekordbox/Sampler


def _samples(nml, dry_run):
    """Loops saved as samples on the page: forget the ones whose WAV is gone, then add the rest to the DJ app's
    collection. Rekordbox gets copies in its sampler folder first (left there by an undo: master.db is what's
    restored). Only adds: library tracks are never relinked to a sample. Sampler kits are a later stage (IDEAS.md)."""
    import shutil
    from pathlib import Path

    from . import web

    con = web._saved_db()
    try:
        rows = con.execute("SELECT file, src_name FROM saved_sample").fetchall()
        gone = [f for f, _ in rows if not Path(f).is_file()]
        if gone and not dry_run:
            con.executemany("DELETE FROM saved_sample WHERE file=?", [(f,) for f in gone])
            con.commit()
    finally:
        con.close()
    files = sorted(Path(f) for f, _ in rows if Path(f).is_file())
    out = {"forgotten": len(gone), "forgotten_examples": [Path(f).name for f in gone[:10]]}
    if not files:
        raise SystemExit("no loops saved on the page to add" + (
            f" ({len(gone)} saved earlier no longer exist on disk, so they're forgotten)" if gone else "")
            + ". Samples already in the other DJ app aren't copied by this step")
    rb = library.app() == "rekordbox"
    if rb:
        from . import rekordbox

        dest = rekordbox.SAMPLER / RB_SAMPLES
        pairs = [(f, dest / f.name) for f in files]
        if not dry_run:
            dest.mkdir(parents=True, exist_ok=True)
            for a, b in pairs:
                if not b.exists():
                    shutil.copy2(a, b)
        out["copied_to"] = str(dest)
        files = [b if b.exists() else a for a, b in pairs]  # a preview adds the originals: same count
    tracks = [{"path": str(f), "title": f.stem, "artist": None, "size": f.stat().st_size} for f in files]
    try:
        r = library.sync_tracks(nml, dry_run=dry_run, tracks=tracks, relink=False)
    except SystemExit:
        raise SystemExit(f"every saved sample is already in {library.app_name()}"
                         + (f"; {len(gone)} gone, forgotten" if gone else ""))
    return out | r


WRITERS = {"tracks": _tracks, "genres": _genres, "hype": _hype, "cues": _cues, "othercues": _othercues, "grids": _grids, "othergrids": _othergrids, "crates": _crates, "samples": _samples}


from . import cues  # noqa: E402


def run(nml: str | None = None, dry_run: bool = False, steps: list[str] | None = None) -> dict:
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
                r = WRITERS[s](nml, dry_run)
            except SystemExit as err:
                out["steps"][s] = {"skipped": str(err)}
                continue
            r.pop("undo", None)  # the sync's own undo replaces each step's
            if dry_run and r.get("add_paths"):  # a preview adds nothing: the cue step counts these as added
                cues.PENDING_TRACKS = list(r["add_paths"])
            r.pop("add_paths", None)
            out["steps"][s] = r
            out["changed"] |= bool(r.get("changed"))
            if r.get("backup"):
                written.append({"step": s, "backup": r["backup"]})
    finally:
        cues.PENDING_TRACKS = []
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
    # resume an undo that stopped part way: steps whose backup was already put back are renamed *.restored
    todo = [w for w in reversed(last["steps"]) if not Path(w["backup"] + ".restored").exists()]
    for i, w in enumerate(todo):
        # only the newest step is checked for changes made by the DJ app: the older ones are this sync's own writes,
        # one after another. Their fingerprints can still differ (Rekordbox's master.db changes bytes when a
        # step that ends up changing nothing opens and closes it), so they're always forced.
        r = gridfix.undo_write(nml) if w["step"] == "grids" else library.undo(
            nml, force=force or i > 0 or len(todo) < len(last["steps"]))
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
    d = run(args.nml, dry_run=args.dry_run, steps=args.only.split(",") if args.only else None)
    print(json.dumps(d, indent=2, ensure_ascii=False) if args.json else text(d))
