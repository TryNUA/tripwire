#!/bin/bash
# SessionStart hook: install tripwire[cli] into the plugin's persistent data
# dir once, and again when the plugin updates. The plugin directory IS the
# tripwire repo, so we install from it directly — no network fetch of tripwire
# code, only deps from PyPI. Must never fail the session: always exit 0.
set -u

VENV="$CLAUDE_PLUGIN_DATA/venv"
STAMP="$CLAUDE_PLUGIN_DATA/installed-plugin.json"
MANIFEST="$CLAUDE_PLUGIN_ROOT/.claude-plugin/plugin.json"

if [ -x "$VENV/bin/tripwire" ] && diff -q "$MANIFEST" "$STAMP" >/dev/null 2>&1; then
  exit 0
fi

{
  python3 -m venv "$VENV" &&
    "$VENV/bin/pip" install --quiet "${CLAUDE_PLUGIN_ROOT}[cli]" &&
    cp "$MANIFEST" "$STAMP"
} >>"$CLAUDE_PLUGIN_DATA/bootstrap.log" 2>&1 || rm -f "$STAMP"

exit 0
