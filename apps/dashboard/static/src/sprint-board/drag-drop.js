/* Sprint-board drag & drop (issue #797) — extracted from project.html.
 *
 * Owns: ticket drag start/end + floating pill, drop onto a sprint column,
 * within-sprint reorder (#247), backlog drag/drop, the ghost pane that creates
 * a new sprint when a ticket is dropped below all sprints, multi-select drag
 * (#660) + the selection bar, and the board move-lock overlay (#276).
 *
 * The handlers run verbatim against the page; shared board caches and remaining
 * inline helpers resolve through the page's global scope. `isDragBlocked` /
 * `computeDropPlan` are the DOM-free decision rules the drag/drop smoke test
 * exercises; `isDragBlocked` is wired into the live drop guards.
 */

/* eslint-disable no-unused-vars */
/* global _activeTab, _blUpdateActions, _arInterval, _smgmtArStartTicker, _smgmtArStopTicker, _smgmtBySprint, _smgmtData, _smgmtFinishedLabels, _smgmtMoveToModalOpen, _smgmtOrderedLabels, _smgmtRender, _smgmtRepo, _smgmtRunningLabels, _smgmtSelectedIssues, _smgmtShowInlineError, _smgmtShowToast, _smgmtSubView, _smgmtUpdateToolbarTop, loadSprintMgmt, sprintLabelDisplay,
   _smgmtDragTicket:writable, _smgmtGhostNextNum:writable, _smgmtLastSelectedNum:writable, _smgmtMoveLock:writable */
/* eslint-enable no-unused-vars */

import {
  BOARD_OVERLAY_PA_ID,
  mountProgressActivity,
  patchProgressActivity,
  appendProgressActivityLog,
  unmountProgressActivity,
} from "../progress-host.js";

let _smgmtBoardOverlayHasProgress = false;

export function isDragBlocked(state) {
  // A drop is blocked while a move is already in flight (issue #276) — mirrors
  // the `if (_smgmtMoveLock) return;` guard wired into the drop handlers below.
  // Tickets in a running sprint are additionally rendered draggable=false.
  return !!(state && state.moveLock);
}

export function computeDropPlan(dragInfo, targetLabel) {
  // Canonical move-set rule shared by _smgmtDropOnSprint / _smgmtDropOnBacklog:
  //  - a multi-selection drag (issue #660) moves every selected ticket;
  //  - a single drag moves just the dragged ticket;
  //  - dropping a single ticket on its own column is a no-op.
  if (!dragInfo) return { mode: 'none', tickets: [], targetLabel, noop: true };
  if (dragInfo.multi && dragInfo.multi.length > 1) {
    return { mode: 'multi', tickets: dragInfo.multi.slice(), targetLabel, noop: false };
  }
  const noop = dragInfo.fromSprint === targetLabel;
  return { mode: 'single', tickets: noop ? [] : [dragInfo.number], targetLabel, noop };
}


export function _smgmtUpdateSelectionUI() {
  const count = _smgmtSelectedIssues.size;
  _blUpdateActions();

  // Remove legacy inline bar if present from an older build.
  document.getElementById('smgmt-selection-bar')?.remove();

  const bar = document.getElementById('proj-selection-bar');
  const listEl = document.getElementById('smgmt-sprint-list');
  const onSprintTab = typeof _activeTab === 'undefined' || _activeTab === 'sprint-mgmt';
  const onBoard = typeof _smgmtSubView === 'undefined' || _smgmtSubView === 'board';

  if (count > 0 && bar && onSprintTab && onBoard) {
    bar.classList.remove('hidden');
    if (listEl) listEl.classList.add('has-selection');
    const countEl = document.getElementById('smgmt-sel-count');
    if (countEl) countEl.textContent = count === 1 ? '1 issue selected' : `${count} issues selected`;
    const closeBtn = document.getElementById('smgmt-sel-close-btn');
    if (closeBtn) {
      const label = count === 1 ? 'Close ticket' : `Close ${count} tickets`;
      closeBtn.innerHTML = `<i class="ti ti-circle-x"></i> ${label}`;
    }
  } else {
    if (bar) {
      bar.classList.add('hidden');
    }
    if (listEl) listEl.classList.remove('has-selection');
  }
  if (typeof _smgmtUpdateToolbarTop === 'function') {
    _smgmtUpdateToolbarTop();
    requestAnimationFrame(_smgmtUpdateToolbarTop);
  }
}

export function _smgmtPopulateSelectionDropdown() {
  // legacy no-op — replaced by _smgmtPopulateMoveToMenu
}

function _smgmtMoveTargetLabels() {
  const partOf = lbl => {
    const m = /^sprint-(\d+)(?:\.(\d+))?$/.exec(lbl || '');
    return m ? [parseInt(m[1], 10), m[2] ? parseInt(m[2], 10) : 0] : [0, 0];
  };
  const finished = _smgmtFinishedLabels || new Set();
  const labels = new Set(Object.keys(_smgmtBySprint || {}));
  // Empty planned sprints (0 tickets) are on the board but absent from bySprint keys.
  const ordered = _smgmtOrderedLabels
    || (_smgmtData?.order || []).filter(l => /^sprint-\d+(\.\d+)*$/.test(l));
  ordered.forEach(lbl => labels.add(lbl));
  return [...labels]
    .filter(lbl => !finished.has(lbl))
    .sort((a, b) => {
      const pa = partOf(a), pb = partOf(b);
      return pa[0] - pb[0] || pa[1] - pb[1];
    });
}

