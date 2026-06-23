/* Run Sprint controls + preflight modal (issue #448) — extracted from
 * project.html (issue #797). Covers the Run / Cancel actions and the full
 * preflight modal: DAG render, dependency order, conflict + mis-sizing flags,
 * and the confirm-to-dispatch flow. Page helpers and broadly-shared board
 * caches resolve through the page's global scope; preflight state (`_pf*`) is
 * seeded on `window` by ./state.js.
 */

/* eslint-disable no-unused-vars */
/* global _smgmtRepo, _smgmtShowToast, escHtml, sprintLabelDisplay, loadSprintMgmt,
   _smgmtShowSubView, _smgmtRunningLabels, _smgmtAnySprintRunning:writable, _smgmtLivePollRestart,
   _smgmtLingerStart, _smgmtLingerLive, _smgmtRunningViewUpdate,
   _pfCurrentLabel, _pfCurrentRepo, _pfState,
   _pfDagData, _pfWarnings, _pfCycle,
   _pfFlags, _pfSelectedIds, _pfUseClineFollowups,
   _pfXLSuggestions, _pfStrictXLGate, _pfXLMinutesSaved,
   _smgmtBySprint */
/* eslint-enable no-unused-vars */

import {
  mountProgressActivity,
  patchProgressActivityStep,
  unmountProgressActivity,
} from "../progress-host.js";
import { _smgmtDorMode, _smgmtDorNotReadyTickets } from "./board-render.js";

// ── Pre-flight stepper component (shared ProgressActivity — stepper mode, issue #933) ─

/** Step definitions matching the pre-flight panel check groups. */
const PF_STEPS = [
  { key: 'ac',        label: 'Acceptance criteria', autoFixable: true  },
  { key: 'estimates', label: 'Estimate coverage',    autoFixable: true  },
  { key: 'cycle',     label: 'Dependency graph',     autoFixable: false },
  { key: 'missizing', label: 'Mis-sizing review',    autoFixable: false },
  { key: 'conflicts', label: 'Conflict analysis',    autoFixable: false },
];

/** Count of steps currently in fail state (blocks Run Sprint). */
let _pfStepFails = 0;

/** True while preflight-fix SSE is still running (non-blocking for Run). */
let _pfAutofixPending = false;

// ────────────────────────────────────────────────────────────────────────────

// Effective agent models for the current preflight (from /preflight `models`,
// resolved server-side from sprint.yaml — what the run will actually use).
let _pfModels = null;

/** Short model label, e.g. "claude-sonnet-4-6" → "sonnet-4-6". */
function _pfModelShort(m) {
  const s = String(m || '');
  return s.replace(/^claude-/, '') || s;
}

/** "Agents" section for the preflight modal: the effective model per role, so
 *  the operator confirms what will run before dispatch. */
function _pfBuildModelsHtml() {
  const m = _pfModels;
  if (!m) return '';
  const rows = [];
  rows.push(`<span class="pf-model-pill"><b>Coder</b> ${escHtml(_pfModelShort(m.coder))}</span>`);
  const br = m.tester_by_risk || {};
  const testerTxt = Object.keys(br).length
    ? Object.keys(br).map(k => `${k.toLowerCase()}:${_pfModelShort(br[k])}`).join(' · ')
    : 'risk-routed';
  rows.push(`<span class="pf-model-pill"><b>Tester</b> ${escHtml(testerTxt)}</span>`);
  rows.push(`<span class="pf-model-pill"><b>Estimator</b> ${escHtml(_pfModelShort(m.estimator))}</span>`);
  if (m.documentor) {
    rows.push(`<span class="pf-model-pill"><b>Documentor</b> ${escHtml(_pfModelShort(m.documentor))}</span>`);
  }
  return `<div class="pf-section">
      <div class="pf-section-label">Agent models <span class="pf-model-note">— confirm before run · edit in Settings → Agent Models</span></div>
      <div class="pf-section-body pf-model-pills">${rows.join('')}</div>
    </div>`;
}

export function smgmtRunBlockedToast() {
  _smgmtShowToast('Another sprint is running — wait for it to finish or cancel it');
}

export function smgmtRunSprint(label) {
  const mode = _smgmtDorMode();
  if (mode === "warn") {
    const tickets = (typeof _smgmtBySprint !== "undefined" && _smgmtBySprint && _smgmtBySprint[label]) || [];
    const notReady = _smgmtDorNotReadyTickets(tickets);
    if (notReady.length > 0) {
      const summary = notReady
        .map((t) => `#${t.number} — ${t.reasons.join(", ")}`)
        .join("\n");
      if (!confirm(`${notReady.length} ticket(s) are not ready:\n\n${summary}\n\nProceed anyway?`)) {
        return;
      }
    }
  }
  _pfOpen(label);
}

export async function smgmtCancelSprint(label) {
  const repo = _smgmtRepo();
  if (!repo) return;
  if (!confirm(`Cancel sprint ${sprintLabelDisplay(label)}? The sprint will stop and tickets will not be modified.`)) return;
  try {
    const res = await fetch(`/api/sprints/run/${encodeURIComponent(label)}?project=${encodeURIComponent(repo)}`, { method: 'DELETE' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      _smgmtShowToast(`Cancel failed: ${err.detail || res.status}`);
    } else {
      _smgmtShowToast(`Sprint ${sprintLabelDisplay(label)} cancel signal sent`);
      _smgmtRunningLabels.delete(label);
      _smgmtAnySprintRunning = _smgmtRunningLabels.size > 0;
      if (typeof _smgmtLingerStart === 'function') {
        _smgmtLingerStart(label, { cancelled: true });
      }
      if (typeof _smgmtLivePollRestart === 'function') _smgmtLivePollRestart();
      if (typeof _smgmtRunningViewUpdate === 'function') {
        const snap = typeof _smgmtLingerLive === 'function' ? _smgmtLingerLive(label) : null;
        _smgmtRunningViewUpdate(label, snap);
      }
      setTimeout(() => loadSprintMgmt(), 2000);
    }
  } catch (e) {
    _smgmtShowToast(`Cancel failed: ${e.message}`);
  }
}


// ── Sprint sign-off: Approve / Reject (issue #862) ───────────────────────────

export async function smgmtApproveSprint(label) {
  const repo = _smgmtRepo();
  if (!repo) return;
  // Confirmation gate: dismissing leaves the sprint pending (no state change).
  if (!confirm(`Approve ${sprintLabelDisplay(label)}? This signs off the sprint and enables Run Sprint.`)) return;
  try {
    const res = await fetch(`/api/sprints/${encodeURIComponent(label)}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project: repo }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      _smgmtShowToast(`Approve failed: ${err.detail || res.status}`);
      return;
    }
    _smgmtShowToast(`${sprintLabelDisplay(label)} approved — ready to run`);
    loadSprintMgmt();
  } catch (e) {
    _smgmtShowToast(`Approve failed: ${e.message}`);
  }
}

export async function smgmtRejectSprint(label) {
  const repo = _smgmtRepo();
  if (!repo) return;
  // Confirmation gate: rejecting dissolves the sprint and returns tickets to backlog.
  if (!confirm(`Reject ${sprintLabelDisplay(label)}? The sprint is dissolved and all its tickets return to the backlog.`)) return;
  try {
    const res = await fetch(`/api/sprints/${encodeURIComponent(label)}/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project: repo }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      _smgmtShowToast(`Reject failed: ${err.detail || res.status}`);
      return;
    }
    _smgmtShowToast(`${sprintLabelDisplay(label)} rejected — tickets returned to backlog`);
    loadSprintMgmt();
  } catch (e) {
    _smgmtShowToast(`Reject failed: ${e.message}`);
  }
}


