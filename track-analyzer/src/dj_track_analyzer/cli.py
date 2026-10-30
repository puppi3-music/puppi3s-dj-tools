"""Command line: `uv run dj-track-analyzer <command>`. With no command it runs the MCP server over stdio."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analysis import (analyze_file, check_rules, download_models, log, split_style, style_classes,
                       train)
from .mcp_server import protect_stdout, build_mcp


# Commands that follow the tempo view loaded in Traktor (if any); --native forces the normal library.
VIEW_CMDS = {"hype", "stats", "export"}
# Commands that always use the normal analysis (each track at its own tempo).
NATIVE_CMDS = {"analyze", "scan", "tag", "untag", "traktor", "colors", "regenre"}


def pick_library(args) -> None:
    """Point this command at the loaded tempo view where it makes sense, and say which library it uses."""
    from .analysis import HOME
    from .collection import DB
    from .traktor import TEMPO_STATE

    state = HOME / TEMPO_STATE
    bpm = json.loads(state.read_text()).get("bpm") if state.exists() else None
    if args.cmd in VIEW_CMDS and bpm and not args.native:
        from . import tempo

        tempo.view_db(bpm).close()  # switches collection.DB / the cache to the view
        log(f"Library: {bpm:g} BPM tempo view, loaded in Traktor ({tempo.view_home(bpm)}). "
            "Add --native for each track at its own tempo")
        return
    note = f"Library: normal, each track at its own tempo ({DB})"
    if bpm:
        note += (f"\n  Note: Traktor has the {bpm:g} BPM view loaded, so its Genre/Colour/Comment show "
                 f"{bpm:g} BPM values, not these")
    log(note)


def say_if_nothing(out: dict, dry_run: bool = False) -> None:
    """After a command that writes, say plainly when there was nothing to change."""
    parts = [v for v in out.values() if isinstance(v, dict)] if "changed" not in out else [out]
    flags = [d["changed"] for d in parts if "changed" in d]
    counts = [out[k] for k in ("written", "would_write", "restored", "would_restore", "top_genre_changed") if k in out]
    if (flags and not any(flags)) or (not flags and counts and not any(counts)):
        log("Nothing would change." if dry_run else "Nothing to change: everything is already up to date.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Local track analyzer + MCP server")
    sub = ap.add_subparsers(dest="cmd")
    su = sub.add_parser("setup", help="download the style models")
    su.add_argument("--no-maest", action="store_true", help="skip the larger MAEST genre model")
    sub.add_parser("styles", help="list the 400 Discogs style names and check dj_genres.json")
    a = sub.add_parser("analyze", help="analyze one file and print JSON")
    a.add_argument("file")
    a.add_argument("--segments", type=float, default=None, help="timeline segment length in seconds")
    a.add_argument("--tracklist", nargs="?", const="auto", default=None, metavar="HISTORY.nml",
                   help="for a recorded mix: check it against Traktor's history file of that session "
                        "(default: found from the recording's date)")
    a.add_argument("--chapters", action="store_true",
                   help="with --tracklist: YouTube chapter lines for the video's description (needs --offset)")
    a.add_argument("--offset", default=None, metavar="SECONDS",
                   help="how far into the finished video the first track starts (14 or 0:14; read it off the video), "
                        "for --chapters and --highlights times")
    a.add_argument("--highlights", nargs="?", type=int, const=2, default=0, metavar="N",
                   help="for a mix: suggest the N best stretches for short clips (default 2)")
    a.add_argument("--clip-length", type=float, default=40.0, metavar="SECONDS",
                   help="length of each --highlights clip (default 40)")
    a.add_argument("--json-only", action="store_true", help="for a mix: leave out the readable summary after the JSON")
    a.add_argument("--start", default=None, metavar="HH:MM:SS",
                   help="with --tracklist: when the recording started (read from Traktor's recording name if left out)")
    cl = sub.add_parser("clips", help="previews of a mix's clip suggestions from the finished video, then the chosen clips")
    cl.add_argument("analysis", help="the JSON from analyze --highlights N --offset M:SS")
    cl.add_argument("--video", required=True, help="the finished video the offset was read off")
    cl.add_argument("--pick", nargs="+", metavar="N", default=None,
                    help="make these clips at full quality: suggestion numbers (1 4) or own ranges (7:35-8:15); "
                         "without it: 720p previews of every suggestion")
    cl.add_argument("--previews", default=None, metavar="DIR",
                    help="folder for previews; they go in a subfolder named after the analysis (default: /tmp/clip-previews)")
    cl.add_argument("--out", default=None, metavar="DIR", help="where --pick clips go (default: next to the video)")
    cl.add_argument("--height", type=int, default=1440, help="--pick clips' height in pixels (default 1440)")
    cl.add_argument("--bitrate", default="16M", help="--pick clips' video bitrate (default 16M)")
    cl.add_argument("--force", action="store_true", help="replace --pick clips that are already there")
    cl.add_argument("--dry-run", action="store_true", help="list what would be made, write nothing")
    sc = sub.add_parser("scan", help="analyze a whole collection (resumable), save to database + spreadsheet")
    sc.add_argument("folders", nargs="*", help="folders (or files) to scan")
    sc.add_argument("--traktor", action="store_true", help="scan every track in Traktor's collection (can be combined with folders)")
    sc.add_argument("--nml", default=None, help="path to Traktor's collection.nml (default: found automatically)")
    sc.add_argument("--exclude", action="append", default=[], metavar="FOLDER",
                    help="leave this folder out (repeat for several)")
    sc.add_argument("--include-recordings", action="store_true",
                    help="also scan Traktor's own mix recordings (skipped by default)")
    sc.add_argument("--workers", type=int, default=None, help="parallel analyzers (default: half your CPU cores, max 5)")
    sc.add_argument("--rescan", action="store_true", help="re-analyze files even if already scanned")
    sc.add_argument("--write-tags", metavar="FIELDS", default=None,
                    help="also write results into the files' tags, e.g. genre or genre,bpm,camelot (fields: genre,bpm,key,camelot)")
    sc.add_argument("--csv", default=None, help="where to write the spreadsheet (default data/collection.csv)")
    tg = sub.add_parser("tag", help="write scanned genres (and optionally BPM/key) into your files' tags")
    tg.add_argument("folders", nargs="*", help="limit to these folders (default: everything scanned)")
    tg.add_argument("--fields", default="genre", help="genre (default), or e.g. genre,bpm,camelot  (fields: genre,bpm,key,camelot)")
    tg.add_argument("--min-score", type=float, default=0.0, help="skip tracks whose genre score is below this (e.g. 0.2)")
    tg.add_argument("--use-my-genre", action="store_true", help="write your trained genres instead, where available")
    tg.add_argument("--dry-run", action="store_true", help="show what would change without touching any file")
    tg.add_argument("--multi", action="store_true", help='also add close runner-up genres, e.g. "Acid Trance; Hard Trance"')
    tg.add_argument("--multi-ratio", type=float, default=0.7, help="a runner-up needs at least this share of the top score (default 0.7)")
    tg.add_argument("--max-genres", type=int, default=3, help="most genres per track with --multi (default 3)")
    ut = sub.add_parser("untag", help="restore the genre/BPM/key tags files had before tagging")
    ut.add_argument("folders", nargs="*")
    ut.add_argument("--dry-run", action="store_true")
    tk = sub.add_parser("traktor", help="put tagged genres into Traktor's collection and create genre Smartlists")
    tk.add_argument("--nml", default=None, help="path to collection.nml (default: newest Traktor folder in ~/Documents/Native Instruments)")
    tk.add_argument("--dry-run", action="store_true", help="show what would change without touching anything")
    tk.add_argument("--smartlists-only", action="store_true", help="only (re)create the Smartlists, don't update genres")
    tk.add_argument("--no-smartlists", action="store_true", help="only update genres in the collection")
    tk.add_argument("--undo", action="store_true", help="restore collection.nml from before the last run")
    tk.add_argument("--force", action="store_true",
                    help="with --undo: restore even if Traktor changed the collection since (loses those changes)")
    tk.add_argument("--no-colors", action="store_true", help="don't set predicted colours")
    tk.add_argument("--overwrite-colors", action="store_true", help="also replace colours you set yourself (not recommended)")
    tk.add_argument("--color-min-confidence", type=float, default=0.0, help="only set predicted colours at or above this confidence")
    co = sub.add_parser("colors", help="learn your Traktor colour scale from tracks you've coloured")
    co.add_argument("action", choices=["train", "review", "explain", "apply", "guessed"],
                    help="train: learn your scale. review: tracks that disagree with their colour "
                         "(--apply recolours the clearest ones in Traktor). "
                         "explain: what each colour means in your collection. "
                         "apply: scan new Traktor tracks and colour the uncoloured ones. "
                         "guessed: the colours apply / traktor wrote that nobody has checked (train leaves them out)")
    co.add_argument("--nml", default=None, help="path to collection.nml")
    co.add_argument("--workers", type=int, default=None, help="parallel workers for the deep listening pass")
    co.add_argument("--no-deep", action="store_true", help="skip the MAEST/mood features (faster, less accurate)")
    co.add_argument("--dry-run", action="store_true", help="apply / review --apply: show which colours would be set, change nothing")
    co.add_argument("--min-confidence", type=float, default=None,
                    help="apply: only set predicted colours at or above this confidence (0-1, default 0). "
                         "review --apply: only recolour tracks at or above it (default 0.75)")
    co.add_argument("--apply", action="store_true",
                    help="review: recolour the tracks over the thresholds in Traktor (Traktor closed; backup + traktor --undo)")
    co.add_argument("--yes", action="store_true",
                    help="review --apply: recolour every track over the thresholds without asking about each one")
    co.add_argument("--min-gap", type=float, default=2.5,
                    help="review --apply: only recolour tracks this many colours away from yours or more (default 2.5)")
    co.add_argument("--kept", action="store_true",
                    help="review: list the tracks you answered [k]eep always for (review --apply skips them)")
    co.add_argument("--keep", metavar="TEXT", default=None,
                    help="review --kept: add the coloured tracks whose path contains TEXT to the keep-always list")
    co.add_argument("--forget", metavar="TEXT", default=None,
                    help="review --kept: take tracks whose path contains TEXT off the keep-always list")
    co.add_argument("--accept", metavar="TEXT", default=None,
                    help="guessed: these guessed colours are right, learn from them: the tracks whose path contains TEXT "
                         "(at most 10), or `all`")
    co.add_argument("--json", action="store_true", help="explain: print JSON instead of tables")
    hy = sub.add_parser("hype", help="find tracks by how hype they are (1-10), optionally write it to Traktor's Comment")
    hy.add_argument("--least", action="store_true", help="least hype (chill) first")
    hy.add_argument("--colour", "--color", dest="colour", default=None, help="only this colour (yours, else predicted)")
    hy.add_argument("--bpm", type=float, default=None, help="only tracks near this BPM")
    hy.add_argument("--bpm-tolerance", type=float, default=4.0, help="BPM window in %% (default 4)")
    hy.add_argument("--key", default=None, help="only tracks that mix with this Camelot key, e.g. 8A")
    hy.add_argument("--gems", action="store_true", help="hype 8+ but rated 4 stars or less, or played at most once")
    hy.add_argument("--limit", type=int, default=25)
    hy.add_argument("--traktor", action="store_true", help="write 'Hype NN' into Comment (Traktor closed)")
    hy.add_argument("--remove", action="store_true", help="with --traktor: take the Hype part out of Comment again")
    hy.add_argument("--clean-comment2", action="store_true",
                    help="with --traktor: only take old 'Hype NN' out of Comment 2 (your own text there stays)")
    hy.add_argument("--dry-run", action="store_true", help="with --traktor: show what would change")
    hy.add_argument("--force", action="store_true",
                    help="with --traktor: write even though Traktor is set to write tags into your files")
    hy.add_argument("--nml", default=None, help="path to collection.nml")
    hy.add_argument("--native", action="store_true", help="use the normal library even while a tempo view is loaded")
    hy.add_argument("--json", action="store_true", help="print JSON instead of a table")
    te = sub.add_parser("tempo", help="your catalogue as heard at one tempo with key lock (default 145 BPM)")
    te.add_argument("action", choices=["scan", "report", "export", "nml", "load", "native", "refresh", "status"],
                    help="scan: analyse every track stretched to --bpm (cached, resumable). report: what changes. "
                         "export: the view's spreadsheet. nml: a standalone collection.nml of the view. "
                         "load: switch Traktor to the view (Traktor closed). native: switch back. "
                         "refresh: redo genres/colours/hype from the cache. status")
    te.add_argument("--bpm", type=float, default=145.0, help="the tempo you play at (default 145)")
    te.add_argument("--workers", type=int, default=None, help="scan: parallel analyzers (default: half your CPU cores, max 5)")
    te.add_argument("--rescan", action="store_true", help="scan: analyse again even if cached")
    te.add_argument("--nml", default=None, help="path to Traktor's collection.nml")
    te.add_argument("--out", default=None, help="export / nml: where to write (default data/at-<bpm>/)")
    te.add_argument("--dry-run", action="store_true", help="load / native: show what would change")
    te.add_argument("--force", action="store_true",
                    help="load: even while Traktor is set to write tags to your files")
    te.add_argument("--lexicon", action="store_true",
                    help="load / native: switch Lexicon too (Lexicon open, Settings → Integrations → Local API on)")
    te.add_argument("--limit", type=int, default=15, help="report: tracks per list")
    te.add_argument("--json", action="store_true", help="report: print JSON")
    cu = sub.add_parser("cues", help="cue points and a mix loop per track, learned from the cues you set yourself")
    cu.add_argument("action", choices=["train", "scan", "show", "apply"],
                    help="train: learn from your own cues (listens to the cued tracks first). "
                         "scan: listen to every track with a beat grid. show: suggestions for one track. "
                         "apply: write the cues you approved in the web page into Traktor (Traktor closed)")
    cu.add_argument("track", nargs="?", default=None, help="show: part of the track's file name")
    cu.add_argument("--nml", default=None, help="path to collection.nml")
    cu.add_argument("--workers", type=int, default=None, help="parallel listeners (default: half your CPU cores, max 5)")
    cu.add_argument("--rescan", action="store_true", help="scan: listen again even if measured")
    cu.add_argument("--dry-run", action="store_true", help="apply: show what would be written, change nothing")
    gr = sub.add_parser("grids", help="check that Traktor's beat grids sit on the kicks, and move the ones you approve")
    gr.add_argument("action", choices=["check", "show", "apply"],
                    help="check: measure every track with a beat grid (cached). show: one track's measurement and "
                         "its stem file. apply: move the grids you approved in the web page, with your cues (Traktor closed)")
    gr.add_argument("track", nargs="?", default=None, help="show: part of the track's file name")
    gr.add_argument("--nml", default=None, help="path to collection.nml")
    gr.add_argument("--workers", type=int, default=None, help="check: parallel workers (default: half your CPU cores, max 6)")
    gr.add_argument("--rescan", action="store_true", help="check: measure again even if measured")
    gr.add_argument("--dry-run", action="store_true", help="apply: show what would be written, change nothing")
    sub.add_parser("regenre", help="recompute the genres of scanned tracks from the cache (after editing dj_genres.json)")
    ex = sub.add_parser("export", help="write the scanned collection to a CSV spreadsheet")
    ex.add_argument("out", nargs="?", default=None)
    ex.add_argument("--native", action="store_true", help="use the normal library even while a tempo view is loaded")
    st = sub.add_parser("stats", help="summary of the scanned collection")
    st.add_argument("--native", action="store_true", help="use the normal library even while a tempo view is loaded")
    t = sub.add_parser("train", help="train your own genres from a folder")
    t.add_argument("folder")
    w = sub.add_parser("web", help="browse everything in a local web page (also holds, cue approvals, loop samples)")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8766)
    w.add_argument("--logs", action="append", default=None, metavar="FOLDER",
                   help="folder with saved JSON output, e.g. analyze results of mixes (default logs/; repeat for several)")
    w.add_argument("--no-open", action="store_true", help="don't open the browser")
    w.add_argument("--export", metavar="FILE.html", default=None,
                   help="save a read-only snapshot of the page as one HTML file instead of serving it")
    ho = sub.add_parser("hold", help="keep tracks' genre or colour as they are when writing to files, Traktor or Lexicon")
    ho.add_argument("--genre", metavar="TEXT", default=None, help="hold the genre of the scanned tracks whose path contains TEXT")
    ho.add_argument("--colour", "--color", dest="colour", metavar="TEXT", default=None,
                    help="hold the colour of the scanned tracks whose path contains TEXT")
    ho.add_argument("--release", action="store_true", help="with --genre/--colour: take those tracks off the hold list")
    s = sub.add_parser("serve", help="run the MCP server")
    s.add_argument("--http", action="store_true", help="streamable HTTP instead of stdio")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    if args.cmd in VIEW_CMDS | NATIVE_CMDS:
        pick_library(args)

    if args.cmd == "setup":
        download_models(maest=not args.no_maest)
        check_rules()
    elif args.cmd == "styles":
        names = [split_style(c)[0] for c in style_classes()]
        print("\n".join(sorted(set(names))))
        check_rules()
    elif args.cmd == "analyze":
        tracklist = None
        if args.chapters and not args.tracklist:
            raise SystemExit("--chapters needs --tracklist (the chapters are the tracks you played)")
        if args.chapters and args.offset is None:
            raise SystemExit("--chapters needs --offset: how many seconds into the finished video the first "
                             "track starts (check it in your video editor), e.g. --offset 0:14")
        if args.tracklist:
            from .tracklist import prepare

            tracklist = prepare(args.file, args.tracklist, args.start)
        result = analyze_file(args.file, args.segments, progress=True, tracklist=tracklist,
                              highlights=args.highlights, clip_seconds=args.clip_length)
        if result.get("type") == "dj_mix":
            from . import tracklist as tl

            offset = tl.parse_offset(args.offset) if args.offset is not None else None
            if args.chapters:
                result["chapters"] = tl.chapters(result, offset)
            tl.describe_highlights(result, offset)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if result.get("type") == "dj_mix" and not args.json_only:
            from .tracklist import summary

            sys.stdout.flush()
            log("\n" + summary(result))
    elif args.cmd == "clips":
        from . import clips

        clips.main(args)
    elif args.cmd == "train":
        print(json.dumps(train(args.folder), indent=2, ensure_ascii=False))
    elif args.cmd == "scan":
        from . import collection

        collection.main_scan(args)
    elif args.cmd == "tag":
        from . import collection

        fields = [f.strip().lower() for f in args.fields.split(",") if f.strip()]
        bad = set(fields) - {"genre", "bpm", "key", "camelot"}
        if bad or ("key" in fields and "camelot" in fields):
            raise SystemExit("--fields takes genre,bpm,key,camelot (key OR camelot, not both)")
        out = collection.tag_from_db(args.folders or None, fields, args.min_score, args.use_my_genre, args.dry_run,
                                     args.multi, args.multi_ratio, args.max_genres)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        say_if_nothing(out, args.dry_run)
    elif args.cmd == "untag":
        from . import collection

        out = collection.untag(args.folders or None, args.dry_run)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        say_if_nothing(out, args.dry_run)
    elif args.cmd == "traktor":
        from . import traktor

        if args.undo:
            out = traktor.undo(args.nml, force=args.force)
        else:
            out = traktor.sync(args.nml, args.dry_run, update_genres=not args.smartlists_only,
                               make_smartlists=not args.no_smartlists, colors=not args.no_colors,
                               overwrite_colors=args.overwrite_colors,
                               color_min_confidence=args.color_min_confidence)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        if not args.undo:
            say_if_nothing(out, args.dry_run)
    elif args.cmd == "colors":
        from . import colors

        if args.action == "explain":
            out = colors.explain(args.nml, workers=args.workers)
            print(json.dumps(out, indent=2, ensure_ascii=False) if args.json else colors.explain_text(out))
            return
        if args.action == "train":
            out = colors.train(args.nml, deep=not args.no_deep, workers=args.workers)
        elif args.action == "guessed":
            out = colors.guessed(accept=args.accept, nml=args.nml)
        elif args.accept:
            raise SystemExit("--accept goes with guessed: uv run dj-track-analyzer colors guessed --accept \"<name>\"")
        elif args.action == "apply":
            out = colors.apply(args.nml, dry_run=args.dry_run, min_confidence=args.min_confidence or 0.0,
                               workers=args.workers)
        elif (args.forget or args.keep) and not args.kept:
            raise SystemExit("--keep and --forget go with --kept: uv run dj-track-analyzer colors review --kept --keep \"<name>\"")
        elif args.kept:
            out = colors.kept(forget=args.forget, keep=args.keep, nml=args.nml)
        else:
            out = colors.review(apply=args.apply, dry_run=args.dry_run, min_gap=args.min_gap,
                                min_confidence=0.75 if args.min_confidence is None else args.min_confidence,
                                nml=args.nml, ask=sys.stdin.isatty() and not args.yes)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        if args.action == "apply" or args.apply:
            say_if_nothing(out, args.dry_run)
    elif args.cmd == "hype":
        from . import hype

        if args.traktor:
            out = hype.to_traktor(args.nml, dry_run=args.dry_run, remove=args.remove, clean_comment2=args.clean_comment2,
                                   force=args.force)
            print(json.dumps(out, indent=2, ensure_ascii=False))
            say_if_nothing(out, args.dry_run)
            return
        out = hype.find(args.nml, least=args.least, colour=args.colour, bpm=args.bpm,
                        bpm_tolerance_pct=args.bpm_tolerance, key=args.key, gems=args.gems, limit=args.limit)
        print(json.dumps(out, indent=2, ensure_ascii=False) if args.json else hype.find_text(out))
    elif args.cmd == "tempo":
        from . import tempo

        tempo.main(args)
    elif args.cmd == "cues":
        from . import cues

        cues.main(args)
    elif args.cmd == "grids":
        from . import gridfix

        gridfix.main(args)
    elif args.cmd == "regenre":
        from . import collection

        out = collection.regenre()
        print(json.dumps(out, indent=2, ensure_ascii=False))
        say_if_nothing(out)
    elif args.cmd == "export":
        from . import collection

        collection.export_csv(Path(args.out) if args.out else collection.DEFAULT_CSV)
    elif args.cmd == "stats":
        from . import collection

        print(json.dumps(collection.summary(), indent=2, ensure_ascii=False))
    elif args.cmd == "web":
        from . import web

        if args.export:
            web.export_file(args.export, args.logs)
        else:
            web.serve(args.host, args.port, args.logs, open_browser=not args.no_open)
    elif args.cmd == "hold":
        from . import collection

        if args.genre and args.colour:
            raise SystemExit("One at a time: --genre TEXT or --colour TEXT")
        if args.genre or args.colour:
            out = collection.hold_matching("genre" if args.genre else "colour", args.genre or args.colour,
                                           on=not args.release)
        elif args.release:
            raise SystemExit("--release goes with --genre TEXT or --colour TEXT")
        else:
            out = collection.holds_list()
        print(json.dumps(out, indent=2, ensure_ascii=False))
    elif args.cmd == "serve" and args.http:
        log(f"Puppi3's Track Analyzer MCP on http://{args.host}:{args.port}/mcp")
        build_mcp().run(transport="streamable-http", host=args.host, port=args.port)
    else:  # default: stdio for LM Studio / Claude Desktop
        protect_stdout()
        build_mcp().run(transport="stdio")
