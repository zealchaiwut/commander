/* Sprint-board render pipeline (issue #797) — extracted from project.html.
 *
 * Owns the board's render core: loadSprintMgmt() (fetch board state) ->
 * _smgmtRender() (group issues by sprint, order columns, inject cards) and the
 * HTML builders for sprint cards, running cards, ticket rows, outcome bands,
 * finish-report cards, the backlog column, and column rollups. Peripheral board
 * helpers (sort menus, density, capacity gauges, popovers, keyboard nav,
 * auto-refresh) remain inline and resolve through the page's global scope; they
 * are scheduled for follow-on extraction waves.
 */

/* global _blApplyFilters, _blBacklogAll, _blSyncFilterPills, _blUpdateActions, _smgmtEnsureCapData, _smgmtLoadMiniRail, _smgmtRenderAllCapBars, _smgmtUpdateSubnav, _cachedFullRepo, _estDataCache, _slug, _smgmtActiveAgentsHtml, _smgmtAgentTagClass, _smgmtApplySort, _smgmtBacklogTicketDragStart, _smgmtBulkEstimate, _smgmtBySprint, _smgmtCancelBannerHtml, _smgmtCapacityInputHtml, _smgmtCheckEstimatorHealth, _smgmtCloseIssueOpen, _smgmtConflictsByIssue, _smgmtCtxMenuOpen, _smgmtData, _smgmtDeactivatedLabels, _smgmtDepOrderByIssue, _smgmtDragLeave, _smgmtDragOver, _smgmtDropOnSprint, _smgmtEstimateBadgeHtml, _smgmtEstimatorAvailable, _smgmtFilterApply, _smgmtFinishCards, _smgmtFinishedLabels, _smgmtHasCompletedTickets, _smgmtInitCapacityGauges, _smgmtInjectOutcomeBand, _smgmtIsCancelled, _smgmtKbRestoreFocus, _smgmtLabelColors, _smgmtLabelFilterToggle, _smgmtLabelFilterToggleExpand, _smgmtLastLabelIssues, _smgmtLevelsHtml, _smgmtLiveAgentBadgesHtml, _smgmtLiveCache, _smgmtLiveLogLinesHtml, _smgmtLivePollRestart, _smgmtLingerRestore, _smgmtLingerStart, _smgmtIsLinger, _smgmtLingerLive, _smgmtNextChildLabel, _smgmtNextUpLabel, _smgmtOutcomeCache, _smgmtOutcomeLogHtml, _smgmtPrimaryRunningLabel, _smgmtReEstimate, _smgmtRepo, _smgmtRiskFlagIconsHtml, _smgmtRowClick, _smgmtRowMenuOpen, _smgmtRunningViewUpdate, _smgmtSchedDepHtml, _smgmtSelectedIssues, _smgmtSetSprintTokenEl, _smgmtStateMeta, _smgmtTicketDragEnd, _smgmtTicketDragStart, _smgmtTicketReorderDragLeave, _smgmtTicketReorderDragOver, _smgmtTicketReorderDrop, _smgmtTicketToSprint, _smgmtToggleSelect, _smgmtUpdateCapacityGauge, _smgmtUpdateCleanupBtn, _smgmtUpdateConflictBadge, _smgmtUpdateDepOrderBadge, _smgmtUpdateEstimateBadge, _smgmtUpdateSelectionUI, escHtml, sprintLabelDisplay,
   _smgmtAnySprintRunning:writable, _smgmtRunningLabels:writable */

export async function loadSprintMgmt(silent, optimisticRunningLabel) {
  const listEl = document.getElementById('smgmt-sprint-list');
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
    if (typeof _smgmtEnsureCapData === 'function') {
      await _smgmtEnsureCapData();
    }

    // Fetch sprint management data + running sprint status + summaries in parallel
    const [resp, runningResp] = await Promise.all([
      fetch('/api/sprint-management/issues?repo=' + encodeURIComponent(repo)),
      fetch('/api/sprints/running-all').catch(() => null),
    ]);
    if (!resp.ok) {
      // Surface a GitHub rate-limit failure specifically (status 429 from
      // _gh_error) so the board says what's wrong instead of "Failed to load".
      let msg = 'Failed to load sprints.';
      const d = await resp.json().catch(() => null);
      const detail = d && typeof d.detail === 'string' ? d.detail : '';
      if (resp.status === 429 || /rate limit/i.test(detail)) {
        msg = detail || 'GitHub API rate limit reached — retry shortly.';
      }
      throw new Error(msg);
    }
    const data = await resp.json();

    if (typeof _smgmtLingerRestore === 'function') _smgmtLingerRestore();

    // Update running labels set; start linger when a label drops off running-all.
    const prevRunning = new Set(_smgmtRunningLabels);
    _smgmtRunningLabels = new Set();
    _smgmtAnySprintRunning = false;
    if (runningResp && runningResp.ok) {
      const runningData = await runningResp.json();
      const running = runningData.running || [];
      running.forEach(r => {
        if (r.project === repo) {
          _smgmtRunningLabels.add(r.sprint_label);
        }
      });
      // Only block Run Sprint if THIS project has a running sprint
      _smgmtAnySprintRunning = _smgmtRunningLabels.size > 0;
    }

    for (const label of prevRunning) {
      if (!_smgmtRunningLabels.has(label) && typeof _smgmtLingerStart === 'function') {
        _smgmtLingerStart(label);
      }
    }

    // Keep sprint in running UI until /api/sprints/running-all catches up (post-dispatch race).
    if (optimisticRunningLabel) {
      _smgmtRunningLabels.add(optimisticRunningLabel);
      _smgmtAnySprintRunning = true;
    }

    _smgmtRender(data);

    // Start (or restart) live polling if there are running sprints
    _smgmtLivePollRestart();

    const lingerLbl = typeof _smgmtPrimaryRunningLabel === 'function'
      ? _smgmtPrimaryRunningLabel() : null;
    if (lingerLbl && typeof _smgmtRunningViewUpdate === 'function') {
      const live = typeof _smgmtLingerLive === 'function'
        ? _smgmtLingerLive(lingerLbl) : (_smgmtLiveCache[lingerLbl] || null);
      _smgmtRunningViewUpdate(lingerLbl, live);
    }
  } catch (err) {
    if (!silent) {
      const msg = (err && err.message) ? err.message : 'Failed to load sprints.';
      listEl.innerHTML = `<div class="loading-msg">${escHtml(msg)}</div>`;
    }
  }
}

export function _smgmtSprintLabelSortKey(label) {
  const m = String(label).match(/^sprint-(\d+(?:\.\d+)*)$/);
  if (!m) return [Infinity];
  return m[1].split('.').map(n => parseInt(n, 10));
}

