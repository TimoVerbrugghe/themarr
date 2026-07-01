#!/usr/bin/env python3
"""Themarr Web Application - Flask-based Web UI for managing Plex theme music."""

import json
import logging
import os
import re
import secrets
import shutil
import tempfile
import time
import hmac
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlparse

from functools import partial
import requests as http_requests
import yt_dlp
from flask import Flask, Response, jsonify, render_template, request, send_file, session

from app.media_utils import (
    MAX_UPLOAD_BYTES, ALLOWED_UPLOAD_TYPES, VIDEO_FILE_EXTENSIONS,
    _is_video_file_path, _is_valid_mp3_magic,
    _validate_local_media_path, _theme_file_path, scan_local_theme_dirs,
)
from app.external_ids import (
    _normalize_external_id, extract_external_ids, extract_jellyfin_external_ids,
)
from app.youtube_utils import (
    ALLOWED_YOUTUBE_HOSTS, MAX_YOUTUBE_DURATION_SECONDS,
    is_valid_youtube_url, youtube_match_filter, _youtube_retry_profiles,
    _youtube_preview_ydl_opts, _youtube_download_ydl_opts,
    _clean_yt_dlp_error, _stream_http_response_chunks,
    extract_youtube_audio_url, is_valid_audio_stream_url, download_youtube_theme_mp3,
    normalize_youtube_trim_window,
)
from app.plex_utils import (
    get_plex, plex_is_configured, plex_session_get, get_section_base_paths,
    get_item_local_path, get_validated_plex_local_path, download_plex_theme_to_path,
    refresh_plex_item_metadata, find_plex_item_by_path,
)
from app.jellyfin_utils import (
    jellyfin_is_configured, get_jellyfin, get_jellyfin_item_local_path, _normalize_provider,
    JELLYFIN_TIMEOUT_SECONDS, jellyfin_session_get, jellyfin_session_post,
    get_jellyfin_user_id, get_jellyfin_library_count, get_jellyfin_libraries,
    get_jellyfin_item, serialize_jellyfin_item, reset_jellyfin_user_id_cache,
    refresh_jellyfin_item_metadata, find_jellyfin_item_id_by_path,
)
from app.auth import (
    _log_generated_api_key_warning, _auth_disabled, _get_ui_credentials,
    _credentials_auth_configured, _ui_auth_misconfigured, _ui_auth_warning_message,
    _get_auth_mode, _parse_api_key, _get_api_key, _check_api_request_auth,
    _check_webhook_basic_auth, _get_settings_env_values,
)
from app.cache import (
    init_cache, get_jellyfin_user_id_cached, set_jellyfin_user_id_cached,
    get_library_cache_for_section, set_library_cache_for_section,
    invalidate_library_cache, get_section_build_lock, set_theme_hydration_total,
    advance_theme_hydration_progress, mark_theme_hydration_finished, get_theme_hydration_status,
    get_cached_item, get_cached_poster, set_cached_poster, fetch_poster_bytes,
    submit_background_job, sync_cached_item, sync_cached_item_theme_state,
    item_to_dict, build_library_items, background_warm_poster_cache,
    kick_off_cache_warmup,
    _library_cache_lock, _section_build_locks_lock, _theme_hydration_status_lock,
    _theme_hydration_status, _library_cache,
)
from app.notifications import send_pushover_notification, TRIGGER_UI
from app.errors import error_response
from app.themerrdb_service import (
    get_themerrdb_theme_for_external_ids, get_themerrdb_theme_for_item, query_themerrdb,
    get_themerrdb_theme, get_themerrdb_data_for_context,
)
from app.theme_state import (
    has_nonempty_theme_file, is_plex_theme_source_unverified,
    get_external_ids_for_context, check_themerrdb_availability_for_context,
    check_plex_preview_availability,
)
from app.webhook_handlers import (
    check_webhook_server_uuid, process_plex_library_new, process_jellyfin_item_added,
    _is_jellyfin_item_added_event, _jellyfin_webhook_event_name, _extract_jellyfin_item_id,
)
from app.bulk_operations import bulk_download_themes

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder='../templates', static_folder='../static')
YTDLP_WORKDIR = Path(tempfile.gettempdir()) / 'themarr_yt_dlp_work'
YTDLP_WORKDIR.mkdir(parents=True, exist_ok=True)
MAX_BULK_ITEMS = 100
ASSET_VERSION = str(int(time.time()))
PLEX_RETRY_ATTEMPTS_DEFAULT = 10
PLEX_RETRY_DELAY_DEFAULT = 30  # seconds between retry attempts
LIBRARY_PAGE_SIZE_DEFAULT = 200
LIBRARY_PAGE_SIZE_MAX = 500
POSTER_CACHE_MAX_ITEMS_DEFAULT = 500
BACKGROUND_WORKER_COUNT_DEFAULT = 4
POSTER_CACHE_MAX_ITEMS = POSTER_CACHE_MAX_ITEMS_DEFAULT
BACKGROUND_WORKER_COUNT = BACKGROUND_WORKER_COUNT_DEFAULT
LIBRARY_PAGE_SIZE = LIBRARY_PAGE_SIZE_DEFAULT
LIBRARY_PAGE_SIZE_MAX_VALUE = LIBRARY_PAGE_SIZE_MAX
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_BYTES
# Sessions are intentionally ephemeral — a fresh random key is generated on every
# container start. Sessions will not survive restarts; do not make this configurable.
app.config['SECRET_KEY'] = secrets.token_hex(32)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = (os.getenv('FLASK_DEBUG', 'false').lower() not in {'true', '1', 'yes'})

