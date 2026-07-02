# tripwire

**Rich bug reports for AI browser agents.**

When your agent hits a bug while driving a web app, tripwire gives you the
information a developer needs to fix it: console logs, network requests,
repro steps, and environment info. It captures this in the background while
the agent works, redacts secrets, and renders it as a markdown issue you can
file directly.

Built for agents driving Chromium via CDP: Playwright,
[browser-use](https://github.com/browser-use/browser-use), Puppeteer bridges,
or any DevTools-protocol client. The only dependency is `pydantic`.

> ⚠️ Pre-1.0: the API may change. Pin a commit.

## Why

Agents hit bugs often, then file issues like *"checkout seems broken"*. The
details a developer needs (the uncaught exception, the 500 response body,
what the agent did in step 4) were in the browser but never saved.
Browser-extension capture tools do this for humans; nothing did it for
agents. Since your agent already controls the browser, tripwire records this
information as the agent works.

## Install

```bash
pip install git+https://github.com/TryNUA/tripwire  # PyPI soon
```

## Quickstart (Playwright)

Three steps: attach, step, report.

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

**Attach before the bug happens.** Console and network history cannot be
recovered after the fact. Call `attach()` when the page is created and
generate the report when a failure happens.

Async Playwright: use `attach_async(recorder, page)` and call
`await fetch_failed_bodies(recorder, cdp)` before rendering the report.

## Quickstart (browser-use)

Works with the CDP-native [browser-use](https://github.com/browser-use/browser-use)
(≥ 0.5). `attach()` wires the recorder to the session's CDP client;
`step_hooks()` turns each agent step into a repro step, described from what
the agent actually did:

```python
from browser_use import Agent
from tripwire import TelemetryRecorder
from tripwire.integrations.browser_use import attach, step_hooks

agent = Agent(task="Buy the blue socks", llm=llm)
recorder = TelemetryRecorder()

tw = await attach(recorder, agent.browser_session)
on_step_start, on_step_end = step_hooks(recorder)
history = await agent.run(on_step_start=on_step_start, on_step_end=on_step_end)

if not history.is_successful():
    await tw.fetch_failed_bodies()              # response bodies + final URL
    file_issue(body=recorder.report())
```

Handlers are registered on the root CDP client, so events from every tab are
captured. Telemetry never interferes with the run: every hook and handler
swallows its own errors.

## With a Claude tool-use loop

Each tool call Claude makes is one repro step. Give Claude a `report_bug`
tool and the report is generated automatically:

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

The report also includes collapsible request/response bodies for the failed
requests.

## Redaction

Reports leave your machine, so tripwire is conservative about what it stores:

- **Request/response headers are never stored.** `Authorization` and `Cookie`
  cannot leak because they are never read.
- Bodies are kept **only for failed requests** (status ≥ 400 or network
  error), capped at 4 KB. Values of keys matching
  `token|key|secret|password|code|session|auth` are scrubbed from bodies and
  URL query strings.
- Pass your own secrets to scrub anywhere:
  `RecorderConfig(extra_secret_values=[MY_PASSWORD])`.
- Ring buffers cap memory (1,000 console / 2,000 network entries; dropped
  counts reported).

## Any CDP client (advanced)

The Playwright adapter is a thin wrapper; it does not even import playwright.
The core consumes raw CDP event dicts, so any DevTools-protocol client works.
Register the six handlers yourself:

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

If you are not using the `step()` context manager, advance
`recorder.current_step_index` from your agent's step callback.

## Scope

- **Chromium/CDP only** (Chrome, Edge, Brave, headless Chromium). Firefox and
  Safari do not support CDP. Contributions for a WebDriver BiDi adapter are
  welcome.
- **You file the issue.** tripwire renders the report; you post it to your
  issue tracker. This keeps `pydantic` as the only dependency.

## Roadmap

- PyPI release once the API is stable
- WebDriver BiDi adapter (cross-browser)
- `tripwire-mcp`: an MCP server wrapping a browser, for agents (Claude Code,
  Cursor) that do not control the browser process

## License

Apache-2.0. Built by the team at Nua. We use tripwire in production to
auto-file bug reports from our testing agents.
