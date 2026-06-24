/* Sprint history ledger (issue #806 / #797 extraction).
 *
 * Lifecycle chips, verb-gated cards, fold groups, stale-branch scan, and
 * run-stats — fed by GET /api/sprints/history (local-only).
 */
/* global escHtml, sprintLabelDisplay, _slug, _cachedFullRepo, _smgmtAnySprintRunning,
          _smgmtBySprint, _smgmtUpdateSubnav, _smgmtRepo, smgmtFinishSprint,
          smgmtDeleteSprint, _nextSprintSublabel, CSS */

// Lifecycle states that require the human (sprint-lifecycle redesign):
//   ready_to_merge → Merge Sprint (UAT sign-off)
//   needs_rework   → review + Re-run
//   failed         → investigate / Resume
// completed / deleted / cancelled / running are excluded (no action needed;
// running has its own pulsing dot on the Running tab).
const _HIST_ACTION_STATES = new Set(['ready_to_merge', 'needs_rework', 'failed']);

export function _histNeedsActionCount() {
  return (_histLedgerData || []).reduce(
    (acc, s) => acc + (_HIST_ACTION_STATES.has(s && s.lifecycle_state) ? 1 : 0),
    0,
  );
}

// ── Sprint history ledger (issue #806) ──────────────────────────────────────
// Verb-gated, lockable sprint cards with per-sprint issue lists and links,
// fed by GET /api/sprints/history (issue #805) — local-only, no GitHub call.
let _histLedgerData = [];
globalThis._histLedgerData = _histLedgerData;
const _histExpanded = new Set();   // labels of currently-expanded cards
const _histGroupCollapsed = new Set(); // base labels whose child wrap is hidden
let _histDidAutoExpand = false;    // auto-expand recent cards once per session
// Folding (issue #807): the N most-recent sprints render expanded; older ones
// collapse into aggregate folds of the same size. _histFoldSize mirrors the
// `history_fold_size` project setting (default 10); _histFoldExpanded tracks
// which fold groups are currently open (distinct from per-card expansion).
let _histFoldSize = 10;
const _histFoldExpanded = new Set();   // ids of currently-expanded fold groups

// Stale-while-revalidate cache for the ledger feed (Tier 1/2 load perf).
// TTL is 5 min by default (was 45s) so the pane serves from cache and does not
// auto-revalidate on every open — a manual "Refresh all" forces a reload.
// Overridable via the global `history_cache_ttl_min` setting.
let _histLedgerCacheRepo = '';
let _histLedgerCacheAt = 0;
let _HIST_LEDGER_TTL_MS = 300000;
let _histLedgerInflight = null;
// Default to the fast active-only feed (running/ready_to_merge/needs_rework/
// partial_finished + 3 recent completed). "Show completed" flips this to load
// the full closed history on demand.
let _histShowClosed = false;

export function _histResetLedgerCache() {
  _histLedgerData = [];
  globalThis._histLedgerData = _histLedgerData;
  _histLedgerCacheRepo = "";
  _histLedgerCacheAt = 0;
  _histLedgerInflight = null;
}
let _histRenderRaf = 0;

// Stale-branch scan (issue #808): label -> { sprint, count, branches[],
// merged[], unmerged[] }, populated by _histScanStale() and consumed by the
// per-row amber chip. Scan/cleanup are repo actions, so a chip renders on a
// History row regardless of whether that row is locked (AC7).
let _histStaleBySprint = {};

// Run-stats (issue #810): label -> the GET /api/sprints/{label}/run-stats
// aggregation, lazily fetched the first time a card is expanded and cached so
// re-renders (fold toggles, stale scans) reuse it. `undefined` means not yet
// fetched; `{has_runs:false}` means a sprint with no agent_runs rows.
const _histRunStats = {};

// Terminal states that lock a card: no further action is possible, links only.
function _histIsLocked(state) {
  const s = (state || '').toLowerCase();
  return s === 'finished' || s === 'deleted' || s === 'completed';
}

// A rerun child sprint carries a dotted label (e.g. sprint-61.1).
function _histIsChild(label) {
  return /^sprint-\d+\.\d+/.test(label || '');
}

// Whole seconds → compact "Xs" / "Xm Ys" / "Xh Ym"; missing time reads as a dash.
function _histFmtSecs(secs) {
  if (secs == null || isNaN(secs)) return '—';
  secs = Math.round(secs);
  if (secs < 60) return secs + 's';
  const m = Math.floor(secs / 60), s = secs % 60;
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60), mm = m % 60;
  return mm ? `${h}h ${mm}m` : `${h}h`;
}

function _histFmtTokens(n) {
  if (n == null || isNaN(n)) return '0';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(n);
}

// Map a per-issue local disposition → its display chip {cls,label}:
//   merged → MERGED · closed/failed → CRASHED · open+ran → OPEN·UAT ·
//   open + never-ran (no time_spent) → NOT RUN.
function _histIssueChip(iss) {
  const st = (iss.state || '').toLowerCase();
  if (iss.failure_reason || (iss.agent_status || '').toLowerCase() === 'failed') {
    return { cls: 'crashed', label: 'CRASHED · in-progress' };
  }
  if (st === 'merged') return { cls: 'merged',  label: 'MERGED' };
  if (st === 'closed') return { cls: 'crashed', label: 'CRASHED' };
  if (iss.time_spent != null) return { cls: 'uat', label: 'OPEN · UAT' };
  return { cls: 'notrun', label: 'NOT RUN' };
}

function _histIssueIcon(iss) {
  const chip = _histIssueChip(iss);
  if (chip.cls === 'merged') {
    return '<span class="iss-icon ok"><i class="ti ti-check"></i></span>';
  }
  if (chip.cls === 'crashed') {
    return '<span class="iss-icon fail"><i class="ti ti-x"></i></span>';
  }
  return '<span class="iss-icon idle"></span>';
}

function _histProgressText(s) {
  const issues = Array.isArray(s.issues) ? s.issues : [];
  if (!issues.length) return '';
  const done = issues.filter(i => (i.state || '').toLowerCase() === 'merged').length;
  return `${done}/${issues.length} done`;
}

function _histLooseEndCount(s) {
  const r = s.reconciliation;
  if (r && Array.isArray(r.checks) && !r.all_clear) {
    const n = r.checks.filter(c => !c.ok).length;
    if (n) return n;
  }
  const g = _histStaleBySprint[s.label];
  if (g && g.count) return g.count;
  return 0;
}

function _histHeadStatsHtml(s) {
  const parts = [];
  const progress = _histProgressText(s);
  if (progress) parts.push(progress);
  const stats = _histRunStats[s.label];
  const agentSecs = stats && stats.has_runs && stats.agent_total_seconds != null
    ? stats.agent_total_seconds
    : null;
  if (agentSecs != null) {
    parts.push(_histFmtSecs(agentSecs) + ' agent');
  } else if (s.duration != null) {
    parts.push(_histFmtSecs(s.duration));
  }
  const looseN = _histLooseEndCount(s);
  if (looseN) parts.push(looseN + ' loose end' + (looseN !== 1 ? 's' : ''));
  if (!parts.length) return '';
  return '<span class="hist-head-stats">'
    + parts.map(p => '<span class="hist-head-stat">' + escHtml(p) + '</span>').join('')
    + '</span>';
}

function _histIssueLogUrl(s, issueNum) {
  const base = _histLogsUrl(s);
  if (issueNum == null) return base;
  const sep = base.includes('?') ? '&' : '?';
  return base + sep + 'issue=' + encodeURIComponent(String(issueNum)) + '&view=raw';
}

function _histFailedIssueMeta(ft, s) {
  const issues = Array.isArray(s.issues) ? s.issues : [];
  const hit = issues.find(i => String(i.ticket_id) === String(ft.ticket_id));
  return {
    title: _histIssueTitle(hit || ft, s),
    time: hit && hit.time_spent != null ? hit.time_spent : null,
  };
}

function _histFailReasonParts(ft, s) {
  const meta = _histFailedIssueMeta(ft, s);
  const reason = String(ft.failure_reason || 'Agent failed');
  let title = meta.title;
  let accent = reason;
  if (title && reason.toLowerCase().startsWith(title.toLowerCase())) {
    accent = reason.slice(title.length).replace(/^[\s·—-]+/, '').trim();
  } else if (!title) {
    accent = reason;
    title = '';
  }
  return { title: title, accent: accent, time: meta.time };
}

function _histEstBarMini(s) {
  const acc = s.estimate_accuracy;
  if (acc == null || isNaN(acc)) return '';
  const accPct = acc <= 1
    ? Math.round(acc * 100)
    : Math.round(Math.min(100, 100 / acc));
  const tone = acc > 1.25 ? 'var(--red)' : (acc > 1.0 ? 'var(--amber)' : 'var(--green)');
  const fill = Math.min(100, Math.max(10, accPct));
  return `<span class="hist-est-mini" title="Estimate accuracy ${Number(acc).toFixed(2)}×">
    <span class="hist-est-mini-fill" style="width:${fill}%;background:${tone}"></span>
    <span class="hist-est-mini-lbl">${accPct}%</span>
  </span>`;
}

// Resolve an issue title from the ledger row, falling back to the board's
// per-sprint ticket cache so History rows show titles even when the ledger
// (e.g. agent_runs-synthesized rows) carries only the number (hotfix #1).
function _histIssueTitle(iss, s) {
  if (iss.title) return String(iss.title);
  try {
    const tickets = (s && s.label && _smgmtBySprint[s.label]) || [];
    const hit = tickets.find(t => String(t.number) === String(iss.ticket_id));
    if (hit && hit.title) return String(hit.title);
  } catch (_) {}
  return '';
}

function _histIssueRowHtml(iss, isChild, s) {
  const chip = _histIssueChip(iss);
  const rerun = iss.is_rerun || iss.rerun || isChild;
  const arrow = rerun ? '<span class="iss-rerun">↳</span> ' : '';
  const num = iss.ticket_id;
  const id = num != null ? ('#' + num) : '#?';
  const titleText = _histIssueTitle(iss, s);
  const title = titleText ? `<span class="iss-title">${escHtml(titleText)}</span>` : '';
  // Clickable like the board: open the GitHub issue in a new tab. Guard
  // stopPropagation so it doesn't toggle the history card's expand state.
  const repo = (typeof _histRepo === 'function') ? _histRepo(s) : '';
  const clickable = (num != null && repo)
    ? ` role="link" tabindex="0" title="Open #${escHtml(String(num))} on GitHub"`
      + ` onclick="event.stopPropagation();window.open('https://github.com/${escHtml(repo)}/issues/${escHtml(String(num))}','_blank','noopener')"`
    : '';
  const cls = 'iss-row' + (clickable ? ' iss-row-link' : '');
  return `<div class="${cls}"${clickable}>
    ${_histIssueIcon(iss)}
    <span class="iss-id">${arrow}${escHtml(String(id))}</span>
    ${title}
    <span class="iss-chip ${chip.cls}">${chip.label}</span>
    <span class="iss-time">${escHtml(_histFmtSecs(iss.time_spent))}</span>
  </div>`;
}

// True when the sprint genuinely failed — not a successful natural end mis-tagged
// as needs_rework by a stale GitHub reconcile (issue #1137).
function _histSprintFailed(s) {
  const st = (s.lifecycle_state || '').toLowerCase();
  if (st === 'failed') return true;
  if (st !== 'needs_rework') return false;
  const er = (s.end_reason || '').toLowerCase();
  if (er === 'natural' || er === 'merge_sprint') return false;
  const failed = Array.isArray(s.failed_tickets) ? s.failed_tickets : [];
  if (failed.length) return true;
  const issues = Array.isArray(s.issues) ? s.issues : [];
  if (issues.length && issues.every(i =>
    (i.state || '').toLowerCase() === 'merged'
    || (i.agent_status || '').toLowerCase() === 'completed'
  )) return false;
  return true;
}

