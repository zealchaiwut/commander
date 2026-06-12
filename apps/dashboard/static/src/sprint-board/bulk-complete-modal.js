/* Bulk Complete Sprint modal — settle a parent + child sprint lineage without merge.
 *
 * Closes UAT and sprint-summary issues across the lineage, then marks every
 * member sprint completed. History cards grey out via the completed lock state.
 */

/* global _setBodyInert, _clearBodyInert, _smgmtRepo, sprintLabelDisplay,
   escHtml, _smgmtShowToast, loadSprintMgmt,
   _bcLabel:writable, _bcPreview:writable */

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
  document.getElementById('bc-loading').classList.remove('hidden');
  document.getElementById('bc-content').classList.add('hidden');
  document.getElementById('bc-error').classList.add('hidden');
  document.getElementById('bc-error').textContent = '';
  const confirmBtn = document.getElementById('bc-confirm-btn');
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Bulk complete'; }
  _bcOpen();

  try {
    const res = await fetch(
      `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/bulk-complete-preview`
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const preview = await res.json();
    _bcPreview = preview;

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
    const actionsEl = document.getElementById('bc-actions');
    actionsEl.innerHTML = [
      `<div class="fs-action-row"><i class="ti ti-circle-check"></i> Close selected tickets (UAT + summary included)</div>`,
      `<div class="fs-action-row"><i class="ti ti-flag-check"></i> Mark ${memberCount} sprint${memberCount !== 1 ? 's' : ''} completed</div>`,
    ].join('');

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

export async function _bcConfirm() {
  const repo = _smgmtRepo();
  if (!_bcLabel || !repo || !_bcPreview) return;
  const parts = repo.split('/');
  const owner = parts[0];
  const repoName = parts.slice(1).join('/');

  const checkboxes = Array.from(document.querySelectorAll('#bc-ticket-list input[type=checkbox]'));
  const selectedNums = checkboxes.filter(c => c.checked).map(c => parseInt(c.dataset.issue, 10));

  const confirmBtn = document.getElementById('bc-confirm-btn');
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Completing…'; }

  try {
    const res = await fetch(
      `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(_bcLabel)}/bulk-complete`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          confirmed: true,
          selected_ticket_numbers: selectedNums,
        }),
      }
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    _bcClose();
    if (data.errors && data.errors.length > 0) {
      _smgmtShowToast(`Bulk complete finished with errors — ${data.closed} closed.`);
    } else {
      _smgmtShowToast(
        `${sprintLabelDisplay(_bcLabel || '')} bulk completed — ${data.closed} closed, ${data.completed} marked completed.`
      );
    }
    await loadSprintMgmt();
  } catch (e) {
    const errEl = document.getElementById('bc-error');
    errEl.textContent = 'Failed to bulk complete: ' + e.message;
    errEl.classList.remove('hidden');
    if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = 'Bulk complete'; }
  }
}
