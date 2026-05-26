// ── State ─────────────────────────────────────────────────────────────────────
let currentFilter    = 'all';
let allProjects      = [];
let expandedProjects = new Set(); // repos currently expanded
let detailsCache     = {};        // repo → detail data
let testReportCache  = {};        // `${repo}#${issueNum}` → report data
let doneAgentsVisible = {};       // repo → bool (toggle state for DONE agents, AC-2d)

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
  ['projects', 'agents', 'activity', 'history', 'sprint-plan'].forEach(t => {
    const viewId = t === 'sprint-plan' ? 'view-sprint-plan' : `view-${t}`;
    const tabId  = `mtab-${t}`;
    document.getElementById(viewId)?.classList.toggle('hidden', t !== tab);
    document.getElementById(tabId)?.classList.toggle('active', t === tab);
  });
  if (tab === 'agents')      fetchAgents();
  if (tab === 'activity')    fetchEvents();
  if (tab === 'history')     loadSprintHistory().catch(() => {});
  if (tab === 'sprint-plan') loadSprintPlanning();
}

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

  // use data attributes to avoid quoting issues in onclick
  return `
    <div class="project-block" id="proj-block-${id}">
      <div class="project-row" id="proj-row-${id}"
           data-repo="${escapeHtml(proj.repo)}" data-id="${id}"
           onclick="toggleProject(this.dataset.id, this.dataset.repo)">
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
        <div class="proj-col-chevron"><i class="ti ti-chevron-down chevron-icon"></i></div>
      </div>
      <div class="proj-expand hidden" id="proj-expand-${id}">
        <div class="expand-inner" id="proj-detail-${id}">
          <div class="expand-loading">Loading…</div>
        </div>
      </div>
    </div>`;
}

function renderProjects(projects) {
  allProjects = projects;
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

  // re-expand panels that were open before the re-render
  expandedProjects.forEach(repo => {
    const id    = _projId(repo);
    const row   = document.getElementById(`proj-row-${id}`);
    const panel = document.getElementById(`proj-expand-${id}`);
    if (!row || !panel) return;
    row.classList.add('expanded');
    panel.classList.remove('hidden');
    if (detailsCache[repo]) {
      renderExpandPanel(id, detailsCache[repo], repo);
    } else {
      loadProjectDetails(id, repo);
    }
  });
}

// ── Expand / collapse ─────────────────────────────────────────────────────────
function toggleProject(id, repo) {
  const row   = document.getElementById(`proj-row-${id}`);
  const panel = document.getElementById(`proj-expand-${id}`);
  if (!row || !panel) return;

  if (row.classList.contains('expanded')) {
    row.classList.remove('expanded');
    panel.classList.add('hidden');
    expandedProjects.delete(repo);
  } else {
    row.classList.add('expanded');
    panel.classList.remove('hidden');
    expandedProjects.add(repo);
    if (detailsCache[repo]) {
      renderExpandPanel(id, detailsCache[repo], repo);
    } else {
      loadProjectDetails(id, repo);
    }
  }
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
        <button class="btn-approve-sm" id="approve-btn-${n}" onclick="approveIssue(${n}, '${r}', this)">Approve</button>
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

  return `
    <div class="ticket-card">
      <div class="ticket-top">
        <a class="ticket-num" href="${escapeHtml(ticket.url)}" target="_blank" rel="noopener">#${ticket.number}</a>
        <a class="ticket-title ticket-title-link" href="${escapeHtml(ticket.url)}" target="_blank" rel="noopener">${escapeHtml(ticket.title)}</a>
        <span class="sbadge ${color}">${escapeHtml(ticket.status)}</span>
      </div>
      <div class="ticket-meta">${assignee}${sep}${updated}</div>
      ${branchChip}
      ${actionsHtml}
    </div>`;
}

