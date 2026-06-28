/* ============================================================
   Themarr - Frontend Application
   ============================================================ */

// ============================================================
// SVG icon constants (neutral currentColor)
// ============================================================
const ICON_PLEX = `<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M11.916 0C5.333 0 0 5.333 0 11.916s5.333 11.916 11.916 11.916 11.916-5.333 11.916-11.916S18.499 0 11.916 0zm1.501 16.14L9.143 12l4.274-4.14 1.263.865-3.25 3.275 3.25 3.275z"/></svg>`;
const ICON_YOUTUBE = `<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/></svg>`;
const ICON_THEMERRDB = `<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8zm3.5-9c.83 0 1.5-.67 1.5-1.5S16.33 8 15.5 8 14 8.67 14 9.5s.67 1.5 1.5 1.5zm-7 0c.83 0 1.5-.67 1.5-1.5S9.33 8 8.5 8 7 8.67 7 9.5 7.67 11 8.5 11zm3.5 6.5c2.33 0 4.31-1.46 5.11-3.5H6.89c.8 2.04 2.78 3.5 5.11 3.5z"/></svg>`;
const ICON_UPLOAD = `<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M9 16h6v-6h4l-7-7-7 7h4zm-4 2h14v2H5z"/></svg>`;
const ICON_PLAY = `<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>`;
const ICON_PAUSE = `<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>`;
const ICON_COPY = `<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M16 1H4a2 2 0 0 0-2 2v12h2V3h12V1zm4 4H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2zm0 16H8V7h12v14z"/></svg>`;

// Action button label HTML: [icon, grid-label, list-label]
const BTN_PLEX      = [ICON_PLEX,      `${ICON_PLEX} Plex`,       `${ICON_PLEX} Download from Plex`];
const BTN_COPY      = [ICON_COPY,      `${ICON_COPY} Copy Theme`, `${ICON_COPY} Copy theme from…`];
const BTN_YOUTUBE   = [ICON_YOUTUBE,   `${ICON_YOUTUBE} YouTube`, `${ICON_YOUTUBE} Download from YouTube`];
const BTN_THEMERRDB = [ICON_THEMERRDB, `${ICON_THEMERRDB} ThemerrDB`, `${ICON_THEMERRDB} Download from ThemerrDB`];
const BTN_UPLOAD    = [ICON_UPLOAD,    `${ICON_UPLOAD} Upload`,   `${ICON_UPLOAD} Upload custom theme`];


// Capture server-rendered defaults before JS mutates the DOM
const SERVER_DEFAULT_THEME = document.documentElement.dataset.theme || 'dark';

// State
let currentLibraryId = null;
let currentLibraryProvider = null;
let currentItems = [];
let activeFilter = 'all';
let activeFilters = { theme: 'all', plex: false, themerrdb: false };
let activeItemContext = null;
// Default view: localStorage override → server default (data-default-view) → 'list'
const _serverDefaultView = document.documentElement.dataset.defaultView || 'list';
const _savedView = localStorage.getItem('themarr-view');
let currentView = (_savedView === 'grid' || _savedView === 'list') ? _savedView : _serverDefaultView;
const selectedItems = new Set();  // ratingKeys of currently selected items
const libraryCache = new Map();   // libraryId -> items[], cleared on theme changes

// Audio state (list-view inline preview)
let activeAudio = null;   // HTMLAudioElement currently playing
let activePlayBtn = null; // button element that triggered playback
const STARTUP_POLL_INTERVAL_MS = 1500;
let lastCompactActionMenuMode = null;
const ACTION_MENU_COLLAPSE_BREAKPOINT = 1200;
// API key is kept in memory only; it is never written to localStorage.
// The server returns it from the authenticated GET /api/settings/runtime endpoint.
let apiKey = '';
// Auth mode reported by /api/init: 'disabled' | 'credentials' | 'misconfigured'
let appAuthMode = 'misconfigured';
const NOT_AUTHENTICATED_TEXT = 'Not authenticated';
let plexConfigured = false;
// Remove any API key previously stored in localStorage by older versions of this app.
try { localStorage.removeItem('themarr-api-token'); } catch (_) { /* ignore */ }

function makeLibraryCacheKey(provider, libraryId) {
  return `${provider}:${libraryId}`;
}

function syncPlexFeatureVisibility() {
  const plexHeading = document.getElementById('plex-libraries-heading');
  const plexNav = document.getElementById('library-nav');
  const plexFilterOption = document.getElementById('filter-plex-option');
  const bulkBtn = document.getElementById('btn-bulk-download');
  const plexSourceBtn = document.getElementById('get-theme-btn-plex');
  const settingsPlexTestBtn = document.getElementById('settings-test-plex-btn');

  if (plexHeading) plexHeading.classList.toggle('hidden', !plexConfigured);
  if (plexNav) plexNav.classList.toggle('hidden', !plexConfigured);
  if (plexFilterOption) plexFilterOption.classList.toggle('hidden', !plexConfigured);
  if (bulkBtn) bulkBtn.classList.toggle('hidden', !plexConfigured);
  if (plexSourceBtn) plexSourceBtn.classList.toggle('hidden', !plexConfigured);
  if (settingsPlexTestBtn) settingsPlexTestBtn.classList.toggle('hidden', !plexConfigured);

  if (!plexConfigured) {
    activeFilters.plex = false;
    const plexCheck = document.getElementById('filter-plex-avail');
    if (plexCheck) plexCheck.checked = false;
  }
}

function copyApiKey() {
  const key = apiKey || '';
  if (!key) {
    showSettingsResult(false, '✗ Not logged in — paste the API key from the server logs and click Login first');
    return;
  }
  navigator.clipboard.writeText(key)
    .then(() => showSettingsResult(true, '✓ API key copied to clipboard'))
    .catch((err) => showSettingsResult(false, `✗ Failed to copy API key: ${err}`));
}

// ============================================================
// Login overlay helpers
// ============================================================

function showLoginOverlay(authMode) {
  const overlay = document.getElementById('login-overlay');
  if (!overlay) return;
  overlay.classList.remove('hidden');
  const credForm = document.getElementById('login-form-credentials');
  credForm && credForm.classList.remove('hidden');
  setLoginError('credentials', '');
  document.getElementById('login-username') && document.getElementById('login-username').focus();
}

function hideLoginOverlay() {
  const overlay = document.getElementById('login-overlay');
  if (overlay) overlay.classList.add('hidden');
}

function setLoginError(formType, msg) {
  const elId = 'login-error';
  const el = document.getElementById(elId);
  if (!el) return;
  if (msg) {
    el.textContent = msg;
    el.classList.remove('hidden');
  } else {
    el.classList.add('hidden');
  }
}

async function loginWithCredentials(event) {
  if (event) event.preventDefault();
  const usernameEl = document.getElementById('login-username');
  const passwordEl = document.getElementById('login-password');
  const submitBtn = document.getElementById('login-submit-btn');
  const username = (usernameEl ? usernameEl.value : '').trim();
  const password = (passwordEl ? passwordEl.value : '').trim();
  setLoginError('credentials', '');
  if (!username || !password) {
    setLoginError('credentials', 'Username and password are required.');
    return;
  }
  if (submitBtn) submitBtn.disabled = true;
  try {
    const resp = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!resp.ok) {
      const err = (await resp.json().catch(() => ({}))).error || 'Invalid username or password';
      setLoginError('credentials', err);
      return;
    }
    if (passwordEl) passwordEl.value = '';
    hideLoginOverlay();
    await postLoginInit();
  } catch (err) {
    setLoginError('credentials', `Login failed: ${err}`);
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
}

/** Called after successful login to resume the startup flow. */
async function postLoginInit() {
  await refreshApiKey();
  await waitForStartupHydration();
  loadLibraries();
}

// ============================================================
// Settings page auth actions (inline API key login / logout)
// ============================================================

async function logoutSession() {
  try {
    await fetch('/api/auth/logout', { method: 'POST' });
  } catch (_) { /* ignore network errors on logout */ }
  apiKey = '';
  const keyEl = document.getElementById('runtime-api-key');
  const sourceEl = document.getElementById('runtime-api-key-source');
  if (keyEl) keyEl.textContent = NOT_AUTHENTICATED_TEXT;
  if (sourceEl) sourceEl.textContent = NOT_AUTHENTICATED_TEXT;
  showSettingsResult(true, '✓ Logged out');
  // If auth is required, redirect back to login overlay
  if (appAuthMode !== 'disabled') {
    showLoginOverlay(appAuthMode);
  }
}

function logoutFromSidebar(event) {
  event.preventDefault();
  logoutSession();
}

function syncAuthNavVisibility() {
  const logoutNavItem = document.getElementById('logout-nav-item');
  if (!logoutNavItem) return;
  logoutNavItem.classList.toggle('hidden', appAuthMode === 'disabled');
}

async function loadSettingsRuntime() {
  try {
    const data = await apiGet('/api/settings/runtime');
    // Store API key in memory so header-based API calls keep working
    if (data.api_key) apiKey = data.api_key;
    const keyEl = document.getElementById('runtime-api-key');
    const sourceEl = document.getElementById('runtime-api-key-source');
    if (keyEl) keyEl.value = data.api_key || 'Not configured';
    if (sourceEl) {
      sourceEl.textContent = data.api_key_configured ? 'from API_KEY env variable' : 'auto-generated at startup';
    }
    populateSettingsEnvValues(data.env_values || {});
  } catch (err) {
    const keyEl = document.getElementById('runtime-api-key');
    const sourceEl = document.getElementById('runtime-api-key-source');
    if (keyEl) keyEl.value = NOT_AUTHENTICATED_TEXT;
    if (sourceEl) sourceEl.textContent = NOT_AUTHENTICATED_TEXT;
    populateSettingsEnvValues({});
  }
}