/** @deprecated Selection bar opens the shared move modal — kept for compat. */
export function _smgmtPopulateMoveToMenu() {}

export function _smgmtToggleMoveToMenu(event) {
  event?.stopPropagation();
  if (typeof _smgmtMoveToModalOpen === 'function') _smgmtMoveToModalOpen();
}

export function _smgmtCloseMoveToMenu() {}

export function _smgmtClearSelection() {
  _smgmtSelectedIssues.forEach(num => {
    const el = document.getElementById(`smgmt-ticket-${num}`);
    if (el) {
      el.classList.remove('is-selected');
      const cb = el.querySelector('.smgmt-ticket-cb');
      if (cb) cb.checked = false;
    }
  });
  _smgmtSelectedIssues.clear();
  _smgmtUpdateSelectionUI();
}

export function _smgmtSetSelected(number, selected) {
  if (selected) _smgmtSelectedIssues.add(number);
  else _smgmtSelectedIssues.delete(number);
  const el = document.getElementById(`smgmt-ticket-${number}`);
  if (el) {
    el.classList.toggle('is-selected', selected);
    const cb = el.querySelector('.smgmt-ticket-cb');
    if (cb) cb.checked = selected;
  }
}

// Multi-select is scoped to ONE sprint (or the backlog) at a time so the batch
// Move-to / hotswap target is unambiguous. Selecting a ticket in a different
// sprint than the current selection clears the old selection first.
function _smgmtTicketSprintKey(number) {
  const iss = (_smgmtData?.issues || []).find(i => i.number === number);
  if (!iss) return undefined;
  return iss.sprint == null ? 'backlog' : iss.sprint;
}

function _smgmtSelectionSprintKey() {
  const first = [..._smgmtSelectedIssues][0];
  return first == null ? undefined : _smgmtTicketSprintKey(first);
}

function _smgmtEnforceSelectionScope(number) {
  if (_smgmtSelectedIssues.size === 0) return;
  const cur = _smgmtSelectionSprintKey();
  const next = _smgmtTicketSprintKey(number);
  if (cur !== undefined && next !== undefined && cur !== next) {
    _smgmtClearSelection();
  }
}

export function _smgmtToggleSelect(number, checked) {
  if (checked) _smgmtEnforceSelectionScope(number);
  _smgmtSetSelected(number, checked);
  _smgmtLastSelectedNum = checked ? number : null;
  _smgmtUpdateSelectionUI();
}

export function _smgmtRowClick(event, number, label) {
  const container = label
    ? document.getElementById(`smgmt-tickets-${label}`)
    : document.getElementById('smgmt-backlog-tickets');

  if (event.shiftKey && _smgmtLastSelectedNum != null && container) {
    const nums = Array.from(container.querySelectorAll('.smgmt-ticket[data-issue]'))
      .map(r => parseInt(r.dataset.issue, 10));
    const a = nums.indexOf(_smgmtLastSelectedNum);
    const b = nums.indexOf(number);
    if (a !== -1 && b !== -1) {
      const [lo, hi] = a <= b ? [a, b] : [b, a];
      for (let i = lo; i <= hi; i++) _smgmtSetSelected(nums[i], true);
      _smgmtLastSelectedNum = number;
      _smgmtUpdateSelectionUI();
      const sel = window.getSelection && window.getSelection();
      if (sel) sel.removeAllRanges();  // clear the text highlight shift-click makes
      return;
    }
  }

  if (event.ctrlKey || event.metaKey) {
    // Ctrl/Cmd+click: toggle this ticket without clearing same-sprint siblings.
    const nowSelected = !_smgmtSelectedIssues.has(number);
    if (nowSelected) _smgmtEnforceSelectionScope(number);
    _smgmtSetSelected(number, nowSelected);
    _smgmtLastSelectedNum = nowSelected ? number : null;
    _smgmtUpdateSelectionUI();
    return;
  }

  // Plain click: toggle this ticket on/off.
  const nowSelected = !_smgmtSelectedIssues.has(number);
  if (nowSelected) _smgmtEnforceSelectionScope(number);
  _smgmtSetSelected(number, nowSelected);
  _smgmtLastSelectedNum = nowSelected ? number : null;
  _smgmtUpdateSelectionUI();
}

export function _smgmtIsDeletableIssue(num) {
  if (!_smgmtData) return false;
  const iss = _smgmtData.issues.find(i => i.number === num);
  if (!iss) return false;
  return iss.status === 'done' || iss.sprint === null;
}

