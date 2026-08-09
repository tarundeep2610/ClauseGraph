// ── Auth helper ────────────────────────────────────────────────────────────
// Credentials are cached by the browser after the initial HTTP Basic challenge
// on page load — do NOT hardcode them here.
function apiFetch(url, opts = {}) {
  opts.credentials = opts.credentials || 'same-origin';
  const abs = url.startsWith('http') ? url : (window.location.origin + url);
  return fetch(abs, opts);
}

// ── Security helpers ───────────────────────────────────────────────────────
function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function sanitizeHtml(html) {
  const tmp = document.createElement('div');
  tmp.innerHTML = html;
  tmp.querySelectorAll('script,iframe,object,embed,form,base').forEach(el => el.remove());
  tmp.querySelectorAll('*').forEach(el => {
    [...el.attributes].forEach(attr => {
      if (attr.name.startsWith('on') ||
          attr.value.trim().toLowerCase().startsWith('javascript:') ||
          attr.value.trim().toLowerCase().startsWith('data:text/html')) {
        el.removeAttribute(attr.name);
      }
    });
  });
  return tmp.innerHTML;
}

// ── State ──────────────────────────────────────────────────────────────────
let activeJobId = null;
let currentViewMode = 'split';
let pollInterval = null;
let _settingsOpen = true;
let _prompts = {};
let _defaults = {};
let _hasActiveJobs = false;
let _localPreviewUrl = null;   // blob URL for local file preview
const _jobMdCache = {};        // jobId -> last rendered markdown string

// ── Init ───────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  setupDragDrop();
  document.getElementById('fileInput').addEventListener('change', onFileInputChange);

  // Static button wiring
  document.getElementById('exitBtn').addEventListener('click', exitServer);
  document.getElementById('resetIdleBtn').addEventListener('click', resetIdleTimer);
  document.getElementById('pauseControlBtn').addEventListener('click', pauseActiveJob);
  document.getElementById('stopAllBtn').addEventListener('click', stopAllJobs);
  document.getElementById('changeFileBtn').addEventListener('click', openFileDialog);
  document.getElementById('clearFileBtn').addEventListener('click', clearFileSelection);
  document.getElementById('submitBtn').addEventListener('click', submitOCRJob);
  document.getElementById('settingsToggle').addEventListener('click', toggleSettings);
  document.getElementById('engineSelect').addEventListener('change', onEngineChange);
  document.getElementById('promptMode').addEventListener('change', onPromptModeChange);
  document.getElementById('savePromptBtn').addEventListener('click', savePrompt);
  document.getElementById('resetPromptBtn').addEventListener('click', resetPrompt);
  document.getElementById('saveOutputBtn').addEventListener('click', saveOutputPath);
  document.getElementById('saveIdleBtn').addEventListener('click', saveIdleTimeout);
  document.getElementById('refreshHistoryBtn').addEventListener('click', () => loadJobsList());
  document.getElementById('toggleSplitBtn').addEventListener('click', () => setViewMode('split'));
  document.getElementById('togglePdfBtn').addEventListener('click', () => setViewMode('pdf'));
  document.getElementById('toggleResultBtn').addEventListener('click', () => setViewMode('result'));
  document.getElementById('pauseActiveBtn').addEventListener('click', pauseActiveJob);
  document.getElementById('resumeActiveBtn').addEventListener('click', resumeActiveJob);
  document.getElementById('downloadPdfBtn').addEventListener('click', downloadPdfFile);
  document.getElementById('downloadResultBtn').addEventListener('click', downloadOcrResult);

  // Slider display updates
  document.getElementById('glmTemp').addEventListener('input', () => {
    document.getElementById('glmTempVal').textContent = parseFloat(document.getElementById('glmTemp').value).toFixed(1);
  });
  document.getElementById('glmRepeat').addEventListener('input', () => {
    document.getElementById('glmRepeatVal').textContent = parseFloat(document.getElementById('glmRepeat').value).toFixed(1);
  });

  // Event delegation for dynamically-rendered history cards
  document.getElementById('historyList').addEventListener('click', e => {
    const actionsDiv = e.target.closest('.history-actions');
    const card = e.target.closest('.history-card');
    if (!card) return;
    const jobId = card.dataset.jobId;
    if (!jobId) return;
    if (actionsDiv) e.stopPropagation();
    const btn = e.target.closest('button[data-action]');
    if (btn) {
      const action = btn.dataset.action;
      if (action === 'select') selectJob(jobId);
      else if (action === 'pause') pauseJob(jobId);
      else if (action === 'retry') retryJob(jobId);
      else if (action === 'download-file') triggerDownloadFile(jobId);
      else if (action === 'download-result') triggerDownloadResult(jobId);
      else if (action === 'delete') deleteJob(jobId);
    } else if (!actionsDiv) {
      selectJob(jobId);
    }
  });

  await loadPromptsFromServer();
  await loadSettingsFromServer();
  await loadJobsList();
  startIdleTimer();
  trackUserActivity();
});

