# Puppi3's Track Analyzer: context for Claude

Local music-analysis tool for DJs: BPM, key/Camelot, subgenre, mood, a learned Traktor colour scale and a hype
score, synced into Traktor Pro 4 or Rekordbox 7 (optionally Lexicon), plus an MCP server for local LLMs (LM Studio, Open WebUI).
macOS and Linux x86_64 (Linux has no Traktor: those steps say so; also as a Docker image), Python 3.11 via `uv`. Data dir: `data/` (`TRACK_ANALYZER_HOME`).

Docs: README.md (install + full reference), docs/GUIDE.html (task-first guide, self-contained, opened in a browser),
docs/IDEAS.md (features, ideas, tried-and-failed), docs/WINDOWS.md (what a Windows port needs). Keep them in sync when commands, options or numbers change: the
`/update-docs` skill does this (`.claude/skills/update-docs/scripts/doc_drift.py` lists CLI options the docs miss).
Public docs stay impersonal: numbers come from "a test collection of about 470 tracks", no personal track names.

A gitignored `CLAUDE.local.md` may hold one developer's own setup, collection facts and history. Personal
details go there, never in this file.

## Working style
- Ask before design decisions; make new behaviour optional behind flags.
- Anything that writes to files, Traktor or Lexicon gets `--dry-run`, a backup and an undo path.
- Real Essentia models can't be downloaded in sandboxes; tests there use stand-ins for the TF models.
  Real-model behaviour is only verified by runs on a Mac, or in the Docker test image (below), which has them.
- `tests/` (pytest, `uv sync --inexact --group test`, `uv run pytest`, ~2 s): a made-up library (FLAC tones with
  accented and Japanese names) and Traktor collection in a temp home (conftest sets HOME/USERPROFILE and
  TRACK_ANALYZER_HOME before import), in Windows Traktor's form on Windows. The analysis is a stand-in
  (`fake_analysis`); scan, `tag`, `traktor` (dry run, write, undo byte for byte), the Traktor-running refusal (a
  process named "Traktor Pro 4"), paths with Windows' rules simulated, unplugged drive letters (Windows only) are real.
  `.github/workflows/tests.yml` runs them on windows-latest and ubuntu-latest on every push to main/windows-port,
  and starts each launcher until the page answers.

## Files
Code is the package `src/dj_track_analyzer/` (relative imports; uv installs it editable). Run it as
`uv run dj-track-analyzer <command>` (entry point `cli:main`; no command = MCP over stdio) or `python -m dj_track_analyzer`.
Saved run output goes in `logs/` (gitignored). Skill scripts import `from dj_track_analyzer import …`.

| File | What it is | Detail in |
|---|---|---|
| `analysis.py` | config, model loading, caches, `analyze_file`, custom genre training | docs/notes/core.md, pipeline.md |
| `dsp/` | Rust (PyO3 + maturin) `track_analyzer_dsp`: Essentia steps bit for bit, faster | docs/notes/dsp.md |
| `cli.py`, `mcp_server.py` | argparse CLI (`main`); MCP server (`build_mcp()`, mcp SDK 2.x) | docs/notes/core.md |
| `collection.py` | `scan` (resumable, parallel workers, SQLite `collection.db`), CSV export | docs/notes/core.md |
| `traktor.py` | `traktor` command, NML read/write, `find_collection()` | docs/notes/core.md |
| `colors.py`, `hype.py`, `setplan.py` | learned colour scale; hype score; `set` arcs | docs/notes/colours-hype-set.md |
| `tempo.py`, `lexicon.py` | catalogue as heard at one tempo; Lexicon Local API | docs/notes/tempo-lexicon-mixes.md |
| `clips.py`, `mixreport.py`, `tracklist.py` | highlight clips; Mixes tab report; mix tracklists | docs/notes/tempo-lexicon-mixes.md |
| `web.py`, `web/` | `web` command, Starlette on 127.0.0.1:8766; page = `index.html` + `style.css` + `js/*.js` | docs/notes/web.md |
| `jobs.py`, `update.py` | Setup tab status/jobs; `update` + update card | docs/notes/web.md |
| `library.py`, `rekordbox.py` | DJ-app switch (`library.app()`, backend per app); Rekordbox master.db + ANLZ read/write, backups, undo | docs/notes/rekordbox.md |
| `sync.py`, `trackfiles.py` | `sync` (all writers, one undo; `--copy` from the other app); moved/missing tracks for the tracks step | docs/notes/cues-grids.md, rekordbox.md |
| `cues.py`, `gridfix.py` | `cues train/scan/show`; `grids check/show/apply` | docs/notes/cues-grids.md |
| `keycnn.py`, `mygenres.py`, `dj_genres.json` | MusicalKeyCNN key; your own genres; Discogs → DJ genre rules | docs/notes/keys-genres.md |

## Before you change something
- Read the matching `docs/notes/*.md` first (table above), and **docs/notes/bugs.md** before touching caches,
  cache keys, Traktor/NML writes, tags, model inputs or the BPM/tempo logic: it lists bugs not to reintroduce.
- Read **docs/notes/tried.md** before proposing a speed or accuracy idea: it lists what was tried and didn't help.
- Traktor NML facts (verified on real files) are in docs/notes/core.md.
- New facts go in the matching notes file, not here; keep this file short (it loads in every session).
- Big generated files (`dsp/src/bpm_tables.rs`, `fftw_codelets.rs`, `mid_qual_coeffs.rs`, model files) are
  excluded from search by `.ignore`; never read them whole.
