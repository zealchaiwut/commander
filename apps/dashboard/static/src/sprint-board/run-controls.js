/* Run controls — dispatch API wiring (issue #2356).
 *
 * Preflight-warnings re-exported from preflight-warnings.js. Run Sprint calls
 * POST /api/sprints/{label}/dispatch with empty tickets / all:true so the
 * server resolves open issues for the label (#2353).
 */

export {
  _pfBuildWarningsHtml,
  smgmtOpenPreflightWarnings,
  _pfClose,
  _pfRetry,
  _pfConfirm,
  _pfBulkClose,
} from './preflight-warnings.js';

const _noop = () => {};
const _noopStr = () => '';

/** Labels with an in-flight dispatch started from the board. */
const _dispatchInFlight = new Set();

export function smgmtRunBlockedToast() {
  if (typeof globalThis._smgmtShowToast === 'function') {
    globalThis._smgmtShowToast('Another sprint is already running');
  }
}

/**
 * Start an API dispatch for ``label`` (issue #2356).
 *
 * Uses resolve-by-label (`all: true`, empty tickets) — same helper as #2353.
 * Refuses a second click while this label is already in-flight on the board.
 */
export async function smgmtRunSprint(label) {
  if (!label) return;
  if (_dispatchInFlight.has(label)) {
    if (typeof globalThis._smgmtShowToast === 'function') {
      globalThis._smgmtShowToast(`Dispatch already running for ${label}`);
    }
    return;
  }

  const repo =
    typeof globalThis._smgmtRepo === 'function'
      ? globalThis._smgmtRepo() || ''
      : '';
  if (!repo) {
    if (typeof globalThis._smgmtShowToast === 'function') {
      globalThis._smgmtShowToast('No project selected');
    }
    return;
  }

  _dispatchInFlight.add(label);
  _setRunBtnBusy(label, true);

  let res;
  try {
    res = await fetch(`/api/sprints/${encodeURIComponent(label)}/dispatch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tickets: [], all: true, repo }),
    });
  } catch (e) {
    _dispatchInFlight.delete(label);
    _setRunBtnBusy(label, false);
    if (typeof globalThis._smgmtShowToast === 'function') {
      globalThis._smgmtShowToast('Dispatch failed: ' + e.message);
    }
    return;
  }

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    _dispatchInFlight.delete(label);
    _setRunBtnBusy(label, false);
    const detail =
      typeof data.detail === 'string'
        ? data.detail
        : data.detail
          ? JSON.stringify(data.detail)
          : 'HTTP ' + res.status;
    if (typeof globalThis._smgmtShowToast === 'function') {
      globalThis._smgmtShowToast('Dispatch failed: ' + detail);
    }
    return;
  }

  if (typeof globalThis._smgmtShowToast === 'function') {
    const n = (data.tickets || []).length;
    globalThis._smgmtShowToast(
      `Dispatch started for ${label}` +
        (data.run_id ? ` (${data.run_id})` : '') +
        (n ? ` · ${n} ticket${n === 1 ? '' : 's'}` : ''),
    );
  }

  // Keep busy until the operator refreshes / Running view picks it up.
  // Clear after a short delay so a failed-to-start race can re-click.
  setTimeout(() => {
    _dispatchInFlight.delete(label);
    _setRunBtnBusy(label, false);
  }, 8000);

  if (typeof globalThis.loadSprintMgmt === 'function') {
    try {
      await globalThis.loadSprintMgmt();
    } catch (_) {
      /* ignore refresh errors */
    }
  }
}

function _setRunBtnBusy(label, busy) {
  try {
    const card = document.getElementById('smgmt-card-' + label);
    if (!card) return;
    const btn = card.querySelector('.smgmt-run-btn');
    if (!btn) return;
    if (busy) {
      btn.disabled = true;
      btn.classList.add('smgmt-run-btn--busy');
      btn.setAttribute('aria-busy', 'true');
    } else {
      btn.disabled = false;
      btn.classList.remove('smgmt-run-btn--busy');
      btn.removeAttribute('aria-busy');
    }
  } catch (_) {
    /* DOM optional in tests */
  }
}

/** Exported for tests — which URL + body smgmtRunSprint would POST. */
export function _dispatchRequestFor(label, repo) {
  return {
    url: `/api/sprints/${encodeURIComponent(label)}/dispatch`,
    body: { tickets: [], all: true, repo },
  };
}

export const smgmtApproveSprint = _noop;
export const smgmtRejectSprint = _noop;
export const _pfOpen = _noop;
export const _pfReset = _noop;
export const _pfFetch = _noop;
export const _pfShowSuccess = _noop;
export const _pfUpdateConfirmBtn = _noop;
export const _pfBuildCycleHtml = _noopStr;
export const _pfBuildFlagsHtml = _noopStr;
export const _pfFlagShowSizePicker = _noop;
export const _pfFlagHidePicker = _noop;
export const _pfFlagAction = _noop;
export const _pfFlagReestimate = _noop;
export const _pfFlagAutoReestimate = _noop;
export const _pfFlagDefaultReestimateSize = _noopStr;
export const _pfApproveAll = _noop;
export const _pfReestimateAll = _noop;
export const _pfBuildDAGHtml = _noopStr;
export const _pfDrawDAGArrows = _noop;
export const _pfToggleTicket = _noop;
export const _pfGetSelectedTickets = () => [];
export const _pfComputeConflicts = () => [];
export const _pfBuildConflictsHtml = _noopStr;
export const _pfBuildOrderHtml = _noopStr;
export const _pfUpdateSections = _noop;
export const _pfShowError = _noop;
export const _pfStepperInit = _noop;
export const _pfStepState = _noop;
export const _pfStepperAnimate = _noop;
export const _pfStepperSummary = _noop;
export const smgmtKickoffRun = _noop;
export const smgmtKickoffRetry = _noop;
