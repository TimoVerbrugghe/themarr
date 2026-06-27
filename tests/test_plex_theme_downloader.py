"""Tests for Themarr CLI plex theme downloader."""
from unittest.mock import MagicMock, patch

import pytest


class TestNormalizeShowName:
    def test_removes_year(self):
        from plex_theme_downloader import normalize_show_name
        assert normalize_show_name('Show Name (2020)') == 'showname'

    def test_lowercase(self):
        from plex_theme_downloader import normalize_show_name
        assert normalize_show_name('My Show') == 'myshow'

    def test_removes_special_chars(self):
        from plex_theme_downloader import normalize_show_name
        assert normalize_show_name('The Show-Name') == 'theshowname'


class TestTVShowScanner:
    def test_scan_shows(self, tmp_path):
        from plex_theme_downloader import TVShowScanner
        show1 = tmp_path / 'Show One'
        show1.mkdir()
        show2 = tmp_path / 'Show Two'
        show2.mkdir()
        (show2 / 'theme.mp3').write_bytes(b'mp3data')

        scanner = TVShowScanner(str(tmp_path))
        shows = scanner.scan_shows()

        assert 'Show One' in shows
        assert 'Show Two' in shows
        assert shows['Show One']['has_local_theme'] is False
        assert shows['Show Two']['has_local_theme'] is True

    def test_invalid_path_raises(self):
        from plex_theme_downloader import TVShowScanner
        with pytest.raises(ValueError):
            TVShowScanner('/nonexistent/path')

    def test_ignores_files(self, tmp_path):
        from plex_theme_downloader import TVShowScanner
        (tmp_path / 'not_a_dir.txt').write_text('hi')
        (tmp_path / 'Show Dir').mkdir()
        scanner = TVShowScanner(str(tmp_path))
        shows = scanner.scan_shows()
        assert 'not_a_dir.txt' not in shows
        assert 'Show Dir' in shows


class TestPlexThemeDownloader:
    def test_init_success(self):
        from plex_theme_downloader import PlexThemeDownloader
        with patch('plex_theme_downloader.PlexServer') as mock_server:
            PlexThemeDownloader('http://plex:32400', 'token123')
            mock_server.assert_called_once_with('http://plex:32400', 'token123')

    def test_init_failure(self):
        from plex_theme_downloader import PlexThemeDownloader
        with patch('plex_theme_downloader.PlexServer', side_effect=Exception('Connection failed')):
            with pytest.raises(Exception):
                PlexThemeDownloader('http://plex:32400', 'badtoken')

    def test_get_show_path(self):
        from plex_theme_downloader import PlexThemeDownloader
        with patch('plex_theme_downloader.PlexServer'):
            client = PlexThemeDownloader('http://plex:32400', 'token')
            show = MagicMock()
            show.title = 'Test Show'
            show.type = 'show'
            show.locations = ['/tv/Test Show (2020)']
            result = client.get_show_path(show)
            assert result == '/tv/Test Show (2020)'

    def test_get_movie_path(self):
        from plex_theme_downloader import PlexThemeDownloader
        with patch('plex_theme_downloader.PlexServer'):
            client = PlexThemeDownloader('http://plex:32400', 'token')
            movie = MagicMock()
            movie.title = 'Test Movie'
            movie.type = 'movie'
            movie.locations = ['/movies/Test Movie (2021)/movie.mkv']
            result = client.get_show_path(movie)
            assert result == '/movies/Test Movie (2021)'

    def test_get_show_path_no_locations(self):
        from plex_theme_downloader import PlexThemeDownloader
        with patch('plex_theme_downloader.PlexServer'):
            client = PlexThemeDownloader('http://plex:32400', 'token')
            show = MagicMock()
            show.title = 'Test Show'
            show.locations = []
            result = client.get_show_path(show)
            assert result is None

    def test_download_theme_no_theme(self, tmp_path):
        from plex_theme_downloader import PlexThemeDownloader
        with patch('plex_theme_downloader.PlexServer'):
            client = PlexThemeDownloader('http://plex:32400', 'token')
            show = MagicMock()
            show.theme = None
            show.title = 'No Theme Show'
            result = client.download_theme(show, tmp_path / 'theme.mp3')
            assert result is False

    def test_get_movie_library(self):
        from plex_theme_downloader import PlexThemeDownloader
        with patch('plex_theme_downloader.PlexServer') as mock_server:
            client = PlexThemeDownloader('http://plex:32400', 'token')
            movie_section = MagicMock()
            movie_section.type = 'movie'
            movie_section.title = 'Movies'
            client.plex.library.sections.return_value = [movie_section]
            result = client.get_movie_library()
            assert result == movie_section

    def test_get_movie_library_not_found(self):
        from plex_theme_downloader import PlexThemeDownloader
        with patch('plex_theme_downloader.PlexServer'):
            client = PlexThemeDownloader('http://plex:32400', 'token')
            client.plex.library.sections.return_value = []
            result = client.get_movie_library()
            assert result is None


class TestMatchShows:
    def test_match_by_folder_name(self, tmp_path):
        from plex_theme_downloader import match_shows

        local_shows = {
            'My Show (2020)': {
                'path': tmp_path / 'My Show (2020)',
                'has_local_theme': False,
                'theme_path': tmp_path / 'My Show (2020)' / 'theme.mp3',
            }
        }

        plex_show = MagicMock()
        plex_show.title = 'My Show'
        plex_show.theme = '/library/metadata/1/theme/1'
        plex_show.locations = ['/plex/tv/My Show (2020)']

        mock_client = MagicMock()
        mock_client.get_show_path.return_value = '/plex/tv/My Show (2020)'

        results = match_shows(local_shows, [plex_show], mock_client)
        assert len(results['matched']) == 1
        assert results['matched'][0]['title'] == 'My Show'

    def test_no_theme_in_plex(self, tmp_path):
        from plex_theme_downloader import match_shows

        local_shows = {}
        plex_show = MagicMock()
        plex_show.title = 'No Theme Show'
        plex_show.theme = None

        mock_client = MagicMock()
        results = match_shows(local_shows, [plex_show], mock_client)
        assert 'No Theme Show' in results['no_theme_in_plex']

    def test_already_has_theme_skips(self, tmp_path):
        from plex_theme_downloader import match_shows

        local_shows = {
            'My Show (2020)': {
                'path': tmp_path / 'My Show (2020)',
                'has_local_theme': True,
                'theme_path': tmp_path / 'My Show (2020)' / 'theme.mp3',
            }
        }

        plex_show = MagicMock()
        plex_show.title = 'My Show'
        plex_show.theme = '/library/metadata/1/theme/1'
        plex_show.locations = ['/plex/tv/My Show (2020)']

        mock_client = MagicMock()
        mock_client.get_show_path.return_value = '/plex/tv/My Show (2020)'

        results = match_shows(local_shows, [plex_show], mock_client)
        assert len(results['already_have_theme']) == 1
        assert len(results['matched']) == 0
