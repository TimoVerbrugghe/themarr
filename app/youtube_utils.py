"""YouTube URL validation and yt-dlp option helpers."""
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

import requests
import yt_dlp

logger = logging.getLogger(__name__)

ALLOWED_YOUTUBE_HOSTS = {
    'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be',
}
MAX_YOUTUBE_DURATION_SECONDS = 15 * 60
MAX_YOUTUBE_DOWNLOAD_BYTES = 1024 * 1024 * 1024


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


def build_youtube_match_filter(start_seconds=None, end_seconds=None):
    """Build a yt-dlp match filter that honors optional trim settings."""
    def youtube_match_filter(info_dict, *, incomplete):
        duration = info_dict.get('duration')
        if not duration:
            return None

        total_duration = int(duration)
        effective_start = start_seconds or 0
        if effective_start >= total_duration:
            return 'Start time exceeds video duration'

        if end_seconds is not None:
            if end_seconds > total_duration:
                return 'Stop time exceeds video duration'
            clip_duration = end_seconds - effective_start
            if clip_duration > MAX_YOUTUBE_DURATION_SECONDS:
                return f'Requested clip exceeds {MAX_YOUTUBE_DURATION_SECONDS} seconds'
            return None

        effective_duration = total_duration - effective_start
        if effective_duration > MAX_YOUTUBE_DURATION_SECONDS:
            return (
                f'Video exceeds {MAX_YOUTUBE_DURATION_SECONDS} seconds; '
                'provide a stop time to trim it'
            )
        return None

    return youtube_match_filter


def youtube_match_filter(info_dict, *, incomplete):
    """Backward-compatible default match filter used by search routes."""
    return build_youtube_match_filter()(info_dict, incomplete=incomplete)


def _youtube_retry_profiles():
    """Yield yt-dlp retry profiles for videos with client-specific availability."""
    return [
        ('default', {}),
        ('android', {'extractor_args': {'youtube': {'player_client': ['android']}}}),
    ]


def _youtube_preview_ydl_opts(profile_overrides=None):
    """Build yt-dlp options for extracting a preview audio stream URL.

    NOTE: Do NOT add 'remote_components' here.  That option instructs yt-dlp
    to fetch and execute JavaScript from an external source (e.g. GitHub) at
    runtime, which is a supply-chain remote-code-execution risk in a server
    process.  The bundled yt-dlp extractor is sufficient for standard YouTube
    URLs.
    """
    opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'skip_download': True,
        'socket_timeout': 30,
    }
    if profile_overrides:
        opts.update(profile_overrides)
    return opts


def _youtube_download_ydl_opts(tmpdir, profile_overrides=None, start_seconds=None, end_seconds=None):
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
        'match_filter': build_youtube_match_filter(
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        ),
        # yt-dlp applies max_filesize to the pre-trim source download, so this
        # limit must allow large inputs that ffmpeg can later trim to a small MP3.
        'max_filesize': MAX_YOUTUBE_DOWNLOAD_BYTES,
    }
    if profile_overrides:
        opts.update(profile_overrides)
    return opts


def _parse_time_to_seconds(value, field_name):
    """Parse a user-provided trim timestamp into total seconds."""
    if value is None:
        return None

    if isinstance(value, bool):
        raise ValueError(f'{field_name} must be a time value')

    if isinstance(value, int):
        if value < 0:
            raise ValueError(f'{field_name} must be greater than or equal to 0')
        return value

    if isinstance(value, float):
        if value < 0 or not value.is_integer():
            raise ValueError(f'{field_name} must be a whole number of seconds')
        return int(value)

    if not isinstance(value, str):
        raise ValueError(f'{field_name} must be a valid time value')

    raw = value.strip()
    if not raw:
        return None

    if raw.isdigit():
        seconds = int(raw)
        return seconds

    parts = raw.split(':')
    if len(parts) not in {2, 3} or not all(part.isdigit() for part in parts):
        raise ValueError(f'{field_name} must use seconds, MM:SS, or HH:MM:SS format')

    if len(parts) == 2:
        minutes, seconds = int(parts[0]), int(parts[1])
        if seconds >= 60:
            raise ValueError(f'{field_name} has invalid seconds value')
        return minutes * 60 + seconds

    hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
    if minutes >= 60 or seconds >= 60:
        raise ValueError(f'{field_name} has invalid minutes or seconds value')
    return hours * 3600 + minutes * 60 + seconds


def normalize_youtube_trim_window(start_time, end_time):
    """Normalize optional trim times and validate the requested range."""
    start_seconds = _parse_time_to_seconds(start_time, 'Start time')
    end_seconds = _parse_time_to_seconds(end_time, 'Stop time')
    if start_seconds is not None and end_seconds is not None and end_seconds <= start_seconds:
        raise ValueError('Stop time must be greater than start time')
    return start_seconds, end_seconds


def _clean_yt_dlp_error(exc):
    """Normalize yt-dlp error messages for user-facing responses."""
    return str(exc).removeprefix('ERROR: ').strip()


def _derive_download_skip_reason(youtube_url, profile_overrides, start_seconds=None, end_seconds=None):
    """Best-effort explanation when yt-dlp finishes without producing an MP3."""
    try:
        with yt_dlp.YoutubeDL(_youtube_preview_ydl_opts(profile_overrides)) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
        reason = build_youtube_match_filter(
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        )(info or {}, incomplete=False)
        return reason
    except yt_dlp.utils.DownloadError:
        return None


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


_ALLOWED_AUDIO_STREAM_HOSTS = {'googlevideo.com', 'youtube.com', 'googleusercontent.com'}


def extract_youtube_audio_url(youtube_url):
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


def is_valid_audio_stream_url(url):
    """Return True when a yt-dlp resolved stream URL is from an allowed CDN host.

    This guards against SSRF: yt-dlp should only return googlevideo.com (or
    similar Google CDN) URLs for YouTube content.  Any other host is rejected.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {'https', 'http'}:
            return False
        hostname = (parsed.hostname or '').lower()
        return any(
            hostname == h or hostname.endswith('.' + h)
            for h in _ALLOWED_AUDIO_STREAM_HOSTS
        )
    except ValueError:
        return False


def download_youtube_theme_mp3(youtube_url, tmpdir, start_seconds=None, end_seconds=None):
    """Download YouTube audio as MP3 with client-profile fallback retries."""
    errors = []
    for profile_name, overrides in _youtube_retry_profiles():
        try:
            ydl_opts = _youtube_download_ydl_opts(
                tmpdir,
                overrides,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
            )
            if start_seconds is not None or end_seconds is not None:
                postprocessor_args = []
                if start_seconds is not None:
                    postprocessor_args.extend(['-ss', str(start_seconds)])
                if end_seconds is not None:
                    postprocessor_args.extend(['-to', str(end_seconds)])
                ydl_opts['postprocessor_args'] = postprocessor_args
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([youtube_url])
            mp3_files = list(Path(tmpdir).glob('*.mp3'))
            if mp3_files:
                return mp3_files[0]
            skip_reason = _derive_download_skip_reason(
                youtube_url,
                overrides,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
            )
            if skip_reason:
                errors.append(f'{profile_name}: {skip_reason}')
            else:
                errors.append(f'{profile_name}: Download failed: no MP3 file produced')
        except yt_dlp.utils.DownloadError as exc:
            errors.append(f'{profile_name}: {_clean_yt_dlp_error(exc)}')
    raise yt_dlp.utils.DownloadError(' | '.join(errors))