export async function _smgmtDeleteSelected() {
  if (_smgmtSelectedIssues.size !== 1) return;
  const num = [..._smgmtSelectedIssues][0];
  const repo = _smgmtRepo();
  if (!repo) return;
  const iss = _smgmtData?.issues.find(i => i.number === num);
  const label = iss ? `#${num}: ${iss.title}` : `#${num}`;
  if (!confirm(`Delete ${label}?\n\nThis will close the issue on GitHub. This cannot be undone.`)) return;
  // Optimistic UI: remove from local data and re-render
  if (_smgmtData) _smgmtData.issues = _smgmtData.issues.filter(i => i.number !== num);
  _smgmtClearSelection();
  _smgmtRender(_smgmtData);
  _smgmtBoardLock(`Deleting #${num}…`);
  try {
    const res = await fetch(`/api/issues/${num}/close?repo=${encodeURIComponent(repo)}`, {
      method: 'POST',
    });
    if (!res.ok) throw new Error(await res.text());
    _smgmtShowToast(`Issue #${num} closed.`);
  } catch (e) {
    alert('Failed to delete issue: ' + e.message);
    await loadSprintMgmt();
  } finally {
    _smgmtBoardUnlock();
  }
}

export async function _smgmtMoveSelectedTo(targetLabel) {
  if (!targetLabel || _smgmtSelectedIssues.size === 0) return;
  const repo = _smgmtRepo();
  if (!repo) return;

  const nums = Array.from(_smgmtSelectedIssues);
  const changes = nums.map(n => ({ issue_num: n, sprint_label: targetLabel }));
  const dest = targetLabel === 'backlog' ? 'Backlog' : `Sprint ${targetLabel.split('-')[1]}`;

  // Optimistic UI update
  if (_smgmtData) {
    const targetNum = targetLabel === 'backlog' ? null : parseInt(targetLabel.split('-')[1], 10);
    nums.forEach(n => {
      const iss = _smgmtData.issues.find(i => i.number === n);
      if (iss) iss.sprint = targetNum;
    });
    _smgmtClearSelection();
    _smgmtRender(_smgmtData);
  }

  _smgmtBoardLock(`Moving ${nums.length} ticket${nums.length !== 1 ? 's' : ''} to ${dest}…`);
  try {
    const res = await fetch('/api/sprints/batch-labels', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ changes, project: repo }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    if (data.failed > 0 && data.errors && data.errors.length > 0) {
      _smgmtShowInlineError(`${data.failed} ticket${data.failed !== 1 ? 's' : ''} failed to move:\n${data.errors.join('\n')}`);
    } else if (data.applied > 0) {
      _smgmtShowToast(`Moved ${data.applied} ticket${data.applied !== 1 ? 's' : ''} to ${dest}.`);
    }
    await loadSprintMgmt();
  } catch (e) {
    _smgmtShowToast('Failed to move tickets: ' + e.message);
    await loadSprintMgmt();
  } finally {
    _smgmtBoardUnlock();
  }
}

export function _smgmtTicketDragStart(event, issueNum, fromSprint) {
  // Suppress drag while an inline rename is active on the source sprint
  if (fromSprint) {
    const card = document.getElementById(`smgmt-card-${fromSprint}`);
    if (card && card.querySelector('.smgmt-rename-wrap')) {
      event.preventDefault();
      return;
    }
  }
  const isChecked = _smgmtSelectedIssues.has(issueNum);

  if (isChecked && _smgmtSelectedIssues.size > 1) {
    // Multi-ticket drag: pack all selected issue numbers
    const nums = Array.from(_smgmtSelectedIssues);
    const sprints = new Set(nums.map(n => {
      const iss = (_smgmtData?.issues || []).find(i => i.number === n);
      return iss ? iss.sprint : null;
    }));
    _smgmtDragTicket = {
      number: issueNum,
      fromSprint: fromSprint || null,
      multi: nums,
      multiSprints: sprints.size,
    };
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', nums.join(','));

    // Show floating pill
    const pill = document.getElementById('smgmt-drag-pill');
    if (pill) {
      const label = sprints.size > 1
        ? `Moving ${nums.length} tickets from ${sprints.size} sprints`
        : `Moving ${nums.length} tickets`;
      pill.textContent = label;
      pill.style.top = (event.clientY - 20) + 'px';
      pill.style.left = (event.clientX + 12) + 'px';
    }
    setTimeout(() => {
      nums.forEach(n => {
        const el = document.getElementById(`smgmt-ticket-${n}`);
        if (el) el.classList.add('dragging-ticket');
      });
    }, 0);
  } else {
    // Single-ticket drag (unchecked row or single selection)
    _smgmtDragTicket = { number: issueNum, fromSprint: fromSprint || null, multi: null };
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', String(issueNum));
    const el = document.getElementById(`smgmt-ticket-${issueNum}`);
    if (el) setTimeout(() => el.classList.add('dragging-ticket'), 0);
  }
  // Show ghost pane (running-lock means draggable=false tickets can't trigger this)
  _smgmtGhostShow();
}

export function _smgmtDragMovePill(event) {
  if (_smgmtDragTicket?.multi) {
    const pill = document.getElementById('smgmt-drag-pill');
    if (pill && pill.textContent) {
      pill.style.top = (event.clientY - 20) + 'px';
      pill.style.left = (event.clientX + 12) + 'px';
    }
  }
}

export function _smgmtGhostComputeNextFree() {
  if (_smgmtData && Number.isInteger(_smgmtData.placeholder_sprint)) {
    return _smgmtData.placeholder_sprint;
  }
  const nums = (_smgmtData?.sprints || []).map(Number).filter(n => !isNaN(n));
  return nums.length ? Math.max(...nums) + 1 : 1;
}

