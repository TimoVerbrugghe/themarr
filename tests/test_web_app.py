"""Tests for Themarr web application."""
import io
import os
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
    from web_app import app as flask_app
    flask_app.config['TESTING'] = True
    yield flask_app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def mock_plex():
    """Mock PlexServer."""
    with patch('web_app.get_plex') as mock_get_plex:
        plex = MagicMock()
        plex.friendlyName = 'Test Plex Server'
        plex.version = '1.0.0'
        mock_get_plex.return_value = plex
        yield plex


def make_mock_show(rating_key=1, title='Test Show', year=2020, has_theme=True, location='/plex/tv/Test Show (2020)'):
    """Create a mock Plex show item."""
    show = MagicMock()
    show.ratingKey = rating_key
    show.title = title
    show.year = year
    show.type = 'show'
    show.thumb = f'/library/metadata/{rating_key}/thumb'
    show.theme = f'/library/metadata/{rating_key}/theme/1' if has_theme else None
    show.locations = [location]
    return show


def make_mock_movie(rating_key=2, title='Test Movie', year=2021, has_theme=True, location='/plex/movies/Test Movie (2021)/movie.mkv'):
    """Create a mock Plex movie item."""
    movie = MagicMock()
    movie.ratingKey = rating_key
    movie.title = title
    movie.year = year
    movie.type = 'movie'
    movie.thumb = f'/library/metadata/{rating_key}/thumb'
    movie.theme = f'/library/metadata/{rating_key}/theme/1' if has_theme else None
    movie.locations = [location]
    return movie


class TestStatus:
    def test_status_connected(self, client, mock_plex):
        resp = client.get('/api/status')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['connected'] is True
        assert data['server_name'] == 'Test Plex Server'

    def test_status_disconnected(self, client):
        with patch('web_app.get_plex', side_effect=Exception('Connection refused')):
            resp = client.get('/api/status')
            assert resp.status_code == 200
            data = resp.get_json()
            assert data['connected'] is False
            assert data['error'] == 'Unable to connect to Plex'


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

    def test_get_libraries_error(self, client):
        with patch('web_app.get_plex', side_effect=Exception('Plex error')):
            resp = client.get('/api/libraries')
            assert resp.status_code == 500
            assert resp.get_json()['error'] == 'Failed to get libraries'


class TestLibraryItems:
    def test_get_show_items(self, client, mock_plex, tmp_path):
        show = make_mock_show()
        section = MagicMock()
        section.all.return_value = [show]
        mock_plex.library.sectionByID.return_value = section

        with patch.dict(os.environ, {'TV_PATH': str(tmp_path)}):
            resp = client.get('/api/libraries/1/items')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['title'] == 'Test Show'
        assert data[0]['type'] == 'show'
        assert data[0]['has_plex_theme'] is True
        assert data[0]['has_local_theme'] is False

    def test_get_movie_items(self, client, mock_plex, tmp_path):
        movie = make_mock_movie()
        section = MagicMock()
        section.all.return_value = [movie]
        mock_plex.library.sectionByID.return_value = section

        with patch.dict(os.environ, {'MOVIES_PATH': str(tmp_path)}):
            resp = client.get('/api/libraries/2/items')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['title'] == 'Test Movie'
        assert data[0]['type'] == 'movie'

    def test_items_with_existing_theme(self, client, mock_plex, tmp_path):
        show = make_mock_show(location='/plex/tv/Test Show (2020)')
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        theme_file = show_dir / 'theme.mp3'
        theme_file.write_bytes(b'\xff\xfb' * 100)

        section = MagicMock()
        section.all.return_value = [show]
        mock_plex.library.sectionByID.return_value = section

        with patch.dict(os.environ, {'TV_PATH': str(tmp_path)}):
            resp = client.get('/api/libraries/1/items')
        data = resp.get_json()
        assert data[0]['has_local_theme'] is True
        assert data[0]['theme_size'] > 0


