# Prompt for Claude Code: improve genre scanning in puppi3-dj-tools

Paste everything below the line into Claude Code, run from the root of the puppi3-dj-tools repo.
First copy the `groove_genre` folder (groove_genre.py, README.md, tests/) into the repo, for example
at `vendor/groove_genre/` or next to the analyzer code.

---

I want to improve the genre detection in my track analyzer (this repo, puppi3-dj-tools). Please read
this whole brief, explore the code, then propose a plan before you change anything.

## The problem

My analyzer reports genre and style scores for tracks and for whole DJ mixes. On my mixes it
consistently over-reports psytrance. Across six sets, the unrestricted style scores put Psy-Trance on
top every time (0.54–0.59, with Goa Trance second). For one set it suggested "Psytrance / Hi-Tech / Darkpsy",
which is impossible at 145 BPM, because hi-tech runs well above 160. Per track, it labeled 15 of 85
tracks psytrance once mixed.

Direct audio measurement of all six sets shows this is wrong. I play melodic house and techno and
progressive house (originally about 126 BPM) sped up to 145 BPM. The sets have:
- no psytrance bassline: no KBBB 16th-note gallop (gallop score 0.22–0.26 in every set, where psytrance KBBB is 1.00)
- an open hi-hat on every offbeat (3.5–8x louder than the other 16ths), with weak 16th-note hats
- sidechained bass that swells toward the offbeat
- short, frequent breakdowns (median 8–9 bars, almost none 32+ bars)
- static minor-key harmony
The correct label is Tech Trance (descriptor: Melodic Tech Trance). The model is most likely using
tempo plus a dense low end as a shortcut for psytrance.

## What I'm giving you

`groove_genre/groove_genre.py` is a tested, standalone module (numpy, scipy, librosa, ffmpeg) that:
- `analyze(path_or_array, stride=1)` returns per-30-second-window structural features plus a summary
  (tempo and tempo ramps, 16th-note bass gallop, offbeat hat ratio, 16th hats, bass offbeat swell,
  kick length and rumble, sidechain pump, chord-change rate, minor share, breakdowns in bars, melodic ratio)
- `genre_evidence(summary, model_scores=None)` scores each genre from those features, with a
  "defining trait" gate per genre (for example, the psytrance family needs the gallop). It returns
  a label (category, descriptor, secondary tags), human-readable reasons, and, if given model
  scores, `adjusted_model_scores`: each model score multiplied by `floor + (1 - floor) * fit`
- `python groove_genre.py --self-test` and `pytest tests` verify it (synthetic psy, offbeat and sustained
  bass patterns, plus regression fixtures from my six sets in `tests/puppi3_sets.json`)
Read `groove_genre/README.md` first.

## What I want

1. **Explore and explain.** Find where my analyzer computes genre/style scores, the "psy limit", the
   per-track "genre in the mix" judgment, clip suggestions and the report output (including the MCP
   tools, if this repo exposes them). Tell me how it works today, which model it uses, and where
   `groove_genre` should plug in. Don't change code until I've seen this.
2. **Integrate structural evidence**, keeping the existing model:
   - Run `groove_genre.analyze` alongside the existing analysis. Reuse already-decoded audio if possible;
     don't decode twice. For mixes, use stride 2 if speed matters.
   - Feed the model's style scores into `genre_evidence` and report **both** the raw and the
     adjusted scores. Base the headline Genre line on the adjusted scores.
   - Replace or complement the current "psy limit" with the evidence gate, so psytrance can only
     lead when the gallop is actually present. Keep the existing setting working, or migrate it cleanly.
   - Per-track "genre in the mix": run `analyze_window` on each track's segment of the mix (from the
     tracklist timings) and apply `genre_evidence`. Treat it as low-confidence during long blends,
     when two decks are playing.
   - Add a short "Why" section to the report: the top three reasons from `fits[genre]["reasons"]` for
     the chosen label and for any genre that was demoted (for example, "Psytrance demoted: 16th-note bass
     gallop 0.24 (psy needs about 0.5+)").
   - Add the label fields to the output: category, descriptor (for example Melodic Tech Trance) and
     secondary tags (for example "Psytrance (moments)").
3. **Keep it safe:**
   - Keep every existing output field and CLI flag. Add fields; don't rename or remove them. If MCP tool
     schemas change, change them additively.
   - Thresholds stay in the `GENRES` table in one place. No magic numbers scattered through my code.
   - Add tests: the module's tests must pass inside my repo, plus an integration test that runs the full
     analyzer on a short synthetic or excerpt file and checks the new fields.
4. **Verify on real audio.** If you have access to my recordings, run the full analyzer on at least one
   of them (for example Recording_2026-11-06_19h17m34s) and show me before and after: raw versus adjusted
   style scores, the label, and the reasons. Expected result: category Tech Trance, Psytrance demoted
   below Trance and Tech Trance, descriptor Melodic Tech Trance (Tech Trance for the harder sets,
   Full Bodied and Pump Up The Jam).
5. **Calibration hook.** Add a simple way for me to drop labeled reference tracks into a folder (for
   example `reference/psytrance/*.mp3`, `reference/tech_trance/*.mp3`, `reference/uplifting/*.mp3`,
   `reference/melodic_techno/*.mp3`, `reference/hard_techno/*.mp3`) and run a script that prints the
   features per genre and flags any reference track the rules mislabel. The current thresholds were
   calibrated on synthetic patterns and my own sets only, so the psytrance, uplifting and hard techno
   branches need real references before I fully trust them. Don't retune thresholds without showing
   me the reference numbers first.

## Ground rules

- Plan first, then make small commits with clear messages.
- If something in this brief conflicts with how the code actually works, tell me instead of guessing.
- Ask before adding heavy dependencies or downloading models. Note: essentia.upf.edu doesn't load on my network.
- Finish with a short summary: what changed, how to run it, the before and after results, and what's still uncalibrated.
