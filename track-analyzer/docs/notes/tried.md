# Tried, no gain: don't retry without a new idea

Detail moved out of CLAUDE.md so it loads only when needed. Code is the source of truth.

- CNN key (2026-10-31, 469 tracks vs Traktor, CNN 75.1%): tuning-corrected CQT 74.6%; averaging ±1/±2 semitone
  pitch-shifted inputs 74.6/74.4%; when the CNN disagrees, Traktor's key is in its top 2 only 13% (top 5 31%), so
  rescoring its runner-ups can't help. CKA ideas (collection chroma templates, CKA choosing among the CNN's top keys,
  the original CKA's suggestions: 63.1%) are on branch `cka`.
  Pooling the last map over part of the track (2026-10-31, 443 tracks, mean of all 76.7%): loudest 75/50% of
  columns 76.3/75.6%, tonal by CQT flatness 76.5%, by chroma peakiness 77.0% (1 fixed, 0 broken), loud AND tonal
  76.1%, intro/outro trimmed 76.3%, loudness-weighted 76.1%, one vote per column 75.6%. The key doesn't hide in parts.
  madmom's CNNKeyRecognitionProcessor as a second model (same day, 443 tracks): alone 75.4%; averaged with ours
  (arithmetic or geometric, madmom weight 0.25-0.75) 75.6-76.1%, always more broken than fixed. It gives our key
  on 97.5% of tracks (same KeyNet family), so there's nothing to combine; ~12.5 s/track. Not added.
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
