/* app.js — PDF TextExtract dashboard
 *
 *  - All endpoints → /api/v1/  (versioned)
 *  - SSE state_update no longer carries files[] → fetched via GET /api/v1/job/files
 *  - All data tables are server-side paginated (no endless scroll)
 *  - Dedup: browser computes SHA-256 of first 64 KB using Web Crypto API
 *    then POSTs to /check-hashes BEFORE uploading — files already extracted
 *    are never uploaded at all.
 *  - Upload sends file_ids to /api/v1/job/start (chunked on the server)
 */

'use strict';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const API          = '/api/v1';
const SSE_URL      = `${location.origin}/api/events`;
const HASH_HEAD    = 32 * 1024;   // 32 KB head — matches server HEAD_SAMPLE_BYTES
const HASH_TAIL    = 32 * 1024;   // 32 KB tail — matches server TAIL_SAMPLE_BYTES
const HASH_BYTES   = HASH_HEAD + HASH_TAIL;  // 64 KB total (kept for reference)
const DEFAULT_SIZE = 50;

// ---------------------------------------------------------------------------
// Application state
// ---------------------------------------------------------------------------

const state = {
  es:            null,    // EventSource
  sseConnected:  false,
  jobStatus:     'idle',  // idle | running | done | cancelled

  // Pending (pre-upload) files chosen by the user
  pendingFiles:  [],      // [{ file: File, id: string, hash: string|null, fileId: string|null }]

  // Are we currently hashing files (pre-upload)
  hashing:       false,

  logPaused:     false,
  logLines:      [],
  elapsedTimer:  null,

  // Per-table pagination state
  pages: {
    jobFiles:     { page: 1, size: DEFAULT_SIZE, total: 0, pages: 1 },
    pendingFiles: { page: 1, size: DEFAULT_SIZE },   // local pagination
    results:      { page: 1, size: DEFAULT_SIZE, total: 0, pages: 1 },
    files:        { page: 1, size: DEFAULT_SIZE, total: 0, pages: 1 },
    search:       { page: 1, size: 20,           total: 0, pages: 1, query: '' },
    runs:         { page: 1, size: 10,           total: 0, pages: 1 },
    runFiles:     { page: 1, size: 50,           total: 0, pages: 1, runId: '' },
  },

  presets:  JSON.parse(localStorage.getItem('presets')  || '[]'),
  settings: JSON.parse(localStorage.getItem('settings') || '{}'),

  // Stashed during modal flow
  _pendingUpload: null,
};

// ---------------------------------------------------------------------------
// DOM shortcuts
// ---------------------------------------------------------------------------

const $ = id => document.getElementById(id);
const fmt = {
  bytes(n) {
    if (n >= 1e6) return (n / 1e6).toFixed(1) + ' MB';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + ' KB';
    return n + ' B';
  },
  time(s) {
    if (!s) return '—';
    const m   = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
  },
  eta(s) { return s ? `~${s}s` : '—'; },
  date(ts) { return ts ? new Date(ts).toLocaleString() : '—'; },
};

function escHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ---------------------------------------------------------------------------
// Toast notifications
// ---------------------------------------------------------------------------

function toast(msg, type = 'info', duration = 4000) {
  let c = $('toast-container');
  if (!c) {
    c = document.createElement('div');
    c.id = 'toast-container';
    document.body.appendChild(c);
  }
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  c.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------

function showView(name) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const view = $(`view-${name}`);
  if (view) view.classList.add('active');
  const nav  = $(`nav-${name}`);
  if (nav)  nav.classList.add('active');
  if (name === 'files')   loadExtractedFiles();
  if (name === 'results') loadResults();
  if (name === 'runs')    loadRuns();
}

document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', e => {
    e.preventDefault();
    showView(item.dataset.view);
  });
});

// ---------------------------------------------------------------------------
// Pagination helper — creates / updates all four pagination UIs
// ---------------------------------------------------------------------------

/**
 * Build or refresh a pagination bar.
 *
 * @param {string}   key      state.pages key (jobFiles|results|files|search)
 * @param {string}   prevId   DOM id of prev button
 * @param {string}   nextId   DOM id of next button
 * @param {string}   infoId   DOM id of page-info span
 * @param {string}   barId    DOM id of the .pagination container
 * @param {Function} fetchFn  () => Promise<void>  called after page change
 * @param {string}   [sizeId] DOM id of the page-size <select> (optional)
 */
function setupPagination(key, prevId, nextId, infoId, barId, fetchFn, sizeId) {
  const update = () => {
    const p = state.pages[key];
    $(infoId).textContent = `Page ${p.page} of ${Math.max(p.pages, 1)}`;
    $(prevId).disabled = p.page <= 1;
    $(nextId).disabled = p.page >= p.pages;
    $(barId).style.display = p.total > 0 ? '' : 'none';
  };

  $(prevId).addEventListener('click', () => {
    if (state.pages[key].page > 1) { state.pages[key].page--; fetchFn(); }
  });
  $(nextId).addEventListener('click', () => {
    if (state.pages[key].page < state.pages[key].pages) { state.pages[key].page++; fetchFn(); }
  });
  if (sizeId) {
    $(sizeId).addEventListener('change', e => {
      state.pages[key].size = parseInt(e.target.value, 10);
      state.pages[key].page = 1;
      fetchFn();
    });
  }

  // Make update accessible for callers
  return update;
}

