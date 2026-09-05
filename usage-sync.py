#!/usr/bin/env python3
"""Syncs the UsageSnapshot file to its destinations. Run by launchd whenever the snapshot folder changes.

Reads  ~/Library/Application Support/ClaudeUsage/usage-snapshot.json
Config ~/Library/Application Support/ClaudeUsage/publisher.json   (optional)
  {
    "destinations": ["~/Library/Mobile Documents/com~apple~CloudDocs/ClaudeUsage/usage-snapshot.json"],
    "command": ["/path/to/hook", "{path}"]      // optional: run after a successful sync
  }
Log    ~/Library/Logs/ClaudeUsage/publisher.log

Without a config file the snapshot is copied to iCloud Drive (if present) so it reaches the phone.
Copies are atomic (temp file + rename) and skipped when the destination already holds identical bytes,
so a launchd trigger for an unchanged file is a no-op.

Optional, UNOFFICIAL add-on — `"unofficialUsage": "claude-code-oauth"` in the config:
  The status line only sees terminal sessions and has no model window. With this flag the sync reads
  the OAuth token Claude Code keeps in the macOS keychain ("Claude Code-credentials") and asks the same
  endpoint Claude Code's /usage panel uses (GET api.anthropic.com/api/oauth/usage) for all plan windows
  (session, week, model), writing them to the copies sent to the destinations. Pair it with the
  launchd StartInterval so it also runs without status line changes. The token goes
  nowhere but api.anthropic.com, is never logged and never refreshed by this script (Claude Code does
  that itself). This uses an undocumented endpoint; it may stop working at any time, and it is off by
  default. See README.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

APP_DIR = os.path.expanduser("~/Library/Application Support/ClaudeUsage")
SNAPSHOT = os.environ.get("CLAUDE_USAGE_SNAPSHOT_PATH", os.path.join(APP_DIR, "usage-snapshot.json"))
CONFIG = os.environ.get("CLAUDE_USAGE_PUBLISHER_CONFIG", os.path.join(APP_DIR, "publisher.json"))
LOG = os.environ.get("CLAUDE_USAGE_PUBLISHER_LOG", os.path.expanduser("~/Library/Logs/ClaudeUsage/publisher.log"))
ICLOUD_DRIVE = os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs")


def log(message):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with open(LOG, "a") as f:
        f.write(f"{stamp} {message}\n")


def load_config():
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except FileNotFoundError:
        destinations = []
        if os.path.isdir(ICLOUD_DRIVE):
            destinations.append(os.path.join(ICLOUD_DRIVE, "ClaudeUsage", "usage-snapshot.json"))
        return {"destinations": destinations}
    except ValueError as e:
        log(f"config invalid, using defaults: {e}")
        return {"destinations": []}


def read_snapshot():
    with open(SNAPSHOT, "rb") as f:
        data = f.read()
    snapshot = json.loads(data)
    if snapshot.get("version") != 1 or not isinstance(snapshot.get("windows"), list):
        raise ValueError("not a UsageSnapshot v1")
    return data, snapshot


def same_bytes(path, data):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).digest() == hashlib.sha256(data).digest()
    except OSError:
        return False


def copy_atomic(data, destination):
    destination = os.path.expanduser(destination)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(destination), prefix=".usage-", suffix=".json")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, destination)


# ---------------------------------------------------------------------------------------------------
# Unofficial model-window add-on

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"


def claude_code_token():
    """Access token from Claude Code's keychain item, or None (missing, unreadable, or expired)."""
    try:
        blob = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        creds = json.loads(blob)
        oauth = creds.get("claudeAiOauth") or creds
        token = oauth.get("accessToken")
        expires = oauth.get("expiresAt")
        if expires and float(expires) / 1000 < datetime.now(timezone.utc).timestamp():
            log("model window: Claude Code token expired; skipped until Claude Code refreshes it")
            return None
        return token
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, AttributeError, OSError):
        return None


def fetch_usage(token):
    import urllib.request
    request = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "Accept": "application/json",
        "User-Agent": "ClaudeUsage-publisher/1.0 (+https://github.com/anthropics/claude-code/issues/91920)",
    })
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read())


WINDOW_KINDS = {
    # limits[].kind → (window id, kind, default name, seconds)
    "session": ("session", "session", "Session", 5 * 3600),
    "weekly_all": ("week", "weekly", "Week", 7 * 24 * 3600),
    "weekly_scoped": ("model", "weekly", "Model", 7 * 24 * 3600),
}
FLAT_KEYS = {
    "five_hour": ("session", "session", "Session", 5 * 3600),
    "seven_day": ("week", "weekly", "Week", 7 * 24 * 3600),
    "seven_day_overage_included": ("model", "weekly", "Fable", 7 * 24 * 3600),
    "seven_day_opus": ("model", "weekly", "Opus", 7 * 24 * 3600),
    "seven_day_sonnet": ("model", "weekly", "Sonnet", 7 * 24 * 3600),
}


