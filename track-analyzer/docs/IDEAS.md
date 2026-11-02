# Puppi3's Track Analyzer: features and ideas

What the app does today, ideas for what it could do next, and approaches that were tried and didn't help (so nobody retries them blindly). Suggestions welcome.

Status: ✅ works · 🧪 partly tested · 💡 idea · ✗ tried, didn't help

Step-by-step recipes are in [GUIDE.html](GUIDE.html#tasks) (`open docs/GUIDE.html`); the full reference is [README.md](../README.md).

## What it does now

| Use | How | Status |
|---|---|---|
| BPM, key/Camelot, loudness, genre, mood and instruments for one track | `analyze <file>` | ✅ |
| Analyse a whole Traktor collection or folder (resumable, cached) | `scan --traktor` / `scan <folder>` | ✅ |
| Spreadsheet of everything | `data/collection.csv` | ✅ |
| Write genres (also several, "A; B") into file tags, e.g. for Navidrome | `tag --multi` | ✅ |
| Genres again after editing `dj_genres.json` (seconds, no re-analysis) | `regenre` | ✅ |
| Genres into Traktor + a genre Smartlist folder | `traktor` | ✅ |
| Learn a personal colour scale from already-coloured tracks | `colors train` | ✅ |
| Tracks whose sound disagrees with their colour; recolour the clearest ones | `colors review [--apply]` | ✅ |
| What each colour means: sound, playlists, sets, artists | `colors explain` | ✅ |
| Colour new tracks in Traktor in one step | `colors apply` | ✅ |
| Find tracks by hype (1–10), filter by colour/BPM/key, underrated "gems" | `hype` | ✅ |
| Hype score in Traktor's Comment | `hype --traktor` | ✅ |
| Ask a local LLM (LM Studio / Open WebUI) about the collection | `serve` (MCP) | ✅ |
| Mix suggestions: compatible key + BPM | via the LLM tools | ✅ |
| Set planner: an arc of colour and hype with keys that mix, written as a Traktor playlist | `set` | ✅ |
| Warm-up / peak / closing crates from colour and hype, as Traktor playlists | `crates` | ✅ |
| Timeline of a DJ mix (genre per 2 min) | `analyze` on a file over 12 min | ✅ |
| Clip suggestions from a recorded mix, previews and full-quality clips cut from the finished video | `analyze --highlights`, `clips` | ✅ |
| Undo changes to Traktor | `traktor --undo` | ✅ |
| The catalogue as heard at one tempo with key lock (145 by default, `--bpm` for any): genres, colours, hype; own CSV + NML; swap Traktor to it and back | `tempo scan / report / load / native` | ✅ |
| Redo a tempo view's genres, colours, hype after rule or colour changes (seconds) | `tempo refresh` | ✅ |
| The same view in Lexicon (genre, colour, hype in Comment and Energy, smartlists) | `tempo load --lexicon` / `native --lexicon` | 🧪 load tested on real Lexicon; `native --lexicon` only against a stand-in API |
| Everything in a local web page: charts, track list, hype, colours, tempo view, mixes; a snapshot file to share | `web` | ✅ |
| Keep a track's genre or colour as it is when writing | `hold`, or the web page | ✅ |
| Cue points and a mix loop per track, learned from your own cues; approve them and write them into free hot-cue slots | `cues train / scan / show / apply`, the web page | ✅ |
| Loop ideas saved as WAV samples in Traktor's sample folder, cut on Traktor's timeline | the web page | ✅ |

## Ideas

### Set building
- ✅ **Set planner.** `set` builds a set along an arc of colour and hype (preset, your own points, or the average of your History sessions) with keys that mix, optionally BPM steps, and writes it as a Traktor playlist. On the test collection the History average came out nearly flat (Orange/Yellow, hype 6–7 all the way): sets there mix hard and pretty tracks throughout, so a mean curve hides the shape. Checked 2026-10-30 on the test collection's History (15 sessions of 8+ tracks, 367 plays): nothing predicts where a track is played in a set (position vs colour ρ +0.14, approachability +0.09, hype −0.04, energy-arc measures < 0.1); Red is played evenly and Magenta too (35/35/31% by thirds).
- 💡 **"What next?"** Given the track playing now, suggest the next one that is one step harder or prettier, in a compatible key. Traktor writes its History only when a session ends, but its Broadcasting sends now-playing metadata live: pointing it at a small local Icecast-style listener would tell the page which track is on the deck, with no controller mapping.
- ✅ **Warm-up / peak / closing crates** (`crates`): by rule from colour and hype, warm-up sorted by approachability, as Traktor playlists. Energy arc left out: it didn't separate parts of a set in the History check above.
- 🧪 **Set review.** Done for recorded sets: the Mixes tab shows each set start to end (hype, colour, loudness per slice) from `analyze --tracklist`. Still an idea: the same curve from a History file alone, for gigs that weren't recorded.
- ✅ **Hype in the set planner.** Colour (hard ↔ pretty) and hype (chill ↔ peak) are its two axes.
- 💡 **Pairs you've already played.** The tracklist check knows which tracks followed which in recorded sets (and History knows it for all sets). A "played together before" graph could feed "What next?" and give the set planner a bonus for proven transitions, next to key and BPM.
- 💡 **Phrase-matched transitions.** The learned cues mark where each track's kick goes out and comes back. The set planner could line the outgoing track's last breakdown up with the incoming track's intro, and say how many bars to blend, so hot-cue weaving lands on the phrase.

### Colours
- 💡 **Approachability in Traktor.** The best single number for hard ↔ pretty (+0.56 with the test collection's colours). Write it into a Traktor field so a crate can be sorted from hard to pretty.
- 💡 **Blind recolour test.** Recolour 30–40 tracks without looking at the old colours, to measure how consistent a person's colouring is. That shows how much better the colour model could still get.
- 💡 **Ask about the tracks that teach the model most.** `colors review` lists the biggest disagreements. An "active learning" mode would instead ask about the few tracks whose colour the model is least sure of, or that sit between two colours, and retrain after each batch: fewer questions for the same gain.
- 💡 **Learn from "no" answers.** Every no, keep-always, unticked cue and ignored grid is already logged (the answers log in collection.db). A cross-validated test of weighting those tracks in training is waiting for enough answers to be meaningful.
- 💡 **MAEST per section.** Per-section EffNet features didn't help (below), but MAEST per section hasn't been tried: it might hear "pretty melody over a hard beat" where whole-track averages don't. Costs hours of compute for a collection, so it would need a cached, resumable pass.
- ✅ **Recolour suggestions.** `colors review` lists the biggest disagreements; `--apply` recolours the clearest ones (gap ≥ 2.5, confidence ≥ 0.75).
- ✅ **Weighted descriptors.** Weighting moods, instruments, vocals, energy arc and approachability more strongly brought the average error from 1.21 to 1.17 colours; it's the current default.
- ✗ **Hype as a colour feature.** Hype and colour are related (−0.31: Reds are more hype), but no gain: the colour model already hears the same styles.
- ✗ **Distortion measures** (flatness, dissonance, crest factor, 2–5 kHz energy, clipping): flat across colours, no help.
- ✗ **Asking a music LLM** (MOSS-Music-8B, local) to rate distortion/banger 1–10 on 30 Red + 30 Orange tracks: it rated nearly everything 7–9, did worse than the current model, and took ~45 s per track.

### Collection housekeeping
- 💡 **Duplicate finder.** Same track twice: different file names, sync-conflict copies, different versions.
- 💡 **Problem-file check.** Flag files tools can't handle: surround/multichannel, very low bitrate, clipping, missing tags.
- 💡 **Missing-tag filler.** Fill empty BPM/key/genre tags from the analysis.
- 💡 **Genre clean-up.** Compare existing genre tags with the analysed genres and list the biggest mismatches.
- 💡 **Stem check.** The grid check already finds a track's generated stem and measures whether it lines up. A Setup step could list tracks whose stem is missing, out of date (older than the file) or offset, so they can be remade before a gig.
- 💡 **Backup tidy-up.** Every write leaves a timestamped collection.nml backup. An optional clean-up that keeps the last N (and always the ones `--undo`, `set --remove` and `tempo native` still need) would stop them piling up.

### Cues and beat-grids
- 💡 **Test the section names.** Cue names (intro, breakdown, drop…) come from the kick and level and have never been checked against real names, since the test cues are all "Cue N". Naming the cues of 20–30 tracks by hand would give a number for how often they're right.
- 💡 **A BPM fix for drifting grids.** `grids check` flags grids whose BPM is off (drift) but only offers shifts. Fitting the BPM that keeps the kicks on the lines would let it propose a corrected BPM too, opt-in and with undo, since Traktor's BPM is otherwise never written.
- 💡 **Cues for tracks with none yet, in bulk.** Today cues are approved track by track. A "first cues only" batch for tracks that have no cues at all (intro, first drop, the loop), previewed as a list, would cover a new batch of tracks quickly.

### Discovery and listening
- 💡 **Similar tracks.** "More like this" from the sound embeddings, also across genres.
- 💡 **Gaps in the collection.** Colours, keys or BPM ranges with few tracks.
- 💡 **Artist profiles.** Each artist's typical colour and sound.
- 💡 **Navidrome playlists** by mood or colour, for listening outside DJing.
- 💡 **Real-time stretching in the page's player**, as Mixxx does: stretch from the playhead as it plays, so a jump or
  the tempo toggle never waits and nothing is stored. Today the player stretches the whole track first, in parallel
  15 s pieces (≈6 s for a 4-min track, started when it opens, then kept). Two ways: (1) the server streams Rubber Band's real-time output (`-R`, same spectrum
  as an offline stretch in a test) from each jump, with the player playing a stream instead of a file (pads, loops,
  jumps, bar readout all reworked); (2) a WebAssembly build of Rubber Band in the page (check one has the R3 engine,
  and that its GPL licence fits).

### Recording and practice
- ✅ **Analyse mix recordings**: `analyze` on a recording (or the Mixes tab's Analyse a mix…, several in a row) gives a timeline of genre, mood, hype and colour; `--tracklist` adds a per-track check from Traktor's History.
- 🧪 **Transition check.** Key steps between heard keys are counted per transition (`transitions`). Still an idea: big energy or loudness jumps at a blend.
- 💡 **Bass clash in blends.** Two kicks or basslines running together show up as a jump in 30–100 Hz level while both tracks play. The tracklist check knows when blends happen, so it could flag the ones where the low end piled up.
- 💡 **Tracklist without History.** Recordings with no matching Traktor History are analysed without the per-track check today. Matching each stretch of the mix against the library's sound embeddings (at the played tempo, via a tempo view) could find the tracks anyway, for sets played on other gear.
- ✗ **MAEST on mix segments** (a 65-minute set at ~145 BPM, 33 two-minute segments): 3 of 33 segment genres changed and agreement with the playing tracks didn't improve, for +125 s on a 72 s analysis. Both models hear that tempo as psy; the tracklist's psy cap is what fixes it. Mix timelines stay EffNet only.

### Key
- ✗ **Making the CNN key more accurate** (on the test collection, against Traktor's keys, the CNN agrees on 75%): tuning-corrected input, averaging pitch-shifted inputs, judging only the loudest or most tonal parts, trimming intros/outros, loudness weighting, and a second key model (madmom, averaged at several weights) all landed within ±1.5 points, mostly lower. When the CNN disagrees, Traktor's key is in its top 2 only 13% of the time, so rescoring its runner-ups can't help either.

### Genres
- 💡 **Beatport-era genres** (Big Room, Future House, Afro House, Organic House, Melodic Techno, Future Bass, Hard Groove) don't exist as Discogs styles. Possible via `train` with example folders, or a shared set of trained examples.

### Platforms
- ✅ **Windows.** Runs natively on Windows 10/11 x64, Traktor and Lexicon included (2026-11-02). Essentia is gone on every platform: decoding is current FFmpeg (PyAV), the signal processing is Rust, the models run on TensorFlow 2.15, all checked against Essentia track by track. The tests run on Windows and Linux on every push. Details: [WINDOWS.md](WINDOWS.md).
- 💡 **Docker on Apple Silicon without emulation.** The image is amd64-only because Essentia had no Linux arm64 build. Essentia is replaced now, so a native arm64 image is possible: TensorFlow 2.15 has arm64 Linux wheels, and the Rust module would need a Linux arm64 build too, which would skip the emulation on Apple Silicon Macs.

## Findings from testing

All on one test collection of about 470 tracks (mostly house/techno around 126 BPM, some trance and hardcore), with a personal Red = hard … Magenta = pretty colour scale.

- **Colours.** Red tracks were hard, heavy, aggressive and synthetic, and were played anywhere in a set. Magenta tracks were peaceful and pretty, with piano/strings/bells, more vocals and long intros, and were played early. The middle colours were steps in between, and neighbouring colours were close to a coin flip from the sound alone.
- **Star ratings.** The ratings had nothing to do with colour (−0.07). They went with how hype a track is: euphoric/rave sound (+0.36) plus energy (+0.30), and most of all with how recently the track was played (−0.46). Stars couldn't be predicted from the sound (0.56 error vs 0.58 for always guessing 4★), which is why there's a hype score rather than an auto-rating.
- **Tempo view.** Sped up to 145, most house and progressive came out as Psytrance/Trance, because the models link that tempo with those styles. Capping psy at what the track had at its own tempo cut Psytrance as top genre from 17 of 29 tracks to 1. A Rubber Band pass at the original tempo changed nothing, so the shifts come from the speed.
- **Traktor re-reading tags.** When a file changes after Traktor last read it, Traktor re-reads its tags on deck load and the file's genre and colour replace the collection's. `tempo load` therefore stamps changed entries with the file's own time and refuses while Traktor writes tags to files. Not yet confirmed in real Traktor.
- **Traktor clock.** Traktor saves neither Sync state nor clock-master state, so `tempo load` sets the clock tempo but Sync stays a button (or controller mapping) in Traktor.
- **Genres left out on purpose.** Electro House (the models give Discogs' 2010s "Electro House" to most modern melodic and peak-time house) and Ambient (stays part of Downtempo). See README section 7.
