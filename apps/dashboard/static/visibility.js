/* Standalone visibility-aware interval guard (issue #1775).
 *
 * Plain-script equivalent of static/src/shell/visibility.js for pages that do
 * not load the esbuild bundle (home.html, home-preview.html, diagnostics.html).
 *
 * Exports window.visibilityInterval and patches window.clearInterval so that
 * existing clearInterval(handle) call-sites work without modification.
 */
(function () {
  'use strict';

  const _viHandles = new Map();
  let _viIdSeq = 1000000;
  const _origClear = window.clearInterval.bind(window);

  /**
   * Visibility-aware setInterval. Pauses when the tab is hidden; resumes with
   * an immediate catch-up tick when the tab becomes visible again.
   *
   * @param {Function} fn   - polling callback
   * @param {number} delay  - interval in milliseconds
   * @returns {number}      - opaque handle; pass to clearInterval to cancel
   */
  function visibilityInterval(fn, delay) {
    const fakeId = ++_viIdSeq;
    let realId = null;

    function stop() {
      if (realId === null) return;
      _origClear(realId);
      realId = null;
    }

    function onVisChange() {
      if (document.hidden) {
        stop();
      } else {
        stop();                      // guard against stale realId on double-show
        fn();                        // immediate catch-up tick
        realId = setInterval(fn, delay);
      }
    }

    document.addEventListener('visibilitychange', onVisChange);
    if (!document.hidden) {
      realId = setInterval(fn, delay);
    }

    _viHandles.set(fakeId, { stop: stop, onVisChange: onVisChange });
    return fakeId;
  }

  // Patch clearInterval so existing clearInterval(handle) call-sites work.
  window.clearInterval = function (id) {
    if (_viHandles.has(id)) {
      var h = _viHandles.get(id);
      h.stop();
      document.removeEventListener('visibilitychange', h.onVisChange);
      _viHandles.delete(id);
    } else {
      _origClear(id);
    }
  };

  window.visibilityInterval = visibilityInterval;
}());
