# Publisher

Publishes Claude plan usage from Claude Code's documented status line data into a small JSON file
and syncs that file to wherever the phone can read it. No network, no tokens, no cookies: only the
fields Claude Code hands to every status line script.

## Files

- `statusline-publish.py` — the status line script. Writes `usage-snapshot.json`, prints a status line.
- `install.sh` — sets it as the status line in `~/.claude/settings.json`, wrapping an existing command
  so your terminal keeps showing what it showed before. `install.sh uninstall` restores it.
- `usage-sync.py` — copies the snapshot to its destinations, atomically, skipping identical files.
- `install-watcher.sh` — installs a per-user launchd agent (`io.fatec.claudeusage.publisher`) that runs
  `usage-sync.py` whenever the snapshot folder changes. `status` and `uninstall` included. No admin rights.

## Install

```sh
Publisher/install.sh
```

Both installers copy their script to `~/Library/Application Support/ClaudeUsage/bin/` and point Claude
Code / launchd there. Reason: macOS blocks background processes from reading `~/Downloads`, `~/Desktop`
and `~/Documents` (a launchd agent started from a checkout in Downloads fails with "Operation not
permitted"), and the installation must survive moving the repo. Re-run the installer after changing a
script to update the copy.

Restart Claude Code (a running session picks up the new command on its own). After the first
assistant response the file appears:

```
~/Library/Application Support/ClaudeUsage/usage-snapshot.json
```

Override the location with `CLAUDE_USAGE_SNAPSHOT_PATH`.

## Watcher

```sh
Publisher/install-watcher.sh            # install + start
Publisher/install-watcher.sh status     # launchd state and last log lines
Publisher/install-watcher.sh uninstall
```

launchd watches `~/Library/Application Support/ClaudeUsage/` (`WatchPaths` on the folder, because the
publisher replaces the file atomically and a watch on the file itself would be lost with the old inode).
Each change starts `usage-sync.py`, which exits immediately when nothing changed. No long-running process.

Destinations come from `~/Library/Application Support/ClaudeUsage/publisher.json`:

```json
{
  "destinations": ["~/Library/Mobile Documents/com~apple~CloudDocs/ClaudeUsage/usage-snapshot.json"],
  "command": ["/usr/local/bin/my-hook", "{path}"]
}
```

Without a config the default destination is that iCloud Drive path when iCloud Drive is enabled,
otherwise nothing is copied and only `~/Library/Logs/ClaudeUsage/publisher.log` records the change.
The optional `command` runs after a successful sync with `{path}` replaced by
`~/Library/Application Support/ClaudeUsage/out/usage-snapshot.json`, the exact bytes the destinations
received (the future CloudKit uploader plugs in here).

### Gist hook — the path to the phone today

The widget extension cannot read iCloud Drive without an iCloud entitlement, but it can fetch an HTTPS
URL. `gist-hook.sh` publishes the snapshot as a **secret gist** (unlisted URL, your GitHub account, `gh`
must be logged in) and updates it on every sync. Configure:

```json
{
  "destinations": ["~/Library/Mobile Documents/com~apple~CloudDocs/ClaudeUsage/usage-snapshot.json"],
  "command": ["~/Library/Application Support/ClaudeUsage/bin/gist-hook.sh", "{path}"]
}
```

The first run prints the raw URL (`https://gist.githubusercontent.com/<user>/<id>/raw/usage-snapshot.json`);
paste it into the app under "Data source". The widgets then fetch it every 15 minutes. The gist holds
only percentages and reset times. Raw gist URLs are served with short caching; the app bypasses the
local cache on fetch.

### Unofficial add-on: live values for all windows, including the model ("Fable")

The status line runs only inside terminal sessions of Claude Code. Usage in the Claude desktop app,
in the browser or on the phone never reaches it, and it has no model-specific window. If you accept an
**unofficial** path, set

```json
{ "unofficialUsage": "claude-code-oauth" }
```

in `publisher.json`. The sync then reads the OAuth token Claude Code stores in the macOS keychain (item
"Claude Code-credentials"), calls the endpoint Claude Code's own `/usage` panel calls
(`GET https://api.anthropic.com/api/oauth/usage`) and writes all plan windows — session, week and the
`weekly_scoped` model window — to the copies sent to the destinations. The local `usage-snapshot.json`
stays pure status line data. The launchd agent also runs the sync every 5 minutes (`StartInterval`,
override with `CLAUDE_USAGE_SYNC_INTERVAL` at install time), so the values stay current without any
terminal session; unchanged answers produce identical bytes and no upload. While the Mac sleeps no
sync runs; launchd fires the missed interval once on wake, so the phone catches up within minutes.

What this does and does not do:

- The token is sent only to `api.anthropic.com`, never written to any file or log, and never refreshed by
  this script. When it has expired, the refresh is skipped until Claude Code refreshes the token itself.
- The endpoint is undocumented. It can change or disappear; the script then logs one line and the phone
  keeps showing the last status line data. Nothing else breaks.
- This is the same kind of access the community usage meters use. It is outside Anthropic's documented
  surface, which is why it is off by default and why the App Store build must not depend on it. The
  official request for the missing window is anthropics/claude-code#91920.

## UsageSnapshot v1

```json
{
  "version": 1,
  "source": "claude-code-statusline",
  "fetchedAt": "2026-09-03T22:07:12Z",
  "windows": [
    { "id": "session", "kind": "session", "name": "Session", "percent": 20.0,
      "resetsAt": "2026-09-04T01:00:00Z", "windowSeconds": 18000 },
    { "id": "week", "kind": "weekly", "name": "Week", "percent": 22.0,
      "resetsAt": "2026-09-05T16:00:00Z", "windowSeconds": 604800 }
  ]
}
```

- `percent` 0–100, `resetsAt` ISO 8601 UTC, `windowSeconds` the window length (start = reset − length).
- An optional third window `id: "model"` (name = model, e.g. "Fable") comes from the unofficial add-on
  above; readers show it as the third tile and carry `source` through untouched.
- A window Claude Code no longer reports (it drops one after its reset until new usage) is omitted;
  readers treat a missing window as 0 % with an unknown reset.
- The model-specific window is not available from the status line
  (feature request anthropics/claude-code#91920). Readers fall back to the
  weekly window for the third tile until another source provides it.
- The file is written atomically and only when a value changed, so watchers can react to every write.

Readers: the companion iOS app decodes this format; any tool can, it is plain JSON.

## Behaviour details

- Runs on every assistant message, `/compact`, permission-mode change and at each `resets_at`
  (Claude Code re-runs status lines when a window resets). Add `"refreshInterval"` to the
  `statusLine` settings if you want time-based refreshes while idle; the widgets do not need it.
- Requires Python 3 (ships with macOS Command Line Tools). No third-party modules.
- The default status line prints `5h 20% · 7d 22% · ctx 5%`. Pass your own command as the first
  argument to keep your layout; it receives the same JSON on stdin.
