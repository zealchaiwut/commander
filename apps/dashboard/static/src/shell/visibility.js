/* Visibility-aware interval guard (issue #1775).
 *
 * visibilityInterval(fn, delay) — drop-in replacement for setInterval that
 * pauses when the tab is hidden and resumes with an immediate catch-up tick
 * when the tab becomes visible again.
 *
 * Returns a fake numeric ID from a shared registry. After calling
 * installVisibilityGuard() once at startup (which patches window.clearInterval),
 * callers can pass this ID to clearInterval as normal — no existing call-site
 * changes are needed.
 */

/** @type {Map<number, {stop: () => void, onVisChange: () => void}>} */
const _viHandles = new Map();
let _viIdSeq = 1_000_000;

/**
 * Visibility-aware setInterval. Pauses when the tab is hidden; resumes with
 * an immediate catch-up tick when the tab becomes visible again.
 *
 * @param {() => void} fn    - polling callback
 * @param {number} delay     - interval in milliseconds
 * @returns {number}         - opaque handle; pass to clearInterval to cancel
 */
export function visibilityInterval(fn, delay) {
  const fakeId = ++_viIdSeq;
  let realId = null;

  function stop() {
    if (realId === null) return;
    clearInterval(realId);
    realId = null;
  }

  function onVisChange() {
    if (typeof document === 'undefined') return;
    if (document.hidden) {
      stop();
    } else {
      stop();                        // guard against stale realId on double-show
      fn();                          // immediate catch-up tick
      realId = setInterval(fn, delay);
    }
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onVisChange);
    if (!document.hidden) {
      realId = setInterval(fn, delay);
    }
  }

  _viHandles.set(fakeId, { stop, onVisChange });
  return fakeId;
}

/**
 * Patch window.clearInterval to handle visibilityInterval fake IDs. Call once
 * at startup before any pollers are created so existing clearInterval(handle)
 * call-sites work without modification.
 */
export function installVisibilityGuard() {
  if (typeof window === 'undefined') return;
  const _orig = window.clearInterval.bind(window);
  window.clearInterval = (id) => {
    if (_viHandles.has(id)) {
      const { stop, onVisChange } = _viHandles.get(id);
      stop();
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', onVisChange);
      }
      _viHandles.delete(id);
    } else {
      _orig(id);
    }
  };
}