export function _pfOpen(label) {
  const repo = _smgmtRepo();
  if (!repo) return;
  _pfCurrentLabel = label;
  _pfCurrentRepo  = repo;
  _pfReset();
  document.getElementById('pf-backdrop').classList.remove('hidden');
  document.getElementById('pf-modal').classList.remove('hidden');
  document.getElementById('pf-close-btn').focus();
  _pfFetch();
}

export function _pfReset() {
  document.getElementById('pf-loading').classList.add('hidden');
  document.getElementById('pf-stepper').classList.remove('hidden');
  document.getElementById('pf-content').classList.add('hidden');
  document.getElementById('pf-error').classList.add('hidden');
  document.getElementById('pf-footer').classList.remove('hidden');
  document.getElementById('pf-confirm-btn').disabled = true;
  document.getElementById('pf-confirm-btn').textContent = 'Run Sprint';
  _pfDagData = null;
  _pfWarnings = null;
  _pfCycle = null;
  _pfFlags = null;
  _pfModels = null;
  _pfSelectedIds = new Set();
  _pfUseClineFollowups = false;
  _pfXLSuggestions = [];
  _pfStrictXLGate = false;
  _pfXLMinutesSaved = 0;
  _pfShowLoadingActivity('Loading pre-flight checks…');
}

export function _pfClose() {
  document.getElementById('pf-backdrop').classList.add('hidden');
  document.getElementById('pf-modal').classList.add('hidden');
  document.getElementById('pf-stepper').classList.add('hidden');
  _pfCurrentLabel = null;
  _pfCurrentRepo  = null;
  _pfState        = 'idle';
  _pfDagData      = null;
  _pfWarnings     = null;
  _pfCycle        = null;
  _pfFlags        = null;
  _pfSelectedIds  = new Set();
  _pfUseClineFollowups = false;
  _pfXLSuggestions = [];
  _pfStrictXLGate = false;
  _pfXLMinutesSaved = 0;
  _pfStepFails    = 0;
  _pfAutofixPending = false;
}

export async function _pfFetch() {
  _pfState = 'loading';
  _pfShowLoadingActivity('Loading pre-flight checks…');
  const label = _pfCurrentLabel;
  const repo  = _pfCurrentRepo;
  try {
    const res = await fetch(
      `/api/sprints/${encodeURIComponent(label)}/preflight?project=${encodeURIComponent(repo)}`
    );
    if (!res.ok) throw new Error(await res.text());
    if (_pfCurrentLabel !== label) return;
    const data = await res.json();
    _pfDagData       = data.dag              || null;
    _pfWarnings      = data.warnings         || null;
    _pfCycle         = data.cycle            || null;
    _pfFlags         = data.mis_sizing_flags || null;
    _pfModels        = data.models           || null;
    _pfXLSuggestions = data.xl_suggestions   || [];
    _pfStrictXLGate  = data.strict_xl_gate   || false;
    _pfXLMinutesSaved = data.xl_minutes_saved || 0;
    if (_pfDagData) {
      for (const t of (_pfDagData.tickets || [])) _pfSelectedIds.add(t.id);
    }
    _pfState = 'success';
    _pfShowSuccess();
    // Drive the stepper animation with the fetched data (issue #933)
    _pfStepperAnimate(data);
  } catch (e) {
    if (_pfCurrentLabel !== label) return;
    _pfState = 'error';
    _pfShowError(e.message || 'Preflight check failed.');
  }
}

export function _pfShowSuccess() {
  document.getElementById('pf-loading').classList.add('hidden');
  document.getElementById('pf-error').classList.add('hidden');
  const n = parseInt((_pfCurrentLabel || '').split('-')[1], 10);
  const dagHtml       = _pfDagData && (_pfDagData.tickets || []).length > 0
    ? _pfBuildDAGHtml(_pfDagData)
    : '';
  const warningsHtml  = _pfBuildWarningsHtml();
  const cycleHtml     = _pfBuildCycleHtml();
  const flagsHtml     = _pfBuildFlagsHtml();
  const xlHtml        = _pfBuildXLSuggestionsHtml();
  const conflictsHtml = _pfBuildConflictsHtml();
  const orderHtml     = _pfBuildOrderHtml();
  const modelsHtml    = _pfBuildModelsHtml();
  const clineCheckboxHtml = `<div class="pf-section pf-cline-section">
     <label class="pf-cline-label">
       <input type="checkbox" id="pf-cline-checkbox" class="pf-cline-checkbox"
         ${_pfUseClineFollowups ? 'checked' : ''}
         onchange="_pfUseClineFollowups = this.checked">
       <span>Use Cline (Sonnet) for follow-up coder fixes — tester stays on Claude</span>
     </label>
   </div>`;
  document.getElementById('pf-content').innerHTML =
    `<p style="font-size:13px;color:var(--text);margin:0;">Ready to run <strong>Sprint ${n}</strong>.</p>
     ${modelsHtml}
     ${clineCheckboxHtml}
     ${warningsHtml}
     ${xlHtml}
     ${cycleHtml}
     ${flagsHtml}
     ${dagHtml}
     <div class="pf-section">
       <div class="pf-section-label">Conflicts</div>
       <div class="pf-section-body" id="pf-conflicts">${conflictsHtml}</div>
     </div>
     <div class="pf-section">
       <div class="pf-section-label">Proposed Execution Order</div>
       <div class="pf-section-body" id="pf-order">${orderHtml}</div>
     </div>`;
  document.getElementById('pf-content').classList.remove('hidden');
  document.getElementById('pf-footer').classList.remove('hidden');
  _pfStepperInit();
  // Run enables after blocking checks (cycle, mis-sizing); auto-fix runs in background.
  document.getElementById('pf-cancel-btn').focus();
  if (_pfDagData && (_pfDagData.edges || []).length > 0) {
    requestAnimationFrame(() => _pfDrawDAGArrows(_pfDagData.edges));
  }
}