export function _smgmtGhostShow() {
  if (_smgmtRunningLabels.size > 0) {
    showToast('Cannot create new sprint while one is running.', 'warning');
    return;
  }
  _smgmtGhostNextNum = _smgmtGhostComputeNextFree();
  const ghost = document.getElementById('smgmt-ghost-pane');
  const titleEl = document.getElementById('smgmt-ghost-title');
  const subEl = document.getElementById('smgmt-ghost-sub');
  if (!ghost) return;

  titleEl.textContent = `Drop here to create Sprint ${_smgmtGhostNextNum}`;
  subEl.textContent = 'next sprint number';

  ghost.classList.add('ghost-visible');
}

export function _smgmtGhostHide() {
  const ghost = document.getElementById('smgmt-ghost-pane');
  if (!ghost) return;
  ghost.classList.remove('ghost-visible', 'ghost-hot');
  _smgmtGhostNextNum = null;
}

export function _smgmtGhostDragOver(event) {
  if (!_smgmtDragTicket) return;
  if (_smgmtRunningLabels.size > 0) return;
  event.preventDefault();
  event.dataTransfer.dropEffect = 'move';
  const ghost = document.getElementById('smgmt-ghost-pane');
  if (!ghost) return;
  const titleEl = document.getElementById('smgmt-ghost-title');
  const subEl = document.getElementById('smgmt-ghost-sub');
  ghost.classList.add('ghost-hot');
  if (titleEl) titleEl.textContent = `Release to create Sprint ${_smgmtGhostNextNum}`;
  if (subEl) subEl.textContent = "you'll be asked to confirm";
}

export function _smgmtGhostDragLeave(event) {
  const ghost = document.getElementById('smgmt-ghost-pane');
  if (!ghost) return;
  if (!ghost.contains(event.relatedTarget)) {
    ghost.classList.remove('ghost-hot');
    const titleEl = document.getElementById('smgmt-ghost-title');
    const subEl = document.getElementById('smgmt-ghost-sub');
    if (titleEl) titleEl.textContent = `Drop here to create Sprint ${_smgmtGhostNextNum}`;
    // Restore sub-text
    const existing = new Set((_smgmtData?.sprints || []).map(n => Number(n)));
    const skipped = [];
    for (let i = 1; i < _smgmtGhostNextNum; i++) {
      if (!existing.has(i)) skipped.push(i);
    }
    if (subEl) subEl.textContent = skipped.length > 0
      ? `next free number · skipped empty ${skipped.map(s => `Sprint ${s}`).join(', ')}`
      : 'next free number';
  }
}

export async function _smgmtGhostDrop(event) {
  event.preventDefault();
  if (!_smgmtDragTicket) return;
  if (_smgmtRunningLabels.size > 0) return;
  const dragInfo = _smgmtDragTicket;
  const nextNum = _smgmtGhostNextNum;
  _smgmtGhostHide();

  // Multi-ticket drag to ghost: not supported per spec — treat as no-op
  if (dragInfo.multi && dragInfo.multi.length > 1) {
    // Leave _smgmtDragTicket for dragend cleanup
    return;
  }

  // Clear dragging-ticket class manually (dragend may fire after we null this)
  const dragEl = document.getElementById(`smgmt-ticket-${dragInfo.number}`);
  if (dragEl) dragEl.classList.remove('dragging-ticket');
  _smgmtDragTicket = null;

  if (nextNum == null) return;
  const repo = _smgmtRepo();
  if (!repo) return;

  // Populate and open confirm modal
  const sprintLabel = `sprint-${nextNum}`;
  const issue = (_smgmtData?.issues || []).find(i => i.number === dragInfo.number);
  const fromLabel = dragInfo.fromSprint || 'backlog';

  document.getElementById('gc-sprint-name').textContent = sprintLabel;
  document.getElementById('gc-ticket-info').textContent =
    issue ? `#${issue.number} — ${issue.title}` : `#${dragInfo.number}`;
  document.getElementById('gc-source-pane').textContent =
    fromLabel === 'backlog' ? 'Backlog' : `Sprint ${fromLabel.replace('sprint-', '')}`;

  const confirmBtn = document.getElementById('gc-confirm-btn');
  confirmBtn.textContent = `Create ${sprintLabel} & move`;
  confirmBtn.disabled = false;

  const errEl = document.getElementById('gc-error');
  errEl.textContent = '';
  errEl.classList.add('hidden');

  // Store drag state for confirm handler
  document.getElementById('gc-modal').dataset.issueNum = String(dragInfo.number);
  document.getElementById('gc-modal').dataset.fromSprint = fromLabel;
  document.getElementById('gc-modal').dataset.sprintNum = String(nextNum);
  document.getElementById('gc-modal').dataset.repo = repo;

  document.getElementById('gc-backdrop').classList.remove('hidden');
  document.getElementById('gc-modal').classList.remove('hidden');
  confirmBtn.focus();
}

export function _gcClose() {
  document.getElementById('gc-backdrop').classList.add('hidden');
  document.getElementById('gc-modal').classList.add('hidden');
}

