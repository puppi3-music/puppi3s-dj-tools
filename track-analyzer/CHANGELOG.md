# Changelog

Each release on GitHub (tags `v…`) takes its notes from its section here. The `dsp-v…` releases are the Rust
module's builds, which installs download by themselves.

## Unreleased

**Added**
- `clips describe` and the mix's **Your editor's clips** card: clips someone else cut from the finished video are
  found in the recording by their sound. Each gets its video time, the track playing, LUFS, rise and energy, plus its
  own genre, moods and BPM; **Copy for posts** copies them as text. A clip that isn't found can be placed by hand.

## 1.2.2 (2026-11-04)

**Fixed**
- Beat-grid check on Windows: tracks longer than about 49 seconds no longer fail (a number overflowed with older
  numpy).

## 1.2.1 (2026-11-03)

**Fixed**
- With Rekordbox chosen, editing a track's colour or genre in its panel now writes Rekordbox (one backup, one undo),
  not Traktor. The key stays in the analyzer.
- The Setup tab with Rekordbox: colour and cue counts, the "new to learn" numbers, and changes Rekordbox hasn't
  saved into master.db yet are picked up; a damaged database no longer breaks the tab. Choosing another database
  reads it again.
- Labels across the page (track panel, deck, cue approval, beat grids, mixes) name the DJ app you chose.
- Library overview: BPM chart in whole BPMs with no gaps in its axis.
- Beat-grid and cue buttons no longer stick on "Writing…"; a slow suggestion reply no longer lands on another track.

## 1.2.0 (2026-11-03)

**New**
- **Rekordbox support.** On the Setup tab, pick your DJ app: Traktor or Rekordbox (its library is found for you).
  With Rekordbox, everything that worked with Traktor works there too: scanning and the BPM/key comparison, writing
  genres, colours, Hype (in Comments) and genre smart playlists, cue suggestions, beat-grid fixes, set and crate
  playlists, the tempo view, and learning from your History and playlists. Every write is backed up first, has an
  undo, and waits until Rekordbox is closed. Not included: exporting to a USB stick (OneLibrary).
- **Sync everything** on the Setup tab: one preview of every change for your DJ app (moved files found again,
  analysed tracks added, genres and colours, Hype, cues, beat grids, crates, loops), one button to write it and one
  undo for all of it. Using both apps? **Copy Rekordbox into Traktor** (or the other way) brings over what the other
  one has.
- Your loops and samples go into your DJ app too: a "Track Analyzer loops" playlist in Traktor, or Rekordbox's
  Sampler folder.
- **Learn your genres from the genre tags of a folder's files** (Setup tab: Learn from › **Genre tags in a folder**;
  in a terminal `train --by-tag`). Subfolders are searched too.

**Better**
- The Setup tab opens faster with Rekordbox (beat grids are remembered until the library changes).
- Cue approvals survive a sync, and approved cues you deleted in the DJ app are put back.
- A list of every third-party project, model and package the app uses (`THIRD-PARTY.md`).

## 1.1.0 (2026-11-02)

**New**
- **Update from the Setup tab.** A card at the top of the Setup tab says when a new release is out, with its notes.
  **Update now…** previews what changes, installs it and starts the page again with the new version. Your `data`
  folder isn't touched; the app's own files are backed up first, and **Go back to …** returns to the version you had.
  Steps a release needs afterwards (genres again, a rescan, new models) show up there as buttons. The page asks GitHub
  at most every 6 hours; untick **check when the page opens** to turn that off. In a terminal: `update` (`--check`,
  `--dry-run`, `--undo`). A clone is updated with `git pull`.
- The launchers start the page again after an update, installing its new packages first.
- **Learn your own genres on the Setup tab**, for genres the built-in ones don't have (like Hyperpop) or your own
  split. Learn from a folder with one subfolder or playlist per genre, playlists you tick in Traktor (each a genre
  named after it) or genres you tick from Traktor's Genre field. It now also learns what the rest of your scanned
  music sounds like, so a track only gets one of your genres when it clearly sounds like it; every other track keeps
  its built-in genre. It shows how well it did on your own tracks.
