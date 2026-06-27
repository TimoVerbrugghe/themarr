#!/usr/bin/env python3
"""Themarr Web Application - Flask-based Web UI for managing Plex theme music."""

import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import requests as http_requests
import yt_dlp
from flask import Flask, Response, jsonify, render_template, request, send_file
from plexapi.server import PlexServer

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
YTDLP_WORKDIR = Path(app.root_path) / '.yt_dlp_work'
YTDLP_WORKDIR.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_YOUTUBE_DURATION_SECONDS = 15 * 60
MAX_BULK_ITEMS = 100
PLEX_RETRY_ATTEMPTS_DEFAULT = 10
PLEX_RETRY_DELAY_DEFAULT = 30  # seconds between retry attempts
ALLOWED_UPLOAD_TYPES = {'audio/mpeg', 'audio/mp3', 'application/octet-stream'}
ALLOWED_YOUTUBE_HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be'}


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


def resolve_existing_media_path(path_value, item_type):
    """Return an existing Plex path when container mounts already match Plex paths."""
    candidate = Path(path_value)
    if item_type == 'show' and candidate.exists():
        return candidate
    if item_type == 'movie' and candidate.parent != candidate and candidate.parent.exists():
        return candidate.parent
    return None


def get_item_local_path(item):
    """Get the local filesystem path for a Plex library item."""
    if not hasattr(item, 'locations') or not item.locations:
        return None

    plex_path = item.locations[0]
    existing_path = resolve_existing_media_path(plex_path, item.type)
    if existing_path is not None:
        return existing_path

    if item.type == 'show':
        tv_path = os.getenv('TV_PATH') or os.getenv('TV_SHOWS_PATH', '/tv')
        return Path(tv_path) / Path(plex_path).name
    if item.type == 'movie':
        movies_path = os.getenv('MOVIES_PATH', '/movies')
        parent = Path(plex_path).parent
        folder_name = parent.name if parent != Path(plex_path) else Path(plex_path).stem
        return Path(movies_path) / folder_name

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


def item_to_dict(item):
    """Serialize a Plex item to a dict for JSON response."""
    local_path = get_item_local_path(item)
    theme_exists = False
    theme_size = 0
    if local_path:
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


@app.route('/')
def index():
    """Serve the main Web UI."""
    return render_template('index.html')


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
                    'title': section.title,
                    'type': section.type,
                    'thumb': section.thumb,
                    'totalSize': section.totalSize,
                })
        return jsonify(result)
    except Exception as exc:
        return error_response('Failed to get libraries', exc=exc)


@app.route('/api/libraries/<int:section_id>/items')
def get_library_items(section_id):
    """Return all items in a library section."""
    try:
        plex = get_plex()
        section = plex.library.sectionByID(section_id)
        items = section.all()
        result = [item_to_dict(item) for item in items]
        result.sort(key=lambda item: item['title'].lower())
        return jsonify(result)
    except Exception as exc:
        return error_response(f'Failed to get items for section {section_id}', exc=exc)


@app.route('/api/poster/<int:rating_key>')
def get_poster(rating_key):
    """Proxy Plex poster image to avoid CORS/token issues in browser."""
    try:
        plex = get_plex()
        item = plex.fetchItem(rating_key)
        if not item.thumb:
            return jsonify({'error': 'No poster available'}), 404
        url = plex.url(item.thumb, includeToken=True)
        response = plex_session_get(plex, url, stream=True, timeout=10)
        response.raise_for_status()
        content_type = response.headers.get('content-type', 'image/jpeg')
        return Response(response.content, mimetype=content_type)
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
        return jsonify({'success': True, 'path': str(theme_path)})
    except Exception as exc:
        return error_response(f'Failed to download theme from Plex for {rating_key}', exc=exc)


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
        return jsonify({'success': True, 'path': str(theme_path)})
    except Exception as exc:
        return error_response(f'Failed to upload theme for {rating_key}', exc=exc)


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
        return jsonify({'success': True, 'path': str(theme_path)})
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
        return jsonify({'success': True})
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


if __name__ == '__main__':
    port = int(os.getenv('PORT', '8080'))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
