#!/usr/bin/env python3
"""Themarr Web Application - Flask-based Web UI for managing Plex theme music."""

import json
import logging
import os
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests as http_requests
import yt_dlp
from flask import Flask, Response, jsonify, render_template, request, send_file
from plexapi.server import PlexServer
from werkzeug.utils import safe_join

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
YTDLP_WORKDIR = Path(tempfile.gettempdir()) / 'themarr_yt_dlp_work'
YTDLP_WORKDIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_YOUTUBE_DURATION_SECONDS = 15 * 60
MAX_BULK_ITEMS = 100
ASSET_VERSION = str(int(time.time()))
PLEX_RETRY_ATTEMPTS_DEFAULT = 10
PLEX_RETRY_DELAY_DEFAULT = 30  # seconds between retry attempts
JELLYFIN_TIMEOUT_SECONDS = 30
ALLOWED_UPLOAD_TYPES = {'audio/mpeg', 'audio/mp3', 'application/octet-stream'}
ALLOWED_YOUTUBE_HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be'}
VIDEO_FILE_EXTENSIONS = {
    '.3gp', '.asf', '.avi', '.divx', '.flv', '.iso', '.m2ts', '.m4v', '.mkv',
    '.mov', '.mp4', '.mpeg', '.mpg', '.mts', '.ts', '.vob', '.webm', '.wmv',
}
THEMERRDB_API_BASE = 'https://app.lizardbyte.dev/ThemerrDB'
THEMERRDB_CACHE_TTL = 24 * 3600  # 24 hours

# In-memory cache for library items, warmed at startup to make first page loads instant.
_library_cache: dict = {}        # {section_id: [item_dict, ...]}
_library_cache_lock = threading.Lock()
_section_build_locks: dict = {}  # {section_id: threading.Lock()}
_section_build_locks_lock = threading.Lock()
_poster_cache: dict = {}         # {rating_key: {'content': bytes, 'content_type': str}}
_poster_cache_lock = threading.Lock()
_themerrdb_cache: dict = {}      # {cache_key: {'data': dict, 'timestamp': float}}
_themerrdb_cache_lock = threading.Lock()
_theme_hydration_status = {
    'running': False,
    'ready': True,
    'sections_total': 0,
    'sections_completed': 0,
}
_theme_hydration_status_lock = threading.Lock()
_jellyfin_user_id_cache = {'value': None}
_jellyfin_user_id_lock = threading.Lock()

def error_response(message, status_code=500, exc=None):
    """Return a safe JSON error response while logging internal details."""
    if exc is None:
        logger.error(message)
    else:
        logger.error('%s: %s', message, exc)
    return jsonify({'error': message}), status_code


def get_plex():
    """Get authenticated PlexServer instance from environment variables."""
    plex_url = os.getenv('PLEX_URL')
    plex_token = os.getenv('PLEX_TOKEN')
    if not plex_url or not plex_token:
        raise ValueError('PLEX_URL and PLEX_TOKEN environment variables must be set')
    return PlexServer(plex_url.rstrip('/'), plex_token)


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


def jellyfin_session_get(jellyfin, path, **kwargs):
    """Perform an authenticated GET against Jellyfin."""
    headers = dict(kwargs.pop('headers', {}) or {})
    headers['X-Emby-Token'] = jellyfin['api_key']
    if path.startswith('http://') or path.startswith('https://'):
        url = path
    else:
        url = f"{jellyfin['url']}{path}"
    return http_requests.get(url, headers=headers, timeout=JELLYFIN_TIMEOUT_SECONDS, **kwargs)


def jellyfin_session_post(jellyfin, path, **kwargs):
    """Perform an authenticated POST against Jellyfin."""
    headers = dict(kwargs.pop('headers', {}) or {})
    headers['X-Emby-Token'] = jellyfin['api_key']
    if path.startswith('http://') or path.startswith('https://'):
        url = path
    else:
        url = f"{jellyfin['url']}{path}"
    return http_requests.post(url, headers=headers, timeout=JELLYFIN_TIMEOUT_SECONDS, **kwargs)


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


def _is_video_file_path(path):
    """Return True when a path appears to reference a video file."""
    return path.suffix.lower() in VIDEO_FILE_EXTENSIONS


def _configured_media_roots():
    """Return configured media root paths used to constrain filesystem operations."""
    roots = []
    for env_name in ('TV_SHOWS_HOST_PATH', 'MOVIES_HOST_PATH'):
        raw_value = (os.getenv(env_name) or '').strip()
        if not raw_value:
            continue
        try:
            roots.append(Path(raw_value).resolve(strict=False))
        except OSError:
            logger.warning('Ignoring invalid %s path configuration', env_name)
    return roots


def _is_within_root(path, root):
    """Return True when *path* is at or below *root*."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_local_media_path(local_path):
    """Normalize and validate local media path for safe filesystem usage."""
    if local_path is None:
        return None

    local_path_str = str(local_path)
    sanitized = safe_join('/', local_path_str.lstrip('/'))
    if sanitized is None:
        raise ValueError('Invalid local media path')

    normalized = os.path.normpath(sanitized)
    if not normalized.startswith('/'):
        raise ValueError('Invalid local media path')

    roots = _configured_media_roots()
    normalized_path = Path(normalized)
    if roots and not any(_is_within_root(normalized_path, root) for root in roots):
        raise ValueError('Item path is outside configured media roots')

    return normalized_path


def _theme_file_path(local_path):
    """Return validated theme file path for a validated media directory."""
    validated_local_path = _validate_local_media_path(local_path)
    if not validated_local_path:
        return None
    return validated_local_path / 'theme.mp3'


def _normalize_provider(provider):
    normalized = (provider or '').strip().lower()
    if normalized not in {'plex', 'jellyfin'}:
        raise ValueError('provider must be either "plex" or "jellyfin"')
    return normalized


def _serialize_jellyfin_item(item, library_id, theme_dirs=None):
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
        themerrdb_data = get_themerrdb_theme_for_item('jellyfin', item)
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
        'has_local_theme': theme_exists,
        'has_themerrdb_theme': has_themerrdb_theme,
        'theme_size': theme_size,
        'local_path': str(local_path) if local_path else None,
        'external_ids': external_ids,
    }


def _get_jellyfin_library_count(jellyfin, user_id, library_id):
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


def _get_jellyfin_libraries():
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
        total_size = _get_jellyfin_library_count(jellyfin, user_id, library_id)
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


def _get_jellyfin_item(jellyfin_item_id):
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


def _get_item_context(provider, item_id):
    """Return provider-specific item context for theme operations."""
    provider = _normalize_provider(provider)
    if provider == 'plex':
        plex = get_plex()
        item = plex.fetchItem(int(item_id))
        local_path = _validate_local_media_path(get_item_local_path(item))
        return {
            'provider': 'plex',
            'item_id': str(item.ratingKey),
            'title': item.title,
            'item': item,
            'client': plex,
            'local_path': local_path,
            'has_plex_theme': bool(getattr(item, 'theme', None)),
        }

    jellyfin, _, item = _get_jellyfin_item(item_id)
    local_path = _validate_local_media_path(get_jellyfin_item_local_path(item))
    return {
        'provider': 'jellyfin',
        'item_id': str(item.get('Id')),
        'title': item.get('Name') or 'Unknown',
        'item': item,
        'client': jellyfin,
        'local_path': local_path,
        'has_plex_theme': False,
    }
def plex_session_get(plex, url, **kwargs):
    """Fetch Plex media using plexapi's authenticated session."""
    # plexapi does not expose a higher-level helper for arbitrary media proxying,
    # so these web endpoints intentionally reuse its authenticated requests session.
    return plex._session.get(url, **kwargs)


def extract_external_ids(item):
    """Extract external IDs (IMDB/TVDB) from Plex item guids.
    
    Returns dict with 'imdb' and 'tvdb' keys (None if not found).
    """
    ids = {'imdb': None, 'tvdb': None, 'tmdb': None}
    for guid in getattr(item, 'guids', []):
        if isinstance(guid, dict):
            guid_id = guid.get('id', '')
        else:
            guid_id = getattr(guid, 'id', '') or ''
        if guid_id.startswith('imdb://'):
            ids['imdb'] = guid_id.replace('imdb://', '')
        elif guid_id.startswith('tmdb://'):
            ids['tmdb'] = guid_id.replace('tmdb://', '')
        elif guid_id.startswith('tvdb://'):
            ids['tvdb'] = guid_id.replace('tvdb://', '')
    return ids


