# Web page: web.py, web/index.html, jobs.py, update.py

Detail moved out of CLAUDE.md so it loads only when needed. Code is the source of truth.

Page source layout: `web/index.html` is the shell (head, header, nav); `web.page_html()` replaces its
`/* include: … */` and `// include: …` lines with `web/style.css` and `web/js/*.js`, in the shell's order, so the
browser (and Export snapshot) still gets one document with one script scope. Edit the part you need:
`core.js` (constants, helpers), `library-overview.js` (library, router, Overview), `tracks.js` (Tracks, drawer),
`gridcheck.js` (beat grid check), `deck.js` (deck player), `hype-colours-tempo.js`, `jobs-update.js`, `setup.js`,
`mixes.js`, `files-init.js` (Files tab, `init()`). A new file needs an include line in index.html.

- `web.py` + `web/index.html`: `web` command, Starlette/uvicorn (already deps of mcp) on 127.0.0.1:8766, one
  vanilla-JS page (no CDN). Reads through the CLI's own functions (`collection.summary/mix_suggestions`,
  `hype.find(rescore=False)`, `colors.review/kept`, `colors.explain(extract=False)`, `tempo.report/status`).
  `web.library()` swaps collection.DB + cache globals per request under a lock (tempo.view_db sets them) and
  restores them (the saved globals are read inside the lock, or a view's stayed for good). In a view the Tracks tab,
  Overview colours and hype show `tempo.view_colours` (your colour from the NATIVE db + the model's shift, as `tempo
  load` writes it; held stays yours; `web.colours`, `hype.find(colours=)`), not the view db's unshifted copy. Mixes/Files tabs read run output JSON from `logs/` (`--logs`; endpoints are /api/saved*, since
  tracker blockers block `/api/log?`); `_json_in` tolerates teed stderr. Mixes' **Analyse a mix…** = job `analyze_mix` (`analyze <file> --out
  <first logs folder>/<stem>.json`, `jobs.MIX_DIR`; replaces an earlier one, via a .part file so a failed run keeps it), its list from /api/recordings (`web.recordings`: Browser.Dir.Recordings
  + the default folder, one row per stem, lossless preferred, > MIX_THRESHOLD_SEC). A dropped file: POST /api/recordings/find
  (name + size → that recording's path, no copy), else /api/mix-upload streams it to data/uploads/ (header
  X-Track-Analyzer: upload + Origin check instead of a JSON body; refused while a job runs), deleted when the job ends. Timeline: hype view (default, `ta-mix-view`) = per 2-min segment the
  library hype (time-weighted) + Traktor colour (longest) of the tracks playing, from `web.played_tracks` (saved_file adds
  `library`, by path, which tracklist output carries since 2026-10-31, else artist+title; native db) and the tracks'
  start + blend lag; not measured from the mix audio (at 145 the models hear psy, as in the psy gate). Clicking a mix genre/style/mood
  bar (`MIX_SEL`) fades the segments without it (style/mood against the strongest segment) and tints tracklist rows (genre = the
  track's heard genre, else its main stretch's segments time-weighted); segment `mood` = the mood heads over its patches
  (`mood_patches`, analyses since 2026-11-01; whole-mix `mood` unchanged). Writes: POST /api/hold, cue/sample endpoints (all JSON
  body + Origin check against CSRF, `from_this_page`), and jobs (below). Host header must be 127.0.0.1/localhost
  (TrustedHostMiddleware, `allowed_hosts`; any with --host 0.0.0.0, except in a container: + TRACK_ANALYZER_HOSTS): against DNS rebinding, which passes the Origin check.
  Every failure answers JSON {error} (`failed()`: SystemExit 409, bad input 400, else 500 + logged); the page's
  `readJSON` copes with a non-JSON answer. `tempo_report` refuses a BPM without a view (`tempo.report` would create
  an empty data/at-<bpm>). The `/audit-web` skill checks all of this live, plus phone width and light/dark. `--export` / /export = snapshot: page + `window.SNAPSHOT` (every tab's data,
  hype in 3 orders filtered client-side as hype.find does, default-rule mix suggestions per track, mix JSONs only). The Tracks filters/sort/scroll survive a refresh in sessionStorage (`ta-tf`, `ta-scroll`), the open track in the address (`#tracks/<path>`, `hashTrack`).
- `jobs.py`: the web page's Setup tab. `setup_status()` (models, tools, Traktor counts cached per NML mtime, scanned /
  not-scanned / changed-on-disk-since-scan, models' mtimes, tempo views, last `traktor --undo` backup) and a one-at-a-time
  job runner: `python -m dj_track_analyzer.jobs <cli args>` (= cli.main; a str SystemExit is sent as one ERROR_MARK
  stderr line) in its own process group, stdin DEVNULL (so `colors review --apply` never asks; the page passes `--yes`
  after a dry-run preview). Only `ACTIONS` (name → label, argv builder that validates options, writes?) can run; writers
  get `--dry-run` for the preview; `UNDO` maps writer → undo action. Stop = SIGINT to the group (scans save), SIGTERM
  after 20 s; `web.serve` stops a running job on exit. `analyze_mix` queues: `options.files` = several recordings, run one
  after another by `_wait` (also past a failure); an analyze_mix started while one runs joins `_queue`; `busy()` counts
  the queue; Stop clears it; `state()` lists it as `job.queued`, and the last jobs that ended as `job.ended` (a queued mix starts
  the moment the one before ends, so the page's poll may never see that one end: `jobPoll` ends them from this list, `JOB.failed`). Log = stderr lines (`[n/total ~eta left] what` or `[n/total] what` → progress with `what`; the page's bar falls back to
  `job.last`, the latest log line, so every action says what it's doing; `\r`
  segments → `live`, curl's meter); result = the JSON on stdout. Routes /api/setup, /api/jobs?since=, /api/jobs/start,
  /api/jobs/stop; /api/saved-file/delete (`web.delete_saved`: moves a listed saved file to its folder's deleted/, which
  `saved_files` doesn't read); `write_cues`/`undo_write` refuse while a job runs. `Start Puppi3's Track Analyzer.command` (repo folder):
  installs uv if missing, then loops `uv sync --inexact` (keeps the opt-in extras) + `uv run dj-track-analyzer web` while it exits 75 (restart after an update, `update.py`); opens the page if 8766 already answers. `Start Puppi3's Track Analyzer.bat` is its Windows twin (CRLF via `.gitattributes`; also `PYTHONUTF8=1`, x64 Python on Arm). Setup's Update training / Update beat-grids buttons are disabled when `learnNew().none` / every grid is checked; a real `cues_train` job recounts the proposed corrections in the background (`web._after_job`).
- `update.py`: `update [--check|--dry-run|--undo]` + the Setup tab's update card (/api/update: `web.update_status`,
  GitHub's releases API cached `UPDATE_EVERY` 6 h, `update_check` setting = "check when the page opens"; `update_channel` (stable/dev, `update.pick`): Dev offers the rolling `dev` prerelease when its commit ≠ the app's COMMIT file, never one based on an older version; a release zip removes COMMIT; jobs
  `update` / `update_undo`, previewed in writeDialog). Zip installs: the newest `v…` release's zip (SHA-256 from the
  API's asset `digest`), only the app's files (`PROTECTED` data/, logs/, .venv, .git never), each written via a temp
  file + rename, files gone from src/ removed (nothing deleted elsewhere); old copies + update.json in
  data/app-backups/ (last 3) first, a failure halfway restores them. Clones: `git pull --ff-only` on clean main,
  undo = `git reset --keep`; a pull that moves the app out of track-analyzer/ (no pyproject.toml left, one in
  the parent) → `_moved_to_root` moves the untracked files up (not .venv, caches deleted), no backup/undo, no
  restart (the old launcher is gone). `REPOS` = old name then puppi3s-track-analyzer, first answer wins
  (sets `REPO`/`RELEASES_PAGE`); drop the old one once the rename has shipped. Docker: refused. `AFTER` {version: step keys} = steps a release needs afterwards
  (add a line when a release changes the genre list, analysis or models); `pending_steps` vs setting
  `app_version_seen` (missing + a collection.db = 1.0.1) → the card's Run buttons. Restart: POST /api/restart →
  server exits `RESTART_CODE` 75 under a launcher (env `TRACK_ANALYZER_LAUNCHER`), whose loop runs `uv sync
  --inexact` and starts it with --no-open; without one (1.0.1's launchers, a terminal) Mac/Linux `os.execv` into
  `uv run dj-track-analyzer web`, Windows asks for a manual restart. The .bat must stay under 1800 bytes: cmd.exe
  re-reads a running batch file by byte offset, and 1.0.1's launcher reads on at byte 1817 after an update replaced
  it (past the end it just stops; `tests/test_update.py`); its own loop is one parenthesized block, parsed whole.
