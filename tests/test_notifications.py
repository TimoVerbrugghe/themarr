"""Tests for app/notifications.py — Pushover notifications and settings test endpoint."""
import os
from unittest.mock import MagicMock, patch

from tests.helpers import make_mock_show


class TestPushoverNotification:
    def test_no_op_without_config(self):
        """send_pushover_notification does nothing if env vars are missing."""
        from app.notifications import send_pushover_notification
        with patch('app.notifications.http_requests') as mock_req, \
             patch.dict(os.environ, {}, clear=True):
            send_pushover_notification('Test', 'body')
            mock_req.post.assert_not_called()

    def test_sends_with_config(self):
        from app.notifications import send_pushover_notification
        with patch('app.notifications.http_requests') as mock_req, \
             patch.dict(os.environ, {'PUSHOVER_APP_TOKEN': 'tok', 'PUSHOVER_USER_KEY': 'usr'}):
            mock_resp = MagicMock()
            mock_req.post.return_value = mock_resp
            send_pushover_notification('Title', 'Body text')
        mock_req.post.assert_called_once()
        call_kwargs = mock_req.post.call_args
        data = call_kwargs[1]['data'] if 'data' in call_kwargs[1] else call_kwargs[0][1]
        assert data['token'] == 'tok'
        assert data['user'] == 'usr'

    def test_handles_request_failure_gracefully(self):
        from app.notifications import send_pushover_notification
        with patch('app.notifications.http_requests') as mock_req, \
             patch.dict(os.environ, {'PUSHOVER_APP_TOKEN': 'tok', 'PUSHOVER_USER_KEY': 'usr'}):
            mock_req.post.side_effect = Exception('Network error')
            # Should not raise
            send_pushover_notification('Title', 'Body text')

    def test_pushover_called_on_download(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show
        mock_plex.url.return_value = 'http://plex/theme.mp3'
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b'mp3data']
        mock_plex._session.get.return_value = mock_resp

        with patch('app.web_app.send_pushover_notification') as mock_notif:
            resp = client.post('/api/items/1/theme/download', json={'overwrite': False})
        assert resp.status_code == 200
        mock_notif.assert_called_once()