// jobFiles pagination routes to pending or running depending on state
function _jobFilesPage() {
  if (state.pendingFiles.length > 0 && state.jobStatus !== 'running') {
    // Sync the shared jobFiles page/size INTO pendingFiles so prev/next/size controls work
    state.pages.pendingFiles.page = state.pages.jobFiles.page;
    state.pages.pendingFiles.size = state.pages.jobFiles.size;
    renderPendingFileRows();
  } else {
    fetchJobFiles();
  }
}

// Wire up all pagination bars (functions called after data arrives)
const updateJobFilesPagination = setupPagination(
  'jobFiles', 'job-files-prev', 'job-files-next',
  'job-files-page-info', 'job-files-pagination',
  _jobFilesPage, 'job-files-page-size',
);
const updateResultsPagination = setupPagination(
  'results', 'results-prev', 'results-next',
  'results-page-info', 'results-pagination',
  loadResults, 'results-page-size',
);
const updateFilesPagination = setupPagination(
  'files', 'files-prev', 'files-next',
  'files-page-info', 'files-pagination',
  loadExtractedFiles, 'files-page-size',
);
const updateSearchPagination = setupPagination(
  'search', 'search-prev', 'search-next',
  'search-page-info', 'search-pagination',
  () => doSearch(state.pages.search.query),
);

// ---------------------------------------------------------------------------
// SSE — EventSource
// ---------------------------------------------------------------------------

function connectSSE() {
  if (state.es) { state.es.close(); state.es = null; }

  const es = new EventSource(SSE_URL);
  state.es = es;

  es.onopen = () => {
    state.sseConnected = true;
    addLog('[INFO] Connected to server.', 'info');
  };

  es.onmessage = ev => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }

    switch (msg.type) {
      case 'state_update':   applyServerState(msg); break;
      case 'log':            addLog(msg.message, classifyLog(msg.message)); break;
      case 'file_progress':  applyFileProgress(msg); break;
      // keepalive comments ': keepalive' are invisible to onmessage
    }
  };

  es.onerror = () => {
    state.sseConnected = false;
    addLog('[WARN] SSE interrupted — browser will reconnect automatically.', 'warn');
  };
}

function classifyLog(msg) {
  if (!msg) return 'info';
  const m = msg.toUpperCase();
  if (m.includes('[OK]') || m.includes('COMPLETED') || m.includes('FINISHED')) return 'ok';
  if (m.includes('[ERROR]') || m.includes('FAILURE') || m.includes('TIMEOUT'))  return 'error';
  if (m.includes('[WARN]')  || m.includes('WARNING') || m.includes('EMPTY'))    return 'warn';
  return 'info';
}

// ---------------------------------------------------------------------------
// Apply server state (metadata only — no files array)
// ---------------------------------------------------------------------------

function applyServerState(s) {
  state.jobStatus = s.status;

  // Quick stats sidebar
  $('qs-total').textContent  = s.total  || 0;
  $('qs-done').textContent   = s.done   || 0;
  $('qs-failed').textContent = s.failed || 0;

  // Log snapshot on initial connect
  if (s.log && Array.isArray(s.log) && state.logLines.length === 0) {
    s.log.forEach(l => addLog(l, classifyLog(l)));
  }

  const running  = s.status === 'running';
  const hasFiles = (s.total || 0) > 0;

  $('processing-card').style.display = (running && hasFiles) ? '' : 'none';

  if (running && hasFiles) {
    const pct = s.progress_pct || 0;
    $('overall-progress').style.width   = pct + '%';
    $('pct-label').textContent          = pct + '%';
    $('tile-elapsed').textContent       = fmt.time(s.elapsed);
    $('tile-eta').textContent           = fmt.eta(s.eta_seconds);
    $('tile-processed').textContent     = `${s.done}/${s.total}`;
    $('tile-remaining').textContent     = Math.max(0, (s.total - s.done));

    if (s.current_file) {
      $('current-file-row').style.display = '';
      $('current-file-name').textContent  = s.current_file;
    } else {
      $('current-file-row').style.display = 'none';
    }

    // Fetch the current page of job files from the API
    fetchJobFiles();
  }

  // Once running, clear pending files from view
  if (running) state.pendingFiles = [];

  updateElapsedTimer(running);
  refreshFileTableVisibility();
}

// ---------------------------------------------------------------------------
// Elapsed timer (client-side seconds tick)
// ---------------------------------------------------------------------------

let _elapsedBase  = 0;
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

// ---------------------------------------------------------------------------
// Job files — fetched from GET /api/v1/job/files (server-side pagination)
// ---------------------------------------------------------------------------

async function fetchJobFiles() {
  if (state.jobStatus !== 'running') return;
  const p = state.pages.jobFiles;
  try {
    const res  = await fetch(`${API}/job/files?page=${p.page}&size=${p.size}`);
    const data = await res.json();
    p.total = data.total;
    p.pages = data.pages;
    renderJobFileRows(data.items);
    updateJobFilesPagination();
  } catch (e) {
    console.warn('fetchJobFiles failed:', e);
  }
}

