"use strict";
(() => {
  // apps/dashboard/static/src/logpanel.js
  /* Log-panel tokenizer module (issue #796 — first esbuild module).
   *
   * Extracted from static/log-colorize.js (issue #765) as the first real module
   * behind the esbuild pipeline. Source-of-truth for log-line colorization lives
   * here now; the bundle (static/dist/bundle.js) re-exposes these helpers on
   * `window` so project.html keeps its historical global API unchanged.
   *
   * colorizeLogLine() always escapes the raw log text FIRST (no injected markup
   * is ever executed), then tokenizes the escaped string in a single pass:
   *   - `#NNN` issue refs  → clickable GitHub issue chips
   *   - the six known agent names (word-bounded, case-insensitive) → per-agent
   *     color chips (coder=blue, tester=green, reviewer=purple, documenter=amber,
   *     estimator=teal, BA=pink — colors live in CSS classes in project.html).
   *
   * A single combined regex pass guarantees the chip markup we inject is never
   * itself re-tokenized, and keeps tokenization O(n) over a 5 000-line log.
   */

  const AGENT_NAMES = ['coder', 'tester', 'reviewer', 'documenter', 'estimator', 'BA'];

  function escapeLogHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // #NNN issue ref OR a word-bounded, case-insensitive agent name. Run over the
  // ALREADY-ESCAPED string; replace() does not re-scan its own output, so the
  // chip HTML we emit is never re-matched.
  const TOKEN_RE = /(#\d+)|\b(coder|tester|reviewer|documenter|estimator|BA)\b/gi;

  /**
   * Extract the display text from a log line.
   *
   * Lines written by envelope_subprocess_line() are JSON objects with a `.raw`
   * field that contains the original agent output.  All other lines (plain text,
   * structured manager events) are returned as-is.  This preserves pre-migration
   * rendering for old log files while transparently unwrapping new envelopes.
   */
  function extractRaw(text) {
    const s = String(text == null ? '' : text).trim();
    if (s.length === 0 || s[0] !== '{') return s;
    try {
      const obj = JSON.parse(s);
      if (typeof obj.raw === 'string') return obj.raw;
    } catch (_) { /* not JSON — fall through */ }
    return s;
  }

  function colorizeLogLine(text, repo) {
    const escaped = escapeLogHtml(extractRaw(text));
    return escaped.replace(TOKEN_RE, function (match, issue, agent) {
      if (issue) {
        const num = issue.slice(1);
        const href = repo ? 'https://github.com/' + repo + '/issues/' + num : '#';
        return '<a class="log-chip log-chip--issue" href="' + escapeLogHtml(href) +
          '" target="_blank" rel="noopener">' + issue + '</a>';
      }
      const key = agent.toLowerCase();
      return '<span class="log-chip log-chip--agent log-agent-' + key + '">' +
        agent + '</span>';
    });
  }
  // apps/dashboard/static/src/sprint-board/state.js
  /* Sprint-board shared state (issue #797).
   *
   * The board's broadly-shared caches (`_smgmtData`, `_smgmtLiveCache`,
   * `_smgmtRunningLabels`, …) are still declared inline in project.html and are
   * reachable from these modules through the page's shared global lexical
   * environment (classic scripts share one). This module owns only the
   * MODAL/DRAG-local state that was extracted alongside its handlers, seeding it
   * on `window` so the (strict) bundle and the inline page resolve the same
   * bindings by bare name.
   *
   * Idempotent: uses `??=` so a reload/re-eval never clobbers live state.
   */

  // Re-run Sprint modal (issue #512)
  globalThis._rrLabel ??= null;
  globalThis._rrVersionedLabel ??= null;

  // Finish Sprint modal (issue #367 parity)
  globalThis._fsLabel ??= null;
  globalThis._fsPreview ??= null;

  // Run preflight modal (issue #448)
  globalThis._pfCurrentLabel ??= null;
  globalThis._pfCurrentRepo ??= null;
  globalThis._pfState ??= "idle";
  globalThis._pfDagData ??= null;
  globalThis._pfWarnings ??= null;
  globalThis._pfCycle ??= null;
  globalThis._pfFlags ??= null;
  globalThis._pfSelectedIds ??= null;

  // Drag/drop local locks
  globalThis._smgmtMoveLock ??= false;
  globalThis._smgmtGhostNextNum ??= null;

  const SPRINT_BOARD_STATE_KEYS = [
    "_rrLabel", "_rrVersionedLabel",
    "_fsLabel", "_fsPreview",
    "_pfCurrentLabel", "_pfCurrentRepo", "_pfState", "_pfDagData",
    "_pfWarnings", "_pfCycle", "_pfFlags", "_pfSelectedIds",
    "_smgmtMoveLock", "_smgmtGhostNextNum",
  ];
  // apps/dashboard/static/src/sprint-board/board-render.js
  /* board-render module (issue #797) — placeholder; populated during extraction. */
  // apps/dashboard/static/src/sprint-board/drag-drop.js
  /* drag-drop module (issue #797) — placeholder; populated during extraction. */
  // apps/dashboard/static/src/sprint-board/run-controls.js
  /* run-controls module (issue #797) — placeholder; populated during extraction. */
  // apps/dashboard/static/src/sprint-board/finish-modal.js
  /* finish-modal module (issue #797) — placeholder; populated during extraction. */
  // apps/dashboard/static/src/sprint-board/rerun-modal.js
  /* Re-run Sprint modal (issue #512) — extracted from project.html (issue #797).
   *
   * Opens the re-run modal for a finished sprint, previews which tickets will be
   * carried into a new versioned sub-sprint (e.g. sprint-5.1), and confirms the
   * re-run. Shared page helpers and the broadly-shared board caches are reached
   * through the page's global scope; modal-local state (`_rrLabel`,
   * `_rrVersionedLabel`) is seeded on `window` by ./state.js.
   */

  /* global _setBodyInert, _clearBodyInert, _smgmtRepo, sprintLabelDisplay,
     escHtml, _smgmtShowToast, loadSprintMgmt, _smgmtOutcomeCache,
     _rrLabel:writable, _rrVersionedLabel:writable */

  function _rrOpen() {
    _setBodyInert(['rr-backdrop', 'rr-modal']);
    document.getElementById('rr-backdrop').classList.remove('hidden');
    document.getElementById('rr-modal').classList.remove('hidden');
  }

  function _rrClose() {
    document.getElementById('rr-backdrop').classList.add('hidden');
    document.getElementById('rr-modal').classList.add('hidden');
    _clearBodyInert();
    _rrLabel = null;
    _rrVersionedLabel = null;
  }

  function _rrCatClass(cat) {
    if (cat === 'UAT') return 'rr-cat-uat';
    if (cat === 'SIT') return 'rr-cat-sit';
    if (cat === 'needs-rework') return 'rr-cat-rework';
    return 'rr-cat-queued';
  }

  function _rrUpdateState() {
    const checkboxes = document.querySelectorAll('#rr-ticket-list input[type=checkbox]');
    const checked = Array.from(checkboxes).filter(c => c.checked);
    const uatChecked = Array.from(checkboxes).filter(c => c.checked && c.dataset.cat === 'UAT').length;

    const confirmBtn = document.getElementById('rr-confirm-btn');
    if (confirmBtn) confirmBtn.disabled = checked.length === 0;

    const warnEl = document.getElementById('rr-uat-warning');
    if (warnEl) {
      if (uatChecked > 0) {
        warnEl.textContent = `${uatChecked} ticket${uatChecked !== 1 ? 's' : ''} in UAT will be re-tested from scratch.`;
      } else {
        warnEl.textContent = '';
      }
    }
  }

  function _rrSelectAll(checked) {
    document.querySelectorAll('#rr-ticket-list input[type=checkbox]').forEach(cb => { cb.checked = checked; });
    _rrUpdateState();
  }

  async function smgmtRerunSprint(label) {
    const repo = _smgmtRepo();
    if (!repo) return;

    _rrLabel = label;
    _rrVersionedLabel = null;

    document.getElementById('rr-modal-title').textContent = `Re-run ${sprintLabelDisplay(label)}?`;
    document.getElementById('rr-loading').classList.remove('hidden');
    document.getElementById('rr-content').classList.add('hidden');
    document.getElementById('rr-error').classList.add('hidden');
    document.getElementById('rr-error').textContent = '';
    const confirmBtn = document.getElementById('rr-confirm-btn');
    if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Create sprint'; }
    _rrOpen();

    try {
      const res = await fetch(
        `/api/sprints/${encodeURIComponent(label)}/rerun-preview?project=${encodeURIComponent(repo)}`
      );
      if (!res.ok) throw new Error(await res.text());
      const preview = await res.json();

      _rrVersionedLabel = preview.suggested_versioned_label;
      document.getElementById('rr-modal-title').textContent =
        `Re-run ${sprintLabelDisplay(label)} as ${sprintLabelDisplay(_rrVersionedLabel)}?`;
      if (confirmBtn) confirmBtn.textContent = `Create ${sprintLabelDisplay(_rrVersionedLabel)}`;

      const listEl = document.getElementById('rr-ticket-list');
      if ((preview.tickets || []).length === 0) {
        listEl.innerHTML = '<div style="padding:10px;color:var(--text-muted);font-size:13px">No tickets in this sprint.</div>';
      } else {
        listEl.innerHTML = (preview.tickets || []).map(t => {
          const checked = t.checked ? 'checked' : '';
          const catClass = _rrCatClass(t.category);
          return `<label class="rr-ticket-row">
            <input type="checkbox" ${checked} data-issue="${t.number}" data-cat="${escHtml(t.category)}" onchange="_rrUpdateState()">
            <span class="rr-ticket-num">#${t.number}</span>
            <span class="rr-ticket-title" title="${escHtml(t.title)}">${escHtml(t.title)}</span>
            <span class="rr-ticket-cat ${catClass}">${escHtml(t.category)}</span>
          </label>`;
        }).join('');
      }

      document.getElementById('rr-loading').classList.add('hidden');
      document.getElementById('rr-content').classList.remove('hidden');
      _rrUpdateState();
    } catch (e) {
      document.getElementById('rr-loading').classList.add('hidden');
      const errEl = document.getElementById('rr-error');
      errEl.textContent = 'Failed to load preview: ' + e.message;
      errEl.classList.remove('hidden');
    }
  }

  async function _rrConfirm() {
    const repo = _smgmtRepo();
    if (!_rrLabel || !repo) return;

    const checkboxes = Array.from(document.querySelectorAll('#rr-ticket-list input[type=checkbox]'));
    const ticketNumbers = checkboxes.filter(c => c.checked).map(c => parseInt(c.dataset.issue, 10));
    if (ticketNumbers.length === 0) return;

    const confirmBtn = document.getElementById('rr-confirm-btn');
    if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Creating…'; }

    try {
      const res = await fetch(
        `/api/sprints/${encodeURIComponent(_rrLabel)}/rerun?project=${encodeURIComponent(repo)}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ticket_numbers: ticketNumbers, auto_run: false }),
        }
      );
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      _rrClose();
      delete _smgmtOutcomeCache[_rrLabel];
      const subLabel = data.sub_label ? sprintLabelDisplay(data.sub_label) : '';
      if (data.errors && data.errors.length > 0) {
        _smgmtShowToast(`${subLabel} created with errors. Check labels manually.`);
      } else {
        _smgmtShowToast(`${subLabel} created: ${ticketNumbers.length} ticket${ticketNumbers.length !== 1 ? 's' : ''} moved.`);
      }
      await loadSprintMgmt();
    } catch (e) {
      const errEl = document.getElementById('rr-error');
      errEl.textContent = 'Failed to re-run sprint: ' + e.message;
      errEl.classList.remove('hidden');
      if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = _rrVersionedLabel ? `Create ${sprintLabelDisplay(_rrVersionedLabel)}` : 'Create sprint'; }
    }
  }
  // apps/dashboard/static/src/sprint-board/index.js
  /* Sprint-board barrel (issue #797).
   *
   * Imports every extracted concern module and re-attaches its public handlers to
   * the global object so project.html's inline HTML handlers (onclick / ondrag*)
   * keep resolving exactly as they did when the code was inline. Importing the
   * concern modules also runs their side effects; ./state.js seeds modal/drag
   * state on `window`.
   *
   * Concerns: board render · drag/drop · run-controls · finish modal · rerun modal.
   */



  // Re-run modal (issue #512)
  globalThis._rrOpen = _rrOpen;
  globalThis._rrClose = _rrClose;
  globalThis._rrCatClass = _rrCatClass;
  globalThis._rrUpdateState = _rrUpdateState;
  globalThis._rrSelectAll = _rrSelectAll;
  globalThis.smgmtRerunSprint = smgmtRerunSprint;
  globalThis._rrConfirm = _rrConfirm;
  // apps/dashboard/static/src/index.js
  /* Bundle entry point (issue #796).
   *
   * esbuild bundles this into static/dist/bundle.js (IIFE format). The dashboard
   * is served from disk with no build step, so the emitted bundle is committed
   * and loaded directly by project.html.
   *
   * Modules: the log-panel tokenizer (#796) and the sprint-management board
   * (#797 — board render, drag/drop, run-controls, finish modal, rerun modal).
   * Follow-on tickets extract additional self-contained blocks into static/src/
   * and import them here.
   */

  // Preserve the historical global API. project.html and run_browser.html call
  // these helpers on `window` (see static/AGENTS.md "What NOT to Touch"); the
  // bundle keeps that contract intact so the page loads with no ReferenceError.
  const root = typeof window !== 'undefined' ? window : globalThis;
  root.colorizeLogLine = colorizeLogLine;
  root.escapeLogHtml = escapeLogHtml;
  root.extractRaw = extractRaw;
  root.AGENT_NAMES = AGENT_NAMES;
})();
//# sourceMappingURL=bundle.js.map
