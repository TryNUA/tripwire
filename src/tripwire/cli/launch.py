"""Find and launch a local Chromium with a CDP debug port."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

_MAC_APPS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]
_LINUX_NAMES = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "brave-browser",
]
_WINDOWS_RELATIVE = [
    r"Google\Chrome\Application\chrome.exe",
    r"Microsoft\Edge\Application\msedge.exe",
    r"BraveSoftware\Brave-Browser\Application\brave.exe",
]


def find_browser(browser_path: str = "") -> str:
    if browser_path:
        if Path(browser_path).exists():
            return browser_path
        raise FileNotFoundError(f"browser not found at {browser_path}")
    candidates: list[str] = []
    if sys.platform == "darwin":
        candidates = _MAC_APPS
    elif sys.platform.startswith("win"):
        env_vars = ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
        roots = [os.environ.get(v, "") for v in env_vars]
        candidates = [
            str(Path(root) / rel) for root in roots if root for rel in _WINDOWS_RELATIVE
        ]
    else:
        candidates = [shutil.which(name) or "" for name in _LINUX_NAMES]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise FileNotFoundError(
        "no Chromium-based browser found; pass --browser-path (tried: "
        + ", ".join(c for c in candidates if c)
        + ")"
    )


@dataclass
class LaunchedBrowser:
    process: subprocess.Popen
    ws_url: str
    http_url: str
    profile_dir: str

    @classmethod
    def launch(
        cls, port: int = 9222, browser_path: str = "", timeout: float = 30.0
    ) -> LaunchedBrowser:
        binary = find_browser(browser_path)
        # Temp profile is mandatory: Chrome 136+ refuses --remote-debugging-port
        # on the default profile.
        profile_dir = tempfile.mkdtemp(prefix="tripwire-chrome-")
        process = subprocess.Popen(
            [
                binary,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        ws_url = _read_ws_url(process, timeout)
        return cls(process, ws_url, f"http://127.0.0.1:{port}", profile_dir)

    def cleanup(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
        shutil.rmtree(self.profile_dir, ignore_errors=True)


def _read_ws_url(process: subprocess.Popen, timeout: float) -> str:
    """The `DevTools listening on ws://...` stderr line is the source of truth."""
    deadline = time.monotonic() + timeout
    lines: list[str] = []
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        line = process.stderr.readline()
        if not line:
            continue
        lines.append(line)
        if "DevTools listening on " in line:
            return line.split("DevTools listening on ", 1)[1].strip()
    process.terminate()
    raise RuntimeError("browser did not report a DevTools endpoint:\n" + "".join(lines[-20:]))
