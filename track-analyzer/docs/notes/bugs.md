# Bugs found and fixed: don't reintroduce

Detail moved out of CLAUDE.md so it loads only when needed. Code is the source of truth.

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
  stereo by PyAV's resampler (`decode`; = the `ffmpeg -ac 2` bits). Rows for files that are gone are dropped by every scan unless they're on
  a drive that isn't mounted (/Volumes/<disk>; Linux /media, /run/media, /mnt via ismount). `nfc()` composes a
  path on macOS always, on Linux only if that spelling exists (ext4 compares bytes).
- `scan` recomputes hype after analysing anything (it's a rank within the collection), and on any scan (even with nothing new: the early return too) while no track has hype yet (a first scan used to leave the column empty until `hype` ran); `hype` is also kept on rescan.
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
  0 (19, and FLAC). m4a with an edit list (AAC priming; ffmpeg trims it): minus its length (-1024 for an ffmpeg-made
  AAC), Traktor plays it that much earlier. Seen on one track only: its grid (right by eye in Traktor) read +18 ms and
  its NUO 4.1.4 stem -23.2 ms with 0, both on (-5 ms, 0) with -1024. Negative offsets cut from the front instead of
  padding; `traktor.offset_key` adds them to the bars/wave/gridcheck cache keys (old MP3/FLAC keys unchanged).
  Onset matching against Traktor's Transients files (version byte, uint32 count, records <f8 time s,
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
- LoudnessEBUR128 (Essentia, so also our bit-identical Rust copy) reads 96 kHz as silence (-70 LUFS, range 0) and 192 kHz
  ~3 LU loud; 22.05-88.2 kHz are right. `analysis.loudness` resamples above `LOUDNESS_MAX_SR` (88.2k) to 48 kHz first
  (`resample_medium`). Found on a 96 kHz Traktor recording (2026-10-31); no collection track was affected.
- `mix_suggestions` same genre = exact genre name (genre/genre_2/my_genre, the columns it always searched), as the Smartlists; `search(genre=)`
  stays a substring search over genres, styles and the genre tag.
