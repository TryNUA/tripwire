#!/bin/bash
# PreToolUse hook, run by the Claude Code harness — the model is not involved.
# Fires on browser_* MCP tools AND on Bash (for playwright test/script runs).
#
#   Bash + browser-ish command      → ensure watcher is running, allow
#   tripwire's own browser tools    → ensure watcher is running, allow
#   any other browser_* tool        → deny with a redirect to the recorded
#                                     browser (bypass: TRIPWIRE_ALLOW_UNRECORDED=1)
#
# "Ensure" validates the watcher, not just liveness: a live watcher pointed at
# the wrong CDP endpoint (stale --launch session, old port) is killed and
# replaced — its own startup guard would otherwise block the right one forever.
# A bug here must never break the user's tools: on any failure, allow (exit 0).
set -u

CDP_URL="http://127.0.0.1:9222"
INPUT=$(cat 2>/dev/null || echo '{}')

# Bash fast path: only commands that drive a browser matter; everything else
# exits immediately without spawning python.
case "$INPUT" in
  *'"tool_name":"Bash"'* | *'"tool_name": "Bash"'*)
    case "$INPUT" in
      *playwright* | *puppeteer* | *chromium* | *remote-debugging-port*) ;;
      *) exit 0 ;;
    esac
    GATE=0 # ensure only — never deny Bash
    ;;
  *)
    GATE=1
    ;;
esac

PY="$CLAUDE_PLUGIN_DATA/venv/bin/python"
[ -x "$PY" ] || PY=python3

if [ "$GATE" = "1" ]; then
  TOOL=$("$PY" -c 'import sys, json; print(json.loads(sys.argv[1]).get("tool_name", ""))' \
    "$INPUT" 2>/dev/null || echo "")
  case "$TOOL" in
    "")
      exit 0
      ;;
    *tripwire*) ;; # recorded browser — fall through to ensure
    *)
      if [ "${TRIPWIRE_ALLOW_UNRECORDED:-0}" != "1" ]; then
        cat <<'JSON'
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "This browser is not recorded by tripwire. Use the tripwire plugin's browser tools instead (the same browser_* tools, served by the tripwire playwright server) so console errors, network failures, and repro steps are captured. To deliberately use an unrecorded browser, set TRIPWIRE_ALLOW_UNRECORDED=1."}}
JSON
      fi
      exit 0
      ;;
  esac
fi

# ------------------------------------------------------------- ensure watcher

TW="$CLAUDE_PLUGIN_DATA/venv/bin/tripwire"
[ -x "$TW" ] || exit 0
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0

SESSION=".tripwire/session.json"
if [ -f "$SESSION" ]; then
  read -r PID URL <<<"$("$PY" -c "
import json
d = json.load(open('$SESSION'))
print(d.get('pid', 0), d.get('cdp_http_url', ''))" 2>/dev/null || echo "0 -")"
  if [ "${PID:-0}" -gt 0 ] 2>/dev/null && kill -0 "$PID" 2>/dev/null; then
    if [ "$URL" = "$CDP_URL" ]; then
      exit 0 # correct watcher already running
    fi
    # Live watcher on the wrong endpoint: kill and replace.
    kill "$PID" 2>/dev/null
    sleep 1
    kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null
    rm -f "$SESSION"
  fi
fi

nohup "$TW" watch --cdp "$CDP_URL" >>"$CLAUDE_PLUGIN_DATA/watch.log" 2>&1 &

exit 0
