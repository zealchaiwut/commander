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
/* global _blApplyFilters, _blBacklogAll, _blSyncFilterPills, _blUpdateActions, _smgmtEnsureCapData, _smgmtLoadMiniRail, _smgmtMiniRailRestoreCached, _smgmtRenderAllCapBars, _smgmtUpdateSubnav, _cachedFullRepo, _estDataCache, _slug, _smgmtActiveAgentsHtml, _smgmtAgentTagClass, _smgmtApplySort, _smgmtBulkEstimate, _smgmtBySprint, _smgmtCapacityInputHtml, _smgmtCheckEstimatorHealth, _smgmtCloseIssueOpen, _smgmtConflictsByIssue, _smgmtCtxMenuOpen, _smgmtDagDataCache, _smgmtData, _smgmtDeactivatedLabels, _smgmtDepOrderByIssue, _smgmtEstimateBadgeHtml, _smgmtEstimatorAvailable, _smgmtFilterApply, _smgmtFinishCards, _smgmtFinishedLabels, _smgmtHasCompletedTickets, _smgmtInitCapacityGauges, _smgmtInjectOutcomeBand, _smgmtIsCancelled, _smgmtKbRestoreFocus, _smgmtLabelColors, _smgmtLabelFilterToggle, _smgmtLabelFilterToggleExpand, _smgmtLastLabelIssues, _smgmtLevelsHtml, _smgmtLiveAgentBadgesHtml, _smgmtLiveCache, _smgmtLiveCacheRepo, _smgmtLiveLogLinesHtml, _smgmtLivePollRestart, _smgmtLingerRestore, _smgmtLingerStart, _smgmtIsLinger, _smgmtLingerLive, _smgmtOutcomeCache, _smgmtOutcomeLogHtml, _smgmtPrimaryRunningLabel, _smgmtReEstimate, _smgmtRepo, _smgmtRiskFlagIconsHtml, _smgmtRowMenuOpen, _smgmtRunningViewUpdate, _smgmtSchedDepHtml, _smgmtSetSprintTokenEl, _smgmtStateMeta, _smgmtTicketToSprint, _smgmtUpdateCapacityGauge, _smgmtUpdateCleanupBtn, _smgmtUpdateConflictBadge, _smgmtUpdateDepOrderBadge, _smgmtUpdateEstimateBadge, _smgmtSelectedIssues, _smgmtRowClickSelect, _smgmtUpdateSelectionUI, _smgmtLoadPlanningInsights, _smgmtLoadEstVsActual, _smgmtToggleEstVsActual, escHtml, sprintLabelDisplay, colorizeLogLine,
   _smgmtAnySprintRunning:writable, _smgmtOrderedLabels:writable, _smgmtRunningLabels:writable,
   sprintHealthStripInit */
/* eslint-enable no-unused-vars */

import { renderProgressActivity } from "../progress-activity.js";

/** Tracks which labels are resolved ancestors on the current board render. */
let _smgmtResolvedAncestors = new Set();

/**
 * Per-label card index from the /api/board aggregate response (issue #1638).
 * Set to a {label → card} object when the aggregate path is active; null on
 * the legacy multi-call path. Exposed on window._smgmtAggregateCards so that
 * inline project.html functions (e.g. _smgmtLoadMiniRail) can short-circuit.
 */
let _smgmtAggregateCards = null;

/**
 * Build a {label → card} index from a /api/board aggregate response.
 * Lineage chain ancestors are registered under their own label so that
 * per-sprint loaders can look up every sprint regardless of section.
 * Exported for unit tests (issue #1638).
 */
export function _smgmtBuildAggCards(agg) {
  const sections = agg.sections || {};
  const all = [
    ...(sections.running || []),
    ...(sections.needs_rework || []),
    ...(sections.ready_to_merge || []),
    ...(sections.draft || []),
    ...(sections.lineage || []),
  ];
  const idx = {};
  for (const card of all) {
    if (card.label) idx[card.label] = card;
    // Register lineage chain ancestors — their card is the latest member's card
    for (const cl of card.chain || []) {
      if (!idx[cl]) idx[cl] = card;
    }
  }
  return idx;
}

/**
 * Transform a /api/board aggregate response into the data shape that
 * _smgmtRender(data) expects.
 * Exported for unit tests (issue #1638).
 *
 * Differences from the legacy shape:
 *  - _aggregateBuckets: {label → bucket} — server-computed; _smgmtCardBucket
 *    uses this to skip the outcome-cache–based derivation (issue #1638).
 *  - sprint_plan_states is always {} — bucket comes from _aggregateBuckets.
 *  - sprint_rerun_into is always {} — collapse is derived from label patterns
 *    and sprint_parents, same as the legacy path.
 */
export function _smgmtAggToRenderData(agg) {
  const sections = agg.sections || {};
  const allCards = [
    ...(sections.running || []),
    ...(sections.needs_rework || []),
    ...(sections.ready_to_merge || []),
    ...(sections.draft || []),
    ...(sections.lineage || []),
  ];

  // Lifecycle states that indicate the sprint has had at least one run attempt
  const _ranStates = new Set([
    "running",
    "needs_rework",
    "ready_to_merge",
    "partial_finished",
    "failed",
    "completed",
  ]);

  const issues = [];
  const sprintLabels = new Set();
  const sprint_has_run = {};
  const sprint_parents = {};
  const aggregateBuckets = {};

  const sectionEntries = [
    ["running", sections.running || []],
    ["needs_rework", sections.needs_rework || []],
    ["ready_to_merge", sections.ready_to_merge || []],
    ["draft", sections.draft || []],
    ["lineage", sections.lineage || []],
  ];

  // Sections where a 0-ticket card is a zombie — action row is suppressed
  const _postRunSections = new Set(["needs_rework", "ready_to_merge"]);
  const staleNoTicketLabels = new Set();

  for (const [sectionName, cards] of sectionEntries) {
    for (const card of cards) {
      const label = card.label;
      if (!label) continue;

      sprintLabels.add(label);
      aggregateBuckets[label] = sectionName;
      sprint_has_run[label] = _ranStates.has(card.lifecycle_state);

      // Propagate server stale_no_tickets flag; only valid for post-run sections
      if (card.stale_no_tickets && _postRunSections.has(sectionName)) {
        staleNoTicketLabels.add(label);
      }

      // Tickets with sprint_label set (only for the card's own label)
      for (const t of card.tickets || []) {
        issues.push({ ...t, sprint_label: label });
      }

      // Lineage chain: register ancestors and set parent links
      const chain = card.chain || [];
      for (let i = 0; i < chain.length; i++) {
        const cl = chain[i];
        sprintLabels.add(cl);
        if (!(cl in aggregateBuckets)) aggregateBuckets[cl] = "lineage";
        if (!(cl in sprint_has_run)) sprint_has_run[cl] = sprint_has_run[label];
      }
      // Link each member to its predecessor as parent (child → parent)
      for (let i = 1; i < chain.length; i++) {
        if (!sprint_parents[chain[i]]) sprint_parents[chain[i]] = chain[i - 1];
      }
    }
  }

  // Backlog tickets (no sprint_label)
  for (const t of (sections.backlog || {}).tickets || []) {
    issues.push({ ...t, sprint_label: null });
  }

  // Ordered sprint labels (ascending sprint number then sub-index)
  const order = [...sprintLabels].sort((a, b) => {
    const ma = String(a).match(/^sprint-(\d+)(?:\.(\d+))?$/);
    const mb = String(b).match(/^sprint-(\d+)(?:\.(\d+))?$/);
    if (!ma || !mb) return String(a).localeCompare(String(b));
    const na = parseInt(ma[1], 10);
    const nb = parseInt(mb[1], 10);
    if (na !== nb) return na - nb;
    return parseInt(ma[2] || 0, 10) - parseInt(mb[2] || 0, 10);
  });

  // Unique base sprint integers
  const sprintNumSet = new Set();
  for (const l of order) {
    const m = String(l).match(/^sprint-(\d+)/);
    if (m) sprintNumSet.add(parseInt(m[1], 10));
  }
  const sprints = [...sprintNumSet].sort((a, b) => a - b);

  // Finished sprints: lifecycle_state === "completed"
  const finished_sprints = allCards
    .filter((c) => c.lifecycle_state === "completed" && c.label)
    .map((c) => c.label);

  return {
    sprints,
    order,
    issues,
    finished_sprints,
    merged_sprints: [...finished_sprints],
    sprint_parents,
    sprint_rerun_into: {},
    sprint_plan_states: {},
    sprint_has_run,
    sprint_signoff: {},
    _aggregateBuckets: aggregateBuckets,
    _staleNoTicketLabels: staleNoTicketLabels,
  };
}

/** Sign-off gate state for a sprint label (issue #862): 'pending' | 'approved' | null. */
function _smgmtSignoffState(label) {
  if (
    typeof globalThis !== "undefined" &&
    globalThis._commanderFeatures &&
    globalThis._commanderFeatures.signoff !== true
  ) {
    return null;
  }
  return ((_smgmtData && _smgmtData.sprint_signoff) || {})[label] || null;
}

function _smgmtSignoffBadgeHtml(label) {
  if (_smgmtSignoffState(label) !== "pending") return "";
  return '<span class="sc-signoff-badge">Pending sign-off</span>';
}

function _smgmtSignoffActionsHtml(label) {
  if (_smgmtSignoffState(label) !== "pending") return "";
  const e = escHtml(label);
  return (
    `<button class="smgmt-approve-btn" type="button"` +
    ` onclick="smgmtApproveSprint('${e}')">` +
    `<i class="ti ti-check"></i> Approve</button>` +
    `<button class="smgmt-reject-btn" type="button"` +
    ` onclick="smgmtRejectSprint('${e}')">` +
    `<i class="ti ti-x"></i> Reject</button>`
  );
}

/** When false (default), sprint goal is optional and does not gate Run Sprint. */
function _smgmtGoalRequired() {
  const f = typeof globalThis !== "undefined" && globalThis._commanderFeatures;
  if (!f) return false;
  return f.goal_required === true;
}

/** Return the Definition of Ready gate mode: 'block', 'warn', or 'off'. */
export function _smgmtDorMode() {
  const f = typeof globalThis !== "undefined" && globalThis._commanderFeatures;
  if (!f) return "off";
  const m = f.definition_of_ready_mode;
  return m === "block" || m === "warn" || m === "off" ? m : "off";
}