_generated_api_key = secrets.token_urlsafe(32)
_SETTINGS_ENV_VARS = (
    'PLEX_URL',
    'PLEX_TOKEN',
    'JELLYFIN_URL',
    'JELLYFIN_API_KEY',
    'JELLYFIN_USER_ID',
    'DEFAULT_THEME',
    'DEFAULT_VIEW',
    'FLASK_DEBUG',
    'API_KEY',
    'AUTH_USERNAME',
    'AUTH_PASSWORD',
    'DISABLE_AUTH',
    'PUSHOVER_APP_TOKEN',
    'PUSHOVER_USER_KEY',
    'NOTIFY_ON_WEBHOOK_DOWNLOAD',
    'NOTIFY_ON_WEBHOOK_FAILURE',
    'NOTIFY_ON_UI_DOWNLOAD',
    'WEBHOOK_USERNAME',
    'WEBHOOK_PASSWORD',
    'PLEX_RETRY_ATTEMPTS',
    'PLEX_RETRY_DELAY',
)


# API_KEY initialization logging
if not (os.getenv('API_KEY') or '').strip():
    _log_generated_api_key_warning()


# Log the active auth mode at startup.
_startup_auth_mode = _get_auth_mode()
if _startup_auth_mode == 'disabled':
    logger.warning(
        'DISABLE_AUTH is set — all UI authentication is bypassed. '
        'Only use this when a trusted reverse proxy handles authentication.'
    )
elif _startup_auth_mode == 'credentials':
    logger.info('Auth mode: username/password credentials (AUTH_USERNAME + AUTH_PASSWORD).')
else:
    logger.warning('Auth mode: misconfigured. %s', _ui_auth_warning_message())


def _paginate_items(items):
    """Return paginated payload when requested, otherwise return full list for compatibility."""
    paginated = (request.args.get('paginated') or '').lower() in {'1', 'true', 'yes'}
    page_arg = request.args.get('page')
    page_size_arg = request.args.get('page_size')
    if not paginated and page_arg is None and page_size_arg is None:
        return items

    try:
        page = int(page_arg or 1)
    except ValueError:
        page = 1
    page = max(1, page)

    try:
        page_size = int(page_size_arg or LIBRARY_PAGE_SIZE)
    except ValueError:
        page_size = LIBRARY_PAGE_SIZE
    page_size = max(1, min(page_size, LIBRARY_PAGE_SIZE_MAX_VALUE))

    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = items[start:end]
    return {
        'items': page_items,
        'pagination': {
            'page': page,
            'page_size': page_size,
            'total': total,
            'has_more': end < total,
        },
    }


def _submit_background_job(name, fn, *args):
    """Submit work to shared background executor with graceful rejection logging."""
    future = submit_background_job(name, fn, *args)
    if future is None:
        logger.warning('Failed to queue background job %s', name)
    return future


def _download_youtube_theme_to_path(youtube_url, theme_path):
    """Download a YouTube theme and move it into its final theme.mp3 location."""
    with tempfile.TemporaryDirectory(dir=YTDLP_WORKDIR) as tmpdir:
        mp3_path = download_youtube_theme_mp3(youtube_url, tmpdir)
        theme_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(mp3_path), str(theme_path))