window.addEventListener('beforeunload', () => {
  if (_localPreviewUrl) { URL.revokeObjectURL(_localPreviewUrl); _localPreviewUrl = null; }
});

// ── Settings panel toggle ──────────────────────────────────────────────────
function toggleSettings() {
  _settingsOpen = !_settingsOpen;
  document.getElementById('settingsBody').style.display = _settingsOpen ? 'block' : 'none';
  document.getElementById('settingsChevron').style.transform = _settingsOpen ? 'rotate(180deg)' : '';
}

// ── Engine change ──────────────────────────────────────────────────────────
function onEngineChange() {
  const engine = document.getElementById('engineSelect').value;
  document.getElementById('glmParams').style.display = engine === 'glm' ? 'block' : 'none';
  document.getElementById('unlimitedParams').style.display = engine === 'unlimited' ? 'block' : 'none';
}

// ── Prompts ────────────────────────────────────────────────────────────────
async function loadPromptsFromServer() {
  try {
    const resp = await apiFetch('/api/prompts');
    if (!resp.ok) return;
    const data = await resp.json();
    _prompts = data.prompts || {};
    _defaults = data.defaults || {};
    onPromptModeChange();
  } catch(e) {}
}

function onPromptModeChange() {
  const mode = document.getElementById('promptMode').value;
  document.getElementById('promptTextarea').value = _prompts[mode] || '';
  document.getElementById('promptSavedMsg').style.display = 'none';
}

