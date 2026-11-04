"""Library warnings (numpy, librosa, torch, ...) go to logs/warnings.log instead of the console window.

Each distinct warning is written once per process. Setting PYTHONWARNINGS (or python -W) keeps Python's own
behaviour, so a developer can still see them in the terminal: PYTHONWARNINGS=default.
"""
from __future__ import annotations

import os
import sys
import time
import warnings
from pathlib import Path

LOG = Path(__file__).resolve().parents[2] / "logs" / "warnings.log"


def route_warnings(path: Path | None = None) -> bool:
    """Send warnings to `path` (default logs/warnings.log). False when the user chose their own warning settings."""
    if sys.warnoptions or os.environ.get("PYTHONWARNINGS"):
        return False
    target = path or LOG
    warnings.simplefilter("once")

    def show(message, category, filename, lineno, file=None, line=None):
        text = warnings.formatwarning(message, category, filename, lineno, line)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [pid {os.getpid()}] {text}")
        except OSError:
            pass  # a read-only or missing logs folder must never break a run

    warnings.showwarning = show
    return True
