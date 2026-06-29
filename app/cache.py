"""Library and poster caching, cache hydration."""

import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.plex_utils import get_plex, plex_is_configured, plex_session_get, get_section_base_paths, get_item_local_path
from app.media_utils import scan_local_theme_dirs, _is_video_file_path
from app.external_ids import extract_external_ids, extract_jellyfin_external_ids
from app.jellyfin_utils import get_jellyfin, get_jellyfin_user_id, get_jellyfin_item_local_path, _normalize_provider, jellyfin_is_configured, jellyfin_session_get, serialize_jellyfin_item, get_jellyfin_libraries
from app.themerrdb_service import get_themerrdb_theme, get_themerrdb_theme_for_item, get_themerrdb_data_for_context
from app.theme_state import is_plex_theme_source_unverified

logger = logging.getLogger(__name__)

POSTER_CACHE_MAX_ITEMS_DEFAULT = 500
BACKGROUND_WORKER_COUNT_DEFAULT = 4

_library_cache = {}
_library_cache_lock = threading.Lock()
_section_build_locks = {}
_section_build_locks_lock = threading.Lock()
_poster_cache = OrderedDict()
_poster_cache_lock = threading.Lock()
_theme_hydration_status = {
    'running': False,
    'ready': True,
    'sections_total': 0,
    'sections_completed': 0,
}
_theme_hydration_status_lock = threading.Lock()
_jellyfin_user_id_cache = {'value': None}
_jellyfin_user_id_lock = threading.Lock()
_background_executor = None
_background_job_lock = threading.Lock()
_cache_warmup_future = None
_poster_warmup_future = None
_startup_warmup_started = False
_startup_warmup_lock = threading.Lock()
_background_worker_count = BACKGROUND_WORKER_COUNT_DEFAULT
_poster_cache_max_items = POSTER_CACHE_MAX_ITEMS_DEFAULT


def init_cache(worker_count=BACKGROUND_WORKER_COUNT_DEFAULT, max_poster_items=POSTER_CACHE_MAX_ITEMS_DEFAULT):
    """Initialize cache pools and settings."""
    global _background_executor, _background_worker_count, _poster_cache_max_items
    _background_worker_count = worker_count
    _poster_cache_max_items = max_poster_items
    _background_executor = ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix='themarr-bg',
    )


def invalidate_library_cache():
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


def get_section_build_lock(section_id):
    """Return a per-section lock to avoid duplicate cache builds under load."""
    section_id = str(section_id)
    with _section_build_locks_lock:
        section_lock = _section_build_locks.get(section_id)
        if section_lock is None:
            section_lock = threading.Lock()
            _section_build_locks[section_id] = section_lock
    return section_lock


def set_theme_hydration_total(sections_total):
    with _theme_hydration_status_lock:
        _theme_hydration_status.update({
            'running': True,
            'ready': False,
            'sections_total': sections_total,
            'sections_completed': 0,
        })


def advance_theme_hydration_progress():
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


def mark_theme_hydration_finished():
    with _theme_hydration_status_lock:
        _theme_hydration_status['running'] = False
        _theme_hydration_status['ready'] = True
        _theme_hydration_status['sections_completed'] = _theme_hydration_status.get('sections_total', 0)


def get_theme_hydration_status():
    with _theme_hydration_status_lock:
        return dict(_theme_hydration_status)


def get_cached_item(rating_key, provider=None):
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


def get_cached_poster(rating_key, provider='plex'):
    """Return cached poster payload dict for *(provider, rating_key)*, or None."""
    cache_key = f'{provider}:{rating_key}'
    with _poster_cache_lock:
        cached = _poster_cache.get(cache_key)
        if cached is None:
            return None
        _poster_cache.move_to_end(cache_key)
        return cached


def set_cached_poster(rating_key, content, content_type, provider='plex'):
    """Store poster bytes in the in-memory poster cache."""
    cache_key = f'{provider}:{rating_key}'
    with _poster_cache_lock:
        _poster_cache[cache_key] = {
            'content': content,
            'content_type': content_type,
        }
        _poster_cache.move_to_end(cache_key)
        while len(_poster_cache) > _poster_cache_max_items:
            _poster_cache.popitem(last=False)


def fetch_poster_bytes(plex, thumb, timeout=10):
    """Fetch poster bytes for a Plex thumb path."""
    url = plex.url(thumb, includeToken=True)
    response = plex_session_get(plex, url, stream=True, timeout=timeout)
    response.raise_for_status()
    content_type = response.headers.get('content-type', 'image/jpeg')
    return response.content, content_type


def submit_background_job(name, fn, *args):
    """Submit work to shared background executor with graceful rejection logging."""
    global _background_executor
    if _background_executor is None:
        init_cache()
    try:
        return _background_executor.submit(fn, *args)
    except RuntimeError as exc:
        logger.warning('Failed to queue background job %s: %s', name, exc)
        return None


def get_jellyfin_user_id_cached():
    """Return cached Jellyfin user ID or None."""
    with _jellyfin_user_id_lock:
        return _jellyfin_user_id_cache.get('value')


def set_jellyfin_user_id_cached(user_id):
    """Cache Jellyfin user ID."""
    with _jellyfin_user_id_lock:
        _jellyfin_user_id_cache['value'] = user_id


def sync_cached_item(item):
    """Update an item's cached entry in-place after local theme state changes."""
    updated = False
    updated_item = None
    with _library_cache_lock:
        for section_items in _library_cache.values():
            for index, cached_item in enumerate(section_items):
                if str(cached_item.get('ratingKey')) == str(item.ratingKey):
                    existing_library_id = cached_item.get('library_id')
                    existing_provider = cached_item.get('provider') or 'plex'
                    updated_item = item_to_dict(
                        item,
                        provider=existing_provider,
                        library_id=existing_library_id,
                    )
                    section_items[index] = updated_item
                    updated = True
                    break
            if updated:
                break
    if updated_item is None:
        updated_item = item_to_dict(item)
    return updated_item, updated