export function _pfUpdateConfirmBtn() {
  const hasCycle = !!(_pfCycle && _pfCycle.length);
  const pendingFlags = (_pfFlags && (_pfFlags.flags || []).filter(f => f.status === 'pending')) || [];
  const hasPending = pendingFlags.length > 0;
  const hasFail = _pfStepFails > 0;
  const hasBlockingXL = _pfStrictXLGate && _pfXLSuggestions && _pfXLSuggestions.length > 0;
  const confirmBtn = document.getElementById('pf-confirm-btn');
  if (!confirmBtn) return;
  confirmBtn.disabled = hasCycle || hasPending || hasFail || hasBlockingXL;
  if (hasCycle) {
    confirmBtn.title = 'Cannot run: dependency cycle detected. Resolve the cycle first.';
    confirmBtn.setAttribute('aria-label', 'Run Sprint — disabled: dependency cycle detected');
  } else if (hasPending) {
    confirmBtn.title = `Cannot run: ${pendingFlags.length} mis-sizing flag${pendingFlags.length > 1 ? 's' : ''} need review.`;
    confirmBtn.setAttribute('aria-label', 'Run Sprint — disabled: mis-sizing flags need review');
  } else if (hasBlockingXL) {
    const n = _pfXLSuggestions.length;
    confirmBtn.title = `Cannot run: ${n} XL ticket${n > 1 ? 's' : ''} must be split or dismissed (Strict XL gate is on).`;
    confirmBtn.setAttribute('aria-label', `Run Sprint — disabled: strict XL gate blocks ${n} ticket(s)`);
  } else if (hasFail) {
    confirmBtn.title = `Cannot run: ${_pfStepFails} blocking issue${_pfStepFails > 1 ? 's' : ''} detected.`;
    confirmBtn.setAttribute('aria-label', `Run Sprint — disabled: ${_pfStepFails} blocking issue(s)`);
  } else {
    confirmBtn.title = '';
    confirmBtn.setAttribute('aria-label', 'Run Sprint');
  }
}

export function _pfBuildWarningsHtml() {
  if (!_pfWarnings) return '';
  const chips = [];
  const unestimated    = _pfWarnings.unestimated    || [];
  const staleEstimates = _pfWarnings.stale_estimates || [];
  const missingAc      = _pfWarnings.missing_ac      || [];
  if (unestimated.length) {
    chips.push(`<span class="pf-warning-chip">${unestimated.length} unestimated: ${escHtml(unestimated.join(', '))}</span>`);
  }
  if (staleEstimates.length) {
    chips.push(`<span class="pf-warning-chip">${staleEstimates.length} stale estimate${staleEstimates.length > 1 ? 's' : ''}: ${escHtml(staleEstimates.join(', '))}</span>`);
  }
  if (missingAc.length) {
    chips.push(`<span class="pf-warning-chip">${missingAc.length} missing AC: ${escHtml(missingAc.join(', '))}</span>`);
  }
  if (!chips.length) return '';
  return `<div class="pf-warnings-section">
    <div class="pf-warnings-label">Warnings</div>
    <div class="pf-warning-chips">${chips.join('')}</div>
  </div>`;
}

/** Build HTML for the XL split suggestions section (issue #1424). */
export function _pfBuildXLSuggestionsHtml() {
  const suggestions = _pfXLSuggestions || [];
  if (!suggestions.length) return '';

  const label = _pfCurrentLabel;
  const strictNote = _pfStrictXLGate
    ? '<span class="pf-xl-strict-badge">Strict gate on — split or dismiss to proceed</span>'
    : '';
  const savedNote = _pfXLMinutesSaved > 0
    ? `<div class="pf-xl-saved">~${_pfXLMinutesSaved} minutes saved if split</div>`
    : '';

  const rows = suggestions.map(s => {
    const sizeLabel = s.size ? escHtml(s.size) : '?';
    const minsLabel = s.estimated_minutes ? `${s.estimated_minutes} min` : '';
    const estimate = [sizeLabel, minsLabel].filter(Boolean).join(' · ');
    const splitBtn = typeof smgmtSplitOpen === 'function'
      ? `<button class="pf-xl-split-btn" onclick="smgmtSplitOpen(${s.issue_number}, '${escHtml(label || '')}')" title="Open Split flow for #${s.issue_number}">Split</button>`
      : `<a class="pf-xl-split-btn" href="https://github.com/${_smgmtRepo()}/issues/${s.issue_number}" target="_blank" rel="noopener">Split</a>`;

    return `<div class="pf-xl-item" id="pf-xl-item-${s.issue_number}">
      <div class="pf-xl-item-header">
        <span class="pf-xl-item-num">#${s.issue_number}</span>
        <span class="pf-xl-item-title" title="${escHtml(s.title)}">${escHtml(s.title)}</span>
        <span class="pf-xl-consider-label">Consider splitting</span>
        <span class="pf-xl-estimate">${escHtml(estimate)}</span>
      </div>
      <div class="pf-xl-item-actions">
        ${splitBtn}
        <button class="pf-xl-dismiss-btn" onclick="_pfDismissXLSuggestion(${s.issue_number})">Dismiss</button>
      </div>
    </div>`;
  });

  return `<div class="pf-xl-section" id="pf-xl-section">
    <div class="pf-xl-section-label">XL tickets — consider splitting ${strictNote}</div>
    ${savedNote}
    ${rows.join('')}
  </div>`;
}

/** Dismiss one XL suggestion (AC8): call backend, remove from local state, re-render. */
export async function _pfDismissXLSuggestion(issueNum) {
  const label = _pfCurrentLabel;
  const repo  = _pfCurrentRepo;
  if (!label || !repo) return;

  const itemEl = document.getElementById(`pf-xl-item-${issueNum}`);
  if (itemEl) itemEl.querySelectorAll('button, a').forEach(b => { b.disabled = true; });

  try {
    const res = await fetch(
      `/api/sprints/${encodeURIComponent(label)}/xl-suggestions/${issueNum}/dismiss`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project: repo }),
      }
    );
    if (!res.ok) {
      const err = await res.text();
      _smgmtShowToast(`Dismiss failed: ${err}`, 'error');
      if (itemEl) itemEl.querySelectorAll('button, a').forEach(b => { b.disabled = false; });
      return;
    }
    // Remove from local state and re-render section
    _pfXLSuggestions = (_pfXLSuggestions || []).filter(s => s.issue_number !== issueNum);
    const xlSection = document.getElementById('pf-xl-section');
    if (xlSection) {
      const newHtml = _pfBuildXLSuggestionsHtml();
      if (newHtml) {
        xlSection.outerHTML = newHtml;
      } else {
        xlSection.remove();
      }
    }
    _pfUpdateConfirmBtn();
  } catch (e) {
    _smgmtShowToast('Dismiss failed: ' + e.message, 'error');
    if (itemEl) itemEl.querySelectorAll('button, a').forEach(b => { b.disabled = false; });
  }
}