class TestGetItemLocalPath:
    def test_show_path(self):
        from web_app import get_item_local_path
        show = MagicMock()
        show.type = 'show'
        show.locations = ['/plex/tv/My Show (2020)']
        with patch.dict(os.environ, {'TV_PATH': '/tv'}):
            result = get_item_local_path(show)
        assert str(result) == '/tv/My Show (2020)'

    def test_movie_path(self):
        from web_app import get_item_local_path
        movie = MagicMock()
        movie.type = 'movie'
        movie.locations = ['/plex/movies/My Movie (2021)/movie.mkv']
        with patch.dict(os.environ, {'MOVIES_PATH': '/movies'}):
            result = get_item_local_path(movie)
        assert str(result) == '/movies/My Movie (2021)'

    def test_no_locations(self):
        from web_app import get_item_local_path
        item = MagicMock()
        item.type = 'show'
        item.locations = []
        result = get_item_local_path(item)
        assert result is None


class TestThemeDownload:
    def test_download_theme_success(self, client, mock_plex, tmp_path):
        show = make_mock_show(location='/plex/tv/Test Show (2020)')
        mock_plex.fetchItem.return_value = show
        mock_plex.url.return_value = 'http://plex/theme.mp3'

        mock_response = MagicMock()
        mock_response.iter_content.return_value = [b'fake_mp3_data']
        mock_plex._session.get.return_value = mock_response

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()

        with patch.dict(os.environ, {'TV_PATH': str(tmp_path)}):
            resp = client.post('/api/items/1/theme/download',
                               json={'overwrite': False},
                               content_type='application/json')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True

    def test_download_theme_no_plex_theme(self, client, mock_plex, tmp_path):
        show = make_mock_show(has_theme=False)
        mock_plex.fetchItem.return_value = show

        with patch.dict(os.environ, {'TV_PATH': str(tmp_path)}):
            resp = client.post('/api/items/1/theme/download', json={})
        assert resp.status_code == 404

    def test_download_theme_already_exists(self, client, mock_plex, tmp_path):
        show = make_mock_show(location='/plex/tv/Test Show (2020)')
        mock_plex.fetchItem.return_value = show

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'existing_theme')

        with patch.dict(os.environ, {'TV_PATH': str(tmp_path)}):
            resp = client.post('/api/items/1/theme/download', json={'overwrite': False})
        assert resp.status_code == 409
        data = resp.get_json()
        assert data['exists'] is True

    def test_download_theme_overwrite(self, client, mock_plex, tmp_path):
        show = make_mock_show(location='/plex/tv/Test Show (2020)')
        mock_plex.fetchItem.return_value = show
        mock_plex.url.return_value = 'http://plex/theme.mp3'

        mock_response = MagicMock()
        mock_response.iter_content.return_value = [b'new_theme_data']
        mock_plex._session.get.return_value = mock_response

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'old_theme')

        with patch.dict(os.environ, {'TV_PATH': str(tmp_path)}):
            resp = client.post('/api/items/1/theme/download', json={'overwrite': True})
        assert resp.status_code == 200


class TestThemeUpload:
    def test_upload_theme(self, client, mock_plex, tmp_path):
        show = make_mock_show(location='/plex/tv/Test Show (2020)')
        mock_plex.fetchItem.return_value = show

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()

        with patch.dict(os.environ, {'TV_PATH': str(tmp_path)}):
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
        show = make_mock_show(location='/plex/tv/Test Show (2020)')
        mock_plex.fetchItem.return_value = show

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'existing_theme')

        with patch.dict(os.environ, {'TV_PATH': str(tmp_path)}):
            resp = client.post('/api/items/1/theme/upload',
                               data={'overwrite': 'false',
                                     'file': (io.BytesIO(b'ID3fake_mp3'), 'theme.mp3', 'audio/mpeg')})
        assert resp.status_code == 409



    def test_upload_rejects_non_mp3(self, client, mock_plex, tmp_path):
        show = make_mock_show(location='/plex/tv/Test Show (2020)')
        mock_plex.fetchItem.return_value = show

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()

        with patch.dict(os.environ, {'TV_PATH': str(tmp_path)}):
            resp = client.post('/api/items/1/theme/upload',
                               data={'overwrite': 'false',
                                     'file': (io.BytesIO(b'not_audio'), 'theme.wav', 'audio/wav')})
        assert resp.status_code == 400

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
        show = make_mock_show(location='/plex/tv/Test Show (2020)')
        mock_plex.fetchItem.return_value = show

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'existing')

        with patch.dict(os.environ, {'TV_PATH': str(tmp_path)}):
            resp = client.post('/api/items/1/theme/youtube',
                               json={'url': 'https://youtube.com/watch?v=test', 'overwrite': False})
        assert resp.status_code == 409

    def test_youtube_download(self, client, mock_plex, tmp_path):
        show = make_mock_show(location='/plex/tv/Test Show (2020)')
        mock_plex.fetchItem.return_value = show

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()

        fake_tmpdir = tmp_path / 'ytdl_tmp'
        fake_tmpdir.mkdir()
        (fake_tmpdir / 'theme.mp3').write_bytes(b'youtube_audio')

        with patch('web_app.yt_dlp') as mock_ytdlp, \
             patch('tempfile.TemporaryDirectory') as mock_tmpdir, \
             patch.dict(os.environ, {'TV_PATH': str(tmp_path)}):
            mock_tmpdir.return_value.__enter__ = lambda s: str(fake_tmpdir)
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            mock_ydl = MagicMock()
            mock_ytdlp.YoutubeDL.return_value.__enter__ = lambda s: mock_ydl
            mock_ytdlp.YoutubeDL.return_value.__exit__ = MagicMock(return_value=False)

            resp = client.post('/api/items/1/theme/youtube',
                               json={'url': 'https://youtube.com/watch?v=test', 'overwrite': False})
        assert resp.status_code == 200