// ---------------------------------------------------------------------------
// File progress — targeted row update (no full re-render per page event)
// ---------------------------------------------------------------------------

function applyFileProgress(msg) {
  const row = $('file-tbody')?.querySelector(`[data-file="${CSS.escape(msg.file)}"]`);
  if (!row) return;
  const bar     = row.querySelector('.row-progress-bar');
  const pctSpan = row.querySelector('.row-pct');
  if (bar)     bar.style.width   = msg.pct + '%';
  if (pctSpan) pctSpan.textContent = msg.total_pages > 0
    ? `${msg.page}/${msg.total_pages} (${msg.pct}%)`
    : `${msg.pct}%`;
}

// ---------------------------------------------------------------------------
// File table rendering
// ---------------------------------------------------------------------------

function refreshFileTableVisibility() {
  const card     = $('file-table-card');
  const dropZone = $('drop-zone');
  const hasPending = state.pendingFiles.length > 0;
  const isRunning  = state.jobStatus === 'running';

  if (!hasPending && !isRunning) {
    card.style.display     = 'none';
    dropZone.style.display = '';
    return;
  }

  card.style.display     = '';
  dropZone.style.display = 'none';
  $('btn-start').style.display = hasPending && !isRunning ? '' : 'none';

  if (hasPending) {
    renderPendingFileRows();
  }
  // If running, fetchJobFiles() already called from applyServerState
}

function renderPendingFileRows() {
  const pp     = state.pages.pendingFiles;
  const all    = state.pendingFiles;
  const total  = all.length;
  const pages  = Math.max(1, Math.ceil(total / pp.size));
  pp.page      = Math.min(pp.page, pages);
  const offset = (pp.page - 1) * pp.size;
  const slice  = all.slice(offset, offset + pp.size);

  const tbody = $('file-tbody');
  $('file-table-title').textContent =
    `Files — Page ${pp.page} of ${pages} (${total} pending)`;

  // Show/update pagination bar
  const bar = $('job-files-pagination');
  bar.style.display = total > 0 ? '' : 'none';
  $('job-files-page-info').textContent = `Page ${pp.page} of ${pages}`;
  $('job-files-prev').disabled = pp.page <= 1;
  $('job-files-next').disabled = pp.page >= pages;

  // Update jobFiles pagination state so the shared controls work
  state.pages.jobFiles.page  = pp.page;
  state.pages.jobFiles.total = total;
  state.pages.jobFiles.pages = pages;

  tbody.innerHTML = slice.map((p, i) => {
    const globalIdx = offset + i;
    return `
    <tr data-file="${escHtml(p.file.name)}">
      <td><input type="checkbox" class="row-chk" data-i="${globalIdx}" /></td>
      <td>
        <div class="file-name-cell">
          <span class="file-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg></span>
          ${escHtml(p.file.name)}
          ${p.hash ? `<span class="badge badge-muted" title="SHA-256: ${escHtml(p.hash)}" style="margin-left:4px;font-size:0.65rem">✓ hashed</span>` : '<span class="badge badge-queued" style="margin-left:4px;font-size:0.65rem">hashing…</span>'}
        </div>
      </td>
      <td><span class="badge badge-queued">queued</span></td>
      <td>${fmt.bytes(p.file.size)}</td>
      <td><div class="row-progress-track"><div class="row-progress-bar" style="width:0%"></div></div><span class="row-pct" style="font-size:0.7rem;color:var(--text-muted);margin-left:4px">0%</span></td>
      <td>
        <button class="icon-btn" title="Remove" onclick="removePending(${globalIdx})">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/></svg>
        </button>
      </td>
    </tr>`;
  }).join('');

  $('chk-all').addEventListener('change', e => {
    document.querySelectorAll('.row-chk').forEach(c => c.checked = e.target.checked);
  });
}

function renderJobFileRows(items) {
  const p = state.pages.jobFiles;
  $('file-table-title').textContent =
    `Files — Page ${p.page} of ${Math.max(p.pages, 1)} (${p.total} total)`;

  const tbody = $('file-tbody');
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted);padding:1.5rem">No files on this page.</td></tr>';
    return;
  }
  tbody.innerHTML = items.map(f => `
    <tr data-file="${escHtml(f.name)}">
      <td></td>
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
      </td>
    </tr>
  `).join('');
}

function removePending(i) {
  state.pendingFiles.splice(i, 1);
  refreshFileTableVisibility();
}

function retryFile(name) { toast(`Retry for ${name} — not yet implemented.`, 'info'); }

// ---------------------------------------------------------------------------
// SHA-256 of first 32 KB + last 32 KB — mirrors server compute_pdf_hash.
// • file ≤ 64 KB  → hash entire file (same as server boundary behaviour)
// • file >  64 KB → SHA-256(head[0..32KB] || tail[last 32KB])
// ---------------------------------------------------------------------------