/** Refresh warning chips after background preflight-fix completes. */
function _pfPatchWarnings() {
  const content = document.getElementById('pf-content');
  if (!content) return;
  const html = _pfBuildWarningsHtml();
  content.querySelector('.pf-warnings-section')?.remove();
  if (!html) return;
  const anchor = content.querySelector('.pf-cline-section')
    || content.querySelector('.pf-models-section');
  if (anchor) anchor.insertAdjacentHTML('afterend', html);
}

function _pfShrinkWarnings(fix, _missingAc, _unestimated) {
  if (!_pfWarnings || (fix.errors && fix.errors.length)) return;
  if (fix.filled > 0 && _pfWarnings.missing_ac?.length) {
    _pfWarnings.missing_ac = _pfWarnings.missing_ac.slice(fix.filled);
  }
  if (fix.estimated > 0 && _pfWarnings.unestimated?.length) {
    _pfWarnings.unestimated = _pfWarnings.unestimated.slice(fix.estimated);
  }
  _pfPatchWarnings();
}

export function _pfBuildCycleHtml() {
  if (!_pfCycle || !_pfCycle.length) return '';
  return `<div class="pf-cycle-banner">
    <strong>Cycle detected:</strong> ${escHtml(_pfCycle.join(' → '))}
  </div>`;
}

// ── Mis-sizing flags section (issue #578) ───────────────────────────────────

export function _pfBuildFlagsHtml() {
  const flags = _pfFlags && (_pfFlags.flags || []);
  if (!flags || !flags.length) return '';

  const rows = flags.map(f => {
    const num = f.issue_number;
    const resolved = f.status !== 'pending';
    const itemClass = resolved ? 'pf-flag-item resolved' : 'pf-flag-item';

    const estLabel = f.current_estimate
      ? `${escHtml(f.current_estimate)} (${f.current_estimate_minutes ?? '?'} min)`
      : 'unknown';
    const avgLabel = f.historical_avg_actual_size
      ? `${escHtml(f.historical_avg_actual_size)} (${f.historical_avg_actual_minutes ?? '?'} min avg)`
      : 'unknown';
    const drivingLabels = (f.driving_labels || []).map(l => `<code>${escHtml(l)}</code>`).join(', ');
    const eventCount = f.mis_sizing_event_count || 0;

    let badgeHtml = '';
    let actionsHtml = '';
    if (resolved) {
      const actionText = { approved: 'Approved', reestimated: 'Re-estimated', dismissed: 'Dismissed' }[f.status] || f.status;
      badgeHtml = `<span class="pf-flag-badge pf-flag-badge-resolved">${escHtml(actionText)}</span>`;
      const noteText = f.action_note ? ` — ${escHtml(f.action_note)}` : '';
      const newSizeText = f.new_size ? ` New size: ${escHtml(f.new_size)}.` : '';
      actionsHtml = `<div class="pf-flag-resolved-note">${escHtml(actionText)}${newSizeText}${noteText}</div>`;
    } else {
      badgeHtml = `<span class="pf-flag-badge pf-flag-badge-pending">Review needed</span>`;
      actionsHtml = `
        <div class="pf-flag-actions" id="pf-flag-actions-${num}">
          <button class="pf-flag-action-btn approve" onclick="_pfFlagAction(${num}, 'approved')">Approve</button>
          <button class="pf-flag-action-btn" onclick="_pfFlagShowSizePicker(${num}, '${escHtml(f.current_estimate || 'S')}')">Re-estimate</button>
          <button class="pf-flag-action-btn dismiss" onclick="_pfFlagAction(${num}, 'dismissed')">Dismiss</button>
        </div>
        <div id="pf-flag-picker-${num}" style="display:none">
          <div class="pf-flag-size-picker">
            <span style="font-size:12px;color:var(--text-muted);">New size:</span>
            ${['S','M','L','XL'].map(s =>
              `<button class="pf-flag-size-btn" onclick="_pfFlagReestimate(${num}, '${s}')">${s}</button>`
            ).join('')}
            <button class="pf-flag-size-cancel" onclick="_pfFlagHidePicker(${num})">Cancel</button>
          </div>
        </div>`;
    }

    return `<div class="${itemClass}" id="pf-flag-item-${num}">
      <div class="pf-flag-header">
        <span class="pf-flag-id">#${num}</span>
        <span class="pf-flag-title" title="${escHtml(f.title)}">${escHtml(f.title)}</span>
        ${badgeHtml}
      </div>
      <div class="pf-flag-details">
        Estimate: <strong>${estLabel}</strong> ·
        Historical avg: <strong>${avgLabel}</strong> ·
        ${eventCount} mis-sizing event${eventCount !== 1 ? 's' : ''} on: ${drivingLabels}
      </div>
      ${actionsHtml}
    </div>`;
  });

  const pending = flags.filter(f => f.status === 'pending').length;
  const subtitle = pending > 0
    ? `${pending} ticket${pending > 1 ? 's' : ''} flagged for review`
    : 'All flags resolved';

  return `<div class="pf-flags-section" id="pf-flags-section">
    <div class="pf-flags-label">Mis-sizing review — ${subtitle}</div>
    ${rows.join('')}
  </div>`;
}

export function _pfFlagShowSizePicker(num, _currentSize) {
  const actionsEl = document.getElementById(`pf-flag-actions-${num}`);
  const pickerEl  = document.getElementById(`pf-flag-picker-${num}`);
  if (actionsEl) actionsEl.style.display = 'none';
  if (pickerEl)  pickerEl.style.display  = 'block';
}

export function _pfFlagHidePicker(num) {
  const actionsEl = document.getElementById(`pf-flag-actions-${num}`);
  const pickerEl  = document.getElementById(`pf-flag-picker-${num}`);
  if (actionsEl) actionsEl.style.display = '';
  if (pickerEl)  pickerEl.style.display  = 'none';
}

