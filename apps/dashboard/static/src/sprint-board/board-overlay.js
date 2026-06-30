/* Board move-lock overlay (issue #276 / #797).
 *
 * Shared overlay functions used by bulk-complete-modal.js, history.js, and
 * the AI resolve-conflict flow. Extracted from drag-drop.js so the overlay
 * can survive the removal of the drag-and-drop feature.
 */

/* global _arInterval, _smgmtArStartTicker, _smgmtArStopTicker, _smgmtMoveLock:writable */

import {
  BOARD_OVERLAY_PA_ID,
  mountProgressActivity,
  patchProgressActivity,
  appendProgressActivityLog,
  unmountProgressActivity,
} from "../progress-host.js";

let _smgmtBoardOverlayHasProgress = false;

export function _smgmtBoardLock(message, opts) {
  _smgmtMoveLock = true;
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
  if (_arInterval > 0) _smgmtArStartTicker();
}

/**
 * Settle an operation and wait for operator acknowledgement: keep the overlay
 * open, stop the spinner, show success/error message, and enable a "Done"
 * button. The overlay never auto-dismisses — operator reads the log then Done
 * unlocks the board and runs the optional onDone callback.
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

/** Back-compat error wrapper around _smgmtBoardFinish (kept for existing callers). */
export function _smgmtBoardHalt(message, onDone) {
  _smgmtBoardFinish({ ok: false, message, onDone });
}
