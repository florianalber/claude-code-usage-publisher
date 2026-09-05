#!/bin/sh
# Sync hook: publishes the snapshot as a secret GitHub gist so the widgets can fetch it over HTTPS.
# Configure in publisher.json:  "command": ["<APP_DIR>/bin/gist-hook.sh", "{path}"]
# The gist id is created on first run and stored in <APP_DIR>/gist-id. Needs `gh` logged in.
set -e
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
SNAPSHOT="$1"
APP_DIR="${CLAUDE_USAGE_APP_DIR:-$HOME/Library/Application Support/ClaudeUsage}"
ID_FILE="$APP_DIR/gist-id"
NAME="usage-snapshot.json"

if [ -f "$ID_FILE" ]; then
  gh gist edit "$(cat "$ID_FILE")" -f "$NAME" "$SNAPSHOT" >/dev/null
else
  URL=$(gh gist create --desc "ClaudeUsage snapshot (secret)" "$SNAPSHOT")
  ID=$(basename "$URL")
  printf '%s\n' "$ID" > "$ID_FILE"
  USER=$(gh api user -q .login)
  echo "gist created: $URL"
  echo "raw URL for the app: https://gist.githubusercontent.com/$USER/$ID/raw/$NAME"
fi
