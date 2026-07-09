/**
 * Short-TTL client cache for /api/sprint-nav-status (issue #1776).
 *
 * snavRefresh() (30s merged loop) and statusRefresh() (60s status tab) both
 * hit /api/sprint-nav-status for the current repo. A 15s cache means the two
 * callers share the response within the same tick window without a second
 * network round-trip.
 */

const _snavNavStatusCache = {};
const _SNAV_NAV_STATUS_TTL = 15000; // 15 s — shorter than both the 30s and 60s loops

/**
 * Fetch /api/sprint-nav-status with a short-TTL client-side cache.
 *
 * If a cached response for `url` is younger than _SNAV_NAV_STATUS_TTL, return
 * it without a network call. Otherwise fetch, cache on success, and return.
 * Throws on non-ok responses (consistent with the callers' existing .catch()).
 *
 * @param {string} url - full URL including query string
 * @returns {Promise<object>} - parsed JSON response
 */
export async function snavNavStatusFetch(url) {
  const cached = _snavNavStatusCache[url];
  if (cached && Date.now() - cached.ts < _SNAV_NAV_STATUS_TTL) {
    return cached.data;
  }
  const res = await fetch(url);
  if (!res.ok) throw new Error('HTTP ' + res.status);
  const data = await res.json();
  _snavNavStatusCache[url] = { data, ts: Date.now() };
  return data;
}

/**
 * Invalidate the cache for a specific URL (or all entries if no URL given).
 * Useful when a force-refresh is needed after a sprint state change.
 *
 * @param {string} [url] - URL to clear; omit to clear the entire cache
 */
export function snavNavStatusCacheClear(url) {
  if (url) {
    delete _snavNavStatusCache[url];
  } else {
    for (const k of Object.keys(_snavNavStatusCache)) {
      delete _snavNavStatusCache[k];
    }
  }
}
