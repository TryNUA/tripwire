from tripwire import (
    ConsoleLogEntry,
    Environment,
    NetworkLogEntry,
    ReportStep,
    TelemetrySnapshot,
    render_report,
)

GOLDEN = """\
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
| Ended at | https://myapp.dev/checkout/confirm |
| run_id | run-42 |

## Console errors
```
[error] Uncaught TypeError: Cannot read properties of undefined (reading 'total') (checkout.js)
```

## Failed network requests
| Method | URL | Status | Duration |
|---|---|---|---|
| POST | https://myapp.dev/api/orders | 500 | 342ms |
<details><summary>POST https://myapp.dev/api/orders</summary>

Response body:

```
{"error": "missing field: cart_id"}
```

</details>
"""


def full_snapshot():
    return TelemetrySnapshot(
        console=[
            ConsoleLogEntry(level="log", text="app booted"),
            ConsoleLogEntry(
                level="error",
                text="Uncaught TypeError: Cannot read properties of undefined (reading 'total')",
                source_url="checkout.js",
            ),
        ],
        network=[
            NetworkLogEntry(method="GET", url="https://myapp.dev/api/cart", status=200),
            NetworkLogEntry(
                method="POST",
                url="https://myapp.dev/api/orders",
                status=500,
                duration_ms=342.0,
                failed=True,
                response_body='{"error": "missing field: cart_id"}',
            ),
        ],
        environment=Environment(
            browser_version="Chrome/138.0.7204.49",
            viewport_width=1280,
            viewport_height=720,
            target_url="https://myapp.dev/checkout",
            final_url="https://myapp.dev/checkout/confirm",
            extra={"run_id": "run-42"},
        ),
    )


def test_golden_report():
    steps = [
        ReportStep(order=0, description="navigate https://myapp.dev/checkout", status="passed"),
        ReportStep(order=1, description="click #pay-button", status="passed"),
        ReportStep(
            order=2,
            description="click #confirm",
            status="failed",
            note="expected confirmation page, got error banner",
        ),
    ]
    assert render_report(steps, full_snapshot()) == GOLDEN


def test_empty_everything_renders_empty_string():
    assert render_report([], TelemetrySnapshot()) == ""


def test_sections_are_omitted_when_empty():
    report = render_report([], full_snapshot())
    assert "## Steps to reproduce" not in report
    assert "## Environment" in report


def test_console_highlight_cap_and_dropped_marker():
    snapshot = TelemetrySnapshot(
        console=[ConsoleLogEntry(level="error", text=f"e{i}") for i in range(5)],
        console_dropped=12,
    )
    report = render_report([], snapshot, max_console_highlights=2)
    assert "_3 earlier errors/warnings not shown._" in report
    assert "_+12 earlier console entries dropped._" in report
    assert "[error] e3" in report and "[error] e4" in report
    assert "[error] e1" not in report


def test_failed_request_cap():
    snapshot = TelemetrySnapshot(
        network=[
            NetworkLogEntry(method="GET", url=f"https://x.dev/{i}", status=500, failed=True)
            for i in range(4)
        ]
    )
    report = render_report([], snapshot, max_failed_requests=2)
    assert "_2 more failed requests not shown._" in report


def test_network_failure_without_status_shows_error_text():
    snapshot = TelemetrySnapshot(
        network=[
            NetworkLogEntry(
                method="GET",
                url="https://x.dev/api",
                failed=True,
                error_text="net::ERR_CONNECTION_REFUSED",
            )
        ]
    )
    report = render_report([], snapshot)
    assert "net::ERR_CONNECTION_REFUSED" in report
