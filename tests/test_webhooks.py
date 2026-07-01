"""Tests for app/webhook_handlers.py — Plex webhook processing and authentication."""
import base64
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from tests.helpers import make_mock_show


class TestPlexWebhook:
    @staticmethod
    def _post_webhook(client, payload, headers=None):
        plex = MagicMock()
        plex.machineIdentifier = 'configured-server-uuid'
        with patch('app.web_app.get_plex', return_value=plex):
            with patch('app.webhook_handlers.get_plex', return_value=plex):
                return client.post('/api/webhooks/plex', data={'payload': json.dumps(payload)}, headers=headers or {})

    def test_library_new_event_queues_theme_processing(self, client):
        """library.new event with valid ratingKey queues theme processing."""
        with patch('app.web_app._submit_background_job') as mock_submit:
            payload = {'event': 'library.new', 'Metadata': {'ratingKey': '12345'}, 'Server': {'uuid': 'configured-server-uuid'}}
            resp = self._post_webhook(client, payload)

        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        mock_submit.assert_called_once()

    def test_library_new_event_with_metadata_fallback(self, client):
        """Handles both 'Metadata' and 'metadata' field names."""
        with patch('app.web_app._submit_background_job') as mock_submit:
            payload = {'event': 'library.new', 'metadata': {'ratingKey': '67890'}, 'Server': {'uuid': 'configured-server-uuid'}}
            resp = self._post_webhook(client, payload)

        assert resp.status_code == 200
        mock_submit.assert_called_once()

    def test_missing_payload_handled_gracefully(self, client):
        """Missing payload field returns 200 without error."""
        resp = client.post('/api/webhooks/plex', data={})
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    def test_invalid_json_payload_handled_gracefully(self, client):
        """Invalid JSON payload returns 200 without error."""
        resp = client.post('/api/webhooks/plex', data={'payload': 'not-json'})
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    def test_library_new_without_rating_key_logged(self, client):
        """library.new event without ratingKey is logged."""
        payload = {'event': 'library.new', 'Metadata': {}, 'Server': {'uuid': 'configured-server-uuid'}}
        resp = self._post_webhook(client, payload)
        assert resp.status_code == 200

    def test_non_library_new_event_ignored(self, client):
        """Non-library.new events are safely ignored."""
        payload = {'event': 'library.update', 'Metadata': {'ratingKey': '12345'}, 'Server': {'uuid': 'configured-server-uuid'}}
        with patch('app.web_app._submit_background_job') as mock_submit:
            resp = self._post_webhook(client, payload)
        assert resp.status_code == 200
        mock_submit.assert_not_called()

    def test_webhook_rejects_missing_basic_auth_when_configured(self, client):
        payload = {'event': 'library.new', 'Metadata': {'ratingKey': '12345'}, 'Server': {'uuid': 'configured-server-uuid'}}
        with patch.dict(os.environ, {'WEBHOOK_USERNAME': 'plex', 'WEBHOOK_PASSWORD': 'secret'}, clear=False):
            resp = self._post_webhook(client, payload)
        assert resp.status_code == 401

    def test_webhook_accepts_valid_basic_auth_when_configured(self, client):
        payload = {'event': 'library.new', 'Metadata': {'ratingKey': '12345'}, 'Server': {'uuid': 'configured-server-uuid'}}
        auth = base64.b64encode(b'plex:secret').decode('ascii')

        with patch.dict(os.environ, {'WEBHOOK_USERNAME': 'plex', 'WEBHOOK_PASSWORD': 'secret'}, clear=False), \
             patch('app.web_app._submit_background_job') as mock_submit:
            resp = self._post_webhook(client, payload, headers={'Authorization': f'Basic {auth}'})

        assert resp.status_code == 200
        mock_submit.assert_called_once()

    def test_webhook_rejects_missing_server_uuid(self, client):
        payload = {'event': 'library.new', 'Metadata': {'ratingKey': '12345'}}
        resp = self._post_webhook(client, payload)
        assert resp.status_code == 400

    def test_webhook_rejects_uuid_mismatch(self, client):
        payload = {'event': 'library.new', 'Metadata': {'ratingKey': '12345'}, 'Server': {'uuid': 'other-server-uuid'}}
        resp = self._post_webhook(client, payload)
        assert resp.status_code == 403

    def test_process_library_new_downloads_theme_when_missing(self, tmp_path):
        from app.webhook_handlers import process_plex_library_new

        show_dir = tmp_path / 'New Show (2024)'
        show_dir.mkdir()
        show = make_mock_show(title='New Show', location=str(show_dir), has_theme=True)

        plex = MagicMock()
        plex.library.fetchItem.return_value = show

        mock_download = MagicMock()
        with patch('app.webhook_handlers.get_plex', return_value=plex), \
             patch('app.webhook_handlers.send_pushover_notification') as mock_notify, \
             patch('app.webhook_handlers.refresh_plex_item_metadata') as mock_refresh:
            process_plex_library_new('123', download_plex_theme_fn=mock_download)

        mock_download.assert_called_once()
        mock_notify.assert_called_once()
        mock_refresh.assert_called_once_with(show)

    def test_process_library_new_triggers_metadata_refresh(self, tmp_path):
        """Plex webhook calls refresh_plex_item_metadata after a successful download."""
        from app.webhook_handlers import process_plex_library_new

        show_dir = tmp_path / 'Refresh Show (2024)'
        show_dir.mkdir()
        show = make_mock_show(title='Refresh Show', location=str(show_dir), has_theme=True)

        plex = MagicMock()
        plex.library.fetchItem.return_value = show

        with patch('app.webhook_handlers.get_plex', return_value=plex), \
             patch('app.webhook_handlers.send_pushover_notification'), \
             patch('app.webhook_handlers.refresh_plex_item_metadata') as mock_refresh:
            process_plex_library_new('123', download_plex_theme_fn=MagicMock())

        mock_refresh.assert_called_once_with(show)

    def test_process_library_new_skips_when_theme_exists(self, tmp_path):
        from app.webhook_handlers import process_plex_library_new

        show_dir = tmp_path / 'Existing Show (2024)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'existing')
        show = make_mock_show(title='Existing Show', location=str(show_dir), has_theme=True)

        plex = MagicMock()
        plex.library.fetchItem.return_value = show

        mock_download = MagicMock()
        with patch('app.webhook_handlers.get_plex', return_value=plex):
            process_plex_library_new('123', download_plex_theme_fn=mock_download)

        mock_download.assert_not_called()