function _histFailedBlockHtml(s) {
  if (!_histSprintFailed(s)) return '';
  const state = (s.lifecycle_state || '').toLowerCase();
  const failed = Array.isArray(s.failed_tickets) ? s.failed_tickets : [];
  const sprintReason = s.failure_reason || s.end_reason;
  if (!failed.length && !sprintReason) return '';
  const items = failed.map(ft => {
    const id = ft.ticket_id != null ? ('#' + ft.ticket_id) : '#?';
    const reason = ft.failure_reason || 'Agent failed';
    return `<div class="hist-fail-item"><span class="fail-id">${escHtml(String(id))}</span><span>${escHtml(String(reason))}</span></div>`;
  }).join('');
  const summary = (!failed.length && sprintReason)
    ? `<div class="hist-fail-item"><span>${escHtml(String(sprintReason))}</span></div>`
    : '';
  return `<div class="hist-fail-block">
    <div class="fail-head"><i class="ti ti-alert-triangle"></i> Sprint failed</div>
    ${items}${summary}
  </div>`;
}

function _histPartialChildrenHtml(s) {
  const state = (s.lifecycle_state || '').toLowerCase();
  if (state !== 'partial_finished') return '';
  const children = Array.isArray(s.partial_children) ? s.partial_children : [];
  if (!children.length) return '';
  const links = children.map(c => {
    const lbl = escHtml(c);
    const display = (typeof sprintLabelDisplay === 'function') ? sprintLabelDisplay(c) : c;
    return `<button type="button" class="hist-partial-link" onclick="event.stopPropagation();_histFocusLabel('${lbl}')">${escHtml(display)}</button>`;
  }).join('');
  return `<div class="hist-partial-block">
    <i class="ti ti-arrows-split"></i>
    <span>Tickets moved to child sprint${children.length !== 1 ? 's' : ''}: ${links}</span>
  </div>`;
}