function populateSettingsEnvValues(values) {
  document.querySelectorAll('[data-env-current]').forEach((cell) => {
    const envKey = cell.dataset.envCurrent;
    const value = values[envKey];
    cell.textContent = value ? value : '—';
  });
}

async function refreshApiKey() {
  // Try to retrieve the API key from an existing session; silently ignore 401
  // (user will need to enter the API key via the settings page login form).
  try {
    const data = await apiGet('/api/settings/runtime');
    if (data.api_key) apiKey = data.api_key;
  } catch (_) { /* not yet authenticated — ignore */ }
}

function libraryItemsPath(provider, libraryId) {
  if (provider === 'plex') return `/api/libraries/${libraryId}/items`;
  return `/api/libraries/${provider}/${encodeURIComponent(libraryId)}/items`;
}

function libraryItemsPagePath(provider, libraryId, page, pageSize = 200) {
  const basePath = libraryItemsPath(provider, libraryId);
  const params = new URLSearchParams({
    paginated: 'true',
    page: String(page),
    page_size: String(pageSize),
  });
  return `${basePath}?${params.toString()}`;
}

async function fetchLibraryItems(provider, libraryId) {
  const allItems = [];
  let page = 1;

  while (true) {
    const payload = await apiGet(libraryItemsPagePath(provider, libraryId, page));
    const pageItems = Array.isArray(payload?.items) ? payload.items : [];
    const hasMore = Boolean(payload?.pagination?.has_more);
    allItems.push(...pageItems);
    if (!hasMore) break;
    page += 1;
  }
  return allItems;
}

function providerPosterPath(item) {
  if ((item.provider || 'plex') === 'plex') return `/api/poster/${item.ratingKey}`;
  return `/api/poster/${item.provider}/${encodeURIComponent(item.id || item.ratingKey)}`;
}

function providerItemThemePath(item) {
  const provider = item.provider || 'plex';
  const itemId = encodeURIComponent(item.id || item.ratingKey);
  return `/api/items/${provider}/${itemId}/theme`;
}

function providerItemActionPath(item, suffix = '') {
  const provider = item.provider || 'plex';
  const itemId = encodeURIComponent(item.id || item.ratingKey);
  return `/api/items/${provider}/${itemId}${suffix}`;
}

function itemSelectionKey(item) {
  return `${item.provider || 'plex'}:${item.id || item.ratingKey}`;
}

function selectedKeyFor(provider, itemId) {
  return `${provider}:${itemId}`;
}

function isCompactActionMenuMode() {
  return window.matchMedia(`(max-width: ${ACTION_MENU_COLLAPSE_BREAKPOINT}px)`).matches;
}

function handleActionMenuBreakpointChange() {
  const compactMode = isCompactActionMenuMode();
  if (compactMode === lastCompactActionMenuMode) return;
  lastCompactActionMenuMode = compactMode;
  closeAllRowActionMenus();
  if (currentView === 'list' && currentItems.length) {
    renderItems(currentItems);
  }
}

// ============================================================
// Init
// ============================================================
document.addEventListener('DOMContentLoaded', async () => {
  initTheme();
  setView(currentView);  // apply default view and sync button active states
  checkConnectionStatuses();
  document.addEventListener('click', () => closeAllRowActionMenus());
  lastCompactActionMenuMode = isCompactActionMenuMode();
  window.addEventListener('resize', handleActionMenuBreakpointChange);

  // Allow pressing Enter in the YouTube search box to trigger search
  document.getElementById('youtube-search-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') doYoutubeSearch();
  });

  document.getElementById('download-overwrite-check').addEventListener('change', () => {
    _syncOverwriteActionButton('btn-confirm-download', 'download-overwrite-check');
  });
  document.getElementById('upload-overwrite-check').addEventListener('change', () => {
    _syncOverwriteActionButton('btn-confirm-upload', 'upload-overwrite-check');
  });
  document.getElementById('youtube-overwrite-check').addEventListener('change', () => {
    _syncOverwriteActionButton('btn-confirm-youtube', 'youtube-overwrite-check');
  });
  document.getElementById('themerrdb-overwrite-check').addEventListener('change', () => {
    _syncOverwriteActionButton('btn-confirm-themerrdb', 'themerrdb-overwrite-check');
  });
  document.getElementById('copy-overwrite-check').addEventListener('change', () => {
    syncCopyThemeConfirmButton();
  });
  document.getElementById('copy-theme-source-library').addEventListener('change', async (e) => {
    await populateCopyThemeSources(e.target.value);
  });
  document.getElementById('copy-theme-source-item').addEventListener('change', () => {
    syncCopyThemeConfirmButton();
  });
  const previewAudio = document.getElementById('preview-audio');
  previewAudio.addEventListener('error', () => {
    const previewError = document.getElementById('preview-audio-error');
    if (previewAudio.src) {
      previewError.textContent = 'Preview stream could not be loaded. Check connection and item availability.';
      previewError.classList.remove('hidden');
    }
  });

  // Check auth state before proceeding — show login overlay if required.
  // Default to auth required / unauthenticated so a /api/init network failure
  // fails secure rather than granting unintended access.
  let initData = { auth_required: true, authenticated: false, auth_mode: 'misconfigured' };
  try {
    const resp = await fetch('/api/init');
    if (resp.ok) initData = await resp.json();
  } catch (err) {
    console.error('Failed to contact /api/init - showing login screen as a safe fallback:', err);
  }

  appAuthMode = initData.auth_mode || 'misconfigured';
  syncAuthNavVisibility();

  if (initData.auth_required && !initData.authenticated) {
    showLoginOverlay(appAuthMode);
    // Startup and library loading happen in postLoginInit() after the user signs in.
    return;
  }

  // Already authenticated (or auth disabled) — proceed with normal startup.
  try {
    await refreshApiKey();
  } catch (err) {
    console.error('Failed to load API key', err);
  }
  await waitForStartupHydration();
  loadLibraries();
});

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForStartupHydration() {
  const overlay = document.getElementById('startup-overlay');
  const message = document.getElementById('startup-overlay-message');

  overlay.classList.remove('hidden');
  while (true) {
    try {
      const data = await apiGet('/api/cache/status');
      const total = Number(data.sections_total || 0);
      const completed = Number(data.sections_completed || 0);

      if (data.ready) {
        overlay.classList.add('hidden');
        return;
      }

      if (total > 0) {
        message.textContent = `Preparing theme state (${completed}/${total} libraries complete)…`;
      } else {
        message.textContent = 'Connecting to Plex and scanning theme files…';
      }
    } catch (err) {
      message.textContent = `Waiting for startup cache… (${String(err)})`;
    }

    await sleep(STARTUP_POLL_INTERVAL_MS);
  }
}

function _syncOverwriteActionButton(buttonId, checkboxId, overwriteRequired = true) {
  const button = document.getElementById(buttonId);
  if (!button) return;
  if (!overwriteRequired) {
    button.disabled = false;
    return;
  }
  const checkbox = document.getElementById(checkboxId);
  button.disabled = !(checkbox && checkbox.checked);
}

// ============================================================
// Theme
// ============================================================
function initTheme() {
  const saved = localStorage.getItem('themarr-theme');
  const savedAgainst = localStorage.getItem('themarr-theme-default');
  // If the server default has changed since the user last toggled (or no
  // toggle was ever recorded), discard the stale preference and follow
  // the server default.
  if (saved && savedAgainst !== SERVER_DEFAULT_THEME) {
    localStorage.removeItem('themarr-theme');
    localStorage.removeItem('themarr-theme-default');
    applyTheme(SERVER_DEFAULT_THEME);
  } else if (saved === 'light' || saved === 'dark') {
    applyTheme(saved);
  } else {
    applyTheme(SERVER_DEFAULT_THEME);
  }
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const btn = document.getElementById('theme-toggle');
  if (btn) {
    btn.textContent = theme === 'dark' ? '☀️' : '🌙';
    btn.title = theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme';
  }
}

function toggleTheme() {
  const current = document.documentElement.dataset.theme || 'dark';
  const next = current === 'dark' ? 'light' : 'dark';
  localStorage.setItem('themarr-theme', next);
  localStorage.setItem('themarr-theme-default', SERVER_DEFAULT_THEME);
  applyTheme(next);
}

// ============================================================
// Connection status
// ============================================================
function setProviderConnectionStatus(providerName, status, statusElementId, statusTextId) {
  const el = document.getElementById(statusElementId);
  const txt = document.getElementById(statusTextId);
  if (!el || !txt) return;

  const shouldShow = Boolean(status?.url_configured);
  el.style.display = shouldShow ? 'flex' : 'none';
  if (!shouldShow) return;

  if (status?.connected) {
    el.className = 'plex-status plex-status--connected';
    txt.textContent = `${providerName} Connected (${status.server_name || 'Unknown'})`;
  } else {
    el.className = 'plex-status plex-status--error';
    txt.textContent = 'Not connected';
  }
}

async function checkConnectionStatuses() {
  const plexFallback = { url_configured: false, connected: false, server_name: null };
  const jellyfinFallback = { url_configured: false, connected: false, server_name: null };
  try {
    const data = await apiGet('/api/status');
    const plexStatus = data.plex || { ...plexFallback, connected: Boolean(data.connected), server_name: data.server_name };
    const jellyfinStatus = data.jellyfin || jellyfinFallback;
    plexConfigured = Boolean(plexStatus.url_configured);
    syncPlexFeatureVisibility();

    setProviderConnectionStatus('Plex', plexStatus, 'plex-status', 'plex-status-text');
    setProviderConnectionStatus('Jellyfin', jellyfinStatus, 'jellyfin-status', 'jellyfin-status-text');
  } catch {
    plexConfigured = false;
    syncPlexFeatureVisibility();
    setProviderConnectionStatus('Plex', plexFallback, 'plex-status', 'plex-status-text');
    setProviderConnectionStatus('Jellyfin', jellyfinFallback, 'jellyfin-status', 'jellyfin-status-text');
    const plexEl = document.getElementById('plex-status');
    const plexTxt = document.getElementById('plex-status-text');
    if (plexEl && plexTxt) {
      plexEl.className = 'plex-status plex-status--error';
      plexTxt.textContent = 'Error';
    }
  }
}

