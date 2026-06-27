#!/usr/bin/env python3
"""Themarr Web Application - Flask-based Web UI for managing Plex theme music."""

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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
YTDLP_WORKDIR = Path(tempfile.gettempdir()) / 'themarr_yt_dlp_work'
YTDLP_WORKDIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_YOUTUBE_DURATION_SECONDS = 15 * 60
MAX_BULK_ITEMS = 100
PLEX_RETRY_ATTEMPTS_DEFAULT = 10
PLEX_RETRY_DELAY_DEFAULT = 30  # seconds between retry attempts
ALLOWED_UPLOAD_TYPES = {'audio/mpeg', 'audio/mp3', 'application/octet-stream'}
ALLOWED_YOUTUBE_HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be'}

# In-memory cache for library items, warmed at startup to make first page loads instant.
_library_cache: dict = {}        # {section_id: [item_dict, ...]}
_library_cache_lock = threading.Lock()
_section_build_locks: dict = {}  # {section_id: threading.Lock()}
_section_build_locks_lock = threading.Lock()
_poster_cache: dict = {}         # {rating_key: {'content': bytes, 'content_type': str}}
_poster_cache_lock = threading.Lock()
_theme_hydration_status = {
    'running': False,
    'ready': True,
    'sections_total': 0,
    'sections_completed': 0,
}
_theme_hydration_status_lock = threading.Lock()

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


def plex_session_get(plex, url, **kwargs):
    """Fetch Plex media using plexapi's authenticated session."""
    # plexapi does not expose a higher-level helper for arbitrary media proxying,
    # so these web endpoints intentionally reuse its authenticated requests session.
    return plex._session.get(url, **kwargs)


def scan_local_theme_dirs(base_paths):
    """Scan base directories and return a dict mapping directory path -> theme.mp3 size.

    Does a single glob pass per base directory instead of one stat() per item,
    which dramatically reduces NFS round-trips when libraries are large.
    """
    theme_dirs = {}
    for base in base_paths:
        try:
            for p in Path(base).glob('*/theme.mp3'):
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
            return candidate.parent if candidate.suffix else candidate

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
# Webhook helpers
# ============================================================

def _check_webhook_auth():
    """Validate basic-auth credentials if WEBHOOK_USERNAME / WEBHOOK_PASSWORD are configured."""
    username = os.getenv('WEBHOOK_USERNAME')
    password = os.getenv('WEBHOOK_PASSWORD')
    if not username or not password:
        return True  # No auth configured — allow all
    auth = request.authorization
    return bool(auth and auth.username == username and auth.password == password)


def _find_plex_item(plex, title, path, media_type):
    """Search Plex for an item by title and optional path, returns first match or None."""
    section_type = 'show' if media_type == 'show' else 'movie'
    folder_name = Path(path).name.lower() if path else None

    for section in plex.library.sections():
        if section.type != section_type:
            continue

        # Fast: title search
        results = section.search(title=title)
        if results:
            return results[0]

        # Fallback: folder-name match (handles slightly different Plex titles)
        if folder_name:
            for item in section.all():
                if not hasattr(item, 'locations') or not item.locations:
                    continue
                plex_path = item.locations[0]
                if section_type == 'movie':
                    item_folder = Path(plex_path).parent.name.lower()
                else:
                    item_folder = Path(plex_path).name.lower()
                if item_folder == folder_name:
                    return item

    return None


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


