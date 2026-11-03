"""Sync: everything the app has for your DJ app in one go, for the app chosen on the Setup tab (App → DJ app only).

Steps, each through its own writer: tracks themselves (relink moved files, add analysed tracks the app lacks: trackfiles), genres + predicted colours + genre smart playlists (library.sync), hype in the
Comment (library.write_hype), approved cues (cues.apply), approved grid fixes (gridfix.apply, Traktor only) and the
warm-up/peak/closing crates (setplan.crates_to_traktor) and the loops saved as samples (_samples). Set playlists aren't rebuilt: each needs its own arc and
name (`set --traktor`). A step with nothing to do is skipped with its reason. Every write makes its own backup;
they're listed in data/sync-last.json so one undo puts back the library from before the whole sync.

The copy (copy=True) is the other job: what the other DJ app has (Traktor <-> Rekordbox) into the chosen one, with
its own steps (COPY_STEPS) and its own undo (data/copy-last.json). The other app's values win where it has one;
an empty value never erases."""
from __future__ import annotations

import json
from pathlib import Path

from . import analysis, library
from .analysis import log

LAST = "sync-last.json"  # in data/
COPY_LAST = "copy-last.json"
STEPS = ("tracks", "genres", "hype", "cues", "grids", "crates", "samples")
COPY_STEPS = ("tracks", "genres", "colours", "cues", "grids", "playlists", "samples")


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


def _grids(nml, dry_run):
    from . import gridfix

    if library.app() != "traktor":
        raise SystemExit("Fixing grids is Traktor only.")
    return gridfix.apply(nml, dry_run=dry_run)


def _crates(nml, dry_run):
    from . import setplan

    return setplan.crates_to_traktor(setplan.crates(nml=nml), nml, dry_run=dry_run)


RB_SAMPLES = "Track Analyzer loops"  # in Rekordbox's sampler folder, ~/Music/rekordbox/Sampler


def _samples(nml, dry_run):
    """Loops saved as samples on the page: forget the ones whose WAV is gone, then add the rest to the DJ app's
    collection. Rekordbox gets copies in its sampler folder first (left there by an undo: master.db is what's
    restored). Only adds: library tracks are never relinked to a sample. Sampler kits are a later stage (IDEAS.md)."""
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
    try:
        r = _add_samples(files, nml, dry_run, RB_SAMPLES)
    except SystemExit:
        raise SystemExit(f"every saved sample is already in {library.app_name()}"
                         + (f"; {len(gone)} gone, forgotten" if gone else "")) from None
    return out | r


def _add_samples(files: list[Path], nml, dry_run, sub: str) -> dict:
    """Add these WAVs to the DJ app's collection as they are (never a relink). Rekordbox gets copies in its sampler
    folder (`sub` in it) first, left there by an undo: master.db is what's restored."""
    import shutil

    out = {}
    if library.app() == "rekordbox":
        from . import rekordbox

        dest = rekordbox.SAMPLER / sub
        pairs = [(f, f if rekordbox.in_sampler(str(f)) else dest / f.name) for f in files]
        if not dry_run:
            dest.mkdir(parents=True, exist_ok=True)
            for a, b in pairs:
                if not b.exists():
                    shutil.copy2(a, b)
        out["copied_to"] = str(dest)
        files = [b if b.exists() else a for a, b in pairs]  # a preview adds the originals: same count
    tracks = [{"path": str(f), "title": f.stem, "artist": None, "size": f.stat().st_size} for f in files]
    return out | library.sync_tracks(nml, dry_run=dry_run, tracks=tracks, relink=False)


WRITERS = {"tracks": _tracks, "genres": _genres, "hype": _hype, "cues": _cues, "grids": _grids, "crates": _crates,
           "samples": _samples}


# ---- the copy: the other DJ app's library into the chosen one ----------------------------------------------------

def other_app() -> str:
    return "rekordbox" if library.app() == "traktor" else "traktor"


def other_name() -> str:
    return library.APPS[other_app()]