def iso_utc(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def windows_from_usage(usage):
    """All plan windows from the usage response as UsageSnapshot windows, keyed by id.
    Prefers the `limits` array (the shape the claude.ai settings page uses), falls back to flat keys."""
    found = {}
    for item in usage.get("limits") or []:
        if not isinstance(item, dict) or item.get("kind") not in WINDOW_KINDS:
            continue
        if item.get("percent") is None or not item.get("resets_at"):
            continue
        wid, kind, name, seconds = WINDOW_KINDS[item["kind"]]
        if wid == "model":
            scope = (item.get("scope") or {}).get("model") or {}
            name = scope.get("display_name") or scope.get("id") or name
        found[wid] = {"id": wid, "kind": kind, "name": name, "percent": round(float(item["percent"]), 1),
                      "resetsAt": iso_utc(item["resets_at"]), "windowSeconds": seconds,
                      "source": "claude-code-oauth (unofficial)"}
    if found:
        return found
    for key, (wid, kind, name, seconds) in FLAT_KEYS.items():
        entry = usage.get(key)
        if wid in found or not isinstance(entry, dict):
            continue
        if entry.get("utilization") is None or not entry.get("resets_at"):
            continue
        found[wid] = {"id": wid, "kind": kind, "name": name, "percent": round(float(entry["utilization"]), 1),
                      "resetsAt": iso_utc(entry["resets_at"]), "windowSeconds": seconds,
                      "source": "claude-code-oauth (unofficial)"}
    return found


def scoped_model_window(usage):
    """Back-compat helper: (name, percent, resets_at) of the model window, or None."""
    w = windows_from_usage(usage).get("model")
    return (w["name"], w["percent"], w["resetsAt"]) if w else None


def refresh_from_oauth(snapshot):
    """Replaces the snapshot's windows with the live values from the usage endpoint (all windows, not
    only the model one — the status line only sees terminal sessions, the endpoint sees the account).
    Returns True when the snapshot changed. Never raises."""
    token = claude_code_token()
    if not token:
        return False
    try:
        usage = fetch_usage(token)
    except Exception as e:                      # network, HTTP, JSON — never let this break the sync
        log(f"oauth usage: fetch failed: {type(e).__name__}")
        return False
    live = windows_from_usage(usage)
    if not live:
        return False
    merged = {w["id"]: w for w in snapshot["windows"]}
    merged.update(live)
    ordered = [merged[k] for k in ("session", "week", "model") if k in merged]
    ordered += [w for k, w in merged.items() if k not in ("session", "week", "model")]
    before = [(w["id"], w.get("percent"), w.get("resetsAt")) for w in snapshot["windows"]]
    after = [(w["id"], w.get("percent"), w.get("resetsAt")) for w in ordered]
    snapshot["windows"] = ordered
    snapshot["source"] = "claude-code-statusline+oauth"
    if after != before:
        snapshot["fetchedAt"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return True


# kept for readers of older configs / docs
enrich_with_model_window = refresh_from_oauth


def main():
    try:
        data, snapshot = read_snapshot()
    except FileNotFoundError:
        return 0                      # folder changed but no snapshot yet (or temp file only)
    except (OSError, ValueError) as e:
        log(f"snapshot unreadable: {e}")
        return 0

    config = load_config()
    unofficial = config.get("unofficialUsage") or config.get("modelWindow")   # `modelWindow`: older name
    if unofficial == "claude-code-oauth":
        # Start from the last published copy so an unchanged endpoint answer keeps its fetchedAt and
        # produces identical bytes (→ no sync, no hook run).
        published_path = os.path.join(os.path.dirname(SNAPSHOT), "out", "usage-snapshot.json")
        try:
            with open(published_path) as f:
                previous_published = json.load(f)
            if isinstance(previous_published.get("windows"), list):
                snapshot["windows"] = previous_published["windows"]
                snapshot["fetchedAt"] = previous_published.get("fetchedAt", snapshot["fetchedAt"])
        except (OSError, ValueError):
            pass
        if refresh_from_oauth(snapshot):
            data = (json.dumps(snapshot, indent=2) + "\n").encode()

    synced = []
    for destination in config.get("destinations", []):
        target = os.path.expanduser(destination)
        if same_bytes(target, data):
            continue
        try:
            copy_atomic(data, target)
            synced.append(target)
        except OSError as e:
            log(f"copy to {target} failed: {e}")

    if synced:
        summary = " ".join(f"{w.get('id')}={w.get('percent')}%" for w in snapshot["windows"])
        log(f"synced {snapshot.get('fetchedAt')} [{summary}] → {len(synced)} destination(s)")
        command = config.get("command")
        if command:
            # The hook gets exactly the bytes the destinations received (including the model window).
            # Written to a subfolder so the launchd folder watch on APP_DIR does not fire again.
            published = os.path.join(os.path.dirname(SNAPSHOT), "out", "usage-snapshot.json")
            try:
                copy_atomic(data, published)
            except OSError as e:
                log(f"published copy failed: {e}")
                published = SNAPSHOT
            args = [str(a).replace("{path}", published) for a in command]
            try:
                subprocess.run(args, timeout=30, check=False)
            except (OSError, subprocess.TimeoutExpired) as e:
                log(f"hook failed: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