def _process_webhook_add(title, path, media_type):
    """Background thread: poll Plex for a newly added item then auto-download its theme."""
    max_attempts = int(os.getenv('PLEX_RETRY_ATTEMPTS', str(PLEX_RETRY_ATTEMPTS_DEFAULT)))
    base_delay = int(os.getenv('PLEX_RETRY_DELAY', str(PLEX_RETRY_DELAY_DEFAULT)))

    logger.info("Webhook: queued Plex search for '%s' (type=%s, max=%d attempts, delay=%ds)",
                title, media_type, max_attempts, base_delay)

    for attempt in range(max_attempts):
        delay = base_delay * (attempt + 1)  # Linear staggering: 30s, 60s, 90s …
        logger.info("Webhook: sleeping %ds before attempt %d/%d for '%s'",
                    delay, attempt + 1, max_attempts, title)
        time.sleep(delay)

        try:
            plex = get_plex()
            item = _find_plex_item(plex, title, path, media_type)

            if item is None:
                logger.info("Webhook: '%s' not yet in Plex (attempt %d)", title, attempt + 1)
                continue

            logger.info("Webhook: found '%s' in Plex as '%s'", title, item.title)

            if not getattr(item, 'theme', None):
                logger.info("Webhook: '%s' has no theme in Plex — nothing to download", item.title)
                return

            local_path = get_item_local_path(item)
            if not local_path:
                logger.warning("Webhook: cannot determine local path for '%s'", item.title)
                return

            theme_path = local_path / 'theme.mp3'
            if theme_path.exists() and theme_path.stat().st_size > 0:
                logger.info("Webhook: '%s' already has a theme file", item.title)
                return

            _download_plex_theme_to_path(plex, item, theme_path)
            send_pushover_notification(
                title='Theme Downloaded',
                message=f'{item.title} theme auto-downloaded via webhook',
            )
            return

        except Exception as exc:
            logger.error("Webhook: attempt %d for '%s' failed: %s", attempt + 1, title, exc)

    logger.warning("Webhook: gave up searching for '%s' after %d attempts", title, max_attempts)
    send_pushover_notification(
        title='Theme Download Failed',
        message=f'Could not find \u201c{title}\u201d in Plex after {max_attempts} attempts',
    )


def item_to_dict(item, theme_dirs=None):
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

    return {
        'ratingKey': item.ratingKey,
        'title': item.title,
        'year': getattr(item, 'year', None),
        'thumb': item.thumb,
        'type': item.type,
        'has_plex_theme': bool(getattr(item, 'theme', None)),
        'has_local_theme': theme_exists,
        'theme_size': theme_size,
        'local_path': str(local_path) if local_path else None,
    }


# ============================================================
# Library item cache
# ============================================================

def _build_library_items(section_id, include_theme_state=True):
    """Fetch and return sorted item dicts for a Plex section (no caching)."""
    started = time.perf_counter()
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

    result = [item_to_dict(item, theme_dirs=theme_dirs) for item in items]
    result.sort(key=lambda item: item['title'].lower())
    if include_theme_state:
        logger.info(
            'Built section %s item payload: %d items (theme scan %.2fs, plex fetch %.2fs, total %.2fs)',
            section_id, len(result), scan_duration, fetch_duration, time.perf_counter() - started,
        )
    else:
        logger.info(
            'Built section %s metadata payload: %d items (plex fetch %.2fs, total %.2fs)',
            section_id, len(result), fetch_duration, time.perf_counter() - started,
        )
    return result


def _invalidate_library_cache():
    """Drop all cached libraries/posters so the next fetch re-queries Plex."""
    with _library_cache_lock:
        _library_cache.clear()
    with _poster_cache_lock:
        _poster_cache.clear()
    with _theme_hydration_status_lock:
        _theme_hydration_status.update({
            'running': True,
            'ready': False,
            'sections_total': 0,
            'sections_completed': 0,
        })


def _get_section_build_lock(section_id):
    """Return a per-section lock to avoid duplicate cache builds under load."""
    section_id = int(section_id)
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


def _get_cached_item(rating_key):
    """Return a cached item dict by ratingKey, or None if not cached."""
    target = str(rating_key)
    with _library_cache_lock:
        for section_items in _library_cache.values():
            for cached_item in section_items:
                if str(cached_item.get('ratingKey')) == target:
                    return cached_item
    return None