def _values(app: str, nml=None) -> dict[str, tuple[str, str | None, int | None]]:
    """{NFC path: (the app's own path string, genre or None, colour 1-7 on our scale or None)} for tracks on disk."""
    import xml.etree.ElementTree as ET

    from . import rekordbox, traktor
    from .trackfiles import nfc

    out = {}
    if app == "traktor":
        root = ET.parse(traktor.find_collection(nml)).getroot()
        for e in root.find("COLLECTION").iter("ENTRY"):
            loc, info = e.find("LOCATION"), e.find("INFO")
            if loc is None:
                continue
            raw = next((c for c in traktor.location_paths(loc.attrib) if Path(c).is_file()), None)
            if not raw:
                continue
            a = info.attrib if info is not None else {}
            c = a.get("COLOR") or ""
            out[nfc(raw)] = (raw, (a.get("GENRE") or "").strip() or None,
                             int(c) if c.isdigit() and 1 <= int(c) <= 7 else None)
    else:
        rows = rekordbox.read(nml)
        for raw, t in rekordbox._live(rows).values():
            out[nfc(raw)] = (raw, (t["genre"] or "").strip() or None, rekordbox.RB_TO_OUR.get(t["color"] or 0))
    return out


def _source(nml):
    """The other app's values; its own collection, never the --nml (that's the chosen app's)."""
    try:
        return _values(other_app())
    except (SystemExit, OSError, ValueError) as err:
        raise SystemExit(f"Couldn't read {other_name()}: {err}") from None


def _under(p: str, dirs: list[str]) -> bool:
    return any(Path(p).is_relative_to(d) for d in dirs)


def _sorted_out(paths) -> tuple[list[str], list[str], int]:
    """(tracks, samples, recordings left out) among the other app's files. Samples: Rekordbox's sampler folder or
    Traktor's Loops folder. Recordings: Traktor's (its Recordings folders, never copied)."""
    from . import rekordbox, traktor

    rec = traktor.recording_dirs(settings=("Browser.Dir.Recordings",))
    loops = traktor.recording_dirs(settings=("Browser.Dir.Loops",))
    loops = [d for d in loops if d not in rec]
    tracks, samples, n_rec = [], [], 0
    for p in sorted(paths):
        if "/TraktorContent/" in p:  # Traktor's own bundled content
            continue
        if rekordbox.in_sampler(p) or _under(p, loops):
            samples.append(p)
        elif _under(p, rec):
            n_rec += 1
        else:
            tracks.append(p)
    return tracks, samples, n_rec


def _copy_tracks(nml, dry_run):
    files, _samples_, n_rec = _sorted_out(_source(nml))
    tracks = [{"path": p, "title": Path(p).stem, "artist": None, "size": Path(p).stat().st_size} for p in files]
    try:
        r = library.sync_tracks(nml, dry_run=dry_run, tracks=tracks, relink=False)
    except SystemExit:
        raise SystemExit(f"every track in {other_name()} is already in {library.app_name()}"
                         + (f" ({n_rec} recordings left out)" if n_rec else "")) from None
    return r | ({"recordings_left_out": n_rec} if n_rec else {})


def _copy_samples(nml, dry_run):
    """The other app's samples (its sampler's or loops folder's WAVs) as samples here."""
    samples = [Path(p) for p in _sorted_out(_source(nml))[1]]
    if not samples:
        raise SystemExit(f"{other_name()} has no samples in its collection")
    try:
        return _add_samples(samples, nml, dry_run, f"From {other_name()}")
    except SystemExit:
        raise SystemExit(f"every sample in {other_name()} is already in {library.app_name()}") from None


def _diff(nml, field: int):
    src, tgt = _source(nml), _values(library.app(), nml)
    return {raw: (cur[field], src[k][field]) for k, cur in tgt.items() for raw in [cur[0]]
            if k in src and src[k][field] and src[k][field] != cur[field]}