export function _histFocusLabel(label) {
  if (!label) return;
  _histExpanded.add(label);
  _histRenderLedger(_histLedgerData);
  const el = document.querySelector(`.hist-card[data-label="${CSS.escape(label)}"]`);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function _histIssueListHtml(s) {
  const issues = Array.isArray(s.issues) ? s.issues : [];
  const isChild = _histIsChild(s.label);
  const failedBlock = _histFailedBlockHtml(s);
  const partialBlock = _histPartialChildrenHtml(s);
  if (!issues.length) {
    if (partialBlock) return `${failedBlock}${partialBlock}`;
    const empty = failedBlock
      ? ''
      : `<div class="iss-list iss-list-empty">No tickets recorded for this sprint.</div>`;
    return `${failedBlock}${empty}`;
  }
  return `${failedBlock}${partialBlock}<div class="iss-list">${issues.map(i => _histIssueRowHtml(i, isChild, s)).join('')}</div>`;
}

// Re-run / Finish / Delete render on COMPLETED or FAILED only; Resume on FAILED
// only; locked (finished/deleted) sprints render no verbs at all (links only).
function _histVerbsHtml(s) {
  // Verbs gated by the unified lifecycle (sprint-lifecycle.md):
  //   needs_rework     → Re-run (creates a child sub-sprint)
  //   ready_to_merge   → Finish (merge sign-off) + Delete
  //   completed        → Finish + Delete
  //   partial_finished → none (waiting on children)
  if (_histIsLocked(s.lifecycle_state)) return '';
  const state = (s.lifecycle_state || '').toLowerCase();
  const lbl = escHtml(s.label || '');
  const rawLabel = s.label || '';
  let html = '';
  if (state === 'needs_rework' || state === 'failed' || state === 'cancelled') {
    const rerunDisabled = _smgmtAnySprintRunning ? 'disabled' : '';
    const rerunTitle = _smgmtAnySprintRunning
      ? 'title="Cannot re-run: another sprint is currently running."'
      : '';
    const childDisplay = sprintLabelDisplay(_histNextChildLabel(rawLabel)).replace('Sprint ', '');
    html += `<button class="hist-verb hist-verb--rerun" ${rerunDisabled} ${rerunTitle}
               onclick="_histRerunSprint('${lbl}')">
               <i class="ti ti-refresh"></i> Re-run → ${escHtml(childDisplay)}</button>`;
  } else if (state === 'completed' || state === 'ready_to_merge') {
    html += `<button class="hist-verb hist-verb--finish" onclick="smgmtFinishSprint('${lbl}')">
               <i class="ti ti-circle-check"></i> Complete</button>`;
    html += `<button class="hist-verb hist-verb--delete" onclick="smgmtDeleteSprint('${lbl}')">
               <i class="ti ti-trash"></i> Delete</button>`;
  }
  if (!html) return '';
  return `<div class="hist-verbs">${html}</div>`;
}

// Link targets. The PR pill opens the GitHub PR; the Summary pill opens the
// sprint's summary issue (or markdown file); Logs deep-links into the Logs tab.
function _histRepo(s) {
  const cached = _cachedFullRepo[_slug];
  if (cached) return cached;
  const p = (s && s.project) ? String(s.project) : '';
  return p.includes('/') ? p : '';
}
function _histPrUrl(s) {
  if (s.pr_number == null) return '';
  const repo = _histRepo(s);
  if (!repo) return '';
  return `https://github.com/${repo}/pull/${s.pr_number}`;
}
function _histSummaryIssueUrl(s) {
  if (s.summary_issue_url) return s.summary_issue_url;
  if (s.summary_issue_num == null) return '';
  const repo = _histRepo(s);
  if (!repo) return '';
  return `https://github.com/${repo}/issues/${s.summary_issue_num}`;
}
function _histSummaryUrl(s) {
  return _histSummaryIssueUrl(s);
}
function _histLogsUrl(s) {
  return `/project/${encodeURIComponent(_slug)}/logs?sprint=${encodeURIComponent(s.label || '')}`;
}

function _histLinksHtml(s) {
  let html = '';
  const pr = _histPrUrl(s);
  if (pr) {
    html += `<a class="hist-link link-pr" href="${escHtml(pr)}" target="_blank" rel="noopener">
               <i class="ti ti-git-pull-request"></i> PR #${s.pr_number}</a>`;
  }
  const sum = _histSummaryUrl(s);
  if (sum) {
    const sumLabel = s.summary_issue_num
      ? `#${s.summary_issue_num} Summary`
      : 'Summary';
    html += `<a class="hist-link link-sum" href="${escHtml(sum)}" target="_blank" rel="noopener">
               <i class="ti ti-file-description"></i> ${escHtml(sumLabel)}</a>`;
  }
  html += `<a class="hist-link link-logs" href="${escHtml(_histLogsUrl(s))}">
             <i class="ti ti-list-details"></i> Logs</a>`;
  return `<div class="hist-links">${html}</div>`;
}

// Compact PR / summary pills on the card head — visible without expanding.
function _histHeadLinksHtml(s) {
  let html = '';
  const pr = _histPrUrl(s);
  if (pr) {
    html += `<a class="hist-head-link link-pr" href="${escHtml(pr)}" target="_blank" rel="noopener" onclick="event.stopPropagation()"
      title="Open sprint PR"><i class="ti ti-git-pull-request"></i> PR #${s.pr_number}</a>`;
  }
  const sum = _histSummaryUrl(s);
  if (sum) {
    const label = s.summary_issue_num ? `#${s.summary_issue_num}` : 'Summary';
    html += `<a class="hist-head-link link-sum" href="${escHtml(sum)}" target="_blank" rel="noopener" onclick="event.stopPropagation()"
      title="Open sprint summary issue"><i class="ti ti-file-description"></i> ${escHtml(label)}</a>`;
  }
  return html ? `<span class="hist-head-links">${html}</span>` : '';
}

// Compact header actions: PR / Summary / Logs + lifecycle verbs in one row.
function _histHeadActionsHtml(s) {
  let html = '';
  const pr = _histPrUrl(s);
  if (pr) {
    html += `<a class="hist-head-btn link-pr" href="${escHtml(pr)}" target="_blank" rel="noopener"
      onclick="event.stopPropagation()" title="Open sprint PR">
      <i class="ti ti-git-pull-request"></i> PR #${s.pr_number}</a>`;
  }
  const sum = _histSummaryUrl(s);
  if (sum) {
    const sumLabel = s.summary_issue_num ? `#${s.summary_issue_num}` : 'Summary';
    html += `<a class="hist-head-btn link-sum" href="${escHtml(sum)}" target="_blank" rel="noopener"
      onclick="event.stopPropagation()" title="Open sprint summary">
      <i class="ti ti-file-description"></i> ${escHtml(sumLabel)}</a>`;
  }
  html += `<a class="hist-head-btn" href="${escHtml(_histLogsUrl(s))}"
    onclick="event.stopPropagation()" title="Open sprint logs">
    <i class="ti ti-list-details"></i> Logs</a>`;

  if (!_histIsLocked(s.lifecycle_state)) {
    const state = (s.lifecycle_state || '').toLowerCase();
    const lbl = escHtml(s.label || '');
    const rawLabel = s.label || '';
    html += `<button type="button" class="hist-head-btn hist-head-btn--reconcile"
      onclick="event.stopPropagation();smgmtReconcileSprint('${lbl}')"
      title="Reconcile this sprint's DB state against GitHub truth">
      <i class="ti ti-git-compare"></i> Reconcile</button>`;
    if (state === 'needs_rework' || state === 'failed' || state === 'cancelled') {
      const rerunDisabled = _smgmtAnySprintRunning ? 'disabled' : '';
      const rerunTitle = _smgmtAnySprintRunning
        ? 'title="Cannot re-run: another sprint is currently running."'
        : '';
      const childDisplay = sprintLabelDisplay(_histNextChildLabel(rawLabel)).replace('Sprint ', '');
      html += `<button type="button" class="hist-head-btn hist-head-btn--rerun" ${rerunDisabled} ${rerunTitle}
        onclick="event.stopPropagation();_histRerunSprint('${lbl}')">
        <i class="ti ti-refresh"></i> Re-run → ${escHtml(childDisplay)}</button>`;
    } else if (state === 'completed' || state === 'ready_to_merge') {
      html += `<button type="button" class="hist-head-btn hist-head-btn--bulk"
        onclick="event.stopPropagation();smgmtFinishSprint('${lbl}')">
        <i class="ti ti-circle-check"></i> Complete</button>`;
      html += `<button type="button" class="hist-head-btn hist-head-btn--delete"
        onclick="event.stopPropagation();smgmtDeleteSprint('${lbl}')">
        <i class="ti ti-trash"></i> Delete</button>`;
    }
  }
  return html ? `<span class="hist-head-actions">${html}</span>` : '';
}

// Metrics row: an estimate-accuracy bar (actual ÷ estimated) plus duration and
// token badges, per mock v5.
function _histMetricsHtml(s) {
  const acc = s.estimate_accuracy;
  let bar = '';
  if (acc != null && !isNaN(acc)) {
    // 1.0× = on target. Fill is acc as a fraction of a 2× full-scale bar; an
    // over-run (>1) reads amber→red, on/under (≤1) reads green.
    const pct = Math.max(4, Math.min(100, (acc / 2) * 100));
    const tone = acc > 1.25 ? 'var(--red)' : (acc > 1.0 ? 'var(--amber)' : 'var(--green)');
    const label = Number(acc).toFixed(2) + '×';
    bar = `<div class="est-bar" title="Estimate accuracy ${label} (actual ÷ estimated)">
             <span class="est-bar-fill" style="width:${pct}%;background:${tone}"></span>
             <span class="est-bar-label">${escHtml(label)} est</span>
           </div>`;
  }
  const badges = [];
  if (s.duration != null) {
    badges.push(`<span class="hist-badge"><i class="ti ti-clock"></i> ${escHtml(_histFmtSecs(s.duration))}</span>`);
  }
  if (s.tokens != null) {
    badges.push(`<span class="hist-badge"><i class="ti ti-coin"></i> ${escHtml(_histFmtTokens(s.tokens))} tok</span>`);
  }
  const state = (s.lifecycle_state || '').toLowerCase();
  const issues = Array.isArray(s.issues) ? s.issues : [];
  if (state === 'partial_finished') {
    const n = (s.partial_children || []).length;
    if (n > 0) {
      badges.push(`<span class="hist-badge"><i class="ti ti-arrows-split"></i> ${n} child sprint${n !== 1 ? 's' : ''}</span>`);
    }
  } else {
    badges.push(`<span class="hist-badge"><i class="ti ti-ticket"></i> ${issues.length} tickets</span>`);
  }
  return `<div class="hist-metrics">${bar}${badges.join('')}</div>`;
}

export function _histStateChip(state, sprint) {
  // Unified lifecycle (docs/architecture/sprint-lifecycle.md). The server maps
  // legacy values (cancelled→needs_rework, failed→needs_rework, …) and derives
  // partial_finished from children's states, so no client-side inference here.
  const s = (state || 'unknown').toLowerCase();
  const er = sprint && sprint.end_reason ? String(sprint.end_reason).toLowerCase() : '';
  const displayState = (s === 'needs_rework' && (er === 'natural' || er === 'merge_sprint')
    && sprint && !_histSprintFailed(sprint))
    ? 'ready_to_merge'
    : s;
  const map = {
    completed:        ['completed', 'COMPLETED'],
    ready_to_merge:   ['ready_to_merge', 'READY TO MERGE'],
    needs_rework:     ['failed',    'FAILED'],
    partial_finished: ['partial',   'PARTIAL'],
    deleted:          ['deleted',   'DELETED'],
    running:          ['running',   'RUNNING'],
    draft:            ['planning',  'DRAFT'],
    planned:          ['planning',  'PLANNED'],
    finished:  ['finished',  'FINISHED'],
    failed:    ['failed',    'FAILED'],
    cancelled: ['failed',    'FAILED'],
    planning:  ['planning',  'DRAFT'],
  };
  const pair = map[displayState] || ['unknown', displayState];
  const reason = sprint && sprint.end_reason && _histSprintFailed(sprint)
    ? `<span class="hist-state-reason" title="${escHtml(String(sprint.end_reason))}">${escHtml(String(sprint.end_reason))}</span>`
    : '';
  return `<span class="hist-state ${pair[0]}">${pair[1]}</span>${reason}`;
}

// ── Run-stats block (issue #810) ────────────────────────────────────────────
// One stat chip: an icon, a label, and a mono value. Built as a helper so every
// chip lines up on the same grid.
function _histStatChip(icon, label, value, cls) {
  const prefix = label ? escHtml(label) + ' ' : '';
  return `<span class="stat-chip${cls ? ' ' + cls : ''}">
    <i class="ti ${icon}"></i>${prefix}<b>${escHtml(value)}</b></span>`;
}

// The split-bar: one width-proportional segment per agent, percentages summing
// to 100 (AC2), with a full-contrast legend beneath for accessible reading.
function _histSplitBarHtml(stats) {
  const split = Array.isArray(stats.split) ? stats.split : [];
  if (!split.length) return '';
  const segs = split.map(seg =>
    `<span class="split-seg split-seg--${escHtml(seg.agent)}" style="width:${seg.pct}%"
       title="${escHtml(seg.agent)} · ${seg.pct}% (${escHtml(_histFmtSecs(seg.seconds))})">${seg.pct}%</span>`
  ).join('');
  const legend = split.map(seg =>
    `<span class="split-legend-item"><span class="split-swatch split-swatch--${escHtml(seg.agent)}"></span>
       ${escHtml(seg.agent)} ${seg.pct}%</span>`
  ).join('');
  return `<div class="stats-block">
    <div class="stats-section-label">Agent time split</div>
    <div class="split-bar">${segs}</div>
    <div class="split-legend">${legend}</div>
  </div>`;
}

function _histShouldAutoExpand(s) {
  if (!s || !s.label) return false;
  const st = (s.lifecycle_state || '').toLowerCase();
  if (_histIsLocked(st)) return false;
  // Completed/partial rows stay collapsed; only actionable states open by default.
  return st === 'needs_rework' || st === 'failed' || st === 'ready_to_merge' || st === 'running';
}

// Base labels whose default group-collapse has already been applied, so a user
// who manually expands a completed parent isn't re-collapsed on the next render.
const _histCollapseDefaultsApplied = new Set();

function _histAutoExpandRecent(groups) {
  const _expand = (s) => {
    if (!_histShouldAutoExpand(s)) return;
    _histExpanded.add(s.label);
    _histLoadRunStats(s.label);
  };
  for (let i = 0; i < groups.length; i++) {
    const g = groups[i];
    const children = g.children || [];
    // Completed parent groups start collapsed by default (just the parent row) —
    // their detail is historical. Apply once per base label so manual expands
    // stick (#5). Only relevant to groups that actually have children.
    const baseLbl = g.baseLabel || (g.baseSprint && g.baseSprint.label) || '';
    if (children.length && baseLbl && !_histCollapseDefaultsApplied.has(baseLbl)) {
      _histCollapseDefaultsApplied.add(baseLbl);
      const st = ((g.baseSprint && g.baseSprint.lifecycle_state) || '').toLowerCase();
      if (st === 'completed') _histGroupCollapsed.add(baseLbl);
    }
    if (i >= _histFoldSize) continue;
    if (children.length) {
      // Re-run chain: collapse the parent and older children; expand only the
      // latest child (children are sub-ascending, so the last one) by default.
      _expand(children[children.length - 1]);
    } else {
      _expand(g.baseSprint);
    }
  }
}

/** Merge run-stats ticket rows with every issue listed on the history record. */
function _histMergeGanttTickets(s, stats) {
  const byNum = new Map();
  (Array.isArray(stats && stats.tickets) ? stats.tickets : []).forEach((t) => {
    byNum.set(String(t.ticket), t);
  });
  (Array.isArray(s.issues) ? s.issues : []).forEach((i) => {
    const id = i.ticket_id;
    if (id == null) return;
    const key = String(id);
    if (!byNum.has(key)) {
      byNum.set(key, { ticket: id, start: 0, end: 0, segments: [] });
    }
  });
  return Array.from(byNum.values()).sort((a, b) => {
    const sa = a.start ?? 0;
    const sb = b.start ?? 0;
    if (sa !== sb) return sa - sb;
    return Number(a.ticket) - Number(b.ticket);
  });
}

// The gantt: one row per sprint issue (union of history issues + agent_runs).
// Coder segments are purple, tester amber — fix rounds use the same solid colors.
// A red ✕ crash marker is painted at the crash ticket's end offset, but only
// for a sprint that actually failed (AC4).
function _histGanttHtml(s, stats) {
  const tickets = _histMergeGanttTickets(s, stats);
  if (!tickets.length) return '';
  const scale = Math.max(1, stats.wall_seconds || 0);
  const sprintFailed = _histSprintFailed(s);
  const crash = stats.crash;
  const rows = tickets.map(t => {
    const segs = (t.segments || []).map(seg => {
      const left  = (seg.start / scale) * 100;
      const width = (seg.duration / scale) * 100;
      const agentCls = seg.agent === 'tester' ? 'g-tester' : 'g-coder';
      const cls = 'g-seg ' + agentCls;
      const title = `${seg.agent}${seg.fix_round ? ' (fix round)' : ''} · ${_histFmtSecs(seg.duration)}`;
      return `<span class="${cls}" style="left:${left}%;width:${width}%" title="${escHtml(title)}"></span>`;
    }).join('');
    // The crash ✕ sits on its own ticket's row, at the segment-end offset.
    let marker = '';
    if (sprintFailed && crash && crash.ticket === t.ticket) {
      const at = (crash.offset / scale) * 100;
      marker = `<span class="g-crash" style="left:${at}%" title="Crashed here">✕</span>`;
    }
    return `<div class="g-row">
      <span class="g-label">#${escHtml(String(t.ticket))}</span>
      <span class="g-track">${segs}${marker}</span>
    </div>`;
  }).join('');
  return `<div class="stats-block">
    <div class="stats-section-label">Timeline</div>
    <div class="hist-gantt"><div class="gantt">${rows}</div></div>
  </div>`;
}

// The full run-stats block for an expanded card. Wall-time and token chips
// always render (sourced from the run-stats span when present, else the history
// feed), so the block is never empty. The agent-derived chips, split-bar and
// gantt render only when agent_runs rows exist (has_runs) — otherwise they are
// hidden with no error (AC7). Independent of lock state (AC6).
function _histStatsHtml(s) {
  const stats = _histRunStats[s.label];
  const hasRuns = !!(stats && stats.has_runs);

  // Wall time + tokens fall back to the history record when no runs are present.
  const wall = hasRuns && stats.wall_seconds != null ? stats.wall_seconds : s.duration;
  const tokens = hasRuns && stats.total_tokens != null ? stats.total_tokens : s.tokens;

  const sprintFailed = _histSprintFailed(s);

  const chips = [];
  if (wall != null) chips.push(_histStatChip('ti-clock', 'wall', _histFmtSecs(wall)));
  if (hasRuns) {
    chips.push(_histStatChip('ti-robot', 'agent time', _histFmtSecs(stats.agent_total_seconds)));
  }
  if (tokens != null) {
    let tokVal = _histFmtTokens(tokens);
    if (hasRuns && stats.token_cost_usd != null) tokVal += ' ≈ $' + Number(stats.token_cost_usd).toFixed(2);
    else tokVal += ' tok';
    chips.push(_histStatChip('ti-coin', 'tokens', tokVal));
  }
  if (hasRuns) {
    if (stats.fix_round_count > 0) {
      const refs = (stats.fix_round_tickets || []).map(n => '#' + n).join(', ');
      const word = stats.fix_round_count === 1 ? 'fix round' : 'fix rounds';
      chips.push(_histStatChip('ti-refresh', '',
        stats.fix_round_count + ' ' + word + (refs ? ' (' + refs + ')' : ''), 'stat-chip--fix'));
    }
    if (stats.slowest_ticket) {
      chips.push(_histStatChip('ti-hourglass-low', 'slowest',
        '#' + stats.slowest_ticket.ticket + ' · ' + _histFmtSecs(stats.slowest_ticket.seconds)));
    }
    if (stats.parallel_saved_seconds != null) {
      chips.push(_histStatChip('ti-arrows-split', 'parallel saved',
        '~' + _histFmtSecs(stats.parallel_saved_seconds)));
    }
    // issue #920: coder backend split chip (only when Cline was used)
    if (stats.coder_backend_split && stats.coder_backend_split.cline_count > 0) {
      const bs = stats.coder_backend_split;
      const parts = [];
      if (bs.cline_count > 0) parts.push('cline: ' + bs.cline_count + ' · ' + _histFmtSecs(bs.cline_seconds));
      if (bs.claude_code_count > 0) parts.push('claude-code: ' + bs.claude_code_count + ' · ' + _histFmtSecs(bs.claude_code_seconds));
      if (parts.length) chips.push(_histStatChip('ti-server', 'backend', parts.join(' | ')));
    }
    if (sprintFailed && stats.crash) {
      const failed = (Array.isArray(s.failed_tickets) ? s.failed_tickets : [])
        .find(ft => ft.ticket_id === stats.crash.ticket);
      const reason = failed ? String(failed.failure_reason || '') : '';
      const crashAgent = /tester/i.test(reason) ? 'tester' : 'coder';
      const tail = reason ? ' · ' + reason.split('\n')[0].slice(0, 40) : '';
      chips.push(_histStatChip('ti-alert-triangle', 'crash',
        '#' + stats.crash.ticket + ' · ' + crashAgent + tail, 'stat-chip--crash'));
    }
  }

  const splitHtml = hasRuns ? _histSplitBarHtml(stats) : '';
  return `<div class="stats" data-stats-label="${escHtml(s.label || '')}">
    <div class="stat-chips">${chips.join('')}</div>
    ${splitHtml}
  </div>`;
}

// Lazily fetch the run-stats aggregation for a sprint the first time its card is
// expanded; cache it and re-render so the block fills in. Failures leave the
// label uncached (so a later expand retries) and the wall/token chips still show.
async function _histLoadRunStats(label) {
  if (label in _histRunStats) return;   // already fetched (or in flight result)
  const repo = _cachedFullRepo[_slug];
  try {
    const url = '/api/sprints/' + encodeURIComponent(label) + '/run-stats'
              + (repo ? '?project=' + encodeURIComponent(repo) : '');
    const resp = await fetch(url);
    if (!resp.ok) return;
    _histRunStats[label] = await resp.json();
    _histScheduleLedgerRender();
  } catch (_) {
    /* transient — leave uncached so a later expand retries */
  }
}

function _histScheduleLedgerRender() {
  if (_histRenderRaf) return;
  _histRenderRaf = requestAnimationFrame(() => {
    _histRenderRaf = 0;
    _histRenderLedger(_histLedgerData);
  });
}

function _histShowLedgerSkeleton() {
  const el = document.getElementById('hist-ledger');
  if (!el || (_histLedgerData && _histLedgerData.length)) return;
  el.innerHTML = `<div class="hist-ledger-skeleton" aria-busy="true" aria-label="Loading sprint history">
    <div class="hist-skeleton-card"></div>
    <div class="hist-skeleton-card"></div>
    <div class="hist-skeleton-card"></div>
  </div>`;
}

// Post-sprint reconciliation (issue #856): the loose-ends checklist surfaced on
// the history card. Each check renders green (resolved) or red (unresolved) with
// its own explanation, so a failure says exactly what and why without GitHub.
function _histReconcileLabel(name) {
  return ({
    summary_issue: 'Summary issue',
    sprint_pr: 'Sprint PR',
    stale_labels: 'Status labels',
  })[name] || name;
}

function _histPostSprintChipHtml(s) {
  const ps = s.post_sprint;
  if (!ps) return '';
  const doc = ps.documenter;
  const rev = ps.reviewer;
  const docRan = doc && doc.status && doc.status !== 'skipped';
  const revRan = rev && rev.status && rev.status !== 'skipped';
  if (!docRan && !revRan) return '';
  const parts = [];
  if (docRan) parts.push('documenter');
  if (revRan) parts.push('reviewer');
  return `<span class="post-sprint-chip" title="${escHtml(ps.note || 'Post-sprint agents')}">
    <i class="ti ti-clock-play"></i> post-sprint</span>`;
}

function _histIssueUrl(num) {
  if (num == null) return '';
  const repo = _cachedFullRepo[_slug] || '';
  if (!repo) return '';
  return `https://github.com/${repo}/issues/${num}`;
}

function _histPostSprintHtml(s) {
  const ps = s.post_sprint;
  if (!ps) return '';
  const doc = ps.documenter;
  const rev = ps.reviewer;
  const docRan = doc && doc.status && doc.status !== 'skipped';
  const revRan = rev && rev.status && rev.status !== 'skipped';
  if (!docRan && !revRan) return '';

  let rows = '';

  if (doc) {
    let body = '';
    if (doc.status === 'skipped') {
      body = '<span class="ps-skipped">Skipped — nothing merged</span>';
    } else if (doc.status === 'failed') {
      body = '<span class="ps-skipped">Documenter failed</span>';
    } else if ((doc.files_touched || []).length) {
      body = (doc.files_touched || []).map(f =>
        `<code class="ps-file">${escHtml(String(f))}</code>`
      ).join('');
      if (doc.commit_sha) {
        body += `<div class="ps-meta">Commit ${escHtml(String(doc.commit_sha).slice(0, 8))}</div>`;
      }
    } else if (doc.status === 'succeeded') {
      body = '<span class="ps-skipped">No doc files changed</span>';
    }
    if (body) {
      rows += `<div class="ps-row"><span class="ps-label">Documenter</span><span class="ps-body">${body}</span></div>`;
    }
  }

  if (rev) {
    let body = '';
    if (rev.status === 'skipped') {
      body = '<span class="ps-skipped">Skipped</span>';
    } else if (rev.status === 'failed') {
      body = '<span class="ps-skipped">Reviewer failed</span>';
    } else {
      const tickets = rev.follow_up_tickets || [];
      if (tickets.length) {
        body = tickets.map(n => {
          const url = _histIssueUrl(n);
          const inner = `#${n}`;
          return url
            ? `<a class="ps-ticket" href="${escHtml(url)}" target="_blank" rel="noopener">${escHtml(inner)}</a>`
            : `<span class="ps-ticket">${escHtml(inner)}</span>`;
        }).join('');
      } else {
        body = '<span class="ps-skipped">No follow-up tickets opened</span>';
      }
      const counts = [];
      if (rev.blockers) counts.push(rev.blockers + ' blocker' + (rev.blockers !== 1 ? 's' : ''));
      if (rev.suggestions) counts.push(rev.suggestions + ' suggestion' + (rev.suggestions !== 1 ? 's' : ''));
      if (rev.nits) counts.push(rev.nits + ' nit' + (rev.nits !== 1 ? 's' : ''));
      if (counts.length) body += `<div class="ps-meta">${escHtml(counts.join(' · '))}</div>`;
      if (rev.comment_url) {
        body += `<div class="ps-meta"><a href="${escHtml(rev.comment_url)}" target="_blank" rel="noopener">Review comment ↗</a></div>`;
      }
    }
    if (body) {
      rows += `<div class="ps-row"><span class="ps-label">Reviewer</span><span class="ps-body">${body}</span></div>`;
    }
  }

  if (!rows) return '';
  const note = ps.note || 'Agents ran after ticket work finished';
  return `<div class="hist-post-sprint">
    <div class="ps-head"><i class="ti ti-clock-play"></i> ${escHtml(note)}</div>
    ${rows}
  </div>`;
}

function _histReconcileHtml(s) {
  const r = s.reconciliation;
  if (!r || !Array.isArray(r.checks) || !r.checks.length) return '';
  const allClear = !!r.all_clear;
  const items = r.checks.map(c => {
    const ok = !!c.ok;
    const icon = ok ? 'ti-circle-check' : 'ti-alert-triangle';
    const detail = c.detail || (ok ? 'OK' : 'unresolved');
    return `<div class="recon-item ${ok ? 'ok' : 'fail'}">
      <i class="ti ${icon}"></i>
      <span><span class="recon-label">${escHtml(_histReconcileLabel(c.name))}:</span>
        <span class="recon-detail">${escHtml(detail)}</span></span>
    </div>`;
  }).join('');
  const failed = r.checks.filter(c => !c.ok).length;
  const summary = allClear
    ? 'All clear — no loose ends'
    : (failed + (failed === 1 ? ' item unresolved' : ' items unresolved'));
  return `<div class="recon">
    <div class="recon-head ${allClear ? 'clear' : 'flagged'}">
      <i class="ti ${allClear ? 'ti-checks' : 'ti-alert-triangle'}"></i>
      Reconciliation <span class="recon-summary">· ${escHtml(summary)}</span>
    </div>
    ${items}
  </div>`;
}

function _histReconcileChipHtml(s) {
  const r = s.reconciliation;
  if (!r || !Array.isArray(r.checks) || !r.checks.length) return '';
  if (r.all_clear) {
    return `<span class="recon-chip clear" title="Post-sprint reconciliation: all clear">
      <i class="ti ti-checks"></i> clear</span>`;
  }
  const n = r.checks.filter(c => !c.ok).length;
  const word = n === 1 ? 'loose end' : 'loose ends';
  return `<span class="recon-chip flagged" title="Post-sprint reconciliation: ${n} ${word}">
    <i class="ti ti-alert-triangle"></i> ${n} ${word}</span>`;
}

// Header hint chips (kept for backward-compat; no longer used by _histCardHtml).
function _histHeadHintsHtml(s, expanded) {
  if (expanded) {
    return _histPostSprintChipHtml(s) + _histReconcileChipHtml(s) + _histStaleChipHtml(s);
  }
  let html = '';
  const r = s.reconciliation;
  if (r && Array.isArray(r.checks) && r.checks.length && !r.all_clear) {
    html += _histReconcileChipHtml(s);
  }
  html += _histStaleChipHtml(s);
  return html;
}

// ── Agent colour legend (history child-sprint mock) ─────────────────────────
export function _histLegendHtml() {
  return `<div class="hist-legend-top" aria-label="Agent colours">
    <span class="hist-legend-label">Agents</span>
    <span class="hist-legend-item"><span class="hist-swatch hist-swatch--coder"></span> coder</span>
    <span class="hist-legend-item"><span class="hist-swatch hist-swatch--tester"></span> tester</span>
    <span class="hist-legend-item"><span class="hist-swatch hist-swatch--documenter"></span> documenter</span>
    <span class="hist-legend-item"><span class="hist-swatch hist-swatch--reviewer"></span> reviewer</span>
    <span class="hist-legend-item"><span class="hist-swatch hist-swatch--fix"></span> fix round</span>
  </div>`;
}

function _histFixRoundSeconds(stats) {
  if (!stats) return 0;
  if (stats.fix_round_seconds != null) return Math.max(0, Number(stats.fix_round_seconds) || 0);
  let total = 0;
  for (const t of stats.tickets || []) {
    for (const seg of t.segments || []) {
      if (seg.fix_round) total += seg.duration || 0;
    }
  }
  return total;
}

function _histAgentSeconds(stats) {
  const raw = (stats && stats.agent_seconds) || {};
  return {
    coder: raw.coder || 0,
    tester: raw.tester || 0,
    documenter: raw.documenter || 0,
    reviewer: raw.reviewer || 0,
  };
}

function _histAgentBarSegments(stats) {
  if (!stats || !stats.has_runs) return [];
  const fixSecs = _histFixRoundSeconds(stats);
  const a = _histAgentSeconds(stats);
  const coderNet = Math.max(0, a.coder - fixSecs);
  const parts = [
    { key: "coder", seconds: coderNet, cls: "hist-bar-coder" },
    { key: "fix", seconds: fixSecs, cls: "hist-bar-fix" },
    { key: "tester", seconds: a.tester, cls: "hist-bar-tester" },
    { key: "documenter", seconds: a.documenter, cls: "hist-bar-documenter" },
    { key: "reviewer", seconds: a.reviewer, cls: "hist-bar-reviewer" },
  ].filter((p) => p.seconds > 0);
  const total = parts.reduce((n, p) => n + p.seconds, 0) || 1;
  return parts.map((p) => ({
    ...p,
    pct: Math.max(0.5, (p.seconds / total) * 100),
    label: _histFmtSecs(p.seconds),
  }));
}

function _histMetricsElapsedLabel(s, stats) {
  const hasRuns = !!(stats && stats.has_runs);
  const secs = hasRuns && stats.agent_total_seconds != null
    ? stats.agent_total_seconds
    : s.duration;
  if (secs == null) return "";
  return `${escHtml(_histFmtSecs(secs))} <small>elapsed</small>`;
}

function _histAgentTimeBarHtml(stats) {
  const segs = _histAgentBarSegments(stats);
  if (!segs.length) {
    return `<div class="hist-agent-bar hist-agent-bar--empty" aria-hidden="true"></div>`;
  }
  const inner = segs
    .map((seg) => {
      const showLabel = seg.key !== "fix" && seg.pct >= 8;
      return `<span class="hist-agent-bar-seg ${seg.cls}" style="width:${seg.pct}%"`
        + ` title="${escHtml(seg.label)}">${showLabel ? escHtml(seg.label) : ""}</span>`;
    })
    .join("");
  return `<div class="hist-agent-bar">${inner}</div>`;
}

function _histAgentBreakdownHtml(stats) {
  if (!stats || !stats.has_runs) return "";
  const fixSecs = _histFixRoundSeconds(stats);
  const a = _histAgentSeconds(stats);
  const coderNet = Math.max(0, a.coder - fixSecs);
  const rows = [
    { cls: "hist-swatch--coder", label: "coder", secs: coderNet },
    { cls: "hist-swatch--fix", label: "fix round", secs: fixSecs },
    { cls: "hist-swatch--tester", label: "tester", secs: a.tester },
    { cls: "hist-swatch--documenter", label: "documenter", secs: a.documenter },
    { cls: "hist-swatch--reviewer", label: "reviewer", secs: a.reviewer },
  ].filter((r) => r.secs > 0);
  if (!rows.length) return "";
  return `<div class="hist-agent-breakdown">${rows
    .map(
      (r) => `<span class="hist-agent-at"><span class="hist-swatch ${r.cls}"></span>`
        + `${escHtml(r.label)} <b>${escHtml(_histFmtSecs(r.secs))}</b></span>`,
    )
    .join("")}</div>`;
}

function _histMetricsChipsHtml(s, stats) {
  const hasRuns = !!(stats && stats.has_runs);
  const wall = hasRuns && stats.wall_seconds != null ? stats.wall_seconds : s.duration;
  const tokens = hasRuns && stats.total_tokens != null ? stats.total_tokens : s.tokens;
  const chips = [];
  if (wall != null) chips.push(`<span class="hist-metric-chip">wall ${_histFmtSecs(wall)}</span>`);
  if (hasRuns) {
    chips.push(
      `<span class="hist-metric-chip">agent time ${_histFmtSecs(stats.agent_total_seconds)}</span>`,
    );
  }
  if (tokens != null) {
    chips.push(`<span class="hist-metric-chip">tokens ${_histFmtTokens(tokens)}</span>`);
  }
  if (hasRuns && stats.fix_round_count > 0) {
    const refs = (stats.fix_round_tickets || []).map((n) => "#" + n).join(", ");
    chips.push(
      `<span class="hist-metric-chip hist-metric-chip--warn">${stats.fix_round_count} fix round`
        + (refs ? ` (${escHtml(refs)})` : "") + `</span>`,
    );
  }
  if (hasRuns && stats.slowest_ticket) {
    chips.push(
      `<span class="hist-metric-chip">slowest #${stats.slowest_ticket.ticket}`
        + ` · ${_histFmtSecs(stats.slowest_ticket.seconds)}</span>`,
    );
  }
  if (hasRuns && stats.parallel_saved_seconds != null) {
    chips.push(
      `<span class="hist-metric-chip">parallel saved ~${_histFmtSecs(stats.parallel_saved_seconds)}</span>`,
    );
  }
  return chips.length ? `<div class="hist-metric-chips">${chips.join("")}</div>` : "";
}

function _histTimelineRowsHtml(s, stats) {
  const tickets = _histMergeGanttTickets(s, stats);
  if (!tickets.length) return "";
  const scale = Math.max(1, stats.wall_seconds || 0);
  const rows = tickets.map((t) => {
    const dur = Math.max(0, (t.end || 0) - (t.start || 0));
    const fixN = (t.segments || []).filter((seg) => seg.fix_round).length;
    let durLabel = _histFmtSecs(dur || t.segments?.reduce((n, seg) => n + (seg.duration || 0), 0));
    if (fixN) durLabel += " · fix";
    const segs = (t.segments || [])
      .map((seg) => {
        const left = (seg.start / scale) * 100;
        const width = Math.max(0.5, (seg.duration / scale) * 100);
        const agentCls = seg.agent === "tester" ? "hist-tl-tester" : "hist-tl-coder";
        const cls = "hist-tl-seg " + agentCls;
        const title = `${seg.agent}${seg.fix_round ? " (fix round)" : ""} · ${_histFmtSecs(seg.duration)}`;
        return `<span class="${cls}" style="left:${left}%;width:${width}%" title="${escHtml(title)}"></span>`;
      })
      .join("");
    return `<div class="hist-tl-row">
      <span class="hist-tl-num">#${escHtml(String(t.ticket))}</span>
      <div class="hist-tl-track">${segs}</div>
      <span class="hist-tl-dur">${escHtml(durLabel)}</span>
    </div>`;
  }).join("");
  return `<div class="hist-tl-section"><div class="hist-sec-label">Timeline</div>${rows}</div>`;
}

function _histFixCountForIssue(issueNum, stats) {
  if (!stats || !stats.tickets) return 0;
  const hit = stats.tickets.find((t) => String(t.ticket) === String(issueNum));
  if (!hit) return 0;
  return (hit.segments || []).filter((seg) => seg.fix_round).length;
}

function _histDoneIssueRowHtml(iss, s, stats) {
  const num = iss.ticket_id;
  const id = num != null ? "#" + num : "#?";
  const titleText = _histIssueTitle(iss, s);
  const repo = _histRepo(s);
  const chip = _histIssueChip(iss);
  const crashed = chip.cls === "crashed";
  const clickable = num != null && repo
    ? ` role="link" tabindex="0" onclick="event.stopPropagation();window.open('https://github.com/${escHtml(repo)}/issues/${escHtml(String(num))}','_blank','noopener')"`
    : "";
  const icon = _histIssueIcon(iss);
  let dur = _histFmtSecs(iss.time_spent);
  const fixN = _histFixCountForIssue(num, stats);
  if (fixN) dur += ` · ${fixN} fix`;
  const reason = crashed && iss.failure_reason
    ? `<span class="hist-irow-reason">${escHtml(String(iss.failure_reason))}</span>`
    : "";
  const logHtml = crashed && num != null
    ? `<a class="hist-irow-log" href="${escHtml(_histIssueLogUrl(s, num))}" onclick="event.stopPropagation()" title="View issue log">Log →</a>`
    : "";
  return `<div class="hist-irow${crashed ? " hist-irow--failed" : ""}${clickable ? " hist-irow-link" : ""}"${clickable}>
    ${icon}
    <span class="hist-irow-main">
      <span class="hist-irow-line">
        <span class="hist-irow-num">${escHtml(String(id))}</span>
        <span class="hist-irow-title">${escHtml(titleText)}</span>
      </span>
      ${reason}
    </span>
    <span class="hist-irow-dur">${escHtml(dur)}</span>
    ${logHtml}
  </div>`;
}

function _histDoneIssuesHtml(s) {
  const issues = Array.isArray(s.issues) ? s.issues : [];
  if (!issues.length) return "";
  const stats = _histRunStats[s.label];
  return `<div class="hist-issue-rows">${issues.map((i) => _histDoneIssueRowHtml(i, s, stats)).join("")}</div>`;
}

/** Agent bar + ticket rows when the sprint has a per-issue run snapshot. */
function _histCardShowsDoneSummary(s) {
  const issues = Array.isArray(s.issues) ? s.issues : [];
  if (_histSprintFailed(s)) return issues.length > 0;
  const state = (s.lifecycle_state || "").toLowerCase();
  if (state === "needs_rework" || state === "partial_finished") {
    const unfinished = issues.filter((i) => (i.state || "").toLowerCase() !== "merged");
    if (unfinished.length) return false;
    // Every ticket merged — the work landed; the needs_rework / partial_finished
    // flag is only about fix-rounds or lineage, not unfinished work. Show the done
    // summary + per-ticket rows (previously these fell through to a final return
    // that excluded needs_rework, so a fully-merged sprint showed no issue list).
    return issues.length > 0;
  }
  return (
    state === "ready_to_merge"
    || state === "completed"
    || state === "running"
  );
}

function _histCardOutcomeHtml(s) {
  if (!_histCardShowsDoneSummary(s)) return "";
  return `${_histChildMetricsHtml(s)}${_histDoneIssuesHtml(s)}`;
}

const _histAgentTimeExpanded = new Set();
const _histMetricsDetailsExpanded = new Set();

function _histChildMetricsHtml(s) {
  const stats = _histRunStats[s.label];
  const metricsOpen = _histAgentTimeExpanded.has(s.label);
  const lbl = escHtml(s.label || "");
  const chev = metricsOpen ? "ti-chevron-down" : "ti-chevron-right";
  const barHtml = stats && stats.has_runs ? _histAgentTimeBarHtml(stats) : "";
  const elapsed = _histMetricsElapsedLabel(s, stats);
  const elapsedHtml = elapsed
    ? `<span class="hist-metrics-elapsed">${elapsed}</span>`
    : "";
  const body = metricsOpen
    ? `<div class="hist-metrics-body">
        ${_histAgentBreakdownHtml(stats)}
        ${_histMetricsChipsHtml(s, stats)}
        ${_histTimelineRowsHtml(s, stats)}
        ${_histPostSprintHtml(s)}
        ${_histReconcileHtml(s)}
      </div>`
    : "";
  return `<div class="hist-metrics-v2${metricsOpen ? " open" : ""}">
    <div class="hist-metrics-head" onclick="event.stopPropagation();_histToggleAgentTime('${lbl}')">
      <i class="ti ${chev} hist-chev"></i>
      <span class="hist-metrics-label">Agent time</span>
      ${barHtml}
      ${elapsedHtml}
    </div>
    ${body}
  </div>`;
}

function _histParentFromLabel(label) {
  const { base, sub } = _histLabelParts(label);
  if (!sub) return "";
  const display = sprintLabelDisplay(base).replace("Sprint ", "");
  return `← from ${display}`;
}

function _histParentRowHtml(s, bulkBtn, groupExpanded) {
  const display = sprintLabelDisplay(s.label);
  const lbl = escHtml(s.label || "");
  const chev = groupExpanded ? "ti-chevron-down" : "ti-chevron-right";
  // Recede a completed group when collapsed, like standalone cards — a group
  // parent (sprint with a rerun child) used a different class and never got the
  // settled grey, so a done group looked active next to greyed siblings.
  const cls = ["hist-parent-row"];
  if ((String(s.lifecycle_state || "").toLowerCase()) === "completed") cls.push("settled");
  if (groupExpanded) cls.push("expanded");
  return `<div class="${cls.join(" ")}" data-label="${lbl}" role="button" tabindex="0"
    onclick="_histToggleGroup('${lbl}')"
    onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();_histToggleGroup('${lbl}')}">
    <i class="ti ${chev} hist-chev" aria-hidden="true"></i>
    <span class="hist-parent-name">${escHtml(display)}</span>
    ${_histStateChip(s.lifecycle_state, s)}
    ${_histHeadStatsHtml(s)}
    <span class="hist-parent-actions" onclick="event.stopPropagation()">${bulkBtn || ""}${_histSecondaryLinksHtml(s)}</span>
  </div>`;
}

function _histChildCardHtml(s) {
  const expanded = _histExpanded.has(s.label);
  const lbl = escHtml(s.label || "");
  const state = (s.lifecycle_state || "").toLowerCase();
  const displayState = state === "needs_rework" && s.end_reason
    && (String(s.end_reason).toLowerCase() === "natural" || String(s.end_reason).toLowerCase() === "merge_sprint")
    && !_histSprintFailed(s)
    ? "ready_to_merge"
    : state;
  const cls = ["hist-child-card"];
  if (displayState === "ready_to_merge") cls.push("ready");
  if (displayState === "completed") cls.push("settled");
  if (expanded) cls.push("expanded");
  const display = sprintLabelDisplay(s.label);
  const fromLine = _histParentFromLabel(s.label);
  const chev = expanded ? "ti-chevron-down" : "ti-chevron-right";
  const recoveryBtn = _histRecoveryBtnHtml(s);
  const deleteBtn = _histDeleteBtnHtml(s);
  const secondaryLinks = _histSecondaryLinksHtml(s);
  const headRight = `<span class="hist-child-head-right">${secondaryLinks}${recoveryBtn}${deleteBtn}</span>`;

  if (expanded && !(s.label in _histRunStats)) _histLoadRunStats(s.label);

  const body = expanded
    ? `<div class="hist-child-body">
        ${_histLooseEndBandHtml(s)}
        ${_histCardOutcomeHtml(s)}
      </div>`
    : "";

  return `<div class="${cls.join(" ")}" data-label="${lbl}">
    <div class="hist-child-head" onclick="_histToggleCard('${lbl}')">
      <div class="hist-child-head-left">
        <i class="ti ${chev} hist-chev"></i>
        <span class="hist-child-title">${escHtml(display)}`
    + (fromLine ? ` <span class="hist-child-from">${escHtml(fromLine)}</span>` : "")
    + `</span>
        ${_histStateChip(s.lifecycle_state, s)}
        ${_histHeadStatsHtml(s)}
      </div>
      ${headRight}
    </div>
    ${body}
  </div>`;
}

// ── Loose-end amber band (issue #1041) ─────────────────────────────────────
// Exactly ONE amber band per card showing the single highest-priority actionable
// loose end. Priority: unresolved stale-labels recon check > stale branches.
// Returns '' when no loose end exists (no band rendered — AC4 / AC7).
function _histLooseEndBandHtml(s) {
  const r = s.reconciliation;
  if (r && Array.isArray(r.checks)) {
    const prCheck = r.checks.find((c) => !c.ok && c.name === "sprint_pr");
    if (prCheck) {
      const looseN = r.checks.filter((c) => !c.ok).length || 1;
      const prNum = s.pr_number;
      const prUrl = _histPrUrl(s);
      const prRef = prNum ? `#${prNum}` : "PR";
      const msg = `${looseN} loose end — Sprint PR ${prRef} is not yet merged`;
      const cta = prUrl
        ? `<a class="hist-band-cta" href="${escHtml(prUrl)}" target="_blank" rel="noopener"
            onclick="event.stopPropagation()">Merge PR</a>`
        : "";
      return `<div class="hist-loose-end-band">
        <i class="ti ti-alert-triangle"></i>
        <span class="hist-band-msg">${escHtml(msg)}</span>
        ${cta}
      </div>`;
    }
    const staleCheck = r.checks.find(c => !c.ok && c.name === 'stale_labels');
    if (staleCheck) {
      const offenders = Array.isArray(staleCheck.tickets) ? staleCheck.tickets : [];
      const looseN = r.checks.filter(c => !c.ok).length || 1;
      const offenderCount = offenders.length;
      const labelParts = offenderCount
        ? offenders.map(o => '#' + o.issue + ' ' + (o.labels || []).join(', ')).join(' · ')
        : (staleCheck.detail || 'Clear stale status labels');
      const msg = offenderCount
        ? (looseN + ' loose end — ' + offenderCount + ' stale status labels to clear: ' + labelParts)
        : (looseN + ' loose end — ' + labelParts);
      const lbl = escHtml(s.label || '');
      return `<div class="hist-loose-end-band">
        <i class="ti ti-alert-triangle"></i>
        <span class="hist-band-msg">${escHtml(msg)}</span>
        <button type="button" class="hist-band-cta hist-band-cta--clear"
          onclick="event.stopPropagation();_histClearStaleLabels('${lbl}')">Clear labels</button>
      </div>`;
    }
  }
  // Priority 2: stale feature branches
  const g = _histStaleBySprint[s.label];
  if (g && g.count) {
    const n = g.count;
    const word = n === 1 ? 'stale branch' : 'stale branches';
    const lbl = escHtml(s.label || '');
    return `<div class="hist-loose-end-band">
      <i class="ti ti-git-branch"></i>
      <span class="hist-band-msg">${n} ${word}</span>
      <button type="button" class="hist-band-cta"
        onclick="event.stopPropagation();_histCleanupStale('${lbl}')">Clean up</button>
    </div>`;
  }
  return '';
}

// ── What-list (issue #1041) ─────────────────────────────────────────────────
// Single-purpose issue list, one variant per card state — eliminates cross-section
// duplication (AC5/AC6/AC14):
//   actually-failed sprint  → "Why it failed" list from s.failed_tickets
//   partial / needs_rework  → "Unfinished N of M" list of unmerged issues
//   complete / locked       → '' (nothing to display — AC7)
function _histWhatListHtml(s) {
  if (_histIsLocked(s.lifecycle_state)) return '';
  const state = (s.lifecycle_state || '').toLowerCase();

  // Failed branch: crash summary header; per-ticket detail lives in issue rows (AC5/AC14)
  if (_histSprintFailed(s)) {
    const failed = Array.isArray(s.failed_tickets) ? s.failed_tickets : [];
    const issues = Array.isArray(s.issues) ? s.issues : [];
    const sprintReason = s.failure_reason || s.end_reason;
    if (!failed.length && !sprintReason) return "";
    const n = failed.length || 1;
    if (issues.length) {
      return `<div class="hist-what-list">
        <div class="hist-what-head hist-what-head--failed">Why it failed · ${n} issue${n !== 1 ? "s" : ""} crashed</div>
      </div>`;
    }
    const items = failed.map(ft => {
      const id = ft.ticket_id != null ? ('#' + ft.ticket_id) : '#?';
      const parts = _histFailReasonParts(ft, s);
      const titleHtml = parts.title
        ? `<span class="wl-title">${escHtml(parts.title)}</span>` : '';
      const accentHtml = parts.accent
        ? `<span class="wl-reason-accent">${escHtml(parts.accent)}</span>` : '';
      const timeHtml = parts.time != null
        ? `<span class="wl-time">${escHtml(_histFmtSecs(parts.time))}</span>` : '';
      const logHref = escHtml(_histIssueLogUrl(s, ft.ticket_id));
      return `<div class="wl-item">
        <span class="wl-icon fail"><i class="ti ti-x"></i></span>
        <span class="wl-main">
          <span class="wl-id">${escHtml(String(id))}</span>
          ${titleHtml}${accentHtml}
        </span>
        ${timeHtml}
        <a class="wl-log" href="${logHref}" onclick="event.stopPropagation()" title="View issue log">Log →</a>
      </div>`;
    }).join('');
    const summary = (!failed.length && sprintReason)
      ? `<div class="wl-item"><span class="wl-reason-accent">${escHtml(String(sprintReason))}</span></div>`
      : '';
    return `<div class="hist-what-list">
      <div class="hist-what-head hist-what-head--failed">Why it failed · ${n} issue${n !== 1 ? 's' : ''} crashed</div>
      ${items}${summary}
    </div>`;
  }

  // Partial branch: unfinished tickets, each listed ONCE (AC6)
  if (state === 'partial_finished' || state === 'needs_rework') {
    const issues = Array.isArray(s.issues) ? s.issues : [];
    const unfinished = issues.filter(i => (i.state || '').toLowerCase() !== 'merged');
    if (!unfinished.length) return '';
    const n = unfinished.length;
    const m = issues.length;
    const isChild = _histIsChild(s.label);
    return `<div class="hist-what-list">
      <div class="hist-what-head">Unfinished ${n} of ${m}</div>
      <div class="iss-list">${unfinished.map(i => _histIssueRowHtml(i, isChild, s)).join('')}</div>
    </div>`;
  }

  return '';
}

// Passed reconciliation checks rendered as small grey checkmarks inside Details
// only — never as green boxes outside Details (AC9).
function _histReconPassedHtml(s) {
  const r = s.reconciliation;
  if (!r || !Array.isArray(r.checks) || !r.checks.length) return '';
  const passed = r.checks.filter(c => !!c.ok);
  if (!passed.length) return '';
  const items = passed.map(c => {
    const detail = c.detail || 'OK';
    return `<span class="recon-passed-item"
      title="${escHtml(_histReconcileLabel(c.name) + ': ' + detail)}">
      <i class="ti ti-check"></i> ${escHtml(_histReconcileLabel(c.name))}</span>`;
  }).join('');
  return `<div class="hist-recon-passed">${items}</div>`;
}

// Metrics / reconciliation — collapsed by default at the bottom of an expanded card.
function _histDetailsHtml(s) {
  const expanded = _histMetricsDetailsExpanded.has(s.label);
  const lbl = escHtml(s.label || '');
  const chev = expanded ? 'ti-chevron-down' : 'ti-chevron-right';
  const body = expanded ? `<div class="hist-details-body">
      ${_histStatsHtml(s)}
      ${_histReconPassedHtml(s)}
    </div>` : '';
  return `<div class="hist-details${expanded ? ' expanded' : ''}">
    <div class="hist-details-head" onclick="event.stopPropagation();_histToggleMetrics('${lbl}')">
      <i class="ti ti-chart-bar hist-details-icon" aria-hidden="true"></i>
      <span class="hist-details-label">Metrics &amp; reconciliation</span>
      <i class="ti ${chev} hist-chev hist-details-chev" aria-hidden="true"></i>
    </div>
    ${body}
  </div>`;
}

export function _histToggleAgentTime(label) {
  if (_histAgentTimeExpanded.has(label)) {
    _histAgentTimeExpanded.delete(label);
  } else {
    _histAgentTimeExpanded.add(label);
    _histLoadRunStats(label);
  }
  _histRenderLedger(_histLedgerData);
}

export function _histToggleMetrics(label) {
  if (_histMetricsDetailsExpanded.has(label)) {
    _histMetricsDetailsExpanded.delete(label);
  } else {
    _histMetricsDetailsExpanded.add(label);
    _histLoadRunStats(label);
  }
  _histRenderLedger(_histLedgerData);
}

// Recovery CTA: one per card — failed → amber Re-run, ready_to_merge → Complete,
// complete/partial → none (partial uses bulk-complete at group level — AC2).
function _histRecoveryBtnHtml(s) {
  if (_histIsLocked(s.lifecycle_state)) return '';
  const state = (s.lifecycle_state || '').toLowerCase();
  const lbl = escHtml(s.label || '');
  const rawLabel = s.label || '';
  if (_histSprintFailed(s) || state === 'needs_rework' || state === 'failed' || state === 'cancelled') {
    const rerunDisabled = _smgmtAnySprintRunning ? 'disabled' : '';
    const rerunTitle = _smgmtAnySprintRunning
      ? 'title="Cannot re-run: another sprint is currently running."' : '';
    const childDisplay = sprintLabelDisplay(_histNextChildLabel(rawLabel)).replace('Sprint ', '');
    return `<button type="button" class="hist-head-btn hist-head-btn--rerun hist-head-btn--rerun-primary" ${rerunDisabled} ${rerunTitle}
      onclick="event.stopPropagation();_histRerunSprint('${lbl}')">
      <i class="ti ti-refresh"></i> Re-run → ${escHtml(childDisplay)}</button>`;
  }
  if (state === 'ready_to_merge') {
    return `<button type="button" class="hist-head-btn hist-head-btn--bulk"
      onclick="event.stopPropagation();smgmtFinishSprint('${lbl}')"
      title="Complete sprint — merge to develop">
      <i class="ti ti-circle-check"></i> Complete</button>`;
  }
  return '';
}

// Delete button: quiet icon only, no label, no red styling (AC11).
function _histDeleteBtnHtml(s) {
  if (_histIsLocked(s.lifecycle_state)) return '';
  const state = (s.lifecycle_state || '').toLowerCase();
  const actionable = new Set([
    'needs_rework', 'failed', 'cancelled', 'ready_to_merge', 'completed', 'partial_finished',
  ]);
  if (!actionable.has(state)) return '';
  const lbl = escHtml(s.label || '');
  return `<button type="button" class="hist-head-btn hist-head-btn--delete-icon"
    onclick="event.stopPropagation();smgmtDeleteSprint('${lbl}')"
    title="Delete sprint"><i class="ti ti-trash"></i></button>`;
}

// Secondary links: PR / Summary / Logs — visually subordinate to the recovery CTA (AC12).
function _histSecondaryLinksHtml(s) {
  let html = '';
  const pr = _histPrUrl(s);
  if (pr) {
    html += `<a class="hist-head-btn hist-head-btn--secondary link-pr"
      href="${escHtml(pr)}" target="_blank" rel="noopener"
      onclick="event.stopPropagation()" title="Open sprint PR">
      <i class="ti ti-git-pull-request"></i> PR #${s.pr_number}</a>`;
  }
  const sum = _histSummaryUrl(s);
  if (sum) {
    const sumLabel = s.summary_issue_num ? `#${s.summary_issue_num}` : 'Summary';
    html += `<a class="hist-head-btn hist-head-btn--secondary link-sum"
      href="${escHtml(sum)}" target="_blank" rel="noopener"
      onclick="event.stopPropagation()" title="Open sprint summary">
      <i class="ti ti-file-description"></i> ${escHtml(sumLabel)}</a>`;
  }
  html += `<a class="hist-head-btn hist-head-btn--secondary"
    href="${escHtml(_histLogsUrl(s))}"
    onclick="event.stopPropagation()" title="Open sprint logs">
    <i class="ti ti-list-details"></i> Logs</a>`;
  return html;
}

// ── Redesigned card builder (issue #1041) ───────────────────────────────────
// Section order: Header → Loose-end band → What-list → Stats / timeline
// AC1 / AC13 / AC14
function _histCardHtml(s, opts) {
  opts = opts || {};
  const locked   = _histIsLocked(s.lifecycle_state);
  const child    = _histIsChild(s.label);
  const expanded = _histExpanded.has(s.label);
  const cls = ['hist-card'];
  if (locked)   cls.push('locked');
  if (child)    cls.push('child');
  if (expanded) cls.push('expanded');
  const lifecycle = (s.lifecycle_state || '').toLowerCase();
  if (lifecycle === 'completed') cls.push('settled');
  if (lifecycle === 'ready_to_merge') cls.push('ready');

  const display = (typeof sprintLabelDisplay === 'function') ? sprintLabelDisplay(s.label) : (s.label || '');
  const chev = expanded ? 'ti-chevron-down' : 'ti-chevron-right';
  const lbl = escHtml(s.label || '');

  // Compact header stats: progress · duration · loose ends (mock)
  const headStatsHtml = _histHeadStatsHtml(s);

  const recoveryBtn = _histRecoveryBtnHtml(s);
  const bulkBtn = opts.bulkCompleteBtn || '';
  const deleteBtn = _histDeleteBtnHtml(s);
  const secondaryLinks = _histSecondaryLinksHtml(s);
  const headRight = (secondaryLinks || deleteBtn || bulkBtn || recoveryBtn)
    ? `<span class="hist-card-head-right">${secondaryLinks}${recoveryBtn}${deleteBtn}${bulkBtn}</span>`
    : '';

  if (expanded && !(s.label in _histRunStats)) _histLoadRunStats(s.label);

  // Body: loose-end band → what-list → done summary → stats / timeline
  const body = expanded ? `<div class="hist-card-body">
      ${_histLooseEndBandHtml(s)}
      ${_histWhatListHtml(s)}
      ${_histCardOutcomeHtml(s)}
      ${_histDetailsHtml(s)}
      ${locked ? _histLinksHtml(s) : ''}
    </div>` : '';

  return `<div class="${cls.join(' ')}" data-label="${lbl}">
    <div class="hist-card-head" onclick="_histToggleCard('${lbl}')">
      <div class="hist-card-head-left">
        <i class="ti ${chev} hist-chev"></i>
        ${child ? '<span class="hist-child-arrow">↳</span>' : ''}
        <span class="hist-card-title">${escHtml(display)}</span>
        ${_histStateChip(s.lifecycle_state, s)}
        ${headStatsHtml}
      </div>
      ${headRight}
    </div>
    ${body}
  </div>`;
}

export function _histToggleCard(label) {
  if (_histExpanded.has(label)) {
    _histExpanded.delete(label);
  } else {
    _histExpanded.add(label);
    // Expanding a card lazily loads its run-stats block (issue #810); cached
    // after the first fetch so subsequent expands are instant.
    _histLoadRunStats(label);
  }
  _histRenderLedger(_histLedgerData);
}

/** Collapse/expand a parent sprint group — hides all child sprint cards. */
export function _histToggleGroup(baseLabel) {
  if (!baseLabel) return;
  if (_histGroupCollapsed.has(baseLabel)) {
    _histGroupCollapsed.delete(baseLabel);
  } else {
    _histGroupCollapsed.add(baseLabel);
  }
  _histRenderLedger(_histLedgerData);
}

// ── Sub-sprint grouping ─────────────────────────────────────────────────────
// Re-run siblings (sprint-68.1, sprint-68.2, …) render nested under sprint-68.
// Top-level History order still follows the newest base-sprint activity first.
function _histLabelParts(label) {
  const m = /^sprint-(\d+)(?:\.(\d+))?$/.exec(label || '');
  if (!m) return { base: label || '', sub: 0, baseNum: 0 };
  return {
    base: `sprint-${m[1]}`,
    sub: m[2] ? parseInt(m[2], 10) : 0,
    baseNum: parseInt(m[1], 10),
  };
}

function _histGroupMembers(group) {
  const out = [];
  if (group.baseSprint) out.push(group.baseSprint);
  if (group.children) out.push(...group.children);
  return out;
}

function _histGroupSprints(sprints) {
  const byBase = new Map();
  const groupOrder = [];
  for (let i = 0; i < sprints.length; i++) {
    const s = sprints[i];
    const { base, sub } = _histLabelParts(s.label);
    if (!byBase.has(base)) {
      byBase.set(base, { baseSprint: null, children: [], order: i });
      groupOrder.push(base);
    }
    const g = byBase.get(base);
    if (sub === 0) g.baseSprint = s;
    else g.children.push(s);
    g.order = Math.min(g.order, i);
  }
  // Float groups that still need attention (a failed / needs_rework /
  // ready_to_merge / running member) to the top, then the rest by recency — so a
  // failed sprint like 65 sits above already-settled ones even when a sibling's
  // recent rerun would otherwise pull its group up.
  const _UNSETTLED = new Set(["needs_rework", "failed", "cancelled", "ready_to_merge", "running"]);
  const _groupUnsettled = (g) => [g.baseSprint, ...(g.children || [])]
    .filter(Boolean)
    .some(s => _UNSETTLED.has((s.lifecycle_state || "").toLowerCase()));
  groupOrder.sort((a, b) => {
    const ua = _groupUnsettled(byBase.get(a)) ? 0 : 1;
    const ub = _groupUnsettled(byBase.get(b)) ? 0 : 1;
    if (ua !== ub) return ua - ub;
    return byBase.get(a).order - byBase.get(b).order;
  });
  return groupOrder.map(baseLabel => {
    const g = byBase.get(baseLabel);
    g.children.sort((a, b) => _histLabelParts(a.label).sub - _histLabelParts(b.label).sub);
    return { baseLabel, baseSprint: g.baseSprint, children: g.children };
  });
}

function _histGroupNeedsBulkComplete(group) {
  const children = group.children || [];
  if (!children.length || !group.baseSprint) return false;
  const members = [group.baseSprint, ...children];
  return members.some(s => {
    const st = (s.lifecycle_state || '').toLowerCase();
    return st !== 'completed' && st !== 'deleted';
  });
}

function _histChildSprintsAllCompleted(group) {
  const children = group.children || [];
  if (!children.length) return false;
  const settled = new Set(['completed', 'deleted', 'ready_to_merge']);
  // A child counts as ready when its own run settled, OR a later sibling (higher
  // sub-index — children are sorted ascending) has. The latter means this run was
  // superseded by a rerun that itself settled, so the middle ancestor staying
  // needs_rework must not dead-end the button. Mirrors the server's recursive
  // _bulk_complete_lineage_settled gate, which already accepts this case.
  return children.every((s, i) => {
    if (settled.has((s.lifecycle_state || '').toLowerCase())) return true;
    return children.slice(i + 1).some(
      later => settled.has((later.lifecycle_state || '').toLowerCase()),
    );
  });
}

function _histBulkCompleteBtnHtml(group) {
  if (!group.children?.length || !group.baseSprint) return '';
  if (!_histGroupNeedsBulkComplete(group)) return '';
  const lbl = escHtml(group.baseLabel || '');
  const childrenReady = _histChildSprintsAllCompleted(group);
  if (!childrenReady) {
    return `<button type="button" class="hist-head-btn hist-head-btn--bulk" disabled
            title="Complete all child sprints before bulk completing">
      <i class="ti ti-circle-check"></i> Bulk complete
    </button>`;
  }
  return `<button type="button" class="hist-head-btn hist-head-btn--bulk"
          onclick="event.stopPropagation();smgmtBulkCompleteSprint('${lbl}')"
          title="Complete parent and all child sprints">
    <i class="ti ti-circle-check"></i> Bulk complete
  </button>`;
}

function _histGroupHtml(group) {
  const bulkBtn = _histBulkCompleteBtnHtml(group);
  const children = group.children || [];
  if (children.length) {
    const baseLbl = group.baseLabel || (group.baseSprint && group.baseSprint.label) || "";
    const groupExpanded = baseLbl ? !_histGroupCollapsed.has(baseLbl) : true;
    const groupCls = groupExpanded ? "hist-sprint-group" : "hist-sprint-group collapsed";
    const parentRow = group.baseSprint
      ? _histParentRowHtml(group.baseSprint, bulkBtn, groupExpanded)
      : "";
    // Render the parent's OWN tickets (the ones it still owns after re-runs
    // moved some to children) under its row when the group is expanded — a
    // parent used to show no issue list at all, hiding its completed tickets.
    const parentOwnIssues = (group.baseSprint && Array.isArray(group.baseSprint.issues))
      ? group.baseSprint.issues : [];
    const parentBody = (groupExpanded && parentOwnIssues.length)
      ? `<div class="hist-parent-body">${_histIssueListHtml(group.baseSprint)}</div>`
      : "";
    const childHtml = children.map((c) => _histChildCardHtml(c)).join("");
    return `<div class="${groupCls}" data-group="${escHtml(baseLbl)}">${parentRow}${parentBody}<div class="hist-child-wrap">${childHtml}</div></div>`;
  }
  if (group.baseSprint) {
    return _histIsChild(group.baseSprint.label)
      ? _histChildCardHtml(group.baseSprint)
      : _histCardHtml(group.baseSprint, { bulkCompleteBtn: bulkBtn });
  }
  return "";
}

// ── Folding (issue #807) ────────────────────────────────────────────────────
// The integer sprint number parsed out of a "sprint-NN" (or "sprint-NN.M"
// rerun-child) label — used to build the "Sprints X–Y" fold range.
function _histSprintNum(label) {
  return _histLabelParts(label).baseNum || null;
}

// A "done" ticket is a merged ticket; count them across a sprint's issue list.
function _histTicketsDone(s) {
  const issues = Array.isArray(s.issues) ? s.issues : [];
  return issues.filter(i => (i.state || '').toLowerCase() === 'merged').length;
}

// Split the (newest-first) sprint feed into the `foldSize` most-recent sprints
// (rendered expanded) plus a list of older folds, each holding exactly
// `foldSize` sprints. Both the recent slice and every fold are sized by the
// same `history_fold_size` setting (AC2/AC8).
function _histPartition(sprints, foldSize) {
  foldSize = Math.max(1, foldSize | 0);
  const recent = sprints.slice(0, foldSize);
  const older = sprints.slice(foldSize);
  const folds = [];
  for (let i = 0; i < older.length; i += foldSize) {
    const group = older.slice(i, i + foldSize);
    const nums = group.map(g => _histSprintNum(g.label)).filter(n => n != null);
    folds.push({
      id: 'fold-' + i,
      sprints: group,
      from: nums.length ? Math.min(...nums) : null,
      to: nums.length ? Math.max(...nums) : null,
    });
  }
  return { recent, folds };
}

// Same as _histPartition but counts base-sprint groups (sub-sprints ride along).
function _histPartitionGroups(groups, foldSize) {
  foldSize = Math.max(1, foldSize | 0);
  const recent = groups.slice(0, foldSize);
  const older = groups.slice(foldSize);
  const folds = [];
  for (let i = 0; i < older.length; i += foldSize) {
    const chunk = older.slice(i, i + foldSize);
    const nums = chunk.map(g => _histLabelParts(g.baseLabel).baseNum).filter(n => n > 0);
    folds.push({
      id: 'fold-' + i,
      groups: chunk,
      sprints: chunk.flatMap(_histGroupMembers),
      from: nums.length ? Math.min(...nums) : null,
      to: nums.length ? Math.max(...nums) : null,
    });
  }
  return { recent, folds };
}

// Aggregate a fold group: tickets done (Σ merged), avg estimation accuracy
// (mean of the sprints that recorded one), and failure count (Σ failed
// sprints) — the three metrics surfaced on the fold head (AC4).
function _histFoldAgg(group) {
  let done = 0, failed = 0, accSum = 0, accN = 0;
  group.forEach(s => {
    done += _histTicketsDone(s);
    const _fst = (s.lifecycle_state || '').toLowerCase();
    if (_fst === 'needs_rework' || _fst === 'failed') failed += 1;
    const acc = s.estimate_accuracy;
    if (acc != null && !isNaN(acc)) { accSum += Number(acc); accN += 1; }
  });
  return { done, failed, avgAcc: accN ? (accSum / accN) : null, count: group.length };
}

function _histFoldAggHtml(group) {
  const a = _histFoldAgg(group);
  const acc = a.avgAcc != null ? (Number(a.avgAcc).toFixed(2) + '×') : '—';
  return `<div class="fold-agg">
    <span class="fold-stat"><i class="ti ti-checks"></i> ${a.done} done</span>
    <span class="fold-stat"><i class="ti ti-gauge"></i> ${escHtml(acc)} avg est</span>
    <span class="fold-stat fold-stat--fail"><i class="ti ti-alert-triangle"></i> ${a.failed} failed</span>
  </div>`;
}

// A collapsed fold row ("Sprints X–Y" + aggregate metrics). Clicking the head
// expands it; member sprints then render through the SAME _histCardHtml builder
// so they are visually identical to recent rows — locked/normal states intact
// (AC5). Collapsing removes the fold from _histFoldExpanded, hiding the rows.
function _histFoldHtml(fold) {
  const open = _histFoldExpanded.has(fold.id);
  const chev = open ? 'ti-chevron-down' : 'ti-chevron-right';
  const range = (fold.from != null && fold.to != null)
    ? (fold.from === fold.to ? ('Sprint ' + fold.from) : ('Sprints ' + fold.from + '–' + fold.to))
    : ('Sprints · ' + fold.sprints.length);
  const fid = escHtml(fold.id);
  const nums = (fold.groups || [])
    .map(g => _histLabelParts(g.baseLabel).baseNum)
    .filter(n => n > 0);
  const chips = !open && nums.length
    ? `<div class="fold-chip-row" onclick="event.stopPropagation();_histToggleFold('${fid}')">`
      + nums.map(n => `<span class="fold-num-chip">${n}</span>`).join('')
      + '</div>'
    : '';
  const body = open
    ? `<div class="fold-body">${(fold.groups || []).map(_histGroupHtml).join('')}</div>`
    : '';
  return `<div class="fold ${open ? 'expanded' : ''}" data-fold="${fid}">
    <div class="fold-head" onclick="_histToggleFold('${fid}')">
      <i class="ti ${chev} fold-chev"></i>
      <span class="fold-title">${open ? escHtml(range) : 'Older sprints'}</span>
      ${open ? `<span class="fold-count">${fold.sprints.length} sprints</span>` : ''}
      ${chips}
      <span class="fold-spacer"></span>
      ${_histFoldAggHtml(fold.sprints)}
    </div>
    ${body}
  </div>`;
}

export function _histToggleFold(id) {
  if (_histFoldExpanded.has(id)) _histFoldExpanded.delete(id);
  else _histFoldExpanded.add(id);
  _histRenderLedger(_histLedgerData);
}

// Toolbar note above the ledger explaining the fold behaviour (mock v5 AC10).
function _histToolbarHtml() {
  return `<div class="hist-toolbar">
    <span class="hist-toolbar-note">
      <i class="ti ti-stack-2"></i>
      Latest ${_histFoldSize} sprint groups expanded below — older groups collapse to sprint numbers; click to open details.
    </span>
  </div>`;
}

// ── Stale-branch scan + cleanup (issue #808) ────────────────────────────────
// The amber chip on a History row. Count is the total leftover feature branches
// mapped to that sprint (merged + unmerged); cleanup deletes only the merged
// ones. Rendered for locked rows too — scan/cleanup are repo actions, not
// record changes (AC7). Clicking "clean up" must not toggle the card, hence
// stopPropagation.
function _histStaleChipHtml(s) {
  const g = _histStaleBySprint[s.label];
  if (!g || !g.count) return '';
  const n = g.count;
  const word = n === 1 ? 'stale branch' : 'stale branches';
  const lbl = escHtml(s.label || '');
  return `<button class="stale-chip" title="${n} leftover feature ${n === 1 ? 'branch' : 'branches'} for this sprint"
    onclick="event.stopPropagation(); _histCleanupStale('${lbl}')">
    <i class="ti ti-git-branch"></i> ${n} ${word} · clean up</button>`;
}

// Scan the remote for stale feature/<N>-* branches and attach amber chips to the
// History rows whose sprint owns them (AC1/AC3/AC4).
export async function _histScanStale() {
  const repo = _cachedFullRepo[_slug];
  if (!repo) return;
  const btn = document.getElementById('ps-stale-scan-btn');
  if (btn) { btn.disabled = true; }
  try {
    const resp = await fetch('/scan-stale-branches?repo=' + encodeURIComponent(repo));
    if (resp.ok) {
      const data = await resp.json();
      _histStaleBySprint = data.by_sprint || {};
      if (_histLedgerData && _histLedgerData.length) {
        _histRenderLedger(_histLedgerData);
      }
    }
  } catch (_) {
    /* leave existing chips untouched on a transient failure */
  } finally {
    const b = document.getElementById('ps-stale-scan-btn');
    if (b) { b.disabled = false; }
  }
}

// Clean up a row's stale branches: dry-run first to learn exactly what will be
// deleted vs skipped (AC2), show a confirm dialog listing both (AC5/AC8), then
// delete only the merged ones on confirm. Unmerged branches are never deleted;
// after a successful delete the chip drops the removed branches and disappears
// once the row has no stale branches left (AC6).
export async function _histClearStaleLabels(label) {
  const repo = _cachedFullRepo[_slug];
  if (!repo) return;
  const s = (_histLedgerData || []).find(x => x.label === label);
  if (!s || !s.reconciliation) return;
  const staleCheck = (s.reconciliation.checks || []).find(c => !c.ok && c.name === 'stale_labels');
  if (!staleCheck) return;
  const tickets = Array.isArray(staleCheck.tickets) ? staleCheck.tickets : [];
  try {
    const resp = await fetch('/api/sprints/' + encodeURIComponent(label) + '/clear-stale-labels', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project: repo, tickets: tickets }),
    });
    if (resp.ok) {
      await _histLoadLedger();
    }
  } catch (_) {
    /* leave band in place on transient failure */
  }
}

