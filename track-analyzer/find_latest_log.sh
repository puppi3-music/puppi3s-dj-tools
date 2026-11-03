#!/bin/bash

# Target directory (defaults to /private/tmp/claude-502/ if not provided)
TARGET_DIR="${1:-/private/tmp/claude-502/}"

newest() {
    # macOS stat: %m = mtime (epoch), %N = name
    find "$TARGET_DIR" -type f "$@" -exec stat -f '%m %N' {} + 2>/dev/null \
        | sort -rn | head -n 1 | cut -d' ' -f2-
}

LATEST_LOG=$(newest \( -name "*.log" \))
#LATEST_LOG=$(newest \( -name "*.log" -o -name "*.output" \))
[ -z "$LATEST_LOG" ] && LATEST_LOG=$(newest)

if [ -z "$LATEST_LOG" ]; then
    echo "No valid files found to tail in: $TARGET_DIR"
    exit 1
fi

echo "Following the latest modified file: $LATEST_LOG"
tail -F "$LATEST_LOG"
