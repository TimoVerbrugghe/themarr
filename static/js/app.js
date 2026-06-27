/* ============================================================
   Themarr - Frontend Application
   ============================================================ */

// ============================================================
// SVG icon constants (neutral currentColor)
// ============================================================
const ICON_PLEX = `<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M11.916 0C5.333 0 0 5.333 0 11.916s5.333 11.916 11.916 11.916 11.916-5.333 11.916-11.916S18.499 0 11.916 0zm1.501 16.14L9.143 12l4.274-4.14 1.263.865-3.25 3.275 3.25 3.275z"/></svg>`;
const ICON_YOUTUBE = `<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/></svg>`;
const ICON_UPLOAD = `<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M9 16h6v-6h4l-7-7-7 7h4zm-4 2h14v2H5z"/></svg>`;
const ICON_PLAY = `<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>`;
const ICON_PAUSE = `<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>`;
const ICON_COPY = `<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M16 1H4a2 2 0 0 0-2 2v12h2V3h12V1zm4 4H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2zm0 16H8V7h12v14z"/></svg>`;

// Action button label HTML: [icon, grid-label, list-label]
const BTN_PLEX    = [ICON_PLEX,    `${ICON_PLEX} Plex`,    `${ICON_PLEX} Download from Plex`];
const BTN_COPY    = [ICON_COPY,    `${ICON_COPY} Copy Theme`, `${ICON_COPY} Copy theme from…`];
const BTN_YOUTUBE = [ICON_YOUTUBE, `${ICON_YOUTUBE} YouTube`, `${ICON_YOUTUBE} Download from YouTube`];
const BTN_UPLOAD  = [ICON_UPLOAD,  `${ICON_UPLOAD} Upload`, `${ICON_UPLOAD} Upload custom theme`];

// Capture server-rendered defaults before JS mutates the DOM
const SERVER_DEFAULT_THEME = document.documentElement.dataset.theme || 'dark';

// State
let currentLibraryId = null;
let currentItems = [];
let activeFilter = 'all';
let activeItemKey = null;
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
  checkPlexStatus();
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
  document.getElementById('copy-overwrite-check').addEventListener('change', () => {
    syncCopyThemeConfirmButton();
  });
  document.getElementById('copy-theme-source-library').addEventListener('change', async (e) => {
    await populateCopyThemeSources(e.target.value);
  });
  document.getElementById('copy-theme-source-item').addEventListener('change', () => {
    syncCopyThemeConfirmButton();
  });

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
// Plex Status
// ============================================================
async function checkPlexStatus() {
  const el = document.getElementById('plex-status');
  const txt = document.getElementById('plex-status-text');
  try {
    const data = await apiGet('/api/status');
    if (data.connected) {
      el.className = 'plex-status plex-status--connected';
      txt.textContent = `Plex Connected (${data.server_name || 'Unknown'})`;
    } else {
      el.className = 'plex-status plex-status--error';
      txt.textContent = 'Not connected';
    }
  } catch {
    el.className = 'plex-status plex-status--error';
    txt.textContent = 'Error';
  }
}

// ============================================================
// Libraries
// ============================================================
async function loadLibraries() {
  const nav = document.getElementById('library-nav');
  try {
    const libraries = await apiGet('/api/libraries');
    if (!libraries.length) {
      nav.innerHTML = '<div class="sidebar-loading">No TV/Movie libraries found</div>';
      return;
    }

    nav.innerHTML = '';
    for (const lib of libraries) {
      const icon = lib.type === 'show' ? '📺' : '🎬';
      const item = document.createElement('a');
      item.className = 'library-nav-item';
      item.dataset.id = lib.id;
      item.href = '#';
      item.innerHTML = `
        <span class="library-nav-icon">${icon}</span>
        <span class="library-nav-name">${escHtml(lib.title)}</span>
        <span class="library-nav-count">${lib.totalSize || ''}</span>
      `;
      item.addEventListener('click', (event) => {
        event.preventDefault();
        selectLibrary(lib.id, lib.title);
      });
      nav.appendChild(item);
    }
  } catch (err) {
    nav.innerHTML = `<div class="sidebar-loading">Error: ${escHtml(String(err))}</div>`;
  }
}

