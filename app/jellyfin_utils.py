"""Jellyfin connection and media path helpers."""
import logging
import os
import threading
from pathlib import Path

import requests as http_requests

from app.external_ids import extract_jellyfin_external_ids
from app.media_utils import _is_video_file_path

logger = logging.getLogger(__name__)

JELLYFIN_TIMEOUT_SECONDS = 30

# User ID caching with thread safety
_jellyfin_user_id_cache = {'value': None}
_jellyfin_user_id_lock = threading.Lock()


def jellyfin_is_configured():
    """Return True when Jellyfin credentials are configured."""
    jellyfin_url = (os.getenv('JELLYFIN_URL') or '').strip()
    jellyfin_api_key = (os.getenv('JELLYFIN_API_KEY') or '').strip()
    return bool(jellyfin_url and jellyfin_api_key)


def get_jellyfin():
    """Get Jellyfin connection settings from environment variables."""
    jellyfin_url = (os.getenv('JELLYFIN_URL') or '').strip()
    jellyfin_api_key = (os.getenv('JELLYFIN_API_KEY') or '').strip()
    jellyfin_user_id = (os.getenv('JELLYFIN_USER_ID') or '').strip() or None
    if not jellyfin_url or not jellyfin_api_key:
        raise ValueError('JELLYFIN_URL and JELLYFIN_API_KEY environment variables must be set')
    return {
        'url': jellyfin_url.rstrip('/'),
        'api_key': jellyfin_api_key,
        'user_id': jellyfin_user_id,
    }


def get_jellyfin_item_local_path(item):
    """Get local filesystem path for a Jellyfin item dict."""
    raw_path = item.get('Path')
    if not raw_path:
        return None
    candidate = Path(raw_path)
    item_type = (item.get('Type') or '').lower()
    if item_type == 'movie' and _is_video_file_path(candidate):
        return candidate.parent
    return candidate


def _normalize_provider(provider):
    normalized = (provider or '').strip().lower()
    if normalized not in {'plex', 'jellyfin'}:
        raise ValueError('provider must be either "plex" or "jellyfin"')
    return normalized


def jellyfin_session_get(jellyfin, path, **kwargs):
    """Perform an authenticated GET against Jellyfin."""
    headers = dict(kwargs.pop('headers', {}) or {})
    headers['X-Emby-Token'] = jellyfin['api_key']
    url = f"{jellyfin['url']}{path}"
    kwargs.setdefault('timeout', JELLYFIN_TIMEOUT_SECONDS)
    return http_requests.get(url, headers=headers, **kwargs)


def jellyfin_session_post(jellyfin, path, **kwargs):
    """Perform an authenticated POST against Jellyfin."""
    headers = dict(kwargs.pop('headers', {}) or {})
    headers['X-Emby-Token'] = jellyfin['api_key']
    url = f"{jellyfin['url']}{path}"
    kwargs.setdefault('timeout', JELLYFIN_TIMEOUT_SECONDS)
    return http_requests.post(url, headers=headers, **kwargs)


def get_jellyfin_user_id(jellyfin):
    """Resolve Jellyfin user id from env or Jellyfin users API."""
    explicit_user_id = jellyfin.get('user_id')
    if explicit_user_id:
        return explicit_user_id

    with _jellyfin_user_id_lock:
        cached_user_id = _jellyfin_user_id_cache.get('value')
        if cached_user_id:
            return cached_user_id

        response = jellyfin_session_get(jellyfin, '/Users')
        response.raise_for_status()
        users = response.json()
        if not isinstance(users, list) or not users:
            raise ValueError('Jellyfin did not return any users; set JELLYFIN_USER_ID explicitly')

        user_id = users[0].get('Id')
        if not user_id:
            raise ValueError('Failed to resolve Jellyfin user id from /Users response')

        _jellyfin_user_id_cache['value'] = user_id
        return user_id


def get_jellyfin_library_count(jellyfin, user_id, library_id):
    """Return item count for a Jellyfin TV/Movie library."""
    response = jellyfin_session_get(
        jellyfin,
        f'/Users/{user_id}/Items',
        params={
            'ParentId': str(library_id),
            'IncludeItemTypes': 'Series,Movie',
            'Recursive': 'true',
            'Limit': 1,
        },
    )
    response.raise_for_status()
    payload = response.json()
    return int(payload.get('TotalRecordCount', 0))