def sync_cached_item_theme_state(provider, item_id):
    """Refresh has_local_theme/theme_size for a cached item by provider/id."""
    from app.plex_utils import get_plex
    
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
                updated['plex_theme_source_unverified'] = False
                if provider == 'plex':
                    try:
                        plex = get_plex()
                        item = plex.fetchItem(int(target_id))
                        updated['has_plex_theme'] = bool(getattr(item, 'theme', None))
                        updated['plex_theme_source_unverified'] = is_plex_theme_source_unverified(
                            item,
                            updated['has_local_theme'],
                        )
                    except Exception as exc:
                        logger.warning('Unable to refresh Plex source availability for item %s: %s', target_id, exc)
                section_items[idx] = updated
                _library_cache[section_key] = section_items
                return updated, True
    return None, False


def get_library_cache_for_section(section_id):
    """Retrieve cached library items for a section, or None if not cached."""
    with _library_cache_lock:
        return _library_cache.get(section_id)


def set_library_cache_for_section(section_id, items):
    """Cache library items for a section."""
    with _library_cache_lock:
        _library_cache[section_id] = items


def background_warm_poster_cache(plex, item_to_dict_fn):
    """Background thread: pre-load poster images for already-cached library items."""
    logger.info('Poster cache warmup starting…')
    
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
        if get_cached_poster(rating_key) is not None:
            return True
        try:
            content, content_type = fetch_poster_bytes(plex, item['thumb'], timeout=10)
            set_cached_poster(rating_key, content, content_type)
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
    if external_ids['imdb'] or external_ids['tvdb'] or external_ids['tmdb']:
        themerrdb_data = get_themerrdb_theme(item)
        has_themerrdb_theme = themerrdb_data is not None

    has_plex_theme = bool(getattr(item, 'theme', None))
    plex_theme_source_unverified = is_plex_theme_source_unverified(item, theme_exists)

    return {
        'id': str(item.ratingKey),
        'ratingKey': item.ratingKey,
        'provider': provider,
        'library_id': str(library_id) if library_id is not None else None,
        'title': item.title,
        'year': getattr(item, 'year', None),
        'thumb': item.thumb,
        'type': item.type,
        'has_plex_theme': has_plex_theme,
        'plex_theme_source_unverified': plex_theme_source_unverified,
        'has_local_theme': theme_exists,
        'has_themerrdb_theme': has_themerrdb_theme,
        'theme_size': theme_size,
        'local_path': str(local_path) if local_path else None,
        'external_ids': external_ids,
    }


def build_library_items(section_id, include_theme_state=True, provider='plex'):
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
            serialize_jellyfin_item(item, section_id, theme_dirs=theme_dirs, get_themerrdb_theme_fn=get_themerrdb_theme_for_item)
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


# ============================================================
# Cache warmup
# ============================================================

def kick_off_cache_warmup():
    """Invalidate and rebuild the library cache in the background.

    Safe to call at any time — if a rebuild is already running it returns
    ``False`` without queuing a second one.
    """
    global _cache_warmup_future
    with _startup_warmup_lock:
        if _cache_warmup_future is not None and not _cache_warmup_future.done():
            return False
        invalidate_library_cache()
        _cache_warmup_future = submit_background_job('library-cache-rebuild', _warm_library_cache)
        if _cache_warmup_future is None:
            logger.warning('Failed to queue background job library-cache-rebuild')
        return _cache_warmup_future is not None


def _warm_library_cache():
    """Background task: pre-load library metadata and theme state, then warm poster cache."""
    logger.info('Library cache warmup starting…')
    sections = []

    if plex_is_configured():
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
            for section in get_jellyfin_libraries():
                section_id = str(section['id'])
                sections.append({
                    'provider': 'jellyfin',
                    'cache_key': f'jellyfin:{section_id}',
                    'section_id': section_id,
                    'title': section.get('title') or section_id,
                })
        except Exception as exc:
            logger.warning('Library cache warmup: could not connect to Jellyfin: %s', exc)

    set_theme_hydration_total(len(sections))
    if not sections:
        mark_theme_hydration_finished()
        return

    def warm_section(section_info):
        provider = section_info['provider']
        cache_key = section_info['cache_key']
        section_id = section_info['section_id']
        section_title = section_info['title']
        section_lock = get_section_build_lock(cache_key)
        with section_lock:
            if get_library_cache_for_section(cache_key) is not None:
                advance_theme_hydration_progress()
                return
            try:
                items = build_library_items(section_id, include_theme_state=True, provider=provider)
                set_library_cache_for_section(cache_key, items)
                logger.info(
                    'Library cache warmup complete: %s section %s (%s) — %d items',
                    provider, section_id, section_title, len(items),
                )
            except Exception as exc:
                logger.warning(
                    'Library cache warmup failed for %s section %s: %s',
                    provider, section_id, exc,
                )
            finally:
                advance_theme_hydration_progress()

    max_workers = min(4, max(1, len(sections)))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='library-cache-warm') as executor:
        futures = [executor.submit(warm_section, s) for s in sections]
        for future in as_completed(futures):
            future.result()

    logger.info('Library cache warmup complete.')

    if plex_is_configured():
        try:
            plex = get_plex()
            poster_future = submit_background_job(
                'poster-cache-warmup',
                background_warm_poster_cache,
                plex,
                item_to_dict,
            )
            if poster_future is None:
                logger.warning('Failed to queue background job poster-cache-warmup')
        except Exception as exc:
            logger.warning('Could not kick off poster cache warmup: %s', exc)


