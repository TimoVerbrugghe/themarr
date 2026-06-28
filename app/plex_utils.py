"""Plex connection and library path helpers."""
import logging
import os
from pathlib import Path

from plexapi.server import PlexServer

from app.media_utils import _is_video_file_path, _validate_local_media_path

logger = logging.getLogger(__name__)


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


def get_validated_plex_local_path(item):
    """Get and validate local filesystem path for a Plex library item."""
    local_path = get_item_local_path(item)
    if not local_path:
        return None
    return _validate_local_media_path(local_path)
