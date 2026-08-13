/* Planning insights — inline stale-estimate warnings (issue #2264).
 *
 * Fetches server-computed stale-estimate warnings from the existing
 * /api/sprints/{label}/preflight endpoint and injects them into the card
 * inline, so they are visible without opening a modal (AC1).
 *
 * Pure builder: _smgmtStaleEstimateHtml
 * Loader:       _smgmtLoadPlanningInsights
 */

/* global _smgmtRepo, _smgmtRunningLabels, _smgmtFinishedLabels, escHtml */

/**
 * Build HTML for a stale-estimates row.
 * Pure function — takes the stale_estimates list from the /preflight API.
 * Returns '' when the list is empty or falsy.
 */
export function _smgmtStaleEstimateHtml(stale) {
  if (!stale || stale.length === 0) return '';
  const count = stale.length;
  const ids = escHtml(stale.join(', '));
  return `<div class="pi-stale-row" role="status" aria-live="polite">
    <i class="ti ti-clock-exclamation pi-stale-icon" aria-hidden="true"></i>
    <span class="pi-stale-label">${count} stale estimate${count !== 1 ? 's' : ''}:</span>
    <span class="pi-stale-ids">${ids}</span>
  </div>`;
}

/**
 * Async: fetch /preflight for each non-running, non-finished sprint and inject
 * the stale-estimate row into the pi-stale-${label} slot that _smgmtCardHtml
 * adds to every planning-state card.
 */
export async function _smgmtLoadPlanningInsights(orderedLabels) {
  const repo = _smgmtRepo();
  if (!repo) return;
  await Promise.all(orderedLabels.map(async (label) => {
    if (_smgmtRunningLabels.has(label)) return;
    if (_smgmtFinishedLabels.has(label)) return;
    const slot = document.getElementById(`pi-stale-${label}`);
    if (!slot || slot.dataset.loaded) return;
    try {
      const resp = await fetch(
        `/api/sprints/${encodeURIComponent(label)}/preflight?project=${encodeURIComponent(repo)}`
      );
      if (!resp.ok) return;
      const data = await resp.json();
      const stale = ((data.warnings) || {}).stale_estimates || [];
      slot.dataset.loaded = '1';
      if (!stale.length) return;
      slot.innerHTML = _smgmtStaleEstimateHtml(stale);
    } catch (_) { /* fail silently — stale warnings are informational */ }
  }));
}
