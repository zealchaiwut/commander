/* Per-sprint "Reconcile against GitHub" modal.
 *
 * Click on a sprint card → fetch a dry-run preview (GitHub truth vs DB lifecycle
 * + post-sprint checks, all from the zero-quota mirror) → show the diff → Apply
 * writes the DB lifecycle + local state to match GitHub (never modifies GitHub).
 *
 * Self-contained: the modal DOM is built in JS so no scaffold lives in
 * project.html. Reuses globals exposed by the board (loadSprintMgmt, escHtml,
 * _smgmtRepo, _smgmtShowToast, sprintLabelDisplay, _histResetLedgerCache).
 */

/* global _smgmtRepo, sprintLabelDisplay, escHtml, _smgmtShowToast, loadSprintMgmt */

let _recLabel = null;

function _recEsc(s) {
  return (typeof escHtml === 'function') ? escHtml(String(s ?? '')) : String(s ?? '');
}

function _recDisplay(label) {
  return (typeof sprintLabelDisplay === 'function') ? sprintLabelDisplay(label) : label;
}

function _recRemove() {
  const bd = document.getElementById('rec-backdrop');
  if (bd) bd.remove();
  _recLabel = null;
}

export function _recClose() {
  _recRemove();
}

function _recCheckRow(c) {
  const ok = !!c.ok;
  const icon = ok ? 'ti-circle-check' : 'ti-alert-triangle';
  const color = ok ? 'var(--green,#1a7f37)' : 'var(--amber,#9a6700)';
  const name = ({
    summary_issue: 'Summary issue',
    sprint_pr: 'Sprint PR',
    stale_labels: 'Stale labels',
  })[c.name] || c.name;
  return `<div style="display:flex;gap:8px;align-items:flex-start;padding:4px 0;font-size:13px">
      <i class="ti ${icon}" style="color:${color};margin-top:2px"></i>
      <span><b>${_recEsc(name)}</b> — ${_recEsc(c.detail || (ok ? 'ok' : 'needs attention'))}</span>
    </div>`;
}

function _recRender(preview) {
  const body = document.getElementById('rec-body');
  const applyBtn = document.getElementById('rec-apply-btn');
  if (!body) return;

  if (!preview || preview.exists === false) {
    body.innerHTML = `<div style="font-size:13px;color:var(--text-muted)">
      This sprint has no lifecycle row in this dashboard's DB${preview && preview.wrong_project ? ' for this project' : ''}, so there is nothing to reconcile here.
    </div>`;
    if (applyBtn) applyBtn.classList.add('hidden');
    return;
  }

  const dbState = preview.db_state || 'unknown';
  const ghState = preview.github_state || 'unknown';
  const wouldChange = !!preview.would_change;
  const checks = preview.checks || [];
  const allClear = preview.all_clear;

  const stateBlock = wouldChange
    ? `<div style="font-size:13px;margin-bottom:10px">
         <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
           <span>DB: <b style="color:var(--amber,#9a6700)">${_recEsc(dbState)}</b></span>
           <i class="ti ti-arrow-right" aria-hidden="true"></i>
           <span>GitHub truth: <b style="color:var(--green,#1a7f37)">${_recEsc(ghState)}</b></span>
         </div>
         <div style="color:var(--text-muted);margin-top:4px">Apply will set the DB lifecycle to <b>${_recEsc(ghState)}</b>${preview.reason ? ` (${_recEsc(preview.reason)})` : ''}.</div>
       </div>`
    : `<div style="font-size:13px;margin-bottom:10px;color:var(--text-muted)">
         Lifecycle already matches GitHub (<b>${_recEsc(dbState)}</b>). ${allClear === false ? 'Some post-sprint checks below are unresolved.' : 'Nothing to change.'}
       </div>`;

  const checksBlock = checks.length
    ? `<div style="border-top:1px solid var(--border,#d0d7de);padding-top:8px;margin-top:6px">
         <div style="font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px">Post-sprint checks</div>
         ${checks.map(_recCheckRow).join('')}
       </div>`
    : '';

  body.innerHTML = stateBlock + checksBlock;

  if (applyBtn) {
    // Allow Apply when there is a lifecycle change OR unresolved checks to refresh.
    const actionable = wouldChange || allClear === false;
    applyBtn.classList.remove('hidden');
    applyBtn.disabled = !actionable;
    applyBtn.textContent = actionable ? 'Apply' : 'Nothing to apply';
  }
}

