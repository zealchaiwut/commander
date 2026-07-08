/**
 * GitHub device-login poll manager (issue #1779).
 * Manages a single recurring poll for the gh-auth/login/status endpoint.
 * Exported as globals by index.js so project.html can call them directly.
 */

export const GH_AUTH_POLL_INTERVAL_MS = 2000;

let _timer = null;

/**
 * Start (or restart) polling pollFn immediately and then every
 * GH_AUTH_POLL_INTERVAL_MS milliseconds.
 */
export function startGhAuthPoll(pollFn) {
  if (_timer != null) { clearInterval(_timer); _timer = null; }
  pollFn();
  _timer = setInterval(pollFn, GH_AUTH_POLL_INTERVAL_MS);
}

/** Stop the active poll timer, if any. Safe to call when idle. */
export function stopGhAuthPoll() {
  if (_timer != null) { clearInterval(_timer); _timer = null; }
}
