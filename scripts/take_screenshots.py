#!/usr/bin/env python3
"""
Automated screenshot script for Themarr web UI.

Uses Playwright to launch a real browser, intercepts all /api/* calls with
mock data, and captures screenshots of every major UI state.  Screenshots are
written to the screenshots/ directory in the repo root.

Usage:
    pip install playwright
    playwright install chromium
    python3 scripts/take_screenshots.py

The script requires no running Plex server — all API responses are mocked.
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Locate repo root (one level above this script)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
SCREENSHOTS_DIR = REPO_ROOT / "screenshots"

# ---------------------------------------------------------------------------
# Mock API data that the browser intercept layer will return
# ---------------------------------------------------------------------------
MOCK_STATUS = {
    "connected": True,
    "server_name": "My Plex Server",
    "version": "1.32.0",
}

MOCK_LIBRARIES = [
    {"id": 1, "title": "TV Shows", "type": "show", "totalSize": 42},
    {"id": 2, "title": "Movies",   "type": "movie", "totalSize": 18},
]

# 12 realistic-looking TV show entries
_TV_ITEMS_RAW = [
    ("Breaking Bad",      2008, True,  True),
    ("Chernobyl",         2019, True,  True),
    ("Dark",              2017, True,  True),
    ("Game of Thrones",   2011, True,  False),
    ("House of the Dragon", 2022, False, False),
    ("Mindhunter",        2017, True,  True),
    ("Ozark",             2017, True,  False),
    ("Peaky Blinders",    2013, True,  True),
    ("Succession",        2018, False, False),
    ("The Bear",          2022, True,  True),
    ("The Crown",         2016, True,  False),
    ("Yellowjackets",     2021, False, False),
]

MOCK_TV_ITEMS = [
    {
        "ratingKey": 100 + i,
        "title": title,
        "year": year,
        "thumb": None,
        "type": "show",
        "has_plex_theme": has_plex,
        "has_local_theme": has_local,
        "theme_size": 245760 if has_local else 0,
        "local_path": f"/tv/{title.replace(' ', '_')}",
    }
    for i, (title, year, has_plex, has_local) in enumerate(_TV_ITEMS_RAW)
]

# ---------------------------------------------------------------------------
# Helper: start the Flask dev-server in a background thread
# ---------------------------------------------------------------------------

def _start_flask(port: int = 18080) -> subprocess.Popen:
    """Start web_app.py on *port* as a subprocess and return the Popen handle."""
    env = os.environ.copy()
    env.update({
        "PLEX_URL":   "http://127.0.0.1:19999",  # non-existent — API is mocked
        "PLEX_TOKEN": "mock_token",
        "TV_PATH":    "/tv",
        "MOVIES_PATH": "/movies",
        "DEFAULT_THEME": "dark",
        "FLASK_DEBUG": "0",
        "PORT": str(port),
    })
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "web_app.py")],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Give Flask a moment to start
    time.sleep(2)
    return proc


# ---------------------------------------------------------------------------
# Main screenshot logic
# ---------------------------------------------------------------------------

def take_screenshots(base_url: str = "http://127.0.0.1:18080") -> None:
    try:
        from playwright.sync_api import sync_playwright, Route, Request
    except ImportError:
        print("ERROR: playwright is not installed.")
        print("  pip install playwright && playwright install chromium")
        sys.exit(1)

    SCREENSHOTS_DIR.mkdir(exist_ok=True)

    def route_handler(route: Route, request: Request) -> None:
        """Intercept /api/* requests and return mock JSON."""
        url = request.url
        if "/api/status" in url:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(MOCK_STATUS))
        elif "/api/libraries" in url and "/items" not in url:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(MOCK_LIBRARIES))
        elif "/api/libraries/1/items" in url:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(MOCK_TV_ITEMS))
        elif "/api/libraries/2/items" in url:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps([]))
        elif "/api/poster/" in url:
            # Return a transparent 1×1 PNG so poster elements don't break layout
            import base64
            png_1x1 = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
            )
            route.fulfill(status=200, content_type="image/png", body=png_1x1)
        else:
            route.continue_()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()

        def new_page(theme: str = "dark") -> object:
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.route("**/api/**", route_handler)
            # Pre-set theme in localStorage before navigating
            page.goto(base_url)
            page.evaluate(f"localStorage.setItem('themarr-theme', '{theme}')")
            page.reload()
            page.wait_for_load_state("networkidle")
            return page

        # ------------------------------------------------------------------
        # 01 — Welcome screen (dark)
        # ------------------------------------------------------------------
        page = new_page("dark")
        page.wait_for_selector("#welcome-screen:not(.hidden)", timeout=5000)
        page.screenshot(path=str(SCREENSHOTS_DIR / "01_welcome.png"))
        print("  ✓ 01_welcome.png")

        # ------------------------------------------------------------------
        # 02 — TV library, poster view (dark)
        # ------------------------------------------------------------------
        page.click("text=TV Shows")
        page.wait_for_timeout(400)
        page.click("#view-btn-grid")
        page.wait_for_selector(".items-grid", timeout=5000)
        page.wait_for_timeout(400)
        page.screenshot(path=str(SCREENSHOTS_DIR / "02_tv_library_poster.png"))
        print("  ✓ 02_tv_library_poster.png")

        # ------------------------------------------------------------------
        # 03 — TV library, list view (dark)
        # ------------------------------------------------------------------
        page.click("#view-btn-list")
        page.wait_for_selector(".items-list", timeout=3000)
        page.wait_for_timeout(300)
        page.screenshot(path=str(SCREENSHOTS_DIR / "03_tv_library_list.png"))
        print("  ✓ 03_tv_library_list.png")

        # ------------------------------------------------------------------
        # 04 — Filter: No Theme (list view, dark)
        # ------------------------------------------------------------------
        page.click("#filter-no-theme")
        page.wait_for_timeout(300)
        page.screenshot(path=str(SCREENSHOTS_DIR / "04_filter_no_theme_list.png"))
        print("  ✓ 04_filter_no_theme_list.png")

        # ------------------------------------------------------------------
        # 05 — Bulk select, poster view (dark)
        # ------------------------------------------------------------------
        page.click("#filter-all")
        page.click("#view-btn-grid")
        page.wait_for_selector(".items-grid", timeout=3000)
        page.wait_for_timeout(300)
        # Select first 3 items
        cards = page.query_selector_all(".item-card .item-select-wrap input[type='checkbox']")
        for card in cards[:3]:
            card.check()
        page.wait_for_timeout(300)
        page.screenshot(path=str(SCREENSHOTS_DIR / "05_bulk_select_poster.png"))
        print("  ✓ 05_bulk_select_poster.png")

        # ------------------------------------------------------------------
        # 06 — Search (dark)
        # ------------------------------------------------------------------
        for card in cards[:3]:
            card.uncheck()
        page.fill("#search-input", "break")
        page.wait_for_timeout(300)
        page.screenshot(path=str(SCREENSHOTS_DIR / "06_search.png"))
        print("  ✓ 06_search.png")

        # ------------------------------------------------------------------
        # 07 — Bulk select, list view (dark)
        # ------------------------------------------------------------------
        page.fill("#search-input", "")
        page.click("#view-btn-list")
        page.wait_for_selector(".items-list", timeout=3000)
        page.wait_for_timeout(300)
        rows = page.query_selector_all(".item-row input[type='checkbox']")
        for row in rows[:4]:
            row.check()
        page.wait_for_timeout(300)
        page.screenshot(path=str(SCREENSHOTS_DIR / "07_bulk_select_list.png"))
        print("  ✓ 07_bulk_select_list.png")

        page.close()

        # ------------------------------------------------------------------
        # 08 — Welcome screen (light)
        # ------------------------------------------------------------------
        page = new_page("light")
        page.wait_for_selector("#welcome-screen:not(.hidden)", timeout=5000)
        page.screenshot(path=str(SCREENSHOTS_DIR / "08_welcome_light.png"))
        print("  ✓ 08_welcome_light.png")

        # ------------------------------------------------------------------
        # 09 — TV library, poster view (light)
        # ------------------------------------------------------------------
        page.click("text=TV Shows")
        page.wait_for_timeout(400)
        page.click("#view-btn-grid")
        page.wait_for_selector(".items-grid", timeout=5000)
        page.wait_for_timeout(400)
        page.screenshot(path=str(SCREENSHOTS_DIR / "09_tv_library_poster_light.png"))
        print("  ✓ 09_tv_library_poster_light.png")

        # ------------------------------------------------------------------
        # 10 — TV library, list view (light)
        # ------------------------------------------------------------------
        page.click("#view-btn-list")
        page.wait_for_selector(".items-list", timeout=3000)
        page.wait_for_timeout(300)
        page.screenshot(path=str(SCREENSHOTS_DIR / "10_tv_library_list_light.png"))
        print("  ✓ 10_tv_library_list_light.png")

        # ------------------------------------------------------------------
        # 11 — Settings page (dark)
        # ------------------------------------------------------------------
        page.close()
        page = new_page("dark")
        page.click("#settings-nav-item")
        page.wait_for_selector("#settings-view:not(.hidden)", timeout=5000)
        page.wait_for_timeout(300)
        page.screenshot(path=str(SCREENSHOTS_DIR / "11_settings.png"))
        print("  ✓ 11_settings.png")

        page.close()
        browser.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = 18080
    print(f"Starting Flask on port {port}…")
    proc = _start_flask(port)
    try:
        print("Taking screenshots…")
        take_screenshots(base_url=f"http://127.0.0.1:{port}")
        print(f"\nDone. Screenshots saved to {SCREENSHOTS_DIR}/")
    finally:
        proc.terminate()
        proc.wait()