async function hashFile(file) {
  let buf;
  if (file.size <= HASH_BYTES) {
    // Small file: hash the entire content — server reads head then tail starting
    // at the head end, so all bytes are fed into SHA-256 exactly once.
    buf = await file.arrayBuffer();
  } else {
    // Large file: concatenate head + tail slices into one 64 KB buffer
    const headBuf = await file.slice(0, HASH_HEAD).arrayBuffer();
    const tailBuf = await file.slice(file.size - HASH_TAIL).arrayBuffer();
    const combined = new Uint8Array(HASH_HEAD + HASH_TAIL);
    combined.set(new Uint8Array(headBuf), 0);
    combined.set(new Uint8Array(tailBuf), HASH_HEAD);
    buf = combined.buffer;
  }
  const digest = await crypto.subtle.digest('SHA-256', buf);
  return Array.from(new Uint8Array(digest))
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');
}

// ---------------------------------------------------------------------------
// Import / add files
// ---------------------------------------------------------------------------

async function addPendingFiles(fileList) {
  const newFiles = Array.from(fileList).filter(f => f.name.toLowerCase().endsWith('.pdf'));
  if (!newFiles.length) { toast('No PDF files found in selection.', 'error'); return; }

  const added = [];
  for (const f of newFiles) {
    if (!state.pendingFiles.find(p => p.file.name === f.name && p.file.size === f.size)) {
      added.push({ file: f, id: crypto.randomUUID(), hash: null, fileId: null });
      state.pendingFiles.push(added[added.length - 1]);
    }
  }

  refreshFileTableVisibility();
  $('btn-start').style.display = '';
  toast(`${added.length} file(s) added. Hashing…`, 'info');

  // Hash files asynchronously in the background (doesn't block UI)
  state.hashing = true;
  for (const entry of added) {
    try {
      entry.hash = await hashFile(entry.file);
    } catch {
      entry.hash = null;
    }
  }
  state.hashing = false;
  refreshFileTableVisibility();
  toast(`${added.length} file(s) ready.`, 'success');
}

$('btn-import-file').addEventListener('click',   () => $('input-files').click());
$('btn-import-folder').addEventListener('click', () => $('input-folder').click());
$('input-files').addEventListener('change', e => { addPendingFiles(e.target.files); e.target.value = ''; });
$('input-folder').addEventListener('change', e => { addPendingFiles(e.target.files); e.target.value = ''; });

// Drag-and-drop
const dropZoneEl = $('drop-zone');
['dragenter','dragover'].forEach(ev => dropZoneEl.addEventListener(ev, e => {
  e.preventDefault(); dropZoneEl.classList.add('drag-over');
}));
['dragleave','drop'].forEach(ev => dropZoneEl.addEventListener(ev, e => {
  e.preventDefault(); dropZoneEl.classList.remove('drag-over');
}));
dropZoneEl.addEventListener('drop', e => { if (e.dataTransfer.files?.length) addPendingFiles(e.dataTransfer.files); });
dropZoneEl.addEventListener('click', () => $('input-files').click());

// ---------------------------------------------------------------------------
// Start pipeline — hash-first flow avoids uploading already-extracted files
// ---------------------------------------------------------------------------

$('btn-start').addEventListener('click', async () => {
  if (!state.pendingFiles.length) return;

  const btn = $('btn-start');
  btn.disabled   = true;
  btn.textContent = 'Checking hashes…';

  try {
    // 1. Collect hashes for the bulk check (wait for any still-running hashing)
    const hashes = await Promise.all(
      state.pendingFiles.map(p => p.hash ? Promise.resolve(p.hash) : hashFile(p.file).then(h => { p.hash = h; return h; }))
    );

    // 2. Bulk hash check — which are already in the DB?
    const checkRes = await fetch(`${API}/upload/check-hashes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hashes }),
    });
    const checkData = checkRes.ok
      ? await checkRes.json()
      : { already_processed: {}, unprocessed: hashes };

    const alreadyHashes = Object.keys(checkData.already_processed || {});

    // 3. Show reprocess modal if some are already done
    if (alreadyHashes.length > 0) {
      state._pendingUpload = { hashes, alreadyHashes };
      $('reprocess-modal-msg').textContent =
        `${alreadyHashes.length} of ${hashes.length} file(s) have already been extracted. What would you like to do?`;
      $('reprocess-modal').style.display = '';
      btn.disabled    = false;
      btn.textContent = 'Start Extraction';
      return;
    }

    // 4. Upload only unprocessed files (no already-done files touched at all)
    await _uploadAndStart(
      state.pendingFiles.filter(p => !alreadyHashes.includes(p.hash)),
      false,
    );
  } catch (err) {
    toast(err.message, 'error');
    btn.disabled    = false;
    btn.textContent = 'Start Extraction';
  }
});

// Modal actions -----------------------------------------------------------

$('modal-btn-skip').addEventListener('click', async () => {
  $('reprocess-modal').style.display = 'none';
  const { alreadyHashes } = state._pendingUpload || {};
  if (!alreadyHashes) return;
  const toUpload = state.pendingFiles.filter(p => !alreadyHashes.includes(p.hash));
  if (!toUpload.length) {
    toast('All files already processed. Nothing to do.', 'info');
    showView('results');
    return;
  }
  await _uploadAndStart(toUpload, false);
});

$('modal-btn-reprocess').addEventListener('click', async () => {
  $('reprocess-modal').style.display = 'none';
  await _uploadAndStart(state.pendingFiles, true);
});

$('modal-btn-cancel').addEventListener('click', () => {
  $('reprocess-modal').style.display = 'none';
  state._pendingUpload = null;
  state.pendingFiles   = [];
  refreshFileTableVisibility();
  toast('Cancelled.', 'info');
});

// Upload + start helper ---------------------------------------------------

const UPLOAD_BATCH_SIZE = 100;   // files per multipart POST — keeps browser memory flat

async function _uploadAndStart(filesToUpload, force) {
  const btn = $('btn-start');
  btn.disabled    = true;

  try {
    // 1. Send files in batches — avoids a single enormous multipart request
    //    that would OOM the browser tab with 16k files.
    const batches  = [];
    for (let i = 0; i < filesToUpload.length; i += UPLOAD_BATCH_SIZE)
      batches.push(filesToUpload.slice(i, i + UPLOAD_BATCH_SIZE));

    const allUploaded = [];
    for (let i = 0; i < batches.length; i++) {
      btn.textContent = `Uploading batch ${i + 1} / ${batches.length}…`;
      const fd = new FormData();
      batches[i].forEach(p => fd.append('files', p.file, p.file.name));
      const upRes = await fetch(`${API}/upload`, { method: 'POST', body: fd });
      if (!upRes.ok) throw new Error(`Upload batch ${i + 1} failed (${upRes.status}): ${upRes.statusText}`);
      const batchResult = await upRes.json();
      allUploaded.push(...batchResult);
      // Cache file_ids back so retry/debug is possible
      batchResult.forEach(u => {
        const p = filesToUpload.find(f => f.file.name === u.name);
        if (p) p.fileId = u.file_id;
      });
    }
    const uploaded = allUploaded;

    btn.textContent = 'Starting…';

    // 3. Start the job
    const settings = collectSettings();
    const startRes = await fetch(`${API}/job/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_ids: uploaded.map(u => u.file_id), force, settings }),
    });
    if (!startRes.ok) {
      const err = await startRes.json().catch(() => ({}));
      throw new Error(err.detail || startRes.statusText);
    }

    state.pendingFiles   = [];
    state._pendingUpload = null;
    _elapsedBase         = 0;
    _elapsedStart        = Date.now();
    toast('Pipeline started!', 'success');
  } catch (err) {
    toast(err.message, 'error');
    btn.disabled    = false;
    btn.textContent = 'Start Extraction';
  }
}

