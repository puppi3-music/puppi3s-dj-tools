"""Check the command reference (docs/commands/*.html) against the real CLI parser: every command and action has a
section (id="<command>" or id="<command>-<action>") on its area's page, and every option appears there in <code>.
Areas whose page isn't written yet are listed as such. Run from track-analyzer/:
uv run python .claude/skills/update-docs/scripts/ref_coverage.py"""
import argparse, html, re
from pathlib import Path

# command → area page. Keep in step with docs/commands/index.html's area cards.
AREAS = {
    "colours.html": ["colors"],
    "library.html": ["setup", "scan", "analyze", "stats", "export", "keys", "styles"],
    "genres.html": ["tag", "untag", "regenre", "train", "traktor", "hold", "edit"],
    "hype-sets.html": ["hype", "set", "crates"],
    "cues-grids-sync.html": ["cues", "grids", "sync"],
    "tempo.html": ["tempo", "install"],
    "mixes.html": ["mixes", "clips", "clips-describe"],
    "app.html": ["web", "serve", "update"],
}
# options documented on another area's page as well (analyze's mix options live with the mixes)
ALSO = {"analyze": ["mixes.html"]}

cap = {}
class Stop(Exception): pass
def grab(self, *a, **k): cap["ap"] = self; raise Stop
orig = argparse.ArgumentParser.parse_args; argparse.ArgumentParser.parse_args = grab
from dj_track_analyzer import cli
try: cli.main()
except Stop: pass
argparse.ArgumentParser.parse_args = orig
cmds = cap["ap"]._subparsers._group_actions[0].choices

docs = Path("docs/commands")
mapped = {c for v in AREAS.values() for c in v}
for c in sorted(set(cmds) - mapped):
    print(f"UNMAPPED command {c}: add it to AREAS here and to an area card in index.html")
missing = 0
for page, names in AREAS.items():
    f = docs / page
    if not f.exists():
        print(f"not written yet: {page} ({', '.join(names)})"); continue
    text = f.read_text()
    ids = set(re.findall(r'id="([^"]+)"', text))
    codes = " ".join(html.unescape(re.sub("<[^>]+>", "", c)) for c in re.findall(r"<code>(.*?)</code>", text, re.S))
    for name in names:
        if name not in cmds: continue  # e.g. a command on another branch
        p = cmds[name]
        extra = " ".join((docs / o).read_text() for o in ALSO.get(name, []) if (docs / o).exists())
        acts = [a for a in p._actions if not a.option_strings and a.choices and a.dest == "action"]
        want = [f"{name}-{ch}" for a in acts for ch in a.choices] or [name]
        for i in want:
            if i not in ids: missing += 1; print(f"{page}: no section id=\"{i}\"")
        for a in p._actions:
            if isinstance(a, argparse._HelpAction) or not a.option_strings: continue
            o = max(a.option_strings, key=len)
            if not re.search(re.escape(o) + r"(?![\w-])", codes + extra):
                missing += 1; print(f"{page}: {name} {o} not in a <code>")
print("missing", missing)
