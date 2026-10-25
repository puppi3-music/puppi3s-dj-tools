#!/bin/bash
# Double-click this in Finder to start Puppi3's Track Analyzer: the first time it installs what it needs (uv, Python and the
# analyzer, about 400 MB), then it opens the web page, where the Setup tab takes it from there.
# Keep this window open while you use the page; close it (or press Ctrl-C) to stop.
# On Linux, run it from a terminal: ./"Start Puppi3's Track Analyzer.command"

cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
URL="http://127.0.0.1:8766/"

open_page() {
  if [ "$(uname)" = Darwin ]; then open "$1"; else xdg-open "$1" >/dev/null 2>&1 || echo "Open $1 in your browser."; fi
}

fail() {
  echo
  echo "$1"
  echo "README.md, section 1, has the same steps to do by hand."
  read -r -n 1 -p "Press any key to close this window. "
  exit 1
}

if curl -fs -o /dev/null "${URL}api/libraries" 2>/dev/null; then
  echo "Puppi3's Track Analyzer is already running: opening the page."
  open_page "$URL"
  exit 0
fi

if [ "$(uname)" = Linux ] && [ "$(uname -m)" != x86_64 ]; then
  fail "On Linux the analyzer needs an x86_64 (Intel/AMD) computer: its audio library has no build for $(uname -m)."
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv, the Python manager the analyzer runs with (from astral.sh)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh || fail "Couldn't install uv. Check the internet connection and try again."
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 || fail "uv was installed but can't be found. Close this window and double-click again."
fi

echo "Getting Python and the analyzer ready (the first time takes a few minutes)…"
uv sync --inexact || fail "Couldn't install the analyzer's Python packages."

echo
echo "Starting Puppi3's Track Analyzer. The page opens in your browser."
echo "Keep this window open while you use it; close it (or press Ctrl-C) to stop."
echo
exec uv run dj-track-analyzer web