def _copy_genres(nml, dry_run):
    ch = _diff(nml, 1)
    if not ch:
        raise SystemExit(f"every track has {other_name()}'s genre already (or {other_name()} has none)")
    return library.backend().set_genres(ch, library._path(nml), dry_run=dry_run)


def _copy_colours(nml, dry_run):
    ch = _diff(nml, 2)
    if not ch:
        raise SystemExit(f"every track has {other_name()}'s colour already (or {other_name()} has none)")
    return library.set_colors(ch, nml, dry_run=dry_run)


def _copy_cues(nml, dry_run):
    from . import cues

    return cues.apply(nml, dry_run=dry_run, other="only")  # the other DJ app's hot cues, into free slots only


def _copy_grids(nml, dry_run):
    from . import cues, rekordbox, traktor

    if library.app() != "rekordbox":
        raise SystemExit("Rekordbox's beat grids into Traktor aren't copied yet: only Traktor's into Rekordbox")
    try:
        tk = cues.grids(str(traktor.find_collection(None)))
    except (SystemExit, OSError, ValueError) as err:
        raise SystemExit(f"Couldn't read Traktor's grids: {err}") from None
    out = rekordbox.write_grids(tk, nml, dry_run)
    if not out.get("changed"):
        raise SystemExit("every analysed track already has Traktor's grid" + (
            f"; {len(out['not_analysed'])} not analysed in Rekordbox yet" if out["not_analysed"] else ""))
    return out


def copy_folder() -> str:
    return f"From {other_name()} (Track Analyzer)"


def _copy_playlists(nml, dry_run):
    """The other app's playlists (not this app's own folders, nor a copy made the other way) as plain playlists in
    one folder, "From <app> (Track Analyzer)", named by their path there; each copy replaces the one before."""
    from . import rekordbox, traktor

    try:
        lists = (traktor.playlists(None) if other_app() == "traktor" else rekordbox.playlists(None))
    except (SystemExit, OSError, ValueError) as err:
        raise SystemExit(f"Couldn't read {other_name()}'s playlists: {err}") from None
    lists = [p for p in lists if not p["path"].startswith("From ")]
    if not lists:
        raise SystemExit(f"{other_name()} has no playlists with tracks on disk")
    folder = copy_folder()
    from . import cues
    from .trackfiles import nfc

    pending = {nfc(p) for p in cues.PENDING_TRACKS}  # a preview: tracks the tracks step adds count as there

    def soon(files: list[str], have) -> int:
        return sum(1 for f in files if nfc(f) in pending and not have(f))

    if library.app() == "rekordbox":
        out = rekordbox.write_playlists({p["path"]: p["files"] for p in lists}, folder, nml, dry_run, first=False,
                                        what="the playlists")
        gone = {nfc(m) for m in out.pop("missing", [])}
        for p in lists:
            out["playlists"][p["path"]] += soon(p["files"], lambda f: nfc(f) not in gone)
        out["not_in_this_app"] = out.pop("not_in_rekordbox") - sum(soon(p["files"], lambda f: nfc(f) not in gone) for p in lists)
        return _listed(out)
    import xml.etree.ElementTree as ET

    from . import setplan

    path = traktor.find_collection(nml)
    traktor.refuse_if_tempo_view(path)
    text = path.read_text(encoding="utf-8")
    by_path = {nfc(p): k for k, ps in setplan._entry_paths(ET.fromstring(text.encode("utf-8"))).items() for p in ps}
    new, written, missing = text, {}, 0
    for p in lists:
        keys = [by_path.get(nfc(f)) for f in p["files"]]
        missing += keys.count(None)
        keys = [k for k in keys if k]
        new = setplan._with_playlist(new, p["path"], setplan._playlist_node(p["path"], keys, folder), folder,
                                     first=False)[0]
        later = soon(p["files"], lambda f: nfc(f) in by_path)
        written[p["path"]] = len(keys) + later
        missing -= later
    return _listed(traktor._save(path, text, new, {"folder": folder, "playlists": written,
                                                   "not_in_this_app": missing}, dry_run))