class TestNotificationTriggers:
    """Tests for trigger-based notification filtering."""

    base_env = {'PUSHOVER_APP_TOKEN': 'tok', 'PUSHOVER_USER_KEY': 'usr'}

    def _send(self, trigger, extra_env=None):
        from app.notifications import send_pushover_notification
        env = dict(self.base_env)
        if extra_env:
            env.update(extra_env)
        with patch('app.notifications.http_requests') as mock_req, \
             patch.dict(os.environ, env, clear=True):
            mock_req.post.return_value = MagicMock()
            send_pushover_notification('T', 'M', trigger=trigger)
            return mock_req.post.call_count

    def test_ui_trigger_sends_by_default(self):
        from app.notifications import TRIGGER_UI
        assert self._send(TRIGGER_UI) == 1

    def test_webhook_download_trigger_sends_by_default(self):
        from app.notifications import TRIGGER_WEBHOOK_DOWNLOAD
        assert self._send(TRIGGER_WEBHOOK_DOWNLOAD) == 1

    def test_webhook_failure_trigger_sends_by_default(self):
        from app.notifications import TRIGGER_WEBHOOK_FAILURE
        assert self._send(TRIGGER_WEBHOOK_FAILURE) == 1

    def test_ui_trigger_suppressed_when_disabled(self):
        from app.notifications import TRIGGER_UI
        count = self._send(TRIGGER_UI, extra_env={'NOTIFY_ON_UI_DOWNLOAD': 'false'})
        assert count == 0

    def test_webhook_download_suppressed_when_disabled(self):
        from app.notifications import TRIGGER_WEBHOOK_DOWNLOAD
        count = self._send(TRIGGER_WEBHOOK_DOWNLOAD, extra_env={'NOTIFY_ON_WEBHOOK_DOWNLOAD': 'false'})
        assert count == 0

    def test_webhook_failure_suppressed_when_disabled(self):
        from app.notifications import TRIGGER_WEBHOOK_FAILURE
        count = self._send(TRIGGER_WEBHOOK_FAILURE, extra_env={'NOTIFY_ON_WEBHOOK_FAILURE': 'false'})
        assert count == 0

    def test_disabling_webhook_download_does_not_affect_ui(self):
        """Suppressing webhook notifications does not affect UI notifications."""
        from app.notifications import TRIGGER_UI
        count = self._send(TRIGGER_UI, extra_env={'NOTIFY_ON_WEBHOOK_DOWNLOAD': 'false'})
        assert count == 1

    def test_disabling_ui_does_not_affect_webhook_download(self):
        from app.notifications import TRIGGER_WEBHOOK_DOWNLOAD
        count = self._send(TRIGGER_WEBHOOK_DOWNLOAD, extra_env={'NOTIFY_ON_UI_DOWNLOAD': 'false'})
        assert count == 1

    def test_webhook_download_passes_correct_trigger_to_notification(self, tmp_path):
        """Plex webhook passes TRIGGER_WEBHOOK_DOWNLOAD to send_pushover_notification."""
        from app.webhook_handlers import process_plex_library_new
        from app.notifications import TRIGGER_WEBHOOK_DOWNLOAD

        show_dir = tmp_path / 'Show'
        show_dir.mkdir()
        show = make_mock_show(title='Show', location=str(show_dir), has_theme=True)
        plex = MagicMock()
        plex.library.fetchItem.return_value = show

        with patch('app.webhook_handlers.get_plex', return_value=plex), \
             patch('app.webhook_handlers.send_pushover_notification') as mock_notif, \
             patch('app.webhook_handlers.refresh_plex_item_metadata'):
            process_plex_library_new('123', download_plex_theme_fn=MagicMock())

        mock_notif.assert_called_once()
        _, kwargs = mock_notif.call_args
        assert kwargs.get('trigger') == TRIGGER_WEBHOOK_DOWNLOAD

    def test_webhook_failure_passes_correct_trigger_to_notification(self):
        """Plex webhook failure passes TRIGGER_WEBHOOK_FAILURE to send_pushover_notification."""
        from app.webhook_handlers import process_plex_library_new
        from app.notifications import TRIGGER_WEBHOOK_FAILURE

        plex = MagicMock()
        plex.library.fetchItem.side_effect = Exception('Plex error')

        with patch('app.webhook_handlers.get_plex', return_value=plex), \
             patch('app.webhook_handlers.send_pushover_notification') as mock_notif:
            process_plex_library_new('999', download_plex_theme_fn=MagicMock())

        mock_notif.assert_called_once()
        _, kwargs = mock_notif.call_args
        assert kwargs.get('trigger') == TRIGGER_WEBHOOK_FAILURE


class TestSettingsTestPushover:
    def test_returns_400_when_not_configured(self, client):
        with patch.dict(os.environ, {}, clear=True):
            resp = client.post('/api/settings/test-pushover')
        assert resp.status_code == 400
        assert 'PUSHOVER_APP_TOKEN' in resp.get_json()['error']

    def test_success_with_valid_config(self, client):
        with patch('app.web_app.http_requests') as mock_req, \
             patch.dict(os.environ, {'PUSHOVER_APP_TOKEN': 'tok', 'PUSHOVER_USER_KEY': 'usr'}):
            mock_resp = MagicMock()
            mock_req.post.return_value = mock_resp
            resp = client.post('/api/settings/test-pushover')
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    def test_returns_500_on_pushover_error(self, client):
        with patch('app.web_app.http_requests') as mock_req, \
             patch.dict(os.environ, {'PUSHOVER_APP_TOKEN': 'tok', 'PUSHOVER_USER_KEY': 'usr'}):
            mock_req.post.side_effect = Exception('Network error')
            resp = client.post('/api/settings/test-pushover')
        assert resp.status_code == 500
