/* Project Settings — sprint file cleanup actions (issue #735 / #808).
 *
 * Each button opens a confirm + progress modal (move-sprint pattern) and appends
 * lines to the persistent cleanup console on the Settings card.
 */

/* global escHtml, _slug, _cachedFullRepo, _setBodyInert, _clearBodyInert,
          renderProgressActivity, mountProgressActivity, patchProgressActivity,
          appendProgressActivityLog, unmountProgressActivity, _histScanStale */

const CLN_PA_ID = 'ps-cln-pa';

let _psCleanupConfirmFn = null;
let _psCleanupBusy = false;

function _psProjectSlug() {
  const slug = (typeof _slug !== 'undefined' && _slug) || window._currentProjectSlug || '';
  return String(slug || '').trim();
}

function _psProjectRepo() {
  const slug = _psProjectSlug();
  if (typeof _cachedFullRepo !== 'undefined' && slug && _cachedFullRepo[slug]) {
    return _cachedFullRepo[slug];
  }
  return slug;
}

function _psCleanupStatus(text) {
  const el = document.getElementById('ps-cleanup-status');
  if (el) el.textContent = text || '';
}

function _psCleanupLog(tag, message, kind, data) {
  const wrap = document.getElementById('ps-cleanup-log');
  const body = document.getElementById('ps-cleanup-log-body');
  if (!body) return;
  if (wrap) wrap.hidden = false;
  const ts = new Date().toLocaleTimeString();
  const kindClass =
    kind === 'ok' ? 'ps-cleanup-log-line--ok'
      : kind === 'err' ? 'ps-cleanup-log-line--err'
        : 'ps-cleanup-log-line--step';
  let extra = '';
  if (data !== undefined) {
    try { extra = ' ' + JSON.stringify(data); } catch (_) { extra = ' ' + String(data); }
  }
  const line = document.createElement('div');
  line.className = 'ps-cleanup-log-line ' + kindClass;
  line.textContent = ts + ' [' + tag + '] ' + message + extra;
  body.appendChild(line);
  body.scrollTop = body.scrollHeight;
}

export function psCleanupLogClear() {
  const body = document.getElementById('ps-cleanup-log-body');
  if (body) body.innerHTML = '';
  const wrap = document.getElementById('ps-cleanup-log');
  if (wrap) wrap.hidden = true;
}

function _psCleanupModalReset() {
  _psCleanupConfirmFn = null;
  _psCleanupBusy = false;
  const err = document.getElementById('ps-cln-error');
  if (err) { err.textContent = ''; err.classList.add('hidden'); }
  const list = document.getElementById('ps-cln-list');
  if (list) list.innerHTML = '';
  const summary = document.getElementById('ps-cln-summary');
  if (summary) summary.textContent = '';
  const review = document.getElementById('ps-cln-review');
  const progress = document.getElementById('ps-cln-progress');
  if (review) review.hidden = false;
  if (progress) {
    progress.hidden = true;
    progress.innerHTML = '';
    unmountProgressActivity('ps-cln-pa-host');
  }
  const confirmBtn = document.getElementById('ps-cln-confirm');
  const doneBtn = document.getElementById('ps-cln-done');
  const cancelBtn = document.getElementById('ps-cln-cancel');
  if (confirmBtn) { confirmBtn.hidden = false; confirmBtn.disabled = false; }
  if (doneBtn) doneBtn.hidden = true;
  if (cancelBtn) cancelBtn.hidden = false;
}

export function _psCleanupModalClose() {
  if (_psCleanupBusy) return;
  document.getElementById('ps-cln-backdrop')?.classList.add('hidden');
  document.getElementById('ps-cln-modal')?.classList.add('hidden');
  if (typeof _clearBodyInert === 'function') _clearBodyInert();
  _psCleanupModalReset();
}

function _psCleanupModalOpen(title) {
  _psCleanupModalReset();
  const titleEl = document.getElementById('ps-cln-title');
  if (titleEl) titleEl.textContent = title || 'Cleanup';
  document.getElementById('ps-cln-backdrop')?.classList.remove('hidden');
  document.getElementById('ps-cln-modal')?.classList.remove('hidden');
  if (typeof _setBodyInert === 'function') {
    _setBodyInert(['ps-cln-backdrop', 'ps-cln-modal']);
  }
}

