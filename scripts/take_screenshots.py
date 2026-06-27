#!/usr/bin/env python3
"""
Automated screenshot script for Themarr web UI.

Uses Playwright to launch a real browser, intercepts all /api/* calls with
mock data, and captures screenshots of every major UI state. Generated mock
poster/thumbnail artwork is served so card visuals are meaningful in captures.
Screenshots are
written to the screenshots/ directory in the repo root.

Usage:
    pip install playwright
    playwright install chromium
    python3 scripts/take_screenshots.py

The script requires no running Plex server — all API responses are mocked.
"""

import json
import os
import re
import subprocess
import sys
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
    {"id": 1, "key": 1, "title": "TV Shows", "type": "show", "totalSize": 42},
    {"id": 2, "key": 2, "title": "Movies",   "type": "movie", "totalSize": 18},
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
        "thumb": f"/library/metadata/{100 + i}/thumb",
        "type": "show",
        "has_plex_theme": has_plex,
        "has_local_theme": has_local,
        "theme_size": 245760 if has_local else 0,
        "local_path": f"/tv/{title.replace(' ', '_')}",
    }
    for i, (title, year, has_plex, has_local) in enumerate(_TV_ITEMS_RAW)
]

_MOVIE_ITEMS_RAW = [
    ("Dune", 2021, True, True),
    ("Inception", 2010, True, True),
    ("Oppenheimer", 2023, True, False),
    ("Interstellar", 2014, True, False),
    ("The Dark Knight", 2008, True, True),
    ("The Batman", 2022, False, False),
]

MOCK_MOVIE_ITEMS = [
    {
        "ratingKey": 300 + i,
        "title": title,
        "year": year,
        "thumb": f"/library/metadata/{300 + i}/thumb",
        "type": "movie",
        "has_plex_theme": has_plex,
        "has_local_theme": has_local,
        "theme_size": 245760 if has_local else 0,
        "local_path": f"/movies/{title.replace(' ', '_')}",
    }
    for i, (title, year, has_plex, has_local) in enumerate(_MOVIE_ITEMS_RAW)
]

MOCK_ITEMS_BY_KEY = {
    int(item["ratingKey"]): item for item in (MOCK_TV_ITEMS + MOCK_MOVIE_ITEMS)
}
MOCK_YOUTUBE_SEARCH = {
    "results": [
        {
            "id": "ilfYnhXD-bE",
            "title": "Breaking Bad Main Title Theme (Extended)",
            "url": "https://www.youtube.com/watch?v=ilfYnhXD-bE",
            "channel": "Dave Porter - Topic",
            "duration": "1:16",
            "thumbnail": "https://i.ytimg.com/vi/ilfYnhXD-bE/hqdefault.jpg",
            "view_count": 14687926,
        },
        {
            "id": "3U6PSWyv5sc",
            "title": "Breaking Bad Full Intro Title Sequence",
            "url": "https://www.youtube.com/watch?v=3U6PSWyv5sc",
            "channel": "AMC",
            "duration": "1:16",
            "thumbnail": "https://i.ytimg.com/vi/3U6PSWyv5sc/hqdefault.jpg",
            "view_count": 8234567,
        },
        {
            "id": "HEmx23LwFhI",
            "title": "Breaking Bad - Theme",
            "url": "https://www.youtube.com/watch?v=HEmx23LwFhI",
            "channel": "SoundtrackHub",
            "duration": "0:18",
            "thumbnail": "https://i.ytimg.com/vi/HEmx23LwFhI/hqdefault.jpg",
            "view_count": 5123456,
        },
        {
            "id": "NYnDrbv7uJs",
            "title": "Breaking Bad Main Theme Extended Version",
            "url": "https://www.youtube.com/watch?v=NYnDrbv7uJs",
            "channel": "TV Themes",
            "duration": "11:05",
            "thumbnail": "https://i.ytimg.com/vi/NYnDrbv7uJs/hqdefault.jpg",
            "view_count": 2345678,
        },
        {
            "id": "PvcmS31dIPw",
            "title": "Breaking Bad Theme - 10 Hour Loop",
            "url": "https://www.youtube.com/watch?v=PvcmS31dIPw",
            "channel": "LoopMaster",
            "duration": "10:00:00",
            "thumbnail": "https://i.ytimg.com/vi/PvcmS31dIPw/hqdefault.jpg",
            "view_count": 987654,
        },
    ]
}


