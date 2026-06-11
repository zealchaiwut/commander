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
  globalThis._pfSelectedIds ??= new Set();

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
  /* Sprint-board drag & drop (issue #797) — extracted from project.html.
   *
   * Owns: ticket drag start/end + floating pill, drop onto a sprint column,
   * within-sprint reorder (#247), backlog drag/drop, the ghost pane that creates
   * a new sprint when a ticket is dropped below all sprints, multi-select drag
   * (#660) + the selection bar, and the board move-lock overlay (#276).
   *
   * The handlers run verbatim against the page; shared board caches and remaining
   * inline helpers resolve through the page's global scope. `isDragBlocked` /
   * `computeDropPlan` are the DOM-free decision rules the drag/drop smoke test
   * exercises; `isDragBlocked` is wired into the live drop guards.
   */

  /* global _arInterval, _smgmtArStartTicker, _smgmtArStopTicker, _smgmtData, _smgmtRender, _smgmtRepo, _smgmtRunningLabels, _smgmtSelectedIssues, _smgmtShowInlineError, _smgmtShowToast, loadSprintMgmt,
     _smgmtDragTicket:writable, _smgmtGhostNextNum:writable, _smgmtLastSelectedNum:writable, _smgmtMoveLock:writable */

  function isDragBlocked(state) {
    // A drop is blocked while a move is already in flight (issue #276) — mirrors
    // the `if (_smgmtMoveLock) return;` guard wired into the drop handlers below.
    // Tickets in a running sprint are additionally rendered draggable=false.
    return !!(state && state.moveLock);
  }

  function computeDropPlan(dragInfo, targetLabel) {
    // Canonical move-set rule shared by _smgmtDropOnSprint / _smgmtDropOnBacklog:
    //  - a multi-selection drag (issue #660) moves every selected ticket;
    //  - a single drag moves just the dragged ticket;
    //  - dropping a single ticket on its own column is a no-op.
    if (!dragInfo) return { mode: 'none', tickets: [], targetLabel, noop: true };
    if (dragInfo.multi && dragInfo.multi.length > 1) {
      return { mode: 'multi', tickets: dragInfo.multi.slice(), targetLabel, noop: false };
    }
    const noop = dragInfo.fromSprint === targetLabel;
    return { mode: 'single', tickets: noop ? [] : [dragInfo.number], targetLabel, noop };
  }


  function _smgmtUpdateSelectionUI() {
    const count = _smgmtSelectedIssues.size;

    // Inline selection bar (lives inside smgmt-sprint-list as first child)
    let bar = document.getElementById('smgmt-selection-bar');
    const listEl = document.getElementById('smgmt-sprint-list');

    if (count > 0) {
      if (!bar) {
        bar = document.createElement('div');
        bar.id = 'smgmt-selection-bar';
        bar.className = 'smgmt-selection-bar';
        bar.setAttribute('role', 'status');
        bar.setAttribute('aria-live', 'polite');
        // Build interior once
        bar.innerHTML = `
          <i class="ti ti-checkbox"></i>
          <strong class="smgmt-selection-bar-count" id="smgmt-sel-count">0 tickets selected</strong>
          <span style="color:var(--text-muted)">— batch move to a sprint or backlog</span>
          <div class="smgmt-sel-bar-actions">
            <button class="smgmt-selection-bar-delete" id="smgmt-sel-delete-btn"
                    onclick="_smgmtDeleteSelected()">
              <i class="ti ti-trash"></i> Delete
            </button>
            <button class="smgmt-selection-bar-deselect" onclick="_smgmtClearSelection()">
              <i class="ti ti-x"></i> Deselect all
            </button>
            <button class="smgmt-move-to-btn" id="smgmt-move-to-btn"
                    onclick="_smgmtToggleMoveToMenu(event)">
              <i class="ti ti-send"></i> Move to Sprint &#9660;
            </button>
            <div class="smgmt-move-to-menu" id="smgmt-move-to-menu"></div>
          </div>`;
        if (listEl) listEl.insertBefore(bar, listEl.firstChild);
      }
      bar.classList.add('show');
      if (listEl) listEl.classList.add('has-selection');
      const countEl = document.getElementById('smgmt-sel-count');
      if (countEl) countEl.textContent = count === 1 ? '1 ticket selected' : `${count} tickets selected`;
      _smgmtPopulateMoveToMenu();
      // Show Delete button only for a single closed/unplanned issue
      const deleteBtn = document.getElementById('smgmt-sel-delete-btn');
      if (deleteBtn) {
        const showDelete = count === 1 && _smgmtIsDeletableIssue([..._smgmtSelectedIssues][0]);
        deleteBtn.classList.toggle('show', showDelete);
      }
    } else {
      if (bar) bar.classList.remove('show');
      if (listEl) listEl.classList.remove('has-selection');
    }
  }

  function _smgmtPopulateSelectionDropdown() {
    // legacy no-op — replaced by _smgmtPopulateMoveToMenu
  }

  function _smgmtPopulateMoveToMenu() {
    const menu = document.getElementById('smgmt-move-to-menu');
    if (!menu || !_smgmtData) return;

    // Sprints that contain at least one selected ticket — exclude from the target list
    const selectedNums = Array.from(_smgmtSelectedIssues);
    const occupiedSprints = new Set(
      selectedNums.map(n => {
        const iss = (_smgmtData.issues || []).find(i => i.number === n);
        return iss ? iss.sprint : undefined;
      }).filter(s => s != null)
    );

    const sprints = (_smgmtData.sprints || []).sort((a, b) => a - b);
    let html = '';
    sprints.forEach(n => {
      if (occupiedSprints.has(n)) return;
      html += `<button class="smgmt-move-to-item" onclick="_smgmtMoveSelectedTo('sprint-${n}');_smgmtCloseMoveToMenu()">Sprint ${n}</button>`;
    });
    html += `<button class="smgmt-move-to-item" onclick="_smgmtMoveSelectedTo('backlog');_smgmtCloseMoveToMenu()">Backlog (no sprint)</button>`;
    menu.innerHTML = html || '<span style="display:block;padding:8px 14px;font-size:12px;color:var(--text-muted)">No other sprints available</span>';
  }

  function _smgmtToggleMoveToMenu(event) {
    event.stopPropagation();
    const menu = document.getElementById('smgmt-move-to-menu');
    if (!menu) return;
    _smgmtPopulateMoveToMenu();
    menu.classList.toggle('open');
  }

  function _smgmtCloseMoveToMenu() {
    const menu = document.getElementById('smgmt-move-to-menu');
    if (menu) menu.classList.remove('open');
  }

  function _smgmtClearSelection() {
    _smgmtSelectedIssues.forEach(num => {
      const el = document.getElementById(`smgmt-ticket-${num}`);
      if (el) {
        el.classList.remove('is-selected');
        const cb = el.querySelector('.smgmt-ticket-cb');
        if (cb) cb.checked = false;
      }
    });
    _smgmtSelectedIssues.clear();
    _smgmtUpdateSelectionUI();
  }

  function _smgmtSetSelected(number, selected) {
    if (selected) _smgmtSelectedIssues.add(number);
    else _smgmtSelectedIssues.delete(number);
    const el = document.getElementById(`smgmt-ticket-${number}`);
    if (el) {
      el.classList.toggle('is-selected', selected);
      const cb = el.querySelector('.smgmt-ticket-cb');
      if (cb) cb.checked = selected;
    }
  }

  function _smgmtToggleSelect(number, checked) {
    _smgmtSetSelected(number, checked);
    _smgmtLastSelectedNum = checked ? number : null;
    _smgmtUpdateSelectionUI();
  }

  function _smgmtRowClick(event, number, label) {
    const container = label
      ? document.getElementById(`smgmt-tickets-${label}`)
      : document.getElementById('smgmt-backlog-tickets');

    if (event.shiftKey && _smgmtLastSelectedNum != null && container) {
      const nums = Array.from(container.querySelectorAll('.smgmt-ticket[data-issue]'))
        .map(r => parseInt(r.dataset.issue, 10));
      const a = nums.indexOf(_smgmtLastSelectedNum);
      const b = nums.indexOf(number);
      if (a !== -1 && b !== -1) {
        const [lo, hi] = a <= b ? [a, b] : [b, a];
        for (let i = lo; i <= hi; i++) _smgmtSetSelected(nums[i], true);
        _smgmtLastSelectedNum = number;
        _smgmtUpdateSelectionUI();
        const sel = window.getSelection && window.getSelection();
        if (sel) sel.removeAllRanges();  // clear the text highlight shift-click makes
        return;
      }
    }

    if (event.ctrlKey || event.metaKey) {
      // Ctrl/Cmd+click: toggle this ticket without clearing other selections.
      const nowSelected = !_smgmtSelectedIssues.has(number);
      _smgmtSetSelected(number, nowSelected);
      _smgmtLastSelectedNum = nowSelected ? number : null;
      _smgmtUpdateSelectionUI();
      return;
    }

    // Plain click: toggle this ticket on/off.
    const nowSelected = !_smgmtSelectedIssues.has(number);
    _smgmtSetSelected(number, nowSelected);
    _smgmtLastSelectedNum = nowSelected ? number : null;
    _smgmtUpdateSelectionUI();
  }

  function _smgmtIsDeletableIssue(num) {
    if (!_smgmtData) return false;
    const iss = _smgmtData.issues.find(i => i.number === num);
    if (!iss) return false;
    return iss.status === 'done' || iss.sprint === null;
  }

  async function _smgmtDeleteSelected() {
    if (_smgmtSelectedIssues.size !== 1) return;
    const num = [..._smgmtSelectedIssues][0];
    const repo = _smgmtRepo();
    if (!repo) return;
    const iss = _smgmtData?.issues.find(i => i.number === num);
    const label = iss ? `#${num}: ${iss.title}` : `#${num}`;
    if (!confirm(`Delete ${label}?\n\nThis will close the issue on GitHub. This cannot be undone.`)) return;
    // Optimistic UI: remove from local data and re-render
    if (_smgmtData) _smgmtData.issues = _smgmtData.issues.filter(i => i.number !== num);
    _smgmtClearSelection();
    _smgmtRender(_smgmtData);
    _smgmtBoardLock(`Deleting #${num}…`);
    try {
      const res = await fetch(`/api/issues/${num}/close?repo=${encodeURIComponent(repo)}`, {
        method: 'POST',
      });
      if (!res.ok) throw new Error(await res.text());
      _smgmtShowToast(`Issue #${num} closed.`);
    } catch (e) {
      alert('Failed to delete issue: ' + e.message);
      await loadSprintMgmt();
    } finally {
      _smgmtBoardUnlock();
    }
  }

  async function _smgmtMoveSelectedTo(targetLabel) {
    if (!targetLabel || _smgmtSelectedIssues.size === 0) return;
    const repo = _smgmtRepo();
    if (!repo) return;

    const nums = Array.from(_smgmtSelectedIssues);
    const changes = nums.map(n => ({ issue_num: n, sprint_label: targetLabel }));
    const dest = targetLabel === 'backlog' ? 'Backlog' : `Sprint ${targetLabel.split('-')[1]}`;

    // Optimistic UI update
    if (_smgmtData) {
      const targetNum = targetLabel === 'backlog' ? null : parseInt(targetLabel.split('-')[1], 10);
      nums.forEach(n => {
        const iss = _smgmtData.issues.find(i => i.number === n);
        if (iss) iss.sprint = targetNum;
      });
      _smgmtClearSelection();
      _smgmtRender(_smgmtData);
    }

    _smgmtBoardLock(`Moving ${nums.length} ticket${nums.length !== 1 ? 's' : ''} to ${dest}…`);
    try {
      const res = await fetch('/api/sprints/batch-labels', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ changes, project: repo }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      if (data.failed > 0 && data.errors && data.errors.length > 0) {
        _smgmtShowInlineError(`${data.failed} ticket${data.failed !== 1 ? 's' : ''} failed to move:\n${data.errors.join('\n')}`);
      } else if (data.applied > 0) {
        _smgmtShowToast(`Moved ${data.applied} ticket${data.applied !== 1 ? 's' : ''} to ${dest}.`);
      }
      await loadSprintMgmt();
    } catch (e) {
      _smgmtShowToast('Failed to move tickets: ' + e.message);
      await loadSprintMgmt();
    } finally {
      _smgmtBoardUnlock();
    }
  }

  function _smgmtTicketDragStart(event, issueNum, fromSprint) {
    // Suppress drag while an inline rename is active on the source sprint
    if (fromSprint) {
      const card = document.getElementById(`smgmt-card-${fromSprint}`);
      if (card && card.querySelector('.smgmt-rename-wrap')) {
        event.preventDefault();
        return;
      }
    }
    const isChecked = _smgmtSelectedIssues.has(issueNum);

    if (isChecked && _smgmtSelectedIssues.size > 1) {
      // Multi-ticket drag: pack all selected issue numbers
      const nums = Array.from(_smgmtSelectedIssues);
      const sprints = new Set(nums.map(n => {
        const iss = (_smgmtData?.issues || []).find(i => i.number === n);
        return iss ? iss.sprint : null;
      }));
      _smgmtDragTicket = {
        number: issueNum,
        fromSprint: fromSprint || null,
        multi: nums,
        multiSprints: sprints.size,
      };
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', nums.join(','));

      // Show floating pill
      const pill = document.getElementById('smgmt-drag-pill');
      if (pill) {
        const label = sprints.size > 1
          ? `Moving ${nums.length} tickets from ${sprints.size} sprints`
          : `Moving ${nums.length} tickets`;
        pill.textContent = label;
        pill.style.top = (event.clientY - 20) + 'px';
        pill.style.left = (event.clientX + 12) + 'px';
      }
      setTimeout(() => {
        nums.forEach(n => {
          const el = document.getElementById(`smgmt-ticket-${n}`);
          if (el) el.classList.add('dragging-ticket');
        });
      }, 0);
    } else {
      // Single-ticket drag (unchecked row or single selection)
      _smgmtDragTicket = { number: issueNum, fromSprint: fromSprint || null, multi: null };
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', String(issueNum));
      const el = document.getElementById(`smgmt-ticket-${issueNum}`);
      if (el) setTimeout(() => el.classList.add('dragging-ticket'), 0);
    }
    // Show ghost pane (running-lock means draggable=false tickets can't trigger this)
    _smgmtGhostShow();
  }

  function _smgmtDragMovePill(event) {
    if (_smgmtDragTicket?.multi) {
      const pill = document.getElementById('smgmt-drag-pill');
      if (pill && pill.textContent) {
        pill.style.top = (event.clientY - 20) + 'px';
        pill.style.left = (event.clientX + 12) + 'px';
      }
    }
  }

  function _smgmtGhostComputeNextFree() {
    if (_smgmtData && Number.isInteger(_smgmtData.placeholder_sprint)) {
      return _smgmtData.placeholder_sprint;
    }
    const nums = (_smgmtData?.sprints || []).map(Number).filter(n => !isNaN(n));
    return nums.length ? Math.max(...nums) + 1 : 1;
  }

  function _smgmtGhostShow() {
    if (_smgmtRunningLabels.size > 0) {
      showToast('Cannot create new sprint while one is running.', 'warning');
      return;
    }
    _smgmtGhostNextNum = _smgmtGhostComputeNextFree();
    const ghost = document.getElementById('smgmt-ghost-pane');
    const titleEl = document.getElementById('smgmt-ghost-title');
    const subEl = document.getElementById('smgmt-ghost-sub');
    if (!ghost) return;

    titleEl.textContent = `Drop here to create Sprint ${_smgmtGhostNextNum}`;
    subEl.textContent = 'next sprint number';

    ghost.classList.add('ghost-visible');
  }

  function _smgmtGhostHide() {
    const ghost = document.getElementById('smgmt-ghost-pane');
    if (!ghost) return;
    ghost.classList.remove('ghost-visible', 'ghost-hot');
    _smgmtGhostNextNum = null;
  }

  function _smgmtGhostDragOver(event) {
    if (!_smgmtDragTicket) return;
    if (_smgmtRunningLabels.size > 0) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    const ghost = document.getElementById('smgmt-ghost-pane');
    if (!ghost) return;
    const titleEl = document.getElementById('smgmt-ghost-title');
    const subEl = document.getElementById('smgmt-ghost-sub');
    ghost.classList.add('ghost-hot');
    if (titleEl) titleEl.textContent = `Release to create Sprint ${_smgmtGhostNextNum}`;
    if (subEl) subEl.textContent = "you'll be asked to confirm";
  }

  function _smgmtGhostDragLeave(event) {
    const ghost = document.getElementById('smgmt-ghost-pane');
    if (!ghost) return;
    if (!ghost.contains(event.relatedTarget)) {
      ghost.classList.remove('ghost-hot');
      const titleEl = document.getElementById('smgmt-ghost-title');
      const subEl = document.getElementById('smgmt-ghost-sub');
      if (titleEl) titleEl.textContent = `Drop here to create Sprint ${_smgmtGhostNextNum}`;
      // Restore sub-text
      const existing = new Set((_smgmtData?.sprints || []).map(n => Number(n)));
      const skipped = [];
      for (let i = 1; i < _smgmtGhostNextNum; i++) {
        if (!existing.has(i)) skipped.push(i);
      }
      if (subEl) subEl.textContent = skipped.length > 0
        ? `next free number · skipped empty ${skipped.map(s => `Sprint ${s}`).join(', ')}`
        : 'next free number';
    }
  }

  async function _smgmtGhostDrop(event) {
    event.preventDefault();
    if (!_smgmtDragTicket) return;
    if (_smgmtRunningLabels.size > 0) return;
    const dragInfo = _smgmtDragTicket;
    const nextNum = _smgmtGhostNextNum;
    _smgmtGhostHide();

    // Multi-ticket drag to ghost: not supported per spec — treat as no-op
    if (dragInfo.multi && dragInfo.multi.length > 1) {
      // Leave _smgmtDragTicket for dragend cleanup
      return;
    }

    // Clear dragging-ticket class manually (dragend may fire after we null this)
    const dragEl = document.getElementById(`smgmt-ticket-${dragInfo.number}`);
    if (dragEl) dragEl.classList.remove('dragging-ticket');
    _smgmtDragTicket = null;

    if (nextNum == null) return;
    const repo = _smgmtRepo();
    if (!repo) return;

    // Populate and open confirm modal
    const sprintLabel = `sprint-${nextNum}`;
    const issue = (_smgmtData?.issues || []).find(i => i.number === dragInfo.number);
    const fromLabel = dragInfo.fromSprint || 'backlog';

    document.getElementById('gc-sprint-name').textContent = sprintLabel;
    document.getElementById('gc-ticket-info').textContent =
      issue ? `#${issue.number} — ${issue.title}` : `#${dragInfo.number}`;
    document.getElementById('gc-source-pane').textContent =
      fromLabel === 'backlog' ? 'Backlog' : `Sprint ${fromLabel.replace('sprint-', '')}`;

    const confirmBtn = document.getElementById('gc-confirm-btn');
    confirmBtn.textContent = `Create ${sprintLabel} & move`;
    confirmBtn.disabled = false;

    const errEl = document.getElementById('gc-error');
    errEl.textContent = '';
    errEl.classList.add('hidden');

    // Store drag state for confirm handler
    document.getElementById('gc-modal').dataset.issueNum = String(dragInfo.number);
    document.getElementById('gc-modal').dataset.fromSprint = fromLabel;
    document.getElementById('gc-modal').dataset.sprintNum = String(nextNum);
    document.getElementById('gc-modal').dataset.repo = repo;

    document.getElementById('gc-backdrop').classList.remove('hidden');
    document.getElementById('gc-modal').classList.remove('hidden');
    confirmBtn.focus();
  }

  function _gcClose() {
    document.getElementById('gc-backdrop').classList.add('hidden');
    document.getElementById('gc-modal').classList.add('hidden');
  }

  async function _gcConfirm() {
    const modal = document.getElementById('gc-modal');
    const issueNum = parseInt(modal.dataset.issueNum, 10);
    const sprintNum = parseInt(modal.dataset.sprintNum, 10);
    const fromSprint = modal.dataset.fromSprint;
    const repo = modal.dataset.repo;
    const sprintLabel = `sprint-${sprintNum}`;

    const confirmBtn = document.getElementById('gc-confirm-btn');
    const errEl = document.getElementById('gc-error');
    confirmBtn.disabled = true;
    confirmBtn.textContent = 'Creating…';
    errEl.classList.add('hidden');

    try {
      // Step 1: create the sprint label (409 = already exists, safe to continue)
      const createRes = await fetch('/api/sprints/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project: repo, sprint_number: sprintNum }),
      });
      if (!createRes.ok && createRes.status !== 409) {
        const d = await createRes.json().catch(() => ({}));
        throw new Error(d.detail || 'HTTP ' + createRes.status);
      }

      // Step 2: move the ticket
      const moveRes = await fetch(`/api/issues/${issueNum}/sprint-label`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sprint_label: sprintLabel, project: repo }),
      });
      if (!moveRes.ok) {
        const d = await moveRes.json().catch(() => ({}));
        throw new Error(d.detail || 'HTTP ' + moveRes.status);
      }

      // Success — close modal and reload board
      _gcClose();
      await loadSprintMgmt();
    } catch (e) {
      errEl.textContent = `Failed: ${e.message}`;
      errEl.classList.remove('hidden');
      confirmBtn.disabled = false;
      confirmBtn.textContent = `Create ${sprintLabel} & move`;
    }
  }

  function _smgmtTicketDragEnd(event) {
    if (_smgmtDragTicket) {
      if (_smgmtDragTicket.multi) {
        _smgmtDragTicket.multi.forEach(n => {
          const el = document.getElementById(`smgmt-ticket-${n}`);
          if (el) el.classList.remove('dragging-ticket');
        });
      } else {
        const el = document.getElementById(`smgmt-ticket-${_smgmtDragTicket.number}`);
        if (el) el.classList.remove('dragging-ticket');
      }
    }
    // Hide drag pill and ghost pane
    const pill = document.getElementById('smgmt-drag-pill');
    if (pill) { pill.style.top = '-100px'; pill.style.left = '-100px'; pill.textContent = ''; }
    _smgmtGhostHide();
    _smgmtDragTicket = null;
    document.querySelectorAll('.smgmt-sprint-card').forEach(el => el.classList.remove('drag-over-sprint'));
    document.getElementById('smgmt-backlog-pane')?.classList.remove('drag-over-backlog');
  }

  function _smgmtDragOver(event, sprintLabel) {
    if (_smgmtDragTicket) {
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      document.querySelectorAll('.smgmt-sprint-card').forEach(b => b.classList.remove('drag-over-sprint'));
      document.getElementById('smgmt-backlog-pane')?.classList.remove('drag-over-backlog');
      const target = document.getElementById(`smgmt-card-${sprintLabel}`);
      if (target) target.classList.add('drag-over-sprint');
    }
  }

  function _smgmtDragLeave(event) {
    if (event.currentTarget && !event.currentTarget.contains(event.relatedTarget)) {
      event.currentTarget.classList.remove('drag-over-sprint');
    }
  }

  async function _smgmtDropOnSprint(event, targetLabel) {
    event.preventDefault();
    document.querySelectorAll('.smgmt-sprint-card').forEach(el => el.classList.remove('drag-over-sprint'));
    document.getElementById('smgmt-backlog-pane')?.classList.remove('drag-over-backlog');

    // Block concurrent moves (issue #276)
    if (isDragBlocked({ moveLock: _smgmtMoveLock })) return;
    if (!_smgmtDragTicket) return;
    const dragInfo = _smgmtDragTicket;
    _smgmtDragTicket = null;

    const repo = _smgmtRepo();
    if (!repo) return;

    if (dragInfo.multi && dragInfo.multi.length > 1) {
      // Multi-ticket drop
      const nums = dragInfo.multi;
      const targetNum = targetLabel ? parseInt(targetLabel.split('-')[1], 10) : null;

      // Optimistic update
      if (_smgmtData) {
        nums.forEach(n => {
          const iss = _smgmtData.issues.find(i => i.number === n);
          if (iss) iss.sprint = targetNum;
        });
      }
      // Clear selection before render
      _smgmtClearSelection();
      if (_smgmtData) _smgmtRender(_smgmtData);

      const changes = nums.map(n => ({ issue_num: n, sprint_label: targetLabel || 'backlog' }));
      _smgmtBoardLock();
      try {
        const res = await fetch('/api/sprints/batch-labels', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ changes, project: repo }),
        });
        if (!res.ok) throw new Error(await res.text());
        await loadSprintMgmt();
      } catch (e) {
        alert(`Failed to move tickets: ${e.message}`);
        await loadSprintMgmt();
      } finally {
        _smgmtBoardUnlock();
      }
    } else {
      // Single-ticket drop
      const { number, fromSprint } = dragInfo;
      if (fromSprint === targetLabel) return;

      const targetNum = targetLabel ? parseInt(targetLabel.split('-')[1], 10) : null;

      // Optimistic update
      if (_smgmtData) {
        const iss = _smgmtData.issues.find(i => i.number === number);
        if (iss) iss.sprint = targetNum;
        _smgmtRender(_smgmtData);
      }
      // Clear selection after any drag completes
      _smgmtClearSelection();

      _smgmtBoardLock();
      try {
        const res = await fetch(`/api/issues/${number}/sprint-label`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sprint_label: targetLabel || 'backlog', project: repo }),
        });
        if (!res.ok) throw new Error(await res.text());
        await loadSprintMgmt();
      } catch (e) {
        // Rollback optimistic update
        if (_smgmtData) {
          const iss = _smgmtData.issues.find(i => i.number === number);
          if (iss) iss.sprint = fromSprint ? parseInt(fromSprint.split('-')[1], 10) : null;
          _smgmtRender(_smgmtData);
        }
        alert(`Failed to move ticket #${number}: ${e.message}`);
      } finally {
        _smgmtBoardUnlock();
      }
    }
  }

  function _smgmtTicketReorderDragOver(event) {
    if (!_smgmtDragTicket || (_smgmtDragTicket.multi && _smgmtDragTicket.multi.length > 1)) return;
    const target = event.currentTarget;
    const targetSprint = target.dataset.sprint;
    const dragSprint = _smgmtDragTicket ? _smgmtDragTicket.fromSprint : null;
    if (targetSprint !== dragSprint) return; // cross-sprint moves handled by _smgmtDropOnSprint
    event.preventDefault();
    event.stopPropagation();
    const rect = target.getBoundingClientRect();
    const midY = rect.top + rect.height / 2;
    target.classList.remove('drag-before', 'drag-after');
    target.classList.add(event.clientY < midY ? 'drag-before' : 'drag-after');
  }

  function _smgmtTicketReorderDragLeave(event) {
    event.currentTarget.classList.remove('drag-before', 'drag-after');
  }

  async function _smgmtTicketReorderDrop(event, targetIssue, sprintLabel) {
    if (!_smgmtDragTicket || (_smgmtDragTicket.multi && _smgmtDragTicket.multi.length > 1)) return;
    const dragInfo = _smgmtDragTicket;
    if (dragInfo.fromSprint !== sprintLabel) return; // cross-sprint handled elsewhere
    const dragIssue = dragInfo.number;
    if (dragIssue === targetIssue) {
      event.currentTarget.classList.remove('drag-before', 'drag-after');
      return;
    }
    event.preventDefault();
    event.stopPropagation();

    const rect = event.currentTarget.getBoundingClientRect();
    const insertAfter = event.clientY >= rect.top + rect.height / 2;
    event.currentTarget.classList.remove('drag-before', 'drag-after');

    const repo = _smgmtRepo();
    if (!repo || !_smgmtData) return;

    // Build new order from current DOM positions.
    const container = document.getElementById(`smgmt-tickets-${sprintLabel}`);
    if (!container) return;
    const rows = Array.from(container.querySelectorAll('.smgmt-ticket[data-issue]'));
    let order = rows.map(r => parseInt(r.dataset.issue, 10)).filter(n => !isNaN(n));
    order = order.filter(n => n !== dragIssue);
    const insertIdx = order.indexOf(targetIssue) + (insertAfter ? 1 : 0);
    order.splice(insertIdx, 0, dragIssue);

    _smgmtDragTicket = null;
    try {
      const res = await fetch(
        `/api/sprints/${encodeURIComponent(sprintLabel)}/plan?project=${encodeURIComponent(repo)}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(order),
        }
      );
      if (!res.ok) throw new Error(await res.text());
      await loadSprintMgmt();
    } catch (e) {
      alert(`Failed to reorder tickets: ${e.message}`);
      await loadSprintMgmt();
    }
  }

  function _smgmtBacklogTicketDragStart(event, issueNum) {
    const isChecked = _smgmtSelectedIssues.has(issueNum);

    if (isChecked && _smgmtSelectedIssues.size > 1) {
      // Multi-ticket drag from backlog: pack all selected issue numbers
      const nums = Array.from(_smgmtSelectedIssues);
      _smgmtDragTicket = { number: issueNum, fromSprint: null, multi: nums, multiSprints: 1 };
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', nums.join(','));

      const pill = document.getElementById('smgmt-drag-pill');
      if (pill) {
        pill.textContent = `Moving ${nums.length} tickets`;
        pill.style.top = (event.clientY - 20) + 'px';
        pill.style.left = (event.clientX + 12) + 'px';
      }
      setTimeout(() => {
        nums.forEach(n => {
          const el = document.getElementById(`smgmt-ticket-${n}`);
          if (el) el.classList.add('dragging-ticket');
        });
      }, 0);
    } else {
      // Single-ticket drag (unchecked row or single selection)
      _smgmtDragTicket = { number: issueNum, fromSprint: null, multi: null };
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', String(issueNum));
      const el = document.getElementById(`smgmt-ticket-${issueNum}`);
      if (el) setTimeout(() => el.classList.add('dragging-ticket'), 0);
    }
    // Show ghost pane
    _smgmtGhostShow();
  }

  function _smgmtBacklogDragOver(event) {
    if (_smgmtDragTicket && _smgmtDragTicket.fromSprint !== null) {
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
      document.getElementById('smgmt-backlog-pane')?.classList.add('drag-over-backlog');
    }
  }

  function _smgmtBacklogDragLeave(event) {
    const pane = document.getElementById('smgmt-backlog-pane');
    if (pane && !pane.contains(event.relatedTarget)) {
      pane.classList.remove('drag-over-backlog');
    }
  }

  async function _smgmtDropOnBacklog(event) {
    event.preventDefault();
    document.getElementById('smgmt-backlog-pane')?.classList.remove('drag-over-backlog');

    // Block concurrent moves (issue #276)
    if (isDragBlocked({ moveLock: _smgmtMoveLock })) return;
    if (!_smgmtDragTicket) return;
    const dragInfo = _smgmtDragTicket;
    _smgmtDragTicket = null;

    // If dragging from backlog back to backlog, no-op
    if (!dragInfo.fromSprint) return;

    const repo = _smgmtRepo();
    if (!repo) return;

    if (dragInfo.multi && dragInfo.multi.length > 1) {
      const nums = dragInfo.multi;
      if (_smgmtData) {
        nums.forEach(n => {
          const iss = _smgmtData.issues.find(i => i.number === n);
          if (iss) iss.sprint = null;
        });
      }
      _smgmtClearSelection();
      if (_smgmtData) _smgmtRender(_smgmtData);

      const changes = nums.map(n => ({ issue_num: n, sprint_label: 'backlog' }));
      _smgmtBoardLock();
      try {
        const res = await fetch('/api/sprints/batch-labels', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ changes, project: repo }),
        });
        if (!res.ok) throw new Error(await res.text());
        await loadSprintMgmt();
      } catch (e) {
        alert(`Failed to move tickets to backlog: ${e.message}`);
        await loadSprintMgmt();
      } finally {
        _smgmtBoardUnlock();
      }
    } else {
      const { number, fromSprint } = dragInfo;

      // Optimistic update
      if (_smgmtData) {
        const iss = _smgmtData.issues.find(i => i.number === number);
        if (iss) iss.sprint = null;
        _smgmtRender(_smgmtData);
      }
      _smgmtClearSelection();

      _smgmtBoardLock();
      try {
        const res = await fetch(`/api/issues/${number}/sprint-label`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sprint_label: 'backlog', project: repo }),
        });
        if (!res.ok) throw new Error(await res.text());
        await loadSprintMgmt();
      } catch (e) {
        // Rollback
        if (_smgmtData) {
          const iss = _smgmtData.issues.find(i => i.number === number);
          if (iss) iss.sprint = fromSprint ? parseInt(fromSprint.split('-')[1], 10) : null;
          _smgmtRender(_smgmtData);
        }
        alert(`Failed to move ticket #${number} to backlog: ${e.message}`);
      } finally {
        _smgmtBoardUnlock();
      }
    }
  }

  function _smgmtBoardLock(message) {
    _smgmtMoveLock = true;
    // Pause the auto-refresh ticker without changing the user's chosen interval
    _smgmtArStopTicker();
    const overlay = document.getElementById('smgmt-move-overlay');
    const msgEl   = document.getElementById('smgmt-move-overlay-msg');
    const text    = message || 'Moving…';
    if (msgEl) msgEl.textContent = text;
    if (overlay) {
      overlay.setAttribute('aria-label', text.replace(/…$/, '') + ', please wait');
      overlay.classList.add('active');
    }
  }

  function _smgmtBoardUnlock() {
    _smgmtMoveLock = false;
    const overlay = document.getElementById('smgmt-move-overlay');
    if (overlay) overlay.classList.remove('active');
    // Resume auto-refresh if an interval is selected
    if (_arInterval > 0) _smgmtArStartTicker();
  }
  // apps/dashboard/static/src/sprint-board/run-controls.js
  /* Run Sprint controls + preflight modal (issue #448) — extracted from
   * project.html (issue #797). Covers the Run / Cancel actions and the full
   * preflight modal: DAG render, dependency order, conflict + mis-sizing flags,
   * and the confirm-to-dispatch flow. Page helpers and broadly-shared board
   * caches resolve through the page's global scope; preflight state (`_pf*`) is
   * seeded on `window` by ./state.js.
   */

  /* global _smgmtRepo, _smgmtShowToast, escHtml, sprintLabelDisplay, loadSprintMgmt,
     _pfCurrentLabel:writable, _pfCurrentRepo:writable, _pfState:writable,
     _pfDagData:writable, _pfWarnings:writable, _pfCycle:writable,
     _pfFlags:writable, _pfSelectedIds:writable */

  function smgmtRunBlockedToast() {
    _smgmtShowToast('Another sprint is running — wait for it to finish or cancel it');
  }

  function smgmtRunSprint(label) {
    _pfOpen(label);
  }

  async function smgmtCancelSprint(label) {
    const repo = _smgmtRepo();
    if (!repo) return;
    if (!confirm(`Cancel sprint ${sprintLabelDisplay(label)}? The sprint will stop and tickets will not be modified.`)) return;
    try {
      const res = await fetch(`/api/sprints/run/${encodeURIComponent(label)}?project=${encodeURIComponent(repo)}`, { method: 'DELETE' });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        _smgmtShowToast(`Cancel failed: ${err.detail || res.status}`);
      } else {
        _smgmtShowToast(`Sprint ${sprintLabelDisplay(label)} cancel signal sent`);
        setTimeout(() => loadSprintMgmt(), 2000);
      }
    } catch (e) {
      _smgmtShowToast(`Cancel failed: ${e.message}`);
    }
  }



  function _pfOpen(label) {
    const repo = _smgmtRepo();
    if (!repo) return;
    _pfCurrentLabel = label;
    _pfCurrentRepo  = repo;
    _pfReset();
    document.getElementById('pf-backdrop').classList.remove('hidden');
    document.getElementById('pf-modal').classList.remove('hidden');
    document.getElementById('pf-close-btn').focus();
    _pfFetch();
  }

  function _pfReset() {
    document.getElementById('pf-loading').classList.remove('hidden');
    document.getElementById('pf-content').classList.add('hidden');
    document.getElementById('pf-error').classList.add('hidden');
    document.getElementById('pf-footer').classList.add('hidden');
    document.getElementById('pf-confirm-btn').disabled = true;
    document.getElementById('pf-confirm-btn').textContent = 'Run Sprint';
    _pfDagData = null;
    _pfWarnings = null;
    _pfCycle = null;
    _pfFlags = null;
    _pfSelectedIds = new Set();
  }

  function _pfClose() {
    document.getElementById('pf-backdrop').classList.add('hidden');
    document.getElementById('pf-modal').classList.add('hidden');
    _pfCurrentLabel = null;
    _pfCurrentRepo  = null;
    _pfState        = 'idle';
    _pfDagData      = null;
    _pfWarnings     = null;
    _pfCycle        = null;
    _pfFlags        = null;
    _pfSelectedIds  = new Set();
  }

  async function _pfFetch() {
    _pfState = 'loading';
    const label = _pfCurrentLabel;
    const repo  = _pfCurrentRepo;
    try {
      const res = await fetch(
        `/api/sprints/${encodeURIComponent(label)}/preflight?project=${encodeURIComponent(repo)}`
      );
      if (!res.ok) throw new Error(await res.text());
      if (_pfCurrentLabel !== label) return;
      const data = await res.json();
      _pfDagData  = data.dag              || null;
      _pfWarnings = data.warnings         || null;
      _pfCycle    = data.cycle            || null;
      _pfFlags    = data.mis_sizing_flags || null;
      if (_pfDagData) {
        for (const t of (_pfDagData.tickets || [])) _pfSelectedIds.add(t.id);
      }
      _pfState = 'success';
      _pfShowSuccess();
    } catch (e) {
      if (_pfCurrentLabel !== label) return;
      _pfState = 'error';
      _pfShowError(e.message || 'Preflight check failed.');
    }
  }

  function _pfShowSuccess() {
    document.getElementById('pf-loading').classList.add('hidden');
    document.getElementById('pf-error').classList.add('hidden');
    const n = parseInt((_pfCurrentLabel || '').split('-')[1], 10);
    const dagHtml       = _pfDagData && (_pfDagData.tickets || []).length > 0
      ? _pfBuildDAGHtml(_pfDagData)
      : '';
    const warningsHtml  = _pfBuildWarningsHtml();
    const cycleHtml     = _pfBuildCycleHtml();
    const flagsHtml     = _pfBuildFlagsHtml();
    const conflictsHtml = _pfBuildConflictsHtml();
    const orderHtml     = _pfBuildOrderHtml();
    document.getElementById('pf-content').innerHTML =
      `<p style="font-size:13px;color:var(--text);margin:0;">Ready to run <strong>Sprint ${n}</strong>.</p>
       ${warningsHtml}
       ${cycleHtml}
       ${flagsHtml}
       ${dagHtml}
       <div class="pf-section">
         <div class="pf-section-label">Conflicts</div>
         <div class="pf-section-body" id="pf-conflicts">${conflictsHtml}</div>
       </div>
       <div class="pf-section">
         <div class="pf-section-label">Proposed Execution Order</div>
         <div class="pf-section-body" id="pf-order">${orderHtml}</div>
       </div>`;
    document.getElementById('pf-content').classList.remove('hidden');
    document.getElementById('pf-footer').classList.remove('hidden');
    _pfUpdateConfirmBtn();
    document.getElementById('pf-cancel-btn').focus();
    if (_pfDagData && (_pfDagData.edges || []).length > 0) {
      requestAnimationFrame(() => _pfDrawDAGArrows(_pfDagData.edges));
    }
  }

  function _pfUpdateConfirmBtn() {
    const hasCycle = !!(_pfCycle && _pfCycle.length);
    const pendingFlags = (_pfFlags && (_pfFlags.flags || []).filter(f => f.status === 'pending')) || [];
    const hasPending = pendingFlags.length > 0;
    const confirmBtn = document.getElementById('pf-confirm-btn');
    if (!confirmBtn) return;
    confirmBtn.disabled = hasCycle || hasPending;
    if (hasCycle) {
      confirmBtn.title = 'Cannot run: dependency cycle detected. Resolve the cycle first.';
      confirmBtn.setAttribute('aria-label', 'Run Sprint — disabled: dependency cycle detected');
    } else if (hasPending) {
      confirmBtn.title = `Cannot run: ${pendingFlags.length} mis-sizing flag${pendingFlags.length > 1 ? 's' : ''} need review.`;
      confirmBtn.setAttribute('aria-label', 'Run Sprint — disabled: mis-sizing flags need review');
    } else {
      confirmBtn.title = '';
      confirmBtn.setAttribute('aria-label', 'Run Sprint');
    }
  }

  function _pfBuildWarningsHtml() {
    if (!_pfWarnings) return '';
    const chips = [];
    const unestimated    = _pfWarnings.unestimated    || [];
    const staleEstimates = _pfWarnings.stale_estimates || [];
    const missingAc      = _pfWarnings.missing_ac      || [];
    if (unestimated.length) {
      chips.push(`<span class="pf-warning-chip">${unestimated.length} unestimated: ${escHtml(unestimated.join(', '))}</span>`);
    }
    if (staleEstimates.length) {
      chips.push(`<span class="pf-warning-chip">${staleEstimates.length} stale estimate${staleEstimates.length > 1 ? 's' : ''}: ${escHtml(staleEstimates.join(', '))}</span>`);
    }
    if (missingAc.length) {
      chips.push(`<span class="pf-warning-chip">${missingAc.length} missing AC: ${escHtml(missingAc.join(', '))}</span>`);
    }
    if (!chips.length) return '';
    return `<div class="pf-warnings-section">
      <div class="pf-warnings-label">Warnings</div>
      <div class="pf-warning-chips">${chips.join('')}</div>
    </div>`;
  }

  function _pfBuildCycleHtml() {
    if (!_pfCycle || !_pfCycle.length) return '';
    return `<div class="pf-cycle-banner">
      <strong>Cycle detected:</strong> ${escHtml(_pfCycle.join(' → '))}
    </div>`;
  }

  // ── Mis-sizing flags section (issue #578) ───────────────────────────────────

  function _pfBuildFlagsHtml() {
    const flags = _pfFlags && (_pfFlags.flags || []);
    if (!flags || !flags.length) return '';

    const label = _pfCurrentLabel;
    const repo  = _pfCurrentRepo;

    const rows = flags.map(f => {
      const num = f.issue_number;
      const resolved = f.status !== 'pending';
      const itemClass = resolved ? 'pf-flag-item resolved' : 'pf-flag-item';

      const estLabel = f.current_estimate
        ? `${escHtml(f.current_estimate)} (${f.current_estimate_minutes ?? '?'} min)`
        : 'unknown';
      const avgLabel = f.historical_avg_actual_size
        ? `${escHtml(f.historical_avg_actual_size)} (${f.historical_avg_actual_minutes ?? '?'} min avg)`
        : 'unknown';
      const drivingLabels = (f.driving_labels || []).map(l => `<code>${escHtml(l)}</code>`).join(', ');
      const eventCount = f.mis_sizing_event_count || 0;

      let badgeHtml = '';
      let actionsHtml = '';
      if (resolved) {
        const actionText = { approved: 'Approved', reestimated: 'Re-estimated', dismissed: 'Dismissed' }[f.status] || f.status;
        badgeHtml = `<span class="pf-flag-badge pf-flag-badge-resolved">${escHtml(actionText)}</span>`;
        const noteText = f.action_note ? ` — ${escHtml(f.action_note)}` : '';
        const newSizeText = f.new_size ? ` New size: ${escHtml(f.new_size)}.` : '';
        actionsHtml = `<div class="pf-flag-resolved-note">${escHtml(actionText)}${newSizeText}${noteText}</div>`;
      } else {
        badgeHtml = `<span class="pf-flag-badge pf-flag-badge-pending">Review needed</span>`;
        actionsHtml = `
          <div class="pf-flag-actions" id="pf-flag-actions-${num}">
            <button class="pf-flag-action-btn approve" onclick="_pfFlagAction(${num}, 'approved')">Approve</button>
            <button class="pf-flag-action-btn" onclick="_pfFlagShowSizePicker(${num}, '${escHtml(f.current_estimate || 'S')}')">Re-estimate</button>
            <button class="pf-flag-action-btn dismiss" onclick="_pfFlagAction(${num}, 'dismissed')">Dismiss</button>
          </div>
          <div id="pf-flag-picker-${num}" style="display:none">
            <div class="pf-flag-size-picker">
              <span style="font-size:12px;color:var(--text-muted);">New size:</span>
              ${['S','M','L','XL'].map(s =>
                `<button class="pf-flag-size-btn" onclick="_pfFlagReestimate(${num}, '${s}')">${s}</button>`
              ).join('')}
              <button class="pf-flag-size-cancel" onclick="_pfFlagHidePicker(${num})">Cancel</button>
            </div>
          </div>`;
      }

      return `<div class="${itemClass}" id="pf-flag-item-${num}">
        <div class="pf-flag-header">
          <span class="pf-flag-id">#${num}</span>
          <span class="pf-flag-title" title="${escHtml(f.title)}">${escHtml(f.title)}</span>
          ${badgeHtml}
        </div>
        <div class="pf-flag-details">
          Estimate: <strong>${estLabel}</strong> ·
          Historical avg: <strong>${avgLabel}</strong> ·
          ${eventCount} mis-sizing event${eventCount !== 1 ? 's' : ''} on: ${drivingLabels}
        </div>
        ${actionsHtml}
      </div>`;
    });

    const pending = flags.filter(f => f.status === 'pending').length;
    const subtitle = pending > 0
      ? `${pending} ticket${pending > 1 ? 's' : ''} flagged for review`
      : 'All flags resolved';

    return `<div class="pf-flags-section" id="pf-flags-section">
      <div class="pf-flags-label">Mis-sizing review — ${subtitle}</div>
      ${rows.join('')}
    </div>`;
  }

  function _pfFlagShowSizePicker(num, currentSize) {
    const actionsEl = document.getElementById(`pf-flag-actions-${num}`);
    const pickerEl  = document.getElementById(`pf-flag-picker-${num}`);
    if (actionsEl) actionsEl.style.display = 'none';
    if (pickerEl)  pickerEl.style.display  = 'block';
  }

  function _pfFlagHidePicker(num) {
    const actionsEl = document.getElementById(`pf-flag-actions-${num}`);
    const pickerEl  = document.getElementById(`pf-flag-picker-${num}`);
    if (actionsEl) actionsEl.style.display = '';
    if (pickerEl)  pickerEl.style.display  = 'none';
  }

  async function _pfFlagAction(num, action, newSize) {
    const label = _pfCurrentLabel;
    const repo  = _pfCurrentRepo;
    if (!label || !repo) return;

    // Disable buttons to prevent double-click
    const itemEl = document.getElementById(`pf-flag-item-${num}`);
    if (itemEl) itemEl.querySelectorAll('button').forEach(b => { b.disabled = true; });

    try {
      const body = { action };
      if (newSize) body.new_size = newSize;
      const res = await fetch(
        `/api/sprints/${encodeURIComponent(label)}/mis-sizing-flags/${num}/action?project=${encodeURIComponent(repo)}`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
      );
      if (!res.ok) {
        const err = await res.text();
        _smgmtShowToast(`Flag action failed: ${err}`, 'error');
        if (itemEl) itemEl.querySelectorAll('button').forEach(b => { b.disabled = false; });
        return;
      }
      const data = await res.json();
      // Update local state and re-render flags section
      _pfFlags = data;
      const flagsSection = document.getElementById('pf-flags-section');
      if (flagsSection) {
        const newHtml = _pfBuildFlagsHtml();
        flagsSection.outerHTML = newHtml || '<div id="pf-flags-section"></div>';
      }
      _pfUpdateConfirmBtn();
    } catch (e) {
      _smgmtShowToast('Flag action failed: ' + e.message, 'error');
      if (itemEl) itemEl.querySelectorAll('button').forEach(b => { b.disabled = false; });
    }
  }

  function _pfFlagReestimate(num, newSize) {
    _pfFlagHidePicker(num);
    _pfFlagAction(num, 'reestimated', newSize);
  }

  // ────────────────────────────────────────────────────────────────────────────

  function _pfBuildDAGHtml(dag) {
    const ticketMap = {};
    for (const t of (dag.tickets || [])) ticketMap[t.id] = t;
    const layers = dag.layers || [];
    if (!layers.length) return '';

    let colsHtml = '';
    for (let i = 0; i < layers.length; i++) {
      const layer = layers[i];
      let cardsHtml = '';
      for (const id of layer) {
        const t = ticketMap[id] || { id, number: id.replace('#', ''), title: id, state: 'backlog', size: null, files_touched: [] };
        const stateClass = t.state || 'backlog';
        const stateBadge = `<span class="ticket-status-pill ${escHtml(stateClass)}">${escHtml(stateClass)}</span>`;
        const sizeBadge  = t.size ? `<span class="pf-dag-size-badge">${escHtml(t.size)}</span>` : '';
        const files      = (t.files_touched || []);
        const shown      = files.slice(0, 3).map(f => `<span>${escHtml(f.split('/').slice(-1)[0])}</span>`).join('');
        const more       = files.length > 3 ? `<span>+${files.length - 3} more</span>` : '';
        const filesHtml  = (shown || more) ? `<div class="pf-dag-card-files">${shown}${more}</div>` : '';
        cardsHtml += `<div class="pf-dag-card" id="pf-card-${escHtml(id)}" data-dag-id="${escHtml(id)}">
            <div class="pf-dag-card-header">
              <input type="checkbox" class="pf-dag-card-check" checked
                onchange="_pfToggleTicket('${escHtml(id)}')" aria-label="Include ticket ${escHtml(id)}">
              <span class="pf-dag-card-num">${escHtml(id)}</span>
            </div>
            <div class="pf-dag-card-title" title="${escHtml(t.title)}">${escHtml(t.title)}</div>
            <div class="pf-dag-card-meta">${stateBadge}${sizeBadge}</div>
            ${filesHtml}
          </div>`;
      }
      colsHtml += `<div class="pf-dag-col">
          <div class="pf-dag-col-label">Level ${i + 1}</div>
          ${cardsHtml}
        </div>`;
    }

    return `<div class="pf-dag-section">
      <div class="pf-dag-section-label">Execution Graph</div>
      <div class="pf-dag-wrap" id="pf-dag-wrap">
        <svg class="pf-dag-svg" id="pf-dag-svg" aria-hidden="true"></svg>
        <div class="pf-dag-levels" id="pf-dag-levels">${colsHtml}</div>
      </div>
    </div>`;
  }

  function _pfDrawDAGArrows(edges) {
    if (!edges || !edges.length) return;
    const wrap   = document.getElementById('pf-dag-wrap');
    const svg    = document.getElementById('pf-dag-svg');
    const levels = document.getElementById('pf-dag-levels');
    if (!wrap || !svg || !levels) return;

    const wrapRect = wrap.getBoundingClientRect();
    const h = levels.getBoundingClientRect().height;
    svg.setAttribute('width',  String(wrapRect.width));
    svg.setAttribute('height', String(h));

    const defs   = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
    const marker = document.createElementNS('http://www.w3.org/2000/svg', 'marker');
    marker.setAttribute('id', 'pf-arrow');
    marker.setAttribute('markerWidth',  '7');
    marker.setAttribute('markerHeight', '7');
    marker.setAttribute('refX', '6');
    marker.setAttribute('refY', '3.5');
    marker.setAttribute('orient', 'auto');
    const arrowPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    arrowPath.setAttribute('d', 'M0,0 L0,7 L7,3.5 z');
    arrowPath.setAttribute('fill', 'var(--text-muted)');
    marker.appendChild(arrowPath);
    defs.appendChild(marker);
    svg.appendChild(defs);

    for (const [fromId, toId] of edges) {
      const fromEl = wrap.querySelector(`[data-dag-id="${fromId}"]`);
      const toEl   = wrap.querySelector(`[data-dag-id="${toId}"]`);
      if (!fromEl || !toEl) continue;

      const fr = fromEl.getBoundingClientRect();
      const tr = toEl.getBoundingClientRect();
      const x1 = fr.right  - wrapRect.left;
      const y1 = fr.top    + fr.height / 2 - wrapRect.top;
      const x2 = tr.left   - wrapRect.left - 7;
      const y2 = tr.top    + tr.height / 2 - wrapRect.top;
      const mx = (x1 + x2) / 2;

      const line = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      line.setAttribute('d', `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`);
      line.setAttribute('stroke', 'var(--text-muted)');
      line.setAttribute('stroke-width', '1.5');
      line.setAttribute('fill', 'none');
      line.setAttribute('marker-end', 'url(#pf-arrow)');
      svg.appendChild(line);
    }
  }

  function _pfToggleTicket(id) {
    if (_pfSelectedIds.has(id)) {
      _pfSelectedIds.delete(id);
    } else {
      _pfSelectedIds.add(id);
    }
    const card = document.getElementById(`pf-card-${id}`);
    if (card) card.classList.toggle('pf-deselected', !_pfSelectedIds.has(id));
    _pfUpdateSections();
  }

  function _pfGetSelectedTickets() {
    if (!_pfDagData) return [];
    return (_pfDagData.tickets || []).filter(t => _pfSelectedIds.has(t.id));
  }

  function _pfComputeConflicts(tickets) {
    const conflicts = [];
    for (let i = 0; i < tickets.length; i++) {
      for (let j = i + 1; j < tickets.length; j++) {
        const filesA = tickets[i].files_touched || [];
        const filesB = tickets[j].files_touched || [];
        const shared = filesA.filter(f => filesB.includes(f));
        for (const file of shared) {
          conflicts.push({ a: tickets[i], b: tickets[j], file });
        }
      }
    }
    return conflicts;
  }

  function _pfBuildConflictsHtml() {
    const selected = _pfGetSelectedTickets();
    const conflicts = _pfComputeConflicts(selected);
    if (!conflicts.length) {
      return '<p class="pf-no-conflict">No file conflicts detected.</p>';
    }
    return conflicts.map(c =>
      `<p class="pf-conflict-item">Tickets #${c.a.number} and #${c.b.number} both touch <code>${escHtml(c.file)}</code></p>`
    ).join('');
  }

  function _pfBuildOrderHtml() {
    if (!_pfDagData) return '<p class="pf-no-conflict">No order data available.</p>';
    const layers = (_pfDagData.layers || [])
      .map(layer => layer.filter(id => _pfSelectedIds.has(id)))
      .filter(l => l.length > 0);
    if (!layers.length) return '<p class="pf-no-conflict">No tickets selected.</p>';

    let html = '<ol class="pf-order-list">';
    for (let i = 0; i < layers.length; i++) {
      const nums = layers[i].map(id => id);
      const descriptor = i === 0 ? 'parallel-eligible' : `runs after Level ${i}`;
      html += `<li class="pf-order-item">Level ${i + 1}: ${escHtml(nums.join(', '))} — ${escHtml(descriptor)}.</li>`;
    }
    html += '</ol>';
    return html;
  }

  function _pfUpdateSections() {
    const conflictsEl = document.getElementById('pf-conflicts');
    const orderEl     = document.getElementById('pf-order');
    if (conflictsEl) conflictsEl.innerHTML = _pfBuildConflictsHtml();
    if (orderEl)     orderEl.innerHTML     = _pfBuildOrderHtml();
  }

  function _pfShowError(msg) {
    document.getElementById('pf-loading').classList.add('hidden');
    document.getElementById('pf-content').classList.add('hidden');
    document.getElementById('pf-error-msg').textContent = msg;
    document.getElementById('pf-error').classList.remove('hidden');
    document.getElementById('pf-footer').classList.remove('hidden');
    document.getElementById('pf-confirm-btn').disabled = true;
    document.getElementById('pf-retry-btn').focus();
  }

  function _pfRetry() {
    _pfReset();
    _pfFetch();
  }

  async function _pfConfirm() {
    if (_pfState !== 'success') return;
    const label = _pfCurrentLabel;
    const repo  = _pfCurrentRepo;
    if (!label || !repo) return;
    const confirmBtn = document.getElementById('pf-confirm-btn');
    confirmBtn.disabled = true;
    confirmBtn.innerHTML = '<span class="pf-spinner" style="width:12px;height:12px;border-width:2px;"></span> Running…';
    try {
      const res = await fetch('/api/sprints/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project: repo, sprint_label: label }),
      });
      if (!res.ok) throw new Error(await res.text());
      _pfClose();
      const n = parseInt(label.split('-')[1], 10);
      _smgmtShowToast(`Sprint ${n} dispatched.`);
      await loadSprintMgmt();
    } catch (e) {
      _pfState = 'error';
      _pfShowError('Failed to run sprint: ' + e.message);
    }
  }
  // apps/dashboard/static/src/sprint-board/finish-modal.js
  /* Finish Sprint modal (issue #367 parity) — extracted from project.html (#797).
   *
   * Opens the finish-sprint modal, previews which tickets close vs. carry forward
   * (and whether a sprint PR will be merged), and confirms the finish. Page
   * helpers and broadly-shared board caches resolve through the page's global
   * scope; modal-local state (`_fsLabel`, `_fsPreview`) is seeded on `window` by
   * ./state.js.
   */

  /* global _setBodyInert, _clearBodyInert, _smgmtRepo, sprintLabelDisplay,
     escHtml, _smgmtShowToast, loadSprintMgmt,
     _fsLabel:writable, _fsPreview:writable */

  function _fsOpen() {
    _setBodyInert(['fs-backdrop', 'fs-modal']);
    document.getElementById('fs-backdrop').classList.remove('hidden');
    document.getElementById('fs-modal').classList.remove('hidden');
  }
  function _fsClose() {
    document.getElementById('fs-backdrop').classList.add('hidden');
    document.getElementById('fs-modal').classList.add('hidden');
    _clearBodyInert();
    _fsLabel = null;
    _fsPreview = null;
  }
  function _fsCatClass(cat) {
    if (cat === 'UAT')            return 'rr-cat-uat';
    if (cat === 'SIT')            return 'rr-cat-sit';
    if (cat === 'needs-rework')   return 'rr-cat-rework';
    if (cat === 'sprint-summary') return 'rr-cat-summary';
    return 'rr-cat-queued';
  }
  function _fsSelectAll(checked) {
    document.querySelectorAll('#fs-ticket-list input[type=checkbox]').forEach(cb => { cb.checked = checked; });
  }

  async function smgmtFinishSprint(label) {
    const repo = _smgmtRepo();
    if (!repo) return;
    _fsLabel = label;
    _fsPreview = null;
    const parts = repo.split('/');
    const owner = parts[0];
    const repoName = parts.slice(1).join('/');

    document.getElementById('fs-modal-title').textContent = `Finish ${sprintLabelDisplay(label)}?`;
    document.getElementById('fs-loading').classList.remove('hidden');
    document.getElementById('fs-content').classList.add('hidden');
    document.getElementById('fs-error').classList.add('hidden');
    document.getElementById('fs-error').textContent = '';
    const confirmBtn = document.getElementById('fs-confirm-btn');
    if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Finish Sprint'; }
    _fsOpen();

    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/finish-preview`
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const preview = await res.json();
      _fsPreview = preview;

      if (preview.conflict_error) {
        throw new Error(preview.conflict_error);
      }

      const listEl = document.getElementById('fs-ticket-list');
      const allTickets = preview.all_tickets || [];
      if (allTickets.length === 0) {
        listEl.innerHTML = '<div style="padding:10px;color:var(--text-muted);font-size:13px">No open tickets in this sprint.</div>';
      } else {
        listEl.innerHTML = allTickets.map(t => {
          const catClass = _fsCatClass(t.category);
          const catLabel = t.category === 'sprint-summary' ? 'SUMMARY' : t.category.toUpperCase();
          return `<label class="rr-ticket-row">
            <input type="checkbox" checked data-issue="${t.number}" onchange="">
            <span class="rr-ticket-num">#${t.number}</span>
            <span class="rr-ticket-title" title="${escHtml(t.title)}">${escHtml(t.title)}</span>
            <span class="rr-ticket-cat ${catClass}">${escHtml(catLabel)}</span>
          </label>`;
        }).join('');
      }

      const actionsEl = document.getElementById('fs-actions');
      const actionRows = [];
      if (preview.sprint_pr) {
        actionRows.push(`<div class="fs-action-row"><i class="ti ti-git-merge"></i> Merge sprint PR
          <a href="${escHtml(preview.sprint_pr.url)}" target="_blank" rel="noopener">#${preview.sprint_pr.number}</a></div>`);
      }
      actionRows.push('<div class="fs-action-row"><i class="ti ti-circle-check"></i> Close all selected tickets</div>');
      actionRows.push('<div class="fs-action-row"><i class="ti ti-tag-off"></i> Remove sprint label</div>');
      actionsEl.innerHTML = actionRows.join('');

      document.getElementById('fs-loading').classList.add('hidden');
      document.getElementById('fs-content').classList.remove('hidden');
      if (confirmBtn) confirmBtn.disabled = false;
    } catch (e) {
      document.getElementById('fs-loading').classList.add('hidden');
      const errEl = document.getElementById('fs-error');
      errEl.textContent = 'Failed to load preview: ' + e.message;
      errEl.classList.remove('hidden');
    }
  }

  async function _fsConfirm() {
    const repo = _smgmtRepo();
    if (!_fsLabel || !repo || !_fsPreview) return;
    const parts = repo.split('/');
    const owner = parts[0];
    const repoName = parts.slice(1).join('/');

    const checkboxes = Array.from(document.querySelectorAll('#fs-ticket-list input[type=checkbox]'));
    const selectedNums = checkboxes.filter(c => c.checked).map(c => parseInt(c.dataset.issue, 10));

    const confirmBtn = document.getElementById('fs-confirm-btn');
    if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Finishing…'; }

    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(_fsLabel)}/finish`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            confirmed: true,
            move_non_uat_to: _fsPreview.next_sprint_label,
            selected_ticket_numbers: selectedNums,
            merge_pr: !!_fsPreview.sprint_pr,
            sprint_pr_url: _fsPreview.sprint_pr ? _fsPreview.sprint_pr.url : null,
          }),
        }
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      _fsClose();
      if (data.errors && data.errors.length > 0) {
        _smgmtShowToast(`Finished with errors — ${data.closed} closed, ${data.moved} moved.`);
      } else {
        let msg = `${sprintLabelDisplay(_fsLabel || '')} finished — ${data.closed} closed`;
        if (data.moved > 0) msg += `, ${data.moved} moved to ${data.next_sprint_label}`;
        _smgmtShowToast(msg + '.');
      }
      await loadSprintMgmt();
    } catch (e) {
      const errEl = document.getElementById('fs-error');
      errEl.textContent = 'Failed to finish sprint: ' + e.message;
      errEl.classList.remove('hidden');
      if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = 'Finish Sprint'; }
    }
  }
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

  // Finish modal (issue #367)
  globalThis._fsOpen = _fsOpen;
  globalThis._fsClose = _fsClose;
  globalThis._fsCatClass = _fsCatClass;
  globalThis._fsSelectAll = _fsSelectAll;
  globalThis.smgmtFinishSprint = smgmtFinishSprint;
  globalThis._fsConfirm = _fsConfirm;

  // Run controls + preflight modal (issue #448)
  globalThis.smgmtRunBlockedToast = smgmtRunBlockedToast;
  globalThis.smgmtRunSprint = smgmtRunSprint;
  globalThis.smgmtCancelSprint = smgmtCancelSprint;
  globalThis._pfOpen = _pfOpen;
  globalThis._pfReset = _pfReset;
  globalThis._pfClose = _pfClose;
  globalThis._pfFetch = _pfFetch;
  globalThis._pfShowSuccess = _pfShowSuccess;
  globalThis._pfUpdateConfirmBtn = _pfUpdateConfirmBtn;
  globalThis._pfBuildWarningsHtml = _pfBuildWarningsHtml;
  globalThis._pfBuildCycleHtml = _pfBuildCycleHtml;
  globalThis._pfBuildFlagsHtml = _pfBuildFlagsHtml;
  globalThis._pfFlagShowSizePicker = _pfFlagShowSizePicker;
  globalThis._pfFlagHidePicker = _pfFlagHidePicker;
  globalThis._pfFlagAction = _pfFlagAction;
  globalThis._pfFlagReestimate = _pfFlagReestimate;
  globalThis._pfBuildDAGHtml = _pfBuildDAGHtml;
  globalThis._pfDrawDAGArrows = _pfDrawDAGArrows;
  globalThis._pfToggleTicket = _pfToggleTicket;
  globalThis._pfGetSelectedTickets = _pfGetSelectedTickets;
  globalThis._pfComputeConflicts = _pfComputeConflicts;
  globalThis._pfBuildConflictsHtml = _pfBuildConflictsHtml;
  globalThis._pfBuildOrderHtml = _pfBuildOrderHtml;
  globalThis._pfUpdateSections = _pfUpdateSections;
  globalThis._pfShowError = _pfShowError;
  globalThis._pfRetry = _pfRetry;
  globalThis._pfConfirm = _pfConfirm;

  // Drag & drop + multi-select + ghost pane + board lock (issues #247/#276/#660)
  globalThis._smgmtUpdateSelectionUI = _smgmtUpdateSelectionUI;
  globalThis._smgmtPopulateSelectionDropdown = _smgmtPopulateSelectionDropdown;
  globalThis._smgmtPopulateMoveToMenu = _smgmtPopulateMoveToMenu;
  globalThis._smgmtToggleMoveToMenu = _smgmtToggleMoveToMenu;
  globalThis._smgmtCloseMoveToMenu = _smgmtCloseMoveToMenu;
  globalThis._smgmtClearSelection = _smgmtClearSelection;
  globalThis._smgmtSetSelected = _smgmtSetSelected;
  globalThis._smgmtToggleSelect = _smgmtToggleSelect;
  globalThis._smgmtRowClick = _smgmtRowClick;
  globalThis._smgmtIsDeletableIssue = _smgmtIsDeletableIssue;
  globalThis._smgmtDeleteSelected = _smgmtDeleteSelected;
  globalThis._smgmtMoveSelectedTo = _smgmtMoveSelectedTo;
  globalThis._smgmtTicketDragStart = _smgmtTicketDragStart;
  globalThis._smgmtDragMovePill = _smgmtDragMovePill;
  globalThis._smgmtGhostComputeNextFree = _smgmtGhostComputeNextFree;
  globalThis._smgmtGhostShow = _smgmtGhostShow;
  globalThis._smgmtGhostHide = _smgmtGhostHide;
  globalThis._smgmtGhostDragOver = _smgmtGhostDragOver;
  globalThis._smgmtGhostDragLeave = _smgmtGhostDragLeave;
  globalThis._smgmtGhostDrop = _smgmtGhostDrop;
  globalThis._gcClose = _gcClose;
  globalThis._gcConfirm = _gcConfirm;
  globalThis._smgmtTicketDragEnd = _smgmtTicketDragEnd;
  globalThis._smgmtDragOver = _smgmtDragOver;
  globalThis._smgmtDragLeave = _smgmtDragLeave;
  globalThis._smgmtDropOnSprint = _smgmtDropOnSprint;
  globalThis._smgmtTicketReorderDragOver = _smgmtTicketReorderDragOver;
  globalThis._smgmtTicketReorderDragLeave = _smgmtTicketReorderDragLeave;
  globalThis._smgmtTicketReorderDrop = _smgmtTicketReorderDrop;
  globalThis._smgmtBacklogTicketDragStart = _smgmtBacklogTicketDragStart;
  globalThis._smgmtBacklogDragOver = _smgmtBacklogDragOver;
  globalThis._smgmtBacklogDragLeave = _smgmtBacklogDragLeave;
  globalThis._smgmtDropOnBacklog = _smgmtDropOnBacklog;
  globalThis._smgmtBoardLock = _smgmtBoardLock;
  globalThis._smgmtBoardUnlock = _smgmtBoardUnlock;
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