class TestJellyfinWebhook:
    def test_item_added_event_queues_processing(self, client):
        with patch('app.web_app._submit_background_job') as mock_submit:
            resp = client.post('/api/webhooks/jellyfin', json={'NotificationType': 'ItemAdded', 'ItemId': 'jf-1'})
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True
        mock_submit.assert_called_once()

    def test_non_item_added_event_is_ignored(self, client):
        with patch('app.web_app._submit_background_job') as mock_submit:
            resp = client.post('/api/webhooks/jellyfin', json={'NotificationType': 'PlaybackStart', 'ItemId': 'jf-1'})
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True
        mock_submit.assert_not_called()

    def test_process_item_added_downloads_themerrdb_theme_when_available(self, tmp_path):
        from app.webhook_handlers import process_jellyfin_item_added

        show_dir = tmp_path / 'New Show (2024)'
        show_dir.mkdir()
        payload = {'NotificationType': 'ItemAdded', 'ItemId': 'jf-1'}
        context = {
            'provider': 'jellyfin',
            'item_id': 'jf-1',
            'title': 'New Show',
            'local_path': show_dir,
            'item': {'Type': 'Series', 'ProviderIds': {'Imdb': 'tt123'}},
        }
        mock_download = MagicMock()

        with patch('app.webhook_handlers.get_themerrdb_theme_for_external_ids', return_value={'youtube_theme_url': 'https://www.youtube.com/watch?v=abc'}), \
             patch('app.webhook_handlers.send_pushover_notification') as mock_notify, \
             patch('app.webhook_handlers.sync_cached_item_theme_state') as mock_sync, \
             patch('app.webhook_handlers.refresh_jellyfin_item_metadata') as mock_refresh:
            process_jellyfin_item_added(
                payload,
                get_item_context_fn=lambda _provider, _item_id: context,
                download_youtube_theme_fn=mock_download,
            )

        mock_download.assert_called_once_with('https://www.youtube.com/watch?v=abc', show_dir / 'theme.mp3')
        mock_sync.assert_called_once_with('jellyfin', 'jf-1')
        mock_notify.assert_called_once()
        mock_refresh.assert_called_once_with('jf-1')

    def test_process_item_added_triggers_metadata_refresh(self, tmp_path):
        """Jellyfin webhook calls refresh_jellyfin_item_metadata after a successful download."""
        from app.webhook_handlers import process_jellyfin_item_added

        show_dir = tmp_path / 'Refresh Show (2024)'
        show_dir.mkdir()
        payload = {'NotificationType': 'ItemAdded', 'ItemId': 'jf-refresh'}
        context = {
            'provider': 'jellyfin',
            'item_id': 'jf-refresh',
            'title': 'Refresh Show',
            'local_path': show_dir,
            'item': {'Type': 'Series', 'ProviderIds': {'Imdb': 'tt456'}},
        }

        with patch('app.webhook_handlers.get_themerrdb_theme_for_external_ids', return_value={'youtube_theme_url': 'https://www.youtube.com/watch?v=xyz'}), \
             patch('app.webhook_handlers.send_pushover_notification'), \
             patch('app.webhook_handlers.sync_cached_item_theme_state'), \
             patch('app.webhook_handlers.refresh_jellyfin_item_metadata') as mock_refresh:
            process_jellyfin_item_added(
                payload,
                get_item_context_fn=lambda _provider, _item_id: context,
                download_youtube_theme_fn=MagicMock(),
            )

        mock_refresh.assert_called_once_with('jf-refresh')

    def test_process_item_added_skips_when_local_theme_exists(self, tmp_path):
        from app.webhook_handlers import process_jellyfin_item_added

        show_dir = tmp_path / 'Existing Show (2024)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'existing')
        payload = {'NotificationType': 'ItemAdded', 'ItemId': 'jf-1'}
        context = {
            'provider': 'jellyfin',
            'item_id': 'jf-1',
            'title': 'Existing Show',
            'local_path': show_dir,
            'item': {'Type': 'Series', 'ProviderIds': {'Imdb': 'tt123'}},
        }
        mock_download = MagicMock()

        with patch('app.webhook_handlers.get_themerrdb_theme_for_external_ids') as mock_themerrdb:
            process_jellyfin_item_added(
                payload,
                get_item_context_fn=lambda _provider, _item_id: context,
                download_youtube_theme_fn=mock_download,
            )

        mock_themerrdb.assert_not_called()
        mock_download.assert_not_called()

    def test_process_item_added_skips_when_themerrdb_has_no_theme(self, tmp_path):
        from app.webhook_handlers import process_jellyfin_item_added

        show_dir = tmp_path / 'No Theme Show (2024)'
        show_dir.mkdir()
        payload = {'NotificationType': 'ItemAdded', 'ItemId': 'jf-1'}
        context = {
            'provider': 'jellyfin',
            'item_id': 'jf-1',
            'title': 'No Theme Show',
            'local_path': show_dir,
            'item': {'Type': 'Series', 'ProviderIds': {'Imdb': 'tt123'}},
        }
        mock_download = MagicMock()

        with patch('app.webhook_handlers.get_themerrdb_theme_for_external_ids', return_value=None):
            process_jellyfin_item_added(
                payload,
                get_item_context_fn=lambda _provider, _item_id: context,
                download_youtube_theme_fn=mock_download,
            )

        mock_download.assert_not_called()


