"""Tests for app/youtube_utils.py — YouTube URL validation, search, and download."""
import os
from unittest.mock import MagicMock, patch

from tests.helpers import make_mock_show


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

        with patch('app.youtube_utils.yt_dlp') as mock_ytdlp, \
             patch('tempfile.TemporaryDirectory') as mock_tmpdir:
            mock_tmpdir.return_value.__enter__ = lambda s: str(fake_tmpdir)
            mock_tmpdir.return_value.__exit__ = MagicMock(return_value=False)
            mock_ydl = MagicMock()
            mock_ytdlp.YoutubeDL.return_value.__enter__ = lambda s: mock_ydl
            mock_ytdlp.YoutubeDL.return_value.__exit__ = MagicMock(return_value=False)

            resp = client.post('/api/items/1/theme/youtube',
                               json={'url': 'https://youtube.com/watch?v=test', 'overwrite': False})
        assert resp.status_code == 200


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


# =============================================================================
# Regression tests for confirmed bugs (multi-agent audit)
# =============================================================================

class TestYoutubeSearchOpts:
    """BUG-001 / BUG-008 — yt-dlp option hygiene in youtube_search."""

    def test_youtube_search_opts_do_not_include_remote_components(self):
        """remote_components must never appear in youtube_search ydl_opts (supply-chain RCE)."""
        from app import web_app
        # Capture the ydl_opts dict built inside youtube_search by intercepting YoutubeDL.__init__
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

        with patch('app.web_app.yt_dlp.YoutubeDL', CapturingYDL):
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
        from app import web_app
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

        with patch('app.web_app.yt_dlp.YoutubeDL', CapturingYDL):
            with patch.dict(os.environ, {'DISABLE_AUTH': 'true'}):
                with web_app.app.test_client() as c:
                    c.get('/api/youtube/search?q=test')

        assert 'socket_timeout' in captured.get('opts', {}), (
            "socket_timeout should be set to bound yt-dlp search requests"
        )

    def test_youtube_search_query_length_cap(self):
        """Queries longer than 200 chars should be rejected with 400."""
        from app import web_app
        with patch.dict(os.environ, {'DISABLE_AUTH': 'true'}):
            with web_app.app.test_client() as c:
                resp = c.get(f'/api/youtube/search?q={"a" * 201}')
        assert resp.status_code == 400