function _psCleanupModalLoading(message) {
  const progress = document.getElementById('ps-cln-progress');
  const review = document.getElementById('ps-cln-review');
  if (review) review.hidden = true;
  if (!progress) return;
  progress.hidden = false;
  progress.innerHTML = '<div id="ps-cln-pa-host"></div>';
  mountProgressActivity('ps-cln-pa-host', {
    status: 'running',
    mode: 'indeterminate',
    current: message || 'Working…',
    log_tail: [],
  }, { id: CLN_PA_ID, hideLog: true });
  const confirmBtn = document.getElementById('ps-cln-confirm');
  const cancelBtn = document.getElementById('ps-cln-cancel');
  if (confirmBtn) confirmBtn.hidden = true;
  if (cancelBtn) cancelBtn.hidden = true;
}

function _psCleanupModalShowReview(opts) {
  const review = document.getElementById('ps-cln-review');
  const progress = document.getElementById('ps-cln-progress');
  if (progress) { progress.hidden = true; progress.innerHTML = ''; }
  if (review) review.hidden = false;

  const items = opts.items || [];
  const shown = items.slice(0, 60);
  const more = items.length - shown.length;
  const summaryEl = document.getElementById('ps-cln-summary');
  if (summaryEl) summaryEl.textContent = opts.summary || '';
  const listEl = document.getElementById('ps-cln-list');
  if (listEl) {
    if (!items.length) {
      listEl.innerHTML = '<li style="color:var(--text-muted)">' + escHtml(opts.emptyMsg || 'Nothing to clean.') + '</li>';
    } else {
      listEl.innerHTML = shown.map((f) => '<li>' + escHtml(String(f)) + '</li>').join('')
        + (more > 0 ? '<li style="color:var(--text-muted)">… and ' + more + ' more</li>' : '');
    }
  }

  _psCleanupConfirmFn = opts.onConfirm || null;
  const confirmBtn = document.getElementById('ps-cln-confirm');
  const doneBtn = document.getElementById('ps-cln-done');
  const cancelBtn = document.getElementById('ps-cln-cancel');
  if (confirmBtn) {
    const canConfirm = !!(items.length && opts.onConfirm);
    confirmBtn.hidden = !canConfirm;
    confirmBtn.disabled = !canConfirm;
    confirmBtn.textContent = opts.confirmLabel || 'Confirm';
  }
  if (doneBtn) doneBtn.hidden = true;
  if (cancelBtn) cancelBtn.hidden = false;
}

function _psCleanupModalShowDone(message) {
  _psCleanupConfirmFn = null;
  _psCleanupBusy = false;
  const summaryEl = document.getElementById('ps-cln-summary');
  if (summaryEl) summaryEl.textContent = message || 'Done.';
  const listEl = document.getElementById('ps-cln-list');
  if (listEl) listEl.innerHTML = '';
  const confirmBtn = document.getElementById('ps-cln-confirm');
  const doneBtn = document.getElementById('ps-cln-done');
  const cancelBtn = document.getElementById('ps-cln-cancel');
  if (confirmBtn) confirmBtn.hidden = true;
  if (cancelBtn) cancelBtn.hidden = true;
  if (doneBtn) doneBtn.hidden = false;
  unmountProgressActivity('ps-cln-pa-host');
  const progress = document.getElementById('ps-cln-progress');
  if (progress) { progress.hidden = true; progress.innerHTML = ''; }
}

function _psCleanupModalShowError(message) {
  _psCleanupBusy = false;
  _psCleanupConfirmFn = null;
  const err = document.getElementById('ps-cln-error');
  if (err) {
    err.textContent = message || 'Something went wrong.';
    err.classList.remove('hidden');
  }
  const confirmBtn = document.getElementById('ps-cln-confirm');
  const cancelBtn = document.getElementById('ps-cln-cancel');
  if (confirmBtn) confirmBtn.hidden = true;
  if (cancelBtn) cancelBtn.hidden = false;
  unmountProgressActivity('ps-cln-pa-host');
}

async function _psCleanupModalConfirm() {
  if (!_psCleanupConfirmFn || _psCleanupBusy) return;
  _psCleanupBusy = true;
  const confirmBtn = document.getElementById('ps-cln-confirm');
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Working…'; }
  _psCleanupModalLoading('Applying…');
  try {
    const msg = await _psCleanupConfirmFn((line, kind) => {
      appendProgressActivityLog('ps-cln-pa-host', line, kind === 'err' ? 'fail' : 'dispatch', { id: CLN_PA_ID });
    });
    _psCleanupModalShowDone(msg || 'Done.');
  } catch (e) {
    _psCleanupLog('cleanup', e.message || String(e), 'err');
    _psCleanupModalShowError(e.message || String(e));
  }
}

