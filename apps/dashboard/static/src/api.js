/**
 * Module-level promise memos for stable, session-scoped API endpoints.
 * Resets on full page reload (in-memory only — no localStorage/sessionStorage).
 * Issue #1779.
 */

let _envPromise = null;
let _versionPromise = null;
let _settingsPromise = null;

export function getEnvironment() {
  if (!_envPromise) {
    _envPromise = fetch('/api/environment')
      .then(r => r.json())
      .catch(err => { _envPromise = null; throw err; });
  }
  return _envPromise;
}

export function getVersion() {
  if (!_versionPromise) {
    _versionPromise = fetch('/api/version', { cache: 'no-store' })
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .catch(err => { _versionPromise = null; throw err; });
  }
  return _versionPromise;
}

export function getSettings() {
  if (!_settingsPromise) {
    _settingsPromise = fetch('/api/settings')
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .catch(err => { _settingsPromise = null; throw err; });
  }
  return _settingsPromise;
}

/** Clear the settings cache so the next getSettings() re-fetches. */
export function invalidateSettings() {
  _settingsPromise = null;
}

/**
 * Test-only: reset all three caches so each test starts from a clean state.
 * Not called in production — only imported by test files.
 */
export function _resetCacheForTesting() {
  _envPromise = null;
  _versionPromise = null;
  _settingsPromise = null;
}
