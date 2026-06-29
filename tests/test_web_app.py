"""Tests for Themarr web application."""
import base64
import io
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def app():
    """Create test Flask app."""
    import web_app
    web_app.app.config['TESTING'] = True
    web_app._invalidate_library_cache()
    web_app._themerrdb_cache.clear()
    yield web_app.app
    web_app._invalidate_library_cache()
    web_app._themerrdb_cache.clear()


@pytest.fixture
def client(app):
    """Create test client."""
    import web_app
    api_key, _ = web_app._get_api_key()
    test_client = app.test_client()
    test_client.environ_base['HTTP_X_THEMARR_API_KEY'] = api_key
    return test_client


@pytest.fixture
def mock_plex():
    """Mock PlexServer."""
    with patch('web_app.plex_is_configured', return_value=True):
        with patch('web_app.get_plex') as mock_get_plex:
            plex = MagicMock()
            plex.friendlyName = 'Test Plex Server'
            plex.version = '1.0.0'
            mock_get_plex.return_value = plex
            yield plex


def make_mock_show(rating_key=1, title='Test Show', year=2020, has_theme=True, location=None):
    """Create a mock Plex show item.

    If *location* is None the item will have no locations (path won't be
    resolved by get_item_local_path, so has_local_theme will be False).
    Pass a real filesystem path to test theme detection.
    """
    show = MagicMock()
    show.ratingKey = rating_key
    show.title = title
    show.year = year
    show.type = 'show'
    show.thumb = f'/library/metadata/{rating_key}/thumb'
    show.theme = f'/library/metadata/{rating_key}/theme/1' if has_theme else None
    show.locations = [location] if location else []
    return show


def make_mock_movie(rating_key=2, title='Test Movie', year=2021, has_theme=True, location=None):
    """Create a mock Plex movie item.

    If *location* is None the item will have no locations (path won't be
    resolved by get_item_local_path, so has_local_theme will be False).
    Pass a real filesystem path (video file or folder) to test theme detection.
    """
    movie = MagicMock()
    movie.ratingKey = rating_key
    movie.title = title
    movie.year = year
    movie.type = 'movie'
    movie.thumb = f'/library/metadata/{rating_key}/thumb'
    movie.theme = f'/library/metadata/{rating_key}/theme/1' if has_theme else None
    movie.locations = [location] if location else []
    return movie