export async function _histCleanupStale(label) {
  const repo = _cachedFullRepo[_slug];
  const g = _histStaleBySprint[label];
  if (!repo || !g) return;
  const branches = g.branches || [];

  let plan;
  try {
    const resp = await fetch('/cleanup-stale-branches', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo, branches, confirm: false }),
    });
    if (!resp.ok) return;
    plan = await resp.json();
  } catch (_) { return; }

  const toDelete = plan.to_delete || [];
  const skipped = plan.skipped_unmerged || [];
  if (!toDelete.length && !skipped.length) return;

  let msg = toDelete.length
    ? ('Delete ' + toDelete.length + ' merged branch' + (toDelete.length !== 1 ? 'es' : '')
       + '?\n\n' + toDelete.join('\n'))
    : 'No merged branches to delete for this sprint.';
  if (skipped.length) {
    msg += '\n\nSkipped (unmerged — never deleted):\n' + skipped.join('\n');
  }
  if (!confirm(msg)) return;
  if (!toDelete.length) return;   // dialog already surfaced the skipped list

  try {
    const resp = await fetch('/cleanup-stale-branches', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo, branches, confirm: true }),
    });
    if (!resp.ok) return;
    const result = await resp.json();
    const deleted = new Set(result.deleted || []);
    const remaining = (g.branches || []).filter(b => !deleted.has(b));
    if (remaining.length) {
      g.branches = remaining;
      g.merged = (g.merged || []).filter(b => !deleted.has(b));
      g.unmerged = (g.unmerged || []).filter(b => !deleted.has(b));
      g.count = remaining.length;
    } else {
      delete _histStaleBySprint[label];
    }
    _histRenderLedger(_histLedgerData);
  } catch (_) {
    /* network error during delete — leave chip as-is for a retry */
  }
}

