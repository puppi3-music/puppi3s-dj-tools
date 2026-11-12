# Changelog

Each release on GitHub (tags `v…`) takes its notes from its section here. The `dsp-v…` releases are the Rust
module's builds, which installs download by themselves.

## 1.4.1 (2026-11-12)

**Changed**
- Genres: the model's top style now leads, and tempo only picks its form (fast breakbeat is Drum & Bass, fast
  techno is Hard Techno; wider Tech Trance and House ranges). Updating re-genres your collection once by itself.
- Track panel: colour review marks.
- Bar measuring shows progress for every track.

**Fixed**
- m4a files: timing now allows for the AAC priming offset, so beats and cues line up.

## 1.4.0 (2026-11-12)

**New**
- Setup: an **Updates: Stable / Dev** switch on the update card. Stable (the default) installs only finished
  releases. Dev installs the newest work in progress, rebuilt after every change: new things arrive first, but may
  have bugs. Switching back to Stable never goes back a version: you keep the dev build until the next release.
  Only for installs from a release zip; a git clone already follows main.

**Changed**
- Colours guessed or predicted by the app show a dashed swatch with a faint fill, so they're easy to tell from
  your own; the ? beside them sits centred on the colour name.

## 1.3.1 (2026-11-12)

**Changed**
- Mixes: **Standalone pieces** also finds solo reworks: one track alone, played changed in half its bars or more
  (EQ, stems, re-arranged), which can work as its own piece too.
- Mixes: standalone pieces measure each track's level by its loudness across the stems you hear, and a track
  counts as near its own level within 6 dB (was the closest stem, within 12 dB). A track that is lower but steady
  (down to -16 dB) counts too, so more real blends are found without false ones. The tracks before and after
  each change are unmixed first, and a bar counts when 2 or more tracks are near their own level.
- Mixes: a piece's level strip shows the 16 bars before each change and the whole piece, so a piece widened to
  whole phrases no longer reaches past the strip.

**Fixed**
- Mixes: a mix's genre holds juke/footwork back the way it already did psytrance: house sped up to about 160 no
  longer comes out as footwork.

Analyse a mix again to get these results.

## 1.3.0 (2026-11-11)

**New**
- Mixes: **Standalone pieces** finds long blends in a recorded set that work as their own track, to post on
  SoundCloud. A change counts when both tracks stay near their own level for 32 bars or more. Tracks with a stem
  file are measured per stem, so a live stem cut or bass swap isn't read as a fade. Each piece gets a name, a
  title and credits with a link to the full set, ready to copy, and a level strip with a preview player that plays
  your recording there; pieces are marked on the mix's timeline. When no change qualifies, it says so and shows the
  closest one. On by default in **Analyse a mix…**; **Separate tracks without a stem file** (off, slow) measures
  the other tracks by stem too.
- DJ genres: **Darkpsy** (150-165 BPM) and **Hi-Tech** (165-200 BPM) are split from Psytrance by tempo, and
  **Ambient** is its own genre at any tempo. Neo Trance now counts as Melodic House & Techno. After updating, press
  **Work out the genres again** on the Setup tab (takes seconds).

**Fixed**
- Mixes: a set's tracklist check finds Traktor's history when Traktor was closed on a later day (after midnight,
  or days later). Traktor names the file by when it closed, and only the set's own day and the day before were
  looked at, so such a set showed "No tracklist check". Analyse it again to get the full result.

## 1.2.4 (2026-11-11)

**Changed**
- Getting ready for the project's new name on GitHub (puppi3s-track-analyzer): the update check asks the old name
  first, then the new one. Install this version before the rename, so updates keep coming.
- A copy made with git now follows the app when a later version moves it from `track-analyzer/` to the top of the
  folder: your data, logs and own notes go with it, and the page says to start it again from there.

## 1.2.3 (2026-11-11)

**Fixed**
- With Rekordbox chosen, the whole page now reads Rekordbox's master.db: Hype's stars, plays and colours, the
  Overview's colours, the Tracks tab's colour guesses and cue corrections, and the Setup tab's scan count. These
  were read from Traktor's collection, so they were empty without Traktor and out of date with it. Changes
  Rekordbox hasn't saved into master.db yet are picked up too.
- Beat grids: Sync writes approved grid fixes into Rekordbox too. The grid check reads tracks over 200 BPM right,
  no longer hides a big BPM drift as unclear, and leaves out stretches with no kick. An approved fix stays visible
  and can be taken back.
- In Rekordbox mode, Save sample and Open folder on the Grid check tab use Rekordbox's sampler folder.
- Copying from the other DJ app with nothing ticked no longer writes every step: Write it stays off until you tick
  one.
- A tab no longer draws over the one you clicked next when its data comes back late (Setup, Overview, Tracks,
  Colours, Tempo, Files, Mixes, the track panel).
- Mixes on Windows: a finished analysis opens its mix, and folder and file names show right.
- The deck no longer sticks on "Starting…" when you switch tracks quickly, and Space after Play toggles once.
- The track panel: a genre can be emptied, the BPM is checked, and Escape only closes an open panel.
- Times show as 1:00, not 0:60. Tooltips stay on screen in narrow windows.
- Messages and dialogs point to buttons on the page instead of terminal commands, and name the DJ app you chose.
  On Windows they name the .bat launcher.
- Library warnings go to logs/warnings.log instead of filling the terminal.

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
