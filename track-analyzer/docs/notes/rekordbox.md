# Rekordbox: what's in master.db and the ANLZ files (stage 0 spike, 2026-11-02)

Read-only findings, so a later `rekordbox.py` doesn't have to guess. Checked with pyrekordbox 0.5.0 on a copy of a
Rekordbox 7.2.18 library (about 470 tracks, synced from Traktor by Lexicon, so the same files exist in both apps),
in the Docker test image. Nothing has been written to Rekordbox yet. Plan: stages 1–4 in the `rekordbox` branch.

## Opening the database
- `pyrekordbox.MasterDatabase(path=…/master.db, db_dir=<folder holding share/>)` unlocks the database itself (no
  key from the user). It works on Linux too: it warns "OS linux not supported" but reads a copied database fine.
- SQLite needs a writable folder even for reads (journal files): a read-only mount fails with "unable to open
  database file". Read from a copy, or from the real folder.
- Rows with `rb_local_deleted = 1` are deleted tracks (here, Rekordbox's own sampler one-shots): skip them.
- `masterPlaylists6.xml` sits next to master.db; pyrekordbox warns if it's missing.
- History tables were empty in this library (it was never played in Rekordbox), so tracklists are still unchecked.
- Processes: only `rekordbox` means the app is open. `rekordboxAgent` can run on its own and must not count.

## Track fields (`djmdContent`)
- `FolderPath`: the full POSIX path of the file. 447 of 471 rows matched Traktor's LOCATION paths after NFC
  normalisation.
- `BPM`: an integer, BPM × 100 (12800 = 128.00). It matched Traktor's TEMPO on every track but one (161.38 vs 121).
- `KeyID` → `djmdKey.ScaleName`: **free text**, in whatever notation was imported. Here it's Open Key in a
  Rekordbox-ish spelling: `1M` = Am, `1D` = C (`M` = minor = Open Key "m", `D` = major = "d"), all 24 seen.
  Rekordbox's own analysis writes names like `Am` / `C#`. A reader has to parse all three notations (classic,
  Open Key, Camelot) and must not trust the ID numbers.
- `ColorID` → `djmdColor`: 8 fixed colours, IDs 1–8 = Pink, Red, Orange, Yellow, Green, Aqua, Blue, Purple; 0 = none.
- `GenreID` → `djmdGenre.Name`, `Commnt` = the comment text, `Rating` 0–5 (0 = none).
- `AnalysisDataPath`: the ANLZ path, relative to `share/` (`/PIONEER/USBANLZ/xxx/<uuid>/ANLZ0000.DAT`).

## Playlists (`djmdPlaylist`)
- `Attribute`: 0 = playlist, 1 = folder, 4 = smart playlist. `ParentID` = "root" or a folder's ID.
- A smart playlist's rules are XML in `SmartList`:
  `<NODE Id="…" AutomaticUpdate="0" LogicalOperator="1"><CONDITION Operator="1" PropertyName="rating" ValueLeft="5" …/></NODE>`.
  The genre condition still needs checking (which `PropertyName`/`Operator` means "genre contains").

## Cues (`djmdCue`)
- `InMsec` / `OutMsec` in milliseconds; `OutMsec` = -1 for a plain cue, > 0 for a loop.
- `Kind`: 0 = memory cue; hot cues A–H are **1, 2, 3, 5, 6, 7, 8, 9** (4 is skipped). Checked against Traktor's
  HOTCUE 0–7 on the same tracks. Lexicon wrote every hot cue twice (hot cue + memory cue at the same spot), so
  counts from a Lexicon-synced library are doubled.
- `Comment` = the cue name; `Color` / `ColorTableIndex` = the cue colour.
- **The cues live in the database.** The tracks' ANLZ PCOB/PCO2 sections were empty while `djmdCue` had their cues.
  So Rekordbox 7 shows cues from the DB, and writing cues means `djmdCue` rows. The ANLZ copies only matter for USB
  exports, and Rekordbox writes those itself.
- MP3 timing: for the same cue, Traktor's position minus Rekordbox's has a **median of +26.5 ms** (5,740 cues,
  range -10 to +95 ms). That is about one MP3 frame (1,152 samples ≈ 26 ms at 44.1 kHz), the usual decoder-delay
  difference. Compare with `traktor.audio_offset` per file before writing cues. This library had only MP3s:
  FLAC/WAV/AIFF are unchecked.

## Beat grid (ANLZ `.DAT` → PQTZ)
- One entry per beat: (bar position 1–4, BPM, time in seconds). Examples: 628 beats at 126.00, first beat at
  0.191 s; another track starts on beat 4 at 0.066 s. A constant-tempo track repeats one BPM. A grid can hold
  several tempos, which Traktor's single anchor + BPM can't.
- The grid exists **only** in ANLZ (no DB table), so fixing grids means rebuilding PQTZ. That's not tried yet:
  stays optional and experimental, off by default (see the plan).
- Each live track had all three files: `.DAT` (PPTH, PVBR, PQTZ, PWAV, PWV2, PCOB×2), `.EXT` (PWV3, PCOB×2,
  PCO2×2, PWV5, PWV4), `.2EX` (PWV7, PWV6, PWVC). pyrekordbox 0.5.0 parsed all of them.

## Still open
- Genre condition XML for smart playlists; whether a pyrekordbox commit with our changes opens cleanly in
  Rekordbox 7 (first write test, on a copy, done by the user).
- Adding `djmdCue` rows (pyrekordbox doesn't list cues among the tables it can add to): IDs, UUIDs, `usn`.
- History/recordings for tracklists; non-MP3 cue offsets; Windows paths (`%APPDATA%\Pioneer\rekordbox`).
