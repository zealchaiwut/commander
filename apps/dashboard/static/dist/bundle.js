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
  // Active finish-sprint progress job (issue #929 — reconnect support)
  globalThis._fsActiveJob ??= null;

  // Bulk Complete Sprint modal (parent + child lineage)
  globalThis._bcLabel ??= null;
  globalThis._bcPreview ??= null;

  // Run preflight modal (issue #448)
  globalThis._pfCurrentLabel ??= null;
  globalThis._pfCurrentRepo ??= null;
  globalThis._pfState ??= "idle";
  globalThis._pfDagData ??= null;
  globalThis._pfWarnings ??= null;
  globalThis._pfCycle ??= null;
  globalThis._pfFlags ??= null;
  globalThis._pfSelectedIds ??= new Set();
  // Cline follow-up opt-in (issue #919)
  globalThis._pfUseClineFollowups ??= false;

  // Drag/drop local locks
  globalThis._smgmtMoveLock ??= false;
  globalThis._smgmtGhostNextNum ??= null;

  const SPRINT_BOARD_STATE_KEYS = [
    "_rrLabel",
    "_rrVersionedLabel",
    "_fsLabel",
    "_fsPreview",
    "_fsActiveJob",
    "_bcLabel",
    "_bcPreview",
    "_pfCurrentLabel",
    "_pfCurrentRepo",
    "_pfState",
    "_pfDagData",
    "_pfWarnings",
    "_pfCycle",
    "_pfFlags",
    "_pfSelectedIds",
    "_pfUseClineFollowups",
    "_smgmtMoveLock",
    "_smgmtGhostNextNum",
  ];
  // apps/dashboard/static/src/sprint-board/board-render.js
  /* Sprint-board render pipeline (issue #797) — extracted from project.html.
   *
   * Owns the board's render core: loadSprintMgmt() (fetch board state) ->
   * _smgmtRender() (group issues by sprint, order columns, inject cards) and the
   * HTML builders for sprint cards, running cards, ticket rows, outcome bands,
   * finish-report cards, the backlog column, and column rollups. Peripheral board
   * helpers (sort menus, density, capacity gauges, popovers, keyboard nav,
   * auto-refresh) remain inline and resolve through the page's global scope; they
   * are scheduled for follow-on extraction waves.
   *
   * The running-card live-log section uses the shared ProgressActivity component
   * (issue #928) for rendering progress and log lines.
   */

  /* eslint-disable no-unused-vars */
  /* global _blApplyFilters, _blBacklogAll, _blSyncFilterPills, _blUpdateActions, _smgmtEnsureCapData, _smgmtLoadMiniRail, _smgmtRenderAllCapBars, _smgmtUpdateSubnav, _cachedFullRepo, _estDataCache, _slug, _smgmtActiveAgentsHtml, _smgmtAgentTagClass, _smgmtApplySort, _smgmtBacklogTicketDragStart, _smgmtBulkEstimate, _smgmtBySprint, _smgmtCancelBannerHtml, _smgmtCapacityInputHtml, _smgmtCheckEstimatorHealth, _smgmtCloseIssueOpen, _smgmtConflictsByIssue, _smgmtCtxMenuOpen, _smgmtData, _smgmtDeactivatedLabels, _smgmtDepOrderByIssue, _smgmtDragLeave, _smgmtDragOver, _smgmtDropOnSprint, _smgmtEstimateBadgeHtml, _smgmtEstimatorAvailable, _smgmtFilterApply, _smgmtFinishCards, _smgmtFinishedLabels, _smgmtHasCompletedTickets, _smgmtInitCapacityGauges, _smgmtInjectOutcomeBand, _smgmtIsCancelled, _smgmtKbRestoreFocus, _smgmtLabelColors, _smgmtLabelFilterToggle, _smgmtLabelFilterToggleExpand, _smgmtLastLabelIssues, _smgmtLevelsHtml, _smgmtLiveAgentBadgesHtml, _smgmtLiveCache, _smgmtLiveCacheRepo, _smgmtLiveLogLinesHtml, _smgmtLivePollRestart, _smgmtLingerRestore, _smgmtLingerStart, _smgmtIsLinger, _smgmtLingerLive, _smgmtNextChildLabel, _smgmtNextUpLabel, _smgmtOutcomeCache, _smgmtOutcomeLogHtml, _smgmtPrimaryRunningLabel, _smgmtReEstimate, _smgmtRepo, _smgmtRiskFlagIconsHtml, _smgmtRowClick, _smgmtRowMenuOpen, _smgmtRunningViewUpdate, _smgmtSchedDepHtml, _smgmtSelectedIssues, _smgmtSetSprintTokenEl, _smgmtStateMeta, _smgmtTicketDragEnd, _smgmtTicketDragStart, _smgmtTicketReorderDragLeave, _smgmtTicketReorderDragOver, _smgmtTicketReorderDrop, _smgmtTicketToSprint, _smgmtToggleSelect, _smgmtUpdateCapacityGauge, _smgmtUpdateCleanupBtn, _smgmtUpdateConflictBadge, _smgmtUpdateDepOrderBadge, _smgmtUpdateEstimateBadge, _smgmtUpdateSelectionUI, _smgmtSchedToggleHtml, _smgmtHydrateSchedToggles, escHtml, sprintLabelDisplay, colorizeLogLine,
     _smgmtAnySprintRunning:writable, _smgmtOrderedLabels:writable, _smgmtRunningLabels:writable */
  /* eslint-enable no-unused-vars */


  /** Tracks which labels are resolved ancestors on the current board render. */
  let _smgmtResolvedAncestors = new Set();

  async function loadSprintMgmt(silent, optimisticRunningLabel) {
    const listEl = document.getElementById("smgmt-sprint-list");
    if (!listEl) return;

    const repo = _cachedFullRepo[_slug] || null;
    if (!repo) {
      listEl.innerHTML = '<div class="loading-msg">Project not found.</div>';
      return;
    }

    if (!silent) {
      listEl.innerHTML = '<div class="loading-msg">Loading sprints…</div>';
      // Reset finish-card cache on a full (non-silent) load so stale cross-sprint cards don't linger
      for (const k of Object.keys(_smgmtFinishCards)) delete _smgmtFinishCards[k];
    }

    try {
      // Load calibrated size minutes before rendering rollups / budget bars (issue #801).
      if (typeof _smgmtEnsureCapData === "function") {
        await _smgmtEnsureCapData();
      }

      // Fetch sprint management data + running sprint status + summaries in parallel
      const [resp, runningResp] = await Promise.all([
        fetch("/api/sprint-management/issues?repo=" + encodeURIComponent(repo)),
        fetch("/api/sprints/running-all").catch(() => null),
      ]);
      if (!resp.ok) {
        // Surface a GitHub rate-limit failure specifically (status 429 from
        // _gh_error) so the board says what's wrong instead of "Failed to load".
        let msg = "Failed to load sprints.";
        const d = await resp.json().catch(() => null);
        const detail = d && typeof d.detail === "string" ? d.detail : "";
        if (resp.status === 429 || /rate limit/i.test(detail)) {
          msg = detail || "GitHub API rate limit reached — retry shortly.";
        }
        throw new Error(msg);
      }
      const data = await resp.json();

      if (_smgmtLiveCacheRepo !== repo) {
        _smgmtLiveCacheRepo = repo;
        for (const k of Object.keys(_smgmtLiveCache)) delete _smgmtLiveCache[k];
      }

      if (typeof _smgmtLingerRestore === "function") _smgmtLingerRestore(repo);

      // Update running labels set; start linger when a label drops off running-all.
      const prevRunning = new Set(_smgmtRunningLabels);
      _smgmtRunningLabels = new Set();
      _smgmtAnySprintRunning = false;
      if (runningResp && runningResp.ok) {
        const runningData = await runningResp.json();
        const running = runningData.running || [];
        running.forEach((r) => {
          if (r.project === repo) {
            _smgmtRunningLabels.add(r.sprint_label);
          }
        });
        // Only block Run Sprint if THIS project has a running sprint
        _smgmtAnySprintRunning = _smgmtRunningLabels.size > 0;
      }

      for (const label of prevRunning) {
        if (
          !_smgmtRunningLabels.has(label) &&
          typeof _smgmtLingerStart === "function"
        ) {
          _smgmtLingerStart(label);
        }
      }

      // Keep sprint in running UI until /api/sprints/running-all catches up (post-dispatch race).
      if (optimisticRunningLabel) {
        _smgmtRunningLabels.add(optimisticRunningLabel);
        _smgmtAnySprintRunning = true;
      }

      _smgmtRender(data);

      // Hydrate Run-on-schedule toggles for approved cards (issue #863).
      if (typeof _smgmtHydrateSchedToggles === "function") {
        _smgmtHydrateSchedToggles(repo);
      }

      // Start (or restart) live polling if there are running sprints
      _smgmtLivePollRestart();

      const lingerLbl =
        typeof _smgmtPrimaryRunningLabel === "function"
          ? _smgmtPrimaryRunningLabel()
          : null;
      if (lingerLbl && typeof _smgmtRunningViewUpdate === "function") {
        const live =
          typeof _smgmtLingerLive === "function"
            ? _smgmtLingerLive(lingerLbl)
            : _smgmtLiveCache[lingerLbl] || null;
        _smgmtRunningViewUpdate(lingerLbl, live);
      } else if (typeof _smgmtRunningViewUpdate === "function") {
        _smgmtRunningViewUpdate(null, null);
      }
    } catch (err) {
      if (!silent) {
        const msg = err && err.message ? err.message : "Failed to load sprints.";
        listEl.innerHTML = `<div class="loading-msg">${escHtml(msg)}</div>`;
      }
    }
  }

  function _smgmtSprintLabelSortKey(label) {
    const m = String(label).match(/^sprint-(\d+(?:\.\d+)*)$/);
    if (!m) return [Infinity];
    return m[1].split(".").map((n) => parseInt(n, 10));
  }

  function _smgmtRender(data) {
    const listEl = document.getElementById("smgmt-sprint-list");
    if (!listEl) return;
    _smgmtData = data;

    // Keep the sub-nav live indicators (running dot + History count) in sync on
    // every render, including auto-refresh, so the dot clears when sprints stop
    // and the badge tracks the sprint total (issue #798).
    _smgmtUpdateSubnav();

    const sprints = data.sprints || [];
    const order = data.order || [];
    const issues = data.issues || [];

    // Group issues by sprint_label (handles both sprint-N and sprint-N.M)
    const bySprint = {};
    const unassigned = [];
    issues.forEach((iss) => {
      const key = iss.sprint_label || null;
      if (key != null) {
        if (!bySprint[key]) bySprint[key] = [];
        bySprint[key].push(iss);
      } else {
        unassigned.push(iss);
      }
    });
    _smgmtBySprint = bySprint;

    // Always render the backlog pane regardless of sprint count
    _smgmtRenderBacklog(unassigned);

    if (order.length === 0 && sprints.length === 0) {
      listEl.innerHTML =
        '<div class="loading-msg">No sprints yet. Create one with + New Sprint.</div>';
      return;
    }

    // Finished sprints (executive summary posted) — hide empty columns so stale
    // GitHub labels do not resurrect as READY TO MERGE zombies when order is [].
    const _finishedSet = new Set(data.finished_sprints || []);

    // Use order list if available (includes sub-labels), else build from integer sprints ascending
    const orderedLabelsRaw =
      order.length > 0
        ? order.filter((l) => /^sprint-\d+(\.\d+)*$/.test(l))
        : [...sprints].sort((a, b) => a - b).map((n) => `sprint-${n}`);

    const _sprintParents = data.sprint_parents || {};
    const _rerunInto = data.sprint_rerun_into || {};

    const _smgmtWorkTickets = (tickets) =>
      (tickets || []).filter((t) => {
        const names = (t.labels || []).map((l) => l.name);
        return !names.some((n) =>
          ["sprint-summary", "docs", "documentation"].includes(n),
        );
      });
    const _smgmtTicketSettledOnBoard = (t) => {
      const names = (t.labels || []).map((l) => l.name);
      return names.some((n) =>
        ["UAT", "UAT-approved", "released", "SIT"].includes(n),
      );
    };
    const _smgmtHideRerunParent = (label, tickets, rerunInto) => {
      if (!rerunInto[label]) return false;
      const work = _smgmtWorkTickets(tickets);
      return work.length === 0 || work.every(_smgmtTicketSettledOnBoard);
    };

    // After a re-run moves tickets to a child label, hide the empty parent card until refresh
    // would have dropped it from the order list anyway (issue #512 UX).
    // Resolved ancestors: sprints that ran and were rerun into a child sprint.
    // Previously hidden; now rendered as compact collapsed rows (issue #1043).
    _smgmtResolvedAncestors = new Set();
    const orderedLabels = orderedLabelsRaw.filter((label) => {
      const tickets = bySprint[label] || [];
      if (_smgmtHideRerunParent(label, tickets, _rerunInto)) {
        _smgmtResolvedAncestors.add(label);
        return true; // keep for compact ancestor row rendering
      }
      const ticketCount = tickets.length;
      if (ticketCount > 0) return true;
      if (_finishedSet.has(label)) return false;
      if (_rerunInto[label]) return false;
      const hasChild = Object.values(_sprintParents).some(
        (parent) => parent === label,
      );
      return !hasChild;
    });
    _smgmtOrderedLabels = orderedLabels;

    // Finished sprints (a summary issue exists) — the same GitHub-backed signal
    // the nav pill uses. Finished sprints are not "NEXT UP" and skip pre-flight.
    _smgmtFinishedLabels = _finishedSet;

    // NEXT UP: lowest label with >= 1 ticket that isn't running or finished (sprint-26 parity)
    let _smgmtNextUpLabel = null;
    const sortedForNext = [...orderedLabels].sort((a, b) => {
      const ka = _smgmtSprintLabelSortKey(a);
      const kb = _smgmtSprintLabelSortKey(b);
      for (let i = 0; i < Math.max(ka.length, kb.length); i++) {
        const d = (ka[i] ?? Infinity) - (kb[i] ?? Infinity);
        if (d !== 0) return d;
      }
      return 0;
    });
    for (const lbl of sortedForNext) {
      if (_smgmtRunningLabels.has(lbl)) continue;
      if (typeof _smgmtIsLinger === "function" && _smgmtIsLinger(lbl)) continue;
      if (_smgmtFinishedLabels.has(lbl)) continue;
      if ((bySprint[lbl] || []).length >= 1) {
        _smgmtNextUpLabel = lbl;
        break;
      }
    }

    const cards = orderedLabels
      .map((label) => {
        const tickets = bySprint[label] || [];

        // Resolved ancestors use compact collapsed rows (issue #1043)
        if (_smgmtResolvedAncestors.has(label)) {
          const childLabel = _rerunInto[label];
          const outcome = _smgmtOutcomeCache[label] || null;
          const ancestorHtml = _smgmtAncestorRowHtml(label, outcome, childLabel);
          return (
            `<div class="smgmt-sprint-unit" id="smgmt-unit-${escHtml(label)}">` +
            ancestorHtml +
            `</div>`
          );
        }

        if (_smgmtIsFreshRerunSprint(label)) delete _smgmtOutcomeCache[label];
        const inLinger =
          typeof _smgmtIsLinger === "function" && _smgmtIsLinger(label);
        const outcome =
          _smgmtRunningLabels.has(label) || inLinger
            ? null
            : _smgmtOutcomeCache[label] || null;
        const parent = _sprintParents[label] || null;
        const cardHtml = _smgmtCardHtml(
          label,
          null,
          tickets,
          outcome,
          label === _smgmtNextUpLabel,
          parent,
          _smgmtFinishedLabels.has(label),
        );
        return (
          `<div class="smgmt-sprint-unit" id="smgmt-unit-${escHtml(label)}">` +
          cardHtml +
          `</div>`
        );
      })
      .join("");

    listEl.innerHTML =
      cards || '<div class="loading-msg">No sprints found.</div>';

    // Populate capacity gauges (reads localStorage + estimate cache)
    _smgmtInitCapacityGauges(orderedLabels);

    // Sprint capacity budget bars (issue #801): render now with cached data,
    // then lazily fetch budget settings + per-size cost averages and re-render.
    _smgmtRenderAllCapBars();
    _smgmtEnsureCapData(false);

    // Re-inject selection bar if tickets are selected
    if (_smgmtSelectedIssues.size > 0) _smgmtUpdateSelectionUI();

    // Re-apply cached finish cards (DOM was rebuilt), then refresh them from the API (issue #367 parity)
    for (const [lbl, fc] of Object.entries(_smgmtFinishCards)) {
      if (fc) _smgmtRenderFinishCard(lbl, fc.card, fc.branch, _smgmtRepo());
    }
    _smgmtLoadFinishCards();

    // Fetch outcome data for sprints that look completed (no open tickets)
    _smgmtFetchMissingOutcomes(orderedLabels, bySprint);

    // Load estimated hours from .commander/estimates/ for each sprint header
    _smgmtLoadEstimates(orderedLabels, bySprint);

    // Check estimator availability and inject re-estimate buttons (issue #561)
    _smgmtCheckEstimatorHealth();
    // Load sprint goals for header summary
    _smgmtLoadGoals(orderedLabels);

    // Inject pre-flight check banners for non-running sprint panes (issue #434)
    _preflightLoadBanners(orderedLabels, bySprint);

    // Load file-conflict badges for pending tickets (issue #579)
    _smgmtLoadConflicts(orderedLabels, bySprint);

    // Load dependency order hints for pending tickets (issue #581)
    _smgmtLoadDepOrder(orderedLabels, bySprint);

    // Render the execution-preview mini-rail per planned sprint (issue #809).
    // Called from here so it refreshes on every board re-render — i.e. after a
    // ticket is added, removed, or reordered.
    _smgmtLoadMiniRail(orderedLabels, bySprint);

    // Update cleanup button visibility (issue #457)
    _smgmtUpdateCleanupBtn(data);

    // Render label filter chips and re-apply current filter state (issue #546)
    _smgmtLabelFilterRender(issues);
    _smgmtLabelFilterApply();

    // Restore keyboard navigation focus ring after DOM rebuild (issue #549)
    _smgmtKbRestoreFocus();

    // Restore per-column sort state (issue #550)
    orderedLabels.forEach((lbl) => _smgmtApplySort(lbl));

    // Re-apply search filter after DOM rebuild (issue #552)
    _smgmtFilterApply();
  }

  function _smgmtLabelFilterRender(issues) {
    _smgmtLastLabelIssues = issues || [];
    const row = document.getElementById("smgmt-label-filter-row");
    if (!row) return;

    // Collect all label names from tickets
    const seen = new Set();
    (issues || []).forEach((iss) => {
      (iss.labels || []).forEach((l) => {
        seen.add(l.name);
        if (l.color) _smgmtLabelColors[l.name] = "#" + l.color;
      });
    });

    // Build ordered list: priority labels first (if present), then remainder alphabetically
    const priority = _SMGMT_FILTER_PRIORITY.filter((n) => seen.has(n));
    const rest = [...seen]
      .filter((n) => !_SMGMT_FILTER_PRIORITY.includes(n))
      .sort();
    const allLabels = [...priority, ...rest];

    if (allLabels.length === 0) {
      row.classList.add("is-empty");
      row.innerHTML = "";
      return;
    }

    const _SMGMT_LABEL_VISIBLE = 5;
    const expanded = row.dataset.expanded === "true";
    const visible = expanded
      ? allLabels
      : allLabels.slice(0, _SMGMT_LABEL_VISIBLE);
    const hidden = allLabels.length - _SMGMT_LABEL_VISIBLE;

    row.classList.remove("is-empty");
    row.innerHTML =
      visible
        .map((name) => {
          const active = !_smgmtDeactivatedLabels.has(name);
          const color = _smgmtLabelColors[name] || "var(--text-muted)";
          return `<button class="smgmt-lf-chip ${active ? "is-active" : "is-inactive"}"
                 data-label="${escHtml(name)}"
                 aria-pressed="${active}"
                 title="${active ? "Hide" : "Show"} tickets labeled &quot;${escHtml(name)}&quot;"
                 onclick="_smgmtLabelFilterToggle('${escHtml(name)}')">
                <span class="smgmt-lf-chip-dot" style="background:${color}"></span>
                ${escHtml(name)}
              </button>`;
        })
        .join("") +
      (hidden > 0 && !expanded
        ? `<button class="smgmt-lf-show-more" onclick="_smgmtLabelFilterToggleExpand(true)">+${hidden} more</button>`
        : hidden > 0 && expanded
          ? `<button class="smgmt-lf-show-more" onclick="_smgmtLabelFilterToggleExpand(false)">Show less</button>`
          : "");
  }

  function _smgmtLabelFilterApply() {
    if (_smgmtDeactivatedLabels.size === 0) {
      // Fast path: all active — show everything
      document.querySelectorAll(".smgmt-ticket[data-labels]").forEach((el) => {
        el.style.display = "";
      });
      return;
    }
    document.querySelectorAll(".smgmt-ticket[data-labels]").forEach((el) => {
      const raw = el.getAttribute("data-labels") || "";
      const ticketLabels = raw ? raw.split(",") : [];
      if (ticketLabels.length === 0) {
        // No labels → always visible
        el.style.display = "";
        return;
      }
      // Hidden only when every ticket label that appears as a chip is deactivated
      const allDeactivated = ticketLabels.every((n) =>
        _smgmtDeactivatedLabels.has(n),
      );
      el.style.display = allDeactivated ? "none" : "";
    });
  }

  function _smgmtIsFreshRerunSprint(label) {
    const parents = (_smgmtData && _smgmtData.sprint_parents) || {};
    if (!parents[label]) return false;
    const planState = ((_smgmtData && _smgmtData.sprint_plan_states) || {})[
      label
    ];
    // 'draft' is the unified-lifecycle spelling; 'planning' covers legacy files.
    return planState === "draft" || planState === "planning";
  }

  /** Optimistic board state after POST /rerun — child visible, parent emptied, no refresh lag. */
  function _smgmtApplyRerunOptimistic(
    parentLabel,
    subLabel,
    ticketNumbers,
  ) {
    if (!_smgmtData || !parentLabel || !subLabel) return;
    const nums = new Set(ticketNumbers || []);
    const issues = _smgmtData.issues || [];
    for (const iss of issues) {
      if (nums.has(iss.number)) iss.sprint_label = subLabel;
    }
    if (!_smgmtData.order) _smgmtData.order = [];
    if (!_smgmtData.order.includes(subLabel)) {
      const parentIdx = _smgmtData.order.indexOf(parentLabel);
      if (parentIdx >= 0) _smgmtData.order.splice(parentIdx + 1, 0, subLabel);
      else _smgmtData.order.push(subLabel);
    }
    if (!_smgmtData.sprint_parents) _smgmtData.sprint_parents = {};
    _smgmtData.sprint_parents[subLabel] = parentLabel;
    if (!_smgmtData.sprint_rerun_into) _smgmtData.sprint_rerun_into = {};
    _smgmtData.sprint_rerun_into[parentLabel] = subLabel;
    if (!_smgmtData.sprint_has_run) _smgmtData.sprint_has_run = {};
    _smgmtData.sprint_has_run[parentLabel] = true;
    if (!_smgmtData.sprint_plan_states) _smgmtData.sprint_plan_states = {};
    _smgmtData.sprint_plan_states[subLabel] = "draft";
    delete _smgmtOutcomeCache[parentLabel];
    delete _smgmtOutcomeCache[subLabel];
    if (_smgmtBySprint) {
      const moved = (_smgmtBySprint[parentLabel] || []).filter((t) =>
        nums.has(t.number),
      );
      _smgmtBySprint[subLabel] = [...(_smgmtBySprint[subLabel] || []), ...moved];
      _smgmtBySprint[parentLabel] = (_smgmtBySprint[parentLabel] || []).filter(
        (t) => !nums.has(t.number),
      );
    }
  }

  /** True when the history ledger says this label has its own run (not ticket labels). */
  function _smgmtHasLedgerRun(label) {
    return Boolean((_smgmtData?.sprint_has_run || {})[label]);
  }

  async function _smgmtFetchMissingOutcomes(orderedLabels, bySprint) {
    const repo = _smgmtRepo();
    if (!repo) return;
    const toFetch = [];
    for (const label of orderedLabels) {
      if (_smgmtRunningLabels.has(label)) continue;
      if (_smgmtIsFreshRerunSprint(label)) continue;
      if (_smgmtOutcomeCache[label] !== undefined) continue;
      // Resolved ancestors always ran; skip the ledger check for them (issue #1043).
      if (!_smgmtHasLedgerRun(label) && !_smgmtResolvedAncestors.has(label)) continue;
      toFetch.push(label);
    }
    await Promise.all(
      toFetch.map(async (label) => {
        try {
          const resp = await fetch(
            `/api/sprints/${encodeURIComponent(label)}/outcome?project=${encodeURIComponent(repo)}`,
          );
          if (resp.ok) {
            const outcome = await resp.json();
            _smgmtOutcomeCache[label] = outcome;
            // Ancestor rows update their compact header; regular cards inject a band.
            if (_smgmtResolvedAncestors.has(label)) {
              _smgmtUpdateAncestorRow(label, outcome);
            } else {
              _smgmtInjectOutcomeBand(label, outcome);
            }
          } else {
            _smgmtOutcomeCache[label] = null;
          }
        } catch (_) {
          _smgmtOutcomeCache[label] = null;
        }
      }),
    );
  }

  async function _smgmtLoadEstimates(orderedLabels, bySprint) {
    const repo = _smgmtRepo();
    if (!repo) return;
    for (const label of orderedLabels) {
      const tickets = bySprint[label] || [];
      if (tickets.length === 0) continue;
      // Populate reverse lookup for reactivity
      for (const t of tickets) _smgmtTicketToSprint[t.number] = label;
      const issueNums = tickets.map((t) => t.number).join(",");
      try {
        const resp = await fetch(
          `/api/estimates/batch?project=${encodeURIComponent(repo)}&issues=${issueNums}`,
        );
        if (!resp.ok) continue;
        const data = await resp.json();
        const estEl = document.getElementById(`smgmt-est-${label}`);
        if (estEl && data.complete && data.total_hours !== null) {
          const h = data.total_hours;
          const display = Number.isInteger(h)
            ? `${h}h`
            : `${parseFloat(h.toFixed(1))}h`;
          estEl.textContent = `${display} estimated`;
        }
        _smgmtSetSprintTokenEl(label, data);
        // Cache per-issue size/confidence and update visible rows
        if (data.issues) {
          for (const [numStr, est] of Object.entries(data.issues)) {
            _estDataCache[parseInt(numStr, 10)] = est;
          }
          for (const t of tickets) {
            _smgmtUpdateEstimateBadge(t.number);
          }
          _smgmtUpdateColRollup(label, tickets);
          _smgmtUpdateCapacityGauge(label);
        }
      } catch (_) {
        // fail silently — leave as "— estimated"
      }
    }
  }

  async function _smgmtLoadConflicts(orderedLabels, bySprint) {
    const repo = _smgmtRepo();
    if (!repo) return;
    for (const label of orderedLabels) {
      if (_smgmtRunningLabels.has(label)) continue;
      if (_smgmtFinishedLabels.has(label)) continue;
      const tickets = bySprint[label] || [];
      const pending = tickets.filter(
        (t) => (t.status || "backlog") === "backlog",
      );
      if (pending.length < 2) continue;
      // Clear stale entries for this sprint's tickets before repopulating
      for (const t of pending) delete _smgmtConflictsByIssue[t.number];
      try {
        const resp = await fetch(
          `/api/sprints/${encodeURIComponent(label)}/conflicts?project=${encodeURIComponent(repo)}`,
        );
        if (!resp.ok) continue;
        const data = await resp.json();
        for (const c of data.conflicts || []) {
          if (!_smgmtConflictsByIssue[c.ticket1_id])
            _smgmtConflictsByIssue[c.ticket1_id] = [];
          if (!_smgmtConflictsByIssue[c.ticket2_id])
            _smgmtConflictsByIssue[c.ticket2_id] = [];
          _smgmtConflictsByIssue[c.ticket1_id].push({
            partnerId: c.ticket2_id,
            partnerTitle: c.ticket2_title,
            sharedFiles: c.shared_files,
          });
          _smgmtConflictsByIssue[c.ticket2_id].push({
            partnerId: c.ticket1_id,
            partnerTitle: c.ticket1_title,
            sharedFiles: c.shared_files,
          });
        }
        for (const t of pending) _smgmtUpdateConflictBadge(t.number);
      } catch (_) {
        // fail silently
      }
    }
  }

  async function _smgmtLoadDepOrder(orderedLabels, bySprint) {
    const repo = _smgmtRepo();
    if (!repo) return;
    for (const label of orderedLabels) {
      if (_smgmtRunningLabels.has(label)) continue;
      if (_smgmtFinishedLabels.has(label)) continue;
      const tickets = bySprint[label] || [];
      const pending = tickets.filter(
        (t) => (t.status || "backlog") === "backlog",
      );
      if (pending.length < 2) continue;
      for (const t of pending) delete _smgmtDepOrderByIssue[t.number];
      try {
        const resp = await fetch(
          `/api/sprints/${encodeURIComponent(label)}/dep-order?project=${encodeURIComponent(repo)}`,
        );
        if (!resp.ok) continue;
        const data = await resp.json();
        if (data.has_cycle) {
          const cycleSet = new Set((data.in_cycle_tickets || []).map(String));
          for (const t of pending) {
            if (cycleSet.has(String(t.number))) {
              _smgmtDepOrderByIssue[t.number] = {
                upstream: [],
                downstream: [],
                inCycle: true,
              };
            }
          }
        } else {
          for (const [idStr, hint] of Object.entries(data.dep_hints || {})) {
            const num = parseInt(idStr, 10);
            _smgmtDepOrderByIssue[num] = {
              upstream: hint.upstream || [],
              downstream: hint.downstream || [],
              inCycle: false,
            };
          }
        }
        for (const t of pending) _smgmtUpdateDepOrderBadge(t.number);
      } catch (_) {
        // fail silently
      }
    }
  }

  async function _smgmtLoadGoals(orderedLabels) {
    const repo = _smgmtRepo();
    if (!repo) return;
    for (const label of orderedLabels) {
      const goalEl = document.getElementById(`smgmt-goal-${label}`);
      if (!goalEl) continue;
      try {
        const resp = await fetch(
          `/api/sprints/goal?project=${encodeURIComponent(repo)}&sprint=${encodeURIComponent(label)}`,
        );
        if (!resp.ok) continue;
        const data = await resp.json();
        const goal = (data.goal || "").trim();
        if (goal) {
          goalEl.textContent = goal;
          goalEl.title = goal;
          goalEl.style.display = "";
        }
      } catch (_) {
        // fail silently
      }
    }
  }

  function _smgmtOutcomeBandHtml(label, outcome) {
    const st = outcome.sprint_status;
    const paneState = outcome.state || "";
    const c = outcome.counts || {};
    const dur = _fmtWallClock(outcome.wall_clock_secs);
    const ts = outcome.ended_at
      ? st === "completed"
        ? `ended ${outcome.ended_at}`
        : `stopped ${outcome.ended_at}`
      : "";
    const issues = outcome.issues || [];

    // Segmented bar: one block per ticket (issue #613)
    let segBarHtml = "";
    if (issues.length > 0) {
      const blocks = issues
        .map((iss) => {
          const o = iss.outcome || "skipped";
          let blockClass = "seg-pending";
          if (o === "done") blockClass = "seg-done";
          else if (o === "failed") blockClass = "seg-failed";
          else if (o === "skipped") blockClass = "seg-skipped";
          return `<div class="seg-block ${blockClass}"></div>`;
        })
        .join("");
      segBarHtml = `<div class="smgmt-seg-bar">${blocks}</div>`;
    }

    // PR + Sprint Summary links for finished/completed state
    let linksHtml = "";
    if (paneState === "completed" || st === "completed") {
      const prNum = outcome.pr_number;
      const prUrl = outcome.pr_url;
      const sumNum = outcome.summary_issue_num;
      const sumUrl = outcome.summary_issue_url;
      const prLink =
        prNum && prUrl
          ? `<a href="${escHtml(prUrl)}" target="_blank" rel="noopener" class="oc-pr-link"><i class="ti ti-git-pull-request"></i> PR #${prNum}</a>`
          : "";
      const sumLink =
        sumNum && sumUrl
          ? `<a href="${escHtml(sumUrl)}" target="_blank" rel="noopener" class="oc-summary-link"><i class="ti ti-file-description"></i> #${sumNum} Sprint Summary</a>`
          : sumNum
            ? `<span class="oc-summary-link"><i class="ti ti-file-description"></i> #${sumNum} Sprint Summary</span>`
            : "";
      if (prLink || sumLink) {
        linksHtml = `<div class="oc-band-links">${prLink}${sumLink}</div>`;
      }
    }

    return `<div class="smgmt-outcome-band ${escHtml(st || "")}">
      <div class="smgmt-outcome-stat"><span class="onum green">${c.done || 0}</span><span class="olbl">Completed</span></div>
      <div class="smgmt-outcome-stat"><span class="onum ${c.failed ? "red" : "muted"}">${c.failed || 0}</span><span class="olbl">Failed</span></div>
      <div class="smgmt-outcome-stat"><span class="onum muted">${c.skipped || 0}</span><span class="olbl">Skipped</span></div>
      <span class="oc-spacer"></span>
      ${segBarHtml}
      <div class="smgmt-outcome-dur"><i class="ti ti-clock" style="vertical-align:-1px;"></i> ${escHtml(dur)}${ts ? " · " + escHtml(ts) : ""}</div>
      ${linksHtml}
    </div>`;
  }

  function _smgmtOutcomeTicketListHtml(issues, label, repo) {
    if (!issues || issues.length === 0) return "";
    const safeLabel = label ? escHtml(label) : "";
    const safeRepo = repo ? escHtml(repo) : "";
    return issues
      .map((iss) => {
        const o = iss.outcome || "skipped";
        let circle = "";
        if (o === "done")
          circle = '<div class="smgmt-ticket-circle done">✓</div>';
        else if (o === "failed")
          circle = '<div class="smgmt-ticket-circle failed">✕</div>';
        else circle = '<div class="smgmt-ticket-circle skipped">−</div>';

        const elapsed = `<span class="smgmt-ticket-elapsed">${escHtml(_fmtElapsed(iss.elapsed_secs))}</span>`;
        const rejLabel =
          o === "failed"
            ? '<span class="smgmt-lbl-rejected">TESTER REJECTED</span>'
            : "";

        const viewLogBtn =
          safeLabel && safeRepo
            ? `<button class="btn-view-log" title="View issue log"
                onclick="event.stopPropagation();openLvIssueLog(${iss.number},'${safeLabel}','${safeRepo}')">
             <i class="ti ti-file-text"></i></button>`
            : "";

        return `<div class="smgmt-ticket" data-issue="${iss.number}" data-labels="" draggable="false">
        ${circle}
        <a class="smgmt-ticket-num" href="${safeRepo ? `https://github.com/${safeRepo}/issues/${iss.number}` : "#"}" target="_blank" rel="noopener">#${iss.number}</a>
        <span class="smgmt-ticket-title" title="${escHtml(iss.title)}">${escHtml(iss.title)}</span>
        ${rejLabel}
        ${viewLogBtn}
        ${elapsed}
        <button class="t-details-btn" onclick="event.stopPropagation();toggleTicketRow('${safeLabel}',${iss.number})">
          <span class="t-dbtn-label">Details</span> <span id="caret-${safeLabel}-${iss.number}">▼</span>
        </button>
      </div>
      <div class="ticket-expand" id="ex-${safeLabel}-${iss.number}" style="display:none">
        <div class="ex-row">
          <span class="ex-label">Conflicts</span>
          <span class="ex-conflicts-val">—</span>
        </div>
        <div class="ex-row">
          <span class="ex-label">Execution</span>
          <span class="ex-exec-val">—</span>
        </div>
        <div class="ex-actions">
          <button class="ex-btn" onclick="event.stopPropagation();_smgmtReEstimate(${iss.number},this)"><i class="ti ti-sparkles" style="font-size:12px"></i> Re-estimate</button>
          <button class="ex-btn" onclick="event.stopPropagation();_smgmtRowMenuOpen(event,${iss.number},'${safeLabel}',false)"><i class="ti ti-arrow-right" style="font-size:12px"></i> Move to sprint</button>
          <button class="ex-btn ex-btn-danger" onclick="event.stopPropagation();_smgmtCloseIssueOpen(${iss.number})"><i class="ti ti-x" style="font-size:12px"></i> Close ticket</button>
        </div>
      </div>`;
      })
      .join("");
  }

  async function _smgmtLoadFinishCards() {
    const repo = _smgmtRepo();
    if (!repo || !_smgmtData) return;
    const order =
      _smgmtData.order && _smgmtData.order.length
        ? _smgmtData.order
        : (_smgmtData.sprints || []).map((n) => `sprint-${n}`);
    await Promise.allSettled(
      order.map(async (label) => {
        if (_smgmtIsFreshRerunSprint(label)) return;
        try {
          const [cardRes, branchRes] = await Promise.all([
            fetch(
              `/api/sprints/${encodeURIComponent(label)}/finish-card?project=${encodeURIComponent(repo)}`,
            ),
            fetch(
              `/api/sprints/${encodeURIComponent(label)}/branch-status?project=${encodeURIComponent(repo)}`,
            ).catch(() => null),
          ]);
          if (!cardRes.ok) {
            console.warn(
              `finish-card: unexpected ${cardRes.status} for ${label}`,
            );
            return;
          }
          const cardData = await cardRes.json();
          if (cardData.state === "no_data") return; // sprint never run — no card shown
          const branchData =
            branchRes && branchRes.ok
              ? await branchRes.json()
              : { exists: false };
          _smgmtFinishCards[label] = { card: cardData, branch: branchData };
          _smgmtRenderFinishCard(label, cardData, branchData, repo);
        } catch (e) {
          console.warn("finish-card load error for", label, e);
        }
      }),
    );
  }

  function _smgmtRenderFinishCard(label, cardData, branchData, repo) {
    // Patch PR link into outcome band if it's visible (issue #613)
    if (branchData && branchData.pr_url && branchData.pr_number) {
      const sprintCard = document.getElementById(`smgmt-card-${label}`);
      if (sprintCard) {
        let linksEl = sprintCard.querySelector(".oc-band-links");
        if (!linksEl) {
          const band = sprintCard.querySelector(".smgmt-outcome-band");
          if (band) {
            linksEl = document.createElement("div");
            linksEl.className = "oc-band-links";
            band.appendChild(linksEl);
          }
        }
        if (linksEl && !linksEl.querySelector(".oc-pr-link")) {
          const prLink = document.createElement("a");
          prLink.href = branchData.pr_url;
          prLink.target = "_blank";
          prLink.rel = "noopener";
          prLink.className = "oc-pr-link";
          prLink.innerHTML = `<i class="ti ti-git-pull-request"></i> PR #${branchData.pr_number}`;
          linksEl.insertBefore(prLink, linksEl.firstChild);
        }
      }
    }

    if (cardData.state === "no_data") return; // sprint never run — nothing to display

    const cardEl = document.getElementById(`smgmt-finish-card-${label}`);
    const blockEl = document.getElementById(`smgmt-card-${label}`);
    if (!cardEl || !blockEl) return;
    // Only show completed/has_rework finish card when PR + summary issue both exist
    const isFinished =
      cardData.state === "completed" || cardData.state === "has_rework";
    const hasPr = !!(branchData && branchData.pr_url);
    const hasSummary = !!cardData.summary_issue_num;
    if (isFinished && !(hasPr && hasSummary)) {
      cardEl.style.display = "none";
      return;
    }
    cardEl.style.display = "";
    cardEl.className = `smgmt-finish-card sfc-${cardData.state}`;
    cardEl.innerHTML = _smgmtFinishCardInnerHtml(cardData, branchData, repo);
    blockEl.classList.add("smgmt-has-card");
  }

  function _smgmtFinishCardInnerHtml(cardData, branchData, repo) {
    const state = cardData.state;
    const n = cardData.sprint_number;
    const branchName = `sprint/sprint-${n}`;
    const branchUrl = `https://github.com/${escHtml(repo)}/tree/${branchName}`;
    const branchLink =
      branchData && branchData.exists
        ? `<a href="${branchUrl}" target="_blank" rel="noopener" class="sfc-branch-link"><i class="ti ti-git-branch"></i> ${escHtml(branchName)}</a>`
        : `<a href="${branchUrl}" target="_blank" rel="noopener" class="sfc-branch-link sfc-branch-link--warn" title="Could not verify branch exists on GitHub"><i class="ti ti-alert-triangle"></i> ${escHtml(branchName)}</a>`;
    if (state === "running") return _sfcRunningHtml(cardData, branchLink, n);
    if (state === "completed")
      return _sfcCompletedHtml(cardData, branchLink, n, branchData);
    // Legacy pane states map to unified lifecycle (sprint-lifecycle.md P4).
    if (state === "has_rework" || state === "cancelled") {
      return _sfcHasReworkHtml(cardData, branchLink, n, branchData);
    }
    return "";
  }

  /** Finished pipeline labels — only these block Run Sprint (mirrors sprint_manager._is_dispatchable). */
  const _NON_DISPATCHABLE_LABELS = new Set([
    "UAT",
    "UAT-approved",
    "released",
  ]);

  function _smgmtHasDispatchableTickets(tickets) {
    return tickets.some((t) => {
      const names = (t.labels || []).map((l) => l.name);
      return !names.some((n) => _NON_DISPATCHABLE_LABELS.has(n));
    });
  }

  function _smgmtCardHtml(
    label,
    n,
    tickets,
    outcome,
    isNext,
    parent,
    finished,
  ) {
    const isRunning = _smgmtRunningLabels.has(label);
    const isLinger =
      !isRunning && typeof _smgmtIsLinger === "function" && _smgmtIsLinger(label);
    const isRunningView = isRunning || isLinger;
    // Running sprints default to collapsed on the Board — their live detail lives
    // in the Running pane, reachable via the header deep-link (hotfix #5). The
    // collapse pref is tri-state: '1' = collapsed, '0' = explicitly expanded,
    // absent = default (collapsed for running, expanded otherwise).
    let isCollapsed = isRunning;
    try {
      const _pref = localStorage.getItem("sprintColumn_" + label + "_collapsed");
      if (_pref === "1") isCollapsed = true;
      else if (_pref === "0") isCollapsed = false;
    } catch (_) {}

    const isFreshRerun = _smgmtIsFreshRerunSprint(label);
    if (isFreshRerun) outcome = null;

    const planState = (
      ((_smgmtData && _smgmtData.sprint_plan_states) || {})[label] || ""
    ).toLowerCase();
    const planBlocksPostRun = [
      "planned",
      "draft",
      "planning",
    ].includes(planState);

    const outcomeLifecycle = ((outcome && outcome.lifecycle) || "").toLowerCase();
    const outcomeState =
      outcome &&
      (outcome.state ||
        (outcome.sprint_status === "completed" ? "completed" : null));
    const hasLedgerRun = _smgmtHasLedgerRun(label);
    const isHasRework =
      hasLedgerRun &&
      (outcomeLifecycle === "needs_rework" ||
        outcomeState === "has_rework" ||
        outcomeState === "cancelled");
    const isReadyToMerge =
      hasLedgerRun &&
      (outcomeLifecycle === "ready_to_merge" ||
        (outcomeLifecycle === "completed" && outcomeState === "completed"));
    const isAwaitingMerge =
      isReadyToMerge ||
      (finished && !isRunning && !isHasRework && !planBlocksPostRun);
    const showRunningChrome = isRunningView && !isAwaitingMerge;
    const isPostRun =
      !isRunningView && !planBlocksPostRun && hasLedgerRun;
    // Run is only for first attempts: post-run labels (incl. has-rework) re-run
    // into a child sub-sprint instead (P0 — no same-label re-dispatch).
    const canRun = tickets.length >= 1 && _smgmtHasDispatchableTickets(tickets);

    // Re-run Sprint button: child sprint for fully completed/stopped runs (not has_rework)
    const rerunDisabled = _smgmtAnySprintRunning ? "disabled" : "";
    const rerunTitle = _smgmtAnySprintRunning
      ? 'title="Cannot re-run: another sprint is currently running."'
      : "";
    const childLabel = _smgmtNextChildLabel(label);
    const childDisplay = sprintLabelDisplay(childLabel).replace("Sprint ", "");
    const rerunBtn = `<button class="smgmt-run-btn smgmt-run-btn--rerun" ${rerunDisabled} ${rerunTitle}
                      onclick="smgmtRerunSprint('${escHtml(label)}')">
                      <i class="ti ti-refresh"></i> Re-run → ${escHtml(childDisplay)}</button>`;

    const rerunInto = (_smgmtData?.sprint_rerun_into || {})[label];
    const rerunChildDisplay = rerunInto
      ? sprintLabelDisplay(rerunInto).replace("Sprint ", "")
      : "";

    // Planning cards: Run Sprint. Running cards: Cancel. Any post-run card
    // (completed or has-rework): Re-run → child sub-sprint. Same-label
    // re-dispatch is blocked server-side (sprint-lifecycle redesign P0) — a
    // label whose run ended is terminal, so has-rework cards must route to the
    // re-run flow even when tickets are still on the column.
    let actionBtn;
    if (isRunning) {
      actionBtn = `<button class="smgmt-cancel-btn" onclick="smgmtCancelSprint('${escHtml(label)}')">
                    <i class="ti ti-player-stop"></i> Cancel sprint</button>`;
    } else if (isLinger) {
      actionBtn = `<span class="smgmt-linger-note">Finished — snapshot kept 1h</span>`;
    } else if (isHasRework && rerunInto && tickets.length === 0) {
      // Tickets moved to a child re-run — run the child, not the empty parent label.
      actionBtn = `<button class="smgmt-run-btn" ${rerunDisabled} ${rerunTitle}
                    onclick="smgmtRunSprint('${escHtml(rerunInto)}')">
                    <i class="ti ti-player-play"></i> Run → ${escHtml(rerunChildDisplay)}</button>`;
    } else if (isHasRework || isPostRun) {
      actionBtn = rerunBtn;
    } else if (_smgmtAnySprintRunning) {
      actionBtn = `<button class="smgmt-run-btn smgmt-run-btn--blocked"
                    title="Another sprint is running"
                    onclick="smgmtRunBlockedToast()">
                    <i class="ti ti-player-play"></i> Run Sprint</button>`;
    } else {
      // Approved / planning card — the only state where the sprint is ready to be
      // dispatched. The Run-on-schedule toggle is rendered here and nowhere else,
      // so it is hidden on running / post-run / linger cards (issue #863, AC2).
      const runDisabled = !canRun ? "disabled" : "";
      const runTitle = !canRun
        ? 'title="No dispatchable tickets — remaining items are already SIT/UAT or in progress"'
        : "";
      const schedToggle =
        typeof _smgmtSchedToggleHtml === "function"
          ? _smgmtSchedToggleHtml(label)
          : "";
      actionBtn = `<button class="smgmt-run-btn" ${runDisabled} ${runTitle}
                    onclick="smgmtRunSprint('${label}')">
                    <i class="ti ti-player-play"></i> Run Sprint</button>${schedToggle}`;
    }

    const isOutcomeCompleted =
      isReadyToMerge || isHasRework || outcomeState === "completed";
    // Show Merge Sprint when the sprint is post-run (ready_to_merge / needs_rework).
    const finishHidden =
      isOutcomeCompleted || (isPostRun && !outcome) ? "" : "hidden";
    const finishDisabled =
      isReadyToMerge && tickets.length === 0 ? "disabled" : "";

    // Outcome state: build band + ticket rows if outcome is cached
    let outcomeBandHtml = "";
    let outcomeCardClass = "";
    let outcomeBadgeHtml = "";
    let headerMetaHtml = "";
    let ticketsContainerHtml = "";
    let rollupItems = tickets;

    if (outcome && (outcome.sprint_status || outcome.state) && !planBlocksPostRun) {
      const meta = _smgmtStateMeta(outcome, (outcome.issues || []).length);
      outcomeCardClass = " " + meta.cardClass;
      outcomeBadgeHtml = `<span class="smgmt-state-badge ${meta.badgeCls}">${escHtml(meta.badge)}</span>`;
      if (meta.state === "needs_rework") {
        const _metaSecs = outcome.wall_clock_secs;
        const _metaStopped = outcome.ended_at
          ? _fmtStoppedAt(outcome.ended_at)
          : null;
        const _metaParts = [];
        if (_metaSecs != null) _metaParts.push(_fmtRunningTime(_metaSecs));
        if (_metaStopped) _metaParts.push(`stopped ${_metaStopped}`);
        if (_metaParts.length)
          headerMetaHtml = `<span class="smgmt-sprint-meta">${escHtml(_metaParts.join(" · "))}</span>`;

        const _elapsedByNum = {};
        if (outcome.issues) {
          for (const _oi of outcome.issues) {
            if (_oi.elapsed_secs != null)
              _elapsedByNum[_oi.number] = _oi.elapsed_secs;
          }
        }
        // Keep planning view: rework tickets stay actionable; the finish-card hat shows the summary.
        ticketsContainerHtml =
          tickets.length > 0
            ? tickets
                .map((t) =>
                  _smgmtTicketRowHtml(t, label, _elapsedByNum[t.number] ?? null),
                )
                .join("")
            : '<div class="smgmt-drop-hint">Drop tickets here</div>';
      } else {
        outcomeBandHtml = _smgmtOutcomeBandHtml(label, outcome);
        const issueList = outcome.issues || [];
        ticketsContainerHtml = _smgmtOutcomeTicketListHtml(
          issueList,
          label,
          _smgmtRepo(),
        );
        rollupItems = issueList.map((i) => ({ number: i.number }));
      }
    } else if (isRunningView) {
      ticketsContainerHtml = _smgmtRunningTicketRowsHtml(label, tickets);
    } else {
      // Planning view ticket rows
      ticketsContainerHtml =
        tickets.length > 0
          ? tickets.map((t) => _smgmtTicketRowHtml(t, label)).join("")
          : '<div class="smgmt-drop-hint">Drop tickets here</div>';
      // A summary issue exists but no detailed outcome cached — still mark finished
      // so the board agrees with the nav pill (and NEXT UP/pre-flight are suppressed).
      if (finished) {
        outcomeBadgeHtml = `<span class="smgmt-state-badge state-finished">READY TO MERGE</span>`;
      }
    }

    // Sprint summary: budget bar + optional goal (progress row removed — mock v5)
    const summaryHtml = `<div class="sc-budget-section">
      <div class="sc-budget-head">
        <span class="sc-budget-eyebrow">SPRINT BUDGET</span>
        <span class="sc-budget-forecast" id="sc-budget-forecast-${escHtml(label)}"></span>
      </div>
      <div class="cap" id="smgmt-cap-${escHtml(label)}"></div>
      <div class="smgmt-sprint-goal-text" id="smgmt-goal-${escHtml(label)}" style="display:none"></div>
    </div>
    <div class="sc-preview-slot" id="sc-preview-${escHtml(label)}"></div>`;

    // Run-stats / dispatch-log forensics live in History only (sprint-lifecycle.md P4).
    const logHtml = "";
    const cancelBannerHtml = "";

    const plannedBadge =
      !isNext && !finished && !isPostRun && !outcomeBadgeHtml
        ? '<span class="sc-planned-badge">PLANNED</span>'
        : "";
    const blockedHint =
      _smgmtAnySprintRunning && !isPostRun && !isRunningView
        ? `<span class="sc-blocked-hint">blocked: ${_smgmtRunningBlockerShort()} running</span>`
        : "";
    const parentLineage =
      parent && !isFreshRerun
        ? `<span class="smgmt-sprint-lineage" title="Child sprint spawned from ${escHtml(parent)}">← from ${escHtml(sprintLabelDisplay(parent))}</span>`
        : "";

    const live = isRunningView
      ? (typeof _smgmtLingerLive === "function"
          ? _smgmtLingerLive(label)
          : null) ||
        _smgmtLiveCache[label] ||
        null
      : null;
    const runningComplete = live
      ? (live.done_count || 0) +
        (live.failed_count || 0) +
        (live.skipped_count || 0)
      : 0;
    const runningTotal = live
      ? live.total_count || tickets.length
      : tickets.length;
    const runningRatio =
      runningTotal > 0 ? `${runningComplete}/${runningTotal}` : "—";
    const runningElapsed =
      live && live.time_spent_sec > 0
        ? `<span class="smgmt-sprint-meta" id="smgmt-elapsed-${escHtml(label)}">elapsed ${_fmtRunningTime(live.time_spent_sec)}</span>`
        : `<span class="smgmt-sprint-meta" id="smgmt-elapsed-${escHtml(label)}"></span>`;
    const runningBadgeHtml = showRunningChrome
      ? `<span class="smgmt-running-badge" id="smgmt-running-badge-${escHtml(label)}"><span class="smgmt-running-badge-dot"></span>${isLinger ? "done" : runningRatio}</span>`
      : "";
    const runningStripeHtml = showRunningChrome
      ? '<div class="smgmt-running-stripe"></div>'
      : "";
    const runningClass = isRunning
      ? " smgmt-running"
      : isLinger && !isAwaitingMerge
        ? " smgmt-linger"
        : "";

    const collapsedClass = isCollapsed ? " smgmt-collapsed" : "";
    const collapseLabel =
      (isCollapsed ? "Expand " : "Collapse ") +
      escHtml(sprintLabelDisplay(label));
    return `
      <div class="smgmt-sprint-card sc-v5${outcomeCardClass}${runningClass}${collapsedClass}" id="smgmt-card-${escHtml(label)}"
           ondragover="${isRunning ? "" : `_smgmtDragOver(event, '${escHtml(label)}')`}"
           ondragleave="${isRunning ? "" : `_smgmtDragLeave(event)`}"
           ondrop="${isRunning ? "" : `_smgmtDropOnSprint(event, '${escHtml(label)}')`}">
        ${runningStripeHtml}
        <div class="sc-header smgmt-sprint-header">
          <div class="smgmt-sprint-header-left sc-header-left">
            <button class="smgmt-collapse-btn" id="smgmt-collapse-btn-${escHtml(label)}"
                    onclick="smgmtToggleCollapse('${escHtml(label)}')"
                    aria-label="${collapseLabel}"
                    title="${collapseLabel}"
                    onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();smgmtToggleCollapse('${escHtml(label)}');}">
              <i class="ti ti-chevron-down"></i></button>
            <span class="smgmt-sprint-name sc-name">${escHtml(sprintLabelDisplay(label))}</span>
            ${parentLineage}
            ${runningBadgeHtml}
            ${showRunningChrome ? `<button type="button" class="smgmt-running-link" title="Open in the Running pane" onclick="event.stopPropagation();_smgmtShowSubView('running')"><i class="ti ti-player-play"></i> Open in Running</button>` : ""}
            ${isNext && !isRunning ? '<span class="smgmt-next-badge">NEXT UP</span>' : ""}
            ${plannedBadge}
            ${outcomeBadgeHtml}
            ${headerMetaHtml}
            <span class="sc-meta smgmt-sprint-count" id="smgmt-col-rollup-${escHtml(label)}">${_smgmtRollupText(rollupItems)}</span>
          </div>
          <div class="smgmt-sprint-header-right sc-header-right">
            <button class="smgmt-delete-btn"
                    aria-label="Delete sprint"
                    title="Delete sprint"
                    onclick="smgmtDeleteSprint('${escHtml(label)}')">
              <i class="ti ti-trash"></i></button>
            ${actionBtn}
            ${blockedHint}
            ${isRunning ? runningElapsed : ""}
            <button class="smgmt-finish-btn sc-merge-link ${finishHidden}" ${finishDisabled}
                    title="${finishDisabled ? "No open tickets" : "Merge sprint"}"
                    onclick="smgmtFinishSprint('${escHtml(label)}')">
              <i class="ti ti-flag-check"></i> Merge Sprint</button>
          </div>
        </div>
        ${(function() {
          const _ss = _smgmtCardStatusSentence(label, {
            isRunning, isLinger, isNext, isHasRework, isReadyToMerge,
            isAwaitingMerge, planState, outcome, tickets,
          });
          return _ss ? `<div class="sc-status-line">${escHtml(_ss)}</div>` : "";
        })()}
        ${cancelBannerHtml}
        ${outcomeBandHtml}
        ${summaryHtml}
        <div class="smgmt-sprint-tickets sc-tickets" id="smgmt-tickets-${escHtml(label)}">
          ${ticketsContainerHtml}
        </div>
        ${logHtml}
      </div>`;
  }

  /** Live ticket rows for a running sprint (board sc-v5 card + legacy running card). */
  function _smgmtRunningTicketRowsHtml(label, tickets) {
    const live = _smgmtLiveCache[label] || null;
    const currentTicket = live ? live.current_ticket : null;
    const liveIssues =
      live && live.issues && live.issues.length > 0 ? live.issues : [];
    const liveByNum = {};
    liveIssues.forEach((i) => {
      liveByNum[i.number] = i;
    });

    const sourceTickets = (liveIssues.length > 0 ? liveIssues : tickets)
      .slice()
      .sort((a, b) => (a.dispatch_level || 0) - (b.dispatch_level || 0));
    const cardRepo = _smgmtRepo();

    if (sourceTickets.length === 0) {
      return '<div class="smgmt-drop-hint">No tickets in this sprint</div>';
    }

    let prevLevel = 0;
    return sourceTickets
      .map((t) => {
        const liveIss = liveByNum[t.number];
        const liveStatus = liveIss ? liveIss.status : null;
        const agentStatus = liveIss ? liveIss.agent_status : null;
        const ticketLevel =
          (liveIss && liveIss.dispatch_level) || t.dispatch_level || 0;

        let sepHtml = "";
        if (ticketLevel > 0 && ticketLevel > prevLevel) {
          sepHtml = `<div class="level-sep">
          <span class="level-sep-num">Level ${ticketLevel}</span>
          <span class="level-sep-desc">· runs after level ${prevLevel} completes</span>
        </div>`;
        }
        if (ticketLevel > 0) prevLevel = ticketLevel;

        const isActiveAgent =
          agentStatus &&
          (agentStatus.endsWith("_running") ||
            agentStatus.endsWith("_dispatched"));
        let indicator = "";
        if (liveStatus === "done") {
          indicator =
            '<div class="smgmt-ticket-indicator"><div class="circle-done">&#10003;</div></div>';
        } else if (agentStatus === "failed" || liveStatus === "skipped") {
          indicator =
            '<div class="smgmt-ticket-indicator"><div class="circle-failed">&#10005;</div></div>';
        } else if (
          liveStatus === "in-progress" ||
          isActiveAgent ||
          (currentTicket && t.number === currentTicket.number)
        ) {
          indicator =
            '<div class="smgmt-ticket-indicator"><div class="ring"></div></div>';
        } else {
          indicator =
            '<div class="smgmt-ticket-indicator"><div class="circle-pending"></div></div>';
        }

        const issueUrl =
          t.url ||
          (cardRepo ? `https://github.com/${cardRepo}/issues/${t.number}` : "#");
        const sizeVal = (liveIss && liveIss.size) || t.size || "";
        const sizePillHtml = sizeVal
          ? `<span class="smgmt-ticket-size-pill" title="≈${(liveIss && liveIss.minutes) || _sizeMinutes(sizeVal)} min">${escHtml(sizeVal)}</span>`
          : "";
        const runSizeAttr = sizeVal ? ` data-size="${escHtml(sizeVal)}"` : "";
        const agentTagHtml =
          liveIss && liveIss.agent
            ? `<span class="smgmt-ticket-agent-tag ${_smgmtAgentTagClass(liveIss.agent)}">${escHtml(liveIss.agent.toUpperCase())}</span>`
            : "";
        const elapsedStr = liveIss
          ? _fmtTicketElapsed(liveIss.elapsed_secs)
          : null;
        const elapsedHtml = elapsedStr
          ? `<span class="smgmt-ticket-elapsed">${elapsedStr}</span>`
          : "";
        const runTicketLabels = escHtml(
          (t.labels || []).map((l) => l.name).join(","),
        );
        return (
          sepHtml +
          `<div class="smgmt-ticket" data-issue="${t.number}" data-labels="${runTicketLabels}" draggable="false"${runSizeAttr}>
        ${indicator}
        <a class="smgmt-ticket-num" href="${escHtml(issueUrl)}" target="_blank"
           rel="noopener">#${t.number}</a>
        <span class="smgmt-ticket-title" title="${escHtml(t.title)}">${escHtml(t.title)}</span>
        ${sizePillHtml}${agentTagHtml}${elapsedHtml}
      </div>`
        );
      })
      .join("");
  }

  /** Level summary for the board running banner, e.g. "level 2 of 3". */
  function _smgmtRunningLevelText(live) {
    const levels = (live && live.levels) || [];
    if (levels.length > 1) {
      const active = levels.find((l) => l.state === "active");
      const cur = active ? active.level : levels[levels.length - 1].level;
      return `level ${cur} of ${levels.length}`;
    }
    const issues = (live && live.issues) || [];
    const levelNums = [...new Set(issues.map((i) => i.dispatch_level || 0 || 1))]
      .filter((l) => l > 0)
      .sort((a, b) => a - b);
    if (levelNums.length <= 1) return null;
    let current = levelNums[0];
    for (const lvl of levelNums) {
      const group = issues.filter((i) => (i.dispatch_level || 0 || 1) === lvl);
      const allDone =
        group.length > 0 &&
        group.every(
          (i) =>
            i.status === "done" ||
            i.status === "skipped" ||
            i.agent_status === "failed",
        );
      if (!allDone) {
        current = lvl;
        break;
      }
      current = lvl;
    }
    return `level ${current} of ${levelNums.length}`;
  }

  /** Compact board banner with a link to the Running sub-view (hotfix 0612). */
  function _smgmtRunningBoardBannerHtml(label, tickets) {
    const isLinger =
      typeof _smgmtIsLinger === "function" && _smgmtIsLinger(label);
    const live =
      (typeof _smgmtLingerLive === "function" ? _smgmtLingerLive(label) : null) ||
      _smgmtLiveCache[label] ||
      null;
    const doneCount = live ? live.done_count || 0 : 0;
    const failedCount = live ? live.failed_count || 0 : 0;
    const skippedCount = live ? live.skipped_count || 0 : 0;
    const totalCount = live ? live.total_count || tickets.length : tickets.length;
    const completeCount = doneCount + failedCount + skippedCount;
    const timeSpentSec = live ? live.time_spent_sec || 0 : 0;
    const levelText = _smgmtRunningLevelText(live);
    const parts = [
      isLinger
        ? `${escHtml(sprintLabelDisplay(label))} finished (snapshot)`
        : `${escHtml(sprintLabelDisplay(label))} is running`,
      `${completeCount}/${totalCount} done`,
      timeSpentSec > 0 ? _fmtRunningTime(timeSpentSec) : null,
      levelText,
    ].filter(Boolean);
    const safeLabel = escHtml(label);
    const lingerCls = isLinger ? " linger" : "";
    return `<div class="smgmt-board-running-banner${lingerCls}" id="smgmt-board-banner-${safeLabel}" data-label="${safeLabel}">
      <span class="smgmt-board-running-banner-dot" aria-hidden="true"></span>
      <span class="smgmt-board-running-banner-text" id="smgmt-board-banner-text-${safeLabel}">${parts.join(" · ")}</span>
      <button type="button" class="smgmt-board-running-banner-link"
              onclick="_smgmtShowSubView('running')">Watch in Running →</button>
    </div>`;
  }

  /** Patch the board running banner in place (no full card re-render). */
  function _smgmtBoardBannerPatch(label, live) {
    const textEl = document.getElementById(`smgmt-board-banner-text-${label}`);
    if (!textEl) return;
    const doneCount = live.done_count || 0;
    const failedCount = live.failed_count || 0;
    const skippedCount = live.skipped_count || 0;
    const totalCount = live.total_count || 0;
    const completeCount = doneCount + failedCount + skippedCount;
    const timeSpentSec = live.time_spent_sec || 0;
    const levelText = _smgmtRunningLevelText(live);
    const parts = [
      `${sprintLabelDisplay(label)} is running`,
      `${completeCount}/${totalCount} done`,
      timeSpentSec > 0 ? _fmtRunningTime(timeSpentSec) : null,
      levelText,
    ].filter(Boolean);
    textEl.textContent = parts.join(" · ");
  }

  function _smgmtRunningCardHtml(label, n, tickets) {
    let isCollapsed = false;
    try {
      isCollapsed =
        localStorage.getItem("sprintColumn_" + label + "_collapsed") === "1";
    } catch (_) {}
    const live = _smgmtLiveCache[label] || null;

    // Stat strip values (use live data if available, else zeros)
    const doneCount = live ? live.done_count || 0 : 0;
    const failedCount = live ? live.failed_count || 0 : 0;
    const skippedCount = live ? live.skipped_count || 0 : 0;
    const totalCount = live ? live.total_count || tickets.length : tickets.length;
    const completeCount = doneCount + failedCount + skippedCount;
    const estRemMins = live ? live.est_remaining_minutes : null;
    const timeSpentSec = live ? live.time_spent_sec || 0 : 0;
    const currentTicket = live ? live.current_ticket : null;
    const recentLogLines = live ? live.recent_log_lines || [] : [];

    // Use locked snapshot (live.issues) as the source of truth for rows when available.
    // Fall back to label-derived tickets only during the pre-snapshot window.
    const liveIssues =
      live && live.issues && live.issues.length > 0 ? live.issues : [];
    const liveByNum = {};
    liveIssues.forEach((i) => {
      liveByNum[i.number] = i;
    });

    const sourceTickets = (liveIssues.length > 0 ? liveIssues : tickets)
      .slice()
      .sort((a, b) => (a.dispatch_level || 0) - (b.dispatch_level || 0));

    // Segmented bar blocks — one per ticket (issue #613)
    const segBarHtml =
      sourceTickets.length > 0
        ? `<div class="smgmt-seg-bar" id="smgmt-seg-${escHtml(label)}">${sourceTickets
            .map((t) => {
              const liveIss = liveByNum[t.number];
              const liveStatus = liveIss ? liveIss.status : null;
              const agentStatus = liveIss ? liveIss.agent_status : null;
              let blockClass = "seg-pending";
              if (liveStatus === "done") blockClass = "seg-done";
              else if (agentStatus === "failed" || liveStatus === "skipped")
                blockClass = "seg-failed";
              else if (
                liveStatus === "in-progress" ||
                agentStatus === "running" ||
                (currentTicket && t.number === currentTicket.number)
              )
                blockClass = "seg-running";
              return `<div class="seg-block ${blockClass}" data-issue="${t.number}"></div>`;
            })
            .join("")}</div>`
        : "";

    // Ticket rows with level-sep rows between dispatch levels (issue #613)
    const ticketRowsHtml = _smgmtRunningTicketRowsHtml(label, tickets);

    const runCollapsedClass = isCollapsed ? " smgmt-collapsed" : "";
    const runCollapseLabel =
      (isCollapsed ? "Expand " : "Collapse ") +
      escHtml(sprintLabelDisplay(label));
    return `
      <div class="smgmt-sprint-card smgmt-running${runCollapsedClass}" id="smgmt-card-${escHtml(label)}">
        <div class="smgmt-running-stripe"></div>
        <div class="smgmt-sprint-header">
          <div class="smgmt-sprint-header-left">
            <button class="smgmt-collapse-btn" id="smgmt-collapse-btn-${escHtml(label)}"
                    onclick="smgmtToggleCollapse('${escHtml(label)}')"
                    aria-label="${runCollapseLabel}"
                    title="${runCollapseLabel}"
                    onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();smgmtToggleCollapse('${escHtml(label)}');}">
              <i class="ti ti-chevron-down"></i></button>
            <i class="ti ti-layout-kanban" style="font-size:14px;color:var(--green)"></i>
            <span class="smgmt-sprint-name" style="font-size:15px;font-weight:700;">${escHtml(sprintLabelDisplay(label))}</span>
            <span class="smgmt-running-badge" id="smgmt-running-badge-${escHtml(label)}">
              <span class="smgmt-running-badge-dot"></span>${totalCount > 0 ? `${completeCount}/${totalCount}` : "—"}
            </span>
          </div>
          <div class="smgmt-sprint-header-right">
            <span class="smgmt-sprint-meta" id="smgmt-elapsed-${escHtml(label)}">${timeSpentSec > 0 ? `elapsed ${_fmtRunningTime(timeSpentSec)}` : ""}</span>
            <button class="smgmt-cancel-btn" onclick="smgmtCancelSprint('${escHtml(label)}')">
              <i class="ti ti-player-stop"></i> Cancel sprint</button>
          </div>
        </div>
        <div class="smgmt-outcome-band" id="smgmt-running-stats-${escHtml(label)}">
          <span class="oc-done" id="smgmt-rs-done-${escHtml(label)}">${doneCount} DONE</span>
          <span class="oc-fail ${failedCount > 0 ? "" : "muted"}" id="smgmt-rs-failed-${escHtml(label)}">${failedCount} FAILED</span>
          <span class="oc-skip" id="smgmt-rs-skipped-${escHtml(label)}">${skippedCount} SKIPPED</span>
          <span class="oc-est" id="smgmt-rs-est-${escHtml(label)}">${estRemMins != null ? `↩ ${estRemMins}m EST. REMAINING` : "↩ EST. REMAINING"}</span>
          <span class="oc-spacer"></span>
          ${segBarHtml}
          <span class="smgmt-outcome-dur" id="smgmt-rs-time-${escHtml(label)}">${_fmtRunningTime(timeSpentSec)}</span>
        </div>
        <div id="smgmt-active-agents-wrap-${escHtml(label)}">${_smgmtActiveAgentsHtml(live, label)}</div>
        <div id="smgmt-levels-wrap-${escHtml(label)}">${_smgmtLevelsHtml(live, label)}</div>
        <div class="smgmt-sprint-tickets" id="smgmt-tickets-${escHtml(label)}">
          ${ticketRowsHtml || '<div class="smgmt-drop-hint">No tickets in this sprint</div>'}
        </div>
        ${renderProgressActivity(
          {
            status: "running",
            mode: totalCount > 0 ? "bar" : "indeterminate",
            current: currentTicket ? `#${currentTicket.number}` : "",
            done: completeCount,
            total: totalCount,
            est_remaining_minutes: estRemMins != null ? estRemMins : undefined,
            log_tail: recentLogLines,
          },
          {
            id: `running-${escHtml(label)}`,
            colorize:
              typeof colorizeLogLine === "function" ? colorizeLogLine : null,
            logHeaderAgentHtml: `<span class="smgmt-live-log-agent" id="smgmt-live-agent-${escHtml(label)}">${_smgmtLiveAgentBadgesHtml(live)}</span>`,
          },
        )}
      </div>`;
  }

  function _smgmtRollupText(items) {
    const count = items.length;
    if (count === 0) return "0 tickets";
    let totalMins = 0,
      unestimated = 0;
    for (const t of items) {
      const size = _smgmtTicketSize(t);
      const mins = size ? _sizeMinutes(size) : 0;
      if (mins > 0) totalMins += mins;
      else unestimated++;
    }
    const countStr = `${count} ticket${count !== 1 ? "s" : ""}`;
    if (unestimated === count) return countStr;
    const h = totalMins / 60;
    const timeStr =
      h < 1
        ? `~${totalMins}m`
        : `~${parseFloat((Math.round(h * 10) / 10).toFixed(1))}h`;
    return `${countStr} · ${timeStr}`;
  }

  /** Single source of truth: JSON cache → ticket.size → GitHub size-* label. */
  function _smgmtTicketSize(t) {
    if (!t) return null;
    const cached = Object.prototype.hasOwnProperty.call(_estDataCache, t.number)
      ? _estDataCache[t.number]
      : undefined;
    let size = cached && cached.size ? cached.size : t.size || null;
    if (!size && t.labels) {
      for (const lbl of t.labels) {
        const m = /^size-([SMLX]+)$/.exec(lbl.name || "");
        if (m) {
          size = m[1];
          break;
        }
      }
    }
    return size || null;
  }

  function _smgmtTicketHasEstimate(t) {
    return _smgmtTicketSize(t) !== null;
  }

  /**
   * Plain-language status sentence for a sprint card — shown directly under the
   * sprint header to give the operator one unambiguous signal at a glance.
   */
  function _smgmtCardStatusSentence(label, opts) {
    const {
      isRunning, isLinger, isNext, isHasRework, isReadyToMerge,
      isAwaitingMerge, planState, outcome, tickets,
    } = opts;
    if (isRunning) return "";
    if (isLinger) return "Sprint finished — snapshot kept 1 hour.";
    if (isHasRework) {
      const c = (outcome && outcome.counts) || {};
      const done = c.done || 0;
      const failed = c.failed || 0;
      const total = outcome && Array.isArray(outcome.issues) ? outcome.issues.length : 0;
      if (total > 0 && failed > 0) {
        return `${done} of ${total} passed, ${failed} need${failed === 1 ? "s" : ""} rework — re-run or merge what passed.`;
      }
      return "Some tickets need rework — re-run or merge what passed.";
    }
    if (isReadyToMerge || isAwaitingMerge) {
      return "All tickets passed. Ready to merge.";
    }
    if (isNext) {
      if (_smgmtAnySprintRunning) {
        const blocker = typeof _smgmtRunningBlockerShort === "function"
          ? _smgmtRunningBlockerShort() : "";
        return `Ready to run. Waiting on ${blocker}.`;
      }
      return "Ready to run.";
    }
    if (_smgmtAnySprintRunning) {
      return "Blocked: another sprint is running.";
    }
    if (!planState || planState === "draft" || planState === "planning") {
      return tickets.length === 0
        ? "No tickets yet — drag some from the backlog."
        : "Set a sprint goal to enable the run.";
    }
    return tickets.length === 0
      ? "No tickets — add some from the backlog."
      : "Planned.";
  }

  /** Short label for the sprint blocking Run, e.g. "S56". */
  function _smgmtRunningBlockerShort() {
    if (!_smgmtRunningLabels || _smgmtRunningLabels.size === 0) return "";
    const lbl = [..._smgmtRunningLabels][0];
    const m = String(lbl).match(/sprint-(\d+(?:\.\d+)?)/);
    return m ? `S${m[1]}` : sprintLabelDisplay(lbl);
  }

  /** Right-aligned estimate minutes; spinner only while an explicit action runs. */
  function _smgmtTicketEstHtml(ticket) {
    const activity =
      typeof globalThis !== "undefined" && globalThis._smgmtRowActivity
        ? globalThis._smgmtRowActivity[ticket.number]
        : null;
    if (activity) {
      const label = activity === "fixing-ac" ? "fixing AC…" : "estimating…";
      return (
        `<span class="smgmt-ticket-est smgmt-ticket-est--pending" id="smgmt-ticket-est-${ticket.number}" aria-label="${label}">` +
        `<span class="smgmt-estimating-dot" aria-hidden="true"></span></span>`
      );
    }
    const size = _smgmtTicketSize(ticket);
    if (!size) {
      return `<span class="smgmt-ticket-est" id="smgmt-ticket-est-${ticket.number}"></span>`;
    }
    const mins = _sizeMinutes(size);
    return `<span class="smgmt-ticket-est" id="smgmt-ticket-est-${ticket.number}">${mins}m</span>`;
  }

  function _smgmtUpdateColRollup(label, items) {
    const el = document.getElementById(`smgmt-col-rollup-${label}`);
    if (el) el.textContent = _smgmtRollupText(items);
  }

  function _smgmtTicketRowHtml(ticket, label, elapsedSecs = null) {
    const hasRework = (ticket.labels || []).some(
      (l) => l.name === "need-rework" || l.name === "needs-rework",
    );
    const statusClass = hasRework
      ? "smgmt-status-need-rework"
      : {
          backlog: "smgmt-status-backlog",
          "in-progress": "smgmt-status-in-progress",
          sit: "smgmt-status-sit",
          uat: "smgmt-status-uat",
          done: "smgmt-status-done",
        }[ticket.status] || "smgmt-status-backlog";
    const statusLabel = hasRework ? "needs rework" : ticket.status || "backlog";
    const isSelected = _smgmtSelectedIssues.has(ticket.number);

    // Outcome icon: green check (done/uat), red X (needs-rework), blue dot (active), gray circle (backlog)
    const _outcomeMap = {
      done: ["ti-circle-check", "outcome-success"],
      uat: ["ti-circle-check", "outcome-success"],
      "needs-rework": ["ti-circle-x", "outcome-rework"],
      "in-progress": ["ti-circle-dot", "outcome-active"],
      sit: ["ti-circle-dot", "outcome-active"],
    };
    const _oc = hasRework
      ? ["ti-circle-x", "outcome-rework"]
      : _outcomeMap[ticket.status] || ["ti-circle", "outcome-backlog"];
    const outcomeIconHtml = `<i class="ti ${_oc[0]} smgmt-outcome-icon ${_oc[1]}" title="${escHtml(statusLabel)}"></i>`;

    const sizeValue = _smgmtTicketSize(ticket) || "";
    const hasEstimate = sizeValue !== "";
    const sizeAttr = sizeValue ? ` data-size="${escHtml(sizeValue)}"` : "";
    // Compute estimateBadgeHtml first; when JSON cache has the size, it renders the
    // interactive estimate button — sizePillHtml must be suppressed in that case to
    // prevent a duplicate size indicator appearing (issue #674).
    const estimateBadgeHtml = _smgmtEstimateBadgeHtml(ticket.number);
    const _cachedEst = Object.prototype.hasOwnProperty.call(
      _estDataCache,
      ticket.number,
    )
      ? _estDataCache[ticket.number]
      : undefined;
    const sizePillHtml =
      sizeValue && !(_cachedEst && _cachedEst.size)
        ? `<span class="smgmt-ticket-size-pill" title="≈${_sizeMinutes(sizeValue)} min">${escHtml(sizeValue)}</span>`
        : "";
    const staleBadgeHtml =
      ticket.estimate_stale && hasEstimate
        ? `<button class="smgmt-stale-badge" data-stale="true" tabindex="0"
           title="Estimate may be outdated — issue body changed since last estimate"
           onclick="event.stopPropagation();_smgmtReEstimate(${ticket.number},this)"
           onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();_smgmtReEstimate(${ticket.number},this);}">stale</button>`
        : "";
    const reEstBtnHtml =
      _smgmtEstimatorAvailable && !ticket.estimate_stale
        ? `<button class="smgmt-reestimate-btn" tabindex="0" title="Re-estimate this ticket"
           onclick="event.stopPropagation();_smgmtReEstimate(${ticket.number},this)"
           onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();_smgmtReEstimate(${ticket.number},this);}">Re-estimate</button>`
        : "";
    const riskFlagIconsHtml = _smgmtRiskFlagIconsHtml(ticket.number);
    const schedDepHtml = _smgmtSchedDepHtml(ticket);

    const ticketLabelNames = (ticket.labels || []).map((l) => l.name).join(",");
    const sk = escHtml(label);

    return `
      <div class="smgmt-ticket${isSelected ? " is-selected" : ""}" id="smgmt-ticket-${ticket.number}"
           tabindex="-1"
           draggable="true"
           data-issue="${ticket.number}"
           data-sprint="${sk}"${sizeAttr}
           data-labels="${escHtml(ticketLabelNames)}"
           ondragstart="_smgmtTicketDragStart(event, ${ticket.number}, '${sk}')"
           ondragend="_smgmtTicketDragEnd(event)"
           ondragover="_smgmtTicketReorderDragOver(event)"
           ondragleave="_smgmtTicketReorderDragLeave(event)"
           ondrop="_smgmtTicketReorderDrop(event, ${ticket.number}, '${sk}')"
           onclick="_smgmtRowClick(event, ${ticket.number}, '${sk}')"
           oncontextmenu="_smgmtCtxMenuOpen(event,${ticket.number})">
        <input type="checkbox" class="smgmt-ticket-cb"
               ${isSelected ? "checked" : ""}
               onclick="event.stopPropagation()"
               onchange="_smgmtToggleSelect(${ticket.number}, this.checked)">
        <i class="ti ti-grip-vertical smgmt-ticket-grip"></i>
        ${outcomeIconHtml}
        <a class="smgmt-ticket-num" href="${escHtml(ticket.url || "#")}" target="_blank"
           rel="noopener" draggable="false" onclick="event.stopPropagation()">#${ticket.number}</a>
        <span class="smgmt-ticket-title" title="${escHtml(ticket.title)}">${escHtml(ticket.title)}</span>
        ${sizePillHtml}${staleBadgeHtml}${estimateBadgeHtml}${riskFlagIconsHtml}${schedDepHtml}${reEstBtnHtml}
        ${hasRework ? '<span class="smgmt-lbl-rejected">TESTER REJECTED</span>' : ""}
        ${elapsedSecs != null ? `<span class="smgmt-ticket-elapsed">${_fmtRunningTime(elapsedSecs)}</span>` : ""}
        ${_smgmtTicketEstHtml(ticket)}
        <span class="smgmt-ticket-status ${statusClass}">${escHtml(statusLabel)}</span>
        <button class="btn-view-log" tabindex="0" title="View issue log"
                onclick="event.stopPropagation();openLvIssueLog(${ticket.number},'${sk}',_smgmtRepo()||'')">
          <i class="ti ti-file-text"></i></button>
        <button class="smgmt-row-menu-btn" tabindex="0" title="Ticket actions" aria-haspopup="true" aria-expanded="false"
                onclick="event.stopPropagation();_smgmtRowMenuOpen(event, ${ticket.number}, '${sk}', ${hasEstimate})"
                onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();_smgmtRowMenuOpen(event,${ticket.number},'${sk}',${hasEstimate});}">
          <i class="ti ti-menu-2"></i></button>
        <button class="t-details-btn" onclick="event.stopPropagation();toggleTicketRow('${sk}',${ticket.number})">
          <span class="t-dbtn-label">Details</span> <span id="caret-${sk}-${ticket.number}">▼</span>
        </button>
      </div>
      <div class="ticket-expand" id="ex-${sk}-${ticket.number}" style="display:none">
        <div class="ex-row">
          <span class="ex-label">Conflicts</span>
          <span class="ex-conflicts-val">—</span>
        </div>
        <div class="ex-row">
          <span class="ex-label">Execution</span>
          <span class="ex-exec-val">—</span>
        </div>
        <div class="ex-actions">
          <button class="ex-btn" onclick="event.stopPropagation();_smgmtReEstimate(${ticket.number},this)"><i class="ti ti-sparkles" style="font-size:12px"></i> Re-estimate</button>
          <button class="ex-btn" onclick="event.stopPropagation();_smgmtRowMenuOpen(event,${ticket.number},'${sk}',${hasEstimate})"><i class="ti ti-arrow-right" style="font-size:12px"></i> Move to sprint</button>
          <button class="ex-btn ex-btn-danger" onclick="event.stopPropagation();_smgmtCloseIssueOpen(${ticket.number})"><i class="ti ti-x" style="font-size:12px"></i> Close ticket</button>
        </div>
      </div>`;
  }

  function _smgmtRenderBacklog(tickets) {
    _blBacklogAll = tickets || [];
    const countEl = document.getElementById("smgmt-backlog-count");
    const ticketsEl = document.getElementById("smgmt-backlog-tickets");
    if (!ticketsEl) return;

    // Apply the active filter pills client-side over the loaded backlog data.
    const filtered = _blApplyFilters(_blBacklogAll);

    if (countEl) {
      const total = _blBacklogAll.length,
        shown = filtered.length;
      countEl.textContent =
        total > 0
          ? `${shown === total ? total : `${shown} of ${total}`} ticket${total !== 1 ? "s" : ""}`
          : "0 tickets";
    }

    // Update bulk estimate button visibility (issue #598) — over full backlog
    const backlogBulkBtn = document.getElementById("smgmt-backlog-bulk-est-btn");
    if (backlogBulkBtn) {
      const hasUnsized = _blBacklogAll.some((t) => !_smgmtTicketHasEstimate(t));
      backlogBulkBtn.classList.toggle("hidden", !hasUnsized);
    }

    // Sort newest first (higher issue number = newer)
    const sorted = [...filtered].sort((a, b) => b.number - a.number);

    // Build list of sprint labels for "Move to" popup
    const allSprintNums = (_smgmtData?.sprints || []).sort((a, b) => a - b);

    if (sorted.length === 0) {
      const msg =
        _blBacklogAll.length === 0
          ? "No backlog tickets — all caught up"
          : "No tickets match the active filters";
      ticketsEl.innerHTML = `<div class="smgmt-drop-hint" style="padding:14px 18px;text-align:center;">${msg}</div>`;
    } else {
      ticketsEl.innerHTML = sorted
        .map((t) => _smgmtBacklogTicketHtml(t, allSprintNums))
        .join("");
    }

    _blSyncFilterPills();
    _blUpdateActions();
  }

  function _smgmtBacklogTicketHtml(ticket, _sprintNums) {
    const isSelected = _smgmtSelectedIssues.has(ticket.number);
    const hasEstimate = _smgmtTicketHasEstimate(ticket);
    const backlogLabelNames = (ticket.labels || []).map((l) => l.name).join(",");
    const schedDepHtml = _smgmtSchedDepHtml(ticket);
    const sizeValue = _smgmtTicketSize(ticket) || "";
    const sizeAttr = sizeValue ? ` data-size="${escHtml(sizeValue)}"` : "";
    const sizePillHtml = sizeValue
      ? `<span class="smgmt-ticket-size-pill">${escHtml(sizeValue)}</span>`
      : "";
    const estHtml = _smgmtTicketEstHtml(ticket);
    return `
      <div class="smgmt-ticket bl-row${isSelected ? " is-selected" : ""}" id="smgmt-ticket-${ticket.number}"
           draggable="true"
           data-issue="${ticket.number}"
           data-sprint=""${sizeAttr}
           data-labels="${escHtml(backlogLabelNames)}"
           ondragstart="_smgmtBacklogTicketDragStart(event, ${ticket.number})"
           ondragend="_smgmtTicketDragEnd(event)"
           onclick="_smgmtRowClick(event, ${ticket.number}, null)"
           oncontextmenu="_smgmtCtxMenuOpen(event,${ticket.number})">
        <input type="checkbox" class="smgmt-ticket-cb" draggable="false"
               ${isSelected ? "checked" : ""}
               onclick="event.stopPropagation()"
               onchange="_smgmtToggleSelect(${ticket.number}, this.checked)">
        <a class="smgmt-ticket-num" href="${escHtml(ticket.url || "#")}" target="_blank"
           rel="noopener" draggable="false" onclick="event.stopPropagation()">#${ticket.number}</a>
        <span class="smgmt-ticket-title" title="${escHtml(ticket.title)}">${escHtml(ticket.title)}</span>
        ${schedDepHtml}${sizePillHtml}${estHtml}
        <button class="smgmt-row-menu-btn" tabindex="0" title="Ticket actions" aria-haspopup="true" aria-expanded="false"
                onclick="event.stopPropagation();_smgmtRowMenuOpen(event, ${ticket.number}, null, ${hasEstimate})"
                onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();_smgmtRowMenuOpen(event,${ticket.number},null,${hasEstimate});}">
          <i class="ti ti-menu-2"></i></button>
      </div>`;
  }

  // ── Ancestor sprint rows (issue #1043) ──────────────────────────────────────

  /**
   * Determine merge state for a resolved ancestor sprint from its outcome data.
   * Returns: "merged" | "needs_merge" | "failed" | "unknown"
   */
  function _smgmtAncestorMergeState(label, outcome) {
    if (!outcome) return "unknown";
    const counts = outcome.counts || {};
    const done = counts.done || 0;
    if (done === 0) return "failed";
    // Use the same lifecycle derivation as _smgmtStateMeta (project.html global)
    const meta =
      typeof _smgmtStateMeta === "function"
        ? _smgmtStateMeta(outcome, (outcome.issues || []).length)
        : { state: "unknown" };
    const state = meta.state;
    if (state === "ready_to_merge" || state === "partial_finished") return "needs_merge";
    if (state === "needs_rework") return "needs_merge";
    if (state === "completed") return "merged";
    // Finished (has summary issue) with passing tickets → merged
    if (_smgmtFinishedLabels && _smgmtFinishedLabels.has(label) && done > 0) return "merged";
    return "needs_merge";
  }

  /** Build carry-down summary: "3 merged · 1 reworked → 73.1". */
  function _smgmtAncestorCarrySummary(outcome, childLabel) {
    if (!outcome) return "";
    const counts = outcome.counts || {};
    const done = counts.done || 0;
    const carried = (counts.failed || 0) + (counts.skipped || 0);
    const childDisplay = childLabel
      ? sprintLabelDisplay(childLabel).replace("Sprint ", "")
      : "";
    let summary = `${done} merged`;
    if (carried > 0 && childDisplay) summary += ` · ${carried} reworked → ${childDisplay}`;
    else if (carried > 0) summary += ` · ${carried} reworked`;
    return summary;
  }

  /** Per-ticket rows for an expanded ancestor sprint (merged / carried fate marks). */
  function _smgmtAncestorTicketsHtml(label, outcome, childLabel) {
    const issues = (outcome && outcome.issues) || [];
    if (issues.length === 0) {
      return '<div class="slp-no-tickets">No ticket data available.</div>';
    }
    const repo = _smgmtRepo();
    const childDisplay = childLabel
      ? sprintLabelDisplay(childLabel).replace("Sprint ", "")
      : "";
    return issues
      .map((iss) => {
        const o = iss.outcome || "skipped";
        const isMerged = o === "done";
        const issueUrl = repo
          ? `https://github.com/${repo}/issues/${iss.number}`
          : "#";
        if (isMerged) {
          return `<div class="slp-ancestor-ticket-row">
            <span class="slp-ticket-merged" title="Merged"><i class="ti ti-circle-check"></i></span>
            <a class="smgmt-ticket-num" href="${escHtml(issueUrl)}" target="_blank" rel="noopener"
               onclick="event.stopPropagation()">#${iss.number}</a>
            <span class="smgmt-ticket-title slp-ticket-title" title="${escHtml(iss.title)}">${escHtml(iss.title)}</span>
            <span class="slp-fate-merged">merged</span>
          </div>`;
        } else {
          return `<div class="slp-ancestor-ticket-row">
            <span class="slp-ticket-carried" title="Carried to ${escHtml(childDisplay)}"><i class="ti ti-arrow-right"></i></span>
            <a class="smgmt-ticket-num" href="${escHtml(issueUrl)}" target="_blank" rel="noopener"
               onclick="event.stopPropagation()">#${iss.number}</a>
            <span class="smgmt-ticket-title slp-ticket-title" title="${escHtml(iss.title)}">${escHtml(iss.title)}</span>
            <span class="slp-fate-carried">carried → ${escHtml(childDisplay)}</span>
          </div>`;
        }
      })
      .join("");
  }

  /** Compact collapsed row for a resolved ancestor sprint (issue #1043). */
  function _smgmtAncestorRowHtml(label, outcome, childLabel) {
    const mergeState = _smgmtAncestorMergeState(label, outcome);
    const safeLabel = escHtml(label);
    const rerunInto = childLabel || (_smgmtData?.sprint_rerun_into || {})[label];

    let markIcon, markText, markCls;
    if (mergeState === "merged") {
      markIcon = "ti-git-merge";
      markText = "Merged";
      markCls = "slp-merged";
    } else if (mergeState === "needs_merge") {
      markIcon = "ti-git-pull-request";
      markText = "Needs merge";
      markCls = "slp-needs-merge";
    } else if (mergeState === "failed") {
      markIcon = "ti-circle-x";
      markText = "Failed";
      markCls = "slp-failed";
    } else {
      markIcon = "ti-clock";
      markText = "Pending";
      markCls = "slp-pending";
    }

    const carrySummary = _smgmtAncestorCarrySummary(outcome, rerunInto);
    const ticketsHtml = outcome
      ? _smgmtAncestorTicketsHtml(label, outcome, rerunInto)
      : '<div class="slp-no-tickets">Loading outcome data…</div>';

    const rerunDisabled = _smgmtAnySprintRunning ? "disabled" : "";
    const rerunTitle = _smgmtAnySprintRunning
      ? 'title="Cannot re-run: another sprint is currently running."'
      : "";
    const actionsHtml =
      mergeState === "needs_merge"
        ? `<div class="slp-ancestor-actions">
            <button class="smgmt-run-btn smgmt-run-btn--rerun" ${rerunDisabled} ${rerunTitle}
                    onclick="event.stopPropagation();smgmtRerunSprint('${safeLabel}')">
              <i class="ti ti-refresh"></i> Re-run</button>
            <button class="smgmt-finish-btn sc-merge-link"
                    onclick="event.stopPropagation();smgmtFinishSprint('${safeLabel}')">
              <i class="ti ti-flag-check"></i> Merge Sprint</button>
          </div>`
        : "";

    return `<div class="slp-ancestor-row" id="smgmt-card-${safeLabel}"
                 onclick="smgmtToggleAncestor('${safeLabel}')">
      <div class="slp-ancestor-header">
        <button class="smgmt-collapse-btn slp-ancestor-toggle"
                aria-label="Expand ${escHtml(sprintLabelDisplay(label))}"
                title="Expand ${escHtml(sprintLabelDisplay(label))}"
                onclick="event.stopPropagation();smgmtToggleAncestor('${safeLabel}')">
          <i class="ti ti-chevron-right"></i>
        </button>
        <span class="slp-merge-mark ${markCls}" title="${escHtml(markText)}">
          <i class="ti ${markIcon}"></i>
          <span class="slp-mark-text">${escHtml(markText)}</span>
        </span>
        <span class="slp-ancestor-name">${escHtml(sprintLabelDisplay(label))}</span>
        ${carrySummary ? `<span class="slp-carry-summary">${escHtml(carrySummary)}</span>` : ""}
      </div>
      <div class="slp-ancestor-body" id="slp-body-${safeLabel}" hidden>
        <div class="slp-ancestor-tickets" id="slp-tickets-${safeLabel}">
          ${ticketsHtml}
        </div>
        ${actionsHtml}
      </div>
    </div>`;
  }

  /** Toggle expanded/collapsed state of a resolved ancestor sprint row. */
  function smgmtToggleAncestor(label) {
    const body = document.getElementById(`slp-body-${label}`);
    const toggleIcon = document.querySelector(
      `#smgmt-card-${CSS.escape(label)} .slp-ancestor-toggle i`,
    );
    if (!body) return;
    const isExpanded = !body.hidden;
    body.hidden = isExpanded;
    if (toggleIcon) {
      toggleIcon.className = isExpanded ? "ti ti-chevron-right" : "ti ti-chevron-down";
    }
    try {
      localStorage.setItem(`slp_ancestor_${label}`, isExpanded ? "0" : "1");
    } catch (_) {}
  }

  /** Update an ancestor row in-place when outcome data arrives asynchronously. */
  function _smgmtUpdateAncestorRow(label, outcome) {
    const card = document.getElementById(`smgmt-card-${label}`);
    if (!card || !card.classList.contains("slp-ancestor-row")) return;
    const childLabel = (_smgmtData?.sprint_rerun_into || {})[label];
    const newHtml = _smgmtAncestorRowHtml(label, outcome, childLabel);
    const wasExpanded =
      document.getElementById(`slp-body-${label}`)?.hidden === false;
    const tmp = document.createElement("div");
    tmp.innerHTML = newHtml;
    const newCard = tmp.firstElementChild;
    if (newCard) {
      card.replaceWith(newCard);
      if (wasExpanded) {
        const newBody = document.getElementById(`slp-body-${label}`);
        if (newBody) newBody.hidden = false;
        const newIcon = document.querySelector(
          `#smgmt-card-${CSS.escape(label)} .slp-ancestor-toggle i`,
        );
        if (newIcon) newIcon.className = "ti ti-chevron-down";
      }
    }
  }
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

  /* global _activeTab, _blUpdateActions, _arInterval, _smgmtArStartTicker, _smgmtArStopTicker, _smgmtBySprint, _smgmtData, _smgmtFinishedLabels, _smgmtMoveToModalOpen, _smgmtOrderedLabels, _smgmtRender, _smgmtRepo, _smgmtRunningLabels, _smgmtSelectedIssues, _smgmtShowInlineError, _smgmtShowToast, _smgmtUpdateToolbarTop, loadSprintMgmt, sprintLabelDisplay,
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
    _blUpdateActions();

    // Remove legacy inline bar if present from an older build.
    document.getElementById('smgmt-selection-bar')?.remove();

    const bar = document.getElementById('proj-selection-bar');
    const listEl = document.getElementById('smgmt-sprint-list');
    const onSprintTab = typeof _activeTab === 'undefined' || _activeTab === 'sprint-mgmt';

    if (count > 0 && bar && onSprintTab) {
      bar.classList.add('show');
      bar.classList.remove('hidden');
      if (listEl) listEl.classList.add('has-selection');
      const countEl = document.getElementById('smgmt-sel-count');
      if (countEl) countEl.textContent = count === 1 ? '1 issue selected' : `${count} issues selected`;
      const deleteBtn = document.getElementById('smgmt-sel-delete-btn');
      if (deleteBtn) {
        const showDelete = count === 1 && _smgmtIsDeletableIssue([..._smgmtSelectedIssues][0]);
        deleteBtn.classList.toggle('show', showDelete);
      }
    } else {
      if (bar) {
        bar.classList.remove('show');
        bar.classList.add('hidden');
      }
      if (listEl) listEl.classList.remove('has-selection');
    }
    if (typeof _smgmtUpdateToolbarTop === 'function') _smgmtUpdateToolbarTop();
  }

  function _smgmtPopulateSelectionDropdown() {
    // legacy no-op — replaced by _smgmtPopulateMoveToMenu
  }

  function _smgmtMoveTargetLabels() {
    const partOf = lbl => {
      const m = /^sprint-(\d+)(?:\.(\d+))?$/.exec(lbl || '');
      return m ? [parseInt(m[1], 10), m[2] ? parseInt(m[2], 10) : 0] : [0, 0];
    };
    const finished = _smgmtFinishedLabels || new Set();
    const labels = new Set(Object.keys(_smgmtBySprint || {}));
    // Empty planned sprints (0 tickets) are on the board but absent from bySprint keys.
    const ordered = _smgmtOrderedLabels
      || (_smgmtData?.order || []).filter(l => /^sprint-\d+(\.\d+)*$/.test(l));
    ordered.forEach(lbl => labels.add(lbl));
    return [...labels]
      .filter(lbl => !finished.has(lbl))
      .sort((a, b) => {
        const pa = partOf(a), pb = partOf(b);
        return pa[0] - pb[0] || pa[1] - pb[1];
      });
  }

  /** @deprecated Selection bar opens the shared move modal — kept for compat. */
  function _smgmtPopulateMoveToMenu() {}

  function _smgmtToggleMoveToMenu(event) {
    event?.stopPropagation();
    if (typeof _smgmtMoveToModalOpen === 'function') _smgmtMoveToModalOpen();
  }

  function _smgmtCloseMoveToMenu() {}

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

  // Multi-select is scoped to ONE sprint (or the backlog) at a time so the batch
  // Move-to / hotswap target is unambiguous. Selecting a ticket in a different
  // sprint than the current selection clears the old selection first.
  function _smgmtTicketSprintKey(number) {
    const iss = (_smgmtData?.issues || []).find(i => i.number === number);
    if (!iss) return undefined;
    return iss.sprint == null ? 'backlog' : iss.sprint;
  }

  function _smgmtSelectionSprintKey() {
    const first = [..._smgmtSelectedIssues][0];
    return first == null ? undefined : _smgmtTicketSprintKey(first);
  }

  function _smgmtEnforceSelectionScope(number) {
    if (_smgmtSelectedIssues.size === 0) return;
    const cur = _smgmtSelectionSprintKey();
    const next = _smgmtTicketSprintKey(number);
    if (cur !== undefined && next !== undefined && cur !== next) {
      _smgmtClearSelection();
    }
  }

  function _smgmtToggleSelect(number, checked) {
    if (checked) _smgmtEnforceSelectionScope(number);
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
      // Ctrl/Cmd+click: toggle this ticket without clearing same-sprint siblings.
      const nowSelected = !_smgmtSelectedIssues.has(number);
      if (nowSelected) _smgmtEnforceSelectionScope(number);
      _smgmtSetSelected(number, nowSelected);
      _smgmtLastSelectedNum = nowSelected ? number : null;
      _smgmtUpdateSelectionUI();
      return;
    }

    // Plain click: toggle this ticket on/off.
    const nowSelected = !_smgmtSelectedIssues.has(number);
    if (nowSelected) _smgmtEnforceSelectionScope(number);
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

  function _smgmtTicketDragEnd(_event) {
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

  function _smgmtBoardLock(message, opts) {
    _smgmtMoveLock = true;
    // Pause the auto-refresh ticker without changing the user's chosen interval
    _smgmtArStopTicker();
    const overlay = document.getElementById('smgmt-move-overlay');
    const msgEl   = document.getElementById('smgmt-move-overlay-msg');
    const progWrap = document.getElementById('smgmt-op-progress-wrap');
    const logEl = document.getElementById('smgmt-op-log');
    const text    = message || 'Moving…';
    if (msgEl) msgEl.textContent = text;
    if (overlay) {
      overlay.setAttribute('aria-label', text.replace(/…$/, '') + ', please wait');
      overlay.classList.add('active');
    }
    const showProgress = !!(opts && opts.progress);
    if (progWrap) progWrap.hidden = !showProgress;
    if (logEl) {
      logEl.hidden = !showProgress;
      if (showProgress && opts.clearLog) logEl.innerHTML = '';
    }
    if (showProgress && opts.total != null) {
      _smgmtBoardProgress(0, opts.total);
    } else if (!showProgress) {
      _smgmtBoardProgress(0, 1);
    }
  }

  function _smgmtBoardProgress(done, total) {
    const fill = document.getElementById('smgmt-op-progress-fill');
    const pctEl = document.getElementById('smgmt-op-progress-pct');
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    if (fill) fill.style.width = pct + '%';
    if (pctEl) pctEl.textContent = pct + '%';
  }

  function _smgmtBoardLog(line, kind) {
    const logEl = document.getElementById('smgmt-op-log');
    if (!logEl) return;
    const row = document.createElement('div');
    row.className = 'smgmt-op-log-line' + (kind ? ` smgmt-op-log-line--${kind}` : '');
    row.textContent = line;
    logEl.appendChild(row);
    logEl.scrollTop = logEl.scrollHeight;
  }

  function _smgmtBoardUnlock() {
    _smgmtMoveLock = false;
    const overlay = document.getElementById('smgmt-move-overlay');
    if (overlay) overlay.classList.remove('active');
    const progWrap = document.getElementById('smgmt-op-progress-wrap');
    const logEl = document.getElementById('smgmt-op-log');
    if (progWrap) progWrap.hidden = true;
    if (logEl) { logEl.hidden = true; logEl.innerHTML = ''; }
    _smgmtBoardProgress(0, 1);
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
     _smgmtShowSubView, _smgmtRunningLabels, _smgmtAnySprintRunning, _smgmtLivePollRestart,
     _smgmtLingerStart, _smgmtLingerLive, _smgmtRunningViewUpdate,
     _pfCurrentLabel:writable, _pfCurrentRepo:writable, _pfState:writable,
     _pfDagData:writable, _pfWarnings:writable, _pfCycle:writable,
     _pfFlags:writable, _pfSelectedIds:writable, _pfUseClineFollowups:writable */

  // ── Pre-flight stepper component (shared ProgressActivity — stepper mode, issue #933) ─

  /** Step definitions matching the pre-flight panel check groups. */
  const PF_STEPS = [
    { key: 'ac',        label: 'Acceptance criteria', autoFixable: true  },
    { key: 'estimates', label: 'Estimate coverage',    autoFixable: true  },
    { key: 'cycle',     label: 'Dependency graph',     autoFixable: false },
    { key: 'missizing', label: 'Mis-sizing review',    autoFixable: false },
    { key: 'conflicts', label: 'Conflict analysis',    autoFixable: false },
  ];

  /** Count of steps currently in fail state (blocks Run Sprint). */
  let _pfStepFails = 0;

  // ────────────────────────────────────────────────────────────────────────────

  // Effective agent models for the current preflight (from /preflight `models`,
  // resolved server-side from sprint.yaml — what the run will actually use).
  let _pfModels = null;

  /** Short model label, e.g. "claude-sonnet-4-6" → "sonnet-4-6". */
  function _pfModelShort(m) {
    const s = String(m || '');
    return s.replace(/^claude-/, '') || s;
  }

  /** "Agents" section for the preflight modal: the effective model per role, so
   *  the operator confirms what will run before dispatch. */
  function _pfBuildModelsHtml() {
    const m = _pfModels;
    if (!m) return '';
    const rows = [];
    rows.push(`<span class="pf-model-pill"><b>Coder</b> ${escHtml(_pfModelShort(m.coder))}</span>`);
    const br = m.tester_by_risk || {};
    const testerTxt = Object.keys(br).length
      ? Object.keys(br).map(k => `${k.toLowerCase()}:${_pfModelShort(br[k])}`).join(' · ')
      : 'risk-routed';
    rows.push(`<span class="pf-model-pill"><b>Tester</b> ${escHtml(testerTxt)}</span>`);
    rows.push(`<span class="pf-model-pill"><b>Estimator</b> ${escHtml(_pfModelShort(m.estimator))}</span>`);
    if (m.documentor) {
      rows.push(`<span class="pf-model-pill"><b>Documentor</b> ${escHtml(_pfModelShort(m.documentor))}</span>`);
    }
    return `<div class="pf-section">
        <div class="pf-section-label">Agent models <span class="pf-model-note">— confirm before run · edit in Settings → Agent Models</span></div>
        <div class="pf-section-body pf-model-pills">${rows.join('')}</div>
      </div>`;
  }

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
        _smgmtRunningLabels.delete(label);
        _smgmtAnySprintRunning = _smgmtRunningLabels.size > 0;
        if (typeof _smgmtLingerStart === 'function') {
          _smgmtLingerStart(label, { cancelled: true });
        }
        if (typeof _smgmtLivePollRestart === 'function') _smgmtLivePollRestart();
        if (typeof _smgmtRunningViewUpdate === 'function') {
          const snap = typeof _smgmtLingerLive === 'function' ? _smgmtLingerLive(label) : null;
          _smgmtRunningViewUpdate(label, snap);
        }
        setTimeout(() => loadSprintMgmt(), 2000);
      }
    } catch (e) {
      _smgmtShowToast(`Cancel failed: ${e.message}`);
    }
  }


  // ── Sprint sign-off: Approve / Reject (issue #862) ───────────────────────────

  async function smgmtApproveSprint(label) {
    const repo = _smgmtRepo();
    if (!repo) return;
    // Confirmation gate: dismissing leaves the sprint pending (no state change).
    if (!confirm(`Approve ${sprintLabelDisplay(label)}? This signs off the sprint and enables Run Sprint.`)) return;
    try {
      const res = await fetch(`/api/sprints/${encodeURIComponent(label)}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project: repo }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        _smgmtShowToast(`Approve failed: ${err.detail || res.status}`);
        return;
      }
      _smgmtShowToast(`${sprintLabelDisplay(label)} approved — ready to run`);
      loadSprintMgmt();
    } catch (e) {
      _smgmtShowToast(`Approve failed: ${e.message}`);
    }
  }

  async function smgmtRejectSprint(label) {
    const repo = _smgmtRepo();
    if (!repo) return;
    // Confirmation gate: rejecting dissolves the sprint and returns tickets to backlog.
    if (!confirm(`Reject ${sprintLabelDisplay(label)}? The sprint is dissolved and all its tickets return to the backlog.`)) return;
    try {
      const res = await fetch(`/api/sprints/${encodeURIComponent(label)}/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project: repo }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        _smgmtShowToast(`Reject failed: ${err.detail || res.status}`);
        return;
      }
      _smgmtShowToast(`${sprintLabelDisplay(label)} rejected — tickets returned to backlog`);
      loadSprintMgmt();
    } catch (e) {
      _smgmtShowToast(`Reject failed: ${e.message}`);
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
    document.getElementById('pf-loading').classList.add('hidden');
    document.getElementById('pf-stepper').classList.remove('hidden');
    document.getElementById('pf-content').classList.add('hidden');
    document.getElementById('pf-error').classList.add('hidden');
    document.getElementById('pf-footer').classList.remove('hidden');
    document.getElementById('pf-confirm-btn').disabled = true;
    document.getElementById('pf-confirm-btn').textContent = 'Run Sprint';
    _pfDagData = null;
    _pfWarnings = null;
    _pfCycle = null;
    _pfFlags = null;
    _pfModels = null;
    _pfSelectedIds = new Set();
    _pfUseClineFollowups = false;
    _pfStepperInit();
  }

  function _pfClose() {
    document.getElementById('pf-backdrop').classList.add('hidden');
    document.getElementById('pf-modal').classList.add('hidden');
    document.getElementById('pf-stepper').classList.add('hidden');
    _pfCurrentLabel = null;
    _pfCurrentRepo  = null;
    _pfState        = 'idle';
    _pfDagData      = null;
    _pfWarnings     = null;
    _pfCycle        = null;
    _pfFlags        = null;
    _pfSelectedIds  = new Set();
    _pfUseClineFollowups = false;
    _pfStepFails    = 0;
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
      _pfModels   = data.models           || null;
      if (_pfDagData) {
        for (const t of (_pfDagData.tickets || [])) _pfSelectedIds.add(t.id);
      }
      _pfState = 'success';
      _pfShowSuccess();
      // Drive the stepper animation with the fetched data (issue #933)
      _pfStepperAnimate(data);
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
    const modelsHtml    = _pfBuildModelsHtml();
    const clineCheckboxHtml = `<div class="pf-section pf-cline-section">
       <label class="pf-cline-label">
         <input type="checkbox" id="pf-cline-checkbox" class="pf-cline-checkbox"
           ${_pfUseClineFollowups ? 'checked' : ''}
           onchange="_pfUseClineFollowups = this.checked">
         <span>Use Cline (Sonnet) for follow-up coder fixes — tester stays on Claude</span>
       </label>
     </div>`;
    document.getElementById('pf-content').innerHTML =
      `<p style="font-size:13px;color:var(--text);margin:0;">Ready to run <strong>Sprint ${n}</strong>.</p>
       ${modelsHtml}
       ${clineCheckboxHtml}
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
    // Note: _pfUpdateConfirmBtn() is called at the end of _pfStepperAnimate (issue #933)
    // so the Run button is only enabled after stepper resolves all steps.
    document.getElementById('pf-cancel-btn').focus();
    if (_pfDagData && (_pfDagData.edges || []).length > 0) {
      requestAnimationFrame(() => _pfDrawDAGArrows(_pfDagData.edges));
    }
  }

  function _pfUpdateConfirmBtn() {
    const hasCycle = !!(_pfCycle && _pfCycle.length);
    const pendingFlags = (_pfFlags && (_pfFlags.flags || []).filter(f => f.status === 'pending')) || [];
    const hasPending = pendingFlags.length > 0;
    const hasFail = _pfStepFails > 0;
    const confirmBtn = document.getElementById('pf-confirm-btn');
    if (!confirmBtn) return;
    confirmBtn.disabled = hasCycle || hasPending || hasFail;
    if (hasCycle) {
      confirmBtn.title = 'Cannot run: dependency cycle detected. Resolve the cycle first.';
      confirmBtn.setAttribute('aria-label', 'Run Sprint — disabled: dependency cycle detected');
    } else if (hasPending) {
      confirmBtn.title = `Cannot run: ${pendingFlags.length} mis-sizing flag${pendingFlags.length > 1 ? 's' : ''} need review.`;
      confirmBtn.setAttribute('aria-label', 'Run Sprint — disabled: mis-sizing flags need review');
    } else if (hasFail) {
      confirmBtn.title = `Cannot run: ${_pfStepFails} blocking issue${_pfStepFails > 1 ? 's' : ''} detected.`;
      confirmBtn.setAttribute('aria-label', `Run Sprint — disabled: ${_pfStepFails} blocking issue(s)`);
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

  function _pfFlagShowSizePicker(num, _currentSize) {
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
    confirmBtn.textContent = 'Starting…';
    _pfClose();
    // Kickoff stepper drives the run from here (issue #932)
    await smgmtKickoffRun(label, repo);
  }


  // ── Pre-flight stepper functions (shared ProgressActivity — stepper mode, issue #933) ─

  /** Initialise all steps to `pending` state. Called from _pfReset(). */
  function _pfStepperInit() {
    _pfStepFails = 0;
    const stepsEl = document.getElementById('pf-stepper-steps');
    if (!stepsEl) return;
    stepsEl.innerHTML = PF_STEPS.map(s =>
      `<div class="pf-step-item pf-step-item--pending" id="pf-step-${s.key}">
        <span class="pf-step-icon" aria-hidden="true"></span>
        <div class="pf-step-content">
          <span class="pf-step-name">${escHtml(s.label)}</span>
          <span class="pf-step-note" id="pf-step-note-${s.key}"></span>
        </div>
      </div>`
    ).join('');
    const summaryEl = document.getElementById('pf-stepper-summary');
    if (summaryEl) {
      summaryEl.textContent = '';
      summaryEl.className = 'pf-stepper-summary hidden';
    }
  }

  /** Transition a single step to a new state with an optional note. */
  function _pfStepState(key, state, note) {
    const item = document.getElementById(`pf-step-${key}`);
    if (!item) return;
    item.className = `pf-step-item pf-step-item--${state}`;
    const noteEl = document.getElementById(`pf-step-note-${key}`);
    if (noteEl) noteEl.textContent = note || '';
  }

  /**
   * Call the preflight-fix SSE endpoint and collect summary counts.
   * Auto-fixes missing AC and missing size estimates for the sprint.
   */
  async function _pfRunAutoFix(label, repo) {
    const resp = await fetch(
      `/api/sprints/${encodeURIComponent(label)}/preflight-fix?project=${encodeURIComponent(repo)}`,
      { method: 'POST' }
    );
    if (!resp.ok) throw new Error(`preflight-fix ${resp.status}`);
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '', filled = 0, estimated = 0, errors = [];
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop();
      for (const part of parts) {
        const m = part.match(/^event:\s*(\S+)\ndata:\s*([\s\S]*)$/);
        if (!m) continue;
        if (m[1] === 'done') {
          try {
            const d = JSON.parse(m[2]);
            filled    = d.filled    || 0;
            estimated = d.estimated || 0;
            errors    = d.errors    || [];
          } catch (_) { /* ignore parse errors */ }
        }
      }
    }
    return { filled, estimated, errors };
  }

  /**
   * Drive the stepper state machine using preflight API response data.
   * Steps animate pending → checking → pass/fail/fixed sequentially.
   * Called from _pfFetch() after a successful preflight response.
   */
  async function _pfStepperAnimate(data) {
    const delay = (ms) => new Promise(r => setTimeout(r, ms));
    const label = _pfCurrentLabel;
    const repo  = _pfCurrentRepo;

    // ── Steps 1 & 2: Acceptance criteria + Estimate coverage (auto-fixable) ──
    _pfStepState('ac',        'checking', '');
    _pfStepState('estimates', 'checking', '');
    await delay(350);

    const missingAc  = (data.warnings && data.warnings.missing_ac    || []);
    const unestimated = (data.warnings && data.warnings.unestimated  || []);
    const hasAcIssues  = missingAc.length  > 0;
    const hasEstIssues = unestimated.length > 0;

    if ((hasAcIssues || hasEstIssues) && label && repo) {
      // Auto-fix: call preflight-fix endpoint for missing AC and estimates
      try {
        const fix = await _pfRunAutoFix(label, repo);
        const acNote  = fix.filled    > 0 ? `${fix.filled} acceptance criteria generated`
                      : hasAcIssues       ? `${missingAc.length} ticket(s) missing AC`
                      : '';
        const estNote = fix.estimated > 0 ? `${fix.estimated} ticket(s) estimated`
                      : hasEstIssues      ? `${unestimated.length} ticket(s) unestimated`
                      : '';
        _pfStepState('ac',        fix.filled    > 0 ? 'fixed' : 'pass', acNote);
        _pfStepState('estimates', fix.estimated > 0 ? 'fixed' : 'pass', estNote);
      } catch (_) {
        // Fix call failed — show pass with warning note (non-blocking)
        _pfStepState('ac',        'pass', hasAcIssues  ? `${missingAc.length} ticket(s) missing AC`     : '');
        _pfStepState('estimates', 'pass', hasEstIssues ? `${unestimated.length} ticket(s) unestimated`   : '');
      }
    } else {
      _pfStepState('ac',        'pass', '');
      _pfStepState('estimates', 'pass', '');
    }
    await delay(300);

    // ── Step 3: Dependency cycle (non-auto-fixable, blocking on cycle) ────────
    _pfStepState('cycle', 'checking', '');
    await delay(350);
    if (data.cycle && data.cycle.length) {
      _pfStepState('cycle', 'fail', `Cycle: ${data.cycle.join(' → ')}`);
      _pfStepFails++;
    } else {
      _pfStepState('cycle', 'pass', '');
    }
    await delay(300);

    // ── Step 4: Mis-sizing review (non-auto-fixable, blocking if pending flags) ─
    _pfStepState('missizing', 'checking', '');
    await delay(350);
    const pendingFlags = (data.mis_sizing_flags && data.mis_sizing_flags.flags || [])
      .filter(f => f.status === 'pending');
    if (pendingFlags.length > 0) {
      _pfStepState('missizing', 'fail', `${pendingFlags.length} flag(s) require review`);
      _pfStepFails++;
    } else {
      _pfStepState('missizing', 'pass', '');
    }
    await delay(300);

    // ── Step 5: Conflict analysis (informational, non-blocking) ───────────────
    _pfStepState('conflicts', 'checking', '');
    await delay(350);
    const selectedTickets = _pfGetSelectedTickets();
    const conflicts = _pfComputeConflicts(selectedTickets);
    if (conflicts.length > 0) {
      _pfStepState('conflicts', 'pass', `${conflicts.length} conflict(s) — execution order planned`);
    } else {
      _pfStepState('conflicts', 'pass', '');
    }

    // ── Summary ───────────────────────────────────────────────────────────────
    _pfStepperSummary();
    _pfUpdateConfirmBtn();
  }

  /** Show the overall summary: all-clear or blocking count. */
  function _pfStepperSummary() {
    const summaryEl = document.getElementById('pf-stepper-summary');
    if (!summaryEl) return;
    summaryEl.classList.remove('hidden');
    if (_pfStepFails > 0) {
      summaryEl.textContent =
        `${_pfStepFails} blocking issue${_pfStepFails > 1 ? 's' : ''} — cannot run`;
      summaryEl.className = 'pf-stepper-summary pf-stepper-summary--blocking';
    } else {
      summaryEl.textContent = 'All checks passed — ready to run';
      summaryEl.className = 'pf-stepper-summary pf-stepper-summary--clear';
    }
  }


  // ── Kickoff stepper (issue #932) ─────────────────────────────────────────────
  // Shows live progress for the three-phase sprint launch: lock acquisition →
  // branch creation → agent dispatch. Uses the shared pf-step-item component
  // (same CSS classes as the pre-flight stepper). Appears in the Running subview
  // immediately when the operator confirms the preflight modal, replacing the
  // bare "Starting…" button state.

  /** Step definitions for the kickoff flow. */
  const KS_STEPS = [
    { key: 'lock',     label: 'Validate and acquire lock' },
    { key: 'branch',   label: 'Create sprint branch'      },
    { key: 'dispatch', label: 'Dispatch first agents'     },
  ];

  /** Which step index failed (-1 = none). Used by retry logic (AC7). */
  let _ksFailedStep = -1;
  let _ksLabel = null;
  let _ksRepo  = null;

  /** Render kickoff steps in pending state. */
  function _ksInit() {
    const stepsEl = document.getElementById('smgmt-kickoff-steps');
    if (!stepsEl) return;
    stepsEl.innerHTML = KS_STEPS.map(s =>
      `<div class="pf-step-item pf-step-item--pending" id="ks-step-${s.key}">
        <span class="pf-step-icon" aria-hidden="true"></span>
        <div class="pf-step-content">
          <span class="pf-step-name">${escHtml(s.label)}</span>
          <span class="pf-step-note" id="ks-step-note-${s.key}"></span>
        </div>
      </div>`
    ).join('');
    const errEl = document.getElementById('smgmt-kickoff-error');
    if (errEl) errEl.hidden = true;
  }

  /** Transition a kickoff step to a new state with an optional note. */
  function _ksSetStep(key, state, note) {
    const item = document.getElementById(`ks-step-${key}`);
    if (!item) return;
    item.className = `pf-step-item pf-step-item--${state}`;
    const noteEl = document.getElementById(`ks-step-note-${key}`);
    if (noteEl) noteEl.textContent = note || '';
  }

  /** Show the kickoff stepper in the Running subview. */
  function _ksShow(label, repo) {
    _ksLabel = label;
    _ksRepo  = repo;
    _ksFailedStep = -1;
    _ksInit();
    const shell   = document.getElementById('smgmt-kickoff-shell');
    const runShell = document.getElementById('smgmt-run-shell');
    const emptyEl  = document.getElementById('smgmt-running-empty');
    if (emptyEl)  emptyEl.hidden  = true;
    if (runShell) runShell.hidden = true;
    if (shell)    shell.hidden    = false;
    if (typeof _smgmtShowSubView === 'function') _smgmtShowSubView('running');
  }

  /** Hide the kickoff stepper. */
  function _ksHide() {
    const shell = document.getElementById('smgmt-kickoff-shell');
    if (shell) shell.hidden = true;
  }

  /** Display the error state for a failed step (AC5). */
  function _ksShowError(stepKey, msg) {
    _ksSetStep(stepKey, 'fail', msg);
    const errEl = document.getElementById('smgmt-kickoff-error');
    if (!errEl) return;
    const msgEl = document.getElementById('smgmt-kickoff-error-msg');
    if (msgEl) msgEl.textContent = msg || 'An error occurred';
    errEl.hidden = false;
  }

  /** True if the given sprint label is currently in the running-all list. */
  async function _ksIsRunning(label) {
    try {
      const res = await fetch('/api/sprints/running-all');
      if (!res.ok) return false;
      const data = await res.json();
      return (data.running || []).some(r => r.sprint_label === label);
    } catch (_) {
      return false;
    }
  }

  /**
   * Step 1: POST /api/sprints/run. Returns true on 202, false on error.
   * On failure the error message is shown inline at the lock step (AC5/AC6).
   */
  async function _ksStep1Post() {
    const label = _ksLabel;
    const repo  = _ksRepo;
    _ksSetStep('lock', 'checking', '');
    try {
      const res = await fetch('/api/sprints/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project: repo, sprint_label: label, use_cline_followups: _pfUseClineFollowups }),
      });
      if (!res.ok) {
        let detail = await res.text();
        try { const p = JSON.parse(detail); detail = typeof p.detail === 'string' ? p.detail : JSON.stringify(p.detail); }
        catch (_) { /* plain-text body */ }
        _ksShowError('lock', detail || `HTTP ${res.status}`);
        _ksFailedStep = 0;
        return false;
      }
      _ksSetStep('lock', 'pass', '');
      return true;
    } catch (e) {
      _ksShowError('lock', e.message);
      _ksFailedStep = 0;
      return false;
    }
  }

  /**
   * Step 2: Poll until sprint appears in running-all (branch created / process alive).
   * Returns true on success, false on timeout.
   */
  async function _ksStep2Branch() {
    _ksSetStep('branch', 'checking', '');
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 1000));
      if (await _ksIsRunning(_ksLabel)) {
        _ksSetStep('branch', 'pass', '');
        return true;
      }
    }
    _ksShowError('branch', 'Timed out waiting for sprint process to start');
    _ksFailedStep = 1;
    return false;
  }

  /**
   * Step 3: Poll /api/sprint-status until the first agents are dispatched.
   * Transitions to running pane after success. Returns true on success.
   */
  async function _ksStep3Dispatch() {
    const label = _ksLabel;
    const repo  = _ksRepo;
    _ksSetStep('dispatch', 'checking', '');
    const deadline = Date.now() + 90000;
    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 2000));
      try {
        const res = await fetch(`/api/sprint-status?project=${encodeURIComponent(repo)}`);
        if (res.ok) {
          const data = await res.json();
          const sprint = (data.running_sprints || []).find(s => s.sprint_label === label);
          // Agents dispatched when status has been posted with at least one issue
          if (sprint && sprint.issues && sprint.issues.length > 0) {
            _ksSetStep('dispatch', 'pass', '');
            return true;
          }
          // Sprint disappeared from running — it terminated before dispatching
          if (!sprint && !(await _ksIsRunning(label))) {
            _ksShowError('dispatch', 'Sprint terminated before agents were dispatched');
            _ksFailedStep = 2;
            return false;
          }
        }
      } catch (_) { /* ignore transient errors — keep polling */ }
    }
    // Timed out but sprint is still running — advance optimistically (slow dispatch)
    _ksSetStep('dispatch', 'pass', '');
    return true;
  }

  /** Finish the kickoff: hide stepper, reload board, start live poll. */
  async function _ksFinish(label) {
    _ksHide();
    _smgmtShowToast(`Sprint ${sprintLabelDisplay(label)} dispatched`);
    if (typeof _smgmtShowSubView === 'function') _smgmtShowSubView('running');
    await loadSprintMgmt(true, label);
    if (typeof _smgmtLivePollRestart === 'function') _smgmtLivePollRestart();
    for (let i = 0; i < 8; i++) {
      if (_smgmtRunningLabels && _smgmtRunningLabels.has(label)) break;
      await new Promise(r => setTimeout(r, 600));
      await loadSprintMgmt(true, label);
    }
  }

  /**
   * Drive the three-step kickoff flow. Called from _pfConfirm() for both initial
   * run and re-run (via the preflight modal). Shows the Running subview immediately
   * so the stepper is visible while the POST and polls complete (AC1).
   */
  async function smgmtKickoffRun(label, repo) {
    _ksShow(label, repo);

    // Step 1: validate/acquire lock
    if (!await _ksStep1Post()) return;   // AC6: return early on failure

    // Step 2: create sprint branch
    if (!await _ksStep2Branch()) return; // AC6: return early on failure

    // Step 3: dispatch first agents
    if (!await _ksStep3Dispatch()) return; // AC6: return early on failure

    // All steps succeeded → transition to running pane (AC4)
    await _ksFinish(label);
  }

  /**
   * Retry from the step that failed, not from step 1 (AC7).
   * - Step 0 (lock) failed: re-run the full flow
   * - Step 1 (branch) failed: re-poll from step 2, then step 3
   * - Step 2 (dispatch) failed: re-poll from step 3
   */
  async function smgmtKickoffRetry() {
    if (!_ksLabel || !_ksRepo) return;
    const failedStep = _ksFailedStep;
    const label = _ksLabel;

    const errEl = document.getElementById('smgmt-kickoff-error');
    if (errEl) errEl.hidden = true;
    _ksFailedStep = -1;

    if (failedStep <= 0) {
      // Lock failed — re-run full kickoff (new POST needed)
      _ksSetStep('lock',     'pending', '');
      _ksSetStep('branch',   'pending', '');
      _ksSetStep('dispatch', 'pending', '');
      if (!await _ksStep1Post()) return;
      if (!await _ksStep2Branch()) return;
      if (!await _ksStep3Dispatch()) return;
    } else if (failedStep === 1) {
      // Branch failed — sprint POST already succeeded; re-poll from step 2
      _ksSetStep('branch',   'pending', '');
      _ksSetStep('dispatch', 'pending', '');
      if (!await _ksStep2Branch()) return;
      if (!await _ksStep3Dispatch()) return;
    } else {
      // Dispatch failed — re-poll from step 3
      _ksSetStep('dispatch', 'pending', '');
      if (!await _ksStep3Dispatch()) return;
    }

    await _ksFinish(label);
  }
  // apps/dashboard/static/src/sprint-board/finish-modal.js
  /* Finish Sprint modal (issue #367 parity) — extracted from project.html (#797).
   *
   * Opens the finish-sprint modal, previews which tickets close vs. carry forward
   * (and whether a sprint PR will be merged), and confirms the finish. Page
   * helpers and broadly-shared board caches resolve through the page's global
   * scope; modal-local state (`_fsLabel`, `_fsPreview`) is seeded on `window` by
   * ./state.js.
   *
   * Issue #929: After confirm, the modal switches to ProgressActivity bar mode
   * and streams live progress via SSE. Closing the modal does not cancel the
   * background task; reopening reconnects to the in-flight stream.
   */

  /* global _setBodyInert, _clearBodyInert, _smgmtRepo, sprintLabelDisplay,
     escHtml, loadSprintMgmt,
     _fsLabel:writable, _fsPreview:writable, _fsActiveJob:writable,
     renderProgressActivity */

  function _fsOpen() {
    _setBodyInert(["fs-backdrop", "fs-modal"]);
    document.getElementById("fs-backdrop").classList.remove("hidden");
    document.getElementById("fs-modal").classList.remove("hidden");
  }

  function _fsClose() {
    document.getElementById("fs-backdrop").classList.add("hidden");
    document.getElementById("fs-modal").classList.remove("hidden");
    document.getElementById("fs-modal").classList.add("hidden");
    _clearBodyInert();
    // Close the EventSource so we don't leak connections, but keep _fsActiveJob
    // so the modal can reconnect on reopen (AC5 — job continues in background).
    if (_fsActiveJob && _fsActiveJob.es) {
      _fsActiveJob.es.close();
      _fsActiveJob.es = null;
    }
    // Clear preview state; active-job state is preserved for reconnect.
    const snap = _fsActiveJob && _fsActiveJob.snapshot;
    if (!snap || snap.status === "done" || snap.status === "error") {
      _fsActiveJob = null;
    }
    _fsLabel = null;
    _fsPreview = null;
  }

  function _fsCatClass(cat) {
    if (cat === "UAT") return "rr-cat-uat";
    if (cat === "SIT") return "rr-cat-sit";
    if (cat === "needs-rework") return "rr-cat-rework";
    if (cat === "sprint-summary") return "rr-cat-summary";
    return "rr-cat-queued";
  }

  function _fsSelectAll(checked) {
    document
      .querySelectorAll("#fs-ticket-list input[type=checkbox]")
      .forEach((cb) => {
        cb.checked = checked;
      });
  }

  // ── Progress view helpers (issue #929) ────────────────────────────────────────

  function _fsProgressSlot() {
    return document.getElementById("fs-progress");
  }
  function _fsPreviewSlot() {
    return document.getElementById("fs-content");
  }

  /** Switch modal body to ProgressActivity bar mode and set footer buttons. */
  function _fsEnterProgressView(snap) {
    document.getElementById("fs-loading").classList.add("hidden");
    _fsPreviewSlot() && _fsPreviewSlot().classList.add("hidden");
    document.getElementById("fs-error").classList.add("hidden");

    const slot = _fsProgressSlot();
    if (slot) {
      slot.innerHTML = renderProgressActivity(snap, {
        id: "fs-pa",
        retryFn: "_fsRetry",
      });
      slot.classList.remove("hidden");
    }

    // Footer: hide "Merge Sprint", change Cancel → Close
    const confirmBtn = document.getElementById("fs-confirm-btn");
    const cancelBtn = document.getElementById("fs-cancel-btn");
    const retryBtn = document.getElementById("fs-retry-btn");
    if (confirmBtn) confirmBtn.classList.add("hidden");
    if (cancelBtn) cancelBtn.textContent = "Close";
    if (retryBtn) retryBtn.classList.add("hidden");
  }

  /** Update the ProgressActivity component in the progress slot on each SSE event. */
  function _fsUpdateProgress(snap) {
    const slot = _fsProgressSlot();
    if (!slot || slot.classList.contains("hidden")) return;

    const logEl = document.getElementById("pa-log-stream-fs-pa");
    const atBottom =
      !logEl || logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 5;

    slot.innerHTML = renderProgressActivity(snap, {
      id: "fs-pa",
      retryFn: "_fsRetry",
    });

    if (atBottom) {
      const newLog = document.getElementById("pa-log-stream-fs-pa");
      if (newLog) newLog.scrollTop = newLog.scrollHeight;
    }
  }

  /** Handle done snapshot: show summary, update footer, refresh board. */
  function _fsDone(snap) {
    _fsUpdateProgress(snap);
    const cancelBtn = document.getElementById("fs-cancel-btn");
    const retryBtn = document.getElementById("fs-retry-btn");
    if (cancelBtn) cancelBtn.textContent = "Close";
    if (retryBtn) retryBtn.classList.add("hidden");
    _fsActiveJob = null;
    // Refresh the sprint board after a brief pause to let GitHub settle.
    setTimeout(() => loadSprintMgmt(), 1500);
  }

  /** Handle error snapshot: show error state and reveal retry button. */
  function _fsHandleError(snap) {
    _fsUpdateProgress(snap);
    const cancelBtn = document.getElementById("fs-cancel-btn");
    const retryBtn = document.getElementById("fs-retry-btn");
    if (cancelBtn) cancelBtn.textContent = "Close";
    if (retryBtn) retryBtn.classList.remove("hidden");
  }

  /** Connect an EventSource to /finish-stream and drive the progress view. */
  function _fsConnectStream(owner, repoName, label) {
    const url = `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/finish-stream`;
    const es = new EventSource(url);
    if (_fsActiveJob) _fsActiveJob.es = es;

    es.onmessage = (e) => {
      let snap;
      try {
        snap = JSON.parse(e.data);
      } catch {
        return;
      }
      if (snap.ping) return;
      if (_fsActiveJob) _fsActiveJob.snapshot = snap;
      if (snap.status === "done") {
        es.close();
        if (_fsActiveJob) _fsActiveJob.es = null;
        _fsDone(snap);
      } else if (snap.status === "error") {
        es.close();
        if (_fsActiveJob) _fsActiveJob.es = null;
        _fsHandleError(snap);
      } else {
        _fsUpdateProgress(snap);
      }
    };

    es.onerror = () => {
      es.close();
      if (_fsActiveJob) _fsActiveJob.es = null;
    };
  }

  /** Retry a failed finish operation using the stored params. */
  async function _fsRetry() {
    if (!_fsActiveJob) return;
    const { owner, repoName, label, params } = _fsActiveJob;
    const emptySnap = {
      status: "running",
      mode: "bar",
      done: 0,
      total: params.total || 2,
      current: "Retrying…",
      log_tail: [],
    };
    _fsEnterProgressView(emptySnap);
    _fsActiveJob.snapshot = emptySnap;

    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/finish-bg`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...params, confirmed: true }),
        },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      _fsConnectStream(owner, repoName, label);
    } catch (e) {
      const slot = _fsProgressSlot();
      if (slot) {
        slot.innerHTML = renderProgressActivity(
          {
            status: "error",
            mode: "bar",
            error: "Retry failed: " + e.message,
            log_tail: [],
          },
          { id: "fs-pa", retryFn: "_fsRetry" },
        );
      }
      const retryBtn = document.getElementById("fs-retry-btn");
      if (retryBtn) retryBtn.classList.remove("hidden");
    }
  }

  // ── Main entry points ─────────────────────────────────────────────────────────

  async function smgmtFinishSprint(label) {
    const repo = _smgmtRepo();
    if (!repo) return;

    const parts = repo.split("/");
    const owner = parts[0];
    const repoName = parts.slice(1).join("/");

    // If a finish job for this label is still running, reconnect instead of
    // reloading the preview (AC6 — modal shows live progress on reopen).
    if (_fsActiveJob && _fsActiveJob.label === label) {
      _fsLabel = label;
      document.getElementById("fs-modal-title").textContent =
        `Merging ${sprintLabelDisplay(label)}…`;
      _fsOpen();
      const snap = _fsActiveJob.snapshot;
      if (snap) {
        _fsEnterProgressView(snap);
        if (snap.status === "done") {
          _fsDone(snap);
        } else if (snap.status === "error") {
          _fsHandleError(snap);
        } else {
          _fsConnectStream(owner, repoName, label);
        }
      }
      return;
    }

    // Normal preview flow
    _fsLabel = label;
    _fsPreview = null;

    document.getElementById("fs-modal-title").textContent =
      `Merge ${sprintLabelDisplay(label)}?`;
    document.getElementById("fs-loading").classList.remove("hidden");
    document.getElementById("fs-content").classList.add("hidden");
    document.getElementById("fs-error").classList.add("hidden");
    document.getElementById("fs-error").textContent = "";

    // Reset footer to preview state
    const confirmBtn = document.getElementById("fs-confirm-btn");
    const cancelBtn = document.getElementById("fs-cancel-btn");
    const retryBtn = document.getElementById("fs-retry-btn");
    if (confirmBtn) {
      confirmBtn.classList.remove("hidden");
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Merge Sprint";
    }
    if (cancelBtn) cancelBtn.textContent = "Cancel";
    if (retryBtn) retryBtn.classList.add("hidden");

    const progSlot = _fsProgressSlot();
    if (progSlot) progSlot.classList.add("hidden");

    _fsOpen();

    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/finish-preview`,
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

      const listEl = document.getElementById("fs-ticket-list");
      const allTickets = preview.all_tickets || [];
      if (allTickets.length === 0) {
        listEl.innerHTML =
          '<div style="padding:10px;color:var(--text-muted);font-size:13px">No open tickets in this sprint.</div>';
      } else {
        listEl.innerHTML = allTickets
          .map((t) => {
            const catClass = _fsCatClass(t.category);
            const catLabel =
              t.category === "sprint-summary"
                ? "SUMMARY"
                : t.category.toUpperCase();
            return `<label class="rr-ticket-row">
            <input type="checkbox" checked data-issue="${t.number}" data-title="${escHtml(t.title)}" onchange="">
            <span class="rr-ticket-num">#${t.number}</span>
            <span class="rr-ticket-title" title="${escHtml(t.title)}">${escHtml(t.title)}</span>
            <span class="rr-ticket-cat ${catClass}">${escHtml(catLabel)}</span>
          </label>`;
          })
          .join("");
      }

      const actionsEl = document.getElementById("fs-actions");
      const actionRows = [];
      const mergeBranches = preview.merge_branches || [];
      for (const mb of mergeBranches) {
        actionRows.push(
          `<div class="fs-action-row"><i class="ti ti-git-merge"></i> Merge ` +
            `<code>${escHtml(mb.head)}</code> → <code>${escHtml(mb.base)}</code></div>`,
        );
      }
      if (preview.sprint_pr) {
        actionRows.push(`<div class="fs-action-row"><i class="ti ti-git-merge"></i> Merge open PR
          <a href="${escHtml(preview.sprint_pr.url)}" target="_blank" rel="noopener">#${preview.sprint_pr.number}</a></div>`);
      }
      actionRows.push(
        '<div class="fs-action-row"><i class="ti ti-circle-check"></i> Close sprint tickets (labels kept)</div>',
      );
      actionsEl.innerHTML = actionRows.join("");

      document.getElementById("fs-loading").classList.add("hidden");
      document.getElementById("fs-content").classList.remove("hidden");
      if (confirmBtn) confirmBtn.disabled = false;
    } catch (e) {
      document.getElementById("fs-loading").classList.add("hidden");
      const errEl = document.getElementById("fs-error");
      errEl.textContent = "Failed to load preview: " + e.message;
      errEl.classList.remove("hidden");
    }
  }

  async function _fsConfirm() {
    const repo = _smgmtRepo();
    if (!_fsLabel || !repo || !_fsPreview) return;
    const parts = repo.split("/");
    const owner = parts[0];
    const repoName = parts.slice(1).join("/");

    // Collect selected tickets (number + title for progress labels — AC3).
    const checkboxes = Array.from(
      document.querySelectorAll("#fs-ticket-list input[type=checkbox]"),
    );
    const selectedTickets = checkboxes
      .filter((c) => c.checked)
      .map((c) => ({
        number: parseInt(c.dataset.issue, 10),
        title: c.dataset.title || `#${c.dataset.issue}`,
      }));
    const selectedNums = selectedTickets.map((t) => t.number);

    const confirmBtn = document.getElementById("fs-confirm-btn");
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Starting…";
    }

    const bgParams = {
      move_non_uat_to: _fsPreview.next_sprint_label || "",
      selected_ticket_numbers: selectedNums,
      selected_tickets: selectedTickets,
      merge_pr: !!_fsPreview.sprint_pr,
      sprint_pr_url: _fsPreview.sprint_pr ? _fsPreview.sprint_pr.url : null,
      total: selectedNums.length + 2,
    };

    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(_fsLabel)}/finish-bg`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirmed: true, ...bgParams }),
        },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      await res.json();

      // Register active job for reconnect support (AC6)
      const initialSnap = {
        status: "running",
        mode: "bar",
        done: 0,
        total: bgParams.total,
        current: "Starting…",
        log_tail: [],
      };
      _fsActiveJob = {
        label: _fsLabel,
        owner,
        repoName,
        params: bgParams,
        snapshot: initialSnap,
        es: null,
      };

      document.getElementById("fs-modal-title").textContent =
        `Merging ${sprintLabelDisplay(_fsLabel)}…`;
      _fsEnterProgressView(initialSnap);
      _fsConnectStream(owner, repoName, _fsLabel);
    } catch (e) {
      const errEl = document.getElementById("fs-error");
      errEl.textContent = "Failed to start finish: " + e.message;
      errEl.classList.remove("hidden");
      if (confirmBtn) {
        confirmBtn.classList.remove("hidden");
        confirmBtn.disabled = false;
        confirmBtn.textContent = "Merge Sprint";
      }
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
     escHtml, _smgmtShowToast, loadSprintMgmt,
     _smgmtApplyRerunOptimistic, smgmtRunSprint,
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
    if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Create sprint and run'; }
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
      if (confirmBtn) confirmBtn.textContent = `Create & run ${sprintLabelDisplay(_rrVersionedLabel)}`;

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

    const parentLabel = _rrLabel;
    const checkboxes = Array.from(document.querySelectorAll('#rr-ticket-list input[type=checkbox]'));
    const ticketNumbers = checkboxes.filter(c => c.checked).map(c => parseInt(c.dataset.issue, 10));
    if (ticketNumbers.length === 0) return;

    const confirmBtn = document.getElementById('rr-confirm-btn');
    if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Creating…'; }

    try {
      const res = await fetch(
        `/api/sprints/${encodeURIComponent(parentLabel)}/rerun?project=${encodeURIComponent(repo)}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ticket_numbers: ticketNumbers, auto_run: false }),
        }
      );
      if (!res.ok) {
        let detail = await res.text();
        try {
          const parsed = JSON.parse(detail);
          detail = parsed.detail || detail;
        } catch (_) { /* plain-text error body */ }
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
      }
      const data = await res.json();
      const subLabel = data.sub_label;
      _rrClose();
      if (typeof _smgmtApplyRerunOptimistic === 'function') {
        _smgmtApplyRerunOptimistic(parentLabel, subLabel, ticketNumbers);
      }
      await loadSprintMgmt(true);
      const subDisplay = subLabel ? sprintLabelDisplay(subLabel) : 'Sub-sprint';
      if (data.errors && data.errors.length > 0) {
        _smgmtShowToast(`${subDisplay} created with label errors — check GitHub.`);
      } else {
        _smgmtShowToast(`${subDisplay} ready — confirm run`);
      }
      if (subLabel && typeof smgmtRunSprint === 'function') {
        smgmtRunSprint(subLabel);
      }
    } catch (e) {
      const errEl = document.getElementById('rr-error');
      errEl.textContent = 'Failed to re-run sprint: ' + e.message;
      errEl.classList.remove('hidden');
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = _rrVersionedLabel
          ? `Create & run ${sprintLabelDisplay(_rrVersionedLabel)}`
          : 'Create sprint and run';
      }
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
   * Concerns: board render · drag/drop · run-controls · finish modal · rerun modal
   * · bulk-complete modal · plan-next · scheduled-run.
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
  globalThis._fsRetry = _fsRetry;

  // Bulk Complete modal (parent + child lineage)
  globalThis._bcOpen = _bcOpen;
  globalThis._bcClose = _bcClose;
  globalThis._bcCatClass = _bcCatClass;
  globalThis._bcSelectAll = _bcSelectAll;
  globalThis.smgmtBulkCompleteSprint = smgmtBulkCompleteSprint;
  globalThis._bcConfirm = _bcConfirm;

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
  // Stepper functions (issue #933)
  globalThis._pfStepperInit = _pfStepperInit;
  globalThis._pfStepState = _pfStepState;
  globalThis._pfStepperAnimate = _pfStepperAnimate;
  globalThis._pfStepperSummary = _pfStepperSummary;
  // Kickoff stepper (issue #932)
  globalThis.smgmtKickoffRun = smgmtKickoffRun;
  globalThis.smgmtKickoffRetry = smgmtKickoffRetry;

  // Drag & drop + multi-select + ghost pane + board lock (issues #247/#276/#660)
  // computeDropPlan is a DOM-free decision helper kept on the global (and thus in
  // the bundle) for the drag/drop smoke-test contract — see test_..__797.py.
  globalThis.computeDropPlan = computeDropPlan;
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
  globalThis._smgmtBoardProgress = _smgmtBoardProgress;
  globalThis._smgmtBoardLog = _smgmtBoardLog;

  // Board render pipeline (issue #797)
  globalThis.loadSprintMgmt = loadSprintMgmt;
  globalThis._smgmtSprintLabelSortKey = _smgmtSprintLabelSortKey;
  globalThis._smgmtRender = _smgmtRender;
  globalThis._smgmtLabelFilterRender = _smgmtLabelFilterRender;
  globalThis._smgmtLabelFilterApply = _smgmtLabelFilterApply;
  globalThis._smgmtFetchMissingOutcomes = _smgmtFetchMissingOutcomes;
  globalThis._smgmtLoadEstimates = _smgmtLoadEstimates;
  globalThis._smgmtLoadConflicts = _smgmtLoadConflicts;
  globalThis._smgmtLoadDepOrder = _smgmtLoadDepOrder;
  globalThis._smgmtLoadGoals = _smgmtLoadGoals;
  globalThis._smgmtOutcomeBandHtml = _smgmtOutcomeBandHtml;
  globalThis._smgmtOutcomeTicketListHtml = _smgmtOutcomeTicketListHtml;
  globalThis._smgmtLoadFinishCards = _smgmtLoadFinishCards;
  globalThis._smgmtRenderFinishCard = _smgmtRenderFinishCard;
  globalThis._smgmtFinishCardInnerHtml = _smgmtFinishCardInnerHtml;
  globalThis._smgmtCardHtml = _smgmtCardHtml;
  globalThis._smgmtRunningCardHtml = _smgmtRunningCardHtml;
  globalThis._smgmtRunningBoardBannerHtml = _smgmtRunningBoardBannerHtml;
  globalThis._smgmtBoardBannerPatch = _smgmtBoardBannerPatch;
  globalThis._smgmtRunningLevelText = _smgmtRunningLevelText;
  globalThis._smgmtRollupText = _smgmtRollupText;
  globalThis._smgmtTicketSize = _smgmtTicketSize;
  globalThis._smgmtTicketHasEstimate = _smgmtTicketHasEstimate;
  globalThis._smgmtUpdateColRollup = _smgmtUpdateColRollup;
  globalThis._smgmtTicketRowHtml = _smgmtTicketRowHtml;
  globalThis._smgmtRenderBacklog = _smgmtRenderBacklog;
  globalThis._smgmtBacklogTicketHtml = _smgmtBacklogTicketHtml;
  globalThis._smgmtApplyRerunOptimistic = _smgmtApplyRerunOptimistic;

  // Ancestor sprint rows (issue #1043)
  globalThis._smgmtAncestorMergeState = _smgmtAncestorMergeState;
  globalThis._smgmtAncestorCarrySummary = _smgmtAncestorCarrySummary;
  globalThis._smgmtAncestorTicketsHtml = _smgmtAncestorTicketsHtml;
  globalThis._smgmtAncestorRowHtml = _smgmtAncestorRowHtml;
  globalThis.smgmtToggleAncestor = smgmtToggleAncestor;
  globalThis._smgmtUpdateAncestorRow = _smgmtUpdateAncestorRow;

  // Run-on-schedule toggle (issue #863)
  globalThis._smgmtSchedToggleHtml = _smgmtSchedToggleHtml;
  globalThis.smgmtToggleRunOnSchedule = smgmtToggleRunOnSchedule;
  globalThis._smgmtHydrateSchedToggles = _smgmtHydrateSchedToggles;

  // Plan next sprint + pending-sign-off decoration (issue #861)
  globalThis.smgmtPlanNextSprint = smgmtPlanNextSprint;
  globalThis._smgmtLoadPendingSignoff = _smgmtLoadPendingSignoff;

  // History ledger (issue #806 / #797 extraction)
  globalThis._histNeedsActionCount = _histNeedsActionCount;
  globalThis._histLoadLedger = _histLoadLedger;
  globalThis._histScanStale = _histScanStale;
  globalThis._histCleanupStale = _histCleanupStale;
  globalThis._histToggleCard = _histToggleCard;
  globalThis._histToggleFold = _histToggleFold;
  globalThis._histFocusLabel = _histFocusLabel;
  globalThis._histStateChip = _histStateChip;
  globalThis._histRenderLedger = _histRenderLedger;
  globalThis._histRerunSprint = _histRerunSprint;
  globalThis._histToggleDetails = _histToggleDetails;
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
  const root = typeof window !== "undefined" ? window : globalThis;
  root.colorizeLogLine = colorizeLogLine;
  root.escapeLogHtml = escapeLogHtml;
  root.extractRaw = extractRaw;
  root.AGENT_NAMES = AGENT_NAMES;

  // ProgressActivity component (issue #928)
  root.renderProgressActivity = renderProgressActivity;
  root.updateProgressActivityLog = updateProgressActivityLog;
  root.paToggleLog = paToggleLog;
  injectProgressActivityCss();
  root.switchTab = switchTab;
  root.toggleStabDropdown = toggleStabDropdown;
  root.closeAllStabDropdowns = closeAllStabDropdowns;
  globalThis.switchTab = switchTab;
  globalThis.toggleStabDropdown = toggleStabDropdown;
  globalThis.closeAllStabDropdowns = closeAllStabDropdowns;
})();
//# sourceMappingURL=bundle.js.map