// ============================================================
// Libraries
// ============================================================
async function loadLibraries() {
  const plexNav = document.getElementById('library-nav');
  const jellyfinNav = document.getElementById('jellyfin-library-nav');
  try {
    const libraries = await apiGet('/api/libraries');
    if (!libraries.length) {
      plexNav.innerHTML = '<div class="sidebar-loading">No Plex libraries found</div>';
      jellyfinNav.innerHTML = '<div class="sidebar-loading">No Jellyfin libraries found</div>';
      return;
    }

    const plexLibraries = libraries.filter((lib) => (lib.provider || 'plex') === 'plex');
    const jellyfinLibraries = libraries.filter((lib) => (lib.provider || 'plex') === 'jellyfin');
    plexNav.innerHTML = '';
    jellyfinNav.innerHTML = '';

    const renderLibraryList = (targetNav, libs, provider) => {
      if (!libs.length) {
        targetNav.innerHTML = `<div class="sidebar-loading">No ${provider === 'plex' ? 'Plex' : 'Jellyfin'} libraries found</div>`;
        return;
      }
      for (const lib of libs) {
        const icon = lib.type === 'show' ? '📺' : '🎬';
        const item = document.createElement('a');
        item.className = 'library-nav-item';
        item.dataset.id = String(lib.id);
        item.dataset.provider = provider;
        item.href = '#';
        item.innerHTML = `
          <span class="library-nav-icon">${icon}</span>
          <span class="library-nav-name">${escHtml(lib.title)}</span>
          <span class="library-nav-count">${lib.totalSize || ''}</span>
        `;
        item.addEventListener('click', (event) => {
          event.preventDefault();
          selectLibrary(provider, String(lib.id), lib.title);
        });
        targetNav.appendChild(item);
      }
    };

    renderLibraryList(plexNav, plexLibraries, 'plex');
    renderLibraryList(jellyfinNav, jellyfinLibraries, 'jellyfin');
  } catch (err) {
    const msg = `<div class="sidebar-loading">Error: ${escHtml(String(err))}</div>`;
    plexNav.innerHTML = msg;
    jellyfinNav.innerHTML = msg;
  }
}

async function selectLibrary(provider, id, title) {
  currentLibraryProvider = provider;
  currentLibraryId = id;
  activeFilter = 'all';
  activeFilters = { theme: 'all', plex: false, themerrdb: false };
  // Reset filter panel UI
  const themeRadios = document.querySelectorAll('input[name="filter-theme"]');
  themeRadios.forEach((r) => { r.checked = r.value === 'all'; });
  const plexCheck = document.getElementById('filter-plex-avail');
  const themerrdbCheck = document.getElementById('filter-themerrdb-avail');
  if (plexCheck) plexCheck.checked = false;
  if (themerrdbCheck) themerrdbCheck.checked = false;
  updateFilterButtonState();
  const bulkBtn = document.getElementById('btn-bulk-download');
  if (bulkBtn) {
    bulkBtn.disabled = provider !== 'plex';
    bulkBtn.textContent = provider === 'plex' ? '↓ Download Themes' : '↓ Download from Plex (Plex only)';
  }

  document.querySelectorAll('.library-nav-item').forEach((el) => el.classList.remove('active'));
  const navItem = Array.from(document.querySelectorAll('.library-nav-item')).find(
    (el) => el.dataset.provider === provider && el.dataset.id === String(id),
  );
  if (navItem) navItem.classList.add('active');

  document.getElementById('welcome-screen').classList.add('hidden');
  document.getElementById('settings-view').classList.add('hidden');
  document.getElementById('library-view').classList.remove('hidden');
  document.getElementById('library-title').textContent = title;
  document.getElementById('library-stats').innerHTML = '';
  document.getElementById('items-grid').innerHTML = '';
  document.getElementById('search-input').value = '';

  // Clear selection when switching libraries
  selectedItems.clear();
  updateBulkBar();

  // Stop any playing audio
  stopInlineAudio();

  const cacheKey = makeLibraryCacheKey(provider, id);

  // Serve from cache if available, fetch otherwise
  if (libraryCache.has(cacheKey)) {
    document.getElementById('items-loading').classList.add('hidden');
    const items = libraryCache.get(cacheKey);
    currentItems = items;
    renderItems(items);
    updateStats(items);
    return;
  }

  document.getElementById('items-loading').classList.remove('hidden');
  try {
    const items = await fetchLibraryItems(provider, id);
    libraryCache.set(cacheKey, items);
    currentItems = items;
    document.getElementById('items-loading').classList.add('hidden');
    renderItems(items);
    updateStats(items);
  } catch (err) {
    document.getElementById('items-loading').classList.add('hidden');
    showToast('error', `Failed to load library: ${err}`);
  }
}

// ============================================================
// Items Rendering
// ============================================================
function renderItems(items) {
  const grid = document.getElementById('items-grid');
  grid.className = currentView === 'list' ? 'items-list' : 'items-grid';
  grid.innerHTML = '';

  const searchVal = (document.getElementById('search-input').value || '').toLowerCase();
  const filtered = items.filter((item) => {
    const matchSearch = !searchVal || item.title.toLowerCase().includes(searchVal);
    // Theme status filter
    let themeOk = true;
    if (activeFilters.theme === 'has_theme') themeOk = item.has_local_theme;
    if (activeFilters.theme === 'no_theme') themeOk = !item.has_local_theme;
    if (!themeOk) return false;
    // Source availability filter (OR logic: show items matching at least one checked source)
    if (!activeFilters.plex && !activeFilters.themerrdb) return matchSearch;
    const sourceOk = (activeFilters.plex && item.has_plex_theme) ||
                     (activeFilters.themerrdb && item.has_themerrdb_theme);
    return matchSearch && sourceOk;
  });

  if (!filtered.length) {
    grid.innerHTML = '<div class="items-loading" style="grid-column:1/-1"><span>No items match your filter.</span></div>';
    return;
  }

  filtered.forEach((item) => grid.appendChild(createItem(item)));
}

function createItemCard(item) {
  const card = document.createElement('div');
  const selectionKey = itemSelectionKey(item);
  const isSelected = selectedItems.has(selectionKey);
  card.className = `item-card${item.has_local_theme ? ' has-theme' : ''}${isSelected ? ' selected' : ''}`;
  card.id = `card-${selectionKey}`;
  card.dataset.selectionKey = selectionKey;

  const poster = document.createElement('div');
  poster.className = 'item-poster';

  // Selection checkbox (top-left of poster)
  const selectWrap = document.createElement('div');
  selectWrap.className = 'item-select-wrap';
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.checked = isSelected;
  checkbox.title = 'Select for bulk action';
  checkbox.addEventListener('change', (e) => {
    e.stopPropagation();
    toggleItemSelection(selectionKey, e.target.checked, card);
  });
  selectWrap.appendChild(checkbox);
  poster.appendChild(selectWrap);

  const image = document.createElement('img');
  image.src = providerPosterPath(item);
  image.alt = item.title;
  image.loading = 'lazy';
  image.decoding = 'async';
  poster.classList.add('poster-loading-state');

  const placeholder = document.createElement('div');
  placeholder.innerHTML = posterPlaceholder(item.type, item.title);
  const placeholderEl = placeholder.firstChild;
  poster.appendChild(placeholderEl);

  const loadingOverlay = document.createElement('div');
  loadingOverlay.className = 'poster-loading';
  loadingOverlay.innerHTML = '<div class="spinner"></div>';
  poster.appendChild(loadingOverlay);

  image.onload = () => {
    poster.classList.remove('poster-loading-state');
    poster.classList.add('poster-loaded');
  };
  image.onerror = () => {
    poster.classList.remove('poster-loading-state');
    image.remove();
    loadingOverlay.remove();
  };
  poster.appendChild(image);

  const badge = document.createElement('div');
  badge.className = `theme-badge ${item.has_local_theme ? 'theme-badge-has' : 'theme-badge-none'}`;
  badge.title = item.has_local_theme ? 'Has theme' : 'No theme';
  badge.textContent = item.has_local_theme ? '🎵' : '○';
  poster.appendChild(badge);

  const body = document.createElement('div');
  body.className = 'item-body';

  const title = document.createElement('div');
  title.className = 'item-title';
  title.title = item.title;
  title.textContent = item.title;
  body.appendChild(title);

  const year = document.createElement('div');
  year.className = 'item-year';
  year.textContent = item.year || '';
  body.appendChild(year);

  if (item.has_local_theme) {
    const playBtn = document.createElement('button');
    playBtn.className = 'action-btn-play-inline action-btn-play-wide';
    playBtn.type = 'button';
    playBtn.title = 'Preview theme';
    playBtn.innerHTML = ICON_PLAY;
    playBtn.addEventListener('click', () => toggleInlineAudio(item, playBtn));
    body.appendChild(playBtn);
  }

  const actions = document.createElement('div');
  actions.className = 'item-actions';

  // Single "Get Theme" button with availability indicators
  const getThemeBtn = createGetThemeButton(item, 'grid');
  getThemeBtn.addEventListener('click', () => openGetThemeModal(item));
  actions.appendChild(getThemeBtn);

  const copyButton = createActionButton('action-btn action-btn-copy', 'Copy theme from another item', BTN_COPY[1]);
  copyButton.addEventListener('click', () => openCopyThemeModal(item));
  actions.appendChild(copyButton);

  const uploadButton = createActionButton('action-btn action-btn-upload', 'Upload custom theme', BTN_UPLOAD[1]);
  uploadButton.addEventListener('click', () => openUploadModal(item));
  actions.appendChild(uploadButton);

  const deleteButton = createActionButton('action-btn action-btn-delete', 'Delete theme', '🗑 Delete');
  deleteButton.disabled = !item.has_local_theme;
  deleteButton.addEventListener('click', () => openDeleteModal(item));
  actions.appendChild(deleteButton);

  body.appendChild(actions);
  card.appendChild(poster);
  card.appendChild(body);
  return card;
}

