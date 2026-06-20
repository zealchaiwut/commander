// Add Project modal — single shared implementation for the home and project
// pages. Both pages embed the matching #apm-modal markup and include this file;
// there is no longer a per-page copy to keep in sync. Two tabs: "Create new
// project" (streams init_project.py) and "Add existing repo" (POST /api/projects,
// or a nested clone via init_project when "Nested layout" is checked).

let _apmTab = 'init';

// Alias retained for the "+ add project" tiles / keydown handlers.
function openAddProjectFlow() { openAddProjectModal(); }

function openAddProjectModal() {
  document.getElementById('apm-repo').value             = '';
  document.getElementById('apm-icon').value             = '';
  document.getElementById('apm-color').value            = '';
  document.getElementById('apm-name').value             = '';
  document.getElementById('apm-projects-dir').value     = '~/dev';
  document.getElementById('apm-nested').checked         = false;
  document.getElementById('apm-skip-uat').checked       = false;
  document.getElementById('apm-add-nested').checked     = false;
  document.getElementById('apm-add-skip-uat').checked   = false;
  document.getElementById('apm-add-projects-dir').value = '~/dev';
  document.getElementById('apm-add-dir-field').classList.add('hidden');
  _apmClearError('apm-repo');
  _apmClearError('apm-name');
  _apmResetLog();
  document.getElementById('apm-backdrop').classList.remove('hidden');
  document.getElementById('apm-modal').classList.remove('hidden');
  apmSwitchTab(_apmTab);
}

function closeAddProjectModal() {
  document.getElementById('apm-backdrop').classList.add('hidden');
  document.getElementById('apm-modal').classList.add('hidden');
}

function apmSwitchTab(tab) {
  _apmTab = tab;
  const init = tab === 'init';
  document.getElementById('apm-init-form').classList.toggle('hidden', !init);
  document.getElementById('apm-add-form').classList.toggle('hidden', init);
  document.getElementById('apm-tab-init').classList.toggle('active', init);
  document.getElementById('apm-tab-add').classList.toggle('active', !init);
  _apmUpdateSubmitLabel();
  document.getElementById(init ? 'apm-name' : 'apm-repo').focus();
}

// One footer button: its label tracks the active tab + nested-clone choice.
function _apmUpdateSubmitLabel() {
  const btn = document.getElementById('apm-submit');
  if (!btn) return;
  if (_apmTab === 'init') { btn.textContent = 'Create'; return; }
  btn.textContent = document.getElementById('apm-add-nested').checked ? 'Clone & add' : 'Add Project';
}

// The single footer button dispatches to the active tab's handler.
function apmSubmit() {
  const ev = { preventDefault() {} };
  if (_apmTab === 'init') apmSubmitInit(ev); else apmSubmitAdd(ev);
}

// Reveal the projects-dir field + relabel when nested-clone is toggled.
function apmAddNestedToggle() {
  const on = document.getElementById('apm-add-nested').checked;
  document.getElementById('apm-add-dir-field').classList.toggle('hidden', !on);
  _apmUpdateSubmitLabel();
}

function _apmErrId(inputId) { return inputId === 'apm-name' ? 'apm-init-error' : 'apm-repo-error'; }

function _apmClearError(inputId) {
  const err = document.getElementById(_apmErrId(inputId));
  const inp = document.getElementById(inputId);
  if (err) { err.textContent = ''; err.classList.add('hidden'); }
  if (inp) inp.classList.remove('error');
}

function _apmShowError(inputId, msg) {
  const err = document.getElementById(_apmErrId(inputId));
  const inp = document.getElementById(inputId);
  if (err) { err.textContent = msg; err.classList.remove('hidden'); }
  if (inp) inp.classList.add('error');
}

function _apmResetLog() {
  const w = document.getElementById('apm-log-wrap');
  const l = document.getElementById('apm-log');
  if (w) w.classList.add('hidden');
  if (l) l.textContent = '';
}

function _apmAppendLog(line) {
  const w = document.getElementById('apm-log-wrap');
  const l = document.getElementById('apm-log');
  if (w) w.classList.remove('hidden');
  if (l) { l.textContent += line + '\n'; l.scrollTop = l.scrollHeight; }
}

function _apmSleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Close + refresh. Home defines loadHomeData(); daily brief defines loadBrief().
async function _apmAfterSuccess() {
  closeAddProjectModal();
  try {
    await fetch('/api/brief/daily/regenerate', { method: 'POST' });
  } catch (_) { /* best-effort — nav still refreshes */ }
  if (typeof loadHomeData === 'function') {
    await loadHomeData(true);
  } else if (typeof loadBrief === 'function') {
    const d = typeof _curDate !== 'undefined' ? _curDate : null;
    await loadBrief(d);
  }
  if (typeof _prefetchFullRepo === 'function') {
    await _prefetchFullRepo();
  } else if (typeof loadHomeData !== 'function' && typeof loadBrief !== 'function') {
    window.location.reload();
  }
}

// Parse the repo slug from a URL or owner/repo string.
function _apmRepoNameFromUrl(url) {
  const s = (url || '').trim().replace(/\.git$/, '').replace(/\/+$/, '');
  const parts = s.split('/');
  return (parts[parts.length - 1] || '').trim();
}

function _apmProjectListed(projects, slug) {
  return (projects || []).some((p) =>
    p.slug === slug || String(p.repo || '').split('/').pop() === slug);
}

// Source-of-truth check: did the project end up registered? Retries tolerate a
// brief pause while projects.json is flushed (API init no longer restarts uvicorn).
async function _apmProjectExists(slug) {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      const r = await fetch('/api/projects');
      if (r.ok) {
        const d = await r.json();
        if (_apmProjectListed(d.projects, slug)) return true;
      }
    } catch (_) { /* retry */ }
    if (attempt < 4) await _apmSleep(1500);
  }
  return false;
}

