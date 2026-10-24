# Track Analyzer: features and ideas

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
| Hype score in Traktor's Comment 2 | `hype --traktor` | ✅ |
| Ask a local LLM (LM Studio / Open WebUI) about the collection | `serve` (MCP) | ✅ |
| Mix suggestions: compatible key + BPM | via the LLM tools | ✅ |
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
- 💡 **Set planner.** Build a set that moves along the colour scale (e.g. pretty openers toward harder peak tracks) with keys and BPM that mix. Traktor's History can show how a DJ already orders colours: on the test collection, 55% of Magenta plays were in the first third of a set.
- 💡 **"What next?"** Given the track playing now, suggest the next one that is one step harder or prettier, in a compatible key.
- 💡 **Warm-up / peak / closing crates** made automatically from colour, approachability and energy arc.
- 💡 **Set review.** After a gig, read the History file and show the set's energy curve (colour per track over time).
- 💡 **Hype in the set planner.** Use colour (hard ↔ pretty) and hype (chill ↔ peak) as two axes.

### Colours
- 💡 **Approachability in Traktor.** The best single number for hard ↔ pretty (+0.56 with the test collection's colours). Write it into a Traktor field so a crate can be sorted from hard to pretty.
- 💡 **Blind recolour test.** Recolour 30–40 tracks without looking at the old colours, to measure how consistent a person's colouring is. That shows how much better the colour model could still get.
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

### Discovery and listening
- 💡 **Similar tracks.** "More like this" from the sound embeddings, also across genres.
- 💡 **Gaps in the collection.** Colours, keys or BPM ranges with few tracks.
- 💡 **Artist profiles.** Each artist's typical colour and sound.
- 💡 **Navidrome playlists** by mood or colour, for listening outside DJing.

### Recording and practice
- 💡 **Analyse mix recordings** (skipped by `scan` today): timeline of genre, energy and colour through the mix.
- 💡 **Transition check.** Spot key clashes or big energy jumps in a recorded mix.

### Genres
- 💡 **Beatport-era genres** (Big Room, Future House, Afro House, Organic House, Melodic Techno, Future Bass, Hard Groove) don't exist as Discogs styles. Possible via `train` with example folders, or a shared set of trained examples.

### Platforms
- 🧪 **Windows.** Blocked by Essentia, which has no Windows build. The plan replaces it step by step on every platform, each step checked against Essentia track by track: [WINDOWS.md](WINDOWS.md). Loudness and resampling are done (identical results, 5–7x faster); decoding, the model input, key and BPM are next.

## Findings from testing

All on one test collection of about 470 tracks (mostly house/techno around 126 BPM, some trance and hardcore), with a personal Red = hard … Magenta = pretty colour scale.

- **Colours.** Red tracks were hard, heavy, aggressive and synthetic, and were played anywhere in a set. Magenta tracks were peaceful and pretty, with piano/strings/bells, more vocals and long intros, and were played early. The middle colours were steps in between, and neighbouring colours were close to a coin flip from the sound alone.
- **Star ratings.** The ratings had nothing to do with colour (−0.07). They went with how hype a track is: euphoric/rave sound (+0.36) plus energy (+0.30), and most of all with how recently the track was played (−0.46). Stars couldn't be predicted from the sound (0.56 error vs 0.58 for always guessing 4★), which is why there's a hype score rather than an auto-rating.
- **Tempo view.** Sped up to 145, most house and progressive came out as Psytrance/Trance, because the models link that tempo with those styles. Capping psy at what the track had at its own tempo cut Psytrance as top genre from 17 of 29 tracks to 1. A Rubber Band pass at the original tempo changed nothing, so the shifts come from the speed.
- **Traktor re-reading tags.** When a file changes after Traktor last read it, Traktor re-reads its tags on deck load and the file's genre and colour replace the collection's. `tempo load` therefore stamps changed entries with the file's own time and refuses while Traktor writes tags to files. Not yet confirmed in real Traktor.
- **Traktor clock.** Traktor saves neither Sync state nor clock-master state, so `tempo load` sets the clock tempo but Sync stays a button (or controller mapping) in Traktor.
- **Genres left out on purpose.** Electro House (the models give Discogs' 2010s "Electro House" to most modern melodic and peak-time house) and Ambient (stays part of Downtempo). See README section 7.