async function selectLibrary(id, title) {
  currentLibraryId = id;
  activeFilter = 'all';

  document.querySelectorAll('.library-nav-item').forEach((el) => el.classList.remove('active'));
  const navItem = document.querySelector(`.library-nav-item[data-id="${id}"]`);
  if (navItem) navItem.classList.add('active');

  document.getElementById('welcome-screen').classList.add('hidden');
  document.getElementById('settings-view').classList.add('hidden');
  document.getElementById('library-view').classList.remove('hidden');
  document.getElementById('library-title').textContent = title;
  document.getElementById('library-stats').innerHTML = '';
  document.getElementById('items-grid').innerHTML = '';
  document.getElementById('search-input').value = '';

  document.querySelectorAll('.filter-buttons .btn').forEach((button) => button.classList.remove('active'));
  document.getElementById('filter-all').classList.add('active');

  // Clear selection when switching libraries
  selectedItems.clear();
  updateBulkBar();

  // Stop any playing audio
  stopInlineAudio();

  // Serve from cache if available, fetch otherwise
  if (libraryCache.has(id)) {
    document.getElementById('items-loading').classList.add('hidden');
    const items = libraryCache.get(id);
    currentItems = items;
    renderItems(items);
    updateStats(items);
    return;
  }

  document.getElementById('items-loading').classList.remove('hidden');
  try {
    const items = await apiGet(`/api/libraries/${id}/items`);
    libraryCache.set(id, items);
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
    let matchFilter = true;
    if (activeFilter === 'has_theme') matchFilter = item.has_local_theme;
    if (activeFilter === 'no_theme') matchFilter = !item.has_local_theme;
    return matchSearch && matchFilter;
  });

  if (!filtered.length) {
    grid.innerHTML = '<div class="items-loading" style="grid-column:1/-1"><span>No items match your filter.</span></div>';
    return;
  }

  filtered.forEach((item) => grid.appendChild(createItem(item)));
}

function createItemCard(item) {
  const card = document.createElement('div');
  const isSelected = selectedItems.has(item.ratingKey);
  card.className = `item-card${item.has_local_theme ? ' has-theme' : ''}${isSelected ? ' selected' : ''}`;
  card.id = `card-${item.ratingKey}`;

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
    toggleItemSelection(item.ratingKey, e.target.checked, card);
  });
  selectWrap.appendChild(checkbox);
  poster.appendChild(selectWrap);

  const image = document.createElement('img');
  image.src = `/api/poster/${item.ratingKey}`;
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
    playBtn.addEventListener('click', () => toggleInlineAudio(item.ratingKey, playBtn));
    body.appendChild(playBtn);
  }

  const actions = document.createElement('div');
  actions.className = 'item-actions';

  // Button order: Download from Plex → YouTube → Copy theme from → Upload → Delete
  const downloadButton = createActionButton('action-btn action-btn-download', 'Download from Plex', BTN_PLEX[1]);
  downloadButton.disabled = !item.has_plex_theme;
  downloadButton.addEventListener('click', () => openDownloadModal(item.ratingKey, item.title, item.has_local_theme, item.has_plex_theme));
  actions.appendChild(downloadButton);

  const youtubeButton = createActionButton('action-btn action-btn-youtube', 'Download from YouTube', BTN_YOUTUBE[1]);
  youtubeButton.addEventListener('click', () => openYoutubeModal(item.ratingKey, item.title, item.has_local_theme));
  actions.appendChild(youtubeButton);

  const copyButton = createActionButton('action-btn action-btn-copy', 'Copy theme from another item', BTN_COPY[1]);
  copyButton.addEventListener('click', () => openCopyThemeModal(item.ratingKey, item.title, item.has_local_theme));
  actions.appendChild(copyButton);

  const uploadButton = createActionButton('action-btn action-btn-upload', 'Upload custom theme', BTN_UPLOAD[1]);
  uploadButton.addEventListener('click', () => openUploadModal(item.ratingKey, item.title, item.has_local_theme));
  actions.appendChild(uploadButton);

  const deleteButton = createActionButton('action-btn action-btn-delete', 'Delete theme', '🗑 Delete');
  deleteButton.disabled = !item.has_local_theme;
  deleteButton.addEventListener('click', () => openDeleteModal(item.ratingKey, item.title));
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

function posterPlaceholder(type, title) {
  const icon = type === 'show' ? '📺' : '🎬';
  return `<div class="poster-placeholder">${icon}<span>${escHtml(title)}</span></div>`;
}

function createItem(item) {
  return currentView === 'list' ? createItemRow(item) : createItemCard(item);
}

