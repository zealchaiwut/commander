(() => {
  // apps/dashboard/static/src/logpanel.js
  var AGENT_NAMES = [
    "coder",
    "tester",
    "reviewer",
    "documenter",
    "estimator",
    "BA"
  ];
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
  function patchProgressActivityInPlace2(rootId, payload, opts) {
    if (typeof document === "undefined" || !rootId)
      return false;
    const root2 = document.getElementById(rootId);
    if (!root2)
      return false;
    const status = payload.status || "running";
    if (status === "done" || status === "error")
      return false;
    const mode = payload.mode || _detectMode(payload);
    if (mode !== "bar")
      return false;
    const fill = root2.querySelector(".pa-bar-fill");
    if (!fill)
      return false;
    const done = Number(payload.done ?? 0);
    const total = Number(payload.total ?? 0);
    const pct = total > 0 ? Math.min(100, Math.round(done / total * 100)) : 0;
    fill.style.transform = `scaleX(${pct / 100})`;
    const cur = root2.querySelector(".pa-current");
    if (cur && payload.current != null)
      cur.textContent = String(payload.current);
    const counts = root2.querySelector(".pa-counts");
    if (counts) {
      counts.textContent = total > 0 ? `${done} of ${total}` : "";
    }
    if (Array.isArray(payload.log_tail)) {
      updateProgressActivityLog(rootId, payload.log_tail, opts && opts.colorize);
    }
    return true;
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
  background: var(--green-bg);
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
  background: linear-gradient(90deg, transparent 0%, rgba(255,255,255,.1) 50%, transparent 100%);
  animation: pa-shimmer 1.8s ease-in-out infinite;
}
.pa-bar-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 16px 0;
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
  padding: 4px 0;
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
  gap: 8px;
  padding: 8px 16px;
}
.pa-spinner {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  border: 2px solid var(--green-bg);
  border-top-color: var(--green);
  border-radius: 50%;
  animation: pa-spin .8s linear infinite;
}
.pa-indet-shimmer {
  height: 4px;
  flex: 1;
  background: var(--green-bg);
  border-radius: 2px;
  position: relative;
  overflow: hidden;
}
.pa-indet-shimmer::after {
  content: '';
  position: absolute;
  top: 0; left: -40%;
  width: 40%; height: 100%;
  background: linear-gradient(90deg, transparent 0%, var(--green-bg) 50%, transparent 100%);
  animation: pa-shimmer 1.8s ease-in-out infinite;
}

/* \u2500\u2500 Done end state \u2500\u2500 */
.pa-done {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
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
  padding: 8px 16px;
}
.pa-error-msg {
  font-size: 13px;
  color: var(--red);
  margin-bottom: 8px;
}
.pa-retry-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 12px;
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
  padding: 8px 16px;
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
  padding: 0 16px 8px;
}
.pa-log-stream.pa-log-collapsed { display: none; }
.pa-log-line {
  display: flex;
  gap: 8px;
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

  // apps/dashboard/static/src/progress-host.js
  var BOARD_OVERLAY_PA_ID = "board-overlay-pa";
  var _payloadById = /* @__PURE__ */ new Map();
  var _MAX_LOG_LINES = 200;
  function _resolveHost(host) {
    if (!host)
      return null;
    if (typeof host === "string") {
      if (typeof document === "undefined")
        return null;
      return document.getElementById(host);
    }
    return host;
  }
  function _resolvePaId(hostEl, explicitId) {
    if (explicitId)
      return explicitId;
    if (hostEl && hostEl.dataset && hostEl.dataset.paId)
      return hostEl.dataset.paId;
    const hostId = hostEl && hostEl.id ? hostEl.id : "progress-activity-host";
    return `${hostId}-pa`;
  }
  function _snapshot(payload) {
    return JSON.parse(JSON.stringify(payload || {}));
  }
  function _storePayload(paId, payload) {
    const snap = _snapshot(payload);
    _payloadById.set(paId, snap);
    return snap;
  }
  function _logStreamId(paId) {
    return `pa-log-stream-${paId}`;
  }
  function _captureLogScroll(paId) {
    if (typeof document === "undefined")
      return null;
    const el = document.getElementById(_logStreamId(paId));
    if (!el)
      return null;
    return {
      top: el.scrollTop,
      atBottom: el.scrollHeight - el.scrollTop - el.clientHeight < 8
    };
  }
  function _restoreLogScroll(paId, state) {
    if (!state || typeof document === "undefined")
      return;
    const el = document.getElementById(_logStreamId(paId));
    if (!el)
      return;
    if (state.atBottom)
      el.scrollTop = el.scrollHeight;
    else
      el.scrollTop = state.top;
  }
  function _renderIntoHost(hostEl, payload, opts) {
    if (!hostEl)
      return;
    const renderOpts = opts || {};
    const paId = _resolvePaId(hostEl, renderOpts.id);
    const scrollState = _captureLogScroll(paId);
    hostEl.innerHTML = renderProgressActivity2(payload, renderOpts);
    _restoreLogScroll(paId, scrollState);
  }
  function mountProgressActivity2(host, payload, opts) {
    const hostEl = _resolveHost(host);
    if (!hostEl)
      return null;
    const paId = _resolvePaId(hostEl, opts && opts.id);
    const renderOpts = Object.assign({}, opts || {}, { id: paId });
    const next = _storePayload(paId, payload || {});
    if (hostEl.dataset)
      hostEl.dataset.paId = paId;
    hostEl.hidden = false;
    _renderIntoHost(hostEl, next, renderOpts);
    return next;
  }
  function getProgressActivityPayload(host) {
    const hostEl = _resolveHost(host);
    const paId = hostEl ? _resolvePaId(hostEl) : typeof host === "string" ? host : null;
    if (!paId)
      return null;
    const payload = _payloadById.get(paId);
    return payload ? _snapshot(payload) : null;
  }
  function patchProgressActivity(host, patch, opts) {
    const hostEl = _resolveHost(host);
    if (!hostEl)
      return null;
    const paId = _resolvePaId(hostEl, opts && opts.id);
    const prev = _payloadById.get(paId) || {};
    const next = Object.assign({}, prev, patch || {});
    if (hostEl.dataset)
      hostEl.dataset.paId = paId;
    _storePayload(paId, next);
    _renderIntoHost(hostEl, next, Object.assign({}, opts || {}, { id: paId }));
    return _snapshot(next);
  }
  function patchProgressActivityStep(host, stepKey, state, note, opts) {
    const hostEl = _resolveHost(host);
    if (!hostEl)
      return null;
    const paId = _resolvePaId(hostEl, opts && opts.id);
    const prev = _payloadById.get(paId) || {};
    const steps = Array.isArray(prev.steps) ? prev.steps.slice() : [];
    const idx = steps.findIndex((s) => s && s.key === stepKey);
    const normState = state === "fail" ? "failed" : state;
    if (idx >= 0) {
      steps[idx] = Object.assign({}, steps[idx], { state: normState, note: note || "" });
    } else {
      steps.push({ key: stepKey, label: stepKey, state: normState, note: note || "" });
    }
    return patchProgressActivity(
      hostEl,
      { steps, mode: prev.mode || "stepper" },
      Object.assign({}, opts || {}, { id: paId })
    );
  }
  function appendProgressActivityLog2(host, line, type, opts) {
    const hostEl = _resolveHost(host);
    if (!hostEl)
      return null;
    const paId = _resolvePaId(hostEl, opts && opts.id);
    const prev = _payloadById.get(paId) || {};
    const nextTail = Array.isArray(prev.log_tail) ? prev.log_tail.slice() : [];
    if (line != null && line !== "") {
      if (typeof line === "string") {
        nextTail.push({
          type: type || "dispatch",
          message: line,
          timestamp: (/* @__PURE__ */ new Date()).toLocaleTimeString()
        });
      } else {
        nextTail.push(line);
      }
    }
    const logTail = nextTail.slice(-_MAX_LOG_LINES);
    return patchProgressActivity(
      hostEl,
      { log_tail: logTail },
      Object.assign({}, opts || {}, { id: paId })
    );
  }
  function unmountProgressActivity2(host) {
    const hostEl = _resolveHost(host);
    if (!hostEl)
      return;
    const paId = _resolvePaId(hostEl);
    hostEl.innerHTML = "";
    hostEl.hidden = true;
    if (hostEl.dataset)
      delete hostEl.dataset.paId;
    _payloadById.delete(paId);
  }

  // apps/dashboard/static/src/shell/tabs.js
  var _GROUP_CHILDREN = {
    manage: ["logs", "deploy", "metrics", "bulk-create"],
    planning: [
      "timeline",
      "compare",
      "est-vs-actual",
      "calibration",
      "notes",
      "roadmap",
      "advisor"
    ]
  };
  function computeRovingTabindex(tab, onGlobalSettings) {
    return Object.fromEntries(
      ["sprint-mgmt", "tickets", "manage", "planning", "settings"].map((t) => {
        const ownsTab = !onGlobalSettings && (t === tab || _GROUP_CHILDREN[t] && _GROUP_CHILDREN[t].includes(tab));
        return [t, ownsTab ? 0 : -1];
      })
    );
  }
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
    const _topLevelTabs = [
      "sprint-mgmt",
      "tickets",
      "manage",
      "planning",
      "settings"
    ];
    [
      "sprint-mgmt",
      "tickets",
      "logs",
      "deploy",
      "bulk-create",
      "timeline",
      "compare",
      "metrics",
      "est-vs-actual",
      "calibration",
      "notes",
      "roadmap",
      "advisor",
      "settings"
    ].forEach((t) => {
      const btn = document.getElementById("stab-" + t);
      if (!btn)
        return;
      const isActive = !onGlobalSettings && t === tab;
      btn.classList.toggle("active", isActive);
      btn.setAttribute("aria-selected", String(isActive));
    });
    const _rovingMap = computeRovingTabindex(tab, onGlobalSettings);
    _topLevelTabs.forEach((t) => {
      const suffix = t === "manage" ? "manage-trigger" : t === "planning" ? "planning-trigger" : t;
      const btn = document.getElementById("stab-" + suffix);
      if (!btn)
        return;
      btn.tabIndex = _rovingMap[t];
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
    [
      "sprint-mgmt",
      "tickets",
      "logs",
      "deploy",
      "bulk-create",
      "timeline",
      "compare",
      "metrics",
      "est-vs-actual",
      "calibration",
      "notes",
      "roadmap",
      "advisor",
      "settings",
      "global-settings"
    ].forEach((t) => {
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
      const enabledTabs = [
        "sprint-mgmt",
        "tickets",
        "manage",
        "logs",
        "deploy",
        "metrics",
        "planning",
        "roadmap",
        "advisor",
        "settings"
      ];
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

  // apps/dashboard/static/src/shell/features.js
  var _features = null;
  function commanderFeatures() {
    return _features || {};
  }
  function signoffEnabled() {
    return commanderFeatures().signoff === true;
  }
  function advisorEnabled() {
    return commanderFeatures().advisor === true;
  }
  function planningEnabled() {
    return commanderFeatures().planning === true;
  }
  async function loadCommanderFeatures() {
    try {
      const res = await fetch("/api/environment", { cache: "no-store" });
      if (!res.ok)
        throw new Error(String(res.status));
      const data = await res.json();
      _features = data.features || {};
    } catch {
      _features = { signoff: false, advisor: false, planning: false };
    }
    const root2 = typeof window !== "undefined" ? window : globalThis;
    root2._commanderFeatures = _features;
    applyFeatureFlags();
    return _features;
  }
  function _hide(el) {
    if (!el)
      return;
    el.classList.add("hidden");
    el.setAttribute("aria-hidden", "true");
  }
  function applyFeatureFlags() {
    if (!advisorEnabled()) {
      _hide(document.getElementById("stab-advisor"));
      _hide(document.getElementById("pane-advisor"));
    }
    if (!planningEnabled()) {
      _hide(document.getElementById("smgmt-plan-next-btn"));
      _hide(document.getElementById("hnav-milestone"));
    }
    if (!signoffEnabled()) {
      _hide(document.getElementById("snav-signoff"));
    }
    if (!advisorEnabled() && !planningEnabled()) {
      const group = document.getElementById("stab-group-planning");
      if (group)
        group.style.display = "none";
    }
  }

  // apps/dashboard/static/src/settings/cleanup.js
  var CLN_PA_ID = "ps-cln-pa";
  var _psCleanupConfirmFn = null;
  var _psCleanupBusy = false;
  function _psProjectSlug() {
    const slug = typeof _slug !== "undefined" && _slug || window._currentProjectSlug || "";
    return String(slug || "").trim();
  }
  function _psProjectRepo() {
    const slug = _psProjectSlug();
    if (typeof _cachedFullRepo !== "undefined" && slug && _cachedFullRepo[slug]) {
      return _cachedFullRepo[slug];
    }
    return slug;
  }
  function _psCleanupStatus(text) {
    const el = document.getElementById("ps-cleanup-status");
    if (el)
      el.textContent = text || "";
  }
  function _psCleanupLog(tag, message, kind, data) {
    const wrap = document.getElementById("ps-cleanup-log");
    const body = document.getElementById("ps-cleanup-log-body");
    if (!body)
      return;
    if (wrap)
      wrap.hidden = false;
    const ts = (/* @__PURE__ */ new Date()).toLocaleTimeString();
    const kindClass = kind === "ok" ? "ps-cleanup-log-line--ok" : kind === "err" ? "ps-cleanup-log-line--err" : "ps-cleanup-log-line--step";
    let extra = "";
    if (data !== void 0) {
      try {
        extra = " " + JSON.stringify(data);
      } catch (_) {
        extra = " " + String(data);
      }
    }
    const line = document.createElement("div");
    line.className = "ps-cleanup-log-line " + kindClass;
    line.textContent = ts + " [" + tag + "] " + message + extra;
    body.appendChild(line);
    body.scrollTop = body.scrollHeight;
  }
  function psCleanupLogClear() {
    const body = document.getElementById("ps-cleanup-log-body");
    if (body)
      body.innerHTML = "";
    const wrap = document.getElementById("ps-cleanup-log");
    if (wrap)
      wrap.hidden = true;
  }
  function _psCleanupModalReset() {
    _psCleanupConfirmFn = null;
    _psCleanupBusy = false;
    const err = document.getElementById("ps-cln-error");
    if (err) {
      err.textContent = "";
      err.classList.add("hidden");
    }
    const list = document.getElementById("ps-cln-list");
    if (list)
      list.innerHTML = "";
    const summary = document.getElementById("ps-cln-summary");
    if (summary)
      summary.textContent = "";
    const review = document.getElementById("ps-cln-review");
    const progress = document.getElementById("ps-cln-progress");
    if (review)
      review.hidden = false;
    if (progress) {
      progress.hidden = true;
      progress.innerHTML = "";
      unmountProgressActivity("ps-cln-pa-host");
    }
    const confirmBtn = document.getElementById("ps-cln-confirm");
    const doneBtn = document.getElementById("ps-cln-done");
    const cancelBtn = document.getElementById("ps-cln-cancel");
    if (confirmBtn) {
      confirmBtn.hidden = false;
      confirmBtn.disabled = false;
    }
    if (doneBtn)
      doneBtn.hidden = true;
    if (cancelBtn)
      cancelBtn.hidden = false;
  }
  function _psCleanupModalClose() {
    if (_psCleanupBusy)
      return;
    document.getElementById("ps-cln-backdrop")?.classList.add("hidden");
    document.getElementById("ps-cln-modal")?.classList.add("hidden");
    if (typeof _clearBodyInert === "function")
      _clearBodyInert();
    _psCleanupModalReset();
  }
  function _psCleanupModalOpen(title) {
    _psCleanupModalReset();
    const titleEl = document.getElementById("ps-cln-title");
    if (titleEl)
      titleEl.textContent = title || "Cleanup";
    document.getElementById("ps-cln-backdrop")?.classList.remove("hidden");
    document.getElementById("ps-cln-modal")?.classList.remove("hidden");
    if (typeof _setBodyInert === "function") {
      _setBodyInert(["ps-cln-backdrop", "ps-cln-modal"]);
    }
  }
  function _psCleanupModalLoading(message) {
    const progress = document.getElementById("ps-cln-progress");
    const review = document.getElementById("ps-cln-review");
    if (review)
      review.hidden = true;
    if (!progress)
      return;
    progress.hidden = false;
    progress.innerHTML = '<div id="ps-cln-pa-host"></div>';
    mountProgressActivity("ps-cln-pa-host", {
      status: "running",
      mode: "indeterminate",
      current: message || "Working\u2026",
      log_tail: []
    }, { id: CLN_PA_ID, hideLog: true });
    const confirmBtn = document.getElementById("ps-cln-confirm");
    const cancelBtn = document.getElementById("ps-cln-cancel");
    if (confirmBtn)
      confirmBtn.hidden = true;
    if (cancelBtn)
      cancelBtn.hidden = true;
  }
  function _psCleanupModalShowReview(opts) {
    const review = document.getElementById("ps-cln-review");
    const progress = document.getElementById("ps-cln-progress");
    if (progress) {
      progress.hidden = true;
      progress.innerHTML = "";
    }
    if (review)
      review.hidden = false;
    const items = opts.items || [];
    const shown = items.slice(0, 60);
    const more = items.length - shown.length;
    const summaryEl = document.getElementById("ps-cln-summary");
    if (summaryEl)
      summaryEl.textContent = opts.summary || "";
    const listEl = document.getElementById("ps-cln-list");
    if (listEl) {
      if (!items.length) {
        listEl.innerHTML = '<li style="color:var(--text-muted)">' + escHtml(opts.emptyMsg || "Nothing to clean.") + "</li>";
      } else {
        listEl.innerHTML = shown.map((f) => "<li>" + escHtml(String(f)) + "</li>").join("") + (more > 0 ? '<li style="color:var(--text-muted)">\u2026 and ' + more + " more</li>" : "");
      }
    }
    _psCleanupConfirmFn = opts.onConfirm || null;
    const confirmBtn = document.getElementById("ps-cln-confirm");
    const doneBtn = document.getElementById("ps-cln-done");
    const cancelBtn = document.getElementById("ps-cln-cancel");
    if (confirmBtn) {
      const canConfirm = !!(items.length && opts.onConfirm);
      confirmBtn.hidden = !canConfirm;
      confirmBtn.disabled = !canConfirm;
      confirmBtn.textContent = opts.confirmLabel || "Confirm";
    }
    if (doneBtn)
      doneBtn.hidden = true;
    if (cancelBtn)
      cancelBtn.hidden = false;
  }
  function _psCleanupModalShowDone(message) {
    _psCleanupConfirmFn = null;
    _psCleanupBusy = false;
    const summaryEl = document.getElementById("ps-cln-summary");
    if (summaryEl)
      summaryEl.textContent = message || "Done.";
    const listEl = document.getElementById("ps-cln-list");
    if (listEl)
      listEl.innerHTML = "";
    const confirmBtn = document.getElementById("ps-cln-confirm");
    const doneBtn = document.getElementById("ps-cln-done");
    const cancelBtn = document.getElementById("ps-cln-cancel");
    if (confirmBtn)
      confirmBtn.hidden = true;
    if (cancelBtn)
      cancelBtn.hidden = true;
    if (doneBtn)
      doneBtn.hidden = false;
    unmountProgressActivity("ps-cln-pa-host");
    const progress = document.getElementById("ps-cln-progress");
    if (progress) {
      progress.hidden = true;
      progress.innerHTML = "";
    }
  }
  function _psCleanupModalShowError(message) {
    _psCleanupBusy = false;
    _psCleanupConfirmFn = null;
    const err = document.getElementById("ps-cln-error");
    if (err) {
      err.textContent = message || "Something went wrong.";
      err.classList.remove("hidden");
    }
    const confirmBtn = document.getElementById("ps-cln-confirm");
    const cancelBtn = document.getElementById("ps-cln-cancel");
    if (confirmBtn)
      confirmBtn.hidden = true;
    if (cancelBtn)
      cancelBtn.hidden = false;
    unmountProgressActivity("ps-cln-pa-host");
  }
  async function _psCleanupModalConfirm() {
    if (!_psCleanupConfirmFn || _psCleanupBusy)
      return;
    _psCleanupBusy = true;
    const confirmBtn = document.getElementById("ps-cln-confirm");
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Working\u2026";
    }
    _psCleanupModalLoading("Applying\u2026");
    try {
      const msg = await _psCleanupConfirmFn((line, kind) => {
        appendProgressActivityLog("ps-cln-pa-host", line, kind === "err" ? "fail" : "dispatch", { id: CLN_PA_ID });
      });
      _psCleanupModalShowDone(msg || "Done.");
    } catch (e) {
      _psCleanupLog("cleanup", e.message || String(e), "err");
      _psCleanupModalShowError(e.message || String(e));
    }
  }
  async function _psCleanupPreviewFlow(tag, title, fetchPreview, buildReview) {
    if (_psCleanupBusy)
      return;
    _psCleanupStatus("");
    _psCleanupLog(tag, "Starting preview\u2026", "step");
    _psCleanupModalOpen(title);
    _psCleanupModalLoading("Scanning\u2026");
    try {
      const data = await fetchPreview();
      const review = buildReview(data);
      _psCleanupLog(tag, review.logMsg || "Preview ready", "ok", review.logData);
      _psCleanupModalShowReview(review);
    } catch (e) {
      const msg = e.message || String(e);
      _psCleanupLog(tag, msg, "err");
      _psCleanupStatus("Preview failed: " + msg);
      _psCleanupModalShowError(msg);
    }
  }
  async function _sprintCleanupPost(dryRun) {
    const slug = _psProjectSlug();
    if (!slug)
      throw new Error("Project not loaded \u2014 switch to Settings again.");
    const resp = await fetch("/api/maintenance/sprints/cleanup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: slug, dry_run: dryRun })
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || "HTTP " + resp.status);
    }
    return resp.json();
  }
  async function _testFilesCleanupPost(dryRun) {
    const slug = _psProjectSlug();
    if (!slug)
      throw new Error("Project not loaded \u2014 switch to Settings again.");
    const resp = await fetch("/api/maintenance/tests/cleanup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: slug, keep: 100, dry_run: dryRun })
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || "HTTP " + resp.status);
    }
    return resp.json();
  }
  async function sprintCleanupPreview() {
    await _psCleanupPreviewFlow("sprint-files", "Archive stale sprint runtime files", () => _sprintCleanupPost(true), (data) => {
      const files = data && data.archived || [];
      return {
        logMsg: files.length ? files.length + " file(s) to archive" : "Nothing to archive",
        logData: { count: files.length },
        title: "Archive stale sprint runtime files",
        summary: files.length ? files.length + " file(s) will be archived." : "",
        items: files,
        emptyMsg: "No stale runtime files to archive.",
        confirmLabel: "Archive " + files.length,
        onConfirm: async (log) => {
          if (log)
            log("Archiving sprint runtime files\u2026", "step");
          const r = await _sprintCleanupPost(false);
          const n = r && r.archived ? r.archived.length : 0;
          const kept = r && typeof r.kept_count === "number" ? r.kept_count : "?";
          _psCleanupLog("sprint-files", "Archived " + n + " file(s)", "ok", { kept_count: kept });
          _psCleanupStatus("Archived " + n + " file(s); " + kept + " kept in place.");
          return "Archived " + n + " file(s); " + kept + " kept.";
        }
      };
    });
  }
  async function testFilesCleanupPreview() {
    await _psCleanupPreviewFlow("test-files", "Clean old test files", () => _testFilesCleanupPost(true), (data) => {
      const files = data && data.remove || [];
      return {
        logMsg: files.length ? files.length + " test file(s) to remove" : "No old test files",
        logData: { remove: files.length, kept: data && data.kept_count || 0 },
        title: "Clean old test files",
        summary: "Keeping " + (data && data.kept_count || 0) + " newest of " + (data && data.total_count || 0) + " test files (git recency).",
        items: files,
        emptyMsg: "No old test files to remove.",
        confirmLabel: "Delete " + files.length,
        onConfirm: async (log) => {
          if (log)
            log("Deleting old test files\u2026", "step");
          const r = await _testFilesCleanupPost(false);
          const n = r && r.deleted ? r.deleted.length : 0;
          _psCleanupLog("test-files", "Deleted " + n + " test file(s)", "ok", { kept: r && r.kept_count || 0 });
          _psCleanupStatus("Deleted " + n + " test file(s); kept " + (r && r.kept_count || 0) + ".");
          return "Deleted " + n + " test file(s); kept " + (r && r.kept_count || 0) + ".";
        }
      };
    });
  }
  async function psStaleBranchesScan() {
    const repo = _psProjectRepo();
    if (!repo) {
      _psCleanupLog("branches", "Project repo not loaded", "err");
      _psCleanupStatus("Project repo not loaded.");
      return;
    }
    if (_psCleanupBusy)
      return;
    _psCleanupStatus("");
    _psCleanupLog("branches", "Scanning remote for stale branches\u2026", "step");
    _psCleanupModalOpen("Scan stale branches");
    _psCleanupModalLoading("Scanning remote\u2026");
    _psCleanupBusy = true;
    const btn = document.getElementById("ps-stale-scan-btn");
    if (btn)
      btn.disabled = true;
    try {
      const resp = await fetch("/scan-stale-branches?repo=" + encodeURIComponent(repo));
      if (!resp.ok)
        throw new Error("HTTP " + resp.status);
      const data = await resp.json();
      const branches = (data.branches || []).map((b) => b.branch || b);
      if (typeof _histScanStale === "function")
        await _histScanStale();
      _psCleanupLog("branches", "Scan complete", "ok", { count: branches.length });
      _psCleanupStatus(branches.length ? branches.length + " stale branch(es) found." : "No stale branches found.");
      _psCleanupModalShowReview({
        title: "Scan stale branches",
        summary: branches.length ? branches.length + " feature branch(es) flagged on History rows." : "No leftover feature branches on the remote.",
        items: branches,
        emptyMsg: "No stale branches found.",
        confirmLabel: "",
        onConfirm: null
      });
      const confirmBtn = document.getElementById("ps-cln-confirm");
      const doneBtn = document.getElementById("ps-cln-done");
      if (confirmBtn)
        confirmBtn.hidden = true;
      if (doneBtn)
        doneBtn.hidden = false;
      _psCleanupBusy = false;
    } catch (e) {
      const msg = e.message || String(e);
      _psCleanupLog("branches", msg, "err");
      _psCleanupStatus("Scan failed: " + msg);
      _psCleanupModalShowError(msg);
    } finally {
      if (btn)
        btn.disabled = false;
      _psCleanupBusy = false;
    }
  }
  async function psPruneMergedBranches() {
    const repo = _psProjectRepo();
    if (!repo) {
      _psCleanupLog("branches", "Project repo not loaded", "err");
      _psCleanupStatus("Project repo not loaded.");
      return;
    }
    await _psCleanupPreviewFlow("branches", "Prune merged feature branches", async () => {
      const scanResp = await fetch("/scan-stale-branches?repo=" + encodeURIComponent(repo));
      if (!scanResp.ok)
        throw new Error("HTTP " + scanResp.status);
      const scanData = await scanResp.json();
      const branches = (scanData.branches || []).map((b) => b.branch || b);
      if (!branches.length)
        return { toDelete: [], skipped: [], branches: [] };
      const dryResp = await fetch("/cleanup-stale-branches", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo, branches, confirm: false })
      });
      if (!dryResp.ok)
        throw new Error("HTTP " + dryResp.status);
      const plan = await dryResp.json();
      return {
        toDelete: plan.to_delete || [],
        skipped: plan.skipped_unmerged || [],
        branches
      };
    }, (plan) => {
      const toDelete = plan.toDelete || [];
      const skipped = plan.skipped || [];
      return {
        logMsg: toDelete.length ? toDelete.length + " merged branch(es) to delete" : "No merged branches to delete",
        logData: { delete: toDelete.length, skipped: skipped.length },
        title: "Prune merged feature branches",
        summary: skipped.length ? skipped.length + " unmerged branch(es) skipped \u2014 never deleted." : "Only fully-merged branches are deleted.",
        items: toDelete,
        emptyMsg: "No merged branches to delete.",
        confirmLabel: "Delete " + toDelete.length,
        onConfirm: async (log) => {
          if (log)
            log("Deleting merged branches\u2026", "step");
          const resp = await fetch("/cleanup-stale-branches", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ repo, branches: plan.branches, confirm: true })
          });
          if (!resp.ok)
            throw new Error("HTTP " + resp.status);
          const result = await resp.json();
          const deleted = (result.deleted || []).length;
          const failed = (result.failed || []).length;
          if (typeof _histScanStale === "function")
            await _histScanStale();
          _psCleanupLog("branches", "Deleted " + deleted + " branch(es)", failed ? "err" : "ok", { failed });
          _psCleanupStatus("Deleted " + deleted + " merged branch" + (deleted !== 1 ? "es" : "") + (failed ? " (" + failed + " failed)" : "") + ".");
          return "Deleted " + deleted + " merged branch" + (deleted !== 1 ? "es" : "") + (failed ? " (" + failed + " failed)" : "") + ".";
        }
      };
    });
  }
  async function psCleanupModalConfirm() {
    await _psCleanupModalConfirm();
  }
  function sprintCleanupConfirm() {
  }
  function testFilesCleanupConfirm() {
  }
  function _psCleanupPaneClose() {
  }
  function _psCleanupPaneConfirm() {
  }

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
  globalThis._pfLlmProvider ??= "anthropic";
  globalThis._pfXLSuggestions ??= [];
  globalThis._pfStrictXLGate ??= false;
  globalThis._pfXLMinutesSaved ??= 0;
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
    if (globalThis._commanderFeatures && globalThis._commanderFeatures.planning !== true) {
      _smgmtShowToast("Plan next sprint is disabled.");
      return;
    }
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
    if (globalThis._commanderFeatures && globalThis._commanderFeatures.signoff !== true)
      return;
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
  var _HIST_ACTION_STATES = /* @__PURE__ */ new Set([
    "ready_to_merge",
    "needs_rework",
    "failed",
    "partial_finished"
  ]);
  var _HIST_INBOX_STATES = _HIST_ACTION_STATES;
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
  var _histLedgerCacheRepo = "";
  var _histLedgerCacheAt = 0;
  var _HIST_LEDGER_TTL_MS = 3e5;
  var _histLedgerInflight = null;
  var _histShowClosed = false;
  function _histResetLedgerCache() {
    _histLedgerData = [];
    globalThis._histLedgerData = _histLedgerData;
    _histLedgerCacheRepo = "";
    _histLedgerCacheAt = 0;
    _histLedgerInflight = null;
  }
  var _histRenderRaf = 0;
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
  function _histIssueChip(iss, opts) {
    opts = opts || {};
    const st = (iss.state || "").toLowerCase();
    const agent = (iss.agent_status || "").toLowerCase();
    if (iss.failure_reason || agent === "failed") {
      return { cls: "crashed", label: "CRASHED \xB7 in-progress" };
    }
    if (st === "merged" || agent === "completed" || agent === "done") {
      return { cls: "merged", label: "MERGED" };
    }
    if (opts.binary) {
      return { cls: "crashed", label: "NOT DONE" };
    }
    if (st === "closed")
      return { cls: "crashed", label: "CRASHED" };
    if (iss.time_spent != null)
      return { cls: "uat", label: "OPEN \xB7 UAT" };
    return { cls: "notrun", label: "NOT RUN" };
  }
  function _histSprintShowsBinaryIssues(s) {
    if (!s)
      return false;
    if ((s.end_reason || "").toLowerCase() === "queued")
      return false;
    if (_histSprintFailed(s))
      return true;
    const st = (s.lifecycle_state || "").toLowerCase();
    return [
      "partial_finished",
      "needs_rework",
      "ready_to_merge",
      "completed",
      "failed",
      "cancelled"
    ].includes(st);
  }
  function _histIssueIcon(iss, sprint) {
    const binary = _histSprintShowsBinaryIssues(sprint);
    const chip = _histIssueChip(iss, { binary });
    if (chip.cls === "merged") {
      return '<span class="iss-icon ok"><i class="ti ti-check"></i></span>';
    }
    if (chip.cls === "crashed") {
      return '<span class="iss-icon fail"><i class="ti ti-x"></i></span>';
    }
    return '<span class="iss-icon idle"></span>';
  }
  function _histIssueSucceeded(iss, sprint) {
    return _histIssueChip(iss, { binary: _histSprintShowsBinaryIssues(sprint) }).cls === "merged";
  }
  function _histProgressText(s, group) {
    const issues = group ? _histIssuesForDisplay(s, group) : Array.isArray(s.issues) ? s.issues : [];
    if (!issues.length)
      return "";
    const done = issues.filter((i) => _histIssueSucceeded(i, s)).length;
    const failed = issues.filter((i) => {
      const chip = _histIssueChip(i, { binary: _histSprintShowsBinaryIssues(s) });
      return chip.cls === "crashed";
    }).length;
    if (failed) {
      return `${done}/${issues.length} done \xB7 ${failed} failed`;
    }
    return `${done}/${issues.length} done`;
  }
  function _histLooseEndCount(s) {
    const r = s.reconciliation;
    if (r && Array.isArray(r.checks) && !r.all_clear) {
      const n = r.checks.filter((c) => !c.ok).length;
      if (n)
        return n;
    }
    const g = _histStaleBySprint[s.label];
    if (g && g.count)
      return g.count;
    return 0;
  }
  function _histHeadStatsHtml(s, group) {
    const parts = [];
    const progress = _histProgressText(s, group);
    if (progress)
      parts.push(progress);
    const stats = _histRunStats[s.label];
    const agentSecs = stats && stats.has_runs && stats.agent_total_seconds != null ? stats.agent_total_seconds : null;
    if (agentSecs != null) {
      parts.push(_histFmtSecs(agentSecs) + " agent");
    } else if (s.duration != null) {
      parts.push(_histFmtSecs(s.duration));
    }
    const looseN = _histLooseEndCount(s);
    if (looseN)
      parts.push(looseN + " loose end" + (looseN !== 1 ? "s" : ""));
    if (!parts.length)
      return "";
    return '<span class="hist-head-stats">' + parts.map((p) => '<span class="hist-head-stat">' + escHtml(p) + "</span>").join("") + "</span>";
  }
  function _histIssueLogUrl(s, issueNum) {
    const base = _histLogsUrl(s);
    if (issueNum == null)
      return base;
    const sep = base.includes("?") ? "&" : "?";
    return base + sep + "issue=" + encodeURIComponent(String(issueNum)) + "&view=raw";
  }
  function _histFailedIssueMeta(ft, s) {
    const issues = Array.isArray(s.issues) ? s.issues : [];
    const hit = issues.find((i) => String(i.ticket_id) === String(ft.ticket_id));
    return {
      title: _histIssueTitle(hit || ft, s),
      time: hit && hit.time_spent != null ? hit.time_spent : null
    };
  }
  function _histFailReasonParts(ft, s) {
    const meta = _histFailedIssueMeta(ft, s);
    const reason = String(ft.failure_reason || "Agent failed");
    let title = meta.title;
    let accent = reason;
    if (title && reason.toLowerCase().startsWith(title.toLowerCase())) {
      accent = reason.slice(title.length).replace(/^[\s·—-]+/, "").trim();
    } else if (!title) {
      accent = reason;
      title = "";
    }
    return { title, accent, time: meta.time };
  }
  function _histIssueTitle(iss, s, titleMap) {
    if (iss.title)
      return String(iss.title);
    const tid = iss.ticket_id;
    if (titleMap && tid != null) {
      const hit = titleMap.get(tid) || titleMap.get(String(tid));
      if (hit)
        return String(hit);
    }
    try {
      const tickets = s && s.label && _smgmtBySprint[s.label] || [];
      const hit = tickets.find((t) => String(t.number) === String(tid));
      if (hit && hit.title)
        return String(hit.title);
    } catch (_) {
    }
    try {
      for (const row of _histLedgerData || []) {
        const hit = (row.issues || []).find(
          (i) => String(i.ticket_id) === String(tid) && i.title
        );
        if (hit)
          return String(hit.title);
      }
    } catch (_) {
    }
    return "";
  }
  function _histBuildLineageTitleMap(group) {
    const map = /* @__PURE__ */ new Map();
    if (!group)
      return map;
    for (const s of _histGroupMembers(group)) {
      for (const iss of s.issues || []) {
        if (iss.ticket_id != null && iss.title) {
          map.set(iss.ticket_id, String(iss.title));
        }
      }
      try {
        const tickets = s.label && _smgmtBySprint[s.label] || [];
        for (const t of tickets) {
          if (t.number != null && t.title && !map.has(t.number)) {
            map.set(t.number, String(t.title));
          }
        }
      } catch (_) {
      }
    }
    return map;
  }
  function _histCanonicalOwnerLabel(ticketId, group) {
    if (!group || ticketId == null)
      return null;
    let bestSub = -1;
    let owner = null;
    for (const s of _histGroupMembers(group)) {
      const sub = _histLabelParts(s.label).sub;
      const listed = (s.issues || []).some(
        (i) => String(i.ticket_id) === String(ticketId)
      );
      if (listed && sub >= bestSub) {
        bestSub = sub;
        owner = s.label;
      }
    }
    return owner;
  }
  function _histIssuesForDisplay(s, group) {
    const issues = Array.isArray(s.issues) ? s.issues : [];
    if (!group)
      return issues;
    return issues.filter((iss) => {
      if (iss.ticket_id == null)
        return true;
      const owner = _histCanonicalOwnerLabel(iss.ticket_id, group);
      return owner === s.label;
    });
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
    if (er === "queued")
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
  function _histPartialChildrenHtml(s) {
    const state = (s.lifecycle_state || "").toLowerCase();
    if (state !== "partial_finished")
      return "";
    const children = Array.isArray(s.partial_children) ? s.partial_children : [];
    if (!children.length)
      return "";
    const links = children.map((c) => {
      const lbl = escHtml(c);
      const display = typeof sprintLabelDisplay === "function" ? sprintLabelDisplay(c) : c;
      return `<button type="button" class="hist-partial-link" onclick="event.stopPropagation();_histFocusLabel('${lbl}')">${escHtml(display)}</button>`;
    }).join("");
    return `<div class="hist-partial-block">
    <i class="ti ti-arrows-split"></i>
    <span>Tickets moved to child sprint${children.length !== 1 ? "s" : ""}: ${links}</span>
  </div>`;
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
    const displayState = s === "needs_rework" && (er === "natural" || er === "merge_sprint") && sprint && !_histSprintFailed(sprint) ? "ready_to_merge" : s === "needs_rework" && er === "queued" ? "queued_rerun" : s;
    const map = {
      completed: ["completed", "COMPLETED"],
      ready_to_merge: ["ready_to_merge", "READY TO MERGE"],
      queued_rerun: ["planning", "QUEUED \xB7 RE-RUN"],
      needs_rework: ["failed", "FAILED"],
      partial_finished: ["partial", "PARTIAL"],
      deleted: ["deleted", "DELETED"],
      running: ["running", "RUNNING"],
      draft: ["planning", "DRAFT"],
      planned: ["planning", "PLANNED"],
      finished: ["finished", "FINISHED"],
      failed: ["failed", "FAILED"],
      cancelled: ["failed", "FAILED"],
      planning: ["planning", "DRAFT"]
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
    return st === "needs_rework" || st === "failed" || st === "ready_to_merge" || st === "running" || st === "draft" || st === "planned" || st === "partial_finished";
  }
  var _histCollapseDefaultsApplied = /* @__PURE__ */ new Set();
  function _histAutoExpandRecent(groups) {
    const _expand = (s) => {
      if (!_histShouldAutoExpand(s))
        return;
      _histExpanded.add(s.label);
      _histLoadRunStats(s.label);
    };
    for (let i = 0; i < groups.length; i++) {
      const g = groups[i];
      const children = g.children || [];
      const baseLbl = g.baseLabel || g.baseSprint && g.baseSprint.label || "";
      if (children.length && baseLbl && !_histCollapseDefaultsApplied.has(baseLbl)) {
        _histCollapseDefaultsApplied.add(baseLbl);
        const parentSt = (g.baseSprint && g.baseSprint.lifecycle_state || "").toLowerCase();
        const anyChildOpen = children.some((c) => {
          const cst = (c.lifecycle_state || "").toLowerCase();
          return cst !== "completed" && cst !== "deleted";
        });
        if (g.baseSprint && parentSt === "completed" && !anyChildOpen) {
          _histExpanded.delete(g.baseSprint.label);
        }
      }
      if (i >= _histFoldSize)
        continue;
      if (children.length) {
        if (g.baseSprint && (_histShouldAutoExpand(g.baseSprint) || _histIssuesForDisplay(g.baseSprint, g).length)) {
          _expand(g.baseSprint);
        }
        for (const c of children) {
          if (_histShouldAutoExpand(c) || _histIssuesForDisplay(c, g).length) {
            _expand(c);
          }
        }
      } else {
        _expand(g.baseSprint);
      }
    }
  }
  function _histMergeGanttTickets(s, stats) {
    const byNum = /* @__PURE__ */ new Map();
    (Array.isArray(stats && stats.tickets) ? stats.tickets : []).forEach((t) => {
      byNum.set(String(t.ticket), t);
    });
    (Array.isArray(s.issues) ? s.issues : []).forEach((i) => {
      const id = i.ticket_id;
      if (id == null)
        return;
      const key = String(id);
      if (!byNum.has(key)) {
        byNum.set(key, { ticket: id, start: 0, end: 0, segments: [] });
      }
    });
    return Array.from(byNum.values()).sort((a, b) => {
      const sa = a.start ?? 0;
      const sb = b.start ?? 0;
      if (sa !== sb)
        return sa - sb;
      return Number(a.ticket) - Number(b.ticket);
    });
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
    return `<div class="stats" data-stats-label="${escHtml(s.label || "")}">
    <div class="stat-chips">${chips.join("")}</div>
    ${splitHtml}
  </div>`;
  }
  function _histSeedRunStatsFromInline(sprints) {
    if (!(globalThis._commanderFeatures && globalThis._commanderFeatures.history_aggregate === true)) {
      return;
    }
    if (!Array.isArray(sprints))
      return;
    for (const s of sprints) {
      if (s && s.label && s.run_stats != null && !(s.label in _histRunStats)) {
        _histRunStats[s.label] = s.run_stats;
      }
    }
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
      _histScheduleLedgerRender();
    } catch (_) {
    }
  }
  function _histScheduleLedgerRender() {
    if (_histRenderRaf)
      return;
    _histRenderRaf = requestAnimationFrame(() => {
      _histRenderRaf = 0;
      _histRenderLedger(_histLedgerData);
    });
  }
  function _histShowLedgerSkeleton() {
    const el = document.getElementById("hist-ledger");
    if (!el || _histLedgerData && _histLedgerData.length)
      return;
    el.innerHTML = `<div class="hist-ledger-skeleton" aria-busy="true" aria-label="Loading sprint history">
    <div class="hist-skeleton-card"></div>
    <div class="hist-skeleton-card"></div>
    <div class="hist-skeleton-card"></div>
  </div>`;
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
  function _histReconcileHtml(s) {
    const r = s.reconciliation;
    if (!r || !Array.isArray(r.checks) || !r.checks.length)
      return "";
    const allClear = !!r.all_clear;
    const items = r.checks.map((c) => {
      const ok = !!c.ok;
      const icon = ok ? "ti-circle-check" : "ti-alert-triangle";
      const detail = c.detail || (ok ? "OK" : "unresolved");
      return `<div class="recon-item ${ok ? "ok" : "fail"}">
      <i class="ti ${icon}"></i>
      <span><span class="recon-label">${escHtml(_histReconcileLabel(c.name))}:</span>
        <span class="recon-detail">${escHtml(detail)}</span></span>
    </div>`;
    }).join("");
    const failed = r.checks.filter((c) => !c.ok).length;
    const summary = allClear ? "All clear \u2014 no loose ends" : failed + (failed === 1 ? " item unresolved" : " items unresolved");
    return `<div class="recon">
    <div class="recon-head ${allClear ? "clear" : "flagged"}">
      <i class="ti ${allClear ? "ti-checks" : "ti-alert-triangle"}"></i>
      Reconciliation <span class="recon-summary">\xB7 ${escHtml(summary)}</span>
    </div>
    ${items}
  </div>`;
  }
  function _histLegendHtml() {
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
    if (!stats)
      return 0;
    if (stats.fix_round_seconds != null)
      return Math.max(0, Number(stats.fix_round_seconds) || 0);
    let total = 0;
    for (const t of stats.tickets || []) {
      for (const seg of t.segments || []) {
        if (seg.fix_round)
          total += seg.duration || 0;
      }
    }
    return total;
  }
  function _histAgentSeconds(stats) {
    const raw = stats && stats.agent_seconds || {};
    return {
      coder: raw.coder || 0,
      tester: raw.tester || 0,
      documenter: raw.documenter || 0,
      reviewer: raw.reviewer || 0
    };
  }
  function _histAgentBarSegments(stats) {
    if (!stats || !stats.has_runs)
      return [];
    const fixSecs = _histFixRoundSeconds(stats);
    const a = _histAgentSeconds(stats);
    const coderNet = Math.max(0, a.coder - fixSecs);
    const parts = [
      { key: "coder", seconds: coderNet, cls: "hist-bar-coder" },
      { key: "fix", seconds: fixSecs, cls: "hist-bar-fix" },
      { key: "tester", seconds: a.tester, cls: "hist-bar-tester" },
      { key: "documenter", seconds: a.documenter, cls: "hist-bar-documenter" },
      { key: "reviewer", seconds: a.reviewer, cls: "hist-bar-reviewer" }
    ].filter((p) => p.seconds > 0);
    const total = parts.reduce((n, p) => n + p.seconds, 0) || 1;
    return parts.map((p) => ({
      ...p,
      pct: Math.max(0.5, p.seconds / total * 100),
      label: _histFmtSecs(p.seconds)
    }));
  }
  function _histMetricsElapsedLabel(s, stats) {
    const hasRuns = !!(stats && stats.has_runs);
    const secs = hasRuns && stats.agent_total_seconds != null ? stats.agent_total_seconds : s.duration;
    if (secs == null)
      return "";
    return `${escHtml(_histFmtSecs(secs))} <small>elapsed</small>`;
  }
  function _histAgentTimeBarHtml(stats) {
    const segs = _histAgentBarSegments(stats);
    if (!segs.length) {
      return `<div class="hist-agent-bar hist-agent-bar--empty" aria-hidden="true"></div>`;
    }
    const inner = segs.map((seg) => {
      const showLabel = seg.key !== "fix" && seg.pct >= 8;
      return `<span class="hist-agent-bar-seg ${seg.cls}" style="width:${seg.pct}%" title="${escHtml(seg.label)}">${showLabel ? escHtml(seg.label) : ""}</span>`;
    }).join("");
    return `<div class="hist-agent-bar">${inner}</div>`;
  }
  function _histAgentBreakdownHtml(stats) {
    if (!stats || !stats.has_runs)
      return "";
    const fixSecs = _histFixRoundSeconds(stats);
    const a = _histAgentSeconds(stats);
    const coderNet = Math.max(0, a.coder - fixSecs);
    const rows = [
      { cls: "hist-swatch--coder", label: "coder", secs: coderNet },
      { cls: "hist-swatch--fix", label: "fix round", secs: fixSecs },
      { cls: "hist-swatch--tester", label: "tester", secs: a.tester },
      { cls: "hist-swatch--documenter", label: "documenter", secs: a.documenter },
      { cls: "hist-swatch--reviewer", label: "reviewer", secs: a.reviewer }
    ].filter((r) => r.secs > 0);
    if (!rows.length)
      return "";
    return `<div class="hist-agent-breakdown">${rows.map(
      (r) => `<span class="hist-agent-at"><span class="hist-swatch ${r.cls}"></span>${escHtml(r.label)} <b>${escHtml(_histFmtSecs(r.secs))}</b></span>`
    ).join("")}</div>`;
  }
  function _histMetricsChipsHtml(s, stats) {
    const hasRuns = !!(stats && stats.has_runs);
    const wall = hasRuns && stats.wall_seconds != null ? stats.wall_seconds : s.duration;
    const tokens = hasRuns && stats.total_tokens != null ? stats.total_tokens : s.tokens;
    const chips = [];
    if (wall != null)
      chips.push(`<span class="hist-metric-chip">wall ${_histFmtSecs(wall)}</span>`);
    if (hasRuns) {
      chips.push(
        `<span class="hist-metric-chip">agent time ${_histFmtSecs(stats.agent_total_seconds)}</span>`
      );
    }
    if (tokens != null) {
      chips.push(`<span class="hist-metric-chip">tokens ${_histFmtTokens(tokens)}</span>`);
    }
    if (hasRuns && stats.fix_round_count > 0) {
      const refs = (stats.fix_round_tickets || []).map((n) => "#" + n).join(", ");
      chips.push(
        `<span class="hist-metric-chip hist-metric-chip--warn">${stats.fix_round_count} fix round` + (refs ? ` (${escHtml(refs)})` : "") + `</span>`
      );
    }
    if (hasRuns && stats.slowest_ticket) {
      chips.push(
        `<span class="hist-metric-chip">slowest #${stats.slowest_ticket.ticket} \xB7 ${_histFmtSecs(stats.slowest_ticket.seconds)}</span>`
      );
    }
    if (hasRuns && stats.parallel_saved_seconds != null) {
      chips.push(
        `<span class="hist-metric-chip">parallel saved ~${_histFmtSecs(stats.parallel_saved_seconds)}</span>`
      );
    }
    return chips.length ? `<div class="hist-metric-chips">${chips.join("")}</div>` : "";
  }
  function _histTimelineRowsHtml(s, stats) {
    const tickets = _histMergeGanttTickets(s, stats);
    if (!tickets.length)
      return "";
    const scale = Math.max(1, stats.wall_seconds || 0);
    const rows = tickets.map((t) => {
      const dur = Math.max(0, (t.end || 0) - (t.start || 0));
      const fixN = (t.segments || []).filter((seg) => seg.fix_round).length;
      let durLabel = _histFmtSecs(dur || t.segments?.reduce((n, seg) => n + (seg.duration || 0), 0));
      if (fixN)
        durLabel += " \xB7 fix";
      const segs = (t.segments || []).map((seg) => {
        const left = seg.start / scale * 100;
        const width = Math.max(0.5, seg.duration / scale * 100);
        const agentCls = seg.agent === "tester" ? "hist-tl-tester" : "hist-tl-coder";
        const cls = "hist-tl-seg " + agentCls;
        const title = `${seg.agent}${seg.fix_round ? " (fix round)" : ""} \xB7 ${_histFmtSecs(seg.duration)}`;
        return `<span class="${cls}" style="left:${left}%;width:${width}%" title="${escHtml(title)}"></span>`;
      }).join("");
      return `<div class="hist-tl-row">
      <span class="hist-tl-num">#${escHtml(String(t.ticket))}</span>
      <div class="hist-tl-track">${segs}</div>
      <span class="hist-tl-dur">${escHtml(durLabel)}</span>
    </div>`;
    }).join("");
    return `<div class="hist-tl-section"><div class="hist-sec-label">Timeline</div>${rows}</div>`;
  }
  function _histFixCountForIssue(issueNum, stats) {
    if (!stats || !stats.tickets)
      return 0;
    const hit = stats.tickets.find((t) => String(t.ticket) === String(issueNum));
    if (!hit)
      return 0;
    return (hit.segments || []).filter((seg) => seg.fix_round).length;
  }
  function _histDoneIssueRowHtml(iss, s, stats, titleMap) {
    const num = iss.ticket_id;
    const id = num != null ? "#" + num : "#?";
    const titleText = _histIssueTitle(iss, s, titleMap);
    const repo = _histRepo(s);
    const chip = _histIssueChip(iss, { binary: _histSprintShowsBinaryIssues(s) });
    const crashed = chip.cls === "crashed";
    const clickable = num != null && repo ? ` role="link" tabindex="0" onclick="event.stopPropagation();window.open('https://github.com/${escHtml(repo)}/issues/${escHtml(String(num))}','_blank','noopener')"` : "";
    const icon = _histIssueIcon(iss, s);
    let dur = _histFmtSecs(iss.time_spent);
    const fixN = _histFixCountForIssue(num, stats);
    if (fixN)
      dur += ` \xB7 ${fixN} fix`;
    const reason = crashed && iss.failure_reason ? `<span class="hist-irow-reason">${escHtml(String(iss.failure_reason))}</span>` : "";
    const logHtml = crashed && num != null ? `<a class="hist-irow-log" href="${escHtml(_histIssueLogUrl(s, num))}" onclick="event.stopPropagation()" title="View issue log">Log \u2192</a>` : "";
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
  function _histDoneIssuesHtml(s, group) {
    const titleMap = group ? _histBuildLineageTitleMap(group) : /* @__PURE__ */ new Map();
    const issues = group ? _histIssuesForDisplay(s, group) : Array.isArray(s.issues) ? s.issues : [];
    if (!issues.length)
      return "";
    const stats = _histRunStats[s.label];
    return `<div class="hist-issue-rows">${issues.map((i) => _histDoneIssueRowHtml(i, s, stats, titleMap)).join("")}</div>`;
  }
  function _histCardShowsDoneSummary(s, group) {
    const issues = group ? _histIssuesForDisplay(s, group) : Array.isArray(s.issues) ? s.issues : [];
    if (_histSprintFailed(s))
      return issues.length > 0;
    if (issues.length)
      return true;
    const state = (s.lifecycle_state || "").toLowerCase();
    return state === "ready_to_merge" || state === "completed" || state === "running";
  }
  function _histCardOutcomeHtml(s, group) {
    const issues = group ? _histIssuesForDisplay(s, group) : Array.isArray(s.issues) ? s.issues : [];
    if (!_histCardShowsDoneSummary(s, group) && !issues.length)
      return "";
    return `${_histChildMetricsHtml(s)}${_histDoneIssuesHtml(s, group)}`;
  }
  var _histAgentTimeExpanded = /* @__PURE__ */ new Set();
  var _histMetricsDetailsExpanded = /* @__PURE__ */ new Set();
  function _histChildMetricsHtml(s) {
    const stats = _histRunStats[s.label];
    const metricsOpen = _histAgentTimeExpanded.has(s.label);
    const lbl = escHtml(s.label || "");
    const chev = metricsOpen ? "ti-chevron-down" : "ti-chevron-right";
    const barHtml = stats && stats.has_runs ? _histAgentTimeBarHtml(stats) : "";
    const elapsed = _histMetricsElapsedLabel(s, stats);
    const elapsedHtml = elapsed ? `<span class="hist-metrics-elapsed">${elapsed}</span>` : "";
    const body = metricsOpen ? `<div class="hist-metrics-body">
        ${_histAgentBreakdownHtml(stats)}
        ${_histMetricsChipsHtml(s, stats)}
        ${_histTimelineRowsHtml(s, stats)}
        ${_histPostSprintHtml(s)}
        ${_histReconcileHtml(s)}
      </div>` : "";
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
    if (!sub)
      return "";
    const display = sprintLabelDisplay(base).replace("Sprint ", "");
    return `\u2190 from ${display}`;
  }
  function _histChildCardHtml(s, group, opts) {
    opts = opts || {};
    const isLineageParent = !!opts.isLineageParent;
    const expanded = _histExpanded.has(s.label);
    const lbl = escHtml(s.label || "");
    const state = (s.lifecycle_state || "").toLowerCase();
    const displayState = state === "needs_rework" && s.end_reason && (String(s.end_reason).toLowerCase() === "natural" || String(s.end_reason).toLowerCase() === "merge_sprint") && !_histSprintFailed(s) ? "ready_to_merge" : state;
    const cls = ["hist-child-card"];
    if (isLineageParent)
      cls.push("hist-lineage-parent");
    if (displayState === "ready_to_merge")
      cls.push("ready");
    if (displayState === "completed")
      cls.push("settled");
    if (expanded)
      cls.push("expanded");
    const display = sprintLabelDisplay(s.label);
    const fromLine = !isLineageParent && _histIsChild(s.label) ? _histParentFromLabel(s.label) : "";
    const chev = expanded ? "ti-chevron-down" : "ti-chevron-right";
    const recoveryBtn = _histRecoveryBtnHtml(s);
    const deleteBtn = _histDeleteBtnHtml(s);
    const secondaryLinks = _histSecondaryLinksHtml(s);
    const bulkBtn = opts.bulkCompleteBtn || "";
    const headRight = `<span class="hist-child-head-right">${secondaryLinks}${recoveryBtn}${deleteBtn}${bulkBtn}</span>`;
    if (expanded && !(s.label in _histRunStats))
      _histLoadRunStats(s.label);
    const body = expanded ? `<div class="hist-child-body">
        ${isLineageParent ? _histPartialChildrenHtml(s) : ""}
        ${_histLooseEndBandHtml(s)}
        ${_histWhatListHtml(s, group)}
        ${_histCardOutcomeHtml(s, group)}
      </div>` : "";
    return `<div class="${cls.join(" ")}" data-label="${lbl}">
    <div class="hist-child-head" onclick="_histToggleCard('${lbl}')">
      <div class="hist-child-head-left">
        <i class="ti ${chev} hist-chev"></i>
        <span class="hist-child-title">${escHtml(display)}` + (fromLine ? ` <span class="hist-child-from">${escHtml(fromLine)}</span>` : "") + `</span>
        ${_histStateChip(s.lifecycle_state, s)}
        ${_histHeadStatsHtml(s, group)}
      </div>
      ${headRight}
    </div>
    ${body}
  </div>`;
  }
  function _histLooseEndBandHtml(s) {
    const r = s.reconciliation;
    if (r && Array.isArray(r.checks)) {
      const prCheck = r.checks.find((c) => !c.ok && c.name === "sprint_pr");
      if (prCheck) {
        const looseN = r.checks.filter((c) => !c.ok).length || 1;
        const prNum = s.pr_number;
        const prUrl = _histPrUrl(s);
        const prRef = prNum ? `#${prNum}` : "PR";
        const msg = `${looseN} loose end \u2014 Sprint PR ${prRef} is not yet merged`;
        const cta = prUrl ? `<a class="hist-band-cta" href="${escHtml(prUrl)}" target="_blank" rel="noopener"
            onclick="event.stopPropagation()">Merge PR</a>` : "";
        return `<div class="hist-loose-end-band">
        <i class="ti ti-alert-triangle"></i>
        <span class="hist-band-msg">${escHtml(msg)}</span>
        ${cta}
      </div>`;
      }
      const staleCheck = r.checks.find((c) => !c.ok && c.name === "stale_labels");
      if (staleCheck) {
        const offenders = Array.isArray(staleCheck.tickets) ? staleCheck.tickets : [];
        const looseN = r.checks.filter((c) => !c.ok).length || 1;
        const offenderCount = offenders.length;
        const labelParts = offenderCount ? offenders.map((o) => "#" + o.issue + " " + (o.labels || []).join(", ")).join(" \xB7 ") : staleCheck.detail || "Clear stale status labels";
        const msg = offenderCount ? looseN + " loose end \u2014 " + offenderCount + " stale status labels to clear: " + labelParts : looseN + " loose end \u2014 " + labelParts;
        const lbl = escHtml(s.label || "");
        return `<div class="hist-loose-end-band">
        <i class="ti ti-alert-triangle"></i>
        <span class="hist-band-msg">${escHtml(msg)}</span>
        <button type="button" class="hist-band-cta hist-band-cta--clear"
          onclick="event.stopPropagation();_histClearStaleLabels('${lbl}')">Clear labels</button>
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
  function _histWhatListHtml(s, group) {
    if (_histIsLocked(s.lifecycle_state))
      return "";
    const state = (s.lifecycle_state || "").toLowerCase();
    const displayIssues = group ? _histIssuesForDisplay(s, group) : Array.isArray(s.issues) ? s.issues : [];
    if (_histSprintFailed(s)) {
      const failed = Array.isArray(s.failed_tickets) ? s.failed_tickets : [];
      const issues = displayIssues;
      const sprintReason = s.failure_reason || s.end_reason;
      if (!failed.length && !sprintReason)
        return "";
      const n = failed.length || 1;
      if (issues.length) {
        return `<div class="hist-what-list">
        <div class="hist-what-head hist-what-head--failed">Why it failed \xB7 ${n} issue${n !== 1 ? "s" : ""} crashed</div>
      </div>`;
      }
      const items = failed.map((ft) => {
        const id = ft.ticket_id != null ? "#" + ft.ticket_id : "#?";
        const parts = _histFailReasonParts(ft, s);
        const titleHtml = parts.title ? `<span class="wl-title">${escHtml(parts.title)}</span>` : "";
        const accentHtml = parts.accent ? `<span class="wl-reason-accent">${escHtml(parts.accent)}</span>` : "";
        const timeHtml = parts.time != null ? `<span class="wl-time">${escHtml(_histFmtSecs(parts.time))}</span>` : "";
        const logHref = escHtml(_histIssueLogUrl(s, ft.ticket_id));
        return `<div class="wl-item">
        <span class="wl-icon fail"><i class="ti ti-x"></i></span>
        <span class="wl-main">
          <span class="wl-id">${escHtml(String(id))}</span>
          ${titleHtml}${accentHtml}
        </span>
        ${timeHtml}
        <a class="wl-log" href="${logHref}" onclick="event.stopPropagation()" title="View issue log">Log \u2192</a>
      </div>`;
      }).join("");
      const summary = !failed.length && sprintReason ? `<div class="wl-item"><span class="wl-reason-accent">${escHtml(String(sprintReason))}</span></div>` : "";
      return `<div class="hist-what-list">
      <div class="hist-what-head hist-what-head--failed">Why it failed \xB7 ${n} issue${n !== 1 ? "s" : ""} crashed</div>
      ${items}${summary}
    </div>`;
    }
    if (state === "partial_finished" || state === "needs_rework") {
      const unfinished = displayIssues.filter(
        (i) => (i.state || "").toLowerCase() !== "merged"
      );
      if (!unfinished.length)
        return "";
      const n = unfinished.length;
      const m = displayIssues.length;
      return `<div class="hist-what-list">
      <div class="hist-what-head">Unfinished ${n} of ${m}</div>
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
  function _histDetailsHtml(s) {
    const expanded = _histMetricsDetailsExpanded.has(s.label);
    const lbl = escHtml(s.label || "");
    const chev = expanded ? "ti-chevron-down" : "ti-chevron-right";
    const body = expanded ? `<div class="hist-details-body">
      ${_histStatsHtml(s)}
      ${_histReconPassedHtml(s)}
    </div>` : "";
    return `<div class="hist-details${expanded ? " expanded" : ""}">
    <div class="hist-details-head" onclick="event.stopPropagation();_histToggleMetrics('${lbl}')">
      <i class="ti ti-chart-bar hist-details-icon" aria-hidden="true"></i>
      <span class="hist-details-label">Metrics &amp; reconciliation</span>
      <i class="ti ${chev} hist-chev hist-details-chev" aria-hidden="true"></i>
    </div>
    ${body}
  </div>`;
  }
  function _histToggleAgentTime(label) {
    if (_histAgentTimeExpanded.has(label)) {
      _histAgentTimeExpanded.delete(label);
    } else {
      _histAgentTimeExpanded.add(label);
      _histLoadRunStats(label);
    }
    _histRenderLedger(_histLedgerData);
  }
  function _histToggleMetrics(label) {
    if (_histMetricsDetailsExpanded.has(label)) {
      _histMetricsDetailsExpanded.delete(label);
    } else {
      _histMetricsDetailsExpanded.add(label);
      _histLoadRunStats(label);
    }
    _histRenderLedger(_histLedgerData);
  }
  function _histRecoveryBtnHtml(s) {
    if (_histIsLocked(s.lifecycle_state))
      return "";
    const state = (s.lifecycle_state || "").toLowerCase();
    const lbl = escHtml(s.label || "");
    const rawLabel = s.label || "";
    const reconcileBtn = `<button type="button" class="hist-head-btn hist-head-btn--reconcile"
      onclick="event.stopPropagation();smgmtReconcileSprint('${lbl}')"
      title="Reconcile this sprint's DB state against GitHub truth">
      <i class="ti ti-git-compare"></i> Reconcile</button>`;
    if (_histSprintFailed(s) || state === "needs_rework" || state === "failed" || state === "cancelled") {
      const rerunDisabled = _smgmtAnySprintRunning ? "disabled" : "";
      const rerunTitle = _smgmtAnySprintRunning ? 'title="Cannot re-run: another sprint is currently running."' : "";
      const childDisplay = sprintLabelDisplay(_histNextChildLabel(rawLabel)).replace("Sprint ", "");
      return `${reconcileBtn}<button type="button" class="hist-head-btn hist-head-btn--rerun hist-head-btn--rerun-primary" ${rerunDisabled} ${rerunTitle}
      onclick="event.stopPropagation();_histRerunSprint('${lbl}')">
      <i class="ti ti-refresh"></i> Re-run \u2192 ${escHtml(childDisplay)}</button>`;
    }
    if (state === "ready_to_merge") {
      return `${reconcileBtn}<button type="button" class="hist-head-btn hist-head-btn--bulk"
      onclick="event.stopPropagation();smgmtFinishSprint('${lbl}')"
      title="Complete sprint \u2014 merge to develop">
      <i class="ti ti-circle-check"></i> Complete</button>`;
    }
    if (state === "draft" && s.parent) {
      const runDisabled = _smgmtAnySprintRunning ? "disabled" : "";
      const runTitle = _smgmtAnySprintRunning ? 'title="Cannot run: another sprint is currently running."' : "";
      return `${reconcileBtn}<button type="button" class="hist-head-btn hist-head-btn--rerun hist-head-btn--rerun-primary" ${runDisabled} ${runTitle}
      onclick="event.stopPropagation();smgmtRunSprint('${lbl}')">
      <i class="ti ti-player-play"></i> Run</button>`;
    }
    if (state === "running")
      return "";
    return reconcileBtn;
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
    const lifecycle = (s.lifecycle_state || "").toLowerCase();
    if (lifecycle === "completed")
      cls.push("settled");
    if (lifecycle === "ready_to_merge")
      cls.push("ready");
    const display = typeof sprintLabelDisplay === "function" ? sprintLabelDisplay(s.label) : s.label || "";
    const chev = expanded ? "ti-chevron-down" : "ti-chevron-right";
    const lbl = escHtml(s.label || "");
    const headStatsHtml = _histHeadStatsHtml(s);
    const recoveryBtn = _histRecoveryBtnHtml(s);
    const bulkBtn = opts.bulkCompleteBtn || "";
    const deleteBtn = _histDeleteBtnHtml(s);
    const secondaryLinks = _histSecondaryLinksHtml(s);
    const headRight = secondaryLinks || deleteBtn || bulkBtn || recoveryBtn ? `<span class="hist-card-head-right">${secondaryLinks}${recoveryBtn}${deleteBtn}${bulkBtn}</span>` : "";
    if (expanded && !(s.label in _histRunStats))
      _histLoadRunStats(s.label);
    const body = expanded ? `<div class="hist-card-body">
      ${_histLooseEndBandHtml(s)}
      ${_histWhatListHtml(s)}
      ${_histCardOutcomeHtml(s, null)}
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
        ${headStatsHtml}
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
  function _histToggleGroup(baseLabel) {
    _histToggleCard(baseLabel);
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
    const _UNSETTLED = /* @__PURE__ */ new Set(["needs_rework", "failed", "cancelled", "ready_to_merge", "running"]);
    const _groupUnsettled = (g) => [g.baseSprint, ...g.children || []].filter(Boolean).some((s) => _UNSETTLED.has((s.lifecycle_state || "").toLowerCase()));
    groupOrder.sort((a, b) => {
      const ua = _groupUnsettled(byBase.get(a)) ? 0 : 1;
      const ub = _groupUnsettled(byBase.get(b)) ? 0 : 1;
      if (ua !== ub)
        return ua - ub;
      return byBase.get(a).order - byBase.get(b).order;
    });
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
  function _histChildRunFinished(s) {
    const st = (s.lifecycle_state || "").toLowerCase();
    return [
      "completed",
      "deleted",
      "ready_to_merge",
      "needs_rework",
      "failed",
      "cancelled",
      "partial_finished"
    ].includes(st);
  }
  function _histChildSprintsAllCompleted(group) {
    const children = group.children || [];
    if (!children.length)
      return false;
    return children.every((s, i) => {
      if (_histChildRunFinished(s))
        return true;
      return children.slice(i + 1).some((later) => _histChildRunFinished(later));
    });
  }
  function _histChildSprintsStillRunning(group) {
    const active = /* @__PURE__ */ new Set(["running", "draft", "planned"]);
    return (group.children || []).some(
      (s) => active.has((s.lifecycle_state || "").toLowerCase())
    );
  }
  function _histBulkCompleteBtnHtml(group) {
    if (!group.children?.length || !group.baseSprint)
      return "";
    if (!_histGroupNeedsBulkComplete(group))
      return "";
    const lbl = escHtml(group.baseLabel || "");
    if (_histChildSprintsStillRunning(group)) {
      return `<button type="button" class="hist-head-btn hist-head-btn--bulk" disabled
            title="Wait for child sprint runs to finish before bulk completing">
      <i class="ti ti-circle-check"></i> Bulk complete
    </button>`;
    }
    if (!_histChildSprintsAllCompleted(group)) {
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
  function _histGroupHasActionable(group) {
    return _histGroupMembers(group).some((s) => {
      const st = (s && s.lifecycle_state || "").toLowerCase();
      return _HIST_INBOX_STATES.has(st);
    });
  }
  function _histSynthParent(baseLabel, group) {
    const children = group.children || [];
    return {
      label: baseLabel,
      lifecycle_state: "partial_finished",
      partial_children: children.map((c) => c.label).filter(Boolean),
      issues: []
    };
  }
  function _histGroupHtml(group) {
    const bulkBtn = _histBulkCompleteBtnHtml(group);
    const children = group.children || [];
    if (children.length) {
      const baseLbl = group.baseLabel || group.baseSprint && group.baseSprint.label || "";
      const groupCls = "hist-sprint-group" + (_histExpanded.has(baseLbl) ? "" : " collapsed");
      const parentSprint = group.baseSprint || _histSynthParent(baseLbl, group);
      const parentCard = _histChildCardHtml(parentSprint, group, {
        isLineageParent: true,
        bulkCompleteBtn: bulkBtn
      });
      const childHtml = children.map((c) => _histChildCardHtml(c, group)).join("");
      return `<div class="${groupCls}" data-group="${escHtml(baseLbl)}">${parentCard}<div class="hist-child-wrap">${childHtml}</div></div>`;
    }
    if (group.baseSprint) {
      return _histIsChild(group.baseSprint.label) ? _histChildCardHtml(group.baseSprint, group) : _histCardHtml(group.baseSprint, { bulkCompleteBtn: bulkBtn });
    }
    return "";
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
    const note = _histShowClosed ? `<span class="hist-toolbar-note"><i class="ti ti-history"></i> Full archive \u2014 older sprint groups collapse into numbered folds; click to expand.</span>` : `<span class="hist-toolbar-note"><i class="ti ti-inbox"></i> Action inbox \u2014 sprints needing Complete, Re-run, or Bulk complete. Lineage groups stay together (e.g. Sprint 98 with 98.1).</span>`;
    const signOffBtn = _histShowClosed ? "" : `<button type="button" class="btn-ghost hist-bulk-signoff-btn" id="hist-bulk-signoff-btn" onclick="_histBulkSignOff()" title="Complete every ready-to-merge sprint in this inbox"><i class="ti ti-circle-check"></i> Sign off all ready</button>`;
    return `<div class="hist-toolbar">${note}${signOffBtn}</div>`;
  }
  async function _histScanStale2() {
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
  async function _histClearStaleLabels(label) {
    const repo = _cachedFullRepo[_slug];
    if (!repo)
      return;
    const s = (_histLedgerData || []).find((x) => x.label === label);
    if (!s || !s.reconciliation)
      return;
    const staleCheck = (s.reconciliation.checks || []).find((c) => !c.ok && c.name === "stale_labels");
    if (!staleCheck)
      return;
    const tickets = Array.isArray(staleCheck.tickets) ? staleCheck.tickets : [];
    try {
      const resp = await fetch("/api/sprints/" + encodeURIComponent(label) + "/clear-stale-labels", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: repo, tickets })
      });
      if (resp.ok) {
        await _histLoadLedger2();
      }
    } catch (_) {
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
      const emptyMsg = _histShowClosed ? "No sprint history yet \u2014 finished and deleted sprints appear here." : "Inbox clear \u2014 no sprints need action. Toggle Show completed for the archive.";
      el.innerHTML = `<div class="hist-ledger-empty">${emptyMsg}</div>`;
      return;
    }
    let groups = _histGroupSprints(sprints);
    if (!_histShowClosed) {
      groups = groups.filter(_histGroupHasActionable);
      if (!groups.length) {
        el.innerHTML = `<div class="hist-ledger-empty">Inbox clear \u2014 no sprints need action. Toggle Show completed for the archive.</div>`;
        return;
      }
    }
    if (!_histDidAutoExpand && groups.length) {
      _histAutoExpandRecent(groups);
      _histDidAutoExpand = true;
    }
    let bodyHtml;
    if (!_histShowClosed) {
      bodyHtml = groups.map(_histGroupHtml).join("");
    } else {
      const { recent, folds } = _histPartitionGroups(groups, _histFoldSize);
      bodyHtml = recent.map(_histGroupHtml).join("") + folds.map(_histFoldHtml).join("");
    }
    el.innerHTML = _histLegendHtml() + _histToolbarHtml() + bodyHtml;
  }
  function _histRerunSprint(label) {
    if (typeof globalThis.smgmtRerunSprint === "function") {
      return globalThis.smgmtRerunSprint(label);
    }
  }
  function _histPrefetchLedger(repo) {
    if (!repo)
      return;
    const hasData = (_histLedgerData || []).length > 0;
    const fresh = repo === _histLedgerCacheRepo && Date.now() - _histLedgerCacheAt < _HIST_LEDGER_TTL_MS && hasData;
    if (fresh || _histLedgerInflight)
      return;
    _histLoadLedger2(repo, { background: true });
  }
  async function _histLoadLedger2(repo, opts) {
    opts = opts || {};
    if (!repo)
      return;
    const el = document.getElementById("hist-ledger");
    const force = opts.force === true;
    const background = opts.background === true;
    const hasCache = repo === _histLedgerCacheRepo && (_histLedgerData || []).length > 0;
    const fresh = !force && hasCache && Date.now() - _histLedgerCacheAt < _HIST_LEDGER_TTL_MS;
    if (fresh) {
      if (!background && el && !el.querySelector(".hist-card, .hist-sprint-group, .hist-fold")) {
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
    if (!hasCache && !background)
      _histShowLedgerSkeleton();
    else if (hasCache && !background)
      _histRenderLedger(_histLedgerData);
    const loadPromise = (async () => {
      try {
        const settingsUrl = `/api/projects/${encodeURIComponent(_slug)}/settings`;
        const activeParam = _histShowClosed ? "" : "&active_only=1";
        const historyUrl = "/api/sprints/history?limit=50" + activeParam + "&project=" + encodeURIComponent(repo || "");
        const [sresp, resp] = await Promise.all([
          fetch(settingsUrl).catch(() => null),
          fetch(historyUrl)
        ]);
        if (sresp && sresp.ok) {
          try {
            const settings = await sresp.json();
            const fs = parseInt(settings.history_fold_size, 10);
            if (!isNaN(fs) && fs > 0)
              _histFoldSize = fs;
            const ttlMin = parseFloat(settings.history_cache_ttl_min);
            if (!isNaN(ttlMin) && ttlMin > 0)
              _HIST_LEDGER_TTL_MS = ttlMin * 6e4;
          } catch (_) {
          }
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
        _histSeedRunStatsFromInline(sprints);
        _histLedgerCacheRepo = repo;
        _histLedgerCacheAt = Date.now();
        const histOpen = document.getElementById("smgmt-subview-history")?.classList.contains("show");
        if (!background || histOpen) {
          _histRenderLedger(sprints);
          _smgmtUpdateSubnav();
        }
      } catch (_) {
        if (!hasCache && el && !background) {
          el.innerHTML = `<div class="hist-ledger-empty">Could not load sprint history.</div>`;
        }
      } finally {
        if (_histLedgerInflight === loadPromise)
          _histLedgerInflight = null;
      }
    })();
    _histLedgerInflight = loadPromise;
    await loadPromise;
  }
  function _histSyncShowClosedBtn() {
    const btn = document.getElementById("hist-show-closed-btn");
    if (!btn)
      return;
    btn.innerHTML = _histShowClosed ? '<i class="ti ti-eye-off"></i> Active only' : '<i class="ti ti-history"></i> Show completed';
    btn.title = _histShowClosed ? "Show only the action inbox (sprints needing you)" : "Load the full closed-sprint archive";
  }
  function _histToggleShowClosed() {
    _histShowClosed = !_histShowClosed;
    _histSyncShowClosedBtn();
    const repo = _cachedFullRepo[_slug];
    if (repo)
      _histLoadLedger2(repo, { force: true });
  }
  function _histSetTtlMin(min) {
    const m = parseFloat(min);
    if (!isNaN(m) && m > 0)
      _HIST_LEDGER_TTL_MS = m * 6e4;
  }
  function _histForceRefresh() {
    _histResetLedgerCache();
    for (const k of Object.keys(_histRunStats))
      delete _histRunStats[k];
    _histStaleBySprint = {};
    const repo = _cachedFullRepo[_slug];
    if (repo)
      _histLoadLedger2(repo, { force: true });
  }
  function _histNextChildLabel(parentLabel) {
    return _nextSprintSublabel(parentLabel);
  }
  function _histBulkSignOffTargets(sprints) {
    const groups = _histGroupSprints(sprints || []).filter(_histGroupHasActionable);
    const targets = [];
    const skipLabels = /* @__PURE__ */ new Set();
    for (const g of groups) {
      const members = _histGroupMembers(g);
      const rtm = members.filter(
        (s) => (s.lifecycle_state || "").toLowerCase() === "ready_to_merge"
      );
      if (!rtm.length)
        continue;
      const useBulk = (g.children || []).length && g.baseLabel && _histGroupNeedsBulkComplete(g) && _histChildSprintsAllCompleted(g) && !_histChildSprintsStillRunning(g);
      if (useBulk) {
        targets.push({ kind: "bulk", label: g.baseLabel });
        for (const s of members)
          skipLabels.add(s.label);
        continue;
      }
      for (const s of rtm) {
        if (!skipLabels.has(s.label)) {
          targets.push({ kind: "finish", label: s.label });
        }
      }
    }
    return targets.sort((a, b) => {
      const pa = _histLabelParts(a.label);
      const pb = _histLabelParts(b.label);
      if (pa.baseNum !== pb.baseNum)
        return pa.baseNum - pb.baseNum;
      return pa.sub - pb.sub;
    });
  }
  async function _histBulkSignOff() {
    if (_histShowClosed) {
      alert("Switch to the action inbox first (toggle off Show completed).");
      return;
    }
    const targets = _histBulkSignOffTargets(_histLedgerData);
    if (!targets.length) {
      alert("No ready-to-merge sprints in the inbox.");
      return;
    }
    const listing = targets.map((t) => {
      const disp = sprintLabelDisplay(t.label);
      return t.kind === "bulk" ? `${disp} (bulk complete lineage)` : disp;
    }).join("\n");
    if (!confirm(`Sign off ${targets.length} sprint(s)? Each will run Complete (merge + close UAT).

${listing}`)) {
      return;
    }
    if (typeof finishSprintAndWait !== "function") {
      alert("Finish helper unavailable \u2014 refresh the page.");
      return;
    }
    const total = targets.length;
    if (typeof _smgmtBoardLock === "function") {
      _smgmtBoardLock("Signing off ready sprints\u2026", {
        progress: true,
        total,
        clearLog: true,
        showDone: true
      });
    }
    let done = 0;
    let failed = null;
    for (const target of targets) {
      const { label, kind } = target;
      const action = kind === "bulk" ? "Bulk completing" : "Completing";
      if (typeof _smgmtBoardLog === "function") {
        _smgmtBoardLog(`${action} ${sprintLabelDisplay(label)}\u2026`, "step");
      }
      try {
        if (kind === "bulk") {
          if (typeof bulkCompleteLineageAndWait !== "function") {
            throw new Error("Bulk complete helper unavailable \u2014 refresh the page.");
          }
          await bulkCompleteLineageAndWait(label);
        } else {
          await finishSprintAndWait(label);
        }
        done += 1;
        if (typeof _smgmtBoardProgress === "function")
          _smgmtBoardProgress(done, total);
        if (typeof _smgmtBoardLog === "function") {
          _smgmtBoardLog(`\u2713 ${sprintLabelDisplay(label)} completed`, "ok");
        }
      } catch (e) {
        failed = { label, message: e.message || String(e) };
        if (typeof _smgmtBoardLog === "function") {
          _smgmtBoardLog(`\u2717 ${sprintLabelDisplay(label)}: ${failed.message}`, "err");
        }
        break;
      }
    }
    const finishMsg = failed ? `Stopped at ${sprintLabelDisplay(failed.label)}: ${failed.message}` : `Signed off ${done} sprint(s).`;
    if (typeof _smgmtBoardFinish === "function") {
      _smgmtBoardFinish({
        ok: !failed,
        message: finishMsg,
        onDone: () => {
          _histResetLedgerCache();
          const repo = _cachedFullRepo[_slug];
          if (repo)
            _histLoadLedger2(repo, { force: true });
          else
            _histForceRefresh();
          if (typeof loadSprintMgmt === "function")
            loadSprintMgmt(true).catch(() => {
            });
        }
      });
    } else {
      alert(finishMsg);
      _histForceRefresh();
    }
  }

  // apps/dashboard/static/src/sprint-board/rerun-modal.js
  function _rrShowPreviewLoading(current) {
    const loading = document.getElementById("rr-loading");
    if (!loading)
      return;
    loading.innerHTML = renderProgressActivity({
      status: "running",
      mode: "indeterminate",
      current: current || "Loading preview\u2026"
    }, {
      id: "rr-preview-pa",
      hideLog: true
    });
    loading.classList.remove("hidden");
  }
  function _rrShowCreateProgress(done, total, current, status, error) {
    const loading = document.getElementById("rr-loading");
    if (!loading)
      return;
    loading.innerHTML = renderProgressActivity({
      status: status || "running",
      mode: "bar",
      done: done || 0,
      total: total || 3,
      current: current || "",
      error: error || "",
      result: status === "done" ? "Sub-sprint created" : ""
    }, {
      id: "rr-create-pa",
      hideLog: true
    });
    loading.classList.remove("hidden");
  }
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
    _rrShowPreviewLoading("Loading preview\u2026");
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
    _rrShowCreateProgress(0, 3, "Creating sprint\u2026", "running", "");
    document.getElementById("rr-content").classList.add("hidden");
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
      _rrShowCreateProgress(1, 3, "Applying local updates\u2026", "running", "");
      if (typeof _smgmtApplyRerunOptimistic === "function") {
        _smgmtApplyRerunOptimistic(parentLabel, subLabel, ticketNumbers);
      }
      loadSprintMgmt(true).catch(() => {
      });
      if (typeof globalThis._histLoadLedger === "function") {
        globalThis._histLoadLedger(repo).catch(() => {
        });
      }
      _rrShowCreateProgress(2, 3, "Queueing sprint run\u2026", "running", "");
      const subDisplay = subLabel ? sprintLabelDisplay(subLabel) : "Sub-sprint";
      if (data.errors && data.errors.length > 0) {
        _smgmtShowToast(`${subDisplay} created with label errors \u2014 check GitHub.`);
      } else {
        _smgmtShowToast(`${subDisplay} ready \u2014 confirm run`);
      }
      if (subLabel && typeof smgmtRunSprint === "function") {
        _rrShowCreateProgress(3, 3, "Done", "done", "");
        smgmtRunSprint(subLabel);
      }
      _rrClose();
    } catch (e) {
      _rrShowCreateProgress(0, 3, "", "error", e.message || "Failed to create re-run sprint");
      const errEl = document.getElementById("rr-error");
      errEl.textContent = "Failed to re-run sprint: " + e.message;
      errEl.classList.remove("hidden");
      document.getElementById("rr-loading").classList.add("hidden");
      document.getElementById("rr-content").classList.remove("hidden");
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
  function _fsRenderPreviewLoading(current) {
    const loading = document.getElementById("fs-loading");
    if (!loading)
      return;
    loading.innerHTML = renderProgressActivity({
      status: "running",
      mode: "indeterminate",
      current: current || "Loading preview\u2026"
    }, {
      id: "fs-preview-pa",
      hideLog: true
    });
    loading.classList.remove("hidden");
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
    const patched = patchProgressActivityInPlace("fs-pa", snap, {
      retryFn: "_fsRetry"
    });
    if (!patched) {
      slot.innerHTML = renderProgressActivity(snap, {
        id: "fs-pa",
        retryFn: "_fsRetry"
      });
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
  function finishSprintAndWait2(label) {
    return new Promise(async (resolve, reject) => {
      const repo = _smgmtRepo();
      if (!repo) {
        reject(new Error("No project loaded"));
        return;
      }
      const parts = repo.split("/");
      const owner = parts[0];
      const repoName = parts.slice(1).join("/");
      try {
        const prevRes = await fetch(
          `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/finish-preview`
        );
        if (!prevRes.ok) {
          const err = await prevRes.json().catch(() => ({}));
          throw new Error(err.detail || `Preview failed (HTTP ${prevRes.status})`);
        }
        const preview = await prevRes.json();
        if (preview.conflict_error)
          throw new Error(preview.conflict_error);
        const allTickets = preview.all_tickets || [];
        const bgParams = {
          confirmed: true,
          move_non_uat_to: preview.next_sprint_label || "",
          selected_ticket_numbers: allTickets.map((t) => t.number),
          selected_tickets: allTickets.map((t) => ({
            number: t.number,
            title: t.title || `#${t.number}`
          })),
          merge_pr: !!preview.sprint_pr,
          sprint_pr_url: preview.sprint_pr ? preview.sprint_pr.url : null,
          total: allTickets.length + 2
        };
        const res = await fetch(
          `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/finish-bg`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(bgParams)
          }
        );
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || `HTTP ${res.status}`);
        }
        const url = `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/finish-stream`;
        const es = new EventSource(url);
        es.onmessage = (e) => {
          let snap;
          try {
            snap = JSON.parse(e.data);
          } catch {
            return;
          }
          if (snap.ping)
            return;
          if (snap.status === "done") {
            es.close();
            resolve(snap);
          } else if (snap.status === "error") {
            es.close();
            reject(new Error(snap.error || "Finish failed"));
          }
        };
        es.onerror = () => {
          es.close();
          reject(new Error("Finish stream disconnected"));
        };
      } catch (e) {
        reject(e);
      }
    });
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
    _fsRenderPreviewLoading("Loading preview\u2026");
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
        `<div class="fs-action-row"><i class="ti ti-circle-check"></i> Close all ${allTickets.length} sprint ticket${allTickets.length !== 1 ? "s" : ""}</div>`
      );
      actionsEl.innerHTML = actionRows.join("");
      const reworkTickets = preview.rework_tickets || [];
      const warningEl = document.getElementById("fs-rework-warning");
      const warningTextEl = document.getElementById("fs-rework-warning-text");
      const reworkCheckbox = document.getElementById("fs-confirm-rework-checkbox");
      if (reworkTickets.length > 0) {
        warningTextEl.textContent = `${reworkTickets.length} ticket${reworkTickets.length !== 1 ? "s" : ""} will be closed unfinished: ` + reworkTickets.map((t) => `#${t.number}`).join(", ");
        warningEl.classList.remove("hidden");
        if (reworkCheckbox)
          reworkCheckbox.checked = false;
      } else {
        warningEl.classList.add("hidden");
      }
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
    const reworkTickets = _fsPreview.rework_tickets || [];
    const reworkCheckbox = document.getElementById("fs-confirm-rework-checkbox");
    if (reworkTickets.length > 0 && !(reworkCheckbox && reworkCheckbox.checked)) {
      const errEl = document.getElementById("fs-error");
      errEl.textContent = "Check the box to confirm closing unfinished tickets, or cancel and re-run them first.";
      errEl.classList.remove("hidden");
      return;
    }
    const allTickets = _fsPreview.all_tickets || [];
    const selectedTickets = allTickets.map((t) => ({
      number: t.number,
      title: t.title || `#${t.number}`
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
      total: selectedNums.length + 2,
      confirm_rework: reworkTickets.length > 0 && !!(reworkCheckbox && reworkCheckbox.checked)
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
        const detail = err.detail;
        const msg = detail && typeof detail === "object" ? detail.message : detail;
        throw new Error(msg || `HTTP ${res.status}`);
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
  function _bcShowPreviewLoading(current) {
    const loading = document.getElementById("bc-loading");
    if (!loading)
      return;
    loading.innerHTML = renderProgressActivity({
      status: "running",
      mode: "indeterminate",
      current: current || "Loading preview\u2026"
    }, {
      id: "bc-preview-pa",
      hideLog: true
    });
    loading.classList.remove("hidden");
  }
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
    _bcShowPreviewLoading("Loading preview\u2026");
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
      const preview = await _bcFetchPreview(owner, repoName, label);
      _bcPreview = preview;
      if (preview.conflict_error) {
        throw new Error(preview.conflict_error);
      }
      const listEl = document.getElementById("bc-ticket-list");
      const allTickets = preview.all_tickets || [];
      const groups = preview.tickets_by_sprint && preview.tickets_by_sprint.length ? preview.tickets_by_sprint : allTickets.length ? [{ label: preview.base_label, tickets: allTickets }] : [];
      const _ticketRow = (t) => {
        const catClass = _bcCatClass(t.category);
        const catLabel = t.category === "sprint-summary" ? "SUMMARY" : t.category.toUpperCase();
        return `<label class="rr-ticket-row">
          <input type="checkbox" checked data-issue="${t.number}" onchange="">
          <span class="rr-ticket-num">#${t.number}</span>
          <span class="rr-ticket-title" title="${escHtml(t.title)}">${escHtml(t.title)}</span>
          <span class="rr-ticket-cat ${catClass}">${escHtml(catLabel)}</span>
        </label>`;
      };
      if (groups.length === 0) {
        listEl.innerHTML = '<div style="padding:10px;color:var(--text-muted);font-size:13px">No open tickets in this sprint lineage.</div>';
      } else {
        listEl.innerHTML = groups.map((g) => `<div style="font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.04em;margin:8px 0 2px">${escHtml(sprintLabelDisplay(g.label))} \xB7 ${g.tickets.length}</div>` + g.tickets.map(_ticketRow).join("")).join("");
      }
      const members = preview.members || [];
      const actionsEl = document.getElementById("bc-actions");
      const actionRows = members.map((m) => {
        const target = m.is_base ? "develop" : sprintLabelDisplay(m.parent);
        let icon, color, note;
        if (m.completed) {
          icon = "ti-circle-check-filled";
          color = "var(--green)";
          note = "completed";
        } else if (m.merged) {
          icon = "ti-git-merge";
          color = "var(--green)";
          note = "merged \u2192 will mark completed";
        } else {
          icon = "ti-circle";
          color = "var(--text-sub)";
          note = "pending";
        }
        return `<div class="fs-action-row"><i class="ti ${icon}" style="color:${color}"></i> ${escHtml(sprintLabelDisplay(m.label))} \u2192 ${escHtml(target)} <span style="color:var(--text-muted);font-size:11px">\xB7 ${note}</span></div>`;
      });
      const memberCount = members.length || (preview.member_labels || []).length;
      actionRows.push(
        `<div class="fs-action-row"><i class="ti ti-circle-check"></i> Close ${allTickets.length} ticket${allTickets.length !== 1 ? "s" : ""} (grouped above; UAT + summary)</div>`,
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
  async function _bcFetchPreview(owner, repoName, label) {
    const res = await fetch(
      `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/bulk-complete-preview`
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      const detail = err.detail || `HTTP ${res.status}`;
      if (res.status === 409 && /merge conflict/i.test(detail)) {
        throw new Error(`Merge conflict \u2014 bulk complete stopped: ${detail}`);
      }
      throw new Error(detail);
    }
    return res.json();
  }
  async function bulkCompleteLineageAndWait2(label) {
    const repo = _smgmtRepo();
    if (!repo)
      throw new Error("No project loaded");
    const parts = repo.split("/");
    const owner = parts[0];
    const repoName = parts.slice(1).join("/");
    const preview = await _bcFetchPreview(owner, repoName, label);
    const order = (preview.complete_order || []).slice();
    if (!order.length)
      throw new Error("Nothing to bulk complete");
    for (const sLabel of order) {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(sLabel)}/complete-step`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirmed: true })
        }
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Failed completing ${sLabel} (HTTP ${res.status})`);
      }
    }
    return { label, steps: order.length };
  }
  async function _bcConfirm() {
    const repo = _smgmtRepo();
    if (!_bcLabel || !repo || !_bcPreview)
      return;
    const parts = repo.split("/");
    const owner = parts[0];
    const repoName = parts.slice(1).join("/");
    const label = _bcLabel;
    const order = (_bcPreview.complete_order || []).slice();
    const confirmBtn = document.getElementById("bc-confirm-btn");
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Completing\u2026";
    }
    _bcClose();
    if (order.length === 0) {
      _smgmtShowToast("Nothing to complete.");
      return;
    }
    let doneSteps = 0;
    const totalSteps = order.length + 1;
    let failedIdx = 0;
    _smgmtBoardLock(`Completing ${sprintLabelDisplay(label)}\u2026`, {
      progress: true,
      total: totalSteps,
      clearLog: true,
      showDone: true
      // Done button stays disabled until the run settles
    });
    _smgmtBoardLog("Starting per-step complete (deepest child first)\u2026", "step");
    const _onDone = () => {
      if (typeof globalThis._histResetLedgerCache === "function")
        globalThis._histResetLedgerCache();
      loadSprintMgmt().catch(() => {
      });
    };
    try {
      for (let i = 0; i < order.length; i++) {
        failedIdx = i;
        const sLabel = order[i];
        _smgmtBoardLog(`Completing ${sprintLabelDisplay(sLabel)}\u2026`, "step");
        const res = await fetch(
          `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(sLabel)}/complete-step`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confirmed: true })
          }
        );
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || `Failed completing ${sLabel} (HTTP ${res.status})`);
        }
        const sd = await res.json();
        doneSteps += 1;
        _smgmtBoardProgress(doneSteps, totalSteps);
        const into = sd.merged ? ` \u2192 merged into ${sd.merged_into}` : "";
        _smgmtBoardLog(`\u2713 ${sprintLabelDisplay(sLabel)} completed${into}`, "ok");
      }
      _smgmtBoardLog("Refreshing board\u2026", "step");
      await loadSprintMgmt();
      doneSteps += 1;
      _smgmtBoardProgress(doneSteps, totalSteps);
      _smgmtBoardLog("\u2713 Complete finished", "ok");
      _smgmtBoardFinish({
        ok: true,
        message: `\u2713 ${sprintLabelDisplay(label)} completed \u2014 ${order.length} sprint(s) settled.`,
        onDone: _onDone
      });
    } catch (e) {
      _smgmtBoardLog(`\u2717 ${e.message}`, "err");
      const isConflict = /merge conflict/i.test(e.message);
      _smgmtBoardFinish({
        ok: false,
        message: "Stopped: " + e.message + (isConflict ? '\n\nClick "Resolve with AI" to fix automatically, or resolve manually and re-run.' : "\n\nResolve the conflict, then re-run Bulk complete to resume (done steps are skipped)."),
        onDone: _onDone
      });
      if (isConflict) {
        const cinfo = _bcParseConflictInfo(e.message);
        if (cinfo) {
          setTimeout(() => {
            _bcInjectResolveButton(cinfo, owner, repoName, label, order, failedIdx, doneSteps, totalSteps, _onDone);
          }, 0);
        }
      }
    }
  }
  function _bcParseConflictInfo(msg) {
    const m = msg.match(/Merge\s+(sprint-[\d.]+)\s*[→>]\s*(sprint-[\d.]+|develop|master)\s+failed/i);
    if (!m)
      return null;
    const baseRaw = m[2];
    const base = /^(develop|master)$/i.test(baseRaw) ? baseRaw : `sprint/${baseRaw}`;
    return { head: `sprint/${m[1]}`, base };
  }
  function _bcInjectResolveButton(cinfo, owner, repoName, label, order, fromIdx, doneSteps, totalSteps, onDone) {
    const doneEl = document.getElementById("smgmt-op-done");
    if (!doneEl)
      return;
    const existing = document.getElementById("smgmt-op-resolve-ai-btn");
    if (existing)
      existing.remove();
    const btn = document.createElement("button");
    btn.id = "smgmt-op-resolve-ai-btn";
    btn.type = "button";
    btn.className = "btn-primary";
    btn.textContent = "\u2726 Resolve with AI";
    btn.style.cssText = "margin-right:8px;background:var(--violet,#6e56cf);border-color:var(--violet,#6e56cf)";
    btn.onclick = () => {
      btn.disabled = true;
      _bcLaunchAIResolve(cinfo, owner, repoName, label, order, fromIdx, doneSteps, totalSteps, onDone);
    };
    const doneBtn = document.getElementById("smgmt-op-done-btn");
    if (doneBtn)
      doneEl.insertBefore(btn, doneBtn);
    else
      doneEl.prepend(btn);
  }
  async function _bcLaunchAIResolve(cinfo, owner, repoName, label, order, fromIdx, doneSteps, totalSteps, onDone) {
    const spinner = document.getElementById("smgmt-move-spinner");
    const msgEl = document.getElementById("smgmt-move-overlay-msg");
    const errEl = document.getElementById("smgmt-op-error");
    const doneEl = document.getElementById("smgmt-op-done");
    const overlay = document.getElementById("smgmt-move-overlay");
    if (spinner)
      spinner.style.display = "";
    if (errEl) {
      errEl.hidden = true;
      errEl.textContent = "";
    }
    if (doneEl) {
      doneEl.hidden = true;
      doneEl.innerHTML = "";
    }
    if (overlay)
      overlay.setAttribute("aria-busy", "true");
    const startMs = Date.now();
    const timerInterval = setInterval(() => {
      const secs = Math.floor((Date.now() - startMs) / 1e3);
      const mins = Math.floor(secs / 60);
      const ts = mins > 0 ? `${mins}m ${secs % 60}s` : `${secs}s`;
      if (msgEl)
        msgEl.textContent = `Resolving conflicts with AI\u2026 (${ts})`;
    }, 1e3);
    if (msgEl)
      msgEl.textContent = "Resolving conflicts with AI\u2026 (0s)";
    try {
      const startRes = await fetch(
        `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/resolve-branch-conflict`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ head: cinfo.head, base: cinfo.base })
        }
      );
      if (!startRes.ok) {
        const err = await startRes.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${startRes.status}`);
      }
      const { job_key } = await startRes.json();
      _smgmtBoardLog("AI resolver started\u2026", "step");
      await new Promise((resolve, reject) => {
        const es = new EventSource(
          `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/resolve-conflict-stream/${encodeURIComponent(job_key)}`
        );
        es.onmessage = (ev) => {
          try {
            const snap = JSON.parse(ev.data);
            if (snap.ping)
              return;
            if (snap.current && snap.status === "running")
              _smgmtBoardLog(snap.current, "step");
            if (snap.status === "done") {
              es.close();
              resolve(snap);
            } else if (snap.status === "error") {
              es.close();
              reject(new Error(snap.error || "Resolve failed"));
            }
          } catch (_) {
          }
        };
        es.onerror = () => {
          es.close();
          reject(new Error("SSE connection lost"));
        };
      });
      clearInterval(timerInterval);
      if (msgEl)
        msgEl.textContent = "\u2713 Resolved \u2014 retrying bulk complete\u2026";
      _smgmtBoardLog("\u2713 Conflicts resolved \u2014 resuming\u2026", "ok");
      if (spinner)
        spinner.style.display = "";
      await _bcResumeFrom(owner, repoName, label, order, fromIdx, doneSteps, totalSteps, onDone);
    } catch (resolveErr) {
      clearInterval(timerInterval);
      _smgmtBoardLog(`\u2717 AI resolve failed: ${resolveErr.message}`, "err");
      _smgmtBoardFinish({
        ok: false,
        message: `AI resolution failed: ${resolveErr.message}

Resolve manually and re-run Bulk complete.`,
        onDone
      });
    }
  }
  async function _bcResumeFrom(owner, repoName, label, order, fromIdx, doneSteps, totalSteps, onDone) {
    try {
      for (let i = fromIdx; i < order.length; i++) {
        const sLabel = order[i];
        _smgmtBoardLog(`Completing ${sprintLabelDisplay(sLabel)}\u2026`, "step");
        const res = await fetch(
          `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(sLabel)}/complete-step`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confirmed: true })
          }
        );
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || `Failed completing ${sLabel} (HTTP ${res.status})`);
        }
        const sd = await res.json();
        doneSteps += 1;
        _smgmtBoardProgress(doneSteps, totalSteps);
        const into = sd.merged ? ` \u2192 merged into ${sd.merged_into}` : "";
        _smgmtBoardLog(`\u2713 ${sprintLabelDisplay(sLabel)} completed${into}`, "ok");
      }
      _smgmtBoardLog("Refreshing board\u2026", "step");
      await loadSprintMgmt();
      doneSteps += 1;
      _smgmtBoardProgress(doneSteps, totalSteps);
      _smgmtBoardLog("\u2713 Complete finished", "ok");
      _smgmtBoardFinish({
        ok: true,
        message: `\u2713 ${sprintLabelDisplay(label)} completed \u2014 ${order.length} sprint(s) settled.`,
        onDone
      });
    } catch (e2) {
      _smgmtBoardLog(`\u2717 ${e2.message}`, "err");
      _smgmtBoardFinish({
        ok: false,
        message: "Stopped: " + e2.message + "\n\nResolve the conflict, then re-run Bulk complete.",
        onDone
      });
    }
  }

  // apps/dashboard/static/src/sprint-board/reconcile-modal.js
  var _recLabel = null;
  function _recEsc(s) {
    return typeof escHtml === "function" ? escHtml(String(s ?? "")) : String(s ?? "");
  }
  function _recDisplay(label) {
    return typeof sprintLabelDisplay === "function" ? sprintLabelDisplay(label) : label;
  }
  function _recRemove() {
    const bd = document.getElementById("rec-backdrop");
    if (bd)
      bd.remove();
    _recLabel = null;
  }
  function _recClose() {
    _recRemove();
  }
  function _recCheckRow(c) {
    const ok = !!c.ok;
    const icon = ok ? "ti-circle-check" : "ti-alert-triangle";
    const color = ok ? "var(--green,#1a7f37)" : "var(--amber,#9a6700)";
    const name = {
      summary_issue: "Summary issue",
      sprint_pr: "Sprint PR",
      stale_labels: "Stale labels"
    }[c.name] || c.name;
    return `<div style="display:flex;gap:8px;align-items:flex-start;padding:4px 0;font-size:13px">
      <i class="ti ${icon}" style="color:${color};margin-top:2px"></i>
      <span><b>${_recEsc(name)}</b> \u2014 ${_recEsc(c.detail || (ok ? "ok" : "needs attention"))}</span>
    </div>`;
  }
  function _recRender(preview) {
    const body = document.getElementById("rec-body");
    const applyBtn = document.getElementById("rec-apply-btn");
    if (!body)
      return;
    if (!preview || preview.exists === false) {
      body.innerHTML = `<div style="font-size:13px;color:var(--text-muted)">
      This sprint has no lifecycle row in this dashboard's DB${preview && preview.wrong_project ? " for this project" : ""}, so Reconcile cannot change lifecycle here.
      ${preview && preview.exists === false ? '<div style="margin-top:8px">If git branches are already merged, use <b>Bulk complete</b> on the lineage parent \u2014 that seeds the DB row and marks each step completed.</div>' : ""}
    </div>`;
      if (applyBtn)
        applyBtn.classList.add("hidden");
      return;
    }
    const dbState = preview.db_state || "unknown";
    const ghState = preview.github_state || "unknown";
    const wouldChange = !!preview.would_change;
    const checks = preview.checks || [];
    const allClear = preview.all_clear;
    const stateBlock = wouldChange ? `<div style="font-size:13px;margin-bottom:10px">
         <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
           <span>DB: <b style="color:var(--amber,#9a6700)">${_recEsc(dbState)}</b></span>
           <i class="ti ti-arrow-right" aria-hidden="true"></i>
           <span>GitHub truth: <b style="color:var(--green,#1a7f37)">${_recEsc(ghState)}</b></span>
         </div>
         <div style="color:var(--text-muted);margin-top:4px">Apply will set the DB lifecycle to <b>${_recEsc(ghState)}</b>${preview.reason ? ` (${_recEsc(preview.reason)})` : ""}.</div>
       </div>` : `<div style="font-size:13px;margin-bottom:10px;color:var(--text-muted)">
         Lifecycle already matches GitHub (<b>${_recEsc(dbState)}</b>). ${allClear === false ? "Some post-sprint checks below are unresolved." : "Nothing to change."}
       </div>`;
    const checksBlock = checks.length ? `<div style="border-top:1px solid var(--border,#d0d7de);padding-top:8px;margin-top:6px">
         <div style="font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px">Post-sprint checks</div>
         ${checks.map(_recCheckRow).join("")}
       </div>` : "";
    body.innerHTML = stateBlock + checksBlock;
    if (applyBtn) {
      const actionable = wouldChange || allClear === false;
      applyBtn.classList.remove("hidden");
      applyBtn.disabled = !actionable;
      applyBtn.textContent = actionable ? "Apply" : "Nothing to apply";
    }
  }
  async function smgmtReconcileSprint(label) {
    const repo = typeof _smgmtRepo === "function" ? _smgmtRepo() : null;
    if (!repo)
      return;
    _recLabel = label;
    _recRemove();
    const bd = document.createElement("div");
    bd.id = "rec-backdrop";
    bd.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:1000;display:flex;align-items:center;justify-content:center";
    bd.innerHTML = `
    <div id="rec-modal" role="dialog" aria-modal="true"
         style="background:var(--card,#fff);color:var(--text,#1f2328);width:min(520px,92vw);max-height:80vh;overflow:auto;border-radius:10px;box-shadow:0 12px 40px rgba(0,0,0,.25);padding:18px 20px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
        <h3 style="margin:0;font-size:15px">Reconcile ${_recEsc(_recDisplay(label))} against GitHub</h3>
        <button type="button" onclick="_recClose()" aria-label="Close"
                style="background:none;border:none;font-size:18px;cursor:pointer;color:var(--text-muted)">&times;</button>
      </div>
      <div id="rec-body" style="min-height:48px">
        <div style="display:flex;gap:8px;align-items:center;color:var(--text-muted);font-size:13px">
          <span class="nt-spinner" aria-hidden="true"></span> Checking GitHub (via mirror)\u2026
        </div>
      </div>
      <div id="rec-error" class="hidden" style="color:var(--red,#cf222e);font-size:13px;margin-top:8px"></div>
      <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px">
        <button type="button" class="rm-btn" onclick="_recClose()">Close</button>
        <button type="button" id="rec-apply-btn" class="rm-btn rm-btn-prim hidden" disabled
                onclick="_recApply()">Apply</button>
      </div>
    </div>`;
    bd.addEventListener("click", (e) => {
      if (e.target === bd)
        _recClose();
    });
    document.body.appendChild(bd);
    try {
      const res = await fetch(
        `/api/sprints/${encodeURIComponent(label)}/reconcile-preview?project=${encodeURIComponent(repo)}`
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const preview = await res.json();
      if (_recLabel !== label)
        return;
      _recRender(preview);
    } catch (e) {
      const errEl = document.getElementById("rec-error");
      if (errEl) {
        errEl.textContent = "Failed to load preview: " + e.message;
        errEl.classList.remove("hidden");
      }
      const bodyEl = document.getElementById("rec-body");
      if (bodyEl)
        bodyEl.innerHTML = "";
    }
  }
  async function _recApply() {
    const repo = typeof _smgmtRepo === "function" ? _smgmtRepo() : null;
    const label = _recLabel;
    if (!repo || !label)
      return;
    const applyBtn = document.getElementById("rec-apply-btn");
    if (applyBtn) {
      applyBtn.disabled = true;
      applyBtn.textContent = "Applying\u2026";
    }
    try {
      const res = await fetch(`/api/sprints/${encodeURIComponent(label)}/reconcile`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: repo, confirmed: true })
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const result = await res.json();
      _recClose();
      const msg = result.updated ? `Reconciled ${_recDisplay(label)}: ${result.db_state_before} \u2192 ${result.db_state_after}` : `${_recDisplay(label)} already matches GitHub.`;
      if (typeof _smgmtShowToast === "function")
        _smgmtShowToast(msg);
      if (typeof globalThis._histResetLedgerCache === "function")
        globalThis._histResetLedgerCache();
      if (typeof loadSprintMgmt === "function")
        loadSprintMgmt().catch(() => {
        });
      if (typeof globalThis._histForceRefresh === "function") {
        try {
          globalThis._histForceRefresh();
        } catch (_) {
        }
      }
    } catch (e) {
      const errEl = document.getElementById("rec-error");
      if (errEl) {
        errEl.textContent = "Apply failed: " + e.message;
        errEl.classList.remove("hidden");
      }
      if (applyBtn) {
        applyBtn.disabled = false;
        applyBtn.textContent = "Apply";
      }
    }
  }

  // apps/dashboard/static/src/sprint-board/board-render.js
  var _smgmtResolvedAncestors = /* @__PURE__ */ new Set();
  var _smgmtAggregateCards = null;
  function _smgmtBuildAggCards(agg) {
    const sections = agg.sections || {};
    const all = [
      ...sections.running || [],
      ...sections.needs_rework || [],
      ...sections.ready_to_merge || [],
      ...sections.draft || [],
      ...sections.lineage || []
    ];
    const idx = {};
    for (const card of all) {
      if (card.label)
        idx[card.label] = card;
      for (const cl of card.chain || []) {
        if (!idx[cl])
          idx[cl] = card;
      }
    }
    return idx;
  }
  function _smgmtAggToRenderData(agg) {
    const sections = agg.sections || {};
    const allCards = [
      ...sections.running || [],
      ...sections.needs_rework || [],
      ...sections.ready_to_merge || [],
      ...sections.draft || [],
      ...sections.lineage || []
    ];
    const _ranStates = /* @__PURE__ */ new Set([
      "running",
      "needs_rework",
      "ready_to_merge",
      "partial_finished",
      "failed",
      "completed"
    ]);
    const issues = [];
    const sprintLabels = /* @__PURE__ */ new Set();
    const sprint_has_run = {};
    const sprint_parents = {};
    const aggregateBuckets = {};
    const sectionEntries = [
      ["running", sections.running || []],
      ["needs_rework", sections.needs_rework || []],
      ["ready_to_merge", sections.ready_to_merge || []],
      ["draft", sections.draft || []],
      ["lineage", sections.lineage || []]
    ];
    for (const [sectionName, cards] of sectionEntries) {
      for (const card of cards) {
        const label = card.label;
        if (!label)
          continue;
        sprintLabels.add(label);
        aggregateBuckets[label] = sectionName;
        sprint_has_run[label] = _ranStates.has(card.lifecycle_state);
        for (const t of card.tickets || []) {
          issues.push({ ...t, sprint_label: label });
        }
        const chain = card.chain || [];
        for (let i = 0; i < chain.length; i++) {
          const cl = chain[i];
          sprintLabels.add(cl);
          if (!(cl in aggregateBuckets))
            aggregateBuckets[cl] = "lineage";
          if (!(cl in sprint_has_run))
            sprint_has_run[cl] = sprint_has_run[label];
        }
        for (let i = 1; i < chain.length; i++) {
          if (!sprint_parents[chain[i]])
            sprint_parents[chain[i]] = chain[i - 1];
        }
      }
    }
    for (const t of (sections.backlog || {}).tickets || []) {
      issues.push({ ...t, sprint_label: null });
    }
    const order = [...sprintLabels].sort((a, b) => {
      const ma = String(a).match(/^sprint-(\d+)(?:\.(\d+))?$/);
      const mb = String(b).match(/^sprint-(\d+)(?:\.(\d+))?$/);
      if (!ma || !mb)
        return String(a).localeCompare(String(b));
      const na = parseInt(ma[1], 10);
      const nb = parseInt(mb[1], 10);
      if (na !== nb)
        return na - nb;
      return parseInt(ma[2] || 0, 10) - parseInt(mb[2] || 0, 10);
    });
    const sprintNumSet = /* @__PURE__ */ new Set();
    for (const l of order) {
      const m = String(l).match(/^sprint-(\d+)/);
      if (m)
        sprintNumSet.add(parseInt(m[1], 10));
    }
    const sprints = [...sprintNumSet].sort((a, b) => a - b);
    const finished_sprints = allCards.filter((c) => c.lifecycle_state === "completed" && c.label).map((c) => c.label);
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
      _aggregateBuckets: aggregateBuckets
    };
  }
  function _smgmtSignoffState(label) {
    if (typeof globalThis !== "undefined" && globalThis._commanderFeatures && globalThis._commanderFeatures.signoff !== true) {
      return null;
    }
    return (_smgmtData && _smgmtData.sprint_signoff || {})[label] || null;
  }
  function _smgmtSignoffBadgeHtml(label) {
    if (_smgmtSignoffState(label) !== "pending")
      return "";
    return '<span class="sc-signoff-badge">Pending sign-off</span>';
  }
  function _smgmtSignoffActionsHtml(label) {
    if (_smgmtSignoffState(label) !== "pending")
      return "";
    const e = escHtml(label);
    return `<button class="smgmt-approve-btn" type="button" onclick="smgmtApproveSprint('${e}')"><i class="ti ti-check"></i> Approve</button><button class="smgmt-reject-btn" type="button" onclick="smgmtRejectSprint('${e}')"><i class="ti ti-x"></i> Reject</button>`;
  }
  function _smgmtGoalRequired() {
    const f = typeof globalThis !== "undefined" && globalThis._commanderFeatures;
    if (!f)
      return false;
    return f.goal_required === true;
  }
  function _smgmtDorMode() {
    const f = typeof globalThis !== "undefined" && globalThis._commanderFeatures;
    if (!f)
      return "off";
    const m = f.definition_of_ready_mode;
    return m === "block" || m === "warn" || m === "off" ? m : "off";
  }
  function _smgmtReadinessCheck(ticket) {
    if (!ticket)
      return { ready: false, reasons: ["invalid ticket"] };
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
  function _smgmtDorNotReadyTickets(tickets) {
    const result = [];
    for (const t of tickets || []) {
      const { ready, reasons } = _smgmtReadinessCheck(t);
      if (!ready)
        result.push({ number: t.number, title: t.title || "", reasons });
    }
    return result;
  }
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
        _smgmtEnsureCapData();
      }
      const _feats = typeof globalThis !== "undefined" ? globalThis._commanderFeatures : null;
      const _useBoardAggregate = Boolean(_feats && _feats.board_aggregate === true);
      let data;
      if (_useBoardAggregate) {
        _smgmtAggregateCards = null;
        const aggResp = await fetch(
          "/api/board?project=" + encodeURIComponent(repo)
        );
        if (!aggResp.ok) {
          let msg = "Failed to load board.";
          const d = await aggResp.json().catch(() => null);
          const detail = d && typeof d.detail === "string" ? d.detail : "";
          if (aggResp.status === 429 || /rate limit/i.test(detail)) {
            msg = detail || "GitHub API rate limit reached \u2014 retry shortly.";
          }
          throw new Error(msg);
        }
        const agg = await aggResp.json();
        _smgmtAggregateCards = _smgmtBuildAggCards(agg);
        if (typeof window !== "undefined")
          window._smgmtAggregateCards = _smgmtAggregateCards;
        if (_smgmtLiveCacheRepo !== repo) {
          _smgmtLiveCacheRepo = repo;
          for (const k of Object.keys(_smgmtLiveCache))
            delete _smgmtLiveCache[k];
        }
        if (typeof _smgmtLingerRestore === "function")
          _smgmtLingerRestore(repo);
        const prevRunningAgg = new Set(_smgmtRunningLabels);
        _smgmtRunningLabels = /* @__PURE__ */ new Set();
        _smgmtAnySprintRunning = false;
        for (const card of (agg.sections || {}).running || []) {
          if (card.label)
            _smgmtRunningLabels.add(card.label);
        }
        _smgmtAnySprintRunning = _smgmtRunningLabels.size > 0;
        for (const label of prevRunningAgg) {
          if (!_smgmtRunningLabels.has(label) && typeof _smgmtLingerStart === "function") {
            _smgmtLingerStart(label);
          }
        }
        if (optimisticRunningLabel) {
          _smgmtRunningLabels.add(optimisticRunningLabel);
          _smgmtAnySprintRunning = true;
        }
        data = _smgmtAggToRenderData(agg);
      } else {
        _smgmtAggregateCards = null;
        if (typeof window !== "undefined")
          window._smgmtAggregateCards = null;
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
        data = await resp.json();
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
      }
      _smgmtRender(data);
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
  function _smgmtSprintBaseLabel(label) {
    const m = String(label).match(/^sprint-(\d+)(?:\.(\d+))?$/);
    return m ? `sprint-${m[1]}` : String(label);
  }
  function _smgmtSprintSubIndex(label) {
    const m = String(label).match(/^sprint-(\d+)(?:\.(\d+))?$/);
    return m && m[2] ? parseInt(m[2], 10) : 0;
  }
  function _smgmtChildrenForParent(parentLabel, parents, order) {
    const fromMeta = (order || []).filter((l) => (parents || {})[l] === parentLabel);
    const fromLabel = _smgmtSprintSubIndex(parentLabel) === 0 ? (order || []).filter(
      (l) => l !== parentLabel && _smgmtSprintBaseLabel(l) === parentLabel && _smgmtSprintSubIndex(l) > 0
    ) : [];
    return [.../* @__PURE__ */ new Set([...fromMeta, ...fromLabel])];
  }
  function _smgmtCompareSprintLabels(a, b) {
    const ka = _smgmtSprintLabelSortKey(a);
    const kb = _smgmtSprintLabelSortKey(b);
    for (let i = 0; i < Math.max(ka.length, kb.length); i++) {
      const d = (ka[i] ?? -1) - (kb[i] ?? -1);
      if (d !== 0)
        return d;
    }
    return 0;
  }
  function _smgmtChildSprintLabel(parentLabel, parents, rerunInto, order) {
    if (rerunInto && rerunInto[parentLabel])
      return rerunInto[parentLabel];
    const children = _smgmtChildrenForParent(parentLabel, parents, order);
    if (!children.length)
      return null;
    return [...children].sort(_smgmtCompareSprintLabels)[children.length - 1];
  }
  function _smgmtLatestLineageLabel(baseLabel, parents, rerunInto, order) {
    const base = _smgmtSprintBaseLabel(baseLabel);
    const members = (order || []).filter(
      (l) => l === base || _smgmtSprintBaseLabel(l) === base && _smgmtSprintSubIndex(l) > 0
    );
    if (!members.length)
      return null;
    return [...members].sort(_smgmtCompareSprintLabels)[members.length - 1];
  }
  function _smgmtShouldCollapseParent(parentLabel, parents, rerunInto, order) {
    return Boolean(_smgmtChildSprintLabel(parentLabel, parents, rerunInto, order));
  }
  function _smgmtShouldCollapseToLineage(label, parents, rerunInto, order) {
    if (_smgmtShouldCollapseParent(label, parents, rerunInto, order))
      return true;
    const base = _smgmtSprintBaseLabel(label);
    const latest = _smgmtLatestLineageLabel(base, parents, rerunInto, order);
    if (!latest || label === latest)
      return false;
    return _smgmtCompareSprintLabels(label, latest) < 0;
  }
  function _smgmtRender(data) {
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
    const _mergedSet = new Set(data.merged_sprints || []);
    const orderedLabelsRaw = order.length > 0 ? order.filter((l) => /^sprint-\d+(\.\d+)*$/.test(l)) : [...sprints].sort((a, b) => a - b).map((n) => `sprint-${n}`);
    const _sprintParents = data.sprint_parents || {};
    const _rerunInto = data.sprint_rerun_into || {};
    _smgmtResolvedAncestors = /* @__PURE__ */ new Set();
    const orderedLabels = orderedLabelsRaw.filter((label) => {
      if (_smgmtShouldCollapseToLineage(label, _sprintParents, _rerunInto, orderedLabelsRaw)) {
        const latest = _smgmtLatestLineageLabel(
          _smgmtSprintBaseLabel(label),
          _sprintParents,
          _rerunInto,
          orderedLabelsRaw
        );
        if (latest && _finishedSet.has(latest))
          return false;
        _smgmtResolvedAncestors.add(label);
        return true;
      }
      if (_mergedSet.has(label))
        return false;
      const tickets = bySprint[label] || [];
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
    const focusGuideEl = document.getElementById("smgmt-focus-guide");
    if (focusGuideEl) {
      focusGuideEl.innerHTML = _smgmtFocusGuideHtml(data, orderedLabels, bySprint);
    }
    const _planStates = data.sprint_plan_states || {};
    const _buildCard = (label) => {
      const tickets = bySprint[label] || [];
      if (_smgmtResolvedAncestors.has(label)) {
        let childLabel = _smgmtChildSprintLabel(
          label,
          _sprintParents,
          _rerunInto,
          orderedLabelsRaw
        );
        if (!childLabel) {
          const latest = _smgmtLatestLineageLabel(
            _smgmtSprintBaseLabel(label),
            _sprintParents,
            _rerunInto,
            orderedLabelsRaw
          );
          if (latest && latest !== label)
            childLabel = latest;
        }
        const cachedOutcome = _smgmtOutcomeCache[label];
        return `<div class="smgmt-sprint-unit" id="smgmt-unit-${escHtml(label)}">` + _smgmtAncestorRowHtml(label, cachedOutcome, childLabel) + `</div>`;
      }
      if (_smgmtIsFreshRerunSprint(label))
        delete _smgmtOutcomeCache[label];
      const outcome = _smgmtRunningLabels.has(label) ? null : _smgmtOutcomeCache[label] || null;
      const parent = _sprintParents[label] || null;
      const cardHtml = _smgmtCardHtml(
        label,
        null,
        tickets,
        outcome,
        false,
        parent,
        _smgmtFinishedLabels.has(label)
      );
      return `<div class="smgmt-sprint-unit" id="smgmt-unit-${escHtml(label)}">` + cardHtml + `</div>`;
    };
    const lineageLabels = orderedLabels.filter((l) => _smgmtResolvedAncestors.has(l));
    const otherLabels = orderedLabels.filter((l) => !_smgmtResolvedAncestors.has(l));
    const mergeLabels = [];
    const reworkLabels = [];
    const runningLabels = [];
    const draftLabels = [];
    for (const lbl of otherLabels) {
      const bucket = _smgmtCardBucket(lbl, _planStates);
      if (bucket === "ready_to_merge")
        mergeLabels.push(lbl);
      else if (bucket === "needs_rework")
        reworkLabels.push(lbl);
      else if (bucket === "running")
        runningLabels.push(lbl);
      else
        draftLabels.push(lbl);
    }
    const sectionLabel = (text, cls) => `<div class="smgmt-section-label ${cls}">${text}</div>`;
    const lineageRangeLabel = (labels) => {
      if (!labels.length)
        return "Lineage";
      const first = sprintLabelDisplay(labels[0]).replace("Sprint ", "");
      const last = sprintLabelDisplay(labels[labels.length - 1]).replace("Sprint ", "");
      return first === last ? `Lineage ${first}` : `Lineage ${first} \u2192 ${last}`;
    };
    let cards = "";
    if (lineageLabels.length > 0) {
      cards += sectionLabel(lineageRangeLabel(lineageLabels), "smgmt-section-lineage");
      cards += `<div class="smgmt-board-section smgmt-board-section--lineage">`;
      cards += lineageLabels.map(_buildCard).join("");
      cards += `</div>`;
    }
    if (mergeLabels.length > 0) {
      const mergeLabel = mergeLabels.length === 1 ? "Ready to merge \u2014 1" : `Ready to merge \u2014 ${mergeLabels.length}`;
      cards += sectionLabel(mergeLabel, "smgmt-section-merge");
      cards += `<div class="smgmt-board-section smgmt-board-section--merge">`;
      cards += mergeLabels.map(_buildCard).join("");
      cards += `</div>`;
    }
    if (reworkLabels.length > 0) {
      const reworkLabel = reworkLabels.length === 1 ? "Needs rework \u2014 1" : `Needs rework \u2014 ${reworkLabels.length}`;
      cards += sectionLabel(reworkLabel, "smgmt-section-rework");
      cards += `<div class="smgmt-board-section smgmt-board-section--rework">`;
      cards += reworkLabels.map(_buildCard).join("");
      cards += `</div>`;
    }
    if (runningLabels.length > 0) {
      const runLabel = runningLabels.length === 1 ? "Running \u2014 1" : `Running \u2014 ${runningLabels.length}`;
      cards += sectionLabel(runLabel, "smgmt-section-running");
      cards += `<div class="smgmt-board-section smgmt-board-section--running">`;
      cards += runningLabels.map(_buildCard).join("");
      cards += `</div>`;
    }
    if (draftLabels.length > 0) {
      const draftLabel = draftLabels.length === 1 ? "Draft \u2014 1" : `Draft \u2014 ${draftLabels.length}`;
      cards += sectionLabel(draftLabel, "smgmt-section-draft");
      cards += `<div class="smgmt-board-section smgmt-board-section--draft">`;
      cards += draftLabels.map(_buildCard).join("");
      cards += `</div>`;
    }
    listEl.innerHTML = cards || '<div class="loading-msg">No sprints found.</div>';
    _smgmtInitCapacityGauges(orderedLabels);
    _smgmtRenderAllCapBars();
    _smgmtEnsureCapData(false);
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
    if (typeof _smgmtMiniRailRestoreCached === "function") {
      _smgmtMiniRailRestoreCached(orderedLabels, bySprint);
    }
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
  function _smgmtCardBucket(label, planStates) {
    const aggBuckets = _smgmtData && _smgmtData._aggregateBuckets;
    if (aggBuckets && label in aggBuckets) {
      if (_smgmtRunningLabels.has(label))
        return "running";
      const inLingerAgg = typeof _smgmtIsLinger === "function" && _smgmtIsLinger(label);
      if (inLingerAgg && !(_smgmtData.sprint_has_run || {})[label])
        return "running";
      const b = aggBuckets[label];
      if (b === "running" || b === "needs_rework" || b === "ready_to_merge" || b === "draft") {
        return b;
      }
    }
    if (_smgmtRunningLabels.has(label))
      return "running";
    const inLinger = typeof _smgmtIsLinger === "function" && _smgmtIsLinger(label);
    const outcome = _smgmtOutcomeCache[label] || null;
    const hasRun = _smgmtHasLedgerRun(label);
    if (inLinger && !hasRun)
      return "running";
    if (hasRun && outcome && typeof _smgmtStateMeta === "function") {
      const meta = _smgmtStateMeta(outcome, (outcome.issues || []).length);
      const st = meta.state;
      if (st === "ready_to_merge" || st === "completed")
        return "ready_to_merge";
      if (st === "needs_rework" || st === "partial_finished")
        return "needs_rework";
    }
    if (hasRun && _smgmtFinishedLabels && _smgmtFinishedLabels.has(label)) {
      return "ready_to_merge";
    }
    if (hasRun && outcome) {
      const lc = (outcome.lifecycle || "").toLowerCase();
      if (lc === "ready_to_merge")
        return "ready_to_merge";
      if (lc === "needs_rework" || lc === "partial_finished")
        return "needs_rework";
    }
    if (hasRun && !outcome && inLinger)
      return "running";
    const ps = ((planStates || {})[label] || "").toLowerCase();
    if (hasRun && ["draft", "planned", "planning"].includes(ps))
      return "draft";
    return "draft";
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
        const isAncestor = _smgmtResolvedAncestors.has(label);
        const previewQs = isAncestor ? "&preview=1" : "";
        try {
          const resp = await fetch(
            `/api/sprints/${encodeURIComponent(label)}/outcome?project=${encodeURIComponent(repo)}${previewQs}`
          );
          if (resp.ok) {
            const outcome = await resp.json();
            _smgmtOutcomeCache[label] = outcome;
            if (isAncestor) {
              _smgmtUpdateAncestorRow(label, outcome);
            } else {
              _smgmtInjectOutcomeBand(label, outcome);
            }
            return;
          }
          const fallback = _smgmtOutcomeFromBoard(label, bySprint[label] || []);
          _smgmtOutcomeCache[label] = fallback;
          if (isAncestor && fallback) {
            _smgmtUpdateAncestorRow(label, fallback);
          } else if (isAncestor) {
            _smgmtUpdateAncestorRow(label, null);
          }
        } catch (_) {
          const fallback = _smgmtOutcomeFromBoard(label, bySprint[label] || []);
          _smgmtOutcomeCache[label] = fallback;
          if (isAncestor) {
            _smgmtUpdateAncestorRow(label, fallback || null);
          }
        }
      })
    );
  }
  function _smgmtOutcomeFromBoard(label, tickets) {
    if (!tickets || tickets.length === 0)
      return null;
    const issues = tickets.map((t) => {
      const labelNames = (t.labels || []).map((l) => l.name);
      let outcome = "skipped";
      if (labelNames.includes("UAT-approved") || t.status === "done")
        outcome = "done";
      else if (labelNames.includes("needs-rework") || labelNames.includes("need-rework"))
        outcome = "failed";
      else if (t.status === "uat")
        outcome = "uat";
      else if (t.status === "sit" || t.status === "in-progress")
        outcome = "skipped";
      return { number: t.number, title: t.title || "", outcome };
    });
    const counts = { done: 0, failed: 0, skipped: 0, uat: 0 };
    for (const iss of issues) {
      const k = iss.outcome === "uat" ? "uat" : iss.outcome;
      if (counts[k] !== void 0)
        counts[k] += 1;
    }
    return {
      sprint_label: label,
      partial: true,
      state: "partial",
      lifecycle: "unknown",
      counts,
      wall_clock_secs: 0,
      issues
    };
  }
  async function _smgmtLoadEstimates(orderedLabels, bySprint) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    if (_smgmtAggregateCards) {
      await Promise.all(orderedLabels.map(async (label) => {
        const tickets = bySprint[label] || [];
        if (tickets.length === 0)
          return;
        for (const t of tickets)
          _smgmtTicketToSprint[t.number] = label;
        const card = _smgmtAggregateCards[label];
        if (!card)
          return;
        const estEl = document.getElementById(`smgmt-est-${label}`);
        if (estEl && card.estimate_hours != null) {
          const h = card.estimate_hours;
          const display = Number.isInteger(h) ? `${h}h` : `${parseFloat(h.toFixed(1))}h`;
          estEl.textContent = `${display} estimated`;
        }
        _smgmtSetSprintTokenEl(label, {});
      }));
      return;
    }
    await Promise.all(orderedLabels.map(async (label) => {
      const tickets = bySprint[label] || [];
      if (tickets.length === 0)
        return;
      for (const t of tickets)
        _smgmtTicketToSprint[t.number] = label;
      const issueNums = tickets.map((t) => t.number).join(",");
      try {
        const resp = await fetch(
          `/api/estimates/batch?project=${encodeURIComponent(repo)}&issues=${issueNums}`
        );
        if (!resp.ok)
          return;
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
    }));
  }
  async function _smgmtLoadConflicts(orderedLabels, bySprint) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    await Promise.all(orderedLabels.map(async (label) => {
      if (_smgmtRunningLabels.has(label))
        return;
      if (_smgmtFinishedLabels.has(label))
        return;
      const tickets = bySprint[label] || [];
      const pending = tickets.filter(
        (t) => (t.status || "backlog") === "backlog"
      );
      if (pending.length < 2)
        return;
      for (const t of pending)
        delete _smgmtConflictsByIssue[t.number];
      try {
        const resp = await fetch(
          `/api/sprints/${encodeURIComponent(label)}/conflicts?project=${encodeURIComponent(repo)}`
        );
        if (!resp.ok)
          return;
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
    }));
  }
  async function _smgmtLoadDepOrder(orderedLabels, bySprint) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    if (_smgmtAggregateCards) {
      await Promise.all(orderedLabels.map(async (label) => {
        if (_smgmtRunningLabels.has(label))
          return;
        if (_smgmtFinishedLabels.has(label))
          return;
        const card = _smgmtAggregateCards[label];
        if (!card || !card.dep_order)
          return;
        const tickets = bySprint[label] || [];
        const pending = tickets.filter((t) => (t.status || "backlog") === "backlog");
        if (pending.length < 2)
          return;
        const depData = card.dep_order;
        for (const t of pending)
          delete _smgmtDepOrderByIssue[t.number];
        if (depData.has_cycle) {
          const cycleSet = new Set((depData.in_cycle_tickets || []).map(String));
          for (const t of pending) {
            if (cycleSet.has(String(t.number))) {
              _smgmtDepOrderByIssue[t.number] = { upstream: [], downstream: [], inCycle: true };
            }
          }
        } else {
          for (const [idStr, hint] of Object.entries(depData.dep_hints || {})) {
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
      }));
      return;
    }
    await Promise.all(orderedLabels.map(async (label) => {
      if (_smgmtRunningLabels.has(label))
        return;
      if (_smgmtFinishedLabels.has(label))
        return;
      const tickets = bySprint[label] || [];
      const pending = tickets.filter(
        (t) => (t.status || "backlog") === "backlog"
      );
      if (pending.length < 2)
        return;
      for (const t of pending)
        delete _smgmtDepOrderByIssue[t.number];
      try {
        const resp = await fetch(
          `/api/sprints/${encodeURIComponent(label)}/dep-order?project=${encodeURIComponent(repo)}`
        );
        if (!resp.ok)
          return;
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
    }));
  }
  async function _smgmtLoadGoals(orderedLabels) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    await Promise.all(orderedLabels.map(async (label) => {
      const goalEl = document.getElementById(`smgmt-goal-${label}`);
      if (!goalEl)
        return;
      try {
        const resp = await fetch(
          `/api/sprints/goal?project=${encodeURIComponent(repo)}&sprint=${encodeURIComponent(label)}`
        );
        if (!resp.ok)
          return;
        const data = await resp.json();
        const goal = (data.goal || "").trim();
        if (goalEl.tagName === "INPUT" || goalEl.tagName === "TEXTAREA") {
          if (goal)
            goalEl.value = goal;
        } else if (goal) {
          goalEl.textContent = goal;
          goalEl.title = goal;
          goalEl.style.display = "";
        }
      } catch (_) {
      }
    }));
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
    const isLinger = false;
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
    const _badgeState = outcome && typeof _smgmtStateMeta === "function" ? _smgmtStateMeta(outcome, (outcome.issues || []).length).state || "" : "";
    const isHasRework = hasLedgerRun && (outcomeLifecycle === "needs_rework" || _badgeState === "needs_rework" || outcomeState === "has_rework" || outcomeState === "cancelled");
    const isReadyToMerge = hasLedgerRun && _badgeState !== "needs_rework" && (outcomeLifecycle === "ready_to_merge" || outcomeLifecycle === "completed" && outcomeState === "completed");
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
    } else if (isLinger && isHasRework) {
      actionBtn = rerunBtn;
    } else if (isLinger) {
      actionBtn = `<span class="smgmt-linger-note">Finished \u2014 snapshot kept 1h</span>`;
    } else if (isHasRework && rerunInto && tickets.length === 0) {
      actionBtn = `<button class="smgmt-run-btn" ${rerunDisabled} ${rerunTitle}
                  onclick="smgmtRunSprint('${escHtml(rerunInto)}')">
                  <i class="ti ti-player-play"></i> Run \u2192 ${escHtml(rerunChildDisplay)}</button>`;
    } else if (isHasRework || isPostRun) {
      actionBtn = rerunBtn;
    } else if (_smgmtSignoffState(label) === "pending") {
      actionBtn = _smgmtSignoffActionsHtml(label);
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
        ).join("") : "";
      } else {
        outcomeBandHtml = _smgmtOutcomeBandHtml(label, outcome);
        const _movedToChild = /* @__PURE__ */ new Set();
        try {
          Object.keys(_smgmtBySprint || {}).forEach((cl) => {
            if (cl !== label && cl.startsWith(label + ".")) {
              (_smgmtBySprint[cl] || []).forEach((t) => _movedToChild.add(t.number));
            }
          });
        } catch (_) {
        }
        const issueList = (outcome.issues || []).filter((i) => !_movedToChild.has(i.number));
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
      ticketsContainerHtml = tickets.length > 0 ? tickets.map((t) => _smgmtTicketRowHtml(t, label)).join("") : "";
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
    const plannedBadge = !finished && !isPostRun && !outcomeBadgeHtml && !isRunningView ? '<span class="sc-draft-badge">DRAFT</span>' : "";
    const signoffBadge = _smgmtSignoffBadgeHtml(label);
    const blockedHint = _smgmtSignoffState(label) === "pending" ? '<span class="sc-blocked-hint smgmt-blocked-hint--signoff">Approve the plan to run</span>' : _smgmtAnySprintRunning && !isPostRun && !isRunningView ? `<span class="sc-blocked-hint">blocked: ${_smgmtRunningBlockerShort()} running</span>` : "";
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
    const showDagOrderBtn = !isRunningView && !isPostRun && !finished && ["planned", "draft", "planning"].includes(planState);
    const cachedDagData = showDagOrderBtn ? typeof _smgmtDagDataCache !== "undefined" ? _smgmtDagDataCache[label] : null : null;
    const dagHasLevels = cachedDagData && (cachedDagData.levels || []).length > 0;
    const dagHasCycles = cachedDagData && (cachedDagData.cycles || []).length > 0;
    const dagOrderBtnDisabled = !dagHasLevels || dagHasCycles ? "disabled" : "";
    const dagOrderBtnTitle = dagHasCycles ? "Apply DAG Order \u2014 disabled: circular dependencies detected" : !dagHasLevels ? "Apply DAG Order \u2014 loading DAG preview\u2026" : cachedDagData && cachedDagData.partial ? "Apply DAG Order (partial preview \u2014 some tickets unestimated)" : "Apply DAG Order";
    const dagOrderBtn = showDagOrderBtn ? `<button class="smgmt-dag-order-btn" id="dag-order-btn-${escHtml(label)}"
               ${dagOrderBtnDisabled}
               title="${escHtml(dagOrderBtnTitle)}"
               onclick="smgmtApplyDagOrder('${escHtml(label)}')">
         <i class="ti ti-sort-ascending-2"></i> Apply DAG Order</button>` : "";
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
          ${actionBtn}
          ${blockedHint}
          ${isRunning ? runningElapsed : ""}
          ${isRunning ? "" : `<button class="smgmt-reconcile-btn sc-merge-link" type="button"
                  title="Reconcile this sprint's DB state against GitHub truth"
                  onclick="event.stopPropagation();smgmtReconcileSprint('${escHtml(label)}')">
            <i class="ti ti-refresh"></i> Reconcile</button>`}
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
        isHasRework,
        isReadyToMerge,
        isAwaitingMerge,
        planState,
        outcome,
        tickets,
        parent,
        isPostRun,
        isRunningView
      });
      if (!_ss)
        return "";
      return `<div class="sc-status-line"><i class="ti ti-clock sc-status-icon" aria-hidden="true"></i><span>${escHtml(_ss)}</span></div>`;
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
      return "";
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
    let isCollapsed = true;
    try {
      const _pref = localStorage.getItem("sprintColumn_" + label + "_collapsed");
      if (_pref === "0")
        isCollapsed = false;
      else if (_pref === "1")
        isCollapsed = true;
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
        ${ticketRowsHtml || ""}
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
      isHasRework,
      isReadyToMerge,
      isAwaitingMerge,
      planState,
      outcome,
      tickets,
      parent,
      isPostRun,
      isRunningView
    } = opts;
    if (isRunning)
      return "";
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
    if (isLinger)
      return "Sprint finished \u2014 snapshot kept 1 hour.";
    if (isReadyToMerge || isAwaitingMerge) {
      return "All tickets passed. Ready to merge.";
    }
    if (!isPostRun && !isRunningView) {
      const n = tickets.length;
      const parentShort = parent ? sprintLabelDisplay(parent).replace("Sprint ", "") : "";
      const held = n > 0 && parentShort ? ` Holds the ${n} ticket${n !== 1 ? "s" : ""} carried from ${parentShort}.` : n > 0 ? ` Holds ${n} ticket${n !== 1 ? "s" : ""}.` : "";
      if (_smgmtAnySprintRunning) {
        const blocker = typeof _smgmtRunningBlockerShort === "function" ? _smgmtRunningBlockerShort() : "another sprint";
        return `Ready to run.${held} Waiting on ${blocker} to finish.`;
      }
      if (!planState || planState === "draft" || planState === "planning") {
        return tickets.length === 0 ? "No tickets yet \u2014 drag some from the backlog." : _smgmtGoalRequired() ? "Set a sprint goal to enable the run." : "Ready to run.";
      }
      return held ? `Ready to run.${held}` : "Ready to run.";
    }
    if (_smgmtAnySprintRunning) {
      return "Blocked: another sprint is running.";
    }
    return tickets.length === 0 ? "No tickets \u2014 add some from the backlog." : "Ready to run.";
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
    const _planningAgent = ticket.status === "in-progress" ? "coder" : ticket.status === "sit" ? "tester" : null;
    const planningAgentHtml = _planningAgent ? `<span class="smgmt-ticket-agent-tag ${_smgmtAgentTagClass(_planningAgent)}">${escHtml(_planningAgent.toUpperCase())}</span>` : "";
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
    const eyebrowEl = document.getElementById("bl-eyebrow");
    if (eyebrowEl) {
      const n = _blBacklogAll.length;
      eyebrowEl.textContent = n > 0 ? `Backlog ${n} \xB7 source for planning` : "Backlog \xB7 source for planning";
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
      ticketsEl.innerHTML = `<div style="padding:var(--space-3) var(--space-4);text-align:center;color:var(--text-sub);font-size:12px;">${msg}</div>`;
    } else {
      ticketsEl.innerHTML = sorted.map((t) => _smgmtBacklogTicketHtml(t, allSprintNums)).join("");
    }
    _blSyncFilterPills();
    _blUpdateActions();
  }
  function _smgmtBacklogTicketHtml(ticket, _sprintNums) {
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
    const isSelected = typeof _smgmtSelectedIssues !== "undefined" && _smgmtSelectedIssues.has(ticket.number);
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
  function _smgmtAncestorStatsLine(outcome) {
    if (!outcome)
      return "";
    const c = outcome.counts || {};
    const parts = [];
    if (c.done)
      parts.push(`${c.done} done`);
    if (c.failed)
      parts.push(`${c.failed} failed`);
    if (c.uat)
      parts.push(`${c.uat} awaiting UAT`);
    if (c.skipped)
      parts.push(`${c.skipped} incomplete`);
    if (outcome.wall_clock_secs) {
      parts.push(`${_fmtRunningTime(outcome.wall_clock_secs)} elapsed`);
    }
    return parts.join(" \xB7 ");
  }
  function _smgmtAncestorCarrySummary(outcome, childLabel, mergeState) {
    if (!outcome)
      return "";
    const counts = outcome.counts || {};
    const done = counts.done || 0;
    const carried = (counts.failed || 0) + (counts.skipped || 0);
    const uat = counts.uat || 0;
    const childDisplay = childLabel ? sprintLabelDisplay(childLabel).replace("Sprint ", "") : "";
    if (mergeState === "failed") {
      if (carried > 0 && childDisplay) {
        return `${done} merged \xB7 ${carried} carried \u2192 ${childDisplay}`;
      }
      if (carried > 0)
        return `${done} merged \xB7 ${carried} carried`;
      return `${done} merged`;
    }
    if (mergeState === "needs_merge") {
      let summary2 = `${done} passed`;
      if (uat > 0)
        summary2 += ` \xB7 ${uat} awaiting UAT`;
      if (carried > 0 && childDisplay)
        summary2 += ` \xB7 ${carried} reworked \u2192 ${childDisplay}`;
      else if (carried > 0)
        summary2 += ` \xB7 ${carried} reworked`;
      return `${summary2} \xB7 not merged yet`;
    }
    let summary = `${done} merged`;
    if (uat > 0)
      summary += ` \xB7 ${uat} awaiting UAT`;
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
      const issueUrl = repo ? `https://github.com/${repo}/issues/${iss.number}` : "#";
      const elapsed = iss.elapsed_secs != null && iss.elapsed_secs > 0 ? `<span class="slp-ticket-elapsed">${escHtml(_fmtRunningTime(iss.elapsed_secs))}</span>` : "";
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
          <span class="slp-fate-carried">carried \u2192 ${escHtml(childDisplay)}</span>${elapsed}
        </div>`;
      }
      return `<div class="slp-ancestor-ticket-row">
          <span class="slp-ticket-incomplete" title="Incomplete"><i class="ti ti-dots"></i></span>
          <a class="smgmt-ticket-num" href="${escHtml(issueUrl)}" target="_blank" rel="noopener"
             onclick="event.stopPropagation()">#${iss.number}</a>
          <span class="smgmt-ticket-title slp-ticket-title" title="${escHtml(iss.title)}">${escHtml(iss.title)}</span>
          <span class="slp-fate-incomplete">incomplete</span>${elapsed}
        </div>`;
    }).join("");
  }
  function _smgmtAncestorRowHtml(label, outcome, childLabel) {
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
    const carrySummary = _smgmtAncestorCarrySummary(outcome || null, rerunInto, mergeState);
    const durationHtml = outcome && outcome.wall_clock_secs != null && outcome.wall_clock_secs > 0 ? `<span class="slp-ancestor-duration">${escHtml(_fmtRunningTime(outcome.wall_clock_secs))}</span>` : "";
    let ticketsHtml;
    if (outcome === void 0) {
      ticketsHtml = '<div class="slp-no-tickets">Loading outcome data\u2026</div>';
    } else if (!outcome) {
      ticketsHtml = '<div class="slp-no-tickets">No run data for this sprint \u2014 tickets may have moved to a child sprint.</div>';
    } else {
      const statsLine = _smgmtAncestorStatsLine(outcome);
      const statsHtml = statsLine ? `<div class="slp-ancestor-stats">${escHtml(statsLine)}</div>` : "";
      const listHtml = (outcome.issues || []).length > 0 ? _smgmtAncestorTicketsHtml(label, outcome, rerunInto) : '<div class="slp-no-tickets">No per-ticket records found.</div>';
      ticketsHtml = statsHtml + listHtml;
    }
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
    const lineageLabels = (orderedLabels || []).filter(
      (l) => _smgmtResolvedAncestors.has(l)
    );
    for (const label of lineageLabels) {
      const outcome = _smgmtOutcomeCache[label] || null;
      const mergeState = _smgmtAncestorMergeState(label, outcome);
      const display = sprintLabelDisplay(label);
      if (mergeState === "needs_merge") {
        const c = outcome && outcome.counts || {};
        const done = c.done || 0;
        const carried = (c.failed || 0) + (c.skipped || 0);
        steps.push({
          text: `${escHtml(display)} needs a merge decision \u2014 ${done} merged, ${carried} reworked`,
          priority: "high"
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
      const blocker = _smgmtAnySprintRunning && typeof _smgmtRunningBlockerShort === "function" ? _smgmtRunningBlockerShort() : "";
      steps.push({
        text: blocker ? `${escHtml(nextDisplay)} ready to run \u2014 blocked: ${escHtml(blocker)} running` : `${escHtml(nextDisplay)} ready to run`,
        priority: "med"
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
        text: `Finish planning ${escHtml(draftDisplay)} \u2014 ${goalNote} \xB7 ${headroomH}h headroom left`,
        priority: "low"
      });
    } else {
      steps.push({ text: "No draft sprint yet \u2014 create one to start planning.", priority: "low" });
    }
    const resolved = [];
    for (const label of lineageLabels) {
      const outcome = _smgmtOutcomeCache[label] || null;
      const mergeState = _smgmtAncestorMergeState(label, outcome);
      if (mergeState !== "merged" && mergeState !== "failed")
        continue;
      const display = sprintLabelDisplay(label).replace("Sprint ", "");
      const child = rerunInto[label];
      const childShort = child ? sprintLabelDisplay(child).replace("Sprint ", "") : "";
      let text = `${escHtml(display)} merged`;
      if (mergeState === "failed" && childShort) {
        const carried = (outcome && outcome.counts || {}).failed || 0;
        text = `${escHtml(display)} merged \xB7 ${escHtml(childShort)} failed (${carried} carried into ${escHtml(childShort)})`;
      }
      resolved.push({ text: `${text} \u2014 resolved`, resolved: true });
    }
    if (steps.length === 0) {
      steps.push({ text: "Board is up to date.", priority: "low" });
    }
    const allSteps = [
      ...steps.map((s, i) => ({ ...s, num: i + 1 })),
      ...resolved.map((s) => ({ ...s, num: null }))
    ];
    const stepHtml = allSteps.map((s) => {
      if (s.resolved) {
        return `<div class="smgmt-focus-step smgmt-focus-step--resolved"><span class="smgmt-focus-check" aria-hidden="true"><i class="ti ti-check"></i></span><span class="smgmt-focus-text">${s.text}</span></div>`;
      }
      return `<div class="smgmt-focus-step"><span class="smgmt-focus-num smgmt-focus-num--${s.priority}">${s.num}</span><span class="smgmt-focus-text">${s.text}</span></div>`;
    }).join("");
    return `<div class="smgmt-focus-guide-title">What to do, in order</div>` + stepHtml;
  }
  function smgmtAddToDraft(issueNum, draftLabel) {
    if (!draftLabel)
      return;
    const fakeEvt = {
      currentTarget: document.getElementById(`smgmt-ticket-${issueNum}`) || document.body,
      stopPropagation() {
      },
      preventDefault() {
      }
    };
    if (typeof _smgmtRowMenuOpen === "function") {
      _smgmtRowMenuOpen(fakeEvt, issueNum, null, false);
    }
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

  // apps/dashboard/static/src/sprint-board/run-controls.js
  var PF_STEPS = [
    { key: "ac", label: "Acceptance criteria", autoFixable: true },
    { key: "estimates", label: "Estimate coverage", autoFixable: true },
    { key: "cycle", label: "Dependency graph", autoFixable: false },
    { key: "missizing", label: "Mis-sizing review", autoFixable: false },
    { key: "conflicts", label: "Conflict analysis", autoFixable: false }
  ];
  var _pfStepFails = 0;
  var _pfAutofixPending = false;
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
    const mode = _smgmtDorMode();
    if (mode === "warn") {
      const tickets = typeof _smgmtBySprint !== "undefined" && _smgmtBySprint && _smgmtBySprint[label] || [];
      const notReady = _smgmtDorNotReadyTickets(tickets);
      if (notReady.length > 0) {
        const summary = notReady.map((t) => `#${t.number} \u2014 ${t.reasons.join(", ")}`).join("\n");
        if (!confirm(`${notReady.length} ticket(s) are not ready:

${summary}

Proceed anyway?`)) {
          return;
        }
      }
    }
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
  async function smgmtApproveSprint(label) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    if (!confirm(`Approve ${sprintLabelDisplay(label)}? This signs off the sprint and enables Run Sprint.`))
      return;
    try {
      const res = await fetch(`/api/sprints/${encodeURIComponent(label)}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: repo })
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        _smgmtShowToast(`Approve failed: ${err.detail || res.status}`);
        return;
      }
      _smgmtShowToast(`${sprintLabelDisplay(label)} approved \u2014 ready to run`);
      loadSprintMgmt();
    } catch (e) {
      _smgmtShowToast(`Approve failed: ${e.message}`);
    }
  }
  async function smgmtRejectSprint(label) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    if (!confirm(`Reject ${sprintLabelDisplay(label)}? The sprint is dissolved and all its tickets return to the backlog.`))
      return;
    try {
      const res = await fetch(`/api/sprints/${encodeURIComponent(label)}/reject`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: repo })
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        _smgmtShowToast(`Reject failed: ${err.detail || res.status}`);
        return;
      }
      _smgmtShowToast(`${sprintLabelDisplay(label)} rejected \u2014 tickets returned to backlog`);
      loadSprintMgmt();
    } catch (e) {
      _smgmtShowToast(`Reject failed: ${e.message}`);
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
    _pfLlmProvider = "anthropic";
    _pfXLSuggestions = [];
    _pfStrictXLGate = false;
    _pfXLMinutesSaved = 0;
    _pfShowLoadingActivity("Loading pre-flight checks\u2026");
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
    _pfLlmProvider = "anthropic";
    _pfXLSuggestions = [];
    _pfStrictXLGate = false;
    _pfXLMinutesSaved = 0;
    _pfStepFails = 0;
    _pfAutofixPending = false;
  }
  async function _pfFetch() {
    _pfState = "loading";
    _pfShowLoadingActivity("Loading pre-flight checks\u2026");
    const label = _pfCurrentLabel;
    const repo = _pfCurrentRepo;
    fetch("/api/settings/provider").then((r) => r.ok ? r.json() : null).then((d) => {
      if (d && d.provider && _pfCurrentLabel === label) {
        _pfLlmProvider = d.provider;
        const sel = document.getElementById("pf-provider-select");
        if (sel)
          sel.value = d.provider;
      }
    }).catch(() => {
    });
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
      _pfXLSuggestions = data.xl_suggestions || [];
      _pfStrictXLGate = data.strict_xl_gate || false;
      _pfXLMinutesSaved = data.xl_minutes_saved || 0;
      if (_pfDagData) {
        for (const t of _pfDagData.tickets || [])
          _pfSelectedIds.add(t.id);
      }
      _pfState = "success";
      _pfShowSuccess();
      _pfStepperAnimate(data).catch(() => _pfUpdateConfirmBtn());
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
    const xlHtml = _pfBuildXLSuggestionsHtml();
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
    const providerOptions = [
      { value: "anthropic", label: "Anthropic (subscription)" },
      { value: "ica", label: "IBM ICA (via claude-proxy)" }
    ];
    const providerSelectorHtml = `<div class="pf-section pf-provider-section">
     <label class="pf-cline-label" style="gap:8px">
       <span>LLM provider for this run:</span>
       <select id="pf-provider-select" onchange="_pfLlmProvider = this.value"
         style="font-size:12px;padding:2px 6px;border-radius:6px;border:1px solid var(--border);background:var(--surface);color:var(--text)">
         ${providerOptions.map(
      (o) => `<option value="${o.value}" ${_pfLlmProvider === o.value ? "selected" : ""}>${o.label}</option>`
    ).join("")}
       </select>
     </label>
   </div>`;
    document.getElementById("pf-content").innerHTML = `<p style="font-size:13px;color:var(--text);margin:0;">Ready to run <strong>Sprint ${n}</strong>.</p>
     ${modelsHtml}
     ${providerSelectorHtml}
     ${clineCheckboxHtml}
     ${warningsHtml}
     ${xlHtml}
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
    _pfStepperInit();
    document.getElementById("pf-cancel-btn").focus();
    if (_pfDagData && (_pfDagData.edges || []).length > 0) {
      requestAnimationFrame(() => _pfDrawDAGArrows(_pfDagData.edges));
    }
  }
  function _pfRecalcStepFails() {
    let fails = 0;
    if (_pfCycle && _pfCycle.length)
      fails++;
    const pendingFlags = _pfFlags && (_pfFlags.flags || []).filter((f) => f.status === "pending") || [];
    if (pendingFlags.length > 0) {
      fails++;
      _pfStepState("missizing", "fail", `${pendingFlags.length} flag(s) require review`);
    } else if (_pfFlags && (_pfFlags.flags || []).length > 0) {
      _pfStepState("missizing", "pass", "All flags resolved");
    }
    _pfStepFails = fails;
    _pfStepperSummary();
  }
  var _PF_SIZE_TIERS = /* @__PURE__ */ new Set(["S", "M", "L", "XL"]);
  function _pfFlagDefaultReestimateSize(flag) {
    const hist = String(flag?.historical_avg_actual_size || "").toUpperCase();
    if (_PF_SIZE_TIERS.has(hist))
      return hist;
    const cur = String(flag?.current_estimate || "").toUpperCase();
    if (_PF_SIZE_TIERS.has(cur))
      return cur;
    return "S";
  }
  function _pfFlagAutoReestimate(num) {
    const flag = (_pfFlags?.flags || []).find((f) => f.issue_number === num);
    if (!flag)
      return;
    _pfFlagAction(num, "reestimated", _pfFlagDefaultReestimateSize(flag));
  }
  function _pfUpdateConfirmBtn() {
    const hasCycle = !!(_pfCycle && _pfCycle.length);
    const pendingFlags = _pfFlags && (_pfFlags.flags || []).filter((f) => f.status === "pending") || [];
    const hasPending = pendingFlags.length > 0;
    const hasFail = _pfStepFails > 0;
    const hasBlockingXL = _pfStrictXLGate && _pfXLSuggestions && _pfXLSuggestions.length > 0;
    const confirmBtn = document.getElementById("pf-confirm-btn");
    if (!confirmBtn)
      return;
    confirmBtn.disabled = hasCycle || hasPending || hasFail || hasBlockingXL;
    if (hasCycle) {
      confirmBtn.title = "Cannot run: dependency cycle detected. Resolve the cycle first.";
      confirmBtn.setAttribute("aria-label", "Run Sprint \u2014 disabled: dependency cycle detected");
    } else if (hasPending) {
      confirmBtn.title = `Cannot run: ${pendingFlags.length} mis-sizing flag${pendingFlags.length > 1 ? "s" : ""} need review.`;
      confirmBtn.setAttribute("aria-label", "Run Sprint \u2014 disabled: mis-sizing flags need review");
    } else if (hasBlockingXL) {
      const n = _pfXLSuggestions.length;
      confirmBtn.title = `Cannot run: ${n} XL ticket${n > 1 ? "s" : ""} must be split or dismissed (Strict XL gate is on).`;
      confirmBtn.setAttribute("aria-label", `Run Sprint \u2014 disabled: strict XL gate blocks ${n} ticket(s)`);
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
  function _pfBuildXLSuggestionsHtml() {
    const suggestions = _pfXLSuggestions || [];
    if (!suggestions.length)
      return "";
    const label = _pfCurrentLabel;
    const strictNote = _pfStrictXLGate ? '<span class="pf-xl-strict-badge">Strict gate on \u2014 split or dismiss to proceed</span>' : "";
    const savedNote = _pfXLMinutesSaved > 0 ? `<div class="pf-xl-saved">~${_pfXLMinutesSaved} minutes saved if split</div>` : "";
    const rows = suggestions.map((s) => {
      const sizeLabel = s.size ? escHtml(s.size) : "?";
      const minsLabel = s.estimated_minutes ? `${s.estimated_minutes} min` : "";
      const estimate = [sizeLabel, minsLabel].filter(Boolean).join(" \xB7 ");
      const splitBtn = `<button class="pf-xl-split-btn" onclick="_smgmtSplitXlOpen('${escHtml(label || "")}', [${s.issue_number}])" title="Split #${s.issue_number} into smaller tickets (BA proposes, you confirm)">Split</button>`;
      return `<div class="pf-xl-item" id="pf-xl-item-${s.issue_number}">
      <div class="pf-xl-item-header">
        <span class="pf-xl-item-num">#${s.issue_number}</span>
        <span class="pf-xl-item-title" title="${escHtml(s.title)}">${escHtml(s.title)}</span>
        <span class="pf-xl-consider-label">Consider splitting</span>
        <span class="pf-xl-estimate">${escHtml(estimate)}</span>
      </div>
      <div class="pf-xl-item-actions">
        ${splitBtn}
        <button class="pf-xl-dismiss-btn" onclick="_pfDismissXLSuggestion(${s.issue_number})">Dismiss</button>
      </div>
    </div>`;
    });
    return `<div class="pf-xl-section" id="pf-xl-section">
    <div class="pf-xl-section-label">XL tickets \u2014 consider splitting ${strictNote}</div>
    ${savedNote}
    ${rows.join("")}
  </div>`;
  }
  function _pfPatchWarnings() {
    const content = document.getElementById("pf-content");
    if (!content)
      return;
    const html = _pfBuildWarningsHtml();
    content.querySelector(".pf-warnings-section")?.remove();
    if (!html)
      return;
    const anchor = content.querySelector(".pf-cline-section") || content.querySelector(".pf-models-section");
    if (anchor)
      anchor.insertAdjacentHTML("afterend", html);
  }
  function _pfShrinkWarnings(fix, _missingAc, _unestimated) {
    if (!_pfWarnings || fix.errors && fix.errors.length)
      return;
    if (fix.filled > 0 && _pfWarnings.missing_ac?.length) {
      _pfWarnings.missing_ac = _pfWarnings.missing_ac.slice(fix.filled);
    }
    if (fix.estimated > 0 && _pfWarnings.unestimated?.length) {
      _pfWarnings.unestimated = _pfWarnings.unestimated.slice(fix.estimated);
    }
    _pfPatchWarnings();
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
          <button class="pf-flag-action-btn" onclick="_pfFlagAutoReestimate(${num})" title="Apply historical average size">Re-estimate</button>
          <button class="pf-flag-action-btn dismiss" onclick="_pfFlagAction(${num}, 'dismissed')">Dismiss</button>
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
    const bulkBtnsHtml = pending > 0 ? `
      <div class="pf-flags-bulk-btns">
        <button class="pf-flags-bulk-btn" onclick="_pfApproveAll()">Approve all</button>
        <button class="pf-flags-bulk-btn" onclick="_pfReestimateAll()">Re-estimate all</button>
      </div>` : "";
    return `<div class="pf-flags-section" id="pf-flags-section">
    <div class="pf-flags-label-row">
      <span class="pf-flags-label">Mis-sizing review \u2014 ${subtitle}</span>${bulkBtnsHtml}
    </div>
    ${rows.join("")}
  </div>`;
  }
  function _pfFlagShowSizePicker(num) {
    _pfFlagAutoReestimate(num);
  }
  function _pfFlagHidePicker(_num) {
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
      _pfRecalcStepFails();
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
    _pfFlagAction(num, "reestimated", newSize);
  }
  var _pfBulkRunning = false;
  async function _pfApproveAll() {
    const pending = (_pfFlags?.flags || []).filter((f) => f.status === "pending");
    if (!pending.length)
      return;
    await _pfBulkProcess(pending, "approved");
  }
  async function _pfReestimateAll() {
    const pending = (_pfFlags?.flags || []).filter((f) => f.status === "pending");
    if (!pending.length)
      return;
    await _pfBulkProcess(pending, "reestimated");
  }
  function _pfBulkClose() {
    if (_pfBulkRunning)
      return;
    const overlay = document.getElementById("pf-bulk-overlay");
    if (overlay)
      overlay.classList.add("hidden");
    const flagsSection = document.getElementById("pf-flags-section");
    if (flagsSection) {
      const newHtml = _pfBuildFlagsHtml();
      flagsSection.outerHTML = newHtml || '<div id="pf-flags-section"></div>';
    }
    _pfRecalcStepFails();
    _pfUpdateConfirmBtn();
  }
  async function _pfBulkProcess(flags, action) {
    _pfBulkRunning = true;
    const overlay = document.getElementById("pf-bulk-overlay");
    const titleEl = document.getElementById("pf-bulk-title");
    const listEl = document.getElementById("pf-bulk-list");
    const doneBtn = document.getElementById("pf-bulk-done-btn");
    if (!overlay || !titleEl || !listEl || !doneBtn) {
      _pfBulkRunning = false;
      return;
    }
    const modeLabel = action === "approved" ? "Approving" : "Re-estimating";
    titleEl.textContent = `${modeLabel} ${flags.length} flag${flags.length !== 1 ? "s" : ""}\u2026`;
    doneBtn.disabled = true;
    doneBtn.textContent = "Close";
    listEl.innerHTML = flags.map((f) => `
    <div class="pf-bulk-item">
      <span class="pf-bulk-item-id">#${f.issue_number}</span>
      <span class="pf-bulk-item-title" title="${escHtml(f.title)}">${escHtml(f.title)}</span>
      <span class="pf-bulk-item-status pending" id="pf-bulk-status-${f.issue_number}">
        <i class="ti ti-clock"></i>
      </span>
    </div>`).join("");
    overlay.classList.remove("hidden");
    let errorCount = 0;
    for (const f of flags) {
      const statusEl = document.getElementById(`pf-bulk-status-${f.issue_number}`);
      if (statusEl) {
        statusEl.className = "pf-bulk-item-status processing";
        statusEl.innerHTML = '<span class="pf-bulk-spinner"></span>';
      }
      try {
        const body = { action };
        if (action === "reestimated")
          body.new_size = _pfFlagDefaultReestimateSize(f);
        const res = await fetch(
          `/api/sprints/${encodeURIComponent(_pfCurrentLabel)}/mis-sizing-flags/${f.issue_number}/action?project=${encodeURIComponent(_pfCurrentRepo)}`,
          { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
        );
        if (!res.ok)
          throw new Error(await res.text());
        _pfFlags = await res.json();
        if (statusEl) {
          statusEl.className = "pf-bulk-item-status done";
          statusEl.innerHTML = '<i class="ti ti-check"></i>';
        }
      } catch (e) {
        errorCount++;
        if (statusEl) {
          statusEl.className = "pf-bulk-item-status error";
          statusEl.innerHTML = '<i class="ti ti-x"></i>';
          statusEl.title = e.message;
        }
      }
    }
    const doneLabel = action === "approved" ? "Approved" : "Re-estimated";
    const suffix = errorCount ? ` \u2014 ${errorCount} error${errorCount !== 1 ? "s" : ""}` : "";
    titleEl.textContent = `${doneLabel}${suffix}`;
    doneBtn.disabled = false;
    _pfBulkRunning = false;
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
    unmountProgressActivity2("pf-stepper-steps");
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
    const llmProvider = _pfLlmProvider;
    const useClineFollowups = _pfUseClineFollowups;
    const confirmBtn = document.getElementById("pf-confirm-btn");
    confirmBtn.disabled = true;
    confirmBtn.textContent = "Starting\u2026";
    _pfClose();
    await smgmtKickoffRun(label, repo, { llmProvider, useClineFollowups });
  }
  function _paStepState(state) {
    return state === "fail" ? "failed" : state;
  }
  function _pfShowLoadingActivity(currentLabel) {
    const stepsEl = document.getElementById("pf-stepper-steps");
    if (!stepsEl)
      return;
    mountProgressActivity2(stepsEl, {
      status: "running",
      mode: "indeterminate",
      current: currentLabel || "Loading\u2026"
    }, {
      id: "pf-pa",
      hideLog: true
    });
  }
  function _pfStepperInit() {
    _pfStepFails = 0;
    const stepsEl = document.getElementById("pf-stepper-steps");
    if (!stepsEl)
      return;
    mountProgressActivity2(stepsEl, {
      status: "running",
      mode: "stepper",
      steps: PF_STEPS.map((s) => ({
        key: s.key,
        label: s.label,
        state: "pending",
        note: ""
      }))
    }, {
      id: "pf-pa",
      hideLog: true
    });
    const summaryEl = document.getElementById("pf-stepper-summary");
    if (summaryEl) {
      summaryEl.textContent = "";
      summaryEl.className = "pf-stepper-summary hidden";
    }
  }
  function _pfStepState(key, state, note) {
    patchProgressActivityStep("pf-stepper-steps", key, _paStepState(state), note || "", {
      id: "pf-pa",
      hideLog: true
    });
  }
  var AUTOFIX_TIMEOUT_MS = 12e4;
  async function _pfRunAutoFix(label, repo, onLog) {
    const controller = new AbortController();
    const timerId = setTimeout(() => controller.abort(), AUTOFIX_TIMEOUT_MS);
    try {
      const resp = await fetch(
        `/api/sprints/${encodeURIComponent(label)}/preflight-fix?project=${encodeURIComponent(repo)}`,
        { method: "POST", signal: controller.signal }
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
          if (m[1] === "log") {
            try {
              const d = JSON.parse(m[2]);
              const msg = typeof d === "string" ? d : d.message || String(d);
              if (onLog)
                onLog(msg);
            } catch (_) {
              if (onLog)
                onLog(m[2]);
            }
          } else if (m[1] === "done") {
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
    } catch (err) {
      const isTimeout = err && err.name === "AbortError";
      throw isTimeout ? new Error("timed out") : err;
    } finally {
      clearTimeout(timerId);
    }
  }
  async function _pfStepperAnimate(data) {
    const label = _pfCurrentLabel;
    const repo = _pfCurrentRepo;
    const missingAc = data.warnings && data.warnings.missing_ac || [];
    const unestimated = data.warnings && data.warnings.unestimated || [];
    const hasAcIssues = missingAc.length > 0;
    const hasEstIssues = unestimated.length > 0;
    const _routeAutofixLog = (msg) => {
      const s = String(msg || "");
      if (/acceptance criteria/i.test(s)) {
        _pfStepState("ac", "checking", s);
      } else if (/Estimating/i.test(s)) {
        _pfStepState("estimates", "checking", s);
      } else if (/Fixing \d+ pre-flight/i.test(s)) {
        if (hasAcIssues)
          _pfStepState("ac", "checking", s);
        if (hasEstIssues)
          _pfStepState("estimates", "checking", s);
      }
    };
    const _finishAutofix = (fix) => {
      const errCount = fix.errors && fix.errors.length ? fix.errors.length : 0;
      const errSuffix = errCount > 0 ? ` (${errCount} could not be fixed)` : "";
      const acNote = fix.filled > 0 ? `${fix.filled} acceptance criteria generated${errSuffix}` : hasAcIssues ? `${missingAc.length} ticket(s) missing AC${errSuffix}` : "";
      const estNote = fix.estimated > 0 ? `${fix.estimated} ticket(s) estimated${errSuffix}` : hasEstIssues ? `${unestimated.length} ticket(s) unestimated${errSuffix}` : "";
      _pfStepState("ac", fix.filled > 0 ? "fixed" : "pass", acNote);
      _pfStepState("estimates", fix.estimated > 0 ? "fixed" : "pass", estNote);
      _pfShrinkWarnings(fix, missingAc, unestimated);
      _pfAutofixPending = false;
      _pfStepperSummary();
    };
    if ((hasAcIssues || hasEstIssues) && label && repo) {
      _pfAutofixPending = true;
      _pfStepState("ac", "checking", hasAcIssues ? `Fixing ${missingAc.length} ticket(s)\u2026` : "");
      _pfStepState("estimates", "checking", hasEstIssues ? `Estimating ${unestimated.length} ticket(s)\u2026` : "");
      _pfRunAutoFix(label, repo, _routeAutofixLog).then(_finishAutofix).catch((err) => {
        const isTimeout = err && err.message === "timed out";
        const suffix = isTimeout ? " (timed out)" : "";
        _pfStepState("ac", "pass", hasAcIssues ? `${missingAc.length} ticket(s) missing AC${suffix}` : "");
        _pfStepState("estimates", "pass", hasEstIssues ? `${unestimated.length} ticket(s) unestimated${suffix}` : "");
        _pfAutofixPending = false;
        _pfStepperSummary();
      });
    } else {
      _pfStepState("ac", "pass", "");
      _pfStepState("estimates", "pass", "");
    }
    _pfStepState("cycle", "checking", "");
    if (data.cycle && data.cycle.length) {
      _pfStepState("cycle", "fail", `Cycle: ${data.cycle.join(" \u2192 ")}`);
      _pfStepFails++;
    } else {
      _pfStepState("cycle", "pass", "");
    }
    _pfStepState("missizing", "checking", "");
    const pendingFlags = (data.mis_sizing_flags && data.mis_sizing_flags.flags || []).filter((f) => f.status === "pending");
    if (pendingFlags.length > 0) {
      _pfStepState("missizing", "fail", `${pendingFlags.length} flag(s) require review`);
      _pfStepFails++;
    } else {
      _pfStepState("missizing", "pass", "");
    }
    _pfStepState("conflicts", "checking", "");
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
    } else if (_pfAutofixPending) {
      summaryEl.textContent = "Ready to run \u2014 preparing tickets in background";
      summaryEl.className = "pf-stepper-summary pf-stepper-summary--clear";
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
  var _ksLlmProvider = "anthropic";
  var _ksUseClineFollowups = false;
  function _ksInit() {
    const stepsEl = document.getElementById("smgmt-kickoff-steps");
    if (!stepsEl)
      return;
    mountProgressActivity2(stepsEl, {
      status: "running",
      mode: "stepper",
      steps: KS_STEPS.map((s) => ({
        key: s.key,
        label: s.label,
        state: "pending",
        note: ""
      }))
    }, {
      id: "ks-pa",
      hideLog: true
    });
    const errEl = document.getElementById("smgmt-kickoff-error");
    if (errEl)
      errEl.hidden = true;
  }
  function _ksSetStep(key, state, note) {
    patchProgressActivityStep("smgmt-kickoff-steps", key, _paStepState(state), note || "", {
      id: "ks-pa",
      hideLog: true
    });
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
        body: JSON.stringify({
          project: repo,
          sprint_label: label,
          use_cline_followups: _ksUseClineFollowups,
          // Omit llm_provider when no explicit choice was made — the server then
          // resolves the global llmProvider setting as the default.
          ..._ksLlmProvider ? { llm_provider: _ksLlmProvider } : {}
        })
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
      await new Promise((r) => setTimeout(r, 2e3));
      if (await _ksIsRunning(_ksLabel)) {
        _ksSetStep("branch", "pass", "");
        return true;
      }
    }
    _ksShowError(
      "branch",
      "Sprint didn\u2019t start running \u2014 it likely exited immediately. Most often no dispatchable tickets (check the sprint label + status labels on the tickets), or it finished/crashed. Check the run log, then Retry."
    );
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
  async function smgmtKickoffRun(label, repo, opts = {}) {
    _ksLlmProvider = opts.llmProvider ?? null;
    _ksUseClineFollowups = opts.useClineFollowups ?? false;
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

  // apps/dashboard/static/src/sprint-board/board-overlay.js
  var _smgmtBoardOverlayHasProgress = false;
  function _smgmtBoardLock2(message, opts) {
    _smgmtMoveLock = true;
    _smgmtArStopTicker();
    const overlay = document.getElementById("smgmt-move-overlay");
    const msgEl = document.getElementById("smgmt-move-overlay-msg");
    const paHost = document.getElementById("smgmt-op-pa-host");
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
    _smgmtBoardOverlayHasProgress = showProgress;
    if (progWrap)
      progWrap.hidden = true;
    if (logEl) {
      logEl.hidden = true;
      if (opts && opts.clearLog)
        logEl.innerHTML = "";
    }
    if (paHost) {
      paHost.hidden = !showProgress;
      if (showProgress) {
        mountProgressActivity2(paHost, {
          status: "running",
          mode: "bar",
          done: 0,
          total: (opts && opts.total) != null ? opts.total : 1,
          current: text,
          log_tail: []
        }, {
          id: BOARD_OVERLAY_PA_ID
        });
      } else {
        unmountProgressActivity2(paHost);
      }
    }
    if (showProgress && opts.total != null) {
      _smgmtBoardProgress2(0, opts.total);
    } else if (!showProgress) {
      _smgmtBoardProgress2(0, 1);
    }
    if (opts && opts.showDone) {
      const doneEl = document.getElementById("smgmt-op-done");
      if (doneEl) {
        doneEl.hidden = false;
        doneEl.style.cssText = "margin-top:12px;text-align:center";
        doneEl.innerHTML = '<button type="button" class="btn-primary" id="smgmt-op-done-btn" disabled>Done</button>';
      }
    }
  }
  function _smgmtBoardProgress2(done, total) {
    if (_smgmtBoardOverlayHasProgress) {
      const d = Number(done || 0);
      const t = Number(total || 0);
      patchProgressActivity("smgmt-op-pa-host", {
        done: d,
        total: t,
        mode: "bar",
        status: "running",
        current: t > 0 ? `${d} of ${t}` : ""
      }, { id: BOARD_OVERLAY_PA_ID });
      return;
    }
    const fill = document.getElementById("smgmt-op-progress-fill");
    const pctEl = document.getElementById("smgmt-op-progress-pct");
    const pct = total > 0 ? Math.round(done / total * 100) : 0;
    if (fill)
      fill.style.width = pct + "%";
    if (pctEl)
      pctEl.textContent = pct + "%";
  }
  function _smgmtBoardLog2(line, kind) {
    if (_smgmtBoardOverlayHasProgress) {
      const mappedType = kind === "ok" ? "success" : kind === "err" ? "fail" : kind === "step" ? "dispatch" : "dispatch";
      appendProgressActivityLog2("smgmt-op-pa-host", line, mappedType, { id: BOARD_OVERLAY_PA_ID });
      return;
    }
    const logEl = document.getElementById("smgmt-op-log");
    if (!logEl)
      return;
    const row = document.createElement("div");
    row.className = "smgmt-op-log-line" + (kind ? ` smgmt-op-log-line--${kind}` : "");
    row.textContent = line;
    logEl.appendChild(row);
    logEl.scrollTop = logEl.scrollHeight;
  }
  function _smgmtBoardUnlock() {
    _smgmtMoveLock = false;
    _smgmtBoardOverlayHasProgress = false;
    const overlay = document.getElementById("smgmt-move-overlay");
    if (overlay)
      overlay.classList.remove("active");
    const paHost = document.getElementById("smgmt-op-pa-host");
    if (paHost) {
      unmountProgressActivity2(paHost);
      paHost.hidden = true;
    }
    const progWrap = document.getElementById("smgmt-op-progress-wrap");
    const logEl = document.getElementById("smgmt-op-log");
    if (progWrap)
      progWrap.hidden = true;
    if (logEl) {
      logEl.hidden = true;
      logEl.innerHTML = "";
    }
    const doneEl = document.getElementById("smgmt-op-done");
    if (doneEl) {
      doneEl.hidden = true;
      doneEl.innerHTML = "";
    }
    const errEl = document.getElementById("smgmt-op-error");
    if (errEl) {
      errEl.hidden = true;
      errEl.textContent = "";
    }
    const spinner = document.getElementById("smgmt-move-spinner");
    if (spinner)
      spinner.style.display = "";
    _smgmtBoardProgress2(0, 1);
    if (_arInterval > 0)
      _smgmtArStartTicker();
  }
  function _smgmtBoardFinish2(opts) {
    opts = opts || {};
    const ok = opts.ok !== false;
    const message = opts.message || (ok ? "Done." : "Stopped.");
    const onDone = opts.onDone;
    _smgmtArStopTicker();
    const spinner = document.getElementById("smgmt-move-spinner");
    if (spinner)
      spinner.style.display = "none";
    const overlay = document.getElementById("smgmt-move-overlay");
    if (overlay)
      overlay.setAttribute("aria-busy", "false");
    if (_smgmtBoardOverlayHasProgress) {
      patchProgressActivity(
        "smgmt-op-pa-host",
        { status: ok ? "done" : "failed", current: message },
        { id: BOARD_OVERLAY_PA_ID }
      );
    }
    const msgEl = document.getElementById("smgmt-move-overlay-msg");
    const errEl = document.getElementById("smgmt-op-error");
    if (ok) {
      if (msgEl)
        msgEl.textContent = message;
      if (errEl) {
        errEl.hidden = true;
        errEl.textContent = "";
      }
    } else {
      if (errEl) {
        errEl.textContent = message;
        errEl.hidden = false;
        errEl.style.cssText = "color:var(--red,#e5484d);font-size:13px;margin-top:10px;text-align:left;white-space:pre-wrap;max-height:160px;overflow:auto";
      }
    }
    const doneEl = document.getElementById("smgmt-op-done");
    if (doneEl) {
      doneEl.hidden = false;
      doneEl.style.cssText = "margin-top:12px;text-align:center";
      let btn = document.getElementById("smgmt-op-done-btn");
      if (!btn) {
        doneEl.innerHTML = '<button type="button" class="btn-primary" id="smgmt-op-done-btn">Done</button>';
        btn = document.getElementById("smgmt-op-done-btn");
      }
      if (btn) {
        btn.disabled = false;
        btn.onclick = () => {
          _smgmtBoardUnlock();
          if (typeof onDone === "function") {
            try {
              onDone();
            } catch (_) {
            }
          }
        };
      }
    }
  }
  function _smgmtBoardHalt(message, onDone) {
    _smgmtBoardFinish2({ ok: false, message, onDone });
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
  globalThis.finishSprintAndWait = finishSprintAndWait2;
  globalThis._bcOpen = _bcOpen;
  globalThis._bcClose = _bcClose;
  globalThis._bcCatClass = _bcCatClass;
  globalThis._bcSelectAll = _bcSelectAll;
  globalThis.smgmtBulkCompleteSprint = smgmtBulkCompleteSprint;
  globalThis._bcConfirm = _bcConfirm;
  globalThis.bulkCompleteLineageAndWait = bulkCompleteLineageAndWait2;
  globalThis.smgmtReconcileSprint = smgmtReconcileSprint;
  globalThis._recApply = _recApply;
  globalThis._recClose = _recClose;
  globalThis.smgmtRunBlockedToast = smgmtRunBlockedToast;
  globalThis.smgmtRunSprint = smgmtRunSprint2;
  globalThis.smgmtCancelSprint = smgmtCancelSprint;
  globalThis.smgmtApproveSprint = smgmtApproveSprint;
  globalThis.smgmtRejectSprint = smgmtRejectSprint;
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
  globalThis._pfFlagAutoReestimate = _pfFlagAutoReestimate;
  globalThis._pfApproveAll = _pfApproveAll;
  globalThis._pfReestimateAll = _pfReestimateAll;
  globalThis._pfBulkClose = _pfBulkClose;
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
  globalThis._smgmtBoardLock = _smgmtBoardLock2;
  globalThis._smgmtBoardUnlock = _smgmtBoardUnlock;
  globalThis._smgmtBoardProgress = _smgmtBoardProgress2;
  globalThis._smgmtBoardLog = _smgmtBoardLog2;
  globalThis._smgmtBoardFinish = _smgmtBoardFinish2;
  globalThis._smgmtBoardHalt = _smgmtBoardHalt;
  globalThis.loadSprintMgmt = loadSprintMgmt2;
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
  globalThis._smgmtApplyRerunOptimistic = _smgmtApplyRerunOptimistic2;
  globalThis._smgmtAncestorMergeState = _smgmtAncestorMergeState;
  globalThis._smgmtAncestorCarrySummary = _smgmtAncestorCarrySummary;
  globalThis._smgmtAncestorTicketsHtml = _smgmtAncestorTicketsHtml;
  globalThis._smgmtAncestorRowHtml = _smgmtAncestorRowHtml;
  globalThis.smgmtToggleAncestor = smgmtToggleAncestor;
  globalThis._smgmtUpdateAncestorRow = _smgmtUpdateAncestorRow;
  globalThis.smgmtAddToDraft = smgmtAddToDraft;
  globalThis._smgmtSchedToggleHtml = _smgmtSchedToggleHtml2;
  globalThis.smgmtToggleRunOnSchedule = smgmtToggleRunOnSchedule;
  globalThis._smgmtHydrateSchedToggles = _smgmtHydrateSchedToggles2;
  globalThis.smgmtPlanNextSprint = smgmtPlanNextSprint;
  globalThis._smgmtLoadPendingSignoff = _smgmtLoadPendingSignoff;
  globalThis._histNeedsActionCount = _histNeedsActionCount;
  globalThis._histLoadLedger = _histLoadLedger2;
  globalThis._histPrefetchLedger = _histPrefetchLedger;
  globalThis._histScanStale = _histScanStale2;
  globalThis._histCleanupStale = _histCleanupStale;
  globalThis._histToggleCard = _histToggleCard;
  globalThis._histToggleGroup = _histToggleGroup;
  globalThis._histToggleFold = _histToggleFold;
  globalThis._histFocusLabel = _histFocusLabel;
  globalThis._histStateChip = _histStateChip;
  globalThis._histRenderLedger = _histRenderLedger;
  globalThis._histRerunSprint = _histRerunSprint;
  globalThis._histToggleAgentTime = _histToggleAgentTime;
  globalThis._histToggleMetrics = _histToggleMetrics;
  globalThis._histResetLedgerCache = _histResetLedgerCache;
  globalThis._histToggleShowClosed = _histToggleShowClosed;
  globalThis._histForceRefresh = _histForceRefresh;
  globalThis._histSetTtlMin = _histSetTtlMin;
  globalThis._histBulkSignOff = _histBulkSignOff;
  globalThis._histClearStaleLabels = _histClearStaleLabels;

  // apps/dashboard/static/src/index.js
  var root = typeof window !== "undefined" ? window : globalThis;
  root.colorizeLogLine = colorizeLogLine2;
  root.escapeLogHtml = escapeLogHtml;
  root.extractRaw = extractRaw;
  root.AGENT_NAMES = AGENT_NAMES;
  root.renderProgressActivity = renderProgressActivity2;
  root.updateProgressActivityLog = updateProgressActivityLog;
  root.patchProgressActivityInPlace = patchProgressActivityInPlace2;
  root.paToggleLog = paToggleLog;
  root.mountProgressActivity = mountProgressActivity2;
  root.patchProgressActivity = patchProgressActivity;
  root.patchProgressActivityStep = patchProgressActivityStep;
  root.unmountProgressActivity = unmountProgressActivity2;
  root.appendProgressActivityLog = appendProgressActivityLog2;
  root.getProgressActivityPayload = getProgressActivityPayload;
  root.BOARD_OVERLAY_PA_ID = BOARD_OVERLAY_PA_ID;
  injectProgressActivityCss();
  root.switchTab = switchTab;
  root.toggleStabDropdown = toggleStabDropdown;
  root.closeAllStabDropdowns = closeAllStabDropdowns;
  root.loadCommanderFeatures = loadCommanderFeatures;
  globalThis.switchTab = switchTab;
  globalThis.toggleStabDropdown = toggleStabDropdown;
  globalThis.closeAllStabDropdowns = closeAllStabDropdowns;
  globalThis.loadCommanderFeatures = loadCommanderFeatures;
  root.sprintCleanupPreview = sprintCleanupPreview;
  root.sprintCleanupConfirm = sprintCleanupConfirm;
  root.testFilesCleanupPreview = testFilesCleanupPreview;
  root.testFilesCleanupConfirm = testFilesCleanupConfirm;
  root.psStaleBranchesScan = psStaleBranchesScan;
  root.psPruneMergedBranches = psPruneMergedBranches;
  root.psCleanupLogClear = psCleanupLogClear;
  root.psCleanupModalConfirm = psCleanupModalConfirm;
  root._psCleanupModalClose = _psCleanupModalClose;
  root._psCleanupPaneClose = _psCleanupPaneClose;
  root._psCleanupPaneConfirm = _psCleanupPaneConfirm;
  globalThis.sprintCleanupPreview = sprintCleanupPreview;
  globalThis.sprintCleanupConfirm = sprintCleanupConfirm;
  globalThis.testFilesCleanupPreview = testFilesCleanupPreview;
  globalThis.testFilesCleanupConfirm = testFilesCleanupConfirm;
  globalThis.psStaleBranchesScan = psStaleBranchesScan;
  globalThis.psPruneMergedBranches = psPruneMergedBranches;
  globalThis.psCleanupLogClear = psCleanupLogClear;
  globalThis.psCleanupModalConfirm = psCleanupModalConfirm;
  globalThis._psCleanupModalClose = _psCleanupModalClose;
  globalThis._psCleanupPaneClose = _psCleanupPaneClose;
  globalThis._psCleanupPaneConfirm = _psCleanupPaneConfirm;
})();
//# sourceMappingURL=bundle.js.map
