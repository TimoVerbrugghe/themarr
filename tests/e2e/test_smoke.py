"""Playwright smoke tests for the Themarr web UI.

These tests spin up a real Flask server and drive a headless Chromium browser
to verify the most critical user flows:

- ``DISABLE_AUTH=true`` — page loads without a login overlay; header, sidebar,
  and library items all render correctly.
- Credentials mode (``AUTH_USERNAME`` + ``AUTH_PASSWORD``) — login overlay is
  shown, wrong credentials produce an error, correct credentials complete the
  login flow and reveal the main UI.

API routes that depend on a real Plex/Jellyfin server are intercepted by
Playwright and answered with mock JSON so the tests have no external runtime
requirements.
"""
import pytest


# ===========================================================================
# Helpers
# ===========================================================================

_STARTUP_TIMEOUT = 15_000   # ms — how long to wait for startup overlay to hide
_PAGE_TIMEOUT    = 10_000   # ms — general element wait timeout


# ===========================================================================
# No-auth mode (DISABLE_AUTH=true)
# ===========================================================================

class TestNoAuthMode:
    """Tests run against a server with DISABLE_AUTH=true."""

    def _load(self, page, no_auth_base_url):
        """Navigate to the app and wait for the startup overlay to disappear."""
        from tests.e2e.conftest import setup_api_mocks
        setup_api_mocks(page, no_auth_base_url)
        page.goto(no_auth_base_url)
        page.locator("#startup-overlay").wait_for(state="hidden", timeout=_STARTUP_TIMEOUT)

    def test_no_login_overlay(self, page, no_auth_base_url):
        """Login overlay must NOT be visible when auth is disabled."""
        self._load(page, no_auth_base_url)
        assert not page.locator("#login-overlay").is_visible()

    def test_header_visible(self, page, no_auth_base_url):
        """The top header bar with the Themarr logo must be visible."""
        self._load(page, no_auth_base_url)
        assert page.locator("header.header").is_visible()

    def test_theme_toggle_visible(self, page, no_auth_base_url):
        """The dark/light theme toggle button must be visible."""
        self._load(page, no_auth_base_url)
        assert page.locator("#theme-toggle").is_visible()

    def test_sidebar_visible(self, page, no_auth_base_url):
        """The sidebar navigation panel must be visible."""
        self._load(page, no_auth_base_url)
        assert page.locator(".sidebar").is_visible()

    def test_library_nav_populated(self, page, no_auth_base_url):
        """At least one Plex library entry must appear in the sidebar."""
        self._load(page, no_auth_base_url)
        page.wait_for_selector(".library-nav-item", timeout=_PAGE_TIMEOUT)
        first_item = page.locator(".library-nav-item").first
        assert first_item.is_visible()
        assert "TV Shows" in (first_item.text_content() or "")

    def test_click_library_shows_items(self, page, no_auth_base_url):
        """Clicking a library in the sidebar must render item cards."""
        self._load(page, no_auth_base_url)
        page.wait_for_selector(".library-nav-item", timeout=_PAGE_TIMEOUT)
        page.locator(".library-nav-item").first.click()
        page.wait_for_selector("#library-view:not(.hidden)", timeout=_PAGE_TIMEOUT)
        page.wait_for_selector(".item-card", timeout=_PAGE_TIMEOUT)
        assert page.locator(".item-card").count() > 0

    def test_items_show_title(self, page, no_auth_base_url):
        """Each item card must contain the show title from the mock data."""
        self._load(page, no_auth_base_url)
        page.wait_for_selector(".library-nav-item", timeout=_PAGE_TIMEOUT)
        page.locator(".library-nav-item").first.click()
        page.wait_for_selector(".item-card", timeout=_PAGE_TIMEOUT)
        titles = [
            page.locator(".item-card").nth(i).text_content() or ""
            for i in range(page.locator(".item-card").count())
        ]
        all_text = " ".join(titles)
        assert "Breaking Bad" in all_text

    def test_view_toggle_buttons_visible(self, page, no_auth_base_url):
        """Grid / list view toggle buttons must be present after loading a library."""
        self._load(page, no_auth_base_url)
        page.wait_for_selector(".library-nav-item", timeout=_PAGE_TIMEOUT)
        page.locator(".library-nav-item").first.click()
        page.wait_for_selector("#library-view:not(.hidden)", timeout=_PAGE_TIMEOUT)
        assert page.locator("#view-btn-grid").is_visible()
        assert page.locator("#view-btn-list").is_visible()


# ===========================================================================
# Credentials mode (AUTH_USERNAME + AUTH_PASSWORD)
# ===========================================================================

class TestCredentialsMode:
    """Tests run against a server that requires username/password login."""

    def test_login_overlay_shown(self, page, credentials_base_url):
        """Login overlay must be visible before the user has authenticated."""
        page.goto(credentials_base_url)
        page.wait_for_selector("#login-overlay:not(.hidden)", timeout=_PAGE_TIMEOUT)
        assert page.locator("#login-overlay").is_visible()

    def test_login_form_visible(self, page, credentials_base_url):
        """Username and password inputs must be rendered inside the overlay."""
        page.goto(credentials_base_url)
        page.wait_for_selector("#login-form-credentials:not(.hidden)", timeout=_PAGE_TIMEOUT)
        assert page.locator("#login-username").is_visible()
        assert page.locator("#login-password").is_visible()
        assert page.locator("#login-submit-btn").is_visible()

    def test_wrong_credentials_show_error(self, page, credentials_base_url):
        """Submitting wrong credentials must display an error message."""
        page.goto(credentials_base_url)
        page.wait_for_selector("#login-form-credentials:not(.hidden)", timeout=_PAGE_TIMEOUT)
        page.fill("#login-username", "wronguser")
        page.fill("#login-password", "wrongpassword")
        page.click("#login-submit-btn")
        page.wait_for_selector("#login-error:not(.hidden)", timeout=_PAGE_TIMEOUT)
        error_text = page.locator("#login-error").text_content() or ""
        assert len(error_text) > 0

    def test_correct_credentials_hide_overlay(self, page, credentials_base_url):
        """Submitting correct credentials must hide the login overlay."""
        from tests.e2e.conftest import setup_api_mocks
        setup_api_mocks(page, credentials_base_url)
        page.goto(credentials_base_url)
        page.wait_for_selector("#login-form-credentials:not(.hidden)", timeout=_PAGE_TIMEOUT)
        page.fill("#login-username", "testuser")
        page.fill("#login-password", "testpass123")
        page.click("#login-submit-btn")
        # Login overlay should disappear after successful auth
        page.locator("#login-overlay").wait_for(state="hidden", timeout=_PAGE_TIMEOUT)
        assert not page.locator("#login-overlay").is_visible()

    def test_correct_credentials_reveal_main_ui(self, page, credentials_base_url):
        """After a successful login the startup overlay must eventually hide."""
        from tests.e2e.conftest import setup_api_mocks
        setup_api_mocks(page, credentials_base_url)
        page.goto(credentials_base_url)
        page.wait_for_selector("#login-form-credentials:not(.hidden)", timeout=_PAGE_TIMEOUT)
        page.fill("#login-username", "testuser")
        page.fill("#login-password", "testpass123")
        page.click("#login-submit-btn")
        page.locator("#startup-overlay").wait_for(state="hidden", timeout=_STARTUP_TIMEOUT)
        assert page.locator("header.header").is_visible()
        assert page.locator(".sidebar").is_visible()
