/* Bulk Complete Sprint modal — merge branch chain then settle lineage.

 * Step 1: merge each child → base → develop (board overlay progress, like hotswap).
 * Step 2: close UAT/summary issues and mark every member sprint completed.
 */

/* global _setBodyInert, _clearBodyInert, _smgmtRepo, sprintLabelDisplay,
   escHtml, _smgmtShowToast, loadSprintMgmt,
   _smgmtBoardLock, _smgmtBoardUnlock, _smgmtBoardProgress, _smgmtBoardLog,
   _bcLabel:writable, _bcPreview:writable, renderProgressActivity */

function _bcShowPreviewLoading(current) {
  const loading = document.getElementById('bc-loading');
  if (!loading) return;
  loading.innerHTML = renderProgressActivity({
    status: 'running',
    mode: 'indeterminate',
    current: current || 'Loading preview…',
  }, {
    id: 'bc-preview-pa',
    hideLog: true,
  });
  loading.classList.remove('hidden');
}

export function _bcOpen() {
  _setBodyInert(['bc-backdrop', 'bc-modal']);
  document.getElementById('bc-backdrop').classList.remove('hidden');
  document.getElementById('bc-modal').classList.remove('hidden');
}
export function _bcClose() {
  document.getElementById('bc-backdrop').classList.add('hidden');
  document.getElementById('bc-modal').classList.add('hidden');
  _clearBodyInert();
  _bcLabel = null;
  _bcPreview = null;
}
export function _bcCatClass(cat) {
  if (cat === 'UAT')            return 'rr-cat-uat';
  if (cat === 'SIT')            return 'rr-cat-sit';
  if (cat === 'needs-rework')   return 'rr-cat-rework';
  if (cat === 'sprint-summary') return 'rr-cat-summary';
  return 'rr-cat-queued';
}
export function _bcSelectAll(checked) {
  document.querySelectorAll('#bc-ticket-list input[type=checkbox]').forEach(cb => { cb.checked = checked; });
}

export async function smgmtBulkCompleteSprint(label) {
  const repo = _smgmtRepo();
  if (!repo) return;
  _bcLabel = label;
  _bcPreview = null;
  const parts = repo.split('/');
  const owner = parts[0];
  const repoName = parts.slice(1).join('/');

  document.getElementById('bc-modal-title').textContent =
    `Bulk complete ${sprintLabelDisplay(label)}?`;
  _bcShowPreviewLoading('Loading preview…');
  document.getElementById('bc-content').classList.add('hidden');
  document.getElementById('bc-error').classList.add('hidden');
  document.getElementById('bc-error').textContent = '';
  const confirmBtn = document.getElementById('bc-confirm-btn');
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Bulk complete'; }
  _bcOpen();

  try {
    const preview = await _bcFetchPreview(owner, repoName, label);
    _bcPreview = preview;

    if (preview.conflict_error) {
      throw new Error(preview.conflict_error);
    }

    const listEl = document.getElementById('bc-ticket-list');
    const allTickets = preview.all_tickets || [];
    if (allTickets.length === 0) {
      listEl.innerHTML = '<div style="padding:10px;color:var(--text-muted);font-size:13px">No open tickets in this sprint lineage.</div>';
    } else {
      listEl.innerHTML = allTickets.map(t => {
        const catClass = _bcCatClass(t.category);
        const catLabel = t.category === 'sprint-summary' ? 'SUMMARY' : t.category.toUpperCase();
        return `<label class="rr-ticket-row">
          <input type="checkbox" checked data-issue="${t.number}" onchange="">
          <span class="rr-ticket-num">#${t.number}</span>
          <span class="rr-ticket-title" title="${escHtml(t.title)}">${escHtml(t.title)}</span>
          <span class="rr-ticket-cat ${catClass}">${escHtml(catLabel)}</span>
        </label>`;
      }).join('');
    }

    const memberCount = (preview.member_labels || []).length;
    const mergeSteps = preview.merge_steps || [];
    const actionsEl = document.getElementById('bc-actions');
    const actionRows = [];
    for (const step of mergeSteps) {
      actionRows.push(
        `<div class="fs-action-row"><i class="ti ti-git-merge"></i> Merge `
        + `<code>${escHtml(step.head)}</code> → <code>${escHtml(step.base)}</code></div>`,
      );
    }
    actionRows.push(
      `<div class="fs-action-row"><i class="ti ti-circle-check"></i> Close all ${allTickets.length} ticket${allTickets.length !== 1 ? 's' : ''} (UAT + summary included)</div>`,
      `<div class="fs-action-row"><i class="ti ti-flag-check"></i> Mark ${memberCount} sprint${memberCount !== 1 ? 's' : ''} completed</div>`,
    );
    actionsEl.innerHTML = actionRows.join('');

    document.getElementById('bc-loading').classList.add('hidden');
    document.getElementById('bc-content').classList.remove('hidden');
    if (confirmBtn) confirmBtn.disabled = false;
  } catch (e) {
    document.getElementById('bc-loading').classList.add('hidden');
    const errEl = document.getElementById('bc-error');
    errEl.textContent = 'Failed to load preview: ' + e.message;
    errEl.classList.remove('hidden');
  }
}

