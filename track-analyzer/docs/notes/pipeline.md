# How the analysis works (the whole pipeline)

Detail moved out of CLAUDE.md so it loads only when needed. Code is the source of truth.

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
  stdout → stderr for MCP); output identical to serial, 121 → 71 s on a 65-min mix. `measure(jobs, meter)`
  keeps a count line updated with \r (segments; the tracklist's tempo and key matching, by track), which the job bar shows as `live`.
