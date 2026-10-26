# DJ tools

Small, self-contained tools for DJs. Each tool lives in its own folder with its own README, dependencies and lockfile, so you can use one without installing the others.

| Tool | What it does |
|---|---|
| [track-analyzer](track-analyzer/) | Local music analysis for a DJ's Mac: BPM, key, subgenre, mood, a learned Traktor colour scale and a hype score, synced into Traktor Pro 4 (optionally Lexicon), plus an MCP server for local LLMs. [Guide](https://puppi3-music.github.io/puppi3s-dj-tools/track-analyzer/) |

Docs: https://puppi3-music.github.io/puppi3s-dj-tools/

**Quick start (Mac):** download or clone this repo, then double-click **`Start Puppi3's Track Analyzer.command`** in the `track-analyzer` folder. The first time, it installs what it needs (a few minutes), then opens the analyzer's web page, where the Setup tab walks you through the rest; the [guide](https://puppi3-music.github.io/puppi3s-dj-tools/track-analyzer/) shows each task step by step. If macOS says it's from an unidentified developer, right-click it and choose **Open**. On Linux, run `./"Start Puppi3's Track Analyzer.command"` from a terminal in that folder.

Each tool is run from its own folder, e.g. `cd track-analyzer && uv sync`.

## Licence

GNU Affero General Public License v3 (see [LICENSE](LICENSE)).