def _trigger_metadata_refresh(provider, item, item_id, local_path):
    """Trigger a metadata refresh on the provider that owns the theme, plus cross-provider.

    After a theme.mp3 is placed on disk the owning media server must be told to
    re-scan so it discovers the new file.  When both Plex and Jellyfin are
    configured they typically share the same library paths, so the other
    provider is refreshed too (best-effort).

    *item* may be a plexapi object (for Plex) or a Jellyfin item dict; pass
    None when the item object is unavailable and the refresh will be skipped for
    the primary Plex case (item_id is always used for Jellyfin).

    All errors are caught and logged as warnings — a refresh failure must never
    prevent the theme download response from being returned to the caller.
    """
    both_configured = plex_is_configured() and jellyfin_is_configured()

    if provider == 'plex':
        if item is not None:
            refresh_plex_item_metadata(item)
        if both_configured and local_path:
            try:
                jf_item_id = find_jellyfin_item_id_by_path(local_path)
                if jf_item_id:
                    refresh_jellyfin_item_metadata(jf_item_id)
            except Exception as exc:
                logger.warning('Cross-provider Jellyfin refresh failed: %s', exc)
    elif provider == 'jellyfin':
        refresh_jellyfin_item_metadata(item_id)
        if both_configured and local_path:
            try:
                plex_instance = get_plex()
                plex_item = find_plex_item_by_path(plex_instance, local_path)
                if plex_item:
                    refresh_plex_item_metadata(plex_item)
            except Exception as exc:
                logger.warning('Cross-provider Plex refresh failed: %s', exc)


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

    jellyfin, _, item = get_jellyfin_item(item_id)
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


def is_valid_upload(upload_file):
    """Validate uploaded theme file name, type, and size.

    Checks filename extension, Content-Type, and MP3 magic bytes to prevent
    non-audio files from being accepted via forged or missing MIME types.
    """
    if request.content_length and request.content_length > MAX_UPLOAD_BYTES:
        return False, ('Uploaded file is too large', 413)

    filename = (upload_file.filename or '').lower()
    if not filename.endswith('.mp3'):
        return False, ('Only MP3 uploads are supported', 400)

    content_type = (upload_file.content_type or '').lower().split(';')[0].strip()
    if not content_type or content_type not in ALLOWED_UPLOAD_TYPES:
        return False, ('Uploaded file must be an MP3 (audio/mpeg)', 400)

    if not _is_valid_mp3_magic(upload_file.stream):
        return False, ('File content does not appear to be a valid MP3', 400)

    return True, None


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
    ui_auth_misconfigured = _ui_auth_misconfigured()
    if ui_auth_misconfigured:
        logger.warning('UI auth misconfiguration: %s', _ui_auth_warning_message())
    return render_template(
        'index.html',
        default_theme=default_theme,
        default_view=default_view,
        asset_version=ASSET_VERSION,
        ui_auth_misconfigured=ui_auth_misconfigured,
        ui_auth_warning_message=_ui_auth_warning_message(),
    )


@app.route('/health')
def health():
    """Health check endpoint for container orchestration systems."""
    return jsonify({'status': 'healthy'}), 200


@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    """Validate credentials and establish a server-side session.

    Accepts username/password when AUTH_USERNAME + AUTH_PASSWORD are set.
    On success the browser receives an httpOnly session cookie; credentials are
    never stored client-side.
    """
    data = request.get_json(silent=True) or {}
    auth_mode = _get_auth_mode()

    if auth_mode == 'disabled':
        # Auth is disabled — establish a session anyway so downstream
        # session.get('authenticated') checks remain consistent.
        session.clear()
        session['authenticated'] = True
        return jsonify({'ok': True, 'auth_mode': 'disabled'})

    if auth_mode == 'credentials':
        provided_username = (data.get('username') or '').strip()
        provided_password = (data.get('password') or '').strip()
        expected_username, expected_password = _get_ui_credentials()
        if not provided_username or not provided_password:
            return jsonify({'error': 'Invalid username or password'}), 401
        if len(provided_username) > 256 or len(provided_password) > 4096:
            return jsonify({'error': 'Invalid username or password'}), 401
        username_ok = hmac.compare_digest(provided_username, expected_username)
        password_ok = hmac.compare_digest(provided_password, expected_password)
        if not (username_ok and password_ok):
            return jsonify({'error': 'Invalid username or password'}), 401
        session.clear()
        session['authenticated'] = True
        return jsonify({'ok': True, 'auth_mode': 'credentials'})

    return jsonify({'error': 'UI auth is misconfigured. Set AUTH_USERNAME and AUTH_PASSWORD.'}), 503


@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    """Clear the server-side session."""
    session.clear()
    return jsonify({'ok': True})


