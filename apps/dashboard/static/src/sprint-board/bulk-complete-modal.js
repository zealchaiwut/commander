/* Bulk Complete Sprint modal — merge branch chain then settle lineage.

 * Step 1: merge each child → base → develop (board overlay progress, like hotswap).
 * Step 2: close UAT/summary issues and mark every member sprint completed.
 */

/* global _setBodyInert, _clearBodyInert, _smgmtRepo, sprintLabelDisplay,
   escHtml, _smgmtShowToast, loadSprintMgmt,
   _smgmtBoardLock, _smgmtBoardUnlock, _smgmtBoardProgress, _smgmtBoardLog, _smgmtBoardFinish,
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
    // Group the close-list by the sprint that owns each ticket (point 2) so it
    // reads "94.4: #1460 #1464 · 94.2: #1461 …" instead of one flat list.
    const groups = (preview.tickets_by_sprint && preview.tickets_by_sprint.length)
      ? preview.tickets_by_sprint
      : (allTickets.length ? [{ label: preview.base_label, tickets: allTickets }] : []);
    const _ticketRow = (t) => {
      const catClass = _bcCatClass(t.category);
      const catLabel = t.category === 'sprint-summary' ? 'SUMMARY' : t.category.toUpperCase();
      return `<label class="rr-ticket-row">
          <input type="checkbox" checked data-issue="${t.number}" onchange="">
          <span class="rr-ticket-num">#${t.number}</span>
          <span class="rr-ticket-title" title="${escHtml(t.title)}">${escHtml(t.title)}</span>
          <span class="rr-ticket-cat ${catClass}">${escHtml(catLabel)}</span>
        </label>`;
    };
    if (groups.length === 0) {
      listEl.innerHTML = '<div style="padding:10px;color:var(--text-muted);font-size:13px">No open tickets in this sprint lineage.</div>';
    } else {
      listEl.innerHTML = groups.map(g => (
        `<div style="font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.04em;margin:8px 0 2px">`
        + `${escHtml(sprintLabelDisplay(g.label))} · ${g.tickets.length}</div>`
        + g.tickets.map(_ticketRow).join('')
      )).join('');
    }

    // Full chain (deepest → base) with per-step status (point 1): done steps show
    // ✓ merged/completed, the rest ○ pending — so you see what already happened.
    const members = preview.members || [];
    const actionsEl = document.getElementById('bc-actions');
    const actionRows = members.map(m => {
      const target = m.is_base ? 'develop' : sprintLabelDisplay(m.parent);
      let icon, color, note;
      if (m.completed) { icon = 'ti-circle-check-filled'; color = 'var(--green)'; note = 'completed'; }
      else if (m.merged) { icon = 'ti-git-merge'; color = 'var(--green)'; note = 'merged → will mark completed'; }
      else { icon = 'ti-circle'; color = 'var(--text-sub)'; note = 'pending'; }
      return `<div class="fs-action-row"><i class="ti ${icon}" style="color:${color}"></i> `
        + `${escHtml(sprintLabelDisplay(m.label))} → ${escHtml(target)} `
        + `<span style="color:var(--text-muted);font-size:11px">· ${note}</span></div>`;
    });
    const memberCount = members.length || (preview.member_labels || []).length;
    actionRows.push(
      `<div class="fs-action-row"><i class="ti ti-circle-check"></i> Close ${allTickets.length} ticket${allTickets.length !== 1 ? 's' : ''} (grouped above; UAT + summary)</div>`,
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

/** Run bulk complete (complete-step chain) for one lineage base; resolves when done. */
export async function bulkCompleteLineageAndWait(label) {
  const repo = _smgmtRepo();
  if (!repo) throw new Error('No project loaded');
  const parts = repo.split('/');
  const owner = parts[0];
  const repoName = parts.slice(1).join('/');
  const preview = await _bcFetchPreview(owner, repoName, label);
  const order = (preview.complete_order || []).slice();
  if (!order.length) throw new Error('Nothing to bulk complete');
  for (const sLabel of order) {
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
  }
  return { label, steps: order.length };
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
  let failedIdx = 0; // index of the step that threw

  _smgmtBoardLock(`Completing ${sprintLabelDisplay(label)}…`, {
    progress: true,
    total: totalSteps,
    clearLog: true,
    showDone: true,   // Done button stays disabled until the run settles
  });
  _smgmtBoardLog('Starting per-step complete (deepest child first)…', 'step');

  const _onDone = () => {
    if (typeof globalThis._histResetLedgerCache === 'function') globalThis._histResetLedgerCache();
    loadSprintMgmt().catch(() => {});
  };

  try {
    for (let i = 0; i < order.length; i++) {
      failedIdx = i;
      const sLabel = order[i];
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

    // Stay open with an enabled Done button — the operator reads the log, then
    // Done closes + refreshes. No auto-dismiss on success.
    _smgmtBoardFinish({
      ok: true,
      message: `✓ ${sprintLabelDisplay(label)} completed — ${order.length} sprint(s) settled.`,
      onDone: _onDone,
    });
  } catch (e) {
    // Keep the overlay open with the error + a Done button so the operator can
    // read what failed (e.g. a merge conflict) before the board refreshes.
    _smgmtBoardLog(`✗ ${e.message}`, 'err');
    const isConflict = /merge conflict/i.test(e.message);
    _smgmtBoardFinish({
      ok: false,
      message: 'Stopped: ' + e.message + (isConflict
        ? '\n\nClick "Resolve with AI" to fix automatically, or resolve manually and re-run.'
        : '\n\nResolve the conflict, then re-run Bulk complete to resume (done steps are skipped).'),
      onDone: _onDone,
    });
    if (isConflict) {
      const cinfo = _bcParseConflictInfo(e.message);
      if (cinfo) {
        setTimeout(() => {
          _bcInjectResolveButton(cinfo, owner, repoName, label, order, failedIdx, doneSteps, totalSteps, _onDone);
        }, 0);
      }
    }
  }
}

/** Parse head/base branch names from a merge-conflict error message. */
function _bcParseConflictInfo(msg) {
  // Matches the child→parent step "Merge sprint-97.5 → sprint-97.4 failed" AND
  // the base→develop step "Merge sprint-92 → develop failed" (or ASCII >). The
  // base may be another sprint branch or the integration branch (develop/master)
  // — without the develop/master alternative the base step got no "Resolve with
  // AI" button (the last lineage step ships to develop).
  const m = msg.match(/Merge\s+(sprint-[\d.]+)\s*[→>]\s*(sprint-[\d.]+|develop|master)\s+failed/i);
  if (!m) return null;
  const baseRaw = m[2];
  const base = /^(develop|master)$/i.test(baseRaw) ? baseRaw : `sprint/${baseRaw}`;
  return { head: `sprint/${m[1]}`, base };
}

/** Inject a "Resolve with AI" button before the Done button in the board overlay. */
function _bcInjectResolveButton(cinfo, owner, repoName, label, order, fromIdx, doneSteps, totalSteps, onDone) {
  const doneEl = document.getElementById('smgmt-op-done');
  if (!doneEl) return;
  const existing = document.getElementById('smgmt-op-resolve-ai-btn');
  if (existing) existing.remove();

  const btn = document.createElement('button');
  btn.id = 'smgmt-op-resolve-ai-btn';
  btn.type = 'button';
  btn.className = 'btn-primary';
  btn.textContent = '✦ Resolve with AI';
  btn.style.cssText = 'margin-right:8px;background:var(--violet,#6e56cf);border-color:var(--violet,#6e56cf)';
  btn.onclick = () => {
    btn.disabled = true;
    _bcLaunchAIResolve(cinfo, owner, repoName, label, order, fromIdx, doneSteps, totalSteps, onDone);
  };

  const doneBtn = document.getElementById('smgmt-op-done-btn');
  if (doneBtn) doneEl.insertBefore(btn, doneBtn);
  else doneEl.prepend(btn);
}

/** Take over the board overlay, run the AI resolver, then resume bulk complete. */
async function _bcLaunchAIResolve(cinfo, owner, repoName, label, order, fromIdx, doneSteps, totalSteps, onDone) {
  const spinner = document.getElementById('smgmt-move-spinner');
  const msgEl = document.getElementById('smgmt-move-overlay-msg');
  const errEl = document.getElementById('smgmt-op-error');
  const doneEl = document.getElementById('smgmt-op-done');
  const overlay = document.getElementById('smgmt-move-overlay');

  if (spinner) spinner.style.display = '';
  if (errEl) { errEl.hidden = true; errEl.textContent = ''; }
  if (doneEl) { doneEl.hidden = true; doneEl.innerHTML = ''; }
  if (overlay) overlay.setAttribute('aria-busy', 'true');

  const startMs = Date.now();
  const timerInterval = setInterval(() => {
    const secs = Math.floor((Date.now() - startMs) / 1000);
    const mins = Math.floor(secs / 60);
    const ts = mins > 0 ? `${mins}m ${secs % 60}s` : `${secs}s`;
    if (msgEl) msgEl.textContent = `Resolving conflicts with AI… (${ts})`;
  }, 1000);
  if (msgEl) msgEl.textContent = 'Resolving conflicts with AI… (0s)';

  try {
    const startRes = await fetch(
      `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/resolve-branch-conflict`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ head: cinfo.head, base: cinfo.base }),
      },
    );
    if (!startRes.ok) {
      const err = await startRes.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${startRes.status}`);
    }
    const { job_key } = await startRes.json();

    _smgmtBoardLog('AI resolver started…', 'step');
    await new Promise((resolve, reject) => {
      const es = new EventSource(
        `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/resolve-conflict-stream/${encodeURIComponent(job_key)}`,
      );
      es.onmessage = (ev) => {
        try {
          const snap = JSON.parse(ev.data);
          if (snap.ping) return;
          if (snap.current && snap.status === 'running') _smgmtBoardLog(snap.current, 'step');
          if (snap.status === 'done') { es.close(); resolve(snap); }
          else if (snap.status === 'error') { es.close(); reject(new Error(snap.error || 'Resolve failed')); }
        } catch (_) {}
      };
      es.onerror = () => { es.close(); reject(new Error('SSE connection lost')); };
    });

    clearInterval(timerInterval);
    if (msgEl) msgEl.textContent = '✓ Resolved — retrying bulk complete…';
    _smgmtBoardLog('✓ Conflicts resolved — resuming…', 'ok');
    if (spinner) spinner.style.display = '';

    await _bcResumeFrom(owner, repoName, label, order, fromIdx, doneSteps, totalSteps, onDone);

  } catch (resolveErr) {
    clearInterval(timerInterval);
    _smgmtBoardLog(`✗ AI resolve failed: ${resolveErr.message}`, 'err');
    _smgmtBoardFinish({
      ok: false,
      message: `AI resolution failed: ${resolveErr.message}\n\nResolve manually and re-run Bulk complete.`,
      onDone,
    });
  }
}

/** Resume the complete-step loop from fromIdx after AI conflict resolution. */
async function _bcResumeFrom(owner, repoName, label, order, fromIdx, doneSteps, totalSteps, onDone) {
  try {
    for (let i = fromIdx; i < order.length; i++) {
      const sLabel = order[i];
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

    _smgmtBoardFinish({
      ok: true,
      message: `✓ ${sprintLabelDisplay(label)} completed — ${order.length} sprint(s) settled.`,
      onDone,
    });
  } catch (e2) {
    _smgmtBoardLog(`✗ ${e2.message}`, 'err');
    _smgmtBoardFinish({
      ok: false,
      message: 'Stopped: ' + e2.message + '\n\nResolve the conflict, then re-run Bulk complete.',
      onDone,
    });
  }
}