def _normalize_external_id(value):
    """Normalize provider ID values to a stripped string or None."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def extract_jellyfin_external_ids(item):
    """Extract external IDs (IMDB/TMDB/TVDB) from a Jellyfin item dict."""
    provider_ids = item.get('ProviderIds') if isinstance(item, dict) else {}
    provider_ids = provider_ids if isinstance(provider_ids, dict) else {}
    return {
        'imdb': _normalize_external_id(provider_ids.get('Imdb')),
        'tmdb': _normalize_external_id(provider_ids.get('Tmdb')),
        'tvdb': _normalize_external_id(provider_ids.get('Tvdb')),
    }


def get_themerrdb_theme_for_external_ids(item_type, external_ids):
    """Resolve ThemerrDB theme metadata from external IDs for a media type."""
    themerr_item_type = 'tv_shows' if item_type == 'show' else 'movies'
    tmdb_id = external_ids.get('tmdb') or external_ids.get('tvdb')
    cache_ids = {
        external_ids.get('imdb'),
        external_ids.get('tmdb'),
        external_ids.get('tvdb'),
    }
    for database, external_id in [('imdb', external_ids.get('imdb')), ('themoviedb', tmdb_id)]:
        if external_id:
            theme_data = query_themerrdb(themerr_item_type, database, external_id)
            if theme_data:
                for cache_external_id in cache_ids:
                    if not cache_external_id:
                        continue
                    _set_cached_themerrdb(cache_external_id, theme_data)
                return theme_data
    return None


def get_themerrdb_theme_for_item(provider, item):
    """Get ThemerrDB theme metadata for a provider item (Plex or Jellyfin)."""
    if provider == 'plex':
        item_type = 'show' if getattr(item, 'type', None) == 'show' else 'movie'
        external_ids = extract_external_ids(item)
    else:
        item_type = 'show' if (item.get('Type') or '').lower() == 'series' else 'movie'
        external_ids = extract_jellyfin_external_ids(item)
    return get_themerrdb_theme_for_external_ids(item_type, external_ids)


def _get_themerrdb_cache_key(external_id):
    """Generate cache key for ThemerrDB query."""
    return f'themerrdb_{external_id}'


def _get_cached_themerrdb(external_id):
    """Return (cache_hit, data) for a ThemerrDB cache lookup."""
    cache_key = _get_themerrdb_cache_key(external_id)
    with _themerrdb_cache_lock:
        cached = _themerrdb_cache.get(cache_key)
        if cached and time.time() - cached['timestamp'] < THEMERRDB_CACHE_TTL:
            return True, cached['data']
    return False, None


def _set_cached_themerrdb(external_id, data):
    """Cache ThemerrDB response."""
    cache_key = _get_themerrdb_cache_key(external_id)
    with _themerrdb_cache_lock:
        _themerrdb_cache[cache_key] = {
            'data': data,
            'timestamp': time.time(),
        }


def query_themerrdb(item_type, database, external_id):
    """Query ThemerrDB API for theme availability.
    
    Args:
        item_type: 'movies' or 'tv_shows'
        database: 'imdb' or 'themoviedb'
        external_id: the IMDB/TVDB ID
    
    Returns:
        Theme metadata dict (with 'youtube_theme_url' key) or None if not found.
    """
    if not external_id:
        return None
    
    # Check cache first
    cache_hit, cached = _get_cached_themerrdb(external_id)
    if cache_hit:
        return cached
    
    try:
        url = f'{THEMERRDB_API_BASE}/{item_type}/{database}/{external_id}.json'
        logger.debug('Querying ThemerrDB for theme availability')
        response = http_requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            _set_cached_themerrdb(external_id, data)
            return data
        elif response.status_code == 404:
            logger.debug('Theme not found in ThemerrDB')
            _set_cached_themerrdb(external_id, None)
            return None
        else:
            logger.warning('ThemerrDB query failed with status %s', response.status_code)
            return None
    except Exception as exc:
        logger.error('Error querying ThemerrDB: %s', type(exc).__name__)
        return None


def get_themerrdb_theme(item):
    """Get ThemerrDB theme data for a Plex item if available.
    
    Returns theme metadata dict or None if not available.
    """
    return get_themerrdb_theme_for_item('plex', item)


def _get_themerrdb_data_for_context(context):
    """Resolve ThemerrDB metadata for a provider item context."""
    return get_themerrdb_theme_for_item(context['provider'], context['item'])


def _get_external_ids_for_context(context):
    """Return normalized external IDs for a provider item context."""
    if context['provider'] == 'plex':
        return extract_external_ids(context['item'])
    return extract_jellyfin_external_ids(context['item'])


def _check_themerrdb_availability_for_context(context, *, validate_preview=False):
    """Return availability metadata for a provider item's ThemerrDB theme."""
    external_ids = _get_external_ids_for_context(context)
    if not any(external_ids.values()):
        return {
            'available': False,
            'reason': 'No IMDB/TMDB/TVDB identifiers are available for this item.',
            'external_ids': external_ids,
        }

    themerrdb_data = _get_themerrdb_data_for_context(context)
    if not themerrdb_data:
        return {
            'available': False,
            'reason': 'No matching theme was found in ThemerrDB.',
            'external_ids': external_ids,
        }

    youtube_url = themerrdb_data.get('youtube_theme_url')
    if not youtube_url:
        return {
            'available': False,
            'reason': 'ThemerrDB did not provide a YouTube theme URL for this item.',
            'external_ids': external_ids,
        }

    if validate_preview:
        try:
            _extract_youtube_audio_url(youtube_url)
        except yt_dlp.utils.DownloadError as exc:
            return {
                'available': False,
                'reason': f'Theme URL found but preview is unavailable: {_clean_yt_dlp_error(exc)}',
                'external_ids': external_ids,
                'youtube_url': youtube_url,
            }

    return {
        'available': True,
        'youtube_url': youtube_url,
        'external_ids': external_ids,
    }


def _check_plex_preview_availability(item):
    """Return availability metadata for Plex source theme preview."""
    if not getattr(item, 'theme', None):
        return {'available': False, 'reason': 'No theme is available in Plex for this item.'}

    try:
        plex = get_plex()
        url = plex.url(item.theme, includeToken=True)
        response = plex_session_get(plex, url, stream=True, timeout=15)
        response.raise_for_status()
        response.close()
        return {'available': True}
    except Exception as exc:
        logger.warning('Unable to stream Plex preview for item %s: %s', getattr(item, 'ratingKey', '?'), exc)
        return {'available': False, 'reason': 'Unable to stream the Plex preview right now.'}


def scan_local_theme_dirs(base_paths):
    """Scan base directories and return a dict mapping directory path -> theme.mp3 size.

    Supports both library root paths and item-directory paths as scan inputs.
    This keeps callers fast while allowing mixed cache hydration strategies.
    """
    theme_dirs = {}
    for base in base_paths:
        try:
            base_path = Path(base)
            direct_theme = base_path / 'theme.mp3'
            if direct_theme.is_file():
                size = direct_theme.stat().st_size
                if size > 0:
                    theme_dirs[str(base_path)] = size

            for p in base_path.glob('*/theme.mp3'):
                try:
                    size = p.stat().st_size
                    if size > 0:
                        theme_dirs[str(p.parent)] = size
                except OSError:
                    pass
        except Exception:
            pass
    return theme_dirs


def get_section_base_paths(plex):
    """Return the unique set of root directory paths for all show/movie sections.

    Uses Plex's reported ``locations`` for each section so we don't need any
    path-related environment variables — the caller must mount library paths at
    the same container path as the Plex container.
    """
    paths = set()
    try:
        for section in plex.library.sections():
            if section.type in ('show', 'movie'):
                for loc in section.locations:
                    paths.add(loc)
    except Exception:
        pass
    return paths


def get_item_local_path(item):
    """Get the local filesystem path for a Plex library item.

    Uses the path reported by Plex directly — this works when the container
    mounts the same library paths at the same locations as the Plex container.
    """
    if not hasattr(item, 'locations') or not item.locations:
        return None

    for loc in item.locations:
        candidate = Path(loc)
        if item.type == 'show':
            # Avoid filesystem stat calls here (especially on NFS). Plex already
            # gives us the canonical library path; we use it directly.
            return candidate
        if item.type == 'movie':
            # Movie locations may be either file paths or folder paths.
            return candidate.parent if _is_video_file_path(candidate) else candidate

    return None