@app.route('/api/init')
def api_init():
    """Return auth state for the UI on initial page load.

    This endpoint is always public — no credentials required — so the frontend
    can decide whether to show the login screen before making any other requests.
    """
    auth_mode = _get_auth_mode()
    disable_auth = auth_mode == 'disabled'
    auth_misconfigured = auth_mode == 'misconfigured'
    authenticated = disable_auth or (auth_mode == 'credentials' and session.get('authenticated', False))
    return jsonify({
        'auth_required': not disable_auth and not auth_misconfigured,
        'authenticated': authenticated,
        'auth_mode': auth_mode,
        'auth_misconfigured': auth_misconfigured,
        'warning': _ui_auth_warning_message() if auth_misconfigured else None,
    })


@app.route('/api/settings/runtime')
def get_settings_runtime():
    """Return runtime settings for the UI settings page.

    This endpoint requires authentication (session cookie or API key header).
    The actual API key is returned so the settings page can display it; it is
    safe to do so because the caller is already authenticated.
    """
    actual_key, is_generated = _get_api_key(_generated_api_key)
    return jsonify({
        'api_key': actual_key,
        'api_key_configured': not is_generated,
        'api_key_generated': is_generated,
        'background_worker_count': BACKGROUND_WORKER_COUNT,
        'library_page_size': LIBRARY_PAGE_SIZE,
        'library_page_size_max': LIBRARY_PAGE_SIZE_MAX_VALUE,
        'poster_cache_max_items': POSTER_CACHE_MAX_ITEMS,
        'env_values': _get_settings_env_values(actual_key if is_generated else None),
    })


@app.after_request
def add_security_headers(response):
    """Add security-relevant HTTP response headers to every response."""
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy', 'geolocation=(), microphone=(), camera=()')
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data: https://cdn.jsdelivr.net https://app.lizardbyte.dev "
        "https://i.ytimg.com https://*.ytimg.com https://img.youtube.com; "
        "media-src 'self' blob: https://*.googlevideo.com; "
        "frame-src https://www.youtube-nocookie.com; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self';"
    )
    return response


@app.before_request
def enforce_api_auth():
    """Protect API endpoints with API key or session auth."""
    if not request.path.startswith('/api/'):
        return None
    # Always-public endpoints: login flow and initial auth-state probe.
    # /api/cache/status is also kept public so the startup overlay works
    # before the user has authenticated.
    if request.path in {'/api/auth/login', '/api/init', '/api/cache/status'}:
        return None
    if request.path in {'/api/webhooks/plex', '/api/webhooks/jellyfin'}:
        return None  # Webhook uses its own Basic Auth
    # Require auth for ALL API routes (both read and mutating)
    return _check_api_request_auth(_generated_api_key)


@app.route('/api/status')
def get_status():
    """Return connection status for Plex and Jellyfin."""
    plex_url_configured = plex_is_configured()
    jellyfin_url_configured = bool((os.getenv('JELLYFIN_URL') or '').strip())

    plex_status = {
        'url_configured': plex_url_configured,
        'connected': False,
        'server_name': None,
        'version': None,
        'error': None,
    }
    jellyfin_status = {
        'url_configured': jellyfin_url_configured,
        'connected': False,
        'server_name': None,
        'version': None,
        'error': None,
    }

    if plex_url_configured:
        try:
            plex = get_plex()
            plex_status.update({
                'connected': True,
                'server_name': plex.friendlyName,
                'version': plex.version,
            })
        except Exception as exc:
            logger.error('Plex connection failed: %s', exc)
            plex_status['error'] = 'Unable to connect to Plex'

    if jellyfin_url_configured:
        try:
            jellyfin = get_jellyfin()
            payload = {}
            status_probe_succeeded = False

            for status_path in ('/System/Info', '/System/Info/Public'):
                try:
                    response = jellyfin_session_get(jellyfin, status_path, timeout=JELLYFIN_TIMEOUT_SECONDS)
                    response.raise_for_status()
                    if response.content:
                        try:
                            payload = response.json()
                        except ValueError:
                            payload = {}
                    else:
                        payload = {}
                    status_probe_succeeded = True
                    break
                except http_requests.RequestException:
                    continue

            # Fall back to the same libraries endpoint path used by normal library
            # loading so connectivity reflects real-world usable access.
            if not status_probe_succeeded:
                libraries_response = jellyfin_session_get(jellyfin, '/Library/VirtualFolders', timeout=JELLYFIN_TIMEOUT_SECONDS)
                libraries_response.raise_for_status()

            jellyfin_status.update({
                'connected': True,
                'server_name': payload.get('ServerName') or payload.get('Name'),
                'version': payload.get('Version'),
            })
        except Exception as exc:
            logger.error('Jellyfin connection failed: %s', exc)
            jellyfin_status['error'] = 'Unable to connect to Jellyfin'

    # Keep these top-level fields for backwards compatibility with existing clients.
    # `connected` is True when either Plex or Jellyfin is reachable.
    response_payload = {
        'connected': plex_status['connected'] or jellyfin_status['connected'],
        'server_name': plex_status['server_name'],
        'version': plex_status['version'],
        'plex': plex_status,
        'jellyfin': jellyfin_status,
    }
    if plex_status['error']:
        response_payload['error'] = plex_status['error']
    return jsonify(response_payload)


