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

// ── New Project Modal (AC-4) ──────────────────────────────────────────────────
function openNewProjectModal() {
  document.getElementById('new-project-backdrop').classList.remove('hidden');
  document.getElementById('new-project-modal').classList.remove('hidden');
  document.getElementById('np-repo').value  = '';
  document.getElementById('np-icon').value  = '';
  document.getElementById('np-color').value = '';
  _npClearError();
  document.getElementById('np-repo').focus();
}

function closeNewProjectModal() {
  document.getElementById('new-project-backdrop').classList.add('hidden');
  document.getElementById('new-project-modal').classList.add('hidden');
}

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

    // AC-4c: success — close modal and reload project list
    closeNewProjectModal();
    loadProjects();
  } catch (e) {
    _npShowError('Network error: ' + e.message);
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Add Project';
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

// ── Sprint Planning (issue #26) ───────────────────────────────────────────────

let _spData = null;          // last-fetched { sprints, issues }
let _spSelectedSprint = null; // currently selected sprint number (int or null)
let _spPendingChanges = {};  // issueNum -> { from: 'unassigned'|'sprint', to: ... }
let _spDragSrc = null;       // { card, issueNum, fromCol }
let _spDragGhost = null;     // pointer-event drag ghost element

// ── fetch / render ────────────────────────────────────────────────────────────

async function loadSprintPlanning() {
  try {
    const res = await fetch('/api/sprint-planning/issues');
    if (!res.ok) throw new Error(await res.text());
    _spData = await res.json();
    _renderSprintPlanning();
  } catch (e) {
    document.getElementById('sp-cards-unassigned').innerHTML =
      `<div class="sp-empty">Failed to load: ${escapeHtml(e.message)}</div>`;
  }
}

function _renderSprintPlanning() {
  if (!_spData) return;

  const { sprints, issues } = _spData;

  // Build sprint selector
  const sel = document.getElementById('sp-sprint-select');
  const prevVal = sel.value;
  sel.innerHTML = '';

  sprints.forEach(n => {
    const opt = document.createElement('option');
    opt.value = n;
    opt.textContent = `sprint-${n}`;
    sel.appendChild(opt);
  });

  // + New sprint option
  const newOpt = document.createElement('option');
  newOpt.value = 'new';
  newOpt.textContent = '+ New sprint';
  sel.appendChild(newOpt);

  // Restore or set default
  if (_spSelectedSprint !== null && sprints.includes(_spSelectedSprint)) {
    sel.value = String(_spSelectedSprint);
  } else if (prevVal && prevVal !== 'new' && sprints.includes(Number(prevVal))) {
    sel.value = prevVal;
    _spSelectedSprint = Number(prevVal);
  } else if (sprints.length > 0) {
    _spSelectedSprint = sprints[sprints.length - 1];
    sel.value = String(_spSelectedSprint);
  }

  _renderColumns(issues);
}

function _renderColumns(issues) {
  const sprint = _spSelectedSprint;

  // Apply pending changes
  const effectiveIssues = issues.map(iss => {
    const pending = _spPendingChanges[iss.number];
    if (!pending) return iss;
    const newSprint = pending.to === 'sprint' ? sprint : null;
    return { ...iss, sprint: newSprint };
  });

  const unassigned = effectiveIssues.filter(iss => iss.sprint === null || iss.sprint === undefined);
  const inSprint   = effectiveIssues.filter(iss => iss.sprint === sprint);

  const unassignedTitle = document.getElementById('sp-unassigned-title');
  const sprintTitle     = document.getElementById('sp-sprint-title');
  if (unassignedTitle) unassignedTitle.textContent = `Unassigned (${unassigned.length})`;
  if (sprintTitle)     sprintTitle.textContent     = `Sprint ${sprint ?? '?'} (${inSprint.length})`;

  document.getElementById('sp-cards-unassigned').innerHTML =
    unassigned.length === 0
      ? '<div class="sp-empty">No unassigned issues</div>'
      : unassigned.map(iss => _spCardHtml(iss)).join('');

  document.getElementById('sp-cards-sprint').innerHTML =
    sprint === null
      ? '<div class="sp-empty">Select a sprint</div>'
      : inSprint.length === 0
        ? '<div class="sp-empty">No issues in this sprint</div>'
        : inSprint.map(iss => _spCardHtml(iss)).join('');

  _bindDragEvents();
  _renderSpFooter(inSprint);

  // Show/hide Apply button if there are pending changes
  const applyBtn = document.getElementById('sp-apply-btn');
  if (applyBtn) {
    const hasPending = Object.keys(_spPendingChanges).length > 0;
    applyBtn.style.display = hasPending ? '' : 'none';
  }
}

function _spCardHtml(iss) {
  const statusColor = { 'in-progress': 'blue', 'SIT': 'amber', 'UAT': 'purple', 'backlog': 'gray', 'blocked': 'red' };
  const color = statusColor[iss.status] || 'gray';
  const title = escapeHtml(iss.title || '');
  return `
    <div class="sp-card" id="sp-card-${iss.number}"
         draggable="true"
         data-issue="${iss.number}" data-sprint="${iss.sprint ?? ''}" data-size="${escapeHtml(iss.size || '?')}">
      <div class="sp-card-top">
        <a class="sp-card-num" href="${escapeHtml(iss.url || '#')}" target="_blank" rel="noopener"
           onclick="event.stopPropagation()">#${iss.number}</a>
        <span class="sp-card-size">${escapeHtml(iss.size || '?')}</span>
        <span class="sp-card-title">${title}</span>
      </div>
      <div class="sp-card-meta">
        <span class="sbadge ${color}">${escapeHtml(iss.status)}</span>
      </div>
    </div>`;
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

function _renderSpFooter(inSprintIssues) {
  const footer = document.getElementById('sp-footer');
  if (!footer) return;
  if (_spSelectedSprint === null || inSprintIssues.length === 0) {
    footer.textContent = '';
    return;
  }

  const sizeHours = { S: 1, M: 2, L: 4, XL: 8 };
  const counts = {};
  let total = 0;
  for (const iss of inSprintIssues) {
    const s = iss.size || 'M';
    counts[s] = (counts[s] || 0) + 1;
    total += (sizeHours[s] || 2);
  }

  const sizeOrder = ['S', 'M', 'L', 'XL'];
  const parts = sizeOrder.filter(s => counts[s]).map(s => `${counts[s]}${s}`);
  footer.textContent = `Sprint ${_spSelectedSprint} size: ${parts.join(' + ')} · est ${total}h`;
}

// ── Sprint selector ───────────────────────────────────────────────────────────

async function onSprintSelect() {
  const sel = document.getElementById('sp-sprint-select');
  const val = sel.value;

  if (val === 'new') {
    // Auto-increment sprint number
    const sprints = (_spData && _spData.sprints) || [];
    const next = sprints.length > 0 ? Math.max(...sprints) + 1 : 1;
    // Create the label via assign endpoint — use a dummy "no-op" call to trigger label creation
    try {
      await fetch('/api/sprint-planning/issues'); // ensure data is loaded
      // Create label by calling ensure via a dry-run assign on a non-existent issue
      // Instead, we hit the assign endpoint with the new sprint for any existing issue
      // Actually, we just set the sprint selector to the new number and let it render
      _spSelectedSprint = next;
      _spPendingChanges = {};

      // Add to local sprints list
      if (_spData) {
        if (!_spData.sprints.includes(next)) _spData.sprints.push(next);
        sel.value = String(next); // will be wrong until re-render
        _renderSprintPlanning();
      }
    } catch (e) {
      sel.value = _spSelectedSprint ? String(_spSelectedSprint) : '';
    }
    return;
  }

  _spSelectedSprint = Number(val);
  _spPendingChanges = {};
  if (_spData) _renderColumns(_spData.issues);
}

// ── Apply pending changes ─────────────────────────────────────────────────────

async function applySprintChanges() {
  const changes = Object.entries(_spPendingChanges);
  if (changes.length === 0) return;

  const btn = document.getElementById('sp-apply-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Applying…'; }

  let failed = false;
  for (const [issNum, change] of changes) {
    const sprintVal = change.to === 'sprint' ? _spSelectedSprint : null;
    try {
      const res = await fetch('/api/sprint-planning/assign', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ issue: Number(issNum), sprint: sprintVal }),
      });
      if (!res.ok) throw new Error(await res.text());
    } catch (e) {
      failed = true;
      const card = document.getElementById(`sp-card-${issNum}`);
      if (card) {
        card.classList.add('error-flash');
        let errEl = card.querySelector('.sp-card-error');
        if (!errEl) { errEl = document.createElement('div'); errEl.className = 'sp-card-error'; card.appendChild(errEl); }
        errEl.textContent = 'Apply failed: ' + e.message;
        setTimeout(() => card.classList.remove('error-flash'), 600);
      }
    }
  }

  if (!failed) {
    _spPendingChanges = {};
  }

  if (btn) { btn.disabled = false; btn.textContent = 'Apply ✓'; }

  // Reload fresh data
  await loadSprintPlanning();
}

