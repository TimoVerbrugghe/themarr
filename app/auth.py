"""Authentication and authorization helpers."""

import logging
import os
import hmac
import threading
import time
from flask import current_app, request, session, jsonify

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Login rate limiter — simple in-memory sliding-window counter.
# Prevents brute-force attacks on the /api/auth/login endpoint.
# ---------------------------------------------------------------------------
_login_attempt_lock = threading.Lock()
_login_attempts: dict[str, list[float]] = {}  # {remote_addr: [timestamp, ...]}
_LOGIN_RATE_LIMIT_WINDOW = 300  # 5-minute sliding window
_LOGIN_RATE_LIMIT_MAX = 20      # max login attempts within the window
_MAX_TRACKED_IPS = 10_000       # evict expired entries when the dict reaches this size


def _is_login_rate_limited(remote_addr: str) -> bool:
    """Return True when *remote_addr* has exceeded the login attempt rate limit.

    Uses a sliding-window in-memory counter keyed on the client IP address.
    Old timestamps outside the window are discarded on each call so memory
    usage stays bounded.  When the total number of tracked IPs reaches
    _MAX_TRACKED_IPS, all fully-expired entries are evicted to prevent
    unbounded growth under a distributed source-IP attack.

    Automatically disabled when Flask TESTING mode is active so the unit
    test suite is not affected.
    """
    if current_app.config.get('TESTING'):
        return False
    if not remote_addr:
        logger.warning('Login attempt received with no remote address; skipping rate-limit check')
        return False
    now = time.time()
    window_start = now - _LOGIN_RATE_LIMIT_WINDOW
    with _login_attempt_lock:
        # Evict fully-expired entries when the dict is large to bound memory.
        if len(_login_attempts) >= _MAX_TRACKED_IPS:
            expired = [ip for ip, ts in _login_attempts.items()
                       if not any(t > window_start for t in ts)]
            for ip in expired:
                del _login_attempts[ip]

        prior = [t for t in _login_attempts.get(remote_addr, []) if t > window_start]
        if len(prior) >= _LOGIN_RATE_LIMIT_MAX:
            # Do NOT record this attempt — the window must be allowed to expire
            # once the client stops sending requests.
            _login_attempts[remote_addr] = prior
            return True
        prior.append(now)
        _login_attempts[remote_addr] = prior
        return False


def _log_generated_api_key_warning():
    """Warn when using an auto-generated API key without logging the secret."""
    logger.warning(
        'API_KEY is not set; a one-time startup API key was generated. '
        'Open the Settings page in the web app after signing in to view it, '
        'or set API_KEY to a stable value to avoid rotation on restart.',
    )


def _auth_disabled():
    """Return True when DISABLE_AUTH=true (authentication bypass mode)."""
    return (os.getenv('DISABLE_AUTH') or '').strip().lower() in {'true', '1', 'yes'}


def _get_ui_credentials():
    """Return (username, password) from AUTH_USERNAME / AUTH_PASSWORD env vars."""
    username = (os.getenv('AUTH_USERNAME') or '').strip()
    password = (os.getenv('AUTH_PASSWORD') or '').strip()
    return username, password


def _credentials_auth_configured():
    """Return True when both AUTH_USERNAME and AUTH_PASSWORD are set."""
    username, password = _get_ui_credentials()
    return bool(username and password)


def _ui_auth_misconfigured():
    """Return True when UI auth is enabled but credentials are missing."""
    return not _auth_disabled() and not _credentials_auth_configured()


def _ui_auth_warning_message():
    """Return warning text shown when UI auth credentials are not configured."""
    return (
        'Web UI authentication is enabled but AUTH_USERNAME/AUTH_PASSWORD are not both set. '
        'Set both variables, or set DISABLE_AUTH=true only when a trusted reverse proxy already enforces authentication.'
    )


def _get_auth_mode():
    """Return the active authentication mode string.

    Returns:
        'disabled'      – DISABLE_AUTH=true; no credentials required.
        'credentials'   – AUTH_USERNAME + AUTH_PASSWORD are both set; login form shown.
        'misconfigured' – UI auth enabled but credentials are missing.
    """
    if _auth_disabled():
        return 'disabled'
    if _credentials_auth_configured():
        return 'credentials'
    return 'misconfigured'