class TestWebhookPartialAuth:
    """BUG-005 — Partial webhook Basic Auth should return 503, not silently accept."""

    def test_partial_auth_only_username_returns_503(self, app):
        with patch.dict(os.environ, {
            'WEBHOOK_USERNAME': 'user', 'WEBHOOK_PASSWORD': '',
            'DISABLE_AUTH': 'true',
        }):
            with app.test_client() as c:
                resp = c.post('/api/webhooks/plex',
                              data='{}', content_type='application/json')
        assert resp.status_code == 503

    def test_partial_auth_only_password_returns_503(self, app):
        with patch.dict(os.environ, {
            'WEBHOOK_USERNAME': '', 'WEBHOOK_PASSWORD': 'secret',
            'DISABLE_AUTH': 'true',
        }):
            with app.test_client() as c:
                resp = c.post('/api/webhooks/plex',
                              data='{}', content_type='application/json')
        assert resp.status_code == 503

    def test_no_auth_config_accepts_request(self, app):
        """When neither credential is set, webhook auth is intentionally disabled."""
        with patch.dict(os.environ, {
            'WEBHOOK_USERNAME': '', 'WEBHOOK_PASSWORD': '',
            'DISABLE_AUTH': 'true',
        }):
            with app.test_client() as c:
                resp = c.post('/api/webhooks/plex',
                              data='{}', content_type='application/json')
        # Request gets past auth check (may fail for other reasons, but not 503)
        assert resp.status_code != 503
