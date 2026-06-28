"""Filesystem, media path, and upload validation helpers."""
import os
from pathlib import Path

from werkzeug.utils import safe_join

MAX_UPLOAD_BYTES = 25 * 1024 * 1024

ALLOWED_UPLOAD_TYPES = {'audio/mpeg', 'audio/mp3'}

# MP3 magic byte prefixes: ID3 tag header or MPEG sync-word variants.
_MP3_MAGIC_PREFIXES = (b'ID3', b'\xff\xfb', b'\xff\xf3', b'\xff\xf2', b'\xff\xe0')

VIDEO_FILE_EXTENSIONS = {
    '.3gp', '.asf', '.avi', '.divx', '.flv', '.iso', '.m2ts', '.m4v', '.mkv',
    '.mov', '.mp4', '.mpeg', '.mpg', '.mts', '.ts', '.vob', '.webm', '.wmv',
}


def _is_video_file_path(path):
    """Return True when a path appears to reference a video file."""
    return path.suffix.lower() in VIDEO_FILE_EXTENSIONS


def _get_allowed_media_roots() -> list:
    """Return the configured media root boundaries from environment variables."""
    roots = []
    for env_var in ('TV_SHOWS_HOST_PATH', 'MOVIES_HOST_PATH'):
        val = (os.getenv(env_var) or '').strip()
        if val:
            try:
                roots.append(Path(val).resolve())
            except Exception:
                pass
    return roots


def _validate_local_media_path(local_path):
    """Normalize and validate local media path for safe filesystem usage.

    When TV_SHOWS_HOST_PATH or MOVIES_HOST_PATH are configured, the resolved
    path must fall within one of those roots (path-boundary enforcement).
    """
    if local_path is None:
        return None

    local_path_str = str(local_path)
    sanitized = safe_join('/', local_path_str.lstrip('/'))
    if sanitized is None:
        raise ValueError('Invalid local media path')

    normalized = os.path.normpath(sanitized)
    if not normalized.startswith('/'):
        raise ValueError('Invalid local media path')

    normalized_path = Path(normalized)

    allowed_roots = _get_allowed_media_roots()
    if allowed_roots:
        normalized_resolved = normalized_path.resolve() if normalized_path.exists() else normalized_path
        # Use is_relative_to() (Python 3.9+) for clear, edge-case-safe boundary check.
        if not any(
            normalized_resolved == root or normalized_resolved.is_relative_to(root)
            for root in allowed_roots
        ):
            raise ValueError(
                f'Path {normalized_path} is outside the allowed media directories'
            )

    return normalized_path


def _is_valid_mp3_magic(file_obj) -> bool:
    """Return True when the first bytes of *file_obj* match an MP3 magic signature."""
    try:
        header = file_obj.read(3)
        file_obj.seek(0)
        return any(header.startswith(prefix) for prefix in _MP3_MAGIC_PREFIXES)
    except Exception:
        return False


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
