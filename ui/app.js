/* app.js — PDF TextExtract dashboard */

'use strict';

// Constants

const API = '';        // same origin
const WS_URL = `ws://${location.host}/api/ws`;
console.log(WS_URL, "@ws_url");

// State

const state = {
  ws: null,
  wsConnected: false,
  // Pending (not-yet-submitted) files chosen by the user
  pendingFiles: [],      // Array of { file: File, id: string, fileId: null|string }
  // Job file list from server  
  jobFiles: [],          // Array of { name, size_bytes, status, progress_pct, … }
  jobStatus: 'idle',     // idle | running | done | cancelled
  logPaused: false,
  logLines: [],
  presets: JSON.parse(localStorage.getItem('presets') || '[]'),
  settings: JSON.parse(localStorage.getItem('settings') || '{}'),
  elapsedTimer: null,
};

// DOM helpers

const $ = id => document.getElementById(id);
const fmt = {
  bytes(n) {
    if (n >= 1e6) return (n / 1e6).toFixed(1) + ' MB';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + ' KB';
    return n + ' B';
  },
  time(s) {
    if (!s) return '—';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
  },
  eta(s) {
    if (!s) return '—';
    return `~${s}s`;
  },
};

// Toast notifications

function toast(msg, type = 'info', duration = 4000) {
  let container = document.getElementById('toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    document.body.appendChild(container);
  }
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

// Navigation

function showView(name) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const view = document.getElementById(`view-${name}`);
  if (view) view.classList.add('active');
  const nav = document.getElementById(`nav-${name}`);
  if (nav) nav.classList.add('active');
  if (name === 'files') loadExtractedFiles();
  if (name === 'results') loadResults();
}

document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', e => {
    e.preventDefault();
    showView(item.dataset.view);
  });
});

// WebSocket

function connectWS() {
  const ws = new WebSocket(WS_URL);
  state.ws = ws;

  ws.onopen = () => {
    state.wsConnected = true;
    addLog('[INFO] Connected to server.', 'info');
  };

  ws.onmessage = ev => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === 'ping') return;
    if (msg.type === 'state_update') applyServerState(msg);
    if (msg.type === 'log') addLog(msg.message, classifyLog(msg.message));
    if (msg.type === 'file_progress') applyFileProgress(msg);
  };

  ws.onclose = () => {
    state.wsConnected = false;
    addLog('[WARN] Disconnected. Reconnecting in 3s…', 'warn');
    setTimeout(connectWS, 3000);
  };

  ws.onerror = () => ws.close();
}

function classifyLog(msg) {
  if (!msg) return 'info';
  const m = msg.toUpperCase();
  if (m.includes('[OK]') || m.includes('COMPLETED') || m.includes('FINISHED')) return 'ok';
  if (m.includes('[ERROR]') || m.includes('FAILURE') || m.includes('TIMEOUT')) return 'error';
  if (m.includes('[WARN]') || m.includes('WARNING') || m.includes('EMPTY_OUTPUT')) return 'warn';
  return 'info';
}

// Apply server state

function applyServerState(s) {
  state.jobStatus = s.status;
  state.jobFiles = s.files || [];

  // Quick stats
  $('qs-total').textContent = s.total || 0;
  $('qs-done').textContent  = s.done  || 0;
  $('qs-failed').textContent = s.failed || 0;

  // Log lines from server snapshot (only on initial connect)
  if (s.log && Array.isArray(s.log) && state.logLines.length === 0) {
    s.log.forEach(l => addLog(l, classifyLog(l)));
  }

  const running = s.status === 'running';
  const hasFiles = (s.total || 0) > 0;

  // Show processing card whenever we have a running job with files
  $('processing-card').style.display = (running && hasFiles) ? '' : 'none';

  if (running && hasFiles) {
    const pct = s.progress_pct || 0;
    $('overall-progress').style.width = pct + '%';
    $('pct-label').textContent = pct + '%';
    $('tile-elapsed').textContent = fmt.time(s.elapsed);
    $('tile-eta').textContent = fmt.eta(s.eta_seconds);
    $('tile-processed').textContent = `${s.done}/${s.total}`;
    $('tile-remaining').textContent = (s.total - s.done) || 0;

    if (s.current_file) {
      $('current-file-row').style.display = '';
      $('current-file-name').textContent = s.current_file;
    } else {
      $('current-file-row').style.display = 'none';
    }
  }

  // When job starts clear pending list so we show the job files
  if (running) state.pendingFiles = [];

  renderJobFileTable(state.jobFiles, s.status);
  updateElapsedTimer(running);
}