class TestStatus:
    def test_status_connected(self, client, mock_plex):
        with patch.dict(os.environ, {'PLEX_URL': 'http://plex.local', 'PLEX_TOKEN': 'token'}, clear=False):
            resp = client.get('/api/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['connected'] is True
        assert data['server_name'] == 'Test Plex Server'
        assert data['plex']['url_configured'] is True
        assert data['plex']['connected'] is True

    def test_status_disconnected(self, client):
        with patch.dict(os.environ, {'PLEX_URL': 'http://plex.local', 'PLEX_TOKEN': 'token'}, clear=False):
            with patch('web_app.get_plex', side_effect=Exception('Connection refused')):
                resp = client.get('/api/status')
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['connected'] is False
            assert data['error'] == 'Unable to connect to Plex'
            assert data['plex']['url_configured'] is True
            assert data['plex']['connected'] is False

    def test_status_hides_when_urls_not_configured(self, client):
        with patch.dict(os.environ, {'PLEX_URL': '', 'JELLYFIN_URL': ''}, clear=False):
            resp = client.get('/api/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['connected'] is False
        assert data['plex']['url_configured'] is False
        assert data['jellyfin']['url_configured'] is False

    def test_status_hides_plex_when_token_missing(self, client):
        with patch.dict(os.environ, {'PLEX_URL': 'http://plex.local', 'PLEX_TOKEN': ''}, clear=False):
            resp = client.get('/api/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['plex']['url_configured'] is False
        assert data['plex']['connected'] is False

    def test_status_includes_jellyfin_connected(self, client):
        mock_response = MagicMock()
        mock_response.content = b'{}'
        mock_response.json.return_value = {'ServerName': 'Test Jellyfin', 'Version': '10.9.1'}
        mock_response.raise_for_status.return_value = None
        with patch.dict(os.environ, {'JELLYFIN_URL': 'http://jellyfin.local', 'JELLYFIN_API_KEY': 'j-key'}, clear=False):
            with patch('web_app.jellyfin_session_get', return_value=mock_response):
                resp = client.get('/api/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['jellyfin']['url_configured'] is True
        assert data['jellyfin']['connected'] is True
        assert data['jellyfin']['server_name'] == 'Test Jellyfin'

    def test_status_marks_jellyfin_connected_when_system_info_endpoints_fail_but_libraries_work(self, client):
        import web_app

        mock_library_response = MagicMock()
        mock_library_response.raise_for_status.return_value = None
        mock_library_response.content = b'[]'
        mock_library_response.json.return_value = []

        with patch.dict(os.environ, {'JELLYFIN_URL': 'http://jellyfin.local', 'JELLYFIN_API_KEY': 'j-key'}, clear=False):
            with patch(
                'web_app.jellyfin_session_get',
                side_effect=[
                    web_app.http_requests.RequestException('System info unavailable'),
                    web_app.http_requests.RequestException('Public system info unavailable'),
                    mock_library_response,
                ],
            ):
                resp = client.get('/api/status')

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['jellyfin']['url_configured'] is True
        assert data['jellyfin']['connected'] is True
        assert data['jellyfin']['server_name'] is None

    def test_status_handles_non_json_system_info_payload_and_still_marks_jellyfin_connected(self, client):
        mock_response = MagicMock()
        mock_response.content = b'<!doctype html>'
        mock_response.json.side_effect = ValueError('Not JSON')
        mock_response.raise_for_status.return_value = None

        with patch.dict(os.environ, {'JELLYFIN_URL': 'http://jellyfin.local', 'JELLYFIN_API_KEY': 'j-key'}, clear=False):
            with patch('web_app.jellyfin_session_get', return_value=mock_response):
                resp = client.get('/api/status')

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['jellyfin']['url_configured'] is True
        assert data['jellyfin']['connected'] is True
        assert data['jellyfin']['server_name'] is None


class TestHealth:
    def test_health_endpoint(self, client):
        resp = client.get('/health')
        assert resp.status_code == 200
        assert resp.get_json() == {'status': 'healthy'}


class TestCacheStatus:
    def test_cache_status_endpoint(self, client):
        import web_app

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
        import web_app

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'local_theme')
        web_app._library_cache['1'] = [{
            'id': '1',
            'ratingKey': 1,
            'provider': 'plex',
            'local_path': str(show_dir),
            'has_local_theme': False,
            'has_plex_theme': True,
            'theme_size': 0,
        }]

        mock_item = make_mock_show(rating_key=1, location=str(show_dir), has_theme=True)
        with patch('web_app.get_plex') as mock_get_plex:
            plex = MagicMock()
            plex.fetchItem.return_value = mock_item
            mock_get_plex.return_value = plex
            updated, found = web_app._sync_cached_item_theme_state('plex', '1')

        assert found is True
        assert updated['has_local_theme'] is True
        assert updated['has_plex_theme'] is True
        assert updated['plex_theme_source_unverified'] is True


class TestSettingsRuntime:
    def test_generated_api_key_warning_does_not_log_secret(self):
        import web_app
        with patch.object(web_app.logger, 'warning') as mock_warning:
            web_app._log_generated_api_key_warning()

        mock_warning.assert_called_once_with(
            'API_KEY is not set; a one-time startup API key was generated. '
            'Open the Settings page in the web app after signing in to view it, '
            'or set API_KEY to a stable value to avoid rotation on restart.',
        )

    def test_runtime_settings_requires_authentication(self, app):
        unauthenticated_client = app.test_client()
        resp = unauthenticated_client.get('/api/settings/runtime')
        assert resp.status_code == 401

    def test_runtime_settings_returns_generated_key_when_env_missing(self, client):
        with patch.dict(os.environ, {'API_KEY': ''}, clear=False):
            resp = client.get('/api/settings/runtime')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'api_key' in data
        assert data['api_key_configured'] is False
        assert data['api_key_generated'] is True
        assert data['background_worker_count'] == 4
        assert data['library_page_size'] == 200
        assert data['library_page_size_max'] == 500
        assert data['poster_cache_max_items'] == 500

    def test_runtime_settings_prefers_configured_key(self, app):
        with patch.dict(os.environ, {'API_KEY': 'configured-key'}, clear=False):
            with app.test_client() as c:
                resp = c.get('/api/settings/runtime', headers={'X-Themarr-Api-Key': 'configured-key'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['api_key'] == 'configured-key'
        assert data['api_key_configured'] is True
        assert data['api_key_generated'] is False

    def test_runtime_settings_includes_current_environment_values(self, app):
        with patch.dict(os.environ, {'API_KEY': 'configured-key', 'DEFAULT_THEME': 'light', 'DISABLE_AUTH': 'false'}, clear=False):
            with app.test_client() as c:
                resp = c.get('/api/settings/runtime', headers={'X-Themarr-Api-Key': 'configured-key'})

        assert resp.status_code == 200
        data = resp.get_json()
        env_values = data['env_values']
        assert env_values['DEFAULT_THEME'] == 'light'
        assert env_values['DISABLE_AUTH'] == 'false'

    def test_runtime_settings_accessible_via_session(self, app):
        import web_app
        with patch.dict(os.environ, {'API_KEY': 'sess-key', 'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret'}):
            with app.test_client() as session_client:
                login_resp = session_client.post(
                    '/api/auth/login',
                    json={'username': 'admin', 'password': 'secret'},
                    content_type='application/json',
                )
                assert login_resp.status_code == 200
                resp = session_client.get('/api/settings/runtime')
        assert resp.status_code == 200
        assert resp.get_json()['api_key'] == 'sess-key'


class TestAuthLogin:
    def test_login_with_missing_credentials_mode_returns_503(self, app):
        with app.test_client() as c:
            resp = c.post('/api/auth/login', json={})
        assert resp.status_code == 503

    def test_login_with_missing_body_returns_503(self, app):
        with app.test_client() as c:
            resp = c.post('/api/auth/login')
        assert resp.status_code == 503

    def test_login_sets_session_that_authenticates_runtime_endpoint(self, app):
        with patch.dict(os.environ, {'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret'}):
            with app.test_client() as c:
                c.post('/api/auth/login', json={'username': 'admin', 'password': 'secret'})
                resp = c.get('/api/settings/runtime')
        assert resp.status_code == 200


class TestAuthLogout:
    def test_logout_clears_session(self, app):
        with patch.dict(os.environ, {'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret', 'API_KEY': 'logout-key'}):
            with app.test_client() as c:
                c.post('/api/auth/login', json={'username': 'admin', 'password': 'secret'})
                # Confirm authenticated
                assert c.get('/api/settings/runtime').status_code == 200
                # Logout
                logout_resp = c.post('/api/auth/logout')
                assert logout_resp.status_code == 200
                # Should now be unauthenticated
                assert c.get('/api/settings/runtime').status_code == 401


class TestLibraries:
    def test_get_libraries(self, client, mock_plex):
        tv_section = MagicMock()
        tv_section.key = 1
        tv_section.title = 'TV Shows'
        tv_section.type = 'show'
        tv_section.thumb = None
        tv_section.totalSize = 50

        movie_section = MagicMock()
        movie_section.key = 2
        movie_section.title = 'Movies'
        movie_section.type = 'movie'
        movie_section.thumb = None
        movie_section.totalSize = 100

        music_section = MagicMock()
        music_section.key = 3
        music_section.title = 'Music'
        music_section.type = 'artist'
        music_section.totalSize = 10

        mock_plex.library.sections.return_value = [tv_section, movie_section, music_section]

        resp = client.get('/api/libraries')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2
        titles = [d['title'] for d in data]
        assert 'TV Shows' in titles
        assert 'Movies' in titles
        assert 'Music' not in titles
        assert all('id' in d and 'key' in d and d['id'] == d['key'] for d in data)

    def test_get_libraries_error(self, client):
        with patch('web_app.plex_is_configured', return_value=True):
            with patch('web_app.get_plex', side_effect=Exception('Plex error')):
                resp = client.get('/api/libraries')
                assert resp.status_code == 500
                assert resp.get_json()['error'] == 'Failed to get libraries'

    def test_get_libraries_includes_jellyfin(self, client, mock_plex):
        tv_section = MagicMock()
        tv_section.key = 1
        tv_section.title = 'TV Shows'
        tv_section.type = 'show'
        tv_section.thumb = None
        tv_section.totalSize = 50
        mock_plex.library.sections.return_value = [tv_section]

        jellyfin_library = {
            'id': 'jf-tv',
            'key': 'jf-tv',
            'title': 'Jellyfin TV',
            'type': 'show',
            'thumb': None,
            'totalSize': 20,
            'provider': 'jellyfin',
        }
        with patch('web_app.jellyfin_is_configured', return_value=True), \
             patch('web_app._get_jellyfin_libraries', return_value=[jellyfin_library]):
            resp = client.get('/api/libraries')

        assert resp.status_code == 200
        data = resp.get_json()
        assert any(entry['provider'] == 'plex' for entry in data)
        assert any(entry['provider'] == 'jellyfin' for entry in data)

    def test_get_libraries_jellyfin_only_does_not_require_plex(self, client):
        jellyfin_library = {
            'id': 'jf-lib',
            'key': 'jf-lib',
            'title': 'Jellyfin Shows',
            'type': 'show',
            'thumb': None,
            'totalSize': 7,
            'provider': 'jellyfin',
        }

        with patch.dict(os.environ, {'PLEX_URL': '', 'PLEX_TOKEN': ''}, clear=False):
            with patch('web_app.get_plex', side_effect=AssertionError('get_plex should not be called')):
                with patch('web_app.jellyfin_is_configured', return_value=True), \
                     patch('web_app._get_jellyfin_libraries', return_value=[jellyfin_library]):
                    resp = client.get('/api/libraries')

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['provider'] == 'jellyfin'


class TestLibraryItems:
    def test_get_show_items(self, client, mock_plex, tmp_path):
        show = make_mock_show()
        section = MagicMock()
        section.all.return_value = [show]
        mock_plex.library.sectionByID.return_value = section
        mock_plex.library.sections.return_value = []

        resp = client.get('/api/libraries/1/items')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['title'] == 'Test Show'
        assert data[0]['type'] == 'show'
        assert data[0]['has_plex_theme'] is True
        assert data[0]['plex_theme_source_unverified'] is False
        assert data[0]['has_local_theme'] is False

    def test_get_movie_items(self, client, mock_plex, tmp_path):
        movie = make_mock_movie()
        section = MagicMock()
        section.all.return_value = [movie]
        mock_plex.library.sectionByID.return_value = section
        mock_plex.library.sections.return_value = []

        resp = client.get('/api/libraries/2/items')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['title'] == 'Test Movie'
        assert data[0]['type'] == 'movie'


class TestExternalIds:
    def test_extract_external_ids_from_dict_guids(self):
        import web_app

        item = MagicMock()
        item.guids = [
            {'id': 'imdb://tt0111161'},
            {'id': 'tvdb://121361'},
        ]

        ids = web_app.extract_external_ids(item)
        assert ids == {'imdb': 'tt0111161', 'tvdb': '121361', 'tmdb': None}

    def test_extract_external_ids_from_plex_guid_objects(self):
        import web_app

        item = MagicMock()
        item.guids = [
            type('GuidObj', (), {'id': 'imdb://tt0468569'})(),
            type('GuidObj', (), {'id': 'tvdb://80379'})(),
        ]

        ids = web_app.extract_external_ids(item)
        assert ids == {'imdb': 'tt0468569', 'tvdb': '80379', 'tmdb': None}

    def test_serialize_jellyfin_item_sets_themerrdb_flag(self):
        import web_app

        jellyfin_item = {
            'Id': 'jf-1',
            'Name': 'Jellyfin Movie',
            'Type': 'Movie',
            'Path': '/movies/Jellyfin Movie (2020)',
            'ProductionYear': 2020,
            'ProviderIds': {'Imdb': 'tt1234567', 'Tmdb': '1234'},
        }

        with patch('web_app.get_themerrdb_theme_for_item', return_value={'youtube_theme_url': 'https://youtube.com/watch?v=test'}):
            data = web_app._serialize_jellyfin_item(jellyfin_item, 'jf-lib', theme_dirs={})

        assert data['has_themerrdb_theme'] is True
        assert data['plex_theme_source_unverified'] is False
        assert data['external_ids'] == {'imdb': 'tt1234567', 'tmdb': '1234', 'tvdb': None}


class TestLibraryItemsAdditional:
    def test_items_with_existing_theme(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        theme_file = show_dir / 'theme.mp3'
        theme_file.write_bytes(b'\xff\xfb' * 100)

        show = make_mock_show(location=str(show_dir))
        section = MagicMock()
        section.all.return_value = [show]
        mock_plex.library.sectionByID.return_value = section
        mock_plex.library.sections.return_value = [
            MagicMock(type='show', locations=[str(tmp_path)])
        ]

        resp = client.get('/api/libraries/1/items')
        data = resp.get_json()
        assert data[0]['has_local_theme'] is True
        assert data[0]['plex_theme_source_unverified'] is True
        assert data[0]['theme_size'] > 0

    def test_build_library_items_prefers_section_locations_for_theme_scan(self, mock_plex):
        import web_app

        section = MagicMock()
        section.key = 1
        section.locations = ['/only-this-path']
        section.all.return_value = []
        mock_plex.library.sectionByID.return_value = section

        with patch('web_app.scan_local_theme_dirs', return_value={}) as mock_scan, \
             patch('web_app.get_section_base_paths', return_value={'/fallback-path'}):
            web_app._build_library_items(1)

        assert mock_scan.call_count == 1
        assert mock_scan.call_args[0][0] == {'/only-this-path'}

    def test_get_jellyfin_items_via_provider_route(self, client):
        jellyfin_items = [{
            'id': 'jf-item-1',
            'ratingKey': 'jf-item-1',
            'provider': 'jellyfin',
            'title': 'Jellyfin Show',
            'type': 'show',
            'has_plex_theme': False,
            'has_local_theme': False,
        }]
        with patch('web_app._build_library_items', return_value=jellyfin_items):
            resp = client.get('/api/libraries/jellyfin/jf-lib/items')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data[0]['provider'] == 'jellyfin'
        assert data[0]['title'] == 'Jellyfin Show'

    def test_scan_local_theme_dirs_detects_theme_when_item_path_is_base(self, tmp_path):
        import web_app

        movie_dir = tmp_path / 'Monsters, Inc. (2001)'
        movie_dir.mkdir()
        theme_path = movie_dir / 'theme.mp3'
        theme_path.write_bytes(b'\xff\xfb' * 100)

        scanned = web_app.scan_local_theme_dirs({str(movie_dir)})
        assert scanned[str(movie_dir)] == theme_path.stat().st_size


class TestGetItemLocalPath:
    def test_show_path(self, tmp_path):
        from web_app import get_item_local_path
        show_dir = tmp_path / 'My Show (2020)'
        show_dir.mkdir()
        show = MagicMock()
        show.type = 'show'
        show.locations = [str(show_dir)]
        result = get_item_local_path(show)
        assert result == show_dir

    def test_movie_path(self, tmp_path):
        from web_app import get_item_local_path
        movie_dir = tmp_path / 'My Movie (2021)'
        movie_dir.mkdir()
        movie_file = movie_dir / 'movie.mkv'
        movie_file.write_bytes(b'fake')
        movie = MagicMock()
        movie.type = 'movie'
        movie.locations = [str(movie_file)]
        result = get_item_local_path(movie)
        assert result == movie_dir

    def test_movie_folder_with_dot_name_is_not_treated_as_file(self, tmp_path):
        from web_app import get_item_local_path
        movie_dir = tmp_path / 'Monsters, Inc. (2001)'
        movie_dir.mkdir()
        movie = MagicMock()
        movie.type = 'movie'
        movie.locations = [str(movie_dir)]
        result = get_item_local_path(movie)
        assert result == movie_dir

    def test_no_locations(self):
        from web_app import get_item_local_path
        item = MagicMock()
        item.type = 'show'
        item.locations = []
        result = get_item_local_path(item)
        assert result is None


class TestGetJellyfinItemLocalPath:
    def test_movie_file_path_returns_parent(self):
        from web_app import get_jellyfin_item_local_path
        item = {'Type': 'Movie', 'Path': '/movies/Monsters, Inc. (2001)/movie.mkv'}
        assert str(get_jellyfin_item_local_path(item)) == '/movies/Monsters, Inc. (2001)'

    def test_movie_folder_with_dot_name_returns_folder(self):
        from web_app import get_jellyfin_item_local_path
        item = {'Type': 'Movie', 'Path': '/movies/Monsters, Inc. (2001)'}
        assert str(get_jellyfin_item_local_path(item)) == '/movies/Monsters, Inc. (2001)'


class TestLocalPathValidation:
    def test_validate_local_media_path_rejects_traversal(self):
        from web_app import _validate_local_media_path

        with pytest.raises(ValueError, match='Invalid local media path'):
            _validate_local_media_path('../../etc/passwd')

    def test_validate_local_media_path_allows_absolute_paths_without_env_roots(self, tmp_path):
        from web_app import _validate_local_media_path

        validated = _validate_local_media_path(tmp_path / 'other' / 'show')
        assert str(validated).endswith('/other/show')

    def test_provider_theme_accepts_absolute_paths_without_env_roots(self, client):
        with patch('web_app._get_item_context', return_value={'local_path': Path('/etc')}):
            resp = client.get('/api/items/jellyfin/abc/theme')

        assert resp.status_code == 404


class TestThemeDownload:
    def test_download_theme_success(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show
        mock_plex.url.return_value = 'http://plex/theme.mp3'

        mock_response = MagicMock()
        mock_response.iter_content.return_value = [b'fake_mp3_data']
        mock_plex._session.get.return_value = mock_response

        resp = client.post('/api/items/1/theme/download',
                           json={'overwrite': False},
                           content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True

    def test_download_theme_updates_cached_item_state(self, client, mock_plex, tmp_path):
        import web_app

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show
        mock_plex.url.return_value = 'http://plex/theme.mp3'

        mock_response = MagicMock()
        mock_response.iter_content.return_value = [b'fake_mp3_data']
        mock_plex._session.get.return_value = mock_response

        web_app._library_cache[1] = [{
            'ratingKey': 1,
            'title': 'Test Show',
            'has_local_theme': False,
            'has_plex_theme': True,
        }]

        resp = client.post('/api/items/1/theme/download',
                           json={'overwrite': False},
                           content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['item']['has_local_theme'] is True
        assert web_app._library_cache[1][0]['has_local_theme'] is True

    def test_download_theme_no_plex_theme(self, client, mock_plex, tmp_path):
        show = make_mock_show(has_theme=False)
        mock_plex.fetchItem.return_value = show

        resp = client.post('/api/items/1/theme/download', json={})
        assert resp.status_code == 404

    def test_download_theme_already_exists(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'existing_theme')
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show

        resp = client.post('/api/items/1/theme/download', json={'overwrite': False})
        assert resp.status_code == 409
        data = resp.get_json()
        assert data['exists'] is True

    def test_download_theme_overwrite(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'old_theme')
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show
        mock_plex.url.return_value = 'http://plex/theme.mp3'

        mock_response = MagicMock()
        mock_response.iter_content.return_value = [b'new_theme_data']
        mock_plex._session.get.return_value = mock_response

        resp = client.post('/api/items/1/theme/download', json={'overwrite': True})
        assert resp.status_code == 200


class TestThemePreview:
    def test_preview_plex_theme_not_available(self, client, mock_plex):
        show = make_mock_show(has_theme=False)
        mock_plex.fetchItem.return_value = show

        resp = client.get('/api/items/1/theme/preview')

        assert resp.status_code == 404
        assert 'error' in resp.get_json()

    def test_preview_plex_theme_success(self, client, mock_plex):
        show = make_mock_show(has_theme=True)
        mock_plex.fetchItem.return_value = show
        mock_plex.url.return_value = 'http://plex/theme.mp3'
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b'preview_audio']
        mock_plex._session.get.return_value = mock_resp

        resp = client.get('/api/items/1/theme/preview')

        assert resp.status_code == 200
        assert resp.mimetype == 'audio/mpeg'
        assert resp.data == b'preview_audio'

class TestProviderThemeDownload:
    def test_jellyfin_download_from_provider_source_not_supported(self, client):
        resp = client.post('/api/items/jellyfin/abc/theme/download', json={'overwrite': False})
        assert resp.status_code == 400
        assert 'only supported for Plex items' in resp.get_json()['error']

    def test_jellyfin_preview_from_provider_source_not_supported(self, client):
        resp = client.get('/api/items/jellyfin/abc/theme/preview')
        assert resp.status_code == 400
        assert 'only supported for Plex items' in resp.get_json()['error']

    def test_jellyfin_preview_check_from_provider_source_not_supported(self, client):
        resp = client.get('/api/items/jellyfin/abc/theme/preview/check')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['available'] is False
        assert 'only supported for Plex items' in data['reason']


class TestThemeUpload:
    def test_upload_theme(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show

        resp = client.post('/api/items/1/theme/upload',
                           data={'overwrite': 'false',
                                 'file': (io.BytesIO(b'ID3fake_mp3'), 'theme.mp3', 'audio/mpeg')})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True

    def test_upload_no_file(self, client, mock_plex):
        resp = client.post('/api/items/1/theme/upload', data={})
        assert resp.status_code == 400

    def test_upload_overwrite_rejected(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'existing_theme')
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show

        resp = client.post('/api/items/1/theme/upload',
                           data={'overwrite': 'false',
                                 'file': (io.BytesIO(b'ID3fake_mp3'), 'theme.mp3', 'audio/mpeg')})
        assert resp.status_code == 409



    def test_upload_rejects_non_mp3(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show

        resp = client.post('/api/items/1/theme/upload',
                           data={'overwrite': 'false',
                                 'file': (io.BytesIO(b'not_audio'), 'theme.wav', 'audio/wav')})
        assert resp.status_code == 400


class TestProviderThemeUpload:
    def test_upload_theme_jellyfin(self, client, tmp_path):
        jf_dir = tmp_path / 'Jellyfin Show'
        jf_dir.mkdir()
        context = {
            'provider': 'jellyfin',
            'item_id': 'jf-item',
            'title': 'Jellyfin Show',
            'local_path': jf_dir,
        }
        with patch('web_app._get_item_context', return_value=context), \
             patch('web_app._sync_cached_item_theme_state', return_value=(None, False)):
            resp = client.post(
                '/api/items/jellyfin/jf-item/theme/upload',
                data={'overwrite': 'false',
                      'file': (io.BytesIO(b'ID3fake_mp3'), 'theme.mp3', 'audio/mpeg')},
            )
        assert resp.status_code == 200
        assert (jf_dir / 'theme.mp3').exists()


class TestThemeCopy:
    def test_copy_theme_success(self, client, mock_plex, tmp_path):
        source_dir = tmp_path / 'Source Show (2020)'
        target_dir = tmp_path / 'Target Show (2021)'
        source_dir.mkdir()
        target_dir.mkdir()
        (source_dir / 'theme.mp3').write_bytes(b'source_theme_data')

        source = make_mock_show(rating_key=1, title='Source Show', location=str(source_dir))
        target = make_mock_show(rating_key=2, title='Target Show', location=str(target_dir))
        mock_plex.fetchItem.side_effect = lambda rating_key: source if int(rating_key) == 1 else target

        resp = client.post('/api/items/2/theme/copy',
                           json={'sourceRatingKey': 1, 'overwrite': False},
                           content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert (target_dir / 'theme.mp3').read_bytes() == b'source_theme_data'

    def test_copy_theme_requires_source_rating_key(self, client, mock_plex):
        resp = client.post('/api/items/2/theme/copy', json={})
        assert resp.status_code == 400

    def test_copy_theme_rejects_invalid_source_rating_key(self, client, mock_plex):
        resp = client.post('/api/items/2/theme/copy', json={'sourceRatingKey': 'abc'})
        assert resp.status_code == 400

    def test_copy_theme_requires_different_source(self, client, mock_plex):
        resp = client.post('/api/items/2/theme/copy', json={'sourceRatingKey': 2})
        assert resp.status_code == 400

    def test_copy_theme_source_missing_theme(self, client, mock_plex, tmp_path):
        source_dir = tmp_path / 'Source Show (2020)'
        target_dir = tmp_path / 'Target Show (2021)'
        source_dir.mkdir()
        target_dir.mkdir()

        source = make_mock_show(rating_key=1, title='Source Show', location=str(source_dir))
        target = make_mock_show(rating_key=2, title='Target Show', location=str(target_dir))
        mock_plex.fetchItem.side_effect = lambda rating_key: source if int(rating_key) == 1 else target

        resp = client.post('/api/items/2/theme/copy', json={'sourceRatingKey': 1, 'overwrite': False})
        assert resp.status_code == 404

    def test_copy_theme_rejects_existing_target_without_overwrite(self, client, mock_plex, tmp_path):
        source_dir = tmp_path / 'Source Show (2020)'
        target_dir = tmp_path / 'Target Show (2021)'
        source_dir.mkdir()
        target_dir.mkdir()
        (source_dir / 'theme.mp3').write_bytes(b'source_theme_data')
        (target_dir / 'theme.mp3').write_bytes(b'existing_target_theme')

        source = make_mock_show(rating_key=1, title='Source Show', location=str(source_dir))
        target = make_mock_show(rating_key=2, title='Target Show', location=str(target_dir))
        mock_plex.fetchItem.side_effect = lambda rating_key: source if int(rating_key) == 1 else target

        resp = client.post('/api/items/2/theme/copy', json={'sourceRatingKey': 1, 'overwrite': False})
        assert resp.status_code == 409
        assert resp.get_json()['exists'] is True

    def test_copy_theme_overwrites_existing_target(self, client, mock_plex, tmp_path):
        source_dir = tmp_path / 'Source Show (2020)'
        target_dir = tmp_path / 'Target Show (2021)'
        source_dir.mkdir()
        target_dir.mkdir()
        (source_dir / 'theme.mp3').write_bytes(b'new_theme_data')
        (target_dir / 'theme.mp3').write_bytes(b'old_theme_data')

        source = make_mock_show(rating_key=1, title='Source Show', location=str(source_dir))
        target = make_mock_show(rating_key=2, title='Target Show', location=str(target_dir))
        mock_plex.fetchItem.side_effect = lambda rating_key: source if int(rating_key) == 1 else target

        resp = client.post('/api/items/2/theme/copy', json={'sourceRatingKey': 1, 'overwrite': True})
        assert resp.status_code == 200
        assert (target_dir / 'theme.mp3').read_bytes() == b'new_theme_data'

    def test_copy_theme_updates_cached_item_state(self, client, mock_plex, tmp_path):
        import web_app

        source_dir = tmp_path / 'Source Show (2020)'
        target_dir = tmp_path / 'Target Show (2021)'
        source_dir.mkdir()
        target_dir.mkdir()
        (source_dir / 'theme.mp3').write_bytes(b'source_theme_data')

        source = make_mock_show(rating_key=1, title='Source Show', location=str(source_dir))
        target = make_mock_show(rating_key=2, title='Target Show', location=str(target_dir))
        mock_plex.fetchItem.side_effect = lambda rating_key: source if int(rating_key) == 1 else target

        web_app._library_cache[1] = [{
            'ratingKey': 2,
            'title': 'Target Show',
            'has_local_theme': False,
            'has_plex_theme': True,
        }]

        resp = client.post('/api/items/2/theme/copy', json={'sourceRatingKey': 1, 'overwrite': False})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['item']['has_local_theme'] is True
        assert web_app._library_cache[1][0]['has_local_theme'] is True


class TestThemeYoutube:
    def test_youtube_missing_url(self, client, mock_plex):
        resp = client.post('/api/items/1/theme/youtube', json={}, content_type='application/json')
        assert resp.status_code == 400

    def test_youtube_rejects_non_youtube_url(self, client, mock_plex):
        resp = client.post('/api/items/1/theme/youtube',
                           json={'url': 'https://example.com/audio.mp3'},
                           content_type='application/json')
        assert resp.status_code == 400

    def test_youtube_already_exists(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'existing')
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show

        resp = client.post('/api/items/1/theme/youtube',
                           json={'url': 'https://youtube.com/watch?v=test', 'overwrite': False})
        assert resp.status_code == 409

    def test_youtube_download(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show

        fake_tmpdir = tmp_path / 'ytdl_tmp'
        fake_tmpdir.mkdir()
        (fake_tmpdir / 'theme.mp3').write_bytes(b'youtube_audio')

        with patch('web_app.yt_dlp') as mock_ytdlp, \
             patch('tempfile.TemporaryDirectory') as mock_tmpdir:
            mock_tmpdir.return_value.__enter__ = lambda s: str(fake_tmpdir)
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            mock_ydl = MagicMock()
            mock_ytdlp.YoutubeDL.return_value.__enter__ = lambda s: mock_ydl
            mock_ytdlp.YoutubeDL.return_value.__exit__ = MagicMock(return_value=False)

            resp = client.post('/api/items/1/theme/youtube',
                               json={'url': 'https://youtube.com/watch?v=test', 'overwrite': False})
        assert resp.status_code == 200


class TestThemerrDB:
    def test_query_themerrdb_caches_not_found_results(self):
        import web_app

        web_app._themerrdb_cache.clear()
        mock_response = MagicMock()
        mock_response.status_code = 404
        with patch('web_app.http_requests.get', return_value=mock_response) as mock_get:
            first = web_app.query_themerrdb('movies', 'imdb', 'tt0000001')
            second = web_app.query_themerrdb('movies', 'imdb', 'tt0000001')

        assert first is None
        assert second is None
        assert mock_get.call_count == 1

    def test_get_themerrdb_theme_reuses_cached_result_across_item_ids(self):
        import web_app

        web_app._themerrdb_cache.clear()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'youtube_theme_url': 'https://youtube.com/watch?v=test'}
        with patch('web_app.http_requests.get', return_value=mock_response) as mock_get:
            first = web_app.get_themerrdb_theme_for_external_ids('movie', {'imdb': 'tt1234567', 'tmdb': '123', 'tvdb': None})
            second = web_app.get_themerrdb_theme_for_external_ids('movie', {'imdb': None, 'tmdb': '123', 'tvdb': None})

        assert first is not None
        assert second is not None
        assert mock_get.call_count == 1

    def test_check_themerrdb_available(self, client, mock_plex):
        show = make_mock_show()
        show.guids = [{'id': 'imdb://tt1234567'}]
        mock_plex.fetchItem.return_value = show

        with patch('web_app._get_themerrdb_data_for_context', return_value={'youtube_theme_url': 'https://youtube.com/watch?v=test'}), \
             patch('web_app._extract_youtube_audio_url', return_value='https://audio.example/stream'):
            resp = client.get('/api/items/1/theme/themerrdb/check')

        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload['available'] is True
        assert payload['youtube_url'] == 'https://youtube.com/watch?v=test'

    def test_check_themerrdb_unavailable(self, client, mock_plex):
        show = make_mock_show()
        show.guids = []
        mock_plex.fetchItem.return_value = show

        with patch('web_app.get_themerrdb_theme', return_value=None):
            resp = client.get('/api/items/1/theme/themerrdb/check')

        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload['available'] is False
        assert 'reason' in payload

    def test_preview_themerrdb_not_found(self, client, mock_plex):
        show = make_mock_show()
        mock_plex.fetchItem.return_value = show

        with patch('web_app.get_themerrdb_theme', return_value=None):
            resp = client.get('/api/items/1/theme/themerrdb/preview')

        assert resp.status_code == 404
        assert 'error' in resp.get_json()

    def test_preview_themerrdb_success(self, client, mock_plex):
        show = make_mock_show()
        mock_plex.fetchItem.return_value = show

        with patch('web_app.get_themerrdb_theme', return_value={'youtube_theme_url': 'https://youtube.com/watch?v=test'}), \
             patch('web_app._extract_youtube_audio_url', return_value='https://rr1---sn-test.googlevideo.com/stream'), \
             patch('web_app.http_requests') as mock_requests:

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

        with patch('web_app.get_themerrdb_theme', return_value={'youtube_theme_url': 'https://youtube.com/watch?v=test'}), \
             patch('web_app.yt_dlp') as mock_ytdlp, \
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
        with patch('web_app._get_item_context', return_value=context), \
             patch('web_app._get_themerrdb_data_for_context', return_value={'youtube_theme_url': 'https://youtube.com/watch?v=test'}), \
             patch('web_app._extract_youtube_audio_url', return_value='https://audio.example/stream'), \
             patch('web_app._get_external_ids_for_context', return_value={'imdb': 'tt1234567', 'tmdb': None, 'tvdb': None}):
            resp = client.get('/api/items/jellyfin/jf-1/theme/themerrdb/check')

        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload['available'] is True
        assert payload['youtube_url'] == 'https://youtube.com/watch?v=test'


class TestYoutubeSearch:
    def test_search_missing_query(self, client):
        resp = client.get('/api/youtube/search')
        assert resp.status_code == 400
        assert 'error' in resp.get_json()

    def test_search_empty_query(self, client):
        resp = client.get('/api/youtube/search?q=')
        assert resp.status_code == 400

    def test_search_returns_results(self, client, monkeypatch):
        mock_info = {
            'entries': [
                {
                    'id': 'abc123',
                    'title': 'Breaking Bad Main Theme',
                    'url': 'https://www.youtube.com/watch?v=abc123',
                    'channel': 'Dave Porter',
                    'duration': 76.0,
                    'thumbnails': [{'url': 'https://i.ytimg.com/vi/abc123/hq720.jpg', 'height': 202, 'width': 360}],
                    'view_count': 14687926,
                }
            ]
        }

        class _FakeYDL:
            def __init__(self, opts): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def extract_info(self, *a, **kw): return mock_info

        monkeypatch.setattr('yt_dlp.YoutubeDL', _FakeYDL)

        resp = client.get('/api/youtube/search?q=Breaking+Bad+theme')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'results' in data
        assert len(data['results']) == 1
        r = data['results'][0]
        assert r['id'] == 'abc123'
        assert r['title'] == 'Breaking Bad Main Theme'
        assert r['channel'] == 'Dave Porter'
        assert r['duration'] == '1:16'
        assert r['view_count'] == 14687926
        assert 'youtube.com/watch?v=abc123' in r['url']

    def test_search_no_results(self, client, monkeypatch):
        class _FakeYDL:
            def __init__(self, opts): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def extract_info(self, *a, **kw): return {'entries': []}

        monkeypatch.setattr('yt_dlp.YoutubeDL', _FakeYDL)

        resp = client.get('/api/youtube/search?q=very+obscure+thing')
        assert resp.status_code == 200
        assert resp.get_json()['results'] == []

    def test_search_limit_capped_at_10(self, client, monkeypatch):
        captured = {}

        class _FakeYDL:
            def __init__(self, opts): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def extract_info(self, query, **kw):
                captured['query'] = query
                return {'entries': []}

        monkeypatch.setattr('yt_dlp.YoutubeDL', _FakeYDL)

        client.get('/api/youtube/search?q=test&limit=50')
        assert captured['query'].startswith('ytsearch10:')

    def test_search_duration_formatting(self, client, monkeypatch):
        mock_info = {
            'entries': [
                {'id': 'x1', 'title': 'T1', 'url': 'https://www.youtube.com/watch?v=x1',
                 'duration': 3661.0, 'thumbnails': [], 'view_count': None, 'channel': None},
            ]
        }

        class _FakeYDL:
            def __init__(self, opts): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def extract_info(self, *a, **kw): return mock_info

        monkeypatch.setattr('yt_dlp.YoutubeDL', _FakeYDL)

        resp = client.get('/api/youtube/search?q=test')
        r = resp.get_json()['results'][0]
        assert r['duration'] == '61:01'

    def test_search_no_duration(self, client, monkeypatch):
        mock_info = {
            'entries': [
                {'id': 'x2', 'title': 'T2', 'url': 'https://www.youtube.com/watch?v=x2',
                 'duration': None, 'thumbnails': [], 'view_count': None, 'channel': None},
            ]
        }

        class _FakeYDL:
            def __init__(self, opts): pass
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def extract_info(self, *a, **kw): return mock_info

        monkeypatch.setattr('yt_dlp.YoutubeDL', _FakeYDL)

        resp = client.get('/api/youtube/search?q=test')
        r = resp.get_json()['results'][0]
        assert r['duration'] is None


class TestThemeDelete:
    def test_delete_theme(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'theme_data')
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show

        resp = client.delete('/api/items/1/theme')
        assert resp.status_code == 200
        assert not (show_dir / 'theme.mp3').exists()

    def test_delete_nonexistent_theme(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show

        resp = client.delete('/api/items/1/theme')
        assert resp.status_code == 404


class TestGetTheme:
    def test_get_theme_file(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'\xff\xfb' * 100)
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show

        resp = client.get('/api/items/1/theme')
        assert resp.status_code == 200

    def test_get_theme_not_found(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show

        resp = client.get('/api/items/1/theme')
        assert resp.status_code == 404


class TestPosterCache:
    def test_get_poster_serves_from_in_memory_cache(self, client, mock_plex):
        import web_app

        web_app._set_cached_poster(1, b'cached_poster', 'image/jpeg')

        resp = client.get('/api/poster/1')
        assert resp.status_code == 200
        assert resp.data == b'cached_poster'
        mock_plex.fetchItem.assert_not_called()

    def test_get_poster_populates_cache_on_first_fetch(self, client, mock_plex):
        import web_app

        show = make_mock_show(rating_key=1)
        mock_plex.fetchItem.return_value = show
        mock_plex.url.return_value = 'http://plex/poster.jpg'
        mock_resp = MagicMock()
        mock_resp.content = b'poster_data'
        mock_resp.headers = {'content-type': 'image/jpeg'}
        mock_plex._session.get.return_value = mock_resp

        resp = client.get('/api/poster/1')
        assert resp.status_code == 200
        assert resp.data == b'poster_data'
        assert web_app._get_cached_poster(1)['content'] == b'poster_data'


class TestIndexPage:
    def test_index_loads(self, client):
        with patch.dict(os.environ, {'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret', 'DISABLE_AUTH': ''}):
            resp = client.get('/')
        assert resp.status_code == 200
        assert b'Themarr' in resp.data
        assert b'id="library-nav"' in resp.data
        assert b'id="items-grid"' in resp.data
        assert b'/static/js/app.js' in resp.data

    def test_index_avoids_inline_script_handlers_for_csp(self, client):
        with patch.dict(os.environ, {'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret', 'DISABLE_AUTH': ''}):
            resp = client.get('/')
        assert resp.status_code == 200
        assert b' onclick=' not in resp.data
        assert b' onchange=' not in resp.data
        assert b' oninput=' not in resp.data
        assert b' onsubmit=' not in resp.data
        assert b' style=' not in resp.data
        assert b'<script>' not in resp.data

    def test_index_shows_warning_when_auth_credentials_missing(self, client):
        with patch.dict(os.environ, {'AUTH_USERNAME': '', 'AUTH_PASSWORD': '', 'DISABLE_AUTH': ''}):
            resp = client.get('/')
        assert resp.status_code == 200
        assert b'Web UI authentication is not configured' in resp.data
        assert b'/static/js/app.js' not in resp.data


# ============================================================
# Bulk download tests
# ============================================================

class TestBulkDownload:
    def test_bulk_download_success(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show
        mock_plex.url.return_value = 'http://plex/theme.mp3'
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b'audio_data']
        mock_plex._session.get.return_value = mock_resp

        resp = client.post('/api/bulk/theme/download',
                           json={'ratingKeys': [1], 'overwrite': False})
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data['success']) == 1
        assert data['success'][0]['ratingKey'] == 1

    def test_bulk_download_missing_rating_keys(self, client, mock_plex):
        resp = client.post('/api/bulk/theme/download', json={})
        assert resp.status_code == 400

    def test_bulk_download_empty_list(self, client, mock_plex):
        resp = client.post('/api/bulk/theme/download', json={'ratingKeys': []})
        assert resp.status_code == 400

    def test_bulk_download_too_many_items(self, client, mock_plex):
        resp = client.post('/api/bulk/theme/download',
                           json={'ratingKeys': list(range(101))})
        assert resp.status_code == 400

    def test_bulk_download_skips_existing(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'existing')
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show

        resp = client.post('/api/bulk/theme/download',
                           json={'ratingKeys': [1], 'overwrite': False})
        data = resp.get_json()
        assert len(data['skipped']) == 1

    def test_bulk_download_no_plex_theme(self, client, mock_plex, tmp_path):
        show = make_mock_show(has_theme=False)
        mock_plex.fetchItem.return_value = show

        resp = client.post('/api/bulk/theme/download',
                           json={'ratingKeys': [1], 'overwrite': False})
        data = resp.get_json()
        assert len(data['no_theme']) == 1

    def test_bulk_download_overwrite(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'old')
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show
        mock_plex.url.return_value = 'http://plex/theme.mp3'
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b'new_audio']
        mock_plex._session.get.return_value = mock_resp

        resp = client.post('/api/bulk/theme/download',
                           json={'ratingKeys': [1], 'overwrite': True})
        data = resp.get_json()
        assert len(data['success']) == 1

    def test_bulk_download_updates_cached_item_state(self, client, mock_plex, tmp_path):
        import web_app

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show
        mock_plex.url.return_value = 'http://plex/theme.mp3'
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b'new_audio']
        mock_plex._session.get.return_value = mock_resp

        web_app._library_cache[1] = [{
            'ratingKey': 1,
            'title': 'Test Show',
            'has_local_theme': False,
            'has_plex_theme': True,
        }]

        resp = client.post('/api/bulk/theme/download',
                           json={'ratingKeys': [1], 'overwrite': False})
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data['success']) == 1
        assert web_app._library_cache[1][0]['has_local_theme'] is True


# ============================================================
# Webhook tests
# ============================================================

class TestPlexWebhook:
    @staticmethod
    def _post_webhook(client, payload, headers=None):
        import json
        plex = MagicMock()
        plex.machineIdentifier = 'configured-server-uuid'
        with patch('web_app.get_plex', return_value=plex):
            return client.post('/api/webhooks/plex', data={'payload': json.dumps(payload)}, headers=headers or {})

    def test_library_new_event_queues_theme_processing(self, client):
        """library.new event with valid ratingKey queues theme processing."""
        with patch('web_app._submit_background_job') as mock_submit:
            payload = {'event': 'library.new', 'Metadata': {'ratingKey': '12345'}, 'Server': {'uuid': 'configured-server-uuid'}}
            resp = self._post_webhook(client, payload)
        
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        mock_submit.assert_called_once()

    def test_library_new_event_with_metadata_fallback(self, client):
        """Handles both 'Metadata' and 'metadata' field names."""
        with patch('web_app._submit_background_job') as mock_submit:
            payload = {'event': 'library.new', 'metadata': {'ratingKey': '67890'}, 'Server': {'uuid': 'configured-server-uuid'}}
            resp = self._post_webhook(client, payload)
        
        assert resp.status_code == 200
        mock_submit.assert_called_once()

    def test_missing_payload_handled_gracefully(self, client):
        """Missing payload field returns 200 without error."""
        resp = client.post('/api/webhooks/plex', data={})
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    def test_invalid_json_payload_handled_gracefully(self, client):
        """Invalid JSON payload returns 200 without error."""
        resp = client.post('/api/webhooks/plex', data={'payload': 'not-json'})
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    def test_library_new_without_rating_key_logged(self, client):
        """library.new event without ratingKey is logged."""
        payload = {'event': 'library.new', 'Metadata': {}, 'Server': {'uuid': 'configured-server-uuid'}}
        resp = self._post_webhook(client, payload)
        assert resp.status_code == 200

    def test_non_library_new_event_ignored(self, client):
        """Non-library.new events are safely ignored."""
        payload = {'event': 'library.update', 'Metadata': {'ratingKey': '12345'}, 'Server': {'uuid': 'configured-server-uuid'}}
        with patch('web_app._submit_background_job') as mock_submit:
            resp = self._post_webhook(client, payload)
        assert resp.status_code == 200
        mock_submit.assert_not_called()

    def test_webhook_rejects_missing_basic_auth_when_configured(self, client):
        payload = {'event': 'library.new', 'Metadata': {'ratingKey': '12345'}, 'Server': {'uuid': 'configured-server-uuid'}}
        with patch.dict(os.environ, {'WEBHOOK_USERNAME': 'plex', 'WEBHOOK_PASSWORD': 'secret'}, clear=False):
            resp = self._post_webhook(client, payload)
        assert resp.status_code == 401

    def test_webhook_accepts_valid_basic_auth_when_configured(self, client):
        payload = {'event': 'library.new', 'Metadata': {'ratingKey': '12345'}, 'Server': {'uuid': 'configured-server-uuid'}}
        auth = base64.b64encode(b'plex:secret').decode('ascii')

        with patch.dict(os.environ, {'WEBHOOK_USERNAME': 'plex', 'WEBHOOK_PASSWORD': 'secret'}, clear=False), \
             patch('web_app._submit_background_job') as mock_submit:
            resp = self._post_webhook(client, payload, headers={'Authorization': f'Basic {auth}'})

        assert resp.status_code == 200
        mock_submit.assert_called_once()

    def test_webhook_rejects_missing_server_uuid(self, client):
        payload = {'event': 'library.new', 'Metadata': {'ratingKey': '12345'}}
        resp = self._post_webhook(client, payload)
        assert resp.status_code == 400

    def test_webhook_rejects_uuid_mismatch(self, client):
        payload = {'event': 'library.new', 'Metadata': {'ratingKey': '12345'}, 'Server': {'uuid': 'other-server-uuid'}}
        resp = self._post_webhook(client, payload)
        assert resp.status_code == 403

    def test_process_library_new_downloads_theme_when_missing(self, tmp_path):
        import web_app

        show_dir = tmp_path / 'New Show (2024)'
        show_dir.mkdir()
        show = make_mock_show(title='New Show', location=str(show_dir), has_theme=True)

        plex = MagicMock()
        plex.library.fetchItem.return_value = show

        with patch('web_app.get_plex', return_value=plex), \
             patch('web_app._download_plex_theme_to_path') as mock_download, \
             patch('web_app.send_pushover_notification') as mock_notify:
            web_app._process_plex_library_new('123')

        mock_download.assert_called_once()
        mock_notify.assert_called_once()

    def test_process_library_new_skips_when_theme_exists(self, tmp_path):
        import web_app

        show_dir = tmp_path / 'Existing Show (2024)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'existing')
        show = make_mock_show(title='Existing Show', location=str(show_dir), has_theme=True)

        plex = MagicMock()
        plex.library.fetchItem.return_value = show

        with patch('web_app.get_plex', return_value=plex), \
             patch('web_app._download_plex_theme_to_path') as mock_download:
            web_app._process_plex_library_new('123')

        mock_download.assert_not_called()


# ============================================================
# Pushover notification tests
# ============================================================

class TestPushoverNotification:
    def test_no_op_without_config(self):
        """send_pushover_notification does nothing if env vars are missing."""
        from web_app import send_pushover_notification
        with patch('web_app.http_requests') as mock_req, \
             patch.dict(os.environ, {}, clear=True):
            send_pushover_notification('Test', 'body')
            mock_req.post.assert_not_called()

    def test_sends_with_config(self):
        from web_app import send_pushover_notification
        with patch('web_app.http_requests') as mock_req, \
             patch.dict(os.environ, {'PUSHOVER_APP_TOKEN': 'tok', 'PUSHOVER_USER_KEY': 'usr'}):
            mock_resp = MagicMock()
            mock_req.post.return_value = mock_resp
            send_pushover_notification('Title', 'Body text')
        mock_req.post.assert_called_once()
        call_kwargs = mock_req.post.call_args
        data = call_kwargs[1]['data'] if 'data' in call_kwargs[1] else call_kwargs[0][1]
        assert data['token'] == 'tok'
        assert data['user'] == 'usr'

    def test_handles_request_failure_gracefully(self):
        from web_app import send_pushover_notification
        with patch('web_app.http_requests') as mock_req, \
             patch.dict(os.environ, {'PUSHOVER_APP_TOKEN': 'tok', 'PUSHOVER_USER_KEY': 'usr'}):
            mock_req.post.side_effect = Exception('Network error')
            # Should not raise
            send_pushover_notification('Title', 'Body text')

    def test_pushover_called_on_download(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show
        mock_plex.url.return_value = 'http://plex/theme.mp3'
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b'mp3data']
        mock_plex._session.get.return_value = mock_resp

        with patch('web_app.send_pushover_notification') as mock_notif:
            resp = client.post('/api/items/1/theme/download', json={'overwrite': False})
        assert resp.status_code == 200
        mock_notif.assert_called_once()

# ============================================================
# Settings endpoint tests
# ============================================================

class TestSettingsTestPushover:
    def test_returns_400_when_not_configured(self, client):
        with patch.dict(os.environ, {}, clear=True):
            resp = client.post('/api/settings/test-pushover')
        assert resp.status_code == 400
        assert 'PUSHOVER_APP_TOKEN' in resp.get_json()['error']

    def test_success_with_valid_config(self, client):
        with patch('web_app.http_requests') as mock_req, \
             patch.dict(os.environ, {'PUSHOVER_APP_TOKEN': 'tok', 'PUSHOVER_USER_KEY': 'usr'}):
            mock_resp = MagicMock()
            mock_req.post.return_value = mock_resp
            resp = client.post('/api/settings/test-pushover')
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    def test_returns_500_on_pushover_error(self, client):
        with patch('web_app.http_requests') as mock_req, \
             patch.dict(os.environ, {'PUSHOVER_APP_TOKEN': 'tok', 'PUSHOVER_USER_KEY': 'usr'}):
            mock_req.post.side_effect = Exception('Network error')
            resp = client.post('/api/settings/test-pushover')
        assert resp.status_code == 500


class TestSettingsRescan:
    def test_rescan_counts_themes(self, client, mock_plex, tmp_path):
        show_a_dir = tmp_path / 'Show A'
        show_a_dir.mkdir()
        (show_a_dir / 'theme.mp3').write_bytes(b'\xff\xfb' * 100)

        show_b_dir = tmp_path / 'Show B'
        show_b_dir.mkdir()  # no theme

        show1 = make_mock_show(rating_key=1, title='Show A', location=str(show_a_dir))
        show2 = make_mock_show(rating_key=2, title='Show B', location=str(show_b_dir))

        section = MagicMock()
        section.type = 'show'
        section.all.return_value = [show1, show2]
        section.locations = [str(tmp_path)]
        mock_plex.library.sections.return_value = [section]

        resp = client.post('/api/settings/rescan')

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['total'] == 2
        assert data['with_theme'] == 1
        assert data['without_theme'] == 1

    def test_rescan_returns_error_on_plex_failure(self, client):
        with patch('web_app.get_plex', side_effect=Exception('Plex error')):
            resp = client.post('/api/settings/rescan')
        assert resp.status_code == 500
        assert 'error' in resp.get_json()


class TestSettingsRefreshCache:
    def test_refresh_cache_starts_background_warmup(self, client):
        with patch('web_app._kick_off_cache_warmup') as mock_warmup:
            mock_warmup.return_value = True
            resp = client.post('/api/settings/refresh-cache')

        assert resp.status_code == 200
        assert resp.get_json()['success'] is True
        mock_warmup.assert_called_once()


class TestApiAuth:
    def test_mutating_endpoint_requires_api_key_when_configured(self, app):
        unauthenticated_client = app.test_client()
        with patch.dict(os.environ, {'API_KEY': 'secret-key'}):
            resp = unauthenticated_client.post('/api/settings/refresh-cache')
        assert resp.status_code == 401

    def test_mutating_endpoint_accepts_valid_api_key_header(self, client):
        with patch.dict(os.environ, {'API_KEY': 'secret-key'}), \
             patch('web_app._kick_off_cache_warmup', return_value=True):
            resp = client.post('/api/settings/refresh-cache', headers={'X-Themarr-Api-Key': 'secret-key'})
        assert resp.status_code == 200

    def test_settings_runtime_requires_auth(self, app):
        unauthenticated_client = app.test_client()
        resp = unauthenticated_client.get('/api/settings/runtime')
        assert resp.status_code == 401


class TestApiInit:
    def test_init_returns_200_without_auth(self, app):
        """GET /api/init is always public."""
        with app.test_client() as c:
            resp = c.get('/api/init')
        assert resp.status_code == 200

    def test_init_misconfigured_mode_unauthenticated(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': '', 'AUTH_USERNAME': '', 'AUTH_PASSWORD': ''}):
            with app.test_client() as c:
                data = c.get('/api/init').get_json()
        assert data['auth_required'] is False
        assert data['authenticated'] is False
        assert data['auth_mode'] == 'misconfigured'
        assert data['auth_misconfigured'] is True

    def test_init_credentials_mode_unauthenticated(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': '', 'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret'}):
            with app.test_client() as c:
                data = c.get('/api/init').get_json()
        assert data['auth_required'] is True
        assert data['authenticated'] is False
        assert data['auth_mode'] == 'credentials'

    def test_init_disable_auth_mode(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': 'true', 'AUTH_USERNAME': '', 'AUTH_PASSWORD': ''}):
            with app.test_client() as c:
                data = c.get('/api/init').get_json()
        assert data['auth_required'] is False
        assert data['authenticated'] is True
        assert data['auth_mode'] == 'disabled'

    def test_init_authenticated_after_session_login(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': '', 'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret'}):
            with app.test_client() as c:
                c.post('/api/auth/login', json={'username': 'admin', 'password': 'secret'})
                data = c.get('/api/init').get_json()
        assert data['authenticated'] is True


class TestCredentialsLogin:
    def test_credentials_login_success(self, app):
        with patch.dict(os.environ, {'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret', 'DISABLE_AUTH': ''}):
            with app.test_client() as c:
                resp = c.post('/api/auth/login', json={'username': 'admin', 'password': 'secret'})
        assert resp.status_code == 200
        assert resp.get_json()['ok'] is True
        assert resp.get_json()['auth_mode'] == 'credentials'

    def test_credentials_login_wrong_password(self, app):
        with patch.dict(os.environ, {'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret', 'DISABLE_AUTH': ''}):
            with app.test_client() as c:
                resp = c.post('/api/auth/login', json={'username': 'admin', 'password': 'wrong'})
        assert resp.status_code == 401

    def test_credentials_login_wrong_username(self, app):
        with patch.dict(os.environ, {'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret', 'DISABLE_AUTH': ''}):
            with app.test_client() as c:
                resp = c.post('/api/auth/login', json={'username': 'hacker', 'password': 'secret'})
        assert resp.status_code == 401

    def test_credentials_login_empty_fields(self, app):
        with patch.dict(os.environ, {'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret', 'DISABLE_AUTH': ''}):
            with app.test_client() as c:
                resp = c.post('/api/auth/login', json={'username': '', 'password': ''})
        assert resp.status_code == 401

    def test_credentials_login_sets_session(self, app):
        with patch.dict(os.environ, {'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret',
                                     'DISABLE_AUTH': '', 'API_KEY': 'key'}):
            with app.test_client() as c:
                c.post('/api/auth/login', json={'username': 'admin', 'password': 'secret'})
                resp = c.get('/api/settings/runtime')
        assert resp.status_code == 200

    def test_api_key_login_rejected_in_credentials_mode(self, app):
        """Submitting only an API key in credentials mode should fail because username/password are required."""
        with patch.dict(os.environ, {'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret',
                                     'DISABLE_AUTH': '', 'API_KEY': 'key'}):
            with app.test_client() as c:
                resp = c.post('/api/auth/login', json={'api_key': 'key'})
        assert resp.status_code == 401


class TestDisableAuth:
    def test_disable_auth_allows_mutating_requests(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': 'true'}):
            with app.test_client() as c:
                with patch('web_app._kick_off_cache_warmup', return_value=True):
                    resp = c.post('/api/settings/refresh-cache')
        assert resp.status_code == 200

    def test_disable_auth_allows_settings_runtime(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': 'true', 'API_KEY': 'key'}):
            with app.test_client() as c:
                resp = c.get('/api/settings/runtime')
        assert resp.status_code == 200

    def test_disable_auth_login_establishes_session(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': 'true', 'AUTH_USERNAME': '', 'AUTH_PASSWORD': ''}):
            with app.test_client() as c:
                resp = c.post('/api/auth/login', json={})
        assert resp.status_code == 200
        assert resp.get_json()['auth_mode'] == 'disabled'


# =============================================================================
# Regression tests for confirmed bugs (multi-agent audit)
# =============================================================================

class TestYoutubeSearchOpts:
    """BUG-001 / BUG-008 — yt-dlp option hygiene in youtube_search."""

    def test_youtube_search_opts_do_not_include_remote_components(self):
        """remote_components must never appear in youtube_search ydl_opts (supply-chain RCE)."""
        import web_app
        # Capture the ydl_opts dict built inside youtube_search by intercepting YoutubeDL.__init__
        captured = {}
        original_init = None

        class CapturingYDL:
            def __init__(self, opts):
                captured['opts'] = dict(opts)
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def extract_info(self, *a, **kw):
                return {'entries': []}

        with patch('web_app.yt_dlp.YoutubeDL', CapturingYDL):
            with patch.dict(os.environ, {'DISABLE_AUTH': 'true'}):
                with web_app.app.test_client() as c:
                    c.get('/api/youtube/search?q=test')

        assert 'remote_components' not in captured.get('opts', {}), (
            "remote_components must not be passed to yt-dlp (supply-chain RCE risk)"
        )
        assert 'js_runtimes' not in captured.get('opts', {}), (
            "js_runtimes must not be passed to yt-dlp"
        )

    def test_youtube_search_opts_include_socket_timeout(self):
        """youtube_search ydl_opts should include socket_timeout for reliability."""
        import web_app
        captured = {}

        class CapturingYDL:
            def __init__(self, opts):
                captured['opts'] = dict(opts)
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def extract_info(self, *a, **kw):
                return {'entries': []}

        with patch('web_app.yt_dlp.YoutubeDL', CapturingYDL):
            with patch.dict(os.environ, {'DISABLE_AUTH': 'true'}):
                with web_app.app.test_client() as c:
                    c.get('/api/youtube/search?q=test')

        assert 'socket_timeout' in captured.get('opts', {}), (
            "socket_timeout should be set to bound yt-dlp search requests"
        )

    def test_youtube_search_query_length_cap(self):
        """Queries longer than 200 chars should be rejected with 400."""
        import web_app
        with patch.dict(os.environ, {'DISABLE_AUTH': 'true'}):
            with web_app.app.test_client() as c:
                resp = c.get(f'/api/youtube/search?q={"a" * 201}')
        assert resp.status_code == 400


class TestWebhookPartialAuth:
    """BUG-005 — Partial webhook Basic Auth should return 503, not silently accept."""

    def test_partial_auth_only_username_returns_503(self, app):
        with patch.dict(os.environ, {
            'WEBHOOK_USERNAME': 'user', 'WEBHOOK_PASSWORD': '',
            'DISABLE_AUTH': 'true',
        }):
            with app.test_client() as c:
                resp = c.post('/api/webhooks/plex',
                              data='{}', content_type='application/json')
        assert resp.status_code == 503

    def test_partial_auth_only_password_returns_503(self, app):
        with patch.dict(os.environ, {
            'WEBHOOK_USERNAME': '', 'WEBHOOK_PASSWORD': 'secret',
            'DISABLE_AUTH': 'true',
        }):
            with app.test_client() as c:
                resp = c.post('/api/webhooks/plex',
                              data='{}', content_type='application/json')
        assert resp.status_code == 503

    def test_no_auth_config_accepts_request(self, app):
        """When neither credential is set, webhook auth is intentionally disabled."""
        with patch.dict(os.environ, {
            'WEBHOOK_USERNAME': '', 'WEBHOOK_PASSWORD': '',
            'DISABLE_AUTH': 'true',
        }):
            with app.test_client() as c:
                resp = c.post('/api/webhooks/plex',
                              data='{}', content_type='application/json')
        # Request gets past auth check (may fail for other reasons, but not 503)
        assert resp.status_code != 503


class TestGetEndpointsRequireAuth:
    """FINDING-02 — All GET /api/ endpoints except the public allowlist require auth."""

    def test_get_libraries_requires_auth(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': '', 'AUTH_USERNAME': 'u', 'AUTH_PASSWORD': 'p'}):
            with app.test_client() as c:
                resp = c.get('/api/libraries')
        assert resp.status_code == 401

    def test_get_status_requires_auth(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': '', 'AUTH_USERNAME': 'u', 'AUTH_PASSWORD': 'p'}):
            with app.test_client() as c:
                resp = c.get('/api/status')
        assert resp.status_code == 401

    def test_get_youtube_search_requires_auth(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': '', 'AUTH_USERNAME': 'u', 'AUTH_PASSWORD': 'p'}):
            with app.test_client() as c:
                resp = c.get('/api/youtube/search?q=test')
        assert resp.status_code == 401

    def test_cache_status_is_public(self, app):
        """cache/status must remain unauthenticated for the startup overlay."""
        with patch.dict(os.environ, {'DISABLE_AUTH': '', 'AUTH_USERNAME': 'u', 'AUTH_PASSWORD': 'p'}):
            with app.test_client() as c:
                resp = c.get('/api/cache/status')
        assert resp.status_code == 200

    def test_init_is_public(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': '', 'AUTH_USERNAME': 'u', 'AUTH_PASSWORD': 'p'}):
            with app.test_client() as c:
                resp = c.get('/api/init')
        assert resp.status_code == 200


class TestSecurityHeaders:
    """FINDING-09 — Security response headers should be present on all responses."""

    def test_security_headers_on_api_response(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': 'true'}):
            with app.test_client() as c:
                resp = c.get('/api/init')
        assert resp.headers.get('X-Content-Type-Options') == 'nosniff'
        assert resp.headers.get('X-Frame-Options') == 'DENY'
        assert 'Referrer-Policy' in resp.headers
        assert 'Content-Security-Policy' in resp.headers
        csp = resp.headers.get('Content-Security-Policy', '')
        assert "'unsafe-inline'" not in csp
        assert "style-src 'self';" in csp

        directives = {}
        for raw_directive in csp.split(';'):
            directive = raw_directive.strip()
            if not directive:
                continue
            parts = directive.split()
            if parts:
                directives[parts[0]] = parts[1:]

        img_src_hosts = []
        for source in directives.get('img-src', []):
            parsed = urlparse(source)
            if parsed.scheme == 'https' and parsed.hostname:
                img_src_hosts.append(parsed.hostname)
        assert 'i.ytimg.com' in img_src_hosts

    def test_security_headers_on_html_response(self, app):
        with app.test_client() as c:
            resp = c.get('/')
        assert resp.headers.get('X-Content-Type-Options') == 'nosniff'
        assert resp.headers.get('X-Frame-Options') == 'DENY'
        csp = resp.headers.get('Content-Security-Policy', '')
        assert "'unsafe-inline'" not in csp


class TestMp3MagicByte:
    """FINDING-06 — Upload validation must check MP3 magic bytes."""

    def test_valid_id3_header_accepted(self, app):
        from app.media_utils import _is_valid_mp3_magic
        f = io.BytesIO(b'ID3\x03\x00\x00\x00\x00\x00\x00' + b'\x00' * 100)
        assert _is_valid_mp3_magic(f) is True
        assert f.read(3) == b'ID3'  # stream rewound

    def test_valid_sync_word_accepted(self, app):
        from app.media_utils import _is_valid_mp3_magic
        f = io.BytesIO(b'\xff\xfb\x90\x00' + b'\x00' * 100)
        assert _is_valid_mp3_magic(f) is True

    def test_invalid_bytes_rejected(self, app):
        from app.media_utils import _is_valid_mp3_magic
        f = io.BytesIO(b'PK\x03\x04' + b'\x00' * 100)  # ZIP magic
        assert _is_valid_mp3_magic(f) is False

    def test_octet_stream_not_in_allowed_types(self):
        from app.media_utils import ALLOWED_UPLOAD_TYPES
        assert 'application/octet-stream' not in ALLOWED_UPLOAD_TYPES


class TestThemerrDbCacheKey:
    """BUG-002 — ThemerrDB cache key must include item_type to prevent cross-type collisions."""

    def test_cache_keys_differ_by_item_type(self):
        import web_app
        key_movie = web_app._get_themerrdb_cache_key('tt1234567', 'movies')
        key_show = web_app._get_themerrdb_cache_key('tt1234567', 'tv_shows')
        assert key_movie != key_show, (
            "Same external ID with different item types must produce different cache keys"
        )

    def test_cache_key_without_type_is_distinct(self):
        import web_app
        key_typed = web_app._get_themerrdb_cache_key('tt1234567', 'movies')
        key_untyped = web_app._get_themerrdb_cache_key('tt1234567')
        assert key_typed != key_untyped