function createActionButton(className, title, html) {
  const button = document.createElement('button');
  button.className = className;
  button.title = title;
  button.type = 'button';
  button.innerHTML = html;
  return button;
}

function createGetThemeButton(item, view) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'action-btn action-btn-get-theme';
  btn.title = 'Get theme';

  const labelSpan = document.createElement('span');
  labelSpan.textContent = 'Get Theme';
  btn.appendChild(labelSpan);

  // Availability indicator images
  const indicators = document.createElement('span');
  indicators.className = 'get-theme-indicators';

  if (plexConfigured) {
    const plexImg = document.createElement('img');
    plexImg.src = 'https://cdn.jsdelivr.net/gh/selfhst/icons@main/svg/plex.svg';
    plexImg.alt = '';
    plexImg.className = 'get-theme-indicator' + (item.has_plex_theme ? '' : ' unavailable');
    if (item.has_plex_theme) {
      plexImg.title = item.plex_theme_source_unverified
        ? 'Plex reports a theme, but source is unverified because a local theme.mp3 already exists'
        : 'Plex theme available';
    } else {
      plexImg.title = 'No Plex theme';
    }
    indicators.appendChild(plexImg);
  }

  const tdbImg = document.createElement('img');
  tdbImg.src = 'https://app.lizardbyte.dev/ThemerrDB/assets/img/navbar-avatar.png';
  tdbImg.alt = '';
  tdbImg.className = 'get-theme-indicator' + (item.has_themerrdb_theme ? '' : ' unavailable');
  tdbImg.title = item.has_themerrdb_theme ? 'ThemerrDB theme available' : 'No ThemerrDB theme';
  indicators.appendChild(tdbImg);

  btn.appendChild(indicators);
  return btn;
}

function posterPlaceholder(type, title) {
  const icon = type === 'show' ? '📺' : '🎬';
  return `<div class="poster-placeholder">${icon}<span>${escHtml(title)}</span></div>`;
}

function createItem(item) {
  return currentView === 'list' ? createItemRow(item) : createItemCard(item);
}

function createItemRow(item) {
  const row = document.createElement('div');
  const selectionKey = itemSelectionKey(item);
  const isSelected = selectedItems.has(selectionKey);
  row.className = `item-card item-row${item.has_local_theme ? ' has-theme' : ''}${isSelected ? ' selected' : ''}`;
  row.id = `card-${selectionKey}`;
  row.dataset.selectionKey = selectionKey;

  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.checked = isSelected;
  checkbox.title = 'Select for bulk action';
  checkbox.addEventListener('change', (e) => {
    e.stopPropagation();
    toggleItemSelection(selectionKey, e.target.checked, row);
  });
  row.appendChild(checkbox);

  const badge = document.createElement('div');
  badge.className = `theme-badge-inline ${item.has_local_theme ? 'theme-badge-has' : 'theme-badge-none'}`;
  badge.title = item.has_local_theme ? 'Has theme' : 'No theme';
  badge.textContent = item.has_local_theme ? '🎵' : '○';
  row.appendChild(badge);

  const info = document.createElement('div');
  info.className = 'item-row-info';
  const titleEl = document.createElement('div');
  titleEl.className = 'item-title';
  titleEl.title = item.title;
  titleEl.textContent = item.title;
  const yearEl = document.createElement('div');
  yearEl.className = 'item-year';
  yearEl.textContent = item.year || '';
  info.appendChild(titleEl);
  info.appendChild(yearEl);
  row.appendChild(info);

  // Inline play/pause button (only when a local theme exists)
  if (item.has_local_theme) {
    const playBtn = document.createElement('button');
    playBtn.className = 'action-btn-play-inline';
    playBtn.type = 'button';
    playBtn.title = 'Preview theme';
    playBtn.innerHTML = ICON_PLAY;
    playBtn.addEventListener('click', () => toggleInlineAudio(item, playBtn));
    row.appendChild(playBtn);
  }

  const actions = document.createElement('div');
  actions.className = 'item-actions item-actions-row item-actions-disclosure';

  const actionMenuToggle = document.createElement('button');
  actionMenuToggle.className = 'action-btn action-btn-menu-toggle';
  actionMenuToggle.type = 'button';
  actionMenuToggle.title = 'Actions';
  actionMenuToggle.textContent = 'Actions';
  actionMenuToggle.addEventListener('click', (e) => {
    e.stopPropagation();
    const compactMode = isCompactActionMenuMode();
    const shouldOpen = compactMode ? actionMenu.style.display === 'none' || actionMenu.style.display === '' : !actions.classList.contains('open');
    closeAllRowActionMenus(actions);
    if (shouldOpen) {
      actions.classList.add('open');
      if (compactMode) actionMenu.style.display = 'flex';
    } else {
      actions.classList.remove('open');
      if (compactMode) actionMenu.style.display = 'none';
    }
  });
  actions.appendChild(actionMenuToggle);

  const actionMenu = document.createElement('div');
  actionMenu.className = 'item-actions-menu';
  if (isCompactActionMenuMode()) {
    actionMenu.style.display = 'none';
  }
  actionMenu.addEventListener('click', (e) => e.stopPropagation());

  // Button order: Get Theme → Copy theme from → Upload → Delete
  const getThemeBtn = createGetThemeButton(item, 'list');
  getThemeBtn.addEventListener('click', () => {
    closeAllRowActionMenus();
    openGetThemeModal(item);
  });
  actionMenu.appendChild(getThemeBtn);

  const copyButton = createActionButton('action-btn action-btn-copy', 'Copy theme from another item', BTN_COPY[2]);
  copyButton.addEventListener('click', () => {
    closeAllRowActionMenus();
    openCopyThemeModal(item);
  });
  actionMenu.appendChild(copyButton);

  const uploadButton = createActionButton('action-btn action-btn-upload', 'Upload custom theme', BTN_UPLOAD[2]);
  uploadButton.addEventListener('click', () => {
    closeAllRowActionMenus();
    openUploadModal(item);
  });
  actionMenu.appendChild(uploadButton);

  const deleteButton = createActionButton('action-btn action-btn-delete', 'Delete theme', '🗑 Delete');
  deleteButton.disabled = !item.has_local_theme;
  deleteButton.addEventListener('click', () => {
    closeAllRowActionMenus();
    openDeleteModal(item);
  });
  actionMenu.appendChild(deleteButton);

  actions.appendChild(actionMenu);

  row.appendChild(actions);
  return row;
}

function closeAllRowActionMenus(except = null) {
  document.querySelectorAll('.item-actions-disclosure.open').forEach((menu) => {
    if (menu === except) return;
    menu.classList.remove('open');
    if (isCompactActionMenuMode()) {
      const panel = menu.querySelector('.item-actions-menu');
      if (panel) panel.style.display = 'none';
    }
  });
}

// ============================================================
// Inline audio preview (list view + grid cards)
// ============================================================
function toggleInlineAudio(item, btn) {
  const src = providerItemThemePath(item);

  // If this button is already the active one, toggle play/pause
  if (activePlayBtn === btn) {
    if (activeAudio && !activeAudio.paused) {
      activeAudio.pause();
      btn.innerHTML = ICON_PLAY;
      btn.classList.remove('playing');
    } else if (activeAudio) {
      activeAudio.play().catch(() => {});
      btn.innerHTML = ICON_PAUSE;
      btn.classList.add('playing');
    }
    return;
  }

  // Stop whatever is currently playing
  stopInlineAudio();

  // Show loading spinner immediately
  btn.innerHTML = '';
  btn.classList.add('loading');
  btn.disabled = true;

  // Create and play a new audio element
  const audio = new Audio(src);

  const onCanPlay = () => {
    btn.classList.remove('loading');
    btn.disabled = false;
    btn.innerHTML = ICON_PAUSE;
    btn.classList.add('playing');
  };
  audio.addEventListener('canplay', onCanPlay, { once: true });

  audio.addEventListener('ended', () => {
    btn.innerHTML = ICON_PLAY;
    btn.classList.remove('playing', 'loading');
    btn.disabled = false;
    activeAudio = null;
    activePlayBtn = null;
  });
  audio.addEventListener('pause', () => {
    if (activePlayBtn === btn) {
      btn.innerHTML = ICON_PLAY;
      btn.classList.remove('playing');
    }
  });
  audio.play().catch(() => {
    btn.classList.remove('loading', 'playing');
    btn.disabled = false;
    btn.innerHTML = ICON_PLAY;
  });

  activeAudio = audio;
  activePlayBtn = btn;
}

function stopInlineAudio() {
  if (activeAudio) {
    activeAudio.pause();
    activeAudio = null;
  }
  if (activePlayBtn) {
    activePlayBtn.innerHTML = ICON_PLAY;
    activePlayBtn.classList.remove('playing');
    activePlayBtn = null;
  }
}

// ============================================================
// View toggle
// ============================================================
function setView(mode) {
  currentView = mode;
  localStorage.setItem('themarr-view', mode);
  document.getElementById('view-btn-grid').classList.toggle('active', mode === 'grid');
  document.getElementById('view-btn-list').classList.toggle('active', mode === 'list');
  stopInlineAudio();
  renderItems(currentItems);
}

function updateStats(items) {
  const total = items.length;
  const withTheme = items.filter((item) => item.has_local_theme).length;
  const statsEl = document.getElementById('library-stats');
  statsEl.innerHTML = `
    <span class="stat-badge"><span class="dot dot-green"></span>${withTheme} with theme</span>
    <span class="stat-badge"><span class="dot dot-gray"></span>${total - withTheme} without theme</span>
    <span class="stat-badge">${total} total</span>
  `;
}