def _listed(out: dict) -> dict:
    """The page's preview shows counts and `changes`: each playlist with its tracks."""
    out["playlists_copied"] = len(out["playlists"])
    out["changes"] = [f"{out['folder']} / {n}: {k} tracks" for n, k in out["playlists"].items()]
    return out


COPY_WRITERS = {"tracks": _copy_tracks, "genres": _copy_genres, "colours": _copy_colours, "cues": _copy_cues,
                "grids": _copy_grids, "playlists": _copy_playlists, "samples": _copy_samples}


from . import cues  # noqa: E402


def run(nml: str | None = None, dry_run: bool = False, steps: list[str] | None = None, copy: bool = False) -> dict:
    """Preview (dry_run) or apply each step in order. Stops at the first error; what was written before it stays
    listed for the undo. copy: the other DJ app's library instead of the analyzer's (COPY_STEPS)."""
    writers, names = (COPY_WRITERS, COPY_STEPS) if copy else (WRITERS, STEPS)
    steps = list(steps or names)
    if bad := [s for s in steps if s not in writers]:
        raise SystemExit(f"Unknown step(s) {', '.join(bad)}: choose from {', '.join(names)}.")
    library.check_closed(dry_run, "the copy" if copy else "the sync")  # once up front: a SystemExit from a step below means "nothing to do"
    out = {"app": library.app_name(), "dry_run": dry_run, "steps": {}, "changed": False}
    if copy:
        out["from"] = other_name()
    written = []
    try:
        for s in steps:
            log(f"Sync: {s}" + (" (preview)" if dry_run else ""))
            try:
                r = writers[s](nml, dry_run)
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
            (analysis.HOME / (COPY_LAST if copy else LAST)).write_text(
                json.dumps({"app": library.app(), "steps": written}, indent=1))
            out["undo"] = "uv run dj-track-analyzer sync --undo" + (" --copy" if copy else "")
    return out


def undo(nml: str | None = None, force: bool = False, copy: bool = False) -> dict:
    """Put back the library from before the last sync: each of its backups, newest first. Refuses (unless force) when
    the DJ app changed the library since the sync wrote it, as the plain undo does."""
    from . import gridfix

    f = analysis.HOME / (COPY_LAST if copy else LAST)
    try:
        last = json.loads(f.read_text())
    except (OSError, ValueError):
        raise SystemExit("No copy to undo." if copy else "No sync to undo.")
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
        r = gridfix.undo_write(nml) if w["step"] == "grids" and not copy else library.undo(
            nml, force=force or i > 0 or len(todo) < len(last["steps"]))
        if r.get("restored_from") != w["backup"]:
            raise SystemExit(f"Undo stopped: restored {r.get('restored_from')}, expected the sync's {w['backup']} "
                             f"(another write happened since). Restored so far: {len(restored) + 1}.")
        restored.append(w["step"])
    f.unlink()
    return {"undone": restored, "app": library.app_name()}


def text(d: dict) -> str:
    lines = [(f"Copy from {d['from']} to {d['app']}" if d.get("from") else f"Sync to {d['app']}") + (" (preview, nothing written)" if d["dry_run"] else "") + ":"]
    for s, r in d["steps"].items():
        lines.append(f"  {s}: " + (f"skipped ({r['skipped']})" if "skipped" in r
                                   else ("changes" if r.get("changed") else "nothing to change")))
    if d.get("undo"):
        lines.append(f"Undo all of it: {d['undo']}")
    return "\n".join(lines)


def main(args) -> None:
    if args.undo:
        print(json.dumps(undo(args.nml, force=args.force, copy=args.copy), indent=2, ensure_ascii=False))
        return
    d = run(args.nml, dry_run=args.dry_run, steps=args.only.split(",") if args.only else None, copy=args.copy)
    print(json.dumps(d, indent=2, ensure_ascii=False) if args.json else text(d))