async function _psCleanupPreviewFlow(tag, title, fetchPreview, buildReview) {
  if (_psCleanupBusy) return;
  _psCleanupStatus('');
  _psCleanupLog(tag, 'Starting preview…', 'step');
  _psCleanupModalOpen(title);
  _psCleanupModalLoading('Scanning…');
  try {
    const data = await fetchPreview();
    const review = buildReview(data);
    _psCleanupLog(tag, review.logMsg || 'Preview ready', 'ok', review.logData);
    _psCleanupModalShowReview(review);
  } catch (e) {
    const msg = e.message || String(e);
    _psCleanupLog(tag, msg, 'err');
    _psCleanupStatus('Preview failed: ' + msg);
    _psCleanupModalShowError(msg);
  }
}

async function _sprintCleanupPost(dryRun) {
  const slug = _psProjectSlug();
  if (!slug) throw new Error('Project not loaded — switch to Settings again.');
  const resp = await fetch('/api/maintenance/sprints/cleanup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project: slug, dry_run: dryRun }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || ('HTTP ' + resp.status));
  }
  return resp.json();
}

async function _testFilesCleanupPost(dryRun) {
  const slug = _psProjectSlug();
  if (!slug) throw new Error('Project not loaded — switch to Settings again.');
  const resp = await fetch('/api/maintenance/tests/cleanup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project: slug, keep: 100, dry_run: dryRun }),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error(err.detail || ('HTTP ' + resp.status));
  }
  return resp.json();
}

export async function sprintCleanupPreview() {
  await _psCleanupPreviewFlow('sprint-files', 'Archive stale sprint runtime files', () => _sprintCleanupPost(true), (data) => {
    const files = (data && data.archived) || [];
    return {
      logMsg: files.length ? (files.length + ' file(s) to archive') : 'Nothing to archive',
      logData: { count: files.length },
      title: 'Archive stale sprint runtime files',
      summary: files.length ? (files.length + ' file(s) will be archived.') : '',
      items: files,
      emptyMsg: 'No stale runtime files to archive.',
      confirmLabel: 'Archive ' + files.length,
      onConfirm: async (log) => {
        if (log) log('Archiving sprint runtime files…', 'step');
        const r = await _sprintCleanupPost(false);
        const n = (r && r.archived) ? r.archived.length : 0;
        const kept = (r && typeof r.kept_count === 'number') ? r.kept_count : '?';
        _psCleanupLog('sprint-files', 'Archived ' + n + ' file(s)', 'ok', { kept_count: kept });
        _psCleanupStatus('Archived ' + n + ' file(s); ' + kept + ' kept in place.');
        return 'Archived ' + n + ' file(s); ' + kept + ' kept.';
      },
    };
  });
}

export async function testFilesCleanupPreview() {
  await _psCleanupPreviewFlow('test-files', 'Clean old test files', () => _testFilesCleanupPost(true), (data) => {
    const files = (data && data.remove) || [];
    return {
      logMsg: files.length ? (files.length + ' test file(s) to remove') : 'No old test files',
      logData: { remove: files.length, kept: (data && data.kept_count) || 0 },
      title: 'Clean old test files',
      summary: 'Keeping ' + ((data && data.kept_count) || 0) + ' newest of '
        + ((data && data.total_count) || 0) + ' test files (git recency).',
      items: files,
      emptyMsg: 'No old test files to remove.',
      confirmLabel: 'Delete ' + files.length,
      onConfirm: async (log) => {
        if (log) log('Deleting old test files…', 'step');
        const r = await _testFilesCleanupPost(false);
        const n = (r && r.deleted) ? r.deleted.length : 0;
        _psCleanupLog('test-files', 'Deleted ' + n + ' test file(s)', 'ok', { kept: (r && r.kept_count) || 0 });
        _psCleanupStatus('Deleted ' + n + ' test file(s); kept ' + ((r && r.kept_count) || 0) + '.');
        return 'Deleted ' + n + ' test file(s); kept ' + ((r && r.kept_count) || 0) + '.';
      },
    };
  });
}