export async function _gcConfirm() {
  const modal = document.getElementById('gc-modal');
  const issueNum = parseInt(modal.dataset.issueNum, 10);
  const sprintNum = parseInt(modal.dataset.sprintNum, 10);
  const repo = modal.dataset.repo;
  const sprintLabel = `sprint-${sprintNum}`;

  const confirmBtn = document.getElementById('gc-confirm-btn');
  const errEl = document.getElementById('gc-error');
  confirmBtn.disabled = true;
  confirmBtn.textContent = 'Creating…';
  errEl.classList.add('hidden');

  try {
    // Step 1: create the sprint label (409 = already exists, safe to continue)
    const createRes = await fetch('/api/sprints/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project: repo, sprint_number: sprintNum }),
    });
    if (!createRes.ok && createRes.status !== 409) {
      const d = await createRes.json().catch(() => ({}));
      throw new Error(d.detail || 'HTTP ' + createRes.status);
    }

    // Step 2: move the ticket
    const moveRes = await fetch(`/api/issues/${issueNum}/sprint-label`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sprint_label: sprintLabel, project: repo }),
    });
    if (!moveRes.ok) {
      const d = await moveRes.json().catch(() => ({}));
      throw new Error(d.detail || 'HTTP ' + moveRes.status);
    }

    // Success — close modal and reload board
    _gcClose();
    await loadSprintMgmt();
  } catch (e) {
    errEl.textContent = `Failed: ${e.message}`;
    errEl.classList.remove('hidden');
    confirmBtn.disabled = false;
    confirmBtn.textContent = `Create ${sprintLabel} & move`;
  }
}

export function _smgmtTicketDragEnd(_event) {
  if (_smgmtDragTicket) {
    if (_smgmtDragTicket.multi) {
      _smgmtDragTicket.multi.forEach(n => {
        const el = document.getElementById(`smgmt-ticket-${n}`);
        if (el) el.classList.remove('dragging-ticket');
      });
    } else {
      const el = document.getElementById(`smgmt-ticket-${_smgmtDragTicket.number}`);
      if (el) el.classList.remove('dragging-ticket');
    }
  }
  // Hide drag pill and ghost pane
  const pill = document.getElementById('smgmt-drag-pill');
  if (pill) { pill.style.top = '-100px'; pill.style.left = '-100px'; pill.textContent = ''; }
  _smgmtGhostHide();
  _smgmtDragTicket = null;
  document.querySelectorAll('.smgmt-sprint-card').forEach(el => el.classList.remove('drag-over-sprint'));
  document.getElementById('smgmt-backlog-pane')?.classList.remove('drag-over-backlog');
}

export function _smgmtDragOver(event, sprintLabel) {
  if (_smgmtDragTicket) {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    document.querySelectorAll('.smgmt-sprint-card').forEach(b => b.classList.remove('drag-over-sprint'));
    document.getElementById('smgmt-backlog-pane')?.classList.remove('drag-over-backlog');
    const target = document.getElementById(`smgmt-card-${sprintLabel}`);
    if (target) target.classList.add('drag-over-sprint');
  }
}

export function _smgmtDragLeave(event) {
  if (event.currentTarget && !event.currentTarget.contains(event.relatedTarget)) {
    event.currentTarget.classList.remove('drag-over-sprint');
  }
}

export async function _smgmtDropOnSprint(event, targetLabel) {
  event.preventDefault();
  document.querySelectorAll('.smgmt-sprint-card').forEach(el => el.classList.remove('drag-over-sprint'));
  document.getElementById('smgmt-backlog-pane')?.classList.remove('drag-over-backlog');

  // Block concurrent moves (issue #276)
  if (isDragBlocked({ moveLock: _smgmtMoveLock })) return;
  if (!_smgmtDragTicket) return;
  const dragInfo = _smgmtDragTicket;
  _smgmtDragTicket = null;

  const repo = _smgmtRepo();
  if (!repo) return;

  if (dragInfo.multi && dragInfo.multi.length > 1) {
    // Multi-ticket drop
    const nums = dragInfo.multi;
    const targetNum = targetLabel ? parseInt(targetLabel.split('-')[1], 10) : null;

    // Optimistic update
    if (_smgmtData) {
      nums.forEach(n => {
        const iss = _smgmtData.issues.find(i => i.number === n);
        if (iss) iss.sprint = targetNum;
      });
    }
    // Clear selection before render
    _smgmtClearSelection();
    if (_smgmtData) _smgmtRender(_smgmtData);

    const changes = nums.map(n => ({ issue_num: n, sprint_label: targetLabel || 'backlog' }));
    _smgmtBoardLock();
    try {
      const res = await fetch('/api/sprints/batch-labels', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ changes, project: repo }),
      });
      if (!res.ok) throw new Error(await res.text());
      await loadSprintMgmt();
    } catch (e) {
      alert(`Failed to move tickets: ${e.message}`);
      await loadSprintMgmt();
    } finally {
      _smgmtBoardUnlock();
    }
  } else {
    // Single-ticket drop
    const { number, fromSprint } = dragInfo;
    if (fromSprint === targetLabel) return;

    const targetNum = targetLabel ? parseInt(targetLabel.split('-')[1], 10) : null;

    // Optimistic update
    if (_smgmtData) {
      const iss = _smgmtData.issues.find(i => i.number === number);
      if (iss) iss.sprint = targetNum;
      _smgmtRender(_smgmtData);
    }
    // Clear selection after any drag completes
    _smgmtClearSelection();

    _smgmtBoardLock();
    try {
      const res = await fetch(`/api/issues/${number}/sprint-label`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sprint_label: targetLabel || 'backlog', project: repo }),
      });
      if (!res.ok) throw new Error(await res.text());
      await loadSprintMgmt();
    } catch (e) {
      // Rollback optimistic update
      if (_smgmtData) {
        const iss = _smgmtData.issues.find(i => i.number === number);
        if (iss) iss.sprint = fromSprint ? parseInt(fromSprint.split('-')[1], 10) : null;
        _smgmtRender(_smgmtData);
      }
      alert(`Failed to move ticket #${number}: ${e.message}`);
    } finally {
      _smgmtBoardUnlock();
    }
  }
}

