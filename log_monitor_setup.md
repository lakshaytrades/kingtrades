# SataVector Log Monitoring Setup

## Overview

The VPS pushes a `live_status.txt` file to the `vps-status` branch on GitHub every 5 minutes
during market hours. An external monitoring agent reads this file via the GitHub API to detect
errors and take corrective action — no SSH access required.

---

## How It Works

`autodeploy.sh` runs every 5 minutes via systemd timer. At the end of each run, during market
hours (8 AM – 5 PM ET), it:

1. Reads today's trading log from `$INSTALL_DIR/logs/trading_YYYY-MM-DD.log`
2. Extracts the last 20 error/warning lines and last 15 signal/fill lines
3. Appends the last 30 raw log lines
4. Pushes the result as `live_status.txt` to the `vps-status` branch using low-level git
   plumbing (`hash-object` + `mktree` + `commit-tree` + `push refspec`) — this never touches
   the working tree of the code branch and cannot corrupt it

---

## live_status.txt Format

```
=== SataVector Live Status ===
Timestamp: YYYY-MM-DD HH:MM:SS ET
Log: /path/to/logs/trading_YYYY-MM-DD.log

=== ERRORS/WARNINGS (last 20) ===
<lines matching: ERROR|WARNING|CRITICAL|BLOCKED|REJECTED|failed|exception>
  — or —
none

=== SIGNALS/FILLS (last 15) ===
<lines matching: FILLED|SCALP|SIGNAL|BarCache refreshed|PAPER|BRACKET>
  — or —
none

=== LAST 30 LOG LINES ===
<raw tail of log file>
```

---

## How an External Agent Reads This File

Use the `mcp__github__get_file_contents` tool:

- **owner**: `lakshaytrades`
- **repo**: `kingtrades`
- **branch / ref**: `vps-status`
- **path**: `live_status.txt`

The file is updated every 5 minutes, so content is at most 5 minutes stale.

---

## Market Hours Gate

Status pushes only happen when ET hour is between 8 and 17 (inclusive), i.e. 8:00 AM – 5:59 PM ET.
Outside those hours the status push is skipped to conserve GitHub API quota.

---

## Safety Properties

- The `vps-status` branch is an independent branch — it shares no history with
  `claude/nse-momentum-groww-bot-hvkv9` and can never conflict with it.
- The git plumbing approach (no `git checkout`, no `git add`) means the working tree and
  HEAD of the code branch are never modified during a status push.
- All git push errors are swallowed with `|| true` so a GitHub outage cannot crash the
  deploy script or interrupt the running bot.
- The temporary `live_status.txt` file is always cleaned up after the push.

---

## Branch Bootstrap

The `vps-status` branch was seeded from `claude/nse-momentum-groww-bot-hvkv9` via
`mcp__github__create_branch`. On the first VPS run, if the branch does not yet exist the
push refspec will create it as an orphan containing only `live_status.txt`.