- **Genres used: Starter / Both / Mine only**: trained genres are no longer used everywhere the moment they're
  trained. Starter = the built-in genres; Both = yours where one clearly fits, built-in elsewhere; Mine only = the
  closest of yours on every track. Picking Both or Mine only first lists the tracks that would change; then your
  genres go on the page, in mix suggestions, sets and the LLM, and in your files and Traktor the next time you write
  genres. Both and Mine only wait for 30 example tracks with at least 10 per genre.
- A note in the README and guide: the built-in genres don't know hyperpop, and how to teach it.

**Trained your own genres before?** They're kept but not used until you learn them again (so they get "everything
else") and set **Genres used** to Both or Mine only. Genres you set by hand aren't affected.

**Updating from 1.0.1:** use **Update now…** on the Setup tab once this release is out. On Windows, close the
launcher's window afterwards and double-click `Start Puppi3's Track Analyzer.bat` again (the 1.0.1 launcher can't
restart by itself); after that, updates restart on their own.

## 1.0.1 (2026-11-02)

**Fixes**
- **Windows: Traktor's collection not found** when Documents isn't `C:\Users\<you>\Documents`, for example when
  OneDrive backs it up or it was moved to another drive. The analyzer now looks in Windows' real Documents folder and
  OneDrive's, for the collection, Traktor's History and its recordings.
- **Choose the collection yourself**: if it still isn't found, or you want another one, the Setup tab's Traktor step
  has **Browse…** (your computer's own file dialog) and a field to paste its path. **Find automatically** goes back.
- After a Traktor update, the Setup tab offers the new version's collection. A copied folder such as
  "Traktor 4.5.0 copy" is no longer picked over the real one.
- A Traktor version newer than the analyzer has been tested with (4.5.0) is marked "not verified yet" on the Setup
  tab and in every write's preview. It should still work; every write is backed up and can be undone.

## 1.0.0 (2026-11-02)

The first release. Puppi3's Track Analyzer analyses a DJ library on your own computer and puts the results into
your files, Traktor and Lexicon.

**What it does**
- BPM, key (Camelot), loudness, genres, moods, instruments and hype for every track, cached and resumable.
- Learns your Traktor colour scale and your cue points, and suggests them for new tracks.
- Writes genres into file tags, Traktor (with a genre Smartlist folder) and Lexicon; every write is previewed, backed
  up and undoable.
- A tempo view: your library as heard stretched to the tempo you play at.
- Mix analysis (tracklists, BPM and key over time), set planning, crates, beat-grid checks, clips.
- A local web page with a Setup tab that takes a new install from nothing to everything in Traktor, and an MCP
  server so a local LLM (LM Studio, Open WebUI) can search and plan with your library.

**New in this release**
- **Windows**: runs natively on Windows 10/11 x64, Traktor and Lexicon included, with the same results as on a Mac.
  Double-click `Start Puppi3's Track Analyzer.bat`; the Setup tab installs ffmpeg (winget) and Rubber Band.
- **No Essentia**: decoding is current FFmpeg (PyAV), the signal processing is a Rust module, and the models run on
  TensorFlow 2.15, each checked against Essentia track by track. Scans are about 28% faster.
- **Genres only in Traktor**: `traktor --genres-from-analysis` (on the page: "only in Traktor") leaves your files'
  tags as they are.
- **Edit a track by hand**: BPM, key, colour, genre and hype in the track's panel or with `edit`.
- **Undo anyway**: when Traktor saved its collection after a write, the undo dialog can still put the backup back,
  after a confirm.
- Writes to Traktor refuse whenever the analyzer can't tell whether Traktor is closed, instead of assuming it is.
- Tests on Windows and Linux on every change.

**Install**: download `puppi3s-track-analyzer-1.0.0.zip` below, unzip it, and double-click
`Start Puppi3's Track Analyzer.command` (Mac) or `Start Puppi3's Track Analyzer.bat` (Windows). See the README for
Linux and Docker.
