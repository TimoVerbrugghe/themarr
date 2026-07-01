"""Plex connection and library path helpers."""
import logging
import os
from pathlib import Path

from plexapi.server import PlexServer

from app.media_utils import _is_video_file_path, _validate_local_media_path

logger = logging.getLogger(__name__)


def plex_is_configured():
    """Return True when Plex credentials are configured."""
    plex_url = (os.getenv('PLEX_URL') or '').strip()
    plex_token = (os.getenv('PLEX_TOKEN') or '').strip()
    return bool(plex_url and plex_token)


def get_plex():
    """Get authenticated PlexServer instance from environment variables."""
    plex_url = (os.getenv('PLEX_URL') or '').strip()
    plex_token = (os.getenv('PLEX_TOKEN') or '').strip()
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


def download_plex_theme_to_path(plex, item, theme_path):
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


def refresh_plex_item_metadata(item):
    """Trigger a Plex metadata refresh for *item* so the server picks up new local files.

    Errors are logged as warnings and do not propagate — the refresh is best-effort.
    """
    try:
        item.refresh()
        logger.info('Triggered Plex metadata refresh for %s', getattr(item, 'title', str(item)))
    except Exception as exc:
        logger.warning('Failed to trigger Plex metadata refresh for item: %s', exc)


def find_plex_item_by_path(plex, local_path):
    """Find the Plex item whose local filesystem path equals *local_path*.

    Iterates all show/movie sections and checks each item's reported location.
    Returns the first matching plexapi item, or None when not found.
    Used for cross-provider metadata refresh when both Plex and Jellyfin are connected.
    """
    path_str = str(local_path)
    try:
        for section in plex.library.sections():
            if section.type not in ('show', 'movie'):
                continue
            for item in section.all():
                item_path = get_item_local_path(item)
                if item_path and str(item_path) == path_str:
                    return item
    except Exception as exc:
        logger.warning('Failed to search Plex for item at path %s: %s', local_path, exc)
    return None