// ============================================================
// Multi-select
// ============================================================
function toggleItemSelection(ratingKey, checked, card) {
  if (checked) {
    selectedItems.add(ratingKey);
    card.classList.add('selected');
  } else {
    selectedItems.delete(ratingKey);
    card.classList.remove('selected');
  }
  updateBulkBar();
}

function updateBulkBar() {
  const count = selectedItems.size;
  const bulkBar = document.getElementById('bulk-bar');
  const bulkCount = document.getElementById('bulk-count');
  const selectAllCheck = document.getElementById('select-all-check');

  if (count === 0) {
    bulkBar.classList.add('hidden');
  } else {
    bulkBar.classList.remove('hidden');
    bulkCount.textContent = `${count} item${count !== 1 ? 's' : ''} selected`;
  }

  // Sync the select-all checkbox state
  if (selectAllCheck) {
    const visibleCards = document.querySelectorAll('.item-card');
    if (visibleCards.length > 0 && count === visibleCards.length) {
      selectAllCheck.checked = true;
      selectAllCheck.indeterminate = false;
    } else if (count === 0) {
      selectAllCheck.checked = false;
      selectAllCheck.indeterminate = false;
    } else {
      selectAllCheck.checked = false;
      selectAllCheck.indeterminate = true;
    }
  }
}

function toggleSelectAll(checked) {
  const visibleCards = document.querySelectorAll('.item-card');
  visibleCards.forEach((card) => {
    const selectionKey = card.dataset.selectionKey || card.id.replace('card-', '');
    const cb = card.querySelector('input[type="checkbox"]');
    if (checked) {
      selectedItems.add(selectionKey);
      card.classList.add('selected');
      if (cb) cb.checked = true;
    } else {
      selectedItems.delete(selectionKey);
      card.classList.remove('selected');
      if (cb) cb.checked = false;
    }
  });
  updateBulkBar();
}

function deselectAll() {
  selectedItems.clear();
  document.querySelectorAll('.item-card.selected').forEach((card) => {
    card.classList.remove('selected');
    const cb = card.querySelector('input[type="checkbox"]');
    if (cb) cb.checked = false;
  });
  const selectAllCheck = document.getElementById('select-all-check');
  if (selectAllCheck) selectAllCheck.checked = false;
  updateBulkBar();
}

async function bulkDownload() {
  if (selectedItems.size === 0) return;
  if (currentLibraryProvider !== 'plex') {
    showToast('info', 'Bulk download from provider source is only available for Plex libraries.');
    return;
  }
  const ratingKeys = currentItems
    .filter((item) => selectedItems.has(itemSelectionKey(item)))
    .map((item) => item.ratingKey);

  // Check how many selected items already have a local theme
  const itemsWithTheme = currentItems.filter(
    (item) => selectedItems.has(itemSelectionKey(item)) && item.has_local_theme
  );

  if (itemsWithTheme.length > 0) {
    const total = ratingKeys.length;
    const count = itemsWithTheme.length;
    const selectionLabel = count !== 1 ? 'items' : 'item';
    const verb = count !== 1 ? 'have' : 'has';
    const msg = count === total
      ? `All ${count} selected ${selectionLabel} already ${verb} a theme. Do you want to overwrite or skip them?`
      : `${count} of the ${total} selected items already ${verb} a theme. Do you want to overwrite or skip them?`;
    document.getElementById('modal-bulk-overwrite-message').textContent = msg;
    openModal('modal-bulk-overwrite');
    return;
  }

  await executeBulkDownload(false);
}

async function confirmBulkDownload(overwrite) {
  closeModal('modal-bulk-overwrite');
  await executeBulkDownload(overwrite);
}

async function executeBulkDownload(overwrite) {
  if (selectedItems.size === 0) return;
  if (currentLibraryProvider !== 'plex') return;
  const ratingKeys = currentItems
    .filter((item) => selectedItems.has(itemSelectionKey(item)))
    .map((item) => item.ratingKey);
  const btn = document.getElementById('btn-bulk-download');
  const origText = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Downloading…';
  try {
    const data = await apiPost('/api/bulk/theme/download', { ratingKeys, overwrite });
    if (data?.error) throw new Error(data.error);
    const s = data.success?.length ?? 0;
    const sk = data.skipped?.length ?? 0;
    const f = data.failed?.length ?? 0;
    const n = data.no_theme?.length ?? 0;
    showToast('success', `Bulk done: ${s} downloaded, ${sk} skipped, ${n} no theme, ${f} failed`);
    // Refresh item cards for successfully downloaded items
    if (s > 0 && currentLibraryId) {
      const items = await fetchLibraryItems(currentLibraryProvider, currentLibraryId);
      libraryCache.set(makeLibraryCacheKey(currentLibraryProvider, currentLibraryId), items);
      currentItems = items;
      updateStats(items);
      renderItems(items);
    }
  } catch (err) {
    showToast('error', `Bulk download failed: ${err}`);
  } finally {
    btn.disabled = false;
    btn.textContent = origText;
  }
}

// ============================================================
// Filter & Search
// ============================================================
function setFilter(filter) {
  activeFilter = filter;
  activeFilters.theme = filter;
  renderItems(currentItems);
}

function applyFilters() {
  const themeRadio = document.querySelector('input[name="filter-theme"]:checked');
  activeFilters.theme = themeRadio ? themeRadio.value : 'all';
  activeFilter = activeFilters.theme;
  const plexCheck = document.getElementById('filter-plex-avail');
  const tdbCheck = document.getElementById('filter-themerrdb-avail');
  activeFilters.plex = plexCheck ? plexCheck.checked : false;
  if (!plexConfigured) activeFilters.plex = false;
  activeFilters.themerrdb = tdbCheck ? tdbCheck.checked : false;
  updateFilterButtonState();
  renderItems(currentItems);
}

function toggleFilterPanel(event) {
  event.stopPropagation();
  const panel = document.getElementById('filter-panel');
  const btn = document.getElementById('filter-icon-btn');
  const isOpen = !panel.classList.contains('hidden');
  if (isOpen) {
    panel.classList.add('hidden');
    btn.setAttribute('aria-expanded', 'false');
  } else {
    panel.classList.remove('hidden');
    btn.setAttribute('aria-expanded', 'true');
  }
}

function clearFilters() {
  const themeRadios = document.querySelectorAll('input[name="filter-theme"]');
  themeRadios.forEach((r) => { r.checked = r.value === 'all'; });
  const plexCheck = document.getElementById('filter-plex-avail');
  const tdbCheck = document.getElementById('filter-themerrdb-avail');
  if (plexCheck) plexCheck.checked = false;
  if (tdbCheck) tdbCheck.checked = false;
  applyFilters();
}

function updateFilterButtonState() {
  const btn = document.getElementById('filter-icon-btn');
  const badge = document.getElementById('filter-active-badge');
  if (!btn || !badge) return;
  let activeCount = 0;
  if (activeFilters.theme !== 'all') activeCount++;
  if (activeFilters.plex) activeCount++;
  if (activeFilters.themerrdb) activeCount++;
  if (activeCount > 0) {
    btn.classList.add('filter-active');
    badge.textContent = activeCount;
    badge.classList.remove('hidden');
  } else {
    btn.classList.remove('filter-active');
    badge.classList.add('hidden');
  }
}

function filterItems() {
  renderItems(currentItems);
}

// Close filter panel when clicking outside
document.addEventListener('click', (e) => {
  const panel = document.getElementById('filter-panel');
  const wrapper = document.querySelector('.filter-wrapper');
  if (panel && wrapper && !wrapper.contains(e.target) && !panel.classList.contains('hidden')) {
    panel.classList.add('hidden');
    const btn = document.getElementById('filter-icon-btn');
    if (btn) btn.setAttribute('aria-expanded', 'false');
  }
});

// ============================================================
// Get Theme Modal (source selection)
// ============================================================
function openGetThemeModal(item) {
  activeItemContext = { provider: item.provider || 'plex', id: String(item.id || item.ratingKey), item };

  // Configure source buttons based on availability
  const plexBtn = document.getElementById('get-theme-btn-plex');
  const tdbBtn = document.getElementById('get-theme-btn-themerrdb');
  const plexStatus = document.getElementById('get-theme-plex-status');
  const tdbStatus = document.getElementById('get-theme-themerrdb-status');

  if (plexBtn) {
    plexBtn.classList.toggle('hidden', !plexConfigured);
    plexBtn.disabled = !item.has_plex_theme;
    if (plexStatus && plexConfigured) {
      if (!item.has_plex_theme) {
        plexStatus.textContent = 'Not available';
      } else if (item.plex_theme_source_unverified) {
        plexStatus.textContent = 'Available (source unverified: local theme exists)';
      } else {
        plexStatus.textContent = 'Theme available';
      }
    }
  }
  if (tdbBtn) {
    tdbBtn.disabled = !item.has_themerrdb_theme;
    if (tdbStatus) tdbStatus.textContent = item.has_themerrdb_theme ? 'Theme available' : 'Not available';
  }

  openModal('modal-get-theme');
}

function getThemeSelectSource(source) {
  closeModal('modal-get-theme');
  const item = activeItemContext && activeItemContext.item;
  if (!item) return;
  if (source === 'plex') {
    if (!plexConfigured) {
      showToast('info', 'Plex source is unavailable because Plex is not configured.');
      return;
    }
    openDownloadModal(item);
  } else if (source === 'themerrdb') {
    openThemerrdbModal(item);
  } else if (source === 'youtube') {
    openYoutubeModal(item);
  }
}