def _parse_api_key():
    """Extract API key from supported auth headers."""
    header_key = (request.headers.get('X-Themarr-Api-Key') or '').strip()
    if header_key:
        return header_key
    auth_header = (request.headers.get('Authorization') or '').strip()
    if auth_header.startswith('Bearer '):
        return auth_header[7:].strip()
    return ''


def _get_api_key(generated_key=None):
    """Return configured API key or generated fallback key."""
    configured_key = (os.getenv('API_KEY') or '').strip()
    if configured_key:
        return configured_key, False
    if generated_key:
        return generated_key, True
    return '', False


def _check_api_request_auth(generated_key=None):
    """Validate API key auth for protected API routes.

    Accepts either a valid Flask session (established via POST /api/auth/login)
    or the API key supplied in the X-Themarr-Api-Key / Authorization header.
    When DISABLE_AUTH=true, all requests are allowed without credentials.
    """
    if _auth_disabled():
        return None
    if session.get('authenticated'):
        return None
    expected_key, _ = _get_api_key(generated_key)
    provided_key = _parse_api_key()
    if provided_key and hmac.compare_digest(provided_key, expected_key):
        return None
    return jsonify({'error': 'Unauthorized API request'}), 401


def _check_webhook_basic_auth():
    """Validate optional webhook Basic Auth credentials."""
    expected_username = (os.getenv('WEBHOOK_USERNAME') or '').strip()
    expected_password = (os.getenv('WEBHOOK_PASSWORD') or '').strip()
    if not expected_username and not expected_password:
        return None
    if not expected_username or not expected_password:
        logger.error(
            'Webhook Basic Auth is partially configured (only one of '
            'WEBHOOK_USERNAME / WEBHOOK_PASSWORD is set); rejecting all '
            'webhook requests until both are set or both are cleared.'
        )
        return jsonify({'error': 'Webhook authentication misconfigured on server'}), 503
    auth = request.authorization
    if not auth:
        return jsonify({'error': 'Webhook authentication required'}), 401
    username_ok = hmac.compare_digest(auth.username or '', expected_username)
    password_ok = hmac.compare_digest(auth.password or '', expected_password)
    if username_ok and password_ok:
        return None
    return jsonify({'error': 'Invalid webhook credentials'}), 401


def _get_settings_env_values(generated_api_key=None):
    """Return current raw environment values shown in the Settings page."""
    settings_vars = (
        'PLEX_URL',
        'PLEX_TOKEN',
        'JELLYFIN_URL',
        'JELLYFIN_API_KEY',
        'JELLYFIN_USER_ID',
        'DEFAULT_THEME',
        'DEFAULT_VIEW',
        'FLASK_DEBUG',
        'API_KEY',
        'AUTH_USERNAME',
        'AUTH_PASSWORD',
        'DISABLE_AUTH',
        'PUSHOVER_APP_TOKEN',
        'PUSHOVER_USER_KEY',
        'NOTIFY_ON_WEBHOOK_DOWNLOAD',
        'NOTIFY_ON_WEBHOOK_FAILURE',
        'NOTIFY_ON_UI_DOWNLOAD',
        'WEBHOOK_USERNAME',
        'WEBHOOK_PASSWORD',
        'PLEX_RETRY_ATTEMPTS',
        'PLEX_RETRY_DELAY',
    )
    defaults_when_unset = {
        'JELLYFIN_USER_ID': 'first user',
        'API_KEY': generated_api_key or '',
        'NOTIFY_ON_WEBHOOK_DOWNLOAD': 'true',
        'NOTIFY_ON_WEBHOOK_FAILURE': 'true',
        'NOTIFY_ON_UI_DOWNLOAD': 'true',
    }
    values = {}
    for key in settings_vars:
        raw_value = (os.getenv(key) or '').strip()
        if not raw_value and key in defaults_when_unset:
            values[key] = defaults_when_unset[key]
        else:
            values[key] = raw_value
    return values
