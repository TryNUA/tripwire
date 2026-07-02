# tripwire

**Rich bug reports for AI browser agents.**

When your agent hits a bug while driving a web app, tripwire gives you
everything a developer needs to fix it — console logs, network requests,
repro steps, environment info — captured passively while the agent works,
redacted, and rendered as a ready-to-file markdown issue.

Built for agents driving Chromium via CDP: Playwright,
[browser-use](https://github.com/browser-use/browser-use), Puppeteer bridges,
or any DevTools-protocol client. Zero dependencies beyond `pydantic`.

> ⚠️ Pre-1.0: the API is still settling. Pin a commit.

## Why

Agents encounter bugs constantly — and then file issues like *"checkout seems
broken"*. The forensics a human developer needs (the uncaught exception, the
500 response body, what the agent did in step 4) were all right there in the
browser and got thrown away. Browser-extension capture tools solve this for
humans; nothing solved it for agents. Your agent already owns the browser —
tripwire just keeps the evidence.

## Install

```bash
pip install git+https://github.com/TryNUA/tripwire  # PyPI soon
```

## Quickstart (Playwright)

Three touchpoints: attach, step, report.

```python
from playwright.sync_api import sync_playwright
from tripwire import TelemetryRecorder
from tripwire.integrations.playwright import attach

recorder = TelemetryRecorder()

with sync_playwright() as p:
    page = p.chromium.launch().new_page()
    attach(recorder, page)                      # capture console + network from here on

    for action in my_agent.actions(page):       # your agent loop
        with recorder.step(action.description): # attributes telemetry to this step
            action.execute(page)

    if my_agent.found_bug:
        markdown = recorder.report()            # ready-to-file issue body
        snapshot = recorder.snapshot()          # full JSON artifact
        file_issue(title=my_agent.bug_summary,  # your Linear/GitHub/Jira call
                   body=markdown,
                   attachment=snapshot.model_dump_json())
```

**Attach before the bug happens.** Console and network history can't be
reconstructed retroactively — `attach()` at page creation, report at failure.

Async Playwright: use `attach_async(recorder, page)` and call
`await fetch_failed_bodies(recorder, cdp)` before rendering the report.

## With a Claude tool-use loop

Each tool call Claude makes is one repro step; give Claude a `report_bug` tool
and the report writes itself:

```python
for block in response.content:
    if block.type != "tool_use":
        continue
    if block.name == "report_bug":
        file_issue(block.input["summary"], recorder.report())
        continue
    with recorder.step(f"{block.name} {block.input}"):
        run_browser_tool(page, block)
```

## What a report looks like

```markdown
## Steps to reproduce
1. navigate https://myapp.dev/checkout
2. click #pay-button
3. **click #confirm — FAILED** — expected confirmation page, got error banner

## Environment
| | |
|---|---|
| Browser | Chrome/138.0.7204.49 |
| Viewport | 1280×720 |
| Started at | https://myapp.dev/checkout |

## Console errors
[error] Uncaught TypeError: Cannot read properties of undefined (reading 'total')

## Failed network requests
| Method | URL | Status | Duration |
|---|---|---|---|
| POST | /api/orders | 500 | 342ms |
```

…plus collapsible request/response bodies for the failures.

## Redaction, by construction

Reports leave your machine, so tripwire is conservative about what it stores:

- **Request/response headers are never stored** — `Authorization` and `Cookie`
  can't leak because they're never read.
- Bodies are kept **only for failed requests** (status ≥ 400 or network error),
  capped at 4 KB, with values of keys matching
  `token|key|secret|password|code|session|auth` scrubbed — in bodies and URL
  query strings.
- Pass your own secrets to scrub anywhere:
  `RecorderConfig(extra_secret_values=[MY_PASSWORD])`.
- Ring buffers cap memory (1,000 console / 2,000 network entries; dropped
  counts reported).

## Any CDP client (advanced)

The Playwright adapter is sugar — it never even imports playwright. The core
consumes raw CDP event dicts, so any DevTools-protocol client works; register
the six handlers yourself:

```python
client.register.Runtime.consoleAPICalled(recorder.on_console_api_called)
client.register.Runtime.exceptionThrown(recorder.on_exception_thrown)
client.register.Network.requestWillBeSent(recorder.on_request_will_be_sent)
client.register.Network.responseReceived(recorder.on_response_received)
client.register.Network.loadingFinished(recorder.on_loading_finished)
client.register.Network.loadingFailed(recorder.on_loading_failed)
# on failure: recorder.pending_failed_request_ids() → Network.getResponseBody
#             → recorder.set_response_body(request_id, body)
```

Advance `recorder.current_step_index` from your agent's step callback if you're
not using the `step()` context manager.

## Scope

- **Chromium/CDP only** (Chrome, Edge, Brave, headless Chromium). Firefox and
  Safari don't speak CDP; a WebDriver BiDi adapter is a welcome contribution.
- **Filing is your job** — tripwire renders the report; you post it wherever
  issues live. Keeps the dependency footprint at exactly `pydantic`.

## Roadmap

- PyPI release once the API survives real-world runs
- WebDriver BiDi adapter (cross-browser)
- `tripwire-mcp`: an MCP server wrapping a browser, for agents (Claude Code,
  Cursor) that don't own the browser process

## License

Apache-2.0. Built by the team at Nua — we use tripwire in production to
auto-file bug reports from our testing agents.
