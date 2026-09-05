#!/bin/sh
# ClaudeUsage publisher — one-command setup.
#
#   Publisher/setup.sh                 install status line + watcher; data goes to iCloud Drive
#   Publisher/setup.sh --with-oauth    also refresh all windows from Claude Code's account (unofficial)
#   Publisher/setup.sh --gist          also publish a secret gist for the phone (needs `gh` logged in)
#   Publisher/setup.sh status          what is running, where the data goes, last log lines
#   Publisher/setup.sh uninstall       remove status line entry and launchd agent (data files stay)
#
# Idempotent: re-running updates the scripts and keeps an existing publisher.json as it is; flags only
# add to it. No admin rights, no questions.
set -e
DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"   # resolve Homebrew symlinks
APP_DIR="${CLAUDE_USAGE_APP_DIR:-$HOME/Library/Application Support/ClaudeUsage}"
CONFIG="$APP_DIR/publisher.json"
ICLOUD="$HOME/Library/Mobile Documents/com~apple~CloudDocs/ClaudeUsage/usage-snapshot.json"
WITH_OAUTH=0; WITH_GIST=0; MODE=install
for arg in "$@"; do
  case "$arg" in
    --with-oauth) WITH_OAUTH=1 ;;
    --gist) WITH_GIST=1 ;;
    status|uninstall) MODE="$arg" ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

case "$MODE" in
  uninstall)
    "$DIR/install.sh" uninstall
    "$DIR/install-watcher.sh" uninstall
    echo "Removed. Data and config remain in: $APP_DIR (delete the folder to remove them)."
    exit 0 ;;
  status)
    "$DIR/install-watcher.sh" status
    echo "--- config: $CONFIG"; cat "$CONFIG" 2>/dev/null || echo "(none)"
    echo "--- status line:"; python3 -c 'import json,os;print(json.load(open(os.path.expanduser("~/.claude/settings.json"))).get("statusLine"))' 2>/dev/null
    exit 0 ;;
esac

command -v python3 >/dev/null || { echo "python3 is required (Xcode Command Line Tools)"; exit 1; }
mkdir -p "$APP_DIR"

# 1. Status line (official data from Claude Code terminal sessions)
"$DIR/install.sh" | sed 's/^/  /'

# 2. Config: create with iCloud Drive as destination when present; keep an existing file, add flags only
python3 - "$CONFIG" "$ICLOUD" "$WITH_OAUTH" "$WITH_GIST" "$APP_DIR" <<'PY'
import json, os, sys
config, icloud, oauth, gist, app = sys.argv[1:6]
try:
    cfg = json.load(open(config))
    created = False
except (OSError, ValueError):
    cfg = {"destinations": [icloud] if os.path.isdir(os.path.dirname(os.path.dirname(icloud))) else []}
    created = True
if oauth == "1":
    cfg["unofficialUsage"] = "claude-code-oauth"
if gist == "1":
    cfg["command"] = [os.path.join(app, "bin", "gist-hook.sh"), "{path}"]
json.dump(cfg, open(config, "w"), indent=2)
print("  config", "created" if created else "kept", "→", ", ".join(cfg.get("destinations") or ["(no file destination)"]))
if cfg.get("unofficialUsage"): print("  unofficial account refresh: on")
if cfg.get("command"): print("  hook:", " ".join(cfg["command"]))
PY

# 3. Watcher (launchd, per user)
"$DIR/install-watcher.sh" install | sed 's/^/  /'

# 4. Optional gist for the phone
if [ "$WITH_GIST" = "1" ]; then
  if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
    if [ -f "$APP_DIR/out/usage-snapshot.json" ] || [ -f "$APP_DIR/usage-snapshot.json" ]; then
      SRC="$APP_DIR/out/usage-snapshot.json"; [ -f "$SRC" ] || SRC="$APP_DIR/usage-snapshot.json"
      "$APP_DIR/bin/gist-hook.sh" "$SRC" | sed 's/^/  /'
    else
      echo "  gist: will be created on the first sync (no snapshot yet)"
    fi
  else
    echo "  gist: skipped — install GitHub CLI and run 'gh auth login' first" >&2
  fi
fi

cat <<TXT

Done. What happens now:
  • Each answer in a Claude Code terminal session writes $APP_DIR/usage-snapshot.json
  • The watcher copies it to the destinations above within a second$( [ "$WITH_OAUTH" = "1" ] && echo " and refreshes every 5 min" )
  • On the phone: open the app → Data source → paste the gist raw URL (run with --gist to create one;
    the iCloud source replaces this step once the app ships with the iCloud capability)
  • $DIR/setup.sh status   |   $DIR/setup.sh uninstall
TXT
