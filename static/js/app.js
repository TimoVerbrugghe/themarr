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

// Action button label HTML: [icon, grid-label, list-label]
const BTN_PLEX    = [ICON_PLEX,    `${ICON_PLEX} Plex`,    `${ICON_PLEX} Download from Plex`];
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

// ============================================================
// Init
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  setView(currentView);  // apply default view and sync button active states
  checkPlexStatus();
  loadLibraries();
});

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
  image.onerror = () => {
    const placeholder = document.createElement('div');
    placeholder.innerHTML = posterPlaceholder(item.type, item.title);
    image.replaceWith(placeholder.firstChild);
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

  // Button order: Download from Plex → YouTube → Upload → Delete
  const downloadButton = createActionButton('action-btn action-btn-download', 'Download from Plex', BTN_PLEX[1]);
  downloadButton.disabled = !item.has_plex_theme;
  downloadButton.addEventListener('click', () => openDownloadModal(item.ratingKey, item.title, item.has_local_theme, item.has_plex_theme));
  actions.appendChild(downloadButton);

  const youtubeButton = createActionButton('action-btn action-btn-youtube', 'Download from YouTube', BTN_YOUTUBE[1]);
  youtubeButton.addEventListener('click', () => openYoutubeModal(item.ratingKey, item.title, item.has_local_theme));
  actions.appendChild(youtubeButton);

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
  actions.className = 'item-actions item-actions-row';

  // Button order: Download from Plex → YouTube → Upload → Delete
  const downloadButton = createActionButton('action-btn action-btn-download', 'Download from Plex', BTN_PLEX[2]);
  downloadButton.disabled = !item.has_plex_theme;
  downloadButton.addEventListener('click', () => openDownloadModal(item.ratingKey, item.title, item.has_local_theme, item.has_plex_theme));
  actions.appendChild(downloadButton);

  const youtubeButton = createActionButton('action-btn action-btn-youtube', 'Download from YouTube', BTN_YOUTUBE[2]);
  youtubeButton.addEventListener('click', () => openYoutubeModal(item.ratingKey, item.title, item.has_local_theme));
  actions.appendChild(youtubeButton);

  const uploadButton = createActionButton('action-btn action-btn-upload', 'Upload custom theme', BTN_UPLOAD[2]);
  uploadButton.addEventListener('click', () => openUploadModal(item.ratingKey, item.title, item.has_local_theme));
  actions.appendChild(uploadButton);

  const deleteButton = createActionButton('action-btn action-btn-delete', 'Delete theme', '🗑 Delete');
  deleteButton.disabled = !item.has_local_theme;
  deleteButton.addEventListener('click', () => openDeleteModal(item.ratingKey, item.title));
  actions.appendChild(deleteButton);

  row.appendChild(actions);
  return row;
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

async function bulkDownload(overwrite) {
  if (selectedItems.size === 0) return;
  const ratingKeys = Array.from(selectedItems);
  const btn = overwrite
    ? document.querySelectorAll('#bulk-bar .btn')[1]
    : document.querySelectorAll('#bulk-bar .btn')[0];
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
      await refreshItem(activeItemKey);
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
      await refreshItem(activeItemKey);
    } else {
      showToast('error', data.error || 'Upload failed');
    }
  } catch (err) {
    showToast('error', String(err));
  }
}

// ============================================================
// YouTube Modal
// ============================================================
function openYoutubeModal(ratingKey, title, hasLocalTheme) {
  activeItemKey = ratingKey;
  document.getElementById('youtube-item-title').textContent = title;
  document.getElementById('youtube-url-input').value = '';
  document.getElementById('youtube-progress').classList.add('hidden');

  const overwriteDiv = document.getElementById('modal-youtube-overwrite');
  if (hasLocalTheme) {
    overwriteDiv.classList.remove('hidden');
    document.getElementById('youtube-overwrite-check').checked = false;
  } else {
    overwriteDiv.classList.add('hidden');
  }

  openModal('modal-youtube');
}

async function confirmYoutube() {
  const url = document.getElementById('youtube-url-input').value.trim();
  if (!url) {
    showToast('error', 'Please enter a YouTube URL');
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
      await refreshItem(activeItemKey);
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
      await refreshItem(activeItemKey);
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
}

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    document.querySelectorAll('.modal-overlay:not(.hidden)').forEach((modal) => {
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
