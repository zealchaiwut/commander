/* Plan next sprint (issue #861).
 *
 * Drives the "Plan next sprint" board action: POST /api/sprints/plan-next to
 * draft the upcoming sprint from the active milestone's backlog, then surface
 * the outcome (created / empty / no-milestone / conflict). On a conflict —
 * a pending-sign-off draft already exists — it prompts to replace rather than
 * silently duplicating (AC11).
 *
 * Also decorates the board after each render: sprints whose persisted plan is
 * in pending-sign-off state get a distinct card class + badge (AC9). Kept as a
 * post-render pass so it never touches the large board-render template.
 *
 * Cross-module globals (attached on window by the sprint-board barrel):
 *   _smgmtRepo, _smgmtShowToast, loadSprintMgmt, escHtml.
 */
/* global _smgmtRepo, _smgmtShowToast, loadSprintMgmt */

async function _planNextRequest(repo, replace) {
  let res;
  try {
    res = await fetch('/api/sprints/plan-next', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project: repo, replace }),
    });
  } catch (e) {
    _smgmtShowToast('Plan next sprint failed: ' + e.message);
    return;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    _smgmtShowToast('Plan next sprint failed: ' + (data.detail || ('HTTP ' + res.status)));
    return;
  }
  switch (data.status) {
    case 'ok':
      await loadSprintMgmt();
      _smgmtShowToast(
        `Planned ${data.sprint_label} · ${(data.tickets || []).length} tickets `
        + `(${data.total_minutes}m) — pending sign-off`);
      break;
    case 'no_milestone':
      _smgmtShowToast('No active milestone — nothing to plan.');
      break;
    case 'empty':
      _smgmtShowToast(data.reason || 'No eligible tickets to plan.');
      break;
    case 'conflict':
      if (window.confirm(
          `${data.reason}\n\nReplace the existing draft (${data.existing_label})?`)) {
        await _planNextRequest(repo, true);
      }
      break;
    default:
      _smgmtShowToast('Plan next sprint: unexpected response.');
  }
}

/** Fetch the advisor look-ahead for *repo* and return first entry or null (issue #883). */
async function _fetchLookAheadHint(repo) {
  try {
    const res = await fetch(
      '/api/projects/' + encodeURIComponent(repo) + '/advisor/look-ahead'
    );
    if (!res.ok) return null;
    const data = await res.json();
    const entries = (data && data.look_ahead) || [];
    return entries.length > 0 ? entries[0] : null;
  } catch {
    // Look-ahead is advisory; never block plan-next on a fetch failure.
    return null;
  }
}

export async function smgmtPlanNextSprint() {
  const repo = _smgmtRepo();
  if (!repo) { _smgmtShowToast('No project selected.'); return; }
  const btn = document.getElementById('smgmt-plan-next-btn');
  if (btn) { btn.disabled = true; btn.classList.add('is-loading'); }
  try {
    // Pre-fill with first look-ahead entry when available (AC6, issue #883).
    // AC7: when no look-ahead exists, scopeHint is null and behaviour is unchanged.
    const scopeHint = await _fetchLookAheadHint(repo);
    if (scopeHint !== null) {
      const confirmed = window.prompt(
        'Plan next sprint — advisor look-ahead for default scope:',
        scopeHint
      );
      if (confirmed === null) return; // user cancelled
    }
    await _planNextRequest(repo, false);
  } finally {
    if (btn) { btn.disabled = false; btn.classList.remove('is-loading'); }
  }
}

/** Mark pending-sign-off sprint cards as visually distinct after a render (AC9). */
export async function _smgmtLoadPendingSignoff() {
  const repo = _smgmtRepo();
  if (!repo) return;
  let labels = [];
  try {
    const res = await fetch(
      `/api/sprints/pending-signoff?project=${encodeURIComponent(repo)}`);
    if (!res.ok) return;
    const data = await res.json();
    labels = data.labels || [];
  } catch {
    return;
  }
  for (const label of labels) {
    const card = document.getElementById(`smgmt-card-${label}`);
    if (!card) continue;
    card.classList.add('smgmt-pending-signoff');
    if (card.querySelector('.smgmt-pending-signoff-badge')) continue;
    const header = card.querySelector('.smgmt-sprint-header, .sc-header');
    if (!header) continue;
    const badge = document.createElement('span');
    badge.className = 'smgmt-pending-signoff-badge';
    badge.textContent = 'Pending sign-off';
    badge.setAttribute('title', 'Awaiting sign-off before this sprint goes live');
    header.appendChild(badge);
  }
}