def is_valid_youtube_url(url):
    """Validate that a URL points to a supported YouTube host."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    if parsed.scheme not in {'http', 'https'}:
        return False

    hostname = (parsed.hostname or '').lower()
    return hostname in ALLOWED_YOUTUBE_HOSTS




def youtube_match_filter(info_dict, *, incomplete):
    """Reject overly long videos before downloading."""
    duration = info_dict.get('duration')
    if duration and duration > MAX_YOUTUBE_DURATION_SECONDS:
        return f'Video exceeds {MAX_YOUTUBE_DURATION_SECONDS} seconds'
    return None


def _youtube_retry_profiles():
    """Yield yt-dlp retry profiles for videos with client-specific availability."""
    return [
        ('default', {}),
        ('android', {'extractor_args': {'youtube': {'player_client': ['android']}}}),
    ]


def _youtube_preview_ydl_opts(profile_overrides=None):
    """Build yt-dlp options for extracting a preview audio stream URL."""
    opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'skip_download': True,
        'socket_timeout': 30,
        'js_runtimes': {'node': {}},
        'remote_components': ['ejs:github'],
    }
    if profile_overrides:
        opts.update(profile_overrides)
    return opts


def _youtube_download_ydl_opts(tmpdir, profile_overrides=None):
    """Build yt-dlp options for downloading and converting a theme MP3."""
    opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': os.path.join(tmpdir, 'theme.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'match_filter': youtube_match_filter,
        'max_filesize': MAX_UPLOAD_BYTES,
        'js_runtimes': {'node': {}},
        'remote_components': ['ejs:github'],
    }
    if profile_overrides:
        opts.update(profile_overrides)
    return opts


def _clean_yt_dlp_error(exc):
    """Normalize yt-dlp error messages for user-facing responses."""
    return str(exc).removeprefix('ERROR: ').strip()


def _stream_http_response_chunks(response, *, chunk_size=8192):
    """Yield streamed HTTP response chunks while handling client disconnects."""
    try:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if chunk:
                yield chunk
    except (http_requests.exceptions.ChunkedEncodingError, http_requests.exceptions.ConnectionError, BrokenPipeError, ConnectionResetError) as exc:
        logger.info('Stream interrupted while sending preview audio: %s', exc)
    finally:
        response.close()


def _extract_youtube_audio_url(youtube_url):
    """Resolve a direct audio stream URL for a YouTube video with retries."""
    errors = []
    for profile_name, overrides in _youtube_retry_profiles():
        try:
            with yt_dlp.YoutubeDL(_youtube_preview_ydl_opts(overrides)) as ydl:
                info = ydl.extract_info(youtube_url, download=False)
            audio_url = info.get('url')
            if audio_url:
                return audio_url
            errors.append(f'{profile_name}: Could not extract audio from YouTube')
        except yt_dlp.utils.DownloadError as exc:
            errors.append(f'{profile_name}: {_clean_yt_dlp_error(exc)}')
    raise yt_dlp.utils.DownloadError(' | '.join(errors))


def _download_youtube_theme_mp3(youtube_url, tmpdir):
    """Download YouTube audio as MP3 with client-profile fallback retries."""
    errors = []
    for profile_name, overrides in _youtube_retry_profiles():
        try:
            with yt_dlp.YoutubeDL(_youtube_download_ydl_opts(tmpdir, overrides)) as ydl:
                ydl.download([youtube_url])
            mp3_files = list(Path(tmpdir).glob('*.mp3'))
            if mp3_files:
                return mp3_files[0]
            errors.append(f'{profile_name}: Download failed: no MP3 file produced')
        except yt_dlp.utils.DownloadError as exc:
            errors.append(f'{profile_name}: {_clean_yt_dlp_error(exc)}')
    raise yt_dlp.utils.DownloadError(' | '.join(errors))

def is_valid_upload(upload_file):
    """Validate uploaded theme file name, type, and size."""
    if request.content_length and request.content_length > MAX_UPLOAD_BYTES:
        return False, ('Uploaded file is too large', 413)

    filename = (upload_file.filename or '').lower()
    if not filename.endswith('.mp3'):
        return False, ('Only MP3 uploads are supported', 400)

    content_type = (upload_file.content_type or '').lower()
    if content_type and content_type not in ALLOWED_UPLOAD_TYPES:
        return False, ('Uploaded file must be an MP3', 400)

    return True, None


# ============================================================
# Pushover notifications
# ============================================================

def send_pushover_notification(title, message):
    """Send a Pushover push notification if PUSHOVER_APP_TOKEN and PUSHOVER_USER_KEY are set."""
    token = os.getenv('PUSHOVER_APP_TOKEN')
    user_key = os.getenv('PUSHOVER_USER_KEY')
    if not token or not user_key:
        return
    try:
        resp = http_requests.post(
            'https://api.pushover.net/1/messages.json',
            data={'token': token, 'user': user_key, 'title': title, 'message': message},
            timeout=10,
        )
        resp.raise_for_status()
        logger.debug('Pushover notification sent: %s', title)
    except Exception as exc:
        logger.warning('Failed to send Pushover notification: %s', exc)


# ============================================================

# Theme download helpers
# ============================================================


def _download_plex_theme_to_path(plex, item, theme_path):
    """Download Plex theme for *item* and save to *theme_path*. Returns True on success."""
    url = plex.url(item.theme, includeToken=True)
    response = plex_session_get(plex, url, stream=True, timeout=30)
    response.raise_for_status()
    theme_path.parent.mkdir(parents=True, exist_ok=True)
    with open(theme_path, 'wb') as fh:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                fh.write(chunk)
    logger.info('Downloaded Plex theme for %s to %s', item.title, theme_path)
    return True


def _process_plex_library_new(rating_key):
    """Process a Plex library.new webhook event by downloading theme if needed.
    
    Retrieves the item from Plex by rating key, checks if theme.mp3 already exists,
    and downloads the Plex theme if available.
    """
    try:
        plex = get_plex()
        item = plex.library.fetchItem(int(rating_key))
        
        logger.info("Plex webhook: processing new item '%s' (ratingKey=%s)", item.title, rating_key)
        
        if not getattr(item, 'theme', None):
            logger.info("Plex webhook: '%s' has no theme in Plex — nothing to download", item.title)
            return
        
        local_path = get_item_local_path(item)
        if not local_path:
            logger.warning("Plex webhook: cannot determine local path for '%s'", item.title)
            return
        
        theme_path = local_path / 'theme.mp3'
        if theme_path.exists() and theme_path.stat().st_size > 0:
            logger.info("Plex webhook: '%s' already has a theme file", item.title)
            return
        
        _download_plex_theme_to_path(plex, item, theme_path)
        send_pushover_notification(
            title='Theme Downloaded',
            message=f'{item.title} theme auto-downloaded via Plex webhook',
        )
    except Exception as exc:
        logger.error("Plex webhook: failed to process item %s: %s", rating_key, exc)
        send_pushover_notification(
            title='Theme Download Failed',
            message=f'Failed to process Plex webhook for item {rating_key}',
        )


def item_to_dict(item, theme_dirs=None, provider='plex', library_id=None):
    """Serialize a Plex item to a dict for JSON response.

    If *theme_dirs* is provided (a dict from scan_local_theme_dirs), theme
    existence is resolved via a dict lookup instead of individual stat() calls.
    """
    local_path = get_item_local_path(item)
    theme_exists = False
    theme_size = 0
    if local_path:
        if theme_dirs is not None:
            theme_size = theme_dirs.get(str(local_path), 0)
            theme_exists = theme_size > 0
        else:
            theme_path = local_path / 'theme.mp3'
            if theme_path.exists() and theme_path.stat().st_size > 0:
                theme_exists = True
                theme_size = theme_path.stat().st_size

    # Extract external IDs for ThemerrDB availability check
    external_ids = extract_external_ids(item)
    has_themerrdb_theme = False
    if external_ids['imdb'] or external_ids['tvdb']:
        themerrdb_data = get_themerrdb_theme(item)
        has_themerrdb_theme = themerrdb_data is not None

    return {
        'id': str(item.ratingKey),
        'ratingKey': item.ratingKey,
        'provider': provider,
        'library_id': str(library_id) if library_id is not None else None,
        'title': item.title,
        'year': getattr(item, 'year', None),
        'thumb': item.thumb,
        'type': item.type,
        'has_plex_theme': bool(getattr(item, 'theme', None)),
        'has_local_theme': theme_exists,
        'has_themerrdb_theme': has_themerrdb_theme,
        'theme_size': theme_size,
        'local_path': str(local_path) if local_path else None,
        'external_ids': external_ids,
    }



# ============================================================
# Library item cache
# ============================================================

def _build_library_items(section_id, include_theme_state=True, provider='plex'):
    """Fetch and return sorted item dicts for a provider library section (no caching)."""
    started = time.perf_counter()
    provider = _normalize_provider(provider)

    if provider == 'plex':
        plex = get_plex()
        section = plex.library.sectionByID(section_id)

        fetch_started = time.perf_counter()
        items = section.all()
        fetch_duration = time.perf_counter() - fetch_started

        if include_theme_state:
            section_locations = getattr(section, 'locations', None)
            if isinstance(section_locations, (list, tuple, set)):
                base_paths = {path for path in section_locations if isinstance(path, str) and path}
            else:
                base_paths = set()
            if not base_paths:
                base_paths = get_section_base_paths(plex)
            scan_started = time.perf_counter()
            theme_dirs = scan_local_theme_dirs(base_paths)
            scan_duration = time.perf_counter() - scan_started
        else:
            theme_dirs = {}
            scan_duration = 0.0

        logger.info('Checking ThemerrDB availability for %s section %s (%d items)', provider, section_id, len(items))
        result = [item_to_dict(item, theme_dirs=theme_dirs, provider='plex', library_id=section_id) for item in items]
    else:
        jellyfin = get_jellyfin()
        user_id = get_jellyfin_user_id(jellyfin)
        fetch_started = time.perf_counter()
        response = jellyfin_session_get(
            jellyfin,
            f'/Users/{user_id}/Items',
            params={
                'ParentId': str(section_id),
                'IncludeItemTypes': 'Series,Movie',
                'Recursive': 'true',
                'Fields': 'Path,ProductionYear,ProviderIds',
            },
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get('Items', [])
        fetch_duration = time.perf_counter() - fetch_started

        if include_theme_state:
            base_paths = set()
            for item in items:
                local_path = get_jellyfin_item_local_path(item)
                if local_path:
                    base_paths.add(str(local_path.parent if _is_video_file_path(local_path) else local_path))
            scan_started = time.perf_counter()
            theme_dirs = scan_local_theme_dirs(base_paths) if base_paths else {}
            scan_duration = time.perf_counter() - scan_started
        else:
            theme_dirs = {}
            scan_duration = 0.0

        logger.info('Checking ThemerrDB availability for %s section %s (%d items)', provider, section_id, len(items))
        result = [
            _serialize_jellyfin_item(item, section_id, theme_dirs=theme_dirs)
            for item in items
        ]

    result.sort(key=lambda item: item['title'].lower())
    if include_theme_state:
        logger.info(
            'Built %s section %s item payload: %d items (theme scan %.2fs, fetch %.2fs, total %.2fs)',
            provider, section_id, len(result), scan_duration, fetch_duration, time.perf_counter() - started,
        )
    else:
        logger.info(
            'Built %s section %s metadata payload: %d items (fetch %.2fs, total %.2fs)',
            provider, section_id, len(result), fetch_duration, time.perf_counter() - started,
        )
    return result


def _invalidate_library_cache():
    """Drop all cached libraries/posters so the next fetch re-queries Plex."""
    with _library_cache_lock:
        _library_cache.clear()
    with _poster_cache_lock:
        _poster_cache.clear()
    with _jellyfin_user_id_lock:
        _jellyfin_user_id_cache['value'] = None
    with _theme_hydration_status_lock:
        _theme_hydration_status.update({
            'running': True,
            'ready': False,
            'sections_total': 0,
            'sections_completed': 0,
        })


def _get_section_build_lock(section_id):
    """Return a per-section lock to avoid duplicate cache builds under load."""
    section_id = str(section_id)
    with _section_build_locks_lock:
        section_lock = _section_build_locks.get(section_id)
        if section_lock is None:
            section_lock = threading.Lock()
            _section_build_locks[section_id] = section_lock
    return section_lock


def _set_theme_hydration_total(sections_total):
    with _theme_hydration_status_lock:
        _theme_hydration_status.update({
            'running': True,
            'ready': False,
            'sections_total': sections_total,
            'sections_completed': 0,
        })


def _advance_theme_hydration_progress():
    with _theme_hydration_status_lock:
        completed = min(
            _theme_hydration_status.get('sections_total', 0),
            _theme_hydration_status.get('sections_completed', 0) + 1,
        )
        _theme_hydration_status['sections_completed'] = completed
        total = _theme_hydration_status.get('sections_total', 0)
        if total > 0 and completed >= total:
            _theme_hydration_status['running'] = False
            _theme_hydration_status['ready'] = True


def _mark_theme_hydration_finished():
    with _theme_hydration_status_lock:
        _theme_hydration_status['running'] = False
        _theme_hydration_status['ready'] = True
        _theme_hydration_status['sections_completed'] = _theme_hydration_status.get('sections_total', 0)


def _get_theme_hydration_status():
    with _theme_hydration_status_lock:
        return dict(_theme_hydration_status)


def _get_cached_item(rating_key, provider=None):
    """Return a cached item dict by ratingKey and optional provider, or None."""
    target = str(rating_key)
    provider = (provider or '').strip().lower() or None
    with _library_cache_lock:
        for section_items in _library_cache.values():
            for cached_item in section_items:
                if str(cached_item.get('ratingKey')) != target:
                    continue
                if provider and (cached_item.get('provider') or 'plex') != provider:
                    continue
                return cached_item
    return None


def _get_cached_poster(rating_key, provider='plex'):
    """Return cached poster payload dict for *(provider, rating_key)*, or None."""
    with _poster_cache_lock:
        return _poster_cache.get(f'{provider}:{rating_key}')


def _set_cached_poster(rating_key, content, content_type, provider='plex'):
    """Store poster bytes in the in-memory poster cache."""
    with _poster_cache_lock:
        _poster_cache[f'{provider}:{rating_key}'] = {
            'content': content,
            'content_type': content_type,
        }


def _fetch_poster_bytes(plex, thumb, timeout=10):
    """Fetch poster bytes for a Plex thumb path."""
    url = plex.url(thumb, includeToken=True)
    response = plex_session_get(plex, url, stream=True, timeout=timeout)
    response.raise_for_status()
    content_type = response.headers.get('content-type', 'image/jpeg')
    return response.content, content_type


def _warm_poster_cache():
    """Background thread: pre-load poster images for already-cached library items."""
    logger.info('Poster cache warmup starting…')
    try:
        plex = get_plex()
    except Exception as exc:
        logger.warning('Poster cache warmup: could not connect to Plex: %s', exc)
        return

    with _library_cache_lock:
        all_items = [
            item
            for section_items in _library_cache.values()
            for item in section_items
            if item.get('thumb') and item.get('ratingKey') is not None
        ]

    unique_items = {}
    for item in all_items:
        unique_items[int(item['ratingKey'])] = item
    items_to_warm = list(unique_items.values())

    if not items_to_warm:
        logger.info('Poster cache warmup skipped: no cached items found.')
        return

    warmed = 0

    def warm_item(item):
        rating_key = int(item['ratingKey'])
        if _get_cached_poster(rating_key) is not None:
            return True
        try:
            content, content_type = _fetch_poster_bytes(plex, item['thumb'], timeout=10)
            _set_cached_poster(rating_key, content, content_type)
            return True
        except Exception as exc:
            logger.debug('Poster cache warmup failed for ratingKey %s: %s', rating_key, exc)
            return False

    max_workers = min(6, max(1, len(items_to_warm)))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='poster-cache-warm') as executor:
        futures = [executor.submit(warm_item, item) for item in items_to_warm]
        for future in as_completed(futures):
            if future.result():
                warmed += 1

    logger.info('Poster cache warmup complete: %d/%d posters cached', warmed, len(items_to_warm))


def _kick_off_poster_cache_warmup():
    """Start poster warmup in a background thread."""
    t = threading.Thread(target=_warm_poster_cache, daemon=True, name='poster-cache-rebuild')
    t.start()


def _sync_cached_item(item):
    """Update an item's cached entry in-place after local theme state changes."""
    updated_item = item_to_dict(item)
    updated = False
    with _library_cache_lock:
        for section_items in _library_cache.values():
            for index, cached_item in enumerate(section_items):
                if str(cached_item.get('ratingKey')) == str(item.ratingKey):
                    section_items[index] = updated_item
                    updated = True
                    break
            if updated:
                break
    return updated_item, updated