// ============================================================
// Download Modal
// ============================================================
function openDownloadModal(item) {
  activeItemContext = { provider: item.provider || 'plex', id: String(item.id || item.ratingKey) };
  const previewAudio = document.getElementById('preview-audio');
  const previewError = document.getElementById('preview-audio-error');
  previewAudio.pause();
  previewAudio.src = '';
  previewError.textContent = '';
  previewError.classList.add('hidden');

  document.getElementById('modal-download-message').textContent =
    `Download the Plex theme for "${item.title}" and save it as theme.mp3?`;

  const overwriteDiv = document.getElementById('modal-download-overwrite');
  if (item.has_local_theme) {
    overwriteDiv.classList.remove('hidden');
    document.getElementById('download-overwrite-check').checked = false;
  } else {
    overwriteDiv.classList.add('hidden');
  }
  _syncOverwriteActionButton('btn-confirm-download', 'download-overwrite-check', item.has_local_theme);

  const previewContainer = document.getElementById('modal-download-preview');
  previewContainer.classList.remove('hidden');
  previewAudio.src = `/api/items/${activeItemContext.provider}/${encodeURIComponent(activeItemContext.id)}/theme/preview?t=${Date.now()}`;
  previewAudio.load();

  openModal('modal-download');
  apiGet(`/api/items/${activeItemContext.provider}/${encodeURIComponent(activeItemContext.id)}/theme/preview/check`)
    .then((check) => {
      if (!check.available) {
        const reason = check.reason || 'Preview is unavailable for this item.';
        previewError.textContent = reason;
        previewError.classList.remove('hidden');
        previewAudio.src = '';
        previewAudio.load();
        showToast('error', reason);
      }
    })
    .catch((err) => {
      previewError.textContent = `Could not prepare preview: ${String(err)}`;
      previewError.classList.remove('hidden');
      previewAudio.src = '';
      previewAudio.load();
    });
}

async function confirmDownload() {
  const overwrite = document.getElementById('download-overwrite-check').checked;
  const btn = document.getElementById('btn-confirm-download');
  btn.disabled = true;
  btn.textContent = 'Downloading…';
  try {
    const data = await apiPost(
      `/api/items/${activeItemContext.provider}/${encodeURIComponent(activeItemContext.id)}/theme/download`,
      { overwrite },
    );
    if (data.error && data.exists) {
      showToast('info', 'Theme already exists. Enable overwrite to replace it.');
    } else if (data.success) {
      showToast('success', 'Theme downloaded successfully!');
      closeModal('modal-download');
      if (!applyServerItemUpdate(data.item)) {
        await refreshItem(activeItemContext.id);
      }
    } else {
      showToast('error', data.error || 'Download failed');
    }
  } catch (err) {
    showToast('error', String(err));
  } finally {
    btn.disabled = false;
    btn.textContent = 'Download';
  }
}


// ============================================================
// ThemerrDB Modal
// ============================================================
async function openThemerrdbModal(item) {
  activeItemContext = { provider: item.provider || 'plex', id: String(item.id || item.ratingKey) };
  document.getElementById('themerrdb-item-title').textContent = item.title;

  const overwriteDiv = document.getElementById('modal-themerrdb-overwrite');
  if (item.has_local_theme) {
    overwriteDiv.classList.remove('hidden');
    document.getElementById('themerrdb-overwrite-check').checked = false;
  } else {
    overwriteDiv.classList.add('hidden');
  }

  const previewContainer = document.getElementById('themerrdb-preview-container');
  previewContainer.innerHTML = `
    <div class="preview-loading">
      <div class="spinner"></div>
      <span class="muted">Loading preview…</span>
    </div>
  `;

  openModal('modal-themerrdb');
  _syncOverwriteActionButton('btn-confirm-themerrdb', 'themerrdb-overwrite-check', item.has_local_theme);

  try {
    const basePath = `/api/items/${activeItemContext.provider}/${encodeURIComponent(activeItemContext.id)}/theme/themerrdb`;
    const response = await fetch(`${basePath}/check`);
    const result = await response.json();

    if (!response.ok || !result.available) {
      const reason = result.reason || 'Theme not available in ThemerrDB';
      previewContainer.innerHTML = `<p class="error">${escHtml(reason)}</p>`;
      document.getElementById('btn-confirm-themerrdb').disabled = true;
      showToast('error', reason);
      return;
    }

    // Show preview player
    previewContainer.innerHTML = `
      <div class="preview-player">
        <audio controls style="width: 100%;">
          <source src="${basePath}/preview" type="audio/mpeg">
          Your browser does not support the audio element.
        </audio>
      </div>
    `;
    document.getElementById('btn-confirm-themerrdb').disabled = false;
  } catch (err) {
    previewContainer.innerHTML = `<p class="error">Failed to load theme preview: ${err}</p>`;
    document.getElementById('btn-confirm-themerrdb').disabled = true;
    showToast('error', `Failed to load ThemerrDB preview: ${err}`);
  }
}


// ============================================================
// Copy Theme Modal
// ============================================================
async function openCopyThemeModal(item) {
  activeItemContext = { provider: item.provider || 'plex', id: String(item.id || item.ratingKey) };
  document.getElementById('copy-target-item-title').textContent = item.title;

  const overwriteDiv = document.getElementById('modal-copy-overwrite');
  if (item.has_local_theme) {
    overwriteDiv.classList.remove('hidden');
    document.getElementById('copy-overwrite-check').checked = false;
  } else {
    overwriteDiv.classList.add('hidden');
  }

  const sourceLibrarySelect = document.getElementById('copy-theme-source-library');
  sourceLibrarySelect.innerHTML = '<option value="">Loading libraries…</option>';
  sourceLibrarySelect.disabled = true;

  const sourceItemSelect = document.getElementById('copy-theme-source-item');
  sourceItemSelect.innerHTML = '<option value="">Select a source item…</option>';
  sourceItemSelect.disabled = true;

  openModal('modal-copy-theme');
  syncCopyThemeConfirmButton();

  try {
    const libraries = await apiGet('/api/libraries');
    sourceLibrarySelect.innerHTML = '';

    for (const library of libraries) {
      const option = document.createElement('option');
      option.value = `${library.provider || 'plex'}:${library.key}`;
      option.textContent = `[${(library.provider || 'plex').toUpperCase()}] ${library.title}`;
      sourceLibrarySelect.appendChild(option);
    }

    if (libraries.length === 0) {
      sourceLibrarySelect.innerHTML = '<option value="">No libraries available</option>';
      sourceLibrarySelect.disabled = true;
      sourceItemSelect.innerHTML = '<option value="">No source items available</option>';
      sourceItemSelect.disabled = true;
      syncCopyThemeConfirmButton();
      return;
    }

    const preferredLibrary = libraries.find(
      (library) => (library.provider || 'plex') === currentLibraryProvider && String(library.key) === String(currentLibraryId),
    );
    const selectedLibrary = preferredLibrary || libraries[0];
    sourceLibrarySelect.value = `${selectedLibrary.provider || 'plex'}:${selectedLibrary.key}`;
    sourceLibrarySelect.disabled = false;
    await populateCopyThemeSources(sourceLibrarySelect.value);
  } catch (err) {
    sourceLibrarySelect.innerHTML = '<option value="">Failed to load libraries</option>';
    sourceItemSelect.innerHTML = '<option value="">Failed to load source items</option>';
    sourceLibrarySelect.disabled = true;
    sourceItemSelect.disabled = true;
    syncCopyThemeConfirmButton();
    showToast('error', `Failed to load source libraries: ${err}`);
  }
}

async function populateCopyThemeSources(sourceLibraryId) {
  const [sourceProvider, sourceLibraryKey] = String(sourceLibraryId).split(':', 2);
  if (!sourceProvider || !sourceLibraryKey) {
    return;
  }
  const sourceItemSelect = document.getElementById('copy-theme-source-item');
  sourceItemSelect.innerHTML = '<option value="">Loading source items…</option>';
  sourceItemSelect.disabled = true;
  syncCopyThemeConfirmButton();

  try {
    const sourceCacheKey = makeLibraryCacheKey(sourceProvider, sourceLibraryKey);
    let items = libraryCache.get(sourceCacheKey);
    if (!items) {
      items = await fetchLibraryItems(sourceProvider, sourceLibraryKey);
      libraryCache.set(sourceCacheKey, items);
    }

    const candidates = items.filter(
      (candidate) => candidate.has_local_theme
        && selectedKeyFor(candidate.provider || 'plex', String(candidate.id || candidate.ratingKey))
          !== selectedKeyFor(activeItemContext.provider, activeItemContext.id),
    );

    sourceItemSelect.innerHTML = '';
    if (candidates.length === 0) {
      sourceItemSelect.innerHTML = '<option value="">No items with local themes in this library</option>';
      sourceItemSelect.disabled = true;
      syncCopyThemeConfirmButton();
      return;
    }

    for (const item of candidates) {
      const option = document.createElement('option');
      option.value = `${item.provider || 'plex'}:${item.id || item.ratingKey}`;
      option.textContent = item.year ? `[${(item.provider || 'plex').toUpperCase()}] ${item.title} (${item.year})` : `[${(item.provider || 'plex').toUpperCase()}] ${item.title}`;
      sourceItemSelect.appendChild(option);
    }

    sourceItemSelect.disabled = false;
    sourceItemSelect.selectedIndex = 0;
    syncCopyThemeConfirmButton();
  } catch (err) {
    sourceItemSelect.innerHTML = '<option value="">Failed to load source items</option>';
    sourceItemSelect.disabled = true;
    syncCopyThemeConfirmButton();
    showToast('error', `Failed to load source items: ${err}`);
  }
}

function syncCopyThemeConfirmButton() {
  const btn = document.getElementById('btn-confirm-copy-theme');
  const hasSource = Boolean(document.getElementById('copy-theme-source-item').value);
  const overwriteWarningVisible = !document.getElementById('modal-copy-overwrite').classList.contains('hidden');
  const overwriteChecked = document.getElementById('copy-overwrite-check').checked;
  btn.disabled = !hasSource || (overwriteWarningVisible && !overwriteChecked);
}

