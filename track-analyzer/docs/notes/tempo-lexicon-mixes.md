# Tempo view, Lexicon, clips, mix reports, tracklists

Detail moved out of CLAUDE.md so it loads only when needed. Code is the source of truth.

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
- `mixreport.py`: `mixes` command + /api/mixes-report (Mixes tab `#mixes/~all`, `renderMixReport`; in snapshots): saved mix
  JSONs (newest per recording) added up: `keys` (heard = our CNN key ≠ Traktor's, key lock on, any heard model), `view`
  (heard_mood vs own_mood / view_mood, mean and mean abs distance; genre as heard = view's / own), `shape` (each set in 20 equal slices: hype time-weighted over the tracks playing via
  `web.played_tracks` + blend lag, colour played longest, LUFS; per mix and mean, drawn by `shapeTimeline`), `arc` (timeline segment
  mood by fifth, each mix once), transitions (CNN-keyed mixes only). Read-only.
- `tracklist.py`: `analyze <mix> --tracklist`: Traktor history NML (COLLECTION entries joined to PLAYLIST
  ENTRY/PRIMARYKEY KEY = VOLUME+DIR+FILE; EXTENDEDDATA STARTDATE = y<<16|m<<8|d, STARTTIME = seconds of day,
  DURATION = deck run time, which includes faded-out tails, so plays overlap ~70% of a mix). Recording start from
  Traktor's `Recording_YYYY-MM-DD_HHhMMmSSs` name or `--start`. No start or no covering history with `--tracklist` auto → `NoHistory` (a SystemExit): `analyze`
  carries on without the check and saves `tracklist_skipped` (the reason; summary line), `tracklist_skipped_kind` (start / history:
  the page's own wording, `skippedNote`) and `recording_start`; the page's **Started at** (one recording) = `--start`; a named file or `--chapters` still stops. Main stretch = start → next start, shifted by a
  measured blend lag (0-150 s, the one where most keys match), middle 60% judged. Key lock on unless off wins by
  ≥2 tracks. Sped >4%: genre = mix acts at played BPM with PSY_GATE vs native acts per track
  (speed-up and stems/effects both really change the genre, only the psy shortcut is
  removed; earlier "native + (mix − view)" and "library genres by time" were dropped). `mixing` = mix − view
  acts. Fields `*_uncorrected` = no gate. Since 2026-11-01 also `key_model: "cnn"`, `keylock.matches_per_lag`, per track
  `library_camelot`, `heard_mood` (mood_patches over the judged window), `own_mood`, `view_mood`/`view_genre` (the view
  db near the played tempo), `transition` (`camelot_step` from the previous heard key) + `transitions` totals.
  Highlights carry `energy` (`mood_energy`: mean of party + aggressive heads per second); `--clip-mood W` adds W × energy
  to the score (default 0 = the same picks as before, checked). `--chapters`/`--highlights`/`--offset`: video time = recording time −
  first track's start (`anchor()`; `first_sound_seconds` without a tracklist) + offset, offset read off the
  finished video by the user (required for --chapters; never guessed from the audio); highlights from 1 s means
  of EBU short-term loudness, level + 0.5·rise (capped 8 LU), windows touching < −45 LUFS skipped. Missing data → SystemExit saying how to get it.
- `standalone.py` (`result.standalone`, with a tracklist; off with `--no-standalone`): long blends that work as
  their own track. Each original is lined up with the mix (speed searched ±1.5% around played/own BPM, lineup
  points every 12 s, ≥60% agreeing to 1 s), then each bar of a change is unmixed by NNLS on a 96-band log
  spectrogram (8 blocks per bar). Stem file (`gridfix.find_stem`) → per-stem fit, a track plays in a bar when ANY
  stem is within 12 dB of that stem's solo level (first 20 s of its window); no stem file → whole-track fit, or
  Demucs over the played stretch with `--standalone-separate` (`separate()`; never run for real yet). Rule: both
  tracks ≥ 32 bars in a row, single off bars allowed; key clash only noted in Why. Fixed bar, no quota: no pick →
  `closest_miss`. Output: `picks`, `changes`, `closest_miss`, `measured_with`, `rule` (+ `near_db`, `min_bars`, `gap` for the page's wording), `names`, `timestamps`
  (chapters at offset 0 for the full-set description). Page: `standalonePanel(d)` in web/js/mixes.js: level strip per change from `bars`, preview via
  `/api/mix-audio?id=<saved id>` (web.mix_audio: only the `file` a saved dj_mix names; Range for seeking), timeline row
  `saTimelineRow`.

### Tempo view (`tempo.py`)
- `analysis.stretch()` = Rubber Band CLI R2 `-c 6 --centre-focus` for the scan (`brew install rubberband`, 4.0; R3 only for the player, `listen=True`). R3 moved style scores 0.52-0.68 at +0.6 % vs 0.16-0.22 for a plain resample (flipped genres), R2 0.20-0.26 (2026-11-12); `tempo.VIEW_STRETCH` in the view cache version; `analyze_file(tempo=(src, target),
  genre_bpm=target)`. Source BPM = Traktor's, else ours. BPM is set to the target, not measured on stretched audio.
  A track already at the view's BPM isn't stretched (`stretch` returns the audio as is; neither engine is a passthrough at ratio 1: R3 is up to
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
- Juke hold (mix tracklist only, 2026-11-11): house played at ~163 came out Footwork on Juke 0.12-0.13 once psy
  was capped. `tracklist.corrected()` caps Juke/Footwork at 1.5 × own-tempo too (`tempo.JUKE_STYLES`).
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
