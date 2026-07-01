"""Pushover notifications."""

import logging
import os
import requests as http_requests

logger = logging.getLogger(__name__)

# Trigger constants for send_pushover_notification.
TRIGGER_WEBHOOK_DOWNLOAD = 'webhook_download'
TRIGGER_WEBHOOK_FAILURE = 'webhook_failure'
TRIGGER_UI = 'ui'


def _is_notification_enabled_for_trigger(trigger):
    """Return True when notifications for the given trigger type are enabled.

    Each trigger maps to its own env var (default: true):
      NOTIFY_ON_WEBHOOK_DOWNLOAD — theme auto-downloaded via a webhook
      NOTIFY_ON_WEBHOOK_FAILURE  — automatic webhook download failed
      NOTIFY_ON_UI_DOWNLOAD      — any download/upload/copy via the Web UI
    """
    if trigger == TRIGGER_WEBHOOK_DOWNLOAD:
        val = (os.getenv('NOTIFY_ON_WEBHOOK_DOWNLOAD') or 'true').strip().lower()
    elif trigger == TRIGGER_WEBHOOK_FAILURE:
        val = (os.getenv('NOTIFY_ON_WEBHOOK_FAILURE') or 'true').strip().lower()
    else:
        val = (os.getenv('NOTIFY_ON_UI_DOWNLOAD') or 'true').strip().lower()
    return val not in {'false', '0', 'no'}


def send_pushover_notification(title, message, trigger=TRIGGER_UI):
    """Send a Pushover push notification if PUSHOVER_APP_TOKEN and PUSHOVER_USER_KEY are set.

    The *trigger* parameter identifies what caused the notification so the user
    can independently enable/disable each class via environment variables:

      trigger='webhook_download' — theme auto-downloaded by a Plex/Jellyfin webhook
                                   controlled by NOTIFY_ON_WEBHOOK_DOWNLOAD (default: true)
      trigger='webhook_failure'  — automatic webhook download failed
                                   controlled by NOTIFY_ON_WEBHOOK_FAILURE (default: true)
      trigger='ui'               — any action through the Web UI (default)
                                   controlled by NOTIFY_ON_UI_DOWNLOAD (default: true)
    """
    token = os.getenv('PUSHOVER_APP_TOKEN')
    user_key = os.getenv('PUSHOVER_USER_KEY')
    if not token or not user_key:
        return
    if not _is_notification_enabled_for_trigger(trigger):
        logger.debug('Pushover notification suppressed (trigger=%s): %s', trigger, title)
        return
    try:
        resp = http_requests.post(
            'https://api.pushover.net/1/messages.json',
            data={'token': token, 'user': user_key, 'title': title, 'message': message},
            timeout=10,
        )
        resp.raise_for_status()
        logger.debug('Pushover notification sent: %s', title)
    except Exception as exc:
        logger.warning('Failed to send Pushover notification: %s', exc)
