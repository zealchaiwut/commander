// ── State ─────────────────────────────────────────────────────────────────────
let currentFilter    = 'all';
let allProjects      = [];
let expandedProjects = new Set(); // repos currently expanded
let detailsCache     = {};        // repo → detail data
let testReportCache  = {};        // `${repo}#${issueNum}` → report data
let doneAgentsVisible = {};       // repo → bool (toggle state for DONE agents, AC-2d)
let _uatTicketsByRepo = {};       // repo → UAT ticket list (populated when expand panel renders)
let _approveAllUatRepo = null;    // repo currently targeted by the approve-all modal

// ── Build stamp cache (issue #329) ────────────────────────────────────────────
let _buildStampCache = null; // fetched once per session from /api/version

// ── Router state ──────────────────────────────────────────────────────────────
let _activeProject    = null;   // "owner/repo" when in project view
let _activeProjectTab = 'sprint-mgmt'; // 'sprint-mgmt' | 'sprint-history' | 'tickets' (hidden)

// ── Agent name parser ────────────────────────────────────────────────────────
// New format: "role·repo·branch·#short"   (4 parts, · separator)
// Old format: plain basename              (graceful fallback)
function _parseAgentName(raw) {
  const parts = (raw || '').split('·');
  if (parts.length === 4) {
    return { role: parts[0], repo: parts[1], branch: parts[2], shortSess: parts[3], isNew: true };
  }
  return { role: 'agent', repo: raw || '?', branch: '', shortSess: '', isNew: false };
}

function _roleBadgeClass(role) {
  return ['coder', 'tester', 'ba'].includes(role) ? `role-${role}` : 'role-agent';
}

// ── Utility ───────────────────────────────────────────────────────────────────
function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function timeAgo(isoStr) {
  if (!isoStr) return 'never';
  const s = Math.floor((Date.now() - new Date(isoStr.endsWith('Z') ? isoStr : isoStr + 'Z')) / 1000);
  if (s < 60)    return `${s}s ago`;
  if (s < 3600)  return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function shortTime(isoStr) {
  if (!isoStr) return '';
  const d   = new Date(isoStr);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) {
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

// ── Theme ─────────────────────────────────────────────────────────────────────
function initTheme() {
  const saved = localStorage.getItem('theme') || 'light';
  document.documentElement.setAttribute('data-theme', saved);
  _syncThemeIcon(saved);
}

function toggleTheme() {
  const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
  _syncThemeIcon(next);
}

function _syncThemeIcon(theme) {
  const icon = document.getElementById('theme-icon');
  if (icon) icon.className = theme === 'dark' ? 'ti ti-sun' : 'ti ti-moon';
}

// ── Tab navigation ────────────────────────────────────────────────────────────
function switchMain(tab) {
  // Hide project view when switching to a global tab
  document.getElementById('view-project')?.classList.add('hidden');
  document.getElementById('global-nav')?.classList.remove('hidden');

  ['overview', 'agents', 'activity', 'sprint'].forEach(t => {
    document.getElementById(`view-${t}`)?.classList.toggle('hidden', t !== tab);
    document.getElementById(`mtab-${t}`)?.classList.toggle('active', t === tab);
  });

  if (tab === 'agents')   fetchAgents();
  if (tab === 'activity') fetchEvents();
  if (tab === 'sprint')   scRefresh();

  if (tab === 'overview') {
    history.pushState({ view: 'overview' }, '', '/');
  }
}

// ── Project-scoped navigation ─────────────────────────────────────────────────
function navigateToOverview() {
  history.pushState({ view: 'overview' }, '', '/');
  _showOverview();
}

function _showOverview() {
  _activeProject = null;
  document.getElementById('view-project')?.classList.add('hidden');
  document.getElementById('view-overview')?.classList.remove('hidden');
  document.getElementById('view-agents')?.classList.add('hidden');
  document.getElementById('view-activity')?.classList.add('hidden');
  document.getElementById('view-sprint')?.classList.add('hidden');

  ['overview', 'agents', 'activity', 'sprint'].forEach(t => {
    document.getElementById(`mtab-${t}`)?.classList.toggle('active', t === 'overview');
  });
}

function drillIntoProject(repo, tab) {
  tab = tab || 'sprint-mgmt';
  _activeProject = repo;
  _activeProjectTab = tab;

  const encoded = encodeURIComponent(repo);
  history.pushState({ view: 'project', repo, tab }, '', `/projects/${encoded}/${tab}`);
  _renderProjectView(repo, tab);
}

function switchProject(repo) {
  if (!repo) return;
  drillIntoProject(repo, _activeProjectTab);
}

function switchProjectTab(tab) {
  _activeProjectTab = tab;
  const encoded = encodeURIComponent(_activeProject);
  history.pushState({ view: 'project', repo: _activeProject, tab }, '', `/projects/${encoded}/${tab}`);
  _activateProjectTab(tab);
}

function _renderProjectView(repo, tab) {
  if (!repo) return;

  // Hide all global views
  ['overview', 'agents', 'activity'].forEach(t => {
    document.getElementById(`view-${t}`)?.classList.add('hidden');
    document.getElementById(`mtab-${t}`)?.classList.remove('active');
  });

  // Show project view
  document.getElementById('view-project')?.classList.remove('hidden');

  // Update project header
  _updateProjectHeader(repo);

  // Activate the right tab
  _activateProjectTab(tab);
}

function _updateProjectHeader(repo) {
  const proj = allProjects.find(p => p.repo === repo);
  const name = proj?.name || repo.split('/')[1] || repo;

  // Breadcrumb
  document.getElementById('pvh-proj-name').textContent = name;

  // Icon
  const iconEl = document.getElementById('pvh-icon');
  if (iconEl && proj) {
    iconEl.style.background = proj.color || '#6b7280';
    iconEl.innerHTML = `<i class="ti ${escapeHtml(proj.icon || 'ti-folder')}"></i>`;
  }

  // Title & sprint
  document.getElementById('pvh-title').textContent = name;
  const sprintEl = document.getElementById('pvh-sprint');
  if (sprintEl) {
    sprintEl.textContent = proj?.current_sprint
      ? `Sprint ${proj.current_sprint}${proj.sprint_theme ? ' · ' + proj.sprint_theme : ''}`
      : '';
  }

  // Project picker
  const picker = document.getElementById('proj-picker');
  if (picker && allProjects.length > 0) {
    picker.innerHTML = allProjects.map(p =>
      `<option value="${escapeHtml(p.repo)}" ${p.repo === repo ? 'selected' : ''}>${escapeHtml(p.name)}</option>`
    ).join('');
  }
}

function _activateProjectTab(tab) {
  ['tickets', 'sprint-mgmt', 'sprint-history'].forEach(t => {
    document.getElementById(`pview-${t}`)?.classList.toggle('hidden', t !== tab);
    document.getElementById(`ptab-${t}`)?.classList.toggle('active', t === tab);
  });

  if (tab === 'tickets')        { smgmtClearSelectionOnNav(); _loadProjectTickets(_activeProject); }
  if (tab === 'sprint-mgmt')    smgmtInitForProject(_activeProject);
  if (tab === 'sprint-history') { smgmtClearSelectionOnNav(); loadSprintHistory().catch(() => {}); }
}

function _loadProjectTickets(repo) {
  const container = document.getElementById('pview-tickets-content');
  if (!container) return;
  if (!repo) { container.innerHTML = '<div class="empty">No project selected.</div>'; return; }

  if (detailsCache[repo]) {
    _renderProjectTickets(repo, detailsCache[repo]);
    return;
  }
  container.innerHTML = '<div class="empty">Loading…</div>';
  fetch(`/api/project-details?repo=${encodeURIComponent(repo)}`)
    .then(r => r.ok ? r.json() : Promise.reject(r))
    .then(data => {
      detailsCache[repo] = data;
      _renderProjectTickets(repo, data);
    })
    .catch(() => {
      container.innerHTML = '<div class="empty">Failed to load tickets.</div>';
    });
}

function _renderProjectTickets(repo, data) {
  const container = document.getElementById('pview-tickets-content');
  if (!container) return;

  const tickets = data.tickets || [];
  const agents  = data.agents  || [];
  const ghUrl   = data.github_url || `https://github.com/${repo}/issues`;

  // Tickets column
  let ticketsHtml;
  if (tickets.length === 0) {
    ticketsHtml = '<div class="empty-small">No open tickets</div>';
  } else {
    const sitT    = tickets.filter(t => t.status === 'SIT');
    const uatT    = tickets.filter(t => t.status === 'UAT');
    const activeT = tickets.filter(t => t.status === 'in-progress' || t.status === 'blocked');
    const backlogT = tickets.filter(t => t.status === 'backlog');
    ticketsHtml = [
      _ticketGroupHtml('SIT',         sitT,     repo),
      _ticketGroupHtml('UAT',         uatT,     repo),
      _ticketGroupHtml('In progress', activeT,  repo),
      _ticketGroupHtml('Backlog',     backlogT, repo),
    ].join('');
  }

  // Agents column
  const projData     = allProjects.find(p => p.repo === repo) || null;
  const miniSprint   = _miniSprintSummaryHtml(projData);
  const workingAgents = agents.filter(a => a.status === 'working');
  const doneAgents    = agents.filter(a => a.status === 'done');
  const showDone      = !!doneAgentsVisible[repo];
  const id            = _projId(repo);

  const doneToggleStyle = doneAgents.length > 0 ? 'cursor:pointer;text-decoration:underline dotted;' : '';
  const doneLabel = `<span id="done-toggle-${id}" style="${doneToggleStyle}"
    onclick="${doneAgents.length > 0 ? `toggleDoneAgents('${id}','${escapeHtml(repo)}')` : ''}">
    done (${doneAgents.length})</span>`;

  let agentsHtml = '';
  if (workingAgents.length === 0 && !showDone) {
    agentsHtml = '<div class="empty-small">No active agents</div>';
  } else {
    agentsHtml = workingAgents.map(agentDetailCardHtml).join('');
  }
  if (showDone && doneAgents.length > 0) {
    agentsHtml += doneAgents.map(agentDetailCardHtml).join('');
  }

  const tokLine = data.tokens_today > 0
    ? `<div class="agent-detail-meta" style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">Tokens today: ${data.tokens_today.toLocaleString()}${data.cost_today_usd != null ? ` · ~$${data.cost_today_usd.toFixed(2)}` : ''}</div>`
    : '';

  container.innerHTML = `
    <div class="pview-tickets-main">
      ${miniSprint}
      <div class="expand-hdr">
        <span class="expand-hdr-title">Active tickets</span>
        <a class="view-all" href="${escapeHtml(ghUrl)}" target="_blank" rel="noopener">View all →</a>
      </div>
      ${ticketsHtml}
    </div>
    <div class="pview-tickets-aside">
      <div class="expand-hdr">
        <span class="expand-hdr-title">AGENTS · working (${workingAgents.length}) · ${doneLabel}</span>
      </div>
      ${agentsHtml}
      ${tokLine}
    </div>`;

  // Load test reports for UAT tickets
  tickets.filter(t => t.is_uat).forEach(t => loadTestReport(t.number, repo));
}

// ── URL Router ────────────────────────────────────────────────────────────────
function _route() {
  const path = window.location.pathname;

  // Match /projects/<encoded-repo>/<tab>
  const m = path.match(/^\/projects\/([^/]+)\/?([^/]*)?$/);
  if (m) {
    const repo    = decodeURIComponent(m[1]);
    const rawTab  = m[2] || '';
    // Redirect stale 'tickets' deep-link to sprint-mgmt; unknown/empty → sprint-mgmt
    const tab = (rawTab === 'sprint-history') ? 'sprint-history'
              : (rawTab === 'sprint-mgmt')    ? 'sprint-mgmt'
              : 'sprint-mgmt'; // covers '', 'tickets', or any unknown segment
    if (rawTab === 'tickets' || (!rawTab && path.includes('/projects/'))) {
      // Replace stale URL silently so the address bar reflects the active tab
      const encoded = encodeURIComponent(repo);
      history.replaceState({ view: 'project', repo, tab }, '', `/projects/${encoded}/${tab}`);
    }
    _activeProject    = repo;
    _activeProjectTab = tab;
    // If projects not yet loaded, defer render until after load
    if (allProjects.length > 0) {
      _renderProjectView(repo, tab);
    }
    // else _route() will be called again after loadProjects resolves
    return;
  }

  // Default: overview
  _showOverview();
}

window.addEventListener('popstate', e => {
  const s = e.state;
  if (s?.view === 'project') {
    _activeProject    = s.repo;
    _activeProjectTab = s.tab || 'sprint-mgmt';
    _renderProjectView(s.repo, s.tab || 'sprint-mgmt');
  } else {
    smgmtClearSelectionOnNav();
    _showOverview();
  }
});

// Clear multi-select when clicking outside the sprint-mgmt ticket lists (issue #206)
document.addEventListener('click', e => {
  if (_smgmtSelectedIssues.size === 0) return;
  // Only act when the sprint-mgmt view is active
  const smgmtView = document.getElementById('pview-sprint-mgmt');
  if (!smgmtView || smgmtView.classList.contains('hidden')) return;
  // If the click is inside a ticket list area or on a checkbox, do nothing
  const inTickets = e.target.closest('#smgmt-body, #smgmt-backlog-tickets');
  if (inTickets) return;
  // If it's inside a modal or the bulk bar, do nothing
  if (e.target.closest('.modal, #smgmt-bulk-bar, #smgmt-moveto-modal')) return;
  smgmtClearSelection();
});

// ── Filter ────────────────────────────────────────────────────────────────────
function setFilter(filter) {
  currentFilter = filter;
  ['all', 'active', 'review'].forEach(f => {
    document.getElementById(`ftab-${f}`).classList.toggle('active', f === filter);
  });
  renderProjects(allProjects);
}

// ── Metrics ───────────────────────────────────────────────────────────────────
function renderMetrics(metrics) {
  const setEl = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };

  setEl('m-sprints',         metrics.active_sprints  ?? '—');
  setEl('m-active-projects', metrics.active_projects ?? '—');
  setEl('m-open-tickets',    metrics.open_tickets    ?? '—');

  // Tokens Today
  const tokVal = metrics.tokens_today;
  const tokEl  = document.getElementById('m-tokens');
  const subEl  = document.getElementById('m-tokens-sub');
  if (tokEl) tokEl.textContent = tokVal != null ? tokVal.toLocaleString() : '—';
  if (subEl) {
    const cost = metrics.cost_today_usd;
    subEl.textContent = cost != null ? `~$${cost.toFixed(2)} est.` : '~$0.00 est.';
  }

  const working     = metrics.working_agents ?? 0;
  const openTix     = metrics.open_tickets   ?? 0;
  const activeSprints = metrics.active_sprints ?? 0;
  document.getElementById('header-subtitle').textContent = working > 0
    ? `${working} agent${working !== 1 ? 's' : ''} working · ${openTix} open tickets`
    : `${openTix} open tickets · ${activeSprints} active sprint${activeSprints !== 1 ? 's' : ''}`;
}

// ── Project list ──────────────────────────────────────────────────────────────
function _projId(repo) {
  return repo.replace('/', '-');
}

function agentPillsHtml(agents) {
  // AC-2a: show only WORKING agents in row summary pills
  const working = (agents || []).filter(a => a.status === 'working');
  if (working.length === 0) {
    return '<span class="agent-pill no-agent">no agents</span>';
  }
  const MAX     = 2;
  const visible = working.slice(0, MAX);
  const extra   = working.length - MAX;
  let html = visible.map(a => {
    const parsed = _parseAgentName(a.name);
    const label  = parsed.isNew ? parsed.role : parsed.repo;
    return `<span class="agent-pill working">${escapeHtml(label)}</span>`;
  }).join('');
  if (extra > 0) html += `<span class="agent-pill overflow">+${extra}</span>`;
  return html;
}

function projectRowHtml(proj) {
  const id            = _projId(proj.repo);
  const colorHex      = proj.color || '#6b7280';
  const hasActiveSprint = proj.has_active_sprint === true;
  const progress      = proj.progress    || { closed: 0, total: 0, pct: 0 };
  const eta           = proj.eta         || { value: 'TBD', sub: 'no data', status: 'idle' };
  const bar           = proj.bar_status  || 'idle';
  const etaSt         = eta.status       || 'idle';
  const openCount     = proj.openCount   || 0;
  const activeCount   = proj.activeCount || 0;
  const uatCount      = proj.uatCount    || 0;

  const sprintLine = proj.current_sprint
    ? `<div class="proj-sprint-line">
         <span class="sprint-tag">Sprint ${proj.current_sprint}</span>
         ${proj.sprint_theme ? `<span class="sprint-theme">${escapeHtml(proj.sprint_theme)}</span>` : ''}
       </div>`
    : `<div class="proj-sprint-line">
         <span class="no-sprint-text">${openCount} open ticket${openCount !== 1 ? 's' : ''}</span>
       </div>`;

  // Only show progress bar and ETA when a sprint is active
  const progHtml = hasActiveSprint
    ? (progress.total > 0
        ? `<div class="proj-col-progress">
             <div class="prog-header">
               <span class="prog-lbl">${progress.closed}/${progress.total} closed</span>
               <span class="prog-pct">${progress.pct}%</span>
             </div>
             <div class="prog-track"><div class="prog-fill ${bar}" style="width:${progress.pct}%"></div></div>
           </div>`
        : `<div class="proj-col-progress">
             <div class="prog-header">
               <span class="prog-lbl">No sprint tickets</span>
               <span class="prog-pct">—</span>
             </div>
             <div class="prog-track"><div class="prog-fill idle" style="width:0%"></div></div>
           </div>`)
    : `<div class="proj-col-progress"></div>`;

  const etaHtml = hasActiveSprint
    ? `<div class="proj-col-eta">
         <span class="eta-val ${etaSt}">${escapeHtml(eta.value)}</span>
         <span class="eta-sub ${etaSt}">${escapeHtml(eta.sub)}</span>
       </div>`
    : `<div class="proj-col-eta"></div>`;

  // AC-3: inline chips — active ticket count + awaiting UAT count
  // AC-4: UAT chip highlights amber when count > 0
  const uatAlertClass = uatCount > 0 ? ' chip-uat-alert' : '';
  const chipsHtml = `
    <div class="proj-chips">
      <span class="proj-chip chip-active" title="Active tickets (in-progress · SIT · UAT)">
        <i class="ti ti-activity"></i>${activeCount} active
      </span>
      <span class="proj-chip chip-uat${uatAlertClass}" title="Awaiting UAT review">
        <i class="ti ti-checkbox"></i>${uatCount} UAT
      </span>
    </div>`;

  // clicking navigates to project-scoped view
  return `
    <div class="project-block" id="proj-block-${id}">
      <div class="project-row" id="proj-row-${id}"
           data-repo="${escapeHtml(proj.repo)}" data-id="${id}"
           onclick="toggleProject(this.dataset.id, this.dataset.repo)"
           title="Open ${escapeHtml(proj.name)}">
        <div class="proj-col-name">
          <div class="proj-icon" style="background:${colorHex}">
            <i class="ti ${escapeHtml(proj.icon || 'ti-folder')}"></i>
          </div>
          <div class="proj-info">
            <a class="proj-title proj-title-link" href="https://github.com/${escapeHtml(proj.repo)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${escapeHtml(proj.name)}</a>
            ${sprintLine}
          </div>
        </div>
        ${progHtml}
        ${etaHtml}
        <div class="proj-col-agents">
          ${chipsHtml}
          ${agentPillsHtml(proj.agents)}
        </div>
        <div class="proj-col-chevron"><i class="ti ti-chevron-right chevron-icon"></i></div>
      </div>
    </div>`;
}

function renderProjects(projects) {
  allProjects = projects;

  // AC-6: keep log panel project filter in sync
  llpUpdateProjectFilter(projects);

  // Refresh project header & picker if currently in project view
  if (_activeProject) {
    _updateProjectHeader(_activeProject);
  }

  const container = document.getElementById('project-list');

  let filtered = projects;
  if (currentFilter === 'active') {
    filtered = projects.filter(p => p.current_sprint && p.openCount > 0);
  } else if (currentFilter === 'review') {
    filtered = projects.filter(p => p.uatCount > 0);
  }

  if (filtered.length === 0) {
    container.innerHTML = '<div class="empty-projects">No projects match this filter.</div>';
    return;
  }

  container.innerHTML = filtered.map(projectRowHtml).join('');
}

// ── Expand / collapse → navigate to project view ──────────────────────────────
function toggleProject(id, repo) {
  drillIntoProject(repo, 'tickets');
}

async function loadProjectDetails(id, repo) {
  try {
    const res = await fetch(`/api/project-details?repo=${encodeURIComponent(repo)}`);
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    detailsCache[repo] = data;
    renderExpandPanel(id, data, repo);
  } catch {
    const el = document.getElementById(`proj-detail-${id}`);
    if (el) el.innerHTML = '<div class="expand-loading">Failed to load details.</div>';
  }
}

// ── Mini sprint summary (AC-7 — issue #82) ────────────────────────────────────
function _miniSprintSummaryHtml(proj) {
  if (!proj || !proj.has_active_sprint) return '';
  const progress    = proj.progress    || { closed: 0, total: 0, pct: 0 };
  const bar         = proj.bar_status  || 'idle';
  const label       = proj.sprint_label || (proj.current_sprint ? `sprint-${proj.current_sprint}` : '');
  const uatCount    = proj.uatCount    || 0;
  const activeCount = proj.activeCount || 0;
  const doneCount   = progress.closed  || 0;
  const pct         = progress.pct     || 0;

  return `
    <div class="mini-sprint-summary">
      <div class="mini-sprint-header">
        <span class="mini-sprint-label">${escapeHtml(label)}</span>
        <span class="mini-sprint-pct">${progress.closed}/${progress.total} · ${pct}%</span>
      </div>
      <div class="mini-sprint-counts">
        <span class="count-chip cc-progress">${activeCount} active</span>
        <span class="count-chip cc-uat">${uatCount} UAT</span>
        <span class="count-chip cc-done">${doneCount} done</span>
      </div>
      <div class="prog-track" style="height:5px;">
        <div class="prog-fill ${bar}" style="width:${pct}%"></div>
      </div>
    </div>`;
}

// ── Expand panel ──────────────────────────────────────────────────────────────
const STATUS_COLOR = {
  'in-progress': 'blue',
  'SIT':         'amber',
  'UAT':         'purple',
  'blocked':     'red',
  'backlog':     'gray',
};

function ticketCardHtml(ticket, repo) {
  const color    = STATUS_COLOR[ticket.status] || 'gray';
  const assignee = ticket.assignee ? `<span>@${escapeHtml(ticket.assignee)}</span>` : '';
  const updated  = ticket.updated_at ? `<span>${shortTime(ticket.updated_at)}</span>` : '';
  const sep      = assignee && updated ? '<span> · </span>' : '';

  let actionsHtml = '';
  if (ticket.is_uat) {
    const n = ticket.number;
    const r = escapeHtml(repo);
    actionsHtml = `
      <div class="uat-report" id="tr-${n}">
        <div class="tr-loading">Loading test report…</div>
      </div>
      <div class="ticket-actions" id="tact-${n}">
        <button class="btn-approve-sm" id="approve-btn-${n}" onclick="confirmApprove(${n}, '${r}')">Approve ✓</button>
        <button class="btn-reject-sm"  onclick="showRejectInline(${n})">Reject…</button>
      </div>
      <div class="reject-inline hidden" id="reject-inline-${n}">
        <textarea id="reject-reason-${n}" rows="2" placeholder="Reason for rejection…"></textarea>
        <div class="reject-row">
          <button class="btn-send-sm"   onclick="submitRejectInline(${n}, '${r}')">Send rejection</button>
          <button class="btn-cancel-sm" onclick="hideRejectInline(${n})">Cancel</button>
        </div>
      </div>`;
  }

  const branchChip = ticket.feature_branch
    ? `<div class="ticket-branch"><i class="ti ti-git-branch"></i>${escapeHtml(ticket.feature_branch)}</div>`
    : '';

  const sprintChip = ticket.sprint_label
    ? `<span class="sprint-chip">${escapeHtml(ticket.sprint_label.replace(/^sprint-(\d+)$/, 'Sprint $1'))}</span>`
    : '';

  const chipsHtml = (sprintChip || branchChip)
    ? `<div class="ticket-chips">${sprintChip}${branchChip}</div>`
    : '';

  // Size / estimating badge (issue #267): show size-S/M/L/XL when available,
  // or an "estimating…" placeholder while the background task runs.
  const labels = ticket.labels || [];
  const hasEstimated = labels.includes('estimated');
  const sizeLabel = labels.find(l => /^size-/.test(l));
  let sizeBadgeHtml = '';
  if (sizeLabel) {
    const sizeVal = sizeLabel.replace('size-', '');
    sizeBadgeHtml = `<span class="ticket-size-badge ticket-size-${sizeVal}">${escapeHtml(sizeVal)}</span>`;
  } else if (!hasEstimated) {
    sizeBadgeHtml = `<span class="ticket-size-badge ticket-size-estimating">estimating…</span>`;
  }

  return `
    <div class="ticket-card">
      <div class="ticket-top">
        <a class="ticket-num" href="${escapeHtml(ticket.url)}" target="_blank" rel="noopener">#${ticket.number}</a>
        <a class="ticket-title ticket-title-link" href="${escapeHtml(ticket.url)}" target="_blank" rel="noopener">${escapeHtml(ticket.title)}</a>
        <span class="sbadge ${color}">${escapeHtml(ticket.status)}</span>
        ${sizeBadgeHtml}
      </div>
      <div class="ticket-meta">${assignee}${sep}${updated}</div>
      ${chipsHtml}
      ${actionsHtml}
    </div>`;
}

function _ticketGroupHtml(label, tickets, repo) {
  if (tickets.length === 0) return '';
  const r = escapeHtml(repo);
  let hdrText, approveAllBtn, hdrStyle;
  if (label === 'UAT') {
    _uatTicketsByRepo[repo] = tickets;
    hdrText = `UAT (${tickets.length})`;
    approveAllBtn = ` <button class="btn-approve-all-uat" onclick="showApproveAllUatModal('${r}')"><i class="ti ti-checks"></i> Approve all UAT</button>`;
    hdrStyle = ' style="display:flex;align-items:center;"';
  } else {
    hdrText = `${escapeHtml(label)} · ${tickets.length}`;
    approveAllBtn = '';
    hdrStyle = '';
  }
  return `<div class="ticket-group">
    <div class="expand-hdr-title ticket-group-hdr"${hdrStyle}>${hdrText}${approveAllBtn}</div>
    ${tickets.map(t => ticketCardHtml(t, repo)).join('')}
  </div>`;
}

function agentDetailCardHtml(agent) {
  const s    = agent.status === 'working' ? 'working' : agent.status === 'done' ? 'done' : 'waiting';
  const dir  = (agent.working_dir || '').replace(/^\/Users\/[^/]+\//, '~/');
  const tool = agent.last_tool
    ? `<div class="agent-detail-tool">Using: ${escapeHtml(agent.last_tool)}</div>`
    : '';
  const p    = _parseAgentName(agent.name);
  const context = p.isNew && p.branch
    ? `<div class="agent-detail-meta">${escapeHtml(p.repo)} · ${escapeHtml(p.branch)}</div>`
    : '';
  const sessLine = p.isNew && p.shortSess
    ? `<div class="agent-detail-meta">${escapeHtml(p.shortSess)} · ${timeAgo(agent.last_seen)}</div>`
    : `<div class="agent-detail-meta">${timeAgo(agent.last_seen)}</div>`;
  return `
    <div class="agent-detail-card">
      <div class="agent-detail-top">
        <span class="role-badge ${_roleBadgeClass(p.role)}">${escapeHtml(p.role)}</span>
        <span class="agent-badge ${s}">${s}</span>
      </div>
      ${tool}
      ${context}
      <div class="agent-detail-meta">${escapeHtml(dir)}</div>
      ${sessLine}
    </div>`;
}

function renderExpandPanel(id, data, repo) {
  const el = document.getElementById(`proj-detail-${id}`);
  if (!el) return;

  const tickets = data.tickets || [];
  const agents  = data.agents  || [];
  const ghUrl   = data.github_url || `https://github.com/${repo}/issues`;

  // Group tickets by workflow stage in priority order: SIT → UAT → in-progress → backlog
  let ticketsHtml;
  if (tickets.length === 0) {
    ticketsHtml = '<div class="empty-small">No open tickets</div>';
  } else {
    const sitT      = tickets.filter(t => t.status === 'SIT');
    const uatT      = tickets.filter(t => t.status === 'UAT');
    const activeT   = tickets.filter(t => t.status === 'in-progress' || t.status === 'blocked');
    const backlogT  = tickets.filter(t => t.status === 'backlog');
    ticketsHtml  = [
      _ticketGroupHtml('SIT',         sitT,     repo),
      _ticketGroupHtml('UAT',         uatT,     repo),
      _ticketGroupHtml('In progress', activeT,  repo),
      _ticketGroupHtml('Backlog',     backlogT, repo),
    ].join('');
  }

  // AC-2: separate working vs done agents; respect toggle state (AC-2d)
  const workingAgents = agents.filter(a => a.status === 'working');
  const doneAgents    = agents.filter(a => a.status === 'done');
  const nWorking      = workingAgents.length;
  const nDone         = doneAgents.length;
  const showDone      = !!doneAgentsVisible[repo]; // AC-2d: per-project toggle

  // AC-2b: summary header
  const doneToggleStyle = nDone > 0 ? 'cursor:pointer;text-decoration:underline dotted;' : '';
  const doneLabel = `<span id="done-toggle-${id}" style="${doneToggleStyle}" onclick="${nDone > 0 ? `toggleDoneAgents('${id}','${escapeHtml(repo)}')` : ''}" title="${nDone > 0 ? 'Click to toggle' : ''}">done (${nDone})</span>`;
  const agentsHeader = `AGENTS · working (${nWorking}) · ${doneLabel}`;

  // AC-2a: show only working by default; AC-2c: toggle shows done
  let agentsListHtml = '';
  if (nWorking === 0 && !showDone) {
    agentsListHtml = '<div class="empty-small">No active agents</div>';
  } else {
    agentsListHtml = workingAgents.map(agentDetailCardHtml).join('');
  }
  if (showDone && nDone > 0) {
    agentsListHtml += doneAgents.map(agentDetailCardHtml).join('');
  }

  // Tokens today line for this project
  const tokTotal = data.tokens_today;
  const tokCost  = data.cost_today_usd;
  let tokLine = '';
  if (tokTotal != null && tokTotal > 0) {
    const costStr = tokCost != null ? ` · ~$${tokCost.toFixed(2)}` : '';
    tokLine = `<div class="agent-detail-meta" style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">Tokens today: ${tokTotal.toLocaleString()}${escapeHtml(costStr)}</div>`;
  } else {
    tokLine = `<div class="agent-detail-meta" style="margin-top:10px;padding-top:10px;border-top:1px solid var(--border)">Tokens today: —</div>`;
  }

  // AC-7: mini sprint summary at top of expand panel
  const projData = allProjects.find(p => p.repo === repo) || null;
  const miniSprintHtml = _miniSprintSummaryHtml(projData);

  const projName = escapeHtml(projData?.name || repo);

  el.innerHTML = `
    <div class="expand-col">
      ${miniSprintHtml}
      <div class="expand-hdr">
        <span class="expand-hdr-title">Active tickets</span>
        <a class="view-all" href="${escapeHtml(ghUrl)}" target="_blank" rel="noopener">View all →</a>
      </div>
      ${ticketsHtml}
    </div>
    <div class="expand-col">
      <div class="expand-hdr">
        <span class="expand-hdr-title">${agentsHeader}</span>
      </div>
      ${agentsListHtml}
      ${tokLine}
    </div>
    <div class="expand-remove-row">
      <button class="btn-danger"
        data-repo="${escapeHtml(repo)}"
        data-name="${projName}"
        onclick="openRemoveProjectDialog(this.dataset.repo, this.dataset.name)">
        <i class="ti ti-trash" style="margin-right:4px;"></i>Remove Project
      </button>
    </div>`;

  // kick off test-report loads for UAT tickets
  tickets.filter(t => t.is_uat).forEach(t => loadTestReport(t.number, repo));
}

// AC-2c: toggle done agents visibility per project
function toggleDoneAgents(id, repo) {
  doneAgentsVisible[repo] = !doneAgentsVisible[repo];
  if (detailsCache[repo] && _activeProject === repo) {
    _renderProjectTickets(repo, detailsCache[repo]);
  }
}

// ── Test report (inline in UAT card) ─────────────────────────────────────────

async function loadTestReport(issueNum, repo) {
  const key = `${repo}#${issueNum}`;
  if (testReportCache[key]) {
    renderTestReport(issueNum, repo, testReportCache[key]);
    return;
  }
  try {
    const res = await fetch(`/api/issues/${issueNum}/test-report?repo=${encodeURIComponent(repo)}`);
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    testReportCache[key] = data;
    renderTestReport(issueNum, repo, data);
  } catch {
    const el = document.getElementById(`tr-${issueNum}`);
    if (el) el.innerHTML = '<div class="tr-loading">Could not load test report.</div>';
  }
}

function renderTestReport(issueNum, repo, data) {
  const el = document.getElementById(`tr-${issueNum}`);
  if (!el) return;

  if (!data.found) {
    // No tester comment yet — show manual UAT steps from ticket body as checkboxes
    const steps = data.uat_steps || [];
    if (steps.length === 0) {
      el.innerHTML = '<div class="tr-loading">No test report yet.</div>';
      updateApproveBtn(issueNum, data);
      return;
    }
    const checked = _getManualChecks(issueNum);
    const r       = escapeHtml(repo);
    el.innerHTML = `
      <div class="tr-section-title">UAT Steps (manual — no tester report yet)</div>
      ${steps.map((s, idx) => `
        <div class="tr-row">
          <span class="tr-icon">⚠️</span>
          <div class="tr-manual-wrap">
            <span class="tr-text">${s.num}. ${escapeHtml(s.text)}</span>
            <div class="tr-manual-check">
              <input type="checkbox" id="mchk-${issueNum}-${idx}"
                     ${checked.has(idx) ? 'checked' : ''}
                     onchange="onManualStepCheck(${issueNum}, '${r}', ${idx}, this.checked)">
              <label for="mchk-${issueNum}-${idx}">Verified</label>
            </div>
          </div>
        </div>`).join('')}`;
    updateApproveBtn(issueNum, data);
    return;
  }

  const checkedSteps = _getManualChecks(issueNum);
  const r = escapeHtml(repo);

  // Criteria section
  let html = '';
  const criteria = data.criteria || [];
  if (criteria.length) {
    html += '<div class="tr-section-title">Acceptance Criteria</div>';
    html += criteria.map(c => {
      const icon = c.status === 'pass' ? '✅' : c.status === 'fail' ? '❌' : '⚠️';
      const cls  = c.status === 'fail' ? ' fail' : '';
      return `<div class="tr-row">
        <span class="tr-icon">${icon}</span>
        <span class="tr-text${cls}">${escapeHtml(c.text)}</span>
      </div>`;
    }).join('');
  }

  // UAT step results section
  const uat = data.uat_results || [];
  if (uat.length) {
    html += '<div class="tr-section-title">UAT Steps</div>';
    html += uat.map((s, idx) => {
      const icon = s.status === 'pass' ? '✅' : s.status === 'fail' ? '❌' : '⚠️';
      const cls  = s.status === 'fail' ? ' fail' : '';
      const manual = s.status === 'manual' ? `
        <div class="tr-manual-check">
          <input type="checkbox" id="mchk-${issueNum}-${idx}"
                 ${checkedSteps.has(idx) ? 'checked' : ''}
                 onchange="onManualStepCheck(${issueNum}, '${r}', ${idx}, this.checked)">
          <label for="mchk-${issueNum}-${idx}">Verified manually</label>
        </div>` : '';
      return `<div class="tr-row">
        <span class="tr-icon">${icon}</span>
        <div class="tr-manual-wrap">
          <span class="tr-text${cls}">${s.num}. ${escapeHtml(s.text)}</span>
          ${manual}
        </div>
      </div>`;
    }).join('');
  }

  // Summary
  if (data.overall_status) {
    const isReady  = data.overall_status === 'READY_FOR_UAT';
    const isFail   = data.overall_status === 'NEEDS_FIXES';
    const cls      = isReady ? 'ready' : isFail ? 'blocked' : '';
    const c        = data.counts || {};
    const detail   = c.passed !== undefined
      ? ` — ${c.passed} passed · ${c.failed} failed · ${c.manual} manual` : '';
    html += `<div class="tr-summary ${cls}">${escapeHtml(data.overall_status)}${detail}</div>`;
  }

  el.innerHTML = html;
  updateApproveBtn(issueNum, data);
}

// ── Manual step checkboxes ────────────────────────────────────────────────────

function _getManualChecks(issueNum) {
  try {
    const raw = localStorage.getItem(`uat-manual-${issueNum}`);
    return new Set(raw ? JSON.parse(raw) : []);
  } catch { return new Set(); }
}

function _saveManualCheck(issueNum, idx, checked) {
  const s = _getManualChecks(issueNum);
  if (checked) s.add(idx); else s.delete(idx);
  localStorage.setItem(`uat-manual-${issueNum}`, JSON.stringify([...s]));
}

function onManualStepCheck(issueNum, repo, idx, checked) {
  _saveManualCheck(issueNum, idx, checked);
  const data = testReportCache[`${repo}#${issueNum}`];
  if (data) updateApproveBtn(issueNum, data);
}

function _allManualVerified(issueNum, data) {
  const checked = _getManualChecks(issueNum);
  const results = data.uat_results || [];

  if (results.length > 0) {
    return results.every((s, idx) => s.status !== 'manual' || checked.has(idx));
  }
  // No tester results: fall back to ticket-body UAT steps (all need manual check)
  const steps = data.uat_steps || [];
  if (steps.length === 0) return true;
  return steps.every((_, idx) => checked.has(idx));
}

function updateApproveBtn(issueNum, data) {
  const btn = document.getElementById(`approve-btn-${issueNum}`);
  if (!btn) return;
  btn.classList.toggle('prominent', _allManualVerified(issueNum, data));
}

// ── Approve / Reject ──────────────────────────────────────────────────────────
let _approveModalIssue = null;
let _approveModalRepo  = null;

function confirmApprove(issueNum, repo) {
  _approveModalIssue = issueNum;
  _approveModalRepo  = repo;
  const ref = document.getElementById('approve-modal-issue-ref');
  if (ref) ref.textContent = `#${issueNum}`;
  document.getElementById('approve-backdrop')?.classList.remove('hidden');
  document.getElementById('approve-modal')?.classList.remove('hidden');
}

function approveModalCancel() {
  document.getElementById('approve-backdrop')?.classList.add('hidden');
  document.getElementById('approve-modal')?.classList.add('hidden');
  _approveModalIssue = null;
  _approveModalRepo  = null;
  const btn = document.getElementById('approve-modal-confirm-btn');
  if (btn) btn.disabled = false;
}

async function approveModalConfirm() {
  const issueNum = _approveModalIssue;
  const repo     = _approveModalRepo;
  if (issueNum === null) return;

  const btn = document.getElementById('approve-modal-confirm-btn');
  if (btn) btn.disabled = true;

  try {
    const res = await fetch(
      `/api/tickets/${issueNum}/approve?repo=${encodeURIComponent(repo)}`,
      { method: 'POST' }
    );
    if (!res.ok) {
      const text = await res.text();
      showToast(`${res.status} ${res.statusText}: ${text}`);
      if (btn) btn.disabled = false;
      return;
    }
    approveModalCancel();
    document.getElementById(`approve-btn-${issueNum}`)?.closest('.ticket-card')?.remove();
  } catch (e) {
    showToast(`Approve failed: ${e.message}`);
    if (btn) btn.disabled = false;
  }
}

async function approveIssue(issueNum, repo, btnEl) {
  if (btnEl) btnEl.disabled = true;
  try {
    const res = await fetch(
      `/api/issues/${issueNum}/approve?repo=${encodeURIComponent(repo)}`,
      { method: 'POST' }
    );
    if (!res.ok) throw new Error(await res.text());
    _refreshAfterAction(repo);
  } catch (e) {
    showToast('Approve failed: ' + e.message);
    if (btnEl) btnEl.disabled = false;
  }
}

function showApproveAllUatModal(repo) {
  _approveAllUatRepo = repo;
  const tickets = _uatTicketsByRepo[repo] || [];
  const n = tickets.length;
  const proj = allProjects.find(p => p.repo === repo);
  const projName = proj ? proj.name : repo.split('/').pop();
  document.getElementById('aua-modal-title').textContent =
    `Approve ${n} UAT ticket${n !== 1 ? 's' : ''} for ${projName}?`;
  document.getElementById('aua-modal-list').innerHTML =
    tickets.map(t => `<li>#${t.number} ${escapeHtml(t.title)}</li>`).join('');
  // Reset state: re-enable all buttons, clear any previous error
  const confirmBtn = document.getElementById('aua-modal-confirm');
  const cancelBtn  = document.getElementById('aua-modal-cancel');
  const closeBtn   = document.getElementById('aua-modal-close');
  if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.innerHTML = '<i class="ti ti-checks"></i> Confirm'; }
  if (cancelBtn)  cancelBtn.disabled = false;
  if (closeBtn)   closeBtn.disabled  = false;
  const errEl = document.getElementById('aua-modal-error');
  if (errEl) { errEl.textContent = ''; errEl.classList.add('hidden'); }
  const backdrop = document.getElementById('aua-modal-backdrop');
  backdrop.onclick = closeApproveAllUatModal;
  backdrop.classList.remove('hidden');
  document.getElementById('aua-modal').classList.remove('hidden');
}

function closeApproveAllUatModal() {
  // Only close when not in loading state
  const confirmBtn = document.getElementById('aua-modal-confirm');
  if (confirmBtn && confirmBtn.disabled && confirmBtn.querySelector('.dt-spinner')) return;
  document.getElementById('aua-modal-backdrop').classList.add('hidden');
  document.getElementById('aua-modal').classList.add('hidden');
  _approveAllUatRepo = null;
}

async function confirmApproveAllUat() {
  const repo = _approveAllUatRepo;
  if (!repo) return;
  const confirmBtn = document.getElementById('aua-modal-confirm');
  const cancelBtn  = document.getElementById('aua-modal-cancel');
  const closeBtn   = document.getElementById('aua-modal-close');
  const errEl      = document.getElementById('aua-modal-error');
  const backdrop   = document.getElementById('aua-modal-backdrop');

  // Enter loading state
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.innerHTML = '<span class="dt-spinner"></span>Approving…'; }
  if (cancelBtn)  cancelBtn.disabled  = true;
  if (closeBtn)   closeBtn.disabled   = true;
  if (backdrop)   backdrop.onclick    = null;
  if (errEl)      { errEl.textContent = ''; errEl.classList.add('hidden'); }

  try {
    const res = await fetch(`/api/projects/${repo}/approve-batch`, { method: 'POST' });
    if (!res.ok) {
      const body = await res.text();
      let msg;
      try { msg = JSON.parse(body).detail || body; } catch (_) { msg = body; }
      throw new Error(msg);
    }
    const data = await res.json();
    // Success: close modal, refresh, show toast
    document.getElementById('aua-modal-backdrop').classList.add('hidden');
    document.getElementById('aua-modal').classList.add('hidden');
    _approveAllUatRepo = null;
    _refreshAfterAction(repo);
    showSuccessToast(`Approved ${data.count} ticket${data.count !== 1 ? 's' : ''}`);
  } catch (e) {
    // Error: re-enable all controls and show inline error
    if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.innerHTML = '<i class="ti ti-checks"></i> Confirm'; }
    if (cancelBtn)  cancelBtn.disabled  = false;
    if (closeBtn)   closeBtn.disabled   = false;
    if (backdrop)   backdrop.onclick    = closeApproveAllUatModal;
    if (errEl)      { errEl.textContent = 'Batch approve failed: ' + e.message; errEl.classList.remove('hidden'); }
  }
}

function showRejectInline(issueNum) {
  document.getElementById(`tact-${issueNum}`)?.classList.add('hidden');
  document.getElementById(`reject-inline-${issueNum}`)?.classList.remove('hidden');
  document.getElementById(`reject-reason-${issueNum}`)?.focus();
}

function hideRejectInline(issueNum) {
  document.getElementById(`tact-${issueNum}`)?.classList.remove('hidden');
  document.getElementById(`reject-inline-${issueNum}`)?.classList.add('hidden');
}

async function submitRejectInline(issueNum, repo) {
  const reasonEl = document.getElementById(`reject-reason-${issueNum}`);
  const reason   = reasonEl?.value.trim();
  if (!reason) { reasonEl?.focus(); return; }

  const sendBtn = document.querySelector(`#reject-inline-${issueNum} .btn-send-sm`);
  if (sendBtn) sendBtn.disabled = true;

  try {
    const res = await fetch(
      `/api/issues/${issueNum}/reject?repo=${encodeURIComponent(repo)}`,
      {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ reason }),
      }
    );
    if (!res.ok) throw new Error(await res.text());
    _refreshAfterAction(repo);
  } catch (e) {
    alert('Reject failed: ' + e.message);
    if (sendBtn) sendBtn.disabled = false;
  }
}

function _refreshAfterAction(repo) {
  delete detailsCache[repo];
  Object.keys(testReportCache).forEach(k => { if (k.startsWith(`${repo}#`)) delete testReportCache[k]; });
  if (_activeProject === repo && _activeProjectTab === 'tickets') {
    _loadProjectTickets(repo);
  }
  loadProjects().catch(() => {});
}

// ── Load projects ─────────────────────────────────────────────────────────────
async function loadProjects() {
  const res = await fetch('/api/projects');
  if (!res.ok) throw new Error(await res.text());
  const data = await res.json();
  renderMetrics(data.metrics   || {});
  renderProjects(data.projects || []);
}

// ── Agents view ───────────────────────────────────────────────────────────────
async function fetchAgents() {
  try {
    const agents = await fetch('/api/agents').then(r => r.json());
    renderAgents(agents);
  } catch { /* silent */ }
}

function _renderAgentCard(a) {
  let badgeCls, badgeLabel;
  if (a.status === 'working')        { badgeCls = 'badge-working';    badgeLabel = 'working'; }
  else if (a.status === 'waiting')   { badgeCls = 'badge-waiting';    badgeLabel = 'waiting'; }
  else if (a.status === 'timed_out') { badgeCls = 'badge-timed-out';  badgeLabel = 'Timed Out'; }
  else                               { badgeCls = 'badge-done';       badgeLabel = a.status; }
  const dir      = (a.working_dir || '').replace(/^\/Users\/[^/]+\//, '~/');
  const toolLine = a.last_tool
    ? `<div class="agent-tool"><span class="lbl">Using </span>${escapeHtml(a.last_tool)}</div>`
    : '';
  const p        = _parseAgentName(a.name);
  const context  = p.isNew
    ? `<div class="agent-context">${escapeHtml(p.repo)}${p.branch ? ` · ${escapeHtml(p.branch)}` : ''}</div>`
    : `<div class="agent-context">${escapeHtml(p.repo)}</div>`;
  const sessLine = p.isNew && p.shortSess
    ? `<div class="agent-sess">${escapeHtml(p.shortSess)} · ${timeAgo(a.last_seen)}</div>`
    : `<div class="agent-time">${timeAgo(a.last_seen)}</div>`;
  return `
    <div class="agent-card ${a.status}">
      <div class="card-top">
        <span class="role-badge ${_roleBadgeClass(p.role)}">${escapeHtml(p.role)}</span>
        <span class="badge ${badgeCls}">${badgeLabel}</span>
      </div>
      ${context}
      <div class="agent-dir">${escapeHtml(dir)}</div>
      ${toolLine}
      ${sessLine}
    </div>`;
}

function renderAgents(agents) {
  const active   = agents.filter(a => a.status === 'working' || a.status === 'waiting');
  const inactive = agents.filter(a => a.status === 'done'    || a.status === 'timed_out');

  document.getElementById('cnt-working').textContent  = agents.filter(a => a.status === 'working').length;
  document.getElementById('cnt-waiting').textContent  = agents.filter(a => a.status === 'waiting').length;
  document.getElementById('cnt-timed-out').textContent = agents.filter(a => a.status === 'timed_out').length;
  document.getElementById('cnt-done').textContent     = agents.filter(a => a.status === 'done').length;

  // Active section — always visible; shows empty-state when no active agents
  document.getElementById('agents-active-label').textContent = `ACTIVE · ${active.length}`;
  const activeGrid = document.getElementById('agents-grid-active');
  activeGrid.innerHTML = active.length
    ? active.map(_renderAgentCard).join('')
    : '<div class="empty">No active agents</div>';

  // Inactive section — hidden entirely when there are no inactive agents
  const inactiveSection = document.getElementById('agents-inactive-section');
  if (inactive.length === 0) {
    inactiveSection.style.display = 'none';
  } else {
    inactiveSection.style.display = '';
    document.getElementById('agents-inactive-label').textContent = `INACTIVE · ${inactive.length}`;
    document.getElementById('agents-grid-inactive').innerHTML = inactive.map(_renderAgentCard).join('');
  }
}

// ── Activity view ─────────────────────────────────────────────────────────────
async function fetchEvents() {
  try {
    const events = await fetch('/api/events').then(r => r.json());
    renderEvents(events);
  } catch { /* silent */ }
}

function renderEvents(events) {
  const list = document.getElementById('activity-list');
  if (!events || events.length === 0) {
    list.innerHTML = '<div class="empty">No activity yet</div>';
    return;
  }
  list.innerHTML = events.map(e => {
    const iso     = e.created_at;
    const d       = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
    const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const agent   = e.agent_name || e.session_id || '—';
    const desc    = e.event_type === 'tool_use' && e.data?.tool_name
      ? `Used ${e.data.tool_name}`
      : e.event_type.replace(/_/g, ' ');
    return `
      <div class="activity-row">
        <span class="act-time">${timeStr}</span>
        <span class="act-agent">${escapeHtml(agent)}</span>
        <span class="act-desc">${escapeHtml(desc)}</span>
      </div>`;
  }).join('');
}

// ── Live Log Panel (issue #176) ───────────────────────────────────────────────

const _LLP_MAX_ENTRIES = 200;
let _llpAutoScroll     = true;
let _llpProjectFilter  = '';   // '' = all
let _llpRoleFilter     = '';   // '' = all
let _llpRelativeTimer  = null;

// Relative timestamp for a stored epoch (ms)
function _llpTimeAgo(tsMs) {
  const s = Math.floor((Date.now() - tsMs) / 1000);
  if (s < 5)    return 'just now';
  if (s < 60)   return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} m ago`;
  return `${Math.floor(s / 3600)} h ago`;
}

// Derive a short project name from working_dir or agent name
function _llpProject(ev) {
  if (!ev) return null;
  // Try agent name first (new format: role·repo·branch·#short)
  const parsed = _parseAgentName(ev.name);
  if (parsed.isNew && parsed.repo) return parsed.repo.split('/').pop();
  // Fallback: last path segment of working_dir
  if (ev.working_dir && ev.working_dir !== 'unknown') {
    return ev.working_dir.split('/').pop() || null;
  }
  return null;
}

function _llpRole(ev) {
  if (!ev) return 'agent';
  const parsed = _parseAgentName(ev.name);
  return parsed.role || 'agent';
}

function _llpDesc(ev) {
  if (!ev) return '—';
  const et = ev.event_type || '';
  if (et === 'tool_use' && ev.tool_name) return `Used ${ev.tool_name}`;
  return et.replace(/_/g, ' ') || '—';
}

function llpAddEntry(ev) {
  const container = document.getElementById('llp-entries');
  if (!container) return;

  // Remove placeholder if present
  const placeholder = container.querySelector('.llp-empty');
  if (placeholder) placeholder.remove();

  const proj    = _llpProject(ev) || null;   // null = global
  const role    = _llpRole(ev);
  const desc    = _llpDesc(ev);
  const tsMs    = Date.now();

  const badgeClass = _roleBadgeClass(role);

  const entry = document.createElement('div');
  entry.className   = 'llp-entry';
  entry.dataset.ts  = tsMs;
  entry.dataset.proj = proj || '';
  entry.dataset.role = role;

  // Apply current filter immediately
  if (_llpProjectFilter && proj !== _llpProjectFilter) entry.classList.add('llp-filtered');
  if (_llpRoleFilter     && role !== _llpRoleFilter)    entry.classList.add('llp-filtered');

  entry.innerHTML = `
    <span class="llp-ts">${_llpTimeAgo(tsMs)}</span>
    <span class="llp-proj" title="${escapeHtml(proj || '—')}">${escapeHtml(proj || '—')}</span>
    <div class="llp-meta">
      <span class="llp-role-badge ${escapeHtml(badgeClass)}">${escapeHtml(role)}</span>
      <span class="llp-desc" title="${escapeHtml(desc)}">${escapeHtml(desc)}</span>
    </div>`;

  container.appendChild(entry);

  // AC-4: cap at 200 DOM entries
  const all = container.querySelectorAll('.llp-entry');
  if (all.length > _LLP_MAX_ENTRIES) {
    for (let i = 0; i < all.length - _LLP_MAX_ENTRIES; i++) {
      all[i].remove();
    }
  }

  // AC-3: auto-scroll
  if (_llpAutoScroll) {
    const scroll = document.getElementById('llp-scroll');
    if (scroll) scroll.scrollTop = scroll.scrollHeight;
  }
}

// AC-3: scroll detection — pause auto-scroll when user scrolls up
function _llpInitScrollListener() {
  const scroll = document.getElementById('llp-scroll');
  const jumpBtn = document.getElementById('llp-jump-btn');
  if (!scroll || !jumpBtn) return;

  scroll.addEventListener('scroll', () => {
    const atBottom = scroll.scrollTop + scroll.clientHeight >= scroll.scrollHeight - 20;
    if (atBottom) {
      _llpAutoScroll = true;
      jumpBtn.classList.add('hidden');
    } else {
      _llpAutoScroll = false;
      jumpBtn.classList.remove('hidden');
    }
  });
}

function llpJumpToLatest() {
  const scroll = document.getElementById('llp-scroll');
  if (scroll) scroll.scrollTop = scroll.scrollHeight;
  _llpAutoScroll = true;
  document.getElementById('llp-jump-btn')?.classList.add('hidden');
}

// AC-5: update SSE connection status in panel
function _llpSetStatus(state) {
  // states: 'connected' | 'reconnecting' | 'disconnected'
  const dot  = document.getElementById('llp-dot');
  const text = document.getElementById('llp-status-text');
  if (!dot || !text) return;
  dot.className = 'llp-dot llp-dot-' + (state === 'connected' ? 'connected' : state === 'reconnecting' ? 'reconnecting' : 'off');
  text.textContent = state === 'connected' ? 'Connected' : state === 'reconnecting' ? 'Reconnecting…' : 'Disconnected';
}

// AC-6: project filter
function llpSetProjectFilter(val) {
  _llpProjectFilter = val;
  _llpApplyFilters();
}

// AC-7: role filter
function llpSetRoleFilter(val) {
  _llpRoleFilter = val;
  _llpApplyFilters();
}

function _llpApplyFilters() {
  const entries = document.querySelectorAll('#llp-entries .llp-entry');
  entries.forEach(entry => {
    const projMatch = !_llpProjectFilter || entry.dataset.proj === _llpProjectFilter;
    const roleMatch = !_llpRoleFilter    || entry.dataset.role === _llpRoleFilter;
    entry.classList.toggle('llp-filtered', !(projMatch && roleMatch));
  });
  // Auto-scroll after filter change
  if (_llpAutoScroll) {
    const scroll = document.getElementById('llp-scroll');
    if (scroll) scroll.scrollTop = scroll.scrollHeight;
  }
}

// Populate project filter dropdown from known projects
function llpUpdateProjectFilter(projects) {
  const sel = document.getElementById('llp-project-filter');
  if (!sel) return;
  const cur = sel.value;
  const names = (projects || []).map(p => ({
    key: p.repo ? p.repo.split('/').pop() : p.name,
    label: p.name || p.repo
  }));
  // Deduplicate by key
  const seen = new Set();
  const opts = [{ key: '', label: 'All projects' }];
  for (const n of names) {
    if (!seen.has(n.key)) { seen.add(n.key); opts.push(n); }
  }
  sel.innerHTML = opts.map(o =>
    `<option value="${escapeHtml(o.key)}" ${o.key === cur ? 'selected' : ''}>${escapeHtml(o.label)}</option>`
  ).join('');
}

// Tick all relative timestamps every 5 seconds
function _llpStartRelativeClock() {
  if (_llpRelativeTimer) return;
  _llpRelativeTimer = setInterval(() => {
    document.querySelectorAll('#llp-entries .llp-entry').forEach(entry => {
      const ts = parseInt(entry.dataset.ts, 10);
      if (!isNaN(ts)) {
        const tsEl = entry.querySelector('.llp-ts');
        if (tsEl) tsEl.textContent = _llpTimeAgo(ts);
      }
    });
  }, 5000);
}

// ── SSE ───────────────────────────────────────────────────────────────────────
function setLive(connected) {
  document.getElementById('live-dot')?.classList.toggle('off', !connected);
}

let _sseReconnectTimer    = null;
const _SSE_DISCONNECT_MS  = 12000; // after 12 s in error state, show "disconnected"

function connectSSE() {
  const es = new EventSource('/events');
  es.onopen    = () => {
    setLive(true);
    _llpSetStatus('connected');
    if (_sseReconnectTimer) { clearTimeout(_sseReconnectTimer); _sseReconnectTimer = null; }
  };
  es.onerror   = () => {
    setLive(false);
    _llpSetStatus('reconnecting');
    if (!_sseReconnectTimer) {
      _sseReconnectTimer = setTimeout(() => {
        _llpSetStatus('disconnected');
        _sseReconnectTimer = null;
      }, _SSE_DISCONNECT_MS);
    }
  };
  es.onmessage = ev => {
    try {
      const msg = JSON.parse(ev.data);

      // Sprint alert banner push (AC-3a)
      if (msg.type === 'alert') {
        loadAlerts().catch(() => {});
        return;
      }

      // Sprint status push (AC-6d / issue #32)
      if (msg.type === 'sprint_update') {
        _sprintState = msg;
        renderSprintPanel(msg);
        _scLastUpdateTime = Date.now();
        const sprintVisible = !document.getElementById('view-sprint')?.classList.contains('hidden');
        if (sprintVisible) {
          scRenderCockpit(_sprintState);
          scRefreshAgents();
          scRefreshFeed();
          scRefreshAttention();
        }
        return;
      }

      // Sprint stopped (issue #32 AC-15)
      if (msg.type === 'sprint_stopped') {
        _sprintState = null;
        scRenderIdle();
        _updateSprintNavDot(false);
        return;
      }

      if (msg.type !== 'update') return;

      // AC-1/AC-2: feed every agent event into the live log panel
      if (msg.event) llpAddEntry(msg.event);

      const projectView  = document.getElementById('view-project');
      const overviewView = document.getElementById('view-overview');
      const isOverview   = overviewView && !overviewView.classList.contains('hidden');
      const isProject    = projectView  && !projectView.classList.contains('hidden');

      if (isOverview) {
        loadProjects().catch(() => {});
      }
      if (isProject && _activeProject) {
        delete detailsCache[_activeProject];
        if (_activeProjectTab === 'tickets') {
          _loadProjectTickets(_activeProject);
        }
        loadProjects().catch(() => {}); // keep header/picker in sync
      }
      if (!document.getElementById('view-agents').classList.contains('hidden')) fetchAgents();
      if (isProject && _activeProjectTab === 'sprint-history') {
        loadSprintHistory().catch(() => {});
      }
      if (msg.event && msg.event.event_type === 'sprint_plan_update') _handleSprintPlanSSE();
      if (msg.event && msg.event.event_type === 'sprint_finished' && _smgmtCurrentRepo) smgmtRefreshBoard();
    } catch { /* ignore */ }
  };
}

// ── Plan Usage card ───────────────────────────────────────────────────────────

function _fmtSecondsRemaining(seconds) {
  if (seconds <= 0) return null;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m left`;
  return `${m}m left`;
}

async function loadPlanUsage() {
  const res = await fetch('/api/plan-usage');
  if (res.status === 404) {
    // Not configured — keep card hidden
    return;
  }
  if (!res.ok) throw new Error(await res.text());
  const d = await res.json();
  renderPlanUsage(d);
}

function renderPlanUsage(d) {
  const card    = document.getElementById('m-plan-card');
  const pctEl   = document.getElementById('m-plan-pct');
  const barEl   = document.getElementById('m-plan-bar');
  const timeEl  = document.getElementById('m-plan-time');
  const slowEl  = document.getElementById('m-plan-slow');
  const row     = document.querySelector('.metrics-row');
  if (!card) return;

  // Show card and expand metrics row
  card.classList.remove('hidden');
  if (row) row.classList.add('has-plan');

  const pct    = d.window_pct ?? 0;
  const status = d.status;

  // AC-7: approx label already in HTML; show percentage
  pctEl.textContent = pct.toFixed(1) + '%';

  // AC-5: color the bar
  barEl.style.width = Math.min(pct, 100) + '%';
  barEl.className   = 'plan-bar-fill';
  if      (pct > 80) barEl.classList.add('plan-red');
  else if (pct >= 50) barEl.classList.add('plan-amber');
  else               barEl.classList.add('plan-green');

  // AC-6: time remaining or ready label
  if (status === 'active') {
    const timeStr = _fmtSecondsRemaining(d.seconds_remaining);
    // AC-7: note "(approx)" on token count in time sub-line
    const tokenStr = d.window_tokens != null
      ? `${d.window_tokens.toLocaleString()} / ${d.window_limit.toLocaleString()} tokens (approx)`
      : '';
    timeEl.textContent = timeStr
      ? `${timeStr} · ${tokenStr}`
      : tokenStr;
  } else {
    // no_activity or expired
    timeEl.textContent = 'ready · new window starts on next agent activity';
  }

  // AC-5: "Slow down" hint when > 80%
  slowEl.classList.toggle('hidden', pct <= 80);
}

// ── New Project Modal ─────────────────────────────────────────────────────────

let _npActiveTab = 'init'; // 'init' | 'add'

function openNewProjectModal() {
  document.getElementById('new-project-backdrop').classList.remove('hidden');
  document.getElementById('new-project-modal').classList.remove('hidden');
  // Reset both tabs
  document.getElementById('np-repo').value         = '';
  document.getElementById('np-icon').value         = '';
  document.getElementById('np-color').value        = '';
  document.getElementById('np-name').value         = '';
  document.getElementById('np-projects-dir').value = '~/dev';
  document.getElementById('np-nested').checked     = false;
  document.getElementById('np-skip-uat').checked   = false;
  _npClearError();
  _npClearInitError();
  _npResetLog();
  switchModalTab(_npActiveTab);
}

function closeNewProjectModal() {
  document.getElementById('new-project-backdrop').classList.add('hidden');
  document.getElementById('new-project-modal').classList.add('hidden');
}

function switchModalTab(tab) {
  _npActiveTab = tab;
  const initForm = document.getElementById('np-init-form');
  const addForm  = document.getElementById('new-project-form');
  const tabInit  = document.getElementById('np-tab-init');
  const tabAdd   = document.getElementById('np-tab-add');

  if (tab === 'init') {
    initForm.classList.remove('hidden');
    addForm.classList.add('hidden');
    tabInit.classList.add('active');
    tabAdd.classList.remove('active');
    document.getElementById('np-name').focus();
  } else {
    addForm.classList.remove('hidden');
    initForm.classList.add('hidden');
    tabAdd.classList.add('active');
    tabInit.classList.remove('active');
    document.getElementById('np-repo').focus();
  }
}

// ── Add existing repo tab helpers ─────────────────────────────────────────────
function _npClearError() {
  const errEl = document.getElementById('np-repo-error');
  const input = document.getElementById('np-repo');
  errEl.textContent = '';
  errEl.classList.add('hidden');
  input.classList.remove('error');
}

function _npShowError(msg) {
  const errEl = document.getElementById('np-repo-error');
  const input = document.getElementById('np-repo');
  errEl.textContent = msg;
  errEl.classList.remove('hidden');
  input.classList.add('error');
}

async function submitNewProject(event) {
  event.preventDefault();
  _npClearError();

  const repoUrl = document.getElementById('np-repo').value.trim();
  const icon    = document.getElementById('np-icon').value.trim()  || 'ti-folder';
  const color   = document.getElementById('np-color').value.trim() || 'gray';

  if (!repoUrl) {
    _npShowError('GitHub repo URL is required.');
    return;
  }

  const submitBtn = document.getElementById('np-submit');
  submitBtn.disabled = true;
  submitBtn.textContent = 'Adding…';

  try {
    const res = await fetch('/api/projects', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ repo_url: repoUrl, icon, color }),
    });

    if (res.status === 409) {
      const data = await res.json();
      _npShowError(data.detail || 'Project already added.');
      return;
    }
    if (res.status === 422) {
      const data = await res.json();
      _npShowError(data.detail || 'Invalid repo or repo not found on GitHub.');
      return;
    }
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      _npShowError(data.detail || `Error ${res.status}`);
      return;
    }

    // Success — close modal and reload project list
    closeNewProjectModal();
    loadProjects();
  } catch (e) {
    _npShowError('Network error: ' + e.message);
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Add Project';
  }
}

// ── Init Project tab helpers ──────────────────────────────────────────────────

function _npClearInitError() {
  const errEl = document.getElementById('np-init-error');
  const input = document.getElementById('np-name');
  errEl.textContent = '';
  errEl.classList.add('hidden');
  input.classList.remove('error');
}

function _npShowInitError(msg) {
  const errEl = document.getElementById('np-init-error');
  errEl.textContent = msg;
  errEl.classList.remove('hidden');
  const input = document.getElementById('np-name');
  input.classList.add('error');
}

function _npResetLog() {
  const logWrap = document.getElementById('np-log-wrap');
  const logEl   = document.getElementById('np-log');
  logWrap.classList.add('hidden');
  if (logEl) logEl.textContent = '';
}

function _npAppendLog(line) {
  const logWrap = document.getElementById('np-log-wrap');
  const logEl   = document.getElementById('np-log');
  logWrap.classList.remove('hidden');
  if (logEl) {
    logEl.textContent += line + '\n';
    logEl.scrollTop = logEl.scrollHeight;
  }
}

async function submitInitProject(event) {
  event.preventDefault();
  _npClearInitError();
  _npResetLog();

  const repoName    = document.getElementById('np-name').value.trim();
  const projectsDir = document.getElementById('np-projects-dir').value.trim() || '~/dev';
  const nested      = document.getElementById('np-nested').checked;
  const skipUat     = document.getElementById('np-skip-uat').checked;

  // AC4: client-side validation
  if (!repoName) {
    _npShowInitError('Project name is required.');
    document.getElementById('np-name').focus();
    return;
  }
  if (repoName.includes('/') || repoName.includes('\\')) {
    _npShowInitError('Project name must not contain path separators (/ or \\).');
    document.getElementById('np-name').focus();
    return;
  }

  const submitBtn = document.getElementById('np-init-submit');
  submitBtn.disabled = true;
  submitBtn.textContent = 'Creating…';

  try {
    const res = await fetch('/api/projects/init', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        repo_name:    repoName,
        projects_dir: projectsDir,
        nested,
        skip_uat: skipUat,
      }),
    });

    // AC4 / AC5: server-side validation errors (non-streaming 400/409)
    if (res.status === 400 || res.status === 409 || res.status === 422) {
      const data = await res.json().catch(() => ({}));
      _npShowInitError(data.detail || `Error ${res.status}`);
      return;
    }
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      _npShowInitError(data.detail || `Server error ${res.status}`);
      return;
    }

    // AC6: read SSE stream and append log lines
    const reader  = res.body.getReader();
    const decoder = new TextDecoder();
    let   buffer  = '';
    let   success = false;
    let   lastErrorMsg = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Parse SSE chunks: split on double newline
      const chunks = buffer.split('\n\n');
      buffer = chunks.pop(); // keep incomplete last chunk

      for (const chunk of chunks) {
        const lines = chunk.split('\n');
        let eventType = 'log';
        let dataLine  = '';
        for (const l of lines) {
          if (l.startsWith('event: ')) eventType = l.slice(7).trim();
          if (l.startsWith('data: '))  dataLine  = l.slice(6).trim();
        }
        if (!dataLine) continue;

        let payload = dataLine;
        try { payload = JSON.parse(dataLine); } catch { /* use raw */ }

        if (eventType === 'log') {
          _npAppendLog(payload);
        } else if (eventType === 'done') {
          success = true;
        } else if (eventType === 'error') {
          lastErrorMsg = payload;
        }
      }
    }

    if (success) {
      // AC7: close modal, refresh project list
      closeNewProjectModal();
      loadProjects().catch(() => {});
    } else {
      // AC8: stay open, show error
      _npShowInitError(lastErrorMsg || 'init_project.py failed. Check the log above.');
    }
  } catch (e) {
    _npShowInitError('Network error: ' + e.message);
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Create';
  }
}

// Close modal on Escape key
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeNewProjectModal();
});

// ── Clear test data ───────────────────────────────────────────────────────────
async function clearTestData() {
  const btn = document.getElementById('btn-clear-test-data');
  if (btn) { btn.disabled = true; btn.textContent = 'Clearing…'; }
  try {
    const res = await fetch('/api/events/test', { method: 'DELETE' });
    if (!res.ok) throw new Error(await res.text());
    fetchEvents();
    loadAlerts().catch(() => {});
  } catch (e) {
    showToast('Clear test data failed: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Clear test data'; }
  }
}

// ── Manual refresh ────────────────────────────────────────────────────────────
async function manualRefresh() {
  const btn  = document.getElementById('btn-refresh');
  const icon = document.getElementById('refresh-icon');
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  icon.classList.add('spinning');
  hideToast();
  try {
    const tasks = [loadProjects(), loadPlanUsage()];
    const isHistory = !document.getElementById('pview-sprint-history')?.classList.contains('hidden');
    if (isHistory) tasks.push(loadSprintHistory());
    if (_activeProject && _activeProjectTab === 'tickets') {
      delete detailsCache[_activeProject];
      tasks.push(Promise.resolve().then(() => _loadProjectTickets(_activeProject)));
    }
    await Promise.all(tasks);
  } catch {
    showToast('Refresh failed — server may be unreachable');
  } finally {
    setTimeout(() => {
      btn.disabled = false;
      icon.classList.remove('spinning');
    }, 500);
  }
}

function showToast(msg) {
  const t = document.getElementById('toast-error');
  if (!t) return;
  t.textContent = msg;
  t.style.display = 'block';
  setTimeout(() => { t.style.display = 'none'; }, 5000);
}

function hideToast() {
  const t = document.getElementById('toast-error');
  if (t) t.style.display = 'none';
}

function showSuccessToast(msg) {
  const t = document.getElementById('toast-success');
  if (!t) return;
  t.textContent = msg;
  t.style.display = 'block';
  setTimeout(() => { t.style.display = 'none'; }, 5000);
}

// ── Sprint alert banners (AC-3a) ──────────────────────────────────────────────

let _alertsCache = [];

async function loadAlerts() {
  try {
    const res = await fetch('/api/alerts');
    if (!res.ok) return;
    _alertsCache = await res.json();
    renderAlertBanners(_alertsCache);
  } catch { /* silent */ }
}

function renderAlertBanners(alerts) {
  const container = document.getElementById('alert-banners');
  if (!container) return;
  if (!alerts || alerts.length === 0) {
    container.innerHTML = '';
    return;
  }
  container.innerHTML = alerts.map((a, idx) => {
    // AC-5: prefix title with repo name when alert has a `repo` field
    const repoPrefix = a.repo ? `[${escapeHtml(a.repo.split('/').pop())}] ` : '';
    const titleText  = `${repoPrefix}${escapeHtml(a.title)}${a.category ? ` [${escapeHtml(a.category)}]` : ''}`;
    return `
    <div class="alert-banner" id="alert-${idx}">
      <div class="alert-banner-body">
        <div class="alert-banner-title">${titleText}</div>
        <div class="alert-banner-msg">${escapeHtml(a.body || '')}</div>
      </div>
      <button class="alert-dismiss" onclick="dismissAlert(${idx})" title="Dismiss">&times;</button>
    </div>`;
  }).join('');
}

async function dismissAlert(idx) {
  try {
    await fetch(`/api/alerts/${idx}`, { method: 'DELETE' });
    _alertsCache.splice(idx, 1);
    renderAlertBanners(_alertsCache);
  } catch { /* silent */ }
}

// ── Sprint status panel (AC-6) ────────────────────────────────────────────────

let _sprintState = null;

async function loadSprintStatus() {
  // The global sprint panel was removed (AC-1, issue #82). Sprint progress is
  // now shown per-project in the expand panel via _miniSprintSummaryHtml.
  // Per-#123: running badges and banner are updated via smgmtPollRunStatus().
  // We still fetch and cache sprint state for SSE compatibility.
  try {
    const res = await fetch('/api/sprint-status');
    if (!res.ok) return;
    const data = await res.json();
    const sprints = data.running_sprints || [];
    _sprintState = sprints.length > 0 ? sprints[0] : null;
    _updateSprintNavDot(!!_sprintState);
    const sprintVisible = !document.getElementById('view-sprint')?.classList.contains('hidden');
    if (sprintVisible && _sprintState) scRenderCockpit(_sprintState);
  } catch { /* silent */ }
}

function renderSprintPanel(state) {
  _sprintState = state;
  _updateSprintNavDot(!!state);
}

function _updateSprintNavDot(active) {
  const dot = document.getElementById('sc-nav-dot');
  if (!dot) return;
  dot.classList.toggle('dot-hidden', !active);
}

// AC-6c: Retry skipped button — opens instructions (actual retry requires CLI)
function retrySkipped() {
  alert('To retry skipped issues, re-run the sprint manager with:\n\npython3 dashboard/scripts/sprint_manager.py <label> --retry-failed');
}

// ── Periodic refresh ──────────────────────────────────────────────────────────
setInterval(() => {
  const overviewVisible = !document.getElementById('view-overview')?.classList.contains('hidden');
  const projectVisible  = !document.getElementById('view-project')?.classList.contains('hidden');

  if (overviewVisible || projectVisible) {
    loadProjects().catch(() => {});
    loadPlanUsage().catch(() => {});
  }
  if (projectVisible && _activeProjectTab === 'sprint-history') {
    loadSprintHistory().catch(() => {});
  }
}, 60_000);

// Sprint status polls every 30s (AC-6d)
// Also refreshes running badges/banner on overview.
setInterval(() => {
  loadSprintStatus().catch(() => {});
  loadAlerts().catch(() => {});
  const sprintVisible = !document.getElementById('view-sprint')?.classList.contains('hidden');
  if (sprintVisible) { scRefreshAgents(); scRefreshFeed(); }
  // Per-#123: refresh overview badges even when sprint-mgmt is not open
  fetch('/api/sprints/running-all').then(r => r.ok ? r.json() : null).then(d => {
    if (!d) return;
    const newMap = {};
    for (const entry of (d.running || [])) {
      const key = `${entry.project}:${entry.sprint_label}`;
      newMap[key] = entry;
    }
    _smgmtAllRunning = newMap;
    _updateOverviewRunningBadges();
    _updateRunningBanner();
  }).catch(() => {});
}, 30_000);

// ── Environment badge ─────────────────────────────────────────────────────────
async function fetchEnvironment() {
  try {
    const res  = await fetch('/api/environment');
    if (!res.ok) return;
    const data = await res.json();
    const env  = (data.environment || '').toLowerCase();
    const el   = document.getElementById('env-badge');
    if (!el) return;
    if (env === 'prd' || env === 'uat') {
      el.textContent = env.toUpperCase();
      el.className   = `env-badge ${env}`;
    }
  } catch { /* ignore — badge is optional */ }
}

// ── Build stamp footer (issue #329) ──────────────────────────────────────────

async function fetchBuildStamp() {
  if (_buildStampCache) return; // already fetched this session
  try {
    const res = await fetch('/api/version');
    if (!res.ok) return;
    const data = await res.json();
    _buildStampCache = data;
    const el = document.getElementById('build-stamp-footer');
    if (!el) return;
    const host = window.location.host;
    const sha  = (data.git_sha || 'unknown').slice(0, 7);
    el.textContent = `v${data.version || '?'} · ${host} · build ${sha} · ${data.branch || 'unknown'}`;
  } catch { /* footer is optional */ }
}

// ── Sprint History view (AC-5) ────────────────────────────────────────────────

let _sprintHistoryData = [];

async function loadSprintHistory() {
  try {
    const res = await fetch('/api/sprint-history');
    if (!res.ok) throw new Error(await res.text());
    _sprintHistoryData = await res.json();
    renderSprintHistory(_sprintHistoryData);
  } catch {
    // silently leave existing content
  }
}

function _statusBadgeHtml(status) {
  const map = {
    complete:   ['green',  'complete'],
    stopped:    ['amber',  'stopped'],
    'budget-hit': ['red', 'budget-hit'],
    unknown:    ['gray',   'unknown'],
  };
  const [color, label] = map[status] || ['gray', status || 'unknown'];
  return `<span class="sbadge ${color}">${escapeHtml(label)}</span>`;
}

function renderSprintHistory(data) {
  const emptyEl = document.getElementById('history-empty');
  const rowsEl  = document.getElementById('history-rows');
  const statsRow = document.getElementById('history-stats-row');
  if (!rowsEl) return;

  if (!data || data.length === 0) {
    if (emptyEl) emptyEl.style.display = '';
    rowsEl.innerHTML = '';
    // Quick-stats: zero
    const avgEl   = document.getElementById('h-avg-duration');
    const tokEl   = document.getElementById('h-tokens-month');
    if (avgEl) avgEl.textContent = '—';
    if (tokEl) tokEl.textContent = '—';
    return;
  }

  if (emptyEl) emptyEl.style.display = 'none';

  // AC-5a: quick-stats
  _renderHistoryStats(data);

  // AC-5b: sprint rows
  rowsEl.innerHTML = data.map((sprint, idx) => _sprintRowHtml(sprint, idx)).join('');
}

function _renderHistoryStats(data) {
  // Avg sprint duration: not directly stored, so we skip if no duration data
  // (sprint_manager doesn't store wall_clock in summary files currently — show count)
  const avgEl  = document.getElementById('h-avg-duration');
  const tokEl  = document.getElementById('h-tokens-month');

  if (avgEl) {
    if (data.length > 0) {
      avgEl.textContent = `${data.length} sprint${data.length !== 1 ? 's' : ''} recorded`;
    } else {
      avgEl.textContent = '—';
    }
  }

  // Total tokens this calendar month
  const now        = new Date();
  const thisMonth  = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  let monthTokens  = 0;
  data.forEach(s => {
    if (s.date && s.date.startsWith(thisMonth)) {
      monthTokens += s.total_tokens || 0;
    }
  });
  if (tokEl) {
    tokEl.textContent = monthTokens > 0 ? monthTokens.toLocaleString() : '—';
  }
}

function _reviewerBadgeHtml(sprint) {
  const rs = sprint.reviewer_status;
  if (!rs || rs === 'skipped') return '<span class="history-reviewer reviewer-skipped">—</span>';
  if (rs === 'failed') return '<span class="history-reviewer reviewer-failed">review failed</span>';
  const f = sprint.reviewer_findings || {};
  const b = f.blockers    ?? 0;
  const s = f.suggestions ?? 0;
  const url = sprint.reviewer_comment_url;
  const label = `${b}B · ${s}S`;
  if (url) {
    return `<a class="history-reviewer reviewer-done${b > 0 ? ' reviewer-has-blockers' : ''}" href="${escapeHtml(url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${escapeHtml(label)}</a>`;
  }
  return `<span class="history-reviewer reviewer-done${b > 0 ? ' reviewer-has-blockers' : ''}">${escapeHtml(label)}</span>`;
}

function _sprintRowHtml(sprint, idx) {
  const n       = sprint.sprint_num != null ? sprint.sprint_num : '?';
  const date    = sprint.date || '—';
  const status  = sprint.status || 'unknown';
  const shipped = sprint.shipped_count ?? 0;
  const skipped = sprint.skipped_count ?? 0;
  const hasBlockers = (sprint.reviewer_findings?.blockers ?? 0) > 0;

  return `
    <div class="history-row${hasBlockers ? ' reviewer-blockers-row' : ''}" id="history-row-${idx}" onclick="toggleHistoryRow(${idx})">
      <div class="history-row-header">
        <span class="history-sprint-num">Sprint ${escapeHtml(String(n))}${hasBlockers ? ' <span class="reviewer-red-dot" title="Reviewer found blockers"></span>' : ''}</span>
        <span class="history-date">${escapeHtml(date)}</span>
        <span class="history-status">${_statusBadgeHtml(status)}</span>
        <span class="history-shipped">&#10003; ${shipped} shipped</span>
        <span class="history-skipped">${skipped > 0 ? skipped + ' skipped' : '—'}</span>
        <span class="history-reviewer-col">${_reviewerBadgeHtml(sprint)}</span>
        <span class="history-chevron"><i class="ti ti-chevron-down"></i></span>
      </div>
      <div class="history-expand-panel" id="history-expand-${idx}">
        <div id="history-content-${idx}">Loading…</div>
      </div>
    </div>`;
}

// ── Plan Sprint (issue #31) ───────────────────────────────────────────────────

const PSP_DRAFT_KEY = 'commander:plan-sprint-draft';
const TOKEN_MAP = { S: 20, M: 40, L: 60, XL: 85 };
const FILE_EXT_PAT = String.raw`\b[\w.-]+\.(?:py|html|js|sh|md|json)\b`;
const STATUS_LABELS_SET = new Set(['in-progress', 'SIT', 'UAT', 'UAT-approved', 'need-rework', 'blocked', 'backlog', 'enhancement', 'bug']);
const SPRINT_NUM_RE = /^sprint-(\d+)$/;

let _pspIssues = [];          // all open issues from /api/open-issues
let _pspSprints = [];         // sprint numbers from /api/sprints
let _pspTargetSprint = null;  // int: next sprint number
let _pspSelected = new Set(); // selected issue numbers (session)
let _pspFilter = 'all';       // 'all' | 'backlog' | 'in-sprint' | label string
let _pspLabelFilter = '';     // label name from dropdown
let _pspSearch = '';          // text search

// ── fetch / render ────────────────────────────────────────────────────────────

async function loadSprintPlanning() {
  const listEl = document.getElementById('psp-issue-list');
  if (listEl) listEl.innerHTML = '<div class="psp-loading">Loading issues…</div>';
  _pspShowError('');
  try {
    const [issRes, spRes] = await Promise.all([
      fetch('/api/open-issues'),
      fetch('/api/sprints'),
    ]);
    if (!issRes.ok) throw new Error(await issRes.text());
    if (!spRes.ok) throw new Error(await spRes.text());
    _pspIssues = await issRes.json();
    const spData = await spRes.json();
    _pspSprints = spData.sprints || [];
    _pspTargetSprint = (_pspSprints.length > 0 ? Math.max(..._pspSprints) : 0) + 1;
  } catch (e) {
    _pspShowError('Failed to load: ' + e.message);
    return;
  }
  _pspRestoreDraft();
  _pspRender();
}

function _pspRender() {
  // Update title
  const titleEl = document.getElementById('psp-title');
  if (titleEl) titleEl.textContent = `Plan sprint ${_pspTargetSprint}`;

  // Update "In sprint N" filter tab label
  const inSprintTab = document.querySelector('.psp-ftab-sprint');
  if (inSprintTab) inSprintTab.textContent = `In sprint ${_pspTargetSprint}`;

  // Update Start button label
  const startBtn = document.getElementById('psp-start-btn');
  if (startBtn) startBtn.textContent = `Start sprint ${_pspTargetSprint}`;

  // Populate label filter dropdown
  _pspBuildLabelDropdown();

  _pspRenderList();
  _pspUpdateStrip();
  _pspUpdateConflicts();
  _pspUpdateBulkBar();
  _pspSaveDraft();
}

function _pspRenderList() {
  const issues = _pspFilteredIssues();
  const listEl = document.getElementById('psp-issue-list');
  if (!listEl) return;

  if (issues.length === 0) {
    listEl.innerHTML = '<div class="psp-loading">No issues match.</div>';
    return;
  }

  const conflicts = _pspConflictSet();
  listEl.innerHTML = issues.map(iss => _pspRowHtml(iss, conflicts)).join('');
}

function _pspFilteredIssues() {
  const q = _pspSearch.toLowerCase();
  return _pspIssues.filter(iss => {
    const labelNames = (iss.labels || []).map(l => l.name);
    const sprintNum = _pspIssueSprintNum(iss);

    if (_pspFilter === 'all') { /* no-op */ }
    else if (_pspFilter === 'backlog') {
      if (!labelNames.includes('backlog')) return false;
    } else if (_pspFilter === 'in-sprint') {
      if (sprintNum !== _pspTargetSprint) return false;
    }

    if (_pspLabelFilter && !labelNames.includes(_pspLabelFilter)) return false;

    if (q && !iss.title.toLowerCase().includes(q) && !String(iss.number).includes(q)) return false;

    return true;
  });
}

function _pspIssueSprintNum(iss) {
  for (const lbl of (iss.labels || [])) {
    const m = SPRINT_NUM_RE.exec(lbl.name);
    if (m) return parseInt(m[1], 10);
  }
  return null;
}

function _pspIssueSize(iss) {
  for (const lbl of (iss.labels || [])) {
    if (['S', 'M', 'L', 'XL'].includes(lbl.name)) return lbl.name;
  }
  return null;
}

function _pspTokenEst(iss) {
  const body = iss.body || '';
  const m = /<!--\s*tokens:(\d+)\s*-->/.exec(body);
  if (m) return parseInt(m[1], 10);
  const size = _pspIssueSize(iss);
  return TOKEN_MAP[size] || TOKEN_MAP['M'];
}

function _pspConflictSet() {
  const selected = _pspIssues.filter(i => _pspSelected.has(i.number));
  const fileMap = {};
  for (const iss of selected) {
    const files = new Set((iss.body || '').match(new RegExp(FILE_EXT_PAT, 'g')) || []);
    for (const f of files) {
      if (!fileMap[f]) fileMap[f] = [];
      fileMap[f].push(iss.number);
    }
  }
  const conflicting = new Set();
  const pairs = [];
  for (const [file, nums] of Object.entries(fileMap)) {
    if (nums.length > 1) {
      for (let i = 0; i < nums.length; i++)
        for (let j = i + 1; j < nums.length; j++) {
          conflicting.add(nums[i]);
          conflicting.add(nums[j]);
          pairs.push({ a: nums[i], b: nums[j], file });
        }
    }
  }
  return { conflicting, pairs };
}

function _pspRowHtml(iss, { conflicting }) {
  const isSelected = _pspSelected.has(iss.number);
  const sprintNum  = _pspIssueSprintNum(iss);
  const inSprint   = sprintNum === _pspTargetSprint;
  const hasConflict = conflicting.has(iss.number);

  let cls = 'psp-row';
  if (inSprint)    cls += ' psp-in-sprint';
  if (isSelected)  cls += ' psp-selected';
  if (hasConflict) cls += ' psp-has-conflict';

  const size = _pspIssueSize(iss) || '?';
  const sizeClass = size === '?' ? 'psp-size-q' : `psp-size-${size}`;
  const tokens = _pspTokenEst(iss);

  const nonStatusLabels = (iss.labels || []).filter(l => !STATUS_LABELS_SET.has(l.name) && !SPRINT_NUM_RE.test(l.name));
  const labelPills = nonStatusLabels.slice(0, 3).map(l =>
    `<span class="psp-lbl-pill">${escapeHtml(l.name)}</span>`
  ).join('');

  return `
    <div class="${escapeHtml(cls)}" id="psp-row-${iss.number}" onclick="pspToggleRow(event, ${iss.number})">
      <input type="checkbox" class="psp-row-cb" ${isSelected ? 'checked' : ''}
             onclick="event.stopPropagation(); pspToggleRow(event, ${iss.number})">
      <a class="psp-row-num" href="${escapeHtml(iss.url || '#')}" target="_blank" rel="noopener"
         onclick="event.stopPropagation()">#${iss.number}</a>
      <span class="psp-row-title">${escapeHtml(iss.title || '')}</span>
      <div class="psp-row-meta">
        <div class="psp-row-labels">${labelPills}</div>
        <span class="psp-size-pill ${sizeClass}">${escapeHtml(size)}</span>
        <span class="psp-token-est">${tokens} k</span>
        <span class="psp-conflict-icon" title="File conflict"><i class="ti ti-alert-triangle"></i></span>
        <button class="psp-row-add" onclick="event.stopPropagation(); pspAddRowToSprint(${iss.number})">Add</button>
      </div>
    </div>`;
}

function _pspUpdateStrip() {
  const sel = _pspIssues.filter(i => _pspSelected.has(i.number));
  const count = sel.length;
  const tokens = sel.reduce((s, i) => s + _pspTokenEst(i), 0);
  const mins = count * 30;
  const h = Math.floor(mins / 60), m = mins % 60;
  const { pairs } = _pspConflictSet();
  const conflictCount = pairs.length;

  document.getElementById('psp-count').textContent = count;
  document.getElementById('psp-tokens').textContent = tokens + ' k';
  document.getElementById('psp-duration').textContent = `${h}h ${m}m`;
  document.getElementById('psp-conflicts').textContent = conflictCount;

  const card = document.getElementById('psp-conflicts-card');
  if (card) card.classList.toggle('has-conflicts', conflictCount > 0);
}

function _pspUpdateConflicts() {
  const { pairs } = _pspConflictSet();
  const banner = document.getElementById('psp-conflict-banner');
  const list   = document.getElementById('psp-conflict-list');
  if (!banner || !list) return;

  if (pairs.length === 0) {
    banner.classList.add('hidden');
    return;
  }

  banner.classList.remove('hidden');
  list.innerHTML = pairs.map(p =>
    `<li>Issue #${p.a} ↔ #${p.b} — shared: ${escapeHtml(p.file)}</li>`
  ).join('');
}

function _pspUpdateBulkBar() {
  const bar = document.getElementById('psp-bulk-bar');
  const countEl = document.getElementById('psp-bulk-count');
  if (!bar) return;
  const n = _pspSelected.size;
  bar.classList.toggle('hidden', n === 0);
  if (countEl) countEl.textContent = `${n} selected`;
}

function _pspBuildLabelDropdown() {
  const sel = document.getElementById('psp-label-select');
  if (!sel) return;
  const allLabels = new Set();
  for (const iss of _pspIssues) {
    for (const l of (iss.labels || [])) {
      if (!STATUS_LABELS_SET.has(l.name) && !SPRINT_NUM_RE.test(l.name)) {
        allLabels.add(l.name);
      }
    }
  }
  const current = sel.value;
  sel.innerHTML = '<option value="">By label…</option>' +
    [...allLabels].sort().map(l => `<option value="${escapeHtml(l)}">${escapeHtml(l)}</option>`).join('');
  if (current && allLabels.has(current)) sel.value = current;
}

// ── Row interaction ───────────────────────────────────────────────────────────

function pspToggleRow(e, issueNum) {
  if (_pspSelected.has(issueNum)) {
    _pspSelected.delete(issueNum);
  } else {
    _pspSelected.add(issueNum);
  }
  _pspUpdateRowState(issueNum);
  _pspUpdateStrip();
  _pspUpdateConflicts();
  _pspUpdateBulkBar();
  // re-render conflict icons on all rows
  const { conflicting } = _pspConflictSet();
  for (const iss of _pspIssues) {
    const row = document.getElementById(`psp-row-${iss.number}`);
    if (row) row.classList.toggle('psp-has-conflict', conflicting.has(iss.number));
  }
  _pspSaveDraft();
}

function _pspUpdateRowState(issueNum) {
  const row = document.getElementById(`psp-row-${issueNum}`);
  if (!row) return;
  const cb = row.querySelector('.psp-row-cb');
  const isSelected = _pspSelected.has(issueNum);
  if (cb) cb.checked = isSelected;
  row.classList.toggle('psp-selected', isSelected);
}

// ── Filters ───────────────────────────────────────────────────────────────────

function pspSetFilter(filter) {
  _pspFilter = filter;
  _pspLabelFilter = '';
  document.getElementById('psp-label-select').value = '';
  document.querySelectorAll('.psp-ftab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.filter === filter);
  });
  _pspRenderList();
}

function pspSetLabelFilter(val) {
  _pspLabelFilter = val;
  _pspFilter = val ? '' : 'all';
  document.querySelectorAll('.psp-ftab').forEach(btn => btn.classList.remove('active'));
  _pspRenderList();
}

function pspOnSearch() {
  _pspSearch = document.getElementById('psp-search').value;
  _pspRenderList();
}

// ── Goal input ────────────────────────────────────────────────────────────────

function pspOnGoalInput() {
  const val = (document.getElementById('psp-goal').value || '').trim();
  const btn = document.getElementById('psp-start-btn');
  if (btn) btn.disabled = val.length === 0;
  _pspSaveDraft();
}

// ── Add to sprint ─────────────────────────────────────────────────────────────

async function pspAddRowToSprint(issueNum) {
  await _pspAddIssuesToSprint([issueNum]);
}

async function pspBulkAddToSprint() {
  await _pspAddIssuesToSprint([..._pspSelected]);
}

async function _pspAddIssuesToSprint(issueNums) {
  _pspShowError('');
  const N = _pspTargetSprint;
  let failed = false;
  for (const num of issueNums) {
    try {
      const res = await fetch(`/api/issues/${num}/sprint-label`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sprint: N }),
      });
      if (!res.ok) throw new Error(await res.text());
      // Mark row as in-sprint, deselect
      _pspSelected.delete(num);
      // Update issue labels locally so row turns green immediately
      const iss = _pspIssues.find(i => i.number === num);
      if (iss && !iss.labels.some(l => l.name === `sprint-${N}`)) {
        iss.labels.push({ name: `sprint-${N}` });
      }
      const row = document.getElementById(`psp-row-${num}`);
      if (row) {
        row.classList.remove('psp-selected');
        row.classList.add('psp-in-sprint');
        const cb = row.querySelector('.psp-row-cb');
        if (cb) cb.checked = false;
      }
    } catch (e) {
      failed = true;
      _pspShowError(`Failed to label #${num}: ${e.message}`);
    }
  }
  _pspUpdateStrip();
  _pspUpdateConflicts();
  _pspUpdateBulkBar();
  _pspSaveDraft();
}

// ── Bulk size mark ────────────────────────────────────────────────────────────

async function pspBulkMarkSize(size) {
  if (!size) return;
  // Size marking via label — call sprint-planning/assign for now;
  // just update locally for visual feedback (GitHub label management is separate scope)
  for (const num of _pspSelected) {
    const iss = _pspIssues.find(i => i.number === num);
    if (!iss) continue;
    iss.labels = iss.labels.filter(l => !['S', 'M', 'L', 'XL'].includes(l.name));
    iss.labels.push({ name: size });
    const row = document.getElementById(`psp-row-${num}`);
    if (row) {
      const pill = row.querySelector('.psp-size-pill');
      if (pill) {
        pill.className = `psp-size-pill psp-size-${size}`;
        pill.textContent = size;
      }
      const tok = row.querySelector('.psp-token-est');
      if (tok) tok.textContent = TOKEN_MAP[size] + ' k';
    }
  }
  _pspUpdateStrip();
}

// ── Clear selection ───────────────────────────────────────────────────────────

function pspClearSelection() {
  const prev = [..._pspSelected];
  _pspSelected.clear();
  prev.forEach(n => _pspUpdateRowState(n));
  _pspUpdateStrip();
  _pspUpdateConflicts();
  _pspUpdateBulkBar();
  _pspSaveDraft();
}

// ── Start sprint ──────────────────────────────────────────────────────────────

function pspStartSprint() {
  const goal = (document.getElementById('psp-goal').value || '').trim();
  if (!goal) return;
  const N = _pspTargetSprint;
  document.getElementById('psp-dialog-title').textContent = `Start sprint ${N}?`;
  document.getElementById('psp-dialog-msg').textContent =
    `Goal: "${goal}" — ${_pspSelected.size} issues selected.`;
  document.getElementById('psp-dialog-backdrop').classList.remove('hidden');
  document.getElementById('psp-dialog').classList.remove('hidden');
}

function pspCloseDialog() {
  document.getElementById('psp-dialog-backdrop').classList.add('hidden');
  document.getElementById('psp-dialog').classList.add('hidden');
}

async function pspConfirmStart() {
  const goal = (document.getElementById('psp-goal').value || '').trim();
  const N = _pspTargetSprint;
  const btn = document.getElementById('psp-dialog-confirm');
  const startBtn = document.getElementById('psp-start-btn');
  pspCloseDialog();

  if (startBtn) { startBtn.disabled = true; startBtn.textContent = 'Launching…'; }
  try {
    const res = await fetch('/api/sprint-run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label: `sprint-${N}`, goal }),
    });
    if (!res.ok) throw new Error(await res.text());
  } catch (e) {
    _pspShowError('Failed to start sprint: ' + e.message);
  } finally {
    if (startBtn) {
      const goalNow = (document.getElementById('psp-goal').value || '').trim();
      startBtn.disabled = goalNow.length === 0;
      startBtn.textContent = `Start sprint ${N}`;
    }
  }
}

// ── LocalStorage draft ────────────────────────────────────────────────────────

function _pspSaveDraft() {
  try {
    localStorage.setItem(PSP_DRAFT_KEY, JSON.stringify({
      selected: [..._pspSelected],
      goal: document.getElementById('psp-goal')?.value || '',
      sprint: _pspTargetSprint,
    }));
  } catch { /* ignore quota errors */ }
}

function _pspRestoreDraft() {
  try {
    const raw = localStorage.getItem(PSP_DRAFT_KEY);
    if (!raw) return;
    const draft = JSON.parse(raw);
    if (draft.sprint === _pspTargetSprint && Array.isArray(draft.selected)) {
      for (const n of draft.selected) {
        if (_pspIssues.some(i => i.number === n)) _pspSelected.add(n);
      }
    }
    const goalEl = document.getElementById('psp-goal');
    if (goalEl && draft.goal) {
      goalEl.value = draft.goal;
      pspOnGoalInput();
    }
  } catch { /* ignore */ }
}

// ── Error helper ──────────────────────────────────────────────────────────────

function _pspShowError(msg) {
  const el = document.getElementById('psp-error');
  if (!el) return;
  el.textContent = msg;
  el.classList.toggle('hidden', !msg);
}

function toggleHistoryRow(idx) {
  const row    = document.getElementById(`history-row-${idx}`);
  const panel  = document.getElementById(`history-expand-${idx}`);
  if (!row || !panel) return;

  const isExpanded = row.classList.contains('expanded');
  if (isExpanded) {
    row.classList.remove('expanded');
  } else {
    row.classList.add('expanded');
    _loadHistoryRowContent(idx);
  }
}

async function _loadHistoryRowContent(idx) {
  const contentEl = document.getElementById(`history-content-${idx}`);
  if (!contentEl) return;

  const sprint = _sprintHistoryData[idx];
  if (!sprint) {
    contentEl.innerHTML = '<div class="empty-small">No data.</div>';
    return;
  }

  // Fetch the summary file content via the existing /api/sprint-summary endpoint
  // or parse from the sprint data. Since we don't have per-sprint content endpoint
  // we embed file_path and read from sprint-history.
  // The API returns file_path — fetch the content from /api/sprint-summary only for latest.
  // For history items we show whatever the server returned; content is in the summary file.
  // We'll make a best-effort request to read via a dedicated per-sprint path.
  let summaryContent = null;
  try {
    // Try /api/sprint-summary-file?path=<encoded>
    // (We'll add this endpoint below, or fallback gracefully.)
    const res = await fetch(
      `/api/sprint-history-content?idx=${idx}&sprint_num=${encodeURIComponent(sprint.sprint_num ?? '')}`
    );
    if (res.ok) {
      const d = await res.json();
      summaryContent = d.content;
    }
  } catch { /* ignore */ }

  const ghLink = sprint.github_issue_url
    ? `<a class="history-gh-link" href="${escapeHtml(sprint.github_issue_url)}" target="_blank" rel="noopener">
         <i class="ti ti-brand-github"></i> View on GitHub
       </a>`
    : '';

  const contentHtml = summaryContent
    ? `<pre class="history-summary-pre">${escapeHtml(summaryContent)}</pre>`
    : `<div class="empty-small" style="margin-bottom:8px;">Summary content not available in this view.</div>`;

  contentEl.innerHTML = contentHtml + ghLink;
}

// ── Sprint Management (issue #95) ─────────────────────────────────────────────

let _smgmtCurrentRepo    = null;   // "owner/repo" currently displayed
let _smgmtData           = null;   // { sprints, order, issues, empty_sprint_labels, placeholder_sprint } from API
let _smgmtDragSprint     = null;   // sprint label currently being drag-reordered
let _smgmtDragTicket     = null;   // { number, fromSprint } being dragged
// Per-#123: replaced single _smgmtRunningInfo with a map of ALL running sprints:
//   key = "project:sprint_label", value = { project, sprint_label }
let _smgmtAllRunning     = {};     // map: "project:sprint_label" -> {project, sprint_label}
let _smgmtPollTimer      = null;
let _smgmtGoals          = {};     // sprint_label -> goal string
let _smgmtGoalSaveTimers = {};     // sprint_label -> debounce timer id
let _smgmtEstimates      = {};     // sprint_label -> EstimateResult from /api/sprints/{label}/estimate
let _smgmtSprintStates   = {};     // sprint_label -> {wall_clock_secs, issues:[{number,duration_secs,failed}]} (issue #212)
let _smgmtBacklogFilter  = 'all';  // 'all' | 'unestimated' | 'attachments'
let _smgmtBacklogExpanded = false; // expand/collapse state (sticky mode only)
let _smgmtBacklogDragRestoreTimer = null; // timer to restore sticky after drag
let _smgmtRerunLabel     = null;   // sprint label pending rerun confirmation
let _smgmtCleanupLabels  = [];     // empty sprint labels pending cleanup confirmation
let _smgmtAutoRefreshTimer     = null; // interval timer for auto-refresh polling
let _smgmtAutoRefreshInterval  = 15;  // seconds between refreshes (5|15|30|60)
let _smgmtAutoRefreshEnabled   = true; // whether auto-refresh is active
let _smgmtSelectedIssues       = new Set(); // issue numbers currently selected (multi-select, issue #206)
let _smgmtDragTickets          = [];   // [{number,fromSprint}] for multi-ticket drag (issue #363)
let _smgmtLastClickedIssue     = null; // last clicked issue for Shift+Click range select (issue #363)
let _smgmtGhostNextN           = null; // next-free sprint number computed once at drag-start (issue #364)
let _smgmtGhostPendingTickets  = null; // [{number,fromSprint,...}] queued for ghost confirm modal
let _smgmtGhostPendingN        = null; // sprint N for the pending ghost confirm

function _rerunPolicyAction(labelNames) {
  const s = new Set(labelNames);
  if (s.has('UAT') || s.has('UAT-approved')) return 'skip';
  if (s.has('SIT')) return 'dispatch_tester';
  return 'dispatch_coder';
}

function sprintLabelCompare(a, b) {
  const parse = (l) => {
    const m = l.match(/^sprint-(\d+)(?:\.(\d+))?$/);
    if (!m) return [0, 0];
    return [parseInt(m[1], 10), m[2] ? parseInt(m[2], 10) : 0];
  };
  const [an, ax] = parse(a);
  const [bn, bx] = parse(b);
  return an !== bn ? an - bn : ax - bx;
}

function sprintLabelDisplay(label) {
  const m = label.match(/^sprint-(\d+)(?:\.(\d+))?$/);
  if (!m) return label;
  return m[2] ? `Sprint ${m[1]}.${m[2]}` : `Sprint ${m[1]}`;
}

function _smgmtComputeNextFreeSprint() {
  if (!_smgmtData) return 1;
  const used = new Set(_smgmtData.sprints || []);
  for (const l of (_smgmtData.order || [])) {
    const m = l.match(/^sprint-(\d+)$/);
    if (m) used.add(parseInt(m[1], 10));
  }
  let n = 1;
  while (used.has(n)) n++;
  return n;
}

async function smgmtInit() {
  // Legacy: called with no repo; use current project or first project
  const repo = _activeProject || _smgmtCurrentRepo || (allProjects[0]?.repo) || null;
  await smgmtInitForProject(repo);
}

async function smgmtInitForProject(repo) {
  if (!repo) {
    document.getElementById('smgmt-loading').textContent = 'No project selected.';
    smgmtAutoRefreshStop();
    _smgmtUpdateAutoRefreshBtn();
    return;
  }
  _smgmtCurrentRepo = repo;
  smgmtShowError('');

  if (allProjects.length === 0) {
    try {
      const res = await fetch('/api/projects');
      if (!res.ok) throw new Error('Failed to load projects');
      const data = await res.json();
      allProjects = data.projects || [];
    } catch (e) {
      smgmtShowError('Failed to load projects: ' + e.message);
    }
  }

  await smgmtSelectProject(repo);

  // Start polling running status
  if (_smgmtPollTimer) clearInterval(_smgmtPollTimer);
  _smgmtPollTimer = setInterval(smgmtPollRunStatus, 5000);
  smgmtPollRunStatus();

  // Load persisted auto-refresh state and start poller (issue #240)
  _smgmtArLoadState();
  smgmtAutoRefreshReset();
}

// ── Partial refresh (issue #179) ─────────────────────────────────────────────
async function smgmtRefreshBoard() {
  if (!_smgmtCurrentRepo) return;
  // Don't clobber the DOM while a ticket drag is in flight (issue #340)
  if (_smgmtDragTicket) return;
  await smgmtSelectProject(_smgmtCurrentRepo);
}

// ── Auto-refresh compact pill (issue #240) ────────────────────────────────────

const _AR_LS_INTERVAL = 'commander.smgmt.arInterval';
const _AR_LS_ENABLED  = 'commander.smgmt.arEnabled';
const _AR_INTERVALS   = [5, 15, 30, 60]; // seconds

/** Load persisted state from localStorage; use defaults if absent. */
function _smgmtArLoadState() {
  const savedInterval = parseInt(localStorage.getItem(_AR_LS_INTERVAL), 10);
  if (_AR_INTERVALS.includes(savedInterval)) _smgmtAutoRefreshInterval = savedInterval;
  const savedEnabled = localStorage.getItem(_AR_LS_ENABLED);
  if (savedEnabled !== null) _smgmtAutoRefreshEnabled = savedEnabled !== 'false';
}

/** Save current state to localStorage. */
function _smgmtArSaveState() {
  localStorage.setItem(_AR_LS_INTERVAL, String(_smgmtAutoRefreshInterval));
  localStorage.setItem(_AR_LS_ENABLED,  String(_smgmtAutoRefreshEnabled));
}

/** Update pill appearance and dropdown checkmarks to match current state. */
function _smgmtUpdateAutoRefreshBtn() {
  const pill      = document.getElementById('smgmt-ar-pill');
  const pillLabel = document.getElementById('smgmt-ar-pill-label');
  if (!pill || !pillLabel) return;

  if (_smgmtAutoRefreshEnabled) {
    pill.classList.replace('is-off', 'is-on');
    pill.classList.add('is-on');
    const secs = _smgmtAutoRefreshInterval;
    pillLabel.textContent = secs >= 60 ? `${secs / 60}m` : `${secs}s`;
  } else {
    pill.classList.replace('is-on', 'is-off');
    pill.classList.add('is-off');
    pillLabel.textContent = 'off';
  }

  // Update checkmarks on menu items
  document.querySelectorAll('.smgmt-ar-menu-item[data-interval]').forEach(item => {
    const iv = parseInt(item.dataset.interval, 10);
    item.classList.toggle('is-active', _smgmtAutoRefreshEnabled && iv === _smgmtAutoRefreshInterval);
  });
}

function smgmtAutoRefreshStop() {
  if (_smgmtAutoRefreshTimer) {
    clearInterval(_smgmtAutoRefreshTimer);
    _smgmtAutoRefreshTimer = null;
  }
}

function smgmtAutoRefreshStart() {
  smgmtAutoRefreshStop();
  if (!_smgmtCurrentRepo || !_smgmtAutoRefreshEnabled) return;
  _smgmtAutoRefreshTimer = setInterval(async () => {
    await smgmtRefreshBoard();
  }, _smgmtAutoRefreshInterval * 1000);
}

function smgmtAutoRefreshReset() {
  smgmtAutoRefreshStart();
  _smgmtUpdateAutoRefreshBtn();
}

async function smgmtAutoRefreshNow() {
  smgmtAutoRefreshReset();
  await smgmtRefreshBoard();
}

/** Called when pill button is clicked — toggle menu open/closed. */
function smgmtArPillClick(event) {
  event.stopPropagation();
  const menu = document.getElementById('smgmt-ar-menu');
  const pill = document.getElementById('smgmt-ar-pill');
  if (!menu) return;
  const isOpen = !menu.classList.contains('hidden');
  if (isOpen) {
    menu.classList.add('hidden');
    pill.setAttribute('aria-expanded', 'false');
  } else {
    _smgmtUpdateAutoRefreshBtn(); // refresh checkmarks before showing
    menu.classList.remove('hidden');
    pill.setAttribute('aria-expanded', 'true');
  }
}

/** Called when a menu interval option is selected. */
function smgmtArSelect(seconds) {
  _smgmtAutoRefreshInterval = seconds;
  _smgmtAutoRefreshEnabled  = true;
  _smgmtArSaveState();
  _smgmtArCloseMenu();
  smgmtAutoRefreshReset();
}

/** Called when "Turn off" is selected. */
function smgmtArTurnOff() {
  _smgmtAutoRefreshEnabled = false;
  _smgmtArSaveState();
  smgmtAutoRefreshStop();
  _smgmtArCloseMenu();
  _smgmtUpdateAutoRefreshBtn();
}

function _smgmtArCloseMenu() {
  const menu = document.getElementById('smgmt-ar-menu');
  const pill = document.getElementById('smgmt-ar-pill');
  if (menu) menu.classList.add('hidden');
  if (pill) pill.setAttribute('aria-expanded', 'false');
}

// Close pill menu when clicking outside
document.addEventListener('click', function(e) {
  const wrap = document.getElementById('smgmt-ar-pill-wrap');
  if (wrap && !wrap.contains(e.target)) {
    _smgmtArCloseMenu();
  }
});

async function smgmtSelectProject(repo) {
  if (!repo) return;
  _smgmtCurrentRepo = repo;
  _smgmtGoals = {};
  _smgmtEstimates = {};
  _smgmtSprintStates = {};
  // Load persisted backlog state from localStorage (issue #225)
  const slug = (repo || '').split('/')[1] || repo || '';
  const savedFilter = localStorage.getItem(`commander.${slug}.backlogFilter`);
  _smgmtBacklogFilter = savedFilter || 'all';
  const savedExpanded = localStorage.getItem(`commander.${slug}.backlogExpanded`);
  _smgmtBacklogExpanded = savedExpanded === 'true';
  _smgmtSelectedIssues.clear();
  _smgmtUpdateSelectionUI();
  smgmtShowError('');
  const bodyEl = document.getElementById('smgmt-body');
  if (bodyEl) bodyEl.innerHTML = '<div class="smgmt-loading">Loading sprints…</div>';

  try {
    const res = await fetch(`/api/sprint-management/issues?repo=${encodeURIComponent(repo)}`);
    if (!res.ok) throw new Error(await res.text());
    _smgmtData = await res.json();
  } catch (e) {
    smgmtShowError('Failed to load sprints: ' + e.message);
    return;
  }

  await smgmtLoadGoals();
  await smgmtLoadEstimates();
  await smgmtLoadSprintStates();
  smgmtRender();
}

async function smgmtLoadEstimates() {
  if (!_smgmtData || !_smgmtCurrentRepo) return;
  const { order } = _smgmtData;
  if (!order || order.length === 0) return;

  // Fetch estimates for each sprint in parallel; silently ignore 404s (not yet generated)
  await Promise.all(order.map(async (label) => {
    try {
      const res = await fetch(
        `/api/sprints/${encodeURIComponent(label)}/estimate?project=${encodeURIComponent(_smgmtCurrentRepo)}`
      );
      if (res.ok) {
        _smgmtEstimates[label] = await res.json();
      }
      // 404 = not yet generated — treat as absent, no error
    } catch (_e) {
      // network error — treat as absent
    }
  }));
}

// ── Sprint timing state (issue #212) ─────────────────────────────────────────

/**
 * Format seconds into human-readable duration string.
 * Examples: 65 → "1m 05s", 3725 → "1h 02m 05s", 45 → "45s"
 */
function formatDuration(secs) {
  const s = Math.floor(secs);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) {
    return `${h}h ${String(m).padStart(2, '0')}m ${String(sec).padStart(2, '0')}s`;
  }
  if (m > 0) {
    return `${m}m ${String(sec).padStart(2, '0')}s`;
  }
  return `${sec}s`;
}

async function smgmtLoadSprintStates() {
  if (!_smgmtData || !_smgmtCurrentRepo) return;
  const { order } = _smgmtData;
  if (!order || order.length === 0) return;

  // Fetch state for each sprint in parallel; silently ignore 404s (no state file yet)
  await Promise.all(order.map(async (label) => {
    try {
      const res = await fetch(
        `/api/sprints/${encodeURIComponent(label)}/state?project=${encodeURIComponent(_smgmtCurrentRepo)}`
      );
      if (res.ok) {
        _smgmtSprintStates[label] = await res.json();
      }
      // 404 = no state file — sprint has not been run yet
    } catch (_e) {
      // network error — treat as absent
    }
  }));
}

/**
 * Apply duration badges to ticket rows and sprint duration footers to sprint cards.
 * Called after smgmtRender() so DOM elements exist.
 * Only applies to completed/done sprints; running sprints are handled by smgmtApplyRunState.
 */
function smgmtApplyDurationBadges() {
  const runningLabels = new Set(
    Object.values(_smgmtAllRunning)
      .filter(e => e.project === _smgmtCurrentRepo)
      .map(e => e.sprint_label)
  );

  for (const [label, state] of Object.entries(_smgmtSprintStates)) {
    // Skip running sprints — they have live badges via smgmtApplyRunState
    if (runningLabels.has(label)) continue;

    // Per-ticket duration badges
    const issueMap = {};
    for (const iss of (state.issues || [])) {
      issueMap[iss.number] = iss;
    }

    for (const [num, issState] of Object.entries(issueMap)) {
      const ticketEl = document.getElementById(`smgmt-ticket-${num}`);
      if (!ticketEl) continue;

      // Remove any existing duration badge (avoid duplicates on re-render)
      ticketEl.querySelectorAll('.smgmt-took-success, .smgmt-took-failed').forEach(el => el.remove());

      const dur = formatDuration(issState.duration_secs);
      const label_text = issState.failed ? `took ${dur} (failed)` : `took ${dur}`;
      const badge = document.createElement('span');
      badge.className = issState.failed ? 'smgmt-took-failed' : 'smgmt-took-success';
      badge.textContent = label_text;

      // Insert before the status badge
      const statusEl = ticketEl.querySelector('.smgmt-ticket-status');
      if (statusEl) {
        ticketEl.insertBefore(badge, statusEl);
      } else {
        ticketEl.appendChild(badge);
      }
    }

    // Sprint duration footer — only for completed (non-running) sprints
    if (!state.wall_clock_secs || state.wall_clock_secs <= 0) continue;

    const block = document.getElementById(`smgmt-block-${label}`);
    if (!block) continue;

    // Remove existing footer to avoid duplicates
    block.querySelectorAll('.smgmt-sprint-duration-footer').forEach(el => el.remove());

    const footer = document.createElement('div');
    footer.className = 'smgmt-sprint-duration-footer';
    footer.textContent = `Total sprint duration: ${formatDuration(state.wall_clock_secs)}`;
    block.appendChild(footer);
  }
}

function smgmtRender() {
  if (!_smgmtData) return;
  const { order, issues, empty_sprint_labels, placeholder_sprint } = _smgmtData;
  const bodyEl = document.getElementById('smgmt-body');
  if (!bodyEl) return;

  // Show cleanup banner for stale empty sprint labels
  const cleanupBanner = document.getElementById('smgmt-cleanup-banner');
  const emptyLabels = empty_sprint_labels || [];
  if (cleanupBanner) {
    if (emptyLabels.length > 0) {
      const count = emptyLabels.length;
      cleanupBanner.innerHTML = `
        <i class="ti ti-alert-triangle"></i>
        ${count} empty sprint label${count !== 1 ? 's' : ''} detected (${emptyLabels.join(', ')}) —
        <button class="smgmt-cleanup-link" onclick="smgmtCleanupOpen()">Clean up empty sprints</button>
      `;
      cleanupBanner.classList.remove('hidden');
    } else {
      cleanupBanner.classList.add('hidden');
    }
  }


  // Build map: sprint_label -> issues[]
  const bySprintLabel = {};
  for (const label of order) bySprintLabel[label] = [];
  const unassigned = [];
  for (const iss of issues) {
    // Prefer the exact sprint_label field (supports dotted sub-labels like sprint-15.1)
    const label = iss.sprint_label || (iss.sprint != null ? `sprint-${iss.sprint}` : null);
    if (label && bySprintLabel[label]) {
      bySprintLabel[label].push(iss);
    } else if (label == null) {
      unassigned.push(iss);
    }
  }

  // NEXT-UP: lowest sprint label with >= 1 ticket (natural sort order)
  const sortedOrderForNext = [...order].sort(sprintLabelCompare);
  let lowestLabel = null;
  for (const lbl of sortedOrderForNext) {
    if ((bySprintLabel[lbl] || []).length >= 1) {
      lowestLabel = lbl;
      break;
    }
  }

  // Identify empty plain sprint-N labels that exist on GitHub but have no tickets
  // These should be rendered as droppable placeholder blocks (issue #206)
  const orderBaseNums = new Set(
    order.map(l => { const m = l.match(/^sprint-(\d+)$/); return m ? parseInt(m[1], 10) : null; })
         .filter(n => n !== null)
  );
  const allBaseNums = order.length > 0
    ? Math.max(...order.map(l => { const m = l.match(/^sprint-(\d+)/); return m ? parseInt(m[1], 10) : 0; }))
    : 0;
  const placeholderN = placeholder_sprint || (allBaseNums + 1);
  const emptySprintNums = (_smgmtData.sprints || [])
    .filter(n => !orderBaseNums.has(n) && n !== placeholderN)
    .sort((a, b) => a - b);

  // Sort non-empty sprint labels naturally (sprint-N before sprint-N.1 before sprint-N+1)
  const sortedOrder = [...order].sort(sprintLabelCompare);

  // Build a merged list of all sprint entries (non-empty labels + empty plain sprints), sorted.
  // Each entry is: { label, isEmpty }
  const allSprintEntries = [
    ...sortedOrder.map(l => ({ label: l, isEmpty: false })),
    ...emptySprintNums.map(n => ({ label: `sprint-${n}`, isEmpty: true })),
  ].sort((a, b) => sprintLabelCompare(a.label, b.label));

  // Render sprint blocks (non-empty sprints and empty plain sprints)
  let blocksHtml = '';
  if (sortedOrder.length === 0 && emptySprintNums.length === 0) {
    blocksHtml = '<div class="smgmt-loading">No sprints found. Use "+ New sprint" to create one.</div>';
  } else {
    // Render all sprint entries (non-empty and empty) in natural sort order.
    // Empty sprints use the full pane (with Delete + disabled Run);
    // only the trailing placeholder uses smgmtPlaceholderBlockHtml.
    blocksHtml = allSprintEntries.map(({ label }) => {
      return smgmtSprintBlockHtml(label, bySprintLabel[label] || [], label === lowestLabel);
    }).join('');
  }

  // Append trailing placeholder card for the next-to-be-created sprint
  blocksHtml += smgmtPlaceholderBlockHtml(placeholderN);

  // Clean up existing live panel state before wiping the DOM
  // (DOM is about to be replaced; SSE connections and timers are explicitly closed)
  for (const label of Object.keys(_sllPanels)) {
    const ps = _sllPanels[label];
    if (ps) {
      if (ps.sse)          { try { ps.sse.close(); } catch {} ps.sse = null; }
      if (ps.tickInterval) { clearInterval(ps.tickInterval); ps.tickInterval = null; }
    }
    delete _sllPanels[label];
  }

  bodyEl.innerHTML = blocksHtml;

  // Restore placeholder visibility if a ticket drag is already in flight (issue #340)
  if (_smgmtDragTicket) {
    document.querySelectorAll('.smgmt-sprint-placeholder').forEach(p => { p.style.display = 'block'; });
  }

  smgmtRenderBacklog(unassigned);
  // Count total visible sprint blocks (non-placeholder) for sticky decision (issue #225)
  const totalSprints = allSprintEntries.length; // includes both non-empty and empty sprints
  smgmtBacklogApplyMode(totalSprints);
  smgmtApplyRunState();
  smgmtApplyDurationBadges();
  // Re-mount live panels for any currently-running sprints after render
  smgmtSyncLivePanels();
}

function smgmtHasCompletedTickets(tickets) {
  return tickets.some(t => {
    const action = _rerunPolicyAction((t.labels || []).map(l => l.name));
    return action === 'dispatch_tester' || action === 'skip';
  });
}

function smgmtSprintBlockHtml(label, tickets, isNext) {
  const displayName = sprintLabelDisplay(label);
  // Sanitize label for use in HTML element IDs (replace hyphens and dots with underscores)
  const safeIdSuffix = label.replace(/-/g, '_').replace(/\./g, '_');
  // Unified Run/Re-run button replaces the separate run + rerun buttons (issue #186)
  const actionBtnId  = `smgmt-run-btn-${safeIdSuffix}`;
  const deleteBtnId  = `smgmt-delete-btn-${safeIdSuffix}`;
  const finishBtnId  = `smgmt-finish-btn-${safeIdSuffix}`;
  const goalId       = `smgmt-goal-${safeIdSuffix}`;
  const savedGoal    = _smgmtGoals[label] || '';
  const hasCompleted = smgmtHasCompletedTickets(tickets);
  // hasCompleted → unified button shows "Re-run Sprint" and calls smgmtRerunSprint
  // otherwise     → unified button shows "Run Sprint"    and calls smgmtRunSprint
  const canRun = !hasCompleted && tickets.length >= 1;
  const hasGoal = savedGoal.trim().length > 0;

  // NEXT UP state badge (aria-label for accessibility)
  const nextBadge = isNext
    ? '<span class="smgmt-next-badge" aria-label="Next up">NEXT UP</span>'
    : '';

  let actionBtn;
  if (hasCompleted) {
    // NEXT UP state: Re-run Sprint (amber border)
    actionBtn = `<button class="smgmt-run-btn smgmt-run-btn--rerun smgmt-rerun-btn" id="${actionBtnId}"
                ${hasCompleted ? '' : 'disabled'}
                onclick="smgmtRerunSprint('${label}')">
                <i class="ti ti-refresh"></i> Re-run Sprint</button>`;
  } else {
    // Planned state: Run Sprint (neutral, disabled when no tickets or has completed)
    // issue #242: goal is no longer required to enable Run Sprint
    const runDisabled = !canRun ? 'disabled' : '';
    const runTitle = !canRun ? 'No tickets to run' : '';
    actionBtn = `<button class="smgmt-run-btn" id="${actionBtnId}"
                ${runDisabled} title="${runTitle}"
                onclick="smgmtRunSprint('${label}')">
                <i class="ti ti-player-play"></i> Run Sprint</button>`;
  }

  const ticketsHtml = tickets.length > 0
    ? tickets.map(t => smgmtTicketCardHtml(t, label)).join('')
    : '<div class="smgmt-drop-hint">Drop tickets here</div>';

  // Estimate summary for sprint block header
  const estimateData = _smgmtEstimates[label];
  let estimateSummaryHtml = '';
  if (estimateData && estimateData.total_minutes > 0) {
    const hrs  = Math.floor(estimateData.total_minutes / 60);
    const mins = estimateData.total_minutes % 60;
    const dur  = hrs > 0 ? `${hrs}h ${mins}m` : `${mins}m`;
    const cnt  = Object.keys(estimateData.estimates || {}).length;
    estimateSummaryHtml = `<span class="smgmt-estimate-total">estimated ${dur} across ${cnt} ticket${cnt !== 1 ? 's' : ''}</span>`;
  }

  // Sprint goal bar — visible read-only display; hidden input for saving
  const goalBarClass = hasGoal ? 'smgmt-sprint-goal-bar' : 'smgmt-sprint-goal-bar placeholder';
  const goalBarText = hasGoal
    ? escapeHtml(savedGoal)
    : 'Sprint goal (required to run) — e.g. Dashboard UX cleanup';

  return `
    <div class="smgmt-sprint-block" id="smgmt-block-${label}"
         ondragover="smgmtDragOverZone(event, '${label}')"
         ondragleave="smgmtDragLeave(event)"
         ondrop="smgmtDropOnSprint(event, '${label}')">
      <div class="smgmt-sprint-header"
           draggable="true"
           ondragstart="smgmtSprintDragStart(event, '${label}')"
           ondragend="smgmtSprintDragEnd(event)">
        <div class="smgmt-sprint-header-left">
          <i class="ti ti-grip-vertical smgmt-sprint-grip"></i>
          <span class="smgmt-sprint-name">${displayName}</span>
          <button class="smgmt-rename-btn" title="Rename sprint"
                  onclick="smgmtStartRename('${label}', event)"><i class="ti ti-pencil"></i></button>
          ${nextBadge}
          ${estimateSummaryHtml}
          <span class="smgmt-sprint-count">${tickets.length === 0 ? 'empty' : `${tickets.length} ticket${tickets.length !== 1 ? 's' : ''}`}</span>
        </div>
        <div class="smgmt-sprint-header-right">
          <button class="smgmt-finish-btn${hasCompleted ? '' : ' hidden'}" id="${finishBtnId}"
                  title="Finish sprint — add UAT label and close all open issues"
                  onclick="smgmtFinishSprint('${label}')">
            <i class="ti ti-flag-check"></i> Finish Sprint</button>
          <button class="smgmt-delete-btn" id="${deleteBtnId}"
                  title="Delete sprint"
                  onclick="smgmtDeleteSprint('${label}')">
            <i class="ti ti-trash"></i> Delete</button>
          ${actionBtn}
        </div>
      </div>
      <div class="${goalBarClass}" id="smgmt-goal-bar-${safeIdSuffix}" style="display:none">${goalBarText}</div>
      <div class="smgmt-sprint-goal-row" style="display:none">
        <input class="smgmt-goal-input" id="${goalId}" type="text"
               placeholder="Sprint goal (required to run) — e.g. Dashboard UX cleanup"
               value="${escapeHtml(savedGoal)}"
               oninput="smgmtGoalInput('${label}', this.value)" />
      </div>
      <div class="smgmt-sprint-tickets" id="smgmt-tickets-${label}">
        ${ticketsHtml}
      </div>
    </div>`;
}

function smgmtPlaceholderBlockHtml(n) {
  return `
    <div class="smgmt-sprint-block smgmt-sprint-placeholder" id="smgmt-block-ghost-next"
         style="display:none"
         ondragover="smgmtDragOverPlaceholder(event)"
         ondragleave="smgmtDragLeaveGhost(event)"
         ondrop="smgmtDropOnPlaceholder(event, ${n})">
      <div class="smgmt-ghost-content">
        <span class="smgmt-ghost-title" id="smgmt-ghost-title">Drop here to create Sprint ${n}</span>
        <span class="smgmt-ghost-sub" id="smgmt-ghost-sub">next free number</span>
      </div>
    </div>`;
}

// ── Sprint inline rename (issue #341) ────────────────────────────────────────

function smgmtStartRename(label, event) {
  event.stopPropagation();
  const block = document.getElementById(`smgmt-block-${label}`);
  if (!block) return;
  const nameEl = block.querySelector('.smgmt-sprint-name');
  if (!nameEl || nameEl.querySelector('.smgmt-rename-wrap')) return; // already editing

  const origN = parseInt(label.split('-')[1], 10);

  nameEl.innerHTML = `
    <span class="smgmt-rename-wrap">
      <span class="smgmt-rename-prefix">Sprint </span>
      <input class="smgmt-rename-input" id="smgmt-rename-input-${label}"
             type="text" inputmode="numeric" pattern="[0-9]*"
             value="${origN}" autocomplete="off" />
      <span class="smgmt-rename-error" id="smgmt-rename-err-${label}"></span>
    </span>`;

  const input = document.getElementById(`smgmt-rename-input-${label}`);
  input.focus();
  input.select();

  input.addEventListener('keydown', function(e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      smgmtCommitRename(label, origN, nameEl);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      smgmtCancelRename(label, origN, nameEl);
    }
  });

  input.addEventListener('input', function() {
    // strip non-numeric characters on input
    this.value = this.value.replace(/[^0-9]/g, '');
  });

  input.addEventListener('blur', function() {
    // Defer so Enter-key commit isn't pre-empted; skip if API call is in flight
    const el = this;
    setTimeout(() => {
      if (el.disabled) return;
      const wrap = nameEl.querySelector('.smgmt-rename-wrap');
      if (wrap) smgmtCancelRename(label, origN, nameEl);
    }, 150);
  });
}

function _smgmtSetRenameError(label, msg) {
  const errEl = document.getElementById(`smgmt-rename-err-${label}`);
  const input = document.getElementById(`smgmt-rename-input-${label}`);
  if (errEl) errEl.textContent = msg;
  if (input) input.classList.toggle('is-invalid', !!msg);
}

async function smgmtCommitRename(label, origN, nameEl) {
  const input = document.getElementById(`smgmt-rename-input-${label}`);
  if (!input) return;
  const raw = input.value.trim();

  if (raw === '') {
    _smgmtSetRenameError(label, 'Sprint number cannot be empty');
    input.focus();
    return;
  }
  const newN = parseInt(raw, 10);
  if (isNaN(newN) || newN <= 0) {
    _smgmtSetRenameError(label, 'Only numbers are allowed');
    input.focus();
    return;
  }
  if (newN === origN) {
    smgmtCancelRename(label, origN, nameEl);
    return;
  }
  const existingNums = (_smgmtData?.order || []).map(l => parseInt(l.split('-')[1], 10));
  if (existingNums.includes(newN)) {
    _smgmtSetRenameError(label, `Sprint ${newN} already exists`);
    input.focus();
    return;
  }

  _smgmtSetRenameError(label, '');
  input.disabled = true;

  try {
    const res = await fetch(`/api/sprints/${label}/rename`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_sprint_number: newN, project: _smgmtCurrentRepo }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      _smgmtSetRenameError(label, data.detail || `Error ${res.status}`);
      if (input) input.disabled = false;
      input.focus();
      return;
    }
  } catch (err) {
    _smgmtSetRenameError(label, 'Network error');
    if (input) input.disabled = false;
    input.focus();
    return;
  }

  // Update in-memory data order immediately
  const newLabel = `sprint-${newN}`;
  if (_smgmtData?.order) {
    _smgmtData.order = _smgmtData.order.map(l => l === label ? newLabel : l);
  }
  if (_smgmtData?.issues) {
    _smgmtData.issues.forEach(iss => {
      if (iss.sprint === label) iss.sprint = newLabel;
    });
  }
  // Migrate goals/estimates/states to new label key
  if (_smgmtGoals[label] !== undefined) {
    _smgmtGoals[newLabel] = _smgmtGoals[label];
    delete _smgmtGoals[label];
  }
  if (_smgmtEstimates[label] !== undefined) {
    _smgmtEstimates[newLabel] = _smgmtEstimates[label];
    delete _smgmtEstimates[label];
  }
  if (_smgmtSprintStates[label] !== undefined) {
    _smgmtSprintStates[newLabel] = _smgmtSprintStates[label];
    delete _smgmtSprintStates[label];
  }

  smgmtRender();
}

function smgmtCancelRename(label, origN, nameEl) {
  if (!nameEl) return;
  nameEl.textContent = `Sprint ${origN}`;
}

function smgmtTicketCardHtml(ticket, currentSprint) {
  const hasNeedsReworkLabel = (ticket.labels || []).some(l => l.name === 'need-rework' || l.name === 'needs-rework');
  const statusClass = hasNeedsReworkLabel ? 'smgmt-status-need-rework' : ({
    'backlog':      'smgmt-status-backlog',
    'in-progress':  'smgmt-status-in-progress',
    'sit':          'smgmt-status-sit',
    'uat':          'smgmt-status-uat',
    'done':         'smgmt-status-done',
  }[ticket.status] || 'smgmt-status-backlog');
  const statusLabel = hasNeedsReworkLabel ? 'needs rework' : (ticket.status || 'backlog');

  // Estimate badge: look up from sprint estimates if available
  let estimateBadgeHtml = '';
  if (currentSprint && _smgmtEstimates[currentSprint]) {
    const sprintEst = _smgmtEstimates[currentSprint];
    const issueEst  = (sprintEst.estimates || {})[String(ticket.number)];
    if (issueEst) {
      estimateBadgeHtml = `<span class="smgmt-estimate-badge" title="~${issueEst.minutes} min">${escapeHtml(issueEst.size)}</span>`;
    }
  }

  const isSelected = _smgmtSelectedIssues.has(ticket.number);
  return `
    <div class="smgmt-ticket${isSelected ? ' is-selected' : ''}" id="smgmt-ticket-${ticket.number}"
         draggable="true"
         data-issue="${ticket.number}"
         data-sprint="${currentSprint || ''}"
         tabindex="0"
         onclick="smgmtTicketClick(event, ${ticket.number})"
         onkeydown="smgmtTicketKeyDown(event, ${ticket.number})"
         ondragstart="smgmtTicketDragStart(event, ${ticket.number}, '${currentSprint || ''}')"
         ondragend="smgmtTicketDragEnd(event)">
      <input type="checkbox" class="smgmt-ticket-cb"
             ${isSelected ? 'checked' : ''}
             onclick="event.stopPropagation()"
             onchange="smgmtToggleIssueSelect(${ticket.number}, this.checked)">
      <i class="ti ti-grip-vertical smgmt-ticket-grip"></i>
      <a class="smgmt-ticket-num" href="${escapeHtml(ticket.url || '#')}" target="_blank"
         rel="noopener" onclick="event.stopPropagation()">#${ticket.number}</a>
      <span class="smgmt-ticket-title" title="${escapeHtml(ticket.title)}">${escapeHtml(ticket.title)}</span>
      ${estimateBadgeHtml}
      <span class="smgmt-ticket-status ${statusClass}">${escapeHtml(statusLabel)}</span>
    </div>`;
}

// ── Backlog block (issue #225) ─────────────────────────────────────────────────

/**
 * Apply inline vs sticky mode based on sprint count.
 * inline (<=3 sprints): body always visible, not sticky
 * sticky (>3 sprints):  collapsed by default (unless saved as expanded), position:sticky
 */
function smgmtBacklogApplyMode(sprintCount) {
  const block = document.getElementById('smgmt-backlog');
  if (!block) return;

  const isInline = sprintCount <= 3;
  block.classList.toggle('smgmt-backlog-inline',  isInline);
  block.classList.toggle('smgmt-backlog-sticky',   !isInline);

  // Toggle bottom padding on the sprint body
  const bodyEl = document.getElementById('smgmt-body');
  if (bodyEl) bodyEl.classList.toggle('smgmt-body-sticky-backlog', !isInline);

  // Apply expanded state from memory (sticky mode only)
  if (!isInline) {
    block.classList.toggle('smgmt-backlog-expanded', _smgmtBacklogExpanded);
  } else {
    // Inline mode: always show body (remove expanded class, let CSS handle it)
    block.classList.remove('smgmt-backlog-expanded');
  }

  // Sync aria-expanded
  const hdr = document.getElementById('smgmt-backlog-header');
  if (hdr) {
    hdr.setAttribute('aria-expanded', isInline ? 'true' : String(_smgmtBacklogExpanded));
  }
}

/**
 * Toggle expand/collapse of the sticky backlog block.
 */
function smgmtBacklogToggle() {
  const block = document.getElementById('smgmt-backlog');
  if (!block) return;

  // In inline mode, no toggle needed
  if (block.classList.contains('smgmt-backlog-inline')) return;

  _smgmtBacklogExpanded = !_smgmtBacklogExpanded;
  block.classList.toggle('smgmt-backlog-expanded', _smgmtBacklogExpanded);

  const hdr = document.getElementById('smgmt-backlog-header');
  if (hdr) hdr.setAttribute('aria-expanded', String(_smgmtBacklogExpanded));

  // Persist to localStorage
  const slug = (_smgmtCurrentRepo || '').split('/')[1] || _smgmtCurrentRepo || '';
  localStorage.setItem(`commander.${slug}.backlogExpanded`, String(_smgmtBacklogExpanded));
}

/**
 * Collapse the backlog block when clicking outside (sticky mode only).
 */
function smgmtBacklogCollapseOutside(event) {
  if (!_smgmtBacklogExpanded) return;
  const block = document.getElementById('smgmt-backlog');
  const popup = document.getElementById('smgmt-backlog-moveto-popup');
  if (!block) return;
  if (block.classList.contains('smgmt-backlog-inline')) return;
  // Don't collapse if click is inside block or popup
  if (block.contains(event.target)) return;
  if (popup && popup.contains(event.target)) return;
  _smgmtBacklogExpanded = false;
  block.classList.remove('smgmt-backlog-expanded');
  const hdr = document.getElementById('smgmt-backlog-header');
  if (hdr) hdr.setAttribute('aria-expanded', 'false');
  const slug = (_smgmtCurrentRepo || '').split('/')[1] || _smgmtCurrentRepo || '';
  localStorage.setItem(`commander.${slug}.backlogExpanded`, 'false');
}

/**
 * Handle Escape key: collapse expanded backlog, or close open "Move to" popup.
 */
function smgmtBacklogEscapeKey(event) {
  if (event.key !== 'Escape') return;
  const popup = document.getElementById('smgmt-backlog-moveto-popup');
  if (popup && !popup.classList.contains('hidden')) {
    smgmtBacklogMoveToClose();
    return;
  }
  if (_smgmtBacklogExpanded) {
    smgmtBacklogToggle();
  }
}

// Install click-outside and escape handlers once
document.addEventListener('click', smgmtBacklogCollapseOutside);
document.addEventListener('keydown', smgmtBacklogEscapeKey);

// Escape during drag dismisses ghost pane; Escape on ghost modal is a no-op close (issue #364)
document.addEventListener('keydown', function(e) {
  if (e.key !== 'Escape') return;
  // Close ghost confirm modal if open
  const modal = document.getElementById('smgmt-ghost-modal');
  if (modal && !modal.classList.contains('hidden')) {
    smgmtGhostConfirmClose();
    return;
  }
  // Dismiss ghost pane while dragging
  if (!_smgmtDragTicket && _smgmtDragTickets.length === 0) return;
  _smgmtGhostNextN = null;
  _smgmtDragTicket = null;
  _smgmtDragTickets = [];
  document.querySelectorAll('.smgmt-sprint-placeholder').forEach(p => {
    p.style.display = 'none';
    p.classList.remove('ghost-hot');
  });
});

/**
 * Set filter ('all' | 'unestimated' | 'attachments') and re-render.
 */
function smgmtBacklogSetFilter(filter) {
  _smgmtBacklogFilter = filter;
  // Persist
  const slug = (_smgmtCurrentRepo || '').split('/')[1] || _smgmtCurrentRepo || '';
  localStorage.setItem(`commander.${slug}.backlogFilter`, filter);
  // Update pill active state
  ['all', 'unestimated', 'attachments'].forEach(f => {
    const pill = document.getElementById(`smgmt-backlog-pill-${f}`);
    if (pill) pill.classList.toggle('active', f === filter);
  });
  if (!_smgmtData) return;
  const unassigned = _smgmtData.issues.filter(i => i.sprint == null);
  smgmtRenderBacklog(unassigned);
}

// Legacy compatibility: old filter used label strings. Keep function name.
function smgmtBacklogFilter(label) {
  smgmtBacklogSetFilter(label || 'all');
}

/**
 * Apply a filter to the ticket list based on _smgmtBacklogFilter.
 */
function smgmtApplyBacklogFilter(tickets) {
  switch (_smgmtBacklogFilter) {
    case 'unestimated':
      return tickets.filter(t => !(t.labels || []).some(l => l.name === 'estimated'));
    case 'attachments':
      return tickets.filter(t => (t.body || '').includes('## Attachments'));
    default:
      return tickets;
  }
}

function smgmtRenderBacklog(tickets) {
  const labelEl   = document.getElementById('smgmt-backlog-label');
  const ticketsEl = document.getElementById('smgmt-backlog-tickets');
  if (!ticketsEl) return;

  // Update count label
  if (labelEl) {
    labelEl.textContent = `Backlog · ${tickets.length} ticket${tickets.length !== 1 ? 's' : ''}`;
  }

  // Sync filter pill active state
  ['all', 'unestimated', 'attachments'].forEach(f => {
    const pill = document.getElementById(`smgmt-backlog-pill-${f}`);
    if (pill) pill.classList.toggle('active', f === _smgmtBacklogFilter);
  });

  // Apply filter
  const filtered = smgmtApplyBacklogFilter(tickets);

  // Sort: most recently created first (higher issue number = newer)
  const sorted = [...filtered].sort((a, b) => b.number - a.number);

  const nonEmptyLabels = _smgmtData?.order || [];
  const emptyLabels    = _smgmtData?.empty_sprint_labels || [];
  // Include placeholder sprint so users can move into it
  const placeholderN   = _smgmtData?.placeholder_sprint;
  const allSprintLabels = [...new Set([...nonEmptyLabels, ...emptyLabels])].sort((a, b) => {
    const numA = parseInt(a.split('-')[1], 10) || 0;
    const numB = parseInt(b.split('-')[1], 10) || 0;
    return numA - numB;
  });

  if (sorted.length === 0) {
    if (tickets.length === 0) {
      ticketsEl.innerHTML = '<div class="smgmt-drop-hint" style="padding:16px;text-align:center;">No backlog tickets &mdash; all caught up</div>';
    } else {
      ticketsEl.innerHTML = '<div class="smgmt-drop-hint" style="padding:10px;text-align:center;">No tickets match this filter</div>';
    }
  } else {
    ticketsEl.innerHTML = sorted.map(t => smgmtBacklogTicketHtml(t, allSprintLabels)).join('');
  }
}

function smgmtBacklogTicketHtml(ticket, sprintLabels) {
  // Build label chips for display
  const displayLabels = (ticket.labels || []).filter(l => !['backlog', 'enhancement', 'bug'].includes(l.name) || true);
  const labelChipsHtml = displayLabels.slice(0, 3).map(l =>
    `<span class="smgmt-backlog-label-chip">${escapeHtml(l.name)}</span>`
  ).join('');

  const sizeLabel = (ticket.labels || []).find(l => /^size-/.test(l.name));
  const sizeChip  = sizeLabel
    ? `<span class="smgmt-size-chip">${escapeHtml(sizeLabel.name.replace('size-', ''))}</span>`
    : '';

  const hasNeedsRework = (ticket.labels || []).some(l => l.name === 'need-rework' || l.name === 'needs-rework');
  const reworkBadge = hasNeedsRework
    ? `<span class="smgmt-rework-badge" title="Needs rework after rejection">needs rework</span>`
    : '';

  const isSelected = _smgmtSelectedIssues.has(ticket.number);
  return `
    <div class="smgmt-ticket${isSelected ? ' is-selected' : ''}${hasNeedsRework ? ' is-needs-rework' : ''}" id="smgmt-ticket-${ticket.number}"
         draggable="true"
         data-issue="${ticket.number}"
         data-sprint=""
         tabindex="0"
         onclick="smgmtTicketClick(event, ${ticket.number})"
         onkeydown="smgmtTicketKeyDown(event, ${ticket.number})"
         ondragstart="smgmtBacklogTicketDragStart(event, ${ticket.number})"
         ondragend="smgmtBacklogTicketDragEnd(event)">
      <input type="checkbox" class="smgmt-ticket-cb"
             ${isSelected ? 'checked' : ''}
             onclick="event.stopPropagation()"
             onchange="smgmtToggleIssueSelect(${ticket.number}, this.checked)">
      <i class="ti ti-grip-vertical smgmt-ticket-grip"></i>
      <a class="smgmt-ticket-num" href="${escapeHtml(ticket.url || '#')}" target="_blank"
         rel="noopener" onclick="event.stopPropagation()">#${ticket.number}</a>
      <span class="smgmt-ticket-title" title="${escapeHtml(ticket.title)}">${escapeHtml(ticket.title)}</span>
      ${reworkBadge}
      ${sizeChip}
      <button class="smgmt-moveto-btn" tabindex="0"
              onclick="smgmtBacklogMoveToOpen(event, ${ticket.number})"
              onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();smgmtBacklogMoveToOpen(event,${ticket.number});}">Move to &#9660;</button>
    </div>`;
}

// ── "Move to" popup for backlog (issue #225) ──────────────────────────────────

let _smgmtBacklogMoveToIssue = null;  // issue number for open "Move to" popup

function smgmtBacklogMoveToOpen(event, issueNum) {
  event.stopPropagation();
  const popup = document.getElementById('smgmt-backlog-moveto-popup');
  if (!popup) return;

  // Build sprint list: planned/next-up/running sprints
  const nonEmptyLabels = _smgmtData?.order || [];
  const emptyLabels    = _smgmtData?.empty_sprint_labels || [];
  const allSprintLabels = [...new Set([...nonEmptyLabels, ...emptyLabels])].sort((a, b) => {
    const numA = parseInt(a.split('-')[1], 10) || 0;
    const numB = parseInt(b.split('-')[1], 10) || 0;
    return numA - numB;
  });

  // If popup is open for same issue, close it
  if (_smgmtBacklogMoveToIssue === issueNum && !popup.classList.contains('hidden')) {
    smgmtBacklogMoveToClose();
    return;
  }

  _smgmtBacklogMoveToIssue = issueNum;

  // Build popup content
  const sprintItems = allSprintLabels.map(label => {
    const n = label.split('-')[1];
    return `<button class="smgmt-moveto-item" onclick="smgmtBacklogMoveTicketTo(${issueNum},'${label}')">Sprint ${n}</button>`;
  }).join('');

  popup.innerHTML = sprintItems +
    `<hr class="smgmt-moveto-divider">
     <button class="smgmt-moveto-item smgmt-moveto-create" onclick="smgmtBacklogMoveToNewSprint(${issueNum})">+ Create new sprint&hellip;</button>`;

  popup.classList.remove('hidden');
  popup.setAttribute('aria-label', `Move ticket #${issueNum} to sprint`);

  // Position popup near the clicked button
  const btn = event.currentTarget || event.target;
  const rect = btn.getBoundingClientRect();
  const popupH = 240; // approximate
  const spaceBelow = window.innerHeight - rect.bottom;
  if (spaceBelow < popupH) {
    popup.style.top  = (rect.top + window.scrollY - popupH) + 'px';
  } else {
    popup.style.top  = (rect.bottom + window.scrollY) + 'px';
  }
  popup.style.left = (rect.left + window.scrollX) + 'px';
  popup.style.minWidth = rect.width + 'px';

  // Close when clicking outside
  setTimeout(() => {
    document.addEventListener('click', _smgmtMoveToOutsideHandler);
  }, 0);
}

function _smgmtMoveToOutsideHandler(event) {
  const popup = document.getElementById('smgmt-backlog-moveto-popup');
  if (!popup || popup.contains(event.target)) return;
  smgmtBacklogMoveToClose();
}

function smgmtBacklogMoveToClose() {
  const popup = document.getElementById('smgmt-backlog-moveto-popup');
  if (popup) popup.classList.add('hidden');
  _smgmtBacklogMoveToIssue = null;
  document.removeEventListener('click', _smgmtMoveToOutsideHandler);
}

async function smgmtBacklogMoveTicketTo(issueNum, sprintLabel) {
  smgmtBacklogMoveToClose();
  if (!sprintLabel || !_smgmtData) return;
  const baseMatch = sprintLabel.match(/^sprint-(\d+)/);
  const sprintNum = baseMatch ? parseInt(baseMatch[1], 10) : null;
  const isDotted = /^sprint-\d+\.\d+$/.test(sprintLabel);

  // Optimistic update
  const iss = _smgmtData.issues.find(i => i.number === issueNum);
  if (iss) {
    iss.sprint = sprintNum;
    iss.sprint_label = sprintLabel;
  }
  // Add sprint to order if needed
  if (!_smgmtData.order.includes(sprintLabel)) {
    _smgmtData.order.push(sprintLabel);
  }
  smgmtRender();

  const apiBody = isDotted
    ? { issue: issueNum, sprint_label: sprintLabel }
    : { issue: issueNum, sprint: sprintNum };
  try {
    const res = await fetch('/api/sprint-planning/assign', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(apiBody),
    });
    if (!res.ok) throw new Error(await res.text());
    await smgmtRefreshBoard();
  } catch (e) {
    // Rollback optimistic update
    const iss2 = _smgmtData.issues.find(i => i.number === issueNum);
    if (iss2) { iss2.sprint = null; iss2.sprint_label = null; }
    smgmtRender();
    smgmtShowError(`Failed to move ticket #${issueNum}: ${e.message}`);
    _showToast(`Failed to move #${issueNum}: ${e.message}`, 'error');
  }
}

function smgmtBacklogMoveToNewSprint(issueNum) {
  smgmtBacklogMoveToClose();
  // Pre-select this ticket in the new sprint flow by opening the New Sprint modal
  // The existing smgmtCreateSprint flow is used; ticket pre-selection is set via a flag
  _smgmtNewSprintPreselectedIssue = issueNum;
  smgmtCreateSprint();
}

// ── Drag from backlog: auto-expand + non-sticky during drag (issue #225) ──────

function smgmtBacklogTicketDragStart(event, issueNum) {
  // Auto-expand the backlog and remove sticky while dragging
  const block = document.getElementById('smgmt-backlog');
  if (block) {
    block.dataset.preDragExpanded = String(_smgmtBacklogExpanded);
    _smgmtBacklogExpanded = true;
    block.classList.add('smgmt-backlog-expanded', 'is-drag-active');
    const hdr = document.getElementById('smgmt-backlog-header');
    if (hdr) hdr.setAttribute('aria-expanded', 'true');
  }
  _smgmtDragTicket = { number: issueNum, fromSprint: null };
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('text/smgmt-ticket', String(issueNum));

  if (_smgmtSelectedIssues.has(issueNum) && _smgmtSelectedIssues.size > 1) {
    // Multi-drag: collect all selected tickets in DOM order
    const allRows = [...document.querySelectorAll('#smgmt-backlog-tickets .smgmt-ticket, #smgmt-body .smgmt-ticket')];
    _smgmtDragTickets = allRows
      .map(r => ({ number: parseInt(r.dataset.issue, 10), fromSprint: r.dataset.sprint || null }))
      .filter(t => _smgmtSelectedIssues.has(t.number));
    setTimeout(() => {
      _smgmtDragTickets.forEach(t => {
        document.getElementById(`smgmt-ticket-${t.number}`)?.classList.add('dragging-ticket');
      });
    }, 0);
    _smgmtSetMultiDragGhost(event, _smgmtDragTickets.length);
  } else {
    _smgmtDragTickets = [];
    const el = document.getElementById(`smgmt-ticket-${issueNum}`);
    if (el) setTimeout(() => el.classList.add('dragging-ticket'), 0);
  }
  // Compute next-free sprint number once per drag session and show ghost (issue #364)
  _smgmtGhostNextN = _smgmtComputeNextFreeSprint();
  _smgmtUpdateGhostLabel(_smgmtGhostNextN, false);
  document.querySelectorAll('.smgmt-sprint-placeholder').forEach(p => { p.style.display = 'block'; });
}

function smgmtBacklogTicketDragEnd(event) {
  const block = document.getElementById('smgmt-backlog');
  if (block) {
    block.classList.remove('is-drag-active');
    // After 500ms, restore previous state
    if (_smgmtBacklogDragRestoreTimer) clearTimeout(_smgmtBacklogDragRestoreTimer);
    _smgmtBacklogDragRestoreTimer = setTimeout(() => {
      const wasExpanded = block.dataset.preDragExpanded === 'true';
      _smgmtBacklogExpanded = wasExpanded;
      block.classList.toggle('smgmt-backlog-expanded', wasExpanded);
      const hdr = document.getElementById('smgmt-backlog-header');
      if (hdr) hdr.setAttribute('aria-expanded', String(wasExpanded));
    }, 500);
  }
  // Reuse standard ticket drag end
  smgmtTicketDragEnd(event);
}

/**
 * Drag over handler for the backlog zone.
 */
function smgmtBacklogDragOver(event) {
  smgmtDragOverZone(event, null);
}

/**
 * Simple toast helper (show error/info message).
 */
function _showToast(msg, type) {
  // Re-use existing smgmtShowError for errors
  if (type === 'error') {
    smgmtShowError(msg);
  }
}

async function smgmtMoveTicketTo(issueNum, sprintLabel) {
  if (!sprintLabel) return;
  await smgmtBacklogMoveTicketTo(issueNum, sprintLabel);
}

// Declare _smgmtNewSprintPreselectedIssue for backlog "Create new sprint" flow
let _smgmtNewSprintPreselectedIssue = null;

async function smgmtLoadGoals() {
  if (!_smgmtCurrentRepo || !_smgmtData) return;
  const repo = _smgmtCurrentRepo;
  await Promise.all(_smgmtData.order.map(async (label) => {
    try {
      const res = await fetch(
        `/api/sprints/goal?project=${encodeURIComponent(repo)}&sprint=${encodeURIComponent(label)}`
      );
      if (res.ok) {
        const data = await res.json();
        _smgmtGoals[label] = data.goal || '';
      } else {
        _smgmtGoals[label] = '';
      }
    } catch (e) {
      _smgmtGoals[label] = '';
    }
  }));
}

function smgmtGoalInput(label, value) {
  // No-op while this sprint is running (input is readonly anyway, but guard defensively)
  const runKey = `${_smgmtCurrentRepo}:${label}`;
  if (_smgmtAllRunning[runKey]) return;

  _smgmtGoals[label] = value;

  // Update the visible goal bar
  const safeLabel = label.replace(/-/g, '_').replace(/\./g, '_');
  const goalBar = document.getElementById(`smgmt-goal-bar-${safeLabel}`);
  if (goalBar) {
    const hasGoal = value.trim().length > 0;
    if (hasGoal) {
      goalBar.textContent = value;
      goalBar.className = 'smgmt-sprint-goal-bar';
    } else {
      goalBar.textContent = 'Sprint goal (required to run) — e.g. Dashboard UX cleanup';
      goalBar.className = 'smgmt-sprint-goal-bar placeholder';
    }
  }

  const runBtnId = `smgmt-run-btn-${safeLabel}`;
  const btn = document.getElementById(runBtnId);
  if (btn) {
    const sprintTickets = (_smgmtData?.issues || []).filter(
      t => (t.sprint_label || (t.sprint != null ? `sprint-${t.sprint}` : null)) === label
    );
    const hasTickets = sprintTickets.length >= 1;
    const hasCompleted = smgmtHasCompletedTickets(sprintTickets);
    if (hasCompleted) {
      // Re-run button: always enabled, no goal dependency
      btn.disabled = false;
      btn.title = '';
    } else {
      // issue #242: goal is no longer required to enable Run Sprint
      const canRun = hasTickets;
      btn.disabled = !canRun;
      btn.title = !hasTickets ? 'Add at least one ticket first' : '';
    }
  }
  if (_smgmtGoalSaveTimers[label]) clearTimeout(_smgmtGoalSaveTimers[label]);
  _smgmtGoalSaveTimers[label] = setTimeout(() => smgmtSaveGoal(label, value), 800);
}

async function smgmtSaveGoal(label, goal) {
  if (!_smgmtCurrentRepo) return;
  try {
    await fetch('/api/sprints/goal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project: _smgmtCurrentRepo, sprint_label: label, goal }),
    });
    // Re-render so NEXT-UP badge moves to the correct sprint after goal changes
    smgmtRender();
  } catch (e) {
    smgmtShowError('Failed to save sprint goal: ' + e.message);
  }
}

// ── Sprint drag-and-drop (reorder) ────────────────────────────────────────────

let _smgmtDragSprintLabel = null;
let _smgmtDragSprintOver  = null;

function smgmtSprintDragStart(event, label) {
  // Suppress drag if this sprint (or any sprint) is locked/running (issue #186)
  const hdr = event.currentTarget;
  if (hdr && hdr.getAttribute('draggable') === 'false') {
    event.preventDefault();
    return;
  }
  // Suppress drag while an inline rename is active (issue #341)
  const block = document.getElementById(`smgmt-block-${label}`);
  if (block && block.querySelector('.smgmt-rename-wrap')) {
    event.preventDefault();
    return;
  }
  _smgmtDragSprintLabel = label;
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('text/smgmt-sprint', label);
  if (block) setTimeout(() => block.classList.add('dragging-sprint'), 0);
}

function smgmtSprintDragEnd(event) {
  if (_smgmtDragSprintLabel) {
    const block = document.getElementById(`smgmt-block-${_smgmtDragSprintLabel}`);
    if (block) block.classList.remove('dragging-sprint');
  }
  _smgmtDragSprintLabel = null;
  // Clear all hover states
  document.querySelectorAll('.smgmt-sprint-block').forEach(b => b.classList.remove('drag-over-sprint'));
}

// ── Ticket drag-and-drop ──────────────────────────────────────────────────────

function smgmtTicketDragStart(event, issueNum, fromSprint) {
  // Suppress drag if the ticket's row has draggable="false" (issue #186)
  const tgt = event.currentTarget;
  if (tgt && tgt.getAttribute('draggable') === 'false') {
    event.preventDefault();
    return;
  }
  _smgmtDragTicket = { number: issueNum, fromSprint: fromSprint || null };
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('text/smgmt-ticket', String(issueNum));

  if (_smgmtSelectedIssues.has(issueNum) && _smgmtSelectedIssues.size > 1) {
    const allRows = [...document.querySelectorAll('#smgmt-backlog-tickets .smgmt-ticket, #smgmt-body .smgmt-ticket')];
    _smgmtDragTickets = allRows
      .map(r => ({ number: parseInt(r.dataset.issue, 10), fromSprint: r.dataset.sprint || null }))
      .filter(t => _smgmtSelectedIssues.has(t.number));
    setTimeout(() => {
      _smgmtDragTickets.forEach(t => {
        document.getElementById(`smgmt-ticket-${t.number}`)?.classList.add('dragging-ticket');
      });
    }, 0);
    _smgmtSetMultiDragGhost(event, _smgmtDragTickets.length);
  } else {
    _smgmtDragTickets = [];
    const el = document.getElementById(`smgmt-ticket-${issueNum}`);
    if (el) setTimeout(() => el.classList.add('dragging-ticket'), 0);
  }
  // Compute next-free sprint number once per drag session and show ghost (issue #364)
  _smgmtGhostNextN = _smgmtComputeNextFreeSprint();
  _smgmtUpdateGhostLabel(_smgmtGhostNextN, false);
  document.querySelectorAll('.smgmt-sprint-placeholder').forEach(p => { p.style.display = 'block'; });
}

function smgmtTicketDragEnd(event) {
  if (_smgmtDragTickets.length > 1) {
    _smgmtDragTickets.forEach(t => {
      document.getElementById(`smgmt-ticket-${t.number}`)?.classList.remove('dragging-ticket');
    });
  } else if (_smgmtDragTicket) {
    const el = document.getElementById(`smgmt-ticket-${_smgmtDragTicket.number}`);
    if (el) el.classList.remove('dragging-ticket');
  }
  _smgmtDragTicket = null;
  _smgmtDragTickets = [];
  // Clear all hover states
  document.querySelectorAll('.smgmt-sprint-block').forEach(el => {
    el.classList.remove('drag-over-sprint', 'drag-over-zone');
  });
  document.getElementById('smgmt-backlog')?.classList.remove('drag-over-sprint', 'drag-over-zone');
  // Hide ghost pane and clear cached ghost number (issue #364)
  _smgmtGhostNextN = null;
  document.querySelectorAll('.smgmt-sprint-placeholder').forEach(p => {
    p.style.display = 'none';
    p.classList.remove('ghost-hot');
  });
}

function smgmtDragOverZone(event, sprintLabel) {
  // Prevent default only for ticket drags or sprint drags
  if (_smgmtDragTicket || _smgmtDragSprintLabel) {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }

  if (_smgmtDragSprintLabel && sprintLabel && sprintLabel !== _smgmtDragSprintLabel) {
    // Sprint reorder hover
    document.querySelectorAll('.smgmt-sprint-block').forEach(b => b.classList.remove('drag-over-sprint'));
    const target = document.getElementById(`smgmt-block-${sprintLabel}`);
    if (target) target.classList.add('drag-over-sprint');
    _smgmtDragSprintOver = sprintLabel;
    return;
  }

  if (_smgmtDragTicket) {
    if (sprintLabel) {
      document.querySelectorAll('.smgmt-sprint-block').forEach(b => b.classList.remove('drag-over-sprint'));
      document.getElementById('smgmt-backlog')?.classList.remove('drag-over-zone');
      const target = document.getElementById(`smgmt-block-${sprintLabel}`);
      if (target) target.classList.add('drag-over-sprint');
    } else {
      document.querySelectorAll('.smgmt-sprint-block').forEach(b => b.classList.remove('drag-over-sprint'));
      document.getElementById('smgmt-backlog')?.classList.add('drag-over-zone');
    }
  }
}

function smgmtDragLeave(event) {
  // Only clear if leaving to outside the zone
  if (event.currentTarget && !event.currentTarget.contains(event.relatedTarget)) {
    event.currentTarget.classList.remove('drag-over-sprint', 'drag-over-zone');
  }
}

function smgmtDragOverPlaceholder(event) {
  if (_smgmtDragTicket || _smgmtDragTickets.length > 0) {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    document.querySelectorAll('.smgmt-sprint-block').forEach(b => b.classList.remove('drag-over-sprint'));
    document.getElementById('smgmt-backlog')?.classList.remove('drag-over-zone');
    const ghost = event.currentTarget;
    if (ghost && !ghost.classList.contains('ghost-hot')) {
      ghost.classList.add('ghost-hot');
      _smgmtUpdateGhostLabel(_smgmtGhostNextN ?? 1, true);
    }
  }
}

function smgmtDragLeaveGhost(event) {
  if (event.currentTarget && !event.currentTarget.contains(event.relatedTarget)) {
    event.currentTarget.classList.remove('ghost-hot');
    _smgmtUpdateGhostLabel(_smgmtGhostNextN ?? 1, false);
  }
}

function _smgmtUpdateGhostLabel(n, isHot) {
  const titleEl = document.getElementById('smgmt-ghost-title');
  const subEl   = document.getElementById('smgmt-ghost-sub');
  if (titleEl) titleEl.textContent = isHot ? `Release to create Sprint ${n}` : `Drop here to create Sprint ${n}`;
  if (subEl)   subEl.textContent   = isHot ? 'you\'ll be asked to confirm' : _smgmtGhostSubText(n);
}

function _smgmtGhostSubText(n) {
  if (!_smgmtData) return 'next free number';
  const used = new Set(_smgmtData.sprints || []);
  const skipped = [];
  for (let i = 1; i < n; i++) {
    if (!used.has(i)) continue;
    // If i exists but is empty, mention it was skipped
    const emptyLabels = _smgmtData.empty_sprint_labels || [];
    if (emptyLabels.includes(`sprint-${i}`) || !(_smgmtData.order || []).includes(`sprint-${i}`)) {
      // Only note sprints that exist as labels but are immediately before n
      if (i === n - 1) skipped.push(i);
    }
  }
  if (skipped.length > 0) return `next free number · skipped empty Sprint ${skipped.join(', ')}`;
  return 'next free number';
}

function smgmtDropOnPlaceholder(event, placeholderN) {
  event.preventDefault();
  document.querySelectorAll('.smgmt-sprint-block').forEach(el => {
    el.classList.remove('drag-over-sprint', 'drag-over-zone', 'ghost-hot');
  });
  document.getElementById('smgmt-backlog')?.classList.remove('drag-over-sprint', 'drag-over-zone');

  if (!_smgmtCurrentRepo) return;

  // Use the cached next-free number (computed at drag-start) over the stale render-time value
  const sprintN = _smgmtGhostNextN ?? placeholderN;

  const tickets = _smgmtDragTickets.length > 0
    ? [..._smgmtDragTickets]
    : (_smgmtDragTicket ? [{ ..._smgmtDragTicket }] : []);

  if (tickets.length === 0) return;

  // Open confirm modal — no sprint is created yet
  _smgmtGhostPendingTickets = tickets;
  _smgmtGhostPendingN = sprintN;
  smgmtGhostConfirmOpen(sprintN, tickets);
}

function smgmtGhostConfirmOpen(sprintN, tickets) {
  const backdrop = document.getElementById('smgmt-ghost-backdrop');
  const modal    = document.getElementById('smgmt-ghost-modal');
  if (!backdrop || !modal) return;

  document.getElementById('smgmt-ghost-modal-sprint-name').textContent = `Sprint ${sprintN}`;
  document.getElementById('smgmt-ghost-modal-sprint-badge').textContent = `sprint-${sprintN}`;
  document.getElementById('smgmt-ghost-confirm-label').textContent = `sprint-${sprintN}`;

  const ticketsList = document.getElementById('smgmt-ghost-modal-tickets');
  ticketsList.innerHTML = '';
  for (const t of tickets) {
    const iss = _smgmtData?.issues.find(i => i.number === t.number);
    const li = document.createElement('li');
    li.textContent = iss ? `#${t.number} ${iss.title}` : `#${t.number}`;
    ticketsList.appendChild(li);
  }

  const firstTicket = tickets[0];
  const sourceLabel = firstTicket.fromSprint || 'Backlog';
  document.getElementById('smgmt-ghost-modal-source').textContent = sourceLabel;

  const errEl = document.getElementById('smgmt-ghost-modal-error');
  errEl.textContent = '';
  errEl.style.display = 'none';

  const confirmBtn = document.getElementById('smgmt-ghost-confirm-btn');
  const cancelBtn  = document.getElementById('smgmt-ghost-cancel-btn');
  if (confirmBtn) confirmBtn.disabled = false;
  if (cancelBtn)  cancelBtn.disabled  = false;

  backdrop.classList.remove('hidden');
  modal.classList.remove('hidden');
}

function smgmtGhostConfirmClose() {
  const backdrop = document.getElementById('smgmt-ghost-backdrop');
  const modal    = document.getElementById('smgmt-ghost-modal');
  if (backdrop) backdrop.classList.add('hidden');
  if (modal)    modal.classList.add('hidden');

  // Full no-op: clear pending state, ticket returns visually to origin (render is not called)
  _smgmtGhostPendingTickets = null;
  _smgmtGhostPendingN = null;
  _smgmtDragTicket = null;
  _smgmtDragTickets = [];
  document.querySelectorAll('.smgmt-sprint-placeholder').forEach(p => {
    p.style.display = 'none';
    p.classList.remove('ghost-hot');
  });
}

async function smgmtGhostConfirmCreate() {
  const sprintN  = _smgmtGhostPendingN;
  const tickets  = _smgmtGhostPendingTickets;
  if (!sprintN || !tickets || tickets.length === 0 || !_smgmtCurrentRepo) return;

  const confirmBtn = document.getElementById('smgmt-ghost-confirm-btn');
  const cancelBtn  = document.getElementById('smgmt-ghost-cancel-btn');
  const errEl      = document.getElementById('smgmt-ghost-modal-error');
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Creating…'; }
  if (cancelBtn)  cancelBtn.disabled = true;
  errEl.style.display = 'none';

  const newSprintLabel = `sprint-${sprintN}`;

  try {
    // Assign all tickets via /api/sprint-planning/assign (creates the label on first call)
    const errors = [];
    await Promise.all(tickets.map(async (t) => {
      const res = await fetch('/api/sprint-planning/assign', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ issue: t.number, sprint: sprintN }),
      });
      if (!res.ok) {
        const msg = await res.text().catch(() => res.statusText);
        errors.push(`#${t.number}: ${msg}`);
      }
    }));

    if (errors.length > 0) {
      throw new Error(errors.join('; '));
    }

    // Optimistically update local state and re-render (label already created on GitHub)
    for (const t of tickets) {
      const iss = _smgmtData.issues.find(i => i.number === t.number);
      if (iss) { iss.sprint = sprintN; iss.sprint_label = newSprintLabel; }
    }
    if (!_smgmtData.sprints.includes(sprintN)) {
      _smgmtData.sprints.push(sprintN);
      _smgmtData.sprints.sort((a, b) => a - b);
    }
    if (!_smgmtData.order.includes(newSprintLabel)) _smgmtData.order.push(newSprintLabel);
    _smgmtData.placeholder_sprint = _smgmtComputeNextFreeSprint();
    _smgmtDragTicket = null;
    _smgmtDragTickets = [];
    smgmtClearSelection();

    // Close modal and re-render
    const backdrop = document.getElementById('smgmt-ghost-backdrop');
    const modal    = document.getElementById('smgmt-ghost-modal');
    if (backdrop) backdrop.classList.add('hidden');
    if (modal)    modal.classList.add('hidden');
    _smgmtGhostPendingTickets = null;
    _smgmtGhostPendingN = null;
    smgmtRender();
    await smgmtRefreshBoard();
  } catch (e) {
    errEl.textContent = `Failed to create sprint: ${e.message}`;
    errEl.style.display = '';
    if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.innerHTML = `Create <span id="smgmt-ghost-confirm-label">sprint-${sprintN}</span> &amp; move`; }
    if (cancelBtn)  cancelBtn.disabled = false;
  }
}

async function smgmtDropOnSprint(event, targetSprintLabel) {
  event.preventDefault();
  document.querySelectorAll('.smgmt-sprint-block').forEach(el => {
    el.classList.remove('drag-over-sprint', 'drag-over-zone');
  });
  document.getElementById('smgmt-backlog')?.classList.remove('drag-over-sprint', 'drag-over-zone');

  // Sprint reorder drop
  if (_smgmtDragSprintLabel && targetSprintLabel && _smgmtDragSprintLabel !== targetSprintLabel) {
    const fromLabel = _smgmtDragSprintLabel;
    _smgmtDragSprintLabel = null;
    await smgmtReorderSprints(fromLabel, targetSprintLabel);
    return;
  }

  // Multi-ticket drop
  if (_smgmtDragTickets.length > 1) {
    const ticketsToMove = [..._smgmtDragTickets];
    _smgmtDragTickets = [];
    _smgmtDragTicket = null;

    const toMove = ticketsToMove.filter(t => (t.fromSprint || null) !== targetSprintLabel);
    if (toMove.length === 0) { smgmtClearSelection(); return; }

    const wasInOrder = !targetSprintLabel || _smgmtData.order.includes(targetSprintLabel);
    const prevData = toMove.map(t => {
      const iss = _smgmtData.issues.find(i => i.number === t.number);
      return { number: t.number, sprint: iss?.sprint ?? null, sprint_label: iss?.sprint_label ?? null };
    });

    for (const t of toMove) {
      const iss = _smgmtData.issues.find(i => i.number === t.number);
      if (iss) {
        const m = targetSprintLabel ? targetSprintLabel.match(/^sprint-(\d+)/) : null;
        iss.sprint = m ? parseInt(m[1], 10) : null;
        iss.sprint_label = targetSprintLabel || null;
      }
    }
    if (targetSprintLabel && !wasInOrder) _smgmtData.order.push(targetSprintLabel);
    smgmtRender();
    smgmtClearSelection();

    const isDotted = targetSprintLabel && /^sprint-\d+\.\d+$/.test(targetSprintLabel);
    const failures = [];
    await Promise.all(toMove.map(async (t) => {
      const apiBody = isDotted || targetSprintLabel === null
        ? { issue: t.number, sprint_label: targetSprintLabel }
        : { issue: t.number, sprint: targetSprintLabel ? parseInt(targetSprintLabel.split('-')[1], 10) : null };
      try {
        const res = await fetch('/api/sprint-planning/assign', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(apiBody),
        });
        if (!res.ok) throw new Error(await res.text());
      } catch (e) { failures.push(t.number); }
    }));

    if (failures.length > 0) {
      for (const prev of prevData) {
        if (failures.includes(prev.number)) {
          const iss2 = _smgmtData.issues.find(i => i.number === prev.number);
          if (iss2) { iss2.sprint = prev.sprint; iss2.sprint_label = prev.sprint_label; }
        }
      }
      if (targetSprintLabel && !wasInOrder) {
        _smgmtData.order = _smgmtData.order.filter(l => l !== targetSprintLabel);
      }
      smgmtRender();
      smgmtShowError(`Failed to move ticket(s) #${failures.join(', #')}.`);
    }
    await smgmtRefreshBoard();
    return;
  }

  // Single ticket drop
  if (_smgmtDragTicket) {
    const { number, fromSprint } = _smgmtDragTicket;
    _smgmtDragTicket = null;

    if (fromSprint === targetSprintLabel) return; // no-op

    const wasInOrder = !targetSprintLabel || _smgmtData.order.includes(targetSprintLabel);

    const iss = _smgmtData.issues.find(i => i.number === number);
    const prevSprintLabel = iss ? (iss.sprint_label || (iss.sprint != null ? `sprint-${iss.sprint}` : null)) : fromSprint;
    if (iss) {
      const targetBaseMatch = targetSprintLabel ? targetSprintLabel.match(/^sprint-(\d+)/) : null;
      iss.sprint = targetBaseMatch ? parseInt(targetBaseMatch[1], 10) : null;
      iss.sprint_label = targetSprintLabel || null;
    }
    if (targetSprintLabel && !wasInOrder) {
      _smgmtData.order.push(targetSprintLabel);
    }
    smgmtRender();

    const isDotted = targetSprintLabel && /^sprint-\d+\.\d+$/.test(targetSprintLabel);
    const apiBody = isDotted || targetSprintLabel === null
      ? { issue: number, sprint_label: targetSprintLabel }
      : { issue: number, sprint: targetSprintLabel ? parseInt(targetSprintLabel.split('-')[1], 10) : null };
    try {
      const res = await fetch('/api/sprint-planning/assign', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(apiBody),
      });
      if (!res.ok) throw new Error(await res.text());
      await smgmtRefreshBoard();
    } catch (e) {
      const iss2 = _smgmtData.issues.find(i => i.number === number);
      if (iss2) {
        const origBaseMatch = prevSprintLabel ? prevSprintLabel.match(/^sprint-(\d+)/) : null;
        iss2.sprint = origBaseMatch ? parseInt(origBaseMatch[1], 10) : null;
        iss2.sprint_label = prevSprintLabel || null;
      }
      if (targetSprintLabel && !wasInOrder) {
        _smgmtData.order = _smgmtData.order.filter(l => l !== targetSprintLabel);
      }
      smgmtRender();
      smgmtShowError(`Failed to move ticket #${number}: ${e.message}`);
    }
  }
}

async function smgmtReorderSprints(fromLabel, toLabel) {
  if (!_smgmtData) return;
  const order = [..._smgmtData.order];
  const fromIdx = order.indexOf(fromLabel);
  const toIdx   = order.indexOf(toLabel);
  if (fromIdx === -1 || toIdx === -1) return;

  // Swap positions
  order.splice(fromIdx, 1);
  order.splice(toIdx, 0, fromLabel);
  _smgmtData.order = order;
  smgmtRender();

  // Persist
  const slug = (_smgmtCurrentRepo || '').split('/')[1] || _smgmtCurrentRepo || '';
  try {
    const res = await fetch(`/api/sprints/order?project=${encodeURIComponent(slug)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ order }),
    });
    if (!res.ok) throw new Error(await res.text());
  } catch (e) {
    smgmtShowError('Failed to save sprint order: ' + e.message);
  }
}

// ── Multi-select (issue #206) ─────────────────────────────────────────────────

function smgmtToggleIssueSelect(issueNum, checked) {
  if (checked) {
    _smgmtSelectedIssues.add(issueNum);
  } else {
    _smgmtSelectedIssues.delete(issueNum);
  }
  _smgmtUpdateSelectionUI();
}

function smgmtToggleSelectAll(checkboxEl) {
  // Collect all visible ticket issue numbers from the DOM
  const allCbs = document.querySelectorAll('#smgmt-body .smgmt-ticket-cb, #smgmt-backlog-tickets .smgmt-ticket-cb');
  allCbs.forEach(cb => {
    const row = cb.closest('.smgmt-ticket');
    const num = row ? parseInt(row.dataset.issue, 10) : NaN;
    if (!isNaN(num)) {
      if (checkboxEl.checked) {
        _smgmtSelectedIssues.add(num);
      } else {
        _smgmtSelectedIssues.delete(num);
      }
      cb.checked = checkboxEl.checked;
      if (checkboxEl.checked) {
        row.classList.add('is-selected');
      } else {
        row.classList.remove('is-selected');
      }
    }
  });
  _smgmtUpdateSelectionUI();
}

function smgmtClearSelection() {
  _smgmtSelectedIssues.clear();
  _smgmtLastClickedIssue = null;
  _smgmtUpdateSelectionUI();
  // Uncheck all visible checkboxes without full re-render
  document.querySelectorAll('.smgmt-ticket-cb').forEach(cb => { cb.checked = false; });
  document.querySelectorAll('.smgmt-ticket.is-selected').forEach(el => el.classList.remove('is-selected'));
  const selectAllCb = document.getElementById('smgmt-backlog-select-all');
  if (selectAllCb) selectAllCb.checked = false;
}

function _smgmtUpdateSelectionUI() {
  const count = _smgmtSelectedIssues.size;
  const bulkBar = document.getElementById('smgmt-bulk-bar');
  const bulkCount = document.getElementById('smgmt-bulk-count');
  const moveToBtn = document.getElementById('smgmt-move-to-btn');

  if (bulkBar) {
    if (count > 0) {
      bulkBar.classList.remove('hidden');
    } else {
      bulkBar.classList.add('hidden');
    }
  }
  if (bulkCount) {
    bulkCount.textContent = `${count} selected`;
  }
  if (moveToBtn) {
    moveToBtn.style.display = count > 0 ? '' : 'none';
  }
}

function smgmtBulkMoveToOpen() {
  const count = _smgmtSelectedIssues.size;
  if (count === 0) return;

  // Populate sprint options
  const select = document.getElementById('smgmt-moveto-select');
  const countEl = document.getElementById('smgmt-moveto-count');
  const errEl = document.getElementById('smgmt-moveto-error');
  if (countEl) countEl.textContent = count;
  if (errEl) { errEl.textContent = ''; errEl.style.display = 'none'; }

  if (select) {
    select.innerHTML = '<option value="">— choose a sprint —</option>';
    const order = _smgmtData?.order || [];
    // Build all sprint labels (from order + plain sprint numbers), sorted naturally
    const orderSet = new Set(order);
    const plainFromSprints = (_smgmtData?.sprints || []).map(n => `sprint-${n}`);
    const allLabels = [...new Set([...order, ...plainFromSprints])].sort(sprintLabelCompare);

    for (const lbl of allLabels) {
      const opt = document.createElement('option');
      opt.value = lbl;
      opt.textContent = sprintLabelDisplay(lbl);
      select.appendChild(opt);
    }
    // Add backlog option
    const backlogOpt = document.createElement('option');
    backlogOpt.value = 'backlog';
    backlogOpt.textContent = 'Backlog (remove sprint label)';
    select.appendChild(backlogOpt);
  }

  const confirmBtn = document.getElementById('smgmt-moveto-confirm');
  if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = 'Move tickets'; }

  document.getElementById('smgmt-moveto-backdrop')?.classList.remove('hidden');
  document.getElementById('smgmt-moveto-modal')?.classList.remove('hidden');
}

function smgmtBulkMoveToClose() {
  document.getElementById('smgmt-moveto-backdrop')?.classList.add('hidden');
  document.getElementById('smgmt-moveto-modal')?.classList.add('hidden');
}

async function smgmtBulkMoveToConfirm() {
  const select = document.getElementById('smgmt-moveto-select');
  const errEl = document.getElementById('smgmt-moveto-error');
  const confirmBtn = document.getElementById('smgmt-moveto-confirm');
  const destination = select?.value;

  if (!destination) {
    if (errEl) { errEl.textContent = 'Please choose a destination sprint.'; errEl.style.display = ''; }
    return;
  }

  const issueNums = [..._smgmtSelectedIssues];
  if (issueNums.length === 0) { smgmtBulkMoveToClose(); return; }

  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Moving…'; }

  const isDottedDest = destination !== 'backlog' && /^sprint-\d+\.\d+$/.test(destination);
  const sprintNum = destination === 'backlog' ? null
    : parseInt(destination.match(/^sprint-(\d+)/)?.[1] || '0', 10);

  // Optimistic update: update local data
  for (const num of issueNums) {
    const iss = _smgmtData?.issues.find(i => i.number === num);
    if (iss) {
      iss.sprint = sprintNum;
      iss.sprint_label = destination === 'backlog' ? null : destination;
    }
  }
  smgmtRender();

  // API calls — fire in parallel
  const apiBodyForDest = isDottedDest || destination === 'backlog'
    ? (num) => ({ issue: num, sprint_label: destination === 'backlog' ? null : destination })
    : (num) => ({ issue: num, sprint: sprintNum });
  const failures = [];
  await Promise.all(issueNums.map(async num => {
    try {
      const res = await fetch('/api/sprint-planning/assign', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(apiBodyForDest(num)),
      });
      if (!res.ok) throw new Error(await res.text());
    } catch (e) {
      failures.push(num);
    }
  }));

  smgmtBulkMoveToClose();
  _smgmtSelectedIssues.clear();
  _smgmtUpdateSelectionUI();

  if (failures.length > 0) {
    smgmtShowError(`Failed to move ticket(s) #${failures.join(', #')}.`);
    await smgmtRefreshBoard();
  } else {
    await smgmtRefreshBoard();
  }
}

// Clear selection when navigating away from sprint-mgmt tab
function smgmtClearSelectionOnNav() {
  _smgmtSelectedIssues.clear();
  _smgmtLastClickedIssue = null;
  _smgmtUpdateSelectionUI();
}

// ── Multi-select click / keyboard / drag helpers (issue #363) ─────────────────

function smgmtTicketClick(event, issueNum) {
  if (event.target.classList.contains('smgmt-ticket-cb') ||
      event.target.closest('a') ||
      event.target.closest('button')) return;

  if (event.shiftKey && _smgmtLastClickedIssue !== null) {
    event.preventDefault();
    smgmtRangeSelect(_smgmtLastClickedIssue, issueNum);
  } else if (event.ctrlKey || event.metaKey) {
    event.preventDefault();
    const nowSelected = !_smgmtSelectedIssues.has(issueNum);
    smgmtToggleIssueSelect(issueNum, nowSelected);
    _smgmtLastClickedIssue = issueNum;
  } else {
    _smgmtLastClickedIssue = issueNum;
  }
}

function smgmtRangeSelect(fromIssue, toIssue) {
  const allRows = [...document.querySelectorAll('#smgmt-backlog-tickets .smgmt-ticket, #smgmt-body .smgmt-ticket')];
  const nums = allRows.map(r => parseInt(r.dataset.issue, 10));
  const fromIdx = nums.indexOf(fromIssue);
  const toIdx   = nums.indexOf(toIssue);
  if (fromIdx === -1 || toIdx === -1) return;
  const start = Math.min(fromIdx, toIdx);
  const end   = Math.max(fromIdx, toIdx);
  for (let i = start; i <= end; i++) {
    _smgmtSelectedIssues.add(nums[i]);
    const cb = allRows[i].querySelector('.smgmt-ticket-cb');
    if (cb) cb.checked = true;
    allRows[i].classList.add('is-selected');
  }
  _smgmtUpdateSelectionUI();
}

function smgmtTicketKeyDown(event, issueNum) {
  if (!event.shiftKey || (event.key !== 'ArrowDown' && event.key !== 'ArrowUp')) return;
  const allRows = [...document.querySelectorAll('#smgmt-backlog-tickets .smgmt-ticket, #smgmt-body .smgmt-ticket')];
  const nums = allRows.map(r => parseInt(r.dataset.issue, 10));
  const idx = nums.indexOf(issueNum);
  if (idx === -1) return;
  const targetIdx = event.key === 'ArrowDown' ? idx + 1 : idx - 1;
  if (targetIdx < 0 || targetIdx >= nums.length) return;
  event.preventDefault();
  _smgmtSelectedIssues.add(issueNum);
  _smgmtSelectedIssues.add(nums[targetIdx]);
  allRows[idx].classList.add('is-selected');
  const cb1 = allRows[idx].querySelector('.smgmt-ticket-cb');
  if (cb1) cb1.checked = true;
  allRows[targetIdx].classList.add('is-selected');
  const cb2 = allRows[targetIdx].querySelector('.smgmt-ticket-cb');
  if (cb2) cb2.checked = true;
  _smgmtLastClickedIssue = nums[targetIdx];
  allRows[targetIdx].focus();
  _smgmtUpdateSelectionUI();
}

function smgmtContainerClick(event) {
  if (!event.target.closest('.smgmt-ticket') && _smgmtSelectedIssues.size > 0) {
    smgmtClearSelection();
  }
}

function _smgmtSetMultiDragGhost(event, count) {
  const ghost = document.createElement('div');
  ghost.className = 'smgmt-multi-drag-ghost';
  ghost.textContent = `${count} tickets`;
  document.body.appendChild(ghost);
  event.dataTransfer.setDragImage(ghost, 20, 20);
  setTimeout(() => ghost.remove(), 0);
}

// ── Run sprint ────────────────────────────────────────────────────────────────

let _smgmtMigrateTargetLabel = null;  // sprint label we are about to run
let _smgmtMigrateChoices     = {};    // sprint_num (int) -> 'move' | 'leave'

// State for the estimate summary modal (issue #211)
let _smgmtEstTargetLabel       = null;   // sprint label pending confirmation
let _smgmtEstEarlierWithTickets = [];    // earlier sprints for post-confirm migration

async function smgmtRunSprint(sprintLabel) {
  if (!_smgmtCurrentRepo) return;
  const goal = _smgmtGoals[sprintLabel] || '';
  // issue #242: goal is no longer required to run a sprint

  // Determine target sprint number and gather earlier sprints for migration
  const [targetNum, targetSuffix] = (() => {
    const m = sprintLabel.match(/^sprint-(\d+)(?:\.(\d+))?$/);
    return m ? [parseInt(m[1], 10), m[2] ? parseInt(m[2], 10) : 0] : [0, 0];
  })();
  const allIssues = _smgmtData?.issues || [];
  const earlierWithTickets = [];  // [ { sprintNum, sprintLabel, tickets: [] } ]

  const { order } = _smgmtData || {};
  if (order) {
    for (const lbl of order) {
      const m = lbl.match(/^sprint-(\d+)(?:\.(\d+))?$/);
      if (!m) continue;
      const [n, s] = [parseInt(m[1], 10), m[2] ? parseInt(m[2], 10) : 0];
      // Only show earlier plain sprints for migration (not sub-labels)
      if (s !== 0 || n >= targetNum) continue;
      const tickets = allIssues.filter(
        t => (t.sprint_label || (t.sprint != null ? `sprint-${t.sprint}` : null)) === lbl
             && t.status !== 'done'
      );
      if (tickets.length > 0) {
        earlierWithTickets.push({ sprintNum: n, sprintLabel: lbl, tickets });
      }
    }
  }

  // Always show estimate summary modal first (issue #211)
  await smgmtEstOpen(sprintLabel, earlierWithTickets);
}

// ── Estimate summary modal (issue #211) ──────────────────────────────────────

async function smgmtEstOpen(sprintLabel, earlierWithTickets) {
  _smgmtEstTargetLabel        = sprintLabel;
  _smgmtEstEarlierWithTickets = earlierWithTickets;

  // Update header title placeholder while loading
  const titleEl = document.getElementById('smgmt-est-title');
  if (titleEl) titleEl.textContent = 'Run sprint?';

  // Show modal immediately with a loading state
  const bodyEl = document.getElementById('smgmt-est-body');
  if (bodyEl) bodyEl.innerHTML = '<p style="color:var(--text-muted);padding:8px 0;">Loading estimate…</p>';
  document.getElementById('smgmt-est-backdrop').classList.remove('hidden');
  document.getElementById('smgmt-est-modal').classList.remove('hidden');
  document.getElementById('smgmt-est-confirm').disabled = true;

  // Fetch estimate summary from server
  let summary = null;
  try {
    const res = await fetch(
      `/api/sprints/${encodeURIComponent(sprintLabel)}/estimate-summary?project=${encodeURIComponent(_smgmtCurrentRepo)}`
    );
    if (res.ok) {
      summary = await res.json();
    }
  } catch (_e) {
    // silently ignore; we'll render without summary data
  }

  // Render modal content
  if (!bodyEl) return;

  const sprintNum = parseInt(sprintLabel.split('-')[1], 10);
  const sprintName = summary ? summary.sprint_name : `Sprint ${sprintNum}`;
  const totalTickets = summary ? summary.total_tickets : '?';
  if (titleEl) titleEl.textContent = `${sprintName} — ${totalTickets} ticket${totalTickets !== 1 ? 's' : ''}`;

  let html = '';

  if (summary) {
    const { size_counts, total_minutes, unsized_numbers } = summary;

    // Size breakdown row (e.g. "3 × S, 5 × M, 2 × L")
    const sizes = ['S', 'M', 'L', 'XL'];
    const breakdownParts = sizes
      .filter(s => (size_counts[s] || 0) > 0)
      .map(s => `${size_counts[s]} &times; ${s}`);
    const unsizedCount = unsized_numbers.length;
    if (unsizedCount > 0) breakdownParts.push(`${unsizedCount} unsized`);
    const breakdownStr = breakdownParts.length > 0 ? breakdownParts.join(', ') : 'No sized tickets';

    // Total estimate display
    let totalStr;
    if (total_minutes > 0) {
      const hrs  = Math.floor(total_minutes / 60);
      const mins = total_minutes % 60;
      if (hrs > 0 && mins > 0) totalStr = `${hrs} h ${mins} min`;
      else if (hrs > 0) totalStr = `${hrs} h`;
      else totalStr = `${mins} min`;
    } else {
      totalStr = 'Unknown (no ticket sizes)';
    }

    html += `
      <table class="smgmt-est-table">
        <tr>
          <td>Size breakdown</td>
          <td>${breakdownStr}</td>
        </tr>
        <tr class="smgmt-est-total-row">
          <td>Total estimate</td>
          <td>${totalStr}</td>
        </tr>
      </table>`;

    if (unsizedCount > 0) {
      const nums = unsized_numbers.map(n => `#${n}`).join(', ');
      html += `
        <div class="smgmt-est-warning">
          <strong>Unsized tickets:</strong> ${nums}<br>
          <span>Unsized tickets will not contribute to the estimate.</span>
        </div>`;
    }
  } else {
    html += '<p style="color:var(--text-muted)">Could not load estimate data. You can still proceed.</p>';
  }

  bodyEl.innerHTML = html;
  document.getElementById('smgmt-est-confirm').disabled = false;
  document.getElementById('smgmt-est-modal').focus();
}

function smgmtEstClose() {
  document.getElementById('smgmt-est-backdrop').classList.add('hidden');
  document.getElementById('smgmt-est-modal').classList.add('hidden');
  _smgmtEstTargetLabel        = null;
  _smgmtEstEarlierWithTickets = [];
}

async function smgmtEstConfirm() {
  if (!_smgmtEstTargetLabel) return;
  const sprintLabel       = _smgmtEstTargetLabel;
  const earlierWithTickets = _smgmtEstEarlierWithTickets;
  smgmtEstClose();

  if (earlierWithTickets.length === 0) {
    // No migration needed — dispatch directly
    await smgmtDispatchRun(sprintLabel, []);
  } else {
    // Show migration modal next
    smgmtMigrateOpen(sprintLabel, earlierWithTickets);
  }
}

function smgmtMigrateOpen(targetLabel, earlierSprints) {
  _smgmtMigrateTargetLabel = targetLabel;
  _smgmtMigrateChoices = {};

  document.getElementById('smgmt-migrate-title').textContent = `Run ${sprintLabelDisplay(targetLabel)}?`;

  const bodyEl = document.getElementById('smgmt-migrate-body');
  let html = '';
  for (const { sprintNum, sprintLabel, tickets } of earlierSprints) {
    const ticketListHtml = tickets.map(t => {
      const status = t.status || 'backlog';
      return `<li class="smgmt-migrate-ticket-item">
        <span class="smgmt-migrate-ticket-num">#${t.number}</span>
        <span class="smgmt-migrate-ticket-title" title="${escapeHtml(t.title)}">${escapeHtml(t.title)}</span>
        <span class="smgmt-migrate-ticket-status">${escapeHtml(status)}</span>
      </li>`;
    }).join('');

    html += `<div class="smgmt-migrate-sprint-section">
      <div class="smgmt-migrate-sprint-heading">Sprint ${sprintNum} — ${tickets.length} open ticket${tickets.length !== 1 ? 's' : ''}</div>
      <ul class="smgmt-migrate-ticket-list">${ticketListHtml}</ul>
      <div class="smgmt-migrate-radios">
        <label class="smgmt-migrate-radio-label">
          <input type="radio" name="migrate-sprint-${sprintNum}" value="move"
                 checked onchange="smgmtMigrateChoice(${sprintNum}, 'move')">
          Move all tickets to Sprint ${targetNum}
        </label>
        <label class="smgmt-migrate-radio-label">
          <input type="radio" name="migrate-sprint-${sprintNum}" value="leave"
                 onchange="smgmtMigrateChoice(${sprintNum}, 'leave')">
          Leave in Sprint ${sprintNum}
        </label>
      </div>
    </div>`;

    // Default choice is 'move'
    _smgmtMigrateChoices[sprintNum] = 'move';
  }
  bodyEl.innerHTML = html;

  smgmtMigrateUpdateConfirmBtn();

  document.getElementById('smgmt-migrate-backdrop').classList.remove('hidden');
  document.getElementById('smgmt-migrate-modal').classList.remove('hidden');
  document.getElementById('smgmt-migrate-modal').focus();
}

function smgmtMigrateChoice(sprintNum, choice) {
  _smgmtMigrateChoices[sprintNum] = choice;
  smgmtMigrateUpdateConfirmBtn();
}

function smgmtMigrateUpdateConfirmBtn() {
  const confirmBtn = document.getElementById('smgmt-migrate-confirm');
  if (!confirmBtn) return;
  // All sprint groups must have a selection (they default to 'move', so always valid after open)
  const allChosen = Object.values(_smgmtMigrateChoices).every(v => v === 'move' || v === 'leave');
  confirmBtn.disabled = !allChosen;
}

function smgmtMigrateClose() {
  document.getElementById('smgmt-migrate-backdrop').classList.add('hidden');
  document.getElementById('smgmt-migrate-modal').classList.add('hidden');
  _smgmtMigrateTargetLabel = null;
  _smgmtMigrateChoices = {};
}

async function smgmtMigrateConfirm() {
  if (!_smgmtMigrateTargetLabel) return;
  const migrateFrom = Object.entries(_smgmtMigrateChoices)
    .filter(([, choice]) => choice === 'move')
    .map(([sprintNum]) => parseInt(sprintNum, 10));

  const targetLabel = _smgmtMigrateTargetLabel;
  smgmtMigrateClose();
  await smgmtDispatchRun(targetLabel, migrateFrom);
}

async function smgmtDispatchRun(sprintLabel, migrateFrom) {
  const runBtnId = `smgmt-run-btn-${sprintLabel.replace(/-/g, '_').replace(/\./g, '_')}`;
  const btn = document.getElementById(runBtnId);
  if (btn) { btn.disabled = true; btn.textContent = 'Starting…'; }

  try {
    const res = await fetch('/api/sprints/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project: _smgmtCurrentRepo, sprint_label: sprintLabel, migrate_from: migrateFrom }),
    });
    if (!res.ok) {
      const rawText = await res.text();
      let errMsg = rawText;
      try {
        const errJson = JSON.parse(rawText);
        if (errJson && errJson.detail) errMsg = errJson.detail;
      } catch (_) { /* use raw text if not JSON */ }
      throw new Error(errMsg);
    }
    const data = await res.json();

    if (data.migrated_count > 0) {
      const fromNums = (data.migrate_from || []).join(', ');
      showSuccessToast(`Migrated ${data.migrated_count} ticket${data.migrated_count !== 1 ? 's' : ''} from Sprint ${fromNums} to ${sprintLabelDisplay(sprintLabel)}. Dispatching sprint…`);
    } else {
      showSuccessToast('Sprint dispatched. Watching for progress...');
    }

    // AC9: track dispatch time for stall warning
    _smgmtLastDispatchTime = Date.now();
    _smgmtLastStatusChangeTime = Date.now();
    _smgmtStallLabel = sprintLabel;
    _ensureStallWarningTimer();

    smgmtPollRunStatus();
  } catch (e) {
    showToast('Failed to dispatch: ' + e.message);
    if (btn) {
      const sprintTickets = (_smgmtData?.issues || []).filter(
        t => t.sprint != null && `sprint-${t.sprint}` === sprintLabel
      );
      const canRun = sprintTickets.length >= 1;
      btn.disabled = !canRun;
      btn.textContent = 'Run sprint';
    }
  }
}

async function smgmtPollRunStatus() {
  try {
    const [runAllRes, statusRes] = await Promise.all([
      fetch('/api/sprints/running-all'),
      fetch('/api/sprint-status').catch(() => ({ ok: false })),
    ]);

    // Build new all-running map from API response
    if (runAllRes.ok) {
      const runAllData = await runAllRes.json();
      const newMap = {};
      for (const entry of (runAllData.running || [])) {
        const key = `${entry.project}:${entry.sprint_label}`;
        newMap[key] = entry;
      }
      _smgmtAllRunning = newMap;
    }

    // Sprint status for progress text — build a map of sprint_label -> status data.
    // Supports both the new multi-sprint format {statuses: [...]} and the legacy
    // single-sprint format {sprint_label, issues, ...}.
    let sprintStatusMap = {};  // sprint_label -> status object
    if (statusRes.ok) {
      const statusData = await statusRes.json();
      const sprints = statusData.running_sprints || [];
      for (const s of sprints) {
        if (s.sprint_label) sprintStatusMap[s.sprint_label] = s;
      }
    }

    // AC9: update last status-change time if any per-ticket status_changed_at is newer
    for (const s of Object.values(sprintStatusMap)) {
      for (const iss of (s.issues || [])) {
        if (iss.status_changed_at) {
          const t = new Date(iss.status_changed_at).getTime();
          if (!isNaN(t) && t > _smgmtLastStatusChangeTime) {
            _smgmtLastStatusChangeTime = t;
          }
        }
      }
    }

    smgmtApplyRunState(sprintStatusMap);
    _updateRunningBanner();
    _updateOverviewRunningBadges();
    smgmtSyncLivePanels();
  } catch { /* ignore poll errors */ }
}

function smgmtApplyRunState(sprintStatusMap) {
  // Per-#123: each sprint card checks its own (project, sprint_label) key independently.
  // Multiple cards can be in RUNNING state simultaneously.
  // sprintStatusMap: { sprint_label -> status_object } built from /api/sprint-status
  if (!sprintStatusMap) sprintStatusMap = {};

  // ── Pass 1: Clear all dynamic state from every block ─────────────────────────
  document.querySelectorAll('.smgmt-sprint-block').forEach(block => {
    block.classList.remove('smgmt-running');
    const hdr = block.querySelector('.smgmt-sprint-header');
    if (hdr) {
      hdr.classList.remove('smgmt-running-header');
      // Remove injected lock icon
      hdr.querySelectorAll('.smgmt-lock-icon').forEach(el => el.remove());
      // Restore draggable on header
      hdr.setAttribute('draggable', 'true');
    }
    // Remove injected running elements (badge, progress section, kill btn)
    block.querySelectorAll('.smgmt-running-badge, .smgmt-progress-text, .smgmt-kill-btn, .smgmt-progress-section').forEach(el => el.remove());
    // Remove status circles injected on ticket rows
    block.querySelectorAll('.smgmt-ticket-status-circle, .smgmt-agent-pill, .smgmt-ticket-elapsed-time').forEach(el => el.remove());
    // Remove running/pending/done classes from ticket rows
    block.querySelectorAll('.smgmt-ticket').forEach(el => {
      el.classList.remove('is-running', 'is-pending', 'is-done');
    });
    // Restore any hidden unified action buttons
    block.querySelectorAll('.smgmt-run-btn').forEach(btn => btn.style.display = '');
    // Restore hidden delete buttons
    block.querySelectorAll('.smgmt-delete-btn').forEach(btn => btn.style.display = '');
    // Restore finish sprint button visibility (re-hidden by the HTML class if not hasCompleted)
    block.querySelectorAll('.smgmt-finish-btn').forEach(btn => btn.style.display = '');
    // Restore goal input
    const goalInput = block.querySelector('.smgmt-goal-input');
    if (goalInput) {
      goalInput.removeAttribute('readonly');
      goalInput.classList.remove('smgmt-goal-readonly');
    }
    // Restore delete button
    const blockId = block.id; // "smgmt-block-sprint-N" or "smgmt-block-sprint-N.X"
    const blockLabel = blockId.replace('smgmt-block-', '');
    const safeLabel = blockLabel.replace(/-/g, '_').replace(/\./g, '_');
    const deleteBtn = document.getElementById(`smgmt-delete-btn-${safeLabel}`);
    if (deleteBtn) {
      deleteBtn.disabled = false;
      deleteBtn.title = 'Delete sprint';
    }
    // Restore draggable on ticket rows
    block.querySelectorAll('.smgmt-ticket').forEach(ticketEl => {
      ticketEl.setAttribute('draggable', 'true');
    });
  });

  // ── Pass 2: Apply RUNNING state to each running sprint in this project ────────
  for (const [key, entry] of Object.entries(_smgmtAllRunning)) {
    const { project: runProj, sprint_label: runLabel } = entry;
    if (runProj !== _smgmtCurrentRepo) continue;  // not the currently viewed project

    const safeLabel = runLabel.replace(/-/g, '_').replace(/\./g, '_');
    const block = document.getElementById(`smgmt-block-${runLabel}`);
    if (!block) continue;

    block.classList.add('smgmt-running');
    const hdr = block.querySelector('.smgmt-sprint-header');
    if (!hdr) continue;

    hdr.classList.add('smgmt-running-header');

    // Disable Delete button (issue #186 AC: disabled with tooltip)
    const deleteBtn = document.getElementById(`smgmt-delete-btn-${safeLabel}`);
    if (deleteBtn) {
      deleteBtn.disabled = true;
      deleteBtn.title = 'Sprint is running';
    }

    // Make goal input readonly + muted (issue #186 AC)
    const goalInput = document.getElementById(`smgmt-goal-${safeLabel}`);
    if (goalInput) {
      goalInput.setAttribute('readonly', '');
      goalInput.classList.add('smgmt-goal-readonly');
    }

    // Suppress drag-and-drop for the running sprint header (issue #186 AC)
    hdr.setAttribute('draggable', 'false');
    // Suppress drag on ticket rows inside the running sprint (issue #186 AC)
    block.querySelectorAll('.smgmt-ticket').forEach(ticketEl => {
      ticketEl.setAttribute('draggable', 'false');
    });

    // Insert RUNNING badge (pulse pill) in the header-left div, after sprint name / NEXT UP badge
    const runBadge = document.createElement('span');
    runBadge.className = 'smgmt-running-badge';
    runBadge.setAttribute('aria-label', 'Running sprint');
    runBadge.innerHTML = '<span class="smgmt-pulse-dot" aria-hidden="true"></span><span>Running</span>';
    const hdrLeft = hdr.querySelector('.smgmt-sprint-header-left') || hdr;
    const nextBadge = hdrLeft.querySelector('.smgmt-next-badge');
    const sprintName = hdrLeft.querySelector('.smgmt-sprint-name');
    const insertAfter = nextBadge || sprintName;
    if (insertAfter && insertAfter.nextSibling) {
      hdrLeft.insertBefore(runBadge, insertAfter.nextSibling);
    } else {
      hdrLeft.appendChild(runBadge);
    }

    // Hide unified action button + Finish/Delete buttons; insert Cancel sprint button
    const actionBtn = document.getElementById(`smgmt-run-btn-${safeLabel}`);
    if (actionBtn) actionBtn.style.display = 'none';
    const deleteBtn2 = document.getElementById(`smgmt-delete-btn-${safeLabel}`);
    if (deleteBtn2) deleteBtn2.style.display = 'none';

    const hdrRight = hdr.querySelector('.smgmt-sprint-header-right') || hdr;
    // Only add one Cancel button (guard against double-apply)
    if (!hdrRight.querySelector('.smgmt-kill-btn')) {
      const killBtn = document.createElement('button');
      killBtn.className = 'smgmt-kill-btn';
      killBtn.innerHTML = '<i class="ti ti-player-stop-filled"></i> Cancel sprint';
      killBtn.onclick = () => smgmtKillSprint(runLabel);
      hdrRight.appendChild(killBtn);
    }

    // Inject progress section between goal bar and ticket list
    const sprintStatus = sprintStatusMap[runLabel];
    const goalBarEl = document.getElementById(`smgmt-goal-bar-${safeLabel}`);
    const ticketsEl2 = document.getElementById(`smgmt-tickets-${runLabel}`);
    // Only inject once
    if (sprintStatus && ticketsEl2 && !block.querySelector('.smgmt-progress-section')) {
      const issues = sprintStatus.issues || [];
      const total  = issues.length;
      const done   = issues.filter(i => i.status === 'done' || i.status === 'skipped').length;
      const pct    = total > 0 ? Math.round((done / total) * 100) : 0;

      let estText = '';
      if (done > 0 && sprintStatus.wall_clock_secs > 0 && total > done) {
        const avgSecs   = sprintStatus.wall_clock_secs / done;
        const remMins   = Math.round(avgSecs * (total - done) / 60);
        if (remMins > 0) {
          const h = Math.floor(remMins / 60);
          const m = remMins % 60;
          estText = h > 0 ? ` · est. ${h}h ${m}m remaining` : ` · est. ${m}m remaining`;
        }
      }

      const progSection = document.createElement('div');
      progSection.className = 'smgmt-progress-section';
      progSection.innerHTML = `
        <div class="smgmt-progress-meta">
          <span>${done} of ${total} ticket${total !== 1 ? 's' : ''} complete</span>
          <span>${pct}%${estText}</span>
        </div>
        <div class="smgmt-progress-bar">
          <div class="smgmt-progress-fill" style="width:${pct}%"
               role="progressbar"
               aria-valuenow="${pct}"
               aria-valuemin="0"
               aria-valuemax="100"></div>
        </div>`;

      // Insert after goal bar if present, otherwise before ticket list
      if (goalBarEl && goalBarEl.nextSibling) {
        block.insertBefore(progSection, goalBarEl.nextSibling);
      } else if (ticketsEl2) {
        block.insertBefore(progSection, ticketsEl2);
      }
    }

    // Hide Finish Sprint button while running (issue #195 AC)
    const finishBtn = document.getElementById(`smgmt-finish-btn-${safeLabel}`);
    if (finishBtn) finishBtn.style.display = 'none';
  }

  // ── Pass 4: Per-ticket live status badges, spinners, elapsed counters ──────────
  smgmtApplyTicketLiveStatus(sprintStatusMap);
  _ensureElapsedTimer();

  // ── Pass 5: Update unified action button state for all visible sprint buttons ──
  // The unified button id is "smgmt-run-btn-<safeLabel>" for all sprints.
  document.querySelectorAll('.smgmt-run-btn').forEach(btn => {
    const safeId = btn.id.replace('smgmt-run-btn-', '');
    // Reconstruct the label: replace first underscore with dash (sprint_N -> sprint-N)
    const btnLabel = safeId.replace('_', '-');
    const runKey = `${_smgmtCurrentRepo}:${btnLabel}`;
    const isThisRunning = !!_smgmtAllRunning[runKey];

    if (isThisRunning) {
      // Running sprint: action button is hidden above; skip state update
      return;
    }

    const sprintTickets = (_smgmtData?.issues || []).filter(
      t => t.sprint != null && `sprint-${t.sprint}` === btnLabel
    );
    const hasCompleted = smgmtHasCompletedTickets(sprintTickets);

    if (hasCompleted) {
      // Re-run mode: always enabled
      btn.disabled = false;
      btn.title = '';
    } else {
      const hasTickets = sprintTickets.length >= 1;
      // issue #242: goal is no longer required to enable Run Sprint
      const canRun = hasTickets;
      btn.disabled = !canRun;
      btn.title = !hasTickets ? 'Add at least one ticket first' : '';
    }
  });
}

// ── Per-ticket live agent status (issue #131) ─────────────────────────────────

/**
 * Return an sbadge HTML string for the given agent_status.
 * label format: "<status> at HH:MM" for point-in-time states,
 *               "<status> Xm Ys"    for elapsed-time states (running).
 * Colors: gray=queued, blue=coder stages, purple=tester stages, amber=completed, red=failed.
 */
function smgmtAgentStatusBadge(agentStatus, statusChangedAt) {
  if (!agentStatus) return '';

  const colorMap = {
    queued:             'gray',
    coder_dispatched:   'blue',
    coder_running:      'blue',
    coder_done:         'blue',
    tester_dispatched:  'purple',
    tester_running:     'purple',
    tester_done:        'purple',
    completed:          'amber',
    failed:             'red',
  };
  const color = colorMap[agentStatus] || 'gray';

  let timeStr = '';
  if (statusChangedAt) {
    try {
      const ts = new Date(statusChangedAt);
      const h = ts.getHours().toString().padStart(2, '0');
      const m = ts.getMinutes().toString().padStart(2, '0');
      timeStr = ` at ${h}:${m}`;
    } catch (_) { /* ignore */ }
  }

  const label = agentStatus.replace(/_/g, ' ') + timeStr;
  return `<span class="smgmt-ticket-agent-badge sbadge ${color}">${label}</span>`;
}

/**
 * Derive ticket run state (done | failed | skipped | running | pending) from agent_status or labels.
 * Graceful fallback: if agent_status is absent, check GitHub labels.
 */
function _smgmtTicketRunState(issueData) {
  const agentStatus = issueData.agent_status || '';
  if (agentStatus === 'failed') return 'failed';
  if (issueData.status === 'skipped') return 'skipped';
  if (agentStatus === 'completed' || issueData.status === 'done') {
    return 'done';
  }
  if (agentStatus === 'coder_running' || agentStatus === 'tester_running') {
    return 'running';
  }
  // Fallback: check GitHub labels
  const labels = issueData.labels || [];
  const labelNames = labels.map(l => (typeof l === 'string' ? l : l.name || ''));
  if (labelNames.some(n => n === 'uat')) return 'done';
  if (labelNames.some(n => /coder.running|tester.running/i.test(n))) return 'running';
  return 'pending';
}

/**
 * Build the active agent type string ('coder' | 'tester' | null) from agent_status.
 */
function _smgmtActiveAgent(agentStatus) {
  if (!agentStatus) return null;
  if (agentStatus.includes('coder')) return 'coder';
  if (agentStatus.includes('tester')) return 'tester';
  return null;
}

/**
 * Format a clock timestamp as HH:MM string.
 * Used in stage labels like "TESTER RUNNING 10:17".
 */
function _smgmtClockHHMM(isoTimestamp) {
  if (!isoTimestamp) return '';
  try {
    const ts = new Date(isoTimestamp);
    const h = ts.getHours().toString().padStart(2, '0');
    const m = ts.getMinutes().toString().padStart(2, '0');
    return `${h}:${m}`;
  } catch (_) { return ''; }
}

/**
 * Format elapsed seconds as HH:MM (e.g. 90 -> "01:30").
 * Used for the elapsed time counter element.
 */
function _smgmtFormatElapsedHHMM(secs) {
  const totalMins = Math.floor(secs / 60);
  const ss = secs % 60;
  const hh = Math.floor(totalMins / 60);
  const mm = totalMins % 60;
  if (hh > 0) {
    return `${hh.toString().padStart(2, '0')}:${mm.toString().padStart(2, '0')}`;
  }
  return `${mm.toString().padStart(2, '0')}:${ss.toString().padStart(2, '0')}`;
}

/**
 * Build the stage label { text, cssClass } for a ticket given its run state and agent_status.
 * Returns { text: string, cssClass: string } for the .smgmt-ticket-stage-label element.
 *
 * Stage label spec (issue #251):
 *   running  → "TESTER RUNNING HH:MM" or "CODER RUNNING HH:MM"      (blue)
 *   done     → "COMPLETED HH:MM"                                     (default/muted)
 *   failed   → "TESTER REJECTED"                                     (red)
 *   skipped  → "SKIPPED"                                             (muted)
 *   pending  → "QUEUED" | "BACKLOG" | "SIT" | "UAT" (from labels)   (default)
 */
function _smgmtBuildStageLabel(runState, issueData) {
  const agentStatus = issueData.agent_status || '';
  const clockTime = _smgmtClockHHMM(issueData.status_changed_at);

  if (runState === 'running') {
    const agentType = _smgmtActiveAgent(agentStatus);
    const prefix = agentType ? agentType.toUpperCase() : 'AGENT';
    const text = clockTime ? `${prefix} RUNNING ${clockTime}` : `${prefix} RUNNING`;
    return { text, cssClass: 'stage-running' };
  }

  if (runState === 'done') {
    const text = clockTime ? `COMPLETED ${clockTime}` : 'COMPLETED';
    return { text, cssClass: 'stage-completed' };
  }

  if (runState === 'failed') {
    return { text: 'TESTER REJECTED', cssClass: 'stage-rejected' };
  }

  if (runState === 'skipped') {
    return { text: 'SKIPPED', cssClass: 'stage-skipped' };
  }

  // pending: derive from GitHub labels / status
  const labels = issueData.labels || [];
  const labelNames = labels.map(l => (typeof l === 'string' ? l : l.name || ''));
  if (issueData.status === 'uat' || labelNames.includes('uat')) {
    return { text: 'UAT', cssClass: 'stage-uat' };
  }
  if (issueData.status === 'sit' || labelNames.includes('sit')) {
    return { text: 'SIT', cssClass: 'stage-sit' };
  }
  if (issueData.status === 'in-progress' || labelNames.includes('in-progress')) {
    return { text: 'QUEUED', cssClass: '' };
  }
  if (issueData.status === 'backlog' || labelNames.includes('backlog')) {
    return { text: 'BACKLOG', cssClass: '' };
  }
  return { text: 'QUEUED', cssClass: '' };
}

/**
 * Update per-ticket badges, spinners, status circles, and elapsed counters.
 * Called from smgmtApplyRunState whenever sprintStatusMap is available.
 */
function smgmtApplyTicketLiveStatus(sprintStatusMap) {
  if (!sprintStatusMap) return;

  for (const [runLabel, sprintStatus] of Object.entries(sprintStatusMap)) {
    const issues = sprintStatus.issues || [];
    for (const issueData of issues) {
      const ticketEl = document.getElementById(`smgmt-ticket-${issueData.number}`);
      if (!ticketEl) continue;

      const agentStatus = issueData.agent_status;
      const runState    = _smgmtTicketRunState(issueData);
      const isRunning   = runState === 'running';
      const isDone      = runState === 'done';
      const isPending   = runState === 'pending';

      // Remove existing injected live-status elements (keep .smgmt-ticket-status label)
      ticketEl.querySelectorAll(
        '.smgmt-ticket-agent-badge, .smgmt-ticket-spinner, .smgmt-ticket-elapsed, ' +
        '.smgmt-ticket-status-circle, .smgmt-agent-pill, .smgmt-ticket-elapsed-time'
      ).forEach(el => el.remove());

      // Apply row-level class for title styling
      ticketEl.classList.remove('is-running', 'is-done', 'is-pending');
      ticketEl.classList.add(`is-${runState}`);

      // Build status circle
      const circle = document.createElement('span');
      circle.className = `smgmt-ticket-status-circle ${runState}`;
      if (isDone) {
        circle.textContent = '✓';
        circle.setAttribute('aria-label', 'Done');
      } else if (isRunning) {
        circle.setAttribute('aria-label', 'Running');
        const dot = document.createElement('span');
        dot.className = 'smgmt-inner-dot';
        circle.appendChild(dot);
      } else {
        circle.textContent = '○';
        circle.setAttribute('aria-label', 'Pending');
      }

      // Insert circle before the ticket number (after checkbox and grip)
      const numEl = ticketEl.querySelector('.smgmt-ticket-num');
      if (numEl) {
        ticketEl.insertBefore(circle, numEl);
      } else {
        ticketEl.insertBefore(circle, ticketEl.firstChild);
      }

      // Elapsed time on the right
      const timeEl = document.createElement('span');
      timeEl.className = 'smgmt-ticket-elapsed-time';
      if (isRunning && issueData.status_changed_at) {
        const startTs = new Date(issueData.status_changed_at).getTime();
        if (!isNaN(startTs)) {
          timeEl.dataset.startTs = startTs;
          _smgmtUpdateElapsedTimeEl(timeEl);
        } else {
          timeEl.textContent = '—';
        }
      } else if (isDone && issueData.elapsed) {
        // elapsed may be a formatted string like "18m 04s" or seconds number
        const elVal = issueData.elapsed;
        timeEl.textContent = typeof elVal === 'number' ? formatDuration(elVal) : elVal;
      } else {
        timeEl.textContent = '—';
      }
      ticketEl.appendChild(timeEl);

      // Agent pill on running ticket
      if (isRunning) {
        const agentType = _smgmtActiveAgent(agentStatus);
        if (agentType) {
          const pill = document.createElement('span');
          pill.className = `smgmt-agent-pill ${agentType}`;
          pill.textContent = agentType.toUpperCase();
          // Insert before elapsed time
          ticketEl.insertBefore(pill, timeEl);
        }

        // Legacy spinner (for existing status badge area)
        const spinner = document.createElement('i');
        spinner.className = 'ti ti-loader-2 smgmt-ticket-spinner';
        const titleEl = ticketEl.querySelector('.smgmt-ticket-title');
        if (titleEl && titleEl.nextSibling) {
          ticketEl.insertBefore(spinner, titleEl.nextSibling);
        }
      }

      // Add legacy agent status badge (for sprint cockpit compatibility)
      const badgeHtml = smgmtAgentStatusBadge(agentStatus, issueData.status_changed_at);
      if (badgeHtml) {
        const statusEl = ticketEl.querySelector('.smgmt-ticket-status');
        const badgeWrap = document.createElement('span');
        badgeWrap.innerHTML = badgeHtml;
        const badgeNode = badgeWrap.firstElementChild;
        if (statusEl) {
          ticketEl.insertBefore(badgeNode, statusEl);
        } else {
          ticketEl.insertBefore(badgeNode, timeEl);
        }
      }

      // Elapsed counter for running states (legacy AC7 - kept for compatibility)
      if (isRunning && issueData.status_changed_at) {
        const startTs = new Date(issueData.status_changed_at).getTime();
        if (!isNaN(startTs)) {
          const elapsedEl = document.createElement('span');
          elapsedEl.className = 'smgmt-ticket-elapsed smgmt-ticket-agent-badge sbadge gray';
          elapsedEl.style.display = 'none'; // hidden; using smgmt-ticket-elapsed-time instead
          elapsedEl.dataset.startTs = startTs;
          ticketEl.appendChild(elapsedEl);
          _smgmtUpdateElapsedEl(elapsedEl);
        }
      }
    }
  }
}

/** Render elapsed time into a legacy elapsed element. */
function _smgmtUpdateElapsedEl(el) {
  const startTs = parseInt(el.dataset.startTs, 10);
  if (isNaN(startTs)) return;
  const secs = Math.floor((Date.now() - startTs) / 1000);
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  el.textContent = m > 0 ? `${m}m ${s}s` : `${s}s`;
}

/** Render elapsed time into the new elapsed-time element (HH:MM format). */
function _smgmtUpdateElapsedTimeEl(el) {
  const startTs = parseInt(el.dataset.startTs, 10);
  if (isNaN(startTs)) return;
  const secs = Math.floor((Date.now() - startTs) / 1000);
  el.textContent = _smgmtFormatElapsedHHMM(secs);
}

// Elapsed counter interval (AC7) — update all elapsed elements every second.
let _smgmtElapsedTimer = null;

function _ensureElapsedTimer() {
  if (_smgmtElapsedTimer) return;
  _smgmtElapsedTimer = setInterval(() => {
    document.querySelectorAll('.smgmt-ticket-elapsed').forEach(_smgmtUpdateElapsedEl);
    document.querySelectorAll('.smgmt-ticket-elapsed-time[data-start-ts]').forEach(_smgmtUpdateElapsedTimeEl);
  }, 1000);
}

// Stall warning tracking (AC9)
let _smgmtLastDispatchTime    = 0;
let _smgmtLastStatusChangeTime = 0;
let _smgmtStallLabel           = null;
let _smgmtStallTimer           = null;
const _SMGMT_STALL_THRESHOLD_MS = 30000;

function _ensureStallWarningTimer() {
  if (_smgmtStallTimer) clearInterval(_smgmtStallTimer);
  _smgmtStallTimer = setInterval(_smgmtCheckStall, 5000);
}

function _smgmtCheckStall() {
  if (!_smgmtStallLabel) return;

  // Stop checking once sprint is no longer running
  const runKey = `${_smgmtCurrentRepo}:${_smgmtStallLabel}`;
  if (!_smgmtAllRunning[runKey]) {
    if (_smgmtStallTimer) { clearInterval(_smgmtStallTimer); _smgmtStallTimer = null; }
    _smgmtRemoveStallWarning(_smgmtStallLabel);
    return;
  }

  const now = Date.now();
  const sinceDispatch     = now - _smgmtLastDispatchTime;
  const sinceStatusChange = now - _smgmtLastStatusChangeTime;

  if (sinceDispatch >= _SMGMT_STALL_THRESHOLD_MS && sinceStatusChange >= _SMGMT_STALL_THRESHOLD_MS) {
    _smgmtShowStallWarning(_smgmtStallLabel);
  } else {
    _smgmtRemoveStallWarning(_smgmtStallLabel);
  }
}

let _smgmtStallRefreshInterval = null;

function _smgmtShowStallWarning(sprintLabel) {
  const block = document.getElementById(`smgmt-block-${sprintLabel}`);
  if (!block) return;
  if (block.querySelector('.smgmt-stall-warning')) return; // already shown

  const warn = document.createElement('div');
  warn.className = 'smgmt-stall-warning';

  const header = document.createElement('div');
  header.className = 'smgmt-stall-header';

  const title = document.createElement('span');
  title.textContent = 'Sprint launched but no progress reported after 30s.';

  const chevron = document.createElement('span');
  chevron.className = 'smgmt-stall-chevron';
  chevron.textContent = '▶';

  const actions = document.createElement('span');
  actions.className = 'smgmt-stall-actions';

  const refreshBtn = document.createElement('button');
  refreshBtn.className = 'smgmt-stall-btn';
  refreshBtn.textContent = 'Refresh log';

  const cancelBtn = document.createElement('button');
  cancelBtn.className = 'smgmt-stall-btn smgmt-stall-btn-cancel';
  cancelBtn.textContent = 'Cancel sprint';
  cancelBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (!_smgmtCurrentRepo) return;
    try {
      await fetch(`/api/sprints/run/${encodeURIComponent(sprintLabel)}?project=${encodeURIComponent(_smgmtCurrentRepo)}`, { method: 'DELETE' });
      _smgmtRemoveStallWarning(sprintLabel);
    } catch (err) {
      console.error('Cancel sprint failed:', err);
    }
  });

  actions.appendChild(refreshBtn);
  actions.appendChild(cancelBtn);
  header.appendChild(chevron);
  header.appendChild(title);
  header.appendChild(actions);

  const body = document.createElement('div');
  body.className = 'smgmt-stall-body';
  body.style.display = 'none';

  const pre = document.createElement('pre');
  pre.className = 'smgmt-stall-log';
  pre.textContent = 'Loading…';
  body.appendChild(pre);

  warn.appendChild(header);
  warn.appendChild(body);
  block.appendChild(warn);

  let expanded = false;

  async function fetchLog() {
    if (!_smgmtCurrentRepo) return;
    const url = `/api/sprints/${encodeURIComponent(sprintLabel)}/dispatch-log?project=${encodeURIComponent(_smgmtCurrentRepo)}&tail_lines=40`;
    try {
      const res = await fetch(url);
      const data = await res.json();
      if (data.found) {
        pre.textContent = data.tail || '(log file is empty)';
      } else {
        pre.textContent = `No dispatch log file found yet at ${data.log_dir}.\nThe subprocess may have failed before writing any output.`;
      }
    } catch (err) {
      pre.textContent = `Error fetching log: ${err.message}`;
    }
  }

  function toggleExpand() {
    expanded = !expanded;
    chevron.textContent = expanded ? '▼' : '▶';
    body.style.display = expanded ? 'block' : 'none';
    if (expanded) fetchLog();
  }

  header.addEventListener('click', toggleExpand);
  refreshBtn.addEventListener('click', (e) => { e.stopPropagation(); fetchLog(); });

  // Auto-expand and start periodic refresh
  toggleExpand();
  if (_smgmtStallRefreshInterval) clearInterval(_smgmtStallRefreshInterval);
  _smgmtStallRefreshInterval = setInterval(() => {
    if (block.querySelector('.smgmt-stall-warning') && expanded) fetchLog();
  }, 5000);
}

function _smgmtRemoveStallWarning(sprintLabel) {
  const block = document.getElementById(`smgmt-block-${sprintLabel}`);
  if (block) block.querySelectorAll('.smgmt-stall-warning').forEach(el => el.remove());
  if (_smgmtStallRefreshInterval) {
    clearInterval(_smgmtStallRefreshInterval);
    _smgmtStallRefreshInterval = null;
  }
}

// ── Per-#123: Running banner + overview badges ────────────────────────────────

/**
 * Update the "N sprints running" banner at the top of the dashboard.
 * Shows when 2+ sprints are running simultaneously with scroll-to anchors.
 */
function _updateRunningBanner() {
  let banner = document.getElementById('running-sprints-banner');
  const entries = Object.values(_smgmtAllRunning);
  const count = entries.length;

  if (count < 2) {
    if (banner) banner.remove();
    return;
  }

  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'running-sprints-banner';
    banner.className = 'running-sprints-banner';
    // Insert below alert banners
    const alertBanners = document.getElementById('alert-banners');
    if (alertBanners && alertBanners.parentNode) {
      alertBanners.parentNode.insertBefore(banner, alertBanners.nextSibling);
    } else {
      document.body.insertBefore(banner, document.body.firstChild);
    }
  }

  const links = entries.map(e => {
    const projName = e.project.split('/')[1] || e.project;
    const sprintN  = e.sprint_label.replace('sprint-', '');
    return `<a class="running-sprint-anchor" href="#" onclick="drillIntoProject('${escapeHtml(e.project)}','sprint-mgmt');event.preventDefault();">${escapeHtml(projName)} S${sprintN}</a>`;
  }).join(', ');

  banner.innerHTML = `
    <i class="ti ti-loader-2 running-banner-spin"></i>
    <strong>${count} sprints running simultaneously:</strong> ${links}
    <button class="running-banner-dismiss" onclick="this.parentElement.remove()" title="Dismiss">&#x2715;</button>
  `;
}

/**
 * Update RUNNING badges on project cards in the Overview tab.
 * A project card gets a RUNNING badge when ANY sprint for that project is active.
 */
function _updateOverviewRunningBadges() {
  // Build a set of projects that have at least one running sprint
  const runningProjects = new Set(Object.values(_smgmtAllRunning).map(e => e.project));

  // Update each project row's name column
  document.querySelectorAll('.project-row[data-repo]').forEach(row => {
    const repo = row.getAttribute('data-repo');
    const nameCol = row.querySelector('.proj-col-name');
    if (!nameCol) return;

    // Remove existing running badge
    nameCol.querySelectorAll('.proj-running-badge').forEach(el => el.remove());

    if (runningProjects.has(repo)) {
      const badge = document.createElement('span');
      badge.className = 'proj-running-badge';
      badge.textContent = 'RUNNING';
      nameCol.appendChild(badge);
    }
  });
}

// ── Rerun sprint ──────────────────────────────────────────────────────────────

async function smgmtRerunSprint(label) {
  if (!_smgmtCurrentRepo) return;

  _smgmtRerunLabel = label;
  document.getElementById('smgmt-rerun-title').textContent = `Re-run ${sprintLabelDisplay(label)}?`;

  const bodyEl = document.getElementById('smgmt-rerun-body');
  bodyEl.innerHTML = '<p style="color:var(--text-muted);padding:8px 0;">Loading preview…</p>';

  const confirmBtn = document.getElementById('smgmt-rerun-confirm');
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Re-run sprint'; }
  document.getElementById('smgmt-rerun-backdrop').classList.remove('hidden');
  document.getElementById('smgmt-rerun-modal').classList.remove('hidden');

  const _rerunPreviewController = new AbortController();
  const _rerunPreviewTimeout = setTimeout(() => _rerunPreviewController.abort(), 8000);

  try {
    const res = await fetch(
      `/api/sprints/${encodeURIComponent(label)}/rerun/preview?project=${encodeURIComponent(_smgmtCurrentRepo)}`,
      { signal: _rerunPreviewController.signal }
    );
    clearTimeout(_rerunPreviewTimeout);
    if (!res.ok) throw new Error(await res.text());
    const preview = await res.json();

    const rows = [];
    if (preview.redispatch_count > 0) {
      const n = preview.redispatch_count;
      rows.push(`<div class="smgmt-rerun-count-row">
        <span class="smgmt-rerun-count-num">${n}</span>
        <span class="smgmt-rerun-count-label">ticket${n !== 1 ? 's' : ''} will re-dispatch (coder)</span>
        <span class="smgmt-rerun-count-reason">Re-dispatch from coder</span>
      </div>`);
    }
    if (preview.tester_count > 0) {
      const n = preview.tester_count;
      rows.push(`<div class="smgmt-rerun-count-row">
        <span class="smgmt-rerun-count-num">${n}</span>
        <span class="smgmt-rerun-count-label">ticket${n !== 1 ? 's' : ''} will start at tester</span>
        <span class="smgmt-rerun-count-reason">Start at tester — coder work already passed</span>
      </div>`);
    }
    if (preview.skip_count > 0) {
      const n = preview.skip_count;
      rows.push(`<div class="smgmt-rerun-count-row">
        <span class="smgmt-rerun-count-num">${n}</span>
        <span class="smgmt-rerun-count-label">ticket${n !== 1 ? 's' : ''} will be skipped (already in UAT)</span>
        <span class="smgmt-rerun-count-reason">Already passed tester; not re-running</span>
      </div>`);
    }

    const isNoop = preview.redispatch_count === 0 && preview.tester_count === 0 && preview.skip_count > 0;
    if (rows.length === 0) {
      bodyEl.innerHTML = '<em style="color:var(--text-muted)">No tickets in this sprint.</em>';
      if (confirmBtn) { confirmBtn.disabled = true; }
    } else {
      if (isNoop) {
        rows.unshift('<p style="color:var(--amber,#d97706);padding-bottom:6px">All tickets are in UAT — nothing to re-run. Confirming will return a no-op.</p>');
      }
      bodyEl.innerHTML = rows.join('');
      if (confirmBtn) { confirmBtn.disabled = false; }
    }
  } catch (e) {
    clearTimeout(_rerunPreviewTimeout);
    const msg = e.name === 'AbortError'
      ? 'Preview timed out — server took too long to respond.'
      : e.message;
    bodyEl.innerHTML = `<p style="color:var(--red,#dc2626)">Failed to load preview: ${escapeHtml(msg)}</p>`;
    if (confirmBtn) { confirmBtn.disabled = false; }
  }
}

function smgmtRerunClose() {
  document.getElementById('smgmt-rerun-backdrop').classList.add('hidden');
  document.getElementById('smgmt-rerun-modal').classList.add('hidden');
  _smgmtRerunLabel = null;
}

async function smgmtRerunConfirm() {
  if (!_smgmtRerunLabel || !_smgmtCurrentRepo) return;
  const confirmBtn = document.getElementById('smgmt-rerun-confirm');
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Starting…'; }

  try {
    const res = await fetch(
      `/api/sprints/${encodeURIComponent(_smgmtRerunLabel)}/rerun?project=${encodeURIComponent(_smgmtCurrentRepo)}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm: true }),
      }
    );
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    smgmtRerunClose();

    if (data.errors && data.errors.length > 0) {
      smgmtShowError(`Re-run started with errors: ${data.errors.join('; ')}`);
    } else {
      const parts = [];
      const coderCount = (data.decisions || []).filter(d => d.action === 'dispatch_coder').length;
      const testerCount = (data.decisions || []).filter(d => d.action === 'dispatch_tester').length;
      const skipCount = data.skip_count || 0;
      if (coderCount) parts.push(`${coderCount} coding`);
      if (testerCount) parts.push(`${testerCount} testing`);
      if (skipCount) parts.push(`${skipCount} skipped`);
      if (data.noop) {
        showSuccessToast(data.message || 'Nothing to re-run — all tickets are in UAT.');
      } else {
        const subLabel = data.sub_label ? ` → ${sprintLabelDisplay(data.sub_label)}` : '';
        showSuccessToast(`Re-run started${subLabel}: ${parts.join(', ') || '0 dispatched'}.`);
      }
    }

    await smgmtSelectProject(_smgmtCurrentRepo);
  } catch (e) {
    smgmtShowError('Failed to re-run sprint: ' + e.message);
    if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = 'Re-run sprint'; }
  }
}

// ── Finish sprint (issue #195) ────────────────────────────────────────────────

let _smgmtFinishLabel = null;

function smgmtFinishSprint(label) {
  if (!_smgmtCurrentRepo || !_smgmtData) return;
  _smgmtFinishLabel = label;
  const displayName = sprintLabelDisplay(label);

  // Count open issues in this sprint (from current data)
  const sprintTickets = (_smgmtData.issues || []).filter(
    t => (t.sprint_label || (t.sprint != null ? `sprint-${t.sprint}` : null)) === label
  );
  const openCount = sprintTickets.length;

  document.getElementById('smgmt-finish-title').textContent = `Finish ${displayName}?`;
  const bodyEl = document.getElementById('smgmt-finish-body');
  if (openCount === 0) {
    bodyEl.innerHTML = '<em style="color:var(--text-muted)">No open issues in this sprint. The operation will succeed with 0 closures.</em>';
  } else {
    bodyEl.innerHTML = `<p>${openCount} open issue${openCount !== 1 ? 's' : ''} will be moved to <strong>UAT</strong> and closed.</p>` +
      '<p style="margin-top:6px;color:var(--text-muted);font-size:12px;">Labels <code>in-progress</code>, <code>sit</code>, and <code>need-rework</code> will be removed. This action cannot be undone.</p>';
  }

  const confirmBtn = document.getElementById('smgmt-finish-confirm');
  if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = 'Finish Sprint'; }
  document.getElementById('smgmt-finish-backdrop').classList.remove('hidden');
  document.getElementById('smgmt-finish-modal').classList.remove('hidden');
}

function smgmtFinishClose() {
  document.getElementById('smgmt-finish-backdrop').classList.add('hidden');
  document.getElementById('smgmt-finish-modal').classList.add('hidden');
  _smgmtFinishLabel = null;
}

function _smgmtFinishShowLoading() {
  const overlay = document.getElementById('smgmt-finish-loading-overlay');
  if (overlay) overlay.classList.remove('hidden');
}

function _smgmtFinishHideLoading() {
  const overlay = document.getElementById('smgmt-finish-loading-overlay');
  if (overlay) overlay.classList.add('hidden');
}

async function smgmtFinishConfirm() {
  if (!_smgmtFinishLabel || !_smgmtCurrentRepo) return;

  const label = _smgmtFinishLabel;
  smgmtFinishClose();
  _smgmtFinishShowLoading();

  const parts = _smgmtCurrentRepo.split('/');
  const owner = parts[0];
  const repoName = parts.slice(1).join('/');

  try {
    const res = await fetch(
      `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/finish`,
      { method: 'POST' }
    );
    const data = await res.json();
    _smgmtFinishHideLoading();

    const mr = data.merge_result || {};
    if (data.errors && data.errors.length > 0) {
      smgmtShowError(`Closed ${data.closed} issue${data.closed !== 1 ? 's' : ''}; ${data.errors.length} error${data.errors.length !== 1 ? 's' : ''}: ${data.errors.join('; ')}`);
    } else {
      let msg = `Sprint finished — ${data.closed} issue${data.closed !== 1 ? 's' : ''} moved to UAT and closed.`;
      if (mr.merged) msg += ` PR #${mr.pr_number} merged into develop.`;
      showSuccessToast(msg);
    }

    await smgmtSelectProject(_smgmtCurrentRepo);
  } catch (e) {
    _smgmtFinishHideLoading();
    smgmtShowError('Failed to finish sprint: ' + e.message);
  }
}

// ── Kill sprint ───────────────────────────────────────────────────────────────

let _smgmtKillLabel = null;

function smgmtKillSprint(label) {
  _smgmtKillLabel = label;
  const confirmBtn = document.getElementById('smgmt-kill-confirm');
  if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = 'Yes, kill it'; }
  document.getElementById('smgmt-kill-backdrop').classList.remove('hidden');
  document.getElementById('smgmt-kill-modal').classList.remove('hidden');
}

function smgmtKillClose() {
  document.getElementById('smgmt-kill-backdrop').classList.add('hidden');
  document.getElementById('smgmt-kill-modal').classList.add('hidden');
  _smgmtKillLabel = null;
}

async function smgmtKillConfirm() {
  if (!_smgmtKillLabel || !_smgmtCurrentRepo) return;
  const confirmBtn = document.getElementById('smgmt-kill-confirm');
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Killing…'; }

  try {
    const res = await fetch(
      `/api/sprints/run/${encodeURIComponent(_smgmtKillLabel)}?project=${encodeURIComponent(_smgmtCurrentRepo)}`,
      { method: 'DELETE' }
    );
    if (!res.ok) throw new Error(await res.text());
    smgmtKillClose();
    // Per-#123: only remove this specific sprint from the running map; others continue.
    const killedKey = `${_smgmtCurrentRepo}:${_smgmtKillLabel}`;
    delete _smgmtAllRunning[killedKey];
    smgmtApplyRunState(null);
    _updateRunningBanner();
    _updateOverviewRunningBadges();
    showSuccessToast('Sprint killed. Run button restored.');
    await smgmtSelectProject(_smgmtCurrentRepo);
  } catch (e) {
    smgmtShowError('Failed to kill sprint: ' + e.message);
    if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = 'Yes, kill it'; }
  }
}

// ── Delete sprint ─────────────────────────────────────────────────────────────

let _smgmtDeleteLabel = null;

function smgmtDeleteSprint(label) {
  if (!_smgmtCurrentRepo || !_smgmtData) return;
  _smgmtDeleteLabel = label;
  const displayName = sprintLabelDisplay(label);
  const sprintTickets = (_smgmtData.issues || []).filter(
    t => (t.sprint_label || (t.sprint != null ? `sprint-${t.sprint}` : null)) === label
  );
  document.getElementById('smgmt-delete-title').textContent = `Delete ${displayName}?`;
  const bodyEl = document.getElementById('smgmt-delete-body');
  const ticketCount = sprintTickets.length;
  bodyEl.innerHTML = ticketCount > 0
    ? `<p>This will delete the <strong>${label}</strong> GitHub label and remove it from <strong>${ticketCount} ticket${ticketCount !== 1 ? 's' : ''}</strong>. The tickets themselves are not deleted.</p>`
    : `<p>This will delete the <strong>${label}</strong> GitHub label. No tickets are attached to this sprint.</p>`;
  const confirmBtn = document.getElementById('smgmt-delete-confirm');
  if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = 'Delete sprint'; }
  document.getElementById('smgmt-delete-backdrop').classList.remove('hidden');
  document.getElementById('smgmt-delete-modal').classList.remove('hidden');
}

function smgmtDeleteClose() {
  document.getElementById('smgmt-delete-backdrop').classList.add('hidden');
  document.getElementById('smgmt-delete-modal').classList.add('hidden');
  _smgmtDeleteLabel = null;
}

async function smgmtDeleteConfirm() {
  if (!_smgmtDeleteLabel || !_smgmtCurrentRepo) return;
  const confirmBtn = document.getElementById('smgmt-delete-confirm');
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Deleting…'; }

  try {
    const res = await fetch(
      `/api/sprints/${encodeURIComponent(_smgmtDeleteLabel)}?project=${encodeURIComponent(_smgmtCurrentRepo)}`,
      { method: 'DELETE' }
    );
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    const n = parseInt(_smgmtDeleteLabel.split('-')[1], 10);
    smgmtDeleteClose();
    const msg = data.unlabelled_count > 0
      ? `Sprint ${n} deleted — removed label from ${data.unlabelled_count} ticket${data.unlabelled_count !== 1 ? 's' : ''}.`
      : `Sprint ${n} deleted.`;
    showSuccessToast(msg);
    await smgmtSelectProject(_smgmtCurrentRepo);
  } catch (e) {
    smgmtShowError('Failed to delete sprint: ' + e.message);
    if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = 'Delete sprint'; }
  }
}

// ── Create sprint ─────────────────────────────────────────────────────────────

function smgmtCreateSprint() {
  if (!_smgmtCurrentRepo) return;
  const sprints = _smgmtData?.sprints || [];
  const proposed = sprints.length > 0 ? Math.max(...sprints) + 1 : 1;

  const input = document.getElementById('smgmt-new-sprint-input');
  const errEl = document.getElementById('smgmt-new-sprint-error');
  if (input) { input.value = proposed; }
  if (errEl) { errEl.textContent = ''; }

  const confirmBtn = document.getElementById('smgmt-new-sprint-confirm');
  if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = 'Create'; }

  document.getElementById('smgmt-new-sprint-backdrop')?.classList.remove('hidden');
  document.getElementById('smgmt-new-sprint-modal')?.classList.remove('hidden');
  setTimeout(() => input?.focus(), 50);
}

function smgmtNewSprintClose() {
  document.getElementById('smgmt-new-sprint-backdrop')?.classList.add('hidden');
  document.getElementById('smgmt-new-sprint-modal')?.classList.add('hidden');
  _smgmtNewSprintPreselectedIssue = null;
}

async function smgmtNewSprintConfirm() {
  const input = document.getElementById('smgmt-new-sprint-input');
  const errEl = document.getElementById('smgmt-new-sprint-error');
  const confirmBtn = document.getElementById('smgmt-new-sprint-confirm');

  const raw = input?.value.trim();
  if (!raw) {
    if (errEl) errEl.textContent = 'Please enter a sprint number';
    return;
  }
  const num = Number(raw);
  if (!Number.isInteger(num) || num < 1) {
    if (errEl) errEl.textContent = 'Sprint number must be a positive integer';
    return;
  }

  const sprints = _smgmtData?.sprints || [];
  if (sprints.includes(num)) {
    if (errEl) errEl.textContent = `Sprint ${num} already exists`;
    return;
  }

  if (errEl) errEl.textContent = '';
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Creating…'; }

  try {
    const res = await fetch('/api/sprints/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project: _smgmtCurrentRepo, sprint_number: num }),
    });
    if (!res.ok) {
      let msg;
      try { msg = (await res.json()).detail; } catch { msg = await res.text(); }
      if (errEl) errEl.textContent = msg || 'Failed to create sprint';
      if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = 'Create'; }
      return;
    }
    smgmtNewSprintClose();
    // If a backlog ticket was pre-selected for this new sprint, assign it now
    if (_smgmtNewSprintPreselectedIssue != null) {
      const preIssue = _smgmtNewSprintPreselectedIssue;
      _smgmtNewSprintPreselectedIssue = null;
      await smgmtBacklogMoveTicketTo(preIssue, `sprint-${num}`);
    } else {
      await smgmtSelectProject(_smgmtCurrentRepo);
    }
  } catch (e) {
    if (errEl) errEl.textContent = 'Failed to create sprint: ' + e.message;
    if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = 'Create'; }
    _smgmtNewSprintPreselectedIssue = null;
  }
}

// ── Clean up empty sprints ─────────────────────────────────────────────────────

function smgmtCleanupOpen() {
  if (!_smgmtData) return;
  _smgmtCleanupLabels = [...(_smgmtData.empty_sprint_labels || [])];
  if (_smgmtCleanupLabels.length === 0) return;

  const bodyEl = document.getElementById('smgmt-cleanup-body');
  if (bodyEl) {
    bodyEl.innerHTML = _smgmtCleanupLabels.map(label => {
      const n = label.split('-')[1];
      return `<div class="smgmt-cleanup-row">
        <span class="smgmt-cleanup-label">${escapeHtml(label)}</span>
        <span class="smgmt-cleanup-desc">0 tickets</span>
      </div>`;
    }).join('');
  }

  const confirmBtn = document.getElementById('smgmt-cleanup-confirm');
  if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = 'Delete labels'; }

  document.getElementById('smgmt-cleanup-backdrop')?.classList.remove('hidden');
  document.getElementById('smgmt-cleanup-modal')?.classList.remove('hidden');
}

function smgmtCleanupClose() {
  document.getElementById('smgmt-cleanup-backdrop')?.classList.add('hidden');
  document.getElementById('smgmt-cleanup-modal')?.classList.add('hidden');
  _smgmtCleanupLabels = [];
}

async function smgmtCleanupConfirm() {
  if (_smgmtCleanupLabels.length === 0 || !_smgmtCurrentRepo) return;
  const confirmBtn = document.getElementById('smgmt-cleanup-confirm');
  if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = 'Deleting…'; }

  try {
    const res = await fetch('/api/sprints/delete-empty', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ labels: _smgmtCleanupLabels, project: _smgmtCurrentRepo }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    smgmtCleanupClose();

    if (data.errors && data.errors.length > 0) {
      smgmtShowError(`Deleted ${data.deleted.length} label(s); errors: ${data.errors.join('; ')}`);
    } else {
      showSuccessToast(`Deleted ${data.deleted.length} empty sprint label${data.deleted.length !== 1 ? 's' : ''}.`);
    }

    await smgmtSelectProject(_smgmtCurrentRepo);
  } catch (e) {
    smgmtShowError('Failed to clean up empty sprints: ' + e.message);
    if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = 'Delete labels'; }
  }
}

// ── Error display ─────────────────────────────────────────────────────────────

function smgmtShowError(msg) {
  const el = document.getElementById('smgmt-error');
  if (!el) return;
  if (!msg) {
    el.innerHTML = '';
    el.classList.add('hidden');
    return;
  }
  // Render multi-line messages (e.g. log tails) in a <pre> block for readability.
  if (msg.includes('\n')) {
    const parts = msg.split('\n');
    const firstLine = escapeHtml(parts[0]);
    const rest = escapeHtml(parts.slice(1).join('\n').trimStart());
    el.innerHTML = `${firstLine}<pre class="smgmt-error-log">${rest}</pre>`;
  } else {
    el.textContent = msg;
  }
  el.classList.remove('hidden');
}

// ── SSE handler for sprint_plan_update ────────────────────────────────────────

function _handleSprintPlanSSE() {
  // Plan sprint view is no longer in main nav; no-op for now
}

// ── Remove Project Dialog ─────────────────────────────────────────────────────

let _rpRepo = null;

function openRemoveProjectDialog(repo, name) {
  _rpRepo = repo;
  document.getElementById('rp-project-name').textContent = name;
  document.getElementById('rp-delete-folders').checked = false;
  document.getElementById('rp-delete-github').checked = false;
  const errEl = document.getElementById('rp-error');
  errEl.textContent = '';
  errEl.classList.add('hidden');
  const btn = document.getElementById('rp-confirm-btn');
  btn.disabled = false;
  btn.textContent = 'Confirm Remove';
  document.getElementById('remove-project-backdrop').classList.remove('hidden');
  document.getElementById('remove-project-modal').classList.remove('hidden');
}

function closeRemoveProjectDialog() {
  _rpRepo = null;
  document.getElementById('remove-project-backdrop').classList.add('hidden');
  document.getElementById('remove-project-modal').classList.add('hidden');
}

async function confirmRemoveProject() {
  if (!_rpRepo) return;
  const repo          = _rpRepo;
  const deleteLocal   = document.getElementById('rp-delete-folders').checked;
  const deleteGithub  = document.getElementById('rp-delete-github').checked;

  const btn   = document.getElementById('rp-confirm-btn');
  const errEl = document.getElementById('rp-error');
  btn.disabled = true;
  btn.textContent = 'Removing…';
  errEl.textContent = '';
  errEl.classList.add('hidden');

  try {
    const parts = repo.split('/');
    const url = `/api/projects/${encodeURIComponent(parts[0])}/${encodeURIComponent(parts[1])}`;
    const res = await fetch(url, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ delete_local_folders: deleteLocal, delete_github_repo: deleteGithub }),
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      errEl.textContent = data.detail || `Error ${res.status}`;
      errEl.classList.remove('hidden');
      btn.disabled = false;
      btn.textContent = 'Confirm Remove';
      return;
    }

    closeRemoveProjectDialog();
    expandedProjects.delete(repo);
    delete detailsCache[repo];
    loadProjects();
  } catch {
    errEl.textContent = 'Network error. Please try again.';
    errEl.classList.remove('hidden');
    btn.disabled = false;
    btn.textContent = 'Confirm Remove';
  }
}

// ── Sprint cockpit (issue #32) ────────────────────────────────────────────────

let _scLastUpdateTime = null;  // timestamp of last sprint_update SSE event
let _scPaused = false;

// Tick "Last update Xs ago" every second
setInterval(() => {
  if (_scLastUpdateTime === null) return;
  const secs = Math.floor((Date.now() - _scLastUpdateTime) / 1000);
  const txt = `Last update ${secs}s ago`;
  ['sc-prog-update', 'sc-budget-update'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = txt;
  });
}, 1000);

async function scRefresh() {
  await loadSprintStatus();
  if (_sprintState) {
    scRenderCockpit(_sprintState);
    scRefreshAttention();
  } else {
    scRenderIdle();
  }
  scRefreshAgents();
  scRefreshFeed();
}

function scRenderIdle() {
  document.getElementById('sc-idle')?.classList.remove('hidden');
  document.getElementById('sc-cockpit')?.classList.add('hidden');
}

function scRenderCockpit(state) {
  if (!state) { scRenderIdle(); return; }
  document.getElementById('sc-idle')?.classList.add('hidden');
  document.getElementById('sc-cockpit')?.classList.remove('hidden');

  _scPaused = !!state.paused;
  const n = state.sprint_number ?? state.sprint_label ?? '?';

  // Header (AC-9)
  const titleEl = document.getElementById('sc-title');
  if (titleEl) titleEl.textContent = `Sprint ${n} · ${_scPaused ? 'paused' : 'running'}`;

  // Subtitle: "Started X ago · est Y remaining"
  const subEl = document.getElementById('sc-subtitle');
  if (subEl) {
    const startedAgo = state.start_timestamp ? _scTimeAgo(state.start_timestamp) : '?';
    const doneCount  = (state.issues || []).filter(i => i.status === 'done').length;
    const pendCount  = (state.issues || []).filter(i => i.status === 'pending').length;
    let estStr = 'est. unknown';
    if (doneCount > 0 && state.wall_clock_secs > 0) {
      const perIssue = state.wall_clock_secs / doneCount;
      const estRemSecs = Math.round(pendCount * perIssue);
      estStr = `est ${_fmtSecs(estRemSecs)} remaining`;
    }
    subEl.textContent = `Started ${startedAgo} · ${estStr}`;
  }

  // Pause button label
  const pauseBtn = document.getElementById('sc-pause-btn');
  if (pauseBtn) pauseBtn.textContent = _scPaused ? 'Resume' : 'Pause';

  // Pills
  const total = (state.issues || []).length;
  const issuesPill = document.getElementById('sc-pill-issues');
  if (issuesPill) issuesPill.textContent = `${total} issue${total !== 1 ? 's' : ''}`;

  // Progress bar (AC-10)
  _scRenderProgressBar(state);

  // Budget bar (AC-11)
  _scRenderBudgetBar(state);
}

function _scRenderProgressBar(state) {
  const issues = state.issues || [];
  const total  = issues.length || 1;
  const done    = issues.filter(i => i.status === 'done').length;
  const skipped = issues.filter(i => i.status === 'skipped').length;
  const pending = issues.filter(i => i.status === 'pending').length;
  // Infer in-progress: the first pending issue when ≥ 1 done
  const inprog = (done >= 1 && pending > 0) ? 1 : 0;
  const effectivePending = Math.max(0, pending - inprog);

  const pDone    = done          / total * 100;
  const pInprog  = inprog        / total * 100;
  const pSkipped = skipped       / total * 100;
  const pPending = effectivePending / total * 100;

  const setW = (id, pct) => {
    const el = document.getElementById(id);
    if (el) el.style.width = pct.toFixed(1) + '%';
  };
  setW('sc-seg-done',       pDone);
  setW('sc-seg-inprogress', pInprog);
  setW('sc-seg-skipped',    pSkipped);
  setW('sc-seg-pending',    pPending);

  const setLabel = (id, count, label) => {
    const el = document.getElementById(id);
    if (el) el.textContent = `${count} ${label}`;
  };
  setLabel('sc-lbl-pending',    effectivePending + pending - effectivePending, 'pending');
  setLabel('sc-lbl-inprogress', inprog,   'in-progress');
  setLabel('sc-lbl-done',       done,     'done');
  setLabel('sc-lbl-skipped',    skipped,  'skipped');
  // Correct pending label
  const pendLblEl = document.getElementById('sc-lbl-pending');
  if (pendLblEl) pendLblEl.textContent = `${pending} pending`;
}

function _scRenderBudgetBar(state) {
  const used    = (state.total_tokens_in || 0) + (state.total_tokens_out || 0);
  const budget  = state.token_budget || 0;
  const fill    = document.getElementById('sc-budget-fill');
  const label   = document.getElementById('sc-budget-label');

  if (!budget) {
    if (fill)  { fill.style.width = '0%'; fill.classList.remove('budget-red'); }
    if (label) label.textContent = 'No budget set';
    return;
  }

  const pct = Math.min(used / budget * 100, 100);
  if (fill) {
    fill.style.width = pct.toFixed(1) + '%';
    fill.classList.toggle('budget-red', pct >= 80);
  }
  if (label) {
    const usedK  = _fmtTokens(used);
    const budgK  = _fmtTokens(budget);
    label.textContent = `${usedK} / ${budgK} tokens`;
  }
}

function _fmtTokens(n) {
  if (n >= 1000) return `${Math.round(n / 1000)}k`;
  return String(n);
}

function _fmtSecs(s) {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function _scTimeAgo(isoStr) {
  const s = Math.floor((Date.now() - new Date(isoStr.endsWith('Z') ? isoStr : isoStr + 'Z')) / 1000);
  if (s < 60)    return `${s}s ago`;
  if (s < 3600)  return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

// Agents grid (AC-12)
async function scRefreshAgents() {
  try {
    const res  = await fetch('/api/agents');
    if (!res.ok) return;
    const data = await res.json();
    const agents = data.filter(a => a.status === 'working');

    const pill = document.getElementById('sc-pill-agents');
    if (pill) pill.textContent = `${agents.length} agent${agents.length !== 1 ? 's' : ''}`;

    const grid = document.getElementById('sc-agents-grid');
    if (!grid) return;
    if (!agents.length) {
      grid.innerHTML = '<div style="color:var(--text-muted);font-size:12px">No active agents.</div>';
      return;
    }
    grid.innerHTML = agents.map(a => {
      const statusClass = { working: 'ac-working', done: 'ac-done', timed_out: 'ac-error' }[a.status] || 'ac-waiting';
      const parsed  = _parseAgentName(a.name || '');
      const role    = parsed.role || 'agent';
      const issueM  = (a.working_dir || '').match(/#?(\d+)/) || (a.name || '').match(/#?(\d+)/);
      const issueRef = issueM ? `#${issueM[1]}` : '';
      const elapsed = a.last_seen ? _scTimeAgo(a.last_seen) : '';
      return `<div class="sc-agent-card ${statusClass}">
        <div class="sc-agent-role">${escapeHtml(role)}</div>
        ${issueRef ? `<div class="sc-agent-issue">${escapeHtml(issueRef)}</div>` : ''}
        <div class="sc-agent-elapsed">${escapeHtml(elapsed)}</div>
        ${a.tool_name ? `<div class="sc-agent-tool">${escapeHtml(a.tool_name)}</div>` : ''}
      </div>`;
    }).join('');
  } catch { /* silent */ }
}

// Activity feed (AC-14)
async function scRefreshFeed() {
  try {
    const res = await fetch('/api/events');
    if (!res.ok) return;
    const events = await res.json();

    // Filter events matching sprint issue numbers
    const sprintIssueNums = (_sprintState?.issues || []).map(i => String(i.number));
    const relevant = events.filter(ev => {
      const dir  = ev.working_dir || ev.session_id || '';
      return sprintIssueNums.some(n => dir.includes(n));
    }).slice(0, 5);

    const container = document.getElementById('sc-feed-rows');
    if (!container) return;
    if (!relevant.length) {
      container.innerHTML = '<div style="color:var(--text-muted);font-size:12px">No recent events.</div>';
      return;
    }
    container.innerHTML = relevant.map(ev => {
      const evType = ev.event_type || '';
      let icon = '⬜';
      if (['done', 'merged'].includes(evType))               icon = '✅';
      else if (['working', 'tool_use'].includes(evType))     icon = '🔵';
      else if (['skipped', 'gate_fail'].includes(evType))    icon = '⚠️';
      else if (['crash', 'error'].includes(evType))          icon = '❌';

      const issueM   = (ev.working_dir || '').match(/#?(\d+)/);
      const issueRef = issueM ? issueM[1] : '';
      const ghUrl    = issueRef ? `https://github.com/${escapeHtml(ev.working_dir?.split('/').slice(-2).join('/') || '')}/issues/${issueRef}` : '';
      const timeStr  = ev.created_at ? _scTimeAgo(ev.created_at) : '';
      return `<div class="sc-feed-row">
        <span class="sc-feed-time">${escapeHtml(timeStr)}</span>
        <span class="sc-feed-icon">${icon}</span>
        <span class="sc-feed-desc">${escapeHtml(evType)}</span>
        ${issueRef
          ? `<a class="sc-feed-ref" href="${ghUrl}" target="_blank" rel="noreferrer">#${escapeHtml(issueRef)}</a>`
          : '<span></span>'
        }
      </div>`;
    }).join('');
  } catch { /* silent */ }
}

// Attention section (AC-13) — fetch labels from GitHub since IssueState doesn't carry them
async function scRefreshAttention() {
  if (!_sprintState) return;
  const sprintNum = _sprintState.sprint_number;
  if (!sprintNum) return;
  try {
    const res = await fetch(`/api/sprint-planning/issues`);
    if (!res.ok) return;
    const data = await res.json();
    const sprintIssues = (data.issues || []).filter(i => i.sprint === sprintNum);

    const uatItems     = sprintIssues.filter(i => {
      const lbls = (i.labels || []).map(l => l.name || l);
      return lbls.includes('UAT') && !lbls.includes('UAT-approved');
    });
    const blockedItems = sprintIssues.filter(i => {
      const lbls = (i.labels || []).map(l => l.name || l);
      return lbls.includes('blocked');
    });

    const section   = document.getElementById('sc-attention-section');
    const container = document.getElementById('sc-attention-items');
    if (!section || !container) return;

    const all = [...uatItems, ...blockedItems];
    section.classList.toggle('hidden', all.length === 0);
    if (!all.length) return;

    container.innerHTML = all.map(i => {
      const isUat = uatItems.includes(i);
      const title  = escapeHtml(i.title || `Issue #${i.number}`);
      const ghUrl  = i.url || '';
      const action = isUat
        ? `<a class="sc-btn-review" href="${escapeHtml(ghUrl)}" target="_blank" rel="noreferrer">Review</a>`
        : `<a class="sc-attention-link" href="${escapeHtml(ghUrl)}" target="_blank" rel="noreferrer">View on GitHub</a>`;
      return `<div class="sc-attention-card">
        <div class="sc-attention-meta"><span class="sc-attention-num">#${i.number}</span>${title}</div>
        ${action}
      </div>`;
    }).join('');
  } catch { /* silent */ }
}

// Pause / Resume (AC-9)
async function scTogglePause() {
  const endpoint = _scPaused ? '/api/sprint-resume' : '/api/sprint-pause';
  try {
    const res = await fetch(endpoint, { method: 'POST' });
    if (!res.ok) throw new Error(await res.text());
    // State update comes via SSE
  } catch (e) {
    showErrorToast('Sprint control failed: ' + e.message);
  }
}

// Stop sprint confirmation (AC-9)
function scStopConfirm() {
  document.getElementById('sc-stop-dialog-backdrop')?.classList.remove('hidden');
  document.getElementById('sc-stop-dialog')?.classList.remove('hidden');
  const btn = document.getElementById('sc-stop-confirm-btn');
  if (btn) { btn.disabled = false; btn.textContent = 'Stop sprint'; }
}

function scStopCancel() {
  document.getElementById('sc-stop-dialog-backdrop')?.classList.add('hidden');
  document.getElementById('sc-stop-dialog')?.classList.add('hidden');
}

async function scStopExecute() {
  const btn = document.getElementById('sc-stop-confirm-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Stopping…'; }
  try {
    const res = await fetch('/api/sprint-stop', { method: 'POST' });
    if (!res.ok) throw new Error(await res.text());
    scStopCancel();
    // sprint_stopped SSE will trigger scRenderIdle()
  } catch (e) {
    if (btn) { btn.disabled = false; btn.textContent = 'Stop sprint'; }
    showErrorToast('Failed to stop sprint: ' + e.message);
  }
}

function showErrorToast(msg) {
  const el = document.getElementById('toast-error');
  if (!el) return;
  el.textContent = msg;
  el.style.display = 'block';
  setTimeout(() => { el.style.display = 'none'; }, 5000);
}

// ── Init ──────────────────────────────────────────────────────────────────────
(function init() {
  initTheme();
  fetchEnvironment();
  fetchBuildStamp().catch(() => {});

  // Load projects first, then route (so project view can show project data)
  loadProjects()
    .then(() => {
      // After projects are loaded, run router to handle deep-link URLs
      _route();
      // Update project picker if we're already in project view
      if (_activeProject) _updateProjectHeader(_activeProject);
    })
    .catch(e => {
      document.getElementById('project-list').innerHTML =
        `<div class="empty-projects">Failed to load projects: ${escapeHtml(e.message)}</div>`;
      _route(); // still route even on failure
    });

  // On first load with a non-project URL, show overview
  if (!window.location.pathname.startsWith('/projects/')) {
    _showOverview();
  }

  loadPlanUsage().catch(() => {});
  loadAlerts().catch(() => {});
  loadSprintStatus().catch(() => {});

  // Per-#123: Global poll for all running sprints every 4 seconds.
  // This drives overview RUNNING badges and the multi-sprint banner
  // independently of which tab is currently active.
  smgmtPollRunStatus().catch(() => {});
  setInterval(() => smgmtPollRunStatus().catch(() => {}), 4000);

  // AC-1: initialise live log panel
  _llpInitScrollListener();
  _llpStartRelativeClock();
  _llpSetStatus('disconnected');

  connectSSE();
  document.getElementById('btn-refresh')?.addEventListener('click', () => window.location.reload());
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && _rpRepo !== null) closeRemoveProjectDialog();
    if (e.key === 'Escape') closeDraftTicketModal();
  });

  // Migration modal keyboard shortcuts
  document.addEventListener('keydown', e => {
    const modal = document.getElementById('smgmt-migrate-modal');
    if (!modal || modal.classList.contains('hidden')) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      smgmtMigrateClose();
    } else if (e.key === 'Enter') {
      const confirmBtn = document.getElementById('smgmt-migrate-confirm');
      if (confirmBtn && !confirmBtn.disabled) {
        e.preventDefault();
        smgmtMigrateConfirm();
      }
    }
  });

  // Estimate summary modal keyboard shortcuts (issue #211)
  document.addEventListener('keydown', e => {
    const modal = document.getElementById('smgmt-est-modal');
    if (!modal || modal.classList.contains('hidden')) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      smgmtEstClose();
    } else if (e.key === 'Enter') {
      const confirmBtn = document.getElementById('smgmt-est-confirm');
      if (confirmBtn && !confirmBtn.disabled) {
        e.preventDefault();
        smgmtEstConfirm();
      }
    }
  });
})();

// ── Draft Ticket Modal (issue #94) ────────────────────────────────────────────

let _dtFiles = [];
let _dtDraftId = null;

// Label picker cache: { ts: number, labels: [{name, color}] } | null
let _dtLabelCache = null;
const _DT_LABEL_CACHE_TTL = 30_000; // 30 seconds

async function _dtFetchLabels(project) {
  const now = Date.now();
  if (_dtLabelCache && (now - _dtLabelCache.ts) < _DT_LABEL_CACHE_TTL) {
    return _dtLabelCache.labels;
  }
  const qs = project ? `?repo=${encodeURIComponent(project)}` : '';
  const res = await fetch(`/api/github/labels${qs}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const labels = await res.json();
  _dtLabelCache = { ts: now, labels };
  return labels;
}

function _dtRenderLabelList(labels) {
  const list = document.getElementById('dt-label-list');
  if (!labels.length) {
    list.innerHTML = '<span class="dt-label-loading">No labels found</span>';
    return;
  }
  list.innerHTML = labels.map(lbl => {
    const safeId = 'dt-lbl-' + lbl.name.replace(/[^a-zA-Z0-9_-]/g, '_');
    const color = lbl.color ? '#' + lbl.color.replace(/^#/, '') : '#cccccc';
    return `<label class="dt-label-item">
      <input type="checkbox" id="${escapeHtml(safeId)}" value="${escapeHtml(lbl.name)}">
      <span class="dt-label-swatch" style="background:${color}"></span>
      <span class="dt-label-name">${escapeHtml(lbl.name)}</span>
    </label>`;
  }).join('');
}

async function _dtLoadLabels(project) {
  const loading = document.getElementById('dt-label-loading');
  const error   = document.getElementById('dt-label-error');
  const list    = document.getElementById('dt-label-list');
  loading.classList.remove('hidden');
  error.classList.add('hidden');
  list.innerHTML = '';
  try {
    const labels = await _dtFetchLabels(project);
    loading.classList.add('hidden');
    _dtRenderLabelList(labels);
  } catch (e) {
    loading.classList.add('hidden');
    error.classList.remove('hidden');
  }
}

function openDraftTicketModal() {
  _dtFiles = [];
  _dtDraftId = null;
  document.getElementById('dt-backdrop').classList.remove('hidden');
  document.getElementById('dt-modal').classList.remove('hidden');
  document.getElementById('dt-description').value = '';
  _dtResetSprintField();
  _dtLoadSprintOptions();
  document.getElementById('dt-error').classList.add('hidden');
  document.getElementById('dt-draft-section').classList.add('hidden');
  document.getElementById('dt-previews').innerHTML = '';
  const generateBtn = document.getElementById('dt-generate-btn');
  generateBtn.disabled = false;
  generateBtn.textContent = 'Generate Draft';

  // Populate project dropdown
  const sel = document.getElementById('dt-project');
  sel.innerHTML = allProjects.map(p =>
    `<option value="${escapeHtml(p.repo)}">${escapeHtml(p.name || p.repo)}</option>`
  ).join('');
  if (_activeProject) {
    sel.value = _activeProject;
  }

  // Load label picker
  _dtLoadLabels(sel.value || '');

  document.getElementById('dt-description').focus();
}

function closeDraftTicketModal() {
  document.getElementById('dt-backdrop').classList.add('hidden');
  document.getElementById('dt-modal').classList.add('hidden');
}

function _dtResetSprintField() {
  const sel = document.getElementById('dt-sprint');
  const newWrap = document.getElementById('dt-sprint-new-wrap');
  sel.classList.remove('hidden');
  newWrap.classList.add('hidden');
  document.getElementById('dt-sprint-new-input').value = '';
  sel.innerHTML = '<option value="">— no sprint —</option><option value="__new__">+ Add new sprint…</option>';
  sel.value = '';
}

async function _dtLoadSprintOptions() {
  try {
    const res = await fetch('/api/sprints');
    const data = res.ok ? await res.json() : {};
    const sprints = data.sprints || [];
    const sorted = [...sprints].sort((a, b) => b - a);
    const sel = document.getElementById('dt-sprint');
    if (!sel || !document.getElementById('dt-modal') || document.getElementById('dt-modal').classList.contains('hidden')) return;
    sel.innerHTML =
      '<option value="">— no sprint —</option>' +
      sorted.map(n => `<option value="sprint-${n}">sprint-${n}</option>`).join('') +
      '<option value="__new__">+ Add new sprint…</option>';
    sel.value = '';
  } catch (_) {
    // Graceful degradation: _dtResetSprintField already placed blank + sentinel
  }
}

function _dtSprintOnChange(selectEl) {
  if (selectEl.value === '__new__') _dtSprintAddNew();
}

function _dtSprintAddNew() {
  document.getElementById('dt-sprint').classList.add('hidden');
  document.getElementById('dt-sprint-new-wrap').classList.remove('hidden');
  const inp = document.getElementById('dt-sprint-new-input');
  inp.value = '';
  inp.focus();
}

function _dtSprintCancel(e) {
  e.preventDefault();
  document.getElementById('dt-sprint-new-wrap').classList.add('hidden');
  const sel = document.getElementById('dt-sprint');
  sel.classList.remove('hidden');
  sel.value = '';
  document.getElementById('dt-sprint-new-input').value = '';
}

function _dtGetSprintValue() {
  const newWrap = document.getElementById('dt-sprint-new-wrap');
  if (newWrap && !newWrap.classList.contains('hidden')) {
    return document.getElementById('dt-sprint-new-input').value.trim();
  }
  const val = document.getElementById('dt-sprint').value;
  return val === '__new__' ? '' : val;
}

function _dtPickFiles() {
  document.getElementById('dt-file-input').click();
}

function _dtOnFileInput(event) {
  _dtAddFiles(Array.from(event.target.files));
  event.target.value = '';
}

function _dtOnDrop(event) {
  event.preventDefault();
  document.getElementById('dt-dropzone').classList.remove('drag-over');
  _dtAddFiles(Array.from(event.dataTransfer.files));
}

function _dtAddFiles(incoming) {
  const allowed = new Set([
    '.html','.htm','.md','.txt','.csv','.json','.yaml','.yml',
    '.png','.jpg','.jpeg','.gif','.svg','.webp','.pdf',
    '.py','.js','.ts','.tsx','.css','.sh','.log',
    '.drawio','.xlsx','.pptx','.docx','.zip',
  ]);
  const fileEl = document.getElementById('dt-file-error');
  if (fileEl) { fileEl.textContent = ''; fileEl.classList.add('hidden'); }
  for (const f of incoming) {
    if (_dtFiles.length >= 10) break;
    const ext = '.' + f.name.split('.').pop().toLowerCase();
    if (!allowed.has(ext)) {
      if (fileEl) {
        fileEl.textContent = `File '${f.name}' has a disallowed extension ('${ext}').`;
        fileEl.classList.remove('hidden');
      }
      continue;
    }
    if (f.size > 25 * 1024 * 1024) {
      if (fileEl) {
        fileEl.textContent = `File '${f.name}' exceeds the 25 MB per-file limit.`;
        fileEl.classList.remove('hidden');
      }
      continue;
    }
    _dtFiles.push(f);
  }
  _dtRenderPreviews();
}

function _dtRemoveFile(idx) {
  _dtFiles.splice(idx, 1);
  _dtRenderPreviews();
}

function _dtRenderPreviews() {
  const container = document.getElementById('dt-previews');
  container.innerHTML = _dtFiles.map((f, i) => {
    const isImage = f.type.startsWith('image/');
    const nameEl = `<div class="dt-preview-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</div>`;
    const removeEl = `<button class="dt-preview-remove" onclick="_dtRemoveFile(${i})" title="Remove">×</button>`;
    if (isImage) {
      const url = URL.createObjectURL(f);
      return `<div class="dt-preview-item">${removeEl}<img src="${url}" class="dt-preview-img" alt="${escapeHtml(f.name)}">${nameEl}</div>`;
    }
    return `<div class="dt-preview-item">${removeEl}<div class="dt-preview-icon">📄</div>${nameEl}</div>`;
  }).join('');
}

async function generateDraft(event) {
  event.preventDefault();

  const desc = document.getElementById('dt-description').value.trim();
  if (!desc) {
    _dtShowError('Description is required.');
    return;
  }

  const generateBtn = document.getElementById('dt-generate-btn');
  generateBtn.disabled = true;
  generateBtn.innerHTML = '<span class="dt-spinner"></span>Generating…';
  document.getElementById('dt-error').classList.add('hidden');
  document.getElementById('dt-draft-section').classList.add('hidden');

  const formData = new FormData();
  formData.append('description', desc);
  formData.append('project', document.getElementById('dt-project').value || '');
  formData.append('sprint_label', _dtGetSprintValue());
  // Attachments are no longer sent at draft-generation time; they are uploaded at post time.

  try {
    const res = await fetch('/api/tickets/draft', { method: 'POST', body: formData });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `Server error ${res.status}`);
    }
    const data = await res.json();
    _dtDraftId = data.draft_id;
    _dtFiles = [];
    _dtRenderPreviews();
    document.getElementById('dt-title').value = data.title || '';
    document.getElementById('dt-body').value = data.body || '';
    document.getElementById('dt-draft-section').classList.remove('hidden');
    document.getElementById('dt-modal').scrollTop = document.getElementById('dt-modal').scrollHeight;
  } catch (e) {
    _dtShowError('Generation failed: ' + e.message);
  } finally {
    generateBtn.disabled = false;
    generateBtn.textContent = 'Generate Draft';
  }
}

async function postDraftToGitHub() {
  const title = document.getElementById('dt-title').value.trim();
  if (!title) {
    _dtShowError('Title is required before posting.');
    return;
  }

  // Collect selected labels from picker
  const checkedBoxes = document.querySelectorAll('#dt-label-list input[type="checkbox"]:checked');
  const extraLabels = Array.from(checkedBoxes).map(cb => cb.value);

  const postBtn = document.getElementById('dt-post-btn');
  postBtn.disabled = true;
  // Show loading indicator while upload+commit happens (typically 2-5 s with attachments)
  const hasFiles = _dtFiles.length > 0;
  postBtn.innerHTML = hasFiles
    ? '<span class="dt-spinner"></span>Uploading &amp; committing…'
    : '<span class="dt-spinner"></span>Posting…';
  document.getElementById('dt-error').classList.add('hidden');
  const fileErrEl = document.getElementById('dt-file-error');
  if (fileErrEl) { fileErrEl.textContent = ''; fileErrEl.classList.add('hidden'); }

  try {
    // Use FormData so we can upload attachment files along with the ticket fields.
    const formData = new FormData();
    formData.append('draft_id', _dtDraftId || '');
    formData.append('title', title);
    formData.append('body', document.getElementById('dt-body').value);
    formData.append('project', document.getElementById('dt-project').value || '');
    formData.append('sprint_label', _dtGetSprintValue());
    for (const lbl of extraLabels) {
      formData.append('extra_labels', lbl);
    }
    for (const f of _dtFiles) {
      formData.append('files', f);
    }

    const res = await fetch('/api/tickets/create', {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      const msg = data.detail || `Server error ${res.status}`;
      if (res.status === 422 && fileErrEl) {
        // Inline error under the file picker for rejected files
        fileErrEl.textContent = msg;
        fileErrEl.classList.remove('hidden');
      } else {
        throw new Error(msg);
      }
      return;
    }
    const data = await res.json();
    // Check for push_warning field (set if attachments committed but push failed gracefully)
    if (data.push_warning) {
      const modal = document.getElementById('dt-push-fail-modal');
      if (modal) modal.classList.remove('hidden');
    }
    closeDraftTicketModal();
    showSuccessToast(`Ticket created: ${data.url}`);
    loadProjects().catch(() => {});
  } catch (e) {
    _dtShowError('Post failed: ' + e.message);
  } finally {
    postBtn.disabled = false;
    postBtn.textContent = 'Post to GitHub';
  }
}

function _dtShowError(msg) {
  const el = document.getElementById('dt-error');
  el.textContent = msg;
  el.classList.remove('hidden');
}

// ── Bulk Create Modal (issue #189 / #205 / #208) ──────────────────────────────

let _bcJobId = null;
let _bcEventSource = null;
let _bcJobState = null;      // current job snapshot {status, concurrency, tickets}
let _bcDebounceTimer = null;
let _bcStartTimes = {};      // index -> start timestamp (for elapsed calc)
let _bcAvgMs = null;         // rolling avg BA time per ticket
let _bcFiles = [];           // queued attachment files (issue #205)
let _bcLastInput = null;     // preserved Step 1 inputs for Recreate (issue #208)

const BC_MAX_PROMPTS = 25;
const BC_ALLOWED_EXTS = new Set([
  '.html','.htm','.md','.txt','.csv','.json','.yaml','.yml',
  '.png','.jpg','.jpeg','.gif','.svg','.webp','.pdf',
  '.py','.js','.ts','.tsx','.css','.sh','.log',
  '.drawio','.xlsx','.pptx','.docx','.zip',
]);

// ── Bulk attachment helpers ───────────────────────────────────────────────────

function _bcPickFiles() {
  document.getElementById('bc-file-input').click();
}

function _bcOnFileInput(event) {
  _bcAddFiles(Array.from(event.target.files));
  event.target.value = '';
}

function _bcOnDrop(event) {
  event.preventDefault();
  document.getElementById('bc-dropzone').classList.remove('drag-over');
  _bcAddFiles(Array.from(event.dataTransfer.files));
}

function _bcAddFiles(incoming) {
  const errEl = document.getElementById('bc-file-error');
  if (errEl) { errEl.textContent = ''; errEl.classList.add('hidden'); }
  let totalSize = _bcFiles.reduce((s, f) => s + f.size, 0);

  for (const f of incoming) {
    if (_bcFiles.length >= 10) break;
    const ext = '.' + f.name.split('.').pop().toLowerCase();
    if (!BC_ALLOWED_EXTS.has(ext)) {
      if (errEl) {
        errEl.textContent = `File '${f.name}' has a disallowed extension ('${ext}').`;
        errEl.classList.remove('hidden');
      }
      continue;
    }
    if (f.size > 25 * 1024 * 1024) {
      if (errEl) {
        errEl.textContent = `File '${f.name}' exceeds the 25 MB per-file limit.`;
        errEl.classList.remove('hidden');
      }
      continue;
    }
    totalSize += f.size;
    if (totalSize > 50 * 1024 * 1024) {
      if (errEl) {
        errEl.textContent = 'Upload batch would exceed the 50 MB total limit.';
        errEl.classList.remove('hidden');
      }
      break;
    }
    _bcFiles.push(f);
  }
  _bcRenderFileQueue();
}

function _bcRemoveFile(idx) {
  _bcFiles.splice(idx, 1);
  _bcRenderFileQueue();
}

function _bcRenderFileQueue() {
  const container = document.getElementById('bc-file-queue');
  if (!container) return;
  if (_bcFiles.length === 0) {
    container.innerHTML = '';
    return;
  }
  container.innerHTML = _bcFiles.map((f, i) =>
    `<span class="bc-file-chip">
      <span class="bc-file-chip-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</span>
      <button class="bc-file-chip-remove" onclick="_bcRemoveFile(${i})" title="Remove">&times;</button>
    </span>`
  ).join('');
}

function openBulkCreateModal() {
  // If there's an active job stored in localStorage, go straight to step 2
  const savedJobId = localStorage.getItem('bc_job_id');
  if (savedJobId) {
    _bcJobId = savedJobId;
    _bcShowStep2Shell();
    _bcConnectSSE(savedJobId);
    document.getElementById('bc-backdrop').classList.remove('hidden');
    document.getElementById('bc-modal').classList.remove('hidden');
    return;
  }

  _bcJobId = null;
  _bcJobState = null;
  document.getElementById('bc-backdrop').classList.remove('hidden');
  document.getElementById('bc-modal').classList.remove('hidden');
  _bcShowStep1();

  // Populate repo dropdown
  const sel = document.getElementById('bc-repo');
  sel.innerHTML = allProjects.map(p =>
    `<option value="${escapeHtml(p.repo)}">${escapeHtml(p.name || p.repo)}</option>`
  ).join('');
  if (_activeProject) sel.value = _activeProject;

  // Reset fields
  document.getElementById('bc-textarea').value = '';
  document.getElementById('bc-default-labels').value = 'enhancement';
  document.getElementById('bc-concurrency').value = '3';
  document.getElementById('bc-step1-error').classList.add('hidden');
  // Reset attachments
  _bcFiles = [];
  _bcRenderFileQueue();
  const fileErrEl = document.getElementById('bc-file-error');
  if (fileErrEl) { fileErrEl.textContent = ''; fileErrEl.classList.add('hidden'); }
  _bcUpdateCounter();

  // Show recovery banner if there are saved inputs from a previous failed run (issue #335)
  const recoverBanner = document.getElementById('bc-recovery-banner');
  if (recoverBanner) {
    try {
      recoverBanner.classList.toggle('hidden', !localStorage.getItem('bc_last_input'));
    } catch (_) {
      recoverBanner.classList.add('hidden');
    }
  }
}

function closeBulkCreateModal() {
  // If a job is in progress, show a toast but keep the job running
  if (_bcJobId && _bcJobState && _bcJobState.status === 'running') {
    _bcShowToast('Bulk job continues running. Reopen via the Bulk create button.');
  } else {
    // Job is done or never started — clear localStorage
    localStorage.removeItem('bc_job_id');
    _bcJobId = null;
  }
  // Disconnect SSE (reconnect later if needed)
  if (_bcEventSource) {
    _bcEventSource.close();
    _bcEventSource = null;
  }
  document.getElementById('bc-backdrop').classList.add('hidden');
  document.getElementById('bc-modal').classList.add('hidden');
}

function _bcBackdropClick() {
  // Backdrop click intentionally does nothing — use Cancel or ✕ to close (issue #208)
}

// Keyboard: Esc closes; Cmd/Ctrl+Enter triggers Run
document.addEventListener('keydown', function(e) {
  const modal = document.getElementById('bc-modal');
  if (!modal || modal.classList.contains('hidden')) return;

  if (e.key === 'Escape') {
    closeBulkCreateModal();
    return;
  }

  const step1 = document.getElementById('bc-step1');
  if (step1 && !step1.classList.contains('hidden')) {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      const btn = document.getElementById('bc-run-btn');
      if (btn && !btn.disabled) bcRunAll();
    }
  }
});

function _bcShowStep1() {
  document.getElementById('bc-step1').classList.remove('hidden');
  document.getElementById('bc-step2').classList.add('hidden');
  document.getElementById('bc-step-badge').textContent = 'Step 1 of 2 · Input';
}

function _bcShowStep2Shell() {
  document.getElementById('bc-step1').classList.add('hidden');
  document.getElementById('bc-step2').classList.remove('hidden');
  document.getElementById('bc-step-badge').textContent = 'Step 2 of 2 · Review & create';
}

function bcRepoChanged() {
  // Nothing extra needed — repo is read at run time
}

let _bcCounterTimer = null;
function bcOnTextareaInput() {
  clearTimeout(_bcCounterTimer);
  _bcCounterTimer = setTimeout(_bcUpdateCounter, 200);
}

function _bcParsePrompts(text) {
  return text.split(/^---$/m).map(s => s.trim()).filter(s => s.length > 0);
}

function _bcUpdateCounter() {
  const text = (document.getElementById('bc-textarea') || {}).value || '';
  const prompts = _bcParsePrompts(text);
  const n = prompts.length;
  const chars = text.length;
  const counterEl = document.getElementById('bc-counter');
  if (counterEl) counterEl.textContent = `${n} tickets detected · ${chars} chars`;

  const runBtn = document.getElementById('bc-run-btn');
  if (runBtn) {
    runBtn.textContent = `Run BA on all ${n}`;
    const repo = (document.getElementById('bc-repo') || {}).value || '';
    runBtn.disabled = (n === 0 || !repo || n > BC_MAX_PROMPTS);
  }

  // Inline validation for >25
  const errEl = document.getElementById('bc-step1-error');
  if (errEl) {
    if (n > BC_MAX_PROMPTS) {
      errEl.textContent = `Batch limit is ${BC_MAX_PROMPTS} prompts (you have ${n})`;
      errEl.classList.remove('hidden');
    } else {
      errEl.classList.add('hidden');
    }
  }
}

async function bcRunAll() {
  const text = document.getElementById('bc-textarea').value;
  const prompts = _bcParsePrompts(text);
  if (prompts.length === 0) return;
  if (prompts.length > BC_MAX_PROMPTS) return;

  const repo = document.getElementById('bc-repo').value;
  if (!repo) return;

  const labelsRaw = document.getElementById('bc-default-labels').value;
  const defaultLabels = labelsRaw.split(',').map(l => l.trim()).filter(l => l.length > 0);
  // Dedupe
  const uniqueLabels = [...new Set(defaultLabels)];

  const concurrency = parseInt(document.getElementById('bc-concurrency').value, 10);

  // Preserve Step 1 inputs so Recreate can repopulate them (issue #208)
  _bcLastInput = {
    text,
    repo,
    labelsRaw,
    concurrency,
  };
  // Persist to localStorage so Recreate still works after a page refresh (issue #335)
  try { localStorage.setItem('bc_last_input', JSON.stringify(_bcLastInput)); } catch (_) {}

  const runBtn = document.getElementById('bc-run-btn');
  runBtn.disabled = true;
  runBtn.textContent = 'Starting…';

  const errEl = document.getElementById('bc-step1-error');
  errEl.classList.add('hidden');

  try {
    // Use FormData so we can include attachment files
    const formData = new FormData();
    formData.append('repo', repo);
    formData.append('prompts', JSON.stringify(prompts));
    formData.append('default_labels', JSON.stringify(uniqueLabels));
    formData.append('concurrency', String(concurrency));
    for (const f of _bcFiles) {
      formData.append('files', f);
    }

    const res = await fetch('/api/tickets/bulk', {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      errEl.textContent = data.detail || `Server error ${res.status}`;
      errEl.classList.remove('hidden');
      runBtn.disabled = false;
      runBtn.textContent = `Run BA on all ${prompts.length}`;
      return;
    }
    const data = await res.json();
    _bcJobId = data.job_id;
    localStorage.setItem('bc_job_id', _bcJobId);

    _bcStartTimes = {};
    _bcAvgMs = null;

    _bcShowStep2Shell();
    _bcConnectSSE(_bcJobId);
  } catch (e) {
    errEl.textContent = 'Failed to start job: ' + e.message;
    errEl.classList.remove('hidden');
    runBtn.disabled = false;
    runBtn.textContent = `Run BA on all ${prompts.length}`;
  }
}

function _bcConnectSSE(jobId) {
  if (_bcEventSource) {
    _bcEventSource.close();
    _bcEventSource = null;
  }

  const es = new EventSource(`/api/tickets/bulk/${jobId}/stream`);
  _bcEventSource = es;

  es.addEventListener('snapshot', (e) => {
    try {
      _bcJobState = JSON.parse(e.data);
      _bcRenderStep2();
    } catch (_) {}
  });

  es.addEventListener('update', (e) => {
    try {
      const event = JSON.parse(e.data);
      if (event.type === 'ticket_update' && _bcJobState) {
        const idx = event.ticket.index;
        _bcJobState.tickets[idx] = event.ticket;
        _bcRenderStep2();
      } else if (event.type === 'job_done' && _bcJobState) {
        _bcJobState.status = 'done';
        _bcRenderStep2();
        es.close();
        _bcEventSource = null;
        localStorage.removeItem('bc_job_id');
        _bcJobId = null;
        // Clear saved input when all tickets were created successfully so the
        // recovery banner doesn't appear on the next modal open (issue #353)
        const failed = (_bcJobState.tickets || []).filter(t => t.state === 'failed').length;
        if (failed === 0) {
          try { localStorage.removeItem('bc_last_input'); } catch (_) {}
          _bcLastInput = null;
        }
      }
    } catch (_) {}
  });

  es.onerror = () => {
    // SSE will auto-reconnect; we'll get a new snapshot on reconnect
  };
}

function _bcRenderStep2() {
  if (!_bcJobState) return;
  const job = _bcJobState;
  const tickets = job.tickets || [];
  const concurrency = job.concurrency || 3;

  const created = tickets.filter(t => t.state === 'created').length;
  const estimating = tickets.filter(t => t.state === 'estimating').length;
  const sized = tickets.filter(t => t.state === 'sized').length;
  const estimateFailed = tickets.filter(t => t.state === 'estimate_failed').length;
  const drafting = tickets.filter(t => t.state === 'drafting').length;
  const pending = tickets.filter(t => t.state === 'pending').length;
  const failed = tickets.filter(t => t.state === 'failed').length;
  const skipped = tickets.filter(t => t.state === 'skipped').length;
  const sizeWarning = tickets.filter(t => t.state === 'size_warning').length;
  const total = tickets.length;
  // Tickets that have fully completed (terminal state reached)
  const fullyDone = sized + estimateFailed + failed + skipped;

  // Banner
  const banner = document.getElementById('bc-parallel-banner');
  if (banner) {
    if (job.status === 'running') {
      banner.innerHTML =
        `BA is drafting ${total} tickets in parallel &mdash; ` +
        `${sized + estimateFailed} done, ${drafting + estimating} running, ${pending + created} queued` +
        `<span class="bc-concurrency-pill">concurrency: ${concurrency}</span>`;
    } else if (job.status === 'done') {
      if (sizeWarning > 0) {
        banner.innerHTML = `All tickets processed. ${sizeWarning} ticket${sizeWarning > 1 ? 's' : ''} need size remediation.`;
      } else {
        banner.innerHTML = `All ${total} tickets processed.`;
      }
    } else if (job.status === 'stopped') {
      banner.innerHTML = `Stopped. ${sized + estimateFailed} estimated, ${failed} failed, ${skipped} skipped.`;
    }
  }

  // Summary strip
  const strip = document.getElementById('bc-summary-strip');
  if (strip) {
    const pct = total > 0 ? Math.round((fullyDone / total) * 100) : 0;
    let etaHtml = '';
    // Estimate remaining based on original BA creation pace
    if ((sized + estimateFailed) > 0 && pending > 0 && _bcAvgMs) {
      const estMs = (_bcAvgMs / concurrency) * pending;
      const estSec = Math.round(estMs / 1000);
      const m = Math.floor(estSec / 60);
      const s = estSec % 60;
      const estLabel = m > 0 ? `${m}m ${s}s` : `${s}s`;
      etaHtml = `<span class="bc-eta">est. ${estLabel} remaining</span>`;
    }
    strip.innerHTML = `
      <span class="bc-metric bc-metric--created"><span class="bc-metric-count">${sized + estimateFailed}</span> Done</span>
      <span class="bc-metric bc-metric--drafting"><span class="bc-metric-count">${drafting + estimating + created}</span> In progress</span>
      <span class="bc-metric bc-metric--pending"><span class="bc-metric-count">${pending}</span> Pending</span>
      <div class="bc-progress-wrap">
        <div class="bc-progress-bar" style="width:${pct}%"></div>
      </div>
      ${etaHtml}
    `;
  }

  // Ticket list
  const list = document.getElementById('bc-ticket-list');
  if (list) {
    list.innerHTML = tickets.map(t => _bcRenderCard(t)).join('');
  }

  // Footer buttons
  const stopBtn = document.getElementById('bc-stop-btn');
  const doneBtn = document.getElementById('bc-done-btn');
  const recreateBtn = document.getElementById('bc-recreate-btn');
  if (stopBtn && doneBtn) {
    if (job.status === 'running') {
      stopBtn.disabled = false;
      doneBtn.disabled = true;
      doneBtn.textContent = 'Running...';
      if (recreateBtn) recreateBtn.classList.add('hidden');
    } else {
      stopBtn.disabled = true;
      doneBtn.disabled = false;
      doneBtn.textContent = 'Close';
      doneBtn.onclick = closeBulkCreateModal;
      // Show Recreate button once job is done/stopped (issue #208)
      if (recreateBtn) recreateBtn.classList.remove('hidden');
    }
  }

  // Track avg time
  tickets.forEach(t => {
    if (t.state === 'drafting' && t.started_at && !_bcStartTimes[t.index]) {
      _bcStartTimes[t.index] = new Date(t.started_at).getTime();
    }
    if (t.state === 'created' && _bcStartTimes[t.index] && t.finished_at) {
      const elapsed = new Date(t.finished_at).getTime() - _bcStartTimes[t.index];
      if (elapsed > 0) {
        _bcAvgMs = _bcAvgMs === null ? elapsed : (_bcAvgMs * 0.7 + elapsed * 0.3);
      }
    }
  });
}

function _bcRenderCard(t) {
  const stateClass = `bc-card--${t.state}`;
  const dotClass = `bc-status-dot--${t.state}`;

  let preview = '';
  let label = '';
  let head = '';
  let actions = '';
  let tags = '';

  if (t.state === 'pending') {
    preview = escapeHtml((t.prompt || '').slice(0, 80));
    label = 'Queued';
    actions = `<button class="bc-action-btn" title="Skip" onclick="bcSkipTicket(${t.index})">&#x00D7;</button>`;
  } else if (t.state === 'drafting') {
    preview = 'BA polishing prompt → drafting AC and UAT steps...';
    const now = Date.now();
    const start = _bcStartTimes[t.index] || (t.started_at ? new Date(t.started_at).getTime() : now);
    const elapsedSec = Math.round((now - start) / 1000);
    label = `Started ${elapsedSec}s ago`;
    // No action icons while drafting
  } else if (t.state === 'created') {
    const issueNum = t.issue_num;
    const issueUrl = t.issue_url || '#';
    head = `<a class="bc-issue-badge" href="${escapeHtml(issueUrl)}" target="_blank" rel="noopener">#${issueNum}</a>`;
    preview = escapeHtml((t.body_preview || t.body || '').slice(0, 200));
    label = 'Created';
    const labelsArr = t.label_pills || [];
    if (labelsArr.length > 0) {
      tags = `<div class="bc-card-tags">${labelsArr.map(l => `<span class="bc-tag">${escapeHtml(l)}</span>`).join('')}</div>`;
    }
    if (t.attachment_warning) {
      tags += `<div class="bc-attach-warn">${escapeHtml(t.attachment_warning)}</div>`;
    }
    actions = `<a class="bc-action-btn" href="${escapeHtml(issueUrl)}" target="_blank" rel="noopener" title="Open on GitHub">&#x2197;</a>`;
  } else if (t.state === 'estimating') {
    const issueNum = t.issue_num;
    const issueUrl = t.issue_url || '#';
    head = `<a class="bc-issue-badge" href="${escapeHtml(issueUrl)}" target="_blank" rel="noopener">#${issueNum}</a>`;
    preview = 'Estimating size (S/M/L/XL)...';
    label = 'estimating…';
    actions = `<a class="bc-action-btn" href="${escapeHtml(issueUrl)}" target="_blank" rel="noopener" title="Open on GitHub">&#x2197;</a>`;
  } else if (t.state === 'sized') {
    const issueNum = t.issue_num;
    const issueUrl = t.issue_url || '#';
    const sizeBadge = t.estimate_size ? `<span class="bc-size-badge">${escapeHtml(t.estimate_size)}</span>` : '';
    head = `<a class="bc-issue-badge" href="${escapeHtml(issueUrl)}" target="_blank" rel="noopener">#${issueNum}</a>${sizeBadge}`;
    preview = escapeHtml((t.body_preview || t.body || '').slice(0, 200));
    label = t.estimate_size ? `Sized: ${t.estimate_size}` : 'Sized';
    const labelsArr = t.label_pills || [];
    if (labelsArr.length > 0) {
      tags = `<div class="bc-card-tags">${labelsArr.map(l => `<span class="bc-tag">${escapeHtml(l)}</span>`).join('')}</div>`;
    }
    if (t.attachment_warning) {
      tags += `<div class="bc-attach-warn">${escapeHtml(t.attachment_warning)}</div>`;
    }
    actions = `<a class="bc-action-btn" href="${escapeHtml(issueUrl)}" target="_blank" rel="noopener" title="Open on GitHub">&#x2197;</a>`;
  } else if (t.state === 'estimate_failed') {
    const issueNum = t.issue_num;
    const issueUrl = t.issue_url || '#';
    head = `<a class="bc-issue-badge" href="${escapeHtml(issueUrl)}" target="_blank" rel="noopener">#${issueNum}</a>`;
    preview = escapeHtml((t.estimate_error || 'Estimation failed').slice(0, 120));
    label = 'Estimate failed';
    const labelsArr = t.label_pills || [];
    if (labelsArr.length > 0) {
      tags = `<div class="bc-card-tags">${labelsArr.map(l => `<span class="bc-tag">${escapeHtml(l)}</span>`).join('')}</div>`;
    }
    actions = `<a class="bc-action-btn" href="${escapeHtml(issueUrl)}" target="_blank" rel="noopener" title="Open on GitHub">&#x2197;</a>`;
  } else if (t.state === 'failed') {
    preview = escapeHtml(t.error || 'Unknown error');
    label = 'Failed';
    actions = `<button class="bc-action-btn bc-action-btn--retry" title="Retry" onclick="bcRetryTicket(${t.index})">&#x21BA;</button>`;
  } else if (t.state === 'size_warning') {
    // Size warning card: amber banner with two remediation buttons (issue #261)
    const charCount = t.body_char_count || (t.body || '').length;
    const overBy    = t.body_over_by   || Math.max(0, charCount - 62000);
    const titleText = t.title ? escapeHtml(t.title.slice(0, 80)) : '';
    const headHtmlSz = titleText
      ? `<div class="bc-card-head"><span class="bc-card-label" style="color:#d97706">Size warning</span><span class="bc-card-preview" style="flex:1;margin-left:6px;">${titleText}</span></div>`
      : `<div class="bc-card-head"><span class="bc-card-label" style="color:#d97706">Size warning</span></div>`;
    const bodyStr = t.body || '';
    const hasInlineImages = /!\[[^\]]*\]\(data:image\/[a-zA-Z]+;base64,/.test(bodyStr);
    const remedyImagesBtn = hasInlineImages
      ? `<button class="bc-action-btn" style="font-size:10px;padding:2px 6px;color:#d97706;border:1px solid #d97706;border-radius:4px;" onclick="bcSizeRemedyImages(${t.index})">Link images</button>`
      : `<button class="bc-action-btn" style="font-size:10px;padding:2px 6px;opacity:0.4;" disabled title="No inlined base64 images">Link images</button>`;
    return `
      <div class="bc-card" style="border-color:#d97706;">
        <div class="bc-status-dot" style="border-color:#d97706;background:#d97706;display:flex;align-items:center;justify-content:center;"><span style="color:#fff;font-size:7px;font-weight:700">!</span></div>
        <div class="bc-card-body">
          ${headHtmlSz}
          <div class="bc-card-preview" style="color:#d97706;white-space:normal;font-size:10px;">
            ${charCount.toLocaleString()} chars — ${overBy.toLocaleString()} over limit
          </div>
        </div>
        <div class="bc-card-actions" style="flex-direction:column;gap:4px;align-items:flex-end;">
          <button class="bc-action-btn" style="font-size:10px;padding:2px 6px;color:#d97706;border:1px solid #d97706;border-radius:4px;" onclick="bcSizeRemedyComment(${t.index})">Split to comment</button>
          ${remedyImagesBtn}
          <button class="bc-action-btn" onclick="bcSkipTicket(${t.index})">&#x00D7;</button>
        </div>
      </div>
    `;
  } else if (t.state === 'skipped') {
    preview = escapeHtml((t.prompt || '').slice(0, 80));
    label = 'Skipped';
    actions = `<button class="bc-action-btn" title="Undo skip" onclick="bcUndoSkip(${t.index})">&#x21A9;</button>`;
  }

  const headHtml = head ? `<div class="bc-card-head">${head}<span class="bc-card-label">${escapeHtml(label)}</span></div>` :
    `<div class="bc-card-head"><span class="bc-card-label">${escapeHtml(label)}</span></div>`;

  const previewClass = t.state === 'failed' ? 'bc-card-preview bc-card-preview--error' : 'bc-card-preview';

  return `
    <div class="bc-card ${stateClass}">
      <div class="bc-status-dot ${dotClass}"></div>
      <div class="bc-card-body">
        ${headHtml}
        <div class="${previewClass}" title="${escapeHtml((t.state === 'failed' ? t.error : t.prompt) || '')}">${preview}</div>
        ${tags}
      </div>
      ${actions ? `<div class="bc-card-actions">${actions}</div>` : ''}
    </div>
  `;
}

async function bcSkipTicket(index) {
  if (!_bcJobId) return;
  try {
    await fetch(`/api/tickets/bulk/${_bcJobId}/skip`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ index }),
    });
    // SSE will update state
  } catch (_) {}
}

async function bcUndoSkip(index) {
  // Re-set to pending and retry
  if (!_bcJobId || !_bcJobState) return;
  const ticket = _bcJobState.tickets[index];
  if (ticket && ticket.state === 'skipped') {
    // Mark pending locally and call retry (which re-queues)
    ticket.state = 'pending';
    _bcRenderStep2();
    try {
      await fetch(`/api/tickets/bulk/${_bcJobId}/retry`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ index }),
      });
    } catch (_) {}
  }
}

async function bcRetryTicket(index) {
  if (!_bcJobId) return;
  try {
    await fetch(`/api/tickets/bulk/${_bcJobId}/retry`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ index }),
    });
    // SSE will update state
  } catch (_) {}
}

async function bcStop() {
  if (!_bcJobId) return;
  const btn = document.getElementById('bc-stop-btn');
  if (btn) btn.disabled = true;
  try {
    await fetch(`/api/tickets/bulk/${_bcJobId}/stop`, { method: 'POST' });
  } catch (_) {}
}

// ── Size-warning remediation (issue #261) ────────────────────────────────────

async function bcSizeRemedyComment(index) {
  if (!_bcJobId) return;
  try {
    await fetch(`/api/tickets/bulk/${_bcJobId}/size-remedy-comment`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ index }),
    });
    // SSE will update state
  } catch (_) {}
}

async function bcSizeRemedyImages(index) {
  if (!_bcJobId) return;
  try {
    await fetch(`/api/tickets/bulk/${_bcJobId}/size-remedy-images`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ index }),
    });
    // SSE will update state
  } catch (_) {}
}

// Recreate: go back to Step 1 with the same input data (issue #208)
function bcRecreate() {
  // Disconnect SSE and clear job state
  if (_bcEventSource) {
    _bcEventSource.close();
    _bcEventSource = null;
  }
  localStorage.removeItem('bc_job_id');
  _bcJobId = null;
  _bcJobState = null;
  _bcStartTimes = {};
  _bcAvgMs = null;

  _bcShowStep1();

  // Populate repo dropdown
  const sel = document.getElementById('bc-repo');
  sel.innerHTML = allProjects.map(p =>
    `<option value="${escapeHtml(p.repo)}">${escapeHtml(p.name || p.repo)}</option>`
  ).join('');

  // Fall back to localStorage if in-memory state was lost (e.g. page refresh, issue #335)
  if (!_bcLastInput) {
    try {
      const saved = localStorage.getItem('bc_last_input');
      if (saved) _bcLastInput = JSON.parse(saved);
    } catch (e) {
      console.warn('[bcRecreate] Failed to parse bc_last_input from localStorage — data may be corrupted. Clearing entry.', e);
      localStorage.removeItem('bc_last_input');
    }
  }

  // Always hide the recovery banner when Recreate is used (data is about to be restored)
  const recoverBanner = document.getElementById('bc-recovery-banner');
  if (recoverBanner) recoverBanner.classList.add('hidden');

  // Restore previous input values if available
  if (_bcLastInput) {
    if (sel && _bcLastInput.repo) sel.value = _bcLastInput.repo;
    const textarea = document.getElementById('bc-textarea');
    if (textarea) textarea.value = _bcLastInput.text || '';
    const labelsEl = document.getElementById('bc-default-labels');
    if (labelsEl) labelsEl.value = _bcLastInput.labelsRaw || 'enhancement';
    const concEl = document.getElementById('bc-concurrency');
    if (concEl) concEl.value = String(_bcLastInput.concurrency || 3);
  } else {
    if (_activeProject) sel.value = _activeProject;
    document.getElementById('bc-textarea').value = '';
    document.getElementById('bc-default-labels').value = 'enhancement';
    document.getElementById('bc-concurrency').value = '3';
  }

  document.getElementById('bc-step1-error').classList.add('hidden');
  // Do NOT reset files — keep the same attachments for the re-run
  _bcUpdateCounter();
}

// Restore saved inputs from localStorage into Step 1 fields (issue #335)
function bcRestoreLastInput() {
  try {
    const saved = localStorage.getItem('bc_last_input');
    if (!saved) return;
    const input = JSON.parse(saved);
    if (!input.repo || typeof input.repo !== 'string' || !input.repo.trim() ||
        !input.text || typeof input.text !== 'string' || !input.text.trim()) {
      console.warn('[bcRestoreLastInput] bc_last_input missing required fields (repo, text) — clearing entry.');
      localStorage.removeItem('bc_last_input');
      return;
    }
    _bcLastInput = input;
    const sel = document.getElementById('bc-repo');
    if (sel && input.repo) sel.value = input.repo;
    const textarea = document.getElementById('bc-textarea');
    if (textarea) textarea.value = input.text || '';
    const labelsEl = document.getElementById('bc-default-labels');
    if (labelsEl) labelsEl.value = input.labelsRaw || 'enhancement';
    const concEl = document.getElementById('bc-concurrency');
    if (concEl) concEl.value = String(input.concurrency || 3);
    document.getElementById('bc-recovery-banner').classList.add('hidden');
    _bcUpdateCounter();
  } catch (e) {
    console.warn('[bcRestoreLastInput] Failed to parse bc_last_input from localStorage — data may be corrupted. Clearing entry.', e);
    localStorage.removeItem('bc_last_input');
  }
}

// Dismiss recovery banner and clear saved inputs (issue #335)
function bcDismissRecovery() {
  try { localStorage.removeItem('bc_last_input'); } catch (_) {}
  _bcLastInput = null;
  document.getElementById('bc-recovery-banner').classList.add('hidden');
}

function _bcShowToast(msg) {
  const existing = document.querySelector('.bc-toast');
  if (existing) existing.remove();
  const toast = document.createElement('div');
  toast.className = 'bc-toast';
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

// ── Deploy to Production ──────────────────────────────────────────────────────

function openDeployModal() {
  const errEl = document.getElementById('deploy-modal-error');
  if (errEl) { errEl.textContent = ''; errEl.classList.add('hidden'); }
  const confirmBtn = document.getElementById('deploy-modal-confirm-btn');
  const cancelBtn  = document.getElementById('deploy-modal-cancel-btn');
  if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.innerHTML = '<i class="ti ti-rocket"></i> Create Draft PR'; }
  if (cancelBtn)  cancelBtn.disabled = false;
  document.getElementById('deploy-modal-backdrop')?.classList.remove('hidden');
  document.getElementById('deploy-modal')?.classList.remove('hidden');
}

function closeDeployModal() {
  const confirmBtn = document.getElementById('deploy-modal-confirm-btn');
  // Don't close while in loading state
  if (confirmBtn && confirmBtn.disabled) return;
  document.getElementById('deploy-modal-backdrop')?.classList.add('hidden');
  document.getElementById('deploy-modal')?.classList.add('hidden');
}

async function confirmDeploy() {
  const confirmBtn = document.getElementById('deploy-modal-confirm-btn');
  const cancelBtn  = document.getElementById('deploy-modal-cancel-btn');
  const errEl      = document.getElementById('deploy-modal-error');
  if (!confirmBtn) return;

  confirmBtn.disabled = true;
  if (cancelBtn) cancelBtn.disabled = true;
  confirmBtn.innerHTML = '<span class="dt-spinner"></span> Creating PR…';
  if (errEl) { errEl.textContent = ''; errEl.classList.add('hidden'); }

  try {
    const res = await fetch('/api/deploy/promote', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ draft: true }),
    });

    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try { const d = await res.json(); detail = d.detail || detail; } catch {}
      if (errEl) { errEl.textContent = detail; errEl.classList.remove('hidden'); }
      confirmBtn.disabled = false;
      if (cancelBtn) cancelBtn.disabled = false;
      confirmBtn.innerHTML = '<i class="ti ti-rocket"></i> Create Draft PR';
      return;
    }

    const data = await res.json();
    const prUrl = data.pr_url || '';

    // Close the modal
    document.getElementById('deploy-modal-backdrop')?.classList.add('hidden');
    document.getElementById('deploy-modal')?.classList.add('hidden');

    // Show deploy toast with PR URL and View PR button
    showDeployToast(prUrl);
  } catch (e) {
    if (errEl) { errEl.textContent = `Request failed: ${e.message}`; errEl.classList.remove('hidden'); }
    if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.innerHTML = '<i class="ti ti-rocket"></i> Create Draft PR'; }
    if (cancelBtn)  cancelBtn.disabled = false;
  }
}

function showDeployToast(prUrl) {
  const t = document.getElementById('toast-deploy');
  if (!t) return;
  if (prUrl) {
    t.innerHTML = `Draft PR created successfully! <a href="${escapeHtml(prUrl)}" target="_blank" rel="noopener noreferrer" style="color:#93c5fd;font-weight:600;text-decoration:underline;">View PR</a>`;
  } else {
    t.innerHTML = 'Draft PR created successfully!';
  }
  t.style.display = 'block';
  setTimeout(() => { t.style.display = 'none'; }, 8000);
}

// ── Sprint Live Log Panel (issue #224) ────────────────────────────────────────
//
// One panel instance per running sprint block. Keyed by sprint_label.
// Each panel has:
//   - a 3-stat grid (time spent, current ticket, active agent)
//   - a streaming log feed
//   - SSE connection to /api/sprints/<label>/live/stream
//
// Panel lifecycle:
//   mount  → smgmtLivePanelMount(label, project)
//   update → smgmtLivePanelUpdate(label, snapshot)   (called by snapshot poll)
//   unmount → smgmtLivePanelUnmount(label)            (called when sprint ends)

const _sllPanels = {};   // label -> { el, sse, tickInterval, autoScroll, lineCount }

// ── CSS injected once ──────────────────────────────────────────────────────────
(function _injectSllCss() {
  if (document.getElementById('sll-styles')) return;
  const style = document.createElement('style');
  style.id = 'sll-styles';
  style.textContent = `
    .sll-panel {
      margin-top: 16px;
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
      background: var(--surface, #1e1e2e);
      transition: opacity 0.3s ease;
    }
    .sll-panel.sll-fading { opacity: 0; pointer-events: none; }

    /* Header */
    .sll-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 14px 18px;
      border-bottom: 1px solid var(--border);
    }
    .sll-header-left {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .sll-live-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #22c55e;
      animation: sll-pulse 1.5s ease-in-out infinite;
      flex-shrink: 0;
    }
    @keyframes sll-pulse {
      0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(34,197,94,0.5); }
      50%       { opacity: 0.7; box-shadow: 0 0 0 4px rgba(34,197,94,0); }
    }
    .sll-title {
      font-size: 14px;
      font-weight: 700;
      color: var(--text);
    }
    .sll-controls {
      display: flex;
      gap: 6px;
      align-items: center;
    }
    .sll-pill {
      font-family: ui-monospace, monospace;
      font-size: 11px;
      padding: 3px 8px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: transparent;
      cursor: pointer;
      color: var(--text-muted);
      transition: background 0.15s, color 0.15s;
    }
    .sll-pill.sll-pill-streaming {
      background: rgba(34,197,94,0.12);
      color: #22c55e;
      border-color: rgba(34,197,94,0.3);
    }
    .sll-pill-pause[aria-pressed="true"] {
      background: rgba(234,179,8,0.15);
      color: #eab308;
      border-color: rgba(234,179,8,0.35);
    }
    .sll-pill:hover { background: var(--hover-bg, rgba(255,255,255,0.06)); }

    /* 3-stat grid */
    .sll-stats {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      border-bottom: 1px solid var(--border);
    }
    .sll-stat {
      padding: 14px 18px;
      border-right: 1px solid var(--border);
    }
    .sll-stat:last-child { border-right: none; }
    .sll-stat-label {
      font-size: 10px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--text-muted);
      margin-bottom: 4px;
    }
    .sll-stat-value {
      font-family: ui-monospace, monospace;
      font-size: 18px;
      font-weight: 700;
      color: var(--text);
      margin-bottom: 2px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .sll-stat-value.sll-stat-value--sm { font-size: 15px; }
    .sll-stat-sub {
      font-family: ui-monospace, monospace;
      font-size: 11px;
      color: var(--text-muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .sll-agent-pill {
      display: inline-block;
      font-family: ui-monospace, monospace;
      font-size: 11px;
      font-weight: 600;
      padding: 2px 8px;
      border-radius: 4px;
    }
    .sll-agent-pill--coder {
      background: rgba(168,85,247,0.15);
      color: #c084fc;
      border: 1px solid rgba(168,85,247,0.3);
    }
    .sll-agent-pill--tester {
      background: rgba(245,158,11,0.15);
      color: #fbbf24;
      border: 1px solid rgba(245,158,11,0.3);
    }

    /* Log feed */
    .sll-feed {
      background: var(--bg, #13131f);
      padding: 12px 20px;
      font-family: ui-monospace, monospace;
      font-size: 12px;
      line-height: 1.7;
      max-height: 220px;
      overflow-y: auto;
    }
    .sll-feed-inner {
      /* aria-live region */
    }
    .sll-line {
      display: flex;
      gap: 10px;
      animation: sll-slideIn 0.3s ease;
    }
    @keyframes sll-slideIn {
      from { opacity: 0; transform: translateX(-8px); }
      to   { opacity: 1; transform: translateX(0); }
    }
    .sll-line-ts  { color: var(--text-muted); flex-shrink: 0; }
    .sll-line-msg { flex: 1; word-break: break-all; }
    .sll-line--dispatch .sll-line-msg { color: #60a5fa; font-weight: 500; }
    .sll-line--success  .sll-line-msg { color: #4ade80; }
    .sll-line--warn     .sll-line-msg { color: #fbbf24; }
    .sll-line--fail     .sll-line-msg { color: #f87171; }
    .sll-line--event    .sll-line-msg { color: var(--text); }
    .sll-cursor {
      display: inline-block;
      color: #22c55e;
      animation: sll-blink 1s steps(2) infinite;
    }
    @keyframes sll-blink {
      0%, 100% { opacity: 1; }
      50%       { opacity: 0; }
    }
  `;
  document.head.appendChild(style);
})();

// ── Helpers ────────────────────────────────────────────────────────────────────

function _sllFmtDuration(totalSec) {
  const mins = Math.floor(totalSec / 60);
  return `${mins}m`;
}

function _sllFmtStarted(isoStr) {
  if (!isoStr) return '';
  try {
    const d = new Date(isoStr);
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    return `started ${hh}:${mm}`;
  } catch { return ''; }
}

function _sllNow() {
  const d = new Date();
  return `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`;
}

function _sllEscHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Mount ──────────────────────────────────────────────────────────────────────

function smgmtLivePanelMount(label, project) {
  if (_sllPanels[label]) return;  // already mounted

  const block = document.getElementById(`smgmt-block-${label}`);
  if (!block) return;

  const n = label.replace('sprint-', '');

  // Build panel DOM
  const panel = document.createElement('div');
  panel.className = 'sll-panel';
  panel.id = `sll-panel-${label}`;
  panel.setAttribute('data-label', label);

  panel.innerHTML = `
    <div class="sll-header">
      <div class="sll-header-left">
        <span class="sll-live-dot" aria-label="Live"></span>
        <span class="sll-title">Sprint ${_sllEscHtml(n)} live log</span>
      </div>
      <div class="sll-controls">
        <span class="sll-pill sll-pill-streaming" id="sll-pill-streaming-${label}">Streaming</span>
        <button class="sll-pill sll-pill-pause"
                id="sll-pill-pause-${label}"
                aria-pressed="false"
                onclick="smgmtLivePanelTogglePause('${label}')">Pause autoscroll</button>
        <button class="sll-pill"
                id="sll-pill-expand-${label}"
                onclick="smgmtLivePanelExpand('${label}')">Expand</button>
      </div>
    </div>

    <div class="sll-stats">
      <div class="sll-stat" id="sll-stat-time-${label}">
        <div class="sll-stat-label"><i class="ti ti-clock" aria-hidden="true"></i> Time Spent</div>
        <div class="sll-stat-value" id="sll-val-time-${label}">—</div>
        <div class="sll-stat-sub"  id="sll-sub-time-${label}">—</div>
      </div>
      <div class="sll-stat" id="sll-stat-ticket-${label}">
        <div class="sll-stat-label"><i class="ti ti-ticket" aria-hidden="true"></i> Currently Working On</div>
        <div class="sll-stat-value sll-stat-value--sm" id="sll-val-ticket-${label}">—</div>
        <div class="sll-stat-sub"  id="sll-sub-ticket-${label}">—</div>
      </div>
      <div class="sll-stat" id="sll-stat-agent-${label}">
        <div class="sll-stat-label"><i class="ti ti-robot" aria-hidden="true"></i> Active Agent</div>
        <div class="sll-stat-value" id="sll-val-agent-${label}">—</div>
        <div class="sll-stat-sub"  id="sll-sub-agent-${label}">—</div>
      </div>
    </div>

    <div class="sll-feed" id="sll-feed-${label}" role="log" aria-live="polite" aria-label="Sprint live log feed">
      <div class="sll-feed-inner" id="sll-feed-inner-${label}"></div>
    </div>`;

  // Insert after the sprint block
  block.insertAdjacentElement('afterend', panel);

  // State
  _sllPanels[label] = {
    el: panel,
    sse: null,
    tickInterval: null,
    autoScroll: true,
    lineCount: 0,
    startedAt: null,
  };

  // Load snapshot immediately
  smgmtLivePanelLoadSnapshot(label, project);

  // Start SSE stream
  smgmtLivePanelConnectSSE(label, project);

  // Start 1-second ticker for the Time Spent stat
  const state = _sllPanels[label];
  state.tickInterval = setInterval(() => {
    if (!_sllPanels[label]) return;
    _sllUpdateTimeStat(label);
  }, 1000);
}

// ── Load snapshot (initial state + backfill of last 50 log lines) ─────────────

async function smgmtLivePanelLoadSnapshot(label, project) {
  try {
    const resp = await fetch(
      `/api/sprints/${encodeURIComponent(label)}/live?project=${encodeURIComponent(project)}`
    );
    if (!resp.ok) return;
    const snap = await resp.json();
    smgmtLivePanelUpdateStats(label, snap);

    // Backfill log lines
    const lines = snap.recent_log_lines || [];
    for (const entry of lines) {
      _sllAddLine(label, entry);
    }
  } catch { /* ignore */ }
}

// ── Update stats ───────────────────────────────────────────────────────────────

function smgmtLivePanelUpdateStats(label, snap) {
  const state = _sllPanels[label];
  if (!state) return;

  // Store started_at for the tick timer
  if (snap.started_at) {
    state.startedAt = snap.started_at;
    state.timeSpentSec = snap.time_spent_sec || 0;
    state.snapshotLoadedAt = Date.now();
  }

  _sllUpdateTimeStat(label, snap);

  // Ticket
  const valTicket = document.getElementById(`sll-val-ticket-${label}`);
  const subTicket = document.getElementById(`sll-sub-ticket-${label}`);
  if (valTicket && subTicket) {
    if (snap.current_ticket) {
      valTicket.textContent = `#${snap.current_ticket.number}`;
      subTicket.textContent = snap.current_ticket.title || '';
      subTicket.title = snap.current_ticket.title || '';
    } else {
      valTicket.textContent = '—';
      subTicket.textContent = 'Waiting…';
    }
  }

  // Agent
  const valAgent = document.getElementById(`sll-val-agent-${label}`);
  const subAgent = document.getElementById(`sll-sub-agent-${label}`);
  if (valAgent && subAgent) {
    if (snap.active_agent) {
      const name = (snap.active_agent.name || 'coder').toLowerCase();
      const pillClass = name === 'tester' ? 'sll-agent-pill--tester' : 'sll-agent-pill--coder';
      valAgent.innerHTML = `<span class="sll-agent-pill ${pillClass}">${_sllEscHtml(name.toUpperCase())}</span>`;
      const model = snap.active_agent.model || '';
      const pid   = snap.active_agent.pid ? `pid ${snap.active_agent.pid}` : '';
      subAgent.textContent = [model, pid].filter(Boolean).join(' · ') || '—';
    } else {
      valAgent.innerHTML = '—';
      subAgent.textContent = '—';
    }
  }
}

function _sllUpdateTimeStat(label, snap) {
  const state = _sllPanels[label];
  if (!state) return;

  let totalSec = 0;
  let secFrac = 0;
  if (state.snapshotLoadedAt && state.timeSpentSec != null) {
    const elapsed = (Date.now() - state.snapshotLoadedAt) / 1000;
    totalSec = Math.floor(state.timeSpentSec + elapsed);
    secFrac = Math.floor((state.timeSpentSec + elapsed) % 60);
  } else if (snap && snap.time_spent_sec) {
    totalSec = snap.time_spent_sec;
    secFrac  = totalSec % 60;
  }

  const valTime = document.getElementById(`sll-val-time-${label}`);
  const subTime = document.getElementById(`sll-sub-time-${label}`);
  if (valTime) valTime.textContent = _sllFmtDuration(totalSec);
  if (subTime) {
    const startedStr = state.startedAt ? _sllFmtStarted(state.startedAt) : '';
    subTime.textContent = `${secFrac}s · ${startedStr}`;
  }
}

// ── Add a log line ─────────────────────────────────────────────────────────────

function _sllAddLine(label, entry) {
  const state = _sllPanels[label];
  if (!state) return;

  const inner = document.getElementById(`sll-feed-inner-${label}`);
  if (!inner) return;

  // Remove cursor from previous last line
  inner.querySelectorAll('.sll-cursor').forEach(el => el.remove());

  const lineType = entry.type || 'event';
  const ts       = entry.timestamp || _sllNow();
  const msg      = entry.message || '';

  const line = document.createElement('div');
  line.className = `sll-line sll-line--${_sllEscHtml(lineType)}`;
  line.innerHTML = `<span class="sll-line-ts">${_sllEscHtml(ts)}</span><span class="sll-line-msg">${_sllEscHtml(msg)}<span class="sll-cursor" aria-hidden="true">&#x25AE;</span></span>`;
  inner.appendChild(line);

  // Prune to last 200 lines
  state.lineCount++;
  if (state.lineCount > 200) {
    const first = inner.querySelector('.sll-line');
    if (first) { first.remove(); state.lineCount--; }
  }

  // Auto-scroll
  if (state.autoScroll) {
    const feed = document.getElementById(`sll-feed-${label}`);
    if (feed) feed.scrollTop = feed.scrollHeight;
  }
}

// ── Toggle autoscroll ──────────────────────────────────────────────────────────

function smgmtLivePanelTogglePause(label) {
  const state = _sllPanels[label];
  if (!state) return;
  state.autoScroll = !state.autoScroll;
  const btn = document.getElementById(`sll-pill-pause-${label}`);
  if (btn) {
    const pressed = !state.autoScroll;
    btn.setAttribute('aria-pressed', pressed ? 'true' : 'false');
    btn.textContent = pressed ? 'Resume autoscroll' : 'Pause autoscroll';
  }
}

// ── Expand ─────────────────────────────────────────────────────────────────────

function smgmtLivePanelExpand(label) {
  // Open the Logs sub-tab (or navigate to the logs pane)
  const logsTab = document.getElementById('stab-logs');
  if (logsTab) {
    switchTab('logs');
  }
}

// ── SSE connection ─────────────────────────────────────────────────────────────

function smgmtLivePanelConnectSSE(label, project) {
  const state = _sllPanels[label];
  if (!state) return;

  // Close any existing connection
  if (state.sse) { try { state.sse.close(); } catch {} state.sse = null; }

  const url = `/api/sprints/${encodeURIComponent(label)}/live/stream?project=${encodeURIComponent(project)}`;
  const es = new EventSource(url);
  state.sse = es;

  // Update streaming pill to connected
  const pill = document.getElementById(`sll-pill-streaming-${label}`);
  if (pill) { pill.textContent = 'Streaming'; pill.classList.add('sll-pill-streaming'); }

  es.addEventListener('log_line', (ev) => {
    try {
      const entry = JSON.parse(ev.data);
      _sllAddLine(label, entry);
    } catch {}
  });

  es.addEventListener('complete', () => {
    smgmtLivePanelUnmount(label);
  });

  es.onerror = () => {
    // EventSource will auto-reconnect — just update the pill to show disconnected briefly
    if (pill) { pill.textContent = 'Reconnecting…'; pill.classList.remove('sll-pill-streaming'); }
    // If the state is gone, fully close
    if (!_sllPanels[label]) { try { es.close(); } catch {} }
  };

  es.onopen = () => {
    if (pill) { pill.textContent = 'Streaming'; pill.classList.add('sll-pill-streaming'); }
  };
}

// ── Unmount (fade then remove) ─────────────────────────────────────────────────

function smgmtLivePanelUnmount(label) {
  const state = _sllPanels[label];
  if (!state) return;

  // Stop SSE
  if (state.sse) { try { state.sse.close(); } catch {} state.sse = null; }

  // Stop tick
  if (state.tickInterval) { clearInterval(state.tickInterval); state.tickInterval = null; }

  // Fade out then remove from DOM
  const panel = state.el;
  if (panel) {
    panel.classList.add('sll-fading');
    setTimeout(() => {
      if (panel.parentNode) panel.parentNode.removeChild(panel);
    }, 350);
  }

  delete _sllPanels[label];
}

// ── Integration with smgmtApplyRunState ──────────────────────────────────────
//
// Called after each smgmtPollRunStatus cycle to reconcile panels with
// the current set of running sprints.

function smgmtSyncLivePanels() {
  if (!_smgmtCurrentRepo) return;

  // Running labels for the current project
  const runningLabels = new Set(
    Object.values(_smgmtAllRunning)
      .filter(e => e.project === _smgmtCurrentRepo)
      .map(e => e.sprint_label)
  );

  // Mount panels for newly-running sprints
  for (const label of runningLabels) {
    if (!_sllPanels[label]) {
      smgmtLivePanelMount(label, _smgmtCurrentRepo);
    }
  }

  // Unmount panels for sprints that are no longer running
  for (const label of Object.keys(_sllPanels)) {
    if (!runningLabels.has(label)) {
      smgmtLivePanelUnmount(label);
    }
  }
}
