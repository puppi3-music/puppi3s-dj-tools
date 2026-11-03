# Cues and grids: cues.py, gridfix.py

Detail moved out of CLAUDE.md so it loads only when needed. Code is the source of truth.

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
- Hand edits (`edit.py`, `edit` command, POST /api/track/edit → `web.edit_track`, the panel head's ✎ Edit
  (`S.editing` = path; `ed()` turns the facts into fields, `editBar`/`bindEdit` Save/Cancel/↺/Undo)): colour/key/genre → `traktor.set_track` (INFO COLOR; MUSICAL_KEY VALUE + INFO KEY as Open
  Key = Camelot + 5, `open_key`/`camelot_value`, MUSICAL_KEY inserted after LOUDNESS if missing; INFO GENRE; Hype in
  COMMENT only where it already has one and tag writing is off; `stamp_if_unchanged`; `_save` backup) first, then the
  NATIVE db: colour → traktor_color + colour hold + `accept_guesses` (a training label), key → traktor_camelot (Traktor
  stays their source, so a later Traktor edit wins). Genre/hype/BPM → collection.db `manual` (path, field, value,
  `was` JSON of the replaced columns, per column) via `collection.set_manual`; `apply_manual(con)` (native db only)
  puts them back after `store_traktor_info`, scan, regenre and `hype.compute` (whose returned dict gets them, so
  `hype --traktor` writes them); `MANUAL` = genre → genre + my_genre, hype, bpm → traktor_bpm (also merged into
  `load_traktor_info`, so genre rules and tempo views use it; Traktor's BPM is never written). Genre set = held.
  `edit()` returns `undo` {changes, clear, traktor_genre}: the page's Undo sends it back (colour may be set and
  cleared at once: set + release the keep-always hold). Refused while a job runs (web) and, for Traktor fields,
  while a tempo view is loaded. Feedback kind `manual`.

## Written approvals are kept (cue_written)
After a write, an approval moves from `cue_plan` to `cue_written` (it used to be deleted). `cues.apply(restore=True)`,
used by `sync`, also re-adds the new cues of written approvals that are missing in the DJ app (deleted there by hand);
only adds are replayed, never moves/deletes/renumbers, and it stays quiet about cues still there or regridded tracks.
First use backfills `cue_written` from `cues-last-written.json` (approvals written before the table existed).
`sync --cues-from-other-app` (page: checkbox under "Sync everything") also copies the other DJ app's hot cues
(cue points and loops in slots 1-8; Traktor <-> Rekordbox, tracks in both) into slots still free in the chosen
one, at the same time; a cue already at that spot is skipped, nothing of yours moves (`cues.other_app_plans`).
The app keeps no copy of cues itself: they're read live from the DJ apps.
