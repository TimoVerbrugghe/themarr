"""Webhook event processing."""

import hmac
import logging

from flask import jsonify

from app.external_ids import extract_jellyfin_external_ids
from app.plex_utils import get_plex, get_validated_plex_local_path
from app.media_utils import _theme_file_path
from app.notifications import send_pushover_notification
from app.theme_state import has_nonempty_theme_file
from app.cache import sync_cached_item, sync_cached_item_theme_state
from app.themerrdb_service import get_themerrdb_theme_for_external_ids
from app.youtube_utils import is_valid_youtube_url

logger = logging.getLogger(__name__)


def check_webhook_server_uuid(payload):
    """Validate webhook server UUID against the configured Plex server."""
    server_info = payload.get('Server') or payload.get('server') or {}
    if not isinstance(server_info, dict):
        return jsonify({'error': 'Invalid webhook payload server metadata'}), 400
    webhook_server_uuid = str(server_info.get('uuid') or '').strip()
    if not webhook_server_uuid:
        return jsonify({'error': 'Missing webhook server UUID'}), 400

    try:
        plex = get_plex()
    except Exception as exc:
        logger.warning('Plex webhook: failed to load configured Plex server for UUID check: %s', exc)
        return jsonify({'error': 'Unable to validate webhook source server'}), 503

    configured_uuid = str(getattr(plex, 'machineIdentifier', '') or '').strip()
    if not configured_uuid:
        logger.warning('Plex webhook: configured Plex server did not expose machineIdentifier')
        return jsonify({'error': 'Unable to validate webhook source server'}), 503

    if not hmac.compare_digest(webhook_server_uuid, configured_uuid):
        logger.warning(
            'Plex webhook rejected: server UUID mismatch (received=%s configured=%s)',
            webhook_server_uuid,
            configured_uuid,
        )
        return jsonify({'error': 'Webhook server UUID mismatch'}), 403
    return None


def process_plex_library_new(rating_key, download_plex_theme_fn):
    """Process a Plex library.new webhook event by downloading theme if needed.
    
    Retrieves the item from Plex by rating key, checks if theme.mp3 already exists,
    and downloads the Plex theme if available.
    """
    try:
        plex = get_plex()
        item = plex.library.fetchItem(int(rating_key))
        
        logger.info("Plex webhook: processing new item '%s' (ratingKey=%s)", item.title, rating_key)
        
        if not getattr(item, 'theme', None):
            logger.info("Plex webhook: '%s' has no theme in Plex — nothing to download", item.title)
            return
        
        local_path = get_validated_plex_local_path(item)
        if not local_path:
            logger.warning("Plex webhook: cannot determine local path for '%s'", item.title)
            return
        
        theme_path = _theme_file_path(local_path)
        if has_nonempty_theme_file(local_path):
            logger.info("Plex webhook: '%s' already has a theme file", item.title)
            return
        
        download_plex_theme_fn(plex, item, theme_path)
        sync_cached_item(item)
        send_pushover_notification(
            title='Theme Downloaded',
            message=f'{item.title} theme auto-downloaded via Plex webhook',
        )
    except Exception as exc:
        logger.error("Plex webhook: failed to process item %s: %s", rating_key, exc)
        send_pushover_notification(
            title='Theme Download Failed',
            message=f'Failed to process Plex webhook for item {rating_key}',
        )


def _is_jellyfin_item_added_event(payload):
    """Return True when a Jellyfin webhook payload represents an item-added event."""
    event = str(
        payload.get('NotificationType')
        or payload.get('notificationType')
        or payload.get('event')
        or ''
    ).strip().lower()
    normalized = ''.join(ch for ch in event if ch.isalnum())
    return normalized in {'itemadded', 'librarynew'}


def _extract_jellyfin_item_id(payload):
    """Extract Jellyfin item identifier from a webhook payload."""
    item = payload.get('Item') if isinstance(payload.get('Item'), dict) else {}
    return str(
        payload.get('ItemId')
        or payload.get('itemId')
        or payload.get('item_id')
        or item.get('Id')
        or item.get('id')
        or ''
    ).strip()


def _item_type_from_jellyfin_value(raw_item_type):
    item_type = str(raw_item_type or '').strip().lower()
    if item_type == 'movie':
        return 'movie'
    if item_type in {'series', 'season', 'episode', 'show', 'tvshow'}:
        return 'show'
    return None


def process_jellyfin_item_added(payload, get_item_context_fn, download_youtube_theme_fn):
    """Process Jellyfin item-added webhook by downloading a ThemerrDB theme when available."""
    if not isinstance(payload, dict) or not _is_jellyfin_item_added_event(payload):
        return

    item_id = _extract_jellyfin_item_id(payload)
    if not item_id:
        logger.warning('Jellyfin webhook: item-added event missing ItemId')
        return

    try:
        context = get_item_context_fn('jellyfin', item_id)
        local_path = context.get('local_path')
        title = context.get('title') or f'Item {item_id}'
        if not local_path:
            logger.warning("Jellyfin webhook: cannot determine local path for '%s'", title)
            return

        if has_nonempty_theme_file(local_path):
            logger.info("Jellyfin webhook: '%s' already has a theme file", title)
            return

        item = context.get('item') if isinstance(context.get('item'), dict) else {}
        item_type = _item_type_from_jellyfin_value(item.get('Type'))
        if not item_type:
            item_type = _item_type_from_jellyfin_value(payload.get('ItemType'))
        if not item_type:
            logger.info("Jellyfin webhook: unsupported item type for '%s'", title)
            return

        external_ids = extract_jellyfin_external_ids(item)
        themerrdb_data = get_themerrdb_theme_for_external_ids(item_type, external_ids)
        youtube_url = themerrdb_data.get('youtube_theme_url') if isinstance(themerrdb_data, dict) else None
        if not youtube_url:
            logger.info("Jellyfin webhook: '%s' has no matching ThemerrDB theme", title)
            return
        if not is_valid_youtube_url(youtube_url):
            logger.warning("Jellyfin webhook: '%s' returned invalid ThemerrDB YouTube URL", title)
            return

        theme_path = _theme_file_path(local_path)
        download_youtube_theme_fn(youtube_url, theme_path)
        sync_cached_item_theme_state('jellyfin', item_id)
        send_pushover_notification(
            title='Theme Downloaded',
            message=f'{title} theme auto-downloaded via Jellyfin webhook',
        )
    except Exception as exc:
        logger.error('Jellyfin webhook: failed to process item %s: %s', item_id, exc)
        send_pushover_notification(
            title='Theme Download Failed',
            message=f'Failed to process Jellyfin webhook for item {item_id}',
        )