def _sync_cached_item_theme_state(provider, item_id):
    """Refresh has_local_theme/theme_size for a cached item by provider/id."""
    provider = _normalize_provider(provider)
    target_id = str(item_id)
    with _library_cache_lock:
        for section_key, section_items in _library_cache.items():
            for idx, cached_item in enumerate(section_items):
                cached_provider = cached_item.get('provider') or 'plex'
                if cached_provider != provider or str(cached_item.get('id') or cached_item.get('ratingKey')) != target_id:
                    continue
                local_path = cached_item.get('local_path')
                theme_size = 0
                if local_path:
                    theme_path = Path(local_path) / 'theme.mp3'
                    if theme_path.exists():
                        try:
                            theme_size = theme_path.stat().st_size
                        except OSError:
                            theme_size = 0
                updated = dict(cached_item)
                updated['has_local_theme'] = theme_size > 0
                updated['theme_size'] = theme_size
                section_items[idx] = updated
                _library_cache[section_key] = section_items
                return updated, True
    return None, False


def _kick_off_cache_warmup():
    """Invalidate the cache and rebuild all sections in a background thread."""
    _invalidate_library_cache()
    t = threading.Thread(target=_warm_library_cache, daemon=True, name='library-cache-rebuild')
    t.start()


def _warm_library_cache():
    """Background thread: pre-load metadata, then hydrate local theme state."""
    logger.info('Library cache warmup starting…')
    sections = []
    try:
        plex = get_plex()
        for section in plex.library.sections():
            if section.type in ('show', 'movie'):
                sections.append({
                    'provider': 'plex',
                    'cache_key': section.key,
                    'section_id': section.key,
                    'title': section.title,
                })
    except Exception as exc:
        logger.warning('Library cache warmup: could not connect to Plex: %s', exc)

    if jellyfin_is_configured():
        try:
            for section in _get_jellyfin_libraries():
                section_id = str(section['id'])
                sections.append({
                    'provider': 'jellyfin',
                    'cache_key': f'jellyfin:{section_id}',
                    'section_id': section_id,
                    'title': section.get('title') or section_id,
                })
        except Exception as exc:
            logger.warning('Library cache warmup: could not connect to Jellyfin: %s', exc)

    _set_theme_hydration_total(len(sections))
    if not sections:
        _mark_theme_hydration_finished()
        return

    def warm_section_metadata(section_info):
        provider = section_info['provider']
        cache_key = section_info['cache_key']
        section_id = section_info['section_id']
        section_lock = _get_section_build_lock(cache_key)
        with section_lock:
            with _library_cache_lock:
                if _library_cache.get(cache_key) is not None:
                    return
            try:
                items = _build_library_items(section_id, include_theme_state=False, provider=provider)
                with _library_cache_lock:
                    _library_cache[cache_key] = items
            except Exception as exc:
                logger.warning('Library metadata warmup failed for %s section %s: %s', provider, section_id, exc)

    def hydrate_section_theme_state(section_info):
        provider = section_info['provider']
        cache_key = section_info['cache_key']
        section_id = section_info['section_id']
        section_title = section_info['title']
        try:
            section_lock = _get_section_build_lock(cache_key)
            with section_lock:
                with _library_cache_lock:
                    cached_items = _library_cache.get(cache_key)
                    if cached_items is None:
                        return

                    base_paths = set()
                    for cached_item in cached_items:
                        local_path = cached_item.get('local_path')
                        if not local_path:
                            continue
                        path = Path(local_path)
                        base_paths.add(str(path.parent if _is_video_file_path(path) else path))

                    theme_dirs = scan_local_theme_dirs(base_paths) if base_paths else {}
                    updated_items = []
                    for cached_item in cached_items:
                        updated_item = dict(cached_item)
                        local_path = updated_item.get('local_path')
                        if local_path:
                            path = Path(local_path)
                            lookup_key = str(path.parent if _is_video_file_path(path) else path)
                            theme_size = theme_dirs.get(lookup_key, 0)
                        else:
                            theme_size = 0
                        updated_item['has_local_theme'] = theme_size > 0
                        updated_item['theme_size'] = theme_size
                        updated_items.append(updated_item)
                    _library_cache[cache_key] = updated_items
            logger.info(
                'Library cache theme hydration complete: %s section %s (%s) — %d items',
                provider, section_id, section_title, len(updated_items),
            )
        except Exception as exc:
            logger.warning('Library cache theme hydration failed for %s section %s: %s', provider, section_id, exc)
        finally:
            _advance_theme_hydration_progress()

    max_workers = min(4, max(1, len(sections)))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='library-cache-warm') as executor:
        futures = [executor.submit(warm_section_metadata, section) for section in sections]
        for future in as_completed(futures):
            future.result()

    hydrate_workers = min(2, max(1, len(sections)))
    with ThreadPoolExecutor(max_workers=hydrate_workers, thread_name_prefix='theme-hydrate') as executor:
        futures = [executor.submit(hydrate_section_theme_state, section) for section in sections]
        for future in as_completed(futures):
            future.result()

    logger.info('Library cache warmup complete.')
    _kick_off_poster_cache_warmup()



@app.route('/')
def index():
    """Serve the main Web UI.

    Reads DEFAULT_THEME from the environment (``dark`` or ``light``) to set
    the initial colour scheme rendered into the page.  The user can override
    this in-browser; their choice is persisted in localStorage.

    Reads DEFAULT_VIEW from the environment (``list`` or ``grid``) to set
    the initial library view.  User preference is persisted in localStorage.
    """
    default_theme = os.getenv('DEFAULT_THEME', 'dark').strip().lower()
    if default_theme not in ('dark', 'light'):
        default_theme = 'dark'
    default_view = os.getenv('DEFAULT_VIEW', 'list').strip().lower()
    if default_view not in ('list', 'grid'):
        default_view = 'list'
    return render_template(
        'index.html',
        default_theme=default_theme,
        default_view=default_view,
        asset_version=ASSET_VERSION,
    )


@app.route('/health')
def health():
    """Health check endpoint for container orchestration systems."""
    return jsonify({'status': 'healthy'}), 200


@app.route('/api/status')
def get_status():
    """Check Plex connection status."""
    try:
        plex = get_plex()
        return jsonify({
            'connected': True,
            'server_name': plex.friendlyName,
            'version': plex.version,
        })
    except Exception as exc:
        logger.error('Plex connection failed: %s', exc)
        return jsonify({'connected': False, 'error': 'Unable to connect to Plex'})


