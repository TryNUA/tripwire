from tripwire.redaction import REDACTED, redact_body, redact_text, redact_url

PATTERN = r"token|key|secret|password|code|session|auth"


class TestRedactUrl:
    def test_matching_query_params_are_redacted(self):
        url = "https://api.example.com/v1?api_key=sk-123&page=2&access_token=abc"
        result = redact_url(url, PATTERN)
        assert "sk-123" not in result
        assert "abc" not in result
        assert "page=2" in result

    def test_match_is_case_insensitive(self):
        result = redact_url("https://x.dev/?Authorization=Bearer+xyz", PATTERN)
        assert "xyz" not in result

    def test_url_without_query_is_untouched(self):
        url = "https://x.dev/path"
        assert redact_url(url, PATTERN) == url


class TestRedactBody:
    def test_json_keys_are_scrubbed_recursively(self):
        body = '{"user": "a", "api_key": "sk-1", "nested": {"password": "p", "ok": 1}}'
        result = redact_body(body, PATTERN, [], 4096)
        assert "sk-1" not in result
        assert '"p"' not in result
        assert '"ok": 1' in result

    def test_literal_secret_values_are_scrubbed_anywhere(self):
        result = redact_body("the secret is hunter2 ok", PATTERN, ["hunter2"], 4096)
        assert "hunter2" not in result
        assert REDACTED in result

    def test_non_json_body_passes_through_with_secrets_scrubbed(self):
        result = redact_body("<html>hunter2</html>", PATTERN, ["hunter2"], 4096)
        assert result == f"<html>{REDACTED}</html>"

    def test_truncation(self):
        result = redact_body("x" * 5000, PATTERN, [], 100)
        assert len(result) < 5000
        assert result.endswith("…(truncated)")

    def test_json_arrays_are_scrubbed(self):
        body = '[{"token": "t1"}, {"token": "t2"}]'
        result = redact_body(body, PATTERN, [], 4096)
        assert "t1" not in result and "t2" not in result


class TestRedactText:
    def test_scrubs_and_truncates(self):
        result = redact_text("hunter2 " + "y" * 100, ["hunter2"], 50)
        assert "hunter2" not in result
        assert result.endswith("…(truncated)")