// POST /api/projects/init and stream its SSE log into the shared log area.
// Returns true on a clean 'done'. Shared by create-new and clone-existing.
async function _apmRunInitStream(body, errFieldId) {
  _apmResetLog();
  const res = await fetch('/api/projects/init', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
  });
  if (res.status === 400 || res.status === 409 || res.status === 422) {
    const d = await res.json().catch(() => ({})); _apmShowError(errFieldId, d.detail || 'Error ' + res.status); return false;
  }
  if (!res.ok) { const d = await res.json().catch(() => ({})); _apmShowError(errFieldId, d.detail || 'Server error ' + res.status); return false; }

  const reader  = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '', success = false, lastErr = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split('\n\n');
    buffer = chunks.pop();
    for (const chunk of chunks) {
      let eventType = 'log', dataLine = '';
      for (const l of chunk.split('\n')) {
        if (l.startsWith('event: ')) eventType = l.slice(7).trim();
        if (l.startsWith('data: '))  dataLine  = l.slice(6).trim();
      }
      if (!dataLine) continue;
      let payload = dataLine;
      try { payload = JSON.parse(dataLine); } catch { /* raw */ }
      if (eventType === 'log')        _apmAppendLog(payload);
      else if (eventType === 'done')  success = true;
      else if (eventType === 'error') lastErr = payload;
    }
  }
  if (!success && lastErr) _apmShowError(errFieldId, lastErr);
  return success;
}

// Run an init/clone stream, then confirm via _apmProjectExists so a dropped SSE
// on a long operation doesn't report a false failure. Returns true if the
// project exists afterwards.
async function _apmRunInitConfirmed(body, errFieldId) {
  let ok = false;
  try {
    ok = await _apmRunInitStream(body, errFieldId);
  } catch (e) {
    _apmAppendLog('stream interrupted (' + e.message + ') — verifying…');
  }
  if (!ok) ok = await _apmProjectExists(body.repo_name);
  return ok;
}

async function apmSubmitAdd(event) {
  event.preventDefault();
  _apmClearError('apm-repo');

  const repoUrl = document.getElementById('apm-repo').value.trim();
  const icon    = document.getElementById('apm-icon').value.trim()  || 'ti-folder';
  const color   = document.getElementById('apm-color').value.trim() || 'gray';

  if (!repoUrl) { _apmShowError('apm-repo', 'GitHub repo URL is required.'); return; }

  const btn = document.getElementById('apm-submit');

  // Clone path: clone the existing repo into a nested layout via init_project.
  if (document.getElementById('apm-add-nested').checked) {
    const repoName = _apmRepoNameFromUrl(repoUrl);
    if (!repoName) { _apmShowError('apm-repo', 'Could not parse a repo name from that URL.'); return; }
    const projectsDir = document.getElementById('apm-add-projects-dir').value.trim() || '~/dev';
    const skipUat     = document.getElementById('apm-add-skip-uat').checked;
    btn.disabled = true; btn.textContent = 'Cloning…';
    const ok = await _apmRunInitConfirmed(
      { repo_name: repoName, projects_dir: projectsDir, nested: true, skip_uat: skipUat, from_existing: true },
      'apm-repo',
    );
    btn.disabled = false; _apmUpdateSubmitLabel();
    if (ok) await _apmAfterSuccess();
    else _apmShowError('apm-repo', 'Clone did not complete — see the log above.');
    return;
  }

  // Track-only path: register the repo in projects.json (no clone).
  btn.disabled = true; btn.textContent = 'Adding…';
  try {
    const res = await fetch('/api/projects', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ repo_url: repoUrl, icon, color }),
    });
    if (res.status === 409) { const d = await res.json().catch(() => ({})); _apmShowError('apm-repo', d.detail || 'Project already added.'); return; }
    if (res.status === 422) { const d = await res.json().catch(() => ({})); _apmShowError('apm-repo', d.detail || 'Invalid repo or repo not found on GitHub.'); return; }
    if (!res.ok)            { const d = await res.json().catch(() => ({})); _apmShowError('apm-repo', d.detail || 'Error ' + res.status); return; }
    await _apmAfterSuccess();
  } catch (e) {
    _apmShowError('apm-repo', 'Network error: ' + e.message);
  } finally {
    btn.disabled = false; _apmUpdateSubmitLabel();
  }
}

async function apmSubmitInit(event) {
  event.preventDefault();
  _apmClearError('apm-name');
  _apmResetLog();

  const repoName    = document.getElementById('apm-name').value.trim();
  const projectsDir = document.getElementById('apm-projects-dir').value.trim() || '~/dev';
  const nested      = document.getElementById('apm-nested').checked;
  const skipUat     = document.getElementById('apm-skip-uat').checked;

  if (!repoName) { _apmShowError('apm-name', 'Project name is required.'); return; }
  if (repoName.includes('/') || repoName.includes('\\')) {
    _apmShowError('apm-name', 'Project name must not contain path separators (/ or \\).');
    return;
  }

  const btn = document.getElementById('apm-submit');
  btn.disabled = true; btn.textContent = 'Creating…';
  const ok = await _apmRunInitConfirmed(
    { repo_name: repoName, projects_dir: projectsDir, nested, skip_uat: skipUat },
    'apm-name',
  );
  btn.disabled = false; _apmUpdateSubmitLabel();
  if (ok) await _apmAfterSuccess();
  else if (!document.getElementById('apm-init-error').textContent) {
    _apmShowError('apm-name', 'init_project.py failed — see the log above.');
  }
}
