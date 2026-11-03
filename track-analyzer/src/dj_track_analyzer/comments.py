"""The "Hype NN" part of a track's comment, the same in every DJ app (Traktor, Rekordbox, Lexicon)."""
from __future__ import annotations

import re

HYPE_RE = re.compile(r"\s*(?:·\s*)?Hype \d{2}\s*$")


def with_hype(comment: str | None, hype: int | None) -> str:
    """The comment with its 'Hype NN' part replaced (or removed when hype is None); other text is kept."""
    rest = HYPE_RE.sub("", comment or "").strip()
    if hype is None:
        return rest
    return f"{rest} · Hype {hype:02d}" if rest else f"Hype {hype:02d}"