export async function _pfFlagAction(num, action, newSize) {
  const label = _pfCurrentLabel;
  const repo  = _pfCurrentRepo;
  if (!label || !repo) return;

  // Disable buttons to prevent double-click
  const itemEl = document.getElementById(`pf-flag-item-${num}`);
  if (itemEl) itemEl.querySelectorAll('button').forEach(b => { b.disabled = true; });

  try {
    const body = { action };
    if (newSize) body.new_size = newSize;
    const res = await fetch(
      `/api/sprints/${encodeURIComponent(label)}/mis-sizing-flags/${num}/action?project=${encodeURIComponent(repo)}`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    );
    if (!res.ok) {
      const err = await res.text();
      _smgmtShowToast(`Flag action failed: ${err}`, 'error');
      if (itemEl) itemEl.querySelectorAll('button').forEach(b => { b.disabled = false; });
      return;
    }
    const data = await res.json();
    // Update local state and re-render flags section
    _pfFlags = data;
    const flagsSection = document.getElementById('pf-flags-section');
    if (flagsSection) {
      const newHtml = _pfBuildFlagsHtml();
      flagsSection.outerHTML = newHtml || '<div id="pf-flags-section"></div>';
    }
    _pfUpdateConfirmBtn();
  } catch (e) {
    _smgmtShowToast('Flag action failed: ' + e.message, 'error');
    if (itemEl) itemEl.querySelectorAll('button').forEach(b => { b.disabled = false; });
  }
}

export function _pfFlagReestimate(num, newSize) {
  _pfFlagHidePicker(num);
  _pfFlagAction(num, 'reestimated', newSize);
}

// ────────────────────────────────────────────────────────────────────────────

export function _pfBuildDAGHtml(dag) {
  const ticketMap = {};
  for (const t of (dag.tickets || [])) ticketMap[t.id] = t;
  const layers = dag.layers || [];
  if (!layers.length) return '';

  let colsHtml = '';
  for (let i = 0; i < layers.length; i++) {
    const layer = layers[i];
    let cardsHtml = '';
    for (const id of layer) {
      const t = ticketMap[id] || { id, number: id.replace('#', ''), title: id, state: 'backlog', size: null, files_touched: [] };
      const stateClass = t.state || 'backlog';
      const stateBadge = `<span class="ticket-status-pill ${escHtml(stateClass)}">${escHtml(stateClass)}</span>`;
      const sizeBadge  = t.size ? `<span class="pf-dag-size-badge">${escHtml(t.size)}</span>` : '';
      const files      = (t.files_touched || []);
      const shown      = files.slice(0, 3).map(f => `<span>${escHtml(f.split('/').slice(-1)[0])}</span>`).join('');
      const more       = files.length > 3 ? `<span>+${files.length - 3} more</span>` : '';
      const filesHtml  = (shown || more) ? `<div class="pf-dag-card-files">${shown}${more}</div>` : '';
      cardsHtml += `<div class="pf-dag-card" id="pf-card-${escHtml(id)}" data-dag-id="${escHtml(id)}">
          <div class="pf-dag-card-header">
            <input type="checkbox" class="pf-dag-card-check" checked
              onchange="_pfToggleTicket('${escHtml(id)}')" aria-label="Include ticket ${escHtml(id)}">
            <span class="pf-dag-card-num">${escHtml(id)}</span>
          </div>
          <div class="pf-dag-card-title" title="${escHtml(t.title)}">${escHtml(t.title)}</div>
          <div class="pf-dag-card-meta">${stateBadge}${sizeBadge}</div>
          ${filesHtml}
        </div>`;
    }
    colsHtml += `<div class="pf-dag-col">
        <div class="pf-dag-col-label">Level ${i + 1}</div>
        ${cardsHtml}
      </div>`;
  }

  return `<div class="pf-dag-section">
    <div class="pf-dag-section-label">Execution Graph</div>
    <div class="pf-dag-wrap" id="pf-dag-wrap">
      <svg class="pf-dag-svg" id="pf-dag-svg" aria-hidden="true"></svg>
      <div class="pf-dag-levels" id="pf-dag-levels">${colsHtml}</div>
    </div>
  </div>`;
}

export function _pfDrawDAGArrows(edges) {
  if (!edges || !edges.length) return;
  const wrap   = document.getElementById('pf-dag-wrap');
  const svg    = document.getElementById('pf-dag-svg');
  const levels = document.getElementById('pf-dag-levels');
  if (!wrap || !svg || !levels) return;

  const wrapRect = wrap.getBoundingClientRect();
  const h = levels.getBoundingClientRect().height;
  svg.setAttribute('width',  String(wrapRect.width));
  svg.setAttribute('height', String(h));

  const defs   = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
  const marker = document.createElementNS('http://www.w3.org/2000/svg', 'marker');
  marker.setAttribute('id', 'pf-arrow');
  marker.setAttribute('markerWidth',  '7');
  marker.setAttribute('markerHeight', '7');
  marker.setAttribute('refX', '6');
  marker.setAttribute('refY', '3.5');
  marker.setAttribute('orient', 'auto');
  const arrowPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  arrowPath.setAttribute('d', 'M0,0 L0,7 L7,3.5 z');
  arrowPath.setAttribute('fill', 'var(--text-muted)');
  marker.appendChild(arrowPath);
  defs.appendChild(marker);
  svg.appendChild(defs);

  for (const [fromId, toId] of edges) {
    const fromEl = wrap.querySelector(`[data-dag-id="${fromId}"]`);
    const toEl   = wrap.querySelector(`[data-dag-id="${toId}"]`);
    if (!fromEl || !toEl) continue;

    const fr = fromEl.getBoundingClientRect();
    const tr = toEl.getBoundingClientRect();
    const x1 = fr.right  - wrapRect.left;
    const y1 = fr.top    + fr.height / 2 - wrapRect.top;
    const x2 = tr.left   - wrapRect.left - 7;
    const y2 = tr.top    + tr.height / 2 - wrapRect.top;
    const mx = (x1 + x2) / 2;

    const line = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    line.setAttribute('d', `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`);
    line.setAttribute('stroke', 'var(--text-muted)');
    line.setAttribute('stroke-width', '1.5');
    line.setAttribute('fill', 'none');
    line.setAttribute('marker-end', 'url(#pf-arrow)');
    svg.appendChild(line);
  }
}

export function _pfToggleTicket(id) {
  if (_pfSelectedIds.has(id)) {
    _pfSelectedIds.delete(id);
  } else {
    _pfSelectedIds.add(id);
  }
  const card = document.getElementById(`pf-card-${id}`);
  if (card) card.classList.toggle('pf-deselected', !_pfSelectedIds.has(id));
  _pfUpdateSections();
}

export function _pfGetSelectedTickets() {
  if (!_pfDagData) return [];
  return (_pfDagData.tickets || []).filter(t => _pfSelectedIds.has(t.id));
}

export function _pfComputeConflicts(tickets) {
  const conflicts = [];
  for (let i = 0; i < tickets.length; i++) {
    for (let j = i + 1; j < tickets.length; j++) {
      const filesA = tickets[i].files_touched || [];
      const filesB = tickets[j].files_touched || [];
      const shared = filesA.filter(f => filesB.includes(f));
      for (const file of shared) {
        conflicts.push({ a: tickets[i], b: tickets[j], file });
      }
    }
  }
  return conflicts;
}

