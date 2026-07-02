"""Scrub secrets from URLs and bodies before anything is stored.

Redaction happens inside the recorder, so snapshots are safe by construction —
there is no separate "sanitize before export" step to forget.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "<redacted>"
_TRUNCATION_MARKER = "…(truncated)"


def redact_url(url: str, key_pattern: str) -> str:
    """Replace the values of query parameters whose *name* matches ``key_pattern``."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    key_re = re.compile(key_pattern, re.IGNORECASE)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    redacted = [(name, REDACTED if key_re.search(name) else value) for name, value in pairs]
    return urlunsplit(parts._replace(query=urlencode(redacted)))


def redact_body(text: str, key_pattern: str, secret_values: list[str], limit: int) -> str:
    """Scrub literal secrets, redact matching JSON keys, then truncate."""
    text = _replace_secrets(text, secret_values)
    try:
        parsed = json.loads(text)
    except ValueError:
        return truncate(text, limit)
    key_re = re.compile(key_pattern, re.IGNORECASE)
    return truncate(json.dumps(_scrub(parsed, key_re)), limit)


def redact_text(text: str, secret_values: list[str], limit: int) -> str:
    """Scrub literal secrets and truncate (for console text)."""
    return truncate(_replace_secrets(text, secret_values), limit)


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + _TRUNCATION_MARKER


def _replace_secrets(text: str, secret_values: list[str]) -> str:
    for secret in secret_values:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


def _scrub(value: Any, key_re: re.Pattern[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: REDACTED if key_re.search(key) else _scrub(item, key_re)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item, key_re) for item in value]
    return value