@app.route('/api/libraries')
def get_libraries():
    """Return list of TV and Movie libraries from Plex and Jellyfin."""
    result = []
    plex_error = None
    jellyfin_error = None

    try:
        plex = get_plex()
        sections = plex.library.sections()
        for section in sections:
            if section.type in ('show', 'movie'):
                result.append({
                    'id': section.key,
                    # Keep `key` for frontend/API backwards compatibility.
                    'key': section.key,
                    'title': section.title,
                    'type': section.type,
                    'thumb': section.thumb,
                    'totalSize': section.totalSize,
                    'provider': 'plex',
                })
    except Exception as exc:
        plex_error = exc

    if jellyfin_is_configured():
        try:
            result.extend(_get_jellyfin_libraries())
        except Exception as exc:
            jellyfin_error = exc

    if result:
        return jsonify(result)

    if plex_error and jellyfin_error:
        return error_response('Failed to get libraries from Plex and Jellyfin', exc=f'{plex_error}; {jellyfin_error}')
    if plex_error:
        return error_response('Failed to get libraries', exc=plex_error)
    if jellyfin_error:
        return error_response('Failed to get libraries', exc=jellyfin_error)
    return jsonify([])


@app.route('/api/cache/status')
def get_cache_status():
    """Return startup cache/hydration status for the Web UI startup overlay."""
    status = _get_theme_hydration_status()
    return jsonify(status)


@app.route('/api/libraries/<int:section_id>/items')
def get_library_items(section_id):
    """Return all items in a library section, served from cache when available."""
    with _library_cache_lock:
        cached = _library_cache.get(section_id)
    if cached is not None:
        return jsonify(cached)

    section_lock = _get_section_build_lock(section_id)
    with section_lock:
        with _library_cache_lock:
            cached = _library_cache.get(section_id)
        if cached is not None:
            return jsonify(cached)
        try:
            result = _build_library_items(section_id, provider='plex')
            with _library_cache_lock:
                _library_cache[section_id] = result
            return jsonify(result)
        except Exception as exc:
            return error_response(f'Failed to get items for section {section_id}', exc=exc)


@app.route('/api/libraries/<provider>/<path:section_id>/items')
def get_library_items_by_provider(provider, section_id):
    """Return all items in a provider library section."""
    try:
        provider = _normalize_provider(provider)
    except ValueError as exc:
        return error_response('Invalid provider', status_code=400, exc=exc)

    if provider == 'plex':
        try:
            plex_section_id = int(section_id)
        except ValueError as exc:
            return error_response('Plex library id must be an integer', status_code=400, exc=exc)
        return get_library_items(plex_section_id)

    cache_key = f'jellyfin:{section_id}'
    with _library_cache_lock:
        cached = _library_cache.get(cache_key)
    if cached is not None:
        return jsonify(cached)

    section_lock = _get_section_build_lock(cache_key)
    with section_lock:
        with _library_cache_lock:
            cached = _library_cache.get(cache_key)
        if cached is not None:
            return jsonify(cached)
        try:
            result = _build_library_items(section_id, provider='jellyfin')
            with _library_cache_lock:
                _library_cache[cache_key] = result
            return jsonify(result)
        except Exception as exc:
            return error_response(f'Failed to get items for {provider} section {section_id}', exc=exc)


@app.route('/api/poster/<int:rating_key>')
def get_poster(rating_key):
    """Proxy Plex poster image to avoid CORS/token issues in browser."""
    try:
        cached_poster = _get_cached_poster(rating_key, provider='plex')
        if cached_poster is not None:
            return Response(cached_poster['content'], mimetype=cached_poster['content_type'])

        plex = get_plex()
        cached_item = _get_cached_item(rating_key, provider='plex')
        thumb = cached_item.get('thumb') if cached_item else None
        if not thumb:
            item = plex.fetchItem(rating_key)
            thumb = item.thumb

        if not thumb:
            return jsonify({'error': 'No poster available'}), 404

        content, content_type = _fetch_poster_bytes(plex, thumb, timeout=10)
        _set_cached_poster(rating_key, content, content_type, provider='plex')
        return Response(content, mimetype=content_type)
    except Exception as exc:
        return error_response(f'Failed to get poster for {rating_key}', exc=exc)


@app.route('/api/poster/<provider>/<path:item_id>')
def get_provider_poster(provider, item_id):
    """Proxy provider poster image to avoid CORS/token issues in browser."""
    try:
        provider = _normalize_provider(provider)
    except ValueError as exc:
        return error_response('Invalid provider', status_code=400, exc=exc)

    if provider == 'plex':
        try:
            return get_poster(int(item_id))
        except ValueError as exc:
            return error_response('Plex item id must be an integer', status_code=400, exc=exc)

    try:
        cached_poster = _get_cached_poster(item_id, provider='jellyfin')
        if cached_poster is not None:
            return Response(cached_poster['content'], mimetype=cached_poster['content_type'])

        jellyfin = get_jellyfin()
        response = jellyfin_session_get(jellyfin, f'/Items/{item_id}/Images/Primary')
        response.raise_for_status()
        content_type = response.headers.get('content-type', 'image/jpeg')
        _set_cached_poster(item_id, response.content, content_type, provider='jellyfin')
        return Response(response.content, mimetype=content_type)
    except Exception as exc:
        return error_response(f'Failed to get poster for {provider} item {item_id}', exc=exc)


@app.route('/api/items/<int:rating_key>/theme', methods=['GET'])
def get_theme(rating_key):
    """Stream the local theme.mp3 file for playback in browser."""
    try:
        plex = get_plex()
        item = plex.fetchItem(rating_key)
        local_path = get_item_local_path(item)
        if not local_path:
            return jsonify({'error': 'Cannot determine local path for item'}), 404
        theme_path = local_path / 'theme.mp3'
        if not theme_path.exists() or theme_path.stat().st_size == 0:
            return jsonify({'error': 'No theme file found'}), 404
        return send_file(str(theme_path), mimetype='audio/mpeg', conditional=True)
    except Exception as exc:
        return error_response(f'Failed to serve theme for {rating_key}', exc=exc)


@app.route('/api/items/<int:rating_key>/theme/preview')
def preview_plex_theme(rating_key):
    """Stream the Plex theme directly without saving it locally (preview)."""
    try:
        plex = get_plex()
        item = plex.fetchItem(rating_key)
        if not getattr(item, 'theme', None):
            return jsonify({'error': 'No theme available in Plex for this item'}), 404
        url = plex.url(item.theme, includeToken=True)
        response = plex_session_get(plex, url, stream=True, timeout=30)
        response.raise_for_status()
        return Response(
            _stream_http_response_chunks(response),
            mimetype='audio/mpeg',
            headers={'Cache-Control': 'no-cache'},
        )
    except Exception as exc:
        return error_response(f'Failed to preview Plex theme for {rating_key}', exc=exc)


@app.route('/api/items/<int:rating_key>/theme/download', methods=['POST'])
def download_theme_from_plex(rating_key):
    """Download the theme from Plex and save it as theme.mp3."""
    try:
        plex = get_plex()
        item = plex.fetchItem(rating_key)

        if not getattr(item, 'theme', None):
            return jsonify({'error': 'No theme available in Plex for this item'}), 404

        local_path = get_item_local_path(item)
        if not local_path:
            return jsonify({'error': 'Cannot determine local path for item'}), 404

        theme_path = local_path / 'theme.mp3'
        data = request.get_json(silent=True) or {}
        overwrite = data.get('overwrite', False)

        if theme_path.exists() and theme_path.stat().st_size > 0 and not overwrite:
            return jsonify({'error': 'Theme already exists', 'exists': True}), 409

        url = plex.url(item.theme, includeToken=True)
        response = plex_session_get(plex, url, stream=True, timeout=30)
        response.raise_for_status()

        local_path.mkdir(parents=True, exist_ok=True)
        with open(theme_path, 'wb') as file_handle:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    file_handle.write(chunk)

        logger.info('Downloaded theme for %s to %s', item.title, theme_path)
        send_pushover_notification('Theme Downloaded', f'{item.title} theme downloaded from Plex')
        item_dict, _ = _sync_cached_item(item)
        return jsonify({'success': True, 'path': str(theme_path), 'item': item_dict})
    except Exception as exc:
        return error_response(f'Failed to download theme from Plex for {rating_key}', exc=exc)


@app.route('/api/items/<int:rating_key>/theme/copy', methods=['POST'])
def copy_theme_from_item(rating_key):
    """Copy a local theme.mp3 from one item to another."""
    try:
        data = request.get_json(silent=True) or {}
        source_rating_key = data.get('sourceRatingKey')
        if source_rating_key is None:
            return jsonify({'error': 'sourceRatingKey is required'}), 400

        try:
            source_rating_key = int(source_rating_key)
        except (TypeError, ValueError):
            return jsonify({'error': 'sourceRatingKey must be a valid integer'}), 400

        if source_rating_key == int(rating_key):
            return jsonify({'error': 'Source item must be different from target item'}), 400

        overwrite = data.get('overwrite', False)

        plex = get_plex()
        target_item = plex.fetchItem(rating_key)
        source_item = plex.fetchItem(source_rating_key)

        target_local_path = get_item_local_path(target_item)
        if not target_local_path:
            return jsonify({'error': 'Cannot determine local path for target item'}), 404

        source_local_path = get_item_local_path(source_item)
        if not source_local_path:
            return jsonify({'error': 'Cannot determine local path for source item'}), 404

        source_theme_path = source_local_path / 'theme.mp3'
        if not source_theme_path.exists() or source_theme_path.stat().st_size == 0:
            return jsonify({'error': 'Source item has no local theme to copy'}), 404

        target_theme_path = target_local_path / 'theme.mp3'
        if target_theme_path.exists() and target_theme_path.stat().st_size > 0 and not overwrite:
            return jsonify({'error': 'Theme already exists', 'exists': True}), 409

        target_local_path.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source_theme_path), str(target_theme_path))

        logger.info(
            'Copied theme from %s (%s) to %s (%s)',
            source_item.title, source_theme_path, target_item.title, target_theme_path,
        )
        send_pushover_notification('Theme Copied', f'{target_item.title} theme copied from {source_item.title}')
        item_dict, _ = _sync_cached_item(target_item)
        return jsonify({'success': True, 'path': str(target_theme_path), 'item': item_dict})
    except Exception as exc:
        return error_response(f'Failed to copy theme for {rating_key}', exc=exc)


