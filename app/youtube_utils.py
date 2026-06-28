"""YouTube URL validation and yt-dlp option helpers."""
import logging
import os
from urllib.parse import urlparse

import requests

from app.media_utils import MAX_UPLOAD_BYTES

logger = logging.getLogger(__name__)

ALLOWED_YOUTUBE_HOSTS = {
    'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be',
}
MAX_YOUTUBE_DURATION_SECONDS = 15 * 60


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
    except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError, BrokenPipeError, ConnectionResetError) as exc:
        logger.info('Stream interrupted while sending preview audio: %s', exc)
    finally:
        response.close()
