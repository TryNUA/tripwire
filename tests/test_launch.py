import subprocess
import sys

import pytest

from tripwire.cli.launch import LaunchedBrowser, _read_ws_url, find_browser


class TestFindBrowser:
    def test_explicit_path_wins(self, tmp_path):
        fake = tmp_path / "chrome"
        fake.touch()
        assert find_browser(str(fake)) == str(fake)

    def test_explicit_path_missing_raises(self):
        with pytest.raises(FileNotFoundError, match="not found at"):
            find_browser("/nope/chrome")

    def test_no_browser_anywhere_raises(self, monkeypatch):
        monkeypatch.setattr("tripwire.cli.launch.Path.exists", lambda self: False)
        monkeypatch.setattr("tripwire.cli.launch.shutil.which", lambda name: None)
        with pytest.raises(FileNotFoundError, match="--browser-path"):
            find_browser()


def fake_browser(tmp_path, script_body):
    script = tmp_path / "fake_browser.py"
    script.write_text(script_body)
    return subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


class TestReadWsUrl:
    def test_parses_devtools_line(self, tmp_path):
        process = fake_browser(
            tmp_path,
            'import sys, time\n'
            'print("noise", file=sys.stderr)\n'
            'print("DevTools listening on ws://127.0.0.1:9222/devtools/browser/abc",'
            ' file=sys.stderr, flush=True)\n'
            'time.sleep(5)\n',
        )
        try:
            assert _read_ws_url(process, timeout=10) == "ws://127.0.0.1:9222/devtools/browser/abc"
        finally:
            process.terminate()

    def test_exit_without_endpoint_raises_with_stderr(self, tmp_path):
        process = fake_browser(
            tmp_path,
            'import sys\nprint("cannot open display", file=sys.stderr, flush=True)\n',
        )
        with pytest.raises(RuntimeError, match="cannot open display"):
            _read_ws_url(process, timeout=10)


class TestCleanup:
    def test_cleanup_terminates_and_removes_profile(self, tmp_path):
        process = fake_browser(tmp_path, "import time\ntime.sleep(60)\n")
        profile = tmp_path / "profile"
        profile.mkdir()
        browser = LaunchedBrowser(process, "ws://x", "http://x", str(profile))
        browser.cleanup()
        assert process.poll() is not None
        assert not profile.exists()
