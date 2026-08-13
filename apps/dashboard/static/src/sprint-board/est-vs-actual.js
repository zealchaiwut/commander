/* Estimate-vs-actual — inline expandable section for finished sprints (issue #2264).
 *
 * Fetches the existing /api/sprints/{label}/estimate-vs-actual endpoint and
 * renders a compact per-ticket comparison panel in the finished sprint card,
 * making the data reachable in one click from the project page (AC3).
 *
 * Pure builder: _smgmtEstVsActualSectionHtml
 * Toggle:       _smgmtToggleEstVsActual
 * Loader:       _smgmtLoadEstVsActual
 */

/* global _smgmtRepo, _smgmtFinishedLabels, _smgmtRunningLabels, escHtml */

function _fmtMin(minutes) {
  if (minutes == null) return '—';
  const m = Math.round(minutes);
  return m >= 60 ? `${(Math.abs(minutes) / 60).toFixed(1)}h` : `${m}m`;
}

function _fmtDelta(delta) {
  if (delta == null) return { text: '—', cls: '' };
  const abs = Math.abs(delta);
  const sign = delta < 0 ? '-' : '+';
  const text = `${sign}${abs >= 60 ? (abs / 60).toFixed(1) + 'h' : Math.round(abs) + 'm'}`;
  const cls = delta < -5 ? 'pi-ev-under' : delta > 5 ? 'pi-ev-over' : 'pi-ev-on';
  return { text, cls };
}

/**
 * Build the estimate-vs-actual collapsible section HTML.
 * Pure function — takes label string and the /estimate-vs-actual API response.
 * Returns '' when data is null/empty.
 */
export function _smgmtEstVsActualSectionHtml(label, data) {
  const tickets = (data && data.tickets) || [];
  if (!tickets.length) return '';
  const safeLabel = escHtml(label);
  const jsLabel = "'" + String(label).replace(/\\/g, '\\\\').replace(/'/g, "\\'") + "'";

  const rows = tickets.map((t) => {
    const estStr = t.estimated_size
      ? `${_fmtMin(t.estimated_minutes)} (${escHtml(t.estimated_size)})`
      : _fmtMin(t.estimated_minutes);
    const actStr = _fmtMin(t.actual_elapsed_minutes);
    const { text: deltaText, cls: deltaCls } = _fmtDelta(t.delta_minutes);
    const statusCls = t.status === 'done' ? 'pi-ev-status-done'
      : t.status === 'failed' ? 'pi-ev-status-fail' : 'pi-ev-status-skip';
    return `<div class="pi-ev-row">
      <span class="pi-ev-num">#${t.ticket_id}</span>
      <span class="pi-ev-title" title="${escHtml(t.title || '')}">${escHtml(t.title || '')}</span>
      <span class="pi-ev-est">${estStr}</span>
      <span class="pi-ev-act">${actStr}</span>
      <span class="pi-ev-delta ${deltaCls}">${escHtml(deltaText)}</span>
      <span class="pi-ev-status ${statusCls}">${escHtml(t.status || '—')}</span>
    </div>`;
  }).join('');

  return `<div class="pi-ev-section" id="pi-ev-section-${safeLabel}">
    <button class="pi-ev-toggle-btn" type="button"
            onclick="_smgmtToggleEstVsActual(${jsLabel})"
            aria-expanded="false"
            aria-controls="pi-ev-content-${safeLabel}">
      <i class="ti ti-chart-bar" aria-hidden="true"></i>
      Estimate vs Actual
      <i class="ti ti-chevron-down pi-ev-chevron" aria-hidden="true"></i>
    </button>
    <div class="pi-ev-content" id="pi-ev-content-${safeLabel}" style="display:none">
      <div class="pi-ev-header-row">
        <span class="pi-ev-num">Ticket</span>
        <span class="pi-ev-title">Title</span>
        <span class="pi-ev-est">Est</span>
        <span class="pi-ev-act">Actual</span>
        <span class="pi-ev-delta">Delta</span>
        <span class="pi-ev-status">Status</span>
      </div>
      ${rows}
    </div>
  </div>`;
}

/** Toggle the estimate-vs-actual expanded/collapsed state for a sprint card. */
export function _smgmtToggleEstVsActual(label) {
  const content = document.getElementById(`pi-ev-content-${label}`);
  const btn = document.querySelector(`#pi-ev-section-${label} .pi-ev-toggle-btn`);
  const chevron = document.querySelector(`#pi-ev-section-${label} .pi-ev-chevron`);
  if (!content) return;
  const isExpanded = content.style.display !== 'none';
  content.style.display = isExpanded ? 'none' : 'block';
  if (btn) btn.setAttribute('aria-expanded', String(!isExpanded));
  if (chevron) {
    chevron.classList.toggle('ti-chevron-down', isExpanded);
    chevron.classList.toggle('ti-chevron-up', !isExpanded);
  }
}

/**
 * Async: fetch /estimate-vs-actual for each finished sprint and inject the
 * collapsible section into the pi-ev-${label} slot in the card.
 * Silent on 404 (sprint not yet run or no estimates).
 */
export async function _smgmtLoadEstVsActual(orderedLabels) {
  const repo = _smgmtRepo();
  if (!repo) return;
  await Promise.all(orderedLabels.map(async (label) => {
    if (_smgmtRunningLabels.has(label)) return;
    if (!_smgmtFinishedLabels.has(label)) return;
    const slot = document.getElementById(`pi-ev-${label}`);
    if (!slot || slot.dataset.loaded) return;
    try {
      const resp = await fetch(
        `/api/sprints/${encodeURIComponent(label)}/estimate-vs-actual?project=${encodeURIComponent(repo)}`
      );
      slot.dataset.loaded = '1';
      if (!resp.ok) return; // 404 for unfinished / no-data sprints — silent skip
      const data = await resp.json();
      const html = _smgmtEstVsActualSectionHtml(label, data);
      if (html) slot.innerHTML = html;
    } catch (_) { /* fail silently */ }
  }));
}