/**
 * Evaluate a single ticket's readiness against the Definition of Ready rules.
 * Returns {ready: boolean, reasons: string[]}.
 * Checks: missing AC, missing design ref, missing test plan, missing estimate, XL-split required.
 * Runs purely on ticket data already in the board — no fetch calls.
 * Heading patterns align with ticket_spec._SECTION_PATTERNS (issue #1539).
 */
export function _smgmtReadinessCheck(ticket) {
  if (!ticket) return { ready: false, reasons: ["invalid ticket"] };
  const reasons = [];
  const body = (ticket.body || "").trim();

  if (!body || !/^#{1,6}\s+(acceptance\s+criteria|acceptance)\b/im.test(body)) {
    reasons.push("missing AC");
  }
  if (!/^#{1,6}\s+(design\s+references?|design\s+refs?)\s*$/im.test(body)) {
    reasons.push("missing design ref");
  }
  if (!/^#{1,6}\s+(uat\s+test\s+steps|test\s+plan)\s*$/im.test(body)) {
    reasons.push("missing test plan");
  }

  const size = _smgmtTicketSize(ticket);
  if (!size) {
    reasons.push("missing estimate");
  } else if (size === "XL") {
    reasons.push("XL-split required");
  }

  return { ready: reasons.length === 0, reasons };
}

/**
 * Return HTML for the per-ticket ✓/✗ readiness badge.
 * Uses the existing checklist icon style (ti-circle-check / ti-circle-x).
 */
export function _smgmtReadinessBadgeHtml(ticket) {
  const { ready, reasons } = _smgmtReadinessCheck(ticket);
  if (ready) {
    return `<span class="smgmt-dor-badge smgmt-dor-badge--ready" title="Ready"><i class="ti ti-circle-check"></i></span>`;
  }
  const reasonText = escHtml(reasons.join(" · "));
  const titleText = escHtml("Not ready: " + reasons.join(", "));
  return (
    `<span class="smgmt-dor-badge smgmt-dor-badge--notready" title="${titleText}">` +
    `<i class="ti ti-circle-x"></i>` +
    `<span class="smgmt-dor-reasons">${reasonText}</span>` +
    `</span>`
  );
}

/**
 * Return not-ready tickets as [{number, title, reasons}] for DOR gating.
 */
export function _smgmtDorNotReadyTickets(tickets) {
  const result = [];
  for (const t of tickets || []) {
    const { ready, reasons } = _smgmtReadinessCheck(t);
    if (!ready)
      result.push({ number: t.number, title: t.title || "", reasons });
  }
  return result;
}