export function _pfBuildConflictsHtml() {
  const selected = _pfGetSelectedTickets();
  const conflicts = _pfComputeConflicts(selected);
  if (!conflicts.length) {
    return '<p class="pf-no-conflict">No file conflicts detected.</p>';
  }
  return conflicts.map(c =>
    `<p class="pf-conflict-item">Tickets #${c.a.number} and #${c.b.number} both touch <code>${escHtml(c.file)}</code></p>`
  ).join('');
}

export function _pfBuildOrderHtml() {
  if (!_pfDagData) return '<p class="pf-no-conflict">No order data available.</p>';
  const layers = (_pfDagData.layers || [])
    .map(layer => layer.filter(id => _pfSelectedIds.has(id)))
    .filter(l => l.length > 0);
  if (!layers.length) return '<p class="pf-no-conflict">No tickets selected.</p>';

  let html = '<ol class="pf-order-list">';
  for (let i = 0; i < layers.length; i++) {
    const nums = layers[i].map(id => id);
    const descriptor = i === 0 ? 'parallel-eligible' : `runs after Level ${i}`;
    html += `<li class="pf-order-item">Level ${i + 1}: ${escHtml(nums.join(', '))} — ${escHtml(descriptor)}.</li>`;
  }
  html += '</ol>';
  return html;
}

export function _pfUpdateSections() {
  const conflictsEl = document.getElementById('pf-conflicts');
  const orderEl     = document.getElementById('pf-order');
  if (conflictsEl) conflictsEl.innerHTML = _pfBuildConflictsHtml();
  if (orderEl)     orderEl.innerHTML     = _pfBuildOrderHtml();
}

export function _pfShowError(msg) {
  document.getElementById('pf-loading').classList.add('hidden');
  unmountProgressActivity('pf-stepper-steps');
  document.getElementById('pf-content').classList.add('hidden');
  document.getElementById('pf-error-msg').textContent = msg;
  document.getElementById('pf-error').classList.remove('hidden');
  document.getElementById('pf-footer').classList.remove('hidden');
  document.getElementById('pf-confirm-btn').disabled = true;
  document.getElementById('pf-retry-btn').focus();
}

export function _pfRetry() {
  _pfReset();
  _pfFetch();
}

export async function _pfConfirm() {
  if (_pfState !== 'success') return;
  const label = _pfCurrentLabel;
  const repo  = _pfCurrentRepo;
  if (!label || !repo) return;
  const confirmBtn = document.getElementById('pf-confirm-btn');
  confirmBtn.disabled = true;
  confirmBtn.textContent = 'Starting…';
  _pfClose();
  // Kickoff stepper drives the run from here (issue #932)
  await smgmtKickoffRun(label, repo);
}


// ── Pre-flight stepper functions (shared ProgressActivity — stepper mode, issue #933) ─

function _paStepState(state) {
  return state === 'fail' ? 'failed' : state;
}

function _pfShowLoadingActivity(currentLabel) {
  const stepsEl = document.getElementById('pf-stepper-steps');
  if (!stepsEl) return;
  mountProgressActivity(stepsEl, {
    status: 'running',
    mode: 'indeterminate',
    current: currentLabel || 'Loading…',
  }, {
    id: 'pf-pa',
    hideLog: true,
  });
}

/** Initialise all steps to `pending` state. Called from _pfReset(). */
export function _pfStepperInit() {
  _pfStepFails = 0;
  const stepsEl = document.getElementById('pf-stepper-steps');
  if (!stepsEl) return;
  mountProgressActivity(stepsEl, {
    status: 'running',
    mode: 'stepper',
    steps: PF_STEPS.map((s) => ({
      key: s.key,
      label: s.label,
      state: 'pending',
      note: '',
    })),
  }, {
    id: 'pf-pa',
    hideLog: true,
  });
  const summaryEl = document.getElementById('pf-stepper-summary');
  if (summaryEl) {
    summaryEl.textContent = '';
    summaryEl.className = 'pf-stepper-summary hidden';
  }
}

/** Transition a single step to a new state with an optional note. */
export function _pfStepState(key, state, note) {
  patchProgressActivityStep('pf-stepper-steps', key, _paStepState(state), note || '', {
    id: 'pf-pa',
    hideLog: true,
  });
}

/**
 * Call the preflight-fix SSE endpoint and collect summary counts.
 * Auto-fixes missing AC and missing size estimates for the sprint.
 */
async function _pfRunAutoFix(label, repo, onLog) {
  const resp = await fetch(
    `/api/sprints/${encodeURIComponent(label)}/preflight-fix?project=${encodeURIComponent(repo)}`,
    { method: 'POST' }
  );
  if (!resp.ok) throw new Error(`preflight-fix ${resp.status}`);
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = '', filled = 0, estimated = 0, errors = [];
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const parts = buf.split('\n\n');
    buf = parts.pop();
    for (const part of parts) {
      const m = part.match(/^event:\s*(\S+)\ndata:\s*([\s\S]*)$/);
      if (!m) continue;
      if (m[1] === 'log') {
        try {
          const d = JSON.parse(m[2]);
          const msg = typeof d === 'string' ? d : (d.message || String(d));
          if (onLog) onLog(msg);
        } catch (_) {
          if (onLog) onLog(m[2]);
        }
      } else if (m[1] === 'done') {
        try {
          const d = JSON.parse(m[2]);
          filled    = d.filled    || 0;
          estimated = d.estimated || 0;
          errors    = d.errors    || [];
        } catch (_) { /* ignore parse errors */ }
      }
    }
  }
  return { filled, estimated, errors };
}

/**
 * Drive the stepper state machine using preflight API response data.
 * Steps animate pending → checking → pass/fail/fixed sequentially.
 * Called from _pfFetch() after a successful preflight response.
 */
