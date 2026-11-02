# DJ tools

Small, self-contained tools for DJs. Each tool lives in its own folder with its own README, dependencies and lockfile, so you can use one without installing the others.

| Tool | What it does |
|---|---|
| [track-analyzer](track-analyzer/) | Local music analysis for a DJ's Mac or Windows PC: BPM, key, subgenre, mood, a learned Traktor colour scale and a hype score, synced into Traktor Pro 4 (optionally Lexicon), plus an MCP server for local LLMs. [Download](https://github.com/puppi3-music/puppi3s-dj-tools/releases/latest) · [Guide](https://puppi3-music.github.io/puppi3s-dj-tools/track-analyzer/) · [Quick start](#track-analyzer-quick-start) |

Docs: https://puppi3-music.github.io/puppi3s-dj-tools/

Each tool is run from its own folder, e.g. `cd track-analyzer && uv sync`.

## Track analyzer: quick start

Download the zip from the [latest release](https://github.com/puppi3-music/puppi3s-dj-tools/releases/latest) (under **Assets**) and unzip it, or clone this repo. Then, in the `track-analyzer` folder:

- **Mac:** double-click **`Start Puppi3's Track Analyzer.command`**. If macOS says it's from an unidentified developer, right-click it and choose **Open**.
- **Windows:** double-click **`Start Puppi3's Track Analyzer.bat`**. If Windows warns that it's from the internet, choose **More info → Run anyway**.
- **Linux:** run `./"Start Puppi3's Track Analyzer.command"` from a terminal in that folder.

The first time, it installs what it needs (a few minutes), then opens the analyzer's web page, where the Setup tab walks you through the rest; the [guide](https://puppi3-music.github.io/puppi3s-dj-tools/track-analyzer/) shows each task step by step.

## Licence

GNU Affero General Public License v3 (see [LICENSE](LICENSE)).