class TestThemeDelete:
    def test_delete_theme(self, client, mock_plex, tmp_path):
        show = make_mock_show(location='/plex/tv/Test Show (2020)')
        mock_plex.fetchItem.return_value = show

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'theme_data')

        with patch.dict(os.environ, {'TV_PATH': str(tmp_path)}):
            resp = client.delete('/api/items/1/theme')
        assert resp.status_code == 200
        assert not (show_dir / 'theme.mp3').exists()

    def test_delete_nonexistent_theme(self, client, mock_plex, tmp_path):
        show = make_mock_show(location='/plex/tv/Test Show (2020)')
        mock_plex.fetchItem.return_value = show

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()

        with patch.dict(os.environ, {'TV_PATH': str(tmp_path)}):
            resp = client.delete('/api/items/1/theme')
        assert resp.status_code == 404


class TestGetTheme:
    def test_get_theme_file(self, client, mock_plex, tmp_path):
        show = make_mock_show(location='/plex/tv/Test Show (2020)')
        mock_plex.fetchItem.return_value = show

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'\xff\xfb' * 100)

        with patch.dict(os.environ, {'TV_PATH': str(tmp_path)}):
            resp = client.get('/api/items/1/theme')
        assert resp.status_code == 200

    def test_get_theme_not_found(self, client, mock_plex, tmp_path):
        show = make_mock_show(location='/plex/tv/Test Show (2020)')
        mock_plex.fetchItem.return_value = show

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()

        with patch.dict(os.environ, {'TV_PATH': str(tmp_path)}):
            resp = client.get('/api/items/1/theme')
        assert resp.status_code == 404


class TestIndexPage:
    def test_index_loads(self, client):
        resp = client.get('/')
        assert resp.status_code == 200
        assert b'Themarr' in resp.data
        assert b'id="library-nav"' in resp.data
        assert b'id="items-grid"' in resp.data
        assert b'/static/js/app.js' in resp.data


# ============================================================
# Bulk download tests
# ============================================================

