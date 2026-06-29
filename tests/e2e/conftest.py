"""Shared fixtures and utilities for Themarr E2E smoke tests.

Uses Playwright's route interception to mock Plex/Jellyfin-dependent API
endpoints while letting auth endpoints (/api/init, /api/auth/*) pass through
to the real Flask process.  Each fixture spins up an isolated Flask server on
a dynamically-allocated port so tests can run in parallel without port clashes.
"""
import http.client
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Mock API payloads
# ---------------------------------------------------------------------------

_MOCK_CACHE_READY = {
    "ready": True,
    "sections_total": 1,
    "sections_completed": 1,
}

_MOCK_STATUS = {
    "connected": True,
    "server_name": "Test Plex Server",
    "version": "1.0.0",
    "plex": {
        "url_configured": True,
        "connected": True,
        "server_name": "Test Plex Server",
        "version": "1.0.0",
        "error": None,
    },
    "jellyfin": {
        "url_configured": False,
        "connected": False,
        "server_name": None,
        "version": None,
        "error": None,
    },
}

_MOCK_LIBRARIES = [
    {"id": 1, "key": 1, "title": "TV Shows", "type": "show", "totalSize": 3},
]

_MOCK_JELLYFIN_LIBRARIES: list = []

_MOCK_TV_ITEMS = [
    {
        "ratingKey": 1,
        "title": "Breaking Bad",
        "year": 2008,
        "thumb": "/library/metadata/1/thumb",
        "type": "show",
        "has_plex_theme": True,
        "has_local_theme": True,
        "theme_size": 245760,
        "local_path": "/tv/Breaking_Bad",
    },
    {
        "ratingKey": 2,
        "title": "Chernobyl",
        "year": 2019,
        "thumb": "/library/metadata/2/thumb",
        "type": "show",
        "has_plex_theme": True,
        "has_local_theme": False,
        "theme_size": 0,
        "local_path": "/tv/Chernobyl",
    },
]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _free_port() -> int:
    """Return an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return sock.getsockname()[1]


def _wait_for_health(port: int, timeout: int = 20) -> None:
    """Block until ``GET /health`` returns HTTP 200 or *timeout* seconds pass."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            conn.request("GET", "/health")
            resp = conn.getresponse()
            conn.close()
            if resp.status == 200:
                return
        except Exception:
            pass
        time.sleep(0.3)
    raise RuntimeError(
        f"Flask app did not become healthy on port {port} within {timeout}s"
    )


def _launch_flask(extra_env: dict) -> tuple[subprocess.Popen, int]:
    """Start ``web_app.py`` on a free port and wait until healthy.

    Returns the (process, port) pair.  The caller is responsible for
    terminating the process.
    """
    port = _free_port()
    env: dict = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": str(REPO_ROOT),
        "FLASK_DEBUG": "false",
        "PORT": str(port),
    }
    env.update(extra_env)
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "web_app.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO_ROOT),
    )
    try:
        _wait_for_health(port)
    except RuntimeError:
        proc.terminate()
        raise
    return proc, port


# ---------------------------------------------------------------------------
# Session-scoped server fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def no_auth_base_url():
    """Flask with ``DISABLE_AUTH=true``; no login required."""
    proc, port = _launch_flask({"DISABLE_AUTH": "true"})
    yield f"http://127.0.0.1:{port}"
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


@pytest.fixture(scope="session")
def credentials_base_url():
    """Flask with ``AUTH_USERNAME`` / ``AUTH_PASSWORD`` set."""
    proc, port = _launch_flask({
        "AUTH_USERNAME": "testuser",
        "AUTH_PASSWORD": "testpass123",
    })
    yield f"http://127.0.0.1:{port}"
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


# ---------------------------------------------------------------------------
# Route-interception helper
# ---------------------------------------------------------------------------

def setup_api_mocks(page, base_url: str) -> None:
    """Intercept Plex/Jellyfin-dependent ``/api/*`` routes with mock data.

    Auth endpoints (``/api/init``, ``/api/auth/*``) are intentionally *not*
    intercepted so the real Flask process handles them.
    """

    def _handler(route, request) -> None:
        url = request.url
        if "/api/cache/status" in url:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_MOCK_CACHE_READY),
            )
        elif "/api/status" in url:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_MOCK_STATUS),
            )
        elif "/api/libraries/1/items" in url:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({
                    "items": _MOCK_TV_ITEMS,
                    "pagination": {
                        "page": 1,
                        "page_size": 200,
                        "total": len(_MOCK_TV_ITEMS),
                        "has_more": False,
                    },
                }),
            )
        elif "/api/libraries" in url and "/items" not in url:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_MOCK_LIBRARIES),
            )
        else:
            route.continue_()

    page.route(f"{base_url}/api/**", _handler)