export function _smgmtRender(data) {
  const listEl = document.getElementById('smgmt-sprint-list');
  if (!listEl) return;
  _smgmtData = data;

  // Keep the sub-nav live indicators (running dot + History count) in sync on
  // every render, including auto-refresh, so the dot clears when sprints stop
  // and the badge tracks the sprint total (issue #798).
  _smgmtUpdateSubnav();

  const sprints = data.sprints || [];
  const order   = data.order   || [];
  const issues  = data.issues  || [];

  // Group issues by sprint_label (handles both sprint-N and sprint-N.M)
  const bySprint = {};
  const unassigned = [];
  issues.forEach(iss => {
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
    listEl.innerHTML = '<div class="loading-msg">No sprints yet. Create one with + New Sprint.</div>';
    return;
  }

  // Use order list if available (includes sub-labels), else build from integer sprints ascending
  const orderedLabelsRaw = order.length > 0
    ? order.filter(l => /^sprint-\d+(\.\d+)*$/.test(l))
    : [...sprints].sort((a, b) => a - b).map(n => `sprint-${n}`);

  const _sprintParents = data.sprint_parents || {};
  const _rerunInto = data.sprint_rerun_into || {};
  // After a re-run moves tickets to a child label, hide the empty parent card until refresh
  // would have dropped it from the order list anyway (issue #512 UX).
  const orderedLabels = orderedLabelsRaw.filter(label => {
    const ticketCount = (bySprint[label] || []).length;
    if (ticketCount > 0) return true;
    if (_rerunInto[label]) return false;
    const hasChild = Object.values(_sprintParents).some(parent => parent === label);
    return !hasChild;
  });

  // Finished sprints (a summary issue exists) — the same GitHub-backed signal
  // the nav pill uses. Finished sprints are not "NEXT UP" and skip pre-flight.
  _smgmtFinishedLabels = new Set(data.finished_sprints || []);

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
    if (typeof _smgmtIsLinger === 'function' && _smgmtIsLinger(lbl)) continue;
    if (_smgmtFinishedLabels.has(lbl)) continue;
    if ((bySprint[lbl] || []).length >= 1) { _smgmtNextUpLabel = lbl; break; }
  }

  const cards = orderedLabels
    .map(label => {
      const tickets = bySprint[label] || [];
      if (_smgmtIsFreshRerunSprint(label)) delete _smgmtOutcomeCache[label];
      const inLinger = typeof _smgmtIsLinger === 'function' && _smgmtIsLinger(label);
      const outcome = (_smgmtRunningLabels.has(label) || inLinger)
        ? null : (_smgmtOutcomeCache[label] || null);
      const parent = _sprintParents[label] || null;
      const cardHtml = _smgmtCardHtml(label, null, tickets, outcome, label === _smgmtNextUpLabel, parent, _smgmtFinishedLabels.has(label));
      return `<div class="smgmt-sprint-unit" id="smgmt-unit-${escHtml(label)}">` +
             cardHtml + `</div>`;
    }).join('');

  listEl.innerHTML = cards || '<div class="loading-msg">No sprints found.</div>';

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
  orderedLabels.forEach(lbl => _smgmtApplySort(lbl));

  // Re-apply search filter after DOM rebuild (issue #552)
  _smgmtFilterApply();
}

export function _smgmtLabelFilterRender(issues) {
  _smgmtLastLabelIssues = issues || [];
  const row = document.getElementById('smgmt-label-filter-row');
  if (!row) return;

  // Collect all label names from tickets
  const seen = new Set();
  (issues || []).forEach(iss => {
    (iss.labels || []).forEach(l => {
      seen.add(l.name);
      if (l.color) _smgmtLabelColors[l.name] = '#' + l.color;
    });
  });

  // Build ordered list: priority labels first (if present), then remainder alphabetically
  const priority = _SMGMT_FILTER_PRIORITY.filter(n => seen.has(n));
  const rest = [...seen].filter(n => !_SMGMT_FILTER_PRIORITY.includes(n)).sort();
  const allLabels = [...priority, ...rest];

  if (allLabels.length === 0) {
    row.classList.add('is-empty');
    row.innerHTML = '';
    return;
  }

  const _SMGMT_LABEL_VISIBLE = 5;
  const expanded = row.dataset.expanded === 'true';
  const visible = expanded ? allLabels : allLabels.slice(0, _SMGMT_LABEL_VISIBLE);
  const hidden = allLabels.length - _SMGMT_LABEL_VISIBLE;

  row.classList.remove('is-empty');
  row.innerHTML = visible.map(name => {
    const active = !_smgmtDeactivatedLabels.has(name);
    const color = _smgmtLabelColors[name] || 'var(--text-muted)';
    return `<button class="smgmt-lf-chip ${active ? 'is-active' : 'is-inactive'}"
               data-label="${escHtml(name)}"
               aria-pressed="${active}"
               title="${active ? 'Hide' : 'Show'} tickets labeled &quot;${escHtml(name)}&quot;"
               onclick="_smgmtLabelFilterToggle('${escHtml(name)}')">
              <span class="smgmt-lf-chip-dot" style="background:${color}"></span>
              ${escHtml(name)}
            </button>`;
  }).join('') + (hidden > 0 && !expanded
    ? `<button class="smgmt-lf-show-more" onclick="_smgmtLabelFilterToggleExpand(true)">+${hidden} more</button>`
    : hidden > 0 && expanded
    ? `<button class="smgmt-lf-show-more" onclick="_smgmtLabelFilterToggleExpand(false)">Show less</button>`
    : '');
}

export function _smgmtLabelFilterApply() {
  if (_smgmtDeactivatedLabels.size === 0) {
    // Fast path: all active — show everything
    document.querySelectorAll('.smgmt-ticket[data-labels]').forEach(el => {
      el.style.display = '';
    });
    return;
  }
  document.querySelectorAll('.smgmt-ticket[data-labels]').forEach(el => {
    const raw = el.getAttribute('data-labels') || '';
    const ticketLabels = raw ? raw.split(',') : [];
    if (ticketLabels.length === 0) {
      // No labels → always visible
      el.style.display = '';
      return;
    }
    // Hidden only when every ticket label that appears as a chip is deactivated
    const allDeactivated = ticketLabels.every(n => _smgmtDeactivatedLabels.has(n));
    el.style.display = allDeactivated ? 'none' : '';
  });
}

export function _smgmtIsFreshRerunSprint(label) {
  const parents = (_smgmtData && _smgmtData.sprint_parents) || {};
  if (!parents[label]) return false;
  const planState = ((_smgmtData && _smgmtData.sprint_plan_states) || {})[label];
  // 'draft' is the unified-lifecycle spelling; 'planning' covers legacy files.
  return planState === 'draft' || planState === 'planning';
}

/** Optimistic board state after POST /rerun — child visible, parent emptied, no refresh lag. */
export function _smgmtApplyRerunOptimistic(parentLabel, subLabel, ticketNumbers) {
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
  if (!_smgmtData.sprint_plan_states) _smgmtData.sprint_plan_states = {};
  _smgmtData.sprint_plan_states[subLabel] = 'draft';
  delete _smgmtOutcomeCache[parentLabel];
  delete _smgmtOutcomeCache[subLabel];
  if (_smgmtBySprint) {
    const moved = (_smgmtBySprint[parentLabel] || []).filter(t => nums.has(t.number));
    _smgmtBySprint[subLabel] = [...(_smgmtBySprint[subLabel] || []), ...moved];
    _smgmtBySprint[parentLabel] = (_smgmtBySprint[parentLabel] || []).filter(t => !nums.has(t.number));
  }
}

export async function _smgmtFetchMissingOutcomes(orderedLabels, bySprint) {
  const repo = _smgmtRepo();
  if (!repo) return;
  const toFetch = [];
  for (const label of orderedLabels) {
    if (_smgmtRunningLabels.has(label)) continue;
    if (_smgmtIsFreshRerunSprint(label)) continue;
    if (_smgmtOutcomeCache[label] !== undefined) continue;
    const tickets = bySprint[label] || [];
    const hasRework    = tickets.some(t => (t.labels || []).some(l => l.name === 'need-rework' || l.name === 'needs-rework'));
    const hasCompleted = _smgmtHasCompletedTickets(tickets);
    if (tickets.length > 0 && !hasRework && !hasCompleted) continue;
    toFetch.push(label);
  }
  await Promise.all(toFetch.map(async label => {
    try {
      const resp = await fetch(
        `/api/sprints/${encodeURIComponent(label)}/outcome?project=${encodeURIComponent(repo)}`
      );
      if (resp.ok) {
        const outcome = await resp.json();
        _smgmtOutcomeCache[label] = outcome;
        _smgmtInjectOutcomeBand(label, outcome);
      } else {
        _smgmtOutcomeCache[label] = null;
      }
    } catch (_) {
      _smgmtOutcomeCache[label] = null;
    }
  }));
}