export async function _pfStepperAnimate(data) {
  const label = _pfCurrentLabel;
  const repo  = _pfCurrentRepo;

  const missingAc   = (data.warnings && data.warnings.missing_ac   || []);
  const unestimated = (data.warnings && data.warnings.unestimated || []);
  const hasAcIssues  = missingAc.length  > 0;
  const hasEstIssues = unestimated.length > 0;

  const _routeAutofixLog = (msg) => {
    const s = String(msg || '');
    if (/acceptance criteria/i.test(s)) {
      _pfStepState('ac', 'checking', s);
    } else if (/Estimating/i.test(s)) {
      _pfStepState('estimates', 'checking', s);
    } else if (/Fixing \d+ pre-flight/i.test(s)) {
      if (hasAcIssues)  _pfStepState('ac', 'checking', s);
      if (hasEstIssues) _pfStepState('estimates', 'checking', s);
    }
  };

  const _finishAutofix = (fix) => {
    const acNote  = fix.filled    > 0 ? `${fix.filled} acceptance criteria generated`
                  : hasAcIssues       ? `${missingAc.length} ticket(s) missing AC`
                  : '';
    const estNote = fix.estimated > 0 ? `${fix.estimated} ticket(s) estimated`
                  : hasEstIssues      ? `${unestimated.length} ticket(s) unestimated`
                  : '';
    _pfStepState('ac',        fix.filled    > 0 ? 'fixed' : 'pass', acNote);
    _pfStepState('estimates', fix.estimated > 0 ? 'fixed' : 'pass', estNote);
    _pfShrinkWarnings(fix, missingAc, unestimated);
    _pfAutofixPending = false;
    _pfStepperSummary();
  };

  // ── Steps 1 & 2: auto-fix in background (warnings are non-blocking) ───────
  if ((hasAcIssues || hasEstIssues) && label && repo) {
    _pfAutofixPending = true;
    _pfStepState('ac',        'checking', hasAcIssues  ? `Fixing ${missingAc.length} ticket(s)…` : '');
    _pfStepState('estimates', 'checking', hasEstIssues ? `Estimating ${unestimated.length} ticket(s)…` : '');
    _pfRunAutoFix(label, repo, _routeAutofixLog)
      .then(_finishAutofix)
      .catch(() => {
        _pfStepState('ac',        'pass', hasAcIssues  ? `${missingAc.length} ticket(s) missing AC`   : '');
        _pfStepState('estimates', 'pass', hasEstIssues ? `${unestimated.length} ticket(s) unestimated` : '');
        _pfAutofixPending = false;
        _pfStepperSummary();
      });
  } else {
    _pfStepState('ac',        'pass', '');
    _pfStepState('estimates', 'pass', '');
  }

  // ── Steps 3–5: blocking / informational checks (no artificial delay) ─────
  _pfStepState('cycle', 'checking', '');
  if (data.cycle && data.cycle.length) {
    _pfStepState('cycle', 'fail', `Cycle: ${data.cycle.join(' → ')}`);
    _pfStepFails++;
  } else {
    _pfStepState('cycle', 'pass', '');
  }

  _pfStepState('missizing', 'checking', '');
  const pendingFlags = (data.mis_sizing_flags && data.mis_sizing_flags.flags || [])
    .filter(f => f.status === 'pending');
  if (pendingFlags.length > 0) {
    _pfStepState('missizing', 'fail', `${pendingFlags.length} flag(s) require review`);
    _pfStepFails++;
  } else {
    _pfStepState('missizing', 'pass', '');
  }

  _pfStepState('conflicts', 'checking', '');
  const selectedTickets = _pfGetSelectedTickets();
  const conflicts = _pfComputeConflicts(selectedTickets);
  if (conflicts.length > 0) {
    _pfStepState('conflicts', 'pass', `${conflicts.length} conflict(s) — execution order planned`);
  } else {
    _pfStepState('conflicts', 'pass', '');
  }

  _pfStepperSummary();
  _pfUpdateConfirmBtn();
}

/** Show the overall summary: all-clear or blocking count. */
export function _pfStepperSummary() {
  const summaryEl = document.getElementById('pf-stepper-summary');
  if (!summaryEl) return;
  summaryEl.classList.remove('hidden');
  if (_pfStepFails > 0) {
    summaryEl.textContent =
      `${_pfStepFails} blocking issue${_pfStepFails > 1 ? 's' : ''} — cannot run`;
    summaryEl.className = 'pf-stepper-summary pf-stepper-summary--blocking';
  } else if (_pfAutofixPending) {
    summaryEl.textContent = 'Ready to run — preparing tickets in background';
    summaryEl.className = 'pf-stepper-summary pf-stepper-summary--clear';
  } else {
    summaryEl.textContent = 'All checks passed — ready to run';
    summaryEl.className = 'pf-stepper-summary pf-stepper-summary--clear';
  }
}


// ── Kickoff stepper (issue #932) ─────────────────────────────────────────────
// Shows live progress for the three-phase sprint launch: lock acquisition →
// branch creation → agent dispatch. Uses the shared pf-step-item component
// (same CSS classes as the pre-flight stepper). Appears in the Running subview
// immediately when the operator confirms the preflight modal, replacing the
// bare "Starting…" button state.

/** Step definitions for the kickoff flow. */
const KS_STEPS = [
  { key: 'lock',     label: 'Validate and acquire lock' },
  { key: 'branch',   label: 'Create sprint branch'      },
  { key: 'dispatch', label: 'Dispatch first agents'     },
];

/** Which step index failed (-1 = none). Used by retry logic (AC7). */
let _ksFailedStep = -1;
let _ksLabel = null;
let _ksRepo  = null;

/** Render kickoff steps in pending state. */
function _ksInit() {
  const stepsEl = document.getElementById('smgmt-kickoff-steps');
  if (!stepsEl) return;
  mountProgressActivity(stepsEl, {
    status: 'running',
    mode: 'stepper',
    steps: KS_STEPS.map((s) => ({
      key: s.key,
      label: s.label,
      state: 'pending',
      note: '',
    })),
  }, {
    id: 'ks-pa',
    hideLog: true,
  });
  const errEl = document.getElementById('smgmt-kickoff-error');
  if (errEl) errEl.hidden = true;
}

/** Transition a kickoff step to a new state with an optional note. */
function _ksSetStep(key, state, note) {
  patchProgressActivityStep('smgmt-kickoff-steps', key, _paStepState(state), note || '', {
    id: 'ks-pa',
    hideLog: true,
  });
}

/** Show the kickoff stepper in the Running subview. */
function _ksShow(label, repo) {
  _ksLabel = label;
  _ksRepo  = repo;
  _ksFailedStep = -1;
  _ksInit();
  const shell   = document.getElementById('smgmt-kickoff-shell');
  const runShell = document.getElementById('smgmt-run-shell');
  const emptyEl  = document.getElementById('smgmt-running-empty');
  if (emptyEl)  emptyEl.hidden  = true;
  if (runShell) runShell.hidden = true;
  if (shell)    shell.hidden    = false;
  if (typeof _smgmtShowSubView === 'function') _smgmtShowSubView('running');
}

/** Hide the kickoff stepper. */
function _ksHide() {
  const shell = document.getElementById('smgmt-kickoff-shell');
  if (shell) shell.hidden = true;
}

/** Display the error state for a failed step (AC5). */
function _ksShowError(stepKey, msg) {
  _ksSetStep(stepKey, 'fail', msg);
  const errEl = document.getElementById('smgmt-kickoff-error');
  if (!errEl) return;
  const msgEl = document.getElementById('smgmt-kickoff-error-msg');
  if (msgEl) msgEl.textContent = msg || 'An error occurred';
  errEl.hidden = false;
}

