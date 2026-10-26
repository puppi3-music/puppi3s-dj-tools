---
name: audit-web
description: Audit the web page (`web` command: web.py + web/index.html) for bugs, slow endpoints, security gaps of a local server, and visual problems in light/dark and at phone width. Use when asked to review, audit, check, speed up or polish the web interface or page.
---

# Audit the web page

Goal: find what breaks, misleads, leaks or looks wrong in the page, not style nits. Read-only: report findings,
then ask the user before fixing (they prefer to decide; see CLAUDE.md).

## 1. Run the live checks (real data on this Mac)

```
uv run python .claude/skills/audit-web/scripts/checks.py 2>&1 | grep -vi warn | tee logs/audit-web.txt
uv run python .claude/skills/audit-web/scripts/checks.py shots <scratch dir>     # screenshots, ~5 min
uv run python .claude/skills/audit-web/scripts/checks.py export                  # snapshot time/size, slow
```

The script starts the page's own app (`web.build_app`) on a spare port in-process and calls it, so it works
whether or not the user's server runs on 8766. It never starts jobs or writes to Traktor/files; the server's own
caches may fill as in normal use. Checks: `endpoints` (every read endpoint per library, cold then warm: status,
ms, KB), `tracks` (a track panel: /api/cues twice, suggest, wave; `_saved_db` alone), `errors` (bad input must
give a 4xx JSON `{error}`: the page calls `.json()` on every answer, so a 500 text page shows as a JSON parse
error), `hosts` (DNS rebinding: a foreign Host header must be refused), `static` (`node --check` on the script,
hard-coded hex colours outside the theme tokens, global listeners), `overflow` (pages that scroll sideways at
390 px and the elements sticking out), `shots` (every tab + one track's panel, light/dark × 1400/390 px), `export`.
`overflow` and `shots` drive Chrome over the DevTools protocol (`scripts/overflow.mjs`, node 22+) with real
phone emulation: Chrome's own `--window-size` stops at ~500 px, so a CLI screenshot at 390 is a wider page cropped. Compare with the baselines; a change is a finding.

Look at the screenshots yourself (Read the PNGs). Chrome follows macOS dark mode unless forced, so check the
"light" shots are really light.

Baselines (2026-10-25 after the fixes, 469 tracks, views at-135 and at-145):
- endpoints warm: tracks 15-20 ms / ~540 KB per library, hype ~20-40 ms, colours/explain ~250 ms (reads the NML +
  History every call), setup 30 ms (90 cold), everything else < 25 ms. Cold hype 450 ms (module load).
- tracks: /api/cues 30-70 ms warm; `_saved_db` 3 ms (the sample-folder walk runs once per server run, was
  29 ms per call); whole-track /api/wave 0.7-1.6 s, 180-310 KB (decode; first zoom only); suggest < 15 ms.
- errors: all ok (JSON 4xx). hosts: all refused (400), foreign Origin 403.
- static: node ok; the 8 CSS hex colours left are white text on accent buttons and the yellow corrections box
  (fine in both themes); no per-render global listener (drawWave's mouseup has an AbortController `signal`).
- overflow: no page scrolls sideways at 390 px.
- Fixed 2026-10-25 (don't reintroduce): DNS rebinding answered; 500 text pages on bad bodies; drawWave leaked a
  window listener per render; the corrections poll re-rendered the Tracks tab (lost focus/scroll) and re-fetched
  the wrong library; Escape/deck keys acted behind dialogs; white-on-white cue pads and legend in light mode; white
  hype-badge text; Mixes/Setup scrolled sideways at phone width; a crashed correction count re-polled forever;
  deck-audio cleanup could delete another request's .tmp.flac; `saved_file` accepted `..\` (Windows);
  /api/tempo/report?bpm=<no view> created an empty data/at-<bpm> (a new library in the menu).

## 2. Read the code with this checklist

**Server (web.py)**
- Every endpoint answers JSON on failure via `failed()`: SystemExit (CLI advice) → 409, bad input → 400, else
  500 + logged. New endpoints use `respond()` / `post()` (body parsed inside the try), not one-off copies.
- Host check (`allowed_hosts` + TrustedHostMiddleware) stays on; POSTs also need `from_this_page`.
- GETs only read: nothing a URL can name may create data (tempo_report checks the view exists first).
- Globals swapped by `library()` (collection.DB, cache) are only read inside it; module caches (`_grids`,
  `_fixes`, `_wave_src`, deck-audio files) are reset where data changes (`_after_job`, rescan, write/undo).
- Background work (`correction_counts`): one track's exception is logged and skipped (the rest still count); any
  other exception still sets the key (partial counts), or every
  /api/tracks restarts it and the page re-polls every 4 s forever.
- Files served or revealed: only from the scanned library (`playable`) or the sample root; `saved_file` takes
  a bare file name only (`Path(name).name == name`, no `\`: Windows).
- Cache cleanup (`_prune_deck_audio`) skips `*.tmp.flac`, which another request may be writing, and files another
  track's cleanup deletes mid-sort (the locks are per file).
- Phone header: ◐ beside the name (`#theme` order 1), library + export on the next line, nav below.
- Work repeated per request that could be cached by mtime (NML, Settings.tsi); the sample folder walk is once per run.

**Page (index.html)**
- Every `innerHTML` value from data goes through `esc()` (track tags are user data); `data-tip` too.
- Listeners on `window`/`document` only at top level or removable (`WAVE_UP` AbortController); per-render ones on
  replaced elements.
- Async races: an answer for an older track/route/library must not draw (`S.routeId`, `d.dataset.path`,
  `DECK.asked`). Background refreshes redraw in place (`S.refreshTracks`), never `route()`, which wipes focus
  and scroll while the user may be typing; `getTracks(lib)` fetches the library it was asked for.
- Keys: Escape and Space/1-8/C must not act behind an open `<dialog>` or menu (`overlayOpen()`).
- Answers read with `readJSON` (a non-JSON answer becomes its status), never a bare `r.json()`.
- Snapshot mode (`SNAP`): every write control hidden, every `api()` path the page calls has a `snapApi` case.
- Numbers with signs: a "+" only on positive values (never glued in front of one that can be negative).
- Overview links (`data-go` → `showTracks`): each opens Tracks with the same rule `collection.summary` counts by,
  so the counts match (genre = top genre only via `TF.genreTop`, colour = yours else predicted, Traktor's BPM/key,
  errors not `tags:`). Filters with no control of their own show as `.fchip` chips with ✕.
- Track panel: the sticky `.drawer-head` (its negative `top`/margins equal the drawer's padding, both widths);
  wide mode redraws the waveform on `transitionend` (the canvas measures itself only when it draws); help folds
  (`helpFold`, `S.helpOpen`) stay open across redraws.

**Visual**
- Light and dark: only theme tokens on page surfaces (`.pads` sit on the panel, not the waveform: `var(--text)`,
  `--good`, `--bad`); fixed colours only inside the dark waveform box and as legend swatches.
- Contrast: text on tinted backgrounds (hype badges keep `var(--text)` on every shade).
- Phone width (390): no sideways page scroll (`overflow` check); grid columns `minmax(0, 1fr)`, long names
  `overflow-wrap: anywhere`; tables scroll inside `.tablewrap`; drawer full width.
- Keyboard: track rows and tooltips are mouse-only (`tr.click` has no tabindex; `data-tip` on hover only).

**Efficiency**
- Payloads: /api/tracks sends every column for every track (~540 KB per library) and is fetched again after
  each job and corrections poll; GZip or fewer columns if it grows.
- `snapshot` calls `track_cues` for every track (minutes; `export` check).

## 3. Report

One list, most severe first: security, then bugs that lose or mis-show data, then speed, then visuals. For
each: file:line, what goes wrong for whom, evidence (a check's line, a screenshot, or "read in code"), and the
fix. Update the baselines above when they move, and CLAUDE.md if a finding changes a documented behaviour.
