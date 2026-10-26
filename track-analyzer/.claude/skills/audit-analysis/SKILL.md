---
name: audit-analysis
description: Audit the track analyzer for bugs that would make it misread tracks — models wired wrongly, data used in the wrong place (our BPM vs Traktor's, stretched vs native values), silent fallbacks, cache gaps — plus efficiency wins. Use when asked to review, audit or sanity-check the analysis code or its results.
---

# Audit the track analyzer

Goal: find things that make the analysis **misread** a track, not style nits. Read-only: report
findings, then ask the user before fixing (they prefer to decide; see CLAUDE.md).

## 1. Run the live checks (real models and data on this Mac)

```
uv run python .claude/skills/audit-analysis/scripts/checks.py 2>&1 | grep -vi warn
```

Checks: `rules` (style names in dj_genres.json vs both models), `heads` (node names and classes from
each model's JSON), `calibration` (EffNet vs MAEST score scale), `bpm` (colour shift from the BPM
input alone), `cache` (tracks missing cached MAEST etc.), `timing` (seconds per stage). Pass names
to run only some. Compare with the baselines below; a change is a finding.

Baselines (2026-10-22, 471 tracks; rules/calibration/bpm re-checked 2026-10-25, unchanged):
- rules: all valid; MAEST-only: Neo Trance, Hard Beat, Doomcore, J-Core, UK Funky, Footwork.
- calibration: MAEST runs ~1.3x EffNet (top score 0.55 vs 0.42); MAEST-only styles are scaled by the per-track ratio (fixed 2026-10-22).
- bpm: BPM input → 145 moves the colour by −0.15 on average (sd 0.19, −1.48 … +0.22) with the model trained 2026-10-22 14:39 (the older model: −0.30, sd 0.10). This is the model's sensitivity; since 2026-10-22 the view feeds it the native BPM, so this number should no longer show up in view shifts.
- cache: nothing missing on 469 tracks (2026-10-25; the conflict copies and the gone row were dropped by scans).
- timing (2026-10-25, PyAV decode + Rust loudness/mels): decode 0.7, rhythm 4.3, key 0.1, loudness 0.3, EffNet 1.3, heads 0.1, MAEST ~5.4 (first run includes loading) s; tempo stretch ~20 s (R3; kept for accuracy). Before the Rust port: decode 1.6, rhythm 3.2, loudness 1.4, EffNet 1.8, MAEST ~6-7.

## 2. Read the code with this checklist

**Model wiring** (analysis.py)
- Hard-coded input/output node names match `heads` output (MAEST input is `melspectrogram`; see CLAUDE.md).
- Positive-class index from the JSON (`classes.index(...)`), never assumed 0/1 (mood_party is `[non_party, party]`).
- Sample rates: models get 16 kHz mono with `quality=4` (as in Essentia's docs); rhythm/key get 44.1 kHz.
- Any per-track `except` that sets a "disabled" flag turns a model off for the rest of the worker's
  scan (`run_maest` does this), and later tracks quietly get EffNet-only genres.

**Which value is used where** (most misreads hide here)
- BPM: genre rules use Traktor's (`genre_bpm`); colour model uses ours; search/tags use `BPM_SQL`.
  Check that each new code path picks the intended one, and that it's assigned *before* it's used
  (e.g. `collection.scan` attaches `traktor_bpm` only after `write_tags` runs).
- Tempo view: anything fed to a model trained on native tracks (BPM especially) should be the native
  value unless the change is the point. Check `tempo._scan_one` → `analyze_file(tempo=...)` → `predict_color`.
- Same track in two caches: `use_view` / `base_cache()` swap `CACHE_DB` + `CACHE_VERSION`, so a read
  outside the intended context silently hits the other cache.
- `save_row` is `INSERT OR REPLACE` over `NAMES`: columns not in the scan result (`hype`, view-only
  `source_bpm`, `native_color_position`) are wiped until something recomputes them.

**Blends and thresholds**
- `label_scores`: styles in only one model aren't blended (see calibration).
- `dj_genre`: ×1.35 bonus, BPM fade, top-3 cut at 25% of best. Run `regenre` after rule changes.
- Colour: `predict_color` returns None when features are missing, so the track silently gets no
  prediction. Count Nones.

**Data hygiene**
- NFC vs NFD paths, `/Volumes/Macintosh HD` vs `/Users`, Syncthing conflict copies, 6-channel files.
- Stale rows: `emb` keeps old keys after tag edits (845 rows / 487 paths), and `traktor_color` is only cleared by `colors train/apply`.

**Efficiency**
- Work done on audio that a later step overrides (the tempo view measures BPM on stretched audio, then sets it to 145).
- Repeated `joblib.load` / sqlite connects per track; models loaded per worker (fine with spawn).

## 3. Report

One list, most severe first. For each: file:line, what goes wrong for which tracks, evidence
(a check's number or a concrete track), and the fix. Mark what's confirmed by a check vs only read
in code. Update the baselines above if they moved, and CLAUDE.md if a finding changes a documented number.