export function _smgmtTicketReorderDragOver(event) {
  if (!_smgmtDragTicket || (_smgmtDragTicket.multi && _smgmtDragTicket.multi.length > 1)) return;
  const target = event.currentTarget;
  const targetSprint = target.dataset.sprint;
  const dragSprint = _smgmtDragTicket ? _smgmtDragTicket.fromSprint : null;
  if (targetSprint !== dragSprint) return; // cross-sprint moves handled by _smgmtDropOnSprint
  event.preventDefault();
  event.stopPropagation();
  const rect = target.getBoundingClientRect();
  const midY = rect.top + rect.height / 2;
  target.classList.remove('drag-before', 'drag-after');
  target.classList.add(event.clientY < midY ? 'drag-before' : 'drag-after');
}

export function _smgmtTicketReorderDragLeave(event) {
  event.currentTarget.classList.remove('drag-before', 'drag-after');
}

export async function _smgmtTicketReorderDrop(event, targetIssue, sprintLabel) {
  if (!_smgmtDragTicket || (_smgmtDragTicket.multi && _smgmtDragTicket.multi.length > 1)) return;
  const dragInfo = _smgmtDragTicket;
  if (dragInfo.fromSprint !== sprintLabel) return; // cross-sprint handled elsewhere
  const dragIssue = dragInfo.number;
  if (dragIssue === targetIssue) {
    event.currentTarget.classList.remove('drag-before', 'drag-after');
    return;
  }
  event.preventDefault();
  event.stopPropagation();

  const rect = event.currentTarget.getBoundingClientRect();
  const insertAfter = event.clientY >= rect.top + rect.height / 2;
  event.currentTarget.classList.remove('drag-before', 'drag-after');

  const repo = _smgmtRepo();
  if (!repo || !_smgmtData) return;

  // Build new order from current DOM positions.
  const container = document.getElementById(`smgmt-tickets-${sprintLabel}`);
  if (!container) return;
  const rows = Array.from(container.querySelectorAll('.smgmt-ticket[data-issue]'));
  let order = rows.map(r => parseInt(r.dataset.issue, 10)).filter(n => !isNaN(n));
  order = order.filter(n => n !== dragIssue);
  const insertIdx = order.indexOf(targetIssue) + (insertAfter ? 1 : 0);
  order.splice(insertIdx, 0, dragIssue);

  _smgmtDragTicket = null;
  try {
    const res = await fetch(
      `/api/sprints/${encodeURIComponent(sprintLabel)}/plan?project=${encodeURIComponent(repo)}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(order),
      }
    );
    if (!res.ok) throw new Error(await res.text());
    await loadSprintMgmt();
  } catch (e) {
    alert(`Failed to reorder tickets: ${e.message}`);
    await loadSprintMgmt();
  }
}

export function _smgmtBacklogTicketDragStart(event, issueNum) {
  const isChecked = _smgmtSelectedIssues.has(issueNum);

  if (isChecked && _smgmtSelectedIssues.size > 1) {
    // Multi-ticket drag from backlog: pack all selected issue numbers
    const nums = Array.from(_smgmtSelectedIssues);
    _smgmtDragTicket = { number: issueNum, fromSprint: null, multi: nums, multiSprints: 1 };
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', nums.join(','));

    const pill = document.getElementById('smgmt-drag-pill');
    if (pill) {
      pill.textContent = `Moving ${nums.length} tickets`;
      pill.style.top = (event.clientY - 20) + 'px';
      pill.style.left = (event.clientX + 12) + 'px';
    }
    setTimeout(() => {
      nums.forEach(n => {
        const el = document.getElementById(`smgmt-ticket-${n}`);
        if (el) el.classList.add('dragging-ticket');
      });
    }, 0);
  } else {
    // Single-ticket drag (unchecked row or single selection)
    _smgmtDragTicket = { number: issueNum, fromSprint: null, multi: null };
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', String(issueNum));
    const el = document.getElementById(`smgmt-ticket-${issueNum}`);
    if (el) setTimeout(() => el.classList.add('dragging-ticket'), 0);
  }
  // Show ghost pane
  _smgmtGhostShow();
}

export function _smgmtBacklogDragOver(event) {
  if (_smgmtDragTicket && _smgmtDragTicket.fromSprint !== null) {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    document.getElementById('smgmt-backlog-pane')?.classList.add('drag-over-backlog');
  }
}

export function _smgmtBacklogDragLeave(event) {
  const pane = document.getElementById('smgmt-backlog-pane');
  if (pane && !pane.contains(event.relatedTarget)) {
    pane.classList.remove('drag-over-backlog');
  }
}

export async function _smgmtDropOnBacklog(event) {
  event.preventDefault();
  document.getElementById('smgmt-backlog-pane')?.classList.remove('drag-over-backlog');

  // Block concurrent moves (issue #276)
  if (isDragBlocked({ moveLock: _smgmtMoveLock })) return;
  if (!_smgmtDragTicket) return;
  const dragInfo = _smgmtDragTicket;
  _smgmtDragTicket = null;

  // If dragging from backlog back to backlog, no-op
  if (!dragInfo.fromSprint) return;

  const repo = _smgmtRepo();
  if (!repo) return;

  if (dragInfo.multi && dragInfo.multi.length > 1) {
    const nums = dragInfo.multi;
    if (_smgmtData) {
      nums.forEach(n => {
        const iss = _smgmtData.issues.find(i => i.number === n);
        if (iss) iss.sprint = null;
      });
    }
    _smgmtClearSelection();
    if (_smgmtData) _smgmtRender(_smgmtData);

    const changes = nums.map(n => ({ issue_num: n, sprint_label: 'backlog' }));
    _smgmtBoardLock();
    try {
      const res = await fetch('/api/sprints/batch-labels', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ changes, project: repo }),
      });
      if (!res.ok) throw new Error(await res.text());
      await loadSprintMgmt();
    } catch (e) {
      alert(`Failed to move tickets to backlog: ${e.message}`);
      await loadSprintMgmt();
    } finally {
      _smgmtBoardUnlock();
    }
  } else {
    const { number, fromSprint } = dragInfo;

    // Optimistic update
    if (_smgmtData) {
      const iss = _smgmtData.issues.find(i => i.number === number);
      if (iss) iss.sprint = null;
      _smgmtRender(_smgmtData);
    }
    _smgmtClearSelection();

    _smgmtBoardLock();
    try {
      const res = await fetch(`/api/issues/${number}/sprint-label`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sprint_label: 'backlog', project: repo }),
      });
      if (!res.ok) throw new Error(await res.text());
      await loadSprintMgmt();
    } catch (e) {
      // Rollback
      if (_smgmtData) {
        const iss = _smgmtData.issues.find(i => i.number === number);
        if (iss) iss.sprint = fromSprint ? parseInt(fromSprint.split('-')[1], 10) : null;
        _smgmtRender(_smgmtData);
      }
      alert(`Failed to move ticket #${number} to backlog: ${e.message}`);
    } finally {
      _smgmtBoardUnlock();
    }
  }
}