export async function loadSprintMgmt(silent, optimisticRunningLabel) {
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
    // Delivery-health stat strip — one-time fetch per page load (issue #1849).
    if (typeof sprintHealthStripInit === "function") {
      sprintHealthStripInit(_slug);
    }

    // Pre-warm capacity data concurrently with sprint data — don't block render on it.
    // The post-DOM-build call at _smgmtEnsureCapData(false) will pick up the result.
    if (typeof _smgmtEnsureCapData === "function") {
      _smgmtEnsureCapData();
    }

    // Aggregate path: single fetch to /api/board (issue #1789 cutover)
    _smgmtAggregateCards = null; // reset before fetch
    const aggResp = await fetch(
      "/api/board?project=" + encodeURIComponent(repo),
    );
    if (!aggResp.ok) {
      let msg = "Failed to load board.";
      const d = await aggResp.json().catch(() => null);
      const detail = d && typeof d.detail === "string" ? d.detail : "";
      if (aggResp.status === 429 || /rate limit/i.test(detail)) {
        msg = detail || "GitHub API rate limit reached — retry shortly.";
      }
      throw new Error(msg);
    }
    const agg = await aggResp.json();

    // Build per-label card index so per-sprint loaders can skip fetches
    _smgmtAggregateCards = _smgmtBuildAggCards(agg);
    if (typeof window !== "undefined")
      window._smgmtAggregateCards = _smgmtAggregateCards;

    if (_smgmtLiveCacheRepo !== repo) {
      _smgmtLiveCacheRepo = repo;
      for (const k of Object.keys(_smgmtLiveCache)) delete _smgmtLiveCache[k];
    }
    if (typeof _smgmtLingerRestore === "function") _smgmtLingerRestore(repo);

    // Derive running labels from aggregate sections
    const prevRunningAgg = new Set(_smgmtRunningLabels);
    _smgmtRunningLabels = new Set();
    _smgmtAnySprintRunning = false;
    for (const card of (agg.sections || {}).running || []) {
      if (card.label) _smgmtRunningLabels.add(card.label);
    }
    _smgmtAnySprintRunning = _smgmtRunningLabels.size > 0;
    for (const label of prevRunningAgg) {
      if (
        !_smgmtRunningLabels.has(label) &&
        typeof _smgmtLingerStart === "function"
      ) {
        _smgmtLingerStart(label);
      }
    }
    if (optimisticRunningLabel) {
      _smgmtRunningLabels.add(optimisticRunningLabel);
      _smgmtAnySprintRunning = true;
    }

    const data = _smgmtAggToRenderData(agg);
    _smgmtRender(data);

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

export function _smgmtSprintLabelSortKey(label) {
  const m = String(label).match(/^sprint-(\d+(?:\.\d+)*)$/);
  if (!m) return [Infinity];
  return m[1].split(".").map((n) => parseInt(n, 10));
}

/** Base label for sprint-N or sprint-N.M (matches history ledger grouping). */
export function _smgmtSprintBaseLabel(label) {
  const m = String(label).match(/^sprint-(\d+)(?:\.(\d+))?$/);
  return m ? `sprint-${m[1]}` : String(label);
}

/** Sub-index for sprint-N.M (0 for base sprint-N). */
export function _smgmtSprintSubIndex(label) {
  const m = String(label).match(/^sprint-(\d+)(?:\.(\d+))?$/);
  return m && m[2] ? parseInt(m[2], 10) : 0;
}

function _smgmtChildrenForParent(parentLabel, parents, order) {
  const fromMeta = (order || []).filter(
    (l) => (parents || {})[l] === parentLabel,
  );
  const fromLabel =
    _smgmtSprintSubIndex(parentLabel) === 0
      ? (order || []).filter(
          (l) =>
            l !== parentLabel &&
            _smgmtSprintBaseLabel(l) === parentLabel &&
            _smgmtSprintSubIndex(l) > 0,
        )
      : [];
  return [...new Set([...fromMeta, ...fromLabel])];
}

export function _smgmtCompareSprintLabels(a, b) {
  const ka = _smgmtSprintLabelSortKey(a);
  const kb = _smgmtSprintLabelSortKey(b);
  for (let i = 0; i < Math.max(ka.length, kb.length); i++) {
    const d = (ka[i] ?? -1) - (kb[i] ?? -1);
    if (d !== 0) return d;
  }
  return 0;
}

/** Latest child sub-sprint for a parent label present in the current order. */
export function _smgmtChildSprintLabel(parentLabel, parents, rerunInto, order) {
  if (rerunInto && rerunInto[parentLabel]) return rerunInto[parentLabel];
  const children = _smgmtChildrenForParent(parentLabel, parents, order);
  if (!children.length) return null;
  return [...children].sort(_smgmtCompareSprintLabels)[children.length - 1];
}

/** Newest label in a base sprint lineage (sprint-N and all sprint-N.M siblings). */
export function _smgmtLatestLineageLabel(baseLabel, parents, rerunInto, order) {
  const base = _smgmtSprintBaseLabel(baseLabel);
  const members = (order || []).filter(
    (l) =>
      l === base ||
      (_smgmtSprintBaseLabel(l) === base && _smgmtSprintSubIndex(l) > 0),
  );
  if (!members.length) return null;
  return [...members].sort(_smgmtCompareSprintLabels)[members.length - 1];
}

/** Collapse parent into Lineage whenever a child sub-sprint exists in order. */
export function _smgmtShouldCollapseParent(
  parentLabel,
  parents,
  rerunInto,
  order,
) {
  return Boolean(
    _smgmtChildSprintLabel(parentLabel, parents, rerunInto, order),
  );
}

/** Lineage row: parent when a child exists, or superseded rerun siblings (85.1 when 85.2 exists). */
export function _smgmtShouldCollapseToLineage(
  label,
  parents,
  rerunInto,
  order,
) {
  if (_smgmtShouldCollapseParent(label, parents, rerunInto, order)) return true;
  const base = _smgmtSprintBaseLabel(label);
  const latest = _smgmtLatestLineageLabel(base, parents, rerunInto, order);
  if (!latest || label === latest) return false;
  return _smgmtCompareSprintLabels(label, latest) < 0;
}

/**
 * Pure helper for the "Clean up empty sprints" bulk action (issue #457).
 *
 * Computes the leading consecutive run of plain sprint-N labels with 0 tickets.
 * Sub-sprint labels (sprint-N.M) are intentionally excluded — those zombie
 * cards are handled per-card via the Delete button (approach b, issue #2061).
 * Returns [] when no active sprint exists so nothing is eligible for cleanup
 * (issue #658).
 *
 * @param {string[]} orderedLabels - sprint labels in display order
 * @param {Array<{sprint_label: string|null}>} issues - open issue list
 * @returns {string[]} leading empty plain-sprint labels eligible for deletion
 */
export function _smgmtComputeLeadingEmpty(orderedLabels, issues) {
  const labeled = new Set((issues || []).map((i) => i.sprint_label).filter(Boolean));
  // Mark a base number active when any sprint-N or sprint-N.M has tickets.
  const activeBases = new Set();
  for (const lbl of labeled) {
    const m = /^sprint-(\d+)/.exec(lbl);
    if (m) activeBases.add(parseInt(m[1], 10));
  }
  const leadingEmpty = [];
  let foundActive = false;
  for (const lbl of orderedLabels) {
    if (!/^sprint-\d+$/.test(lbl)) continue; // sub-sprint zombies handled per-card (approach b, #2061)
    const base = parseInt(lbl.replace('sprint-', ''), 10);
    if (activeBases.has(base)) { foundActive = true; break; }
    leadingEmpty.push(lbl);
  }
  return foundActive ? leadingEmpty : [];
}

export function _smgmtRender(data) {
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
  // Merged/shipped sprints (sign-off / lifecycle-completed / merged-branch) —
  // a subset of _finishedSet. These belong to History only and are dropped from
  // the board even with an open UAT ticket, so a merged sprint stops showing as
  // a Ready-to-merge card while History already lists it Completed. A bare
  // run-end summary (in _finishedSet but NOT here) still needs its merge card.
  const _mergedSet = new Set(data.merged_sprints || []);

  // Use order list if available (includes sub-labels), else build from integer sprints ascending
  const orderedLabelsRaw =
    order.length > 0
      ? order.filter((l) => /^sprint-\d+(\.\d+)*$/.test(l))
      : [...sprints].sort((a, b) => a - b).map((n) => `sprint-${n}`);

  const _sprintParents = data.sprint_parents || {};
  const _rerunInto = data.sprint_rerun_into || {};

  // When a child sub-sprint exists (e.g. 85.1), collapse the parent (85) into
  // the Lineage section — do not show it in Draft / Ready to merge (issue #512).
  // Superseded siblings (85.1 when 85.2 is the latest) also belong in Lineage.
  _smgmtResolvedAncestors = new Set();
  const orderedLabels = orderedLabelsRaw.filter((label) => {
    if (
      _smgmtShouldCollapseToLineage(
        label,
        _sprintParents,
        _rerunInto,
        orderedLabelsRaw,
      )
    ) {
      // A superseded ancestor stays on the active board only while the lineage is
      // still in play. Once the latest member is finished (whole lineage merged),
      // the group belongs to History — drop the ancestor instead of parking it in
      // a permanent Lineage section. Without this a collapsed ancestor returned
      // early and never reached the _finishedSet drop below, so a fully-completed
      // lineage lingered on the board forever.
      const latest = _smgmtLatestLineageLabel(
        _smgmtSprintBaseLabel(label),
        _sprintParents,
        _rerunInto,
        orderedLabelsRaw,
      );
      if (latest && _finishedSet.has(latest)) return false;
      _smgmtResolvedAncestors.add(label);
      return true;
    }
    // A merged/shipped sprint belongs to History only — drop it before the
    // open-ticket check so an open UAT sign-off ticket can't keep it on the
    // board as a Ready-to-merge zombie (its sign-off ticket still lives in the
    // UAT column). Summary-only finished sprints are NOT in _mergedSet, so they
    // fall through and keep their merge card while tickets remain.
    if (_mergedSet.has(label)) return false;
    const tickets = bySprint[label] || [];
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

  // ── Planning board layout (issue #1044): inject Focus Guide ─────────────────
  const focusGuideEl = document.getElementById("smgmt-focus-guide");
  if (focusGuideEl) {
    focusGuideEl.innerHTML = _smgmtFocusGuideHtml(
      data,
      orderedLabels,
      bySprint,
    );
  }

  // ── Categorize sprints into Lineage / Ready to merge / Rework / Running / Draft ─
  const _planStates = data.sprint_plan_states || {};

  const _buildCard = (label) => {
    const tickets = bySprint[label] || [];

    if (_smgmtResolvedAncestors.has(label)) {
      let childLabel = _smgmtChildSprintLabel(
        label,
        _sprintParents,
        _rerunInto,
        orderedLabelsRaw,
      );
      if (!childLabel) {
        const latest = _smgmtLatestLineageLabel(
          _smgmtSprintBaseLabel(label),
          _sprintParents,
          _rerunInto,
          orderedLabelsRaw,
        );
        if (latest && latest !== label) childLabel = latest;
      }
      const cachedOutcome = _smgmtOutcomeCache[label];
      return (
        `<div class="smgmt-sprint-unit" id="smgmt-unit-${escHtml(label)}">` +
        _smgmtAncestorRowHtml(label, cachedOutcome, childLabel) +
        `</div>`
      );
    }

    // Draft/planning sprints render via the same _smgmtCardHtml as planned/running
    // cards (unified design: budget bar + dispatch-level mini-rail, no sprint-goal
    // input — the goal field was dropped). Previously they used _smgmtDraftCardHtml.

    // Linger is Running-pane only: on the Board a lingering (just-finished) sprint
    // still resolves its real ingested outcome, so the card renders as a settled
    // post-run card (rework / ready-to-merge) rather than a frozen live snapshot.
    // Only an actively-running sprint nulls its outcome (live data drives it).
    const outcome = _smgmtRunningLabels.has(label)
      ? null
      : _smgmtOutcomeCache[label] || null;
    const parent = _sprintParents[label] || null;
    const cardHtml = _smgmtCardHtml(
      label,
      null,
      tickets,
      outcome,
      false,
      parent,
      _smgmtFinishedLabels.has(label),
    );
    return (
      `<div class="smgmt-sprint-unit" id="smgmt-unit-${escHtml(label)}">` +
      cardHtml +
      `</div>`
    );
  };

  const lineageLabels = orderedLabels.filter((l) =>
    _smgmtResolvedAncestors.has(l),
  );
  const otherLabels = orderedLabels.filter(
    (l) => !_smgmtResolvedAncestors.has(l),
  );

  const mergeLabels = [];
  const reworkLabels = [];
  const runningLabels = [];
  const draftLabels = [];

  for (const lbl of otherLabels) {
    const bucket = _smgmtCardBucket(lbl, _planStates);
    if (bucket === "ready_to_merge") mergeLabels.push(lbl);
    else if (bucket === "needs_rework") reworkLabels.push(lbl);
    else if (bucket === "running") runningLabels.push(lbl);
    else draftLabels.push(lbl);
  }

  const sectionLabel = (text, cls) =>
    `<div class="smgmt-section-label ${cls}">${text}</div>`;

  const lineageRangeLabel = (labels) => {
    if (!labels.length) return "Lineage";
    const first = sprintLabelDisplay(labels[0]).replace("Sprint ", "");
    const last = sprintLabelDisplay(labels[labels.length - 1]).replace(
      "Sprint ",
      "",
    );
    return first === last ? `Lineage ${first}` : `Lineage ${first} → ${last}`;
  };

  let cards = "";

  // Section: Lineage (collapsed ancestor sprints)
  if (lineageLabels.length > 0) {
    cards += sectionLabel(
      lineageRangeLabel(lineageLabels),
      "smgmt-section-lineage",
    );
    cards += `<div class="smgmt-board-section smgmt-board-section--lineage">`;
    cards += lineageLabels.map(_buildCard).join("");
    cards += `</div>`;
  }

  // Section: Ready to merge (post-run success — operator sign-off)
  if (mergeLabels.length > 0) {
    const mergeLabel =
      mergeLabels.length === 1
        ? "Ready to merge — 1"
        : `Ready to merge — ${mergeLabels.length}`;
    cards += sectionLabel(mergeLabel, "smgmt-section-merge");
    cards += `<div class="smgmt-board-section smgmt-board-section--merge">`;
    cards += mergeLabels.map(_buildCard).join("");
    cards += `</div>`;
  }

  // Section: Needs rework (failed / partial post-run)
  if (reworkLabels.length > 0) {
    const reworkLabel =
      reworkLabels.length === 1
        ? "Needs rework — 1"
        : `Needs rework — ${reworkLabels.length}`;
    cards += sectionLabel(reworkLabel, "smgmt-section-rework");
    cards += `<div class="smgmt-board-section smgmt-board-section--rework">`;
    cards += reworkLabels.map(_buildCard).join("");
    cards += `</div>`;
  }

  // Section: Running (live sprint — detail in Running pane)
  if (runningLabels.length > 0) {
    const runLabel =
      runningLabels.length === 1
        ? "Running — 1"
        : `Running — ${runningLabels.length}`;
    cards += sectionLabel(runLabel, "smgmt-section-running");
    cards += `<div class="smgmt-board-section smgmt-board-section--running">`;
    cards += runningLabels.map(_buildCard).join("");
    cards += `</div>`;
  }

  // Section: Draft — pending to run + active planning (single blue frame)
  if (draftLabels.length > 0) {
    const draftLabel =
      draftLabels.length === 1 ? "Draft — 1" : `Draft — ${draftLabels.length}`;
    cards += sectionLabel(draftLabel, "smgmt-section-draft");
    cards += `<div class="smgmt-board-section smgmt-board-section--draft">`;
    cards += draftLabels.map(_buildCard).join("");
    cards += `</div>`;
  }

  listEl.innerHTML =
    cards || '<div class="loading-msg">No sprints found.</div>';

  // Populate capacity gauges (reads localStorage + estimate cache)
  _smgmtInitCapacityGauges(orderedLabels);

  // Sprint capacity budget bars (issue #801): render now with cached data,
  // then lazily fetch budget settings + per-size cost averages and re-render.
  _smgmtRenderAllCapBars();
  _smgmtEnsureCapData(false);

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
  // Restore cached HTML synchronously (no flicker), then refresh async if stale.
  if (typeof _smgmtMiniRailRestoreCached === "function") {
    _smgmtMiniRailRestoreCached(orderedLabels, bySprint);
  }
  _smgmtLoadMiniRail(orderedLabels, bySprint);

  // Inject stale-estimate warnings inline for planning sprints (issue #2264 AC1).
  if (typeof _smgmtLoadPlanningInsights === "function") {
    _smgmtLoadPlanningInsights(orderedLabels);
  }

  // Inject estimate-vs-actual section for finished sprints (issue #2264 AC3).
  if (typeof _smgmtLoadEstVsActual === "function") {
    _smgmtLoadEstVsActual(orderedLabels);
  }

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

export function _smgmtLabelFilterRender(issues) {
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

export function _smgmtLabelFilterApply() {
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

/** Bucket a sprint label for board section grouping (issue #1044). */
export function _smgmtCardBucket(label, planStates) {
  // Aggregate path (issue #1638): server-computed bucket overrides the outcome-cache logic
  const aggBuckets = _smgmtData && _smgmtData._aggregateBuckets;
  if (aggBuckets && label in aggBuckets) {
    // Running labels may have changed since the aggregate snapshot (SSE)
    if (_smgmtRunningLabels.has(label)) return "running";
    const inLingerAgg =
      typeof _smgmtIsLinger === "function" && _smgmtIsLinger(label);
    if (inLingerAgg && !(_smgmtData.sprint_has_run || {})[label])
      return "running";
    const b = aggBuckets[label];
    if (
      b === "running" ||
      b === "needs_rework" ||
      b === "ready_to_merge" ||
      b === "draft"
    ) {
      return b;
    }
    // "lineage" is not a valid bucket — handled by _smgmtResolvedAncestors; fall through
  }

  if (_smgmtRunningLabels.has(label)) return "running";
  const inLinger =
    typeof _smgmtIsLinger === "function" && _smgmtIsLinger(label);
  const outcome = _smgmtOutcomeCache[label] || null;
  const hasRun = _smgmtHasLedgerRun(label);

  if (inLinger && !hasRun) return "running";

  if (hasRun && outcome && typeof _smgmtStateMeta === "function") {
    const meta = _smgmtStateMeta(outcome, (outcome.issues || []).length);
    const st = meta.state;
    if (st === "ready_to_merge" || st === "completed") return "ready_to_merge";
    if (st === "needs_rework" || st === "partial_finished")
      return "needs_rework";
  }
  if (hasRun && _smgmtFinishedLabels && _smgmtFinishedLabels.has(label)) {
    return "ready_to_merge";
  }
  if (hasRun && outcome) {
    const lc = (outcome.lifecycle || "").toLowerCase();
    if (lc === "ready_to_merge") return "ready_to_merge";
    if (lc === "needs_rework" || lc === "partial_finished")
      return "needs_rework";
  }
  if (hasRun && !outcome && inLinger) return "running";

  const ps = ((planStates || {})[label] || "").toLowerCase();
  if (hasRun && ["draft", "planned", "planning"].includes(ps)) return "draft";

  return "draft";
}

/** True when the history ledger says this label has its own run (not ticket labels). */
function _smgmtHasLedgerRun(label) {
  return Boolean((_smgmtData?.sprint_has_run || {})[label]);
}

export async function _smgmtFetchMissingOutcomes(orderedLabels, _bySprint) {
  const repo = _smgmtRepo();
  if (!repo) return;

  for (const label of orderedLabels) {
    if (_smgmtRunningLabels.has(label)) continue;
    if (_smgmtOutcomeCache[label] !== undefined) continue;
    const card = _smgmtAggregateCards && _smgmtAggregateCards[label];
    if (!card || card.outcome == null) continue;
    const outcome = card.outcome;
    _smgmtOutcomeCache[label] = outcome;
    const isAncestor = _smgmtResolvedAncestors.has(label);
    if (isAncestor) {
      _smgmtUpdateAncestorRow(label, outcome);
    } else {
      _smgmtInjectOutcomeBand(label, outcome);
    }
  }
}

/** Build a minimal outcome snapshot from board tickets (GitHub label columns). */
export function _smgmtOutcomeFromBoard(label, tickets) {
  if (!tickets || tickets.length === 0) return null;
  const issues = tickets.map((t) => {
    const labelNames = (t.labels || []).map((l) => l.name);
    let outcome = "skipped";
    if (labelNames.includes("UAT-approved") || t.status === "done")
      outcome = "done";
    else if (
      labelNames.includes("needs-rework") ||
      labelNames.includes("need-rework")
    )
      outcome = "failed";
    else if (t.status === "uat") outcome = "uat";
    else if (t.status === "sit" || t.status === "in-progress")
      outcome = "skipped";
    return { number: t.number, title: t.title || "", outcome };
  });
  const counts = { done: 0, failed: 0, skipped: 0, uat: 0 };
  for (const iss of issues) {
    const k = iss.outcome === "uat" ? "uat" : iss.outcome;
    if (counts[k] !== undefined) counts[k] += 1;
  }
  return {
    sprint_label: label,
    partial: true,
    state: "partial",
    lifecycle: "unknown",
    counts,
    wall_clock_secs: 0,
    issues,
  };
}

export async function _smgmtLoadEstimates(orderedLabels, bySprint) {
  const repo = _smgmtRepo();
  if (!repo) return;

  await Promise.all(
    orderedLabels.map(async (label) => {
      const tickets = bySprint[label] || [];
      if (tickets.length === 0) return;
      for (const t of tickets) _smgmtTicketToSprint[t.number] = label;
      const card = _smgmtAggregateCards && _smgmtAggregateCards[label];
      if (!card) return;
      const estEl = document.getElementById(`smgmt-est-${label}`);
      if (estEl && card.estimate_hours != null) {
        const h = card.estimate_hours;
        const display = Number.isInteger(h)
          ? `${h}h`
          : `${parseFloat(h.toFixed(1))}h`;
        estEl.textContent = `${display} estimated`;
      }
      _smgmtSetSprintTokenEl(label, {});
    }),
  );
}

export async function _smgmtLoadConflicts(orderedLabels, bySprint) {
  const repo = _smgmtRepo();
  if (!repo) return;

  await Promise.all(
    orderedLabels.map(async (label) => {
      if (_smgmtRunningLabels.has(label)) return;
      if (_smgmtFinishedLabels.has(label)) return;
      const card = _smgmtAggregateCards && _smgmtAggregateCards[label];
      if (!card || !card.conflicts) return;
      const tickets = bySprint[label] || [];
      const pending = tickets.filter(
        (t) => (t.status || "backlog") === "backlog",
      );
      if (pending.length < 2) return;
      for (const t of pending) delete _smgmtConflictsByIssue[t.number];
      for (const c of card.conflicts.conflicts || []) {
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
    }),
  );
}

export async function _smgmtLoadDepOrder(orderedLabels, bySprint) {
  const repo = _smgmtRepo();
  if (!repo) return;

  await Promise.all(
    orderedLabels.map(async (label) => {
      if (_smgmtRunningLabels.has(label)) return;
      if (_smgmtFinishedLabels.has(label)) return;
      const card = _smgmtAggregateCards && _smgmtAggregateCards[label];
      if (!card || !card.dep_order) return;
      const tickets = bySprint[label] || [];
      const pending = tickets.filter(
        (t) => (t.status || "backlog") === "backlog",
      );
      if (pending.length < 2) return;
      const depData = card.dep_order;
      for (const t of pending) delete _smgmtDepOrderByIssue[t.number];
      if (depData.has_cycle) {
        const cycleSet = new Set((depData.in_cycle_tickets || []).map(String));
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
        for (const [idStr, hint] of Object.entries(depData.dep_hints || {})) {
          const num = parseInt(idStr, 10);
          _smgmtDepOrderByIssue[num] = {
            upstream: hint.upstream || [],
            downstream: hint.downstream || [],
            inCycle: false,
          };
        }
      }
      for (const t of pending) _smgmtUpdateDepOrderBadge(t.number);
    }),
  );
}

export async function _smgmtLoadGoals(orderedLabels) {
  const repo = _smgmtRepo();
  if (!repo) return;

  for (const label of orderedLabels) {
    const goalEl = document.getElementById(`smgmt-goal-${label}`);
    if (!goalEl) continue;
    const card = _smgmtAggregateCards && _smgmtAggregateCards[label];
    if (!card) continue;
    const goal = (card.goal || "").trim();
    if (goalEl.tagName === "INPUT" || goalEl.tagName === "TEXTAREA") {
      if (goal) goalEl.value = goal;
    } else if (goal) {
      goalEl.textContent = goal;
      goalEl.title = goal;
      goalEl.style.display = "";
    }
  }
}

export function _smgmtOutcomeBandHtml(label, outcome) {
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

export function _smgmtOutcomeTicketListHtml(issues, label, repo) {
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

export async function _smgmtLoadFinishCards() {
  const repo = _smgmtRepo();
  if (!repo || !_smgmtData) return;

  // Aggregate path (issue #1744): consume inline finish_card/branch_status — no fetch
  if (_smgmtAggregateCards) {
    for (const [label, card] of Object.entries(_smgmtAggregateCards)) {
      if (!card || !card.finish_card) continue;
      const cardData = card.finish_card;
      if (cardData.state === "no_data") continue;
      const branchData = card.branch_status || { exists: false };
      _smgmtFinishCards[label] = { card: cardData, branch: branchData };
      _smgmtRenderFinishCard(label, cardData, branchData, repo);
    }
    return;
  }

  const order =
    _smgmtData.order && _smgmtData.order.length
      ? _smgmtData.order
      : (_smgmtData.sprints || []).map((n) => `sprint-${n}`);
  await Promise.allSettled(
    order.map(async (label) => {
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

export function _smgmtRenderFinishCard(label, cardData, branchData, repo) {
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

export function _smgmtFinishCardInnerHtml(cardData, branchData, repo) {
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
const _NON_DISPATCHABLE_LABELS = new Set(["UAT", "UAT-approved", "released"]);

/**
 * Build the action button HTML for a sprint card.
 * Extracted from _smgmtCardHtml (issue #2251) so status rendering and action
 * computation are separate concerns. Dispatch controls have been removed from
 * the card; this function preserves the original logic for reference and future
 * re-enablement without requiring a re-implementation.
 *
 * @param {string} label - Sprint label (e.g. "sprint-100")
 * @param {object} opts
 */
export function _smgmtCardActionBtnHtml(label, {
  isRunning, isLinger, isHasRework, isPostRun,
  rerunInto, rerunChildDisplay, canRun, tickets,
} = {}) {
  if (isRunning || isLinger) return "";
  if (isHasRework || isPostRun) return "";
  if (_smgmtSignoffState(label) === "pending") return _smgmtSignoffActionsHtml(label);
  if (_smgmtAnySprintRunning) {
    return `<button class="smgmt-run-btn smgmt-run-btn--blocked"
                  title="Another sprint is running"
                  onclick="smgmtRunBlockedToast()">
                  <i class="ti ti-player-play"></i> Run Sprint</button>`;
  }
  const runDisabled = !canRun ? "disabled" : "";
  const runTitle = !canRun
    ? 'title="No dispatchable tickets — remaining items are already SIT/UAT or in progress"'
    : "";
  return `<button class="smgmt-run-btn" ${runDisabled} ${runTitle}
                  onclick="smgmtRunSprint('${escHtml(label)}')">
                  <i class="ti ti-player-play"></i> Run Sprint</button>`;
}

function _smgmtHasDispatchableTickets(tickets) {
  return tickets.some((t) => {
    const names = (t.labels || []).map((l) => l.name);
    return !names.some((n) => _NON_DISPATCHABLE_LABELS.has(n));
  });
}

export function _smgmtCardHtml(
  label,
  n,
  tickets,
  outcome,
  isNext,
  parent,
  finished,
) {
  const isRunning = _smgmtRunningLabels.has(label);
  // Linger (the frozen "snapshot kept 1 hour" running-style card) is a Running-pane
  // concept only. On the Board a just-finished sprint renders as a normal settled
  // post-run card (rework / ready-to-merge), not a lingering running snapshot. The
  // Running pane keeps linger via _smgmtRunningCardHtml.
  const isLinger = false;
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

  const planState = (
    ((_smgmtData && _smgmtData.sprint_plan_states) || {})[label] || ""
  ).toLowerCase();
  const planBlocksPostRun = ["planned", "draft", "planning"].includes(
    planState,
  );

  const outcomeLifecycle = ((outcome && outcome.lifecycle) || "").toLowerCase();
  const outcomeState =
    outcome &&
    (outcome.state ||
      (outcome.sprint_status === "completed" ? "completed" : null));
  const hasLedgerRun = _smgmtHasLedgerRun(label);
  // Align the rework/ready flags with the BADGE source (_smgmtStateMeta), not just
  // the raw outcome.lifecycle — the two can disagree (e.g. a sprint whose state was
  // set to needs_rework while the cached outcome.lifecycle still reads
  // ready_to_merge), which made the badge say NEEDS REWORK but the status line say
  // "All tickets passed. Ready to merge."
  const _badgeState =
    outcome && typeof _smgmtStateMeta === "function"
      ? _smgmtStateMeta(outcome, (outcome.issues || []).length).state || ""
      : "";
  const isHasRework =
    hasLedgerRun &&
    (outcomeLifecycle === "needs_rework" ||
      _badgeState === "needs_rework" ||
      outcomeState === "has_rework" ||
      outcomeState === "cancelled");
  const isReadyToMerge =
    hasLedgerRun &&
    _badgeState !== "needs_rework" &&
    (outcomeLifecycle === "ready_to_merge" ||
      (outcomeLifecycle === "completed" && outcomeState === "completed"));
  const isAwaitingMerge =
    isReadyToMerge ||
    (finished && !isRunning && !isHasRework && !planBlocksPostRun);
  const showRunningChrome = isRunningView && !isAwaitingMerge;
  const isPostRun = !isRunningView && !planBlocksPostRun && hasLedgerRun;
  // Dispatch controls removed (issue #2251). Action button logic is preserved
  // in _smgmtCardActionBtnHtml() for reference; preflight warnings are still
  // accessible via the Preflight button on planning-state cards.
  const actionBtn = (!isRunning && !isLinger && !isHasRework && !isPostRun && !finished)
    ? `<button class="smgmt-preflight-warnings-btn" type="button"
              title="View preflight warnings for this sprint"
              onclick="smgmtOpenPreflightWarnings('${escHtml(label)}')">
         <i class="ti ti-alert-circle"></i> Preflight</button>`
    : '';

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

  if (
    outcome &&
    (outcome.sprint_status || outcome.state) &&
    !planBlocksPostRun
  ) {
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
          : "";
      // issue #2205: `tickets` is the live open-GitHub-ticket list — empty
      // for a needs_rework sprint whose tickets all closed without being
      // fixed (a "stale — no tickets" zombie, e.g. perf-coach sprint-121).
      // Falling back to it left the header rollup reading "0 tickets" right
      // next to a "needs rework" badge. Fall back to the historical
      // outcome.issues list, same as the completed-state branch below.
      if (tickets.length === 0 && (outcome.issues || []).length > 0) {
        rollupItems = outcome.issues.map((i) => ({ number: i.number }));
      }
    } else {
      outcomeBandHtml = _smgmtOutcomeBandHtml(label, outcome);
      // Tickets re-run into a child sprint (e.g. #572/#574 → sprint-63.1) belong
      // to the child now — drop them from the parent's finished card so they
      // aren't listed under both.
      const _movedToChild = new Set();
      try {
        Object.keys(_smgmtBySprint || {}).forEach((cl) => {
          if (cl !== label && cl.startsWith(label + ".")) {
            (_smgmtBySprint[cl] || []).forEach((t) =>
              _movedToChild.add(t.number),
            );
          }
        });
      } catch (_) {}
      const issueList = (outcome.issues || []).filter(
        (i) => !_movedToChild.has(i.number),
      );
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
        : "";
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
  <div class="sc-preview-slot" id="sc-preview-${escHtml(label)}"></div>
  <div class="pi-stale-slot" id="pi-stale-${escHtml(label)}"></div>
  <div class="pi-ev-slot" id="pi-ev-${escHtml(label)}"></div>`;

  // Run-stats / dispatch-log forensics live in History only (sprint-lifecycle.md P4).
  const logHtml = "";
  const cancelBannerHtml = "";

  const plannedBadge =
    !finished && !isPostRun && !outcomeBadgeHtml && !isRunningView
      ? '<span class="sc-draft-badge">DRAFT</span>'
      : "";
  const signoffBadge = _smgmtSignoffBadgeHtml(label);
  // Dispatch hints removed with dispatch controls (issue #2251).
  const blockedHint = "";
  const parentLineage = parent
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

  // Apply DAG Order button (issue #1420): shown for planned/draft/planning states only.
  const showDagOrderBtn =
    !isRunningView &&
    !isPostRun &&
    !finished &&
    ["planned", "draft", "planning"].includes(planState);
  const cachedDagData = showDagOrderBtn
    ? typeof _smgmtDagDataCache !== "undefined"
      ? _smgmtDagDataCache[label]
      : null
    : null;
  const dagHasLevels = cachedDagData && (cachedDagData.levels || []).length > 0;
  const dagHasCycles = cachedDagData && (cachedDagData.cycles || []).length > 0;
  const dagOrderBtnDisabled = !dagHasLevels || dagHasCycles ? "disabled" : "";
  const dagOrderBtnTitle = dagHasCycles
    ? "Apply DAG Order — disabled: circular dependencies detected"
    : !dagHasLevels
      ? "Apply DAG Order — loading DAG preview…"
      : cachedDagData && cachedDagData.partial
        ? "Apply DAG Order (partial preview — some tickets unestimated)"
        : "Apply DAG Order";
  const dagOrderBtn = showDagOrderBtn
    ? `<button class="smgmt-dag-order-btn" id="dag-order-btn-${escHtml(label)}"
               ${dagOrderBtnDisabled}
               title="${escHtml(dagOrderBtnTitle)}"
               onclick="smgmtApplyDagOrder('${escHtml(label)}')">
         <i class="ti ti-sort-ascending-2"></i> Apply DAG Order</button>`
    : "";

  // Zombie card: post-run sprint with 0 tickets — suppress Re-run / Reconcile / Merge Sprint
  const isStaleNoTickets =
    !isRunning &&
    !!(
      _smgmtData &&
      _smgmtData._staleNoTicketLabels instanceof Set &&
      _smgmtData._staleNoTicketLabels.has(label)
    );
  const staleNoticeHtml = isStaleNoTickets
    ? `<span class="smgmt-stale-no-tickets-notice" title="No open tickets remain on this sprint"><i class="ti ti-alert-circle"></i> stale — no tickets</span>`
    : "";

  // Pre-dispatch guidance note (issue #2310): visible only on draft sprints that
  // have never been run and are not yet finished. The run button was removed in
  // the shrink milestone — dispatch now happens in a Claude Code session.
  const dispatchNoteHtml = (!isRunning && !hasLedgerRun && !finished)
    ? `<div class="smgmt-dispatch-note"><i class="ti ti-terminal-2" aria-hidden="true"></i> Dispatch is a Claude Code session — run <code>/coder</code> then <code>/tester</code> per ticket, in dependency order.</div>`
    : "";

  return `
    <div class="smgmt-sprint-card sc-v5${outcomeCardClass}${runningClass}${collapsedClass}" id="smgmt-card-${escHtml(label)}">
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
          ${plannedBadge}
          ${signoffBadge}
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
          ${dagOrderBtn}
          ${isStaleNoTickets ? staleNoticeHtml : `${actionBtn}
          ${blockedHint}
          ${isRunning ? runningElapsed : ""}
          ${isRunning ? "" : `<button class="smgmt-reconcile-btn sc-merge-link" type="button"
                  title="Reconcile this sprint's DB state against GitHub truth"
                  onclick="event.stopPropagation();smgmtReconcileSprint('${escHtml(label)}')">
            <i class="ti ti-refresh"></i> Reconcile</button>`}
          ${(!isRunning && tickets.some(t => t.status === "uat")) ? `<button class="smgmt-uat-signoff-btn sc-merge-link" type="button"
                  title="Close all UAT tickets for this sprint"
                  onclick="event.stopPropagation();smgmtUatSignoffSprint('${escHtml(label)}')">
            <i class="ti ti-circle-check"></i> Sign off UAT</button>` : ""}
          <button class="smgmt-finish-btn sc-merge-link ${finishHidden}" ${finishDisabled}
                  title="${finishDisabled ? "No open tickets" : "Merge sprint"}"
                  onclick="smgmtFinishSprint('${escHtml(label)}')">
            <i class="ti ti-flag-check"></i> Merge Sprint</button>`}
        </div>
      </div>
      ${(function () {
        const _ss = _smgmtCardStatusSentence(label, {
          isRunning,
          isLinger,
          isHasRework,
          isReadyToMerge,
          isAwaitingMerge,
          planState,
          outcome,
          tickets,
          parent,
          isPostRun,
          isRunningView,
        });
        if (!_ss) return "";
        return `<div class="sc-status-line"><i class="ti ti-clock sc-status-icon" aria-hidden="true"></i><span>${escHtml(_ss)}</span></div>`;
      })()}
      ${dispatchNoteHtml}
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
export function _smgmtRunningTicketRowsHtml(label, tickets) {
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
    return "";
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
export function _smgmtRunningLevelText(live) {
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
export function _smgmtRunningBoardBannerHtml(label, tickets) {
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
export function _smgmtBoardBannerPatch(label, live) {
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

export function _smgmtRunningCardHtml(label, n, tickets) {
  // #C: a running sprint card defaults to COLLAPSED right after start — body
  // (ticket list/levels) hidden, only the header + Cancel button — and the whole
  // card navigates to the Running pane on click. An explicit "0" pref (operator
  // expanded it in place) still wins.
  let isCollapsed = true;
  try {
    const _pref = localStorage.getItem("sprintColumn_" + label + "_collapsed");
    if (_pref === "0") isCollapsed = false;
    else if (_pref === "1") isCollapsed = true;
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
    <div class="smgmt-sprint-card smgmt-running smgmt-running-clickable${runCollapsedClass}" id="smgmt-card-${escHtml(label)}"
         role="button" tabindex="0"
         title="Open the Running pane"
         onclick="if(!event.target.closest('button,a,input')){_smgmtShowSubView('running');}"
         onkeydown="if((event.key==='Enter'||event.key===' ')&&!event.target.closest('button,a,input')){event.preventDefault();_smgmtShowSubView('running');}">
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
          <span class="smgmt-sprint-name">${escHtml(sprintLabelDisplay(label))}</span>
          <span class="smgmt-running-badge" id="smgmt-running-badge-${escHtml(label)}">
            <span class="smgmt-running-badge-dot"></span>${totalCount > 0 ? `${completeCount}/${totalCount}` : "—"}
          </span>
        </div>
        <div class="smgmt-sprint-header-right">
          <span class="smgmt-sprint-meta" id="smgmt-elapsed-${escHtml(label)}">${timeSpentSec > 0 ? `elapsed ${_fmtRunningTime(timeSpentSec)}` : ""}</span>
          <!-- Cancel sprint button removed (issue #2251) -->
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
        ${ticketRowsHtml || ""}
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

export function _smgmtRollupText(items) {
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
export function _smgmtTicketSize(t) {
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

export function _smgmtTicketHasEstimate(t) {
  return _smgmtTicketSize(t) !== null;
}

/**
 * Plain-language status sentence for a sprint card — shown directly under the
 * sprint header to give the operator one unambiguous signal at a glance.
 */
export function _smgmtCardStatusSentence(label, opts) {
  const {
    isRunning,
    isLinger,
    isHasRework,
    isReadyToMerge,
    isAwaitingMerge,
    planState,
    outcome,
    tickets,
    parent,
    isPostRun,
    isRunningView,
  } = opts;
  if (isRunning) return "";
  // Rework is checked BEFORE linger so a needs_rework sprint reads as rework even
  // inside its 1-hour linger window (otherwise the linger note shadowed it, and a
  // lingering rework sprint looked identical to a settled one).
  if (isHasRework) {
    const c = (outcome && outcome.counts) || {};
    const done = c.done || 0;
    const failed = c.failed || 0;
    const total =
      outcome && Array.isArray(outcome.issues) ? outcome.issues.length : 0;
    if (total > 0 && failed > 0) {
      return `${done} of ${total} passed, ${failed} need${failed === 1 ? "s" : ""} rework — re-run or merge what passed.`;
    }
    return "Some tickets need rework — re-run or merge what passed.";
  }
  if (isLinger) return "Sprint finished — snapshot kept 1 hour.";
  if (isReadyToMerge || isAwaitingMerge) {
    return "All tickets passed. Ready to merge.";
  }
  if (!isPostRun && !isRunningView) {
    const n = tickets.length;
    const parentShort = parent
      ? sprintLabelDisplay(parent).replace("Sprint ", "")
      : "";
    const held =
      n > 0 && parentShort
        ? ` Holds the ${n} ticket${n !== 1 ? "s" : ""} carried from ${parentShort}.`
        : n > 0
          ? ` Holds ${n} ticket${n !== 1 ? "s" : ""}.`
          : "";
    if (_smgmtAnySprintRunning) {
      const blocker =
        typeof _smgmtRunningBlockerShort === "function"
          ? _smgmtRunningBlockerShort()
          : "another sprint";
      return `Ready to run.${held} Waiting on ${blocker} to finish.`;
    }
    if (!planState || planState === "draft" || planState === "planning") {
      return tickets.length === 0
        ? "No tickets yet — drag some from the backlog."
        : _smgmtGoalRequired()
          ? "Set a sprint goal to enable the run."
          : "Ready to run.";
    }
    return held ? `Ready to run.${held}` : "Ready to run.";
  }
  if (_smgmtAnySprintRunning) {
    return "Blocked: another sprint is running.";
  }
  return tickets.length === 0
    ? "No tickets — add some from the backlog."
    : "Ready to run.";
}

/** Short label for the sprint blocking Run, e.g. "S56". */
export function _smgmtRunningBlockerShort() {
  if (!_smgmtRunningLabels || _smgmtRunningLabels.size === 0) return "";
  const lbl = [..._smgmtRunningLabels][0];
  const m = String(lbl).match(/sprint-(\d+(?:\.\d+)?)/);
  return m ? `S${m[1]}` : sprintLabelDisplay(lbl);
}

/** Right-aligned estimate minutes; spinner only while an explicit action runs. */
export function _smgmtTicketEstHtml(ticket) {
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

export function _smgmtUpdateColRollup(label, items) {
  const el = document.getElementById(`smgmt-col-rollup-${label}`);
  if (el) el.textContent = _smgmtRollupText(items);
}

export function _smgmtTicketRowHtml(ticket, label, elapsedSecs = null) {
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

  // Agent chip derived from ticket status for consistent card anatomy (issue #1055)
  const _planningAgent =
    ticket.status === "in-progress"
      ? "coder"
      : ticket.status === "sit"
        ? "tester"
        : null;
  const planningAgentHtml = _planningAgent
    ? `<span class="smgmt-ticket-agent-tag ${_smgmtAgentTagClass(_planningAgent)}">${escHtml(_planningAgent.toUpperCase())}</span>`
    : "";

  const ticketLabelNames = (ticket.labels || []).map((l) => l.name).join(",");
  const sk = escHtml(label);

  return `
    <div class="smgmt-ticket" id="smgmt-ticket-${ticket.number}"
         tabindex="-1"
         data-issue="${ticket.number}"
         data-sprint="${sk}"${sizeAttr}
         data-labels="${escHtml(ticketLabelNames)}"
         oncontextmenu="_smgmtCtxMenuOpen(event,${ticket.number})">
      ${outcomeIconHtml}
      <a class="smgmt-ticket-num" href="${escHtml(ticket.url || "#")}" target="_blank"
         rel="noopener" draggable="false" onclick="event.stopPropagation()">#${ticket.number}</a>
      <span class="smgmt-ticket-title" title="${escHtml(ticket.title)}">${escHtml(ticket.title)}</span>
      ${sizePillHtml}${staleBadgeHtml}${estimateBadgeHtml}${riskFlagIconsHtml}${schedDepHtml}${reEstBtnHtml}
      ${hasRework ? '<span class="smgmt-lbl-rejected">TESTER REJECTED</span>' : ""}
      ${elapsedSecs != null ? `<span class="smgmt-ticket-elapsed" title="Actual time spent">${Math.round(elapsedSecs / 60)}m</span>` : ""}
      ${_smgmtTicketEstHtml(ticket)}
      ${planningAgentHtml}
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

export function _smgmtRenderBacklog(tickets) {
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

  const eyebrowEl = document.getElementById("bl-eyebrow");
  if (eyebrowEl) {
    const n = _blBacklogAll.length;
    eyebrowEl.textContent =
      n > 0
        ? `Backlog ${n} · source for planning`
        : "Backlog · source for planning";
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

  // AC2 (issue #1748): skip DOM replacement while the user has an active selection
  // so mid-click node replacement cannot swallow clicks or orphan checked checkboxes.
  const hasActiveSelection =
    typeof _smgmtSelectedIssues !== "undefined" && _smgmtSelectedIssues.size > 0;

  if (!hasActiveSelection) {
    if (sorted.length === 0) {
      const msg =
        _blBacklogAll.length === 0
          ? "No backlog tickets — all caught up"
          : "No tickets match the active filters";
      ticketsEl.innerHTML = `<div style="padding:var(--space-3) var(--space-4);text-align:center;color:var(--text-sub);font-size:12px;">${msg}</div>`;
    } else {
      ticketsEl.innerHTML = sorted
        .map((t) => _smgmtBacklogTicketHtml(t, allSprintNums))
        .join("");
    }
  }

  _blSyncFilterPills();
  // AC1 (issue #1748): sync selection bar count + visibility after every render,
  // including suppressed renders so the bar reflects the live JS Set.
  if (typeof _smgmtUpdateSelectionUI === "function") {
    _smgmtUpdateSelectionUI();
  } else {
    _blUpdateActions();
  }
}

const _BACKLOG_CHIP_LIMIT = 3;
const _BACKLOG_CHIP_KEEP_RE = /^(uat|sit|in-progress|needs-rework|blocked|sprint-.+)$/i;

/**
 * Render compact label chips for a backlog row (issue #2060).
 * Only meaningful stage/sprint labels are shown; uncapped lists get a "+N" overflow chip.
 */
export function _smgmtBacklogLabelChipsHtml(labels) {
  if (!labels || !labels.length) return "";
  const kept = labels
    .map((l) => (typeof l === "string" ? l : l.name || ""))
    .filter((n) => _BACKLOG_CHIP_KEEP_RE.test(n));
  if (!kept.length) return "";

  const visible = kept.slice(0, _BACKLOG_CHIP_LIMIT);
  const overflow = kept.length - visible.length;

  const chips = visible.map((name) => {
    const lc = name.toLowerCase();
    let mod = "";
    if (lc === "uat") mod = "smgmt-bl-label-chip--uat";
    else if (lc === "sit" || lc === "in-progress") mod = "smgmt-bl-label-chip--active";
    else if (lc === "needs-rework" || lc === "blocked") mod = "smgmt-bl-label-chip--alert";
    const cls = mod ? `smgmt-bl-label-chip ${mod}` : "smgmt-bl-label-chip";
    return `<span class="${cls}">${escHtml(name)}</span>`;
  });

  if (overflow > 0) {
    chips.push(`<span class="smgmt-bl-label-chip smgmt-bl-label-chip--overflow">+${overflow}</span>`);
  }

  return `<span class="smgmt-bl-label-chips">${chips.join("")}</span>`;
}

export function _smgmtBacklogTicketHtml(ticket, _sprintNums) {
  const hasEstimate = _smgmtTicketHasEstimate(ticket);
  const backlogLabelNames = (ticket.labels || []).map((l) => l.name).join(",");
  const schedDepHtml = _smgmtSchedDepHtml(ticket);
  const sizeValue = _smgmtTicketSize(ticket) || "";
  const sizeAttr = sizeValue ? ` data-size="${escHtml(sizeValue)}"` : "";
  const sizePillHtml = sizeValue
    ? `<span class="smgmt-ticket-size-pill">${escHtml(sizeValue)}</span>`
    : "";
  const estHtml = _smgmtTicketEstHtml(ticket);
  const labelChipsHtml = _smgmtBacklogLabelChipsHtml(ticket.labels);

  // Determine if there's a current draft sprint to offer "Add to sprint" affordance
  const draftLabel = _smgmtOrderedLabels
    ? _smgmtOrderedLabels.find((l) => {
        if (_smgmtResolvedAncestors.has(l) || _smgmtRunningLabels.has(l))
          return false;
        const ps = (
          (_smgmtData?.sprint_plan_states || {})[l] || ""
        ).toLowerCase();
        return ["draft", "planned", "planning"].includes(ps);
      })
    : null;

  const addToSprintBtn = draftLabel
    ? `<button class="smgmt-add-to-sprint-btn" tabindex="0"
         title="Add to ${escHtml(sprintLabelDisplay(draftLabel))}"
         onclick="event.stopPropagation();smgmtAddToDraft(${ticket.number},'${escHtml(draftLabel)}')">
         <i class="ti ti-circle-plus"></i> Add to ${escHtml(sprintLabelDisplay(draftLabel).replace("Sprint ", "S"))}</button>`
    : "";

  const isSelected =
    typeof _smgmtSelectedIssues !== "undefined" &&
    _smgmtSelectedIssues.has(ticket.number);

  return `
    <div class="smgmt-ticket bl-row bl-row--selectable${isSelected ? " is-selected" : ""}" id="smgmt-ticket-${ticket.number}"
         data-issue="${ticket.number}"
         data-sprint=""${sizeAttr}
         data-labels="${escHtml(backlogLabelNames)}"
         onclick="_smgmtRowClickSelect(event,${ticket.number})"
         oncontextmenu="_smgmtCtxMenuOpen(event,${ticket.number})">
      <input type="checkbox" class="smgmt-ticket-cb" ${isSelected ? "checked" : ""}
             title="Select for bulk add to a sprint"
             onclick="event.stopPropagation()"
             onchange="_smgmtToggleSelect(${ticket.number}, this.checked)">
      <a class="smgmt-ticket-num" href="${escHtml(ticket.url || "#")}" target="_blank"
         rel="noopener" onclick="event.stopPropagation()">#${ticket.number}</a>
      <span class="smgmt-ticket-title" title="${escHtml(ticket.title)}">${escHtml(ticket.title)}</span>
      ${schedDepHtml}${sizePillHtml}${estHtml}${labelChipsHtml}
      ${addToSprintBtn}
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
export function _smgmtAncestorMergeState(label, outcome) {
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
  if (state === "ready_to_merge" || state === "partial_finished")
    return "needs_merge";
  if (state === "needs_rework") return "needs_merge";
  if (state === "completed") return "merged";
  // Finished (has summary issue) with passing tickets → merged
  if (_smgmtFinishedLabels && _smgmtFinishedLabels.has(label) && done > 0)
    return "merged";
  return "needs_merge";
}

/** One-line stats for an ancestor sprint body: done / failed / UAT / elapsed. */
export function _smgmtAncestorStatsLine(outcome) {
  if (!outcome) return "";
  const c = outcome.counts || {};
  const parts = [];
  if (c.done) parts.push(`${c.done} done`);
  if (c.failed) parts.push(`${c.failed} failed`);
  if (c.uat) parts.push(`${c.uat} awaiting UAT`);
  if (c.skipped) parts.push(`${c.skipped} incomplete`);
  if (outcome.wall_clock_secs) {
    parts.push(`${_fmtRunningTime(outcome.wall_clock_secs)} elapsed`);
  }
  return parts.join(" · ");
}

/** Build carry-down summary: "3 merged · 1 reworked → 73.1". */
export function _smgmtAncestorCarrySummary(outcome, childLabel, mergeState) {
  if (!outcome) return "";
  const counts = outcome.counts || {};
  const done = counts.done || 0;
  const carried = (counts.failed || 0) + (counts.skipped || 0);
  const uat = counts.uat || 0;
  const childDisplay = childLabel
    ? sprintLabelDisplay(childLabel).replace("Sprint ", "")
    : "";

  if (mergeState === "failed") {
    if (carried > 0 && childDisplay) {
      return `${done} merged · ${carried} carried → ${childDisplay}`;
    }
    if (carried > 0) return `${done} merged · ${carried} carried`;
    return `${done} merged`;
  }

  if (mergeState === "needs_merge") {
    let summary = `${done} passed`;
    if (uat > 0) summary += ` · ${uat} awaiting UAT`;
    if (carried > 0 && childDisplay)
      summary += ` · ${carried} reworked → ${childDisplay}`;
    else if (carried > 0) summary += ` · ${carried} reworked`;
    return `${summary} · not merged yet`;
  }

  let summary = `${done} merged`;
  if (uat > 0) summary += ` · ${uat} awaiting UAT`;
  if (carried > 0 && childDisplay)
    summary += ` · ${carried} reworked → ${childDisplay}`;
  else if (carried > 0) summary += ` · ${carried} reworked`;
  return summary;
}

/** Per-ticket rows for an expanded ancestor sprint (merged / carried fate marks). */
export function _smgmtAncestorTicketsHtml(label, outcome, childLabel) {
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
      const issueUrl = repo
        ? `https://github.com/${repo}/issues/${iss.number}`
        : "#";
      const elapsed =
        iss.elapsed_secs != null && iss.elapsed_secs > 0
          ? `<span class="slp-ticket-elapsed">${escHtml(_fmtRunningTime(iss.elapsed_secs))}</span>`
          : "";
      if (o === "done") {
        return `<div class="slp-ancestor-ticket-row">
          <span class="slp-ticket-merged" title="Done"><i class="ti ti-circle-check"></i></span>
          <a class="smgmt-ticket-num" href="${escHtml(issueUrl)}" target="_blank" rel="noopener"
             onclick="event.stopPropagation()">#${iss.number}</a>
          <span class="smgmt-ticket-title slp-ticket-title" title="${escHtml(iss.title)}">${escHtml(iss.title)}</span>
          <span class="slp-fate-merged">done</span>${elapsed}
        </div>`;
      }
      if (o === "uat") {
        return `<div class="slp-ancestor-ticket-row">
          <span class="slp-ticket-uat" title="Awaiting UAT sign-off"><i class="ti ti-hourglass"></i></span>
          <a class="smgmt-ticket-num" href="${escHtml(issueUrl)}" target="_blank" rel="noopener"
             onclick="event.stopPropagation()">#${iss.number}</a>
          <span class="smgmt-ticket-title slp-ticket-title" title="${escHtml(iss.title)}">${escHtml(iss.title)}</span>
          <span class="slp-fate-uat">awaiting UAT</span>${elapsed}
        </div>`;
      }
      if (o === "failed") {
        return `<div class="slp-ancestor-ticket-row">
          <span class="slp-ticket-failed" title="Failed / rework"><i class="ti ti-circle-x"></i></span>
          <a class="smgmt-ticket-num" href="${escHtml(issueUrl)}" target="_blank" rel="noopener"
             onclick="event.stopPropagation()">#${iss.number}</a>
          <span class="smgmt-ticket-title slp-ticket-title" title="${escHtml(iss.title)}">${escHtml(iss.title)}</span>
          <span class="slp-fate-failed">failed</span>${elapsed}
        </div>`;
      }
      if (childDisplay) {
        return `<div class="slp-ancestor-ticket-row">
          <span class="slp-ticket-carried" title="Carried to ${escHtml(childDisplay)}"><i class="ti ti-arrow-right"></i></span>
          <a class="smgmt-ticket-num" href="${escHtml(issueUrl)}" target="_blank" rel="noopener"
             onclick="event.stopPropagation()">#${iss.number}</a>
          <span class="smgmt-ticket-title slp-ticket-title" title="${escHtml(iss.title)}">${escHtml(iss.title)}</span>
          <span class="slp-fate-carried">carried → ${escHtml(childDisplay)}</span>${elapsed}
        </div>`;
      }
      return `<div class="slp-ancestor-ticket-row">
          <span class="slp-ticket-incomplete" title="Incomplete"><i class="ti ti-dots"></i></span>
          <a class="smgmt-ticket-num" href="${escHtml(issueUrl)}" target="_blank" rel="noopener"
             onclick="event.stopPropagation()">#${iss.number}</a>
          <span class="smgmt-ticket-title slp-ticket-title" title="${escHtml(iss.title)}">${escHtml(iss.title)}</span>
          <span class="slp-fate-incomplete">incomplete</span>${elapsed}
        </div>`;
    })
    .join("");
}

/** Compact collapsed row for a resolved ancestor sprint (issue #1043). */
export function _smgmtAncestorRowHtml(label, outcome, childLabel) {
  const mergeState = _smgmtAncestorMergeState(label, outcome || null);
  const safeLabel = escHtml(label);
  const rerunInto = childLabel || (_smgmtData?.sprint_rerun_into || {})[label];

  let statusIcon, statusText, statusCls;
  if (mergeState === "merged") {
    statusIcon = "ti-circle-check";
    statusText = "Merged";
    statusCls = "slp-merged";
  } else if (mergeState === "needs_merge") {
    statusIcon = "ti-alert-triangle";
    statusText = "Needs merge";
    statusCls = "slp-needs-merge";
  } else if (mergeState === "failed") {
    statusIcon = "ti-circle-x";
    statusText = "Failed";
    statusCls = "slp-failed";
  } else {
    statusIcon = "ti-clock";
    statusText = "Pending";
    statusCls = "slp-pending";
  }

  const carrySummary = _smgmtAncestorCarrySummary(
    outcome || null,
    rerunInto,
    mergeState,
  );
  const durationHtml =
    outcome && outcome.wall_clock_secs != null && outcome.wall_clock_secs > 0
      ? `<span class="slp-ancestor-duration">${escHtml(_fmtRunningTime(outcome.wall_clock_secs))}</span>`
      : "";
  let ticketsHtml;
  if (outcome === undefined) {
    ticketsHtml = '<div class="slp-no-tickets">Loading outcome data…</div>';
  } else if (!outcome) {
    ticketsHtml =
      '<div class="slp-no-tickets">No run data for this sprint — tickets may have moved to a child sprint.</div>';
  } else {
    const statsLine = _smgmtAncestorStatsLine(outcome);
    const statsHtml = statsLine
      ? `<div class="slp-ancestor-stats">${escHtml(statsLine)}</div>`
      : "";
    const listHtml =
      (outcome.issues || []).length > 0
        ? _smgmtAncestorTicketsHtml(label, outcome, rerunInto)
        : '<div class="slp-no-tickets">No per-ticket records found.</div>';
    ticketsHtml = statsHtml + listHtml;
  }

  const actionsHtml =
    mergeState === "needs_merge"
      ? `<div class="slp-ancestor-actions">
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
      <span class="slp-merge-mark ${statusCls}">
        <i class="ti ${statusIcon}"></i>
        <span class="slp-mark-text">${escHtml(statusText)}</span>
      </span>
      <span class="slp-ancestor-name">${escHtml(sprintLabelDisplay(label))}</span>
      ${carrySummary ? `<span class="slp-carry-summary">${escHtml(carrySummary)}</span>` : ""}
      ${durationHtml}
      <button class="slp-ancestor-menu" type="button"
              title="Sprint actions"
              aria-label="Sprint actions"
              onclick="event.stopPropagation();smgmtToggleAncestor('${safeLabel}')">
        <i class="ti ti-menu-2"></i>
      </button>
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
export function smgmtToggleAncestor(label) {
  const body = document.getElementById(`slp-body-${label}`);
  const toggleIcon = document.querySelector(
    `#smgmt-card-${CSS.escape(label)} .slp-ancestor-toggle i`,
  );
  if (!body) return;
  const isExpanded = !body.hidden;
  body.hidden = isExpanded;
  if (toggleIcon) {
    toggleIcon.className = isExpanded
      ? "ti ti-chevron-right"
      : "ti ti-chevron-down";
  }
  try {
    localStorage.setItem(`slp_ancestor_${label}`, isExpanded ? "0" : "1");
  } catch (_) {}
}

// ── Planning board layout (issue #1044) ─────────────────────────────────────

/**
 * Synthesize a short ordered focus guide from current board state.
 * Returns an HTML string for the #smgmt-focus-guide container.
 */
export function _smgmtFocusGuideHtml(data, orderedLabels, bySprint) {
  const steps = [];
  const planStates = data.sprint_plan_states || {};
  const rerunInto = data.sprint_rerun_into || {};
  const finishedSet = new Set(data.finished_sprints || []);
  const lineageLabels = (orderedLabels || []).filter((l) =>
    _smgmtResolvedAncestors.has(l),
  );

  for (const label of lineageLabels) {
    const outcome = _smgmtOutcomeCache[label] || null;
    const mergeState = _smgmtAncestorMergeState(label, outcome);
    const display = sprintLabelDisplay(label);
    if (mergeState === "needs_merge") {
      const c = (outcome && outcome.counts) || {};
      const done = c.done || 0;
      const carried = (c.failed || 0) + (c.skipped || 0);
      steps.push({
        text: `${escHtml(display)} needs a merge decision — ${done} merged, ${carried} reworked`,
        priority: "high",
      });
    }
  }

  const draftLabel = (orderedLabels || []).find((l) => {
    if (_smgmtResolvedAncestors.has(l) || _smgmtRunningLabels.has(l))
      return false;
    const ps = (planStates[l] || "").toLowerCase();
    return ["draft", "planned", "planning"].includes(ps);
  });

  const upNextCandidates = (orderedLabels || []).filter((l) => {
    if (_smgmtResolvedAncestors.has(l)) return false;
    if (_smgmtRunningLabels.has(l)) return false;
    if (l === draftLabel) return false;
    if (finishedSet.has(l)) return false;
    return (bySprint[l] || []).length > 0;
  });

  if (upNextCandidates.length > 0) {
    const nextDisplay = sprintLabelDisplay(upNextCandidates[0]);
    const blocker =
      _smgmtAnySprintRunning && typeof _smgmtRunningBlockerShort === "function"
        ? _smgmtRunningBlockerShort()
        : "";
    steps.push({
      text: blocker
        ? `${escHtml(nextDisplay)} ready to run — blocked: ${escHtml(blocker)} running`
        : `${escHtml(nextDisplay)} ready to run`,
      priority: "med",
    });
  }

  if (draftLabel) {
    const draftDisplay = sprintLabelDisplay(draftLabel);
    const draftTickets = bySprint[draftLabel] || [];
    let usedMin = 0;
    for (const t of draftTickets) {
      usedMin += _sizeMinutes(_smgmtTicketSize(t)) || 0;
    }
    const headroomH = Math.max(0, Math.round((180 - usedMin) / 60));
    const goalNote = "needs a goal";
    steps.push({
      text: `Finish planning ${escHtml(draftDisplay)} — ${goalNote} · ${headroomH}h headroom left`,
      priority: "low",
    });
  } else {
    steps.push({
      text: "No draft sprint yet — create one to start planning.",
      priority: "low",
    });
  }

  const resolved = [];
  for (const label of lineageLabels) {
    const outcome = _smgmtOutcomeCache[label] || null;
    const mergeState = _smgmtAncestorMergeState(label, outcome);
    if (mergeState !== "merged" && mergeState !== "failed") continue;
    const display = sprintLabelDisplay(label).replace("Sprint ", "");
    const child = rerunInto[label];
    const childShort = child
      ? sprintLabelDisplay(child).replace("Sprint ", "")
      : "";
    let text = `${escHtml(display)} merged`;
    if (mergeState === "failed" && childShort) {
      const carried = ((outcome && outcome.counts) || {}).failed || 0;
      text = `${escHtml(display)} merged · ${escHtml(childShort)} failed (${carried} carried into ${escHtml(childShort)})`;
    }
    resolved.push({ text: `${text} — resolved`, resolved: true });
  }

  if (steps.length === 0) {
    steps.push({ text: "Board is up to date.", priority: "low" });
  }

  const allSteps = [
    ...steps.map((s, i) => ({ ...s, num: i + 1 })),
    ...resolved.map((s) => ({ ...s, num: null })),
  ];

  const stepHtml = allSteps
    .map((s) => {
      if (s.resolved) {
        return (
          `<div class="smgmt-focus-step smgmt-focus-step--resolved">` +
          `<span class="smgmt-focus-check" aria-hidden="true"><i class="ti ti-check"></i></span>` +
          `<span class="smgmt-focus-text">${s.text}</span>` +
          `</div>`
        );
      }
      return (
        `<div class="smgmt-focus-step">` +
        `<span class="smgmt-focus-num smgmt-focus-num--${s.priority}">${s.num}</span>` +
        `<span class="smgmt-focus-text">${s.text}</span>` +
        `</div>`
      );
    })
    .join("");

  return (
    `<div class="smgmt-focus-guide-title">What to do, in order</div>` + stepHtml
  );
}

/**
 * Sum estimate minutes and size breakdown for sprint budget display.
 */
function _smgmtTicketsEstBreakdown(tickets) {
  const sizeCounts = { S: 0, M: 0, L: 0, XL: 0 };
  let totalMin = 0;
  for (const t of tickets || []) {
    const sz = _smgmtTicketSize(t);
    if (sz && sizeCounts[sz] !== undefined) sizeCounts[sz]++;
    totalMin += _sizeMinutes(sz) || 0;
  }
  return { totalMin, sizeCounts };
}

function _smgmtFmtBudgetHours(minutes) {
  if (minutes >= 60) {
    const h = minutes / 60;
    return Number.isInteger(h) ? `${h}h` : `${Math.round(h * 10) / 10}h`;
  }
  return `${minutes}m`;
}

/**
 * Render a proportional budget bar for a draft sprint.
 * tickets: array of ticket objects with size labels.
 * Returns an HTML string.
 */
export function _smgmtBudgetBarHtml(tickets, capHours = 3) {
  const { totalMin, sizeCounts } = _smgmtTicketsEstBreakdown(tickets);
  const capMin = capHours * 60;
  const pct =
    capMin > 0 ? Math.min(100, Math.round((totalMin / capMin) * 100)) : 0;
  const headroomMin = capMin - totalMin;
  const overBudget = totalMin > capMin;
  const fillClass = overBudget
    ? "smgmt-budget-fill smgmt-budget-fill--over"
    : totalMin >= capMin * 0.85
      ? "smgmt-budget-fill smgmt-budget-fill--warn"
      : "smgmt-budget-fill";

  const breakdown = Object.entries(sizeCounts)
    .filter(([, n]) => n > 0)
    .map(([s, n]) => `${s}x${n}`)
    .join(" · ");

  let headroomText;
  if (overBudget) {
    headroomText = `${_smgmtFmtBudgetHours(totalMin - capMin)} over`;
  } else if (headroomMin >= 60) {
    const xlRoom = Math.floor(headroomMin / 60);
    headroomText = `${_smgmtFmtBudgetHours(headroomMin)} headroom · room for ~${xlRoom} more XL`;
  } else {
    headroomText = `${headroomMin}m headroom`;
  }

  const subLine = breakdown ? `${breakdown} — ${headroomText}` : headroomText;

  return (
    `<div class="smgmt-budget-bar">` +
    `<div class="smgmt-budget-bar-top">` +
    `<span class="smgmt-budget-label">Sprint budget</span>` +
    `<span class="smgmt-budget-used">${_smgmtFmtBudgetHours(totalMin)} of ${capHours}h</span>` +
    `</div>` +
    `<div class="smgmt-budget-track"><div class="${fillClass}" style="width:${pct}%"></div></div>` +
    `<div class="smgmt-budget-sub${overBudget ? " smgmt-budget-sub--over" : headroomMin < 30 ? " smgmt-budget-sub--warn" : ""}">` +
    escHtml(subLine) +
    `</div>` +
    `</div>`
  );
}

/**
 * Add a backlog ticket to the current draft sprint.
 * issueNum: number, draftLabel: sprint label string
 */
export function smgmtAddToDraft(issueNum, draftLabel) {
  // Reuse the existing move-to mechanism via _smgmtRowMenuOpen action pipeline
  // or delegate directly to the sprint assign endpoint.
  if (!draftLabel) return;
  const fakeEvt = {
    currentTarget:
      document.getElementById(`smgmt-ticket-${issueNum}`) || document.body,
    stopPropagation() {},
    preventDefault() {},
  };
  if (typeof _smgmtRowMenuOpen === "function") {
    _smgmtRowMenuOpen(fakeEvt, issueNum, null, false);
  }
}

/** Update an ancestor row in-place when outcome data arrives asynchronously. */
export function _smgmtUpdateAncestorRow(label, outcome) {
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

// ── SSE board_invalidated handler (issue #1785) ───────────────────────────────

let _boardSseTimer = null;
let _boardSsePending = false;

function _boardSseFireRefetch() {
  _boardSseTimer = null;
  loadSprintMgmt(true);
}

/**
 * Handle a `board_invalidated` SSE event (issue #1785 / #1789 cutover).
 *
 * When the tab is visible, schedules a debounced refetch of `/api/board` (≥ 2 s).
 * Rapid events collapse to one request; events while the tab is hidden are
 * deferred until the tab shows. Exported for unit tests.
 */
export function _boardSseOnInvalidated(_project) {
  if (typeof document !== "undefined" && document.hidden) {
    _boardSsePending = true;
    return;
  }
  if (_boardSseTimer !== null) clearTimeout(_boardSseTimer);
  _boardSseTimer = setTimeout(_boardSseFireRefetch, 2000);
}

/**
 * Called when the tab becomes visible — fires a deferred board refetch if a
 * `board_invalidated` arrived while the tab was hidden (issue #1785).
 * Exported for unit tests.
 */
export function _boardSseOnVisible() {
  if (!_boardSsePending) return;
  _boardSsePending = false;
  if (_boardSseTimer !== null) clearTimeout(_boardSseTimer);
  _boardSseTimer = setTimeout(_boardSseFireRefetch, 2000);
}