class TestBulkDownload:
    def test_bulk_download_success(self, client, mock_plex, tmp_path):
        show = make_mock_show(location='/plex/tv/Test Show (2020)')
        mock_plex.fetchItem.return_value = show
        mock_plex.url.return_value = 'http://plex/theme.mp3'
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b'audio_data']
        mock_plex._session.get.return_value = mock_resp

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()

        with patch.dict(os.environ, {'TV_PATH': str(tmp_path)}):
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
        show = make_mock_show(location='/plex/tv/Test Show (2020)')
        mock_plex.fetchItem.return_value = show

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'existing')

        with patch.dict(os.environ, {'TV_PATH': str(tmp_path)}):
            resp = client.post('/api/bulk/theme/download',
                               json={'ratingKeys': [1], 'overwrite': False})
        data = resp.get_json()
        assert len(data['skipped']) == 1

    def test_bulk_download_no_plex_theme(self, client, mock_plex, tmp_path):
        show = make_mock_show(has_theme=False)
        mock_plex.fetchItem.return_value = show

        with patch.dict(os.environ, {'TV_PATH': str(tmp_path)}):
            resp = client.post('/api/bulk/theme/download',
                               json={'ratingKeys': [1], 'overwrite': False})
        data = resp.get_json()
        assert len(data['no_theme']) == 1

    def test_bulk_download_overwrite(self, client, mock_plex, tmp_path):
        show = make_mock_show(location='/plex/tv/Test Show (2020)')
        mock_plex.fetchItem.return_value = show
        mock_plex.url.return_value = 'http://plex/theme.mp3'
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b'new_audio']
        mock_plex._session.get.return_value = mock_resp

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'old')

        with patch.dict(os.environ, {'TV_PATH': str(tmp_path)}):
            resp = client.post('/api/bulk/theme/download',
                               json={'ratingKeys': [1], 'overwrite': True})
        data = resp.get_json()
        assert len(data['success']) == 1


# ============================================================
# Webhook tests
# ============================================================

class TestSonarrWebhook:
    def test_series_add_queues_thread(self, client):
        with patch('web_app._process_webhook_add') as mock_process, \
             patch('web_app.threading') as mock_threading:
            mock_thread = MagicMock()
            mock_threading.Thread.return_value = mock_thread
            resp = client.post('/api/webhooks/sonarr',
                               json={'eventType': 'SeriesAdd',
                                     'series': {'title': 'Breaking Bad', 'path': '/tv/Breaking Bad'}})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['eventType'] == 'SeriesAdd'
        assert data['queued'] is True

    def test_series_delete_acknowledged(self, client):
        resp = client.post('/api/webhooks/sonarr',
                           json={'eventType': 'SeriesDelete',
                                 'series': {'title': 'Breaking Bad'}})
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    def test_test_event(self, client):
        resp = client.post('/api/webhooks/sonarr',
                           json={'eventType': 'Test'})
        assert resp.status_code == 200

    def test_invalid_json(self, client):
        resp = client.post('/api/webhooks/sonarr',
                           data='not-json',
                           content_type='application/json')
        assert resp.status_code == 400

    def test_unauthorized_with_auth_configured(self, client):
        with patch.dict(os.environ, {'WEBHOOK_USERNAME': 'user', 'WEBHOOK_PASSWORD': 'pass'}):
            resp = client.post('/api/webhooks/sonarr',
                               json={'eventType': 'Test'})
        assert resp.status_code == 401

    def test_authorized_with_correct_credentials(self, client):
        import base64
        token = base64.b64encode(b'user:pass').decode()
        with patch.dict(os.environ, {'WEBHOOK_USERNAME': 'user', 'WEBHOOK_PASSWORD': 'pass'}):
            resp = client.post('/api/webhooks/sonarr',
                               json={'eventType': 'Test'},
                               headers={'Authorization': f'Basic {token}'})
        assert resp.status_code == 200

    def test_series_add_no_title_not_queued(self, client):
        with patch('web_app.threading') as mock_threading:
            resp = client.post('/api/webhooks/sonarr',
                               json={'eventType': 'SeriesAdd',
                                     'series': {'title': '', 'path': '/tv/Unknown'}})
        assert resp.status_code == 200
        assert resp.get_json()['queued'] is False