// Elapsed timer (client-side ticking)

let _elapsedBase = 0;
let _elapsedStart = null;

function updateElapsedTimer(running) {
  if (running && !state.elapsedTimer) {
    _elapsedStart = Date.now();
    state.elapsedTimer = setInterval(() => {
      const secs = _elapsedBase + (Date.now() - _elapsedStart) / 1000;
      $('tile-elapsed').textContent = fmt.time(secs);
    }, 1000);
  } else if (!running && state.elapsedTimer) {
    clearInterval(state.elapsedTimer);
    state.elapsedTimer = null;
    _elapsedBase = 0;
  }
}

// File progress (page-level, targeted DOM update — no full re-render)

function applyFileProgress(msg) {
  // Update in-memory state
  const f = state.jobFiles.find(f => f.name === msg.file);
  if (f) {
    f.progress_pct = msg.pct;
    f.current_page = msg.page;
    f.total_pages  = msg.total_pages;
  }
  // Update only the affected row — avoids full table rebuild on every page
  const row = $('file-tbody')?.querySelector(`[data-file="${CSS.escape(msg.file)}"]`);
  if (!row) return;
  const bar     = row.querySelector('.row-progress-bar');
  const pctSpan = row.querySelector('.row-pct');
  if (bar) bar.style.width = msg.pct + '%';
  if (pctSpan) {
    pctSpan.textContent = msg.total_pages > 0
      ? `${msg.page}/${msg.total_pages} (${msg.pct}%)`
      : `${msg.pct}%`;
  }
}

// File table

function renderJobFileTable(files, jobStatus) {
  const card     = $('file-table-card');
  const dropZone = $('drop-zone');

  if (files.length === 0 && state.pendingFiles.length === 0) {
    card.style.display = 'none';
    dropZone.style.display = '';
    return;
  }

  card.style.display = '';
  dropZone.style.display = 'none';

  const isPending = state.pendingFiles.length > 0 && (jobStatus === 'idle' || jobStatus === 'done');
  $('btn-start').style.display = isPending ? '' : 'none';

  // Show pending files (pre-start) or server-reported job files (during/after)
  const displayFiles = isPending
    ? state.pendingFiles.map(p => ({
        name: p.file.name,
        size_bytes: p.file.size,
        status: p.uploaded ? 'uploaded' : 'queued',
        progress_pct: 0, method: '', elapsed: 0,
        char_count: 0, message: '', current_page: 0, total_pages: 0,
      }))
    : files;

  const tbody = $('file-tbody');
  tbody.innerHTML = displayFiles.map((f, i) => `
    <tr data-file="${escHtml(f.name)}">
      <td><input type="checkbox" class="row-chk" data-i="${i}" /></td>
      <td>
        <div class="file-name-cell">
          <span class="file-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg></span>
          ${escHtml(f.name)}
        </div>
      </td>
      <td><span class="badge badge-${f.status}">${f.status}</span></td>
      <td>${fmt.bytes(f.size_bytes)}</td>
      <td>
        <div class="row-progress-track">
          <div class="row-progress-bar" style="width:${f.progress_pct}%"></div>
        </div>
        <span class="row-pct" style="font-size:0.7rem;color:var(--text-muted);margin-left:4px">${
          (f.total_pages || 0) > 0
            ? `${f.current_page}/${f.total_pages} (${f.progress_pct}%)`
            : `${f.progress_pct}%`
        }</span>
      </td>
      <td>
        ${f.status === 'failed' ? `<button class="icon-btn" title="Retry" onclick="retryFile('${escHtml(f.name)}')"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-3.4"/></svg></button>` : ''}
        ${isPending ? `<button class="icon-btn" title="Remove" onclick="removeRow(${i})"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/></svg></button>` : ''}
      </td>
    </tr>
  `).join('');

  $('file-table-title').textContent = `Files (${displayFiles.length})`;
  $('chk-all').addEventListener('change', e => {
    document.querySelectorAll('.row-chk').forEach(c => c.checked = e.target.checked);
  });
}

function removeRow(i) {
  if (state.pendingFiles.length > 0) {
    state.pendingFiles.splice(i, 1);
    renderJobFileTable([], 'idle');
  }
}

function retryFile(name) {
  toast(`Retry for ${name} — not yet implemented.`, 'info');
}

// Log

function addLog(msg, cls = 'info') {
  if (!msg) return;
  // Deduplicate consecutive identical lines
  if (state.logLines[state.logLines.length - 1] === msg) return;
  state.logLines.push(msg);
  if (state.logLines.length > 500) state.logLines.shift();

  const count = state.logLines.length;
  $('log-count').textContent = `${count} entr${count === 1 ? 'y' : 'ies'}`;

  if (state.logPaused) return;

  const body = $('log-body');
  const now = new Date().toLocaleTimeString();
  const line = document.createElement('div');
  line.className = `log-line log-${cls}`;
  line.textContent = `${now}  ${msg}`;
  body.appendChild(line);

  // Keep max 300 DOM lines for performance
  while (body.children.length > 300) body.removeChild(body.firstChild);
  body.scrollTop = body.scrollHeight;
}

$('btn-pause-log').addEventListener('click', () => {
  state.logPaused = !state.logPaused;
  $('btn-pause-log').innerHTML = state.logPaused
    ? `<svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14"><polygon points="5 3 19 12 5 21 5 3"/></svg> Resume`
    : `<svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg> Pause`;
});

