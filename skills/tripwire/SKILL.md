---
name: tripwire
description: >
  Flight recorder for browser work. Use whenever driving, testing, or debugging
  a web app in a Chromium browser (Playwright tools, CDP, or scripts you write):
  start the tripwire watcher before browsing, check status after tasks, and save
  a rich bug report when something breaks.
---

# Tripwire flight recorder

Tripwire passively records console errors, uncaught exceptions, failed network
requests (with response bodies, secrets redacted), and every browser action
(clicks, typing, navigation) as reproduction steps. You decide when a capture
is worth saving as a report.

All commands self-bootstrap — no install needed (falls back to `pipx run` if
`uvx` is missing):

```
alias tw="uvx --from 'tripwire[cli] @ git+https://github.com/TryNUA/tripwire' tripwire"
```

## Workflow

1. **Before any browser work**, start the watcher in the background:
   - `tw watch --launch` — launches a Chromium and prints its CDP endpoint.
     Drive that browser: point Playwright MCP at it with `--cdp-endpoint`, or
     connect scripts via `connect_over_cdp("http://127.0.0.1:9222")`.
   - `tw watch --cdp http://127.0.0.1:9222` — attach to a browser you already
     launched with `--remote-debugging-port`.

   Start it BEFORE reproducing anything — history is not retroactive. Leave it
   running for the whole session.
2. **Browse normally.** Actions, console, and network record automatically
   into `.tripwire/`.
3. **After each task** — and whenever behavior seems off — run `tw status`.
   It lists new exceptions, console errors, and failed requests since your
   last check. It is cheap and read-only; run it liberally.
4. **When there is a real bug** (from status, or something you observed —
   broken layout, dead button, wrong result), save a report:

   ```
   tw save --summary "Checkout 500 on confirm — expected confirmation page, got error banner"
   ```

   It prints the path of a ready-to-file markdown report. Read that file and
   show the report to the user. Don't re-save a bug you already reported.
