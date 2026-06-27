#!/usr/bin/env python3
"""Themarr Web Application - Flask-based Web UI for managing Plex theme music."""

import logging
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

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


if __name__ == '__main__':
    port = int(os.getenv('PORT', '8080'))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