@app.route('/api/libraries')
def get_libraries():
    """Return list of TV and Movie libraries from Plex and Jellyfin."""
    result = []
    plex_error = None
    jellyfin_error = None

    if plex_is_configured():
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
            result.extend(get_jellyfin_libraries())
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
    status = get_theme_hydration_status()
    return jsonify(status)


@app.route('/api/libraries/<int:section_id>/items')
def get_library_items(section_id):
    """Return all items in a library section, served from cache when available."""
    cached = get_library_cache_for_section(section_id)
    if cached is not None:
        return jsonify(_paginate_items(cached))

    section_lock = get_section_build_lock(section_id)
    with section_lock:
        cached = get_library_cache_for_section(section_id)
        if cached is not None:
            return jsonify(_paginate_items(cached))
        try:
            result = build_library_items(section_id, provider='plex')
            set_library_cache_for_section(section_id, result)
            return jsonify(_paginate_items(result))
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
    cached = get_library_cache_for_section(cache_key)
    if cached is not None:
        return jsonify(_paginate_items(cached))

    section_lock = get_section_build_lock(cache_key)
    with section_lock:
        cached = get_library_cache_for_section(cache_key)
        if cached is not None:
            return jsonify(_paginate_items(cached))
        try:
            result = build_library_items(section_id, provider='jellyfin')
            set_library_cache_for_section(cache_key, result)
            return jsonify(_paginate_items(result))
        except Exception as exc:
            return error_response(f'Failed to get items for {provider} section {section_id}', exc=exc)


@app.route('/api/poster/<int:rating_key>')
def get_poster(rating_key):
    """Proxy Plex poster image to avoid CORS/token issues in browser."""
    try:
        cached_poster = get_cached_poster(rating_key, provider='plex')
        if cached_poster is not None:
            return Response(cached_poster['content'], mimetype=cached_poster['content_type'])

        plex = get_plex()
        cached_item = get_cached_item(rating_key, provider='plex')
        thumb = cached_item.get('thumb') if cached_item else None
        if not thumb:
            item = plex.fetchItem(rating_key)
            thumb = item.thumb

        if not thumb:
            return jsonify({'error': 'No poster available'}), 404

        content, content_type = fetch_poster_bytes(plex, thumb, timeout=10)
        set_cached_poster(rating_key, content, content_type, provider='plex')
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
        cached_poster = get_cached_poster(item_id, provider='jellyfin')
        if cached_poster is not None:
            return Response(cached_poster['content'], mimetype=cached_poster['content_type'])

        jellyfin = get_jellyfin()
        response = jellyfin_session_get(jellyfin, f'/Items/{item_id}/Images/Primary')
        response.raise_for_status()
        content_type = response.headers.get('content-type', 'image/jpeg')
        set_cached_poster(item_id, response.content, content_type, provider='jellyfin')
        return Response(response.content, mimetype=content_type)
    except Exception as exc:
        return error_response(f'Failed to get poster for {provider} item {item_id}', exc=exc)


