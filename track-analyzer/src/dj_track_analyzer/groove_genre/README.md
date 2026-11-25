# groove_genre

Structural genre evidence for EDM tracks and DJ mixes. Built for puppi3-dj-tools to stop a style
model from mislabeling melodic techno at 145 BPM as psytrance.

## Why it exists

Style models such as Discogs-EffNet (Essentia) learn tempo and low-end density as shortcuts.
At 138–148 BPM with a constantly driving low end, they over-select psytrance, even when the
track has none of psytrance's defining traits. Across six Puppi3 sets the analyzer put
Psy-Trance on top every time (0.54–0.59). Direct measurement showed no psytrance bass engine
in any of them.

`groove_genre` measures the traits that genres are actually built on and uses them to check
and correct the model:

| Feature | What it measures | Why it matters |
|---|---|---|
| `bpm`, `tempo_sections` | Tempo per 30 s window, refined to 0.05 BPM; ramps detected | Genre tempo ranges |
| `gallop` | Share of between-kick bass movement at the 16th-note rate | Psytrance's KBBB bass; calibrated psy 1.00 / sustained 0.33 / offbeat 0.11 |
| `psy_windows` | Share of windows with gallop > 0.5 | Psy moments inside a non-psy set |
| `hat_offbeat`, `hat_16ths` | Offbeat open hat vs 16th-note hats | Trance/techno offbeat hat vs psy 16th hats |
| `bass_swell` | Bass level on the offbeat vs neighboring 16ths | Offbeat/sidechained bass (trance, melodic techno) |
| `kick_ms`, `rumble` | Kick length; kick-band energy between kicks | Hard techno rumble kick |
| `pump` | Sidechain ducking of pads and leads | Trance and melodic techno |
| `chord_change`, `minor_share` | Harmonic movement per bar; key mode | Uplifting progressions vs static minor loops |
| `breakdown_*` | Kick-out sections measured in bars | Uplifting (long) vs tech trance (short) |
| `melodic_ratio` | Harmonic vs percussive energy, 300–3000 Hz | Melodic vs drum-led |

## Use

```bash
# from track-analyzer/ (numpy, scipy, librosa are already dependencies; ffmpeg must be on PATH for a file path)
uv run python -m dj_track_analyzer.groove_genre --self-test   # synthetic psy / offbeat / sustained patterns
uv run python -m dj_track_analyzer.groove_genre set.mp3 --model-scores scores.json --json result.json
uv run pytest tests/groove_genre
```

```python
from dj_track_analyzer.groove_genre import analyze, genre_evidence
res = analyze("set.mp3")                    # per-window features + summary
ev = genre_evidence(res["summary"], model_scores={"Electronic---Psy-Trance": 0.56, "Electronic---Goa Trance": 0.46})
ev["label"]                  # {"category": "Tech Trance", "descriptor": "Melodic Tech Trance", "secondary": [...]}
ev["adjusted_model_scores"]  # model scores re-weighted by measured evidence
ev["fits"]["Psytrance"]["reasons"]   # human-readable explanation
```

Model labels may carry the Discogs parent genre ("Electronic---Psy-Trance"): `ALIASES` matches the style part
(`analysis.split_style`). The original brief and the six-set validation are in `docs/notes/groove-genre/`.

For a single track (3–8 minutes), use `stride=1`. For mixes, `stride=2` gives the same conclusions about twice as fast.
Speed: about 3–5 s of compute per 30 s window on one CPU core.

## How the correction works

Each genre in `GENRES` has a tempo range and trait checks that each return a fit from 0 to 1 and a reason.
A genre's fit is `tempo_fit × (0.1 + 0.9 × gate) × (0.35 + 0.65 × mean(trait fits))`, where `gate` is the genre's defining trait (the 16th-note gallop for the psytrance family, a rumble kick or low melodic content for hard techno). A missing defining trait all but rules the genre out. A model score is multiplied by
`floor + (1 − floor) × fit` (default floor 0.15), so contradicted styles are pushed down but never
deleted. Thresholds live in the `GENRES` table. Tune them with reference tracks, not by eye.

## Limits

- Calibrated on synthetic patterns and six Puppi3 mixes. Before trusting the psytrance, uplifting
  and hard techno branches, add real reference tracks (see `docs/notes/groove-genre/validation_results.json`).
- Mono, signal-processing features, not a trained model. Trust large, consistent patterns over decimals.
- In DJ mixes, overlapping decks blur patterns. Per-window results are more reliable than per-track ones during long blends.
