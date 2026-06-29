"""Tests for app/themerrdb_service.py — ThemerrDB queries, caching, and cache key collision."""
from unittest.mock import MagicMock, patch

from tests.helpers import make_mock_show


class TestThemerrDB:
    def test_query_themerrdb_caches_not_found_results(self):
        from app import web_app
        from app import themerrdb_service

        themerrdb_service._themerrdb_cache.clear()
        mock_response = MagicMock()
        mock_response.status_code = 404
        with patch('app.themerrdb_service.http_requests.get', return_value=mock_response) as mock_get:
            first = web_app.query_themerrdb('movies', 'imdb', 'tt0000001')
            second = web_app.query_themerrdb('movies', 'imdb', 'tt0000001')

        assert first is None
        assert second is None
        assert mock_get.call_count == 1

    def test_get_themerrdb_theme_reuses_cached_result_across_item_ids(self):
        from app import web_app
        from app import themerrdb_service

        themerrdb_service._themerrdb_cache.clear()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'youtube_theme_url': 'https://youtube.com/watch?v=test'}
        with patch('app.themerrdb_service.http_requests.get', return_value=mock_response) as mock_get:
            first = web_app.get_themerrdb_theme_for_external_ids('movie', {'imdb': 'tt1234567', 'tmdb': '123', 'tvdb': None})
            second = web_app.get_themerrdb_theme_for_external_ids('movie', {'imdb': None, 'tmdb': '123', 'tvdb': None})

        assert first is not None
        assert second is not None
        assert mock_get.call_count == 1

    def test_check_themerrdb_available(self, client, mock_plex):
        show = make_mock_show()
        show.guids = [{'id': 'imdb://tt1234567'}]
        mock_plex.fetchItem.return_value = show

        with patch('app.theme_state.get_themerrdb_data_for_context', return_value={'youtube_theme_url': 'https://youtube.com/watch?v=test'}), \
             patch('app.theme_state.extract_youtube_audio_url', return_value='https://audio.example/stream'):
            resp = client.get('/api/items/1/theme/themerrdb/check')

        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload['available'] is True
        assert payload['youtube_url'] == 'https://youtube.com/watch?v=test'

    def test_check_themerrdb_unavailable(self, client, mock_plex):
        show = make_mock_show()
        show.guids = []
        mock_plex.fetchItem.return_value = show

        with patch('app.web_app.get_themerrdb_theme', return_value=None):
            resp = client.get('/api/items/1/theme/themerrdb/check')

        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload['available'] is False
        assert 'reason' in payload

    def test_preview_themerrdb_not_found(self, client, mock_plex):
        show = make_mock_show()
        mock_plex.fetchItem.return_value = show

        with patch('app.web_app.get_themerrdb_theme', return_value=None):
            resp = client.get('/api/items/1/theme/themerrdb/preview')

        assert resp.status_code == 404
        assert 'error' in resp.get_json()

    def test_preview_themerrdb_success(self, client, mock_plex):
        show = make_mock_show()
        mock_plex.fetchItem.return_value = show

        with patch('app.web_app.get_themerrdb_theme', return_value={'youtube_theme_url': 'https://youtube.com/watch?v=test'}), \
             patch('app.web_app.extract_youtube_audio_url', return_value='https://rr1---sn-test.googlevideo.com/stream'), \
             patch('app.web_app.http_requests') as mock_requests:

            mock_resp = MagicMock()
            mock_resp.iter_content.return_value = [b'audio_chunk']
            mock_requests.get.return_value = mock_resp

            resp = client.get('/api/items/1/theme/themerrdb/preview')

        assert resp.status_code == 200

    def test_preview_check_returns_availability_payload(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'local_theme')
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show

        resp = client.get('/api/items/plex/1/theme/preview/check')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'available' in data
        assert data['source_unverified'] is True
        assert 'local theme.mp3' in data['reason']

    def test_download_from_themerrdb_success(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show

        fake_tmpdir = tmp_path / 'themerrdb_tmp'
        fake_tmpdir.mkdir()
        (fake_tmpdir / 'theme.mp3').write_bytes(b'themerrdb_audio')

        with patch('app.web_app.get_themerrdb_theme', return_value={'youtube_theme_url': 'https://youtube.com/watch?v=test'}), \
             patch('app.youtube_utils.yt_dlp') as mock_ytdlp, \
             patch('tempfile.TemporaryDirectory') as mock_tmpdir:
            mock_tmpdir.return_value.__enter__ = lambda s: str(fake_tmpdir)
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            mock_ytdlp.YoutubeDL.return_value.__enter__ = lambda s: MagicMock()
            mock_ytdlp.YoutubeDL.return_value.__exit__ = MagicMock(return_value=False)

            resp = client.post('/api/items/1/theme/themerrdb',
                               json={'overwrite': False},
                               content_type='application/json')

        assert resp.status_code == 200
        assert resp.get_json()['success'] is True
        assert (show_dir / 'theme.mp3').read_bytes() == b'themerrdb_audio'

    def test_download_from_themerrdb_already_exists(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'existing_theme')
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show

        resp = client.post('/api/items/1/theme/themerrdb', json={'overwrite': False})

        assert resp.status_code == 409
        assert resp.get_json()['exists'] is True

    def test_check_provider_themerrdb_available_for_jellyfin(self, client):
        context = {
            'provider': 'jellyfin',
            'item_id': 'jf-1',
            'title': 'Jellyfin Movie',
            'item': {'Id': 'jf-1', 'Type': 'Movie', 'ProviderIds': {'Imdb': 'tt1234567'}},
            'local_path': '/movies/Jellyfin Movie (2020)',
        }
        with patch('app.web_app._get_item_context', return_value=context), \
             patch('app.theme_state.get_themerrdb_data_for_context', return_value={'youtube_theme_url': 'https://youtube.com/watch?v=test'}), \
             patch('app.theme_state.extract_youtube_audio_url', return_value='https://audio.example/stream'), \
             patch('app.theme_state.get_external_ids_for_context', return_value={'imdb': 'tt1234567', 'tmdb': None, 'tvdb': None}):
            resp = client.get('/api/items/jellyfin/jf-1/theme/themerrdb/check')

        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload['available'] is True
        assert payload['youtube_url'] == 'https://youtube.com/watch?v=test'


class TestThemerrDbCacheKey:
    """BUG-002 — ThemerrDB cache key must include item_type to prevent cross-type collisions."""

    def test_cache_keys_differ_by_item_type(self):
        from app import themerrdb_service
        key_movie = themerrdb_service._get_themerrdb_cache_key('tt1234567', 'movies')
        key_show = themerrdb_service._get_themerrdb_cache_key('tt1234567', 'tv_shows')
        assert key_movie != key_show, (
            "Same external ID with different item types must produce different cache keys"
        )

    def test_cache_key_without_type_is_distinct(self):
        from app import themerrdb_service
        key_typed = themerrdb_service._get_themerrdb_cache_key('tt1234567', 'movies')
        key_untyped = themerrdb_service._get_themerrdb_cache_key('tt1234567')
        assert key_typed != key_untyped