function createItemRow(item) {
  const row = document.createElement('div');
  const isSelected = selectedItems.has(item.ratingKey);
  row.className = `item-card item-row${item.has_local_theme ? ' has-theme' : ''}${isSelected ? ' selected' : ''}`;
  row.id = `card-${item.ratingKey}`;

  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.checked = isSelected;
  checkbox.title = 'Select for bulk action';
  checkbox.addEventListener('change', (e) => {
    e.stopPropagation();
    toggleItemSelection(item.ratingKey, e.target.checked, row);
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
    playBtn.addEventListener('click', () => toggleInlineAudio(item.ratingKey, playBtn));
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

  // Button order: Download from Plex → YouTube → Copy theme from → Upload → Delete
  const downloadButton = createActionButton('action-btn action-btn-download', 'Download from Plex', BTN_PLEX[2]);
  downloadButton.disabled = !item.has_plex_theme;
  downloadButton.addEventListener('click', () => {
    closeAllRowActionMenus();
    openDownloadModal(item.ratingKey, item.title, item.has_local_theme, item.has_plex_theme);
  });
  actionMenu.appendChild(downloadButton);

  const youtubeButton = createActionButton('action-btn action-btn-youtube', 'Download from YouTube', BTN_YOUTUBE[2]);
  youtubeButton.addEventListener('click', () => {
    closeAllRowActionMenus();
    openYoutubeModal(item.ratingKey, item.title, item.has_local_theme);
  });
  actionMenu.appendChild(youtubeButton);

  const copyButton = createActionButton('action-btn action-btn-copy', 'Copy theme from another item', BTN_COPY[2]);
  copyButton.addEventListener('click', () => {
    closeAllRowActionMenus();
    openCopyThemeModal(item.ratingKey, item.title, item.has_local_theme);
  });
  actionMenu.appendChild(copyButton);

  const uploadButton = createActionButton('action-btn action-btn-upload', 'Upload custom theme', BTN_UPLOAD[2]);
  uploadButton.addEventListener('click', () => {
    closeAllRowActionMenus();
    openUploadModal(item.ratingKey, item.title, item.has_local_theme);
  });
  actionMenu.appendChild(uploadButton);

  const deleteButton = createActionButton('action-btn action-btn-delete', 'Delete theme', '🗑 Delete');
  deleteButton.disabled = !item.has_local_theme;
  deleteButton.addEventListener('click', () => {
    closeAllRowActionMenus();
    openDeleteModal(item.ratingKey, item.title);
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
function toggleInlineAudio(ratingKey, btn) {
  const src = `/api/items/${ratingKey}/theme`;

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
    const ratingKey = parseInt(card.id.replace('card-', ''), 10);
    const cb = card.querySelector('input[type="checkbox"]');
    if (checked) {
      selectedItems.add(ratingKey);
      card.classList.add('selected');
      if (cb) cb.checked = true;
    } else {
      selectedItems.delete(ratingKey);
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
  const ratingKeys = Array.from(selectedItems);

  // Check how many selected items already have a local theme
  const itemsWithTheme = currentItems.filter(
    (item) => selectedItems.has(item.ratingKey) && item.has_local_theme
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
  const ratingKeys = Array.from(selectedItems);
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
      const items = await apiGet(`/api/libraries/${currentLibraryId}/items`);
      libraryCache.set(currentLibraryId, items);
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
  document.querySelectorAll('.filter-buttons .btn').forEach((button) => button.classList.remove('active'));
  document.getElementById(`filter-${filter.replace(/_/g, '-')}`).classList.add('active');
  renderItems(currentItems);
}

function filterItems() {
  renderItems(currentItems);
}

// ============================================================
// Download Modal
// ============================================================
function openDownloadModal(ratingKey, title, hasLocalTheme, hasPlexTheme) {
  activeItemKey = ratingKey;
  const previewAudio = document.getElementById('preview-audio');
  previewAudio.pause();
  previewAudio.src = '';

  document.getElementById('modal-download-message').textContent =
    `Download the Plex theme for "${title}" and save it as theme.mp3?`;

  const overwriteDiv = document.getElementById('modal-download-overwrite');
  if (hasLocalTheme) {
    overwriteDiv.classList.remove('hidden');
    document.getElementById('download-overwrite-check').checked = false;
  } else {
    overwriteDiv.classList.add('hidden');
  }
  _syncOverwriteActionButton('btn-confirm-download', 'download-overwrite-check', hasLocalTheme);

  document.getElementById('btn-preview-plex').disabled = !hasPlexTheme;
  document.getElementById('modal-download-preview').classList.add('hidden');

  openModal('modal-download');
}

async function previewPlexTheme() {
  const audio = document.getElementById('preview-audio');
  audio.src = `/api/items/${activeItemKey}/theme/preview?t=${Date.now()}`;
  document.getElementById('modal-download-preview').classList.remove('hidden');
  audio.play().catch(() => {});
}

async function confirmDownload() {
  const overwrite = document.getElementById('download-overwrite-check').checked;
  const btn = document.getElementById('btn-confirm-download');
  btn.disabled = true;
  btn.textContent = 'Downloading…';
  try {
    const data = await apiPost(`/api/items/${activeItemKey}/theme/download`, { overwrite });
    if (data.error && data.exists) {
      showToast('info', 'Theme already exists. Enable overwrite to replace it.');
    } else if (data.success) {
      showToast('success', 'Theme downloaded successfully!');
      closeModal('modal-download');
      if (!applyServerItemUpdate(data.item)) {
        await refreshItem(activeItemKey);
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
// Copy Theme Modal
// ============================================================
async function openCopyThemeModal(ratingKey, title, hasLocalTheme) {
  activeItemKey = ratingKey;
  document.getElementById('copy-target-item-title').textContent = title;

  const overwriteDiv = document.getElementById('modal-copy-overwrite');
  if (hasLocalTheme) {
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
      option.value = String(library.key);
      option.textContent = library.title;
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

    const preferredLibrary = libraries.find((library) => String(library.key) === String(currentLibraryId));
    sourceLibrarySelect.value = preferredLibrary ? String(preferredLibrary.key) : String(libraries[0].key);
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
  const sourceItemSelect = document.getElementById('copy-theme-source-item');
  sourceItemSelect.innerHTML = '<option value="">Loading source items…</option>';
  sourceItemSelect.disabled = true;
  syncCopyThemeConfirmButton();

  try {
    let items = libraryCache.get(sourceLibraryId);
    if (!items) {
      items = await apiGet(`/api/libraries/${sourceLibraryId}/items`);
      libraryCache.set(sourceLibraryId, items);
    }

    const candidates = items.filter(
      (item) => item.has_local_theme && String(item.ratingKey) !== String(activeItemKey),
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
      option.value = String(item.ratingKey);
      option.textContent = item.year ? `${item.title} (${item.year})` : item.title;
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
  const sourceRatingKey = Number(document.getElementById('copy-theme-source-item').value);
  if (!sourceRatingKey) {
    showToast('error', 'Please select a source item');
    return;
  }

  const overwrite = document.getElementById('copy-overwrite-check').checked;
  const btn = document.getElementById('btn-confirm-copy-theme');
  btn.disabled = true;
  btn.textContent = 'Copying…';

  try {
    const data = await apiPost(`/api/items/${activeItemKey}/theme/copy`, { sourceRatingKey, overwrite });
    if (data.error && data.exists) {
      showToast('info', 'Theme already exists. Enable overwrite to replace it.');
    } else if (data.success) {
      showToast('success', 'Theme copied successfully!');
      closeModal('modal-copy-theme');
      if (!applyServerItemUpdate(data.item)) {
        await refreshItem(activeItemKey);
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
function openUploadModal(ratingKey, title, hasLocalTheme) {
  activeItemKey = ratingKey;
  document.getElementById('upload-item-title').textContent = title;
  document.getElementById('upload-file-input').value = '';
  document.getElementById('selected-file-name').classList.add('hidden');

  const overwriteDiv = document.getElementById('modal-upload-overwrite');
  if (hasLocalTheme) {
    overwriteDiv.classList.remove('hidden');
    document.getElementById('upload-overwrite-check').checked = false;
  } else {
    overwriteDiv.classList.add('hidden');
  }
  _syncOverwriteActionButton('btn-confirm-upload', 'upload-overwrite-check', hasLocalTheme);

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
    const resp = await fetch(`/api/items/${activeItemKey}/theme/upload`, {
      method: 'POST',
      body: formData,
    });
    const data = await resp.json();
    if (data.error && data.exists) {
      showToast('info', 'Theme already exists. Enable overwrite to replace it.');
    } else if (data.success) {
      showToast('success', 'Theme uploaded successfully!');
      closeModal('modal-upload');
      if (!applyServerItemUpdate(data.item)) {
        await refreshItem(activeItemKey);
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

function openYoutubeModal(ratingKey, title, hasLocalTheme) {
  activeItemKey = ratingKey;
  document.getElementById('youtube-item-title').textContent = title;
  document.getElementById('youtube-url-input').value = '';
  document.getElementById('youtube-progress').classList.add('hidden');
  document.getElementById('youtube-search-results').innerHTML = '';
  _stopYoutubePlayer();

  const overwriteDiv = document.getElementById('modal-youtube-overwrite');
  if (hasLocalTheme) {
    overwriteDiv.classList.remove('hidden');
    document.getElementById('youtube-overwrite-check').checked = false;
  } else {
    overwriteDiv.classList.add('hidden');
  }
  _syncOverwriteActionButton('btn-confirm-youtube', 'youtube-overwrite-check', hasLocalTheme);

  const defaultQuery = `${title} theme song`;
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
    const data = await apiPost(`/api/items/${activeItemKey}/theme/youtube`, { url, overwrite });
    progressEl.classList.add('hidden');
    if (data.error && data.exists) {
      showToast('info', 'Theme already exists. Enable overwrite to replace it.');
    } else if (data.success) {
      showToast('success', 'YouTube theme downloaded successfully!');
      closeModal('modal-youtube');
      if (!applyServerItemUpdate(data.item)) {
        await refreshItem(activeItemKey);
      }
    } else {
      showToast('error', data.error || 'YouTube download failed');
    }
  } catch (err) {
    progressEl.classList.add('hidden');
    showToast('error', String(err));
  }
}

// ============================================================
// Delete Modal
// ============================================================
function openDeleteModal(ratingKey, title) {
  activeItemKey = ratingKey;
  document.getElementById('delete-item-title').textContent = title;
  openModal('modal-delete');
}

async function confirmDelete() {
  try {
    const resp = await fetch(`/api/items/${activeItemKey}/theme`, { method: 'DELETE' });
    const data = await resp.json();
    if (data.success) {
      showToast('success', 'Theme deleted.');
      closeModal('modal-delete');
      if (!applyServerItemUpdate(data.item)) {
        await refreshItem(activeItemKey);
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

  const itemIndex = currentItems.findIndex((item) => String(item.ratingKey) === String(updatedItem.ratingKey));
  if (itemIndex === -1) return false;

  const nextItems = [...currentItems];
  nextItems[itemIndex] = updatedItem;
  currentItems = nextItems;
  libraryCache.set(currentLibraryId, nextItems);

  if (!updatedItem.has_local_theme
      && activeAudio
      && activeAudio.src
      && activeAudio.src.includes(`/api/items/${updatedItem.ratingKey}/theme`)) {
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
    const items = await apiGet(`/api/libraries/${currentLibraryId}/items`);
    libraryCache.set(currentLibraryId, items);
    currentItems = items;
    const updated = items.find((item) => item.ratingKey === ratingKey);
    if (updated) {
      const card = document.getElementById(`card-${ratingKey}`);
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
}

function showSettingsResult(ok, message) {
  const el = document.getElementById('settings-action-result');
  el.className = `settings-action-result ${ok ? 'result-ok' : 'result-err'}`;
  el.textContent = message;
}

async function settingsTestPlex() {
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
  showSettingsResult(true, 'Refreshing Plex libraries…');
  try {
    libraryCache.clear();
    await loadLibraries();
    // Rebuild server-side item cache in the background (don't await — it takes time)
    apiPost('/api/settings/refresh-cache', {}).catch(() => {});
    showSettingsResult(true, '✓ Plex libraries refreshed successfully. Item cache is rebuilding in the background.');
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

function closeModal(id, event) {
  const overlay = document.getElementById(id);
  if (event && event.target !== overlay) return;
  overlay.classList.add('hidden');
  const previewAudio = document.getElementById('preview-audio');
  if (previewAudio) {
    previewAudio.pause();
    previewAudio.src = '';
  }
  if (id === 'modal-youtube') _stopYoutubePlayer();
}

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    const wasYoutubeOpen = !document.getElementById('modal-youtube').classList.contains('hidden');
    document.querySelectorAll('.modal-overlay:not(.hidden)').forEach((modal) => {
      modal.classList.add('hidden');
    });
    if (wasYoutubeOpen) _stopYoutubePlayer();
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
  const resp = await fetch(url);
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
  return data;
}

async function apiPost(url, body) {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return resp.json();
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
