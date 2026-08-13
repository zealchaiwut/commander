/* Preflight warnings — read-only view (issue #2251).
 *
 * Stripped from run-controls.js: shows quality warnings (unestimated,
 * stale-estimate, missing-AC) without dispatch controls. The existing
 * pf-modal is reused in a read-only mode (no Run Sprint button shown).
 */

/* global _smgmtRepo, escHtml */

/**
 * Build the HTML for preflight warnings chips.
 * Pure function — takes the `warnings` object from the /preflight API.
 * Returns an empty string when there are no warnings.
 */
export function _pfBuildWarningsHtml(pfWarnings) {
  if (!pfWarnings) return '';
  const chips = [];
  const unestimated = pfWarnings.unestimated || [];
  const staleEstimates = pfWarnings.stale_estimates || [];
  const missingAc = pfWarnings.missing_ac || [];
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

/**
 * Open the preflight modal in read-only mode: fetch sprint warnings and show
 * them without the Run Sprint dispatch button.
 */
export function smgmtOpenPreflightWarnings(label) {
  const repo = _smgmtRepo();
  if (!repo) return;
  const backdrop = document.getElementById('pf-backdrop');
  const modal = document.getElementById('pf-modal');
  const titleEl = document.getElementById('pf-modal-title');
  const loading = document.getElementById('pf-loading');
  const content = document.getElementById('pf-content');
  const stepper = document.getElementById('pf-stepper');
  const footer = document.getElementById('pf-footer');
  const errEl = document.getElementById('pf-error');
  if (!backdrop || !modal) return;
  if (titleEl) titleEl.textContent = 'Preflight Warnings';
  if (loading) loading.classList.remove('hidden');
  if (content) { content.innerHTML = ''; content.classList.add('hidden'); }
  if (stepper) stepper.classList.add('hidden');
  if (footer) footer.classList.add('hidden');
  if (errEl) errEl.classList.add('hidden');
  backdrop.classList.remove('hidden');
  modal.classList.remove('hidden');
  fetch(`/api/sprints/${encodeURIComponent(label)}/preflight?project=${encodeURIComponent(repo)}`)
    .then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); })
    .then(data => {
      if (loading) loading.classList.add('hidden');
      if (content) {
        const html = _pfBuildWarningsHtml(data.warnings);
        content.innerHTML = html || '<div class="pf-no-warnings">No warnings found.</div>';
        content.classList.remove('hidden');
      }
    })
    .catch(e => {
      if (loading) loading.classList.add('hidden');
      if (errEl) {
        const msg = document.getElementById('pf-error-msg');
        if (msg) msg.textContent = `Failed to load: ${e.message}`;
        errEl.classList.remove('hidden');
      }
    });
}

/** Close the preflight modal. */
export function _pfClose() {
  const backdrop = document.getElementById('pf-backdrop');
  const modal = document.getElementById('pf-modal');
  if (backdrop) backdrop.classList.add('hidden');
  if (modal) modal.classList.add('hidden');
}

/** No-op stub — retained for project.html event-listener wiring compatibility. */
export function _pfRetry() {}

/** No-op stub — dispatch removed (issue #2251). */
export function _pfConfirm() {}

/** No-op stub — bulk actions removed with dispatch. */
export function _pfBulkClose() {
  const overlay = document.getElementById('pf-bulk-overlay');
  if (overlay) overlay.classList.add('hidden');
}