// ---------------------------------------------------------------------------
// Cancel
// ---------------------------------------------------------------------------

$('btn-cancel').addEventListener('click', async () => {
  if (!confirm('Cancel the running job?')) return;
  await fetch(`${API}/job/cancel`, { method: 'POST' });
  toast('Job cancelled.', 'info');
});

// ---------------------------------------------------------------------------
// Activity log
// ---------------------------------------------------------------------------

function addLog(msg, cls = 'info') {
  if (!msg) return;
  if (state.logLines[state.logLines.length - 1] === msg) return;  // deduplicate
  state.logLines.push(msg);
  if (state.logLines.length > 500) state.logLines.shift();

  const count = state.logLines.length;
  $('log-count').textContent = `${count} entr${count === 1 ? 'y' : 'ies'}`;

  if (state.logPaused) return;

  const body = $('log-body');
  const now  = new Date().toLocaleTimeString();
  const line = document.createElement('div');
  line.className   = `log-line log-${cls}`;
  line.textContent = `${now}  ${msg}`;
  body.appendChild(line);

  while (body.children.length > 300) body.removeChild(body.firstChild);
  body.scrollTop = body.scrollHeight;
}

$('btn-pause-log').addEventListener('click', () => {
  state.logPaused = !state.logPaused;
  $('btn-pause-log').innerHTML = state.logPaused
    ? `<svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14"><polygon points="5 3 19 12 5 21 5 3"/></svg> Resume`
    : `<svg viewBox="0 0 24 24" fill="currentColor" width="14" height="14"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg> Pause`;
});

// ---------------------------------------------------------------------------
// Results view — GET /api/v1/results (server-side pagination)
// ---------------------------------------------------------------------------

