#!/usr/bin/env python3
"""Claude Code status line script that publishes plan usage for the ClaudeUsage widgets.

Claude Code runs this on every assistant message (and on window resets) with its status line JSON
on stdin. The script:

  1. extracts `rate_limits` (five_hour, seven_day) and writes a UsageSnapshot v1 JSON file,
     atomically and only when the values changed;
  2. prints a status line — either the output of your own status line command (pass it as the
     first argument, it receives the same stdin) or a compact default.

Only documented status line fields are used. No network, no tokens, no cookies.

Usage in ~/.claude/settings.json:
  {"statusLine": {"type": "command", "command": "~/path/statusline-publish.py"}}
  {"statusLine": {"type": "command", "command": "~/path/statusline-publish.py ~/.claude/my-statusline.sh"}}

Environment:
  CLAUDE_USAGE_SNAPSHOT_PATH  override the output file
                              (default ~/Library/Application Support/ClaudeUsage/usage-snapshot.json)
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

SNAPSHOT_VERSION = 1
SOURCE = "claude-code-statusline"
DEFAULT_PATH = os.path.expanduser("~/Library/Application Support/ClaudeUsage/usage-snapshot.json")

# status line key → (window id, kind, display name, duration in seconds)
WINDOWS = {
    "five_hour": ("session", "session", "Session", 5 * 3600),
    "seven_day": ("week", "weekly", "Week", 7 * 24 * 3600),
}


def iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def load_previous(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return {w["id"]: w for w in data.get("windows", [])}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def build_windows(rate_limits, previous, now):
    """Windows from the status line; a window Claude Code dropped after its reset is kept from the
    previous snapshot only while its reset lies in the future (Claude Code drops it at reset time,
    so this is a safety net for clock skew), otherwise it is omitted (= no tracked usage)."""
    windows = []
    for key, (wid, kind, name, duration) in WINDOWS.items():
        entry = rate_limits.get(key)
        if isinstance(entry, dict) and "used_percentage" in entry and "resets_at" in entry:
            windows.append({
                "id": wid,
                "kind": kind,
                "name": name,
                "percent": round(float(entry["used_percentage"]), 1),
                "resetsAt": iso(int(entry["resets_at"])),
                "windowSeconds": duration,
            })
        elif wid in previous:
            prev = previous[wid]
            try:
                resets = datetime.fromisoformat(prev["resetsAt"].replace("Z", "+00:00"))
                if resets > now:
                    windows.append(prev)
            except (KeyError, ValueError):
                pass
    return windows


def write_snapshot(path, windows, now):
    snapshot = {
        "version": SNAPSHOT_VERSION,
        "source": SOURCE,
        "fetchedAt": now.isoformat().replace("+00:00", "Z"),
        "windows": windows,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".usage-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(snapshot, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def changed(previous, windows):
    current = {w["id"]: (w["percent"], w["resetsAt"]) for w in windows}
    before = {k: (v.get("percent"), v.get("resetsAt")) for k, v in previous.items()}
    return current != before


def default_line(data, windows):
    parts = []
    by_id = {w["id"]: w for w in windows}
    if "session" in by_id:
        parts.append(f"5h {by_id['session']['percent']:.0f}%")
    if "week" in by_id:
        parts.append(f"7d {by_id['week']['percent']:.0f}%")
    ctx = (data.get("context_window") or {}).get("used_percentage")
    if ctx is not None:
        parts.append(f"ctx {ctx:.0f}%")
    return " · ".join(parts) if parts else "usage: waiting for first response"


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}

    path = os.environ.get("CLAUDE_USAGE_SNAPSHOT_PATH", DEFAULT_PATH)
    now = datetime.now(timezone.utc).replace(microsecond=0)   # whole seconds: plain ISO 8601 for every reader
    previous = load_previous(path)
    windows = build_windows(data.get("rate_limits") or {}, previous, now)

    try:
        if windows and changed(previous, windows):
            write_snapshot(path, windows, now)
    except OSError as e:
        print(f"usage publish failed: {e}", file=sys.stderr)

    inner = sys.argv[1:]
    if inner:
        # Hand the same JSON to the user's own status line and show its output.
        result = subprocess.run(inner, input=raw, capture_output=True, text=True, shell=len(inner) == 1)
        sys.stdout.write(result.stdout)
        if result.returncode != 0 and result.stderr:
            sys.stderr.write(result.stderr)
    else:
        print(default_line(data, windows))


if __name__ == "__main__":
    main()
