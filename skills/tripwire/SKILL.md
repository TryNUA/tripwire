---
name: tripwire
description: >
  Flight recorder for browser work. Use whenever driving, testing, or debugging
  a web app in a browser: recording happens automatically — check
  `tripwire status` after browser tasks and save a rich bug report with
  `tripwire save` when something breaks.
---

# Tripwire flight recorder

Tripwire passively records console errors, uncaught exceptions, failed network
requests (with response bodies, secrets redacted), and every browser action
(clicks, typing, navigation) as reproduction steps. **Recording is automatic**:
this plugin's hooks start the watcher and the bundled browser tools launch a
Chromium it is attached to. Your job is only judgment — check, decide, save.

The `tripwire` CLI lives at `$CLAUDE_PLUGIN_DATA/venv/bin/tripwire` (also fine
from PATH if installed). State lives in `.tripwire/` in the project root.

## Rules

1. **Use tripwire's browser tools.** Other browser tools are denied by the
   plugin's gate with a redirect — that's expected behavior, not an error;
   switch to the tripwire ones. If you write a Playwright script instead of
   using the tools, connect it to the recorded browser:
   `chromium.connect_over_cdp("http://127.0.0.1:9222")` — do not `launch()`.
2. **Anomalies find you.** After every browser action, a hook checks the
   recorder and injects new exceptions, console errors, and failed requests
   into your context. You can still run `tripwire status` manually anytime
   (cheap, read-only).
3. **When there is a real bug** (from an injected anomaly, or something you
   observed — broken layout, dead button, wrong result), save a report:

   ```
   tripwire save --summary "Checkout 500 on confirm — expected confirmation page, got error banner"
   ```

   It prints the path of a ready-to-file markdown report. Read that file and
   show the report to the user. Don't re-save a bug you already reported.
4. Anomalies that are clearly noise (favicon 404s, third-party analytics
   errors) don't warrant a report — mention them only if asked.
