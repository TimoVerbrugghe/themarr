"""Jellyfin connection and media path helpers."""
import logging
import os
from pathlib import Path

from app.media_utils import _is_video_file_path

logger = logging.getLogger(__name__)


def jellyfin_is_configured():
    """Return True when Jellyfin credentials are configured."""
    jellyfin_url = (os.getenv('JELLYFIN_URL') or '').strip()
    jellyfin_api_key = (os.getenv('JELLYFIN_API_KEY') or '').strip()
    return bool(jellyfin_url and jellyfin_api_key)


def get_jellyfin():
    """Get Jellyfin connection settings from environment variables."""
    jellyfin_url = (os.getenv('JELLYFIN_URL') or '').strip()
    jellyfin_api_key = (os.getenv('JELLYFIN_API_KEY') or '').strip()
    jellyfin_user_id = (os.getenv('JELLYFIN_USER_ID') or '').strip() or None
    if not jellyfin_url or not jellyfin_api_key:
        raise ValueError('JELLYFIN_URL and JELLYFIN_API_KEY environment variables must be set')
    return {
        'url': jellyfin_url.rstrip('/'),
        'api_key': jellyfin_api_key,
        'user_id': jellyfin_user_id,
    }


def get_jellyfin_item_local_path(item):
    """Get local filesystem path for a Jellyfin item dict."""
    raw_path = item.get('Path')
    if not raw_path:
        return None
    candidate = Path(raw_path)
    item_type = (item.get('Type') or '').lower()
    if item_type == 'movie' and _is_video_file_path(candidate):
        return candidate.parent
    return candidate


def _normalize_provider(provider):
    normalized = (provider or '').strip().lower()
    if normalized not in {'plex', 'jellyfin'}:
        raise ValueError('provider must be either "plex" or "jellyfin"')
    return normalized
