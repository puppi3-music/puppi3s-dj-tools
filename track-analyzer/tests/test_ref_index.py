"""The command reference's search index (docs/commands/search-index.js) matches the pages it's built from."""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / ".claude/skills/update-docs/scripts/ref_index.py"


def test_search_index_is_current():
    spec = importlib.util.spec_from_file_location("ref_index", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.OUT.read_text() == mod.build(), "stale: run .claude/skills/update-docs/scripts/ref_index.py"
