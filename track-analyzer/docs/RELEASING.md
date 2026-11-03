# Releasing a new version

How to publish a new version of the track analyzer, so existing installs see **Update now…** on their Setup tab.
`main` is protected, so the version change goes through a pull request; publishing is pushing a tag.

Run the commands from the `dj-tools` folder (the repository root) in a terminal.

## 1. Pick the number

Versions look like `1.1.0`: major.minor.patch.

| What changed since the last release | Raise | Example from 1.1.0 |
|---|---|---|
| Fixes only | the last number | **1.1.1** |
| New features | the middle number, last back to 0 | **1.2.0** |
| Something that breaks how people use it | the first number, the others back to 0 | **2.0.0** |

The `## Unreleased` section at the top of `track-analyzer/CHANGELOG.md` lists what changed.

## 2. Make the version pull request

```bash
git checkout main && git pull
git checkout -b release-1.2.0
```

Edit two files in `track-analyzer/`:

- `pyproject.toml`: `version = "1.1.0"` → `version = "1.2.0"`.
- `CHANGELOG.md`: `## Unreleased` → `## 1.2.0 (YYYY-MM-DD)`. This section becomes the release notes on GitHub, so
  read it through as a user would.

**Only if this release changes the genre list, the analysis or the models:** add a line to `AFTER` in
`track-analyzer/src/dj_track_analyzer/update.py`, so people who update get a button for the step they need:

```python
AFTER: dict[str, list[str]] = {"1.2.0": ["regenre"]}
```

| Step | Use it when the release… |
|---|---|
| `"regenre"` | changes the genre list or `dj_genres.json` (seconds) |
| `"rescan"` | changes the analysis itself (about half an hour per 500 tracks) |
| `"models"` | needs new or different analysis models |
| `"train"` | changes what the colour scale learns from |

Then update the lock file, commit and open the pull request:

```bash
cd track-analyzer && uv lock && cd ..
git add track-analyzer/pyproject.toml track-analyzer/uv.lock track-analyzer/CHANGELOG.md
git commit -m "Version 1.2.0"
git push -u origin release-1.2.0
gh pr create --base main --title "Version 1.2.0" --body "Version 1.2.0"
```

(Add `track-analyzer/src/dj_track_analyzer/update.py` to the `git add` line if you changed `AFTER`.)

Wait for the checks (Windows, Linux, Docker) to pass, then merge the pull request on GitHub.

## 3. Publish: push the tag

```bash
git checkout main && git pull
git tag v1.2.0 && git push origin v1.2.0
```

The **release** workflow (repository › **Actions**) runs the tests again, checks that the tag matches
`pyproject.toml` and that `CHANGELOG.md` has a `## 1.2.0` section, then publishes the release as **Latest**, with
that section as its notes and a zip of the analyzer (launchers included). The Setup tab of existing installs asks
GitHub at most every 6 hours, so they see the update within that time.

## If the release workflow fails

Its log in **Actions** says why. Usually the tag doesn't match `version` in `pyproject.toml`, or `CHANGELOG.md` has
no `## 1.2.0 ` section (the space after the number matters: `## 1.2.0 (date)`). To try again:

```bash
git push --delete origin v1.2.0 && git tag -d v1.2.0
```

Fix the files in a new pull request, merge it, and push the tag again (step 3).

## The Rust module (`dsp-v…` tags)

The `dsp/` module has releases of its own (tags `dsp-v<version>`), which installs download by themselves. They're
only needed after changing the Rust code: see `docs/notes/dsp.md`. App releases don't touch them.