@app.route('/api/items/<int:rating_key>/theme/upload', methods=['POST'])
def upload_theme(rating_key):
    """Accept an uploaded MP3 file and save it as theme.mp3."""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided in request'}), 400

        plex = get_plex()
        item = plex.fetchItem(rating_key)

        local_path = get_item_local_path(item)
        if not local_path:
            return jsonify({'error': 'Cannot determine local path for item'}), 404

        theme_path = local_path / 'theme.mp3'
        overwrite = request.form.get('overwrite', 'false').lower() == 'true'

        if theme_path.exists() and theme_path.stat().st_size > 0 and not overwrite:
            return jsonify({'error': 'Theme already exists', 'exists': True}), 409

        upload_file = request.files['file']
        valid, error = is_valid_upload(upload_file)
        if not valid:
            message, status_code = error
            return jsonify({'error': message}), status_code

        local_path.mkdir(parents=True, exist_ok=True)
        upload_file.save(str(theme_path))

        logger.info('Uploaded theme for %s to %s', item.title, theme_path)
        send_pushover_notification('Theme Uploaded', f'{item.title} theme uploaded')
        item_dict, _ = _sync_cached_item(item)
        return jsonify({'success': True, 'path': str(theme_path), 'item': item_dict})
    except Exception as exc:
        return error_response(f'Failed to upload theme for {rating_key}', exc=exc)


@app.route('/api/youtube/search', methods=['GET'])
def youtube_search():
    """Search YouTube and return up to *limit* results for a query string."""
    query = (request.args.get('q') or '').strip()
    if not query:
        return jsonify({'error': 'Search query is required'}), 400

    try:
        limit = min(int(request.args.get('limit', 5)), 10)
    except (TypeError, ValueError):
        limit = 5

    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
            'skip_download': True,
            'js_runtimes': {'node': {}},
            'remote_components': ['ejs:github'],
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f'ytsearch{limit}:{query}', download=False)

        results = []
        for entry in (info.get('entries') or []):
            video_id = entry.get('id')
            if not video_id:
                continue
            thumbnails = entry.get('thumbnails') or []
            thumbnail = thumbnails[0]['url'] if thumbnails else None
            raw_duration = entry.get('duration')
            if raw_duration:
                mins, secs = divmod(int(raw_duration), 60)
                duration_str = f'{mins}:{secs:02d}'
            else:
                duration_str = None
            results.append({
                'id': video_id,
                'title': entry.get('title'),
                'url': entry.get('url') or f'https://www.youtube.com/watch?v={video_id}',
                'channel': entry.get('channel') or entry.get('uploader'),
                'duration': duration_str,
                'thumbnail': thumbnail,
                'view_count': entry.get('view_count'),
            })

        return jsonify({'results': results})
    except Exception as exc:
        return error_response('YouTube search failed', exc=exc)


@app.route('/api/items/<int:rating_key>/theme/youtube', methods=['POST'])
def download_from_youtube(rating_key):
    """Download audio from a YouTube URL and save it as theme.mp3."""
    try:
        data = request.get_json(silent=True)
        if not data or not data.get('url'):
            return jsonify({'error': 'YouTube URL is required'}), 400

        youtube_url = data['url']
        if not is_valid_youtube_url(youtube_url):
            return jsonify({'error': 'Only YouTube URLs are supported'}), 400

        overwrite = data.get('overwrite', False)

        plex = get_plex()
        item = plex.fetchItem(rating_key)

        local_path = get_item_local_path(item)
        if not local_path:
            return jsonify({'error': 'Cannot determine local path for item'}), 404

        theme_path = local_path / 'theme.mp3'

        if theme_path.exists() and theme_path.stat().st_size > 0 and not overwrite:
            return jsonify({'error': 'Theme already exists', 'exists': True}), 409

        with tempfile.TemporaryDirectory(dir=YTDLP_WORKDIR) as tmpdir:
            mp3_path = _download_youtube_theme_mp3(youtube_url, tmpdir)
            local_path.mkdir(parents=True, exist_ok=True)
            shutil.move(str(mp3_path), str(theme_path))

        logger.info('Downloaded YouTube theme for %s to %s', item.title, theme_path)
        send_pushover_notification('Theme Downloaded', f'{item.title} theme downloaded from YouTube')
        item_dict, _ = _sync_cached_item(item)
        return jsonify({'success': True, 'path': str(theme_path), 'item': item_dict})
    except yt_dlp.utils.DownloadError as exc:
        msg = _clean_yt_dlp_error(exc)
        logger.error('Failed YouTube download for %s: %s', rating_key, exc)
        return jsonify({'error': msg}), 500
    except Exception as exc:
        return error_response(f'Failed YouTube download for {rating_key}', exc=exc)


@app.route('/api/items/<int:rating_key>/theme/themerrdb/check', methods=['GET'])
def check_themerrdb_availability(rating_key):
    """Check if a theme is available in ThemerrDB for an item."""
    try:
        plex = get_plex()
        item = plex.fetchItem(rating_key)
        context = {
            'provider': 'plex',
            'item': item,
        }
        return jsonify(_check_themerrdb_availability_for_context(context, validate_preview=True))
    except http_requests.exceptions.RequestException as exc:
        logger.warning('Failed to check ThemerrDB availability for Plex item %s: %s', rating_key, exc)
        return jsonify({'available': False, 'reason': 'Could not reach provider metadata.'})
    except Exception as exc:
        return error_response(f'Failed to check ThemerrDB availability for {rating_key}', exc=exc)


@app.route('/api/items/<int:rating_key>/theme/themerrdb/preview', methods=['GET'])
def preview_themerrdb_theme(rating_key):
    """Stream a theme preview from ThemerrDB via YouTube URL."""
    try:
        plex = get_plex()
        item = plex.fetchItem(rating_key)
        
        themerrdb_data = get_themerrdb_theme(item)
        if not themerrdb_data or not themerrdb_data.get('youtube_theme_url'):
            return jsonify({'error': 'No theme available in ThemerrDB'}), 404
        
        youtube_url = themerrdb_data['youtube_theme_url']
        
        audio_url = _extract_youtube_audio_url(youtube_url)
        response = http_requests.get(audio_url, stream=True, timeout=30)
        response.raise_for_status()
        return Response(
            _stream_http_response_chunks(response),
            mimetype='audio/mpeg',
            headers={'Cache-Control': 'no-cache'},
        )
    except yt_dlp.utils.DownloadError as exc:
        msg = _clean_yt_dlp_error(exc)
        logger.error('Failed to stream ThemerrDB theme preview: %s', exc)
        return jsonify({'error': f'ThemerrDB preview unavailable: {msg}'}), 502
    except Exception as exc:
        return error_response(f'Failed to preview ThemerrDB theme for {rating_key}', exc=exc)


@app.route('/api/items/<int:rating_key>/theme/themerrdb', methods=['POST'])
def download_from_themerrdb(rating_key):
    """Download theme from ThemerrDB and save as theme.mp3."""
    try:
        data = request.get_json(silent=True) or {}
        overwrite = data.get('overwrite', False)
        
        plex = get_plex()
        item = plex.fetchItem(rating_key)
        
        local_path = get_item_local_path(item)
        if not local_path:
            return jsonify({'error': 'Cannot determine local path for item'}), 404
        
        theme_path = local_path / 'theme.mp3'
        
        if theme_path.exists() and theme_path.stat().st_size > 0 and not overwrite:
            return jsonify({'error': 'Theme already exists', 'exists': True}), 409
        
        themerrdb_data = get_themerrdb_theme(item)
        if not themerrdb_data or not themerrdb_data.get('youtube_theme_url'):
            return jsonify({'error': 'No theme available in ThemerrDB'}), 404
        
        youtube_url = themerrdb_data['youtube_theme_url']
        
        with tempfile.TemporaryDirectory(dir=YTDLP_WORKDIR) as tmpdir:
            mp3_path = _download_youtube_theme_mp3(youtube_url, tmpdir)
            local_path.mkdir(parents=True, exist_ok=True)
            shutil.move(str(mp3_path), str(theme_path))
        
        logger.info('Downloaded ThemerrDB theme for %s to %s', item.title, theme_path)
        send_pushover_notification('Theme Downloaded', f'{item.title} theme downloaded from ThemerrDB')
        item_dict, _ = _sync_cached_item(item)
        return jsonify({'success': True, 'path': str(theme_path), 'item': item_dict})
    except yt_dlp.utils.DownloadError as exc:
        msg = _clean_yt_dlp_error(exc)
        logger.error('Failed ThemerrDB download for %s: %s', rating_key, exc)
        return jsonify({'error': f'ThemerrDB download failed: {msg}'}), 502
    except Exception as exc:
        return error_response(f'Failed to download ThemerrDB theme for {rating_key}', exc=exc)


@app.route('/api/items/<int:rating_key>/theme', methods=['DELETE'])
def delete_theme(rating_key):
    """Delete the local theme.mp3 file for an item."""
    try:
        plex = get_plex()
        item = plex.fetchItem(rating_key)
        local_path = get_item_local_path(item)
        if not local_path:
            return jsonify({'error': 'Cannot determine local path for item'}), 404
        theme_path = local_path / 'theme.mp3'
        if not theme_path.exists():
            return jsonify({'error': 'No theme file to delete'}), 404
        theme_path.unlink()
        logger.info('Deleted theme for %s', item.title)
        item_dict, _ = _sync_cached_item(item)
        return jsonify({'success': True, 'item': item_dict})
    except Exception as exc:
        return error_response(f'Failed to delete theme for {rating_key}', exc=exc)