def _safe_svg_text(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _build_mock_poster_svg(title: str, media_type: str, width: int, height: int) -> bytes:
    icon = "📺" if media_type == "show" else "🎬"
    top = "#2f5d8a" if media_type == "show" else "#6b3d87"
    bottom = "#1b2735" if media_type == "show" else "#2d1f3a"
    safe_title = _safe_svg_text(title)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{top}"/>
      <stop offset="100%" stop-color="{bottom}"/>
    </linearGradient>
  </defs>
  <rect width="100%" height="100%" fill="url(#bg)"/>
  <rect x="12" y="12" width="{width - 24}" height="{height - 24}" rx="12" fill="none" stroke="rgba(255,255,255,0.28)" stroke-width="2"/>
  <text x="50%" y="44%" text-anchor="middle" font-size="54">{icon}</text>
  <text x="50%" y="66%" text-anchor="middle" fill="#eef4ff" font-family="Arial, sans-serif" font-size="22" font-weight="700">{safe_title}</text>
</svg>"""
    return svg.encode("utf-8")


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
        elif "/api/cache/status" in url:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"ready": True, "sections_total": 2, "sections_completed": 2}),
            )
        elif "/api/libraries" in url and "/items" not in url:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(MOCK_LIBRARIES))
        elif "/api/libraries/1/items" in url:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(MOCK_TV_ITEMS))
        elif "/api/libraries/2/items" in url:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(MOCK_MOVIE_ITEMS))
        elif "/api/youtube/search" in url:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(MOCK_YOUTUBE_SEARCH))
        elif "/api/poster/" in url:
            match = re.search(r"/api/poster/(\d+)", url)
            rating_key = int(match.group(1)) if match else None
            item = MOCK_ITEMS_BY_KEY.get(rating_key, {})
            poster_svg = _build_mock_poster_svg(
                title=str(item.get("title", "Themarr")),
                media_type=str(item.get("type", "show")),
                width=320,
                height=480,
            )
            route.fulfill(status=200, content_type="image/svg+xml", body=poster_svg)
        elif "i.ytimg.com/vi/" in url:
            thumb_svg = _build_mock_poster_svg(
                title="YouTube Preview",
                media_type="movie",
                width=320,
                height=180,
            )
            route.fulfill(status=200, content_type="image/svg+xml", body=thumb_svg)
        else:
            route.continue_()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()

        def new_page(theme: str = "dark") -> object:
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.route("**/api/**", route_handler)
            page.route("https://i.ytimg.com/**", route_handler)
            # Pre-set theme in localStorage before app scripts execute.
            page.add_init_script(
                f"""
                window.localStorage.setItem('themarr-theme', {json.dumps(theme)});
                // Keep this aligned with the server-rendered default in this script.
                window.localStorage.setItem('themarr-theme-default', 'dark');
                window.localStorage.setItem('themarr-view', 'list');
                """,
            )
            page.goto(base_url, wait_until="networkidle")
            page.wait_for_function(
                "(expected) => document.documentElement.dataset.theme === expected",
                arg=theme,
            )
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

        # ------------------------------------------------------------------
        # 12 — YouTube search modal (dark)
        # ------------------------------------------------------------------
        page.close()
        page = new_page("dark")
        page.click("text=TV Shows")
        page.wait_for_selector(".items-list, .items-grid", timeout=5000)
        page.wait_for_timeout(400)
        # Click the YouTube button on the first item in list view
        page.click("#view-btn-list")
        page.wait_for_selector(".items-list", timeout=3000)
        page.wait_for_timeout(300)
        yt_buttons = page.query_selector_all(".action-btn-youtube")
        if yt_buttons:
            yt_buttons[0].click()
            page.wait_for_selector(".yt-result", timeout=5000)
            page.wait_for_timeout(500)
            page.screenshot(path=str(SCREENSHOTS_DIR / "12_youtube_search_modal.png"))
            print("  ✓ 12_youtube_search_modal.png")
        else:
            print("  ⚠ 12_youtube_search_modal.png — no YouTube button found, skipped")

        # ------------------------------------------------------------------
        # 13 — Copy theme modal (dark)
        # ------------------------------------------------------------------
        page.click("#modal-youtube .modal-close")
        page.wait_for_selector("#modal-youtube", state="hidden", timeout=5000)
        page.locator(".action-btn-copy").first.click()
        page.wait_for_selector("#modal-copy-theme:not(.hidden)", timeout=5000)
        page.wait_for_selector("#copy-theme-source-item:not([disabled])", timeout=5000)
        page.wait_for_timeout(500)
        page.screenshot(path=str(SCREENSHOTS_DIR / "13_copy_theme_modal.png"))
        print("  ✓ 13_copy_theme_modal.png")

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