function _ticketGroupHtml(label, tickets, repo) {
  if (tickets.length === 0) return '';
  return `<div class="ticket-group">
    <div class="expand-hdr-title ticket-group-hdr">${escapeHtml(label)} · ${tickets.length}</div>
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
  if (detailsCache[repo]) {
    renderExpandPanel(id, detailsCache[repo], repo);
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
    alert('Approve failed: ' + e.message);
    if (btnEl) btnEl.disabled = false;
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
  // clear both caches so next expand fetches fresh data
  delete detailsCache[repo];
  Object.keys(testReportCache).forEach(k => { if (k.startsWith(`${repo}#`)) delete testReportCache[k]; });
  loadProjectDetails(_projId(repo), repo);
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

// ── SSE ───────────────────────────────────────────────────────────────────────
function setLive(connected) {
  document.getElementById('live-dot')?.classList.toggle('off', !connected);
}

function connectSSE() {
  const es = new EventSource('/events');
  es.onopen    = () => setLive(true);
  es.onerror   = () => setLive(false);
  es.onmessage = ev => {
    try {
      const msg = JSON.parse(ev.data);

      // Sprint alert banner push (AC-3a)
      if (msg.type === 'alert') {
        loadAlerts().catch(() => {});
        return;
      }

      // Sprint status push (AC-6d)
      if (msg.type === 'sprint_update' && msg.sprint) {
        _sprintState = msg.sprint;
        renderSprintPanel(msg.sprint);
        return;
      }

      if (msg.type !== 'update') return;
      if (!document.getElementById('view-projects').classList.contains('hidden')) {
        loadProjects().catch(() => {});
        // AC-1d: when cache refreshes, also refresh details for expanded projects
        expandedProjects.forEach(repo => {
          delete detailsCache[repo];
          loadProjectDetails(_projId(repo), repo);
        });
      }
      if (!document.getElementById('view-agents').classList.contains('hidden')) fetchAgents();
      if (!document.getElementById('view-history')?.classList.contains('hidden')) {
        loadSprintHistory().catch(() => {});
      }
      if (msg.event && msg.event.event_type === 'sprint_plan_update') _handleSprintPlanSSE();
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
    if (!document.getElementById('view-history')?.classList.contains('hidden')) {
      tasks.push(loadSprintHistory());
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
  // We still fetch and cache sprint state for SSE compatibility.
  try {
    const res = await fetch('/api/sprint-status');
    if (res.status === 404) return;
    if (!res.ok) return;
    _sprintState = await res.json();
  } catch { /* silent */ }
}

function renderSprintPanel(state) {
  // The global sprint panel was removed (AC-1, issue #82).
  // Per-project progress bars are rendered in _miniSprintSummaryHtml inside each expand panel.
  // This function is kept as a no-op so SSE sprint_update messages don't throw errors.
  _sprintState = state;
}

// AC-6c: Retry skipped button — opens instructions (actual retry requires CLI)
function retrySkipped() {
  alert('To retry skipped issues, re-run the sprint manager with:\n\npython3 dashboard/scripts/sprint_manager.py <label> --retry-failed');
}

// ── Periodic refresh ──────────────────────────────────────────────────────────
setInterval(() => {
  if (!document.getElementById('view-projects').classList.contains('hidden')) {
    loadProjects().catch(() => {});
    loadPlanUsage().catch(() => {});
  }
  if (!document.getElementById('view-history')?.classList.contains('hidden')) {
    loadSprintHistory().catch(() => {});
  }
}, 60_000);

// Sprint status polls every 30s (AC-6d)
setInterval(() => {
  loadSprintStatus().catch(() => {});
  loadAlerts().catch(() => {});
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

function _sprintRowHtml(sprint, idx) {
  const n       = sprint.sprint_num != null ? sprint.sprint_num : '?';
  const date    = sprint.date || '—';
  const status  = sprint.status || 'unknown';
  const shipped = sprint.shipped_count ?? 0;
  const skipped = sprint.skipped_count ?? 0;

  return `
    <div class="history-row" id="history-row-${idx}" onclick="toggleHistoryRow(${idx})">
      <div class="history-row-header">
        <span class="history-sprint-num">Sprint ${escapeHtml(String(n))}</span>
        <span class="history-date">${escapeHtml(date)}</span>
        <span class="history-status">${_statusBadgeHtml(status)}</span>
        <span class="history-shipped">&#10003; ${shipped} shipped</span>
        <span class="history-skipped">${skipped > 0 ? skipped + ' skipped' : '—'}</span>
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
const STATUS_LABELS_SET = new Set(['in-progress', 'SIT', 'UAT', 'UAT-approved', 'needs-rework', 'blocked', 'backlog', 'enhancement', 'bug']);
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
        <span class="psp-conflict-icon" title="File conflict">&#9888;</span>
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

// ── SSE handler for sprint_plan_update ────────────────────────────────────────

function _handleSprintPlanSSE() {
  if (!document.getElementById('view-sprint-plan').classList.contains('hidden')) {
    loadSprintPlanning();
  }
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

// ── Init ──────────────────────────────────────────────────────────────────────
(function init() {
  initTheme();
  fetchEnvironment();
  loadProjects().catch(e => {
    document.getElementById('project-list').innerHTML =
      `<div class="empty-projects">Failed to load projects: ${escapeHtml(e.message)}</div>`;
  });
  loadPlanUsage().catch(() => {});
  loadAlerts().catch(() => {});
  loadSprintStatus().catch(() => {});
  connectSSE();
  document.getElementById('btn-refresh')?.addEventListener('click', manualRefresh);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && _rpRepo !== null) closeRemoveProjectDialog();
  });
})();
