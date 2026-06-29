"""Tests for app/web_app.py — core routes: status, libraries, items, theme operations, settings."""
import io
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.helpers import make_mock_show, make_mock_movie


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
            with patch('app.web_app.get_plex', side_effect=Exception('Connection refused')):
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
            with patch('app.web_app.jellyfin_session_get', return_value=mock_response):
                resp = client.get('/api/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['jellyfin']['url_configured'] is True
        assert data['jellyfin']['connected'] is True
        assert data['jellyfin']['server_name'] == 'Test Jellyfin'

    def test_status_marks_jellyfin_connected_when_system_info_endpoints_fail_but_libraries_work(self, client):
        from app import web_app

        mock_library_response = MagicMock()
        mock_library_response.raise_for_status.return_value = None
        mock_library_response.content = b'[]'
        mock_library_response.json.return_value = []

        with patch.dict(os.environ, {'JELLYFIN_URL': 'http://jellyfin.local', 'JELLYFIN_API_KEY': 'j-key'}, clear=False):
            with patch(
                'app.web_app.jellyfin_session_get',
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
            with patch('app.web_app.jellyfin_session_get', return_value=mock_response):
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
        with patch('app.web_app.plex_is_configured', return_value=True):
            with patch('app.web_app.get_plex', side_effect=Exception('Plex error')):
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
        with patch('app.web_app.jellyfin_is_configured', return_value=True), \
             patch('app.web_app.get_jellyfin_libraries', return_value=[jellyfin_library]):
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
            with patch('app.web_app.get_plex', side_effect=AssertionError('get_plex should not be called')):
                with patch('app.web_app.jellyfin_is_configured', return_value=True), \
                     patch('app.web_app.get_jellyfin_libraries', return_value=[jellyfin_library]):
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
        from app import web_app

        section = MagicMock()
        section.key = 1
        section.locations = ['/only-this-path']
        section.all.return_value = []
        mock_plex.library.sectionByID.return_value = section

        with patch('app.cache.scan_local_theme_dirs', return_value={}) as mock_scan, \
             patch('app.cache.get_section_base_paths', return_value={'/fallback-path'}):
            from app.cache import build_library_items
            build_library_items(1)

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
        with patch('app.web_app.build_library_items', return_value=jellyfin_items):
            resp = client.get('/api/libraries/jellyfin/jf-lib/items')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data[0]['provider'] == 'jellyfin'
        assert data[0]['title'] == 'Jellyfin Show'

    def test_scan_local_theme_dirs_detects_theme_when_item_path_is_base(self, tmp_path):
        from app import web_app

        movie_dir = tmp_path / 'Monsters, Inc. (2001)'
        movie_dir.mkdir()
        theme_path = movie_dir / 'theme.mp3'
        theme_path.write_bytes(b'\xff\xfb' * 100)

        scanned = web_app.scan_local_theme_dirs({str(movie_dir)})
        assert scanned[str(movie_dir)] == theme_path.stat().st_size


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
        from app import web_app

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
        with patch('app.web_app._get_item_context', return_value=context), \
             patch('app.web_app.sync_cached_item_theme_state', return_value=(None, False)):
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
        from app import web_app

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
        from app.cache import set_cached_poster

        set_cached_poster(1, b'cached_poster', 'image/jpeg', provider='plex')

        resp = client.get('/api/poster/1')
        assert resp.status_code == 200
        assert resp.data == b'cached_poster'
        mock_plex.fetchItem.assert_not_called()

    def test_get_poster_populates_cache_on_first_fetch(self, client, mock_plex):
        from app.cache import get_cached_poster

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
        assert get_cached_poster(1, provider='plex')['content'] == b'poster_data'


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


<<<<<<< HEAD
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
        from app import web_app

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
        with patch('app.web_app.get_plex', return_value=plex):
            with patch('app.webhook_handlers.get_plex', return_value=plex):
                return client.post('/api/webhooks/plex', data={'payload': json.dumps(payload)}, headers=headers or {})

    def test_library_new_event_queues_theme_processing(self, client):
        """library.new event with valid ratingKey queues theme processing."""
        with patch('app.web_app._submit_background_job') as mock_submit:
            payload = {'event': 'library.new', 'Metadata': {'ratingKey': '12345'}, 'Server': {'uuid': 'configured-server-uuid'}}
            resp = self._post_webhook(client, payload)
        
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        mock_submit.assert_called_once()

    def test_library_new_event_with_metadata_fallback(self, client):
        """Handles both 'Metadata' and 'metadata' field names."""
        with patch('app.web_app._submit_background_job') as mock_submit:
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
        with patch('app.web_app._submit_background_job') as mock_submit:
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
             patch('app.web_app._submit_background_job') as mock_submit:
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
        from app.webhook_handlers import process_plex_library_new

        show_dir = tmp_path / 'New Show (2024)'
        show_dir.mkdir()
        show = make_mock_show(title='New Show', location=str(show_dir), has_theme=True)

        plex = MagicMock()
        plex.library.fetchItem.return_value = show

        mock_download = MagicMock()
        with patch('app.webhook_handlers.get_plex', return_value=plex), \
             patch('app.webhook_handlers.send_pushover_notification') as mock_notify:
            process_plex_library_new('123', download_plex_theme_fn=mock_download)

        mock_download.assert_called_once()
        mock_notify.assert_called_once()

    def test_process_library_new_skips_when_theme_exists(self, tmp_path):
        from app.webhook_handlers import process_plex_library_new

        show_dir = tmp_path / 'Existing Show (2024)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'existing')
        show = make_mock_show(title='Existing Show', location=str(show_dir), has_theme=True)

        plex = MagicMock()
        plex.library.fetchItem.return_value = show

        mock_download = MagicMock()
        with patch('app.webhook_handlers.get_plex', return_value=plex):
            process_plex_library_new('123', download_plex_theme_fn=mock_download)

        mock_download.assert_not_called()


class TestJellyfinWebhook:
    def test_item_added_event_queues_processing(self, client):
        with patch('app.web_app._submit_background_job') as mock_submit:
            resp = client.post('/api/webhooks/jellyfin', json={'NotificationType': 'ItemAdded', 'ItemId': 'jf-1'})
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True
        mock_submit.assert_called_once()

    def test_non_item_added_event_is_ignored(self, client):
        with patch('app.web_app._submit_background_job') as mock_submit:
            resp = client.post('/api/webhooks/jellyfin', json={'NotificationType': 'PlaybackStart', 'ItemId': 'jf-1'})
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True
        mock_submit.assert_not_called()

    def test_process_item_added_downloads_themerrdb_theme_when_available(self, tmp_path):
        from app.webhook_handlers import process_jellyfin_item_added

        show_dir = tmp_path / 'New Show (2024)'
        show_dir.mkdir()
        payload = {'NotificationType': 'ItemAdded', 'ItemId': 'jf-1'}
        context = {
            'provider': 'jellyfin',
            'item_id': 'jf-1',
            'title': 'New Show',
            'local_path': show_dir,
            'item': {'Type': 'Series', 'ProviderIds': {'Imdb': 'tt123'}},
        }
        mock_download = MagicMock()

        with patch('app.webhook_handlers.get_themerrdb_theme_for_external_ids', return_value={'youtube_theme_url': 'https://www.youtube.com/watch?v=abc'}), \
             patch('app.webhook_handlers.send_pushover_notification') as mock_notify, \
             patch('app.webhook_handlers.sync_cached_item_theme_state') as mock_sync:
            process_jellyfin_item_added(
                payload,
                get_item_context_fn=lambda _provider, _item_id: context,
                download_youtube_theme_fn=mock_download,
            )

        mock_download.assert_called_once_with('https://www.youtube.com/watch?v=abc', show_dir / 'theme.mp3')
        mock_sync.assert_called_once_with('jellyfin', 'jf-1')
        mock_notify.assert_called_once()

    def test_process_item_added_skips_when_local_theme_exists(self, tmp_path):
        from app.webhook_handlers import process_jellyfin_item_added

        show_dir = tmp_path / 'Existing Show (2024)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'existing')
        payload = {'NotificationType': 'ItemAdded', 'ItemId': 'jf-1'}
        context = {
            'provider': 'jellyfin',
            'item_id': 'jf-1',
            'title': 'Existing Show',
            'local_path': show_dir,
            'item': {'Type': 'Series', 'ProviderIds': {'Imdb': 'tt123'}},
        }
        mock_download = MagicMock()

        with patch('app.webhook_handlers.get_themerrdb_theme_for_external_ids') as mock_themerrdb:
            process_jellyfin_item_added(
                payload,
                get_item_context_fn=lambda _provider, _item_id: context,
                download_youtube_theme_fn=mock_download,
            )

        mock_themerrdb.assert_not_called()
        mock_download.assert_not_called()

    def test_process_item_added_skips_when_themerrdb_has_no_theme(self, tmp_path):
        from app.webhook_handlers import process_jellyfin_item_added

        show_dir = tmp_path / 'No Theme Show (2024)'
        show_dir.mkdir()
        payload = {'NotificationType': 'ItemAdded', 'ItemId': 'jf-1'}
        context = {
            'provider': 'jellyfin',
            'item_id': 'jf-1',
            'title': 'No Theme Show',
            'local_path': show_dir,
            'item': {'Type': 'Series', 'ProviderIds': {'Imdb': 'tt123'}},
        }
        mock_download = MagicMock()

        with patch('app.webhook_handlers.get_themerrdb_theme_for_external_ids', return_value=None):
            process_jellyfin_item_added(
                payload,
                get_item_context_fn=lambda _provider, _item_id: context,
                download_youtube_theme_fn=mock_download,
            )

        mock_download.assert_not_called()


# ============================================================
# Pushover notification tests
# ============================================================

class TestPushoverNotification:
    def test_no_op_without_config(self):
        """send_pushover_notification does nothing if env vars are missing."""
        from app.notifications import send_pushover_notification
        with patch('app.notifications.http_requests') as mock_req, \
             patch.dict(os.environ, {}, clear=True):
            send_pushover_notification('Test', 'body')
            mock_req.post.assert_not_called()

    def test_sends_with_config(self):
        from app.notifications import send_pushover_notification
        with patch('app.notifications.http_requests') as mock_req, \
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
        from app.notifications import send_pushover_notification
        with patch('app.notifications.http_requests') as mock_req, \
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

        with patch('app.web_app.send_pushover_notification') as mock_notif:
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
        with patch('app.web_app.http_requests') as mock_req, \
             patch.dict(os.environ, {'PUSHOVER_APP_TOKEN': 'tok', 'PUSHOVER_USER_KEY': 'usr'}):
            mock_resp = MagicMock()
            mock_req.post.return_value = mock_resp
            resp = client.post('/api/settings/test-pushover')
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    def test_returns_500_on_pushover_error(self, client):
        with patch('app.web_app.http_requests') as mock_req, \
             patch.dict(os.environ, {'PUSHOVER_APP_TOKEN': 'tok', 'PUSHOVER_USER_KEY': 'usr'}):
            mock_req.post.side_effect = Exception('Network error')
            resp = client.post('/api/settings/test-pushover')
        assert resp.status_code == 500


=======
>>>>>>> origin/main
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
        with patch('app.web_app.get_plex', side_effect=Exception('Plex error')):
            resp = client.post('/api/settings/rescan')
        assert resp.status_code == 500
        assert 'error' in resp.get_json()


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

        img_src_sources = directives.get('img-src', [])
        assert any(source == 'https://i.ytimg.com' for source in img_src_sources)

    def test_security_headers_on_html_response(self, app):
        with app.test_client() as c:
            resp = c.get('/')
        assert resp.headers.get('X-Content-Type-Options') == 'nosniff'
        assert resp.headers.get('X-Frame-Options') == 'DENY'
        csp = resp.headers.get('Content-Security-Policy', '')
        assert "'unsafe-inline'" not in csp