// ── Drag & drop (HTML5 native) ────────────────────────────────────────────────

function _bindDragEvents() {
  document.querySelectorAll('.sp-card').forEach(card => {
    card.addEventListener('dragstart', _onDragStart);
    card.addEventListener('dragend',   _onDragEnd);
  });
}

function _onDragStart(e) {
  const card = e.currentTarget;
  _spDragSrc = {
    card,
    issueNum: Number(card.dataset.issue),
    fromCol: card.closest('.sp-col')?.dataset.col,
  };
  card.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', card.dataset.issue);
}

function _onDragEnd(e) {
  e.currentTarget.classList.remove('dragging');
  _spDragSrc = null;
}

function onDrop(e, targetCol) {
  e.preventDefault();
  if (!_spDragSrc) return;

  const { card, issueNum, fromCol } = _spDragSrc;

  if (fromCol === targetCol) {
    // Reorder within sprint column (client-side only)
    return;
  }

  // Optimistic UI: move card immediately
  const targetCards = document.getElementById(`sp-cards-${targetCol}`);
  const fromCards   = document.getElementById(`sp-cards-${fromCol}`);
  if (!targetCards || !fromCards) return;

  // Remove empty placeholder if present
  targetCards.querySelectorAll('.sp-empty').forEach(el => el.remove());

  // Move card
  fromCards.removeChild(card);
  targetCards.appendChild(card);

  // If source is now empty, add empty placeholder
  if (fromCards.querySelectorAll('.sp-card').length === 0) {
    const empty = document.createElement('div');
    empty.className = 'sp-empty';
    empty.textContent = fromCol === 'sprint' ? 'No issues in this sprint' : 'No unassigned issues';
    fromCards.appendChild(empty);
  }

  // Track pending change
  const pendingPrev = _spPendingChanges[issueNum];
  if (pendingPrev && pendingPrev.from === targetCol) {
    // Moving back to original — cancel pending
    delete _spPendingChanges[issueNum];
  } else {
    _spPendingChanges[issueNum] = { from: fromCol, to: targetCol };
  }

  // Update Apply button visibility
  const applyBtn = document.getElementById('sp-apply-btn');
  if (applyBtn) {
    applyBtn.style.display = Object.keys(_spPendingChanges).length > 0 ? '' : 'none';
  }

  // Re-compute footer from current DOM state
  _recomputeFooterFromDOM();

  // Immediately apply (no "Apply" button required for single drags)
  _applyOneDrop(issueNum, targetCol);
}

