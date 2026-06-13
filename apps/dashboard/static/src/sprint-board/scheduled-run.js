/* Run-on-schedule toggle for approved sprint cards (issue #863).
 *
 * Renders a per-sprint "Run on schedule" switch that is shown ONLY on the
 * approved/planning card (the Run Sprint state) — see board-render.js, which
 * calls _smgmtSchedToggleHtml() only in that branch (AC2). The toggle defaults
 * off (AC3) and persists through /api/scheduler/sprints. Initial states are
 * hydrated after render from /api/scheduler/sprints so a checkbox reflects the
 * stored value even though the board markup builds before the fetch resolves.
 */

/* global _smgmtRepo, escHtml, _smgmtShowToast */

// label -> bool cache, seeded by _smgmtHydrateSchedToggles().
const _schedMap = {};

export function _smgmtSchedToggleHtml(label) {
  const on = !!_schedMap[label];
  const id = `sched-toggle-${label}`;
  return `<label class="smgmt-sched-toggle" title="Auto-run this sprint at the project's scheduled time">
    <input type="checkbox" id="${escHtml(id)}" ${on ? 'checked' : ''}
      onchange="smgmtToggleRunOnSchedule('${escHtml(label)}', this)"
      aria-label="Run sprint ${escHtml(label)} on schedule">
    <span>Run on schedule</span>
  </label>`;
}

export async function smgmtToggleRunOnSchedule(label, el) {
  const repo = typeof _smgmtRepo === 'function' ? _smgmtRepo() : null;
  if (!repo) return;
  const enabled = !!(el && el.checked);
  // Optimistic — reflect immediately, revert on failure.
  _schedMap[label] = enabled;
  try {
    const res = await fetch('/api/scheduler/sprints', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project: repo, sprint_label: label, enabled }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (e) {
    _schedMap[label] = !enabled;
    if (el) el.checked = !enabled;
    if (typeof _smgmtShowToast === 'function') {
      _smgmtShowToast('Could not update schedule: ' + (e.message || e));
    }
  }
}

export async function _smgmtHydrateSchedToggles(repo) {
  if (!repo) return;
  try {
    const res = await fetch(`/api/scheduler/sprints?project=${encodeURIComponent(repo)}`);
    if (!res.ok) return;
    const data = await res.json();
    const map = data.run_on_schedule || {};
    for (const k of Object.keys(_schedMap)) delete _schedMap[k];
    Object.keys(map).forEach(k => { _schedMap[k] = !!map[k]; });
    // Reflect into any already-rendered checkboxes.
    Object.keys(map).forEach(label => {
      const cb = document.getElementById(`sched-toggle-${label}`);
      if (cb) cb.checked = !!map[label];
    });
  } catch (_) { /* non-fatal — toggles stay at default off */ }
}
