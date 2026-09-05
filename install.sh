#!/bin/sh
# Installs statusline-publish.py as the Claude Code status line, wrapping any existing status line
# command so its output is unchanged. `install.sh uninstall` restores the previous command.
set -e
SOURCE="$(cd "$(dirname "$0")" && pwd)/statusline-publish.py"
APP_DIR="${CLAUDE_USAGE_APP_DIR:-$HOME/Library/Application Support/ClaudeUsage}"
# The script is copied out of the repo: a checkout under ~/Downloads, ~/Desktop or ~/Documents is not
# readable for background processes (macOS privacy protection), and moving the repo must not break it.
SCRIPT="$APP_DIR/bin/statusline-publish.py"
SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
MODE="${1:-install}"
if [ "$MODE" = "install" ]; then
  mkdir -p "$APP_DIR/bin"
  cp "$SOURCE" "$SCRIPT"
  chmod +x "$SCRIPT"
fi

python3 - "$SCRIPT" "$SETTINGS" "$MODE" <<'PY'
import json, os, sys
script, settings, mode = sys.argv[1:4]
data = {}
if os.path.exists(settings):
    with open(settings) as f:
        data = json.load(f)
current = data.get("statusLine") or {}
cmd = current.get("command", "") if current.get("type") == "command" else ""
marker = os.path.basename(script)

if mode == "install":
    if marker in cmd:
        print("already installed:", cmd); sys.exit(0)
    new_cmd = f'"{script}"' + (f" {json.dumps(cmd)}" if cmd else "")
    data["_statusLineBeforeClaudeUsage"] = current or None
    data["statusLine"] = {"type": "command", "command": new_cmd}
    print("installed:", new_cmd)
elif mode == "uninstall":
    if marker not in cmd:
        print("not installed"); sys.exit(0)
    previous = data.pop("_statusLineBeforeClaudeUsage", None)
    if previous:
        data["statusLine"] = previous
    else:
        data.pop("statusLine", None)
    print("restored:", data.get("statusLine"))
else:
    sys.exit("usage: install.sh [install|uninstall]")

os.makedirs(os.path.dirname(settings), exist_ok=True)
with open(settings, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
