# Puppi3's Track Analyzer: context for Claude

Local music-analysis tool for DJs: BPM, key/Camelot, subgenre, mood, a learned Traktor colour scale and a hype
score, synced into Traktor Pro 4 (optionally Lexicon), plus an MCP server for local LLMs (LM Studio, Open WebUI).
macOS and Linux x86_64 (Linux has no Traktor: those steps say so; also as a Docker image), Python 3.11 via `uv`. Data dir: `data/` (`TRACK_ANALYZER_HOME`).

Docs: README.md (install + full reference), docs/GUIDE.html (task-first guide, self-contained, opened in a browser),
docs/IDEAS.md (features, ideas, tried-and-failed), docs/WINDOWS.md (what a Windows port needs). Keep them in sync when commands, options or numbers change: the
`/update-docs` skill does this (`.claude/skills/update-docs/scripts/doc_drift.py` lists CLI options the docs miss).
Public docs stay impersonal: numbers come from "a test collection of about 470 tracks", no personal track names.

A gitignored `CLAUDE.local.md` may hold one developer's own setup, collection facts and history. Personal
details go there, never in this file.

## Working style
- Ask before design decisions; make new behaviour optional behind flags.
- Anything that writes to files, Traktor or Lexicon gets `--dry-run`, a backup and an undo path.
- Real Essentia models can't be downloaded in sandboxes; tests there use stand-ins for the TF models.
  Real-model behaviour is only verified by runs on a Mac, or in the Docker test image (below), which has them.

## Files
Code is the package `src/dj_track_analyzer/` (relative imports; uv installs it editable). Run it as
`uv run dj-track-analyzer <command>` (entry point `cli:main`; no command = MCP over stdio) or `python -m dj_track_analyzer`.
Saved run output goes in `logs/` (gitignored). Skill scripts import `from dj_track_analyzer import …`.
- `analysis.py`: config, model loading, caches, `analyze_file`, custom genre training. `find_tool()` (PATH + Homebrew
  folders), `install_hint()` (brew on a Mac, apt on Linux), `rubberband()` (refuses < 3.0: the R3 engine).
  `color_model()` aliases `sys.modules["colors"]` because bundles trained before the package move pickled
  `colors.BlockWeights` under that name.