async function loadResults() {
  const wrap = $('results-tree-wrap');
  wrap.innerHTML = '<div class="search-empty">Loading…</div>';
  const p = state.pages.results;
  try {
    const res  = await fetch(`${API}/results?page=${p.page}&size=${p.size}`);
    const data = await res.json();
    p.total = data.total;
    p.pages = data.pages;
    renderResultsTree(data.items);
    updateResultsPagination();
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

  // Group by directory
  const tree = {};
  records.forEach(r => {
    const parts = r.rel_path.replace(/\.pdf$/i, '').split('/');
    parts.pop();
    const dir = parts.join('/') || '(root)';
    (tree[dir] = tree[dir] || []).push(r);
  });

  const chevron = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="6 9 12 15 18 9"/></svg>`;
  let html = '';

  Object.keys(tree).sort().forEach(dir => {
    const files = tree[dir];
    const uid   = 'dir-' + btoa(encodeURIComponent(dir)).replace(/[^a-zA-Z0-9]/g, '');
    html += `
      <div class="tree-dir">
        <div class="tree-dir-header" onclick="toggleDir('${uid}')">
          ${chevron}
          <span class="tree-dir-name">${escHtml(dir)}/</span>
          <span class="badge badge-muted">${files.length}</span>
        </div>
        <div class="tree-dir-body" id="${uid}">
          ${files.map(f => {
            const fname   = f.rel_path.replace(/\.pdf$/i, '').split('/').pop();
            const dt      = f.processed_at ? new Date(f.processed_at).toLocaleString() : '';
            const runBadge = f.run_id
              ? `<span class="badge badge-muted" title="Run ID: ${escHtml(f.run_id)}" style="font-size:0.6rem;cursor:default;font-family:monospace">${escHtml(f.run_id.slice(0,8))}&hellip;</span>`
              : '';
            const confBadge = f.confidence !== null && f.confidence !== undefined
              ? `<span class="badge ${f.confidence >= 0.85 ? 'badge-ok' : 'badge-warn'}" title="Confidence: ${(f.confidence * 100).toFixed(1)}%">${(f.confidence * 100).toFixed(1)}%</span>`
              : '';
            return `
              <div class="tree-file">
                <span class="tree-file-name" title="${escHtml(f.rel_path)}">${escHtml(fname)}</span>
                <span class="badge badge-${f.method || 'muted'}">${f.method || '?'}</span>
                ${confBadge}
                <span class="tree-file-chars">${(f.char_count || 0).toLocaleString()} ch</span>
                <span class="tree-file-date" title="Processed: ${dt}">${dt}</span>
                ${runBadge}
                <button class="btn btn-sm btn-ghost" onclick="viewResult(${f.id})">View</button>
                <a class="btn btn-sm btn-outline" href="${API}/results/${f.id}/download" download="${escHtml(fname)}.txt" title="Download extracted text">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="11" height="11"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                  .txt
                </a>
                <button class="icon-btn" title="Delete record" onclick="deleteResult(${f.id})">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/></svg>
                </button>
              </div>`;
          }).join('')}
        </div>
      </div>`;
  });
  wrap.innerHTML = html;
}

function toggleDir(uid) {
  const body   = $(uid);
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
  body.textContent    = 'Loading…';
  meta.innerHTML      = '';
  try {
    const res  = await fetch(`${API}/results/${id}`);
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
  await fetch(`${API}/results/${id}`, { method: 'DELETE' });
  toast('Record removed.', 'info');
  loadResults();
}

$('btn-close-viewer').addEventListener('click',   () => { $('text-viewer-panel').style.display = 'none'; });
$('btn-refresh-results').addEventListener('click', loadResults);

// ---------------------------------------------------------------------------
// Extracted Files view — GET /api/v1/files (server-side pagination)
// ---------------------------------------------------------------------------

async function loadExtractedFiles() {
  const tbody = $('extracted-tbody');
  tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted);padding:1.5rem">Loading…</td></tr>';
  const p = state.pages.files;
  try {
    const res  = await fetch(`${API}/files?page=${p.page}&size=${p.size}`);
    const data = await res.json();
    p.total = data.total;
    p.pages = data.pages;
    if (!data.items.length) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--text-muted);padding:1.5rem">No extracted files yet.</td></tr>';
    } else {
      tbody.innerHTML = data.items.map(f => {
        const confStr = f.confidence !== null && f.confidence !== undefined ? (f.confidence * 100).toFixed(1) + '%' : '—';
        const flagsStr = f.flags ? (f.flags.split(',').join(', ')) : '—';
        return `
        <tr>
          <td>${escHtml(f.name)}</td>
          <td style="color:var(--text-muted);font-size:0.8rem">${escHtml(f.rel_path)}</td>
          <td>${fmt.bytes(f.size_bytes)}</td>
          <td><span style="font-size:0.8rem;${f.confidence && f.confidence < 0.85 ? 'color:#ef4444' : ''}">${confStr}</span></td>
          <td style="color:var(--text-muted);font-size:0.85rem">${escHtml(flagsStr)}</td>
          <td>${new Date(f.modified * 1000).toLocaleString()}</td>
          <td>
            <button class="icon-btn" title="Delete" onclick="deleteExtracted('${escHtml(f.rel_path)}', this)">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/></svg>
            </button>
          </td>
        </tr>
      `}).join('');
    }
    updateFilesPagination();
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5" style="color:var(--error);padding:1rem">${e.message}</td></tr>`;
  }
}

async function deleteExtracted(relPath, btn) {
  if (!confirm(`Delete ${relPath}?`)) return;
  btn.disabled = true;
  try {
    await fetch(`${API}/files/${encodeURIComponent(relPath)}`, { method: 'DELETE' });
    await loadExtractedFiles();
    toast('File deleted.', 'success');
  } catch (e) {
    toast(e.message, 'error');
    btn.disabled = false;
  }
}

$('btn-refresh-files').addEventListener('click', loadExtractedFiles);

// ---------------------------------------------------------------------------
// Search view — GET /api/v1/search (FTS5, server-side pagination)
// ---------------------------------------------------------------------------