// File import

function addPendingFiles(fileList) {
  const newFiles = Array.from(fileList).filter(f => f.name.toLowerCase().endsWith('.pdf'));
  if (!newFiles.length) { toast('No PDF files found in selection.', 'error'); return; }

  newFiles.forEach(f => {
    if (!state.pendingFiles.find(p => p.file.name === f.name && p.file.size === f.size)) {
      state.pendingFiles.push({ file: f, id: crypto.randomUUID(), fileId: null });
    }
  });

  renderJobFileTable([], 'idle');
  $('btn-start').style.display = '';
  toast(`${newFiles.length} file(s) added.`, 'success');
}

$('btn-import-file').addEventListener('click', () => $('input-files').click());
$('btn-import-folder').addEventListener('click', () => $('input-folder').click());
$('input-files').addEventListener('change', e => { addPendingFiles(e.target.files); e.target.value = ''; });
$('input-folder').addEventListener('change', e => { addPendingFiles(e.target.files); e.target.value = ''; });

// Drop zone drag-and-drop
const dropZoneEl = $('drop-zone');
['dragenter','dragover'].forEach(ev => dropZoneEl.addEventListener(ev, e => { e.preventDefault(); dropZoneEl.classList.add('drag-over'); }));
['dragleave','drop'].forEach(ev => dropZoneEl.addEventListener(ev, e => { e.preventDefault(); dropZoneEl.classList.remove('drag-over'); }));
dropZoneEl.addEventListener('drop', e => {
  const files = e.dataTransfer.files;
  if (files?.length) addPendingFiles(files);
});
dropZoneEl.addEventListener('click', () => $('input-files').click());

// Start pipeline