- `dsp/`: Rust (PyO3 + maturin) module `track_analyzer_dsp`, replacing Essentia steps bit for bit, faster
  (`analysis.dsp()`; Essentia when it isn't installed). Now: `loudness_ebur128` (= LoudnessEBUR128, needs FMA
  in the coefficients and `b*x - a*y` to match the arm64 build) and `resample_sinc_medium` (= Resample quality 1,
  libsamplerate 0.2.2: its ring-buffer walk replayed on one thread, dot products in parallel; via
  `analysis.resample_medium`) and `resample_linear` (= Resample quality 4, the models' 16 kHz input via
  `analysis.resample_linear`; needs FMA in `x0 + t*(x1 - x0)`: the plain form was 1 sample off in 62.9M on a mix,
  though every test signal passed). `analysis.mono_mix` is MonoMixer in numpy (same bits). `musicnn_mels` (= TensorflowInputMusiCNN per
  FrameGenerator frame; `analysis.musicnn_mels`, MAEST's patches; `streaming=True` = the streaming FrameCutter's
  frames, whose silentFrames="noise" blanks a silent first frame) and `analysis.effnet_embeddings` (EffNet's
  padding/patches/batches as TensorflowPredictEffnetDiscogs, graph via plain TensorflowPredict). Its 512-point FFT is
  FFTW 3.3.10's plan with the codelets ported (`dsp/tools/gen_fftw_codelets.py`: Essentia's FFTW runs the PLAIN
  codelet variant and gcc -O3 fuses mul+add across statements, modelled with mul_add; checked by calling the
  wheel's local `_r2cf_16` etc. via ctypes). Tables (window, mel weights, twiddles, 268 macOS log10f exceptions:
  it isn't correctly rounded) from `dsp/tools/gen_musicnn_tables.py` (macOS + Essentia). A step goes in only
  after a bitwise check against Essentia over the whole collection + 48 kHz mixes (test signals aren't enough):
  `uv run python dsp/check.py --collection --files-from <list of mixes>` (all steps, ~25 min; before a
  release). While working, `--steps <changed step>` (~3 min: Essentia's own steps are most of the time).
  uv installs the wheels CI attaches to the `dsp-v<version>` release (`.github/workflows/dsp-wheels.yml`; the
  `dsp` index in pyproject.toml). After changing dsp/: `cd dsp && VIRTUAL_ENV=../.venv maturin develop --release
  --uv` (needs Rust; `uv sync` puts the release wheel back), then bump the version in dsp/Cargo.toml +
  dsp/pyproject.toml, tag `dsp-v<version>`, and update the version + index URL in pyproject.toml. Plan:
  docs/WINDOWS.md.
  Essentia's `AudioLoader` (checked 2026-10, docs/WINDOWS.md stage 0): MP3 via FFmpeg's fixed-point `mp3`
  decoder (16-bit, clipped), mono files as left = signal / right = silence (so loudness reads mono ~3 dB low),
  AAC/Opus untrimmed. `analysis.decode()` = AudioLoader's output from PyAV when installed (`analysis.pyav()`), bit
  for bit on 507/508 files (stage 2; the other: FFmpeg 2.8 decoded one MP3's LAME Info frame as 1152 samples of audio,
  current FFmpeg doesn't, as `traktor.audio_offset` assumes); raw frame bytes converted ~1M samples at a time (per-frame
  numpy was slower than Essentia), FLAC/ALAC frame threads; >2 channels via PyAV's resampler = `ffmpeg -ac 2` bits.
  Opus length = sum of packet durations (FFmpeg 2.8's). Python `tensorflow==2.15.0` runs the model graphs bit-identically to Essentia's TF, at the same
  speed; onnxruntime (tf2onnx) doesn't (MAEST embeddings off by up to 5e-4) and is slower on MAEST.
- `cli.py`: argparse CLI (`main`). `mcp_server.py`: `build_mcp()` (mcp SDK **2.x**:
  `from mcp.server.mcpserver import MCPServer`) + `protect_stdout()`.
  Essentia (`essentia-tensorflow`, pinned dev1177 on Apple Silicon for macOS 11+ support).
- `collection.py`: `scan` (resumable, parallel spawn workers, SQLite `collection.db`), CSV export,
  `tag`/`untag` (mutagen; originals backed up in `original_tags` table), search, mix suggestions, `regenre`
  (genres again from cached scores + current dj_genres.json; reproduces scan exactly).
- `traktor.py`: `traktor` command. Surgical text edits of collection.nml (never a full XML rewrite),
  timestamped backups + `--undo` (refuses if the NML changed since our write, via a `.written-sha1` file next to
  the backup; `--force` overrides), refuses while Traktor runs (`check_closed(dry_run)`, used by every NML writer:
  in a container, `CONTAINER` = TRACK_ANALYZER_CONTAINER=1, pgrep can't see Traktor, so it needs a y in a terminal,
  TRACK_ANALYZER_TRAKTOR_CLOSED=1, or the page's answer (`page_answer`; else `ConfirmClosed` → web 409
  {confirm: "traktor_closed"}, the page's `postJSON` asks and resends with traktor_closed; jobs check before starting). Sets INFO GENRE, INFO COLOR (only where
  missing unless `--overwrite-colors`), and a "Genres (Track Analyzer)" Smartlist folder, only for
  `known_genres()` = dj_genres.json names + trained genres (shop tags like "melodic techno, indie dance" would
  otherwise become ~100 Smartlists). `track_info()` reads Traktor's TEMPO BPM and MUSICAL_KEY (0-11 = C..B major,
  12-23 = C..B minor; INFO KEY is Open Key display text). Also `collection_tracks()` for `scan --traktor` and
  `recording_dirs()` (skips recordings + samples; `mac_path()` converts the .tsi's colon-style paths like
  `"Macintosh HD:Users:Shared:Traktor:Samples:"`).
- `colors.py`: `colors train|review|explain|apply`: learns a Traktor colour scale from already-coloured tracks.
- Starter models (`src/dj_track_analyzer/starter/{colors,cues}.joblib`, built by `scripts/make_starter.py` from the
  developer's trained models with every track path and the collection path dropped, `starter: True`):
  `analysis.own_or_starter()` loads them in `color_model()` / `cues.model()` (both cached per path + mtime) until
  `data/my_*.joblib` exists. With a starter: `colors review` refuses, `cues.suggest` proposes no corrections or moves
  of the user's cues; `setup_status` reports `starter`. Rebuild after retraining or a scikit-learn bump.
- README screenshots (`docs/img/{cues,overview,track}-{light,dark}.png`, shown via `<picture>`): `scripts/readme_shots.py
  [--track TEXT]` (+ `readme_shots.mjs`, headless Chrome) from the real library, with titles/artists/albums/paths
  swapped for made-up ones in the page's fetch answers and text. Retake after visible page changes; look at every PNG
  for leftover names before committing.
- Docker (`Dockerfile`, `compose.yaml`; README "Docker"): linux/amd64 only (no Linux arm64 Essentia; on Apple
  Silicon it runs emulated, TF included). Target `app` (web on 0.0.0.0, HOME=/home/dj so Traktor's folder mounts at
  ~/Documents/Native Instruments, data /data, any uid) and `test` (+ `setup` models baked in, never published:
  licence; + `scripts/make_fixtures.py` library: 4 synthetic tracks 124-145 BPM, MP3/FLAC/WAV, NML with grid, 5 cues,
  a loop). `docker compose run --rm test` = `scripts/docker_smoke.py` (scan BPM/key exact, Traktor refusal, write +
  undo byte-identical, web 409/Host checks; ~1 min emulated), also CI `.github/workflows/docker.yml`. Develop
  against real models: `docker compose run --rm dev` (test image, ./src + ./scripts mounted, page on 8767).
- `hype.py`: `hype` command (find by hype, filters, gems, `--traktor` → Comment).
- `setplan.py`: `set` command. Arc = points (x 0..1, colour 1 Red..7 Magenta, hype 1-10): `ARCS` presets, `parse_arc`
  ("Magenta/3,Red/9"), `mine` = `learned_arc` (mean colour/hype per fifth of History sessions with 8+ known public
  plays; nearly flat on the test collection). `library()`: native collection.db (colour = traktor_color else
  color_position, Traktor's BPM/key first, stars from the NML); `--tempo` reads the view's collection.db directly
  (`tempo.view_colours`, view hype; no global swap). `plan()`: beam search (BEAM 256, PER_STATE 24), cost =
  colour² + (hype/HYPE_STEP)² − stars bonus + transitions (KEY_CLASH, KEY_UNKNOWN, SAME_ARTIST, BPM_JUMP with
  --bpm-step). `--traktor`: playlist in the "Sets (Track Analyzer)" root folder (`_with_playlist`, same name
  replaced), backup prefix `collection.nml.before-set-` so `traktor --undo` never restores it; undo = `set --remove
  NAME` (surgical; write + remove is byte-identical), so it works while a tempo view is loaded.
  `crates` command (same module): `CRATES` rules (colour zone + hype range; Warm-up Green…Magenta ≤5 by
  approachability, Peak Red…Yellow ≥8, Closing Blue…Magenta ≥6; hype bounds are options), a track can be in several;
  playlists in "Crates (Track Analyzer)", " @<bpm>" with --tempo, replaced in place on re-run; `crates --remove
  [--tempo]` = undo. History (2026-10-30, 15 sessions, 367 plays) doesn't predict set position (|ρ| ≤ 0.14 for
  colour, hype, approachability, energy arc), so crates are rules, not learned.
- `tempo.py`: `tempo` command, the catalogue as heard at one tempo (default 145, `--bpm` for any).
- `lexicon.py`: `tempo load/native --lexicon` via Lexicon's Local API.
- `clips.py`: `clips <analysis.json> --video <final.mp4>`: cuts `analyze --highlights --offset` suggestions (video
  times from the JSON; highlights carry `rank`, 1 = best score) out of the finished video with ffmpeg: 720p previews
  in /tmp/clip-previews/<json stem>/, or `--pick` (numbers or M:SS-M:SS ranges) at 1440p30 h264_videotoolbox 16M
  next to the video as `<video>-clip-N.mp4` (N = suggestion number; own ranges `-clip-7m35s-8m15s`), no overwrite
  without --force. All clips are checked against the video's length before any is cut. Re-encodes (exact cuts; `-ss` before `-i`). Previews also measure
  movement in the same decode (64x36 grey at 2 fps, mean frame diff) → overall rank = mean(audio rank, movement
  rank) among the clips; files named "#overall clip-N MmSSs.mp4" (a new preview run deletes the old ones), --pick still takes N. Test set: audio #2 was at
  the 6th percentile of movement (checked: static), audio #4 at the 97th (lively).
- `tracklist.py`: `analyze <mix> --tracklist`: Traktor history NML (COLLECTION entries joined to PLAYLIST
  ENTRY/PRIMARYKEY KEY = VOLUME+DIR+FILE; EXTENDEDDATA STARTDATE = y<<16|m<<8|d, STARTTIME = seconds of day,
  DURATION = deck run time, which includes faded-out tails, so plays overlap ~70% of a mix). Recording start from
  Traktor's `Recording_YYYY-MM-DD_HHhMMmSSs` name or `--start`. Main stretch = start → next start, shifted by a
  measured blend lag (0-150 s, the one where most keys match), middle 60% judged. Key lock on unless off wins by
  ≥2 tracks. Sped >4%: genre = mix acts at played BPM with PSY_GATE vs native acts per track
  (speed-up and stems/effects both really change the genre, only the psy shortcut is
  removed; earlier "native + (mix − view)" and "library genres by time" were dropped). `mixing` = mix − view
  acts. Fields `*_uncorrected` = no gate. `--chapters`/`--highlights`/`--offset`: video time = recording time −
  first track's start (`anchor()`; `first_sound_seconds` without a tracklist) + offset, offset read off the
  finished video by the user (required for --chapters; never guessed from the audio); highlights from 1 s means
  of EBU short-term loudness, level + 0.5·rise (capped 8 LU), windows touching < −45 LUFS skipped. Missing data → SystemExit saying how to get it.
- `web.py` + `web/index.html`: `web` command, Starlette/uvicorn (already deps of mcp) on 127.0.0.1:8766, one
  vanilla-JS page (no CDN). Reads through the CLI's own functions (`collection.summary/mix_suggestions`,
  `hype.find(rescore=False)`, `colors.review/kept`, `colors.explain(extract=False)`, `tempo.report/status`).
  `web.library()` swaps collection.DB + cache globals per request under a lock (tempo.view_db sets them) and
  restores them (the saved globals are read inside the lock, or a view's stayed for good). In a view the Tracks tab,
  Overview colours and hype show `tempo.view_colours` (your colour from the NATIVE db + the model's shift, as `tempo
  load` writes it; held stays yours; `web.colours`, `hype.find(colours=)`), not the view db's unshifted copy. Mixes/Files tabs read run output JSON from `logs/` (`--logs`; endpoints are /api/saved*, since
  tracker blockers block `/api/log?`); `_json_in` tolerates teed stderr. Writes: POST /api/hold, cue/sample endpoints (all JSON
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
  after 20 s; `web.serve` stops a running job on exit. Log = stderr lines (`[n/total ~eta left]` → progress; `\r`
  segments → `live`, curl's meter); result = the JSON on stdout. Routes /api/setup, /api/jobs?since=, /api/jobs/start,
  /api/jobs/stop; `write_cues`/`undo_write` refuse while a job runs. `Start Puppi3's Track Analyzer.command` (repo folder):
  installs uv if missing, `uv sync --inexact` (keeps the opt-in extras), `exec uv run dj-track-analyzer web`; opens the page if 8766 already answers. Setup's Update training / Update beat-grids buttons are disabled when `learnNew().none` / every grid is checked; a real `cues_train` job recounts the proposed corrections in the background (`web._after_job`).
- `cues.py`: `cues train|scan|show`. Traktor's grid (AutoGrid CUE_V2 TYPE 4 START ms + TEMPO BPM; `grids()`,
  read-only; your cues = TYPE 0 cue / 5 loop, LEN ms) → bars. Per bar (cache.db `bars`, keyed path+grid, valid
  while mutagen length is within 0.25 s): 6 band dB, 40 mel dB (2048 FFT, 10 ms hop, 44.1k mono), kick body
  (100-250 Hz, first 3/16 of each beat vs 5/16-15/16; bars-3), kick pulse (30-100 Hz on-beat vs mid-beat) and off-beat
  hat pulse. `kick_on` = (30-100 pulse > half the track's p80, OR kick body > `KICK_BODY_DB` (4)) AND 30-100 Hz level within
  `KICK_SUB_DB` (15) of the track's p90: below 100 Hz trance kicks' long tails and rolling / off-beat basslines fill
  the gaps (pulse went negative, to -11 dB, in drops), so the 30-100 test alone read the loud half of 108 of 447
  tracks as kick-free; with the body 15. Without sub-bass both pulses fire on noise or synths (an echoing synth in a
  breakdown read as kick, its sub 17-53 dB down): sub-less bars read as kick 37% → 0%. OR a bare kick (DJ intro, no
  bassline yet): both pulses > `KICK_BARE_DB` (10), sub within 30 dB, 2 of 3 bars (806 bars, 70 tracks). Kick body is NOT a
  model feature (tried: cue F1 0.633 vs 0.635, loop exact 7.5% vs 8.2%). `features()`: bar level, after−before over 1-16 bars,
  Foote novelty (2-16) on mel cosine SSM, loopability (similarity / level spread of the next 2-8 bars), phrase
  position (mod 2..32 from the grid marker's bar). Two HistGradientBoosting models (cue bar, loop start bar),
  GroupKFold(5) by track; picks: threshold 0.25, ≤7, ≥4 bars apart; loop = argmax, length = your most common.
  Bundle `data/my_cues.joblib`. Section names from kick on (`kick_on`) over the next 8 bars vs the 16 before (a 2-bar fill isn't a breakdown) + level 4 bars either side (`_name`, untested
  against truth: test cues are named "Cue N"). `_one_path_each`: a startup-disk track is listed as
  /Users/… and /Volumes/<disk>/Users/…, both exist. cache.db `wave` (path + WAVE_VERSION, same length check): 1500 slices x [peak, rms, low, mid, high] uint8, made in the same decode as the bars (`_wave`); the panel draws it on a canvas (base64 in /api/cues). Zoomed in, /api/wave cuts finer slices from a `cues.WaveSource` (decoded audio + cumulative 10 ms band powers, last WAVE_KEEP=2 tracks, ≈65 MB each; peaks / band levels scaled against the whole track so zoom levels match): the whole track at 100/s, or t0/t1/n for the visible stretch at device-pixel resolution, down to single samples; min zoom one beat. Web: /api/cues + waveform in the track panel. Approval (cue by cue, named,
  loop in slot 8, empty slots + corrections of their own slips): collection.db `cue_plan` {path, grid key, add[],
  move[], remove[], renumber? ("Number cues in order": `resolve` puts cue points 1-7 in time order)} via POST /api/cues/approve (`clean_plan`; POST /api/cues/slots = `web.slot_preview`, the slot each
  ticked new cue gets, before approving); `cues apply [--dry-run]` (Traktor closed, refuses with a tempo view loaded,
  `traktor._save` backup → `traktor --undo`): deletions first (`remove`: any cue, loop or fade/load marker
  still where it was, CUE_V2 dropped by `_drop_tag`, hot cue points after it in slots 1-7 slide back (slot 8 stays); ✕ on a pad or the page's
  Delete your cues group), then adds into free HOTCUE slots (cues 0-6, loop prefers 7; a "hand" cue, set at the playhead
  with C, is only refused on the same spot, not within a bar),
  skipped within a bar of an existing cue or another added one (`resolve()`, shared by `apply` and the
  page's `preview()`, so what the page shows as approved is what gets written; hot-cue slots counted over all
  CUE_V2 incl. fade/load, `grids()["all_cues"]`); moves set START of the CUE_V2 with that HOTCUE
  only if START still matches; grid key mismatch → skipped; ENTRY MODIFIED stamped with the file's mtime; applied
  plans deleted. Ignored corrections: collection.db `cue_ignore` (path, hotcue, from_ms, to_ms), flagged in suggest(); the counts and
  the page only count `pending_corrections()` (not ignored, not approved); `web.recount()` updates one track after
  approve/ignore/rescan; rescan = `cues.forget_track()` + measure again + re-read the NML. Page write: POST /api/cues/write {dry_run} →
  `web.write_cues` (= `cues.apply`, SystemExit → 400 message; the dialog previews first) saves the plans it used in
  `data/cues-last-written.json` with the backup name; POST /api/cues/undo → `traktor.undo()` (no force) and puts those
  plans back if the restored backup is that one. POST /api/samples/reveal → `web._show` (open -R / explorer /select /
  xdg-open), only inside `sample_root()`. ▶ buttons (`button.mini[data-t]`) show ⏸ while `DECK.active` is theirs. The pads show a cue that changes slot as "2→3", Delete your cues as "Cue 2 → 3" (`data-renum`); dragging one of your cues on the big waveform calls `box._cueDrag` (a ticked move, `DRAGMV`; hand cues move in `HAND`). Hand-picked moves: `alternatives()` in suggest() lists, per own cue point, the bars either side, nearby bars (±4) scoring ≥ threshold/2, every suggestion and its own downbeat when >1/20 beat off; the page shows one row per cue with a target `<select>` (a cue's corrections are among its options; cues without one stay hidden in `#byhand` until a suggestion's "move my cue here…" menu `button.useas` picks them; a page-made menu, since an open <select> reports no hover; the target <select> itself opens the same kind of menu on mousedown, so the hovered bar lights up on the waveform). With a free hot-cue slot (`free_slots`), suggestions offered as corrections are also listed as new cues, and `more_cues` (suggest(): the six best other bars, ≥2 bars from your cues, ≥4 from the picks, no score floor) sits folded under New cues; neither is ticked by Tick all new cues (`data-more`), `resolve()` renumbers your hot cue points to keep time order when they already were in it (6 of 447 tracks weren't; loops etc. keep their slots): returns renumbers, `preview()` gives such cues `was_hotcue` (identity for move keys on the page), `apply()` sets HOTCUE, a new cue's `select.slot` sends `slot` (0-7; set_plan keeps it): `resolve()` puts it there, moving the cue points from that slot up into the next free one (never past another kind), and then skips the time-order renumbering, and any approved move of a cue settles its corrections. Corrections (`corrections()`): own cue whose best bar within ±2 scores ≥ threshold and ≥0.2 more,
  scored with the bundle's `oof` (cross-validated probabilities per trained track and grid; tracks not in
  training use the live model; grid changed → none). Web deck: /api/audio streams a file (Starlette FileResponse, Range) only if its path is in the native
  collection.db; the page's `DECK` (one Audio element, preload auto set on the first jump); loops play gapless in Web
  Audio from /api/loop (`web._render_loop`: ffmpeg decode via `_cut`, edge fades and optional smoothing in numpy, f32 WAV; falls back to decoding the whole track). Tempo view
  player (`deck_stretched`, /api/audio?bpm=, /api/loop?bpm=, /api/deck/stretch to start it): R3 from `source_bpm` (the
  view scan's) to the view's BPM, `stretch_parallel` (15 s pieces, 8 threads, 4 s lead-in, 30 ms equal-power
  crossfade: phases don't line up at the joins), Traktor's offset scaled in front, cached as FLAC with deck_audio's.
  Save sample: POST /api/samples/save → `web.save_loop`: ffmpeg cut
  into Traktor's Browser.Dir.Loops/<subfolder> (only existing subfolders or `Track Analyzer loops`), WAV codec by
  `_wav_codec` (lossless: source bit depth; lossy: pcm_f32le, since a test MP3 decoded to 1.049 peak and 24-bit
  clipped 128 samples per loop); identical to the decoded source apart from the edge fades (below); never overwrites (" (2)"); collection.db `saved_sample` (file, src_name, start, len, smooth, format) skips a loop saved before (±2 ms, any subfolder; samples not in it are read once per run from their ffprobe comment "Loop from <name> at <s> s, <len> s", files named "* from bar *.wav"), shown via /api/cues `saved_samples` drives the playhead over the zoomable canvas (`box._wave`). Web: per-track correction counts (`web.correction_counts`) start counting in a
  background thread at `serve()` (one track per `library()` lock; predictions under `threadpool_limits(1)`, 16 vs ~100
  ms, identical) and are saved in `data/cue-corrections.json`, keyed by the model's mtime and, per track, a hash of its grid + cues
  (`_fix_sig`): a write to Traktor or Traktor saving recounts only the tracks that changed (≤ `FIXES_SYNC` 30 in the
  request, more in the background; counting all of them on each NML change made the list show "counting…" for a minute).
  Loop ideas (`loop_ideas`, `_idea`, up to 6): the 2 best by the loop model, 2 peak loops (best loopability within
  1.5 dB of the loudest bar, kick on, outside the first/last 15%; then without the kick: kick_on still misses it
  under a full drop on ~3% of tracks; then anywhere; then loudest), then the best `loopability()` (mean timbre
  cosine of the L bars − level std/6) on the grid chosen in train among 1/2/4 bars by recall of the user's loops
  (measured without the peak loops, n=5: 1 bar, 70% within 4 bars, 18% exact, vs one loop 34% / 8%; bars-3); ≥8 bars
  apart; kinds from kick_on and level. Loop parts (`loop_parts`, optional `stems` extra = Demucs htdemucs): each idea ±4 s
  separated (one model, a lock, mps/cuda/cpu), per-beat stem levels against the loop and the drum stem's 30-150 Hz
  kick cached in cache.db `parts` (PARTS_VERSION), so thresholds (PART_DB, PART_BG_DB, KICK_STEM_DB) change without
  separating again; suggest() reads only the cache (`parts_pending`), the page then calls /api/cues/parts (~8 s). Demucs runs outside
  web's `library()` lock (`cues.separate_parts`, each idea stored as it's done), so approving or opening another track
  doesn't wait for it. /api/cues/parts, /api/deck/stretch, /api/audio and /api/loop stop when the page drops the request
  (`until_gone` → a cancel Event → `analysis.Cancelled`; Rubber Band processes killed); the page aborts them on opening
  another track or closing the panel (`PREP` / `prepFor`), and asks again when you come back. The
  drum stem's kick overrides kick_on in the kind. No male/female: no head or pitch test told them apart.
  The suggested loop never starts within a bar of a suggested
  cue (the user's never do), in suggest() and in the CV loop score.
  Loop length (`train_lengths`, bundle `length_model`, only kept if CV by track beats always-the-most-common): per loop
  start, `length_features` = loopability at 1/2/4/8 bars + how closely the next L bars repeat the L + BPM →
  HistGradientBoosting over `LOOP_LENGTHS` (1/2/4/8; 16-bar loops too few). Test collection 2026-10-26: 438 loops,
  53.7% vs 45.2% always 4 (8 bars found 28/74, 1 bar 1/25); track metadata (BPM, genre, colour, hype) didn't predict
  it. Ideas are still chosen at `loop_bars`; suggest() adds `suggested_bars`, the page's length menu (1-16, what fits)
  preselects it.
- `gridfix.py`: `grids check|show|apply`: does Traktor's grid sit on the kicks. Per track, up to 6 eight-bar stretches
  where kick_on (cue bars, one-bar gaps bridged: a fill every 8th bar left no 8-bar run and the 20-80% fallback
  hit a breakdown), envelope 100-400 Hz + 4x 2-8 kHz, zero-phase, 1 ms bins on
  Traktor's timeline (bins by time: 44.1 samples/ms; integer 44 drifted 0.7 s by 5 min), folded on the grid lines;
  attack = where the fold rises past 30% from floor to peak within ±150 ms (steepest rise read +20 ms on a track
  Traktor grids right). The cache holds raw readings (`measure`); `classify()` makes the verdict when read, so
  thresholds change without measuring again (`results()` keeps each verdict while its stored reading is unchanged: classifying
  every grid on each /api/tracks was 3/4 of its time). Offset = all stretches folded together; stretches only check it (2/3
  within AGREE_MS 12 of their median: a two-part attack reads 47 or 60 ms; > 25 ms from the whole = no kick there,
  left out); a steady line fit ≥ 15 ms = `drift` (BPM off, not fixable by a shift). States on / off (≥ PROPOSE_MS 10)
  / drift / unclear; `preview_ms` = an agreeing stretch (the click preview). Test collection 2026-10-26 (grid-3):
  others median 0 ms (p5/p95 -4/+9); 41 of 42 stem-gridded off (+51), the other drift; 403 on, 62 off, 2 drift,
  6 unclear. Cache: the NORMAL cache.db `gridcheck` (own sqlite connection, so a view's swapped CACHE_DB doesn't
  matter), keyed path+BPM+grid+VERSION.
  Stems (`find_stem`: Browser.Dir.GeneratedStems, matched by artist+title tags; `stem_check`: cross-correlation of
  the stem's stream 0 with the decoded track; a stem lines up when lag = audio_offset + AAC_PRIMING 2112): NUO Stems
  left the MP3 lead-in (2257) out of 42 stems from LAME 3.100 MP3s (not all LAME 3.100), whose grids had been set on
  the stem (Traktor: one grid per track, cues/grid shared by time with the stem; user checked). Remaking with NUO
  4.1.0 lined Timeless's up. Web: /api/grid (measure + stem check, `lead_in`: an MP3 with offset 2257 read within 5 ms
  of 51.2 is set to exactly 51.2), approve/ignore (collection.db `grid_plan` / `grid_ignore`, per grid), click preview
  (16 beats from /api/loop with ticks on old or moved lines), write/undo. `apply` shifts every CUE_V2 START of the
  entry (grid marker a bar later if it would go negative; refuses a cue that would), stamps MODIFIED, moves that
  track's `cue_plan` (new grid key) and `cue_ignore` along; `undo_write` restores the NML (`traktor.undo`) and both,
  from data/grids-last-written.json. The cue model's oof is per grid, so a moved track has no corrections until retrained.
- Holds (`collection.held/set_hold/hold_matching`, `hold` command): tables `genre_keep` / `color_keep` (= the
  colour "keep always" list) in the NATIVE collection.db (`collection.NATIVE_DB`, also while a view is in use).
  Skipped by `_wanted` (tag, scan --write-tags), `traktor.sync` (genre and colour, even --overwrite-colors),
  `colors review --apply`, `tempo.build_view` and `lexicon.build` (held genre keeps its Smartlist); stats
  `genre_held` / `colour_held`.
- `keycnn.py`: key from MusicalKeyCNN (puppi3-music/MusicalKeyCNN-CKA, from a1ex90/MusicalKeyCNN; MIT): KeyNet in
  numpy (`forward`; weights `starter/keynet.npz`, BN folded, made by `scripts/convert_keynet.py` from keynet.pt),
  input = librosa CQT exactly as the repo trained on (hence the librosa pin). Checked: same key as torch on 32/32
  (probabilities within 2e-7) and as the repo's own pipeline on 459/459. `analysis.key_cnn` → `cnn_key`,
  `cnn_camelot`, `cnn_confidence` (tracks and tempo views; mix segments still use Essentia).
  `keys.py`: `keys` command, backfill for tracks scanned before (spawn pool, resumable) + MIREX-category report.
  The Cross-Key Analysis part of that repo lives on branch `cka` (it didn't beat the CNN; kept for development).
- `dj_genres.json`: maps Discogs styles to DJ genre names (rules with "needs" groups + BPM range); a user copy in
  `data/dj_genres.json` wins.

## How the analysis works
- Decode once → stereo (loudness EBU R128), mono 44.1k (BPM via RhythmExtractor2013 multifeature, used raw,
  folded into 85–220; key via the CNN (`keycnn.py`) and KeyExtractor `edma` → Camelot), mono 16k (models).
- Our key = `cnn_camelot`, else Essentia's (`collection.OUR_CAMELOT_SQL`, `our_camelot`, `our_key`); Essentia's
  key/camelot/key_strength stay stored because the colour model was trained on them.
- **BPMs are whole numbers everywhere**: `analysis.round_bpm()` at measurement, Traktor's value as read
  (`load_traktor_info`), genre_bpm, the tempo view's source BPM, displays and CSVs (`collection.csv_value`).
  Traktor's own BPM/grid are never written.
- Traktor's BPM/key: `scan` stores them as `traktor_bpm` / `traktor_camelot` and passes Traktor's BPM to
  `analyze_file(genre_bpm=…)` for the genre rules. Search, mix suggestions, hype filters and BPM/key tag writes
  use COALESCE(Traktor's, ours) (`collection.BPM_SQL` / `CAMELOT_SQL`). The colour model uses our BPM.
  Key vs Traktor's on a test collection: CNN 75.1% (MIREX 78.4), Essentia 57.6% (64.3, parallel errors 18.8%);
  neither is ground truth.
- Genre: Discogs-EffNet (400 styles) averaged over the **main section** (windows within 6 dB of the loud part) +
  MAEST-519 on the 3 loudest 30 s blocks, blended 60% MAEST / 40% EffNet → `dj_genres.json` rules → `dj_genre`
  (top 3). Out-of-range BPM fades the score to 60% over `BPM_MARGIN` (3 BPM), not a cliff. The ×1.35 multi-group
  bonus only applies when different styles fill the groups. `label_scores`: MAEST-only styles (Neo Trance,
  UK Funky, …) stand in for EffNet at the track's EffNet/MAEST ratio over shared styles (MAEST runs ~1.3x higher).
- MAEST runs ONCE per track (`run_maest` → style scores + layer-7 embedding) via plain `TensorflowPredict` with
  our own input pipeline (`_maest_patches`: MusiCNN mels, (x − 2.0676)/(2·1.2683), 1876-frame patches, hop 1875,
  one patch per run: the graph has batch size 1). Verified identical to TensorflowPredictMAEST (max diff 2e-7).
  Cached in cache.db `deep` (`maest`, `maest_styles`), so rescans skip MAEST. A failure on one track doesn't
  disable MAEST (or any mood/instrument/vocals/extra head) for the rest of the worker; only load failures do. A head
  that failed on a track leaves it out of the cache, so `deep_missing` (moods included) asks for it again, and
  `predict_color` returns None when a downloaded mood wasn't measured (rather than color_inputs' 0.5 default).
- `tag --multi` writes "A; B" when the runner-up ≥ 70% of top and names don't overlap.
- Mood: EffNet heads mood_{aggressive,happy,party,relaxed,sad} (`model/Softmax`), optional downloads.
- Instruments: `mtg_jamendo_instrument` head (40 classes, main section) + `voice_instrumental` head (`vocals` mean /
  `vocal_share` >0.5, **whole track**); the tagger's own "voice" class is ~0.02 even on vocal club tracks.
  Cached in cache.db `deep.instruments`; `deep_features` adds them without recomputing MAEST.
- Extras (deep.extra JSON): approachability/engagement regression heads (`model/Identity`, whole track) +
  `energy_arc()` from short-term loudness (silence before / inside the music counts as quiet, trailing silence
  is trimmed: identical to the old silence-dropping version on all 469 test tracks, whose silence was all at the end).
- Files > 12 min are DJ mixes → timeline per 2-min segment (`--tracklist` adds a per-track check). Their BPM/key
  measurements run in `analysis.AudioPool` (spawn workers as `scan`, audio via a memory-mapped .npy, worker
  stdout → stderr for MCP); output identical to serial, 121 → 71 s on a 65-min mix.

### Colour
- Traktor INFO COLOR 1..7 = Red, Orange, Yellow, Green, Blue, Violet, Magenta. Treated as an ordered scale; labels
  and LLM tool docs assume Red = hardest/darkest … Magenta = softest/prettiest.
- `colors train` tries feature sets (incl. "descriptors weighted": moods, instruments, approachability/engagement,
  energy arc ×10 after standardising, `colors.WEIGHTED_SETS`) and picks by `average_error_per_colour` (macro),
  not the plain average, which rewards shrinking toward the middle. Baseline = always guess the median colour.
  Test collection (~470 tracks): 1.10 avg error vs 1.59 baseline, within-one 70%; neighbouring colours are near
  coin flips from audio, the ends are clearly apart.
- `confidence` = `analysis.color_confidence()`: kernel-smoothed (σ 0.3) share of CV tracks placed near that position
  that really had that colour (+5 pseudo-tracks of the overall rate). Bundles store `cv` (positions, truth);
  bundles without it → None (retrain). Typical 0.2-0.3. `review` uses within=1.
- `colors review --apply` recolours CV disagreements with gap ≥ 2.5 and within-one confidence ≥ 0.75
  (`traktor.set_colors`, skips tracks recoloured since). Asks per track (y/n/k/a/c; `--yes` skips); `k` = keep always → collection.db `color_keep`, which
  `--apply` skips. `colors review --kept [--keep|--forget TEXT]` shows/edits it (`--keep` alone is refused; ≤10 matches).
  `read_traktor_colors` refuses while a tempo view is loaded (train, explain, apply, `--keep`). `colors apply`: scan --traktor (new only) → deep features
  for uncoloured → refresh predictions → `traktor.sync(update_genres=False, make_smartlists=False)`.
- `colors explain`: per-colour profile + playlists + History set position + artist consistency (read-only).
- Learn from what was set or accepted, whoever suggested it (user, 2026-10-26). `traktor.sync` (colour fills and
  `--overwrite-colors`, i.e. `traktor` and `colors apply`) records each colour it writes in NATIVE collection.db
  `color_guess` (`collection.add_guesses`); `collection.unchecked_guesses(traktor colours)` = those Traktor still has.
  `colors train` (`usable_labels`) leaves them out; changing one in Traktor, `set_colors` (review --apply: said yes),
  `colors guessed --accept TEXT|all` or the page (POST /api/colours/accept, `web.colour_guesses` → `colour_guess` on
  /api/tracks, "?" mark, All tracks → Colour, selection bar / panel button) make it yours. Bundles store `labels`
  ({path: COLOR} for colours; {path: (grid key, cues)} for cues, `cues._label_key`; make_starter drops them);
  `colors.new_labels` / `cues.new_labels` = added/changed/removed vs those, None for starters and older bundles;
  `jobs._new_labels` (cached per NML/models/db mtime) → setup `colours.new` / `cues.new`, shown on the Setup steps.
- Answers log (user, 2026-10-26: "anything that can be labelled as no or ignore"): NATIVE collection.db `feedback`
  (at, kind, path, JSON), append-only, latest row per track+kind counts; `collection.log_feedback`, `feedback_counts`.
  Kinds: hold (`set_hold`), colour_no / colour_keep (`review --apply` answers, yours + model), cue_ignore
  (`cues.set_ignore`), cue_unticked (`cues.apply`, before writing: `_unticked` = suggest()'s cues + loop not within half
  a bar of an approved or existing cue, free slots, starter), grid_ignore (`gridfix.set_ignore`, with the check's
  reading via `web.ignore_grid`), grid_written (`gridfix.apply`). Created on first use with the earlier holds,
  cue_ignore and grid_ignore rows copied in (`_feedback_table`). Not used by training yet: for a later with/without
  CV test of weighting these (too few on 2026-10-26: 12 holds, 4 cue ignores, 11 grid ignores, 8 of them at 10-18 ms
  against PROPOSE_MS 10).

### Hype
- 1-10 decile within the scanned collection of (rank(rave styles) − rank(deep styles))/2 +
  (rank(lufs)+rank(party)+rank(aggressive)−rank(relaxed))/4. Rave = Trance, Hard Trance, Hands Up, Hardstyle,
  Tech Trance; deep = Deep Techno, Tech House, Deep House, Downtempo (EffNet activations from cache.db `emb`,
  never re-analyses). Stored in collection.db `hype`. Rationale: on a test collection, star ratings followed rave
  sound (+0.36) and energy (+0.30), not colour (−0.07) or drop size (~0); stars weren't predictable from audio
  (CV 0.56 vs 0.58 baseline), so there's a hype score instead of an auto-rating.
- `--traktor` writes "Hype NN" into **Comment = INFO COMMENT** (moved from Comment 2 = INFO RATING on 2026-10-28; stars = INFO RANKING, 51 per star), appended after
  existing text with " · "; `--remove` strips it. Any "Hype NN" still in INFO RATING is stripped on every write (`--clean-comment2` does only that). Traktor re-reads COMMENT from the file tag when the file mtime ≠ ENTRY MODIFIED stamp, so entries get the file's mtime stamped (also when the Hype was already right: a stale stamp would still lose it) via
  `traktor.stamp_if_unchanged`, shared with `tempo load`: only when the file's title/artist/album/comment (`tags_match`;
  fields the file has, Hype ignored, genre not compared since `traktor` writes genres the files lack) still match the
  entry, else Traktor should read those edits: hype skips the track (`skipped_file_tags_changed`), load writes it
  unstamped. Refuses while `tag_writing_on` (would copy Hype into the files) unless `--force`. Hype goes in Comment because Pioneer DDJs show Comment on USB exports, not Comment 2.

### Tempo view (`tempo.py`)
- `analysis.stretch()` = Rubber Band CLI R3 (`brew install rubberband`, 4.0); `analyze_file(tempo=(src, target),
  genre_bpm=target)`. Source BPM = Traktor's, else ours. BPM is set to the target, not measured on stretched audio.
  A track already at the view's BPM isn't stretched (`stretch` returns the audio as is; R3 at ratio 1 is up to
  -55 dBFS off), so its view analysis equals its own (11 tracks over the 135/145 views, colour shift 0.0).
  The colour model gets OUR BPM measured at the track's own tempo (`analyze_file(own_bpm=…)`, `refresh_predictions(own_bpm=…)`),
  as the native position does, so the shift is the tempo's alone (with Traktor's BPM, tracks 1-2 BPM apart shifted ~+0.13 extra on a test collection).
- Separate data in `data/at-<bpm>/` (cache.db with CACHE_VERSION + "|tempo-<bpm>", collection.db with
  extra `source_bpm`, `native_color_position`; `traktor_bpm` = target, so BPM_SQL gives the target). A changed source
  BPM drops that track's view cache. Models + colour model shared, never retrained on the view.
- The view's cache stores the STRETCHED duration: `analysis.STRETCH` {path: bpm/source_bpm} (filled by
  `tempo.view_db`, replaced per view) scales the expected length for `_recover_key`, only while CACHE_VERSION is a
  view's (`|tempo-`): `base_cache()` and `web.library()` leave STRETCH filled, and dividing the native duration by it
  made native recovery fail (psy gate and native colour position silently skipped). `tempo scan` only updates
  size/mtime for files whose cache recovers instead of re-stretching them.
- Psy gate: sped up, the models hear Psy-Trance 0.6-0.9 in house that scores 0.0-0.1 at its own tempo (a tempo
  shortcut, not the BPM rules). In a view Psy-Trance/Goa are capped at 1.5 × the track's own-tempo psy
  (`tempo.view_genres`, in the scan loop and `tempo refresh`).
- `load`/`native` = carry-over swap: only INFO GENRE/COLOR/COMMENT (views loaded before 2026-10-28 remembered RATING; `native` restores whatever field is stored), the genre Smartlist folder (@<bpm> folder takes
  the native folder's place, `traktor.swap_root_folder`), each changed entry's MODIFIED_DATE/TIME and the clock
  change. State in `data/tempo-loaded.json` as {VOLUME|DIR|FILE: {field: [yours, view]}}; `native`
  restores a field only if it still holds the view's value (clock always). Colour in view = user's colour +
  (view position − native position), both from the current model. Round trip byte-identical (except an old "Hype NN" in INFO RATING, which `load` strips for good); real Traktor 4
  opened, rewrote and closed a loaded NML and `native` still matched every value.
- Backups: `collection.nml.before-tempo-view-…`, `Traktor Settings.tsi.before-tempo-view-…` (`traktor --undo`
  ignores them). `traktor.refuse_if_tempo_view` blocks sync/write_hype/set_colors/undo while loaded.
- Traktor re-reads file tags on deck load / Check Consistency when the file's mtime ≠ ENTRY MODIFIED_DATE/TIME
  (UTC date + seconds into that day); the file's genre/colour then replace INFO GENRE/COLOR (and COMMENT, from the file comment tag).
  So `load` stamps changed entries with the file's mtime (`traktor.file_stamp`, field "MODIFIED") and refuses while
  `FileAccess.TagsSerialization` ≠ 0 in Traktor Settings.tsi (`--force` overrides). Not yet confirmed in real Traktor.
- `load` sets `Status.Master Tempo` in the .tsi to the view BPM (`traktor.set_setting`). Traktor saves no deck Sync
  state and no clock-master state.

### Lexicon (`lexicon.py`)
- Local API localhost:48624/v1 (off by default; spec lexicondj.com/developer/api-docs.yaml): GET /tracks,
  PATCH /track, POST /playlist (type 1 folder, 3 smartlist), DELETE /playlists. Locations come as
  `/Volumes/Macintosh HD/Users/…` (prefix stripped to match).
- Changes genre, colour (same shift; non-main shades left alone), comment ("Hype NN" appended) and energy (view
  hype 1-10; empty energy is 0, not ""). Smartlists use StringRegExpMatch on whole "; " entries.
- State `data/tempo-loaded-lexicon.json` {lexicon id: {field: [yours, view]}}, written before editing.
  Before each real load/native: SQLite backup API copy of `~/Library/Application Support/lexicon/main.db`
  (ro URI, integrity_check) → `data/lexicon-backups/` (keep 10).
- Real load tested (smartlist counts exact); `native --lexicon` only against a mock API so far.

### Genre set
- 42 DJ genres. Progressive Trance is its own genre (126-145 BPM, taken out of Trance's needs).
- Deliberately NOT added: Electro House (the models give Discogs' 2010s "Electro House" to most modern melodic/
  peak-time house), Ambient (it went to a psytrance track in testing), Afro/Organic House as 2-style combos (matched
  nothing). Big Room, Future House, Afro House, Organic House, Melodic Techno, Future Bass, Hard Groove don't
  exist in Discogs: only via `train` with example folders.

## Traktor NML facts (verified on real Traktor 4 files)
- Smartlist node: `<NODE TYPE="SMARTLIST" NAME="X"><SMARTLIST UUID="hex"><SEARCH_EXPRESSION VERSION="1" QUERY="$GENRE % &quot;X&quot;"></SEARCH_EXPRESSION></SMARTLIST></NODE>`.
  `%` contains, `!%` not contains, `&` and, `|` or. Brackets reportedly don't work.
- LOCATION: `DIR="/:Users/:me/:Music/:" FILE="x.mp3" VOLUME="Macintosh HD"`; other drives → /Volumes/<VOLUME>.
- Real Traktor format uses explicit close tags and `standalone="no"`; keep edits textual.

## Bugs found and fixed (don't reintroduce)
- MAEST graph input is `melspectrogram`, NOT Essentia's default `serving_default_melspectrogram`: pass `input=`
  explicitly (`maest_input_name()`). Outputs: `PartitionedCall/Identity_13` = 519 predictions,
  `PartitionedCall/Identity_7` = layer-7 embeddings (768).
- Tag writes change file size/mtime → cache keys are re-keyed (`_rekey_cache`) so analysis isn't lost. For edits
  by Traktor, other taggers or file sync, a key miss falls back to the same path + `ver` = CACHE_VERSION + duration
  within 0.25 s of mutagen's audio length (`_recover_key`; mutagen vs decoded differ ≤ 0.05 s in practice).
  `analysis.prune_cache` (start of `scan` / `tempo scan`, before workers) drops emb/deep rows nothing can reach:
  other versions, gone-for-good files, older keys once the current key has a row, Syncthing conflict copies
  (`never_read` = `collection.sync_conflict`) and decomposed spellings of a path whose composed one has rows; a file
  whose current key has no row keeps its old ones for recovery. Recordings run through `analyze` keep theirs
  (2026-10-25: 383 native rows → 469 tracks + 10 recordings, 14 at-145, all 469 tracks still hit).
- `refine_bpm` (line fit through the beat grid) was REMOVED: off-grid beats in breakdowns were counted as beats and
  pushed tempos up to 5 BPM high. Vs Traktor on 80 tracks: raw 96% within 1 BPM, with the fit 50%. A robust
  re-anchored fit was no better.
- Colour confidence used to be 1 − 2·|pos − round(pos)|, which gave the model's hedges (pos ≈ 4.0) confidence 1.0.
- Smartlist exclusions match the other genre's full name ("Techno" excludes "Melodic House & Techno").
- `scan --write-tags`: Traktor's BPM/key must be attached to the row before tags are written.
- `store_traktor_info` clears Traktor's BPM/key on rows Traktor no longer lists (else a stale value wins in BPM_SQL).
- `save_row` is `INSERT OR REPLACE` over `NAMES`: columns not in the scan result are wiped until recomputed
  (view-only `source_bpm`/`native_color_position`; `hype` is in `KEEP_ON_RESCAN`).
- NFC vs NFD paths create duplicate rows (`_drop_decomposed_duplicates`). Syncthing `*.sync-conflict-*` copies are
  skipped by `scan` (and earlier rows dropped). >2-channel files (Essentia's AudioLoader rejects them) are mixed down to
  stereo by PyAV's resampler (`decode`), or by the ffmpeg command without PyAV (`_load_downmixed`, float; same bits). Rows for files that are gone are dropped by every scan unless they're on
  a drive that isn't mounted (/Volumes/<disk>; Linux /media, /run/media, /mnt via ismount). `nfc()` composes a
  path on macOS always, on Linux only if that spelling exists (ext4 compares bytes).
- `scan` recomputes hype after analysing anything (it's a rank within the collection); `hype` is also kept on rescan.
- `refresh_predictions` clears a track's prediction when the current model can't place it (no stale colours).
- `stretch` writes 32-bit float WAV for Rubber Band (16-bit input clipped peaks above full scale; R3 output then
  differs in phase only: style scores ±0.004, same key, LUFS ±0.001). R3 stays: accuracy over speed.
- Edge fades (`web.EDGE_FADE` = 64 samples, gain √(n/64) in, mirrored out; pads and saved samples): copied from a
  Traktor 4 Remix Deck capture (drag the track's title from a deck with an active loop onto a cell → 16-bit WAV in
  Browser.Dir.Loops, exact loop length, no tail, NML LOOPINFO ORIGINAL_LOOP_SIZE). Ours then matches that capture
  within 2 16-bit steps. Without them a loop ending at full level clicked in Traktor (deck loop and Remix Deck, any
  sample rate, perfect BPM/grid; a 0.5 s tail also fixed it). `saved_sample.format` 2 = with fades (comment ", edge
  fades"); older samples no longer count as saved.
- Traktor's MP3 timeline (`traktor.audio_offset`): Traktor time = decoded time (ffmpeg = Essentia, checked to the
  sample) + 1152 if the MP3 has a Xing/Info frame (`_mp3_info_frame`) + round(ffmpeg start_time × sr) (the LAME delay
  ffmpeg skips). Verified to the sample with quantized Remix Deck captures (16-bit, edge fades, clipped at 1.0, else
  identical to the decode) of one track per kind: 2257 (413 MP3s, LAME 3.100 and Lavc/Lavf), 1152 (31), 1681 (5),
  0 (19, and FLAC). Onset matching against Traktor's Transients files (version byte, uint32 count, records <f8 time s,
  <f4, <f4>) agreed only on average: periodic onsets make single tracks ambiguous; don't use it per track.
  Used everywhere audio meets Traktor's grid: loop cuts (`_render_loop`, `saved_sample.format` 3), cue bars and
  waveform (`cues._on_traktor_time`, BARS_VERSION bars-2 / wave-2), the page's player (`web.deck_audio`: MP3s with an
  offset are served as a 24-bit FLAC copy with the offset in front, data/deck-audio, last 6 kept, ≈5 s to make).
- Smooth loop point (`web._render_loop`, `?smooth=1` / save `smooth`, page checkbox in localStorage `ta-smooth-loop`):
  the last SMOOTH_MS (5) raised-cosine crossfaded with the whole samples just before the start; all other samples
  identical to the exact cut; `saved_sample.smooth`, comment ", 5 ms crossfade". User heard a faint click at a clean
  (0.025 step) loop point in the page and in Traktor's Remix Deck, 2026-10-24.
- Loop cuts (`web._cut`, page loops and saved samples): `-ss` straight to the loop start starts the MP3 decoder cold and
  its first ~660 samples (15 ms) came out wrong, a click every time round. Decode from 0.5 s earlier and cut with an
  output `-ss`: identical to the whole decoded track (0 diff, MP3 + FLAC, also at 0.2 s).
- `mix_suggestions` same genre = exact genre name (genre/genre_2/my_genre, the columns it always searched), as the Smartlists; `search(genre=)`
  stays a substring search over genres, styles and the genre tag.

## Tried, no gain (don't retry without a new idea)
- CNN key (2026-10-31, 469 tracks vs Traktor, CNN 75.1%): tuning-corrected CQT 74.6%; averaging ±1/±2 semitone
  pitch-shifted inputs 74.6/74.4%; when the CNN disagrees, Traktor's key is in its top 2 only 13% (top 5 31%), so
  rescoring its runner-ups can't help. CKA ideas (collection chroma templates, CKA choosing among the CNN's top keys,
  the original CKA's suggestions: 63.1%) are on branch `cka`.
- MAEST on mix segments (2026-10-25, 65-min set at ~145, 33 two-minute segments, all 30 s patches, no psy gate):
  3/33 segment genres changed; agreement with the playing track's @145 view genre 8/33 either way (own tempo 1 vs
  0); +125 s on a 72 s analysis. Both models hear the tempo as psy; the tracklist's psy gate is what fixes that.
  Mix timelines stay EffNet only.
- Cue model (2026-10-23, 447 tracks, CV F1 0.629 exact bar): a second stage over neighbouring bars' scores;
  fixed top-k; "lead-in" features (next w bars vs the w before the previous w, kick share before/after,
  biggest single-bar fall in the last 8 bars): 0.627; a bonus for 8-bar boundaries where the kick goes out/back
  for the phrase (0.1-0.5): 0.629-0.538. Estimating each track's phrase offset from audio: right 74% vs 79% for
  the grid marker. The user's own cues are on the marker's 8-bar grid 67% (75% on the track's own most common
  offset; 8.5% half a phrase off, the rest 1-3 bars off, half at a kick change), the suggestions 78%.
  On Traktor's timeline (bars-2, 2026-10-24) the cue model scores F1 0.632 (precision 0.616, recall 0.650): same as
  before within noise; the 51 ms shift is small against a 2 s bar.
- Colour model: hype as a feature; approachability/energy-arc alone over MAEST+sound; direct distortion measures
  (flatness, dissonance, crest, 2-5 kHz, clipping: flat across colours); MOSS-Music-8B 1-10 ratings (AUC 0.39-0.55
  vs 0.63 for the model, ~45 s/track).
- Colour model, for "pretty vocal/melody over a driving beat" and "hard drops diluted by the average": weighting
  vocals / moods / melodic instruments up (x2-x30); per-section features (EffNet heads per window: loudest-quarter vs
  quiet means, p90/p10/std, vocals while loud; EffNet sound of the loudest quarter). On the test collection
  0.94 per-colour error at best vs 0.94 now, worse with all 242 section stats (up to 1.05). The heads tell that a
  vocal is there, not that it's beautiful. Untried: MAEST per section (hours of compute).
- Colour model: blending the scale model's position with the classes model's expected colour (0.25-0.75): 0.93-1.02
  per-colour error vs 0.92 for the scale model alone. The classes model is worst at the ends (Magenta 1.47 vs 1.11)
  with ~35 tracks per end colour.
  Averaging the top scale models (descriptors weighted ± calibrated, + MAEST+instruments / MAEST+sound): 0.92-0.97
  vs 0.92. The scale models make the same mistakes, and every blend pulls the ends back toward the middle.
  Stacking all 17 candidates (nested CV): plain average 1.03; learned non-negative weights 0.913 vs 0.919 but
  within-one 80% vs 83% and plain average error 0.92 vs 0.89 (trades Red 0.89 → 1.10 for Magenta 1.11 → 0.90);
  Caruana ensemble selection 0.920 (picks the calibrated model 84%).
