# Colour, hype and set planning: colors.py, hype.py, setplan.py

Detail moved out of CLAUDE.md so it loads only when needed. Code is the source of truth.

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
- Docker (`Dockerfile`, `compose.yaml`; README "Docker"): linux/amd64 only (the dsp wheels and `[tool.uv] environments` cover Linux x86_64 only; on Apple
  Silicon it runs emulated, TF included). Target `app` (web on 0.0.0.0, HOME=/home/dj so Traktor's folder mounts at
  ~/Documents/Native Instruments, data /data, any uid) and `test` (+ models from stage `models`, which copies only
  `model_files.py` (the model list + downloader, no package imports) so code changes reuse it; never published:
  licence; + `scripts/make_fixtures.py` library: 4 synthetic tracks 124-145 BPM, MP3/FLAC/WAV, NML with grid, 5 cues,
  a loop). `docker compose run --rm test` = `scripts/docker_smoke.py` (scan BPM/key exact, Traktor refusal, write +
  undo byte-identical, web 409/Host checks; ~1 min emulated). CI `.github/workflows/docker.yml` builds target `smoke`
  (= test + the smoke run as a build step as uid 1000, `outputs: type=cacheonly`: no 2 GB image export/load; cancels
  the previous run of the same PR). Develop
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
  (`traktor.set_colors`, skips tracks recoloured since). `--track PATH` (repeatable; `review(only=)`) = only those listed tracks,
  thresholds ignored, refused if any is kept always. The page's Approve / Keep only mark rows: native collection.db `colour_plan`
  (`colors.set_plan/plans`, POST /api/colours/mark; stale marks dropped by review); **Write…** = job `colors_write_marked` =
  `review --apply --planned` (set_colors for approvals, then keep-always + colour_keep feedback, marks cleared; no undo offered when
  only keeps). **Retrain now** (shown when `recoloured_since_training`) = job `colors_train` {views} = `train --refresh-views`.
  `colors.sync_traktor_colours()` (review, web `tracks`/`stats`) copies Traktor's colours into the native db's `traktor_color` whenever
  collection.nml's mtime changes (not while a view is loaded), so recolours, undos and hand edits show at once; review lists a track
  whose colour changed since training with `recoloured` (its colour now; not approvable or markable, not counted toward the
  threshold, skipped by --apply; `recoloured_since_training` shows Retrain now) until retraining drops it from `disagreements`. Asks per track (y/n/k/a/c; `--yes` skips); `k` = keep always → collection.db `color_keep`, which
  `--apply` skips. `colors review --kept [--keep|--forget TEXT]` shows/edits it (`--keep` alone is refused; ≤10 matches).
  `read_traktor_colors` refuses while a tempo view is loaded (train, explain, apply, `--keep`). `colors apply`: scan --traktor (new only) → deep features
  for uncoloured → refresh predictions → `traktor.sync(update_genres=False, make_smartlists=False)`.
- The page never shows the final model's guess for a track it learned from (it leans toward your colour): `web._unbiased_colours`
  swaps in the CV position (`colors.unbiased_positions`: bundle `cv.paths`, or older bundles' `labels` order when lengths and
  truths match) + the view's shift; `colour_unbiased` on /api/tracks. Approve / Recolour are disabled while a view is loaded.
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