async function confirmCopyTheme() {
  const sourceValue = document.getElementById('copy-theme-source-item').value;
  if (!sourceValue) {
    showToast('error', 'Please select a source item');
    return;
  }
  const [sourceProvider, sourceItemId] = sourceValue.split(':', 2);

  const overwrite = document.getElementById('copy-overwrite-check').checked;
  const btn = document.getElementById('btn-confirm-copy-theme');
  btn.disabled = true;
  btn.textContent = 'Copying…';

  try {
    const data = await apiPost(
      `/api/items/${activeItemContext.provider}/${encodeURIComponent(activeItemContext.id)}/theme/copy`,
      { sourceProvider, sourceItemId, overwrite },
    );
    if (data.error && data.exists) {
      showToast('info', 'Theme already exists. Enable overwrite to replace it.');
    } else if (data.success) {
      showToast('success', 'Theme copied successfully!');
      closeModal('modal-copy-theme');
      if (!applyServerItemUpdate(data.item)) {
        await refreshItem(activeItemContext.id);
      }
    } else {
      showToast('error', data.error || 'Copy failed');
    }
  } catch (err) {
    showToast('error', String(err));
  } finally {
    btn.textContent = 'Copy Theme';
    syncCopyThemeConfirmButton();
  }
}

// ============================================================
// Upload Modal
// ============================================================
function openUploadModal(item) {
  activeItemContext = { provider: item.provider || 'plex', id: String(item.id || item.ratingKey) };
  document.getElementById('upload-item-title').textContent = item.title;
  document.getElementById('upload-file-input').value = '';
  document.getElementById('selected-file-name').classList.add('hidden');

  const overwriteDiv = document.getElementById('modal-upload-overwrite');
  if (item.has_local_theme) {
    overwriteDiv.classList.remove('hidden');
    document.getElementById('upload-overwrite-check').checked = false;
  } else {
    overwriteDiv.classList.add('hidden');
  }
  _syncOverwriteActionButton('btn-confirm-upload', 'upload-overwrite-check', item.has_local_theme);

  openModal('modal-upload');
}

function handleFileSelect(event) {
  const file = event.target.files[0];
  if (file) {
    const el = document.getElementById('selected-file-name');
    el.textContent = file.name;
    el.classList.remove('hidden');
  }
}

async function confirmUpload() {
  const fileInput = document.getElementById('upload-file-input');
  if (!fileInput.files[0]) {
    showToast('error', 'Please select a file first');
    return;
  }
  const overwrite = document.getElementById('upload-overwrite-check').checked;
  const formData = new FormData();
  formData.append('file', fileInput.files[0]);
  formData.append('overwrite', overwrite ? 'true' : 'false');

  try {
    const resp = await apiFormPost(`/api/items/${activeItemContext.provider}/${encodeURIComponent(activeItemContext.id)}/theme/upload`, formData);
    const data = await resp.json();
    if (data.error && data.exists) {
      showToast('info', 'Theme already exists. Enable overwrite to replace it.');
    } else if (data.success) {
      showToast('success', 'Theme uploaded successfully!');
      closeModal('modal-upload');
      if (!applyServerItemUpdate(data.item)) {
        await refreshItem(activeItemContext.id);
      }
    } else {
      showToast('error', data.error || 'Upload failed');
    }
  } catch (err) {
    showToast('error', String(err));
  }
}

// ============================================================
// YouTube Search & Modal
// ============================================================
let currentlyPlayingVideoId = null;

function openYoutubeModal(item) {
  activeItemContext = { provider: item.provider || 'plex', id: String(item.id || item.ratingKey) };
  document.getElementById('youtube-item-title').textContent = item.title;
  document.getElementById('youtube-url-input').value = '';
  document.getElementById('youtube-progress').classList.add('hidden');
  document.getElementById('youtube-search-results').innerHTML = '';
  _stopYoutubePlayer();

  const overwriteDiv = document.getElementById('modal-youtube-overwrite');
  if (item.has_local_theme) {
    overwriteDiv.classList.remove('hidden');
    document.getElementById('youtube-overwrite-check').checked = false;
  } else {
    overwriteDiv.classList.add('hidden');
  }
  _syncOverwriteActionButton('btn-confirm-youtube', 'youtube-overwrite-check', item.has_local_theme);

  const defaultQuery = `${item.title} theme song`;
  document.getElementById('youtube-search-input').value = defaultQuery;

  openModal('modal-youtube');
  doYoutubeSearch();
}

async function doYoutubeSearch() {
  const query = document.getElementById('youtube-search-input').value.trim();
  if (!query) return;

  _stopYoutubePlayer();
  document.getElementById('youtube-url-input').value = '';

  const resultsEl = document.getElementById('youtube-search-results');
  resultsEl.innerHTML = '<div class="yt-search-loading"><div class="spinner"></div><span>Searching YouTube…</span></div>';

  try {
    const resp = await fetch(`/api/youtube/search?q=${encodeURIComponent(query)}&limit=5`);
    const data = await resp.json();
    if (!resp.ok || data.error) {
      resultsEl.innerHTML = `<div class="yt-search-empty">Search failed: ${data.error || 'Unknown error'}</div>`;
      return;
    }
    if (!data.results || data.results.length === 0) {
      resultsEl.innerHTML = '<div class="yt-search-empty">No results found.</div>';
      return;
    }
    _renderYoutubeResults(data.results, resultsEl);
  } catch (err) {
    resultsEl.innerHTML = `<div class="yt-search-empty">Search failed: ${err.message}</div>`;
  }
}

function _renderYoutubeResults(results, container) {
  container.innerHTML = '';
  for (const result of results) {
    const wrapper = document.createElement('div');
    wrapper.className = 'yt-result-wrapper';

    // ── Result row ──
    const row = document.createElement('div');
    row.className = 'yt-result';
    row.dataset.url = result.url;
    row.addEventListener('click', () => _selectYoutubeResult(result.url, row));

    // Thumbnail
    const thumb = document.createElement('img');
    thumb.className = 'yt-result-thumb';
    thumb.src = result.thumbnail || '';
    thumb.alt = '';
    row.appendChild(thumb);

    // Info
    const info = document.createElement('div');
    info.className = 'yt-result-info';
    const titleEl = document.createElement('div');
    titleEl.className = 'yt-result-title';
    titleEl.textContent = result.title;
    info.appendChild(titleEl);
    const meta = document.createElement('div');
    meta.className = 'yt-result-meta';
    const parts = [];
    if (result.channel) parts.push(result.channel);
    if (result.duration) parts.push(result.duration);
    if (result.view_count) parts.push(_formatViewCount(result.view_count) + ' views');
    meta.textContent = parts.join(' · ');
    info.appendChild(meta);
    row.appendChild(info);

    // Play button
    const playBtn = document.createElement('button');
    playBtn.className = 'yt-play-btn';
    playBtn.title = 'Preview';
    playBtn.innerHTML = '▶';
    const playerDiv = document.createElement('div');
    playBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      _toggleYoutubePlayer(result.id, playBtn, playerDiv);
    });
    row.appendChild(playBtn);

    wrapper.appendChild(row);

    // Inline player (hidden by default)
    playerDiv.className = 'yt-inline-player';
    playerDiv.id = `yt-player-${result.id}`;
    wrapper.appendChild(playerDiv);

    container.appendChild(wrapper);
  }
}

function _selectYoutubeResult(url, rowEl) {
  document.querySelectorAll('.yt-result.selected').forEach(el => el.classList.remove('selected'));
  rowEl.classList.add('selected');
  document.getElementById('youtube-url-input').value = url;
}

function _toggleYoutubePlayer(videoId, playBtn, playerDiv) {
  // Close any other open player first
  if (currentlyPlayingVideoId && currentlyPlayingVideoId !== videoId) {
    const prevPlayer = document.getElementById(`yt-player-${currentlyPlayingVideoId}`);
    if (prevPlayer) { prevPlayer.innerHTML = ''; prevPlayer.classList.remove('open'); }
    const prevBtn = document.querySelector(`.yt-play-btn[data-video-id="${currentlyPlayingVideoId}"]`);
    if (prevBtn) { prevBtn.classList.remove('playing'); prevBtn.innerHTML = '▶'; }
  }

  if (currentlyPlayingVideoId === videoId) {
    // Stop
    playerDiv.innerHTML = '';
    playerDiv.classList.remove('open');
    playBtn.classList.remove('playing');
    playBtn.innerHTML = '▶';
    currentlyPlayingVideoId = null;
  } else {
    // Start
    const iframe = document.createElement('iframe');
    iframe.src = `https://www.youtube-nocookie.com/embed/${videoId}?autoplay=1`;
    iframe.allow = 'autoplay; encrypted-media';
    iframe.setAttribute('allowfullscreen', '');
    playerDiv.innerHTML = '';
    playerDiv.appendChild(iframe);
    playerDiv.classList.add('open');
    playBtn.dataset.videoId = videoId;
    playBtn.classList.add('playing');
    playBtn.innerHTML = '⏹';
    currentlyPlayingVideoId = videoId;
  }
}

function _stopYoutubePlayer() {
  if (!currentlyPlayingVideoId) return;
  const prev = document.getElementById(`yt-player-${currentlyPlayingVideoId}`);
  if (prev) { prev.innerHTML = ''; prev.classList.remove('open'); }
  currentlyPlayingVideoId = null;
}