# Provider-aware theme endpoints (Plex + Jellyfin)
@app.route('/api/items/<provider>/<path:item_id>/theme', methods=['GET'])
def get_provider_theme(provider, item_id):
    """Stream the local theme.mp3 file for provider items."""
    try:
        provider = _normalize_provider(provider)
        context = _get_item_context(provider, item_id)
        theme_path = _theme_file_path(context['local_path'])
        if not theme_path:
            return jsonify({'error': 'Cannot determine local path for item'}), 404
        if not theme_path.exists() or theme_path.stat().st_size == 0:
            return jsonify({'error': 'No theme file found'}), 404
        return send_file(str(theme_path), mimetype='audio/mpeg', conditional=True)
    except ValueError as exc:
        return error_response('Invalid provider or item identifier', status_code=400, exc=exc)
    except Exception as exc:
        return error_response(f'Failed to serve theme for {provider} item {item_id}', exc=exc)


@app.route('/api/items/<provider>/<path:item_id>/theme/preview')
def preview_provider_theme(provider, item_id):
    """Preview theme from provider source when supported."""
    try:
        provider = _normalize_provider(provider)
    except ValueError as exc:
        return error_response('Invalid provider', status_code=400, exc=exc)

    if provider != 'plex':
        return jsonify({'error': 'Theme preview from provider source is only supported for Plex items'}), 400
    try:
        return preview_plex_theme(int(item_id))
    except ValueError as exc:
        return error_response('Plex item id must be an integer', status_code=400, exc=exc)


@app.route('/api/items/<provider>/<path:item_id>/theme/preview/check')
def check_provider_theme_preview(provider, item_id):
    """Check whether previewing provider source theme is possible."""
    try:
        provider = _normalize_provider(provider)
        if provider != 'plex':
            return jsonify({'available': False, 'reason': 'Theme preview from provider source is only supported for Plex items.'})

        context = _get_item_context(provider, item_id)
        return jsonify(_check_plex_preview_availability(context['item']))
    except http_requests.exceptions.RequestException as exc:
        logger.warning('Failed to check preview availability for %s item %s: %s', provider, item_id, exc)
        return jsonify({'available': False, 'reason': 'Could not reach provider to validate preview.'})
    except ValueError as exc:
        return error_response('Invalid provider or item identifier', status_code=400, exc=exc)
    except Exception as exc:
        return error_response(f'Failed to check preview availability for {provider} item {item_id}', exc=exc)


@app.route('/api/items/<provider>/<path:item_id>/theme/download', methods=['POST'])
def download_provider_theme(provider, item_id):
    """Download theme from provider source when supported."""
    try:
        provider = _normalize_provider(provider)
    except ValueError as exc:
        return error_response('Invalid provider', status_code=400, exc=exc)

    if provider != 'plex':
        return jsonify({'error': 'Downloading from provider source is only supported for Plex items'}), 400
    try:
        return download_theme_from_plex(int(item_id))
    except ValueError as exc:
        return error_response('Plex item id must be an integer', status_code=400, exc=exc)


@app.route('/api/items/<provider>/<path:item_id>/theme/themerrdb/check', methods=['GET'])
def check_provider_themerrdb_availability(provider, item_id):
    """Check if a ThemerrDB theme is available for a provider item."""
    try:
        provider = _normalize_provider(provider)
        context = _get_item_context(provider, item_id)
        return jsonify(_check_themerrdb_availability_for_context(context, validate_preview=True))
    except http_requests.exceptions.RequestException as exc:
        logger.warning('Failed to check ThemerrDB availability for %s item %s: %s', provider, item_id, exc)
        return jsonify({'available': False, 'reason': 'Could not reach provider metadata.'})
    except ValueError as exc:
        return error_response('Invalid provider or item identifier', status_code=400, exc=exc)
    except Exception as exc:
        return error_response(f'Failed to check ThemerrDB availability for {provider} item {item_id}', exc=exc)


@app.route('/api/items/<provider>/<path:item_id>/theme/themerrdb/preview', methods=['GET'])
def preview_provider_themerrdb_theme(provider, item_id):
    """Stream a ThemerrDB theme preview for a provider item."""
    try:
        provider = _normalize_provider(provider)
        context = _get_item_context(provider, item_id)
        themerrdb_data = _get_themerrdb_data_for_context(context)
        if not themerrdb_data or not themerrdb_data.get('youtube_theme_url'):
            return jsonify({'error': 'No theme available in ThemerrDB'}), 404

        youtube_url = themerrdb_data['youtube_theme_url']
        audio_url = _extract_youtube_audio_url(youtube_url)
        response = http_requests.get(audio_url, stream=True, timeout=30)
        response.raise_for_status()
        return Response(
            _stream_http_response_chunks(response),
            mimetype='audio/mpeg',
            headers={'Cache-Control': 'no-cache'},
        )
    except yt_dlp.utils.DownloadError as exc:
        msg = _clean_yt_dlp_error(exc)
        logger.error('Failed to stream ThemerrDB theme preview for %s item %s: %s', provider, item_id, exc)
        return jsonify({'error': f'ThemerrDB preview unavailable: {msg}'}), 502
    except ValueError as exc:
        return error_response('Invalid provider or item identifier', status_code=400, exc=exc)
    except Exception as exc:
        return error_response(f'Failed to preview ThemerrDB theme for {provider} item {item_id}', exc=exc)


@app.route('/api/items/<provider>/<path:item_id>/theme/themerrdb', methods=['POST'])
def download_provider_from_themerrdb(provider, item_id):
    """Download a ThemerrDB theme for a provider item and save as theme.mp3."""
    try:
        provider = _normalize_provider(provider)
        data = request.get_json(silent=True) or {}
        overwrite = data.get('overwrite', False)

        context = _get_item_context(provider, item_id)
        local_path = _validate_local_media_path(context['local_path'])
        if not local_path:
            return jsonify({'error': 'Cannot determine local path for item'}), 404

        theme_path = _theme_file_path(local_path)
        if theme_path.exists() and theme_path.stat().st_size > 0 and not overwrite:
            return jsonify({'error': 'Theme already exists', 'exists': True}), 409

        themerrdb_data = _get_themerrdb_data_for_context(context)
        if not themerrdb_data or not themerrdb_data.get('youtube_theme_url'):
            return jsonify({'error': 'No theme available in ThemerrDB'}), 404

        youtube_url = themerrdb_data['youtube_theme_url']
        with tempfile.TemporaryDirectory(dir=YTDLP_WORKDIR) as tmpdir:
            mp3_path = _download_youtube_theme_mp3(youtube_url, tmpdir)
            local_path.mkdir(parents=True, exist_ok=True)
            shutil.move(str(mp3_path), str(theme_path))

        logger.info('Downloaded ThemerrDB theme for %s item', provider)
        send_pushover_notification('Theme Downloaded', f"{context['title']} theme downloaded from ThemerrDB")
        item_dict, _ = _sync_cached_item_theme_state(provider, item_id)
        return jsonify({'success': True, 'path': str(theme_path), 'item': item_dict})
    except yt_dlp.utils.DownloadError as exc:
        msg = _clean_yt_dlp_error(exc)
        logger.error('Failed ThemerrDB download for %s item %s: %s', provider, item_id, exc)
        return jsonify({'error': f'ThemerrDB download failed: {msg}'}), 502
    except ValueError as exc:
        return error_response('Invalid provider or item identifier', status_code=400, exc=exc)
    except Exception as exc:
        return error_response(f'Failed to download ThemerrDB theme for {provider} item {item_id}', exc=exc)


@app.route('/api/items/<provider>/<path:item_id>/theme/copy', methods=['POST'])
def copy_provider_theme_from_item(provider, item_id):
    """Copy a local theme.mp3 from one provider item to another."""
    try:
        provider = _normalize_provider(provider)
        data = request.get_json(silent=True) or {}
        source_provider = _normalize_provider(data.get('sourceProvider') or provider)
        source_item_id = data.get('sourceItemId')
        if source_item_id is None:
            return jsonify({'error': 'sourceItemId is required'}), 400
        source_item_id = str(source_item_id)

        if source_provider == provider and str(source_item_id) == str(item_id):
            return jsonify({'error': 'Source item must be different from target item'}), 400

        overwrite = data.get('overwrite', False)

        target_context = _get_item_context(provider, item_id)
        source_context = _get_item_context(source_provider, source_item_id)

        target_local_path = _validate_local_media_path(target_context['local_path'])
        if not target_local_path:
            return jsonify({'error': 'Cannot determine local path for target item'}), 404

        source_local_path = _validate_local_media_path(source_context['local_path'])
        if not source_local_path:
            return jsonify({'error': 'Cannot determine local path for source item'}), 404

        source_theme_path = _theme_file_path(source_local_path)
        if not source_theme_path.exists() or source_theme_path.stat().st_size == 0:
            return jsonify({'error': 'Source item has no local theme to copy'}), 404

        target_theme_path = _theme_file_path(target_local_path)
        if target_theme_path.exists() and target_theme_path.stat().st_size > 0 and not overwrite:
            return jsonify({'error': 'Theme already exists', 'exists': True}), 409

        target_local_path.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(source_theme_path), str(target_theme_path))

        logger.info('Copied theme from %s item to %s item', source_provider, provider)
        send_pushover_notification(
            'Theme Copied',
            f"{target_context['title']} theme copied from {source_context['title']}",
        )
        item_dict, _ = _sync_cached_item_theme_state(provider, item_id)
        return jsonify({'success': True, 'path': str(target_theme_path), 'item': item_dict})
    except ValueError as exc:
        return error_response('Invalid provider or item identifier', status_code=400, exc=exc)
    except Exception as exc:
        return error_response(f'Failed to copy theme for {provider} item {item_id}', exc=exc)


