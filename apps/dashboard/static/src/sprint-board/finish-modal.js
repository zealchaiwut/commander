/* Finish Sprint modal (issue #367 parity) — extracted from project.html (#797).
 *
 * Opens the finish-sprint modal, previews which tickets close vs. carry forward
 * (and whether a sprint PR will be merged), and confirms the finish. Page
 * helpers and broadly-shared board caches resolve through the page's global
 * scope; modal-local state (`_fsLabel`, `_fsPreview`) is seeded on `window` by
 * ./state.js.
 */

/* global _setBodyInert, _clearBodyInert, _smgmtRepo, sprintLabelDisplay,
   escHtml, _smgmtShowToast, loadSprintMgmt,
   _fsLabel:writable, _fsPreview:writable */

export function _fsOpen() {
  _setBodyInert(['fs-backdrop', 'fs-modal']);
  document.getElementById('fs-backdrop').classList.remove('hidden');
  document.getElementById('fs-modal').classList.remove('hidden');
}
export function _fsClose() {
  document.getElementById('fs-backdrop').classList.add('hidden');
  document.getElementById('fs-modal').classList.add('hidden');
  _clearBodyInert();
  _fsLabel = null;
  _fsPreview = null;
}
export function _fsCatClass(cat) {
  if (cat === 'UAT')            return 'rr-cat-uat';
  if (cat === 'SIT')            return 'rr-cat-sit';
  if (cat === 'needs-rework')   return 'rr-cat-rework';
  if (cat === 'sprint-summary') return 'rr-cat-summary';
  return 'rr-cat-queued';
}
export function _fsSelectAll(checked) {
  document.querySelectorAll('#fs-ticket-list input[type=checkbox]').forEach(cb => { cb.checked = checked; });
}

export async function smgmtFinishSprint(label) {
  const repo = _smgmtRepo();
  if (!repo) return;
  _fsLabel = label;
  _fsPreview = null;
  const parts = repo.split('/');
  const owner = parts[0];
  const repoName = parts.slice(1).join('/');

  document.getElementById('fs-modal-title').textContent = `Finish ${sprintLabelDisplay(label)}?`;
  document.getElementById('fs-loading').classList.remove('hidden');
  document.getElementById('fs-content').classList.add('hidden');
  document.getElementById('fs-error').classList.add('hidden');
  document.getElementById('fs-error').textContent = '';
  const confirmBtn = document.getElementById('fs-confirm-btn');
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Finish Sprint'; }
  _fsOpen();

  try {
    const res = await fetch(
      `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/finish-preview`
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const preview = await res.json();
    _fsPreview = preview;

    if (preview.conflict_error) {
      throw new Error(preview.conflict_error);
    }

    const listEl = document.getElementById('fs-ticket-list');
    const allTickets = preview.all_tickets || [];
    if (allTickets.length === 0) {
      listEl.innerHTML = '<div style="padding:10px;color:var(--text-muted);font-size:13px">No open tickets in this sprint.</div>';
    } else {
      listEl.innerHTML = allTickets.map(t => {
        const catClass = _fsCatClass(t.category);
        const catLabel = t.category === 'sprint-summary' ? 'SUMMARY' : t.category.toUpperCase();
        return `<label class="rr-ticket-row">
          <input type="checkbox" checked data-issue="${t.number}" onchange="">
          <span class="rr-ticket-num">#${t.number}</span>
          <span class="rr-ticket-title" title="${escHtml(t.title)}">${escHtml(t.title)}</span>
          <span class="rr-ticket-cat ${catClass}">${escHtml(catLabel)}</span>
        </label>`;
      }).join('');
    }

    const actionsEl = document.getElementById('fs-actions');
    const actionRows = [];
    if (preview.sprint_pr) {
      actionRows.push(`<div class="fs-action-row"><i class="ti ti-git-merge"></i> Merge sprint PR
        <a href="${escHtml(preview.sprint_pr.url)}" target="_blank" rel="noopener">#${preview.sprint_pr.number}</a></div>`);
    }
    actionRows.push('<div class="fs-action-row"><i class="ti ti-circle-check"></i> Close all selected tickets</div>');
    actionRows.push('<div class="fs-action-row"><i class="ti ti-tag-off"></i> Remove sprint label</div>');
    actionsEl.innerHTML = actionRows.join('');

    document.getElementById('fs-loading').classList.add('hidden');
    document.getElementById('fs-content').classList.remove('hidden');
    if (confirmBtn) confirmBtn.disabled = false;
  } catch (e) {
    document.getElementById('fs-loading').classList.add('hidden');
    const errEl = document.getElementById('fs-error');
    errEl.textContent = 'Failed to load preview: ' + e.message;
    errEl.classList.remove('hidden');
  }
}

export async function _fsConfirm() {
  const repo = _smgmtRepo();
  if (!_fsLabel || !repo || !_fsPreview) return;
  const parts = repo.split('/');
  const owner = parts[0];
  const repoName = parts.slice(1).join('/');

  const checkboxes = Array.from(document.querySelectorAll('#fs-ticket-list input[type=checkbox]'));
  const selectedNums = checkboxes.filter(c => c.checked).map(c => parseInt(c.dataset.issue, 10));

  const confirmBtn = document.getElementById('fs-confirm-btn');
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Finishing…'; }

  try {
    const res = await fetch(
      `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(_fsLabel)}/finish`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          confirmed: true,
          move_non_uat_to: _fsPreview.next_sprint_label,
          selected_ticket_numbers: selectedNums,
          merge_pr: !!_fsPreview.sprint_pr,
          sprint_pr_url: _fsPreview.sprint_pr ? _fsPreview.sprint_pr.url : null,
        }),
      }
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    _fsClose();
    if (data.errors && data.errors.length > 0) {
      _smgmtShowToast(`Finished with errors — ${data.closed} closed, ${data.moved} moved.`);
    } else {
      let msg = `${sprintLabelDisplay(_fsLabel || '')} finished — ${data.closed} closed`;
      if (data.moved > 0) msg += `, ${data.moved} moved to ${data.next_sprint_label}`;
      _smgmtShowToast(msg + '.');
    }
    await loadSprintMgmt();
  } catch (e) {
    const errEl = document.getElementById('fs-error');
    errEl.textContent = 'Failed to finish sprint: ' + e.message;
    errEl.classList.remove('hidden');
    if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = 'Finish Sprint'; }
  }
}