export async function smgmtReconcileSprint(label) {
  const repo = (typeof _smgmtRepo === 'function') ? _smgmtRepo() : null;
  if (!repo) return;
  _recLabel = label;

  _recRemove();
  const bd = document.createElement('div');
  bd.id = 'rec-backdrop';
  bd.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:1000;display:flex;align-items:center;justify-content:center';
  bd.innerHTML = `
    <div id="rec-modal" role="dialog" aria-modal="true"
         style="background:var(--card,#fff);color:var(--text,#1f2328);width:min(520px,92vw);max-height:80vh;overflow:auto;border-radius:10px;box-shadow:0 12px 40px rgba(0,0,0,.25);padding:18px 20px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
        <h3 style="margin:0;font-size:15px">Reconcile ${_recEsc(_recDisplay(label))} against GitHub</h3>
        <button type="button" onclick="_recClose()" aria-label="Close"
                style="background:none;border:none;font-size:18px;cursor:pointer;color:var(--text-muted)">&times;</button>
      </div>
      <div id="rec-body" style="min-height:48px">
        <div style="display:flex;gap:8px;align-items:center;color:var(--text-muted);font-size:13px">
          <span class="nt-spinner" aria-hidden="true"></span> Checking GitHub (via mirror)…
        </div>
      </div>
      <div id="rec-error" class="hidden" style="color:var(--red,#cf222e);font-size:13px;margin-top:8px"></div>
      <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px">
        <button type="button" class="rm-btn" onclick="_recClose()">Close</button>
        <button type="button" id="rec-apply-btn" class="rm-btn rm-btn-prim hidden" disabled
                onclick="_recApply()">Apply</button>
      </div>
    </div>`;
  bd.addEventListener('click', (e) => { if (e.target === bd) _recClose(); });
  document.body.appendChild(bd);

  try {
    const res = await fetch(
      `/api/sprints/${encodeURIComponent(label)}/reconcile-preview?project=${encodeURIComponent(repo)}`,
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const preview = await res.json();
    if (_recLabel !== label) return; // modal closed/replaced while loading
    _recRender(preview);
  } catch (e) {
    const errEl = document.getElementById('rec-error');
    if (errEl) { errEl.textContent = 'Failed to load preview: ' + e.message; errEl.classList.remove('hidden'); }
    const bodyEl = document.getElementById('rec-body');
    if (bodyEl) bodyEl.innerHTML = '';
  }
}

export async function _recApply() {
  const repo = (typeof _smgmtRepo === 'function') ? _smgmtRepo() : null;
  const label = _recLabel;
  if (!repo || !label) return;
  const applyBtn = document.getElementById('rec-apply-btn');
  if (applyBtn) { applyBtn.disabled = true; applyBtn.textContent = 'Applying…'; }
  try {
    const res = await fetch(`/api/sprints/${encodeURIComponent(label)}/reconcile`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project: repo, confirmed: true }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const result = await res.json();
    _recClose();
    const msg = result.updated
      ? `Reconciled ${_recDisplay(label)}: ${result.db_state_before} → ${result.db_state_after}`
      : `${_recDisplay(label)} already matches GitHub.`;
    if (typeof _smgmtShowToast === 'function') _smgmtShowToast(msg);
    if (typeof globalThis._histResetLedgerCache === 'function') globalThis._histResetLedgerCache();
    if (typeof loadSprintMgmt === 'function') loadSprintMgmt().catch(() => {});
    if (typeof globalThis._histForceRefresh === 'function') {
      try { globalThis._histForceRefresh(); } catch (_) { /* history pane may be closed */ }
    }
  } catch (e) {
    const errEl = document.getElementById('rec-error');
    if (errEl) { errEl.textContent = 'Apply failed: ' + e.message; errEl.classList.remove('hidden'); }
    if (applyBtn) { applyBtn.disabled = false; applyBtn.textContent = 'Apply'; }
  }
}