def get_jellyfin_libraries():
    """Return Jellyfin TV and Movie libraries."""
    jellyfin = get_jellyfin()
    user_id = get_jellyfin_user_id(jellyfin)
    response = jellyfin_session_get(jellyfin, '/Library/VirtualFolders')
    response.raise_for_status()
    folders = response.json()

    result = []
    for folder in folders:
        collection_type = (folder.get('CollectionType') or '').lower()
        if collection_type not in {'tvshows', 'movies'}:
            continue
        library_id = folder.get('ItemId') or folder.get('Id')
        if not library_id:
            continue
        media_type = 'show' if collection_type == 'tvshows' else 'movie'
        total_size = get_jellyfin_library_count(jellyfin, user_id, library_id)
        result.append({
            'id': str(library_id),
            'key': str(library_id),
            'title': folder.get('Name') or 'Unnamed Library',
            'type': media_type,
            'thumb': None,
            'totalSize': total_size,
            'provider': 'jellyfin',
        })
    return result


def get_jellyfin_item(jellyfin_item_id):
    """Fetch a Jellyfin item for the current Jellyfin user."""
    jellyfin = get_jellyfin()
    user_id = get_jellyfin_user_id(jellyfin)
    response = jellyfin_session_get(
        jellyfin,
        f'/Users/{user_id}/Items/{jellyfin_item_id}',
        params={'Fields': 'Path,ProductionYear,ParentId,ProviderIds'},
    )
    response.raise_for_status()
    return jellyfin, user_id, response.json()


def serialize_jellyfin_item(item, library_id, theme_dirs=None, get_themerrdb_theme_fn=None):
    """Serialize a Jellyfin item for API response."""
    local_path = get_jellyfin_item_local_path(item)
    theme_exists = False
    theme_size = 0
    if local_path:
        if theme_dirs is not None:
            theme_size = theme_dirs.get(str(local_path), 0)
            theme_exists = theme_size > 0

    item_type = (item.get('Type') or '').lower()
    media_type = 'show' if item_type == 'series' else 'movie'
    external_ids = extract_jellyfin_external_ids(item)
    has_themerrdb_theme = False
    if external_ids['imdb'] or external_ids['tmdb'] or external_ids['tvdb']:
        if get_themerrdb_theme_fn:
            themerrdb_data = get_themerrdb_theme_fn('jellyfin', item)
            has_themerrdb_theme = themerrdb_data is not None
    item_id = str(item.get('Id'))
    return {
        'id': item_id,
        'ratingKey': item_id,
        'provider': 'jellyfin',
        'library_id': str(library_id),
        'title': item.get('Name'),
        'year': item.get('ProductionYear'),
        'thumb': None,
        'type': media_type,
        'has_plex_theme': False,
        'plex_theme_source_unverified': False,
        'has_local_theme': theme_exists,
        'has_themerrdb_theme': has_themerrdb_theme,
        'theme_size': theme_size,
        'local_path': str(local_path) if local_path else None,
        'external_ids': external_ids,
    }


def reset_jellyfin_user_id_cache():
    """Reset the cached Jellyfin user ID (called during library cache refresh)."""
    with _jellyfin_user_id_lock:
        _jellyfin_user_id_cache['value'] = None


def refresh_jellyfin_item_metadata(item_id):
    """Trigger a Jellyfin metadata refresh for *item_id* so it picks up new local files.

    Sends ``POST /Items/{item_id}/Refresh`` with a full metadata re-scan.
    Errors are logged as warnings and do not propagate — the refresh is best-effort.
    """
    try:
        jellyfin = get_jellyfin()
        response = jellyfin_session_post(
            jellyfin,
            f'/Items/{item_id}/Refresh',
            params={
                'Recursive': 'false',
                'ImageRefreshMode': 'None',
                'MetadataRefreshMode': 'FullRefresh',
            },
        )
        response.raise_for_status()
        logger.info('Triggered Jellyfin metadata refresh for item %s', item_id)
    except Exception as exc:
        logger.warning('Failed to trigger Jellyfin metadata refresh for item %s: %s', item_id, exc)


def find_jellyfin_item_id_by_path(local_path):
    """Find the Jellyfin item ID whose local path matches *local_path*.

    Queries all series/movie items across all libraries and returns the ID of
    the first match.  Returns None when not found or when Jellyfin is not
    configured.  Used for cross-provider metadata refresh when both Plex and
    Jellyfin are connected.
    """
    try:
        jellyfin = get_jellyfin()
        user_id = get_jellyfin_user_id(jellyfin)
        response = jellyfin_session_get(
            jellyfin,
            f'/Users/{user_id}/Items',
            params={
                'Fields': 'Path',
                'Recursive': 'true',
                'IncludeItemTypes': 'Series,Movie',
            },
        )
        if response.status_code != 200:
            return None
        path_str = str(local_path)
        for item in response.json().get('Items', []):
            raw_path = item.get('Path') or ''
            candidate = Path(raw_path)
            if _is_video_file_path(candidate):
                candidate = candidate.parent
            if str(candidate) == path_str:
                return str(item.get('Id'))
    except Exception as exc:
        logger.warning('Failed to search Jellyfin for item at path %s: %s', local_path, exc)
    return None
