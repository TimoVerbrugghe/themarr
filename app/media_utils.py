"""Filesystem, media path, and upload validation helpers."""
import logging
import os
from pathlib import Path

from werkzeug.utils import safe_join

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024

ALLOWED_UPLOAD_TYPES = {'audio/mpeg', 'audio/mp3', 'application/octet-stream'}

VIDEO_FILE_EXTENSIONS = {
    '.3gp', '.asf', '.avi', '.divx', '.flv', '.iso', '.m2ts', '.m4v', '.mkv',
    '.mov', '.mp4', '.mpeg', '.mpg', '.mts', '.ts', '.vob', '.webm', '.wmv',
}


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
