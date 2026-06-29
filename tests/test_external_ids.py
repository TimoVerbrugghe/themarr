"""Tests for app/external_ids.py — external ID extraction from Plex and Jellyfin items."""
from unittest.mock import MagicMock


class TestExternalIds:
    def test_extract_external_ids_from_dict_guids(self):
        from app import web_app

        item = MagicMock()
        item.guids = [
            {'id': 'imdb://tt0111161'},
            {'id': 'tvdb://121361'},
        ]

        ids = web_app.extract_external_ids(item)
        assert ids == {'imdb': 'tt0111161', 'tvdb': '121361', 'tmdb': None}

    def test_extract_external_ids_from_plex_guid_objects(self):
        from app import web_app

        item = MagicMock()
        item.guids = [
            type('GuidObj', (), {'id': 'imdb://tt0468569'})(),
            type('GuidObj', (), {'id': 'tvdb://80379'})(),
        ]

        ids = web_app.extract_external_ids(item)
        assert ids == {'imdb': 'tt0468569', 'tvdb': '80379', 'tmdb': None}

    def test_serialize_jellyfin_item_sets_themerrdb_flag(self):
        from app import jellyfin_utils

        jellyfin_item = {
            'Id': 'jf-1',
            'Name': 'Jellyfin Movie',
            'Type': 'Movie',
            'Path': '/movies/Jellyfin Movie (2020)',
            'ProductionYear': 2020,
            'ProviderIds': {'Imdb': 'tt1234567', 'Tmdb': '1234'},
        }

        def mock_get_themerrdb(provider, item):
            return {'youtube_theme_url': 'https://youtube.com/watch?v=test'}

        data = jellyfin_utils.serialize_jellyfin_item(jellyfin_item, 'jf-lib', theme_dirs={}, get_themerrdb_theme_fn=mock_get_themerrdb)

        assert data['has_themerrdb_theme'] is True
        assert data['plex_theme_source_unverified'] is False
        assert data['external_ids'] == {'imdb': 'tt1234567', 'tmdb': '1234', 'tvdb': None}