/** True if the given sprint label is currently in the running-all list. */
async function _ksIsRunning(label) {
  try {
    const res = await fetch('/api/sprints/running-all');
    if (!res.ok) return false;
    const data = await res.json();
    return (data.running || []).some(r => r.sprint_label === label);
  } catch (_) {
    return false;
  }
}

/**
 * Step 1: POST /api/sprints/run. Returns true on 202, false on error.
 * On failure the error message is shown inline at the lock step (AC5/AC6).
 */
async function _ksStep1Post() {
  const label = _ksLabel;
  const repo  = _ksRepo;
  _ksSetStep('lock', 'checking', '');
  try {
    const res = await fetch('/api/sprints/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project: repo, sprint_label: label, use_cline_followups: _pfUseClineFollowups }),
    });
    if (!res.ok) {
      let detail = await res.text();
      try { const p = JSON.parse(detail); detail = typeof p.detail === 'string' ? p.detail : JSON.stringify(p.detail); }
      catch (_) { /* plain-text body */ }
      _ksShowError('lock', detail || `HTTP ${res.status}`);
      _ksFailedStep = 0;
      return false;
    }
    _ksSetStep('lock', 'pass', '');
    return true;
  } catch (e) {
    _ksShowError('lock', e.message);
    _ksFailedStep = 0;
    return false;
  }
}

/**
 * Step 2: Poll until sprint appears in running-all (branch created / process alive).
 * Returns true on success, false on timeout.
 */
async function _ksStep2Branch() {
  _ksSetStep('branch', 'checking', '');
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    // 2s (was 1s) — matches step 3 and halves the running-all polling burst
    // while the process is starting.
    await new Promise(r => setTimeout(r, 2000));
    if (await _ksIsRunning(_ksLabel)) {
      _ksSetStep('branch', 'pass', '');
      return true;
    }
  }
  // The process never entered the running state. The usual cause is NOT a slow
  // start — it exited almost immediately, most often because no tickets were
  // dispatchable (wrong/missing sprint label or status labels on the tickets),
  // or it finished/crashed. Surface that instead of a bare "waiting" timeout.
  _ksShowError('branch',
    'Sprint didn’t start running — it likely exited immediately. Most often no '
    + 'dispatchable tickets (check the sprint label + status labels on the '
    + 'tickets), or it finished/crashed. Check the run log, then Retry.');
  _ksFailedStep = 1;
  return false;
}

/**
 * Step 3: Poll /api/sprint-status until the first agents are dispatched.
 * Transitions to running pane after success. Returns true on success.
 */
async function _ksStep3Dispatch() {
  const label = _ksLabel;
  const repo  = _ksRepo;
  _ksSetStep('dispatch', 'checking', '');
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 2000));
    try {
      const res = await fetch(`/api/sprint-status?project=${encodeURIComponent(repo)}`);
      if (res.ok) {
        const data = await res.json();
        const sprint = (data.running_sprints || []).find(s => s.sprint_label === label);
        // Agents dispatched when status has been posted with at least one issue
        if (sprint && sprint.issues && sprint.issues.length > 0) {
          _ksSetStep('dispatch', 'pass', '');
          return true;
        }
        // Sprint disappeared from running — it terminated before dispatching
        if (!sprint && !(await _ksIsRunning(label))) {
          _ksShowError('dispatch', 'Sprint terminated before agents were dispatched');
          _ksFailedStep = 2;
          return false;
        }
      }
    } catch (_) { /* ignore transient errors — keep polling */ }
  }
  // Timed out but sprint is still running — advance optimistically (slow dispatch)
  _ksSetStep('dispatch', 'pass', '');
  return true;
}

/** Finish the kickoff: hide stepper, reload board, start live poll. */
async function _ksFinish(label) {
  _ksHide();
  _smgmtShowToast(`Sprint ${sprintLabelDisplay(label)} dispatched`);
  if (typeof _smgmtShowSubView === 'function') _smgmtShowSubView('running');
  await loadSprintMgmt(true, label);
  if (typeof _smgmtLivePollRestart === 'function') _smgmtLivePollRestart();
  for (let i = 0; i < 8; i++) {
    if (_smgmtRunningLabels && _smgmtRunningLabels.has(label)) break;
    await new Promise(r => setTimeout(r, 600));
    await loadSprintMgmt(true, label);
  }
}

/**
 * Drive the three-step kickoff flow. Called from _pfConfirm() for both initial
 * run and re-run (via the preflight modal). Shows the Running subview immediately
 * so the stepper is visible while the POST and polls complete (AC1).
 */
export async function smgmtKickoffRun(label, repo) {
  _ksShow(label, repo);

  // Step 1: validate/acquire lock
  if (!await _ksStep1Post()) return;   // AC6: return early on failure

  // Step 2: create sprint branch
  if (!await _ksStep2Branch()) return; // AC6: return early on failure

  // Step 3: dispatch first agents
  if (!await _ksStep3Dispatch()) return; // AC6: return early on failure

  // All steps succeeded → transition to running pane (AC4)
  await _ksFinish(label);
}

/**
 * Retry from the step that failed, not from step 1 (AC7).
 * - Step 0 (lock) failed: re-run the full flow
 * - Step 1 (branch) failed: re-poll from step 2, then step 3
 * - Step 2 (dispatch) failed: re-poll from step 3
 */
export async function smgmtKickoffRetry() {
  if (!_ksLabel || !_ksRepo) return;
  const failedStep = _ksFailedStep;
  const label = _ksLabel;

  const errEl = document.getElementById('smgmt-kickoff-error');
  if (errEl) errEl.hidden = true;
  _ksFailedStep = -1;

  if (failedStep <= 0) {
    // Lock failed — re-run full kickoff (new POST needed)
    _ksSetStep('lock',     'pending', '');
    _ksSetStep('branch',   'pending', '');
    _ksSetStep('dispatch', 'pending', '');
    if (!await _ksStep1Post()) return;
    if (!await _ksStep2Branch()) return;
    if (!await _ksStep3Dispatch()) return;
  } else if (failedStep === 1) {
    // Branch failed — sprint POST already succeeded; re-poll from step 2
    _ksSetStep('branch',   'pending', '');
    _ksSetStep('dispatch', 'pending', '');
    if (!await _ksStep2Branch()) return;
    if (!await _ksStep3Dispatch()) return;
  } else {
    // Dispatch failed — re-poll from step 3
    _ksSetStep('dispatch', 'pending', '');
    if (!await _ksStep3Dispatch()) return;
  }

  await _ksFinish(label);
}
