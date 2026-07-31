/* URL parsing — extracted from project.html inline script (issue #2038).
 *
 * Two exports:
 *   _parseUrlImpl(pathname, search, hash) — pure function; used by tests.
 *   parseUrl()                           — browser wrapper; reads window.location.
 *
 * index.js exposes parseUrl as window.parseUrl so project.html inline scripts
 * (init, popstate handler) and the bundled tabs.js can call it as a global.
 *
 * Tab mapping (as of issue #2038):
 *   failures     → 'failures'   (was missing — fell through to sprint-mgmt)
 *   brain        → 'brain'      (was missing — fell through to sprint-mgmt)
 *   logs         → removed: server returns 302 → /failures before project.html loads
 *   status       → removed: server returns 302 → /failures before project.html loads
 *   metrics      → removed: server returns 302 → /failures before project.html loads
 *   analytics    → removed: server returns 301 → /failures before project.html loads
 */

const _PATH_RE = /^\/project\/([^/]+)\/?([^/]*)?$/;

/**
 * Map a URL pathname + search string to parsed tab state.
 * Pure — no window dependency — so Node tests can call it directly.
 *
 * @param {string} pathname  - e.g. '/project/commander/failures'
 * @param {string} [search]  - query string (e.g. '?view=board')
 * @returns {{ slug: string|null, tab: string, view: string|null, filter: string|null }}
 */
export function _parseUrlImpl(pathname, search = '') {
  const m = pathname.match(_PATH_RE);
  const _q = new URLSearchParams(search);
  const view = (_q.get('view') || '').toLowerCase() || null;
  const filter = (_q.get('filter') || '').toLowerCase() || null;
  if (!m) return { slug: null, tab: 'sprint-mgmt', view, filter };
  const slug = decodeURIComponent(m[1]);
  const rawTab = m[2] || '';
  // 'sprint' is a deep-link alias for 'sprint-mgmt' (issue #798): the tab was
  // renamed to "Sprint" while the pane id stays pane-sprint-mgmt for compatibility.
  const tab = (rawTab === 'tickets')         ? 'tickets'
            : (rawTab === 'sprint')          ? 'sprint-mgmt'
            : (rawTab === 'bulk-create')     ? 'bulk-create'
            : (rawTab === 'failures')        ? 'failures'
            : (rawTab === 'brain')           ? 'brain'
            : (rawTab === 'settings')        ? 'settings'
            : (rawTab === 'global-settings') ? 'global-settings'
            : 'sprint-mgmt';
  return { slug, tab, view, filter };
}

/**
 * Read window.location and return parsed URL state.
 * Exposed as window.parseUrl by index.js.
 */
export function parseUrl() {
  const loc = window.location;
  return _parseUrlImpl(loc.pathname, loc.search);
}
