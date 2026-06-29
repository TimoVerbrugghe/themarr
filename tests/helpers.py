"""Shared test helper factories for Themarr tests."""
from unittest.mock import MagicMock


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
