"""Shared pytest fixtures for Themarr tests."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def app():
    """Create test Flask app."""
    from app import web_app
    from app import themerrdb_service
    from app.cache import invalidate_library_cache
    web_app.app.config['TESTING'] = True
    invalidate_library_cache()
    web_app._startup_warmup_started = True  # Prevent middleware from resetting cache on each request
    themerrdb_service._themerrdb_cache.clear()
    yield web_app.app
    invalidate_library_cache()
    web_app._startup_warmup_started = True  # Reset for next test
    themerrdb_service._themerrdb_cache.clear()


@pytest.fixture
def client(app):
    """Create test client."""
    from app import web_app
    api_key, _ = web_app._get_api_key(web_app._generated_api_key)
    test_client = app.test_client()
    test_client.environ_base['HTTP_X_THEMARR_API_KEY'] = api_key
    return test_client


@pytest.fixture
def mock_plex():
    """Mock PlexServer."""
    with patch('app.web_app.plex_is_configured', return_value=True):
        with patch('app.web_app.get_plex') as mock_get_plex:
            with patch('app.cache.get_plex') as mock_cache_get_plex:
                with patch('app.webhook_handlers.get_plex') as mock_webhook_get_plex:
                    with patch('app.bulk_operations.get_plex') as mock_bulk_get_plex:
                        with patch('app.theme_state.get_plex') as mock_theme_state_get_plex:
                            plex = MagicMock()
                            plex.friendlyName = 'Test Plex Server'
                            plex.version = '1.0.0'
                            mock_get_plex.return_value = plex
                            mock_cache_get_plex.return_value = plex
                            mock_webhook_get_plex.return_value = plex
                            mock_bulk_get_plex.return_value = plex
                            mock_theme_state_get_plex.return_value = plex
                            yield plex
