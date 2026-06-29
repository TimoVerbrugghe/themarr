"""Tests for app/cache.py and app/theme_state.py — cache status, theme state sync, cache refresh."""
from unittest.mock import MagicMock, patch

import pytest

from tests.helpers import make_mock_show


class TestCacheStatus:
    def test_cache_status_endpoint(self, client):
        from app import web_app

        with web_app._theme_hydration_status_lock:
            web_app._theme_hydration_status.update({
                'running': True,
                'ready': False,
                'sections_total': 5,
                'sections_completed': 2,
            })

        resp = client.get('/api/cache/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['running'] is True
        assert data['ready'] is False
        assert data['sections_total'] == 5
        assert data['sections_completed'] == 2


class TestCachedThemeStateSync:
    def test_sync_cached_item_theme_state_preserves_plex_source_when_reported(self, tmp_path):
        from app.cache import _library_cache, sync_cached_item_theme_state

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'local_theme')
        _library_cache['1'] = [{
            'id': '1',
            'ratingKey': 1,
            'provider': 'plex',
            'local_path': str(show_dir),
            'has_local_theme': False,
            'has_plex_theme': True,
            'theme_size': 0,
        }]

        mock_item = make_mock_show(rating_key=1, location=str(show_dir), has_theme=True)
        with patch('app.plex_utils.get_plex') as mock_get_plex:
            with patch('app.theme_state.get_plex') as mock_theme_state_get_plex:
                plex = MagicMock()
                plex.fetchItem.return_value = mock_item
                mock_get_plex.return_value = plex
                mock_theme_state_get_plex.return_value = plex
                updated, found = sync_cached_item_theme_state('plex', '1')

        assert found is True
        assert updated['has_local_theme'] is True
        assert updated['has_plex_theme'] is True
        assert updated['plex_theme_source_unverified'] is True


class TestSettingsRefreshCache:
    def test_refresh_cache_starts_background_warmup(self, client):
        with patch('app.web_app.kick_off_cache_warmup') as mock_warmup:
            mock_warmup.return_value = True
            resp = client.post('/api/settings/refresh-cache')

        assert resp.status_code == 200
        assert resp.get_json()['success'] is True
        mock_warmup.assert_called_once()
