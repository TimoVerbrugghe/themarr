"""Bulk operations on multiple items."""

import logging

from flask import request, jsonify

from app.plex_utils import get_plex, plex_session_get, get_validated_plex_local_path, refresh_plex_item_metadata
from app.media_utils import _theme_file_path
from app.errors import error_response
from app.notifications import send_pushover_notification, TRIGGER_UI
from app.theme_state import has_nonempty_theme_file
from app.cache import sync_cached_item

logger = logging.getLogger(__name__)

MAX_BULK_ITEMS = 100


def bulk_download_themes():
    """Download themes for multiple Plex items in one request."""
    data = request.get_json(silent=True)
    if not data or not isinstance(data.get('ratingKeys'), list):
        return jsonify({'error': 'ratingKeys (list) is required'}), 400

    rating_keys = data['ratingKeys']
    overwrite = data.get('overwrite', False)

    if not rating_keys:
        return jsonify({'error': 'ratingKeys list is empty'}), 400
    if len(rating_keys) > MAX_BULK_ITEMS:
        return jsonify({'error': f'Maximum {MAX_BULK_ITEMS} items per bulk operation'}), 400

    try:
        plex = get_plex()
    except Exception as exc:
        return error_response('Failed to connect to Plex', exc=exc)

    results = {'success': [], 'skipped': [], 'no_theme': [], 'failed': []}

    for rating_key in rating_keys:
        try:
            item = plex.fetchItem(int(rating_key))

            if not getattr(item, 'theme', None):
                results['no_theme'].append({'ratingKey': rating_key, 'title': item.title})
                continue

            local_path = get_validated_plex_local_path(item)
            if not local_path:
                results['failed'].append({
                    'ratingKey': rating_key,
                    'title': getattr(item, 'title', '?'),
                    'error': 'Cannot determine local path',
                })
                continue

            theme_path = _theme_file_path(local_path)

            if has_nonempty_theme_file(local_path) and not overwrite:
                results['skipped'].append({'ratingKey': rating_key, 'title': item.title})
                continue

            url = plex.url(item.theme, includeToken=True)
            response = plex_session_get(plex, url, stream=True, timeout=30)
            response.raise_for_status()

            local_path.mkdir(parents=True, exist_ok=True)
            with open(theme_path, 'wb') as fh:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        fh.write(chunk)

            results['success'].append({'ratingKey': rating_key, 'title': item.title})
            sync_cached_item(item)
            refresh_plex_item_metadata(item)
            logger.info('Bulk: downloaded theme for %s', item.title)

        except Exception as exc:
            results['failed'].append({'ratingKey': rating_key, 'error': str(exc)})

    if results['success']:
        titles = ', '.join(r['title'] for r in results['success'][:5])
        extra = len(results['success']) - 5
        msg = f"{titles}{f' and {extra} more' if extra > 0 else ''}"
        send_pushover_notification(
            title=f"Themes Downloaded ({len(results['success'])})",
            message=msg,
            trigger=TRIGGER_UI,
        )

    return jsonify(results)
