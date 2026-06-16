(() => {
  // apps/dashboard/static/src/logpanel.js
  var AGENT_NAMES = ["coder", "tester", "reviewer", "documenter", "estimator", "BA"];
  function escapeLogHtml(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  var TOKEN_RE = /(#\d+)|\b(coder|tester|reviewer|documenter|estimator|BA)\b/gi;
  function extractRaw(text) {
    const s = String(text == null ? "" : text).trim();
    if (s.length === 0 || s[0] !== "{")
      return s;
    try {
      const obj = JSON.parse(s);
      if (typeof obj.raw === "string")
        return obj.raw;
    } catch (_) {
    }
    return s;
  }
  function colorizeLogLine2(text, repo) {
    const escaped = escapeLogHtml(extractRaw(text));
    return escaped.replace(TOKEN_RE, function(match, issue, agent) {
      if (issue) {
        const num = issue.slice(1);
        const href = repo ? "https://github.com/" + repo + "/issues/" + num : "#";
        return '<a class="log-chip log-chip--issue" href="' + escapeLogHtml(href) + '" target="_blank" rel="noopener">' + issue + "</a>";
      }
      const key = agent.toLowerCase();
      return '<span class="log-chip log-chip--agent log-agent-' + key + '">' + agent + "</span>";
    });
  }

  // apps/dashboard/static/src/progress-activity.js
  function _e(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function _detectMode(payload) {
    if (payload.mode)
      return payload.mode;
    if (Array.isArray(payload.steps) && payload.steps.length > 0)
      return "stepper";
    if (payload.total != null)
      return "bar";
    return "indeterminate";
  }
  var _STEP_ICON = {
    pending: "\u25CB",
    checking: "\u2026",
    running: "\u25CF",
    done: "\u2713",
    pass: "\u2713",
    fixed: "\u2713",
    failed: "\u2717"
  };
  var _LOG_CLASS = {
    dispatch: "pa-log-dispatch",
    success: "pa-log-success",
    warn: "pa-log-warn",
    fail: "pa-log-fail"
  };
  function _barHtml(p) {
    const done = Number(p.done ?? 0);
    const total = Number(p.total ?? 0);
    const pct = total > 0 ? Math.min(100, Math.round(done / total * 100)) : 0;
    const shimmer = pct < 100 ? '<div class="pa-bar-shimmer"></div>' : "";
    const countsHtml = total > 0 ? `<span class="pa-counts">${done} of ${total}</span>` : "";
    const estHtml = p.est_remaining_minutes != null ? `<span class="pa-est-rem">~${_e(p.est_remaining_minutes)}m remaining</span>` : "";
    return `<div class="pa-bar-track">
    <div class="pa-bar-fill" style="transform:scaleX(${pct / 100})">${shimmer}</div>
  </div>
  <div class="pa-bar-meta">
    <span class="pa-current">${_e(p.current)}</span>
    ${countsHtml}
    ${estHtml}
  </div>`;
  }
  function _stepperHtml(p) {
    const steps = Array.isArray(p.steps) ? p.steps : [];
    const rows = steps.map((s) => {
      const state = _e(s.state || "pending");
      const icon = _STEP_ICON[s.state] || "\u25CB";
      const note = s.note ? `<span class="pa-step-note">${_e(s.note)}</span>` : "";
      return `<div class="pa-step pa-step--${state}" id="pa-step-${_e(s.key || "")}">
      <span class="pa-step-icon" aria-hidden="true">${icon}</span>
      <div class="pa-step-content">
        <span class="pa-step-name">${_e(s.label || s.key || "")}</span>
        ${note}
      </div>
    </div>`;
    }).join("");
    return `<div class="pa-steps">${rows}</div>`;
  }
  function _indeterminateHtml(p) {
    const cur = p.current ? `<span class="pa-current">${_e(p.current)}</span>` : "";
    return `<div class="pa-indeterminate">
    <div class="pa-spinner"></div>
    ${cur}
    <div class="pa-indet-shimmer"></div>
  </div>`;
  }
  function _doneHtml(p) {
    const txt = p.result ? _e(p.result) : "Done";
    return `<div class="pa-done">
    <span class="pa-done-icon" aria-hidden="true">\u2713</span>
    <span class="pa-result">${txt}</span>
  </div>`;
  }
  function _errorHtml(p, opts) {
    const msg = p.error || "An error occurred.";
    const fn = opts.retryFn || "";
    const retryBtn = fn ? `<button class="pa-retry-btn" type="button" onclick="${_e(fn)}()">Retry</button>` : `<button class="pa-retry-btn" type="button">Retry</button>`;
    return `<div class="pa-error">
    <div class="pa-error-msg">${_e(msg)}</div>
    ${retryBtn}
  </div>`;
  }
  function _logLineHtml(line, colorize) {
    if (!line)
      return "";
    if (typeof line === "string") {
      const msg2 = colorize ? colorize(line, "") : _e(line);
      return `<div class="pa-log-line"><span class="pa-log-msg">${msg2}</span></div>`;
    }
    const cls = _LOG_CLASS[line.type] || "";
    const tsHtml = line.timestamp && line.timestamp !== "\u2014" ? `<span class="pa-log-time">${_e(line.timestamp)}</span>` : "";
    const msg = colorize ? colorize(String(line.message || ""), "") : _e(line.message || "");
    return `<div class="pa-log-line${cls ? " " + cls : ""}">${tsHtml}<span class="pa-log-msg">${msg}</span></div>`;
  }
  function _logSectionHtml(p, rootId, opts) {
    const lines = Array.isArray(p.log_tail) ? p.log_tail : [];
    const colorize = opts.colorize || null;
    const collapsed = opts.logCollapsed ? " pa-log-collapsed" : "";
    const streamId = rootId ? ` id="pa-log-stream-${_e(rootId)}"` : "";
    const toggleArg = rootId ? `'${_e(rootId)}'` : "''";
    const agentSlot = opts.logHeaderAgentHtml || "";
    const emptyMsg = '<div class="pa-log-line" style="color:var(--text-sub)">Waiting for log\u2026</div>';
    const linesHtml = lines.length ? lines.map((l) => _logLineHtml(l, colorize)).join("") : emptyMsg;
    return `<div class="pa-log">
    <div class="pa-log-header" onclick="paToggleLog(${toggleArg})" role="button" tabindex="0"
         onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();paToggleLog(${toggleArg})}">
      <span class="pa-log-indicator" aria-hidden="true"></span>
      <span class="pa-log-label">live</span>
      ${agentSlot}
      <button class="pa-log-toggle-btn" type="button" aria-label="Toggle log" tabindex="-1">&#9650;</button>
    </div>
    <div class="pa-log-stream${collapsed}"${streamId}>${linesHtml}</div>
  </div>`;
  }
  function renderProgressActivity2(payload, opts) {
    if (!payload || typeof payload !== "object")
      payload = {};
    opts = opts || {};
    const status = payload.status || "running";
    const mode = _detectMode(payload);
    const rootId = opts.id || "";
    const idAttr = rootId ? ` id="${_e(rootId)}"` : "";
    let bodyHtml;
    if (status === "done") {
      bodyHtml = _doneHtml(payload);
    } else if (status === "error") {
      bodyHtml = _errorHtml(payload, opts);
    } else if (mode === "stepper") {
      bodyHtml = _stepperHtml(payload);
    } else if (mode === "bar") {
      bodyHtml = _barHtml(payload);
    } else {
      bodyHtml = _indeterminateHtml(payload);
    }
    const showLog = !opts.hideLog && status !== "done" && status !== "error" && (status === "running" || Array.isArray(payload.log_tail));
    const logHtml = showLog ? _logSectionHtml(payload, rootId, opts) : "";
    return `<div class="pa-root pa-mode-${_e(mode)} pa-status-${_e(status)}"${idAttr}>${bodyHtml}${logHtml}</div>`;
  }
  function updateProgressActivityLog(rootId, logTail, colorize) {
    if (typeof document === "undefined")
      return;
    const streamEl = document.getElementById("pa-log-stream-" + rootId);
    if (!streamEl)
      return;
    const lines = Array.isArray(logTail) ? logTail : [];
    const emptyMsg = '<div class="pa-log-line" style="color:var(--text-sub)">Waiting for log\u2026</div>';
    streamEl.innerHTML = lines.length ? lines.map((l) => _logLineHtml(l, colorize || null)).join("") : emptyMsg;
    streamEl.scrollTop = streamEl.scrollHeight;
  }
  function paToggleLog(rootId) {
    if (typeof document === "undefined")
      return;
    const el = document.getElementById("pa-log-stream-" + rootId);
    if (el)
      el.classList.toggle("pa-log-collapsed");
  }
  var PA_CSS = `
@keyframes pa-shimmer {
  0%   { left: -40%; }
  100% { left: 110%; }
}
@keyframes pa-spin {
  to { transform: rotate(360deg); }
}
@keyframes pa-pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: .4; }
}

.pa-root {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
}

/* \u2500\u2500 Bar mode \u2500\u2500 */
.pa-bar-track {
  height: 4px;
  background: rgba(22,163,74,.18);
  border-radius: 2px;
  overflow: hidden;
  position: relative;
  margin: 0 16px;
}
.pa-bar-fill {
  height: 100%;
  width: 100%;
  background: var(--green);
  border-radius: 2px;
  transform-origin: left;
  transition: transform .6s ease;
  position: relative;
  overflow: hidden;
}
.pa-bar-shimmer {
  position: absolute;
  top: 0; left: -40%;
  width: 40%; height: 100%;
  background: linear-gradient(90deg, transparent 0%, rgba(255,255,255,.55) 50%, transparent 100%);
  animation: pa-shimmer 1.8s ease-in-out infinite;
}
.pa-bar-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 16px 0;
  font-size: 12px;
  color: var(--text-muted);
  min-height: 22px;
}
.pa-current {
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.pa-counts {
  font-family: 'SF Mono', ui-monospace, 'Cascadia Code', monospace;
  font-weight: 600;
  color: var(--text);
  flex-shrink: 0;
}
.pa-est-rem {
  color: var(--text-sub);
  font-size: 11px;
  flex-shrink: 0;
}

/* \u2500\u2500 Stepper mode \u2500\u2500 */
.pa-steps {
  display: flex;
  flex-direction: column;
  padding: 8px 16px;
}
.pa-step {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 5px 0;
  min-height: 28px;
}
.pa-step-icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  margin-top: 1px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 700;
}
.pa-step--pending  .pa-step-icon { color: var(--text-sub); }
.pa-step--checking .pa-step-icon { color: var(--blue); }
.pa-step--running  .pa-step-icon { color: var(--blue); }
.pa-step--done     .pa-step-icon { color: var(--green); }
.pa-step--pass     .pa-step-icon { color: var(--green); }
.pa-step--fixed    .pa-step-icon { color: var(--amber); }
.pa-step--failed   .pa-step-icon { color: var(--red); }
.pa-step-content {
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.pa-step-name {
  font-size: 13px;
  color: var(--text);
}
.pa-step-note {
  font-size: 11px;
  color: var(--text-sub);
}

/* \u2500\u2500 Indeterminate mode \u2500\u2500 */
.pa-indeterminate {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
}
.pa-spinner {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  border: 2px solid rgba(22,163,74,.2);
  border-top-color: var(--green);
  border-radius: 50%;
  animation: pa-spin .8s linear infinite;
}
.pa-indet-shimmer {
  height: 4px;
  flex: 1;
  background: rgba(22,163,74,.15);
  border-radius: 2px;
  position: relative;
  overflow: hidden;
}
.pa-indet-shimmer::after {
  content: '';
  position: absolute;
  top: 0; left: -40%;
  width: 40%; height: 100%;
  background: linear-gradient(90deg, transparent 0%, rgba(22,163,74,.45) 50%, transparent 100%);
  animation: pa-shimmer 1.8s ease-in-out infinite;
}

/* \u2500\u2500 Done end state \u2500\u2500 */
.pa-done {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
}
.pa-done-icon {
  color: var(--green);
  font-size: 14px;
  flex-shrink: 0;
}
.pa-result {
  font-size: 13px;
  color: var(--text-muted);
}

/* \u2500\u2500 Error end state \u2500\u2500 */
.pa-error {
  padding: 10px 16px;
}
.pa-error-msg {
  font-size: 13px;
  color: var(--red);
  margin-bottom: 8px;
}
.pa-retry-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 12px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  color: var(--red);
  border: 1px solid var(--red);
  background: var(--surface);
  transition: background .12s;
  font-family: inherit;
}
.pa-retry-btn:hover { background: var(--red-bg); }

/* \u2500\u2500 Live-log slot \u2500\u2500 */
.pa-log {
  border-top: 1px solid var(--border);
  background: var(--bg);
}
.pa-log-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 16px;
  font-family: 'SF Mono', ui-monospace, 'Cascadia Code', monospace;
  font-size: 11px;
  color: var(--text-muted);
  cursor: pointer;
  user-select: none;
}
.pa-log-header:focus-visible {
  outline: 2px solid var(--blue);
  outline-offset: -2px;
}
.pa-log-indicator {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--green);
  flex-shrink: 0;
  animation: pa-pulse 2s ease-in-out infinite;
}
.pa-log-label { flex: 1; }
.pa-log-toggle-btn {
  background: none;
  border: none;
  padding: 0 2px;
  font-size: 10px;
  color: var(--text-sub);
  cursor: pointer;
  font-family: inherit;
}
.pa-log-stream {
  max-height: 200px;
  overflow-y: auto;
  font-family: 'SF Mono', ui-monospace, 'Cascadia Code', monospace;
  font-size: 11px;
  line-height: 1.7;
  padding: 0 16px 10px;
}
.pa-log-stream.pa-log-collapsed { display: none; }
.pa-log-line {
  display: flex;
  gap: 6px;
}
.pa-log-time {
  color: var(--text-sub);
  flex-shrink: 0;
}
.pa-log-msg {
  color: var(--text);
  flex: 1;
  word-break: break-all;
}
.pa-log-line.pa-log-dispatch .pa-log-msg { color: var(--blue); }
.pa-log-line.pa-log-success  .pa-log-msg { color: var(--green); }
.pa-log-line.pa-log-warn     .pa-log-msg { color: var(--amber); }
.pa-log-line.pa-log-fail     .pa-log-msg { color: var(--red); }
`;
  var _cssInjected = false;
  function injectProgressActivityCss() {
    if (_cssInjected || typeof document === "undefined")
      return;
    _cssInjected = true;
    const style = document.createElement("style");
    style.dataset.paStyle = "1";
    style.textContent = PA_CSS;
    document.head.appendChild(style);
  }

  // apps/dashboard/static/src/shell/tabs.js
  function switchTab(tab, pushHistory) {
    let _statusDeepLink = false;
    if (tab === "status") {
      tab = "metrics";
      _statusDeepLink = true;
    }
    if (_activeTab === "sprint-mgmt" && tab !== "sprint-mgmt") {
      if (_smgmtLivePollId !== null) {
        clearInterval(_smgmtLivePollId);
        _smgmtLivePollId = null;
      }
      if (_smgmtLogPollId !== null) {
        clearInterval(_smgmtLogPollId);
        _smgmtLogPollId = null;
      }
    }
    if (_activeTab === "logs" && tab !== "logs") {
      logsDestroy();
    }
    if (_activeTab === "metrics" && tab !== "metrics") {
      if (_statusRefreshId !== null) {
        clearInterval(_statusRefreshId);
        _statusRefreshId = null;
      }
    }
    if (_activeTab === "deploy" && tab !== "deploy") {
      deployTabDestroy();
    }
    _activeTab = tab;
    const onGlobalSettings = tab === "global-settings";
    _globalSettingsLinkActive(onGlobalSettings);
    const projHeader = document.getElementById("proj-header");
    if (projHeader)
      projHeader.classList.toggle("hidden", onGlobalSettings);
    const subTabsRow = document.querySelector(".sub-tabs-row");
    if (subTabsRow)
      subTabsRow.classList.toggle("hidden", onGlobalSettings);
    ["sprint-mgmt", "tickets", "logs", "deploy", "bulk-create", "timeline", "compare", "metrics", "est-vs-actual", "calibration", "notes", "roadmap", "advisor", "settings"].forEach((t) => {
      const btn = document.getElementById("stab-" + t);
      if (!btn)
        return;
      const isActive = !onGlobalSettings && t === tab;
      btn.classList.toggle("active", isActive);
      btn.setAttribute("aria-selected", String(isActive));
    });
    closeAllStabDropdowns();
    ["analytics", "more", "planning", "manage"].forEach((groupName) => {
      const group = document.getElementById("stab-group-" + groupName);
      if (!group)
        return;
      const trigger = group.querySelector(".stab-trigger");
      if (trigger)
        trigger.classList.toggle("active", !!group.querySelector(".stab.active"));
    });
    ["sprint-mgmt", "tickets", "logs", "deploy", "bulk-create", "timeline", "compare", "metrics", "est-vs-actual", "calibration", "notes", "roadmap", "advisor", "settings", "global-settings"].forEach((t) => {
      const pane = document.getElementById("pane-" + t);
      if (pane)
        pane.classList.toggle("active", t === tab);
    });
    const newUrl = "/project/" + encodeURIComponent(_slug) + "/" + tab;
    if (pushHistory !== false) {
      window.history.pushState({ slug: _slug, tab }, "", newUrl);
    }
    if (tab === "tickets" && !_ticketsLoaded) {
      _ticketsLoaded = true;
      loadTickets();
    }
    if (tab === "sprint-mgmt") {
      if (_deepLinkSprintSubView())
        _applyDeepLinkSubView();
      else
        _smgmtShowSubView(_smgmtSavedSubView() || "board");
    }
    if (tab === "sprint-mgmt" && !_sprintMgmtLoaded && _cachedFullRepo[_slug]) {
      _sprintMgmtLoaded = true;
      loadSprintMgmt().then(() => _smgmtArInit());
      _histLoadLedger(_cachedFullRepo[_slug]);
    } else if (tab === "sprint-mgmt" && _sprintMgmtLoaded) {
      if (_arTickerId === null && _arInterval > 0)
        _smgmtArStartTicker();
    }
    if (tab === "bulk-create") {
      _bcInitTab();
      _lpRenderBc();
    }
    if (tab === "logs")
      logsInit();
    if (tab === "deploy")
      deployTabInit();
    if (tab === "timeline")
      ganttInit();
    if (tab === "compare")
      compareInit();
    if (tab === "metrics") {
      metricsInit();
      if (_statusDeepLink && typeof window.anlShowTab === "function") {
        window.anlShowTab("status");
      }
    }
    if (tab === "est-vs-actual")
      evaInit();
    if (tab === "calibration")
      calibInit();
    if (tab === "notes")
      notesInit();
    if (tab === "roadmap")
      roadmapInit();
    if (tab === "advisor")
      advInit();
    if (tab === "settings")
      projSettingsInit();
    if (tab === "global-settings") {
      settingsInitValues();
      settingsPopulateRepos();
      globalSettingsLoad();
    }
    if (typeof window._smgmtUpdateSelectionUI === "function")
      window._smgmtUpdateSelectionUI();
    if (typeof window._bulkUpdateActionBar === "function")
      window._bulkUpdateActionBar();
    if (typeof window._smgmtUpdateToolbarTop === "function")
      window._smgmtUpdateToolbarTop();
  }
  function toggleStabDropdown(name, e) {
    e.stopPropagation();
    const group = document.getElementById("stab-group-" + name);
    const isOpen = group.classList.contains("open");
    closeAllStabDropdowns();
    if (!isOpen)
      group.classList.add("open");
  }
  function closeAllStabDropdowns() {
    document.querySelectorAll(".stab-group.open").forEach((g) => g.classList.remove("open"));
  }
  document.addEventListener("click", closeAllStabDropdowns);
  var _subTabsEl = document.getElementById("sub-tabs");
  if (_subTabsEl) {
    _subTabsEl.addEventListener("keydown", function(e) {
      const enabledTabs = ["sprint-mgmt", "tickets", "manage", "logs", "deploy", "metrics", "planning", "roadmap", "advisor", "settings"];
      const focused = document.activeElement;
      const currentId = focused ? focused.id.replace("stab-", "") : null;
      const currentIdx = enabledTabs.indexOf(currentId);
      if (currentIdx < 0)
        return;
      if (e.key === "ArrowRight") {
        e.preventDefault();
        const next = enabledTabs[(currentIdx + 1) % enabledTabs.length];
        document.getElementById("stab-" + next).focus();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        const prev = enabledTabs[(currentIdx - 1 + enabledTabs.length) % enabledTabs.length];
        document.getElementById("stab-" + prev).focus();
      } else if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        if (currentId)
          switchTab(currentId);
      }
    });
  }
  window.addEventListener("popstate", function(e) {
    const { slug, tab, view, filter } = parseUrl();
    const effSlug = slug || e.state && e.state.slug;
    const effTab = (slug ? tab : e.state && e.state.tab) || "sprint-mgmt";
    if (!effSlug)
      return;
    if (effSlug !== _slug) {
      _ticketsRepo = null;
      _ticketsLoaded = false;
    }
    _slug = effSlug;
    _deepLinkView = view;
    _deepLinkFilter = filter;
    _evlState.errorsOnly = filter === "errors";
    switchTab(effTab, false);
  });

  // apps/dashboard/static/src/sprint-board/state.js
  globalThis._rrLabel ??= null;
  globalThis._rrVersionedLabel ??= null;
  globalThis._fsLabel ??= null;
  globalThis._fsPreview ??= null;
  globalThis._fsActiveJob ??= null;
  globalThis._bcLabel ??= null;
  globalThis._bcPreview ??= null;
  globalThis._pfCurrentLabel ??= null;
  globalThis._pfCurrentRepo ??= null;
  globalThis._pfState ??= "idle";
  globalThis._pfDagData ??= null;
  globalThis._pfWarnings ??= null;
  globalThis._pfCycle ??= null;
  globalThis._pfFlags ??= null;
  globalThis._pfSelectedIds ??= /* @__PURE__ */ new Set();
  globalThis._pfUseClineFollowups ??= false;
  globalThis._smgmtMoveLock ??= false;
  globalThis._smgmtGhostNextNum ??= null;

  // apps/dashboard/static/src/sprint-board/plan-next.js
  async function _planNextRequest(repo, replace) {
    let res;
    try {
      res = await fetch("/api/sprints/plan-next", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: repo, replace })
      });
    } catch (e) {
      _smgmtShowToast("Plan next sprint failed: " + e.message);
      return;
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      _smgmtShowToast("Plan next sprint failed: " + (data.detail || "HTTP " + res.status));
      return;
    }
    switch (data.status) {
      case "ok":
        await loadSprintMgmt();
        _smgmtShowToast(
          `Planned ${data.sprint_label} \xB7 ${(data.tickets || []).length} tickets (${data.total_minutes}m) \u2014 pending sign-off`
        );
        break;
      case "no_milestone":
        _smgmtShowToast("No active milestone \u2014 nothing to plan.");
        break;
      case "empty":
        _smgmtShowToast(data.reason || "No eligible tickets to plan.");
        break;
      case "conflict":
        if (window.confirm(
          `${data.reason}

Replace the existing draft (${data.existing_label})?`
        )) {
          await _planNextRequest(repo, true);
        }
        break;
      default:
        _smgmtShowToast("Plan next sprint: unexpected response.");
    }
  }
  async function smgmtPlanNextSprint() {
    const repo = _smgmtRepo();
    if (!repo) {
      _smgmtShowToast("No project selected.");
      return;
    }
    const btn = document.getElementById("smgmt-plan-next-btn");
    if (btn) {
      btn.disabled = true;
      btn.classList.add("is-loading");
    }
    try {
      await _planNextRequest(repo, false);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.classList.remove("is-loading");
      }
    }
  }
  async function _smgmtLoadPendingSignoff() {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    let labels = [];
    try {
      const res = await fetch(
        `/api/sprints/pending-signoff?project=${encodeURIComponent(repo)}`
      );
      if (!res.ok)
        return;
      const data = await res.json();
      labels = data.labels || [];
    } catch {
      return;
    }
    for (const label of labels) {
      const card = document.getElementById(`smgmt-card-${label}`);
      if (!card)
        continue;
      card.classList.add("smgmt-pending-signoff");
      if (card.querySelector(".smgmt-pending-signoff-badge"))
        continue;
      const header = card.querySelector(".smgmt-sprint-header, .sc-header");
      if (!header)
        continue;
      const badge = document.createElement("span");
      badge.className = "smgmt-pending-signoff-badge";
      badge.textContent = "Pending sign-off";
      badge.setAttribute("title", "Awaiting sign-off before this sprint goes live");
      header.appendChild(badge);
    }
  }

  // apps/dashboard/static/src/sprint-board/scheduled-run.js
  var _schedMap = {};
  function _smgmtSchedToggleHtml2(label) {
    const on = !!_schedMap[label];
    const id = `sched-toggle-${label}`;
    return `<label class="smgmt-sched-toggle" title="Auto-run this sprint at the project's scheduled time">
    <input type="checkbox" id="${escHtml(id)}" ${on ? "checked" : ""}
      onchange="smgmtToggleRunOnSchedule('${escHtml(label)}', this)"
      aria-label="Run sprint ${escHtml(label)} on schedule">
    <span>Run on schedule</span>
  </label>`;
  }
  async function smgmtToggleRunOnSchedule(label, el) {
    const repo = typeof _smgmtRepo === "function" ? _smgmtRepo() : null;
    if (!repo)
      return;
    const enabled = !!(el && el.checked);
    _schedMap[label] = enabled;
    try {
      const res = await fetch("/api/scheduler/sprints", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: repo, sprint_label: label, enabled })
      });
      if (!res.ok)
        throw new Error(`HTTP ${res.status}`);
    } catch (e) {
      _schedMap[label] = !enabled;
      if (el)
        el.checked = !enabled;
      if (typeof _smgmtShowToast === "function") {
        _smgmtShowToast("Could not update schedule: " + (e.message || e));
      }
    }
  }
  async function _smgmtHydrateSchedToggles2(repo) {
    if (!repo)
      return;
    try {
      const res = await fetch(`/api/scheduler/sprints?project=${encodeURIComponent(repo)}`);
      if (!res.ok)
        return;
      const data = await res.json();
      const map = data.run_on_schedule || {};
      for (const k of Object.keys(_schedMap))
        delete _schedMap[k];
      Object.keys(map).forEach((k) => {
        _schedMap[k] = !!map[k];
      });
      Object.keys(map).forEach((label) => {
        const cb = document.getElementById(`sched-toggle-${label}`);
        if (cb)
          cb.checked = !!map[label];
      });
    } catch (_) {
    }
  }

  // apps/dashboard/static/src/sprint-board/history.js
  var _HIST_ACTION_STATES = /* @__PURE__ */ new Set(["ready_to_merge", "needs_rework", "failed"]);
  function _histNeedsActionCount() {
    return (_histLedgerData || []).reduce(
      (acc, s) => acc + (_HIST_ACTION_STATES.has(s && s.lifecycle_state) ? 1 : 0),
      0
    );
  }
  var _histLedgerData = [];
  globalThis._histLedgerData = _histLedgerData;
  var _histExpanded = /* @__PURE__ */ new Set();
  var _histDidAutoExpand = false;
  var _histFoldSize = 10;
  var _histFoldExpanded = /* @__PURE__ */ new Set();
  var _histStaleBySprint = {};
  var _histRunStats = {};
  function _histIsLocked(state) {
    const s = (state || "").toLowerCase();
    return s === "finished" || s === "deleted" || s === "completed";
  }
  function _histIsChild(label) {
    return /^sprint-\d+\.\d+/.test(label || "");
  }
  function _histFmtSecs(secs) {
    if (secs == null || isNaN(secs))
      return "\u2014";
    secs = Math.round(secs);
    if (secs < 60)
      return secs + "s";
    const m = Math.floor(secs / 60), s = secs % 60;
    if (m < 60)
      return s ? `${m}m ${s}s` : `${m}m`;
    const h = Math.floor(m / 60), mm = m % 60;
    return mm ? `${h}h ${mm}m` : `${h}h`;
  }
  function _histFmtTokens(n) {
    if (n == null || isNaN(n))
      return "0";
    if (n >= 1e6)
      return (n / 1e6).toFixed(1) + "M";
    if (n >= 1e3)
      return (n / 1e3).toFixed(1) + "k";
    return String(n);
  }
  function _histIssueChip(iss) {
    const st = (iss.state || "").toLowerCase();
    if (iss.failure_reason || (iss.agent_status || "").toLowerCase() === "failed") {
      return { cls: "crashed", label: "CRASHED \xB7 in-progress" };
    }
    if (st === "merged")
      return { cls: "merged", label: "MERGED" };
    if (st === "closed")
      return { cls: "crashed", label: "CRASHED" };
    if (iss.time_spent != null)
      return { cls: "uat", label: "OPEN \xB7 UAT" };
    return { cls: "notrun", label: "NOT RUN" };
  }
  function _histIssueIcon(iss) {
    const chip = _histIssueChip(iss);
    if (chip.cls === "merged") {
      return '<span class="iss-icon ok"><i class="ti ti-check"></i></span>';
    }
    if (chip.cls === "crashed") {
      return '<span class="iss-icon fail"><i class="ti ti-x"></i></span>';
    }
    return '<span class="iss-icon idle"></span>';
  }
  function _histIssueTitle(iss, s) {
    if (iss.title)
      return String(iss.title);
    try {
      const tickets = s && s.label && _smgmtBySprint[s.label] || [];
      const hit = tickets.find((t) => String(t.number) === String(iss.ticket_id));
      if (hit && hit.title)
        return String(hit.title);
    } catch (_) {
    }
    return "";
  }
  function _histIssueRowHtml(iss, isChild, s) {
    const chip = _histIssueChip(iss);
    const rerun = iss.is_rerun || iss.rerun || isChild;
    const arrow = rerun ? '<span class="iss-rerun">\u21B3</span> ' : "";
    const num = iss.ticket_id;
    const id = num != null ? "#" + num : "#?";
    const titleText = _histIssueTitle(iss, s);
    const title = titleText ? `<span class="iss-title">${escHtml(titleText)}</span>` : "";
    const repo = typeof _histRepo === "function" ? _histRepo(s) : "";
    const clickable = num != null && repo ? ` role="link" tabindex="0" title="Open #${escHtml(String(num))} on GitHub" onclick="event.stopPropagation();window.open('https://github.com/${escHtml(repo)}/issues/${escHtml(String(num))}','_blank','noopener')"` : "";
    const cls = "iss-row" + (clickable ? " iss-row-link" : "");
    return `<div class="${cls}"${clickable}>
    ${_histIssueIcon(iss)}
    <span class="iss-id">${arrow}${escHtml(String(id))}</span>
    ${title}
    <span class="iss-chip ${chip.cls}">${chip.label}</span>
    <span class="iss-time">${escHtml(_histFmtSecs(iss.time_spent))}</span>
  </div>`;
  }
  function _histSprintFailed(s) {
    const st = (s.lifecycle_state || "").toLowerCase();
    if (st === "failed")
      return true;
    if (st !== "needs_rework")
      return false;
    const er = (s.end_reason || "").toLowerCase();
    if (er === "natural" || er === "merge_sprint")
      return false;
    const failed = Array.isArray(s.failed_tickets) ? s.failed_tickets : [];
    if (failed.length)
      return true;
    const issues = Array.isArray(s.issues) ? s.issues : [];
    if (issues.length && issues.every(
      (i) => (i.state || "").toLowerCase() === "merged" || (i.agent_status || "").toLowerCase() === "completed"
    ))
      return false;
    return true;
  }
  function _histFocusLabel(label) {
    if (!label)
      return;
    _histExpanded.add(label);
    _histRenderLedger(_histLedgerData);
    const el = document.querySelector(`.hist-card[data-label="${CSS.escape(label)}"]`);
    if (el)
      el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  function _histRepo(s) {
    const cached = _cachedFullRepo[_slug];
    if (cached)
      return cached;
    const p = s && s.project ? String(s.project) : "";
    return p.includes("/") ? p : "";
  }
  function _histPrUrl(s) {
    if (s.pr_number == null)
      return "";
    const repo = _histRepo(s);
    if (!repo)
      return "";
    return `https://github.com/${repo}/pull/${s.pr_number}`;
  }
  function _histSummaryIssueUrl(s) {
    if (s.summary_issue_url)
      return s.summary_issue_url;
    if (s.summary_issue_num == null)
      return "";
    const repo = _histRepo(s);
    if (!repo)
      return "";
    return `https://github.com/${repo}/issues/${s.summary_issue_num}`;
  }
  function _histSummaryUrl(s) {
    return _histSummaryIssueUrl(s);
  }
  function _histLogsUrl(s) {
    return `/project/${encodeURIComponent(_slug)}/logs?sprint=${encodeURIComponent(s.label || "")}`;
  }
  function _histLinksHtml(s) {
    let html = "";
    const pr = _histPrUrl(s);
    if (pr) {
      html += `<a class="hist-link link-pr" href="${escHtml(pr)}" target="_blank" rel="noopener">
               <i class="ti ti-git-pull-request"></i> PR #${s.pr_number}</a>`;
    }
    const sum = _histSummaryUrl(s);
    if (sum) {
      const sumLabel = s.summary_issue_num ? `#${s.summary_issue_num} Summary` : "Summary";
      html += `<a class="hist-link link-sum" href="${escHtml(sum)}" target="_blank" rel="noopener">
               <i class="ti ti-file-description"></i> ${escHtml(sumLabel)}</a>`;
    }
    html += `<a class="hist-link link-logs" href="${escHtml(_histLogsUrl(s))}">
             <i class="ti ti-list-details"></i> Logs</a>`;
    return `<div class="hist-links">${html}</div>`;
  }
  function _histStateChip(state, sprint) {
    const s = (state || "unknown").toLowerCase();
    const er = sprint && sprint.end_reason ? String(sprint.end_reason).toLowerCase() : "";
    const displayState = s === "needs_rework" && (er === "natural" || er === "merge_sprint") && sprint && !_histSprintFailed(sprint) ? "ready_to_merge" : s;
    const map = {
      completed: ["completed", "Completed"],
      ready_to_merge: ["completed", "Ready to merge"],
      needs_rework: ["failed", "Failed"],
      partial_finished: ["partial", "Partial"],
      deleted: ["deleted", "Deleted"],
      running: ["running", "Running"],
      draft: ["planning", "Draft"],
      planned: ["planning", "Planned"],
      finished: ["finished", "Finished"],
      failed: ["failed", "Failed"],
      cancelled: ["failed", "Failed"],
      planning: ["planning", "Draft"]
    };
    const pair = map[displayState] || ["unknown", displayState];
    const reason = sprint && sprint.end_reason && _histSprintFailed(sprint) ? `<span class="hist-state-reason" title="${escHtml(String(sprint.end_reason))}">${escHtml(String(sprint.end_reason))}</span>` : "";
    return `<span class="hist-state ${pair[0]}">${pair[1]}</span>${reason}`;
  }
  function _histStatChip(icon, label, value, cls) {
    const prefix = label ? escHtml(label) + " " : "";
    return `<span class="stat-chip${cls ? " " + cls : ""}">
    <i class="ti ${icon}"></i>${prefix}<b>${escHtml(value)}</b></span>`;
  }
  function _histSplitBarHtml(stats) {
    const split = Array.isArray(stats.split) ? stats.split : [];
    if (!split.length)
      return "";
    const segs = split.map(
      (seg) => `<span class="split-seg split-seg--${escHtml(seg.agent)}" style="width:${seg.pct}%"
       title="${escHtml(seg.agent)} \xB7 ${seg.pct}% (${escHtml(_histFmtSecs(seg.seconds))})">${seg.pct}%</span>`
    ).join("");
    const legend = split.map(
      (seg) => `<span class="split-legend-item"><span class="split-swatch split-swatch--${escHtml(seg.agent)}"></span>
       ${escHtml(seg.agent)} ${seg.pct}%</span>`
    ).join("");
    return `<div class="stats-block">
    <div class="stats-section-label">Agent time split</div>
    <div class="split-bar">${segs}</div>
    <div class="split-legend">${legend}</div>
  </div>`;
  }
  function _histShouldAutoExpand(s) {
    if (!s || !s.label)
      return false;
    const st = (s.lifecycle_state || "").toLowerCase();
    if (_histIsLocked(st))
      return false;
    return st === "needs_rework" || st === "failed" || st === "ready_to_merge" || st === "running";
  }
  function _histAutoExpandRecent(groups) {
    const _expand = (s) => {
      if (!_histShouldAutoExpand(s))
        return;
      _histExpanded.add(s.label);
      _histLoadRunStats(s.label);
    };
    for (let i = 0; i < groups.length && i < _histFoldSize; i++) {
      const g = groups[i];
      const children = g.children || [];
      if (children.length) {
        _expand(children[children.length - 1]);
      } else {
        _expand(g.baseSprint);
      }
    }
  }
  function _histGanttHtml(s, stats) {
    const tickets = Array.isArray(stats.tickets) ? stats.tickets : [];
    if (!tickets.length)
      return "";
    const scale = Math.max(1, stats.wall_seconds || 0);
    const sprintFailed = _histSprintFailed(s);
    const crash = stats.crash;
    const rows = tickets.map((t) => {
      const segs = (t.segments || []).map((seg) => {
        const left = seg.start / scale * 100;
        const width = seg.duration / scale * 100;
        const agentCls = seg.agent === "tester" ? "g-tester" : "g-coder";
        const cls = "g-seg " + agentCls + (seg.fix_round ? " g-fix" : "");
        const title = `${seg.agent}${seg.fix_round ? " (fix round)" : ""} \xB7 ${_histFmtSecs(seg.duration)}`;
        return `<span class="${cls}" style="left:${left}%;width:${width}%" title="${escHtml(title)}"></span>`;
      }).join("");
      let marker = "";
      if (sprintFailed && crash && crash.ticket === t.ticket) {
        const at = crash.offset / scale * 100;
        marker = `<span class="g-crash" style="left:${at}%" title="Crashed here">\u2715</span>`;
      }
      return `<div class="g-row">
      <span class="g-label">#${escHtml(String(t.ticket))}</span>
      <span class="g-track">${segs}${marker}</span>
    </div>`;
    }).join("");
    return `<div class="stats-block">
    <div class="stats-section-label">Timeline</div>
    <div class="hist-gantt"><div class="gantt">${rows}</div></div>
  </div>`;
  }
  function _histStatsHtml(s) {
    const stats = _histRunStats[s.label];
    const hasRuns = !!(stats && stats.has_runs);
    const wall = hasRuns && stats.wall_seconds != null ? stats.wall_seconds : s.duration;
    const tokens = hasRuns && stats.total_tokens != null ? stats.total_tokens : s.tokens;
    const sprintFailed = _histSprintFailed(s);
    const chips = [];
    if (wall != null)
      chips.push(_histStatChip("ti-clock", "wall", _histFmtSecs(wall)));
    if (hasRuns) {
      chips.push(_histStatChip("ti-robot", "agent time", _histFmtSecs(stats.agent_total_seconds)));
    }
    if (tokens != null) {
      let tokVal = _histFmtTokens(tokens);
      if (hasRuns && stats.token_cost_usd != null)
        tokVal += " \u2248 $" + Number(stats.token_cost_usd).toFixed(2);
      else
        tokVal += " tok";
      chips.push(_histStatChip("ti-coin", "tokens", tokVal));
    }
    if (hasRuns) {
      if (stats.fix_round_count > 0) {
        const refs = (stats.fix_round_tickets || []).map((n) => "#" + n).join(", ");
        const word = stats.fix_round_count === 1 ? "fix round" : "fix rounds";
        chips.push(_histStatChip(
          "ti-refresh",
          "",
          stats.fix_round_count + " " + word + (refs ? " (" + refs + ")" : ""),
          "stat-chip--fix"
        ));
      }
      if (stats.slowest_ticket) {
        chips.push(_histStatChip(
          "ti-hourglass-low",
          "slowest",
          "#" + stats.slowest_ticket.ticket + " \xB7 " + _histFmtSecs(stats.slowest_ticket.seconds)
        ));
      }
      if (stats.parallel_saved_seconds != null) {
        chips.push(_histStatChip(
          "ti-arrows-split",
          "parallel saved",
          "~" + _histFmtSecs(stats.parallel_saved_seconds)
        ));
      }
      if (stats.coder_backend_split && stats.coder_backend_split.cline_count > 0) {
        const bs = stats.coder_backend_split;
        const parts = [];
        if (bs.cline_count > 0)
          parts.push("cline: " + bs.cline_count + " \xB7 " + _histFmtSecs(bs.cline_seconds));
        if (bs.claude_code_count > 0)
          parts.push("claude-code: " + bs.claude_code_count + " \xB7 " + _histFmtSecs(bs.claude_code_seconds));
        if (parts.length)
          chips.push(_histStatChip("ti-server", "backend", parts.join(" | ")));
      }
      if (sprintFailed && stats.crash) {
        const failed = (Array.isArray(s.failed_tickets) ? s.failed_tickets : []).find((ft) => ft.ticket_id === stats.crash.ticket);
        const reason = failed ? String(failed.failure_reason || "") : "";
        const crashAgent = /tester/i.test(reason) ? "tester" : "coder";
        const tail = reason ? " \xB7 " + reason.split("\n")[0].slice(0, 40) : "";
        chips.push(_histStatChip(
          "ti-alert-triangle",
          "crash",
          "#" + stats.crash.ticket + " \xB7 " + crashAgent + tail,
          "stat-chip--crash"
        ));
      }
    }
    const splitHtml = hasRuns ? _histSplitBarHtml(stats) : "";
    const ganttHtml = hasRuns ? _histGanttHtml(s, stats) : "";
    return `<div class="stats" data-stats-label="${escHtml(s.label || "")}">
    <div class="stat-chips">${chips.join("")}</div>
    ${splitHtml}
    ${ganttHtml}
  </div>`;
  }
  async function _histLoadRunStats(label) {
    if (label in _histRunStats)
      return;
    const repo = _cachedFullRepo[_slug];
    try {
      const url = "/api/sprints/" + encodeURIComponent(label) + "/run-stats" + (repo ? "?project=" + encodeURIComponent(repo) : "");
      const resp = await fetch(url);
      if (!resp.ok)
        return;
      _histRunStats[label] = await resp.json();
      _histRenderLedger(_histLedgerData);
    } catch (_) {
    }
  }
  function _histReconcileLabel(name) {
    return {
      summary_issue: "Summary issue",
      sprint_pr: "Sprint PR",
      stale_labels: "Status labels"
    }[name] || name;
  }
  function _histIssueUrl(num) {
    if (num == null)
      return "";
    const repo = _cachedFullRepo[_slug] || "";
    if (!repo)
      return "";
    return `https://github.com/${repo}/issues/${num}`;
  }
  function _histPostSprintHtml(s) {
    const ps = s.post_sprint;
    if (!ps)
      return "";
    const doc = ps.documenter;
    const rev = ps.reviewer;
    const docRan = doc && doc.status && doc.status !== "skipped";
    const revRan = rev && rev.status && rev.status !== "skipped";
    if (!docRan && !revRan)
      return "";
    let rows = "";
    if (doc) {
      let body = "";
      if (doc.status === "skipped") {
        body = '<span class="ps-skipped">Skipped \u2014 nothing merged</span>';
      } else if (doc.status === "failed") {
        body = '<span class="ps-skipped">Documenter failed</span>';
      } else if ((doc.files_touched || []).length) {
        body = (doc.files_touched || []).map(
          (f) => `<code class="ps-file">${escHtml(String(f))}</code>`
        ).join("");
        if (doc.commit_sha) {
          body += `<div class="ps-meta">Commit ${escHtml(String(doc.commit_sha).slice(0, 8))}</div>`;
        }
      } else if (doc.status === "succeeded") {
        body = '<span class="ps-skipped">No doc files changed</span>';
      }
      if (body) {
        rows += `<div class="ps-row"><span class="ps-label">Documenter</span><span class="ps-body">${body}</span></div>`;
      }
    }
    if (rev) {
      let body = "";
      if (rev.status === "skipped") {
        body = '<span class="ps-skipped">Skipped</span>';
      } else if (rev.status === "failed") {
        body = '<span class="ps-skipped">Reviewer failed</span>';
      } else {
        const tickets = rev.follow_up_tickets || [];
        if (tickets.length) {
          body = tickets.map((n) => {
            const url = _histIssueUrl(n);
            const inner = `#${n}`;
            return url ? `<a class="ps-ticket" href="${escHtml(url)}" target="_blank" rel="noopener">${escHtml(inner)}</a>` : `<span class="ps-ticket">${escHtml(inner)}</span>`;
          }).join("");
        } else {
          body = '<span class="ps-skipped">No follow-up tickets opened</span>';
        }
        const counts = [];
        if (rev.blockers)
          counts.push(rev.blockers + " blocker" + (rev.blockers !== 1 ? "s" : ""));
        if (rev.suggestions)
          counts.push(rev.suggestions + " suggestion" + (rev.suggestions !== 1 ? "s" : ""));
        if (rev.nits)
          counts.push(rev.nits + " nit" + (rev.nits !== 1 ? "s" : ""));
        if (counts.length)
          body += `<div class="ps-meta">${escHtml(counts.join(" \xB7 "))}</div>`;
        if (rev.comment_url) {
          body += `<div class="ps-meta"><a href="${escHtml(rev.comment_url)}" target="_blank" rel="noopener">Review comment \u2197</a></div>`;
        }
      }
      if (body) {
        rows += `<div class="ps-row"><span class="ps-label">Reviewer</span><span class="ps-body">${body}</span></div>`;
      }
    }
    if (!rows)
      return "";
    const note = ps.note || "Agents ran after ticket work finished";
    return `<div class="hist-post-sprint">
    <div class="ps-head"><i class="ti ti-clock-play"></i> ${escHtml(note)}</div>
    ${rows}
  </div>`;
  }
  function _histLooseEndBandHtml(s) {
    const r = s.reconciliation;
    if (r && Array.isArray(r.checks)) {
      const staleCheck = r.checks.find((c) => !c.ok && c.name === "stale_labels");
      if (staleCheck) {
        const detail = staleCheck.detail || "Clear stale status labels";
        return `<div class="hist-loose-end-band">
        <i class="ti ti-tag"></i>
        <span class="hist-band-msg">${escHtml(detail)}</span>
      </div>`;
      }
    }
    const g = _histStaleBySprint[s.label];
    if (g && g.count) {
      const n = g.count;
      const word = n === 1 ? "stale branch" : "stale branches";
      const lbl = escHtml(s.label || "");
      return `<div class="hist-loose-end-band">
      <i class="ti ti-git-branch"></i>
      <span class="hist-band-msg">${n} ${word}</span>
      <button type="button" class="hist-band-cta"
        onclick="event.stopPropagation();_histCleanupStale('${lbl}')">Clean up</button>
    </div>`;
    }
    return "";
  }
  function _histWhatListHtml(s) {
    if (_histIsLocked(s.lifecycle_state))
      return "";
    const state = (s.lifecycle_state || "").toLowerCase();
    if (_histSprintFailed(s)) {
      const failed = Array.isArray(s.failed_tickets) ? s.failed_tickets : [];
      const sprintReason = s.failure_reason || s.end_reason;
      if (!failed.length && !sprintReason)
        return "";
      const items = failed.map((ft) => {
        const id = ft.ticket_id != null ? "#" + ft.ticket_id : "#?";
        const reason = ft.failure_reason || "Agent failed";
        const ts = ft.failed_at || ft.timestamp || "";
        const tsHtml = ts ? `<span class="wl-ts">${escHtml(String(ts).slice(0, 16).replace("T", " "))}</span>` : "";
        const logHref = escHtml(_histLogsUrl(s));
        return `<div class="wl-item">
        <span class="wl-id">${escHtml(String(id))}</span>
        <span class="wl-reason">${escHtml(String(reason))}</span>
        ${tsHtml}
        <a class="wl-log" href="${logHref}" onclick="event.stopPropagation()" title="View logs">log</a>
      </div>`;
      }).join("");
      const summary = !failed.length && sprintReason ? `<div class="wl-item"><span class="wl-reason">${escHtml(String(sprintReason))}</span></div>` : "";
      return `<div class="hist-what-list">
      <div class="hist-what-head"><i class="ti ti-x"></i> Why it failed</div>
      ${items}${summary}
    </div>`;
    }
    if (state === "partial_finished" || state === "needs_rework") {
      const issues = Array.isArray(s.issues) ? s.issues : [];
      const unfinished = issues.filter((i) => (i.state || "").toLowerCase() !== "merged");
      if (!unfinished.length)
        return "";
      const n = unfinished.length;
      const m = issues.length;
      const isChild = _histIsChild(s.label);
      return `<div class="hist-what-list">
      <div class="hist-what-head">Unfinished ${n} of ${m}</div>
      <div class="iss-list">${unfinished.map((i) => _histIssueRowHtml(i, isChild, s)).join("")}</div>
    </div>`;
    }
    return "";
  }
  function _histReconPassedHtml(s) {
    const r = s.reconciliation;
    if (!r || !Array.isArray(r.checks) || !r.checks.length)
      return "";
    const passed = r.checks.filter((c) => !!c.ok);
    if (!passed.length)
      return "";
    const items = passed.map((c) => {
      const detail = c.detail || "OK";
      return `<span class="recon-passed-item"
      title="${escHtml(_histReconcileLabel(c.name) + ": " + detail)}">
      <i class="ti ti-check"></i> ${escHtml(_histReconcileLabel(c.name))}</span>`;
    }).join("");
    return `<div class="hist-recon-passed">${items}</div>`;
  }
  var _histDetailsExpanded = /* @__PURE__ */ new Set();
  globalThis._histDetailsExpanded = _histDetailsExpanded;
  function _histDetailsHtml(s) {
    const detExpanded = _histDetailsExpanded.has(s.label);
    const lbl = escHtml(s.label || "");
    const chev = detExpanded ? "ti-chevron-down" : "ti-chevron-right";
    const body = detExpanded ? `<div class="hist-details-body">
      ${_histStatsHtml(s)}
      ${_histPostSprintHtml(s)}
      ${_histReconPassedHtml(s)}
    </div>` : "";
    return `<div class="hist-details${detExpanded ? " expanded" : ""}">
    <div class="hist-details-head" onclick="event.stopPropagation();_histToggleDetails('${lbl}')">
      <i class="ti ${chev} hist-chev"></i> Details
    </div>
    ${body}
  </div>`;
  }
  function _histRecoveryBtnHtml(s) {
    if (_histIsLocked(s.lifecycle_state))
      return "";
    const state = (s.lifecycle_state || "").toLowerCase();
    const lbl = escHtml(s.label || "");
    const rawLabel = s.label || "";
    if (_histSprintFailed(s) || state === "needs_rework" || state === "failed" || state === "cancelled") {
      const rerunDisabled = _smgmtAnySprintRunning ? "disabled" : "";
      const rerunTitle = _smgmtAnySprintRunning ? 'title="Cannot re-run: another sprint is currently running."' : "";
      const childDisplay = sprintLabelDisplay(_histNextChildLabel(rawLabel)).replace("Sprint ", "");
      return `<button type="button" class="hist-head-btn hist-head-btn--rerun" ${rerunDisabled} ${rerunTitle}
      onclick="event.stopPropagation();_histRerunSprint('${lbl}')">
      <i class="ti ti-refresh"></i> Re-run \u2192 ${escHtml(childDisplay)}</button>`;
    }
    if (state === "ready_to_merge") {
      return `<button type="button" class="hist-head-btn hist-head-btn--finish"
      onclick="event.stopPropagation();smgmtFinishSprint('${lbl}')">
      <i class="ti ti-flag-check"></i> Merge</button>`;
    }
    return "";
  }
  function _histDeleteBtnHtml(s) {
    if (_histIsLocked(s.lifecycle_state))
      return "";
    const state = (s.lifecycle_state || "").toLowerCase();
    const actionable = /* @__PURE__ */ new Set([
      "needs_rework",
      "failed",
      "cancelled",
      "ready_to_merge",
      "completed",
      "partial_finished"
    ]);
    if (!actionable.has(state))
      return "";
    const lbl = escHtml(s.label || "");
    return `<button type="button" class="hist-head-btn hist-head-btn--delete-icon"
    onclick="event.stopPropagation();smgmtDeleteSprint('${lbl}')"
    title="Delete sprint"><i class="ti ti-trash"></i></button>`;
  }
  function _histSecondaryLinksHtml(s) {
    let html = "";
    const pr = _histPrUrl(s);
    if (pr) {
      html += `<a class="hist-head-btn hist-head-btn--secondary link-pr"
      href="${escHtml(pr)}" target="_blank" rel="noopener"
      onclick="event.stopPropagation()" title="Open sprint PR">
      <i class="ti ti-git-pull-request"></i> PR #${s.pr_number}</a>`;
    }
    const sum = _histSummaryUrl(s);
    if (sum) {
      const sumLabel = s.summary_issue_num ? `#${s.summary_issue_num}` : "Summary";
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
  function _histCardHtml(s, opts) {
    opts = opts || {};
    const locked = _histIsLocked(s.lifecycle_state);
    const child = _histIsChild(s.label);
    const expanded = _histExpanded.has(s.label);
    const cls = ["hist-card"];
    if (locked)
      cls.push("locked");
    if (child)
      cls.push("child");
    if (expanded)
      cls.push("expanded");
    const display = typeof sprintLabelDisplay === "function" ? sprintLabelDisplay(s.label) : s.label || "";
    const chev = expanded ? "ti-chevron-down" : "ti-chevron-right";
    const lbl = escHtml(s.label || "");
    const issues = Array.isArray(s.issues) ? s.issues : [];
    const endDate = s.ended_at ? String(s.ended_at).slice(0, 10) : s.started_at ? String(s.started_at).slice(0, 10) : "";
    const metaParts = [];
    if (endDate)
      metaParts.push(endDate);
    metaParts.push(issues.length + " ticket" + (issues.length !== 1 ? "s" : ""));
    const metaHtml = `<span class="hist-meta">${escHtml(metaParts.join(" \xB7 "))}</span>`;
    const recoveryBtn = _histRecoveryBtnHtml(s);
    const bulkBtn = opts.bulkCompleteBtn || "";
    const deleteBtn = _histDeleteBtnHtml(s);
    const secondaryLinks = _histSecondaryLinksHtml(s);
    const headRight = secondaryLinks || deleteBtn || bulkBtn || recoveryBtn ? `<span class="hist-card-head-right">${secondaryLinks}${deleteBtn}${bulkBtn}${recoveryBtn}</span>` : "";
    const body = expanded ? `<div class="hist-card-body">
      ${_histLooseEndBandHtml(s)}
      ${_histWhatListHtml(s)}
      ${_histDetailsHtml(s)}
      ${locked ? _histLinksHtml(s) : ""}
    </div>` : "";
    return `<div class="${cls.join(" ")}" data-label="${lbl}">
    <div class="hist-card-head" onclick="_histToggleCard('${lbl}')">
      <div class="hist-card-head-left">
        <i class="ti ${chev} hist-chev"></i>
        ${child ? '<span class="hist-child-arrow">\u21B3</span>' : ""}
        <span class="hist-card-title">${escHtml(display)}</span>
        ${_histStateChip(s.lifecycle_state, s)}
        ${metaHtml}
      </div>
      ${headRight}
    </div>
    ${body}
  </div>`;
  }
  function _histToggleCard(label) {
    if (_histExpanded.has(label)) {
      _histExpanded.delete(label);
    } else {
      _histExpanded.add(label);
      _histLoadRunStats(label);
    }
    _histRenderLedger(_histLedgerData);
  }
  function _histToggleDetails(label) {
    if (_histDetailsExpanded.has(label)) {
      _histDetailsExpanded.delete(label);
    } else {
      _histDetailsExpanded.add(label);
      _histLoadRunStats(label);
    }
    _histRenderLedger(_histLedgerData);
  }
  function _histLabelParts(label) {
    const m = /^sprint-(\d+)(?:\.(\d+))?$/.exec(label || "");
    if (!m)
      return { base: label || "", sub: 0, baseNum: 0 };
    return {
      base: `sprint-${m[1]}`,
      sub: m[2] ? parseInt(m[2], 10) : 0,
      baseNum: parseInt(m[1], 10)
    };
  }
  function _histGroupMembers(group) {
    const out = [];
    if (group.baseSprint)
      out.push(group.baseSprint);
    if (group.children)
      out.push(...group.children);
    return out;
  }
  function _histGroupSprints(sprints) {
    const byBase = /* @__PURE__ */ new Map();
    const groupOrder = [];
    for (let i = 0; i < sprints.length; i++) {
      const s = sprints[i];
      const { base, sub } = _histLabelParts(s.label);
      if (!byBase.has(base)) {
        byBase.set(base, { baseSprint: null, children: [], order: i });
        groupOrder.push(base);
      }
      const g = byBase.get(base);
      if (sub === 0)
        g.baseSprint = s;
      else
        g.children.push(s);
      g.order = Math.min(g.order, i);
    }
    groupOrder.sort((a, b) => byBase.get(a).order - byBase.get(b).order);
    return groupOrder.map((baseLabel) => {
      const g = byBase.get(baseLabel);
      g.children.sort((a, b) => _histLabelParts(a.label).sub - _histLabelParts(b.label).sub);
      return { baseLabel, baseSprint: g.baseSprint, children: g.children };
    });
  }
  function _histGroupNeedsBulkComplete(group) {
    const children = group.children || [];
    if (!children.length || !group.baseSprint)
      return false;
    const members = [group.baseSprint, ...children];
    return members.some((s) => {
      const st = (s.lifecycle_state || "").toLowerCase();
      return st !== "completed" && st !== "deleted";
    });
  }
  function _histChildSprintsAllCompleted(group) {
    const children = group.children || [];
    if (!children.length)
      return false;
    const settled = /* @__PURE__ */ new Set(["completed", "deleted", "ready_to_merge"]);
    return children.every((s) => settled.has((s.lifecycle_state || "").toLowerCase()));
  }
  function _histBulkCompleteBtnHtml(group) {
    if (!group.children?.length || !group.baseSprint)
      return "";
    if (!_histGroupNeedsBulkComplete(group))
      return "";
    const lbl = escHtml(group.baseLabel || "");
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
    const head = group.baseSprint ? _histCardHtml(group.baseSprint, { bulkCompleteBtn: bulkBtn }) : "";
    const childHtml = (group.children || []).map(_histCardHtml).join("");
    if (!childHtml)
      return head;
    if (!head) {
      return `<div class="hist-sprint-group"><div class="hist-group-children">${childHtml}</div></div>`;
    }
    return `<div class="hist-sprint-group">${head}<div class="hist-group-children">${childHtml}</div></div>`;
  }
  function _histTicketsDone(s) {
    const issues = Array.isArray(s.issues) ? s.issues : [];
    return issues.filter((i) => (i.state || "").toLowerCase() === "merged").length;
  }
  function _histPartitionGroups(groups, foldSize) {
    foldSize = Math.max(1, foldSize | 0);
    const recent = groups.slice(0, foldSize);
    const older = groups.slice(foldSize);
    const folds = [];
    for (let i = 0; i < older.length; i += foldSize) {
      const chunk = older.slice(i, i + foldSize);
      const nums = chunk.map((g) => _histLabelParts(g.baseLabel).baseNum).filter((n) => n > 0);
      folds.push({
        id: "fold-" + i,
        groups: chunk,
        sprints: chunk.flatMap(_histGroupMembers),
        from: nums.length ? Math.min(...nums) : null,
        to: nums.length ? Math.max(...nums) : null
      });
    }
    return { recent, folds };
  }
  function _histFoldAgg(group) {
    let done = 0, failed = 0, accSum = 0, accN = 0;
    group.forEach((s) => {
      done += _histTicketsDone(s);
      const _fst = (s.lifecycle_state || "").toLowerCase();
      if (_fst === "needs_rework" || _fst === "failed")
        failed += 1;
      const acc = s.estimate_accuracy;
      if (acc != null && !isNaN(acc)) {
        accSum += Number(acc);
        accN += 1;
      }
    });
    return { done, failed, avgAcc: accN ? accSum / accN : null, count: group.length };
  }
  function _histFoldAggHtml(group) {
    const a = _histFoldAgg(group);
    const acc = a.avgAcc != null ? Number(a.avgAcc).toFixed(2) + "\xD7" : "\u2014";
    return `<div class="fold-agg">
    <span class="fold-stat"><i class="ti ti-checks"></i> ${a.done} done</span>
    <span class="fold-stat"><i class="ti ti-gauge"></i> ${escHtml(acc)} avg est</span>
    <span class="fold-stat fold-stat--fail"><i class="ti ti-alert-triangle"></i> ${a.failed} failed</span>
  </div>`;
  }
  function _histFoldHtml(fold) {
    const open = _histFoldExpanded.has(fold.id);
    const chev = open ? "ti-chevron-down" : "ti-chevron-right";
    const range = fold.from != null && fold.to != null ? fold.from === fold.to ? "Sprint " + fold.from : "Sprints " + fold.from + "\u2013" + fold.to : "Sprints \xB7 " + fold.sprints.length;
    const fid = escHtml(fold.id);
    const nums = (fold.groups || []).map((g) => _histLabelParts(g.baseLabel).baseNum).filter((n) => n > 0);
    const chips = !open && nums.length ? `<div class="fold-chip-row" onclick="event.stopPropagation();_histToggleFold('${fid}')">` + nums.map((n) => `<span class="fold-num-chip">${n}</span>`).join("") + "</div>" : "";
    const body = open ? `<div class="fold-body">${(fold.groups || []).map(_histGroupHtml).join("")}</div>` : "";
    return `<div class="fold ${open ? "expanded" : ""}" data-fold="${fid}">
    <div class="fold-head" onclick="_histToggleFold('${fid}')">
      <i class="ti ${chev} fold-chev"></i>
      <span class="fold-title">${open ? escHtml(range) : "Older sprints"}</span>
      ${open ? `<span class="fold-count">${fold.sprints.length} sprints</span>` : ""}
      ${chips}
      <span class="fold-spacer"></span>
      ${_histFoldAggHtml(fold.sprints)}
    </div>
    ${body}
  </div>`;
  }
  function _histToggleFold(id) {
    if (_histFoldExpanded.has(id))
      _histFoldExpanded.delete(id);
    else
      _histFoldExpanded.add(id);
    _histRenderLedger(_histLedgerData);
  }
  function _histToolbarHtml() {
    return `<div class="hist-toolbar">
    <span class="hist-toolbar-note">
      <i class="ti ti-stack-2"></i>
      Latest ${_histFoldSize} sprint groups expanded below \u2014 older groups collapse to sprint numbers; click to open details.
    </span>
  </div>`;
  }
  async function _histScanStale() {
    const repo = _cachedFullRepo[_slug];
    if (!repo)
      return;
    const btn = document.getElementById("ps-stale-scan-btn");
    if (btn) {
      btn.disabled = true;
    }
    try {
      const resp = await fetch("/scan-stale-branches?repo=" + encodeURIComponent(repo));
      if (resp.ok) {
        const data = await resp.json();
        _histStaleBySprint = data.by_sprint || {};
        if (_histLedgerData && _histLedgerData.length) {
          _histRenderLedger(_histLedgerData);
        }
      }
    } catch (_) {
    } finally {
      const b = document.getElementById("ps-stale-scan-btn");
      if (b) {
        b.disabled = false;
      }
    }
  }
  async function _histCleanupStale(label) {
    const repo = _cachedFullRepo[_slug];
    const g = _histStaleBySprint[label];
    if (!repo || !g)
      return;
    const branches = g.branches || [];
    let plan;
    try {
      const resp = await fetch("/cleanup-stale-branches", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo, branches, confirm: false })
      });
      if (!resp.ok)
        return;
      plan = await resp.json();
    } catch (_) {
      return;
    }
    const toDelete = plan.to_delete || [];
    const skipped = plan.skipped_unmerged || [];
    if (!toDelete.length && !skipped.length)
      return;
    let msg = toDelete.length ? "Delete " + toDelete.length + " merged branch" + (toDelete.length !== 1 ? "es" : "") + "?\n\n" + toDelete.join("\n") : "No merged branches to delete for this sprint.";
    if (skipped.length) {
      msg += "\n\nSkipped (unmerged \u2014 never deleted):\n" + skipped.join("\n");
    }
    if (!confirm(msg))
      return;
    if (!toDelete.length)
      return;
    try {
      const resp = await fetch("/cleanup-stale-branches", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo, branches, confirm: true })
      });
      if (!resp.ok)
        return;
      const result = await resp.json();
      const deleted = new Set(result.deleted || []);
      const remaining = (g.branches || []).filter((b) => !deleted.has(b));
      if (remaining.length) {
        g.branches = remaining;
        g.merged = (g.merged || []).filter((b) => !deleted.has(b));
        g.unmerged = (g.unmerged || []).filter((b) => !deleted.has(b));
        g.count = remaining.length;
      } else {
        delete _histStaleBySprint[label];
      }
      _histRenderLedger(_histLedgerData);
    } catch (_) {
    }
  }
  function _histRenderLedger(sprints) {
    const el = document.getElementById("hist-ledger");
    if (!el)
      return;
    if (!sprints || !sprints.length) {
      el.innerHTML = `<div class="hist-ledger-empty">No sprint history yet \u2014 finished and deleted sprints appear here.</div>`;
      return;
    }
    const groups = _histGroupSprints(sprints);
    if (!_histDidAutoExpand && groups.length) {
      _histAutoExpandRecent(groups);
      _histDidAutoExpand = true;
    }
    const { recent, folds } = _histPartitionGroups(groups, _histFoldSize);
    const recentHtml = recent.map(_histGroupHtml).join("");
    const foldsHtml = folds.map(_histFoldHtml).join("");
    el.innerHTML = _histToolbarHtml() + recentHtml + foldsHtml;
  }
  async function _histRerunSprint(label) {
    const repo = (typeof _smgmtRepo === "function" ? _smgmtRepo() : null) || _cachedFullRepo && _cachedFullRepo[_slug] || "";
    if (!repo)
      return;
    const enc = encodeURIComponent(label);
    try {
      const prev = await fetch(`/api/sprints/${enc}/rerun-preview?project=${encodeURIComponent(repo)}`);
      if (!prev.ok) {
        console.error("_histRerunSprint preview failed", await prev.text());
        return;
      }
      const data = await prev.json();
      const ticketNumbers = (data.tickets || []).map((t) => t.number);
      const res = await fetch(`/api/sprints/${enc}/rerun?project=${encodeURIComponent(repo)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticket_numbers: ticketNumbers, auto_run: true })
      });
      if (!res.ok) {
        console.error("_histRerunSprint failed", await res.text());
        return;
      }
      await _histLoadLedger2(repo);
    } catch (e) {
      console.error("_histRerunSprint error", e);
    }
  }
  async function _histLoadLedger2(repo) {
    if (!repo)
      return;
    const el = document.getElementById("hist-ledger");
    try {
      try {
        const sresp = await fetch(`/api/projects/${encodeURIComponent(_slug)}/settings`);
        if (sresp.ok) {
          const settings = await sresp.json();
          const fs = parseInt(settings.history_fold_size, 10);
          if (!isNaN(fs) && fs > 0)
            _histFoldSize = fs;
        }
      } catch (_) {
      }
      const resp = await fetch("/api/sprints/history?limit=50&project=" + encodeURIComponent(repo || ""));
      if (!resp.ok)
        return;
      const data = await resp.json();
      let sprints = data.sprints || [];
      _histLedgerData = sprints;
      globalThis._histLedgerData = sprints;
      _histRenderLedger(sprints);
      _smgmtUpdateSubnav();
    } catch (_) {
      if (el)
        el.innerHTML = `<div class="hist-ledger-empty">Could not load sprint history.</div>`;
    }
  }
  function _histNextChildLabel(parentLabel) {
    return _nextSprintSublabel(parentLabel);
  }

  // apps/dashboard/static/src/sprint-board/rerun-modal.js
  function _rrOpen() {
    _setBodyInert(["rr-backdrop", "rr-modal"]);
    document.getElementById("rr-backdrop").classList.remove("hidden");
    document.getElementById("rr-modal").classList.remove("hidden");
  }
  function _rrClose() {
    document.getElementById("rr-backdrop").classList.add("hidden");
    document.getElementById("rr-modal").classList.add("hidden");
    _clearBodyInert();
    _rrLabel = null;
    _rrVersionedLabel = null;
  }
  function _rrCatClass(cat) {
    if (cat === "UAT")
      return "rr-cat-uat";
    if (cat === "SIT")
      return "rr-cat-sit";
    if (cat === "needs-rework")
      return "rr-cat-rework";
    return "rr-cat-queued";
  }
  function _rrUpdateState() {
    const checkboxes = document.querySelectorAll("#rr-ticket-list input[type=checkbox]");
    const checked = Array.from(checkboxes).filter((c) => c.checked);
    const uatChecked = Array.from(checkboxes).filter((c) => c.checked && c.dataset.cat === "UAT").length;
    const confirmBtn = document.getElementById("rr-confirm-btn");
    if (confirmBtn)
      confirmBtn.disabled = checked.length === 0;
    const warnEl = document.getElementById("rr-uat-warning");
    if (warnEl) {
      if (uatChecked > 0) {
        warnEl.textContent = `${uatChecked} ticket${uatChecked !== 1 ? "s" : ""} in UAT will be re-tested from scratch.`;
      } else {
        warnEl.textContent = "";
      }
    }
  }
  function _rrSelectAll(checked) {
    document.querySelectorAll("#rr-ticket-list input[type=checkbox]").forEach((cb) => {
      cb.checked = checked;
    });
    _rrUpdateState();
  }
  async function smgmtRerunSprint(label) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    _rrLabel = label;
    _rrVersionedLabel = null;
    document.getElementById("rr-modal-title").textContent = `Re-run ${sprintLabelDisplay(label)}?`;
    document.getElementById("rr-loading").classList.remove("hidden");
    document.getElementById("rr-content").classList.add("hidden");
    document.getElementById("rr-error").classList.add("hidden");
    document.getElementById("rr-error").textContent = "";
    const confirmBtn = document.getElementById("rr-confirm-btn");
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Create sprint and run";
    }
    _rrOpen();
    try {
      const res = await fetch(
        `/api/sprints/${encodeURIComponent(label)}/rerun-preview?project=${encodeURIComponent(repo)}`
      );
      if (!res.ok)
        throw new Error(await res.text());
      const preview = await res.json();
      _rrVersionedLabel = preview.suggested_versioned_label;
      document.getElementById("rr-modal-title").textContent = `Re-run ${sprintLabelDisplay(label)} as ${sprintLabelDisplay(_rrVersionedLabel)}?`;
      if (confirmBtn)
        confirmBtn.textContent = `Create & run ${sprintLabelDisplay(_rrVersionedLabel)}`;
      const listEl = document.getElementById("rr-ticket-list");
      if ((preview.tickets || []).length === 0) {
        listEl.innerHTML = '<div style="padding:10px;color:var(--text-muted);font-size:13px">No tickets in this sprint.</div>';
      } else {
        listEl.innerHTML = (preview.tickets || []).map((t) => {
          const checked = t.checked ? "checked" : "";
          const catClass = _rrCatClass(t.category);
          return `<label class="rr-ticket-row">
          <input type="checkbox" ${checked} data-issue="${t.number}" data-cat="${escHtml(t.category)}" onchange="_rrUpdateState()">
          <span class="rr-ticket-num">#${t.number}</span>
          <span class="rr-ticket-title" title="${escHtml(t.title)}">${escHtml(t.title)}</span>
          <span class="rr-ticket-cat ${catClass}">${escHtml(t.category)}</span>
        </label>`;
        }).join("");
      }
      document.getElementById("rr-loading").classList.add("hidden");
      document.getElementById("rr-content").classList.remove("hidden");
      _rrUpdateState();
    } catch (e) {
      document.getElementById("rr-loading").classList.add("hidden");
      const errEl = document.getElementById("rr-error");
      errEl.textContent = "Failed to load preview: " + e.message;
      errEl.classList.remove("hidden");
    }
  }
  async function _rrConfirm() {
    const repo = _smgmtRepo();
    if (!_rrLabel || !repo)
      return;
    const parentLabel = _rrLabel;
    const checkboxes = Array.from(document.querySelectorAll("#rr-ticket-list input[type=checkbox]"));
    const ticketNumbers = checkboxes.filter((c) => c.checked).map((c) => parseInt(c.dataset.issue, 10));
    if (ticketNumbers.length === 0)
      return;
    const confirmBtn = document.getElementById("rr-confirm-btn");
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Creating\u2026";
    }
    try {
      const res = await fetch(
        `/api/sprints/${encodeURIComponent(parentLabel)}/rerun?project=${encodeURIComponent(repo)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ticket_numbers: ticketNumbers, auto_run: false })
        }
      );
      if (!res.ok) {
        let detail = await res.text();
        try {
          const parsed = JSON.parse(detail);
          detail = parsed.detail || detail;
        } catch (_) {
        }
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      const data = await res.json();
      const subLabel = data.sub_label;
      _rrClose();
      if (typeof _smgmtApplyRerunOptimistic === "function") {
        _smgmtApplyRerunOptimistic(parentLabel, subLabel, ticketNumbers);
      }
      await loadSprintMgmt(true);
      const subDisplay = subLabel ? sprintLabelDisplay(subLabel) : "Sub-sprint";
      if (data.errors && data.errors.length > 0) {
        _smgmtShowToast(`${subDisplay} created with label errors \u2014 check GitHub.`);
      } else {
        _smgmtShowToast(`${subDisplay} ready \u2014 confirm run`);
      }
      if (subLabel && typeof smgmtRunSprint === "function") {
        smgmtRunSprint(subLabel);
      }
    } catch (e) {
      const errEl = document.getElementById("rr-error");
      errEl.textContent = "Failed to re-run sprint: " + e.message;
      errEl.classList.remove("hidden");
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = _rrVersionedLabel ? `Create & run ${sprintLabelDisplay(_rrVersionedLabel)}` : "Create sprint and run";
      }
    }
  }

  // apps/dashboard/static/src/sprint-board/finish-modal.js
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
    if (_fsActiveJob && _fsActiveJob.es) {
      _fsActiveJob.es.close();
      _fsActiveJob.es = null;
    }
    const snap = _fsActiveJob && _fsActiveJob.snapshot;
    if (!snap || snap.status === "done" || snap.status === "error") {
      _fsActiveJob = null;
    }
    _fsLabel = null;
    _fsPreview = null;
  }
  function _fsCatClass(cat) {
    if (cat === "UAT")
      return "rr-cat-uat";
    if (cat === "SIT")
      return "rr-cat-sit";
    if (cat === "needs-rework")
      return "rr-cat-rework";
    if (cat === "sprint-summary")
      return "rr-cat-summary";
    return "rr-cat-queued";
  }
  function _fsSelectAll(checked) {
    document.querySelectorAll("#fs-ticket-list input[type=checkbox]").forEach((cb) => {
      cb.checked = checked;
    });
  }
  function _fsProgressSlot() {
    return document.getElementById("fs-progress");
  }
  function _fsPreviewSlot() {
    return document.getElementById("fs-content");
  }
  function _fsEnterProgressView(snap) {
    document.getElementById("fs-loading").classList.add("hidden");
    _fsPreviewSlot() && _fsPreviewSlot().classList.add("hidden");
    document.getElementById("fs-error").classList.add("hidden");
    const slot = _fsProgressSlot();
    if (slot) {
      slot.innerHTML = renderProgressActivity(snap, {
        id: "fs-pa",
        retryFn: "_fsRetry"
      });
      slot.classList.remove("hidden");
    }
    const confirmBtn = document.getElementById("fs-confirm-btn");
    const cancelBtn = document.getElementById("fs-cancel-btn");
    const retryBtn = document.getElementById("fs-retry-btn");
    if (confirmBtn)
      confirmBtn.classList.add("hidden");
    if (cancelBtn)
      cancelBtn.textContent = "Close";
    if (retryBtn)
      retryBtn.classList.add("hidden");
  }
  function _fsUpdateProgress(snap) {
    const slot = _fsProgressSlot();
    if (!slot || slot.classList.contains("hidden"))
      return;
    const logEl = document.getElementById("pa-log-stream-fs-pa");
    const atBottom = !logEl || logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 5;
    slot.innerHTML = renderProgressActivity(snap, {
      id: "fs-pa",
      retryFn: "_fsRetry"
    });
    if (atBottom) {
      const newLog = document.getElementById("pa-log-stream-fs-pa");
      if (newLog)
        newLog.scrollTop = newLog.scrollHeight;
    }
  }
  function _fsDone(snap) {
    _fsUpdateProgress(snap);
    const cancelBtn = document.getElementById("fs-cancel-btn");
    const retryBtn = document.getElementById("fs-retry-btn");
    if (cancelBtn)
      cancelBtn.textContent = "Close";
    if (retryBtn)
      retryBtn.classList.add("hidden");
    _fsActiveJob = null;
    setTimeout(() => loadSprintMgmt(), 1500);
  }
  function _fsHandleError(snap) {
    _fsUpdateProgress(snap);
    const cancelBtn = document.getElementById("fs-cancel-btn");
    const retryBtn = document.getElementById("fs-retry-btn");
    if (cancelBtn)
      cancelBtn.textContent = "Close";
    if (retryBtn)
      retryBtn.classList.remove("hidden");
  }
  function _fsConnectStream(owner, repoName, label) {
    const url = `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/finish-stream`;
    const es = new EventSource(url);
    if (_fsActiveJob)
      _fsActiveJob.es = es;
    es.onmessage = (e) => {
      let snap;
      try {
        snap = JSON.parse(e.data);
      } catch {
        return;
      }
      if (snap.ping)
        return;
      if (_fsActiveJob)
        _fsActiveJob.snapshot = snap;
      if (snap.status === "done") {
        es.close();
        if (_fsActiveJob)
          _fsActiveJob.es = null;
        _fsDone(snap);
      } else if (snap.status === "error") {
        es.close();
        if (_fsActiveJob)
          _fsActiveJob.es = null;
        _fsHandleError(snap);
      } else {
        _fsUpdateProgress(snap);
      }
    };
    es.onerror = () => {
      es.close();
      if (_fsActiveJob)
        _fsActiveJob.es = null;
    };
  }
  async function _fsRetry() {
    if (!_fsActiveJob)
      return;
    const { owner, repoName, label, params } = _fsActiveJob;
    const emptySnap = {
      status: "running",
      mode: "bar",
      done: 0,
      total: params.total || 2,
      current: "Retrying\u2026",
      log_tail: []
    };
    _fsEnterProgressView(emptySnap);
    _fsActiveJob.snapshot = emptySnap;
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/finish-bg`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...params, confirmed: true })
        }
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
            log_tail: []
          },
          { id: "fs-pa", retryFn: "_fsRetry" }
        );
      }
      const retryBtn = document.getElementById("fs-retry-btn");
      if (retryBtn)
        retryBtn.classList.remove("hidden");
    }
  }
  async function smgmtFinishSprint(label) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    const parts = repo.split("/");
    const owner = parts[0];
    const repoName = parts.slice(1).join("/");
    if (_fsActiveJob && _fsActiveJob.label === label) {
      _fsLabel = label;
      document.getElementById("fs-modal-title").textContent = `Merging ${sprintLabelDisplay(label)}\u2026`;
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
    _fsLabel = label;
    _fsPreview = null;
    document.getElementById("fs-modal-title").textContent = `Merge ${sprintLabelDisplay(label)}?`;
    document.getElementById("fs-loading").classList.remove("hidden");
    document.getElementById("fs-content").classList.add("hidden");
    document.getElementById("fs-error").classList.add("hidden");
    document.getElementById("fs-error").textContent = "";
    const confirmBtn = document.getElementById("fs-confirm-btn");
    const cancelBtn = document.getElementById("fs-cancel-btn");
    const retryBtn = document.getElementById("fs-retry-btn");
    if (confirmBtn) {
      confirmBtn.classList.remove("hidden");
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Merge Sprint";
    }
    if (cancelBtn)
      cancelBtn.textContent = "Cancel";
    if (retryBtn)
      retryBtn.classList.add("hidden");
    const progSlot = _fsProgressSlot();
    if (progSlot)
      progSlot.classList.add("hidden");
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
      const listEl = document.getElementById("fs-ticket-list");
      const allTickets = preview.all_tickets || [];
      if (allTickets.length === 0) {
        listEl.innerHTML = '<div style="padding:10px;color:var(--text-muted);font-size:13px">No open tickets in this sprint.</div>';
      } else {
        listEl.innerHTML = allTickets.map((t) => {
          const catClass = _fsCatClass(t.category);
          const catLabel = t.category === "sprint-summary" ? "SUMMARY" : t.category.toUpperCase();
          return `<label class="rr-ticket-row">
          <input type="checkbox" checked data-issue="${t.number}" data-title="${escHtml(t.title)}" onchange="">
          <span class="rr-ticket-num">#${t.number}</span>
          <span class="rr-ticket-title" title="${escHtml(t.title)}">${escHtml(t.title)}</span>
          <span class="rr-ticket-cat ${catClass}">${escHtml(catLabel)}</span>
        </label>`;
        }).join("");
      }
      const actionsEl = document.getElementById("fs-actions");
      const actionRows = [];
      const mergeBranches = preview.merge_branches || [];
      for (const mb of mergeBranches) {
        actionRows.push(
          `<div class="fs-action-row"><i class="ti ti-git-merge"></i> Merge <code>${escHtml(mb.head)}</code> \u2192 <code>${escHtml(mb.base)}</code></div>`
        );
      }
      if (preview.sprint_pr) {
        actionRows.push(`<div class="fs-action-row"><i class="ti ti-git-merge"></i> Merge open PR
        <a href="${escHtml(preview.sprint_pr.url)}" target="_blank" rel="noopener">#${preview.sprint_pr.number}</a></div>`);
      }
      actionRows.push(
        '<div class="fs-action-row"><i class="ti ti-circle-check"></i> Close sprint tickets (labels kept)</div>'
      );
      actionsEl.innerHTML = actionRows.join("");
      document.getElementById("fs-loading").classList.add("hidden");
      document.getElementById("fs-content").classList.remove("hidden");
      if (confirmBtn)
        confirmBtn.disabled = false;
    } catch (e) {
      document.getElementById("fs-loading").classList.add("hidden");
      const errEl = document.getElementById("fs-error");
      errEl.textContent = "Failed to load preview: " + e.message;
      errEl.classList.remove("hidden");
    }
  }
  async function _fsConfirm() {
    const repo = _smgmtRepo();
    if (!_fsLabel || !repo || !_fsPreview)
      return;
    const parts = repo.split("/");
    const owner = parts[0];
    const repoName = parts.slice(1).join("/");
    const checkboxes = Array.from(
      document.querySelectorAll("#fs-ticket-list input[type=checkbox]")
    );
    const selectedTickets = checkboxes.filter((c) => c.checked).map((c) => ({
      number: parseInt(c.dataset.issue, 10),
      title: c.dataset.title || `#${c.dataset.issue}`
    }));
    const selectedNums = selectedTickets.map((t) => t.number);
    const confirmBtn = document.getElementById("fs-confirm-btn");
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Starting\u2026";
    }
    const bgParams = {
      move_non_uat_to: _fsPreview.next_sprint_label || "",
      selected_ticket_numbers: selectedNums,
      selected_tickets: selectedTickets,
      merge_pr: !!_fsPreview.sprint_pr,
      sprint_pr_url: _fsPreview.sprint_pr ? _fsPreview.sprint_pr.url : null,
      total: selectedNums.length + 2
    };
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(_fsLabel)}/finish-bg`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirmed: true, ...bgParams })
        }
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      await res.json();
      const initialSnap = {
        status: "running",
        mode: "bar",
        done: 0,
        total: bgParams.total,
        current: "Starting\u2026",
        log_tail: []
      };
      _fsActiveJob = {
        label: _fsLabel,
        owner,
        repoName,
        params: bgParams,
        snapshot: initialSnap,
        es: null
      };
      document.getElementById("fs-modal-title").textContent = `Merging ${sprintLabelDisplay(_fsLabel)}\u2026`;
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

  // apps/dashboard/static/src/sprint-board/bulk-complete-modal.js
  function _bcOpen() {
    _setBodyInert(["bc-backdrop", "bc-modal"]);
    document.getElementById("bc-backdrop").classList.remove("hidden");
    document.getElementById("bc-modal").classList.remove("hidden");
  }
  function _bcClose() {
    document.getElementById("bc-backdrop").classList.add("hidden");
    document.getElementById("bc-modal").classList.add("hidden");
    _clearBodyInert();
    _bcLabel = null;
    _bcPreview = null;
  }
  function _bcCatClass(cat) {
    if (cat === "UAT")
      return "rr-cat-uat";
    if (cat === "SIT")
      return "rr-cat-sit";
    if (cat === "needs-rework")
      return "rr-cat-rework";
    if (cat === "sprint-summary")
      return "rr-cat-summary";
    return "rr-cat-queued";
  }
  function _bcSelectAll(checked) {
    document.querySelectorAll("#bc-ticket-list input[type=checkbox]").forEach((cb) => {
      cb.checked = checked;
    });
  }
  async function smgmtBulkCompleteSprint(label) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    _bcLabel = label;
    _bcPreview = null;
    const parts = repo.split("/");
    const owner = parts[0];
    const repoName = parts.slice(1).join("/");
    document.getElementById("bc-modal-title").textContent = `Bulk complete ${sprintLabelDisplay(label)}?`;
    document.getElementById("bc-loading").classList.remove("hidden");
    document.getElementById("bc-content").classList.add("hidden");
    document.getElementById("bc-error").classList.add("hidden");
    document.getElementById("bc-error").textContent = "";
    const confirmBtn = document.getElementById("bc-confirm-btn");
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Bulk complete";
    }
    _bcOpen();
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/bulk-complete-preview`
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const preview = await res.json();
      _bcPreview = preview;
      const listEl = document.getElementById("bc-ticket-list");
      const allTickets = preview.all_tickets || [];
      if (allTickets.length === 0) {
        listEl.innerHTML = '<div style="padding:10px;color:var(--text-muted);font-size:13px">No open tickets in this sprint lineage.</div>';
      } else {
        listEl.innerHTML = allTickets.map((t) => {
          const catClass = _bcCatClass(t.category);
          const catLabel = t.category === "sprint-summary" ? "SUMMARY" : t.category.toUpperCase();
          return `<label class="rr-ticket-row">
          <input type="checkbox" checked data-issue="${t.number}" onchange="">
          <span class="rr-ticket-num">#${t.number}</span>
          <span class="rr-ticket-title" title="${escHtml(t.title)}">${escHtml(t.title)}</span>
          <span class="rr-ticket-cat ${catClass}">${escHtml(catLabel)}</span>
        </label>`;
        }).join("");
      }
      const memberCount = (preview.member_labels || []).length;
      const mergeSteps = preview.merge_steps || [];
      const actionsEl = document.getElementById("bc-actions");
      const actionRows = [];
      for (const step of mergeSteps) {
        actionRows.push(
          `<div class="fs-action-row"><i class="ti ti-git-merge"></i> Merge <code>${escHtml(step.head)}</code> \u2192 <code>${escHtml(step.base)}</code></div>`
        );
      }
      actionRows.push(
        `<div class="fs-action-row"><i class="ti ti-circle-check"></i> Close selected tickets (UAT + summary included)</div>`,
        `<div class="fs-action-row"><i class="ti ti-flag-check"></i> Mark ${memberCount} sprint${memberCount !== 1 ? "s" : ""} completed</div>`
      );
      actionsEl.innerHTML = actionRows.join("");
      document.getElementById("bc-loading").classList.add("hidden");
      document.getElementById("bc-content").classList.remove("hidden");
      if (confirmBtn)
        confirmBtn.disabled = false;
    } catch (e) {
      document.getElementById("bc-loading").classList.add("hidden");
      const errEl = document.getElementById("bc-error");
      errEl.textContent = "Failed to load preview: " + e.message;
      errEl.classList.remove("hidden");
    }
  }
  async function _bcMergeStep(owner, repoName, step) {
    const res = await fetch(
      `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprint-branch-merge`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          confirmed: true,
          head: step.head,
          base: step.base,
          title: step.title || "",
          delete_branch: step.delete_branch !== false
        })
      }
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  }
  async function _bcConfirm() {
    const repo = _smgmtRepo();
    if (!_bcLabel || !repo || !_bcPreview)
      return;
    const parts = repo.split("/");
    const owner = parts[0];
    const repoName = parts.slice(1).join("/");
    const label = _bcLabel;
    const mergeSteps = _bcPreview.merge_steps || [];
    const checkboxes = Array.from(document.querySelectorAll("#bc-ticket-list input[type=checkbox]"));
    const selectedNums = checkboxes.filter((c) => c.checked).map((c) => parseInt(c.dataset.issue, 10));
    const confirmBtn = document.getElementById("bc-confirm-btn");
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Completing\u2026";
    }
    _bcClose();
    const settleLabel = "Close tickets and mark sprints completed";
    const refreshLabel = "Refreshing board\u2026";
    const totalSteps = mergeSteps.length + 2;
    let doneSteps = 0;
    _smgmtBoardLock(`Bulk completing ${sprintLabelDisplay(label)}\u2026`, {
      progress: true,
      total: totalSteps,
      clearLog: true
    });
    _smgmtBoardLog("Starting bulk complete\u2026", "step");
    try {
      for (const step of mergeSteps) {
        const stepLabel = step.label || `${step.head} \u2192 ${step.base}`;
        _smgmtBoardLog(stepLabel, "step");
        await _bcMergeStep(owner, repoName, step);
        doneSteps += 1;
        _smgmtBoardProgress(doneSteps, totalSteps);
        _smgmtBoardLog(`\u2713 ${stepLabel}`, "ok");
      }
      _smgmtBoardLog(settleLabel, "step");
      const res = await fetch(
        `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/bulk-complete`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            confirmed: true,
            selected_ticket_numbers: selectedNums
          })
        }
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      doneSteps += 1;
      _smgmtBoardProgress(doneSteps, totalSteps);
      _smgmtBoardLog(`\u2713 ${settleLabel}`, "ok");
      _smgmtBoardLog(refreshLabel, "step");
      await loadSprintMgmt();
      doneSteps += 1;
      _smgmtBoardProgress(doneSteps, totalSteps);
      _smgmtBoardLog("\u2713 Bulk complete finished", "ok");
      if (data.errors && data.errors.length > 0) {
        _smgmtShowToast(`Bulk complete finished with errors \u2014 ${data.closed} closed.`);
      } else {
        _smgmtShowToast(
          `${sprintLabelDisplay(label)} bulk completed \u2014 ${data.closed} closed, ${data.completed} marked completed.`
        );
      }
    } catch (e) {
      _smgmtBoardLog(`\u2717 ${e.message}`, "err");
      _smgmtShowToast("Bulk complete failed: " + e.message);
      try {
        await loadSprintMgmt();
      } catch (_) {
      }
    } finally {
      _smgmtBoardUnlock();
    }
  }

  // apps/dashboard/static/src/sprint-board/run-controls.js
  var PF_STEPS = [
    { key: "ac", label: "Acceptance criteria", autoFixable: true },
    { key: "estimates", label: "Estimate coverage", autoFixable: true },
    { key: "cycle", label: "Dependency graph", autoFixable: false },
    { key: "missizing", label: "Mis-sizing review", autoFixable: false },
    { key: "conflicts", label: "Conflict analysis", autoFixable: false }
  ];
  var _pfStepFails = 0;
  var _pfModels = null;
  function _pfModelShort(m) {
    const s = String(m || "");
    return s.replace(/^claude-/, "") || s;
  }
  function _pfBuildModelsHtml() {
    const m = _pfModels;
    if (!m)
      return "";
    const rows = [];
    rows.push(`<span class="pf-model-pill"><b>Coder</b> ${escHtml(_pfModelShort(m.coder))}</span>`);
    const br = m.tester_by_risk || {};
    const testerTxt = Object.keys(br).length ? Object.keys(br).map((k) => `${k.toLowerCase()}:${_pfModelShort(br[k])}`).join(" \xB7 ") : "risk-routed";
    rows.push(`<span class="pf-model-pill"><b>Tester</b> ${escHtml(testerTxt)}</span>`);
    rows.push(`<span class="pf-model-pill"><b>Estimator</b> ${escHtml(_pfModelShort(m.estimator))}</span>`);
    if (m.documentor) {
      rows.push(`<span class="pf-model-pill"><b>Documentor</b> ${escHtml(_pfModelShort(m.documentor))}</span>`);
    }
    return `<div class="pf-section">
      <div class="pf-section-label">Agent models <span class="pf-model-note">\u2014 confirm before run \xB7 edit in Settings \u2192 Agent Models</span></div>
      <div class="pf-section-body pf-model-pills">${rows.join("")}</div>
    </div>`;
  }
  function smgmtRunBlockedToast() {
    _smgmtShowToast("Another sprint is running \u2014 wait for it to finish or cancel it");
  }
  function smgmtRunSprint2(label) {
    _pfOpen(label);
  }
  async function smgmtCancelSprint(label) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    if (!confirm(`Cancel sprint ${sprintLabelDisplay(label)}? The sprint will stop and tickets will not be modified.`))
      return;
    try {
      const res = await fetch(`/api/sprints/run/${encodeURIComponent(label)}?project=${encodeURIComponent(repo)}`, { method: "DELETE" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        _smgmtShowToast(`Cancel failed: ${err.detail || res.status}`);
      } else {
        _smgmtShowToast(`Sprint ${sprintLabelDisplay(label)} cancel signal sent`);
        _smgmtRunningLabels.delete(label);
        _smgmtAnySprintRunning = _smgmtRunningLabels.size > 0;
        if (typeof _smgmtLingerStart === "function") {
          _smgmtLingerStart(label, { cancelled: true });
        }
        if (typeof _smgmtLivePollRestart === "function")
          _smgmtLivePollRestart();
        if (typeof _smgmtRunningViewUpdate === "function") {
          const snap = typeof _smgmtLingerLive === "function" ? _smgmtLingerLive(label) : null;
          _smgmtRunningViewUpdate(label, snap);
        }
        setTimeout(() => loadSprintMgmt(), 2e3);
      }
    } catch (e) {
      _smgmtShowToast(`Cancel failed: ${e.message}`);
    }
  }
  function _pfOpen(label) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    _pfCurrentLabel = label;
    _pfCurrentRepo = repo;
    _pfReset();
    document.getElementById("pf-backdrop").classList.remove("hidden");
    document.getElementById("pf-modal").classList.remove("hidden");
    document.getElementById("pf-close-btn").focus();
    _pfFetch();
  }
  function _pfReset() {
    document.getElementById("pf-loading").classList.add("hidden");
    document.getElementById("pf-stepper").classList.remove("hidden");
    document.getElementById("pf-content").classList.add("hidden");
    document.getElementById("pf-error").classList.add("hidden");
    document.getElementById("pf-footer").classList.remove("hidden");
    document.getElementById("pf-confirm-btn").disabled = true;
    document.getElementById("pf-confirm-btn").textContent = "Run Sprint";
    _pfDagData = null;
    _pfWarnings = null;
    _pfCycle = null;
    _pfFlags = null;
    _pfModels = null;
    _pfSelectedIds = /* @__PURE__ */ new Set();
    _pfUseClineFollowups = false;
    _pfStepperInit();
  }
  function _pfClose() {
    document.getElementById("pf-backdrop").classList.add("hidden");
    document.getElementById("pf-modal").classList.add("hidden");
    document.getElementById("pf-stepper").classList.add("hidden");
    _pfCurrentLabel = null;
    _pfCurrentRepo = null;
    _pfState = "idle";
    _pfDagData = null;
    _pfWarnings = null;
    _pfCycle = null;
    _pfFlags = null;
    _pfSelectedIds = /* @__PURE__ */ new Set();
    _pfUseClineFollowups = false;
    _pfStepFails = 0;
  }
  async function _pfFetch() {
    _pfState = "loading";
    const label = _pfCurrentLabel;
    const repo = _pfCurrentRepo;
    try {
      const res = await fetch(
        `/api/sprints/${encodeURIComponent(label)}/preflight?project=${encodeURIComponent(repo)}`
      );
      if (!res.ok)
        throw new Error(await res.text());
      if (_pfCurrentLabel !== label)
        return;
      const data = await res.json();
      _pfDagData = data.dag || null;
      _pfWarnings = data.warnings || null;
      _pfCycle = data.cycle || null;
      _pfFlags = data.mis_sizing_flags || null;
      _pfModels = data.models || null;
      if (_pfDagData) {
        for (const t of _pfDagData.tickets || [])
          _pfSelectedIds.add(t.id);
      }
      _pfState = "success";
      _pfShowSuccess();
      _pfStepperAnimate(data);
    } catch (e) {
      if (_pfCurrentLabel !== label)
        return;
      _pfState = "error";
      _pfShowError(e.message || "Preflight check failed.");
    }
  }
  function _pfShowSuccess() {
    document.getElementById("pf-loading").classList.add("hidden");
    document.getElementById("pf-error").classList.add("hidden");
    const n = parseInt((_pfCurrentLabel || "").split("-")[1], 10);
    const dagHtml = _pfDagData && (_pfDagData.tickets || []).length > 0 ? _pfBuildDAGHtml(_pfDagData) : "";
    const warningsHtml = _pfBuildWarningsHtml();
    const cycleHtml = _pfBuildCycleHtml();
    const flagsHtml = _pfBuildFlagsHtml();
    const conflictsHtml = _pfBuildConflictsHtml();
    const orderHtml = _pfBuildOrderHtml();
    const modelsHtml = _pfBuildModelsHtml();
    const clineCheckboxHtml = `<div class="pf-section pf-cline-section">
     <label class="pf-cline-label">
       <input type="checkbox" id="pf-cline-checkbox" class="pf-cline-checkbox"
         ${_pfUseClineFollowups ? "checked" : ""}
         onchange="_pfUseClineFollowups = this.checked">
       <span>Use Cline (Sonnet) for follow-up coder fixes \u2014 tester stays on Claude</span>
     </label>
   </div>`;
    document.getElementById("pf-content").innerHTML = `<p style="font-size:13px;color:var(--text);margin:0;">Ready to run <strong>Sprint ${n}</strong>.</p>
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
    document.getElementById("pf-content").classList.remove("hidden");
    document.getElementById("pf-footer").classList.remove("hidden");
    document.getElementById("pf-cancel-btn").focus();
    if (_pfDagData && (_pfDagData.edges || []).length > 0) {
      requestAnimationFrame(() => _pfDrawDAGArrows(_pfDagData.edges));
    }
  }
  function _pfUpdateConfirmBtn() {
    const hasCycle = !!(_pfCycle && _pfCycle.length);
    const pendingFlags = _pfFlags && (_pfFlags.flags || []).filter((f) => f.status === "pending") || [];
    const hasPending = pendingFlags.length > 0;
    const hasFail = _pfStepFails > 0;
    const confirmBtn = document.getElementById("pf-confirm-btn");
    if (!confirmBtn)
      return;
    confirmBtn.disabled = hasCycle || hasPending || hasFail;
    if (hasCycle) {
      confirmBtn.title = "Cannot run: dependency cycle detected. Resolve the cycle first.";
      confirmBtn.setAttribute("aria-label", "Run Sprint \u2014 disabled: dependency cycle detected");
    } else if (hasPending) {
      confirmBtn.title = `Cannot run: ${pendingFlags.length} mis-sizing flag${pendingFlags.length > 1 ? "s" : ""} need review.`;
      confirmBtn.setAttribute("aria-label", "Run Sprint \u2014 disabled: mis-sizing flags need review");
    } else if (hasFail) {
      confirmBtn.title = `Cannot run: ${_pfStepFails} blocking issue${_pfStepFails > 1 ? "s" : ""} detected.`;
      confirmBtn.setAttribute("aria-label", `Run Sprint \u2014 disabled: ${_pfStepFails} blocking issue(s)`);
    } else {
      confirmBtn.title = "";
      confirmBtn.setAttribute("aria-label", "Run Sprint");
    }
  }
  function _pfBuildWarningsHtml() {
    if (!_pfWarnings)
      return "";
    const chips = [];
    const unestimated = _pfWarnings.unestimated || [];
    const staleEstimates = _pfWarnings.stale_estimates || [];
    const missingAc = _pfWarnings.missing_ac || [];
    if (unestimated.length) {
      chips.push(`<span class="pf-warning-chip">${unestimated.length} unestimated: ${escHtml(unestimated.join(", "))}</span>`);
    }
    if (staleEstimates.length) {
      chips.push(`<span class="pf-warning-chip">${staleEstimates.length} stale estimate${staleEstimates.length > 1 ? "s" : ""}: ${escHtml(staleEstimates.join(", "))}</span>`);
    }
    if (missingAc.length) {
      chips.push(`<span class="pf-warning-chip">${missingAc.length} missing AC: ${escHtml(missingAc.join(", "))}</span>`);
    }
    if (!chips.length)
      return "";
    return `<div class="pf-warnings-section">
    <div class="pf-warnings-label">Warnings</div>
    <div class="pf-warning-chips">${chips.join("")}</div>
  </div>`;
  }
  function _pfBuildCycleHtml() {
    if (!_pfCycle || !_pfCycle.length)
      return "";
    return `<div class="pf-cycle-banner">
    <strong>Cycle detected:</strong> ${escHtml(_pfCycle.join(" \u2192 "))}
  </div>`;
  }
  function _pfBuildFlagsHtml() {
    const flags = _pfFlags && (_pfFlags.flags || []);
    if (!flags || !flags.length)
      return "";
    const rows = flags.map((f) => {
      const num = f.issue_number;
      const resolved = f.status !== "pending";
      const itemClass = resolved ? "pf-flag-item resolved" : "pf-flag-item";
      const estLabel = f.current_estimate ? `${escHtml(f.current_estimate)} (${f.current_estimate_minutes ?? "?"} min)` : "unknown";
      const avgLabel = f.historical_avg_actual_size ? `${escHtml(f.historical_avg_actual_size)} (${f.historical_avg_actual_minutes ?? "?"} min avg)` : "unknown";
      const drivingLabels = (f.driving_labels || []).map((l) => `<code>${escHtml(l)}</code>`).join(", ");
      const eventCount = f.mis_sizing_event_count || 0;
      let badgeHtml = "";
      let actionsHtml = "";
      if (resolved) {
        const actionText = { approved: "Approved", reestimated: "Re-estimated", dismissed: "Dismissed" }[f.status] || f.status;
        badgeHtml = `<span class="pf-flag-badge pf-flag-badge-resolved">${escHtml(actionText)}</span>`;
        const noteText = f.action_note ? ` \u2014 ${escHtml(f.action_note)}` : "";
        const newSizeText = f.new_size ? ` New size: ${escHtml(f.new_size)}.` : "";
        actionsHtml = `<div class="pf-flag-resolved-note">${escHtml(actionText)}${newSizeText}${noteText}</div>`;
      } else {
        badgeHtml = `<span class="pf-flag-badge pf-flag-badge-pending">Review needed</span>`;
        actionsHtml = `
        <div class="pf-flag-actions" id="pf-flag-actions-${num}">
          <button class="pf-flag-action-btn approve" onclick="_pfFlagAction(${num}, 'approved')">Approve</button>
          <button class="pf-flag-action-btn" onclick="_pfFlagShowSizePicker(${num}, '${escHtml(f.current_estimate || "S")}')">Re-estimate</button>
          <button class="pf-flag-action-btn dismiss" onclick="_pfFlagAction(${num}, 'dismissed')">Dismiss</button>
        </div>
        <div id="pf-flag-picker-${num}" style="display:none">
          <div class="pf-flag-size-picker">
            <span style="font-size:12px;color:var(--text-muted);">New size:</span>
            ${["S", "M", "L", "XL"].map(
          (s) => `<button class="pf-flag-size-btn" onclick="_pfFlagReestimate(${num}, '${s}')">${s}</button>`
        ).join("")}
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
        Estimate: <strong>${estLabel}</strong> \xB7
        Historical avg: <strong>${avgLabel}</strong> \xB7
        ${eventCount} mis-sizing event${eventCount !== 1 ? "s" : ""} on: ${drivingLabels}
      </div>
      ${actionsHtml}
    </div>`;
    });
    const pending = flags.filter((f) => f.status === "pending").length;
    const subtitle = pending > 0 ? `${pending} ticket${pending > 1 ? "s" : ""} flagged for review` : "All flags resolved";
    return `<div class="pf-flags-section" id="pf-flags-section">
    <div class="pf-flags-label">Mis-sizing review \u2014 ${subtitle}</div>
    ${rows.join("")}
  </div>`;
  }
  function _pfFlagShowSizePicker(num, _currentSize) {
    const actionsEl = document.getElementById(`pf-flag-actions-${num}`);
    const pickerEl = document.getElementById(`pf-flag-picker-${num}`);
    if (actionsEl)
      actionsEl.style.display = "none";
    if (pickerEl)
      pickerEl.style.display = "block";
  }
  function _pfFlagHidePicker(num) {
    const actionsEl = document.getElementById(`pf-flag-actions-${num}`);
    const pickerEl = document.getElementById(`pf-flag-picker-${num}`);
    if (actionsEl)
      actionsEl.style.display = "";
    if (pickerEl)
      pickerEl.style.display = "none";
  }
  async function _pfFlagAction(num, action, newSize) {
    const label = _pfCurrentLabel;
    const repo = _pfCurrentRepo;
    if (!label || !repo)
      return;
    const itemEl = document.getElementById(`pf-flag-item-${num}`);
    if (itemEl)
      itemEl.querySelectorAll("button").forEach((b) => {
        b.disabled = true;
      });
    try {
      const body = { action };
      if (newSize)
        body.new_size = newSize;
      const res = await fetch(
        `/api/sprints/${encodeURIComponent(label)}/mis-sizing-flags/${num}/action?project=${encodeURIComponent(repo)}`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
      );
      if (!res.ok) {
        const err = await res.text();
        _smgmtShowToast(`Flag action failed: ${err}`, "error");
        if (itemEl)
          itemEl.querySelectorAll("button").forEach((b) => {
            b.disabled = false;
          });
        return;
      }
      const data = await res.json();
      _pfFlags = data;
      const flagsSection = document.getElementById("pf-flags-section");
      if (flagsSection) {
        const newHtml = _pfBuildFlagsHtml();
        flagsSection.outerHTML = newHtml || '<div id="pf-flags-section"></div>';
      }
      _pfUpdateConfirmBtn();
    } catch (e) {
      _smgmtShowToast("Flag action failed: " + e.message, "error");
      if (itemEl)
        itemEl.querySelectorAll("button").forEach((b) => {
          b.disabled = false;
        });
    }
  }
  function _pfFlagReestimate(num, newSize) {
    _pfFlagHidePicker(num);
    _pfFlagAction(num, "reestimated", newSize);
  }
  function _pfBuildDAGHtml(dag) {
    const ticketMap = {};
    for (const t of dag.tickets || [])
      ticketMap[t.id] = t;
    const layers = dag.layers || [];
    if (!layers.length)
      return "";
    let colsHtml = "";
    for (let i = 0; i < layers.length; i++) {
      const layer = layers[i];
      let cardsHtml = "";
      for (const id of layer) {
        const t = ticketMap[id] || { id, number: id.replace("#", ""), title: id, state: "backlog", size: null, files_touched: [] };
        const stateClass = t.state || "backlog";
        const stateBadge = `<span class="ticket-status-pill ${escHtml(stateClass)}">${escHtml(stateClass)}</span>`;
        const sizeBadge = t.size ? `<span class="pf-dag-size-badge">${escHtml(t.size)}</span>` : "";
        const files = t.files_touched || [];
        const shown = files.slice(0, 3).map((f) => `<span>${escHtml(f.split("/").slice(-1)[0])}</span>`).join("");
        const more = files.length > 3 ? `<span>+${files.length - 3} more</span>` : "";
        const filesHtml = shown || more ? `<div class="pf-dag-card-files">${shown}${more}</div>` : "";
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
    if (!edges || !edges.length)
      return;
    const wrap = document.getElementById("pf-dag-wrap");
    const svg = document.getElementById("pf-dag-svg");
    const levels = document.getElementById("pf-dag-levels");
    if (!wrap || !svg || !levels)
      return;
    const wrapRect = wrap.getBoundingClientRect();
    const h = levels.getBoundingClientRect().height;
    svg.setAttribute("width", String(wrapRect.width));
    svg.setAttribute("height", String(h));
    const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    const marker = document.createElementNS("http://www.w3.org/2000/svg", "marker");
    marker.setAttribute("id", "pf-arrow");
    marker.setAttribute("markerWidth", "7");
    marker.setAttribute("markerHeight", "7");
    marker.setAttribute("refX", "6");
    marker.setAttribute("refY", "3.5");
    marker.setAttribute("orient", "auto");
    const arrowPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
    arrowPath.setAttribute("d", "M0,0 L0,7 L7,3.5 z");
    arrowPath.setAttribute("fill", "var(--text-muted)");
    marker.appendChild(arrowPath);
    defs.appendChild(marker);
    svg.appendChild(defs);
    for (const [fromId, toId] of edges) {
      const fromEl = wrap.querySelector(`[data-dag-id="${fromId}"]`);
      const toEl = wrap.querySelector(`[data-dag-id="${toId}"]`);
      if (!fromEl || !toEl)
        continue;
      const fr = fromEl.getBoundingClientRect();
      const tr = toEl.getBoundingClientRect();
      const x1 = fr.right - wrapRect.left;
      const y1 = fr.top + fr.height / 2 - wrapRect.top;
      const x2 = tr.left - wrapRect.left - 7;
      const y2 = tr.top + tr.height / 2 - wrapRect.top;
      const mx = (x1 + x2) / 2;
      const line = document.createElementNS("http://www.w3.org/2000/svg", "path");
      line.setAttribute("d", `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`);
      line.setAttribute("stroke", "var(--text-muted)");
      line.setAttribute("stroke-width", "1.5");
      line.setAttribute("fill", "none");
      line.setAttribute("marker-end", "url(#pf-arrow)");
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
    if (card)
      card.classList.toggle("pf-deselected", !_pfSelectedIds.has(id));
    _pfUpdateSections();
  }
  function _pfGetSelectedTickets() {
    if (!_pfDagData)
      return [];
    return (_pfDagData.tickets || []).filter((t) => _pfSelectedIds.has(t.id));
  }
  function _pfComputeConflicts(tickets) {
    const conflicts = [];
    for (let i = 0; i < tickets.length; i++) {
      for (let j = i + 1; j < tickets.length; j++) {
        const filesA = tickets[i].files_touched || [];
        const filesB = tickets[j].files_touched || [];
        const shared = filesA.filter((f) => filesB.includes(f));
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
    return conflicts.map(
      (c) => `<p class="pf-conflict-item">Tickets #${c.a.number} and #${c.b.number} both touch <code>${escHtml(c.file)}</code></p>`
    ).join("");
  }
  function _pfBuildOrderHtml() {
    if (!_pfDagData)
      return '<p class="pf-no-conflict">No order data available.</p>';
    const layers = (_pfDagData.layers || []).map((layer) => layer.filter((id) => _pfSelectedIds.has(id))).filter((l) => l.length > 0);
    if (!layers.length)
      return '<p class="pf-no-conflict">No tickets selected.</p>';
    let html = '<ol class="pf-order-list">';
    for (let i = 0; i < layers.length; i++) {
      const nums = layers[i].map((id) => id);
      const descriptor = i === 0 ? "parallel-eligible" : `runs after Level ${i}`;
      html += `<li class="pf-order-item">Level ${i + 1}: ${escHtml(nums.join(", "))} \u2014 ${escHtml(descriptor)}.</li>`;
    }
    html += "</ol>";
    return html;
  }
  function _pfUpdateSections() {
    const conflictsEl = document.getElementById("pf-conflicts");
    const orderEl = document.getElementById("pf-order");
    if (conflictsEl)
      conflictsEl.innerHTML = _pfBuildConflictsHtml();
    if (orderEl)
      orderEl.innerHTML = _pfBuildOrderHtml();
  }
  function _pfShowError(msg) {
    document.getElementById("pf-loading").classList.add("hidden");
    document.getElementById("pf-content").classList.add("hidden");
    document.getElementById("pf-error-msg").textContent = msg;
    document.getElementById("pf-error").classList.remove("hidden");
    document.getElementById("pf-footer").classList.remove("hidden");
    document.getElementById("pf-confirm-btn").disabled = true;
    document.getElementById("pf-retry-btn").focus();
  }
  function _pfRetry() {
    _pfReset();
    _pfFetch();
  }
  async function _pfConfirm() {
    if (_pfState !== "success")
      return;
    const label = _pfCurrentLabel;
    const repo = _pfCurrentRepo;
    if (!label || !repo)
      return;
    const confirmBtn = document.getElementById("pf-confirm-btn");
    confirmBtn.disabled = true;
    confirmBtn.textContent = "Starting\u2026";
    _pfClose();
    await smgmtKickoffRun(label, repo);
  }
  function _pfStepperInit() {
    _pfStepFails = 0;
    const stepsEl = document.getElementById("pf-stepper-steps");
    if (!stepsEl)
      return;
    stepsEl.innerHTML = PF_STEPS.map(
      (s) => `<div class="pf-step-item pf-step-item--pending" id="pf-step-${s.key}">
      <span class="pf-step-icon" aria-hidden="true"></span>
      <div class="pf-step-content">
        <span class="pf-step-name">${escHtml(s.label)}</span>
        <span class="pf-step-note" id="pf-step-note-${s.key}"></span>
      </div>
    </div>`
    ).join("");
    const summaryEl = document.getElementById("pf-stepper-summary");
    if (summaryEl) {
      summaryEl.textContent = "";
      summaryEl.className = "pf-stepper-summary hidden";
    }
  }
  function _pfStepState(key, state, note) {
    const item = document.getElementById(`pf-step-${key}`);
    if (!item)
      return;
    item.className = `pf-step-item pf-step-item--${state}`;
    const noteEl = document.getElementById(`pf-step-note-${key}`);
    if (noteEl)
      noteEl.textContent = note || "";
  }
  async function _pfRunAutoFix(label, repo) {
    const resp = await fetch(
      `/api/sprints/${encodeURIComponent(label)}/preflight-fix?project=${encodeURIComponent(repo)}`,
      { method: "POST" }
    );
    if (!resp.ok)
      throw new Error(`preflight-fix ${resp.status}`);
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = "", filled = 0, estimated = 0, errors = [];
    while (true) {
      const { done, value } = await reader.read();
      if (done)
        break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split("\n\n");
      buf = parts.pop();
      for (const part of parts) {
        const m = part.match(/^event:\s*(\S+)\ndata:\s*([\s\S]*)$/);
        if (!m)
          continue;
        if (m[1] === "done") {
          try {
            const d = JSON.parse(m[2]);
            filled = d.filled || 0;
            estimated = d.estimated || 0;
            errors = d.errors || [];
          } catch (_) {
          }
        }
      }
    }
    return { filled, estimated, errors };
  }
  async function _pfStepperAnimate(data) {
    const delay = (ms) => new Promise((r) => setTimeout(r, ms));
    const label = _pfCurrentLabel;
    const repo = _pfCurrentRepo;
    _pfStepState("ac", "checking", "");
    _pfStepState("estimates", "checking", "");
    await delay(350);
    const missingAc = data.warnings && data.warnings.missing_ac || [];
    const unestimated = data.warnings && data.warnings.unestimated || [];
    const hasAcIssues = missingAc.length > 0;
    const hasEstIssues = unestimated.length > 0;
    if ((hasAcIssues || hasEstIssues) && label && repo) {
      try {
        const fix = await _pfRunAutoFix(label, repo);
        const acNote = fix.filled > 0 ? `${fix.filled} acceptance criteria generated` : hasAcIssues ? `${missingAc.length} ticket(s) missing AC` : "";
        const estNote = fix.estimated > 0 ? `${fix.estimated} ticket(s) estimated` : hasEstIssues ? `${unestimated.length} ticket(s) unestimated` : "";
        _pfStepState("ac", fix.filled > 0 ? "fixed" : "pass", acNote);
        _pfStepState("estimates", fix.estimated > 0 ? "fixed" : "pass", estNote);
      } catch (_) {
        _pfStepState("ac", "pass", hasAcIssues ? `${missingAc.length} ticket(s) missing AC` : "");
        _pfStepState("estimates", "pass", hasEstIssues ? `${unestimated.length} ticket(s) unestimated` : "");
      }
    } else {
      _pfStepState("ac", "pass", "");
      _pfStepState("estimates", "pass", "");
    }
    await delay(300);
    _pfStepState("cycle", "checking", "");
    await delay(350);
    if (data.cycle && data.cycle.length) {
      _pfStepState("cycle", "fail", `Cycle: ${data.cycle.join(" \u2192 ")}`);
      _pfStepFails++;
    } else {
      _pfStepState("cycle", "pass", "");
    }
    await delay(300);
    _pfStepState("missizing", "checking", "");
    await delay(350);
    const pendingFlags = (data.mis_sizing_flags && data.mis_sizing_flags.flags || []).filter((f) => f.status === "pending");
    if (pendingFlags.length > 0) {
      _pfStepState("missizing", "fail", `${pendingFlags.length} flag(s) require review`);
      _pfStepFails++;
    } else {
      _pfStepState("missizing", "pass", "");
    }
    await delay(300);
    _pfStepState("conflicts", "checking", "");
    await delay(350);
    const selectedTickets = _pfGetSelectedTickets();
    const conflicts = _pfComputeConflicts(selectedTickets);
    if (conflicts.length > 0) {
      _pfStepState("conflicts", "pass", `${conflicts.length} conflict(s) \u2014 execution order planned`);
    } else {
      _pfStepState("conflicts", "pass", "");
    }
    _pfStepperSummary();
    _pfUpdateConfirmBtn();
  }
  function _pfStepperSummary() {
    const summaryEl = document.getElementById("pf-stepper-summary");
    if (!summaryEl)
      return;
    summaryEl.classList.remove("hidden");
    if (_pfStepFails > 0) {
      summaryEl.textContent = `${_pfStepFails} blocking issue${_pfStepFails > 1 ? "s" : ""} \u2014 cannot run`;
      summaryEl.className = "pf-stepper-summary pf-stepper-summary--blocking";
    } else {
      summaryEl.textContent = "All checks passed \u2014 ready to run";
      summaryEl.className = "pf-stepper-summary pf-stepper-summary--clear";
    }
  }
  var KS_STEPS = [
    { key: "lock", label: "Validate and acquire lock" },
    { key: "branch", label: "Create sprint branch" },
    { key: "dispatch", label: "Dispatch first agents" }
  ];
  var _ksFailedStep = -1;
  var _ksLabel = null;
  var _ksRepo = null;
  function _ksInit() {
    const stepsEl = document.getElementById("smgmt-kickoff-steps");
    if (!stepsEl)
      return;
    stepsEl.innerHTML = KS_STEPS.map(
      (s) => `<div class="pf-step-item pf-step-item--pending" id="ks-step-${s.key}">
      <span class="pf-step-icon" aria-hidden="true"></span>
      <div class="pf-step-content">
        <span class="pf-step-name">${escHtml(s.label)}</span>
        <span class="pf-step-note" id="ks-step-note-${s.key}"></span>
      </div>
    </div>`
    ).join("");
    const errEl = document.getElementById("smgmt-kickoff-error");
    if (errEl)
      errEl.hidden = true;
  }
  function _ksSetStep(key, state, note) {
    const item = document.getElementById(`ks-step-${key}`);
    if (!item)
      return;
    item.className = `pf-step-item pf-step-item--${state}`;
    const noteEl = document.getElementById(`ks-step-note-${key}`);
    if (noteEl)
      noteEl.textContent = note || "";
  }
  function _ksShow(label, repo) {
    _ksLabel = label;
    _ksRepo = repo;
    _ksFailedStep = -1;
    _ksInit();
    const shell = document.getElementById("smgmt-kickoff-shell");
    const runShell = document.getElementById("smgmt-run-shell");
    const emptyEl = document.getElementById("smgmt-running-empty");
    if (emptyEl)
      emptyEl.hidden = true;
    if (runShell)
      runShell.hidden = true;
    if (shell)
      shell.hidden = false;
    if (typeof _smgmtShowSubView === "function")
      _smgmtShowSubView("running");
  }
  function _ksHide() {
    const shell = document.getElementById("smgmt-kickoff-shell");
    if (shell)
      shell.hidden = true;
  }
  function _ksShowError(stepKey, msg) {
    _ksSetStep(stepKey, "fail", msg);
    const errEl = document.getElementById("smgmt-kickoff-error");
    if (!errEl)
      return;
    const msgEl = document.getElementById("smgmt-kickoff-error-msg");
    if (msgEl)
      msgEl.textContent = msg || "An error occurred";
    errEl.hidden = false;
  }
  async function _ksIsRunning(label) {
    try {
      const res = await fetch("/api/sprints/running-all");
      if (!res.ok)
        return false;
      const data = await res.json();
      return (data.running || []).some((r) => r.sprint_label === label);
    } catch (_) {
      return false;
    }
  }
  async function _ksStep1Post() {
    const label = _ksLabel;
    const repo = _ksRepo;
    _ksSetStep("lock", "checking", "");
    try {
      const res = await fetch("/api/sprints/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: repo, sprint_label: label, use_cline_followups: _pfUseClineFollowups })
      });
      if (!res.ok) {
        let detail = await res.text();
        try {
          const p = JSON.parse(detail);
          detail = typeof p.detail === "string" ? p.detail : JSON.stringify(p.detail);
        } catch (_) {
        }
        _ksShowError("lock", detail || `HTTP ${res.status}`);
        _ksFailedStep = 0;
        return false;
      }
      _ksSetStep("lock", "pass", "");
      return true;
    } catch (e) {
      _ksShowError("lock", e.message);
      _ksFailedStep = 0;
      return false;
    }
  }
  async function _ksStep2Branch() {
    _ksSetStep("branch", "checking", "");
    const deadline = Date.now() + 3e4;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 1e3));
      if (await _ksIsRunning(_ksLabel)) {
        _ksSetStep("branch", "pass", "");
        return true;
      }
    }
    _ksShowError("branch", "Timed out waiting for sprint process to start");
    _ksFailedStep = 1;
    return false;
  }
  async function _ksStep3Dispatch() {
    const label = _ksLabel;
    const repo = _ksRepo;
    _ksSetStep("dispatch", "checking", "");
    const deadline = Date.now() + 9e4;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 2e3));
      try {
        const res = await fetch(`/api/sprint-status?project=${encodeURIComponent(repo)}`);
        if (res.ok) {
          const data = await res.json();
          const sprint = (data.running_sprints || []).find((s) => s.sprint_label === label);
          if (sprint && sprint.issues && sprint.issues.length > 0) {
            _ksSetStep("dispatch", "pass", "");
            return true;
          }
          if (!sprint && !await _ksIsRunning(label)) {
            _ksShowError("dispatch", "Sprint terminated before agents were dispatched");
            _ksFailedStep = 2;
            return false;
          }
        }
      } catch (_) {
      }
    }
    _ksSetStep("dispatch", "pass", "");
    return true;
  }
  async function _ksFinish(label) {
    _ksHide();
    _smgmtShowToast(`Sprint ${sprintLabelDisplay(label)} dispatched`);
    if (typeof _smgmtShowSubView === "function")
      _smgmtShowSubView("running");
    await loadSprintMgmt(true, label);
    if (typeof _smgmtLivePollRestart === "function")
      _smgmtLivePollRestart();
    for (let i = 0; i < 8; i++) {
      if (_smgmtRunningLabels && _smgmtRunningLabels.has(label))
        break;
      await new Promise((r) => setTimeout(r, 600));
      await loadSprintMgmt(true, label);
    }
  }
  async function smgmtKickoffRun(label, repo) {
    _ksShow(label, repo);
    if (!await _ksStep1Post())
      return;
    if (!await _ksStep2Branch())
      return;
    if (!await _ksStep3Dispatch())
      return;
    await _ksFinish(label);
  }
  async function smgmtKickoffRetry() {
    if (!_ksLabel || !_ksRepo)
      return;
    const failedStep = _ksFailedStep;
    const label = _ksLabel;
    const errEl = document.getElementById("smgmt-kickoff-error");
    if (errEl)
      errEl.hidden = true;
    _ksFailedStep = -1;
    if (failedStep <= 0) {
      _ksSetStep("lock", "pending", "");
      _ksSetStep("branch", "pending", "");
      _ksSetStep("dispatch", "pending", "");
      if (!await _ksStep1Post())
        return;
      if (!await _ksStep2Branch())
        return;
      if (!await _ksStep3Dispatch())
        return;
    } else if (failedStep === 1) {
      _ksSetStep("branch", "pending", "");
      _ksSetStep("dispatch", "pending", "");
      if (!await _ksStep2Branch())
        return;
      if (!await _ksStep3Dispatch())
        return;
    } else {
      _ksSetStep("dispatch", "pending", "");
      if (!await _ksStep3Dispatch())
        return;
    }
    await _ksFinish(label);
  }

  // apps/dashboard/static/src/sprint-board/drag-drop.js
  function isDragBlocked(state) {
    return !!(state && state.moveLock);
  }
  function computeDropPlan(dragInfo, targetLabel) {
    if (!dragInfo)
      return { mode: "none", tickets: [], targetLabel, noop: true };
    if (dragInfo.multi && dragInfo.multi.length > 1) {
      return { mode: "multi", tickets: dragInfo.multi.slice(), targetLabel, noop: false };
    }
    const noop = dragInfo.fromSprint === targetLabel;
    return { mode: "single", tickets: noop ? [] : [dragInfo.number], targetLabel, noop };
  }
  function _smgmtUpdateSelectionUI2() {
    const count = _smgmtSelectedIssues.size;
    _blUpdateActions();
    document.getElementById("smgmt-selection-bar")?.remove();
    const bar = document.getElementById("proj-selection-bar");
    const listEl = document.getElementById("smgmt-sprint-list");
    const onSprintTab = typeof _activeTab === "undefined" || _activeTab === "sprint-mgmt";
    if (count > 0 && bar && onSprintTab) {
      bar.classList.add("show");
      bar.classList.remove("hidden");
      if (listEl)
        listEl.classList.add("has-selection");
      const countEl = document.getElementById("smgmt-sel-count");
      if (countEl)
        countEl.textContent = count === 1 ? "1 issue selected" : `${count} issues selected`;
      const deleteBtn = document.getElementById("smgmt-sel-delete-btn");
      if (deleteBtn) {
        const showDelete = count === 1 && _smgmtIsDeletableIssue([..._smgmtSelectedIssues][0]);
        deleteBtn.classList.toggle("show", showDelete);
      }
    } else {
      if (bar) {
        bar.classList.remove("show");
        bar.classList.add("hidden");
      }
      if (listEl)
        listEl.classList.remove("has-selection");
    }
    if (typeof _smgmtUpdateToolbarTop === "function") {
      _smgmtUpdateToolbarTop();
      requestAnimationFrame(_smgmtUpdateToolbarTop);
    }
  }
  function _smgmtPopulateSelectionDropdown() {
  }
  function _smgmtPopulateMoveToMenu() {
  }
  function _smgmtToggleMoveToMenu(event) {
    event?.stopPropagation();
    if (typeof _smgmtMoveToModalOpen === "function")
      _smgmtMoveToModalOpen();
  }
  function _smgmtCloseMoveToMenu() {
  }
  function _smgmtClearSelection() {
    _smgmtSelectedIssues.forEach((num) => {
      const el = document.getElementById(`smgmt-ticket-${num}`);
      if (el) {
        el.classList.remove("is-selected");
        const cb = el.querySelector(".smgmt-ticket-cb");
        if (cb)
          cb.checked = false;
      }
    });
    _smgmtSelectedIssues.clear();
    _smgmtUpdateSelectionUI2();
  }
  function _smgmtSetSelected(number, selected) {
    if (selected)
      _smgmtSelectedIssues.add(number);
    else
      _smgmtSelectedIssues.delete(number);
    const el = document.getElementById(`smgmt-ticket-${number}`);
    if (el) {
      el.classList.toggle("is-selected", selected);
      const cb = el.querySelector(".smgmt-ticket-cb");
      if (cb)
        cb.checked = selected;
    }
  }
  function _smgmtTicketSprintKey(number) {
    const iss = (_smgmtData?.issues || []).find((i) => i.number === number);
    if (!iss)
      return void 0;
    return iss.sprint == null ? "backlog" : iss.sprint;
  }
  function _smgmtSelectionSprintKey() {
    const first = [..._smgmtSelectedIssues][0];
    return first == null ? void 0 : _smgmtTicketSprintKey(first);
  }
  function _smgmtEnforceSelectionScope(number) {
    if (_smgmtSelectedIssues.size === 0)
      return;
    const cur = _smgmtSelectionSprintKey();
    const next = _smgmtTicketSprintKey(number);
    if (cur !== void 0 && next !== void 0 && cur !== next) {
      _smgmtClearSelection();
    }
  }
  function _smgmtToggleSelect(number, checked) {
    if (checked)
      _smgmtEnforceSelectionScope(number);
    _smgmtSetSelected(number, checked);
    _smgmtLastSelectedNum = checked ? number : null;
    _smgmtUpdateSelectionUI2();
  }
  function _smgmtRowClick(event, number, label) {
    const container = label ? document.getElementById(`smgmt-tickets-${label}`) : document.getElementById("smgmt-backlog-tickets");
    if (event.shiftKey && _smgmtLastSelectedNum != null && container) {
      const nums = Array.from(container.querySelectorAll(".smgmt-ticket[data-issue]")).map((r) => parseInt(r.dataset.issue, 10));
      const a = nums.indexOf(_smgmtLastSelectedNum);
      const b = nums.indexOf(number);
      if (a !== -1 && b !== -1) {
        const [lo, hi] = a <= b ? [a, b] : [b, a];
        for (let i = lo; i <= hi; i++)
          _smgmtSetSelected(nums[i], true);
        _smgmtLastSelectedNum = number;
        _smgmtUpdateSelectionUI2();
        const sel = window.getSelection && window.getSelection();
        if (sel)
          sel.removeAllRanges();
        return;
      }
    }
    if (event.ctrlKey || event.metaKey) {
      const nowSelected2 = !_smgmtSelectedIssues.has(number);
      if (nowSelected2)
        _smgmtEnforceSelectionScope(number);
      _smgmtSetSelected(number, nowSelected2);
      _smgmtLastSelectedNum = nowSelected2 ? number : null;
      _smgmtUpdateSelectionUI2();
      return;
    }
    const nowSelected = !_smgmtSelectedIssues.has(number);
    if (nowSelected)
      _smgmtEnforceSelectionScope(number);
    _smgmtSetSelected(number, nowSelected);
    _smgmtLastSelectedNum = nowSelected ? number : null;
    _smgmtUpdateSelectionUI2();
  }
  function _smgmtIsDeletableIssue(num) {
    if (!_smgmtData)
      return false;
    const iss = _smgmtData.issues.find((i) => i.number === num);
    if (!iss)
      return false;
    return iss.status === "done" || iss.sprint === null;
  }
  async function _smgmtDeleteSelected() {
    if (_smgmtSelectedIssues.size !== 1)
      return;
    const num = [..._smgmtSelectedIssues][0];
    const repo = _smgmtRepo();
    if (!repo)
      return;
    const iss = _smgmtData?.issues.find((i) => i.number === num);
    const label = iss ? `#${num}: ${iss.title}` : `#${num}`;
    if (!confirm(`Delete ${label}?

This will close the issue on GitHub. This cannot be undone.`))
      return;
    if (_smgmtData)
      _smgmtData.issues = _smgmtData.issues.filter((i) => i.number !== num);
    _smgmtClearSelection();
    _smgmtRender(_smgmtData);
    _smgmtBoardLock2(`Deleting #${num}\u2026`);
    try {
      const res = await fetch(`/api/issues/${num}/close?repo=${encodeURIComponent(repo)}`, {
        method: "POST"
      });
      if (!res.ok)
        throw new Error(await res.text());
      _smgmtShowToast(`Issue #${num} closed.`);
    } catch (e) {
      alert("Failed to delete issue: " + e.message);
      await loadSprintMgmt();
    } finally {
      _smgmtBoardUnlock2();
    }
  }
  async function _smgmtMoveSelectedTo(targetLabel) {
    if (!targetLabel || _smgmtSelectedIssues.size === 0)
      return;
    const repo = _smgmtRepo();
    if (!repo)
      return;
    const nums = Array.from(_smgmtSelectedIssues);
    const changes = nums.map((n) => ({ issue_num: n, sprint_label: targetLabel }));
    const dest = targetLabel === "backlog" ? "Backlog" : `Sprint ${targetLabel.split("-")[1]}`;
    if (_smgmtData) {
      const targetNum = targetLabel === "backlog" ? null : parseInt(targetLabel.split("-")[1], 10);
      nums.forEach((n) => {
        const iss = _smgmtData.issues.find((i) => i.number === n);
        if (iss)
          iss.sprint = targetNum;
      });
      _smgmtClearSelection();
      _smgmtRender(_smgmtData);
    }
    _smgmtBoardLock2(`Moving ${nums.length} ticket${nums.length !== 1 ? "s" : ""} to ${dest}\u2026`);
    try {
      const res = await fetch("/api/sprints/batch-labels", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ changes, project: repo })
      });
      if (!res.ok)
        throw new Error(await res.text());
      const data = await res.json();
      if (data.failed > 0 && data.errors && data.errors.length > 0) {
        _smgmtShowInlineError(`${data.failed} ticket${data.failed !== 1 ? "s" : ""} failed to move:
${data.errors.join("\n")}`);
      } else if (data.applied > 0) {
        _smgmtShowToast(`Moved ${data.applied} ticket${data.applied !== 1 ? "s" : ""} to ${dest}.`);
      }
      await loadSprintMgmt();
    } catch (e) {
      _smgmtShowToast("Failed to move tickets: " + e.message);
      await loadSprintMgmt();
    } finally {
      _smgmtBoardUnlock2();
    }
  }
  function _smgmtTicketDragStart(event, issueNum, fromSprint) {
    if (fromSprint) {
      const card = document.getElementById(`smgmt-card-${fromSprint}`);
      if (card && card.querySelector(".smgmt-rename-wrap")) {
        event.preventDefault();
        return;
      }
    }
    const isChecked = _smgmtSelectedIssues.has(issueNum);
    if (isChecked && _smgmtSelectedIssues.size > 1) {
      const nums = Array.from(_smgmtSelectedIssues);
      const sprints = new Set(nums.map((n) => {
        const iss = (_smgmtData?.issues || []).find((i) => i.number === n);
        return iss ? iss.sprint : null;
      }));
      _smgmtDragTicket = {
        number: issueNum,
        fromSprint: fromSprint || null,
        multi: nums,
        multiSprints: sprints.size
      };
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", nums.join(","));
      const pill = document.getElementById("smgmt-drag-pill");
      if (pill) {
        const label = sprints.size > 1 ? `Moving ${nums.length} tickets from ${sprints.size} sprints` : `Moving ${nums.length} tickets`;
        pill.textContent = label;
        pill.style.top = event.clientY - 20 + "px";
        pill.style.left = event.clientX + 12 + "px";
      }
      setTimeout(() => {
        nums.forEach((n) => {
          const el = document.getElementById(`smgmt-ticket-${n}`);
          if (el)
            el.classList.add("dragging-ticket");
        });
      }, 0);
    } else {
      _smgmtDragTicket = { number: issueNum, fromSprint: fromSprint || null, multi: null };
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", String(issueNum));
      const el = document.getElementById(`smgmt-ticket-${issueNum}`);
      if (el)
        setTimeout(() => el.classList.add("dragging-ticket"), 0);
    }
    _smgmtGhostShow();
  }
  function _smgmtDragMovePill(event) {
    if (_smgmtDragTicket?.multi) {
      const pill = document.getElementById("smgmt-drag-pill");
      if (pill && pill.textContent) {
        pill.style.top = event.clientY - 20 + "px";
        pill.style.left = event.clientX + 12 + "px";
      }
    }
  }
  function _smgmtGhostComputeNextFree() {
    if (_smgmtData && Number.isInteger(_smgmtData.placeholder_sprint)) {
      return _smgmtData.placeholder_sprint;
    }
    const nums = (_smgmtData?.sprints || []).map(Number).filter((n) => !isNaN(n));
    return nums.length ? Math.max(...nums) + 1 : 1;
  }
  function _smgmtGhostShow() {
    if (_smgmtRunningLabels.size > 0) {
      showToast("Cannot create new sprint while one is running.", "warning");
      return;
    }
    _smgmtGhostNextNum = _smgmtGhostComputeNextFree();
    const ghost = document.getElementById("smgmt-ghost-pane");
    const titleEl = document.getElementById("smgmt-ghost-title");
    const subEl = document.getElementById("smgmt-ghost-sub");
    if (!ghost)
      return;
    titleEl.textContent = `Drop here to create Sprint ${_smgmtGhostNextNum}`;
    subEl.textContent = "next sprint number";
    ghost.classList.add("ghost-visible");
  }
  function _smgmtGhostHide() {
    const ghost = document.getElementById("smgmt-ghost-pane");
    if (!ghost)
      return;
    ghost.classList.remove("ghost-visible", "ghost-hot");
    _smgmtGhostNextNum = null;
  }
  function _smgmtGhostDragOver(event) {
    if (!_smgmtDragTicket)
      return;
    if (_smgmtRunningLabels.size > 0)
      return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    const ghost = document.getElementById("smgmt-ghost-pane");
    if (!ghost)
      return;
    const titleEl = document.getElementById("smgmt-ghost-title");
    const subEl = document.getElementById("smgmt-ghost-sub");
    ghost.classList.add("ghost-hot");
    if (titleEl)
      titleEl.textContent = `Release to create Sprint ${_smgmtGhostNextNum}`;
    if (subEl)
      subEl.textContent = "you'll be asked to confirm";
  }
  function _smgmtGhostDragLeave(event) {
    const ghost = document.getElementById("smgmt-ghost-pane");
    if (!ghost)
      return;
    if (!ghost.contains(event.relatedTarget)) {
      ghost.classList.remove("ghost-hot");
      const titleEl = document.getElementById("smgmt-ghost-title");
      const subEl = document.getElementById("smgmt-ghost-sub");
      if (titleEl)
        titleEl.textContent = `Drop here to create Sprint ${_smgmtGhostNextNum}`;
      const existing = new Set((_smgmtData?.sprints || []).map((n) => Number(n)));
      const skipped = [];
      for (let i = 1; i < _smgmtGhostNextNum; i++) {
        if (!existing.has(i))
          skipped.push(i);
      }
      if (subEl)
        subEl.textContent = skipped.length > 0 ? `next free number \xB7 skipped empty ${skipped.map((s) => `Sprint ${s}`).join(", ")}` : "next free number";
    }
  }
  async function _smgmtGhostDrop(event) {
    event.preventDefault();
    if (!_smgmtDragTicket)
      return;
    if (_smgmtRunningLabels.size > 0)
      return;
    const dragInfo = _smgmtDragTicket;
    const nextNum = _smgmtGhostNextNum;
    _smgmtGhostHide();
    if (dragInfo.multi && dragInfo.multi.length > 1) {
      return;
    }
    const dragEl = document.getElementById(`smgmt-ticket-${dragInfo.number}`);
    if (dragEl)
      dragEl.classList.remove("dragging-ticket");
    _smgmtDragTicket = null;
    if (nextNum == null)
      return;
    const repo = _smgmtRepo();
    if (!repo)
      return;
    const sprintLabel = `sprint-${nextNum}`;
    const issue = (_smgmtData?.issues || []).find((i) => i.number === dragInfo.number);
    const fromLabel = dragInfo.fromSprint || "backlog";
    document.getElementById("gc-sprint-name").textContent = sprintLabel;
    document.getElementById("gc-ticket-info").textContent = issue ? `#${issue.number} \u2014 ${issue.title}` : `#${dragInfo.number}`;
    document.getElementById("gc-source-pane").textContent = fromLabel === "backlog" ? "Backlog" : `Sprint ${fromLabel.replace("sprint-", "")}`;
    const confirmBtn = document.getElementById("gc-confirm-btn");
    confirmBtn.textContent = `Create ${sprintLabel} & move`;
    confirmBtn.disabled = false;
    const errEl = document.getElementById("gc-error");
    errEl.textContent = "";
    errEl.classList.add("hidden");
    document.getElementById("gc-modal").dataset.issueNum = String(dragInfo.number);
    document.getElementById("gc-modal").dataset.fromSprint = fromLabel;
    document.getElementById("gc-modal").dataset.sprintNum = String(nextNum);
    document.getElementById("gc-modal").dataset.repo = repo;
    document.getElementById("gc-backdrop").classList.remove("hidden");
    document.getElementById("gc-modal").classList.remove("hidden");
    confirmBtn.focus();
  }
  function _gcClose() {
    document.getElementById("gc-backdrop").classList.add("hidden");
    document.getElementById("gc-modal").classList.add("hidden");
  }
  async function _gcConfirm() {
    const modal = document.getElementById("gc-modal");
    const issueNum = parseInt(modal.dataset.issueNum, 10);
    const sprintNum = parseInt(modal.dataset.sprintNum, 10);
    const repo = modal.dataset.repo;
    const sprintLabel = `sprint-${sprintNum}`;
    const confirmBtn = document.getElementById("gc-confirm-btn");
    const errEl = document.getElementById("gc-error");
    confirmBtn.disabled = true;
    confirmBtn.textContent = "Creating\u2026";
    errEl.classList.add("hidden");
    try {
      const createRes = await fetch("/api/sprints/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: repo, sprint_number: sprintNum })
      });
      if (!createRes.ok && createRes.status !== 409) {
        const d = await createRes.json().catch(() => ({}));
        throw new Error(d.detail || "HTTP " + createRes.status);
      }
      const moveRes = await fetch(`/api/issues/${issueNum}/sprint-label`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sprint_label: sprintLabel, project: repo })
      });
      if (!moveRes.ok) {
        const d = await moveRes.json().catch(() => ({}));
        throw new Error(d.detail || "HTTP " + moveRes.status);
      }
      _gcClose();
      await loadSprintMgmt();
    } catch (e) {
      errEl.textContent = `Failed: ${e.message}`;
      errEl.classList.remove("hidden");
      confirmBtn.disabled = false;
      confirmBtn.textContent = `Create ${sprintLabel} & move`;
    }
  }
  function _smgmtTicketDragEnd(_event) {
    if (_smgmtDragTicket) {
      if (_smgmtDragTicket.multi) {
        _smgmtDragTicket.multi.forEach((n) => {
          const el = document.getElementById(`smgmt-ticket-${n}`);
          if (el)
            el.classList.remove("dragging-ticket");
        });
      } else {
        const el = document.getElementById(`smgmt-ticket-${_smgmtDragTicket.number}`);
        if (el)
          el.classList.remove("dragging-ticket");
      }
    }
    const pill = document.getElementById("smgmt-drag-pill");
    if (pill) {
      pill.style.top = "-100px";
      pill.style.left = "-100px";
      pill.textContent = "";
    }
    _smgmtGhostHide();
    _smgmtDragTicket = null;
    document.querySelectorAll(".smgmt-sprint-card").forEach((el) => el.classList.remove("drag-over-sprint"));
    document.getElementById("smgmt-backlog-pane")?.classList.remove("drag-over-backlog");
  }
  function _smgmtDragOver(event, sprintLabel) {
    if (_smgmtDragTicket) {
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      document.querySelectorAll(".smgmt-sprint-card").forEach((b) => b.classList.remove("drag-over-sprint"));
      document.getElementById("smgmt-backlog-pane")?.classList.remove("drag-over-backlog");
      const target = document.getElementById(`smgmt-card-${sprintLabel}`);
      if (target)
        target.classList.add("drag-over-sprint");
    }
  }
  function _smgmtDragLeave(event) {
    if (event.currentTarget && !event.currentTarget.contains(event.relatedTarget)) {
      event.currentTarget.classList.remove("drag-over-sprint");
    }
  }
  async function _smgmtDropOnSprint(event, targetLabel) {
    event.preventDefault();
    document.querySelectorAll(".smgmt-sprint-card").forEach((el) => el.classList.remove("drag-over-sprint"));
    document.getElementById("smgmt-backlog-pane")?.classList.remove("drag-over-backlog");
    if (isDragBlocked({ moveLock: _smgmtMoveLock }))
      return;
    if (!_smgmtDragTicket)
      return;
    const dragInfo = _smgmtDragTicket;
    _smgmtDragTicket = null;
    const repo = _smgmtRepo();
    if (!repo)
      return;
    if (dragInfo.multi && dragInfo.multi.length > 1) {
      const nums = dragInfo.multi;
      const targetNum = targetLabel ? parseInt(targetLabel.split("-")[1], 10) : null;
      if (_smgmtData) {
        nums.forEach((n) => {
          const iss = _smgmtData.issues.find((i) => i.number === n);
          if (iss)
            iss.sprint = targetNum;
        });
      }
      _smgmtClearSelection();
      if (_smgmtData)
        _smgmtRender(_smgmtData);
      const changes = nums.map((n) => ({ issue_num: n, sprint_label: targetLabel || "backlog" }));
      _smgmtBoardLock2();
      try {
        const res = await fetch("/api/sprints/batch-labels", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ changes, project: repo })
        });
        if (!res.ok)
          throw new Error(await res.text());
        await loadSprintMgmt();
      } catch (e) {
        alert(`Failed to move tickets: ${e.message}`);
        await loadSprintMgmt();
      } finally {
        _smgmtBoardUnlock2();
      }
    } else {
      const { number, fromSprint } = dragInfo;
      if (fromSprint === targetLabel)
        return;
      const targetNum = targetLabel ? parseInt(targetLabel.split("-")[1], 10) : null;
      if (_smgmtData) {
        const iss = _smgmtData.issues.find((i) => i.number === number);
        if (iss)
          iss.sprint = targetNum;
        _smgmtRender(_smgmtData);
      }
      _smgmtClearSelection();
      _smgmtBoardLock2();
      try {
        const res = await fetch(`/api/issues/${number}/sprint-label`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sprint_label: targetLabel || "backlog", project: repo })
        });
        if (!res.ok)
          throw new Error(await res.text());
        await loadSprintMgmt();
      } catch (e) {
        if (_smgmtData) {
          const iss = _smgmtData.issues.find((i) => i.number === number);
          if (iss)
            iss.sprint = fromSprint ? parseInt(fromSprint.split("-")[1], 10) : null;
          _smgmtRender(_smgmtData);
        }
        alert(`Failed to move ticket #${number}: ${e.message}`);
      } finally {
        _smgmtBoardUnlock2();
      }
    }
  }
  function _smgmtTicketReorderDragOver(event) {
    if (!_smgmtDragTicket || _smgmtDragTicket.multi && _smgmtDragTicket.multi.length > 1)
      return;
    const target = event.currentTarget;
    const targetSprint = target.dataset.sprint;
    const dragSprint = _smgmtDragTicket ? _smgmtDragTicket.fromSprint : null;
    if (targetSprint !== dragSprint)
      return;
    event.preventDefault();
    event.stopPropagation();
    const rect = target.getBoundingClientRect();
    const midY = rect.top + rect.height / 2;
    target.classList.remove("drag-before", "drag-after");
    target.classList.add(event.clientY < midY ? "drag-before" : "drag-after");
  }
  function _smgmtTicketReorderDragLeave(event) {
    event.currentTarget.classList.remove("drag-before", "drag-after");
  }
  async function _smgmtTicketReorderDrop(event, targetIssue, sprintLabel) {
    if (!_smgmtDragTicket || _smgmtDragTicket.multi && _smgmtDragTicket.multi.length > 1)
      return;
    const dragInfo = _smgmtDragTicket;
    if (dragInfo.fromSprint !== sprintLabel)
      return;
    const dragIssue = dragInfo.number;
    if (dragIssue === targetIssue) {
      event.currentTarget.classList.remove("drag-before", "drag-after");
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const rect = event.currentTarget.getBoundingClientRect();
    const insertAfter = event.clientY >= rect.top + rect.height / 2;
    event.currentTarget.classList.remove("drag-before", "drag-after");
    const repo = _smgmtRepo();
    if (!repo || !_smgmtData)
      return;
    const container = document.getElementById(`smgmt-tickets-${sprintLabel}`);
    if (!container)
      return;
    const rows = Array.from(container.querySelectorAll(".smgmt-ticket[data-issue]"));
    let order = rows.map((r) => parseInt(r.dataset.issue, 10)).filter((n) => !isNaN(n));
    order = order.filter((n) => n !== dragIssue);
    const insertIdx = order.indexOf(targetIssue) + (insertAfter ? 1 : 0);
    order.splice(insertIdx, 0, dragIssue);
    _smgmtDragTicket = null;
    try {
      const res = await fetch(
        `/api/sprints/${encodeURIComponent(sprintLabel)}/plan?project=${encodeURIComponent(repo)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(order)
        }
      );
      if (!res.ok)
        throw new Error(await res.text());
      await loadSprintMgmt();
    } catch (e) {
      alert(`Failed to reorder tickets: ${e.message}`);
      await loadSprintMgmt();
    }
  }
  function _smgmtBacklogTicketDragStart(event, issueNum) {
    const isChecked = _smgmtSelectedIssues.has(issueNum);
    if (isChecked && _smgmtSelectedIssues.size > 1) {
      const nums = Array.from(_smgmtSelectedIssues);
      _smgmtDragTicket = { number: issueNum, fromSprint: null, multi: nums, multiSprints: 1 };
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", nums.join(","));
      const pill = document.getElementById("smgmt-drag-pill");
      if (pill) {
        pill.textContent = `Moving ${nums.length} tickets`;
        pill.style.top = event.clientY - 20 + "px";
        pill.style.left = event.clientX + 12 + "px";
      }
      setTimeout(() => {
        nums.forEach((n) => {
          const el = document.getElementById(`smgmt-ticket-${n}`);
          if (el)
            el.classList.add("dragging-ticket");
        });
      }, 0);
    } else {
      _smgmtDragTicket = { number: issueNum, fromSprint: null, multi: null };
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", String(issueNum));
      const el = document.getElementById(`smgmt-ticket-${issueNum}`);
      if (el)
        setTimeout(() => el.classList.add("dragging-ticket"), 0);
    }
    _smgmtGhostShow();
  }
  function _smgmtBacklogDragOver(event) {
    if (_smgmtDragTicket && _smgmtDragTicket.fromSprint !== null) {
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      document.getElementById("smgmt-backlog-pane")?.classList.add("drag-over-backlog");
    }
  }
  function _smgmtBacklogDragLeave(event) {
    const pane = document.getElementById("smgmt-backlog-pane");
    if (pane && !pane.contains(event.relatedTarget)) {
      pane.classList.remove("drag-over-backlog");
    }
  }
  async function _smgmtDropOnBacklog(event) {
    event.preventDefault();
    document.getElementById("smgmt-backlog-pane")?.classList.remove("drag-over-backlog");
    if (isDragBlocked({ moveLock: _smgmtMoveLock }))
      return;
    if (!_smgmtDragTicket)
      return;
    const dragInfo = _smgmtDragTicket;
    _smgmtDragTicket = null;
    if (!dragInfo.fromSprint)
      return;
    const repo = _smgmtRepo();
    if (!repo)
      return;
    if (dragInfo.multi && dragInfo.multi.length > 1) {
      const nums = dragInfo.multi;
      if (_smgmtData) {
        nums.forEach((n) => {
          const iss = _smgmtData.issues.find((i) => i.number === n);
          if (iss)
            iss.sprint = null;
        });
      }
      _smgmtClearSelection();
      if (_smgmtData)
        _smgmtRender(_smgmtData);
      const changes = nums.map((n) => ({ issue_num: n, sprint_label: "backlog" }));
      _smgmtBoardLock2();
      try {
        const res = await fetch("/api/sprints/batch-labels", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ changes, project: repo })
        });
        if (!res.ok)
          throw new Error(await res.text());
        await loadSprintMgmt();
      } catch (e) {
        alert(`Failed to move tickets to backlog: ${e.message}`);
        await loadSprintMgmt();
      } finally {
        _smgmtBoardUnlock2();
      }
    } else {
      const { number, fromSprint } = dragInfo;
      if (_smgmtData) {
        const iss = _smgmtData.issues.find((i) => i.number === number);
        if (iss)
          iss.sprint = null;
        _smgmtRender(_smgmtData);
      }
      _smgmtClearSelection();
      _smgmtBoardLock2();
      try {
        const res = await fetch(`/api/issues/${number}/sprint-label`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sprint_label: "backlog", project: repo })
        });
        if (!res.ok)
          throw new Error(await res.text());
        await loadSprintMgmt();
      } catch (e) {
        if (_smgmtData) {
          const iss = _smgmtData.issues.find((i) => i.number === number);
          if (iss)
            iss.sprint = fromSprint ? parseInt(fromSprint.split("-")[1], 10) : null;
          _smgmtRender(_smgmtData);
        }
        alert(`Failed to move ticket #${number} to backlog: ${e.message}`);
      } finally {
        _smgmtBoardUnlock2();
      }
    }
  }
  function _smgmtBoardLock2(message, opts) {
    _smgmtMoveLock = true;
    _smgmtArStopTicker();
    const overlay = document.getElementById("smgmt-move-overlay");
    const msgEl = document.getElementById("smgmt-move-overlay-msg");
    const progWrap = document.getElementById("smgmt-op-progress-wrap");
    const logEl = document.getElementById("smgmt-op-log");
    const text = message || "Moving\u2026";
    if (msgEl)
      msgEl.textContent = text;
    if (overlay) {
      overlay.setAttribute("aria-label", text.replace(/…$/, "") + ", please wait");
      overlay.classList.add("active");
    }
    const showProgress = !!(opts && opts.progress);
    if (progWrap)
      progWrap.hidden = !showProgress;
    if (logEl) {
      logEl.hidden = !showProgress;
      if (showProgress && opts.clearLog)
        logEl.innerHTML = "";
    }
    if (showProgress && opts.total != null) {
      _smgmtBoardProgress2(0, opts.total);
    } else if (!showProgress) {
      _smgmtBoardProgress2(0, 1);
    }
  }
  function _smgmtBoardProgress2(done, total) {
    const fill = document.getElementById("smgmt-op-progress-fill");
    const pctEl = document.getElementById("smgmt-op-progress-pct");
    const pct = total > 0 ? Math.round(done / total * 100) : 0;
    if (fill)
      fill.style.width = pct + "%";
    if (pctEl)
      pctEl.textContent = pct + "%";
  }
  function _smgmtBoardLog2(line, kind) {
    const logEl = document.getElementById("smgmt-op-log");
    if (!logEl)
      return;
    const row = document.createElement("div");
    row.className = "smgmt-op-log-line" + (kind ? ` smgmt-op-log-line--${kind}` : "");
    row.textContent = line;
    logEl.appendChild(row);
    logEl.scrollTop = logEl.scrollHeight;
  }
  function _smgmtBoardUnlock2() {
    _smgmtMoveLock = false;
    const overlay = document.getElementById("smgmt-move-overlay");
    if (overlay)
      overlay.classList.remove("active");
    const progWrap = document.getElementById("smgmt-op-progress-wrap");
    const logEl = document.getElementById("smgmt-op-log");
    if (progWrap)
      progWrap.hidden = true;
    if (logEl) {
      logEl.hidden = true;
      logEl.innerHTML = "";
    }
    _smgmtBoardProgress2(0, 1);
    if (_arInterval > 0)
      _smgmtArStartTicker();
  }

  // apps/dashboard/static/src/sprint-board/board-render.js
  var _smgmtResolvedAncestors = /* @__PURE__ */ new Set();
  async function loadSprintMgmt2(silent, optimisticRunningLabel) {
    const listEl = document.getElementById("smgmt-sprint-list");
    if (!listEl)
      return;
    const repo = _cachedFullRepo[_slug] || null;
    if (!repo) {
      listEl.innerHTML = '<div class="loading-msg">Project not found.</div>';
      return;
    }
    if (!silent) {
      listEl.innerHTML = '<div class="loading-msg">Loading sprints\u2026</div>';
      for (const k of Object.keys(_smgmtFinishCards))
        delete _smgmtFinishCards[k];
    }
    try {
      if (typeof _smgmtEnsureCapData === "function") {
        await _smgmtEnsureCapData();
      }
      const [resp, runningResp] = await Promise.all([
        fetch("/api/sprint-management/issues?repo=" + encodeURIComponent(repo)),
        fetch("/api/sprints/running-all").catch(() => null)
      ]);
      if (!resp.ok) {
        let msg = "Failed to load sprints.";
        const d = await resp.json().catch(() => null);
        const detail = d && typeof d.detail === "string" ? d.detail : "";
        if (resp.status === 429 || /rate limit/i.test(detail)) {
          msg = detail || "GitHub API rate limit reached \u2014 retry shortly.";
        }
        throw new Error(msg);
      }
      const data = await resp.json();
      if (_smgmtLiveCacheRepo !== repo) {
        _smgmtLiveCacheRepo = repo;
        for (const k of Object.keys(_smgmtLiveCache))
          delete _smgmtLiveCache[k];
      }
      if (typeof _smgmtLingerRestore === "function")
        _smgmtLingerRestore(repo);
      const prevRunning = new Set(_smgmtRunningLabels);
      _smgmtRunningLabels = /* @__PURE__ */ new Set();
      _smgmtAnySprintRunning = false;
      if (runningResp && runningResp.ok) {
        const runningData = await runningResp.json();
        const running = runningData.running || [];
        running.forEach((r) => {
          if (r.project === repo) {
            _smgmtRunningLabels.add(r.sprint_label);
          }
        });
        _smgmtAnySprintRunning = _smgmtRunningLabels.size > 0;
      }
      for (const label of prevRunning) {
        if (!_smgmtRunningLabels.has(label) && typeof _smgmtLingerStart === "function") {
          _smgmtLingerStart(label);
        }
      }
      if (optimisticRunningLabel) {
        _smgmtRunningLabels.add(optimisticRunningLabel);
        _smgmtAnySprintRunning = true;
      }
      _smgmtRender2(data);
      if (typeof _smgmtHydrateSchedToggles === "function") {
        _smgmtHydrateSchedToggles(repo);
      }
      _smgmtLivePollRestart();
      const lingerLbl = typeof _smgmtPrimaryRunningLabel === "function" ? _smgmtPrimaryRunningLabel() : null;
      if (lingerLbl && typeof _smgmtRunningViewUpdate === "function") {
        const live = typeof _smgmtLingerLive === "function" ? _smgmtLingerLive(lingerLbl) : _smgmtLiveCache[lingerLbl] || null;
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
    if (!m)
      return [Infinity];
    return m[1].split(".").map((n) => parseInt(n, 10));
  }
  function _smgmtRender2(data) {
    const listEl = document.getElementById("smgmt-sprint-list");
    if (!listEl)
      return;
    _smgmtData = data;
    _smgmtUpdateSubnav();
    const sprints = data.sprints || [];
    const order = data.order || [];
    const issues = data.issues || [];
    const bySprint = {};
    const unassigned = [];
    issues.forEach((iss) => {
      const key = iss.sprint_label || null;
      if (key != null) {
        if (!bySprint[key])
          bySprint[key] = [];
        bySprint[key].push(iss);
      } else {
        unassigned.push(iss);
      }
    });
    _smgmtBySprint = bySprint;
    _smgmtRenderBacklog(unassigned);
    if (order.length === 0 && sprints.length === 0) {
      listEl.innerHTML = '<div class="loading-msg">No sprints yet. Create one with + New Sprint.</div>';
      return;
    }
    const _finishedSet = new Set(data.finished_sprints || []);
    const orderedLabelsRaw = order.length > 0 ? order.filter((l) => /^sprint-\d+(\.\d+)*$/.test(l)) : [...sprints].sort((a, b) => a - b).map((n) => `sprint-${n}`);
    const _sprintParents = data.sprint_parents || {};
    const _rerunInto = data.sprint_rerun_into || {};
    const _smgmtWorkTickets = (tickets) => (tickets || []).filter((t) => {
      const names = (t.labels || []).map((l) => l.name);
      return !names.some(
        (n) => ["sprint-summary", "docs", "documentation"].includes(n)
      );
    });
    const _smgmtTicketSettledOnBoard = (t) => {
      const names = (t.labels || []).map((l) => l.name);
      return names.some(
        (n) => ["UAT", "UAT-approved", "released", "SIT"].includes(n)
      );
    };
    const _smgmtHideRerunParent = (label, tickets, rerunInto) => {
      if (!rerunInto[label])
        return false;
      const work = _smgmtWorkTickets(tickets);
      return work.length === 0 || work.every(_smgmtTicketSettledOnBoard);
    };
    _smgmtResolvedAncestors = /* @__PURE__ */ new Set();
    const orderedLabels = orderedLabelsRaw.filter((label) => {
      const tickets = bySprint[label] || [];
      if (_smgmtHideRerunParent(label, tickets, _rerunInto)) {
        _smgmtResolvedAncestors.add(label);
        return true;
      }
      const ticketCount = tickets.length;
      if (ticketCount > 0)
        return true;
      if (_finishedSet.has(label))
        return false;
      if (_rerunInto[label])
        return false;
      const hasChild = Object.values(_sprintParents).some(
        (parent) => parent === label
      );
      return !hasChild;
    });
    _smgmtOrderedLabels = orderedLabels;
    _smgmtFinishedLabels = _finishedSet;
    let _smgmtNextUpLabel = null;
    const sortedForNext = [...orderedLabels].sort((a, b) => {
      const ka = _smgmtSprintLabelSortKey(a);
      const kb = _smgmtSprintLabelSortKey(b);
      for (let i = 0; i < Math.max(ka.length, kb.length); i++) {
        const d = (ka[i] ?? Infinity) - (kb[i] ?? Infinity);
        if (d !== 0)
          return d;
      }
      return 0;
    });
    for (const lbl of sortedForNext) {
      if (_smgmtRunningLabels.has(lbl))
        continue;
      if (typeof _smgmtIsLinger === "function" && _smgmtIsLinger(lbl))
        continue;
      if (_smgmtFinishedLabels.has(lbl))
        continue;
      if ((bySprint[lbl] || []).length >= 1) {
        _smgmtNextUpLabel = lbl;
        break;
      }
    }
    const focusGuideEl = document.getElementById("smgmt-focus-guide");
    if (focusGuideEl) {
      focusGuideEl.innerHTML = _smgmtFocusGuideHtml(data, orderedLabels, bySprint);
    }
    const _planStates = data.sprint_plan_states || {};
    const planningLabel = orderedLabels.find((l) => {
      if (_smgmtResolvedAncestors.has(l))
        return false;
      if (_smgmtRunningLabels.has(l))
        return false;
      const ps = (_planStates[l] || "").toLowerCase();
      return ["draft", "planned", "planning"].includes(ps);
    });
    const _buildCard = (label) => {
      const tickets = bySprint[label] || [];
      if (_smgmtResolvedAncestors.has(label)) {
        const childLabel = _rerunInto[label];
        const outcome2 = _smgmtOutcomeCache[label] || null;
        return `<div class="smgmt-sprint-unit" id="smgmt-unit-${escHtml(label)}">` + _smgmtAncestorRowHtml(label, outcome2, childLabel) + `</div>`;
      }
      if (label === planningLabel) {
        return `<div class="smgmt-sprint-unit smgmt-planning-unit" id="smgmt-unit-${escHtml(label)}">` + _smgmtDraftCardHtml(label, tickets) + `</div>`;
      }
      if (_smgmtIsFreshRerunSprint(label))
        delete _smgmtOutcomeCache[label];
      const inLinger = typeof _smgmtIsLinger === "function" && _smgmtIsLinger(label);
      const outcome = _smgmtRunningLabels.has(label) || inLinger ? null : _smgmtOutcomeCache[label] || null;
      const parent = _sprintParents[label] || null;
      const cardHtml = _smgmtCardHtml(
        label,
        null,
        tickets,
        outcome,
        label === _smgmtNextUpLabel,
        parent,
        _smgmtFinishedLabels.has(label)
      );
      return `<div class="smgmt-sprint-unit" id="smgmt-unit-${escHtml(label)}">` + cardHtml + `</div>`;
    };
    const lineageLabels = orderedLabels.filter((l) => _smgmtResolvedAncestors.has(l));
    const upNextLabels = orderedLabels.filter(
      (l) => !_smgmtResolvedAncestors.has(l) && l !== planningLabel
    );
    const sectionLabel = (text, cls) => `<div class="smgmt-section-label ${cls}">${text}</div>`;
    let cards = "";
    if (lineageLabels.length > 0) {
      cards += sectionLabel("Lineage", "smgmt-section-lineage");
      cards += lineageLabels.map(_buildCard).join("");
    }
    if (upNextLabels.length > 0) {
      cards += sectionLabel("Up next", "smgmt-section-upnext");
      cards += upNextLabels.map(_buildCard).join("");
    }
    if (planningLabel) {
      cards += sectionLabel("Planning", "smgmt-section-planning smgmt-planning-section");
      cards += _buildCard(planningLabel);
    }
    listEl.innerHTML = cards || '<div class="loading-msg">No sprints found.</div>';
    _smgmtInitCapacityGauges(orderedLabels);
    _smgmtRenderAllCapBars();
    _smgmtEnsureCapData(false);
    if (_smgmtSelectedIssues.size > 0)
      _smgmtUpdateSelectionUI();
    for (const [lbl, fc] of Object.entries(_smgmtFinishCards)) {
      if (fc)
        _smgmtRenderFinishCard(lbl, fc.card, fc.branch, _smgmtRepo());
    }
    _smgmtLoadFinishCards();
    _smgmtFetchMissingOutcomes(orderedLabels, bySprint);
    _smgmtLoadEstimates(orderedLabels, bySprint);
    _smgmtCheckEstimatorHealth();
    _smgmtLoadGoals(orderedLabels);
    _preflightLoadBanners(orderedLabels, bySprint);
    _smgmtLoadConflicts(orderedLabels, bySprint);
    _smgmtLoadDepOrder(orderedLabels, bySprint);
    _smgmtLoadMiniRail(orderedLabels, bySprint);
    _smgmtUpdateCleanupBtn(data);
    _smgmtLabelFilterRender(issues);
    _smgmtLabelFilterApply();
    _smgmtKbRestoreFocus();
    orderedLabels.forEach((lbl) => _smgmtApplySort(lbl));
    _smgmtFilterApply();
  }
  function _smgmtLabelFilterRender(issues) {
    _smgmtLastLabelIssues = issues || [];
    const row = document.getElementById("smgmt-label-filter-row");
    if (!row)
      return;
    const seen = /* @__PURE__ */ new Set();
    (issues || []).forEach((iss) => {
      (iss.labels || []).forEach((l) => {
        seen.add(l.name);
        if (l.color)
          _smgmtLabelColors[l.name] = "#" + l.color;
      });
    });
    const priority = _SMGMT_FILTER_PRIORITY.filter((n) => seen.has(n));
    const rest = [...seen].filter((n) => !_SMGMT_FILTER_PRIORITY.includes(n)).sort();
    const allLabels = [...priority, ...rest];
    if (allLabels.length === 0) {
      row.classList.add("is-empty");
      row.innerHTML = "";
      return;
    }
    const _SMGMT_LABEL_VISIBLE = 5;
    const expanded = row.dataset.expanded === "true";
    const visible = expanded ? allLabels : allLabels.slice(0, _SMGMT_LABEL_VISIBLE);
    const hidden = allLabels.length - _SMGMT_LABEL_VISIBLE;
    row.classList.remove("is-empty");
    row.innerHTML = visible.map((name) => {
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
    }).join("") + (hidden > 0 && !expanded ? `<button class="smgmt-lf-show-more" onclick="_smgmtLabelFilterToggleExpand(true)">+${hidden} more</button>` : hidden > 0 && expanded ? `<button class="smgmt-lf-show-more" onclick="_smgmtLabelFilterToggleExpand(false)">Show less</button>` : "");
  }
  function _smgmtLabelFilterApply() {
    if (_smgmtDeactivatedLabels.size === 0) {
      document.querySelectorAll(".smgmt-ticket[data-labels]").forEach((el) => {
        el.style.display = "";
      });
      return;
    }
    document.querySelectorAll(".smgmt-ticket[data-labels]").forEach((el) => {
      const raw = el.getAttribute("data-labels") || "";
      const ticketLabels = raw ? raw.split(",") : [];
      if (ticketLabels.length === 0) {
        el.style.display = "";
        return;
      }
      const allDeactivated = ticketLabels.every(
        (n) => _smgmtDeactivatedLabels.has(n)
      );
      el.style.display = allDeactivated ? "none" : "";
    });
  }
  function _smgmtIsFreshRerunSprint(label) {
    const parents = _smgmtData && _smgmtData.sprint_parents || {};
    if (!parents[label])
      return false;
    const planState = (_smgmtData && _smgmtData.sprint_plan_states || {})[label];
    return planState === "draft" || planState === "planning";
  }
  function _smgmtApplyRerunOptimistic2(parentLabel, subLabel, ticketNumbers) {
    if (!_smgmtData || !parentLabel || !subLabel)
      return;
    const nums = new Set(ticketNumbers || []);
    const issues = _smgmtData.issues || [];
    for (const iss of issues) {
      if (nums.has(iss.number))
        iss.sprint_label = subLabel;
    }
    if (!_smgmtData.order)
      _smgmtData.order = [];
    if (!_smgmtData.order.includes(subLabel)) {
      const parentIdx = _smgmtData.order.indexOf(parentLabel);
      if (parentIdx >= 0)
        _smgmtData.order.splice(parentIdx + 1, 0, subLabel);
      else
        _smgmtData.order.push(subLabel);
    }
    if (!_smgmtData.sprint_parents)
      _smgmtData.sprint_parents = {};
    _smgmtData.sprint_parents[subLabel] = parentLabel;
    if (!_smgmtData.sprint_rerun_into)
      _smgmtData.sprint_rerun_into = {};
    _smgmtData.sprint_rerun_into[parentLabel] = subLabel;
    if (!_smgmtData.sprint_has_run)
      _smgmtData.sprint_has_run = {};
    _smgmtData.sprint_has_run[parentLabel] = true;
    if (!_smgmtData.sprint_plan_states)
      _smgmtData.sprint_plan_states = {};
    _smgmtData.sprint_plan_states[subLabel] = "draft";
    delete _smgmtOutcomeCache[parentLabel];
    delete _smgmtOutcomeCache[subLabel];
    if (_smgmtBySprint) {
      const moved = (_smgmtBySprint[parentLabel] || []).filter(
        (t) => nums.has(t.number)
      );
      _smgmtBySprint[subLabel] = [..._smgmtBySprint[subLabel] || [], ...moved];
      _smgmtBySprint[parentLabel] = (_smgmtBySprint[parentLabel] || []).filter(
        (t) => !nums.has(t.number)
      );
    }
  }
  function _smgmtHasLedgerRun(label) {
    return Boolean((_smgmtData?.sprint_has_run || {})[label]);
  }
  async function _smgmtFetchMissingOutcomes(orderedLabels, bySprint) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    const toFetch = [];
    for (const label of orderedLabels) {
      if (_smgmtRunningLabels.has(label))
        continue;
      if (_smgmtIsFreshRerunSprint(label))
        continue;
      if (_smgmtOutcomeCache[label] !== void 0)
        continue;
      if (!_smgmtHasLedgerRun(label) && !_smgmtResolvedAncestors.has(label))
        continue;
      toFetch.push(label);
    }
    await Promise.all(
      toFetch.map(async (label) => {
        try {
          const resp = await fetch(
            `/api/sprints/${encodeURIComponent(label)}/outcome?project=${encodeURIComponent(repo)}`
          );
          if (resp.ok) {
            const outcome = await resp.json();
            _smgmtOutcomeCache[label] = outcome;
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
      })
    );
  }
  async function _smgmtLoadEstimates(orderedLabels, bySprint) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    for (const label of orderedLabels) {
      const tickets = bySprint[label] || [];
      if (tickets.length === 0)
        continue;
      for (const t of tickets)
        _smgmtTicketToSprint[t.number] = label;
      const issueNums = tickets.map((t) => t.number).join(",");
      try {
        const resp = await fetch(
          `/api/estimates/batch?project=${encodeURIComponent(repo)}&issues=${issueNums}`
        );
        if (!resp.ok)
          continue;
        const data = await resp.json();
        const estEl = document.getElementById(`smgmt-est-${label}`);
        if (estEl && data.complete && data.total_hours !== null) {
          const h = data.total_hours;
          const display = Number.isInteger(h) ? `${h}h` : `${parseFloat(h.toFixed(1))}h`;
          estEl.textContent = `${display} estimated`;
        }
        _smgmtSetSprintTokenEl(label, data);
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
      }
    }
  }
  async function _smgmtLoadConflicts(orderedLabels, bySprint) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    for (const label of orderedLabels) {
      if (_smgmtRunningLabels.has(label))
        continue;
      if (_smgmtFinishedLabels.has(label))
        continue;
      const tickets = bySprint[label] || [];
      const pending = tickets.filter(
        (t) => (t.status || "backlog") === "backlog"
      );
      if (pending.length < 2)
        continue;
      for (const t of pending)
        delete _smgmtConflictsByIssue[t.number];
      try {
        const resp = await fetch(
          `/api/sprints/${encodeURIComponent(label)}/conflicts?project=${encodeURIComponent(repo)}`
        );
        if (!resp.ok)
          continue;
        const data = await resp.json();
        for (const c of data.conflicts || []) {
          if (!_smgmtConflictsByIssue[c.ticket1_id])
            _smgmtConflictsByIssue[c.ticket1_id] = [];
          if (!_smgmtConflictsByIssue[c.ticket2_id])
            _smgmtConflictsByIssue[c.ticket2_id] = [];
          _smgmtConflictsByIssue[c.ticket1_id].push({
            partnerId: c.ticket2_id,
            partnerTitle: c.ticket2_title,
            sharedFiles: c.shared_files
          });
          _smgmtConflictsByIssue[c.ticket2_id].push({
            partnerId: c.ticket1_id,
            partnerTitle: c.ticket1_title,
            sharedFiles: c.shared_files
          });
        }
        for (const t of pending)
          _smgmtUpdateConflictBadge(t.number);
      } catch (_) {
      }
    }
  }
  async function _smgmtLoadDepOrder(orderedLabels, bySprint) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    for (const label of orderedLabels) {
      if (_smgmtRunningLabels.has(label))
        continue;
      if (_smgmtFinishedLabels.has(label))
        continue;
      const tickets = bySprint[label] || [];
      const pending = tickets.filter(
        (t) => (t.status || "backlog") === "backlog"
      );
      if (pending.length < 2)
        continue;
      for (const t of pending)
        delete _smgmtDepOrderByIssue[t.number];
      try {
        const resp = await fetch(
          `/api/sprints/${encodeURIComponent(label)}/dep-order?project=${encodeURIComponent(repo)}`
        );
        if (!resp.ok)
          continue;
        const data = await resp.json();
        if (data.has_cycle) {
          const cycleSet = new Set((data.in_cycle_tickets || []).map(String));
          for (const t of pending) {
            if (cycleSet.has(String(t.number))) {
              _smgmtDepOrderByIssue[t.number] = {
                upstream: [],
                downstream: [],
                inCycle: true
              };
            }
          }
        } else {
          for (const [idStr, hint] of Object.entries(data.dep_hints || {})) {
            const num = parseInt(idStr, 10);
            _smgmtDepOrderByIssue[num] = {
              upstream: hint.upstream || [],
              downstream: hint.downstream || [],
              inCycle: false
            };
          }
        }
        for (const t of pending)
          _smgmtUpdateDepOrderBadge(t.number);
      } catch (_) {
      }
    }
  }
  async function _smgmtLoadGoals(orderedLabels) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    for (const label of orderedLabels) {
      const goalEl = document.getElementById(`smgmt-goal-${label}`);
      if (!goalEl)
        continue;
      try {
        const resp = await fetch(
          `/api/sprints/goal?project=${encodeURIComponent(repo)}&sprint=${encodeURIComponent(label)}`
        );
        if (!resp.ok)
          continue;
        const data = await resp.json();
        const goal = (data.goal || "").trim();
        if (goal) {
          goalEl.textContent = goal;
          goalEl.title = goal;
          goalEl.style.display = "";
        }
      } catch (_) {
      }
    }
  }
  function _smgmtOutcomeBandHtml(label, outcome) {
    const st = outcome.sprint_status;
    const paneState = outcome.state || "";
    const c = outcome.counts || {};
    const dur = _fmtWallClock(outcome.wall_clock_secs);
    const ts = outcome.ended_at ? st === "completed" ? `ended ${outcome.ended_at}` : `stopped ${outcome.ended_at}` : "";
    const issues = outcome.issues || [];
    let segBarHtml = "";
    if (issues.length > 0) {
      const blocks = issues.map((iss) => {
        const o = iss.outcome || "skipped";
        let blockClass = "seg-pending";
        if (o === "done")
          blockClass = "seg-done";
        else if (o === "failed")
          blockClass = "seg-failed";
        else if (o === "skipped")
          blockClass = "seg-skipped";
        return `<div class="seg-block ${blockClass}"></div>`;
      }).join("");
      segBarHtml = `<div class="smgmt-seg-bar">${blocks}</div>`;
    }
    let linksHtml = "";
    if (paneState === "completed" || st === "completed") {
      const prNum = outcome.pr_number;
      const prUrl = outcome.pr_url;
      const sumNum = outcome.summary_issue_num;
      const sumUrl = outcome.summary_issue_url;
      const prLink = prNum && prUrl ? `<a href="${escHtml(prUrl)}" target="_blank" rel="noopener" class="oc-pr-link"><i class="ti ti-git-pull-request"></i> PR #${prNum}</a>` : "";
      const sumLink = sumNum && sumUrl ? `<a href="${escHtml(sumUrl)}" target="_blank" rel="noopener" class="oc-summary-link"><i class="ti ti-file-description"></i> #${sumNum} Sprint Summary</a>` : sumNum ? `<span class="oc-summary-link"><i class="ti ti-file-description"></i> #${sumNum} Sprint Summary</span>` : "";
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
    <div class="smgmt-outcome-dur"><i class="ti ti-clock" style="vertical-align:-1px;"></i> ${escHtml(dur)}${ts ? " \xB7 " + escHtml(ts) : ""}</div>
    ${linksHtml}
  </div>`;
  }
  function _smgmtOutcomeTicketListHtml(issues, label, repo) {
    if (!issues || issues.length === 0)
      return "";
    const safeLabel = label ? escHtml(label) : "";
    const safeRepo = repo ? escHtml(repo) : "";
    return issues.map((iss) => {
      const o = iss.outcome || "skipped";
      let circle = "";
      if (o === "done")
        circle = '<div class="smgmt-ticket-circle done">\u2713</div>';
      else if (o === "failed")
        circle = '<div class="smgmt-ticket-circle failed">\u2715</div>';
      else
        circle = '<div class="smgmt-ticket-circle skipped">\u2212</div>';
      const elapsed = `<span class="smgmt-ticket-elapsed">${escHtml(_fmtElapsed(iss.elapsed_secs))}</span>`;
      const rejLabel = o === "failed" ? '<span class="smgmt-lbl-rejected">TESTER REJECTED</span>' : "";
      const viewLogBtn = safeLabel && safeRepo ? `<button class="btn-view-log" title="View issue log"
              onclick="event.stopPropagation();openLvIssueLog(${iss.number},'${safeLabel}','${safeRepo}')">
           <i class="ti ti-file-text"></i></button>` : "";
      return `<div class="smgmt-ticket" data-issue="${iss.number}" data-labels="" draggable="false">
      ${circle}
      <a class="smgmt-ticket-num" href="${safeRepo ? `https://github.com/${safeRepo}/issues/${iss.number}` : "#"}" target="_blank" rel="noopener">#${iss.number}</a>
      <span class="smgmt-ticket-title" title="${escHtml(iss.title)}">${escHtml(iss.title)}</span>
      ${rejLabel}
      ${viewLogBtn}
      ${elapsed}
      <button class="t-details-btn" onclick="event.stopPropagation();toggleTicketRow('${safeLabel}',${iss.number})">
        <span class="t-dbtn-label">Details</span> <span id="caret-${safeLabel}-${iss.number}">\u25BC</span>
      </button>
    </div>
    <div class="ticket-expand" id="ex-${safeLabel}-${iss.number}" style="display:none">
      <div class="ex-row">
        <span class="ex-label">Conflicts</span>
        <span class="ex-conflicts-val">\u2014</span>
      </div>
      <div class="ex-row">
        <span class="ex-label">Execution</span>
        <span class="ex-exec-val">\u2014</span>
      </div>
      <div class="ex-actions">
        <button class="ex-btn" onclick="event.stopPropagation();_smgmtReEstimate(${iss.number},this)"><i class="ti ti-sparkles" style="font-size:12px"></i> Re-estimate</button>
        <button class="ex-btn" onclick="event.stopPropagation();_smgmtRowMenuOpen(event,${iss.number},'${safeLabel}',false)"><i class="ti ti-arrow-right" style="font-size:12px"></i> Move to sprint</button>
        <button class="ex-btn ex-btn-danger" onclick="event.stopPropagation();_smgmtCloseIssueOpen(${iss.number})"><i class="ti ti-x" style="font-size:12px"></i> Close ticket</button>
      </div>
    </div>`;
    }).join("");
  }
  async function _smgmtLoadFinishCards() {
    const repo = _smgmtRepo();
    if (!repo || !_smgmtData)
      return;
    const order = _smgmtData.order && _smgmtData.order.length ? _smgmtData.order : (_smgmtData.sprints || []).map((n) => `sprint-${n}`);
    await Promise.allSettled(
      order.map(async (label) => {
        if (_smgmtIsFreshRerunSprint(label))
          return;
        try {
          const [cardRes, branchRes] = await Promise.all([
            fetch(
              `/api/sprints/${encodeURIComponent(label)}/finish-card?project=${encodeURIComponent(repo)}`
            ),
            fetch(
              `/api/sprints/${encodeURIComponent(label)}/branch-status?project=${encodeURIComponent(repo)}`
            ).catch(() => null)
          ]);
          if (!cardRes.ok) {
            console.warn(
              `finish-card: unexpected ${cardRes.status} for ${label}`
            );
            return;
          }
          const cardData = await cardRes.json();
          if (cardData.state === "no_data")
            return;
          const branchData = branchRes && branchRes.ok ? await branchRes.json() : { exists: false };
          _smgmtFinishCards[label] = { card: cardData, branch: branchData };
          _smgmtRenderFinishCard(label, cardData, branchData, repo);
        } catch (e) {
          console.warn("finish-card load error for", label, e);
        }
      })
    );
  }
  function _smgmtRenderFinishCard(label, cardData, branchData, repo) {
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
    if (cardData.state === "no_data")
      return;
    const cardEl = document.getElementById(`smgmt-finish-card-${label}`);
    const blockEl = document.getElementById(`smgmt-card-${label}`);
    if (!cardEl || !blockEl)
      return;
    const isFinished = cardData.state === "completed" || cardData.state === "has_rework";
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
    const branchLink = branchData && branchData.exists ? `<a href="${branchUrl}" target="_blank" rel="noopener" class="sfc-branch-link"><i class="ti ti-git-branch"></i> ${escHtml(branchName)}</a>` : `<a href="${branchUrl}" target="_blank" rel="noopener" class="sfc-branch-link sfc-branch-link--warn" title="Could not verify branch exists on GitHub"><i class="ti ti-alert-triangle"></i> ${escHtml(branchName)}</a>`;
    if (state === "running")
      return _sfcRunningHtml(cardData, branchLink, n);
    if (state === "completed")
      return _sfcCompletedHtml(cardData, branchLink, n, branchData);
    if (state === "has_rework" || state === "cancelled") {
      return _sfcHasReworkHtml(cardData, branchLink, n, branchData);
    }
    return "";
  }
  var _NON_DISPATCHABLE_LABELS = /* @__PURE__ */ new Set([
    "UAT",
    "UAT-approved",
    "released"
  ]);
  function _smgmtHasDispatchableTickets(tickets) {
    return tickets.some((t) => {
      const names = (t.labels || []).map((l) => l.name);
      return !names.some((n) => _NON_DISPATCHABLE_LABELS.has(n));
    });
  }
  function _smgmtCardHtml(label, n, tickets, outcome, isNext, parent, finished) {
    const isRunning = _smgmtRunningLabels.has(label);
    const isLinger = !isRunning && typeof _smgmtIsLinger === "function" && _smgmtIsLinger(label);
    const isRunningView = isRunning || isLinger;
    let isCollapsed = isRunning;
    try {
      const _pref = localStorage.getItem("sprintColumn_" + label + "_collapsed");
      if (_pref === "1")
        isCollapsed = true;
      else if (_pref === "0")
        isCollapsed = false;
    } catch (_) {
    }
    const isFreshRerun = _smgmtIsFreshRerunSprint(label);
    if (isFreshRerun)
      outcome = null;
    const planState = ((_smgmtData && _smgmtData.sprint_plan_states || {})[label] || "").toLowerCase();
    const planBlocksPostRun = [
      "planned",
      "draft",
      "planning"
    ].includes(planState);
    const outcomeLifecycle = (outcome && outcome.lifecycle || "").toLowerCase();
    const outcomeState = outcome && (outcome.state || (outcome.sprint_status === "completed" ? "completed" : null));
    const hasLedgerRun = _smgmtHasLedgerRun(label);
    const isHasRework = hasLedgerRun && (outcomeLifecycle === "needs_rework" || outcomeState === "has_rework" || outcomeState === "cancelled");
    const isReadyToMerge = hasLedgerRun && (outcomeLifecycle === "ready_to_merge" || outcomeLifecycle === "completed" && outcomeState === "completed");
    const isAwaitingMerge = isReadyToMerge || finished && !isRunning && !isHasRework && !planBlocksPostRun;
    const showRunningChrome = isRunningView && !isAwaitingMerge;
    const isPostRun = !isRunningView && !planBlocksPostRun && hasLedgerRun;
    const canRun = tickets.length >= 1 && _smgmtHasDispatchableTickets(tickets);
    const rerunDisabled = _smgmtAnySprintRunning ? "disabled" : "";
    const rerunTitle = _smgmtAnySprintRunning ? 'title="Cannot re-run: another sprint is currently running."' : "";
    const childLabel = _smgmtNextChildLabel(label);
    const childDisplay = sprintLabelDisplay(childLabel).replace("Sprint ", "");
    const rerunBtn = `<button class="smgmt-run-btn smgmt-run-btn--rerun" ${rerunDisabled} ${rerunTitle}
                    onclick="smgmtRerunSprint('${escHtml(label)}')">
                    <i class="ti ti-refresh"></i> Re-run \u2192 ${escHtml(childDisplay)}</button>`;
    const rerunInto = (_smgmtData?.sprint_rerun_into || {})[label];
    const rerunChildDisplay = rerunInto ? sprintLabelDisplay(rerunInto).replace("Sprint ", "") : "";
    let actionBtn;
    if (isRunning) {
      actionBtn = `<button class="smgmt-cancel-btn" onclick="smgmtCancelSprint('${escHtml(label)}')">
                  <i class="ti ti-player-stop"></i> Cancel sprint</button>`;
    } else if (isLinger) {
      actionBtn = `<span class="smgmt-linger-note">Finished \u2014 snapshot kept 1h</span>`;
    } else if (isHasRework && rerunInto && tickets.length === 0) {
      actionBtn = `<button class="smgmt-run-btn" ${rerunDisabled} ${rerunTitle}
                  onclick="smgmtRunSprint('${escHtml(rerunInto)}')">
                  <i class="ti ti-player-play"></i> Run \u2192 ${escHtml(rerunChildDisplay)}</button>`;
    } else if (isHasRework || isPostRun) {
      actionBtn = rerunBtn;
    } else if (_smgmtAnySprintRunning) {
      actionBtn = `<button class="smgmt-run-btn smgmt-run-btn--blocked"
                  title="Another sprint is running"
                  onclick="smgmtRunBlockedToast()">
                  <i class="ti ti-player-play"></i> Run Sprint</button>`;
    } else {
      const runDisabled = !canRun ? "disabled" : "";
      const runTitle = !canRun ? 'title="No dispatchable tickets \u2014 remaining items are already SIT/UAT or in progress"' : "";
      const schedToggle = typeof _smgmtSchedToggleHtml === "function" ? _smgmtSchedToggleHtml(label) : "";
      actionBtn = `<button class="smgmt-run-btn" ${runDisabled} ${runTitle}
                  onclick="smgmtRunSprint('${label}')">
                  <i class="ti ti-player-play"></i> Run Sprint</button>${schedToggle}`;
    }
    const isOutcomeCompleted = isReadyToMerge || isHasRework || outcomeState === "completed";
    const finishHidden = isOutcomeCompleted || isPostRun && !outcome ? "" : "hidden";
    const finishDisabled = isReadyToMerge && tickets.length === 0 ? "disabled" : "";
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
        const _metaStopped = outcome.ended_at ? _fmtStoppedAt(outcome.ended_at) : null;
        const _metaParts = [];
        if (_metaSecs != null)
          _metaParts.push(_fmtRunningTime(_metaSecs));
        if (_metaStopped)
          _metaParts.push(`stopped ${_metaStopped}`);
        if (_metaParts.length)
          headerMetaHtml = `<span class="smgmt-sprint-meta">${escHtml(_metaParts.join(" \xB7 "))}</span>`;
        const _elapsedByNum = {};
        if (outcome.issues) {
          for (const _oi of outcome.issues) {
            if (_oi.elapsed_secs != null)
              _elapsedByNum[_oi.number] = _oi.elapsed_secs;
          }
        }
        ticketsContainerHtml = tickets.length > 0 ? tickets.map(
          (t) => _smgmtTicketRowHtml(t, label, _elapsedByNum[t.number] ?? null)
        ).join("") : '<div class="smgmt-drop-hint">Drop tickets here</div>';
      } else {
        outcomeBandHtml = _smgmtOutcomeBandHtml(label, outcome);
        const issueList = outcome.issues || [];
        ticketsContainerHtml = _smgmtOutcomeTicketListHtml(
          issueList,
          label,
          _smgmtRepo()
        );
        rollupItems = issueList.map((i) => ({ number: i.number }));
      }
    } else if (isRunningView) {
      ticketsContainerHtml = _smgmtRunningTicketRowsHtml(label, tickets);
    } else {
      ticketsContainerHtml = tickets.length > 0 ? tickets.map((t) => _smgmtTicketRowHtml(t, label)).join("") : '<div class="smgmt-drop-hint">Drop tickets here</div>';
      if (finished) {
        outcomeBadgeHtml = `<span class="smgmt-state-badge state-finished">READY TO MERGE</span>`;
      }
    }
    const summaryHtml = `<div class="sc-budget-section">
    <div class="sc-budget-head">
      <span class="sc-budget-eyebrow">SPRINT BUDGET</span>
      <span class="sc-budget-forecast" id="sc-budget-forecast-${escHtml(label)}"></span>
    </div>
    <div class="cap" id="smgmt-cap-${escHtml(label)}"></div>
    <div class="smgmt-sprint-goal-text" id="smgmt-goal-${escHtml(label)}" style="display:none"></div>
  </div>
  <div class="sc-preview-slot" id="sc-preview-${escHtml(label)}"></div>`;
    const logHtml = "";
    const cancelBannerHtml = "";
    const plannedBadge = !isNext && !finished && !isPostRun && !outcomeBadgeHtml ? '<span class="sc-planned-badge">PLANNED</span>' : "";
    const blockedHint = _smgmtAnySprintRunning && !isPostRun && !isRunningView ? `<span class="sc-blocked-hint">blocked: ${_smgmtRunningBlockerShort()} running</span>` : "";
    const parentLineage = parent && !isFreshRerun ? `<span class="smgmt-sprint-lineage" title="Child sprint spawned from ${escHtml(parent)}">\u2190 from ${escHtml(sprintLabelDisplay(parent))}</span>` : "";
    const live = isRunningView ? (typeof _smgmtLingerLive === "function" ? _smgmtLingerLive(label) : null) || _smgmtLiveCache[label] || null : null;
    const runningComplete = live ? (live.done_count || 0) + (live.failed_count || 0) + (live.skipped_count || 0) : 0;
    const runningTotal = live ? live.total_count || tickets.length : tickets.length;
    const runningRatio = runningTotal > 0 ? `${runningComplete}/${runningTotal}` : "\u2014";
    const runningElapsed = live && live.time_spent_sec > 0 ? `<span class="smgmt-sprint-meta" id="smgmt-elapsed-${escHtml(label)}">elapsed ${_fmtRunningTime(live.time_spent_sec)}</span>` : `<span class="smgmt-sprint-meta" id="smgmt-elapsed-${escHtml(label)}"></span>`;
    const runningBadgeHtml = showRunningChrome ? `<span class="smgmt-running-badge" id="smgmt-running-badge-${escHtml(label)}"><span class="smgmt-running-badge-dot"></span>${isLinger ? "done" : runningRatio}</span>` : "";
    const runningStripeHtml = showRunningChrome ? '<div class="smgmt-running-stripe"></div>' : "";
    const runningClass = isRunning ? " smgmt-running" : isLinger && !isAwaitingMerge ? " smgmt-linger" : "";
    const collapsedClass = isCollapsed ? " smgmt-collapsed" : "";
    const collapseLabel = (isCollapsed ? "Expand " : "Collapse ") + escHtml(sprintLabelDisplay(label));
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
      ${function() {
      const _ss = _smgmtCardStatusSentence(label, {
        isRunning,
        isLinger,
        isNext,
        isHasRework,
        isReadyToMerge,
        isAwaitingMerge,
        planState,
        outcome,
        tickets
      });
      return _ss ? `<div class="sc-status-line">${escHtml(_ss)}</div>` : "";
    }()}
      ${cancelBannerHtml}
      ${outcomeBandHtml}
      ${summaryHtml}
      <div class="smgmt-sprint-tickets sc-tickets" id="smgmt-tickets-${escHtml(label)}">
        ${ticketsContainerHtml}
      </div>
      ${logHtml}
    </div>`;
  }
  function _smgmtRunningTicketRowsHtml(label, tickets) {
    const live = _smgmtLiveCache[label] || null;
    const currentTicket = live ? live.current_ticket : null;
    const liveIssues = live && live.issues && live.issues.length > 0 ? live.issues : [];
    const liveByNum = {};
    liveIssues.forEach((i) => {
      liveByNum[i.number] = i;
    });
    const sourceTickets = (liveIssues.length > 0 ? liveIssues : tickets).slice().sort((a, b) => (a.dispatch_level || 0) - (b.dispatch_level || 0));
    const cardRepo = _smgmtRepo();
    if (sourceTickets.length === 0) {
      return '<div class="smgmt-drop-hint">No tickets in this sprint</div>';
    }
    let prevLevel = 0;
    return sourceTickets.map((t) => {
      const liveIss = liveByNum[t.number];
      const liveStatus = liveIss ? liveIss.status : null;
      const agentStatus = liveIss ? liveIss.agent_status : null;
      const ticketLevel = liveIss && liveIss.dispatch_level || t.dispatch_level || 0;
      let sepHtml = "";
      if (ticketLevel > 0 && ticketLevel > prevLevel) {
        sepHtml = `<div class="level-sep">
        <span class="level-sep-num">Level ${ticketLevel}</span>
        <span class="level-sep-desc">\xB7 runs after level ${prevLevel} completes</span>
      </div>`;
      }
      if (ticketLevel > 0)
        prevLevel = ticketLevel;
      const isActiveAgent = agentStatus && (agentStatus.endsWith("_running") || agentStatus.endsWith("_dispatched"));
      let indicator = "";
      if (liveStatus === "done") {
        indicator = '<div class="smgmt-ticket-indicator"><div class="circle-done">&#10003;</div></div>';
      } else if (agentStatus === "failed" || liveStatus === "skipped") {
        indicator = '<div class="smgmt-ticket-indicator"><div class="circle-failed">&#10005;</div></div>';
      } else if (liveStatus === "in-progress" || isActiveAgent || currentTicket && t.number === currentTicket.number) {
        indicator = '<div class="smgmt-ticket-indicator"><div class="ring"></div></div>';
      } else {
        indicator = '<div class="smgmt-ticket-indicator"><div class="circle-pending"></div></div>';
      }
      const issueUrl = t.url || (cardRepo ? `https://github.com/${cardRepo}/issues/${t.number}` : "#");
      const sizeVal = liveIss && liveIss.size || t.size || "";
      const sizePillHtml = sizeVal ? `<span class="smgmt-ticket-size-pill" title="\u2248${liveIss && liveIss.minutes || _sizeMinutes(sizeVal)} min">${escHtml(sizeVal)}</span>` : "";
      const runSizeAttr = sizeVal ? ` data-size="${escHtml(sizeVal)}"` : "";
      const agentTagHtml = liveIss && liveIss.agent ? `<span class="smgmt-ticket-agent-tag ${_smgmtAgentTagClass(liveIss.agent)}">${escHtml(liveIss.agent.toUpperCase())}</span>` : "";
      const elapsedStr = liveIss ? _fmtTicketElapsed(liveIss.elapsed_secs) : null;
      const elapsedHtml = elapsedStr ? `<span class="smgmt-ticket-elapsed">${elapsedStr}</span>` : "";
      const runTicketLabels = escHtml(
        (t.labels || []).map((l) => l.name).join(",")
      );
      return sepHtml + `<div class="smgmt-ticket" data-issue="${t.number}" data-labels="${runTicketLabels}" draggable="false"${runSizeAttr}>
      ${indicator}
      <a class="smgmt-ticket-num" href="${escHtml(issueUrl)}" target="_blank"
         rel="noopener">#${t.number}</a>
      <span class="smgmt-ticket-title" title="${escHtml(t.title)}">${escHtml(t.title)}</span>
      ${sizePillHtml}${agentTagHtml}${elapsedHtml}
    </div>`;
    }).join("");
  }
  function _smgmtRunningLevelText(live) {
    const levels = live && live.levels || [];
    if (levels.length > 1) {
      const active = levels.find((l) => l.state === "active");
      const cur = active ? active.level : levels[levels.length - 1].level;
      return `level ${cur} of ${levels.length}`;
    }
    const issues = live && live.issues || [];
    const levelNums = [...new Set(issues.map((i) => i.dispatch_level || 0 || 1))].filter((l) => l > 0).sort((a, b) => a - b);
    if (levelNums.length <= 1)
      return null;
    let current = levelNums[0];
    for (const lvl of levelNums) {
      const group = issues.filter((i) => (i.dispatch_level || 0 || 1) === lvl);
      const allDone = group.length > 0 && group.every(
        (i) => i.status === "done" || i.status === "skipped" || i.agent_status === "failed"
      );
      if (!allDone) {
        current = lvl;
        break;
      }
      current = lvl;
    }
    return `level ${current} of ${levelNums.length}`;
  }
  function _smgmtRunningBoardBannerHtml(label, tickets) {
    const isLinger = typeof _smgmtIsLinger === "function" && _smgmtIsLinger(label);
    const live = (typeof _smgmtLingerLive === "function" ? _smgmtLingerLive(label) : null) || _smgmtLiveCache[label] || null;
    const doneCount = live ? live.done_count || 0 : 0;
    const failedCount = live ? live.failed_count || 0 : 0;
    const skippedCount = live ? live.skipped_count || 0 : 0;
    const totalCount = live ? live.total_count || tickets.length : tickets.length;
    const completeCount = doneCount + failedCount + skippedCount;
    const timeSpentSec = live ? live.time_spent_sec || 0 : 0;
    const levelText = _smgmtRunningLevelText(live);
    const parts = [
      isLinger ? `${escHtml(sprintLabelDisplay(label))} finished (snapshot)` : `${escHtml(sprintLabelDisplay(label))} is running`,
      `${completeCount}/${totalCount} done`,
      timeSpentSec > 0 ? _fmtRunningTime(timeSpentSec) : null,
      levelText
    ].filter(Boolean);
    const safeLabel = escHtml(label);
    const lingerCls = isLinger ? " linger" : "";
    return `<div class="smgmt-board-running-banner${lingerCls}" id="smgmt-board-banner-${safeLabel}" data-label="${safeLabel}">
    <span class="smgmt-board-running-banner-dot" aria-hidden="true"></span>
    <span class="smgmt-board-running-banner-text" id="smgmt-board-banner-text-${safeLabel}">${parts.join(" \xB7 ")}</span>
    <button type="button" class="smgmt-board-running-banner-link"
            onclick="_smgmtShowSubView('running')">Watch in Running \u2192</button>
  </div>`;
  }
  function _smgmtBoardBannerPatch(label, live) {
    const textEl = document.getElementById(`smgmt-board-banner-text-${label}`);
    if (!textEl)
      return;
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
      levelText
    ].filter(Boolean);
    textEl.textContent = parts.join(" \xB7 ");
  }
  function _smgmtRunningCardHtml(label, n, tickets) {
    let isCollapsed = false;
    try {
      isCollapsed = localStorage.getItem("sprintColumn_" + label + "_collapsed") === "1";
    } catch (_) {
    }
    const live = _smgmtLiveCache[label] || null;
    const doneCount = live ? live.done_count || 0 : 0;
    const failedCount = live ? live.failed_count || 0 : 0;
    const skippedCount = live ? live.skipped_count || 0 : 0;
    const totalCount = live ? live.total_count || tickets.length : tickets.length;
    const completeCount = doneCount + failedCount + skippedCount;
    const estRemMins = live ? live.est_remaining_minutes : null;
    const timeSpentSec = live ? live.time_spent_sec || 0 : 0;
    const currentTicket = live ? live.current_ticket : null;
    const recentLogLines = live ? live.recent_log_lines || [] : [];
    const liveIssues = live && live.issues && live.issues.length > 0 ? live.issues : [];
    const liveByNum = {};
    liveIssues.forEach((i) => {
      liveByNum[i.number] = i;
    });
    const sourceTickets = (liveIssues.length > 0 ? liveIssues : tickets).slice().sort((a, b) => (a.dispatch_level || 0) - (b.dispatch_level || 0));
    const segBarHtml = sourceTickets.length > 0 ? `<div class="smgmt-seg-bar" id="smgmt-seg-${escHtml(label)}">${sourceTickets.map((t) => {
      const liveIss = liveByNum[t.number];
      const liveStatus = liveIss ? liveIss.status : null;
      const agentStatus = liveIss ? liveIss.agent_status : null;
      let blockClass = "seg-pending";
      if (liveStatus === "done")
        blockClass = "seg-done";
      else if (agentStatus === "failed" || liveStatus === "skipped")
        blockClass = "seg-failed";
      else if (liveStatus === "in-progress" || agentStatus === "running" || currentTicket && t.number === currentTicket.number)
        blockClass = "seg-running";
      return `<div class="seg-block ${blockClass}" data-issue="${t.number}"></div>`;
    }).join("")}</div>` : "";
    const ticketRowsHtml = _smgmtRunningTicketRowsHtml(label, tickets);
    const runCollapsedClass = isCollapsed ? " smgmt-collapsed" : "";
    const runCollapseLabel = (isCollapsed ? "Expand " : "Collapse ") + escHtml(sprintLabelDisplay(label));
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
            <span class="smgmt-running-badge-dot"></span>${totalCount > 0 ? `${completeCount}/${totalCount}` : "\u2014"}
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
        <span class="oc-est" id="smgmt-rs-est-${escHtml(label)}">${estRemMins != null ? `\u21A9 ${estRemMins}m EST. REMAINING` : "\u21A9 EST. REMAINING"}</span>
        <span class="oc-spacer"></span>
        ${segBarHtml}
        <span class="smgmt-outcome-dur" id="smgmt-rs-time-${escHtml(label)}">${_fmtRunningTime(timeSpentSec)}</span>
      </div>
      <div id="smgmt-active-agents-wrap-${escHtml(label)}">${_smgmtActiveAgentsHtml(live, label)}</div>
      <div id="smgmt-levels-wrap-${escHtml(label)}">${_smgmtLevelsHtml(live, label)}</div>
      <div class="smgmt-sprint-tickets" id="smgmt-tickets-${escHtml(label)}">
        ${ticketRowsHtml || '<div class="smgmt-drop-hint">No tickets in this sprint</div>'}
      </div>
      ${renderProgressActivity2(
      {
        status: "running",
        mode: totalCount > 0 ? "bar" : "indeterminate",
        current: currentTicket ? `#${currentTicket.number}` : "",
        done: completeCount,
        total: totalCount,
        est_remaining_minutes: estRemMins != null ? estRemMins : void 0,
        log_tail: recentLogLines
      },
      {
        id: `running-${escHtml(label)}`,
        colorize: typeof colorizeLogLine === "function" ? colorizeLogLine : null,
        logHeaderAgentHtml: `<span class="smgmt-live-log-agent" id="smgmt-live-agent-${escHtml(label)}">${_smgmtLiveAgentBadgesHtml(live)}</span>`
      }
    )}
    </div>`;
  }
  function _smgmtRollupText(items) {
    const count = items.length;
    if (count === 0)
      return "0 tickets";
    let totalMins = 0, unestimated = 0;
    for (const t of items) {
      const size = _smgmtTicketSize(t);
      const mins = size ? _sizeMinutes(size) : 0;
      if (mins > 0)
        totalMins += mins;
      else
        unestimated++;
    }
    const countStr = `${count} ticket${count !== 1 ? "s" : ""}`;
    if (unestimated === count)
      return countStr;
    const h = totalMins / 60;
    const timeStr = h < 1 ? `~${totalMins}m` : `~${parseFloat((Math.round(h * 10) / 10).toFixed(1))}h`;
    return `${countStr} \xB7 ${timeStr}`;
  }
  function _smgmtTicketSize(t) {
    if (!t)
      return null;
    const cached = Object.prototype.hasOwnProperty.call(_estDataCache, t.number) ? _estDataCache[t.number] : void 0;
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
  function _smgmtCardStatusSentence(label, opts) {
    const {
      isRunning,
      isLinger,
      isNext,
      isHasRework,
      isReadyToMerge,
      isAwaitingMerge,
      planState,
      outcome,
      tickets
    } = opts;
    if (isRunning)
      return "";
    if (isLinger)
      return "Sprint finished \u2014 snapshot kept 1 hour.";
    if (isHasRework) {
      const c = outcome && outcome.counts || {};
      const done = c.done || 0;
      const failed = c.failed || 0;
      const total = outcome && Array.isArray(outcome.issues) ? outcome.issues.length : 0;
      if (total > 0 && failed > 0) {
        return `${done} of ${total} passed, ${failed} need${failed === 1 ? "s" : ""} rework \u2014 re-run or merge what passed.`;
      }
      return "Some tickets need rework \u2014 re-run or merge what passed.";
    }
    if (isReadyToMerge || isAwaitingMerge) {
      return "All tickets passed. Ready to merge.";
    }
    if (isNext) {
      if (_smgmtAnySprintRunning) {
        const blocker = typeof _smgmtRunningBlockerShort === "function" ? _smgmtRunningBlockerShort() : "";
        return `Ready to run. Waiting on ${blocker}.`;
      }
      return "Ready to run.";
    }
    if (_smgmtAnySprintRunning) {
      return "Blocked: another sprint is running.";
    }
    if (!planState || planState === "draft" || planState === "planning") {
      return tickets.length === 0 ? "No tickets yet \u2014 drag some from the backlog." : "Set a sprint goal to enable the run.";
    }
    return tickets.length === 0 ? "No tickets \u2014 add some from the backlog." : "Planned.";
  }
  function _smgmtRunningBlockerShort() {
    if (!_smgmtRunningLabels || _smgmtRunningLabels.size === 0)
      return "";
    const lbl = [..._smgmtRunningLabels][0];
    const m = String(lbl).match(/sprint-(\d+(?:\.\d+)?)/);
    return m ? `S${m[1]}` : sprintLabelDisplay(lbl);
  }
  function _smgmtTicketEstHtml(ticket) {
    const activity = typeof globalThis !== "undefined" && globalThis._smgmtRowActivity ? globalThis._smgmtRowActivity[ticket.number] : null;
    if (activity) {
      const label = activity === "fixing-ac" ? "fixing AC\u2026" : "estimating\u2026";
      return `<span class="smgmt-ticket-est smgmt-ticket-est--pending" id="smgmt-ticket-est-${ticket.number}" aria-label="${label}"><span class="smgmt-estimating-dot" aria-hidden="true"></span></span>`;
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
    if (el)
      el.textContent = _smgmtRollupText(items);
  }
  function _smgmtTicketRowHtml(ticket, label, elapsedSecs = null) {
    const hasRework = (ticket.labels || []).some(
      (l) => l.name === "need-rework" || l.name === "needs-rework"
    );
    const statusClass = hasRework ? "smgmt-status-need-rework" : {
      backlog: "smgmt-status-backlog",
      "in-progress": "smgmt-status-in-progress",
      sit: "smgmt-status-sit",
      uat: "smgmt-status-uat",
      done: "smgmt-status-done"
    }[ticket.status] || "smgmt-status-backlog";
    const statusLabel = hasRework ? "needs rework" : ticket.status || "backlog";
    const isSelected = _smgmtSelectedIssues.has(ticket.number);
    const _outcomeMap = {
      done: ["ti-circle-check", "outcome-success"],
      uat: ["ti-circle-check", "outcome-success"],
      "needs-rework": ["ti-circle-x", "outcome-rework"],
      "in-progress": ["ti-circle-dot", "outcome-active"],
      sit: ["ti-circle-dot", "outcome-active"]
    };
    const _oc = hasRework ? ["ti-circle-x", "outcome-rework"] : _outcomeMap[ticket.status] || ["ti-circle", "outcome-backlog"];
    const outcomeIconHtml = `<i class="ti ${_oc[0]} smgmt-outcome-icon ${_oc[1]}" title="${escHtml(statusLabel)}"></i>`;
    const sizeValue = _smgmtTicketSize(ticket) || "";
    const hasEstimate = sizeValue !== "";
    const sizeAttr = sizeValue ? ` data-size="${escHtml(sizeValue)}"` : "";
    const estimateBadgeHtml = _smgmtEstimateBadgeHtml(ticket.number);
    const _cachedEst = Object.prototype.hasOwnProperty.call(
      _estDataCache,
      ticket.number
    ) ? _estDataCache[ticket.number] : void 0;
    const sizePillHtml = sizeValue && !(_cachedEst && _cachedEst.size) ? `<span class="smgmt-ticket-size-pill" title="\u2248${_sizeMinutes(sizeValue)} min">${escHtml(sizeValue)}</span>` : "";
    const staleBadgeHtml = ticket.estimate_stale && hasEstimate ? `<button class="smgmt-stale-badge" data-stale="true" tabindex="0"
         title="Estimate may be outdated \u2014 issue body changed since last estimate"
         onclick="event.stopPropagation();_smgmtReEstimate(${ticket.number},this)"
         onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();_smgmtReEstimate(${ticket.number},this);}">stale</button>` : "";
    const reEstBtnHtml = _smgmtEstimatorAvailable && !ticket.estimate_stale ? `<button class="smgmt-reestimate-btn" tabindex="0" title="Re-estimate this ticket"
         onclick="event.stopPropagation();_smgmtReEstimate(${ticket.number},this)"
         onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();_smgmtReEstimate(${ticket.number},this);}">Re-estimate</button>` : "";
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
        <span class="t-dbtn-label">Details</span> <span id="caret-${sk}-${ticket.number}">\u25BC</span>
      </button>
    </div>
    <div class="ticket-expand" id="ex-${sk}-${ticket.number}" style="display:none">
      <div class="ex-row">
        <span class="ex-label">Conflicts</span>
        <span class="ex-conflicts-val">\u2014</span>
      </div>
      <div class="ex-row">
        <span class="ex-label">Execution</span>
        <span class="ex-exec-val">\u2014</span>
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
    if (!ticketsEl)
      return;
    const filtered = _blApplyFilters(_blBacklogAll);
    if (countEl) {
      const total = _blBacklogAll.length, shown = filtered.length;
      countEl.textContent = total > 0 ? `${shown === total ? total : `${shown} of ${total}`} ticket${total !== 1 ? "s" : ""}` : "0 tickets";
    }
    const backlogBulkBtn = document.getElementById("smgmt-backlog-bulk-est-btn");
    if (backlogBulkBtn) {
      const hasUnsized = _blBacklogAll.some((t) => !_smgmtTicketHasEstimate(t));
      backlogBulkBtn.classList.toggle("hidden", !hasUnsized);
    }
    const sorted = [...filtered].sort((a, b) => b.number - a.number);
    const allSprintNums = (_smgmtData?.sprints || []).sort((a, b) => a - b);
    if (sorted.length === 0) {
      const msg = _blBacklogAll.length === 0 ? "No backlog tickets \u2014 all caught up" : "No tickets match the active filters";
      ticketsEl.innerHTML = `<div class="smgmt-drop-hint" style="padding:14px 18px;text-align:center;">${msg}</div>`;
    } else {
      ticketsEl.innerHTML = sorted.map((t) => _smgmtBacklogTicketHtml(t, allSprintNums)).join("");
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
    const sizePillHtml = sizeValue ? `<span class="smgmt-ticket-size-pill">${escHtml(sizeValue)}</span>` : "";
    const estHtml = _smgmtTicketEstHtml(ticket);
    const draftLabel = _smgmtOrderedLabels ? _smgmtOrderedLabels.find((l) => {
      if (_smgmtResolvedAncestors.has(l) || _smgmtRunningLabels.has(l))
        return false;
      const ps = ((_smgmtData?.sprint_plan_states || {})[l] || "").toLowerCase();
      return ["draft", "planned", "planning"].includes(ps);
    }) : null;
    const addToSprintBtn = draftLabel ? `<button class="smgmt-add-to-sprint-btn" tabindex="0"
         title="Add to ${escHtml(sprintLabelDisplay(draftLabel))}"
         onclick="event.stopPropagation();smgmtAddToDraft(${ticket.number},'${escHtml(draftLabel)}')">
         <i class="ti ti-circle-plus"></i> Add to ${escHtml(sprintLabelDisplay(draftLabel).replace("Sprint ", "S"))}</button>` : "";
    return `
    <div class="smgmt-ticket bl-row${isSelected ? " is-selected" : ""}" id="smgmt-ticket-${ticket.number}"
         data-issue="${ticket.number}"
         data-sprint=""${sizeAttr}
         data-labels="${escHtml(backlogLabelNames)}"
         onclick="_smgmtRowClick(event, ${ticket.number}, null)"
         oncontextmenu="_smgmtCtxMenuOpen(event,${ticket.number})">
      <input type="checkbox" class="smgmt-ticket-cb"
             ${isSelected ? "checked" : ""}
             onclick="event.stopPropagation()"
             onchange="_smgmtToggleSelect(${ticket.number}, this.checked)">
      <a class="smgmt-ticket-num" href="${escHtml(ticket.url || "#")}" target="_blank"
         rel="noopener" onclick="event.stopPropagation()">#${ticket.number}</a>
      <span class="smgmt-ticket-title" title="${escHtml(ticket.title)}">${escHtml(ticket.title)}</span>
      ${schedDepHtml}${sizePillHtml}${estHtml}
      ${addToSprintBtn}
      <button class="smgmt-row-menu-btn" tabindex="0" title="Ticket actions" aria-haspopup="true" aria-expanded="false"
              onclick="event.stopPropagation();_smgmtRowMenuOpen(event, ${ticket.number}, null, ${hasEstimate})"
              onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();_smgmtRowMenuOpen(event,${ticket.number},null,${hasEstimate});}">
        <i class="ti ti-menu-2"></i></button>
    </div>`;
  }
  function _smgmtAncestorMergeState(label, outcome) {
    if (!outcome)
      return "unknown";
    const counts = outcome.counts || {};
    const done = counts.done || 0;
    if (done === 0)
      return "failed";
    const meta = typeof _smgmtStateMeta === "function" ? _smgmtStateMeta(outcome, (outcome.issues || []).length) : { state: "unknown" };
    const state = meta.state;
    if (state === "ready_to_merge" || state === "partial_finished")
      return "needs_merge";
    if (state === "needs_rework")
      return "needs_merge";
    if (state === "completed")
      return "merged";
    if (_smgmtFinishedLabels && _smgmtFinishedLabels.has(label) && done > 0)
      return "merged";
    return "needs_merge";
  }
  function _smgmtAncestorCarrySummary(outcome, childLabel) {
    if (!outcome)
      return "";
    const counts = outcome.counts || {};
    const done = counts.done || 0;
    const carried = (counts.failed || 0) + (counts.skipped || 0);
    const childDisplay = childLabel ? sprintLabelDisplay(childLabel).replace("Sprint ", "") : "";
    let summary = `${done} merged`;
    if (carried > 0 && childDisplay)
      summary += ` \xB7 ${carried} reworked \u2192 ${childDisplay}`;
    else if (carried > 0)
      summary += ` \xB7 ${carried} reworked`;
    return summary;
  }
  function _smgmtAncestorTicketsHtml(label, outcome, childLabel) {
    const issues = outcome && outcome.issues || [];
    if (issues.length === 0) {
      return '<div class="slp-no-tickets">No ticket data available.</div>';
    }
    const repo = _smgmtRepo();
    const childDisplay = childLabel ? sprintLabelDisplay(childLabel).replace("Sprint ", "") : "";
    return issues.map((iss) => {
      const o = iss.outcome || "skipped";
      const isMerged = o === "done";
      const issueUrl = repo ? `https://github.com/${repo}/issues/${iss.number}` : "#";
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
          <span class="slp-fate-carried">carried \u2192 ${escHtml(childDisplay)}</span>
        </div>`;
      }
    }).join("");
  }
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
    const ticketsHtml = outcome ? _smgmtAncestorTicketsHtml(label, outcome, rerunInto) : '<div class="slp-no-tickets">Loading outcome data\u2026</div>';
    const rerunDisabled = _smgmtAnySprintRunning ? "disabled" : "";
    const rerunTitle = _smgmtAnySprintRunning ? 'title="Cannot re-run: another sprint is currently running."' : "";
    const actionsHtml = mergeState === "needs_merge" ? `<div class="slp-ancestor-actions">
          <button class="smgmt-run-btn smgmt-run-btn--rerun" ${rerunDisabled} ${rerunTitle}
                  onclick="event.stopPropagation();smgmtRerunSprint('${safeLabel}')">
            <i class="ti ti-refresh"></i> Re-run</button>
          <button class="smgmt-finish-btn sc-merge-link"
                  onclick="event.stopPropagation();smgmtFinishSprint('${safeLabel}')">
            <i class="ti ti-flag-check"></i> Merge Sprint</button>
        </div>` : "";
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
  function smgmtToggleAncestor(label) {
    const body = document.getElementById(`slp-body-${label}`);
    const toggleIcon = document.querySelector(
      `#smgmt-card-${CSS.escape(label)} .slp-ancestor-toggle i`
    );
    if (!body)
      return;
    const isExpanded = !body.hidden;
    body.hidden = isExpanded;
    if (toggleIcon) {
      toggleIcon.className = isExpanded ? "ti ti-chevron-right" : "ti ti-chevron-down";
    }
    try {
      localStorage.setItem(`slp_ancestor_${label}`, isExpanded ? "0" : "1");
    } catch (_) {
    }
  }
  function _smgmtFocusGuideHtml(data, orderedLabels, bySprint) {
    const steps = [];
    const planStates = data.sprint_plan_states || {};
    const rerunInto = data.sprint_rerun_into || {};
    const finishedSet = new Set(data.finished_sprints || []);
    for (const label of orderedLabels || []) {
      const display = sprintLabelDisplay(label).replace("Sprint ", "Sprint ");
      if (_smgmtResolvedAncestors.has(label)) {
        steps.push({ text: `${escHtml(display)} needs a merge decision.`, priority: "high" });
      }
    }
    const draftLabel = (orderedLabels || []).find((l) => {
      if (_smgmtResolvedAncestors.has(l) || _smgmtRunningLabels.has(l))
        return false;
      const ps = (planStates[l] || "").toLowerCase();
      return ["draft", "planned", "planning"].includes(ps);
    });
    const upNextCandidates = (orderedLabels || []).filter((l) => {
      if (_smgmtResolvedAncestors.has(l))
        return false;
      if (_smgmtRunningLabels.has(l))
        return false;
      if (l === draftLabel)
        return false;
      if (finishedSet.has(l))
        return false;
      return (bySprint[l] || []).length > 0;
    });
    if (upNextCandidates.length > 0) {
      const nextDisplay = sprintLabelDisplay(upNextCandidates[0]);
      steps.push({ text: `${escHtml(nextDisplay)} is queued and ready to run.`, priority: "med" });
    }
    if (draftLabel) {
      const draftDisplay = sprintLabelDisplay(draftLabel);
      const tickets = (bySprint[draftLabel] || []).length;
      steps.push({
        text: `Finish planning ${escHtml(draftDisplay)} \u2014 ${tickets} ticket${tickets !== 1 ? "s" : ""} added.`,
        priority: "low"
      });
    } else {
      steps.push({ text: "No draft sprint yet \u2014 create one to start planning.", priority: "low" });
    }
    if (steps.length === 0) {
      steps.push({ text: "Board is up to date.", priority: "low" });
    }
    const stepHtml = steps.map(
      (s, i) => `<div class="smgmt-focus-step"><span class="smgmt-focus-num smgmt-focus-num--${s.priority}">${i + 1}</span><span class="smgmt-focus-text">${s.text}</span></div>`
    ).join("");
    return `<div class="smgmt-focus-guide-title">Focus</div>` + stepHtml;
  }
  function _smgmtBudgetBarHtml(tickets) {
    const sizePoints = { S: 1, M: 2, L: 3, XL: 5 };
    let used = 0;
    for (const t of tickets || []) {
      const sz = _smgmtTicketSize(t);
      used += sizePoints[sz] || 0;
    }
    const capacity = 10;
    const pct = Math.min(100, Math.round(used / capacity * 100));
    const remaining = Math.max(0, capacity - used);
    const overBudget = used > capacity;
    const fillClass = overBudget ? "smgmt-budget-fill smgmt-budget-fill--over" : "smgmt-budget-fill";
    return `<div class="smgmt-budget-bar"><div class="smgmt-budget-bar-top"><span class="smgmt-budget-label">${used} / ${capacity} pts used</span><span class="smgmt-budget-headroom${overBudget ? " smgmt-budget-headroom--over" : ""}">` + (overBudget ? `${used - capacity} pts over` : `${remaining} pts remaining`) + `</span></div><div class="smgmt-budget-track"><div class="${fillClass}" style="width:${pct}%"></div></div></div>`;
  }
  function _smgmtDraftCardHtml(label, tickets) {
    const display = sprintLabelDisplay(label);
    const budgetBar = _smgmtBudgetBarHtml(tickets);
    const ticketRowsHtml = (tickets || []).map((t) => {
      const sizeValue = _smgmtTicketSize(t) || "";
      const sizePill = sizeValue ? `<span class="smgmt-ticket-size-pill">${escHtml(sizeValue)}</span>` : "";
      return `<div class="smgmt-ticket smgmt-plan-ticket" id="smgmt-ticket-${t.number}" data-issue="${t.number}" data-sprint="${escHtml(label)}" onclick="_smgmtRowClick(event,${t.number},'${escHtml(label)}')" oncontextmenu="_smgmtCtxMenuOpen(event,${t.number})"><a class="smgmt-ticket-num" href="${escHtml(t.url || "#")}" target="_blank" rel="noopener" onclick="event.stopPropagation()">#${t.number}</a><span class="smgmt-ticket-title" title="${escHtml(t.title)}">${escHtml(t.title)}</span>` + sizePill + `<button class="smgmt-row-menu-btn smgmt-plan-row-menu" tabindex="0" title="Ticket actions" aria-haspopup="true" onclick="event.stopPropagation();smgmtPlanningRowMenu(event,${t.number},'${escHtml(label)}')"><i class="ti ti-dots"></i></button></div>`;
    }).join("");
    const goalInputId = `smgmt-goal-${CSS.escape ? CSS.escape(label) : label}`;
    const runBtnId = `smgmt-run-btn-${CSS.escape ? CSS.escape(label) : label}`;
    return `<div class="smgmt-sprint-card smgmt-draft-card" id="smgmt-card-${escHtml(label)}"><div class="smgmt-card-head"><span class="smgmt-sprint-name">${escHtml(display)}</span><span class="smgmt-draft-badge">Draft</span><div class="smgmt-card-actions"><button class="smgmt-run-btn" id="${escHtml(runBtnId)}" disabled title="Enter a sprint goal to enable Run" onclick="smgmtRunSprint('${escHtml(label)}')"><i class="ti ti-player-play"></i> Run Sprint</button></div></div><div class="smgmt-goal-slot"><input class="smgmt-goal-input" id="${escHtml(goalInputId)}" type="text" placeholder="Set a sprint goal\u2026" oninput="smgmtDraftGoalInput(this,'${escHtml(runBtnId)}')"></div>` + // Budget bar
    budgetBar + // Ticket rows
    `<div class="smgmt-plan-tickets">` + (ticketRowsHtml || `<div class="smgmt-plan-empty">No tickets yet \u2014 add from Backlog below.</div>`) + `</div><div class="smgmt-add-ticket-row"><button class="smgmt-add-ticket-btn" onclick="smgmtOpenTicketPicker('${escHtml(label)}')"><i class="ti ti-circle-plus"></i> Add ticket</button></div></div>`;
  }
  function _smgmtUpdateAncestorRow(label, outcome) {
    const card = document.getElementById(`smgmt-card-${label}`);
    if (!card || !card.classList.contains("slp-ancestor-row"))
      return;
    const childLabel = (_smgmtData?.sprint_rerun_into || {})[label];
    const newHtml = _smgmtAncestorRowHtml(label, outcome, childLabel);
    const wasExpanded = document.getElementById(`slp-body-${label}`)?.hidden === false;
    const tmp = document.createElement("div");
    tmp.innerHTML = newHtml;
    const newCard = tmp.firstElementChild;
    if (newCard) {
      card.replaceWith(newCard);
      if (wasExpanded) {
        const newBody = document.getElementById(`slp-body-${label}`);
        if (newBody)
          newBody.hidden = false;
        const newIcon = document.querySelector(
          `#smgmt-card-${CSS.escape(label)} .slp-ancestor-toggle i`
        );
        if (newIcon)
          newIcon.className = "ti ti-chevron-down";
      }
    }
  }

  // apps/dashboard/static/src/sprint-board/index.js
  globalThis._rrOpen = _rrOpen;
  globalThis._rrClose = _rrClose;
  globalThis._rrCatClass = _rrCatClass;
  globalThis._rrUpdateState = _rrUpdateState;
  globalThis._rrSelectAll = _rrSelectAll;
  globalThis.smgmtRerunSprint = smgmtRerunSprint;
  globalThis._rrConfirm = _rrConfirm;
  globalThis._fsOpen = _fsOpen;
  globalThis._fsClose = _fsClose;
  globalThis._fsCatClass = _fsCatClass;
  globalThis._fsSelectAll = _fsSelectAll;
  globalThis.smgmtFinishSprint = smgmtFinishSprint;
  globalThis._fsConfirm = _fsConfirm;
  globalThis._fsRetry = _fsRetry;
  globalThis._bcOpen = _bcOpen;
  globalThis._bcClose = _bcClose;
  globalThis._bcCatClass = _bcCatClass;
  globalThis._bcSelectAll = _bcSelectAll;
  globalThis.smgmtBulkCompleteSprint = smgmtBulkCompleteSprint;
  globalThis._bcConfirm = _bcConfirm;
  globalThis.smgmtRunBlockedToast = smgmtRunBlockedToast;
  globalThis.smgmtRunSprint = smgmtRunSprint2;
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
  globalThis._pfStepperInit = _pfStepperInit;
  globalThis._pfStepState = _pfStepState;
  globalThis._pfStepperAnimate = _pfStepperAnimate;
  globalThis._pfStepperSummary = _pfStepperSummary;
  globalThis.smgmtKickoffRun = smgmtKickoffRun;
  globalThis.smgmtKickoffRetry = smgmtKickoffRetry;
  globalThis.computeDropPlan = computeDropPlan;
  globalThis._smgmtUpdateSelectionUI = _smgmtUpdateSelectionUI2;
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
  globalThis._smgmtBoardLock = _smgmtBoardLock2;
  globalThis._smgmtBoardUnlock = _smgmtBoardUnlock2;
  globalThis._smgmtBoardProgress = _smgmtBoardProgress2;
  globalThis._smgmtBoardLog = _smgmtBoardLog2;
  globalThis.loadSprintMgmt = loadSprintMgmt2;
  globalThis._smgmtSprintLabelSortKey = _smgmtSprintLabelSortKey;
  globalThis._smgmtRender = _smgmtRender2;
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
  globalThis._smgmtApplyRerunOptimistic = _smgmtApplyRerunOptimistic2;
  globalThis._smgmtAncestorMergeState = _smgmtAncestorMergeState;
  globalThis._smgmtAncestorCarrySummary = _smgmtAncestorCarrySummary;
  globalThis._smgmtAncestorTicketsHtml = _smgmtAncestorTicketsHtml;
  globalThis._smgmtAncestorRowHtml = _smgmtAncestorRowHtml;
  globalThis.smgmtToggleAncestor = smgmtToggleAncestor;
  globalThis._smgmtUpdateAncestorRow = _smgmtUpdateAncestorRow;
  globalThis._smgmtSchedToggleHtml = _smgmtSchedToggleHtml2;
  globalThis.smgmtToggleRunOnSchedule = smgmtToggleRunOnSchedule;
  globalThis._smgmtHydrateSchedToggles = _smgmtHydrateSchedToggles2;
  globalThis.smgmtPlanNextSprint = smgmtPlanNextSprint;
  globalThis._smgmtLoadPendingSignoff = _smgmtLoadPendingSignoff;
  globalThis._histNeedsActionCount = _histNeedsActionCount;
  globalThis._histLoadLedger = _histLoadLedger2;
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
  var root = typeof window !== "undefined" ? window : globalThis;
  root.colorizeLogLine = colorizeLogLine2;
  root.escapeLogHtml = escapeLogHtml;
  root.extractRaw = extractRaw;
  root.AGENT_NAMES = AGENT_NAMES;
  root.renderProgressActivity = renderProgressActivity2;
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