$('btn-start').addEventListener('click', async () => {
  if (!state.pendingFiles.length) return;

  const btn = $('btn-start');
  btn.disabled = true;
  btn.textContent = 'Uploading…';

  try {
    // 1. Upload files
    const formData = new FormData();
    state.pendingFiles.forEach(p => formData.append('files', p.file));
    const upRes = await fetch(`${API}/api/upload`, { method: 'POST', body: formData });
    if (!upRes.ok) throw new Error(`Upload failed: ${upRes.statusText}`);
    const uploaded = await upRes.json();   // [{file_id, name, size_bytes}]

    // Mark pending files as uploaded in the UI
    state.pendingFiles.forEach(p => { p.uploaded = true; });
    renderJobFileTable([], 'idle');
    btn.textContent = 'Checking…';

    // 2. Check which are already processed
    const names = uploaded.map(u => u.name);
    const checkRes = await fetch(`${API}/api/check_files`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ names }),
    });
    const checkData = checkRes.ok ? await checkRes.json() : { processed: {}, unprocessed: names };
    const processedNames = Object.keys(checkData.processed || {});

    // 3. Decide whether to show the reprocess modal
    if (processedNames.length > 0) {
      // Stash upload details for modal callbacks
      state._pendingUpload = { uploaded, processedNames };
      $('reprocess-modal-msg').textContent =
        `${processedNames.length} of ${uploaded.length} file(s) have already been extracted. ` +
        `What would you like to do?`;
      $('reprocess-modal').style.display = '';
      btn.disabled = false;
      btn.textContent = 'Start Extraction';
      return;  // modal callbacks handle the rest
    }

    // 4. No already-processed files — start immediately
    await _doStartJob(uploaded.map(u => u.file_id), false);

  } catch (err) {
    toast(err.message, 'error');
    btn.disabled = false;
    btn.textContent = 'Start Extraction';
  }
});

// Modal: skip already-processed, run only new files
$('modal-btn-skip').addEventListener('click', async () => {
  const { uploaded, processedNames } = state._pendingUpload || {};
  $('reprocess-modal').style.display = 'none';
  if (!uploaded) return;

  // Filter out already-processed file_ids
  const filteredIds = uploaded
    .filter(u => !processedNames.includes(u.name))
    .map(u => u.file_id);

  if (!filteredIds.length) {
    toast('All files already processed. Nothing to do.', 'info');
    // Show already-done results
    showView('results');
    return;
  }
  await _doStartJob(filteredIds, false);
});

// Modal: reprocess everything
$('modal-btn-reprocess').addEventListener('click', async () => {
  const { uploaded } = state._pendingUpload || {};
  $('reprocess-modal').style.display = 'none';
  if (!uploaded) return;
  await _doStartJob(uploaded.map(u => u.file_id), true);
});

// Modal: cancel the whole operation
$('modal-btn-cancel').addEventListener('click', () => {
  $('reprocess-modal').style.display = 'none';
  state._pendingUpload = null;
  state.pendingFiles = [];
  renderJobFileTable([], 'idle');
  toast('Cancelled.', 'info');
});