export async function _smgmtLoadEstimates(orderedLabels, bySprint) {
  const repo = _smgmtRepo();
  if (!repo) return;
  for (const label of orderedLabels) {
    const tickets = bySprint[label] || [];
    if (tickets.length === 0) continue;
    // Populate reverse lookup for reactivity
    for (const t of tickets) _smgmtTicketToSprint[t.number] = label;
    const issueNums = tickets.map(t => t.number).join(',');
    try {
      const resp = await fetch(
        `/api/estimates/batch?project=${encodeURIComponent(repo)}&issues=${issueNums}`
      );
      if (!resp.ok) continue;
      const data = await resp.json();
      const estEl = document.getElementById(`smgmt-est-${label}`);
      if (estEl && data.complete && data.total_hours !== null) {
        const h = data.total_hours;
        const display = Number.isInteger(h) ? `${h}h` : `${parseFloat(h.toFixed(1))}h`;
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

export async function _smgmtLoadConflicts(orderedLabels, bySprint) {
  const repo = _smgmtRepo();
  if (!repo) return;
  for (const label of orderedLabels) {
    if (_smgmtRunningLabels.has(label)) continue;
    if (_smgmtFinishedLabels.has(label)) continue;
    const tickets = bySprint[label] || [];
    const pending = tickets.filter(t => (t.status || 'backlog') === 'backlog');
    if (pending.length < 2) continue;
    // Clear stale entries for this sprint's tickets before repopulating
    for (const t of pending) delete _smgmtConflictsByIssue[t.number];
    try {
      const resp = await fetch(
        `/api/sprints/${encodeURIComponent(label)}/conflicts?project=${encodeURIComponent(repo)}`
      );
      if (!resp.ok) continue;
      const data = await resp.json();
      for (const c of (data.conflicts || [])) {
        if (!_smgmtConflictsByIssue[c.ticket1_id]) _smgmtConflictsByIssue[c.ticket1_id] = [];
        if (!_smgmtConflictsByIssue[c.ticket2_id]) _smgmtConflictsByIssue[c.ticket2_id] = [];
        _smgmtConflictsByIssue[c.ticket1_id].push({ partnerId: c.ticket2_id, partnerTitle: c.ticket2_title, sharedFiles: c.shared_files });
        _smgmtConflictsByIssue[c.ticket2_id].push({ partnerId: c.ticket1_id, partnerTitle: c.ticket1_title, sharedFiles: c.shared_files });
      }
      for (const t of pending) _smgmtUpdateConflictBadge(t.number);
    } catch (_) {
      // fail silently
    }
  }
}

export async function _smgmtLoadDepOrder(orderedLabels, bySprint) {
  const repo = _smgmtRepo();
  if (!repo) return;
  for (const label of orderedLabels) {
    if (_smgmtRunningLabels.has(label)) continue;
    if (_smgmtFinishedLabels.has(label)) continue;
    const tickets = bySprint[label] || [];
    const pending = tickets.filter(t => (t.status || 'backlog') === 'backlog');
    if (pending.length < 2) continue;
    for (const t of pending) delete _smgmtDepOrderByIssue[t.number];
    try {
      const resp = await fetch(
        `/api/sprints/${encodeURIComponent(label)}/dep-order?project=${encodeURIComponent(repo)}`
      );
      if (!resp.ok) continue;
      const data = await resp.json();
      if (data.has_cycle) {
        const cycleSet = new Set((data.in_cycle_tickets || []).map(String));
        for (const t of pending) {
          if (cycleSet.has(String(t.number))) {
            _smgmtDepOrderByIssue[t.number] = { upstream: [], downstream: [], inCycle: true };
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

export async function _smgmtLoadGoals(orderedLabels) {
  const repo = _smgmtRepo();
  if (!repo) return;
  for (const label of orderedLabels) {
    const goalEl = document.getElementById(`smgmt-goal-${label}`);
    if (!goalEl) continue;
    try {
      const resp = await fetch(
        `/api/sprints/goal?project=${encodeURIComponent(repo)}&sprint=${encodeURIComponent(label)}`
      );
      if (!resp.ok) continue;
      const data = await resp.json();
      const goal = (data.goal || '').trim();
      if (goal) {
        goalEl.textContent = goal;
        goalEl.title = goal;
        goalEl.style.display = '';
      }
    } catch (_) {
      // fail silently
    }
  }
}

export function _smgmtOutcomeBandHtml(label, outcome) {
  const st = outcome.sprint_status;
  const paneState = outcome.state || '';
  const c = outcome.counts || {};
  const dur = _fmtWallClock(outcome.wall_clock_secs);
  const ts = outcome.ended_at
    ? (st === 'completed' ? `ended ${outcome.ended_at}` : `stopped ${outcome.ended_at}`)
    : '';
  const issues = outcome.issues || [];

  // Segmented bar: one block per ticket (issue #613)
  let segBarHtml = '';
  if (issues.length > 0) {
    const blocks = issues.map(iss => {
      const o = iss.outcome || 'skipped';
      let blockClass = 'seg-pending';
      if (o === 'done')    blockClass = 'seg-done';
      else if (o === 'failed')  blockClass = 'seg-failed';
      else if (o === 'skipped') blockClass = 'seg-skipped';
      return `<div class="seg-block ${blockClass}"></div>`;
    }).join('');
    segBarHtml = `<div class="smgmt-seg-bar">${blocks}</div>`;
  }

  // PR + Sprint Summary links for finished/completed state
  let linksHtml = '';
  if (paneState === 'completed' || st === 'completed') {
    const prNum = outcome.pr_number;
    const prUrl = outcome.pr_url;
    const sumNum = outcome.summary_issue_num;
    const sumUrl = outcome.summary_issue_url;
    const prLink = prNum && prUrl
      ? `<a href="${escHtml(prUrl)}" target="_blank" rel="noopener" class="oc-pr-link"><i class="ti ti-git-pull-request"></i> PR #${prNum}</a>`
      : '';
    const sumLink = sumNum && sumUrl
      ? `<a href="${escHtml(sumUrl)}" target="_blank" rel="noopener" class="oc-summary-link"><i class="ti ti-file-description"></i> #${sumNum} Sprint Summary</a>`
      : (sumNum
          ? `<span class="oc-summary-link"><i class="ti ti-file-description"></i> #${sumNum} Sprint Summary</span>`
          : '');
    if (prLink || sumLink) {
      linksHtml = `<div class="oc-band-links">${prLink}${sumLink}</div>`;
    }
  }

  return `<div class="smgmt-outcome-band ${escHtml(st || '')}">
    <div class="smgmt-outcome-stat"><span class="onum green">${c.done || 0}</span><span class="olbl">Completed</span></div>
    <div class="smgmt-outcome-stat"><span class="onum ${c.failed ? 'red' : 'muted'}">${c.failed || 0}</span><span class="olbl">Failed</span></div>
    <div class="smgmt-outcome-stat"><span class="onum muted">${c.skipped || 0}</span><span class="olbl">Skipped</span></div>
    <span class="oc-spacer"></span>
    ${segBarHtml}
    <div class="smgmt-outcome-dur"><i class="ti ti-clock" style="vertical-align:-1px;"></i> ${escHtml(dur)}${ts ? ' · ' + escHtml(ts) : ''}</div>
    ${linksHtml}
  </div>`;
}

export function _smgmtOutcomeTicketListHtml(issues, label, repo) {
  if (!issues || issues.length === 0) return '';
  const safeLabel = label ? escHtml(label) : '';
  const safeRepo  = repo  ? escHtml(repo)  : '';
  return issues.map(iss => {
    const o = iss.outcome || 'skipped';
    let circle = '';
    if (o === 'done')    circle = '<div class="smgmt-ticket-circle done">✓</div>';
    else if (o === 'failed')  circle = '<div class="smgmt-ticket-circle failed">✕</div>';
    else                 circle = '<div class="smgmt-ticket-circle skipped">−</div>';

    const elapsed = `<span class="smgmt-ticket-elapsed">${escHtml(_fmtElapsed(iss.elapsed_secs))}</span>`;
    const rejLabel = o === 'failed'
      ? '<span class="smgmt-lbl-rejected">TESTER REJECTED</span>'
      : '';

    const viewLogBtn = safeLabel && safeRepo
      ? `<button class="btn-view-log" title="View issue log"
              onclick="event.stopPropagation();openLvIssueLog(${iss.number},'${safeLabel}','${safeRepo}')">
           <i class="ti ti-file-text"></i></button>`
      : '';

    return `<div class="smgmt-ticket" data-issue="${iss.number}" data-labels="" draggable="false">
      ${circle}
      <a class="smgmt-ticket-num" href="${safeRepo ? `https://github.com/${safeRepo}/issues/${iss.number}` : '#'}" target="_blank" rel="noopener">#${iss.number}</a>
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
  }).join('');
}

export async function _smgmtLoadFinishCards() {
  const repo = _smgmtRepo();
  if (!repo || !_smgmtData) return;
  const order = (_smgmtData.order && _smgmtData.order.length)
    ? _smgmtData.order
    : (_smgmtData.sprints || []).map(n => `sprint-${n}`);
  await Promise.allSettled(order.map(async (label) => {
    if (_smgmtIsFreshRerunSprint(label)) return;
    try {
      const [cardRes, branchRes] = await Promise.all([
        fetch(`/api/sprints/${encodeURIComponent(label)}/finish-card?project=${encodeURIComponent(repo)}`),
        fetch(`/api/sprints/${encodeURIComponent(label)}/branch-status?project=${encodeURIComponent(repo)}`).catch(() => null),
      ]);
      if (!cardRes.ok) {
        console.warn(`finish-card: unexpected ${cardRes.status} for ${label}`);
        return;
      }
      const cardData = await cardRes.json();
      if (cardData.state === 'no_data') return; // sprint never run — no card shown
      const branchData = branchRes && branchRes.ok ? await branchRes.json() : { exists: false };
      _smgmtFinishCards[label] = { card: cardData, branch: branchData };
      _smgmtRenderFinishCard(label, cardData, branchData, repo);
    } catch (e) { console.warn('finish-card load error for', label, e); }
  }));
}

export function _smgmtRenderFinishCard(label, cardData, branchData, repo) {
  // Patch PR link into outcome band if it's visible (issue #613)
  if (branchData && branchData.pr_url && branchData.pr_number) {
    const sprintCard = document.getElementById(`smgmt-card-${label}`);
    if (sprintCard) {
      let linksEl = sprintCard.querySelector('.oc-band-links');
      if (!linksEl) {
        const band = sprintCard.querySelector('.smgmt-outcome-band');
        if (band) {
          linksEl = document.createElement('div');
          linksEl.className = 'oc-band-links';
          band.appendChild(linksEl);
        }
      }
      if (linksEl && !linksEl.querySelector('.oc-pr-link')) {
        const prLink = document.createElement('a');
        prLink.href = branchData.pr_url;
        prLink.target = '_blank';
        prLink.rel = 'noopener';
        prLink.className = 'oc-pr-link';
        prLink.innerHTML = `<i class="ti ti-git-pull-request"></i> PR #${branchData.pr_number}`;
        linksEl.insertBefore(prLink, linksEl.firstChild);
      }
    }
  }

  if (cardData.state === 'no_data') return; // sprint never run — nothing to display

  const cardEl  = document.getElementById(`smgmt-finish-card-${label}`);
  const blockEl = document.getElementById(`smgmt-card-${label}`);
  if (!cardEl || !blockEl) return;
  // Only show completed/has_rework finish card when PR + summary issue both exist
  const isFinished = cardData.state === 'completed' || cardData.state === 'has_rework';
  const hasPr      = !!(branchData && branchData.pr_url);
  const hasSummary = !!cardData.summary_issue_num;
  if (isFinished && !(hasPr && hasSummary)) {
    cardEl.style.display = 'none';
    return;
  }
  cardEl.style.display = '';
  cardEl.className = `smgmt-finish-card sfc-${cardData.state}`;
  cardEl.innerHTML  = _smgmtFinishCardInnerHtml(cardData, branchData, repo);
  blockEl.classList.add('smgmt-has-card');
}

export function _smgmtFinishCardInnerHtml(cardData, branchData, repo) {
  const state = cardData.state;
  const n = cardData.sprint_number;
  const branchName = `sprint/sprint-${n}`;
  const branchUrl  = `https://github.com/${escHtml(repo)}/tree/${branchName}`;
  const branchLink = branchData && branchData.exists
    ? `<a href="${branchUrl}" target="_blank" rel="noopener" class="sfc-branch-link"><i class="ti ti-git-branch"></i> ${escHtml(branchName)}</a>`
    : `<a href="${branchUrl}" target="_blank" rel="noopener" class="sfc-branch-link sfc-branch-link--warn" title="Could not verify branch exists on GitHub"><i class="ti ti-alert-triangle"></i> ${escHtml(branchName)}</a>`;
  if (state === 'running')    return _sfcRunningHtml(cardData, branchLink, n);
  if (state === 'completed')  return _sfcCompletedHtml(cardData, branchLink, n, branchData);
  // Legacy pane states map to unified lifecycle (sprint-lifecycle.md P4).
  if (state === 'has_rework' || state === 'cancelled') {
    return _sfcHasReworkHtml(cardData, branchLink, n, branchData);
  }
  return '';
}

export function _smgmtCardHtml(label, n, tickets, outcome, isNext, parent, finished) {
  const isRunning = _smgmtRunningLabels.has(label);
  const isLinger = !isRunning && typeof _smgmtIsLinger === 'function' && _smgmtIsLinger(label);
  const isRunningView = isRunning || isLinger;
  // A running sprint collapses to a compact strip by default — the live detail
  // (issue list, budget, agent stats) lives in the Running pane, so the board
  // card is just a status header + Cancel. An explicit user toggle still wins.
  let isCollapsed = isRunning;
  try {
    const _cv = localStorage.getItem('sprintColumn_' + label + '_collapsed');
    if (_cv !== null) isCollapsed = _cv === '1';
  } catch (_) {}

  const isFreshRerun = _smgmtIsFreshRerunSprint(label);
  if (isFreshRerun) outcome = null;

  const outcomeLifecycle = (outcome && outcome.lifecycle || '').toLowerCase();
  const outcomeState = outcome && (outcome.state || (outcome.sprint_status === 'completed' ? 'completed' : null));
  const isHasRework = outcomeLifecycle === 'needs_rework' || outcomeState === 'has_rework'
    || outcomeState === 'cancelled';
  const isReadyToMerge = outcomeLifecycle === 'ready_to_merge'
    || (outcomeLifecycle === 'completed' && outcomeState === 'completed');
  const hasCompleted = isFreshRerun ? false : _smgmtHasCompletedTickets(tickets);
  const isPostRun = !isRunningView && !!((outcome && (outcome.sprint_status || outcome.state)) || hasCompleted);
  // Run is only for first attempts: post-run labels (incl. has-rework) re-run
  // into a child sub-sprint instead (P0 — no same-label re-dispatch).
  const canRun = tickets.length >= 1 && !hasCompleted;

  // Re-run Sprint button: child sprint for fully completed/stopped runs (not has_rework)
  const rerunDisabled = _smgmtAnySprintRunning ? 'disabled' : '';
  const rerunTitle = _smgmtAnySprintRunning
    ? 'title="Cannot re-run: another sprint is currently running."'
    : '';
  const childLabel = _smgmtNextChildLabel(label);
  const childDisplay = sprintLabelDisplay(childLabel).replace('Sprint ', '');
  const rerunBtn = `<button class="smgmt-run-btn smgmt-run-btn--rerun" ${rerunDisabled} ${rerunTitle}
                    onclick="smgmtRerunSprint('${escHtml(label)}')">
                    <i class="ti ti-refresh"></i> Re-run → ${escHtml(childDisplay)}</button>`;

  const rerunInto = (_smgmtData?.sprint_rerun_into || {})[label];
  const rerunChildDisplay = rerunInto
    ? sprintLabelDisplay(rerunInto).replace('Sprint ', '')
    : '';

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
    const runDisabled = !canRun ? 'disabled' : '';
    const runTitle = !canRun ? 'title="Add at least one ticket first"' : '';
    actionBtn = `<button class="smgmt-run-btn" ${runDisabled} ${runTitle}
                  onclick="smgmtRunSprint('${label}')">
                  <i class="ti ti-player-play"></i> Run Sprint</button>`;
  }

  const isOutcomeCompleted = isReadyToMerge || isHasRework
    || outcomeState === 'completed';
  // Show Merge Sprint when the sprint is post-run (ready_to_merge / needs_rework).
  const finishHidden = (isOutcomeCompleted || (isPostRun && !outcome)) ? '' : 'hidden';
  const finishDisabled = isReadyToMerge && tickets.length === 0 ? 'disabled' : '';

  // Outcome state: build band + ticket rows if outcome is cached
  let outcomeBandHtml = '';
  let outcomeCardClass = '';
  let outcomeBadgeHtml = '';
  let headerMetaHtml = '';
  let ticketCount = tickets.length;
  let ticketsContainerHtml = '';
  let isOutcomeView = false;
  let rollupItems = tickets;

  if (outcome && (outcome.sprint_status || outcome.state)) {
    const meta = _smgmtStateMeta(outcome, (outcome.issues || []).length);
    outcomeCardClass = ' ' + meta.cardClass;
    outcomeBadgeHtml = `<span class="smgmt-state-badge ${meta.badgeCls}">${escHtml(meta.badge)}</span>`;
    if (meta.state === 'needs_rework') {
      const _metaSecs = outcome.wall_clock_secs;
      const _metaStopped = outcome.ended_at ? _fmtStoppedAt(outcome.ended_at) : null;
      const _metaParts = [];
      if (_metaSecs != null) _metaParts.push(_fmtRunningTime(_metaSecs));
      if (_metaStopped) _metaParts.push(`stopped ${_metaStopped}`);
      if (_metaParts.length) headerMetaHtml = `<span class="smgmt-sprint-meta">${escHtml(_metaParts.join(' · '))}</span>`;

      const _elapsedByNum = {};
      if (outcome.issues) { for (const _oi of outcome.issues) { if (_oi.elapsed_secs != null) _elapsedByNum[_oi.number] = _oi.elapsed_secs; } }
      // Keep planning view: rework tickets stay actionable; the finish-card hat shows the summary.
      ticketsContainerHtml = tickets.length > 0
        ? tickets.map(t => _smgmtTicketRowHtml(t, label, _elapsedByNum[t.number] ?? null)).join('')
        : '<div class="smgmt-drop-hint">Drop tickets here</div>';
    } else {
      outcomeBandHtml = _smgmtOutcomeBandHtml(label, outcome);
      const issueList = outcome.issues || [];
      ticketCount = issueList.length;
      ticketsContainerHtml = _smgmtOutcomeTicketListHtml(issueList, label, _smgmtRepo());
      isOutcomeView = true;
      rollupItems = issueList.map(i => ({ number: i.number }));
    }
  } else if (isRunningView) {
    ticketsContainerHtml = _smgmtRunningTicketRowsHtml(label, tickets);
  } else {
    // Planning view ticket rows
    ticketsContainerHtml = tickets.length > 0
      ? tickets.map(t => _smgmtTicketRowHtml(t, label)).join('')
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
  const logHtml = '';
  const cancelBannerHtml = '';

  // Bulk estimate button (issue #598): show when any ticket lacks a size-* label
  const hasUnsizedTickets = tickets.length > 0 && tickets.some(t => !_smgmtTicketHasEstimate(t));
  const bulkEstBtnHtml = `<button class="smgmt-bulk-est-btn${hasUnsizedTickets ? '' : ' hidden'}"
                    onclick="event.stopPropagation();_smgmtBulkEstimate('${escHtml(label)}',this)"
                    title="Estimate all unsized tickets in this sprint">
                    <i class="ti ti-calculator"></i> Estimate all unsized</button>
                   <span class="smgmt-bulk-est-progress"></span>`;

  const plannedBadge = (!isNext && !finished && !isPostRun && !outcomeBadgeHtml)
    ? '<span class="sc-planned-badge">PLANNED</span>'
    : '';
  const blockedHint = (_smgmtAnySprintRunning && !isPostRun && !isRunningView)
    ? `<span class="sc-blocked-hint">blocked: ${_smgmtRunningBlockerShort()} running</span>`
    : '';
  const parentLineage = (parent && !isFreshRerun)
    ? `<span class="smgmt-sprint-lineage" title="Child sprint spawned from ${escHtml(parent)}">← from ${escHtml(sprintLabelDisplay(parent))}</span>`
    : '';

  const live = isRunningView
    ? ((typeof _smgmtLingerLive === 'function' ? _smgmtLingerLive(label) : null)
       || _smgmtLiveCache[label] || null)
    : null;
  const runningComplete = live
    ? ((live.done_count || 0) + (live.failed_count || 0) + (live.skipped_count || 0))
    : 0;
  const runningTotal = live ? (live.total_count || tickets.length) : tickets.length;
  const runningRatio = runningTotal > 0 ? `${runningComplete}/${runningTotal}` : '—';
  const runningElapsed = live && live.time_spent_sec > 0
    ? `<span class="smgmt-sprint-meta" id="smgmt-elapsed-${escHtml(label)}">elapsed ${_fmtRunningTime(live.time_spent_sec)}</span>`
    : `<span class="smgmt-sprint-meta" id="smgmt-elapsed-${escHtml(label)}"></span>`;
  const runningBadgeHtml = isRunningView
    ? `<span class="smgmt-running-badge" id="smgmt-running-badge-${escHtml(label)}"><span class="smgmt-running-badge-dot"></span>${isLinger ? 'done' : runningRatio}</span>`
    : '';
  const runningStripeHtml = isRunningView ? '<div class="smgmt-running-stripe"></div>' : '';
  const runningClass = isRunning ? ' smgmt-running' : (isLinger ? ' smgmt-linger' : '');

  const collapsedClass = isCollapsed ? ' smgmt-collapsed' : '';
  const collapseLabel = (isCollapsed ? 'Expand ' : 'Collapse ') + escHtml(sprintLabelDisplay(label));
  return `
    <div class="smgmt-sprint-card sc-v5${outcomeCardClass}${runningClass}${collapsedClass}${isRunning ? ' smgmt-running-clickable' : ''}" id="smgmt-card-${escHtml(label)}"
         ondragover="${isRunning ? '' : `_smgmtDragOver(event, '${escHtml(label)}')`}"
         ondragleave="${isRunning ? '' : `_smgmtDragLeave(event)`}"
         ondrop="${isRunning ? '' : `_smgmtDropOnSprint(event, '${escHtml(label)}')`}"
         ${isRunning ? `onclick="if(!event.target.closest('button,a')) _smgmtShowSubView('running')"` : ''}>
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
          ${runningBadgeHtml}
          ${isNext && !isRunning ? '<span class="smgmt-next-badge">NEXT UP</span>' : ''}
          ${plannedBadge}
          ${outcomeBadgeHtml}
          ${headerMetaHtml}
          ${parentLineage}
          <span class="sc-meta smgmt-sprint-count" id="smgmt-col-rollup-${escHtml(label)}">${_smgmtRollupText(rollupItems)}</span>
        </div>
        <div class="smgmt-sprint-header-right sc-header-right">
          ${isRunning ? '' : `<button class="smgmt-delete-btn"
                  onclick="smgmtDeleteSprint('${escHtml(label)}')">
            <i class="ti ti-trash"></i> Delete</button>`}
          ${actionBtn}
          ${blockedHint}
          ${isRunning ? runningElapsed : ''}
          <button class="smgmt-finish-btn ${finishHidden}" ${finishDisabled}
                  title="${finishDisabled ? 'No open tickets' : 'Finish sprint'}"
                  onclick="smgmtFinishSprint('${escHtml(label)}')">
            <i class="ti ti-flag-check"></i> Merge Sprint</button>
        </div>
      </div>
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
  const liveIssues = (live && live.issues && live.issues.length > 0) ? live.issues : [];
  const liveByNum = {};
  liveIssues.forEach(i => { liveByNum[i.number] = i; });

  const sourceTickets = (liveIssues.length > 0 ? liveIssues : tickets)
    .slice().sort((a, b) => (a.dispatch_level || 0) - (b.dispatch_level || 0));
  const cardRepo = _smgmtRepo();

  if (sourceTickets.length === 0) {
    return '<div class="smgmt-drop-hint">No tickets in this sprint</div>';
  }

  let prevLevel = 0;
  return sourceTickets.map(t => {
    const liveIss = liveByNum[t.number];
    const liveStatus = liveIss ? liveIss.status : null;
    const agentStatus = liveIss ? liveIss.agent_status : null;
    const ticketLevel = (liveIss && liveIss.dispatch_level) || (t.dispatch_level) || 0;

    let sepHtml = '';
    if (ticketLevel > 0 && ticketLevel > prevLevel) {
      sepHtml = `<div class="level-sep">
        <span class="level-sep-num">Level ${ticketLevel}</span>
        <span class="level-sep-desc">· runs after level ${prevLevel} completes</span>
      </div>`;
    }
    if (ticketLevel > 0) prevLevel = ticketLevel;

    const isActiveAgent = agentStatus && (agentStatus.endsWith('_running') || agentStatus.endsWith('_dispatched'));
    let indicator = '';
    if (liveStatus === 'done') {
      indicator = '<div class="smgmt-ticket-indicator"><div class="circle-done">&#10003;</div></div>';
    } else if (agentStatus === 'failed' || liveStatus === 'skipped') {
      indicator = '<div class="smgmt-ticket-indicator"><div class="circle-failed">&#10005;</div></div>';
    } else if (liveStatus === 'in-progress' || isActiveAgent || (currentTicket && t.number === currentTicket.number)) {
      indicator = '<div class="smgmt-ticket-indicator"><div class="ring"></div></div>';
    } else {
      indicator = '<div class="smgmt-ticket-indicator"><div class="circle-pending"></div></div>';
    }

    const issueUrl = t.url || (cardRepo ? `https://github.com/${cardRepo}/issues/${t.number}` : '#');
    const sizeVal = (liveIss && liveIss.size) || t.size || '';
    const sizePillHtml = sizeVal
      ? `<span class="smgmt-ticket-size-pill" title="≈${(liveIss && liveIss.minutes) || _sizeMinutes(sizeVal)} min">${escHtml(sizeVal)}</span>`
      : '';
    const runSizeAttr = sizeVal ? ` data-size="${escHtml(sizeVal)}"` : '';
    const agentTagHtml = (liveIss && liveIss.agent)
      ? `<span class="smgmt-ticket-agent-tag ${_smgmtAgentTagClass(liveIss.agent)}">${escHtml(liveIss.agent.toUpperCase())}</span>`
      : '';
    const elapsedStr = liveIss ? _fmtTicketElapsed(liveIss.elapsed_secs) : null;
    const elapsedHtml = elapsedStr
      ? `<span class="smgmt-ticket-elapsed">${elapsedStr}</span>`
      : '';
    const runTicketLabels = escHtml((t.labels || []).map(l => l.name).join(','));
    return sepHtml + `<div class="smgmt-ticket" data-issue="${t.number}" data-labels="${runTicketLabels}" draggable="false"${runSizeAttr}>
      ${indicator}
      <a class="smgmt-ticket-num" href="${escHtml(issueUrl)}" target="_blank"
         rel="noopener">#${t.number}</a>
      <span class="smgmt-ticket-title" title="${escHtml(t.title)}">${escHtml(t.title)}</span>
      ${sizePillHtml}${agentTagHtml}${elapsedHtml}
    </div>`;
  }).join('');
}

/** Level summary for the board running banner, e.g. "level 2 of 3". */
export function _smgmtRunningLevelText(live) {
  const levels = (live && live.levels) || [];
  if (levels.length > 1) {
    const active = levels.find(l => l.state === 'active');
    const cur = active ? active.level : levels[levels.length - 1].level;
    return `level ${cur} of ${levels.length}`;
  }
  const issues = (live && live.issues) || [];
  const levelNums = [...new Set(
    issues.map(i => (i.dispatch_level || 0) || 1),
  )].filter(l => l > 0).sort((a, b) => a - b);
  if (levelNums.length <= 1) return null;
  let current = levelNums[0];
  for (const lvl of levelNums) {
    const group = issues.filter(i => ((i.dispatch_level || 0) || 1) === lvl);
    const allDone = group.length > 0 && group.every(i =>
      i.status === 'done' || i.status === 'skipped' || i.agent_status === 'failed',
    );
    if (!allDone) { current = lvl; break; }
    current = lvl;
  }
  return `level ${current} of ${levelNums.length}`;
}

/** Compact board banner with a link to the Running sub-view (hotfix 0612). */
export function _smgmtRunningBoardBannerHtml(label, tickets) {
  const isLinger = typeof _smgmtIsLinger === 'function' && _smgmtIsLinger(label);
  const live = (typeof _smgmtLingerLive === 'function' ? _smgmtLingerLive(label) : null)
    || _smgmtLiveCache[label] || null;
  const doneCount = live ? (live.done_count || 0) : 0;
  const failedCount = live ? (live.failed_count || 0) : 0;
  const skippedCount = live ? (live.skipped_count || 0) : 0;
  const totalCount = live ? (live.total_count || tickets.length) : tickets.length;
  const completeCount = doneCount + failedCount + skippedCount;
  const timeSpentSec = live ? (live.time_spent_sec || 0) : 0;
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
  const lingerCls = isLinger ? ' linger' : '';
  return `<div class="smgmt-board-running-banner${lingerCls}" id="smgmt-board-banner-${safeLabel}" data-label="${safeLabel}">
    <span class="smgmt-board-running-banner-dot" aria-hidden="true"></span>
    <span class="smgmt-board-running-banner-text" id="smgmt-board-banner-text-${safeLabel}">${parts.join(' · ')}</span>
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
  textEl.textContent = parts.join(' · ');
}

export function _smgmtRunningCardHtml(label, n, tickets) {
  let isCollapsed = false;
  try { isCollapsed = localStorage.getItem('sprintColumn_' + label + '_collapsed') === '1'; } catch (_) {}
  const live = _smgmtLiveCache[label] || null;

  // Stat strip values (use live data if available, else zeros)
  const doneCount     = live ? (live.done_count    || 0) : 0;
  const failedCount   = live ? (live.failed_count  || 0) : 0;
  const skippedCount  = live ? (live.skipped_count || 0) : 0;
  const totalCount    = live ? (live.total_count   || tickets.length) : tickets.length;
  const completeCount = doneCount + failedCount + skippedCount;
  const estRemMins    = live ? live.est_remaining_minutes : null;
  const timeSpentSec  = live ? (live.time_spent_sec || 0) : 0;
  const currentTicket = live ? live.current_ticket : null;
  const activeAgent   = live ? live.active_agent : null;
  const recentLogLines = live ? (live.recent_log_lines || []) : [];

  // Progress bar percentage (complete / total)
  const pct = totalCount > 0 ? Math.round((completeCount / totalCount) * 100) : 0;

  // Use locked snapshot (live.issues) as the source of truth for rows when available.
  // Fall back to label-derived tickets only during the pre-snapshot window.
  const liveIssues = (live && live.issues && live.issues.length > 0) ? live.issues : [];
  const liveByNum = {};
  liveIssues.forEach(i => { liveByNum[i.number] = i; });

  const sourceTickets = (liveIssues.length > 0 ? liveIssues : tickets)
    .slice().sort((a, b) => (a.dispatch_level || 0) - (b.dispatch_level || 0));

  // Segmented bar blocks — one per ticket (issue #613)
  const segBarHtml = sourceTickets.length > 0
    ? `<div class="smgmt-seg-bar" id="smgmt-seg-${escHtml(label)}">${
        sourceTickets.map(t => {
          const liveIss = liveByNum[t.number];
          const liveStatus = liveIss ? liveIss.status : null;
          const agentStatus = liveIss ? liveIss.agent_status : null;
          let blockClass = 'seg-pending';
          if (liveStatus === 'done') blockClass = 'seg-done';
          else if (agentStatus === 'failed' || liveStatus === 'skipped') blockClass = 'seg-failed';
          else if (liveStatus === 'in-progress' || agentStatus === 'running' || (currentTicket && t.number === currentTicket.number)) blockClass = 'seg-running';
          return `<div class="seg-block ${blockClass}" data-issue="${t.number}"></div>`;
        }).join('')
      }</div>`
    : '';

  // Ticket rows with level-sep rows between dispatch levels (issue #613)
  const ticketRowsHtml = _smgmtRunningTicketRowsHtml(label, tickets);

  const runCollapsedClass = isCollapsed ? ' smgmt-collapsed' : '';
  const runCollapseLabel = (isCollapsed ? 'Expand ' : 'Collapse ') + escHtml(sprintLabelDisplay(label));
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
            <span class="smgmt-running-badge-dot"></span>${totalCount > 0 ? `${completeCount}/${totalCount}` : '—'}
          </span>
        </div>
        <div class="smgmt-sprint-header-right">
          <span class="smgmt-sprint-meta" id="smgmt-elapsed-${escHtml(label)}">${timeSpentSec > 0 ? `elapsed ${_fmtRunningTime(timeSpentSec)}` : ''}</span>
          <button class="smgmt-cancel-btn" onclick="smgmtCancelSprint('${escHtml(label)}')">
            <i class="ti ti-player-stop"></i> Cancel sprint</button>
        </div>
      </div>
      <div class="smgmt-outcome-band" id="smgmt-running-stats-${escHtml(label)}">
        <span class="oc-done" id="smgmt-rs-done-${escHtml(label)}">${doneCount} DONE</span>
        <span class="oc-fail ${failedCount > 0 ? '' : 'muted'}" id="smgmt-rs-failed-${escHtml(label)}">${failedCount} FAILED</span>
        <span class="oc-skip" id="smgmt-rs-skipped-${escHtml(label)}">${skippedCount} SKIPPED</span>
        <span class="oc-est" id="smgmt-rs-est-${escHtml(label)}">${estRemMins != null ? `↩ ${estRemMins}m EST. REMAINING` : '↩ EST. REMAINING'}</span>
        <span class="oc-spacer"></span>
        ${segBarHtml}
        <span class="smgmt-outcome-dur" id="smgmt-rs-time-${escHtml(label)}">${_fmtRunningTime(timeSpentSec)}</span>
      </div>
      <div id="smgmt-active-agents-wrap-${escHtml(label)}">${_smgmtActiveAgentsHtml(live, label)}</div>
      <div id="smgmt-levels-wrap-${escHtml(label)}">${_smgmtLevelsHtml(live, label)}</div>
      <div class="smgmt-sprint-tickets" id="smgmt-tickets-${escHtml(label)}">
        ${ticketRowsHtml || '<div class="smgmt-drop-hint">No tickets in this sprint</div>'}
      </div>
      <div class="smgmt-live-log" id="smgmt-live-log-${escHtml(label)}">
        <div class="smgmt-live-log-bar">
          <span class="smgmt-live-indicator"></span>
          <span>live</span>
          <span class="smgmt-live-log-agent" id="smgmt-live-agent-${escHtml(label)}">${
            _smgmtLiveAgentBadgesHtml(live)
          }</span>
        </div>
        <div class="smgmt-live-log-stream" id="smgmt-live-log-stream-${escHtml(label)}">${
          _smgmtLiveLogLinesHtml(recentLogLines)
        }</div>
      </div>
    </div>`;
}

export function _smgmtRollupText(items) {
  const count = items.length;
  if (count === 0) return '0 tickets';
  let totalMins = 0, unestimated = 0;
  for (const t of items) {
    const size = _smgmtTicketSize(t);
    const mins = size ? _sizeMinutes(size) : 0;
    if (mins > 0) totalMins += mins;
    else unestimated++;
  }
  const countStr = `${count} ticket${count !== 1 ? 's' : ''}`;
  if (unestimated === count) return countStr;
  const h = totalMins / 60;
  const timeStr = h < 1
    ? `~${totalMins}m`
    : `~${parseFloat((Math.round(h * 10) / 10).toFixed(1))}h`;
  return `${countStr} · ${timeStr}`;
}

/** Single source of truth: JSON cache → ticket.size → GitHub size-* label. */
export function _smgmtTicketSize(t) {
  if (!t) return null;
  const cached = Object.prototype.hasOwnProperty.call(_estDataCache, t.number)
    ? _estDataCache[t.number] : undefined;
  let size = (cached && cached.size) ? cached.size : (t.size || null);
  if (!size && t.labels) {
    for (const lbl of t.labels) {
      const m = /^size-([SMLX]+)$/.exec(lbl.name || '');
      if (m) { size = m[1]; break; }
    }
  }
  return size || null;
}

export function _smgmtTicketHasEstimate(t) {
  return _smgmtTicketSize(t) !== null;
}

/** Short label for the sprint blocking Run, e.g. "S56". */
export function _smgmtRunningBlockerShort() {
  if (!_smgmtRunningLabels || _smgmtRunningLabels.size === 0) return '';
  const lbl = [..._smgmtRunningLabels][0];
  const m = String(lbl).match(/sprint-(\d+(?:\.\d+)?)/);
  return m ? `S${m[1]}` : sprintLabelDisplay(lbl);
}

/** Right-aligned estimate minutes; spinner only while an explicit action runs. */
export function _smgmtTicketEstHtml(ticket) {
  const activity = (typeof globalThis !== 'undefined' && globalThis._smgmtRowActivity)
    ? globalThis._smgmtRowActivity[ticket.number]
    : null;
  if (activity) {
    const label = activity === 'fixing-ac' ? 'fixing AC…' : 'estimating…';
    return `<span class="smgmt-ticket-est smgmt-ticket-est--pending" id="smgmt-ticket-est-${ticket.number}" aria-label="${label}">` +
      `<span class="smgmt-estimating-dot" aria-hidden="true"></span></span>`;
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
  const hasRework = (ticket.labels || []).some(l => l.name === 'need-rework' || l.name === 'needs-rework');
  const statusClass = hasRework ? 'smgmt-status-need-rework' : ({
    'backlog':      'smgmt-status-backlog',
    'in-progress':  'smgmt-status-in-progress',
    'sit':          'smgmt-status-sit',
    'uat':          'smgmt-status-uat',
    'done':         'smgmt-status-done',
  }[ticket.status] || 'smgmt-status-backlog');
  const statusLabel = hasRework ? 'needs rework' : (ticket.status || 'backlog');
  const isSelected = _smgmtSelectedIssues.has(ticket.number);

  // Outcome icon: green check (done/uat), red X (needs-rework), blue dot (active), gray circle (backlog)
  const _outcomeMap = {
    'done':         ['ti-circle-check', 'outcome-success'],
    'uat':          ['ti-circle-check', 'outcome-success'],
    'needs-rework': ['ti-circle-x',     'outcome-rework'],
    'in-progress':  ['ti-circle-dot',   'outcome-active'],
    'sit':          ['ti-circle-dot',   'outcome-active'],
  };
  const _oc = hasRework ? ['ti-circle-x', 'outcome-rework'] : (_outcomeMap[ticket.status] || ['ti-circle', 'outcome-backlog']);
  const outcomeIconHtml = `<i class="ti ${_oc[0]} smgmt-outcome-icon ${_oc[1]}" title="${escHtml(statusLabel)}"></i>`;

  const sizeValue = _smgmtTicketSize(ticket) || '';
  const hasEstimate = sizeValue !== '';
  const sizeAttr = sizeValue ? ` data-size="${escHtml(sizeValue)}"` : '';
  // Compute estimateBadgeHtml first; when JSON cache has the size, it renders the
  // interactive estimate button — sizePillHtml must be suppressed in that case to
  // prevent a duplicate size indicator appearing (issue #674).
  const estimateBadgeHtml = _smgmtEstimateBadgeHtml(ticket.number);
  const _cachedEst = Object.prototype.hasOwnProperty.call(_estDataCache, ticket.number)
    ? _estDataCache[ticket.number] : undefined;
  const sizePillHtml = (sizeValue && !(_cachedEst && _cachedEst.size))
    ? `<span class="smgmt-ticket-size-pill" title="≈${_sizeMinutes(sizeValue)} min">${escHtml(sizeValue)}</span>`
    : '';
  const staleBadgeHtml = (ticket.estimate_stale && hasEstimate)
    ? `<button class="smgmt-stale-badge" data-stale="true" tabindex="0"
         title="Estimate may be outdated — issue body changed since last estimate"
         onclick="event.stopPropagation();_smgmtReEstimate(${ticket.number},this)"
         onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();_smgmtReEstimate(${ticket.number},this);}">stale</button>`
    : '';
  const reEstBtnHtml = (_smgmtEstimatorAvailable && !ticket.estimate_stale)
    ? `<button class="smgmt-reestimate-btn" tabindex="0" title="Re-estimate this ticket"
         onclick="event.stopPropagation();_smgmtReEstimate(${ticket.number},this)"
         onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();_smgmtReEstimate(${ticket.number},this);}">Re-estimate</button>`
    : '';
  const riskFlagIconsHtml = _smgmtRiskFlagIconsHtml(ticket.number);
  const schedDepHtml = _smgmtSchedDepHtml(ticket);

  const ticketLabelNames = (ticket.labels || []).map(l => l.name).join(',');
  const sk = escHtml(label);

  return `
    <div class="smgmt-ticket${isSelected ? ' is-selected' : ''}" id="smgmt-ticket-${ticket.number}"
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
             ${isSelected ? 'checked' : ''}
             onclick="event.stopPropagation()"
             onchange="_smgmtToggleSelect(${ticket.number}, this.checked)">
      <i class="ti ti-grip-vertical smgmt-ticket-grip"></i>
      ${outcomeIconHtml}
      <a class="smgmt-ticket-num" href="${escHtml(ticket.url || '#')}" target="_blank"
         rel="noopener" draggable="false" onclick="event.stopPropagation()">#${ticket.number}</a>
      <span class="smgmt-ticket-title" title="${escHtml(ticket.title)}">${escHtml(ticket.title)}</span>
      ${sizePillHtml}${staleBadgeHtml}${estimateBadgeHtml}${riskFlagIconsHtml}${schedDepHtml}${reEstBtnHtml}
      ${hasRework ? '<span class="smgmt-lbl-rejected">TESTER REJECTED</span>' : ''}
      ${elapsedSecs != null ? `<span class="smgmt-ticket-elapsed">${_fmtRunningTime(elapsedSecs)}</span>` : ''}
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

export function _smgmtRenderBacklog(tickets) {
  _blBacklogAll = tickets || [];
  const countEl   = document.getElementById('smgmt-backlog-count');
  const ticketsEl = document.getElementById('smgmt-backlog-tickets');
  if (!ticketsEl) return;

  // Apply the active filter pills client-side over the loaded backlog data.
  const filtered = _blApplyFilters(_blBacklogAll);

  if (countEl) {
    const total = _blBacklogAll.length, shown = filtered.length;
    countEl.textContent = total > 0
      ? `${shown === total ? total : `${shown} of ${total}`} ticket${total !== 1 ? 's' : ''}`
      : '0 tickets';
  }

  // Update bulk estimate button visibility (issue #598) — over full backlog
  const backlogBulkBtn = document.getElementById('smgmt-backlog-bulk-est-btn');
  if (backlogBulkBtn) {
    const hasUnsized = _blBacklogAll.some(t => !_smgmtTicketHasEstimate(t));
    backlogBulkBtn.classList.toggle('hidden', !hasUnsized);
  }

  // Sort newest first (higher issue number = newer)
  const sorted = [...filtered].sort((a, b) => b.number - a.number);

  // Build list of sprint labels for "Move to" popup
  const allSprintNums = (_smgmtData?.sprints || []).sort((a, b) => a - b);

  if (sorted.length === 0) {
    const msg = _blBacklogAll.length === 0
      ? 'No backlog tickets — all caught up'
      : 'No tickets match the active filters';
    ticketsEl.innerHTML = `<div class="smgmt-drop-hint" style="padding:14px 18px;text-align:center;">${msg}</div>`;
  } else {
    ticketsEl.innerHTML = sorted.map(t => _smgmtBacklogTicketHtml(t, allSprintNums)).join('');
  }

  _blSyncFilterPills();
  _blUpdateActions();
}

export function _smgmtBacklogTicketHtml(ticket, sprintNums) {
  const isSelected = _smgmtSelectedIssues.has(ticket.number);
  const hasEstimate = _smgmtTicketHasEstimate(ticket);
  const backlogLabelNames = (ticket.labels || []).map(l => l.name).join(',');
  const schedDepHtml = _smgmtSchedDepHtml(ticket);
  const sizeValue = _smgmtTicketSize(ticket) || '';
  const sizeAttr = sizeValue ? ` data-size="${escHtml(sizeValue)}"` : '';
  const sizePillHtml = sizeValue
    ? `<span class="smgmt-ticket-size-pill">${escHtml(sizeValue)}</span>`
    : '';
  const ageDays = ticket.created_at
    ? Math.floor((Date.now() - Date.parse(ticket.created_at)) / 86400000)
    : null;
  const ageHtml = ageDays != null && !isNaN(ageDays)
    ? `<span class="bl-row-age">${ageDays}d</span>`
    : '';
  return `
    <div class="smgmt-ticket bl-row${isSelected ? ' is-selected' : ''}" id="smgmt-ticket-${ticket.number}"
         draggable="true"
         data-issue="${ticket.number}"
         data-sprint=""${sizeAttr}
         data-labels="${escHtml(backlogLabelNames)}"
         ondragstart="_smgmtBacklogTicketDragStart(event, ${ticket.number})"
         ondragend="_smgmtTicketDragEnd(event)"
         onclick="_smgmtRowClick(event, ${ticket.number}, null)"
         oncontextmenu="_smgmtCtxMenuOpen(event,${ticket.number})">
      <input type="checkbox" class="smgmt-ticket-cb" draggable="false"
             ${isSelected ? 'checked' : ''}
             onclick="event.stopPropagation()"
             onchange="_smgmtToggleSelect(${ticket.number}, this.checked)">
      <a class="smgmt-ticket-num" href="${escHtml(ticket.url || '#')}" target="_blank"
         rel="noopener" draggable="false" onclick="event.stopPropagation()">#${ticket.number}</a>
      <span class="smgmt-ticket-title" title="${escHtml(ticket.title)}">${escHtml(ticket.title)}</span>
      ${schedDepHtml}${sizePillHtml}${ageHtml}
      <button class="smgmt-row-menu-btn" tabindex="0" title="Ticket actions" aria-haspopup="true" aria-expanded="false"
              onclick="event.stopPropagation();_smgmtRowMenuOpen(event, ${ticket.number}, null, ${hasEstimate})"
              onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();_smgmtRowMenuOpen(event,${ticket.number},null,${hasEstimate});}">
        <i class="ti ti-menu-2"></i></button>
    </div>`;
}
