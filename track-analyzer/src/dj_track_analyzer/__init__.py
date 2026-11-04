"""Puppi3's Track Analyzer: local music analysis for DJs (BPM, key, genre, mood, Traktor colour) + MCP server."""
from ._warnings import route_warnings

route_warnings()  # here, not in cli.main, so spawn workers and job subprocesses get it too