export function _smgmtBoardLock(message, opts) {
  _smgmtMoveLock = true;
  // Pause the auto-refresh ticker without changing the user's chosen interval
  _smgmtArStopTicker();
  const overlay = document.getElementById('smgmt-move-overlay');
  const msgEl   = document.getElementById('smgmt-move-overlay-msg');
  const paHost  = document.getElementById('smgmt-op-pa-host');
  const progWrap = document.getElementById('smgmt-op-progress-wrap');
  const logEl = document.getElementById('smgmt-op-log');
  const text    = message || 'Moving…';
  if (msgEl) msgEl.textContent = text;
  if (overlay) {
    overlay.setAttribute('aria-label', text.replace(/…$/, '') + ', please wait');
    overlay.classList.add('active');
  }
  const showProgress = !!(opts && opts.progress);
  _smgmtBoardOverlayHasProgress = showProgress;
  if (progWrap) progWrap.hidden = true;
  if (logEl) {
    logEl.hidden = true;
    if (opts && opts.clearLog) logEl.innerHTML = '';
  }
  if (paHost) {
    paHost.hidden = !showProgress;
    if (showProgress) {
      mountProgressActivity(paHost, {
        status: 'running',
        mode: 'bar',
        done: 0,
        total: (opts && opts.total) != null ? opts.total : 1,
        current: text,
        log_tail: [],
      }, {
        id: BOARD_OVERLAY_PA_ID,
      });
    } else {
      unmountProgressActivity(paHost);
    }
  }
  if (showProgress && opts.total != null) {
    _smgmtBoardProgress(0, opts.total);
  } else if (!showProgress) {
    _smgmtBoardProgress(0, 1);
  }
  // Render a disabled Done button up front so the operator sees the overlay will
  // wait for an explicit acknowledge — _smgmtBoardFinish() enables it on
  // success/failure. The overlay never auto-dismisses when showDone is set.
  if (opts && opts.showDone) {
    const doneEl = document.getElementById('smgmt-op-done');
    if (doneEl) {
      doneEl.hidden = false;
      doneEl.style.cssText = 'margin-top:12px;text-align:center';
      doneEl.innerHTML = '<button type="button" class="btn-primary" id="smgmt-op-done-btn" disabled>Done</button>';
    }
  }
}

