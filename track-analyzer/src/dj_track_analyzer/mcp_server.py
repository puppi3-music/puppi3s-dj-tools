"""MCP server: the analyzer as tools for local LLMs (LM Studio, Open WebUI, Claude Desktop)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .analysis import (AUDIO_EXTS, GENRE_MODEL, HOME, analyze_file, dj_rules, models_present,
                       train)


def build_mcp():
    import functools

    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    def friendly(fn):
        """Turn expected problems (bad path, missing models...) into a clear message for the LLM."""

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (OSError, ValueError, RuntimeError) as err:
                raise ToolError(str(err)) from err

        return wrapper

    mcp = MCPServer(
        "track-analyzer",
        instructions=(
            "Local music analysis for a DJ. Use analyze_track for a single audio file path. "
            "Results include BPM, key with Camelot notation, loudness, and genre in three layers: "
            "dj_genre (DJ-style names such as Acid Trance or Melodic House & Techno, derived from a "
            "pretrained model, no training needed), discogs_styles (the raw pretrained style scores) and, "
            "only if the user trained one, my_genre (their own labels; prefer it when present). Scores are "
            "relative confidence, not probabilities. Long files are treated as DJ mixes and get a timeline. "
            "The user colour-codes tracks with Traktor's 7 colours as a scale from Red (hardest, darkest) "
            "through Orange, Yellow, Green, Blue, Violet to Magenta (prettiest, most beautiful); `color` is "
            "the colour learned from their own colouring, `mood` holds pretrained mood scores (0-1)."
        ),
    )

    @mcp.tool()
    @friendly
    def analyze_track(path: str, segment_seconds: float | None = None) -> dict:
        """Analyze one local audio file (mp3, wav, flac, aiff, m4a...).

        Returns BPM, key + Camelot, loudness (LUFS), an energy curve, the DJ subgenre
        (dj_genre, e.g. Acid Trance / Hard Trance / Techno / Melodic House & Techno), the raw
        Discogs styles with scores, and the user's own genre prediction if they trained one.
        Files over ~12 minutes are treated as DJ mixes and get a per-segment timeline.

        Args:
            path: Full path to the audio file, e.g. /Users/me/Music/track.mp3 (~ is allowed).
            segment_seconds: Optional. Also return a timeline in segments of this many seconds.
        """
        return analyze_file(path, segment_seconds)

    @mcp.tool()
    @friendly
    def analyze_folder(folder: str, limit: int = 25) -> dict:
        """Quick summary of the audio files in a folder (not recursive): BPM, Camelot key and
        subgenre per track. Slower on first run; results are cached.

        Args:
            folder: Full path to a folder of tracks.
            limit: Maximum number of files to analyze (default 25).
        """
        root = Path(os.path.expanduser(folder)).resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"{root} is not a folder")
        files = [f for f in sorted(root.iterdir()) if f.suffix.lower() in AUDIO_EXTS and not f.name.startswith("._")]
        rows = []
        for f in files[:limit]:
            try:
                r = analyze_file(str(f))
                rows.append({
                    "file": f.name,
                    "bpm": r.get("bpm"),
                    "camelot": r.get("cnn_camelot") or r.get("camelot"),
                    "key": r.get("cnn_key") or r.get("key"),
                    "lufs": r["loudness"]["integrated_lufs"],
                    "genre": (r.get("dj_genre") or [{}])[0].get("genre"),
                    "top_style": (r.get("discogs_styles") or [{}])[0].get("style"),
                })
                if r.get("my_genre"):
                    rows[-1]["my_genre"] = r["my_genre"][0]["genre"]
            except Exception as err:
                rows.append({"file": f.name, "error": str(err)})
        return {"folder": str(root), "analyzed": len(rows), "total_audio_files": len(files), "tracks": rows}

    @mcp.tool()
    @friendly
    def train_genres(folder: str) -> dict:
        """OPTIONAL. Teach the analyzer the user's own genre labels from example tracks.
        Not needed for normal use: dj_genre already works without training.

        Layout: one subfolder per genre (folder/Acid Trance/*.mp3, folder/Hard Trance/*.mp3 ...)
        or one .m3u8 playlist per genre named after the genre. Aim for 20-50 tracks per genre.
        Replaces any previous custom model. Large sets can take several minutes the first time
        (embeddings are cached afterwards); for big libraries the CLI is better:
        `uv run dj-track-analyzer train <folder>`.

        Args:
            folder: Full path to the training folder.
        """
        return train(folder, progress=lambda *a: None)

    @mcp.tool()
    @friendly
    def list_genres() -> dict:
        """Show which custom genres are trained, how many example tracks each has, and whether
        the style models are installed."""
        info: dict = {"style_models_installed": models_present(), "data_dir": str(HOME)}
        if GENRE_MODEL.exists():
            import joblib

            b = joblib.load(GENRE_MODEL)
            info.update({"my_genres": b["counts"], "trained_at": b["trained_at"], "trained_from": b["source"]})
        else:
            info["my_genres"] = None
            info["note"] = "No custom genres trained (optional). dj_genre names come from dj_genres.json."
        info["dj_genre_names"] = sorted(dj_rules())
        return info

    @mcp.tool()
    @friendly
    def search_collection(
        genre: str | None = None,
        bpm_min: float | None = None,
        bpm_max: float | None = None,
        camelot: str | None = None,
        harmonic: bool = False,
        text: str | None = None,
        include_mixes: bool = False,
        order: str = "bpm",
        limit: int = 50,
        color: str | None = None,
    ) -> dict:
        """Search the user's scanned music collection (instant; uses results of `scan`).

        Args:
            genre: Subgenre text to match, e.g. "acid trance", "hard trance", "techno".
            bpm_min: Minimum BPM.
            bpm_max: Maximum BPM.
            camelot: Camelot key such as "8A".
            harmonic: If true with camelot, also include harmonically compatible keys (±1, relative major/minor).
            text: Match artist, title, album, filename or folder.
            include_mixes: Include long DJ-mix files (default: only tracks).
            order: bpm (default), genre, key, loudness, artist, recent or color (Red → Magenta).
            limit: Maximum tracks to return (default 50).
            color: Traktor colour: Red (hardest, darkest), Orange, Yellow, Green, Blue, Violet or
                Magenta (prettiest). Uses the user's own colour, or the predicted one if they haven't set one.
        """
        from . import collection

        return collection.search(genre, bpm_min, bpm_max, camelot, harmonic, text,
                                 None if include_mixes else "track", limit, order, color)

    @mcp.tool()
    @friendly
    def mix_suggestions(track: str, bpm_tolerance_pct: float = 4.0, same_genre: bool = True, limit: int = 20) -> dict:
        """Find tracks in the scanned collection that mix well with a given track:
        close BPM and harmonically compatible Camelot key (optionally same subgenre).

        Args:
            track: Full path, or part of the title / filename / "artist - title".
            bpm_tolerance_pct: Allowed BPM difference in percent (default 4).
            same_genre: Only suggest tracks of the same subgenre (default true).
            limit: Maximum suggestions.
        """
        from . import collection

        return collection.mix_suggestions(track, bpm_tolerance_pct, same_genre, limit)

    @mcp.tool()
    @friendly
    def collection_stats() -> dict:
        """Overview of the scanned collection: track count, tracks per subgenre with average BPM,
        most common Camelot keys and a BPM histogram."""
        from . import collection

        out = collection.summary()
        if not out["tracks"]:
            out["note"] = "Nothing scanned yet. The user runs: uv run dj-track-analyzer scan <music folder>"
        return out

    return mcp


def protect_stdout() -> None:
    """Send anything native libraries print to stderr so it can't corrupt the stdio protocol."""
    saved = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = os.fdopen(saved, "w", buffering=1, encoding="utf-8")