async function _applyOneDrop(issueNum, targetCol) {
  const sprintVal = targetCol === 'sprint' ? _spSelectedSprint : null;
  const card = document.getElementById(`sp-card-${issueNum}`);
  const fromCol = targetCol === 'sprint' ? 'unassigned' : 'sprint';

  try {
    const res = await fetch('/api/sprint-planning/assign', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ issue: issueNum, sprint: sprintVal }),
    });
    if (!res.ok) throw new Error(await res.text());
    // Success — remove from pending
    delete _spPendingChanges[issueNum];
    const applyBtn = document.getElementById('sp-apply-btn');
    if (applyBtn) applyBtn.style.display = Object.keys(_spPendingChanges).length > 0 ? '' : 'none';
  } catch (e) {
    // Rollback: move card back
    if (card) {
      const targetCards = document.getElementById(`sp-cards-${targetCol}`);
      const fromCards   = document.getElementById(`sp-cards-${fromCol}`);
      if (targetCards && fromCards) {
        targetCards.removeChild(card);
        fromCards.querySelectorAll('.sp-empty').forEach(el => el.remove());
        fromCards.appendChild(card);
        if (targetCards.querySelectorAll('.sp-card').length === 0) {
          const empty = document.createElement('div');
          empty.className = 'sp-empty';
          empty.textContent = targetCol === 'sprint' ? 'No issues in this sprint' : 'No unassigned issues';
          targetCards.appendChild(empty);
        }
      }
      // Flash error on card
      card.classList.add('error-flash');
      let errEl = card.querySelector('.sp-card-error');
      if (!errEl) { errEl = document.createElement('div'); errEl.className = 'sp-card-error'; card.appendChild(errEl); }
      errEl.textContent = 'Failed: ' + e.message;
      setTimeout(() => {
        card.classList.remove('error-flash');
        if (errEl) errEl.remove();
      }, 3000);
    }
    delete _spPendingChanges[issueNum];
    _recomputeFooterFromDOM();
  }
}

