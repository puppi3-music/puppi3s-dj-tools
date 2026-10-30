"""Compare the CLI and MCP surface in the code with what README.md, docs/GUIDE.html and docs/IDEAS.md mention.

Run from the project root:  uv run python .claude/skills/update-docs/scripts/doc_drift.py
Read-only. Prints the real CLI (commands, options, defaults), then what each doc is missing,
broken in-page anchors/links in docs/GUIDE.html, and docs mentioning options that no longer exist.
"""
import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path.cwd()
DOCS = ["README.md", "docs/GUIDE.html", "docs/IDEAS.md"]
NOT_OURS = {"--directory", "--version", "--project", "--python", "--extra", "--inexact",  # uv / tool flags the docs may show
            "--rm"}  # docker compose run --rm


class _Captured(Exception):
    pass


def cli_parser() -> argparse.ArgumentParser:
    """Build cli.main()'s parser without running anything (parse_args is intercepted)."""
    from dj_track_analyzer import cli

    holder = {}
    original = argparse.ArgumentParser.parse_args

    def grab(self, *a, **k):
        holder["p"] = self
        raise _Captured

    argparse.ArgumentParser.parse_args = grab
    try:
        cli.main()
    except _Captured:
        pass
    finally:
        argparse.ArgumentParser.parse_args = original
    return holder["p"]


def surface(parser):
    """{command: [(option strings, choices, default, help)]}"""
    out = {}
    for act in parser._actions:
        if isinstance(act, argparse._SubParsersAction):
            for name, sp in act.choices.items():
                opts = []
                for a in sp._actions:
                    if isinstance(a, argparse._HelpAction):
                        continue
                    opts.append((a.option_strings or [a.dest], a.choices, a.default, a.help or ""))
                out[name] = opts
    return out


def mcp_tools() -> list[str]:
    tree = ast.parse((ROOT / "src/dj_track_analyzer/mcp_server.py").read_text())
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "tool":
                    names.append(node.name)
    return names


def main() -> None:
    cli = surface(cli_parser())
    tools = mcp_tools()
    docs = {d: (ROOT / d).read_text() if (ROOT / d).exists() else "" for d in DOCS}

    print("== CLI in the code ==")
    for cmd, opts in cli.items():
        print(f"\n{cmd}")
        for strings, choices, default, help_ in opts:
            extra = f" choices={list(choices)}" if choices else ""
            if default not in (None, False, [], argparse.SUPPRESS):
                extra += f" default={default}"
            print(f"  {', '.join(strings):28}{extra}  {help_}")
    print("\n== MCP tools ==\n  " + ", ".join(tools))

    print("\n== Missing from docs (README = reference, so it should have everything) ==")
    for d in ("README.md", "docs/GUIDE.html"):
        text = docs[d]
        miss = []
        for cmd, opts in cli.items():
            if not re.search(rf"\b{re.escape(cmd)}\b", text):
                miss.append(cmd)
            for strings, choices, *_ in opts:
                for s in strings:
                    if s.startswith("--") and s not in text:
                        miss.append(f"{cmd} {s}")
                for c in choices or []:
                    if not re.search(rf"{re.escape(cmd)} {re.escape(str(c))}\b", text):
                        miss.append(f"{cmd} {c}")
        if d == "README.md":
            miss += [f"tool {t}" for t in tools if t not in text]
        print(f"\n{d}: {len(miss)}")
        for m in miss:
            print(f"  - {m}")

    print("\n== Options mentioned in docs that the CLI doesn't have ==")
    known = {s for opts in cli.values() for o in opts for s in o[0]}
    for d, text in docs.items():
        text = re.sub(r"<style.*?</style>|var\(--[\w-]+\)", "", text, flags=re.S)
        stale = sorted({m for m in re.findall(r"(?<![\w-])--[a-z][a-z0-9-]+", text)} - known - NOT_OURS)
        print(f"  {d}: {', '.join(stale) or 'none'}")

    print("\n== docs/GUIDE.html links ==")
    g = docs["docs/GUIDE.html"]
    ids = set(re.findall(r'\bid="([^"]+)"', g))
    bad = [h for h in re.findall(r'href="#([^"]+)"', g) if h not in ids]
    files = [f for f in re.findall(r'href="([^"#:]+)"', g) if not (ROOT / "docs" / f).exists()]
    print(f"  broken anchors: {bad or 'none'}\n  missing files: {files or 'none'}")
    # README.md#anchor links (GitHub slugs: lowercase, punctuation dropped, spaces → -)
    readme = (ROOT / "README.md").read_text()
    slugs = {re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-")
             for h in re.findall(r"^#{1,6} (.+)$", readme, flags=re.M)}
    refs = re.findall(r"\]\(#([^)]+)\)", readme) + re.findall(r'href="\.\./README\.md#([^"]+)"', g)
    print(f"  broken README anchors: {sorted(set(r for r in refs if r not in slugs)) or 'none'}")
    for d in ("README.md", "docs/IDEAS.md", "CLAUDE.md"):
        text = (ROOT / d).read_text() if (ROOT / d).exists() else ""
        files = [f for f in re.findall(r"\]\(([^)#:]+)\)", text) if not ((ROOT / d).parent / f).exists()]
        if files:
            print(f"  {d} missing files: {files}")


if __name__ == "__main__":
    main()