function _formatViewCount(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

async function confirmYoutube() {
  const url = document.getElementById('youtube-url-input').value.trim();
  if (!url) {
    showToast('error', 'Please select a result or enter a YouTube URL');
    return;
  }
  const overwrite = document.getElementById('youtube-overwrite-check').checked;
  const progressEl = document.getElementById('youtube-progress');
  progressEl.classList.remove('hidden');

  try {
    const data = await apiPost(
      `/api/items/${activeItemContext.provider}/${encodeURIComponent(activeItemContext.id)}/theme/youtube`,
      { url, overwrite },
    );
    progressEl.classList.add('hidden');
    if (data.error && data.exists) {
      showToast('info', 'Theme already exists. Enable overwrite to replace it.');
    } else if (data.success) {
      showToast('success', 'YouTube theme downloaded successfully!');
      closeModal('modal-youtube');
      if (!applyServerItemUpdate(data.item)) {
        await refreshItem(activeItemContext.id);
      }
    } else {
      showToast('error', data.error || 'YouTube download failed');
    }
  } catch (err) {
    progressEl.classList.add('hidden');
    showToast('error', String(err));
  }
}

async function confirmThemerrdb() {
  const overwrite = document.getElementById('themerrdb-overwrite-check').checked;
  const progressEl = document.getElementById('themerrdb-progress');
  const btn = document.getElementById('btn-confirm-themerrdb');
  progressEl.classList.remove('hidden');
  btn.disabled = true;

  try {
    const data = await apiPost(
      `/api/items/${activeItemContext.provider}/${encodeURIComponent(activeItemContext.id)}/theme/themerrdb`,
      { overwrite },
    );
    progressEl.classList.add('hidden');
    if (data.error && data.exists) {
      showToast('info', 'Theme already exists. Enable overwrite to replace it.');
    } else if (data.success) {
      showToast('success', 'ThemerrDB theme downloaded successfully!');
      closeModal('modal-themerrdb');
      if (!applyServerItemUpdate(data.item)) {
        await refreshItem(activeItemContext.id);
      }
    } else {
      showToast('error', data.error || 'ThemerrDB download failed');
    }
  } catch (err) {
    progressEl.classList.add('hidden');
    showToast('error', String(err));
  } finally {
    btn.disabled = false;
  }
}

// ============================================================
// Delete Modal
// ============================================================
function openDeleteModal(item) {
  activeItemContext = { provider: item.provider || 'plex', id: String(item.id || item.ratingKey) };
  document.getElementById('delete-item-title').textContent = item.title;
  openModal('modal-delete');
}

async function confirmDelete() {
  try {
    const data = await apiDelete(`/api/items/${activeItemContext.provider}/${encodeURIComponent(activeItemContext.id)}/theme`);
    if (data.success) {
      showToast('success', 'Theme deleted.');
      closeModal('modal-delete');
      if (!applyServerItemUpdate(data.item)) {
        await refreshItem(activeItemContext.id);
      }
    } else {
      showToast('error', data.error || 'Delete failed');
    }
  } catch (err) {
    showToast('error', String(err));
  }
}

// ============================================================
// Refresh single item card
// ============================================================
function applyServerItemUpdate(updatedItem) {
  if (!updatedItem || !currentLibraryId || !Array.isArray(currentItems)) return false;

  const updatedKey = selectedKeyFor(updatedItem.provider || 'plex', String(updatedItem.id || updatedItem.ratingKey));
  const itemIndex = currentItems.findIndex(
    (item) => selectedKeyFor(item.provider || 'plex', String(item.id || item.ratingKey)) === updatedKey,
  );
  if (itemIndex === -1) return false;

  const nextItems = [...currentItems];
  nextItems[itemIndex] = updatedItem;
  currentItems = nextItems;
  libraryCache.set(makeLibraryCacheKey(currentLibraryProvider, currentLibraryId), nextItems);

  if (!updatedItem.has_local_theme
      && activeAudio
      && activeAudio.src
      && activeAudio.src.includes(providerItemThemePath(updatedItem))) {
    stopInlineAudio();
  }

  renderItems(nextItems);
  updateStats(nextItems);
  updateBulkBar();
  return true;
}

async function refreshItem(ratingKey) {
  if (!currentLibraryId) return;
  try {
    const items = await fetchLibraryItems(currentLibraryProvider, currentLibraryId);
    libraryCache.set(makeLibraryCacheKey(currentLibraryProvider, currentLibraryId), items);
    currentItems = items;
    const updated = items.find((item) => String(item.id || item.ratingKey) === String(ratingKey));
    if (updated) {
      const card = document.getElementById(`card-${itemSelectionKey(updated)}`);
      if (card) {
        // Stop inline audio if it belongs to this card
        if (activePlayBtn && card.contains(activePlayBtn)) {
          stopInlineAudio();
        }
        card.replaceWith(createItem(updated));
      }
    }
    updateStats(items);
    updateBulkBar();
  } catch (err) {
    console.error('Failed to refresh item', err);
    showToast('error', 'Theme list refresh failed. Reload the library to sync state.');
  }
}

// ============================================================
// Settings page
// ============================================================
function showSettingsPage(event) {
  if (event) event.preventDefault();
  currentLibraryId = null;
  currentLibraryProvider = null;
  activeItemContext = null;
  stopInlineAudio();

  document.getElementById('welcome-screen').classList.add('hidden');
  document.getElementById('library-view').classList.add('hidden');
  document.getElementById('settings-view').classList.remove('hidden');

  document.querySelectorAll('.library-nav-item').forEach((el) => el.classList.remove('active'));
  document.getElementById('settings-nav-item').classList.add('active');

  // Reset action result banner
  const result = document.getElementById('settings-action-result');
  result.className = 'settings-action-result hidden';
  result.textContent = '';
  loadSettingsRuntime();
}

function showSettingsResult(ok, message) {
  const el = document.getElementById('settings-action-result');
  el.className = `settings-action-result ${ok ? 'result-ok' : 'result-err'}`;
  el.textContent = message;
}

async function settingsTestPlex() {
  if (!plexConfigured) {
    showSettingsResult(false, '✗ Plex is not configured (set both PLEX_URL and PLEX_TOKEN).');
    return;
  }
  showSettingsResult(true, 'Connecting to Plex…');
  try {
    const data = await apiGet('/api/status');
    if (data.connected) {
      showSettingsResult(true, `✓ Connected to "${data.server_name}" (Plex Media Server v${data.version})`);
    } else {
      showSettingsResult(false, `✗ Not connected: ${data.error || 'Unknown error'}`);
    }
  } catch (err) {
    showSettingsResult(false, `✗ ${err}`);
  }
}

async function settingsRefreshLibraries() {
  showSettingsResult(true, 'Refreshing libraries…');
  try {
    libraryCache.clear();
    await loadLibraries();
    // Rebuild server-side item cache in the background (don't await — it takes time)
    apiPost('/api/settings/refresh-cache', {}).catch(() => {});
    showSettingsResult(true, '✓ Libraries refreshed successfully. Item cache is rebuilding in the background.');
  } catch (err) {
    showSettingsResult(false, `✗ Failed to refresh libraries: ${err}`);
  }
}

async function settingsTestPushover() {
  showSettingsResult(true, 'Sending test notification…');
  try {
    const data = await apiPost('/api/settings/test-pushover', {});
    if (data.error) {
      showSettingsResult(false, `✗ ${data.error}`);
    } else {
      showSettingsResult(true, '✓ Test notification sent successfully. Check your Pushover app.');
    }
  } catch (err) {
    showSettingsResult(false, `✗ ${err}`);
  }
}

async function settingsRescan() {
  showSettingsResult(true, 'Scanning libraries for theme.mp3 files…');
  try {
    const data = await apiPost('/api/settings/rescan', {});
    if (data.error) {
      showSettingsResult(false, `✗ ${data.error}`);
    } else {
      libraryCache.clear();
      showSettingsResult(
        true,
        `✓ Scan complete — ${data.total} items found: ${data.with_theme} with theme, ${data.without_theme} without. Cache is rebuilding in the background.`,
      );
    }
  } catch (err) {
    showSettingsResult(false, `✗ ${err}`);
  }
}

// ============================================================
// Modal helpers
// ============================================================
function openModal(id) {
  document.getElementById(id).classList.remove('hidden');
}

function _stopModalMediaPlayback(modalId) {
  const overlay = document.getElementById(modalId);
  if (overlay) {
    overlay.querySelectorAll('audio').forEach((audio) => {
      audio.pause();
      audio.removeAttribute('src');
      audio.querySelectorAll('source').forEach((source) => source.removeAttribute('src'));
      audio.load();
    });
  }
  if (modalId === 'modal-youtube') _stopYoutubePlayer();
}

function closeModal(id, event) {
  const overlay = document.getElementById(id);
  if (event && event.target !== overlay) return;
  overlay.classList.add('hidden');
  _stopModalMediaPlayback(id);
}

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    document.querySelectorAll('.modal-overlay:not(.hidden)').forEach((modal) => {
      _stopModalMediaPlayback(modal.id);
      modal.classList.add('hidden');
    });
  }
});

// ============================================================
// Toast notifications
// ============================================================
function showToast(type, message) {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  const icon = type === 'success' ? '✓' : type === 'error' ? '✗' : 'ℹ';
  toast.innerHTML = `<span>${icon}</span><span>${escHtml(message)}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('toast-out');
    setTimeout(() => toast.remove(), 200);
  }, 4000);
}

// ============================================================
// API helpers
// ============================================================
async function apiGet(url) {
  const resp = await fetch(url, { headers: buildApiHeaders() });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

async function apiPost(url, body) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: buildApiHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

async function apiDelete(url) {
  const resp = await fetch(url, { method: 'DELETE', headers: buildApiHeaders() });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

async function apiFormPost(url, formData) {
  return fetch(url, {
    method: 'POST',
    headers: buildApiHeaders(),
    body: formData,
  });
}

function buildApiHeaders(extraHeaders = {}) {
  const headers = { ...extraHeaders };
  if (apiKey) headers['X-Themarr-Api-Key'] = apiKey;
  return headers;
}

function escHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#x27;');
}