function _recomputeFooterFromDOM() {
  const footer = document.getElementById('sp-footer');
  if (!footer || _spSelectedSprint === null) return;

  const sizeHours = { S: 1, M: 2, L: 4, XL: 8 };
  const counts = {};
  let total = 0;

  document.querySelectorAll('#sp-cards-sprint .sp-card').forEach(card => {
    const s = card.dataset.size || 'M';
    counts[s] = (counts[s] || 0) + 1;
    total += (sizeHours[s] || 2);
  });

  if (Object.keys(counts).length === 0) {
    footer.textContent = '';
    return;
  }

  const sizeOrder = ['S', 'M', 'L', 'XL'];
  const parts = sizeOrder.filter(s => counts[s]).map(s => `${counts[s]}${s}`);
  footer.textContent = `Sprint ${_spSelectedSprint} size: ${parts.join(' + ')} · est ${total}h`;
}

// ── Mobile touch/pointer drag ─────────────────────────────────────────────────

(function _initPointerDrag() {
  // We use event delegation on the sp-columns container (added after render)
  document.addEventListener('pointerdown', _spPointerDown, { passive: false });
  document.addEventListener('pointermove', _spPointerMove, { passive: false });
  document.addEventListener('pointerup',   _spPointerUp,   { passive: false });
})();

let _spTouchDrag = null; // { card, issueNum, fromCol, ghost, startX, startY }

function _spPointerDown(e) {
  // Only act on touch/pen on sp-card elements
  if (e.pointerType === 'mouse') return;
  const card = e.target.closest('.sp-card');
  if (!card) return;

  e.preventDefault();
  const rect = card.getBoundingClientRect();

  // Create ghost
  const ghost = card.cloneNode(true);
  ghost.style.cssText = `
    position: fixed; left: ${rect.left}px; top: ${rect.top}px;
    width: ${rect.width}px; opacity: 0.8; pointer-events: none;
    z-index: 9999; box-shadow: 0 4px 16px rgba(0,0,0,.2);
    transition: none;
  `;
  document.body.appendChild(ghost);
  _spDragGhost = ghost;

  _spTouchDrag = {
    card,
    issueNum: Number(card.dataset.issue),
    fromCol: card.closest('.sp-col')?.dataset.col,
    ghost,
    startX: e.clientX,
    startY: e.clientY,
    offsetX: e.clientX - rect.left,
    offsetY: e.clientY - rect.top,
  };
  card.classList.add('dragging');
}

function _spPointerMove(e) {
  if (!_spTouchDrag) return;
  e.preventDefault();
  const { ghost, offsetX, offsetY } = _spTouchDrag;
  ghost.style.left = (e.clientX - offsetX) + 'px';
  ghost.style.top  = (e.clientY - offsetY) + 'px';
}

function _spPointerUp(e) {
  if (!_spTouchDrag) return;
  const { card, issueNum, fromCol, ghost } = _spTouchDrag;

  ghost.style.display = 'none';
  const el = document.elementFromPoint(e.clientX, e.clientY);
  ghost.remove();
  _spDragGhost = null;
  card.classList.remove('dragging');

  const targetCol = el?.closest('.sp-col')?.dataset.col;
  _spTouchDrag = null;

  if (targetCol && targetCol !== fromCol) {
    _spDragSrc = { card, issueNum, fromCol };
    onDrop({ preventDefault: () => {} }, targetCol);
  }
}

// ── SSE handler for sprint_plan_update ────────────────────────────────────────

function _handleSprintPlanSSE() {
  // Re-fetch and re-render if the Sprint Planning tab is active
  if (!document.getElementById('view-sprint-plan').classList.contains('hidden')) {
    loadSprintPlanning();
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
})();