export function _histRenderLedger(sprints) {
  const el = document.getElementById('hist-ledger');
  if (!el) return;
  if (!sprints || !sprints.length) {
    el.innerHTML = `<div class="hist-ledger-empty">No sprint history yet — finished and deleted sprints appear here.</div>`;
    return;
  }
  // Group re-run sub-sprints under their base sprint, then partition by fold
  // size on base groups (AC2/AC3) — sub-sprints (.1, .2) are not top-level rows.
  const groups = _histGroupSprints(sprints);
  if (!_histDidAutoExpand && groups.length) {
    _histAutoExpandRecent(groups);
    _histDidAutoExpand = true;
  }
  const { recent, folds } = _histPartitionGroups(groups, _histFoldSize);
  const recentHtml = recent.map(_histGroupHtml).join('');
  const foldsHtml = folds.map(_histFoldHtml).join('');
  el.innerHTML = _histLegendHtml() + _histToolbarHtml() + recentHtml + foldsHtml;
}

// Re-run from History uses the shared re-run modal (ticket pick) then pre-run
// kickoff — same flow as the sprint board Re-run button.
export function _histRerunSprint(label) {
  if (typeof globalThis.smgmtRerunSprint === 'function') {
    return globalThis.smgmtRerunSprint(label);
  }
}