async function _doStartJob(fileIds, force) {
  const btn = $('btn-start');
  btn.disabled = true;
  btn.textContent = 'Starting…';
  try {
    const settings = collectSettings();
    const res = await fetch(`${API}/api/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids: fileIds, force, settings }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || res.statusText);
    }
    state.pendingFiles = [];
    state._pendingUpload = null;
    _elapsedBase = 0;
    _elapsedStart = Date.now();
    toast('Pipeline started!', 'success');
  } catch (err) {
    toast(err.message, 'error');
    btn.disabled = false;
    btn.textContent = 'Start Extraction';
  }
}

// Cancel

$('btn-cancel').addEventListener('click', async () => {
  if (!confirm('Cancel the running job?')) return;
  await fetch(`${API}/api/cancel`, { method: 'POST' });
  toast('Job cancelled.', 'info');
});

// Settings (UI-only stubs)

function collectSettings() {
  return {
    removeHeader:    $('s-remove-header').checked,
    removeFooter:    $('s-remove-footer').checked,
    removePageNums:  $('s-remove-page-numbers').checked,
    removeNumeric:   $('s-remove-numeric').checked,
    lemmatize:       $('s-lemmatize').checked,
    applyToAll:      $('s-apply-all').checked,
  };
}

function loadSettings() {
  const s = state.settings;
  if (!Object.keys(s).length) return;
  $('s-remove-header').checked    = s.removeHeader    ?? true;
  $('s-remove-footer').checked    = s.removeFooter    ?? true;
  $('s-remove-page-numbers').checked = s.removePageNums ?? false;
  $('s-remove-numeric').checked   = s.removeNumeric   ?? false;
  $('s-lemmatize').checked        = s.lemmatize        ?? false;
  $('s-apply-all').checked        = s.applyToAll       ?? true;
}

['s-remove-header','s-remove-footer','s-remove-page-numbers','s-remove-numeric','s-lemmatize','s-apply-all'].forEach(id => {
  $(id).addEventListener('change', () => {
    state.settings = collectSettings();
    localStorage.setItem('settings', JSON.stringify(state.settings));
  });
});

// Presets─

function renderPresets() {
  const list = $('presets-list');
  list.innerHTML = state.presets.map((p, i) => `
    <div class="preset-chip" onclick="applyPreset(${i})">
      <span>${escHtml(p.name)}</span>
      <span class="preset-chip-del" onclick="deletePreset(event,${i})">✕</span>
    </div>
  `).join('');
}

$('btn-save-preset').addEventListener('click', () => {
  const name = $('preset-name').value.trim();
  if (!name) { toast('Enter a preset name.', 'error'); return; }
  state.presets.push({ name, settings: collectSettings() });
  localStorage.setItem('presets', JSON.stringify(state.presets));
  $('preset-name').value = '';
  renderPresets();
  toast(`Preset "${name}" saved.`, 'success');
});

function applyPreset(i) {
  const p = state.presets[i];
  if (!p) return;
  Object.assign(state.settings, p.settings);
  loadSettings();
  toast(`Preset "${p.name}" applied.`, 'success');
}

function deletePreset(e, i) {
  e.stopPropagation();
  state.presets.splice(i, 1);
  localStorage.setItem('presets', JSON.stringify(state.presets));
  renderPresets();
}

// Extracted files view

async function loadExtractedFiles() {
  const tbody = $('extracted-tbody');
  tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted);padding:1.5rem">Loading…</td></tr>';
  try {
    const res = await fetch(`${API}/api/files`);
    const files = await res.json();
    if (!files.length) {
      tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted);padding:1.5rem">No extracted files yet.</td></tr>';
      return;
    }
    tbody.innerHTML = files.map(f => `
      <tr>
        <td>${escHtml(f.name)}</td>
        <td>${fmt.bytes(f.size_bytes)}</td>
        <td>${new Date(f.modified * 1000).toLocaleString()}</td>
        <td>
          <button class="icon-btn" title="Delete" onclick="deleteExtracted('${escHtml(f.rel_path)}', this)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/></svg>
          </button>
        </td>
      </tr>
    `).join('');
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="4" style="color:var(--error);padding:1rem">${e.message}</td></tr>`;
  }
}

async function deleteExtracted(relPath, btn) {
  if (!confirm(`Delete ${relPath}?`)) return;
  btn.disabled = true;
  try {
    await fetch(`${API}/api/files/${relPath}`, { method: 'DELETE' });
    await loadExtractedFiles();
    toast('File deleted.', 'success');
  } catch (e) {
    toast(e.message, 'error');
    btn.disabled = false;
  }
}

$('btn-refresh-files').addEventListener('click', loadExtractedFiles);

// Results view

async function loadResults() {
  const wrap = $('results-tree-wrap');
  wrap.innerHTML = '<div class="search-empty">Loading…</div>';
  try {
    const res = await fetch(`${API}/api/results`);
    const records = await res.json();
    renderResultsTree(records);
  } catch (e) {
    wrap.innerHTML = `<div class="search-empty" style="color:var(--error)">${e.message}</div>`;
  }
}

function renderResultsTree(records) {
  const wrap = $('results-tree-wrap');
  if (!records.length) {
    wrap.innerHTML = '<div class="search-empty">No extracted results yet. Run the pipeline first.</div>';
    return;
  }

  // Group by directory part of rel_path
  const tree = {};
  records.forEach(r => {
    const parts = r.rel_path.replace(/\.pdf$/i, '').split('/');
    parts.pop();  // remove filename
    const dir = parts.join('/') || '(root)';
    if (!tree[dir]) tree[dir] = [];
    tree[dir].push(r);
  });

  const chevron = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="6 9 12 15 18 9"/></svg>`;

  let html = '';
  Object.keys(tree).sort().forEach(dir => {
    const files = tree[dir];
    const uid = 'dir-' + btoa(dir).replace(/[^a-zA-Z0-9]/g, '');
    html += `
      <div class="tree-dir">
        <div class="tree-dir-header" onclick="toggleDir('${uid}')">
          ${chevron}
          <span class="tree-dir-name">${escHtml(dir)}/</span>
          <span class="badge badge-muted">${files.length}</span>
        </div>
        <div class="tree-dir-body" id="${uid}">
          ${files.map(f => {
            const fname = f.rel_path.replace(/\.pdf$/i, '').split('/').pop();
            const dt = f.processed_at ? new Date(f.processed_at).toLocaleDateString() : '';
            return `
              <div class="tree-file">
                <span class="tree-file-name" title="${escHtml(f.rel_path)}">${escHtml(fname)}</span>
                <span class="badge badge-${f.method || 'muted'}">${f.method || '?'}</span>
                <span class="tree-file-chars">${(f.char_count || 0).toLocaleString()} ch</span>
                <span class="tree-file-date">${dt}</span>
                <button class="btn btn-sm btn-ghost" onclick="viewResult(${f.id})">View</button>
                <button class="icon-btn" title="Remove from results" onclick="deleteResult(${f.id})"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/></svg></button>
              </div>`;
          }).join('')}
        </div>
      </div>`;
  });
  wrap.innerHTML = html;
}

function toggleDir(uid) {
  const body = $(uid);
  const header = body?.previousElementSibling;
  if (!body) return;
  const collapsed = body.classList.toggle('collapsed');
  header?.classList.toggle('collapsed', collapsed);
}

async function viewResult(id) {
  const panel = $('text-viewer-panel');
  const body  = $('viewer-body');
  const meta  = $('viewer-meta');
  panel.style.display = '';
  body.textContent = 'Loading…';
  meta.innerHTML = '';
  try {
    const res = await fetch(`${API}/api/results/${id}`);
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    $('viewer-filename').textContent = data.filename || data.rel_path;
    meta.innerHTML = `
      <span>📄 ${data.method || '?'}</span>
      <span>📝 ${(data.char_count || 0).toLocaleString()} chars</span>
      <span>📃 ${data.page_count || '?'} pages</span>
      <span>🕒 ${data.processed_at ? new Date(data.processed_at).toLocaleString() : ''}</span>`;
    body.textContent = data.content || '(empty)';
  } catch (e) {
    body.textContent = `Error: ${e.message}`;
  }
}

async function deleteResult(id) {
  if (!confirm('Remove this result record? (The .txt file on disk is kept.)')) return;
  await fetch(`${API}/api/results/${id}`, { method: 'DELETE' });
  toast('Record removed.', 'info');
  loadResults();
}

$('btn-close-viewer').addEventListener('click', () => {
  $('text-viewer-panel').style.display = 'none';
});

$('btn-refresh-results').addEventListener('click', loadResults);

// Search

async function doSearch(q) {
  if (!q.trim()) return;
  const results = $('search-results');
  results.innerHTML = '<div class="search-empty">Searching…</div>';
  try {
    const res = await fetch(`${API}/api/search?q=${encodeURIComponent(q)}`);
    const data = await res.json();
    if (!data.results.length) {
      results.innerHTML = `<div class="search-empty">No results for "<strong>${escHtml(q)}</strong>"</div>`;
      return;
    }
    results.innerHTML = data.results.map(r => `
      <div class="search-result">
        <div class="search-result-file">${escHtml(r.file)}</div>
        <div class="search-result-snippet">${highlightSnippet(r.snippet, q)}</div>
      </div>
    `).join('');
  } catch (e) {
    results.innerHTML = `<div class="search-empty" style="color:var(--error)">${e.message}</div>`;
  }
}

function highlightSnippet(text, q) {
  const escaped = escHtml(text);
  const re = new RegExp(`(${escHtml(q).replace(/[.*+?^${}()|[\]\\]/g,'\\$&')})`, 'gi');
  return escaped.replace(re, '<mark>$1</mark>');
}

$('btn-search').addEventListener('click', () => doSearch($('search-main').value));
$('search-main').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(e.target.value); });

// Topbar search → switch to search view
$('search-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    showView('search');
    $('search-main').value = e.target.value;
    doSearch(e.target.value);
  }
});

// Utility─

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// Init

loadSettings();
renderPresets();
connectWS();

// Poll status once on load to sync initial state (in case page refreshed mid-job)
fetch(`${API}/api/status`).then(r => r.json()).then(applyServerState).catch(() => {});
