# Core modules: analysis.py, cli.py, mcp_server.py, collection.py, traktor.py

Detail moved out of CLAUDE.md so it loads only when needed. Code is the source of truth.

- `analysis.py`: config, model loading, caches, `analyze_file`, custom genre training. `find_tool()` (PATH + Homebrew
  folders), `install_hint()` (brew on a Mac, apt on Linux), `rubberband()` (refuses < 3.0: the R3 engine).
  `color_model()` aliases `sys.modules["colors"]` because bundles trained before the package move pickled
  `colors.BlockWeights` under that name.
- `cli.py`: argparse CLI (`main`). `mcp_server.py`: `build_mcp()` (mcp SDK **2.x**:
  `from mcp.server.mcpserver import MCPServer`) + `protect_stdout()`.
  No Essentia at run time (stage 7): `essentia-tensorflow` is only in the `check` dependency group, for dsp/check.py.
- `collection.py`: `scan` (resumable, parallel spawn workers, SQLite `collection.db`), CSV export,
  `tag`/`untag` (mutagen; originals backed up in `original_tags` table), search, mix suggestions, `regenre`
  (genres again from cached scores + current dj_genres.json; reproduces scan exactly).
- `traktor.py`: `traktor` command. `find_collection()`: `--nml`, else the Setup tab's choice (`data/settings.json` via
  `analysis.app_setting`; POST /api/traktor/collection: `web.pick_file` = the system's file dialog run by the server,
  osascript / PowerShell / zenity, or a pasted path, `check_collection`), else the newest in `documents_dirs()` (on
  Windows also the real Documents folder via SHGetKnownFolderPath and OneDrive's); `newer_collection()` offers a newer
  Traktor version's. `TESTED_TRAKTOR` / `TESTED_FORMATS` (NML VERSION): above them Setup says "not verified yet"
  (`collection_version`) and `_save` logs `untested_note` on every write and preview; raise them after checking a new
  Traktor version for real. A "Traktor 4.5.0 copy" folder loses to "Traktor 4.5.0" (`_version_key`). Surgical text edits of collection.nml (never a full XML rewrite),
  timestamped backups + `--undo` (refuses if the NML changed since our write, via a `.written-sha1` file next to
  the backup; `--force` overrides), refuses while Traktor runs (`check_closed(dry_run)`, used by every NML writer;
  `running_state()`: pgrep, `tasklist` on Windows, None when it can't tell. Never assumed closed: in a container
  (`CONTAINER` = TRACK_ANALYZER_CONTAINER=1, pgrep can't see Traktor) or when the check fails, it needs a y in a terminal,
  TRACK_ANALYZER_TRAKTOR_CLOSED=1, or the page's answer (`page_answer`; else `ConfirmClosed` → web 409
  {confirm: "traktor_closed"}, the page's `postJSON` asks and resends with traktor_closed; jobs check before starting). Sets INFO GENRE (from the files' tags; `--genres-from-analysis` / the page's "only in Traktor": `multi_genre` of the DB row, a hand genre as typed, files untouched), INFO COLOR (only where
  missing unless `--overwrite-colors`), and a "Genres (Track Analyzer)" Smartlist folder, only for
  `known_genres()` = dj_genres.json names + trained genres (shop tags like "melodic techno, indie dance" would
  otherwise become ~100 Smartlists). `track_info()` reads Traktor's TEMPO BPM and MUSICAL_KEY (0-11 = C..B major,
  12-23 = C..B minor; INFO KEY is Open Key display text). Also `collection_tracks()` for `scan --traktor` and
  `recording_dirs()` (skips recordings + samples; `mac_path()` converts the .tsi's colon-style paths like
  `"Macintosh HD:Users:Shared:Traktor:Samples:"`; Windows Traktor's plain `C:\…` paths pass through).
  `location_paths()`: a drive-letter VOLUME (`"C:"`, Windows Traktor) gives `C:\…`; folder checks go through
  `collection.under()` (any case and `/` or `\` on Windows).

## Traktor NML facts (verified on real Traktor 4 files)
- Smartlist node: `<NODE TYPE="SMARTLIST" NAME="X"><SMARTLIST UUID="hex"><SEARCH_EXPRESSION VERSION="1" QUERY="$GENRE % &quot;X&quot;"></SEARCH_EXPRESSION></SMARTLIST></NODE>`.
  `%` contains, `!%` not contains, `&` and, `|` or. Brackets reportedly don't work.
- LOCATION: `DIR="/:Users/:me/:Music/:" FILE="x.mp3" VOLUME="Macintosh HD"`; other drives → /Volumes/<VOLUME>.
- Real Traktor format uses explicit close tags and `standalone="no"`; keep edits textual.