export function _histPrefetchLedger(repo) {
  if (!repo) return;
  const hasData = (_histLedgerData || []).length > 0;
  const fresh = repo === _histLedgerCacheRepo
    && (Date.now() - _histLedgerCacheAt) < _HIST_LEDGER_TTL_MS
    && hasData;
  if (fresh || _histLedgerInflight) return;
  _histLoadLedger(repo, { background: true });
}

export async function _histLoadLedger(repo, opts) {
  opts = opts || {};
  if (!repo) return;
  const el = document.getElementById('hist-ledger');
  const force = opts.force === true;
  const background = opts.background === true;
  const hasCache = repo === _histLedgerCacheRepo && (_histLedgerData || []).length > 0;
  const fresh = !force && hasCache
    && (Date.now() - _histLedgerCacheAt) < _HIST_LEDGER_TTL_MS;

  if (fresh) {
    if (!background && el && !el.querySelector('.hist-card, .hist-sprint-group, .hist-fold')) {
      _histRenderLedger(_histLedgerData);
    }
    return;
  }

  if (_histLedgerInflight) {
    await _histLedgerInflight;
    if (!background && (_histLedgerData || []).length) {
      _histRenderLedger(_histLedgerData);
      _smgmtUpdateSubnav();
    }
    return;
  }

  if (!hasCache && !background) _histShowLedgerSkeleton();
  else if (hasCache && !background) _histRenderLedger(_histLedgerData);

  const loadPromise = (async () => {
    try {
      const settingsUrl = `/api/projects/${encodeURIComponent(_slug)}/settings`;
      // active_only is the default fast feed; "Show completed" loads the full set.
      const activeParam = _histShowClosed ? '' : '&active_only=1';
      const historyUrl = '/api/sprints/history?limit=50' + activeParam
        + '&project=' + encodeURIComponent(repo || '');
      const [sresp, resp] = await Promise.all([
        fetch(settingsUrl).catch(() => null),
        fetch(historyUrl),
      ]);
      if (sresp && sresp.ok) {
        try {
          const settings = await sresp.json();
          const fs = parseInt(settings.history_fold_size, 10);
          if (!isNaN(fs) && fs > 0) _histFoldSize = fs;
          const ttlMin = parseFloat(settings.history_cache_ttl_min);
          if (!isNaN(ttlMin) && ttlMin > 0) _HIST_LEDGER_TTL_MS = ttlMin * 60000;
        } catch (_) { /* keep current fold size / ttl */ }
      }
      if (!resp.ok) {
        if (!hasCache && el && !background) {
          el.innerHTML = `<div class="hist-ledger-empty">Could not load sprint history.</div>`;
        }
        return;
      }
      const data = await resp.json();
      const sprints = data.sprints || [];
      _histLedgerData = sprints;
      globalThis._histLedgerData = sprints;
      _histLedgerCacheRepo = repo;
      _histLedgerCacheAt = Date.now();
      const histOpen = document.getElementById('smgmt-subview-history')?.classList.contains('show');
      if (!background || histOpen) {
        _histRenderLedger(sprints);
        _smgmtUpdateSubnav();
      }
    } catch (_) {
      if (!hasCache && el && !background) {
        el.innerHTML = `<div class="hist-ledger-empty">Could not load sprint history.</div>`;
      }
    } finally {
      if (_histLedgerInflight === loadPromise) _histLedgerInflight = null;
    }
  })();

  _histLedgerInflight = loadPromise;
  await loadPromise;
}