def _get_cached_poster(rating_key):
    """Return cached poster payload dict for *rating_key*, or None."""
    with _poster_cache_lock:
        return _poster_cache.get(int(rating_key))


def _set_cached_poster(rating_key, content, content_type):
    """Store poster bytes in the in-memory poster cache."""
    with _poster_cache_lock:
        _poster_cache[int(rating_key)] = {
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


def _kick_off_cache_warmup():
    """Invalidate the cache and rebuild all sections in a background thread."""
    _invalidate_library_cache()
    t = threading.Thread(target=_warm_library_cache, daemon=True, name='library-cache-rebuild')
    t.start()


def _warm_library_cache():
    """Background thread: pre-load metadata, then hydrate local theme state."""
    logger.info('Library cache warmup starting…')
    try:
        plex = get_plex()
        sections = [s for s in plex.library.sections() if s.type in ('show', 'movie')]
    except Exception as exc:
        logger.warning('Library cache warmup: could not connect to Plex: %s', exc)
        _mark_theme_hydration_finished()
        return

    _set_theme_hydration_total(len(sections))
    if not sections:
        _mark_theme_hydration_finished()
        return

    def warm_section_metadata(section):
        section_lock = _get_section_build_lock(section.key)
        with section_lock:
            with _library_cache_lock:
                if _library_cache.get(section.key) is not None:
                    return
            try:
                items = _build_library_items(section.key, include_theme_state=False)
                with _library_cache_lock:
                    _library_cache[section.key] = items
            except Exception as exc:
                logger.warning('Library metadata warmup failed for section %s: %s', section.key, exc)

    def hydrate_section_theme_state(section):
        try:
            section_locations = getattr(section, 'locations', None)
            if isinstance(section_locations, (list, tuple, set)):
                base_paths = {path for path in section_locations if isinstance(path, str) and path}
            else:
                base_paths = set()
            if not base_paths:
                theme_dirs = {}
            else:
                theme_dirs = scan_local_theme_dirs(base_paths)

            section_lock = _get_section_build_lock(section.key)
            with section_lock:
                with _library_cache_lock:
                    cached_items = _library_cache.get(section.key)
                    if cached_items is None:
                        return
                    updated_items = []
                    for cached_item in cached_items:
                        updated_item = dict(cached_item)
                        local_path = updated_item.get('local_path')
                        theme_size = theme_dirs.get(local_path, 0) if local_path else 0
                        updated_item['has_local_theme'] = theme_size > 0
                        updated_item['theme_size'] = theme_size
                        updated_items.append(updated_item)
                    _library_cache[section.key] = updated_items
            logger.info(
                'Library cache theme hydration complete: section %s (%s) — %d items',
                section.key, section.title, len(updated_items),
            )
        except Exception as exc:
            logger.warning('Library cache theme hydration failed for section %s: %s', section.key, exc)
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
    return render_template('index.html', default_theme=default_theme, default_view=default_view)


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
    """Return list of TV and Movie libraries from Plex."""
    try:
        plex = get_plex()
        sections = plex.library.sections()
        result = []
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
                })
        return jsonify(result)
    except Exception as exc:
        return error_response('Failed to get libraries', exc=exc)


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
            result = _build_library_items(section_id)
            with _library_cache_lock:
                _library_cache[section_id] = result
            return jsonify(result)
        except Exception as exc:
            return error_response(f'Failed to get items for section {section_id}', exc=exc)


