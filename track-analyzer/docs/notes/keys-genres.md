# Key and genres: keycnn.py, mygenres.py, dj_genres.json

Detail moved out of CLAUDE.md so it loads only when needed. Code is the source of truth.

- `keycnn.py`: key from MusicalKeyCNN (puppi3-music/MusicalKeyCNN-CKA, from a1ex90/MusicalKeyCNN; MIT): KeyNet in
  numpy (`forward`; weights `starter/keynet.npz`, BN folded, made by `scripts/convert_keynet.py` from keynet.pt),
  input = librosa CQT exactly as the repo trained on (hence the librosa pin). Checked: same key as torch on 32/32
  (probabilities within 2e-7) and as the repo's own pipeline on 459/459. `_conv` is im2col (one matmul per layer,
  0.35 vs 0.78 s/track; same key and 3-decimal confidence as the per-position loop on 443/443). `analysis.key_cnn` → `cnn_key`,
  `cnn_camelot`, `cnn_confidence` (tracks, tempo views and the tracklist check's heard keys, `_measure` want key "cnn"; mix timeline segments still use Essentia).
  `keys.py`: `keys` command, backfill for tracks scanned before (spawn pool, resumable) + MIREX-category report.
  The Cross-Key Analysis part of that repo lives on branch `cka` (it didn't beat the CNN; kept for development).
- `mygenres.py` (+ `analysis.train`, `collection.refresh_my_genres`): your own genres. Examples from a folder
  (`collect_training_set`), ticked Traktor playlists (`traktor.playlists`: any with tracks, `OWN_FOLDERS` skipped;
  genre = playlist name, same names merged, a track in two genres dropped) or Traktor's Genre field (`traktor.genre_field`:
  multi-genre values dropped; refused while a tempo view is loaded); `mygenres.traktor_choices` = /api/genres/traktor,
  bundle `source_kind` for Learn again. `train` learns the examples' genres + `OTHER_GENRE` ("∅ everything else") from the scanned collection's cached EffNet means
  (`background_embeddings`: examples and copies of them, cosine ≥ `SAME_TRACK_COS` 0.995, left out; ≥ 30 needed, ≤ 1500;
  copies measured 0.99999, different tracks ≤ 0.983), LR C=0.5 balanced; CV report kept in the bundle
  (`accuracy_per_genre`, `kept_built_in`, `others_given_yours`). Tested 2026-11-02 on the test collection's genres
  (pairs trained from 7-20 tracks): plain probability or distance gates let 40-85% of other tracks through, the
  everything-else class 2-13% (own tracks found 18-69%, harsher than a distinct genre would be). Columns:
  `trained_genre` (+ confidence; None = none fits) and `nearest_genre` (the closest of yours, everything else
  ignored) for every track; `my_genre` per the switch `my_genres_mode()` (setting `my_genres_mode`, starter unless
  `my_genres_not_ready()` is None: 30 examples, ≥ 10 each, an everything-else model): starter None, both
  trained_genre, mine nearest_genre; or a hand genre (MANUAL). Everything that uses your genre reads `my_genre` (page, mix suggestions, sets, LLM,
  `tag`, `traktor --genres-from-analysis`; `known_genres` adds trained names only while in use). The switch/train
  refresh the native db and every tempo view (`_refresh_views`), then `apply_manual`. Page: Setup step 7 ("Genres used" Starter / Both /
  Mine only; Both and Mine preview first), /api/genres/preview?mode=, use {mode}, browse (folder dialog via `pick_file(folder=True)`). A db from before gets `my_genre` moved to
  `trained_genre` once (collection.db()).
- `dj_genres.json`: maps Discogs styles to DJ genre names (rules with "needs" groups + BPM range); a user copy in
  `data/dj_genres.json` wins.


### Genre set
- 42 DJ genres. Progressive Trance is its own genre (126-145 BPM, taken out of Trance's needs).
- Deliberately NOT added: Electro House (the models give Discogs' 2010s "Electro House" to most modern melodic/
  peak-time house), Ambient (it went to a psytrance track in testing), Afro/Organic House as 2-style combos (matched
  nothing). Big Room, Future House, Afro House, Organic House, Melodic Techno, Future Bass, Hard Groove don't
  exist in Discogs: only via `train` with example folders.
- No Hyperpop either: Discogs has the style now, but neither model's class list (400 / 519, from the Discogs list
  at training time) has it; digicore/glitchcore neither: README §7 and GUIDE "Genres" say so plainly, as
  it's a scene trans and queer artists built, and point to ✎ Edit (a typed genre is held) or Learn your genres.