class TestRadarrWebhook:
    def test_movie_added_queues_thread(self, client):
        with patch('web_app.threading') as mock_threading:
            mock_thread = MagicMock()
            mock_threading.Thread.return_value = mock_thread
            resp = client.post('/api/webhooks/radarr',
                               json={'eventType': 'MovieAdded',
                                     'movie': {'title': 'The Dark Knight',
                                               'folderPath': '/movies/The Dark Knight (2008)'}})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['queued'] is True

    def test_movie_added_uses_folder_path(self, client):
        with patch('web_app.threading') as mock_threading:
            mock_thread = MagicMock()
            mock_threading.Thread.return_value = mock_thread
            resp = client.post('/api/webhooks/radarr',
                               json={'eventType': 'MovieAdded',
                                     'movie': {'title': 'Test', 'folderPath': '/movies/Test (2020)'}})
        assert resp.status_code == 200

    def test_movie_deleted_acknowledged(self, client):
        resp = client.post('/api/webhooks/radarr',
                           json={'eventType': 'MovieDeleted',
                                 'movie': {'title': 'The Dark Knight'}})
        assert resp.status_code == 200

    def test_test_event(self, client):
        resp = client.post('/api/webhooks/radarr',
                           json={'eventType': 'Test'})
        assert resp.status_code == 200

    def test_unauthorized(self, client):
        with patch.dict(os.environ, {'WEBHOOK_USERNAME': 'u', 'WEBHOOK_PASSWORD': 'p'}):
            resp = client.post('/api/webhooks/radarr', json={'eventType': 'Test'})
        assert resp.status_code == 401


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
        show = make_mock_show(location='/plex/tv/Test Show (2020)')
        mock_plex.fetchItem.return_value = show
        mock_plex.url.return_value = 'http://plex/theme.mp3'
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b'mp3data']
        mock_plex._session.get.return_value = mock_resp

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()

        with patch('web_app.send_pushover_notification') as mock_notif, \
             patch.dict(os.environ, {'TV_PATH': str(tmp_path)}):
            resp = client.post('/api/items/1/theme/download', json={'overwrite': False})
        assert resp.status_code == 200
        mock_notif.assert_called_once()


# ============================================================
# Webhook helper: _check_webhook_auth
# ============================================================

class TestWebhookAuth:
    def test_no_credentials_configured_allows_all(self, app):
        from web_app import _check_webhook_auth
        with app.test_request_context('/api/webhooks/sonarr',
                                      method='POST',
                                      environ_base={}):
            with patch.dict(os.environ, {'WEBHOOK_USERNAME': '', 'WEBHOOK_PASSWORD': ''}):
                assert _check_webhook_auth() is True

    def test_correct_credentials(self, app):
        import base64
        from web_app import _check_webhook_auth
        token = base64.b64encode(b'admin:secret').decode()
        with app.test_request_context(
            '/api/webhooks/sonarr',
            method='POST',
            headers={'Authorization': f'Basic {token}'},
        ):
            with patch.dict(os.environ, {'WEBHOOK_USERNAME': 'admin',
                                         'WEBHOOK_PASSWORD': 'secret'}):
                assert _check_webhook_auth() is True

    def test_wrong_password(self, app):
        import base64
        from web_app import _check_webhook_auth
        token = base64.b64encode(b'admin:wrong').decode()
        with app.test_request_context(
            '/api/webhooks/sonarr',
            method='POST',
            headers={'Authorization': f'Basic {token}'},
        ):
            with patch.dict(os.environ, {'WEBHOOK_USERNAME': 'admin',
                                         'WEBHOOK_PASSWORD': 'secret'}):
                assert _check_webhook_auth() is False


# ============================================================
# _find_plex_item helper
# ============================================================

class TestFindPlexItem:
    def test_finds_by_title_search(self):
        from web_app import _find_plex_item
        plex = MagicMock()
        show_item = MagicMock()
        show_item.title = 'Breaking Bad'
        section = MagicMock()
        section.type = 'show'
        section.search.return_value = [show_item]
        plex.library.sections.return_value = [section]
        result = _find_plex_item(plex, 'Breaking Bad', '/tv/Breaking Bad', 'show')
        assert result == show_item

    def test_returns_none_when_not_found(self):
        from web_app import _find_plex_item
        plex = MagicMock()
        section = MagicMock()
        section.type = 'show'
        section.search.return_value = []
        section.all.return_value = []
        plex.library.sections.return_value = [section]
        result = _find_plex_item(plex, 'Unknown Show', '/tv/Unknown', 'show')
        assert result is None

    def test_falls_back_to_folder_name_match(self):
        from web_app import _find_plex_item
        plex = MagicMock()
        show_item = MagicMock()
        show_item.locations = ['/plex/tv/Breaking Bad']
        section = MagicMock()
        section.type = 'show'
        section.search.return_value = []  # title search misses
        section.all.return_value = [show_item]
        plex.library.sections.return_value = [section]
        result = _find_plex_item(plex, 'Breaking Bad', '/arr/tv/Breaking Bad', 'show')
        assert result == show_item