async function doSearch(q) {
  q = (q || '').trim();
  if (!q) return;
  state.pages.search.query = q;
  const p = state.pages.search;

  const resultsDiv = $('search-results');
  resultsDiv.innerHTML = '<div class="search-empty">Searching…</div>';

  try {
    const res  = await fetch(`${API}/search?q=${encodeURIComponent(q)}&page=${p.page}&size=${p.size}`);

    // 503 = FTS index empty
    if (res.status === 503) {
      resultsDiv.innerHTML = '';
      $('search-reindex-notice').style.display = 'flex';
      $('search-pagination').style.display = 'none';
      return;
    }
    $('search-reindex-notice').style.display = 'none';

    const data = await res.json();
    p.total = data.total;
    p.pages = data.pages;

    if (!data.results.length) {
      resultsDiv.innerHTML = `<div class="search-empty">No results for "<strong>${escHtml(q)}</strong>"</div>`;
      $('search-pagination').style.display = 'none';
      return;
    }

    resultsDiv.innerHTML = data.results.map(r => {
      // snippet already contains <mark> tags from SQLite FTS5 snippet()
      const safeSnippet = r.snippet
        .replace(/&(?!amp;|lt;|gt;|quot;|#)/g, '&amp;')
        .replace(/<(?!\/?(mark)\b)/gi, '&lt;');
      return `
        <div class="search-result-item">
          <div class="search-result-header">
            <span class="search-filename">${escHtml(r.file)}</span>
            <span class="search-page-badge">Page ${r.page_no}</span>
            <a href="#" class="btn btn-sm btn-outline" onclick="showResultDetail(${r.result_id});return false;">View →</a>
          </div>
          <div class="search-rel-path">${escHtml(r.rel_path)}</div>
          <div class="search-snippet">${safeSnippet}</div>
        </div>`;
    }).join('');

    updateSearchPagination();
  } catch (e) {
    resultsDiv.innerHTML = `<div class="search-empty" style="color:var(--error)">${e.message}</div>`;
  }
}

// highlightSnippet is kept for topbar quick-search fallback but FTS5
// snippet() already provides <mark> tags in the API response.
function highlightSnippet(text, q) {
  const escaped = escHtml(text);
  const re = new RegExp(`(${escHtml(q).replace(/[.*+?^${}()|[\]\\]/g,'\\$&')})`, 'gi');
  return escaped.replace(re, '<mark>$1</mark>');
}

$('btn-search').addEventListener('click', () => { state.pages.search.page = 1; doSearch($('search-main').value); });
$('search-main').addEventListener('keydown', e => { if (e.key === 'Enter') { state.pages.search.page = 1; doSearch(e.target.value); } });

// Build FTS index from existing extractions
$('btn-reindex').addEventListener('click', async () => {
  $('btn-reindex').textContent = 'Indexing…';
  $('btn-reindex').disabled = true;
  try {
    await fetch(`${API}/search/reindex`, { method: 'POST' });
    toast('Reindex started. Watch the activity log for progress.', 'info');
  } catch (e) {
    toast('Reindex request failed: ' + e.message, 'error');
  } finally {
    $('btn-reindex').textContent = 'Build Index';
    $('btn-reindex').disabled = false;
  }
});

// Topbar quick-search → search view
$('search-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    showView('search');
    $('search-main').value = e.target.value;
    state.pages.search.page = 1;
    doSearch(e.target.value);
  }
});

// ---------------------------------------------------------------------------
// Settings & Presets
// ---------------------------------------------------------------------------

function collectSettings() {
  return {
    removeHeader:   $('s-remove-header').checked,
    removeFooter:   $('s-remove-footer').checked,
    removePageNums: $('s-remove-page-numbers').checked,
    removeNumeric:  $('s-remove-numeric').checked,
    lemmatize:      $('s-lemmatize').checked,
    applyToAll:     $('s-apply-all').checked,
  };
}

function loadSettings() {
  const s = state.settings;
  if (!Object.keys(s).length) return;
  $('s-remove-header').checked       = s.removeHeader  ?? true;
  $('s-remove-footer').checked       = s.removeFooter  ?? true;
  $('s-remove-page-numbers').checked = s.removePageNums ?? false;
  $('s-remove-numeric').checked      = s.removeNumeric  ?? false;
  $('s-lemmatize').checked           = s.lemmatize      ?? false;
  $('s-apply-all').checked           = s.applyToAll     ?? true;
}

['s-remove-header','s-remove-footer','s-remove-page-numbers','s-remove-numeric','s-lemmatize','s-apply-all']
  .forEach(id => {
    $(id).addEventListener('change', () => {
      state.settings = collectSettings();
      localStorage.setItem('settings', JSON.stringify(state.settings));
    });
  });

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

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

loadSettings();
renderPresets();
connectSSE();

// Sync state immediately on load (handles page refresh mid-job)
fetch(`${API}/job/status`)
  .then(r => r.json())
  .then(applyServerState)
  .catch(() => {});

// ---------------------------------------------------------------------------
// Runs view — pipeline run log
// ---------------------------------------------------------------------------

const updateRunsPagination = setupPagination(
  'runs', 'runs-prev', 'runs-next',
  'runs-page-info', 'runs-pagination',
  loadRuns,
);

const updateRunFilesPagination = setupPagination(
  'runFiles', 'run-files-prev', 'run-files-next',
  'run-files-page-info', 'run-files-pagination',
  () => loadRunFiles(state.pages.runFiles.runId),
);