async function _bcFetchPreview(owner, repoName, label) {
  const res = await fetch(
    `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/bulk-complete-preview`,
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = err.detail || `HTTP ${res.status}`;
    if (res.status === 409 && /merge conflict/i.test(detail)) {
      throw new Error(`Merge conflict — bulk complete stopped: ${detail}`);
    }
    throw new Error(detail);
  }
  return res.json();
}

/** Re-query merge chain after each merge — parent→develop only appears once child→parent lands. */
async function _bcRemainingMergeSteps(owner, repoName, label) {
  const preview = await _bcFetchPreview(owner, repoName, label);
  return preview.merge_steps || [];
}

async function _bcMergeStep(owner, repoName, step) {
  const res = await fetch(
    `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprint-branch-merge`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        confirmed: true,
        head: step.head,
        base: step.base,
        title: step.title || '',
        delete_branch: step.delete_branch !== false,
      }),
    },
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = err.detail || `HTTP ${res.status}`;
    if (res.status === 409 && /merge conflict/i.test(detail)) {
      throw new Error(`Merge conflict — bulk complete stopped: ${detail}`);
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function _bcConfirm() {
  const repo = _smgmtRepo();
  if (!_bcLabel || !repo || !_bcPreview) return;
  const parts = repo.split('/');
  const owner = parts[0];
  const repoName = parts.slice(1).join('/');
  const label = _bcLabel;
  // Per-step complete: walk the lineage deepest child → base, finalising each
  // sprint (merge → close summary → mark completed; base also closes tickets +
  // merges to develop) via the idempotent complete-step endpoint. A conflict at
  // any step stops here with a clear message; resolve it and re-run to resume
  // (merged/closed steps are skipped).
  const order = (_bcPreview.complete_order || []).slice();

  const confirmBtn = document.getElementById('bc-confirm-btn');
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Completing…'; }

  _bcClose();

  if (order.length === 0) { _smgmtShowToast('Nothing to complete.'); return; }

  let doneSteps = 0;
  const totalSteps = order.length + 1; // members + board refresh

  _smgmtBoardLock(`Completing ${sprintLabelDisplay(label)}…`, {
    progress: true,
    total: totalSteps,
    clearLog: true,
  });
  _smgmtBoardLog('Starting per-step complete (deepest child first)…', 'step');

  try {
    for (const sLabel of order) {
      _smgmtBoardLog(`Completing ${sprintLabelDisplay(sLabel)}…`, 'step');
      const res = await fetch(
        `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(sLabel)}/complete-step`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirmed: true }),
        },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Failed completing ${sLabel} (HTTP ${res.status})`);
      }
      const sd = await res.json();
      doneSteps += 1;
      _smgmtBoardProgress(doneSteps, totalSteps);
      const into = sd.merged ? ` → merged into ${sd.merged_into}` : '';
      _smgmtBoardLog(`✓ ${sprintLabelDisplay(sLabel)} completed${into}`, 'ok');
    }

    _smgmtBoardLog('Refreshing board…', 'step');
    await loadSprintMgmt();
    doneSteps += 1;
    _smgmtBoardProgress(doneSteps, totalSteps);
    _smgmtBoardLog('✓ Complete finished', 'ok');

    _smgmtShowToast(`${sprintLabelDisplay(label)} completed — ${order.length} sprint(s) settled.`);
  } catch (e) {
    _smgmtBoardLog(`✗ ${e.message}`, 'err');
    _smgmtShowToast('Stopped: ' + e.message + ' — resolve the conflict, then re-run to resume.');
    try { await loadSprintMgmt(); } catch (_) { /* ignore */ }
  } finally {
    _smgmtBoardUnlock();
  }
}
