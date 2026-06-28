"""External ID extraction utilities for Plex and Jellyfin media items."""


def _normalize_external_id(value):
    """Normalize provider ID values to a stripped string or None."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def extract_external_ids(item):
    """Extract external IDs (IMDB/TVDB/TMDB) from Plex item guids.

    Returns dict with 'imdb', 'tvdb', and 'tmdb' keys (None if not found).
    """
    ids = {'imdb': None, 'tvdb': None, 'tmdb': None}
    for guid in getattr(item, 'guids', []):
        if isinstance(guid, dict):
            guid_id = guid.get('id', '')
        else:
            guid_id = getattr(guid, 'id', '') or ''
        if guid_id.startswith('imdb://'):
            ids['imdb'] = guid_id.replace('imdb://', '')
        elif guid_id.startswith('tmdb://'):
            ids['tmdb'] = guid_id.replace('tmdb://', '')
        elif guid_id.startswith('tvdb://'):
            ids['tvdb'] = guid_id.replace('tvdb://', '')
    return ids


def extract_jellyfin_external_ids(item):
    """Extract external IDs (IMDB/TMDB/TVDB) from a Jellyfin item dict."""
    provider_ids = item.get('ProviderIds') if isinstance(item, dict) else {}
    provider_ids = provider_ids if isinstance(provider_ids, dict) else {}
    return {
        'imdb': _normalize_external_id(provider_ids.get('Imdb')),
        'tmdb': _normalize_external_id(provider_ids.get('Tmdb')),
        'tvdb': _normalize_external_id(provider_ids.get('Tvdb')),
    }