export function _smgmtBoardProgress(done, total) {
  if (_smgmtBoardOverlayHasProgress) {
    const d = Number(done || 0);
    const t = Number(total || 0);
    patchProgressActivity('smgmt-op-pa-host', {
      done: d,
      total: t,
      mode: 'bar',
      status: 'running',
      current: t > 0 ? `${d} of ${t}` : '',
    }, { id: BOARD_OVERLAY_PA_ID });
    return;
  }
  const fill = document.getElementById('smgmt-op-progress-fill');
  const pctEl = document.getElementById('smgmt-op-progress-pct');
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  if (fill) fill.style.width = pct + '%';
  if (pctEl) pctEl.textContent = pct + '%';
}

export function _smgmtBoardLog(line, kind) {
  if (_smgmtBoardOverlayHasProgress) {
    const mappedType =
      kind === 'ok' ? 'success' :
      kind === 'err' ? 'fail' :
      kind === 'step' ? 'dispatch' : 'dispatch';
    appendProgressActivityLog('smgmt-op-pa-host', line, mappedType, { id: BOARD_OVERLAY_PA_ID });
    return;
  }
  const logEl = document.getElementById('smgmt-op-log');
  if (!logEl) return;
  const row = document.createElement('div');
  row.className = 'smgmt-op-log-line' + (kind ? ` smgmt-op-log-line--${kind}` : '');
  row.textContent = line;
  logEl.appendChild(row);
  logEl.scrollTop = logEl.scrollHeight;
}

export function _smgmtBoardUnlock() {
  _smgmtMoveLock = false;
  _smgmtBoardOverlayHasProgress = false;
  const overlay = document.getElementById('smgmt-move-overlay');
  if (overlay) overlay.classList.remove('active');
  const paHost = document.getElementById('smgmt-op-pa-host');
  if (paHost) {
    unmountProgressActivity(paHost);
    paHost.hidden = true;
  }
  const progWrap = document.getElementById('smgmt-op-progress-wrap');
  const logEl = document.getElementById('smgmt-op-log');
  if (progWrap) progWrap.hidden = true;
  if (logEl) { logEl.hidden = true; logEl.innerHTML = ''; }
  const doneEl = document.getElementById('smgmt-op-done');
  if (doneEl) { doneEl.hidden = true; doneEl.innerHTML = ''; }
  const errEl = document.getElementById('smgmt-op-error');
  if (errEl) { errEl.hidden = true; errEl.textContent = ''; }
  const spinner = document.getElementById('smgmt-move-spinner');
  if (spinner) spinner.style.display = '';
  _smgmtBoardProgress(0, 1);
  // Resume auto-refresh if an interval is selected
  if (_arInterval > 0) _smgmtArStartTicker();
}

/**
 * Settle an operation and WAIT for the operator to acknowledge: keep the overlay
 * open, stop the spinner, show a success or error message, and enable a "Done"
 * button. The overlay never auto-dismisses — the operator reads the log, then
 * Done unlocks the board and runs the optional onDone callback (e.g. refresh).
 *
 * opts = { ok?: boolean (default true), message?: string, onDone?: fn }
 */
export function _smgmtBoardFinish(opts) {
  opts = opts || {};
  const ok = opts.ok !== false;
  const message = opts.message || (ok ? 'Done.' : 'Stopped.');
  const onDone = opts.onDone;

  _smgmtArStopTicker();
  const spinner = document.getElementById('smgmt-move-spinner');
  if (spinner) spinner.style.display = 'none';
  const overlay = document.getElementById('smgmt-move-overlay');
  if (overlay) overlay.setAttribute('aria-busy', 'false');
  if (_smgmtBoardOverlayHasProgress) {
    patchProgressActivity('smgmt-op-pa-host',
      { status: ok ? 'done' : 'failed', current: message },
      { id: BOARD_OVERLAY_PA_ID });
  }

  const msgEl = document.getElementById('smgmt-move-overlay-msg');
  const errEl = document.getElementById('smgmt-op-error');
  if (ok) {
    if (msgEl) msgEl.textContent = message;
    if (errEl) { errEl.hidden = true; errEl.textContent = ''; }
  } else {
    if (errEl) {
      errEl.textContent = message;
      errEl.hidden = false;
      errEl.style.cssText = 'color:var(--red,#e5484d);font-size:13px;margin-top:10px;text-align:left;white-space:pre-wrap;max-height:160px;overflow:auto';
    }
  }

  const doneEl = document.getElementById('smgmt-op-done');
  if (doneEl) {
    doneEl.hidden = false;
    doneEl.style.cssText = 'margin-top:12px;text-align:center';
    // Reuse a pre-rendered (disabled) Done button if _smgmtBoardLock placed one;
    // otherwise create it. Either way, enable it now.
    let btn = document.getElementById('smgmt-op-done-btn');
    if (!btn) {
      doneEl.innerHTML = '<button type="button" class="btn-primary" id="smgmt-op-done-btn">Done</button>';
      btn = document.getElementById('smgmt-op-done-btn');
    }
    if (btn) {
      btn.disabled = false;
      btn.onclick = () => {
        _smgmtBoardUnlock();
        if (typeof onDone === 'function') { try { onDone(); } catch (_) { /* ignore */ } }
      };
    }
  }
}

/**
 * Back-compat error wrapper around _smgmtBoardFinish (kept for existing callers).
 */
export function _smgmtBoardHalt(message, onDone) {
  _smgmtBoardFinish({ ok: false, message, onDone });
}
