#!/bin/bash
# PostToolUse hook on browser_* tools: after every browser action the harness
# runs `tripwire status` and pushes any NEW anomalies straight into the
# model's context — the agent never has to remember to check. Emits nothing
# when clean; silent (allow) on any error.
set -u

TW="$CLAUDE_PLUGIN_DATA/venv/bin/tripwire"
[ -x "$TW" ] || exit 0
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
[ -f .tripwire/session.json ] || exit 0

STATUS=$("$TW" status 2>/dev/null) || exit 0
case "$STATUS" in
  *"no new anomalies"*) exit 0 ;;
  "") exit 0 ;;
esac

"$CLAUDE_PLUGIN_DATA/venv/bin/python" -c '
import json, sys
status, tw = sys.argv[1], sys.argv[2]
context = (
    "tripwire recorded new anomalies during that browser action:\n"
    + status
    + f"\n(tripwire CLI: {tw})"
)
print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": context,
}}))
' "$STATUS" "$TW" 2>/dev/null

exit 0
