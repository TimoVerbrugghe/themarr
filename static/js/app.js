/* ============================================================
   Themarr - Frontend Application
   ============================================================ */

// State
let currentLibraryId = null;
let currentItems = [];
let activeFilter = 'all';
let activeItemKey = null;
const selectedItems = new Set();  // ratingKeys of currently selected items

// ============================================================
// Init
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
  checkPlexStatus();
  loadLibraries();
});

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
      txt.textContent = data.server_name || 'Connected';
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
  document.getElementById('library-view').classList.remove('hidden');
  document.getElementById('library-title').textContent = title;
  document.getElementById('library-stats').innerHTML = '';
  document.getElementById('items-grid').innerHTML = '';
  document.getElementById('items-loading').classList.remove('hidden');
  document.getElementById('search-input').value = '';

  document.querySelectorAll('.filter-buttons .btn').forEach((button) => button.classList.remove('active'));
  document.getElementById('filter-all').classList.add('active');

  // Clear selection when switching libraries
  selectedItems.clear();
  updateBulkBar();

  try {
    const items = await apiGet(`/api/libraries/${id}/items`);
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

  filtered.forEach((item) => grid.appendChild(createItemCard(item)));
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
    const audioWrap = document.createElement('div');
    audioWrap.className = 'item-audio-player';
    const audio = document.createElement('audio');
    audio.controls = true;
    audio.preload = 'none';
    audio.src = `/api/items/${item.ratingKey}/theme`;
    audioWrap.appendChild(audio);
    body.appendChild(audioWrap);
  }

  const actions = document.createElement('div');
  actions.className = 'item-actions';

  const downloadButton = createActionButton('action-btn action-btn-download', 'Download from Plex', '↓');
  downloadButton.disabled = !item.has_plex_theme;
  downloadButton.addEventListener('click', () => openDownloadModal(item.ratingKey, item.title, item.has_local_theme, item.has_plex_theme));
  actions.appendChild(downloadButton);

  const uploadButton = createActionButton('action-btn action-btn-upload', 'Upload theme', '↑');
  uploadButton.addEventListener('click', () => openUploadModal(item.ratingKey, item.title, item.has_local_theme));
  actions.appendChild(uploadButton);

  const youtubeButton = createActionButton('action-btn action-btn-youtube', 'Download from YouTube', '▶YT');
  youtubeButton.addEventListener('click', () => openYoutubeModal(item.ratingKey, item.title, item.has_local_theme));
  actions.appendChild(youtubeButton);

  const deleteButton = createActionButton('action-btn action-btn-delete', 'Delete theme', '🗑');
  deleteButton.disabled = !item.has_local_theme;
  deleteButton.addEventListener('click', () => openDeleteModal(item.ratingKey, item.title));
  actions.appendChild(deleteButton);

  body.appendChild(actions);
  card.appendChild(poster);
  card.appendChild(body);
  return card;
}

function createActionButton(className, title, text) {
  const button = document.createElement('button');
  button.className = className;
  button.title = title;
  button.type = 'button';
  button.textContent = text;
  return button;
}

function posterPlaceholder(type, title) {
  const icon = type === 'show' ? '📺' : '🎬';
  return `<div class="poster-placeholder">${icon}<span>${escHtml(title)}</span></div>`;
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
    const s = data.success?.length ?? 0;
    const sk = data.skipped?.length ?? 0;
    const f = data.failed?.length ?? 0;
    const n = data.no_theme?.length ?? 0;
    showToast('success', `Bulk done: ${s} downloaded, ${sk} skipped, ${n} no theme, ${f} failed`);
    // Refresh item cards for successfully downloaded items
    if (s > 0 && currentLibraryId) {
      const items = await apiGet(`/api/libraries/${currentLibraryId}/items`);
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
    currentItems = items;
    const updated = items.find((item) => item.ratingKey === ratingKey);
    if (updated) {
      const card = document.getElementById(`card-${ratingKey}`);
      if (card) {
        card.replaceWith(createItemCard(updated));
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
