# Changelog

Each release on GitHub (tags `v…`) takes its notes from its section here. The `dsp-v…` releases are the Rust
module's builds, which installs download by themselves.

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
