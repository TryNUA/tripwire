#!/bin/bash
# PreToolUse hook on browser_* tools, run by the Claude Code harness before
# every matching tool call — the model is not involved. Two jobs:
#   1. tripwire's own browser tools → make sure the watcher is running, allow
#   2. any other browser tool → deny with a redirect (that browser has no
#      debug port, so tripwire cannot record it); bypass with
#      TRIPWIRE_ALLOW_UNRECORDED=1
# A bug here must never break browsing: on any failure, allow (exit 0).
set -u

INPUT=$(cat 2>/dev/null || echo '{}')
PY="$CLAUDE_PLUGIN_DATA/venv/bin/python"
[ -x "$PY" ] || PY=python3
TOOL=$("$PY" -c 'import sys, json; print(json.loads(sys.argv[1]).get("tool_name", ""))' \
  "$INPUT" 2>/dev/null || echo "")

case "$TOOL" in
  "")
    exit 0
    ;;
  *tripwire*)
    : # recorded browser — fall through and ensure the watcher is up
    ;;
  *)
    if [ "${TRIPWIRE_ALLOW_UNRECORDED:-0}" != "1" ]; then
      cat <<'JSON'
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "This browser is not recorded by tripwire. Use the tripwire plugin's browser tools instead (the same browser_* tools, served by the tripwire playwright server) so console errors, network failures, and repro steps are captured. To deliberately use an unrecorded browser, set TRIPWIRE_ALLOW_UNRECORDED=1."}}
JSON
    fi
    exit 0
    ;;
esac

TW="$CLAUDE_PLUGIN_DATA/venv/bin/tripwire"
[ -x "$TW" ] || exit 0
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0

SESSION=".tripwire/session.json"
if [ -f "$SESSION" ]; then
  PID=$("$PY" -c "import json; print(json.load(open('$SESSION')).get('pid', 0))" 2>/dev/null || echo 0)
  if [ "${PID:-0}" -gt 0 ] 2>/dev/null && kill -0 "$PID" 2>/dev/null; then
    exit 0
  fi
fi

nohup "$TW" watch --cdp http://127.0.0.1:9222 >>"$CLAUDE_PLUGIN_DATA/watch.log" 2>&1 &

exit 0