/** Scan-only — updates History chips; no destructive action. */
export async function psStaleBranchesScan() {
  const repo = _psProjectRepo();
  if (!repo) {
    _psCleanupLog('branches', 'Project repo not loaded', 'err');
    _psCleanupStatus('Project repo not loaded.');
    return;
  }
  if (_psCleanupBusy) return;
  _psCleanupStatus('');
  _psCleanupLog('branches', 'Scanning remote for stale branches…', 'step');
  _psCleanupModalOpen('Scan stale branches');
  _psCleanupModalLoading('Scanning remote…');
  _psCleanupBusy = true;
  const btn = document.getElementById('ps-stale-scan-btn');
  if (btn) btn.disabled = true;
  try {
    const resp = await fetch('/scan-stale-branches?repo=' + encodeURIComponent(repo));
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const data = await resp.json();
    const branches = (data.branches || []).map((b) => b.branch || b);
    if (typeof _histScanStale === 'function') await _histScanStale();
    _psCleanupLog('branches', 'Scan complete', 'ok', { count: branches.length });
    _psCleanupStatus(branches.length ? (branches.length + ' stale branch(es) found.') : 'No stale branches found.');
    _psCleanupModalShowReview({
      title: 'Scan stale branches',
      summary: branches.length
        ? (branches.length + ' feature branch(es) flagged on History rows.')
        : 'No leftover feature branches on the remote.',
      items: branches,
      emptyMsg: 'No stale branches found.',
      confirmLabel: '',
      onConfirm: null,
    });
    const confirmBtn = document.getElementById('ps-cln-confirm');
    const doneBtn = document.getElementById('ps-cln-done');
    if (confirmBtn) confirmBtn.hidden = true;
    if (doneBtn) doneBtn.hidden = false;
    _psCleanupBusy = false;
  } catch (e) {
    const msg = e.message || String(e);
    _psCleanupLog('branches', msg, 'err');
    _psCleanupStatus('Scan failed: ' + msg);
    _psCleanupModalShowError(msg);
  } finally {
    if (btn) btn.disabled = false;
    _psCleanupBusy = false;
  }
}

export async function psPruneMergedBranches() {
  const repo = _psProjectRepo();
  if (!repo) {
    _psCleanupLog('branches', 'Project repo not loaded', 'err');
    _psCleanupStatus('Project repo not loaded.');
    return;
  }
  await _psCleanupPreviewFlow('branches', 'Prune merged feature branches', async () => {
    const scanResp = await fetch('/scan-stale-branches?repo=' + encodeURIComponent(repo));
    if (!scanResp.ok) throw new Error('HTTP ' + scanResp.status);
    const scanData = await scanResp.json();
    const branches = (scanData.branches || []).map((b) => b.branch || b);
    if (!branches.length) return { toDelete: [], skipped: [], branches: [] };
    const dryResp = await fetch('/cleanup-stale-branches', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo, branches, confirm: false }),
    });
    if (!dryResp.ok) throw new Error('HTTP ' + dryResp.status);
    const plan = await dryResp.json();
    return {
      toDelete: plan.to_delete || [],
      skipped: plan.skipped_unmerged || [],
      branches,
    };
  }, (plan) => {
    const toDelete = plan.toDelete || [];
    const skipped = plan.skipped || [];
    return {
      logMsg: toDelete.length ? (toDelete.length + ' merged branch(es) to delete') : 'No merged branches to delete',
      logData: { delete: toDelete.length, skipped: skipped.length },
      title: 'Prune merged feature branches',
      summary: skipped.length
        ? (skipped.length + ' unmerged branch(es) skipped — never deleted.')
        : 'Only fully-merged branches are deleted.',
      items: toDelete,
      emptyMsg: 'No merged branches to delete.',
      confirmLabel: 'Delete ' + toDelete.length,
      onConfirm: async (log) => {
        if (log) log('Deleting merged branches…', 'step');
        const resp = await fetch('/cleanup-stale-branches', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ repo, branches: plan.branches, confirm: true }),
        });
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        const result = await resp.json();
        const deleted = (result.deleted || []).length;
        const failed = (result.failed || []).length;
        if (typeof _histScanStale === 'function') await _histScanStale();
        _psCleanupLog('branches', 'Deleted ' + deleted + ' branch(es)', failed ? 'err' : 'ok', { failed });
        _psCleanupStatus('Deleted ' + deleted + ' merged branch' + (deleted !== 1 ? 'es' : '')
          + (failed ? ' (' + failed + ' failed)' : '') + '.');
        return 'Deleted ' + deleted + ' merged branch' + (deleted !== 1 ? 'es' : '')
          + (failed ? ' (' + failed + ' failed)' : '') + '.';
      },
    };
  });
}

export async function psCleanupModalConfirm() {
  await _psCleanupModalConfirm();
}

// Legacy no-ops kept for any stale inline confirm panels in project.html.
export function sprintCleanupConfirm() {}
export function testFilesCleanupConfirm() {}
export function _psCleanupPaneClose() {}
export function _psCleanupPaneConfirm() {}
