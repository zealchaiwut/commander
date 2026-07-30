/**
 * Visibility-aware auto-refresh for the Dev Report landing page (issue #2023).
 *
 * Exports:
 *   REPORT_REFRESH_INTERVAL_MS  — default polling interval (20 s)
 *   startDevReportAutoRefresh   — creates the visibility-gated poller; returns a stop fn
 *
 * The function is exported so Node tests can import it directly:
 *
 *   import { startDevReportAutoRefresh } from
 *     '../../apps/dashboard/static/src/home/live-refresh.js';
 *
 * Tests stub globalThis.document and globalThis.setInterval before importing.
 * See tests/frontend/ for the matching behavioral test file.
 */

import { visibilityInterval } from '../shell/visibility.js';

/** Default polling interval — 20 seconds. */
export const REPORT_REFRESH_INTERVAL_MS = 20_000;

/**
 * Start visibility-aware auto-refresh for the Dev Report landing.
 *
 * Calls fetchFn() on each interval tick. The interval pauses while the
 * browser tab is hidden and resumes — with an immediate catch-up tick —
 * when the tab becomes visible again. This matches the behavior of
 * visibilityInterval() in shell/visibility.js.
 *
 * @param {object} opts
 * @param {() => Promise<void>} opts.fetchFn
 *   Callback invoked on each tick to refresh the report. Errors are
 *   swallowed silently so a transient network failure does not break
 *   subsequent ticks.
 * @param {number} [opts.interval]
 *   Polling interval in milliseconds. Defaults to REPORT_REFRESH_INTERVAL_MS.
 * @param {(isoTimestamp: string) => void} [opts.onUpdate]
 *   Called with an ISO-format timestamp string after each successful
 *   fetchFn() invocation. Use this to update a "last updated" indicator.
 * @returns {() => void}  Stop function — clears the interval completely.
 */
export function startDevReportAutoRefresh({
  fetchFn,
  interval = REPORT_REFRESH_INTERVAL_MS,
  onUpdate,
} = {}) {
  const tick = async () => {
    try {
      await fetchFn();
      if (typeof onUpdate === 'function') {
        onUpdate(new Date().toISOString());
      }
    } catch (_) {
      /* swallow — a transient fetch error must not break subsequent ticks */
    }
  };

  const handle = visibilityInterval(tick, interval);
  return () => clearInterval(handle);
}
