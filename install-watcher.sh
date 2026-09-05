#!/bin/sh
# Installs a per-user launchd agent that runs usage-sync.py whenever the snapshot folder changes.
# No admin rights needed. `install-watcher.sh uninstall` removes it, `status` shows launchd's view.
set -e
DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"   # resolve Homebrew symlinks
LABEL="${CLAUDE_USAGE_AGENT_LABEL:-io.fatec.claudeusage.publisher}"
APP_DIR="${CLAUDE_USAGE_APP_DIR:-$HOME/Library/Application Support/ClaudeUsage}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/ClaudeUsage"
DOMAIN="gui/$(id -u)"
MODE="${1:-install}"

case "$MODE" in
  install)
    mkdir -p "$HOME/Library/LaunchAgents" "$APP_DIR/bin" "$LOG_DIR"
    # launchd agents may not read ~/Downloads, ~/Desktop or ~/Documents (macOS privacy protection),
    # so the sync script is copied next to the data instead of being run from the repo.
    cp "$DIR/usage-sync.py" "$DIR/gist-hook.sh" "$APP_DIR/bin/"
    chmod +x "$APP_DIR/bin/usage-sync.py" "$APP_DIR/bin/gist-hook.sh"
    cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>$APP_DIR/bin/usage-sync.py</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>CLAUDE_USAGE_SNAPSHOT_PATH</key><string>$APP_DIR/usage-snapshot.json</string>
    <key>CLAUDE_USAGE_PUBLISHER_CONFIG</key><string>$APP_DIR/publisher.json</string>
  </dict>
  <key>WatchPaths</key>
  <array><string>$APP_DIR</string></array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>${CLAUDE_USAGE_SYNC_INTERVAL:-300}</integer>
  <key>ThrottleInterval</key><integer>2</integer>
  <key>StandardOutPath</key><string>$LOG_DIR/agent.out.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/agent.err.log</string>
</dict>
</plist>
PLIST
    # bootout is asynchronous; wait until the old instance is gone before bootstrapping the new one.
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
      launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
      for _ in 1 2 3 4 5 6 7 8 9 10; do
        launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 || break
        sleep 0.5
      done
    fi
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "installed $LABEL (watching $APP_DIR)"
    ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST" "$APP_DIR/bin/usage-sync.py" "$APP_DIR/bin/gist-hook.sh"
    echo "removed $LABEL"
    ;;
  status)
    launchctl print "$DOMAIN/$LABEL" 2>/dev/null | grep -E 'state|last exit|runs' || echo "$LABEL not loaded"
    tail -n 5 "$LOG_DIR/publisher.log" 2>/dev/null || echo "(no publisher.log yet)"
    ;;
  *)
    echo "usage: install-watcher.sh [install|uninstall|status]" >&2; exit 2 ;;
esac