@app.route('/api/items/<int:rating_key>/theme', methods=['GET'])
def get_theme(rating_key):
    """Stream the local theme.mp3 file for playback in browser."""
    try:
        plex = get_plex()
        item = plex.fetchItem(rating_key)
        local_path = get_validated_plex_local_path(item)
        if not local_path:
            return jsonify({'error': 'Cannot determine local path for item'}), 404
        theme_path = _theme_file_path(local_path)
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

        local_path = get_validated_plex_local_path(item)
        if not local_path:
            return jsonify({'error': 'Cannot determine local path for item'}), 404

        theme_path = _theme_file_path(local_path)
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
        send_pushover_notification('Theme Downloaded', f'{item.title} theme downloaded from Plex', trigger=TRIGGER_UI)
        _trigger_metadata_refresh('plex', item, str(rating_key), local_path)
        item_dict, _ = sync_cached_item(item)
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

        target_local_path = get_validated_plex_local_path(target_item)
        if not target_local_path:
            return jsonify({'error': 'Cannot determine local path for target item'}), 404

        source_local_path = get_validated_plex_local_path(source_item)
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

        logger.info(
            'Copied theme from %s (%s) to %s (%s)',
            source_item.title, source_theme_path, target_item.title, target_theme_path,
        )
        send_pushover_notification('Theme Copied', f'{target_item.title} theme copied from {source_item.title}', trigger=TRIGGER_UI)
        _trigger_metadata_refresh('plex', target_item, str(rating_key), target_local_path)
        item_dict, _ = sync_cached_item(target_item)
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

        local_path = get_validated_plex_local_path(item)
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
        tmp_path = theme_path.with_suffix('.mp3.tmp')
        try:
            upload_file.save(str(tmp_path))
            if tmp_path.stat().st_size > MAX_UPLOAD_BYTES:
                return jsonify({'error': 'Uploaded file is too large'}), 413
            tmp_path.rename(theme_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

        logger.info('Uploaded theme for %s to %s', item.title, theme_path)
        send_pushover_notification('Theme Uploaded', f'{item.title} theme uploaded', trigger=TRIGGER_UI)
        _trigger_metadata_refresh('plex', item, str(rating_key), local_path)
        item_dict, _ = sync_cached_item(item)
        return jsonify({'success': True, 'path': str(theme_path), 'item': item_dict})
    except Exception as exc:
        return error_response(f'Failed to upload theme for {rating_key}', exc=exc)


@app.route('/api/youtube/search', methods=['GET'])
def youtube_search():
    """Search YouTube and return up to *limit* results for a query string."""
    query = (request.args.get('q') or '').strip()
    if not query:
        return jsonify({'error': 'Search query is required'}), 400
    if len(query) > 200:
        return jsonify({'error': 'Search query is too long'}), 400

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
            'noplaylist': True,
            'socket_timeout': 30,
            # DO NOT add remote_components or js_runtimes — see app/youtube_utils.py
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
        try:
            start_seconds, end_seconds = normalize_youtube_trim_window(
                data.get('start_time'),
                data.get('end_time'),
            )
        except ValueError as exc:
            logger.warning('Invalid YouTube trim window for %s: %s', rating_key, exc)
            return jsonify({'error': 'Invalid trim window values'}), 400

        plex = get_plex()
        item = plex.fetchItem(rating_key)

        local_path = get_validated_plex_local_path(item)
        if not local_path:
            return jsonify({'error': 'Cannot determine local path for item'}), 404

        theme_path = _theme_file_path(local_path)

        if theme_path.exists() and theme_path.stat().st_size > 0 and not overwrite:
            return jsonify({'error': 'Theme already exists', 'exists': True}), 409

        with tempfile.TemporaryDirectory(dir=YTDLP_WORKDIR) as tmpdir:
            mp3_path = download_youtube_theme_mp3(
                youtube_url,
                tmpdir,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
            )
            local_path.mkdir(parents=True, exist_ok=True)
            shutil.move(str(mp3_path), str(theme_path))

        logger.info('Downloaded YouTube theme for %s to %s', item.title, theme_path)
        send_pushover_notification('Theme Downloaded', f'{item.title} theme downloaded from YouTube', trigger=TRIGGER_UI)
        _trigger_metadata_refresh('plex', item, str(rating_key), local_path)
        item_dict, _ = sync_cached_item(item)
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
        return jsonify(check_themerrdb_availability_for_context(context, validate_preview=True))
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

        if not is_valid_youtube_url(youtube_url):
            logger.warning('ThemerrDB returned an invalid YouTube URL for item %s', rating_key)
            return jsonify({'error': 'ThemerrDB returned an invalid theme URL'}), 502

        audio_url = extract_youtube_audio_url(youtube_url)
        if not is_valid_audio_stream_url(audio_url):
            logger.warning('yt-dlp returned an unexpected stream host for item %s', rating_key)
            return jsonify({'error': 'Resolved audio stream URL is not from an allowed host'}), 502
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
        
        local_path = get_validated_plex_local_path(item)
        if not local_path:
            return jsonify({'error': 'Cannot determine local path for item'}), 404
        
        theme_path = _theme_file_path(local_path)
        
        if theme_path.exists() and theme_path.stat().st_size > 0 and not overwrite:
            return jsonify({'error': 'Theme already exists', 'exists': True}), 409
        
        themerrdb_data = get_themerrdb_theme(item)
        if not themerrdb_data or not themerrdb_data.get('youtube_theme_url'):
            return jsonify({'error': 'No theme available in ThemerrDB'}), 404
        
        youtube_url = themerrdb_data['youtube_theme_url']

        if not is_valid_youtube_url(youtube_url):
            logger.warning('ThemerrDB returned an invalid YouTube URL for item %s', rating_key)
            return jsonify({'error': 'ThemerrDB returned an invalid theme URL'}), 502

        with tempfile.TemporaryDirectory(dir=YTDLP_WORKDIR) as tmpdir:
            mp3_path = download_youtube_theme_mp3(youtube_url, tmpdir)
            local_path.mkdir(parents=True, exist_ok=True)
            shutil.move(str(mp3_path), str(theme_path))
        
        logger.info('Downloaded ThemerrDB theme for %s to %s', item.title, theme_path)
        send_pushover_notification('Theme Downloaded', f'{item.title} theme downloaded from ThemerrDB', trigger=TRIGGER_UI)
        _trigger_metadata_refresh('plex', item, str(rating_key), local_path)
        item_dict, _ = sync_cached_item(item)
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
        local_path = get_validated_plex_local_path(item)
        if not local_path:
            return jsonify({'error': 'Cannot determine local path for item'}), 404
        theme_path = _theme_file_path(local_path)
        if not theme_path.exists():
            return jsonify({'error': 'No theme file to delete'}), 404
        theme_path.unlink()
        logger.info('Deleted theme for %s', item.title)
        item_dict, _ = sync_cached_item(item)
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
        return jsonify(check_plex_preview_availability(context['item']))
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
        return jsonify(check_themerrdb_availability_for_context(context, validate_preview=True))
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

        if not is_valid_youtube_url(youtube_url):
            logger.warning('ThemerrDB returned an invalid YouTube URL for %s item %s', provider, item_id)
            return jsonify({'error': 'ThemerrDB returned an invalid theme URL'}), 502

        audio_url = extract_youtube_audio_url(youtube_url)
        if not is_valid_audio_stream_url(audio_url):
            logger.warning('yt-dlp returned an unexpected stream host for %s item %s', provider, item_id)
            return jsonify({'error': 'Resolved audio stream URL is not from an allowed host'}), 502
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

        if not is_valid_youtube_url(youtube_url):
            logger.warning('ThemerrDB returned an invalid YouTube URL for %s item %s', provider, item_id)
            return jsonify({'error': 'ThemerrDB returned an invalid theme URL'}), 502

        with tempfile.TemporaryDirectory(dir=YTDLP_WORKDIR) as tmpdir:
            mp3_path = download_youtube_theme_mp3(youtube_url, tmpdir)
            local_path.mkdir(parents=True, exist_ok=True)
            shutil.move(str(mp3_path), str(theme_path))

        logger.info('Downloaded ThemerrDB theme for %s item', provider)
        send_pushover_notification('Theme Downloaded', f"{context['title']} theme downloaded from ThemerrDB", trigger=TRIGGER_UI)
        _trigger_metadata_refresh(provider, context.get('item'), item_id, local_path)
        item_dict, _ = sync_cached_item_theme_state(provider, item_id)
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
            trigger=TRIGGER_UI,
        )
        _trigger_metadata_refresh(provider, target_context.get('item'), item_id, target_local_path)
        item_dict, _ = sync_cached_item_theme_state(provider, item_id)
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
        tmp_path = theme_path.with_suffix('.mp3.tmp')
        try:
            upload_file.save(str(tmp_path))
            if tmp_path.stat().st_size > MAX_UPLOAD_BYTES:
                return jsonify({'error': 'Uploaded file is too large'}), 413
            tmp_path.rename(theme_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

        logger.info('Uploaded theme for %s item', provider)
        send_pushover_notification('Theme Uploaded', f"{context['title']} theme uploaded", trigger=TRIGGER_UI)
        _trigger_metadata_refresh(provider, context.get('item'), item_id, local_path)
        item_dict, _ = sync_cached_item_theme_state(provider, item_id)
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
        try:
            start_seconds, end_seconds = normalize_youtube_trim_window(
                data.get('start_time'),
                data.get('end_time'),
            )
        except ValueError as exc:
            logger.warning(
                'Invalid YouTube trim parameters for %s:%s: %s',
                provider,
                item_id,
                exc,
            )
            return jsonify({'error': 'Invalid trim parameters'}), 400
        context = _get_item_context(provider, item_id)

        local_path = _validate_local_media_path(context['local_path'])
        if not local_path:
            return jsonify({'error': 'Cannot determine local path for item'}), 404

        theme_path = _theme_file_path(local_path)
        if theme_path.exists() and theme_path.stat().st_size > 0 and not overwrite:
            return jsonify({'error': 'Theme already exists', 'exists': True}), 409

        with tempfile.TemporaryDirectory(dir=YTDLP_WORKDIR) as tmpdir:
            mp3_path = download_youtube_theme_mp3(
                youtube_url,
                tmpdir,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
            )
            local_path.mkdir(parents=True, exist_ok=True)
            shutil.move(str(mp3_path), str(theme_path))

        logger.info('Downloaded YouTube theme for %s item', provider)
        send_pushover_notification('Theme Downloaded', f"{context['title']} theme downloaded from YouTube", trigger=TRIGGER_UI)
        _trigger_metadata_refresh(provider, context.get('item'), item_id, local_path)
        item_dict, _ = sync_cached_item_theme_state(provider, item_id)
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
        item_dict, _ = sync_cached_item_theme_state(provider, item_id)
        return jsonify({'success': True, 'item': item_dict})
    except ValueError as exc:
        return error_response('Invalid provider or item identifier', status_code=400, exc=exc)
    except Exception as exc:
        return error_response(f'Failed to delete theme for {provider} item {item_id}', exc=exc)


# ============================================================
# Bulk operations
# ============================================================

@app.route('/api/bulk/theme/download', methods=['POST'])
def bulk_download_themes_route():
    """Route handler for bulk theme download."""
    return bulk_download_themes()


# ============================================================
# Webhook Helpers — Plex
# ============================================================


# ============================================================
# Webhooks — Plex
# ============================================================

@app.route('/api/webhooks/plex', methods=['POST'])
def plex_webhook():
    """Handle Plex webhook events (library.new).
    
    Plex sends webhooks as form-encoded data with a 'payload' field containing JSON.
    For library.new events, we extract the ratingKey and process the theme download.
    """
    basic_auth_error = _check_webhook_basic_auth()
    if basic_auth_error:
        return basic_auth_error

    payload_str = request.form.get('payload', '')
    if not payload_str:
        logger.warning('Plex webhook: missing payload')
        return jsonify({'success': True}), 200
    
    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError:
        logger.warning('Plex webhook: invalid JSON payload')
        return jsonify({'success': True}), 200

    server_validation_error = check_webhook_server_uuid(payload)
    if server_validation_error:
        return server_validation_error
    
    event = payload.get('event', '')
    logger.info('Plex webhook: event=%s', event)
    
    if event == 'library.new':
        metadata = payload.get('Metadata', {})
        if not metadata:
            metadata = payload.get('metadata', {})
        
        rating_key = metadata.get('ratingKey')
        if rating_key:
            fn = partial(process_plex_library_new, download_plex_theme_fn=download_plex_theme_to_path)
            future = _submit_background_job(f'webhook-plex-{rating_key}', fn, rating_key)
            if future is None:
                logger.warning('Plex webhook: failed to queue processing for ratingKey=%s', rating_key)
            else:
                logger.info('Plex webhook: queued theme processing for ratingKey=%s', rating_key)
        else:
            logger.warning('Plex webhook: library.new event without ratingKey')
    
    return jsonify({'success': True}), 200


# ============================================================
# Webhooks — Jellyfin
# ============================================================

@app.route('/api/webhooks/jellyfin', methods=['POST'])
def jellyfin_webhook():
    """Handle Jellyfin webhook events (ItemAdded/library.new)."""
    basic_auth_error = _check_webhook_basic_auth()
    if basic_auth_error:
        return basic_auth_error

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload_str = request.form.get('payload', '')
        if payload_str:
            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                payload = None

    if not isinstance(payload, dict):
        logger.warning('Jellyfin webhook: missing or invalid payload')
        return jsonify({'success': True}), 200

    event = _jellyfin_webhook_event_name(payload)
    logger.info('Jellyfin webhook: event=%s', event)
    if not _is_jellyfin_item_added_event(payload):
        return jsonify({'success': True}), 200

    item_id = _extract_jellyfin_item_id(payload) or 'unknown'
    fn = partial(
        process_jellyfin_item_added,
        get_item_context_fn=_get_item_context,
        download_youtube_theme_fn=_download_youtube_theme_to_path,
    )
    future = _submit_background_job(f'webhook-jellyfin-{item_id}', fn, payload)
    if future is None:
        logger.warning('Jellyfin webhook: failed to queue processing for itemId=%s', item_id)
    else:
        logger.info('Jellyfin webhook: queued processing for itemId=%s', item_id)

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
        kick_off_cache_warmup()
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
    started = kick_off_cache_warmup()
    if started:
        return jsonify({'success': True, 'message': 'Cache refresh started in background'})
    return jsonify({'success': True, 'message': 'Cache refresh already in progress'})


if __name__ == '__main__':
    kick_off_cache_warmup()
    port = int(os.getenv('PORT', '8080'))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
else:
    # Running under gunicorn — warm the cache as soon as the worker starts.
    kick_off_cache_warmup()