@app.route('/api/poster/<int:rating_key>')
def get_poster(rating_key):
    """Proxy Plex poster image to avoid CORS/token issues in browser."""
    try:
        cached_poster = _get_cached_poster(rating_key)
        if cached_poster is not None:
            return Response(cached_poster['content'], mimetype=cached_poster['content_type'])

        plex = get_plex()
        cached_item = _get_cached_item(rating_key)
        thumb = cached_item.get('thumb') if cached_item else None
        if not thumb:
            item = plex.fetchItem(rating_key)
            thumb = item.thumb

        if not thumb:
            return jsonify({'error': 'No poster available'}), 404

        content, content_type = _fetch_poster_bytes(plex, thumb, timeout=10)
        _set_cached_poster(rating_key, content, content_type)
        return Response(content, mimetype=content_type)
    except Exception as exc:
        return error_response(f'Failed to get poster for {rating_key}', exc=exc)


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
            response.iter_content(chunk_size=8192),
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
            ydl_opts = {
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
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([youtube_url])

            mp3_files = list(Path(tmpdir).glob('*.mp3'))
            if not mp3_files:
                return jsonify({'error': 'Download failed: no MP3 file produced'}), 500

            local_path.mkdir(parents=True, exist_ok=True)
            shutil.move(str(mp3_files[0]), str(theme_path))

        logger.info('Downloaded YouTube theme for %s to %s', item.title, theme_path)
        send_pushover_notification('Theme Downloaded', f'{item.title} theme downloaded from YouTube')
        item_dict, _ = _sync_cached_item(item)
        return jsonify({'success': True, 'path': str(theme_path), 'item': item_dict})
    except yt_dlp.utils.DownloadError as exc:
        # Strip the leading "ERROR: " prefix yt-dlp adds so the toast reads cleanly
        msg = str(exc).removeprefix('ERROR: ').strip()
        logger.error('Failed YouTube download for %s: %s', rating_key, exc)
        return jsonify({'error': msg}), 500
    except Exception as exc:
        return error_response(f'Failed YouTube download for {rating_key}', exc=exc)


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
# Webhooks — Sonarr & Radarr
# ============================================================

@app.route('/api/webhooks/sonarr', methods=['POST'])
def sonarr_webhook():
    """Handle Sonarr webhook events (SeriesAdd, SeriesDelete, Test)."""
    if not _check_webhook_auth():
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Invalid JSON body'}), 400

    event_type = data.get('eventType', '')
    logger.info('Sonarr webhook: eventType=%s', event_type)

    if event_type == 'SeriesAdd':
        series = data.get('series', {})
        title = series.get('title', '')
        path = series.get('path', '')
        if title:
            threading.Thread(
                target=_process_webhook_add,
                args=(title, path, 'show'),
                daemon=True,
                name=f'webhook-sonarr-{title[:30]}',
            ).start()
        return jsonify({'success': True, 'eventType': event_type, 'queued': bool(title)})

    if event_type == 'SeriesDelete':
        series = data.get('series', {})
        logger.info("Sonarr SeriesDelete: '%s' — no action taken", series.get('title', ''))

    elif event_type == 'Test':
        logger.info('Sonarr webhook: test event received successfully')

    return jsonify({'success': True, 'eventType': event_type})


@app.route('/api/webhooks/radarr', methods=['POST'])
def radarr_webhook():
    """Handle Radarr webhook events (MovieAdded, MovieDeleted, Test)."""
    if not _check_webhook_auth():
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Invalid JSON body'}), 400

    event_type = data.get('eventType', '')
    logger.info('Radarr webhook: eventType=%s', event_type)

    if event_type == 'MovieAdded':
        movie = data.get('movie', {})
        title = movie.get('title', '')
        # Radarr provides 'folderPath' for the movie folder, 'path' as fallback
        path = movie.get('folderPath') or movie.get('path', '')
        if title:
            threading.Thread(
                target=_process_webhook_add,
                args=(title, path, 'movie'),
                daemon=True,
                name=f'webhook-radarr-{title[:30]}',
            ).start()
        return jsonify({'success': True, 'eventType': event_type, 'queued': bool(title)})

    if event_type == 'MovieDeleted':
        movie = data.get('movie', {})
        logger.info("Radarr MovieDeleted: '%s' — no action taken", movie.get('title', ''))

    elif event_type == 'Test':
        logger.info('Radarr webhook: test event received successfully')

    return jsonify({'success': True, 'eventType': event_type})


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
