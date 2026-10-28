---
name: update-docs
description: Bring the track analyzer's documentation (README.md, docs/GUIDE.html, docs/IDEAS.md, CLAUDE.md, CLAUDE.local.md) back in line with the code, and make it easy to follow and good-looking. Use when asked to update, check, tidy or polish the docs, after commands/options/numbers change, or before handing the project to someone.
---

# Update the documentation

Goal: every doc says what the code does **today**, a DJ (not a programmer) can follow it step by step,
and it looks good. The code is the source of truth; the docs never are. When docs and code disagree
on *behaviour* (not just wording), that may be a bug: ask the user which is right instead of guessing.

## The four docs and their jobs (keep them distinct)

| Doc | Reader | Job |
|---|---|---|
| `README.md` | someone installing or looking something up | Install, every command and option, how the analysis works, LLM setup, settings. The full reference. |
| `docs/GUIDE.html` | the user on a normal day, in a browser | Task-first: "I bought new tracks" → what to run, what you'll see, how to read it, how to undo. Self-contained (no external files besides Google Fonts). |
| `docs/IDEAS.md` | the user planning | What exists (short), ideas by theme with status. Not a manual. |
| `CLAUDE.md` | Claude, for any contributor (public) | Architecture, design rules, verified technical facts, bugs not to reintroduce. Dense is fine; don't prettify it into prose. No personal details. |
| `CLAUDE.local.md` | Claude, for this one user (gitignored) | Their setup, collection numbers, track names, decisions with dates and *who decided*, test history, current status. |

Don't copy long passages between docs; link instead (GUIDE ↔ README sections, README → GUIDE for daily use).

## 1. Find the drift (mechanical)

```
uv run python .claude/skills/update-docs/scripts/doc_drift.py 2>/dev/null
```

It prints the real CLI (every command, option, choice, default, help text) and the MCP tools, then:
commands/options/choices each doc doesn't mention, options the docs mention that no longer exist,
and broken anchors/file links. README should reach 0 missing; GUIDE may leave out rare options on
purpose (it has an "Useful options" table, not a full list), but every command and action must appear.

## 2. Find the drift (by reading)

The script can't see these; check them against the code:
- **Defaults and numbers**: default BPM (145), workers (half the cores, max 5), thresholds (`--min-gap 2.5`,
  review confidence 0.75, `--multi-ratio 0.7`), genre count (count rules in `dj_genres.json`), model sizes,
  timings, the colour model's current scores. Numbers from runs come from CLAUDE.md; numbers from code come
  from the code. Never invent a number; if one is unknown, leave it out.
- **Safety claims**: which commands refuse while Traktor runs (callers of `traktor.traktor_running()`), which
  refuse while a tempo view is loaded (`refuse_if_tempo_view`), what `--undo`/`native`/`untag` restore,
  backup names and locations, `--force` meaning. These must be exactly right; a wrong one costs the user data.
- **What gets written where**: INFO GENRE / COLOR / COMMENT (hype; Comment 2 = RATING no longer written),
  file tags, Smartlist folder names, Lexicon fields, `data/` files.
- **Output examples**: run a cheap read-only command (`stats`, `tempo status`, `hype --limit 5`,
  `colors review`) and make sure the docs' sample output and field names match. Don't run `scan`,
  `colors train`, anything with `--traktor`/`load`/`native`/`apply` without `--dry-run`, or anything that writes.
- **Status lines**: "not yet tested", "planned", "next steps" that CLAUDE.md says are now done (or vice versa).
- **Workflow order**: e.g. after editing `dj_genres.json` → `regenre`; after a view fix → `tempo refresh`, reload.

Make a short list of findings before editing. If a finding needs a decision (behaviour vs docs, or
dropping a section), ask the user first; they prefer to decide.

## 3. Rewrite for use and understanding

- **Task first.** Lead with what the reader wants to do, then the command, then what they'll see, then
  how to undo. Explanations of *why* come after, briefly.
- **Every command is copy-pasteable** from the project folder: `uv run dj-track-analyzer …`, real flags, no placeholders
  except obvious ones (`<track>`). Show `--dry-run` first for anything that writes.
- **Plain language**, short sentences, the user's words (banger, hype, Smartlist, Comment, "at 145").
  Define a term the first time (Camelot, LUFS, Smartlist, tempo view). No marketing tone.
- **Warnings where they matter**: "Quit Traktor first", "changes your files", "undo with …", placed right
  before the command, not in a footnote.
- **Numbers carry context**: "1.17 colours off on average (guessing the middle: 1.63)", not a bare 1.17.
- Remove stale history from README/GUIDE (that belongs in CLAUDE.md). Keep headings stable so links work;
  if you rename an anchor, fix every link to it.

## 4. Make it beautiful

**README.md** (renders on GitHub and in VS Code preview):
- One-paragraph intro + a "what you get" list, then numbered sections. A table of contents if it grows past ~6 sections.
- Tables for options (Option · What it does · Default), fenced `bash` blocks for commands, `> **Note**`
  callouts sparingly. Consistent heading levels; no walls of text over ~5 lines.

**docs/GUIDE.html** (opened locally in a browser): load the `artifact-design` skill first and follow its
craft guidance (type scale, spacing, colour tokens, dark mode, phone width). Keep what already works:
- the colour-scale strip (Traktor Red → Magenta), the sticky nav, copy buttons on commands, light/dark via
  `:root` tokens + `prefers-color-scheme` + a `data-theme` toggle.
- Fully self-contained: inline CSS/JS only, works from `file://`, no CDN dependencies.
- Readable measure (~70 ch), clear hierarchy, cards for "do / don't", tables for options and outputs.
- After editing, open it (`open docs/GUIDE.html`) or screenshot it if a browser tool is available, in both light
  and dark and at phone width. Check the console for JS errors.

**docs/IDEAS.md**: status markers kept consistent (e.g. ✅ done · 🔜 next · 💡 idea · ✗ tried, didn't help) with one
line each; "tried, didn't help" items keep their evidence so nobody retries them.

**CLAUDE.md / CLAUDE.local.md**: only correct facts; keep the terse style. Status, dated decisions and anything about the user's own
collection go in CLAUDE.local.md; CLAUDE.md gets only what any contributor needs.

**The repo is public.** README, GUIDE, IDEAS and CLAUDE.md must not contain the user's track names, playlist names, device or
account names, personal paths or dated personal history. Numbers from the user's runs are cited as "a test collection of about 470
tracks". Before finishing, grep the public docs for names that appear in CLAUDE.local.md.

## 5. Finish

1. Re-run `doc_drift.py`: README 0 missing, no stale options, no broken links.
2. Check HTML is well-formed: `python3 -c "import html.parser,sys; html.parser.HTMLParser().feed(open('docs/GUIDE.html').read())"`
   and that every `id` is unique.
3. Report to the user: what was wrong (grouped: outdated, missing, unclear), what changed per doc, and any
   decisions you need from them. Don't commit unless asked.
4. If this run found a kind of drift the script could catch, add that check to `doc_drift.py`.
