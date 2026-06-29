"""Tests for app/media_utils.py — local path helpers, path validation, and MP3 magic byte checks."""
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestGetItemLocalPath:
    def test_show_path(self, tmp_path):
        from app.web_app import get_item_local_path
        show_dir = tmp_path / 'My Show (2020)'
        show_dir.mkdir()
        show = MagicMock()
        show.type = 'show'
        show.locations = [str(show_dir)]
        result = get_item_local_path(show)
        assert result == show_dir

    def test_movie_path(self, tmp_path):
        from app.web_app import get_item_local_path
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
        from app.web_app import get_item_local_path
        movie_dir = tmp_path / 'Monsters, Inc. (2001)'
        movie_dir.mkdir()
        movie = MagicMock()
        movie.type = 'movie'
        movie.locations = [str(movie_dir)]
        result = get_item_local_path(movie)
        assert result == movie_dir

    def test_no_locations(self):
        from app.web_app import get_item_local_path
        item = MagicMock()
        item.type = 'show'
        item.locations = []
        result = get_item_local_path(item)
        assert result is None


class TestGetJellyfinItemLocalPath:
    def test_movie_file_path_returns_parent(self):
        from app.web_app import get_jellyfin_item_local_path
        item = {'Type': 'Movie', 'Path': '/movies/Monsters, Inc. (2001)/movie.mkv'}
        assert str(get_jellyfin_item_local_path(item)) == '/movies/Monsters, Inc. (2001)'

    def test_movie_folder_with_dot_name_returns_folder(self):
        from app.web_app import get_jellyfin_item_local_path
        item = {'Type': 'Movie', 'Path': '/movies/Monsters, Inc. (2001)'}
        assert str(get_jellyfin_item_local_path(item)) == '/movies/Monsters, Inc. (2001)'


class TestLocalPathValidation:
    def test_validate_local_media_path_rejects_traversal(self):
        from app.web_app import _validate_local_media_path

        with pytest.raises(ValueError, match='Invalid local media path'):
            _validate_local_media_path('../../etc/passwd')

    def test_validate_local_media_path_allows_absolute_paths_without_env_roots(self, tmp_path):
        from app.web_app import _validate_local_media_path

        validated = _validate_local_media_path(tmp_path / 'other' / 'show')
        assert str(validated).endswith('/other/show')

    def test_provider_theme_accepts_absolute_paths_without_env_roots(self, client):
        with patch('app.web_app._get_item_context', return_value={'local_path': Path('/etc')}):
            resp = client.get('/api/items/jellyfin/abc/theme')

        assert resp.status_code == 404


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