async function loadRuns() {
  const p = state.pages.runs;
  const list = $('runs-list');
  list.innerHTML = '<div class="search-empty">Loading…</div>';
  try {
    const res  = await fetch(`${API}/runs?page=${p.page}&size=${p.size}`);
    const data = await res.json();
    p.total = data.total;
    p.pages = Math.ceil(data.total / p.size) || 1;
    if (!data.items.length) {
      list.innerHTML = '<div class="search-empty">No runs yet. Start an extraction to see run history.</div>';
      $('runs-pagination').style.display = 'none';
      return;
    }
    list.innerHTML = data.items.map(run => renderRunCard(run)).join('');
    updateRunsPagination();
  } catch (e) {
    list.innerHTML = `<div class="search-empty" style="color:var(--error)">${e.message}</div>`;
  }
}

function fmtDuration(secs) {
  if (secs == null) return '—';
  const s = Math.round(secs);
  if (s < 60)  return `${s}s`;
  if (s < 3600) return `${Math.floor(s/60)}m ${s%60}s`;
  return `${Math.floor(s/3600)}h ${Math.floor((s%3600)/60)}m`;
}

function fmtDatetime(iso) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString(undefined, { dateStyle:'medium', timeStyle:'short' }); }
  catch { return iso; }
}

function renderRunCard(run) {
  const done    = run.done_files   || 0;
  const total   = run.total_files  || 0;
  const direct  = run.direct_files || 0;
  const ocr     = run.ocr_files    || 0;
  const failed  = run.failed_files || 0;
  const pct     = total > 0 ? Math.round((direct / (done || 1)) * 100) : 0;
  const status  = (run.status || 'running').toLowerCase();
  const shortId = (run.run_id || '').slice(0, 8);

  return `
  <div class="run-card">
    <div class="run-card-header">
      <span class="run-id-label" title="${escHtml(run.run_id)}">Run&nbsp;${shortId}…</span>
      <span class="run-status-badge ${status}">${status}</span>
      <span class="run-card-meta">${fmtDatetime(run.started_at)}</span>
    </div>
    <div class="run-card-stats">
      <div class="run-stat">
        <div class="run-stat-label">Duration</div>
        <div class="run-stat-value">${fmtDuration(run.elapsed_seconds)}</div>
      </div>
      <div class="run-stat">
        <div class="run-stat-label">Files</div>
        <div class="run-stat-value">${done} / ${total}</div>
        ${failed ? `<div class="run-stat-sub" style="color:#ef4444">${failed} failed</div>` : ''}
      </div>
      <div class="run-stat">
        <div class="run-stat-label">Direct</div>
        <div class="run-stat-value">${direct}</div>
      </div>
      <div class="run-stat">
        <div class="run-stat-label">OCR</div>
        <div class="run-stat-value">${ocr}</div>
      </div>
    </div>
    <div class="run-method-bar" title="${pct}% direct text">
      <div class="run-method-bar-fill" style="width:${pct}%"></div>
    </div>
    <button class="btn btn-sm btn-outline" onclick="loadRunFiles('${escHtml(run.run_id)}')">View files in this run ↗</button>
  </div>`;
}

async function loadRunFiles(runId) {
  state.pages.runFiles.runId = runId;
  const p = state.pages.runFiles;
  const card  = $('run-files-card');
  const tbody = $('run-files-tbody');
  $('run-files-title').textContent = `Files in run ${runId.slice(0,8)}…`;
  card.style.display = '';
  tbody.innerHTML = '<tr><td colspan="5" style="color:var(--text-muted)">Loading…</td></tr>';
  card.scrollIntoView({ behavior: 'smooth', block: 'start' });
  try {
    const res  = await fetch(`${API}/runs/${encodeURIComponent(runId)}/files?page=${p.page}&size=${p.size}`);
    const data = await res.json();
    p.total = data.total;
    p.pages = Math.ceil(data.total / p.size) || 1;
    if (!data.items.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="color:var(--text-muted)">No files.</td></tr>';
      return;
    }
    tbody.innerHTML = data.items.map(f => {
      const confStr = f.confidence !== null && f.confidence !== undefined ? (f.confidence * 100).toFixed(1) + '%' : '—';
      const flagsStr = f.flags ? (f.flags.split(',').join(', ')) : '—';
      return `
      <tr>
        <td class="mono" title="${escHtml(f.filename)}">${escHtml(f.filename)}</td>
        <td><span class="badge badge-${f.method}">${escHtml(f.method || '—')}</span></td>
        <td>${f.page_count ?? '—'}</td>
        <td>${f.char_count?.toLocaleString() ?? '—'}</td>
        <td><span style="font-size:0.8rem;${f.confidence && f.confidence < 0.85 ? 'color:#ef4444' : ''}">${confStr}</span></td>
        <td style="color:var(--text-muted);font-size:0.85rem">${escHtml(flagsStr)}</td>
        <td class="date-cell" title="${escHtml(f.processed_at || '')}">${fmtDatetime(f.processed_at)}</td>
      </tr>
    `}).join('');
    updateRunFilesPagination();
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5" style="color:var(--error)">${e.message}</td></tr>`;
  }
}

$('btn-refresh-runs').addEventListener('click', () => { state.pages.runs.page = 1; loadRuns(); });
$('btn-close-run-files').addEventListener('click', () => { $('run-files-card').style.display = 'none'; });