async function savePrompt() {
  const mode = document.getElementById('promptMode').value;
  const text = document.getElementById('promptTextarea').value.trim();
  if (!text) return;
  try {
    const resp = await apiFetch(`/api/prompts/${mode}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text})
    });
    if (resp.ok) {
      _prompts[mode] = text;
      const msg = document.getElementById('promptSavedMsg');
      msg.innerHTML = '<i class="fa-solid fa-check"></i> Saved';
      msg.style.display = 'inline-flex';
      msg.style.gap = '5px';
      msg.style.color = 'var(--success)';
      msg.style.fontSize = '0.75rem';
      msg.style.fontWeight = '600';
      setTimeout(() => { msg.style.display = 'none'; }, 3000);
    }
  } catch(e) {}
}

async function resetPrompt() {
  const mode = document.getElementById('promptMode').value;
  try {
    const resp = await apiFetch(`/api/prompts/${mode}`, { method: 'DELETE' });
    if (resp.ok) {
      const data = await resp.json();
      _prompts[mode] = data.text;
      document.getElementById('promptTextarea').value = data.text;
      const msg = document.getElementById('promptSavedMsg');
      msg.innerHTML = '<i class="fa-solid fa-rotate-left"></i> Reset to default';
      msg.style.display = 'inline-flex';
      msg.style.gap = '5px';
      msg.style.color = 'var(--muted)';
      msg.style.fontSize = '0.75rem';
      msg.style.fontWeight = '600';
      setTimeout(() => { msg.style.display = 'none'; }, 3000);
    }
  } catch(e) {}
}

// ── Settings ───────────────────────────────────────────────────────────────
async function loadSettingsFromServer() {
  try {
    const resp = await apiFetch('/api/settings');
    if (!resp.ok) return;
    const s = await resp.json();
    if (s.output_path) document.getElementById('outputPath').value = s.output_path;
    if (s.idle_timeout_minutes !== undefined) document.getElementById('idleTimeoutInput').value = s.idle_timeout_minutes;
  } catch(e) {}
}

async function saveOutputPath() {
  const path = document.getElementById('outputPath').value.trim();
  try {
    await apiFetch('/api/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({output_path: path})
    });
    flashSave('outputSavedMsg');
  } catch(e) {}
}

async function saveIdleTimeout() {
  const mins = parseInt(document.getElementById('idleTimeoutInput').value) || 0;
  try {
    await apiFetch('/api/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({idle_timeout_minutes: mins})
    });
    flashSave('idleTimeoutSavedMsg');
  } catch(e) {}
}

function flashSave(id) {
  const el = document.getElementById(id);
  el.style.display = 'inline-flex';
  el.style.gap = '5px';
  setTimeout(() => { el.style.display = 'none'; }, 3000);
}

// ── Idle timer ─────────────────────────────────────────────────────────────
let _lastUserAction = Date.now();
let _sessionPaused = false;

function trackUserActivity() {
  ['mousedown','keydown','touchstart','scroll','click'].forEach(evt => {
    document.addEventListener(evt, () => { _lastUserAction = Date.now(); }, {passive: true});
  });
}

function resetIdleTimer() {
  _lastUserAction = Date.now();
  _sessionPaused = false;
  document.getElementById('idleBanner').className = '';
  document.getElementById('idleBanner').style.display = 'none';
  document.getElementById('idlePill').className = 'idle';
  if (!pollInterval) startAutoPolling();
  // Ping backend to reset its idle clock
  apiFetch('/api/idle-status').catch(()=>{});
}

function startIdleTimer() {
  setInterval(async () => {
    let timeoutMins = parseInt(document.getElementById('idleTimeoutInput').value) || 15;
    if (timeoutMins <= 0) {
      document.getElementById('idlePill').innerHTML =
        '<i class="fa-solid fa-circle" style="font-size:0.5rem;"></i> Idle timeout disabled';
      return;
    }
    // Don't count down while OCR is actively running
    if (_hasActiveJobs) {
      _lastUserAction = Date.now();
      document.getElementById('idlePill').innerHTML =
        '<i class="fa-solid fa-circle-notch fa-spin" style="font-size:0.5rem;"></i> OCR running — idle timer paused';
      document.getElementById('idleBanner').className = '';
      document.getElementById('idleBanner').style.display = 'none';
      return;
    }
    const idleMs = Date.now() - _lastUserAction;
    const totalMs = timeoutMins * 60 * 1000;
    const remainMs = Math.max(0, totalMs - idleMs);
    const remainSecs = Math.floor(remainMs / 1000);
    const m = Math.floor(remainSecs / 60);
    const s = remainSecs % 60;
    const label = `${m}:${String(s).padStart(2,'0')}`;

    const pill = document.getElementById('idlePill');
    const banner = document.getElementById('idleBanner');

    if (remainMs <= 0) {
      // Session expired
      if (!_sessionPaused) {
        _sessionPaused = true;
        if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
      }
      pill.className = 'expired';
      pill.innerHTML = '<i class="fa-solid fa-power-off" style="font-size:0.5rem;"></i> Session paused — server shutting down';
      banner.className = 'expired';
      banner.style.display = 'flex';
      document.getElementById('idleBannerMsg').textContent =
        'No activity for ' + timeoutMins + ' minutes. Polling stopped. The server will shut down shortly.';
    } else if (remainMs <= 2 * 60 * 1000) {
      // Warning zone (last 2 min)
      pill.className = 'warn';
      pill.innerHTML = `<i class="fa-solid fa-triangle-exclamation" style="font-size:0.5rem;"></i> Idle shutdown in ${label}`;
      banner.className = 'warn';
      banner.style.display = 'flex';
      document.getElementById('idleBannerMsg').textContent =
        `Idle for ${timeoutMins - m - 1}m ${60 - s}s — server will shut down in ${label}`;
    } else {
      pill.className = '';
      pill.innerHTML = `<i class="fa-solid fa-circle" style="font-size:0.5rem;"></i> Session active — idle shutdown in ${label}`;
      banner.className = '';
      banner.style.display = 'none';
    }
  }, 5000);
}

// ── Job submission ─────────────────────────────────────────────────────────
async function submitOCRJob() {
  const fileInput = document.getElementById('fileInput');
  if (!fileInput.files || !fileInput.files.length) {
    alert('Please select a file first.');
    return;
  }
  _lastUserAction = Date.now();

  const engine       = document.getElementById('engineSelect').value;
  const scale        = document.getElementById('scaleSelect').value;
  const promptMode   = document.getElementById('promptMode').value;
  const customPrompt = document.getElementById('promptTextarea').value.trim();

  const pageRange = (document.getElementById('pageRangeInput').value || '').trim();

  // Build query string
  const params = new URLSearchParams({ engine, scale, prompt_mode: promptMode });
  if (pageRange) params.set('page_range', pageRange);

  if (customPrompt && customPrompt !== (_defaults[promptMode] || '')) {
    params.set('custom_prompt', customPrompt);
  }

  if (engine === 'glm') {
    params.set('tiling',            document.getElementById('glmTiling').checked ? 'true' : 'false');
    params.set('glm_temperature',   document.getElementById('glmTemp').value);
    params.set('glm_num_ctx',       document.getElementById('glmNumCtx').value);
    params.set('glm_num_predict',   document.getElementById('glmNumPredict').value);
    params.set('glm_repeat_penalty',document.getElementById('glmRepeat').value);
  } else {
    params.set('tiling', 'false');
    params.set('unlimited_base_size', document.getElementById('unlimitedBaseSize').value);
  }

  const submitBtn = document.getElementById('submitBtn');
  submitBtn.disabled = true;
  submitBtn.innerHTML = `<div class="spinner"></div> Submitting...`;

  const formData = new FormData();
  formData.append('file', fileInput.files[0]);

  try {
    const resp = await apiFetch(`/api/jobs?${params.toString()}`, { method: 'POST', body: formData });
    if (!resp.ok) {
      const err = await resp.json();
      alert(`Failed: ${err.detail || resp.statusText}`);
      return;
    }
    const res = await resp.json();
    activeJobId = res.job_id;
    fileInput.value = '';
    // Revoke local preview blob — server now serves the file
    if (_localPreviewUrl) { URL.revokeObjectURL(_localPreviewUrl); _localPreviewUrl = null; }
    showFileEmpty();
    await loadJobsList();
    selectJob(activeJobId);
  } catch(e) {
    alert(`Network error: ${e.message}`);
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = `<i class="fa-solid fa-play"></i> Start Fail-Safe OCR`;
  }
}

// ── Job list ───────────────────────────────────────────────────────────────
function startAutoPolling() {
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = setInterval(() => {
    loadJobsList(true);
    if (activeJobId) fetchJobDetails(activeJobId, true);
  }, 2500);
}

async function loadJobsList(silent = false) {
  try {
    const resp = await apiFetch('/api/jobs');
    if (!resp.ok) return;
    const jobs = await resp.json();
    const listEl = document.getElementById('historyList');

    if (!jobs.length) {
      listEl.innerHTML = `<div style="text-align:center;color:var(--muted);padding:18px;font-size:0.83rem;">No documents yet.</div>`;
      return;
    }

    let html = '';
    let anyActive = false;
    jobs.forEach(j => {
      const isActive = j.job_id === activeJobId ? 'active' : '';
      let badgeClass = 'pending', badgeText = j.status;
      if (j.status === 'completed') badgeClass = 'completed';
      else if (j.status === 'processing') { badgeClass = 'processing'; badgeText = `${j.processed_pages}/${j.total_pages}`; anyActive = true; }
      else if (j.status === 'pausing') { badgeClass = 'processing'; badgeText = 'pausing...'; anyActive = true; }
      else if (j.status === 'paused') badgeClass = 'paused';
      else if (j.status === 'failed') badgeClass = 'failed';

      const canPause = j.status === 'processing';
      const canResume = j.status === 'paused' || j.status === 'failed';

      html += `
        <div class="history-card ${isActive}" data-job-id="${escHtml(j.job_id)}">
          <div class="history-header">
            <span class="history-filename" title="${escHtml(j.filename)}">${escHtml(j.filename)}</span>
            <span class="status-badge ${badgeClass}">${escHtml(badgeText)}</span>
          </div>
          <div class="history-meta">
            <span><i class="fa-regular fa-file"></i> ${j.total_pages}p · ${escHtml(j.engine.toUpperCase())}</span>
            <span>${new Date(j.created_at).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}</span>
          </div>
          <div class="history-actions">
            <button class="btn btn-sm btn-secondary" data-action="select"><i class="fa-solid fa-eye"></i></button>
            ${canPause ? `<button class="btn btn-sm btn-warn" data-action="pause"><i class="fa-solid fa-pause"></i></button>` : ''}
            ${canResume ? `<button class="btn btn-sm btn-secondary" data-action="retry"><i class="fa-solid fa-rotate-right"></i></button>` : ''}
            <button class="btn btn-sm btn-outline" data-action="download-file"><i class="fa-solid fa-file-pdf"></i></button>
            <button class="btn btn-sm btn-outline" data-action="download-result"><i class="fa-solid fa-file-lines"></i></button>
            <button class="btn btn-sm btn-danger" data-action="delete"><i class="fa-solid fa-trash-can"></i></button>
          </div>
        </div>`;
    });
    _hasActiveJobs = anyActive;
    document.getElementById('controlBar').className = anyActive ? 'visible' : '';
    if (anyActive && !pollInterval) startAutoPolling();
    else if (!anyActive && pollInterval) { clearInterval(pollInterval); pollInterval = null; }
    listEl.innerHTML = html;
    if (!activeJobId && jobs.length) selectJob(jobs[0].job_id);
  } catch(e) { if (!silent) console.error(e); }
}

async function selectJob(jobId) {
  activeJobId = jobId;
  document.getElementById('downloadGroup').style.display = 'flex';
  const pdfFrame = document.getElementById('pdfFrame');
  const targetUrl = window.location.origin + `/api/jobs/${jobId}/file`;
  if (pdfFrame.src !== targetUrl) pdfFrame.src = targetUrl;
  await fetchJobDetails(jobId);
  loadJobsList(true);
}

async function fetchJobDetails(jobId, silent = false) {
  try {
    const resp = await apiFetch(`/api/jobs/${jobId}`);
    if (!resp.ok) return;
    const job = await resp.json();
    document.getElementById('pdfStatusLabel').innerText = `${job.filename} (${job.total_pages}p)`;
    const pl = document.getElementById('jobProgressLabel');

    if (job.status === 'processing')
      pl.innerHTML = `<div class="spinner"></div> Processing (${job.processed_pages}/${job.total_pages} pages)...`;
    else if (job.status === 'completed')
      pl.innerHTML = `<span style="color:var(--success)"><i class="fa-solid fa-check"></i> Done (${job.total_pages} pages)</span>`;
    else if (job.status === 'paused')
      pl.innerHTML = `<span style="color:#67e8f9"><i class="fa-solid fa-pause"></i> Paused (${job.processed_pages}/${job.total_pages} pages extracted)</span>`;
    else if (job.status === 'failed')
      pl.innerHTML = `<span style="color:var(--error)"><i class="fa-solid fa-triangle-exclamation"></i> Failed</span>`;
    else
      pl.innerText = 'Queued...';

    // Show/hide Pause and Resume buttons in toolbar
    const pauseBtn = document.getElementById('pauseActiveBtn');
    const resumeBtn = document.getElementById('resumeActiveBtn');
    if (pauseBtn && resumeBtn) {
      pauseBtn.style.display = job.status === 'processing' ? 'inline-flex' : 'none';
      resumeBtn.style.display = (job.status === 'paused' || job.status === 'failed') ? 'inline-flex' : 'none';
    }

    const mdEl = document.getElementById('resultMarkdown');
    const pages = job.pages_data || [];
    if (!pages.length) {
      if (job.status === 'processing')
        mdEl.innerHTML = `<div style="text-align:center;padding-top:60px;color:var(--accent)"><div class="spinner" style="width:22px;height:22px;margin-bottom:10px;"></div><br>Scanning...</div>`;
      else if (job.status === 'failed')
        mdEl.innerHTML = `<div style="color:var(--error);padding:18px;">Extraction failed: ${escHtml(job.error_message)}</div>`;
      else
        mdEl.innerHTML = `<div style="text-align:center;padding-top:60px;color:var(--muted)">Waiting for first page...</div>`;
      return;
    }
    let md = '';
    pages.forEach(p => { md += `### Page ${p.page}\n\n${p.text||''}\n\n---\n\n`; });
    if (_jobMdCache[jobId] !== md) {
      _jobMdCache[jobId] = md;
      mdEl.innerHTML = sanitizeHtml(marked.parse(md));
    }
  } catch(e) { if (!silent) console.error(e); }
}

// ── View mode ──────────────────────────────────────────────────────────────
function setViewMode(mode) {
  currentViewMode = mode;
  ['Split','Pdf','Result'].forEach(m =>
    document.getElementById(`toggle${m}Btn`).classList.remove('active'));
  document.getElementById('pdfBox').style.display = 'flex';
  document.getElementById('resultBox').style.display = 'flex';
  if (mode === 'split') { document.getElementById('toggleSplitBtn').classList.add('active'); }
  else if (mode === 'pdf') { document.getElementById('togglePdfBtn').classList.add('active'); document.getElementById('resultBox').style.display = 'none'; }
  else { document.getElementById('toggleResultBtn').classList.add('active'); document.getElementById('pdfBox').style.display = 'none'; }
}

// ── Downloads ──────────────────────────────────────────────────────────────
function downloadPdfFile()    { if (activeJobId) triggerDownloadFile(activeJobId); }
function downloadOcrResult()  { if (activeJobId) triggerDownloadResult(activeJobId); }
function triggerDownloadFile(jobId)   { window.open(window.location.origin + `/api/jobs/${jobId}/file?download=true`, '_blank'); }
function triggerDownloadResult(jobId) { window.open(window.location.origin + `/api/jobs/${jobId}/download-result`, '_blank'); }

// ── Job actions ────────────────────────────────────────────────────────────
async function retryJob(jobId) {
  await apiFetch(`/api/jobs/${jobId}/retry`, {method:'POST'});
  loadJobsList();
}
async function deleteJob(jobId) {
  if (!confirm('Delete this document and its results?')) return;
  await apiFetch(`/api/jobs/${jobId}`, {method:'DELETE'});
  if (activeJobId === jobId) activeJobId = null;
  loadJobsList();
}

// ── Upload zone UI ─────────────────────────────────────────────────────────
function onFileInputChange() {
  const fileInput = document.getElementById('fileInput');
  if (fileInput.files && fileInput.files.length > 0) {
    showFileSelected(fileInput.files[0]);
  } else {
    showFileEmpty();
  }
}

function showFileSelected(file) {
  const ext = file.name.split('.').pop().toUpperCase();
  const size = file.size < 1024 * 1024
    ? `${(file.size / 1024).toFixed(0)} KB`
    : `${(file.size / 1024 / 1024).toFixed(1)} MB`;

  document.getElementById('dropzoneFilename').textContent = file.name;
  document.getElementById('dropzoneFileMeta').textContent = `${ext} · ${size}`;
  document.getElementById('dropzoneEmpty').style.display = 'none';
  document.getElementById('dropzoneSelected').style.display = 'block';

  // Show page range input for PDFs
  const isPdf = file.name.toLowerCase().endsWith('.pdf');
  document.getElementById('pageRangeGroup').style.display = isPdf ? 'block' : 'none';
  document.getElementById('pageRangeInput').value = '';
  if (isPdf) {
    document.getElementById('pageCountHint').textContent = 'PDF selected — enter a page range or leave blank to extract all pages';
  }

  // Show local preview in viewer immediately
  if (_localPreviewUrl) { URL.revokeObjectURL(_localPreviewUrl); _localPreviewUrl = null; }
  _localPreviewUrl = URL.createObjectURL(file);
  const pdfFrame = document.getElementById('pdfFrame');
  pdfFrame.src = _localPreviewUrl;
  document.getElementById('pdfStatusLabel').textContent = `${file.name} (local preview)`;
  document.getElementById('downloadGroup').style.display = 'flex';
  document.getElementById('jobProgressLabel').textContent = 'Not submitted yet';
  document.getElementById('pauseActiveBtn').style.display = 'none';
  document.getElementById('resumeActiveBtn').style.display = 'none';
}

function showFileEmpty() {
  document.getElementById('dropzoneEmpty').style.display = 'block';
  document.getElementById('dropzoneSelected').style.display = 'none';
  document.getElementById('pageRangeGroup').style.display = 'none';
  if (_localPreviewUrl) {
    URL.revokeObjectURL(_localPreviewUrl);
    _localPreviewUrl = null;
    document.getElementById('pdfFrame').src = 'about:blank';
    document.getElementById('pdfStatusLabel').textContent = 'No document selected';
    document.getElementById('downloadGroup').style.display = 'none';
  }
}

function openFileDialog() {
  document.getElementById('fileInput').click();
}

function clearFileSelection() {
  document.getElementById('fileInput').value = '';
  showFileEmpty();
}

// ── Drag & drop ────────────────────────────────────────────────────────────
function setupDragDrop() {
  ['dropzoneEmpty', 'dropzoneSelected'].forEach(id => {
    const el = document.getElementById(id);
    el.addEventListener('dragover', e => { e.preventDefault(); el.classList.add('dragging'); });
    el.addEventListener('dragleave', () => el.classList.remove('dragging'));
    el.addEventListener('drop', e => {
      e.preventDefault();
      el.classList.remove('dragging');
      const files = e.dataTransfer.files;
      if (files.length) {
        const dt = new DataTransfer();
        dt.items.add(files[0]);
        document.getElementById('fileInput').files = dt.files;
        onFileInputChange();
      }
    });
  });
}

// ── Pause / Stop / Exit ────────────────────────────────────────────────────
async function pauseJob(jobId) {
  const pauseBtn = document.getElementById('pauseActiveBtn');
  if (pauseBtn) { pauseBtn.disabled = true; pauseBtn.innerHTML = '<div class="spinner"></div> Pausing…'; }
  try {
    await apiFetch(`/api/jobs/${jobId}/pause`, { method: 'POST' });
  } catch(e) {}
  if (pauseBtn) { pauseBtn.disabled = false; pauseBtn.innerHTML = '<i class="fa-solid fa-pause"></i> Pause'; }
  loadJobsList();
}

async function pauseActiveJob() {
  if (activeJobId) await pauseJob(activeJobId);
}

async function resumeActiveJob() {
  if (activeJobId) await retryJob(activeJobId);
}

async function stopAllJobs() {
  if (!confirm('Stop all running OCR extractions? Pages extracted so far are preserved.')) return;
  await apiFetch('/api/jobs/stop-all', { method: 'POST' });
  setTimeout(loadJobsList, 600);
}

async function exitServer() {
  if (!confirm('Shut down the OCR server?\n\nThis will:\n• Stop all running OCR tasks\n• Free all memory\n• Close this tab')) return;
  try { await apiFetch('/api/shutdown', { method: 'POST' }); } catch(e) {}
  if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
  // Replace page content with shutdown notice, then try to close
  document.body.innerHTML = `
    <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;
                height:100vh;background:#09090b;color:#f4f4f5;gap:16px;font-family:sans-serif;text-align:center;padding:20px;">
      <div style="font-size:3rem;">&#x2B1B;</div>
      <h2 style="font-size:1.4rem;font-weight:700;">Server shut down</h2>
      <p style="color:#71717a;">All OCR processes stopped. Memory freed.</p>
      <p style="color:#52525b;font-size:0.85rem;">You can close this tab.</p>
    </div>`;
  setTimeout(() => window.close(), 800);
}