@app.route('/api/items/<provider>/<path:item_id>/theme/upload', methods=['POST'])
def upload_provider_theme(provider, item_id):
    """Accept an uploaded MP3 file and save it as theme.mp3."""
    try:
        provider = _normalize_provider(provider)
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided in request'}), 400

        context = _get_item_context(provider, item_id)
        local_path = _validate_local_media_path(context['local_path'])
        if not local_path:
            return jsonify({'error': 'Cannot determine local path for item'}), 404

        theme_path = _theme_file_path(local_path)
        overwrite = request.form.get('overwrite', 'false').lower() == 'true'
        if theme_path.exists() and theme_path.stat().st_size > 0 and not overwrite:
            return jsonify({'error': 'Theme already exists', 'exists': True}), 409

        upload_file = request.files['file']
        valid, error = is_valid_upload(upload_file)
        if not valid:
            message, status_code = error
            return jsonify({'error': message}), status_code

        local_path.mkdir(parents=True, exist_ok=True)
        upload_file.save(str(theme_path))

        logger.info('Uploaded theme for %s item', provider)
        send_pushover_notification('Theme Uploaded', f"{context['title']} theme uploaded")
        item_dict, _ = _sync_cached_item_theme_state(provider, item_id)
        return jsonify({'success': True, 'path': str(theme_path), 'item': item_dict})
    except ValueError as exc:
        return error_response('Invalid provider or item identifier', status_code=400, exc=exc)
    except Exception as exc:
        return error_response(f'Failed to upload theme for {provider} item {item_id}', exc=exc)


@app.route('/api/items/<provider>/<path:item_id>/theme/youtube', methods=['POST'])
def download_provider_from_youtube(provider, item_id):
    """Download audio from a YouTube URL and save it as theme.mp3."""
    try:
        provider = _normalize_provider(provider)
        data = request.get_json(silent=True)
        if not data or not data.get('url'):
            return jsonify({'error': 'YouTube URL is required'}), 400

        youtube_url = data['url']
        if not is_valid_youtube_url(youtube_url):
            return jsonify({'error': 'Only YouTube URLs are supported'}), 400

        overwrite = data.get('overwrite', False)
        context = _get_item_context(provider, item_id)

        local_path = _validate_local_media_path(context['local_path'])
        if not local_path:
            return jsonify({'error': 'Cannot determine local path for item'}), 404

        theme_path = _theme_file_path(local_path)
        if theme_path.exists() and theme_path.stat().st_size > 0 and not overwrite:
            return jsonify({'error': 'Theme already exists', 'exists': True}), 409

        with tempfile.TemporaryDirectory(dir=YTDLP_WORKDIR) as tmpdir:
            mp3_path = _download_youtube_theme_mp3(youtube_url, tmpdir)
            local_path.mkdir(parents=True, exist_ok=True)
            shutil.move(str(mp3_path), str(theme_path))

        logger.info('Downloaded YouTube theme for %s item', provider)
        send_pushover_notification('Theme Downloaded', f"{context['title']} theme downloaded from YouTube")
        item_dict, _ = _sync_cached_item_theme_state(provider, item_id)
        return jsonify({'success': True, 'path': str(theme_path), 'item': item_dict})
    except yt_dlp.utils.DownloadError as exc:
        msg = _clean_yt_dlp_error(exc)
        logger.error('Failed YouTube download for %s:%s: %s', provider, item_id, exc)
        return jsonify({'error': msg}), 500
    except ValueError as exc:
        return error_response('Invalid provider or item identifier', status_code=400, exc=exc)
    except Exception as exc:
        return error_response(f'Failed YouTube download for {provider} item {item_id}', exc=exc)


@app.route('/api/items/<provider>/<path:item_id>/theme', methods=['DELETE'])
def delete_provider_theme(provider, item_id):
    """Delete the local theme.mp3 file for a provider item."""
    try:
        provider = _normalize_provider(provider)
        context = _get_item_context(provider, item_id)
        local_path = _validate_local_media_path(context['local_path'])
        if not local_path:
            return jsonify({'error': 'Cannot determine local path for item'}), 404
        theme_path = _theme_file_path(local_path)
        if not theme_path.exists():
            return jsonify({'error': 'No theme file to delete'}), 404
        theme_path.unlink()
        logger.info('Deleted theme for %s item %s', provider, item_id)
        item_dict, _ = _sync_cached_item_theme_state(provider, item_id)
        return jsonify({'success': True, 'item': item_dict})
    except ValueError as exc:
        return error_response('Invalid provider or item identifier', status_code=400, exc=exc)
    except Exception as exc:
        return error_response(f'Failed to delete theme for {provider} item {item_id}', exc=exc)


# ============================================================
# Bulk operations
# ============================================================

@app.route('/api/bulk/theme/download', methods=['POST'])
def bulk_download_themes():
    """Download themes for multiple Plex items in one request."""
    data = request.get_json(silent=True)
    if not data or not isinstance(data.get('ratingKeys'), list):
        return jsonify({'error': 'ratingKeys (list) is required'}), 400

    rating_keys = data['ratingKeys']
    overwrite = data.get('overwrite', False)

    if not rating_keys:
        return jsonify({'error': 'ratingKeys list is empty'}), 400
    if len(rating_keys) > MAX_BULK_ITEMS:
        return jsonify({'error': f'Maximum {MAX_BULK_ITEMS} items per bulk operation'}), 400

    try:
        plex = get_plex()
    except Exception as exc:
        return error_response('Failed to connect to Plex', exc=exc)

    results = {'success': [], 'skipped': [], 'no_theme': [], 'failed': []}

    for rating_key in rating_keys:
        try:
            item = plex.fetchItem(int(rating_key))

            if not getattr(item, 'theme', None):
                results['no_theme'].append({'ratingKey': rating_key, 'title': item.title})
                continue

            local_path = get_item_local_path(item)
            if not local_path:
                results['failed'].append({
                    'ratingKey': rating_key,
                    'title': getattr(item, 'title', '?'),
                    'error': 'Cannot determine local path',
                })
                continue

            theme_path = local_path / 'theme.mp3'

            if theme_path.exists() and theme_path.stat().st_size > 0 and not overwrite:
                results['skipped'].append({'ratingKey': rating_key, 'title': item.title})
                continue

            url = plex.url(item.theme, includeToken=True)
            response = plex_session_get(plex, url, stream=True, timeout=30)
            response.raise_for_status()

            local_path.mkdir(parents=True, exist_ok=True)
            with open(theme_path, 'wb') as fh:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        fh.write(chunk)

            results['success'].append({'ratingKey': rating_key, 'title': item.title})
            _sync_cached_item(item)
            logger.info('Bulk: downloaded theme for %s', item.title)

        except Exception as exc:
            results['failed'].append({'ratingKey': rating_key, 'error': str(exc)})

    if results['success']:
        titles = ', '.join(r['title'] for r in results['success'][:5])
        extra = len(results['success']) - 5
        msg = f"{titles}{f' and {extra} more' if extra > 0 else ''}"
        send_pushover_notification(
            title=f"Themes Downloaded ({len(results['success'])})",
            message=msg,
        )

    return jsonify(results)


# ============================================================
# Webhooks — Plex
# ============================================================

@app.route('/api/webhooks/plex', methods=['POST'])
def plex_webhook():
    """Handle Plex webhook events (library.new).
    
    Plex sends webhooks as form-encoded data with a 'payload' field containing JSON.
    For library.new events, we extract the ratingKey and process the theme download.
    """
    payload_str = request.form.get('payload', '')
    if not payload_str:
        logger.warning('Plex webhook: missing payload')
        return jsonify({'success': True}), 200
    
    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError:
        logger.warning('Plex webhook: invalid JSON payload')
        return jsonify({'success': True}), 200
    
    event = payload.get('event', '')
    logger.info('Plex webhook: event=%s', event)
    
    if event == 'library.new':
        metadata = payload.get('Metadata', {})
        if not metadata:
            metadata = payload.get('metadata', {})
        
        rating_key = metadata.get('ratingKey')
        if rating_key:
            threading.Thread(
                target=_process_plex_library_new,
                args=(rating_key,),
                daemon=True,
                name=f'webhook-plex-{rating_key}',
            ).start()
            logger.info('Plex webhook: queued theme processing for ratingKey=%s', rating_key)
        else:
            logger.warning('Plex webhook: library.new event without ratingKey')
    
    return jsonify({'success': True}), 200


# ============================================================
# Settings endpoints
# ============================================================

@app.route('/api/settings/test-pushover', methods=['POST'])
def settings_test_pushover():
    """Send a test Pushover notification to verify connectivity."""
    token = os.getenv('PUSHOVER_APP_TOKEN')
    user_key = os.getenv('PUSHOVER_USER_KEY')
    if not token or not user_key:
        return jsonify({
            'error': 'Pushover is not configured. Set PUSHOVER_APP_TOKEN and PUSHOVER_USER_KEY.',
        }), 400
    try:
        resp = http_requests.post(
            'https://api.pushover.net/1/messages.json',
            data={'token': token, 'user': user_key,
                  'title': 'Themarr - Test Notification',
                  'message': 'Pushover notifications are working correctly.'},
            timeout=10,
        )
        resp.raise_for_status()
        return jsonify({'success': True})
    except Exception as exc:
        return error_response('Failed to send test Pushover notification', exc=exc)


@app.route('/api/settings/rescan', methods=['POST'])
def settings_rescan():
    """Rescan all media library items and count local theme.mp3 files."""
    try:
        plex = get_plex()
        base_paths = get_section_base_paths(plex)
        theme_dirs = scan_local_theme_dirs(base_paths)
        sections = plex.library.sections()
        total = 0
        with_theme = 0
        for section in sections:
            if section.type not in ('show', 'movie'):
                continue
            for item in section.all():
                total += 1
                local_path = get_item_local_path(item)
                if local_path and str(local_path) in theme_dirs:
                    with_theme += 1
        _kick_off_cache_warmup()
        return jsonify({
            'success': True,
            'total': total,
            'with_theme': with_theme,
            'without_theme': total - with_theme,
        })
    except Exception as exc:
        return error_response('Failed to rescan libraries', exc=exc)


@app.route('/api/settings/refresh-cache', methods=['POST'])
def settings_refresh_cache():
    """Invalidate and rebuild the library item cache in the background."""
    _kick_off_cache_warmup()
    return jsonify({'success': True, 'message': 'Cache refresh started in background'})


if __name__ == '__main__':
    port = int(os.getenv('PORT', '8080'))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    _kick_off_cache_warmup()
    app.run(host='0.0.0.0', port=port, debug=debug)