// Reflect the current view mode on the toolbar's "Show completed" button.
function _histSyncShowClosedBtn() {
  const btn = document.getElementById('hist-show-closed-btn');
  if (!btn) return;
  btn.innerHTML = _histShowClosed
    ? '<i class="ti ti-eye-off"></i> Active only'
    : '<i class="ti ti-history"></i> Show completed';
  btn.title = _histShowClosed
    ? 'Show only actionable sprints + a few recent completed'
    : 'Load the full closed-sprint history';
}

// Toggle between the fast active-only feed and the full closed history.
export function _histToggleShowClosed() {
  _histShowClosed = !_histShowClosed;
  _histSyncShowClosedBtn();
  const repo = _cachedFullRepo[_slug];
  if (repo) _histLoadLedger(repo, { force: true });
}

// Set the ledger cache TTL (minutes) from the global settings control.
export function _histSetTtlMin(min) {
  const m = parseFloat(min);
  if (!isNaN(m) && m > 0) _HIST_LEDGER_TTL_MS = m * 60000;
}

// Manual "Refresh all" — drop every cache and refetch the current view.
export function _histForceRefresh() {
  _histResetLedgerCache();
  for (const k of Object.keys(_histRunStats)) delete _histRunStats[k];
  _histStaleBySprint = {};
  const repo = _cachedFullRepo[_slug];
  if (repo) _histLoadLedger(repo, { force: true });
}

function _histNextChildLabel(parentLabel) {
  return _nextSprintSublabel(parentLabel);
}
