/* Sprint-board delivery-health stat strip (issue #1849).
 *
 * Renders a compact strip — first-pass rate, rework rate, avg sprint duration
 * — in the Board/History header. Shares a single cached fetch with the
 * Analytics metrics pane via window._anlHealthData.
 */

function _fmtPct(rate) { return Math.round((rate || 0) * 100) + '%'; }

function _fmtDur(minutes) {
  minutes = minutes || 0;
  return minutes < 60 ? Math.round(minutes) + 'm' : (minutes / 60).toFixed(1) + 'h';
}

/**
 * Pure builder — returns an HTML string or null when the strip should be hidden.
 * Returns null when there are no completed sprints (AC3).
 */
export function _sHealthBuildHtml(data) {
  const fpr = (data && data.first_pass_rate) || {};
  const rwr = (data && data.rework_rate) || {};
  const thr = (data && data.throughput) || {};
  if ((fpr.total_completed || 0) <= 0) return null;
  const fprPct = _fmtPct(fpr.rate);
  const rwrPct = _fmtPct(rwr.rate);
  const durStr = _fmtDur(thr.avg_sprint_length_minutes);
  return (
    '<span class="shs-stat"><span class="shs-val">' + fprPct + '</span>' +
      '<span class="shs-label">first-pass</span></span>' +
    '<span class="shs-stat"><span class="shs-val">' + rwrPct + '</span>' +
      '<span class="shs-label">rework</span></span>' +
    '<span class="shs-stat"><span class="shs-val">' + durStr + '</span>' +
      '<span class="shs-label">avg sprint</span></span>' +
    '<a class="shs-see-more" href="#" ' +
      'onclick="switchTab(\'metrics\');if(typeof anlShowTab===\'function\')anlShowTab(\'metrics\');return false;">' +
      'See more →</a>'
  );
}

/** Renders the strip into #sprint-health-strip, or hides it if no sprints. */
export function _sHealthStripRender(data) {
  const el = document.getElementById('sprint-health-strip');
  if (!el) return;
  const html = _sHealthBuildHtml(data);
  if (html === null) { el.hidden = true; return; }
  el.innerHTML = html;
  el.hidden = false;
}

/**
 * One-time initialiser — called when the Sprint tab first loads.
 * Skips the fetch if window._anlHealthData is already set by the Analytics
 * pane (AC4: at most one analytics/metrics call per page load).
 */
export function sprintHealthStripInit(slug) {
  if (!slug) return;
  // Reuse data already fetched by anlFetchDeliveryHealth (AC4)
  if (typeof window !== 'undefined' && window._anlHealthData) {
    _sHealthStripRender(window._anlHealthData);
    return;
  }
  // Guard against multiple calls (e.g. loadSprintMgmt called repeatedly)
  if (typeof window !== 'undefined' && window._anlHealthPromise) return;
  const url = '/api/projects/' + encodeURIComponent(slug) + '/analytics/metrics';
  const p = fetch(url)
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (d) {
        if (typeof window !== 'undefined') window._anlHealthData = d;
        _sHealthStripRender(d);
      }
      return d;
    })
    .catch(function () { return null; });
  if (typeof window !== 'undefined') window._anlHealthPromise = p;
}
