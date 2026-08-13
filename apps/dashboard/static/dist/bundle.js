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
    if (s.length === 0 || s[0] !== "{") return s;
    try {
      const obj = JSON.parse(s);
      if (typeof obj.raw === "string") return obj.raw;
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
    if (payload.mode) return payload.mode;
    if (Array.isArray(payload.steps) && payload.steps.length > 0)
      return "stepper";
    if (payload.total != null) return "bar";
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
    if (!line) return "";
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
    if (!payload || typeof payload !== "object") payload = {};
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
    if (typeof document === "undefined") return;
    const streamEl = document.getElementById("pa-log-stream-" + rootId);
    if (!streamEl) return;
    const lines = Array.isArray(logTail) ? logTail : [];
    const emptyMsg = '<div class="pa-log-line" style="color:var(--text-sub)">Waiting for log\u2026</div>';
    streamEl.innerHTML = lines.length ? lines.map((l) => _logLineHtml(l, colorize || null)).join("") : emptyMsg;
    streamEl.scrollTop = streamEl.scrollHeight;
  }
  function patchProgressActivityInPlace2(rootId, payload, opts) {
    if (typeof document === "undefined" || !rootId) return false;
    const root2 = document.getElementById(rootId);
    if (!root2) return false;
    const status = payload.status || "running";
    if (status === "done" || status === "error") return false;
    const mode = payload.mode || _detectMode(payload);
    if (mode !== "bar") return false;
    const fill = root2.querySelector(".pa-bar-fill");
    if (!fill) return false;
    const done = Number(payload.done ?? 0);
    const total = Number(payload.total ?? 0);
    const pct = total > 0 ? Math.min(100, Math.round(done / total * 100)) : 0;
    fill.style.transform = `scaleX(${pct / 100})`;
    const cur = root2.querySelector(".pa-current");
    if (cur && payload.current != null) cur.textContent = String(payload.current);
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
    if (typeof document === "undefined") return;
    const el = document.getElementById("pa-log-stream-" + rootId);
    if (el) el.classList.toggle("pa-log-collapsed");
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
    if (_cssInjected || typeof document === "undefined") return;
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
    if (!host) return null;
    if (typeof host === "string") {
      if (typeof document === "undefined") return null;
      return document.getElementById(host);
    }
    return host;
  }
  function _resolvePaId(hostEl, explicitId) {
    if (explicitId) return explicitId;
    if (hostEl && hostEl.dataset && hostEl.dataset.paId) return hostEl.dataset.paId;
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
    if (typeof document === "undefined") return null;
    const el = document.getElementById(_logStreamId(paId));
    if (!el) return null;
    return {
      top: el.scrollTop,
      atBottom: el.scrollHeight - el.scrollTop - el.clientHeight < 8
    };
  }
  function _restoreLogScroll(paId, state) {
    if (!state || typeof document === "undefined") return;
    const el = document.getElementById(_logStreamId(paId));
    if (!el) return;
    if (state.atBottom) el.scrollTop = el.scrollHeight;
    else el.scrollTop = state.top;
  }
  function _renderIntoHost(hostEl, payload, opts) {
    if (!hostEl) return;
    const renderOpts = opts || {};
    const paId = _resolvePaId(hostEl, renderOpts.id);
    const scrollState = _captureLogScroll(paId);
    hostEl.innerHTML = renderProgressActivity2(payload, renderOpts);
    _restoreLogScroll(paId, scrollState);
  }
  function mountProgressActivity2(host, payload, opts) {
    const hostEl = _resolveHost(host);
    if (!hostEl) return null;
    const paId = _resolvePaId(hostEl, opts && opts.id);
    const renderOpts = Object.assign({}, opts || {}, { id: paId });
    const next = _storePayload(paId, payload || {});
    if (hostEl.dataset) hostEl.dataset.paId = paId;
    hostEl.hidden = false;
    _renderIntoHost(hostEl, next, renderOpts);
    return next;
  }
  function getProgressActivityPayload(host) {
    const hostEl = _resolveHost(host);
    const paId = hostEl ? _resolvePaId(hostEl) : typeof host === "string" ? host : null;
    if (!paId) return null;
    const payload = _payloadById.get(paId);
    return payload ? _snapshot(payload) : null;
  }
  function patchProgressActivity(host, patch, opts) {
    const hostEl = _resolveHost(host);
    if (!hostEl) return null;
    const paId = _resolvePaId(hostEl, opts && opts.id);
    const prev = _payloadById.get(paId) || {};
    const next = Object.assign({}, prev, patch || {});
    if (hostEl.dataset) hostEl.dataset.paId = paId;
    _storePayload(paId, next);
    _renderIntoHost(hostEl, next, Object.assign({}, opts || {}, { id: paId }));
    return _snapshot(next);
  }
  function patchProgressActivityStep(host, stepKey, state, note, opts) {
    const hostEl = _resolveHost(host);
    if (!hostEl) return null;
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
    if (!hostEl) return null;
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
    if (!hostEl) return;
    const paId = _resolvePaId(hostEl);
    hostEl.innerHTML = "";
    hostEl.hidden = true;
    if (hostEl.dataset) delete hostEl.dataset.paId;
    _payloadById.delete(paId);
  }

  // apps/dashboard/static/src/shell/tabs.js
  var _GROUP_CHILDREN = {
    manage: ["deploy", "bulk-create"]
  };
  function computeRovingTabindex(tab, onGlobalSettings) {
    return Object.fromEntries(
      ["sprint-mgmt", "tickets", "failures", "brain", "manage", "settings"].map(
        (t) => {
          const ownsTab = !onGlobalSettings && (t === tab || _GROUP_CHILDREN[t] && _GROUP_CHILDREN[t].includes(tab));
          return [t, ownsTab ? 0 : -1];
        }
      )
    );
  }
  function switchTab(tab, pushHistory) {
    if (tab === "metrics" || tab === "logs" || tab === "status") {
      console.warn(
        '[tabs] switchTab("' + tab + '"): tab removed in #2025, redirecting to "failures"'
      );
      tab = "failures";
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
    if (_activeTab === "deploy" && tab !== "deploy") {
      deployTabDestroy();
    }
    _activeTab = tab;
    const onGlobalSettings = tab === "global-settings";
    _globalSettingsLinkActive(onGlobalSettings);
    const projHeader = document.getElementById("proj-header");
    if (projHeader) projHeader.classList.toggle("hidden", onGlobalSettings);
    const subTabsRow = document.querySelector(".sub-tabs-row");
    if (subTabsRow) subTabsRow.classList.toggle("hidden", onGlobalSettings);
    const _topLevelTabs = [
      "sprint-mgmt",
      "tickets",
      "failures",
      "brain",
      "manage",
      "settings"
    ];
    [
      "sprint-mgmt",
      "tickets",
      "deploy",
      "bulk-create",
      "failures",
      "brain",
      "settings"
    ].forEach((t) => {
      const btn = document.getElementById("stab-" + t);
      if (!btn) return;
      const isActive = !onGlobalSettings && t === tab;
      btn.classList.toggle("active", isActive);
      btn.setAttribute("aria-selected", String(isActive));
    });
    const _rovingMap = computeRovingTabindex(tab, onGlobalSettings);
    _topLevelTabs.forEach((t) => {
      const suffix = t === "manage" ? "manage-trigger" : t;
      const btn = document.getElementById("stab-" + suffix);
      if (!btn) return;
      btn.tabIndex = _rovingMap[t];
    });
    closeAllStabDropdowns();
    ["manage"].forEach((groupName) => {
      const group = document.getElementById("stab-group-" + groupName);
      if (!group) return;
      const trigger = group.querySelector(".stab-trigger");
      if (trigger)
        trigger.classList.toggle("active", !!group.querySelector(".stab.active"));
    });
    [
      "sprint-mgmt",
      "tickets",
      "deploy",
      "bulk-create",
      "failures",
      "brain",
      "settings",
      "global-settings"
    ].forEach((t) => {
      const pane = document.getElementById("pane-" + t);
      if (pane) pane.classList.toggle("active", t === tab);
    });
    const newUrl = "/project/" + encodeURIComponent(_slug) + "/" + tab;
    if (pushHistory !== false) {
      window.history.pushState({ slug: _slug, tab }, "", newUrl);
    }
    if (tab === "tickets") {
      loadTickets();
    }
    if (tab === "sprint-mgmt") {
      if (_deepLinkSprintSubView()) _applyDeepLinkSubView();
      else _smgmtShowSubView(_smgmtSavedSubView() || "board");
    }
    if (tab === "sprint-mgmt" && !_sprintMgmtLoaded && _cachedFullRepo[_slug]) {
      _sprintMgmtLoaded = true;
      loadSprintMgmt().then(() => _smgmtArInit());
      _histLoadLedger(_cachedFullRepo[_slug]);
    } else if (tab === "sprint-mgmt" && _sprintMgmtLoaded) {
      if (_arTickerId === null && _arInterval > 0) _smgmtArStartTicker();
    }
    if (tab === "bulk-create") {
      _bcInitTab();
      _lpRenderBc();
    }
    if (tab === "deploy") deployTabInit();
    if (tab === "failures") failuresInit();
    if (tab === "brain") brainInit();
    if (tab === "settings") projSettingsInit();
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
    if (!isOpen) {
      group.classList.add("open");
      if (window.innerWidth <= 430) {
        const trigger = group.querySelector(".stab-trigger");
        const dropdown = group.querySelector(".stab-dropdown");
        if (trigger && dropdown) {
          const rect = trigger.getBoundingClientRect();
          dropdown.style.setProperty("position", "fixed");
          dropdown.style.setProperty("top", rect.bottom + 2 + "px");
          dropdown.style.setProperty("left", rect.left + "px");
        }
      }
    }
  }
  function closeAllStabDropdowns() {
    document.querySelectorAll(".stab-group.open").forEach((g) => {
      g.classList.remove("open");
      const dropdown = g.querySelector(".stab-dropdown");
      if (dropdown) {
        dropdown.style.removeProperty("position");
        dropdown.style.removeProperty("top");
        dropdown.style.removeProperty("left");
      }
    });
  }
  document.addEventListener("click", closeAllStabDropdowns);
  var _subTabsEl = document.getElementById("sub-tabs");
  if (_subTabsEl) {
    _subTabsEl.addEventListener("keydown", function(e) {
      const enabledTabs = [
        "sprint-mgmt",
        "tickets",
        "failures",
        "brain",
        "manage",
        "deploy",
        "settings"
      ];
      const focused = document.activeElement;
      const currentId = focused ? focused.id.replace("stab-", "") : null;
      const currentIdx = enabledTabs.indexOf(currentId);
      if (currentIdx < 0) return;
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
        if (currentId) switchTab(currentId);
      }
    });
  }
  window.addEventListener("popstate", function(e) {
    const { slug, tab, view, filter } = parseUrl();
    const effSlug = slug || e.state && e.state.slug;
    const effTab = (slug ? tab : e.state && e.state.tab) || "sprint-mgmt";
    if (!effSlug) return;
    if (effSlug !== _slug) {
      _ticketsRepo = null;
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
  function planningEnabled() {
    return commanderFeatures().planning === true;
  }
  async function loadCommanderFeatures() {
    try {
      const res = await fetch("/api/environment", { cache: "no-store" });
      if (!res.ok) throw new Error(String(res.status));
      const data = await res.json();
      _features = data.features || {};
    } catch {
      _features = { signoff: false, planning: false };
    }
    const root2 = typeof window !== "undefined" ? window : globalThis;
    root2._commanderFeatures = _features;
    applyFeatureFlags();
    return _features;
  }
  function _hide(el) {
    if (!el) return;
    el.classList.add("hidden");
    el.setAttribute("aria-hidden", "true");
  }
  function applyFeatureFlags() {
    if (!planningEnabled()) {
      _hide(document.getElementById("smgmt-plan-next-btn"));
    }
    if (!signoffEnabled()) {
      _hide(document.getElementById("snav-signoff"));
    }
  }

  // apps/dashboard/static/src/shell/url-parser.js
  var _PATH_RE = /^\/project\/([^/]+)\/?([^/]*)?$/;
  function _parseUrlImpl(pathname, search = "") {
    const m = pathname.match(_PATH_RE);
    const _q = new URLSearchParams(search);
    const view = (_q.get("view") || "").toLowerCase() || null;
    const filter = (_q.get("filter") || "").toLowerCase() || null;
    if (!m) return { slug: null, tab: "sprint-mgmt", view, filter };
    const slug = decodeURIComponent(m[1]);
    const rawTab = m[2] || "";
    const tab = rawTab === "tickets" ? "tickets" : rawTab === "sprint" ? "sprint-mgmt" : rawTab === "bulk-create" ? "bulk-create" : rawTab === "failures" ? "failures" : rawTab === "brain" ? "brain" : rawTab === "settings" ? "settings" : rawTab === "global-settings" ? "global-settings" : "sprint-mgmt";
    return { slug, tab, view, filter };
  }
  function parseUrl2() {
    const loc = window.location;
    return _parseUrlImpl(loc.pathname, loc.search);
  }

  // apps/dashboard/static/src/shell/visibility.js
  var _viHandles = /* @__PURE__ */ new Map();
  var _viIdSeq = 1e6;
  function visibilityInterval(fn, delay) {
    const fakeId = ++_viIdSeq;
    let realId = null;
    function stop() {
      if (realId === null) return;
      clearInterval(realId);
      realId = null;
    }
    function onVisChange() {
      if (typeof document === "undefined") return;
      if (document.hidden) {
        stop();
      } else {
        stop();
        fn();
        realId = setInterval(fn, delay);
      }
    }
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisChange);
      if (!document.hidden) {
        realId = setInterval(fn, delay);
      }
    }
    _viHandles.set(fakeId, { stop, onVisChange });
    return fakeId;
  }
  function installVisibilityGuard() {
    if (typeof window === "undefined") return;
    const _orig = window.clearInterval.bind(window);
    window.clearInterval = (id) => {
      if (_viHandles.has(id)) {
        const { stop, onVisChange } = _viHandles.get(id);
        stop();
        if (typeof document !== "undefined") {
          document.removeEventListener("visibilitychange", onVisChange);
        }
        _viHandles.delete(id);
      } else {
        _orig(id);
      }
    };
  }

  // apps/dashboard/static/src/shell/snav-cache.js
  var _snavNavStatusCache = {};
  var _SNAV_NAV_STATUS_TTL = 15e3;
  async function snavNavStatusFetch(url) {
    const cached = _snavNavStatusCache[url];
    if (cached && Date.now() - cached.ts < _SNAV_NAV_STATUS_TTL) {
      return cached.data;
    }
    const res = await fetch(url);
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    _snavNavStatusCache[url] = { data, ts: Date.now() };
    return data;
  }
  function snavNavStatusCacheClear(url) {
    if (url) {
      delete _snavNavStatusCache[url];
    } else {
      for (const k of Object.keys(_snavNavStatusCache)) {
        delete _snavNavStatusCache[k];
      }
    }
  }

  // apps/dashboard/static/src/api.js
  var _envPromise = null;
  var _versionPromise = null;
  var _settingsPromise = null;
  function getEnvironment() {
    if (!_envPromise) {
      _envPromise = fetch("/api/environment").then((r) => r.json()).catch((err) => {
        _envPromise = null;
        throw err;
      });
    }
    return _envPromise;
  }
  function getVersion() {
    if (!_versionPromise) {
      _versionPromise = fetch("/api/version", { cache: "no-store" }).then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      }).catch((err) => {
        _versionPromise = null;
        throw err;
      });
    }
    return _versionPromise;
  }
  function getSettings() {
    if (!_settingsPromise) {
      _settingsPromise = fetch("/api/settings").then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      }).catch((err) => {
        _settingsPromise = null;
        throw err;
      });
    }
    return _settingsPromise;
  }
  function invalidateSettings() {
    _settingsPromise = null;
  }

  // apps/dashboard/static/src/device-login.js
  var GH_AUTH_POLL_INTERVAL_MS = 2e3;
  var _timer = null;
  function startGhAuthPoll(pollFn) {
    if (_timer != null) {
      clearInterval(_timer);
      _timer = null;
    }
    pollFn();
    _timer = setInterval(pollFn, GH_AUTH_POLL_INTERVAL_MS);
  }
  function stopGhAuthPoll() {
    if (_timer != null) {
      clearInterval(_timer);
      _timer = null;
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
    if (el) el.textContent = text || "";
  }
  function _psCleanupLog(tag, message, kind, data) {
    const wrap = document.getElementById("ps-cleanup-log");
    const body = document.getElementById("ps-cleanup-log-body");
    if (!body) return;
    if (wrap) wrap.hidden = false;
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
    if (body) body.innerHTML = "";
    const wrap = document.getElementById("ps-cleanup-log");
    if (wrap) wrap.hidden = true;
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
    if (list) list.innerHTML = "";
    const summary = document.getElementById("ps-cln-summary");
    if (summary) summary.textContent = "";
    const review = document.getElementById("ps-cln-review");
    const progress = document.getElementById("ps-cln-progress");
    if (review) review.hidden = false;
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
    if (doneBtn) doneBtn.hidden = true;
    if (cancelBtn) cancelBtn.hidden = false;
  }
  function _psCleanupModalClose() {
    if (_psCleanupBusy) return;
    document.getElementById("ps-cln-backdrop")?.classList.add("hidden");
    document.getElementById("ps-cln-modal")?.classList.add("hidden");
    if (typeof _clearBodyInert === "function") _clearBodyInert();
    _psCleanupModalReset();
  }
  function _psCleanupModalOpen(title) {
    _psCleanupModalReset();
    const titleEl = document.getElementById("ps-cln-title");
    if (titleEl) titleEl.textContent = title || "Cleanup";
    document.getElementById("ps-cln-backdrop")?.classList.remove("hidden");
    document.getElementById("ps-cln-modal")?.classList.remove("hidden");
    if (typeof _setBodyInert === "function") {
      _setBodyInert(["ps-cln-backdrop", "ps-cln-modal"]);
    }
  }
  function _psCleanupModalLoading(message) {
    const progress = document.getElementById("ps-cln-progress");
    const review = document.getElementById("ps-cln-review");
    if (review) review.hidden = true;
    if (!progress) return;
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
    if (confirmBtn) confirmBtn.hidden = true;
    if (cancelBtn) cancelBtn.hidden = true;
  }
  function _psCleanupModalShowReview(opts) {
    const review = document.getElementById("ps-cln-review");
    const progress = document.getElementById("ps-cln-progress");
    if (progress) {
      progress.hidden = true;
      progress.innerHTML = "";
    }
    if (review) review.hidden = false;
    const items = opts.items || [];
    const shown = items.slice(0, 60);
    const more = items.length - shown.length;
    const summaryEl = document.getElementById("ps-cln-summary");
    if (summaryEl) summaryEl.textContent = opts.summary || "";
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
    if (doneBtn) doneBtn.hidden = true;
    if (cancelBtn) cancelBtn.hidden = false;
  }
  function _psCleanupModalShowDone(message) {
    _psCleanupConfirmFn = null;
    _psCleanupBusy = false;
    const summaryEl = document.getElementById("ps-cln-summary");
    if (summaryEl) summaryEl.textContent = message || "Done.";
    const listEl = document.getElementById("ps-cln-list");
    if (listEl) listEl.innerHTML = "";
    const confirmBtn = document.getElementById("ps-cln-confirm");
    const doneBtn = document.getElementById("ps-cln-done");
    const cancelBtn = document.getElementById("ps-cln-cancel");
    if (confirmBtn) confirmBtn.hidden = true;
    if (cancelBtn) cancelBtn.hidden = true;
    if (doneBtn) doneBtn.hidden = false;
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
    if (confirmBtn) confirmBtn.hidden = true;
    if (cancelBtn) cancelBtn.hidden = false;
    unmountProgressActivity("ps-cln-pa-host");
  }
  async function _psCleanupModalConfirm() {
    if (!_psCleanupConfirmFn || _psCleanupBusy) return;
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
    if (_psCleanupBusy) return;
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
    if (!slug) throw new Error("Project not loaded \u2014 switch to Settings again.");
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
    if (!slug) throw new Error("Project not loaded \u2014 switch to Settings again.");
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
          if (log) log("Archiving sprint runtime files\u2026", "step");
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
          if (log) log("Deleting old test files\u2026", "step");
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
    if (_psCleanupBusy) return;
    _psCleanupStatus("");
    _psCleanupLog("branches", "Scanning remote for stale branches\u2026", "step");
    _psCleanupModalOpen("Scan stale branches");
    _psCleanupModalLoading("Scanning remote\u2026");
    _psCleanupBusy = true;
    const btn = document.getElementById("ps-stale-scan-btn");
    if (btn) btn.disabled = true;
    try {
      const resp = await fetch("/scan-stale-branches?repo=" + encodeURIComponent(repo));
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const data = await resp.json();
      const branches = (data.branches || []).map((b) => b.branch || b);
      if (typeof _histScanStale === "function") await _histScanStale();
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
      if (confirmBtn) confirmBtn.hidden = true;
      if (doneBtn) doneBtn.hidden = false;
      _psCleanupBusy = false;
    } catch (e) {
      const msg = e.message || String(e);
      _psCleanupLog("branches", msg, "err");
      _psCleanupStatus("Scan failed: " + msg);
      _psCleanupModalShowError(msg);
    } finally {
      if (btn) btn.disabled = false;
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
      if (!scanResp.ok) throw new Error("HTTP " + scanResp.status);
      const scanData = await scanResp.json();
      const branches = (scanData.branches || []).map((b) => b.branch || b);
      if (!branches.length) return { toDelete: [], skipped: [], branches: [] };
      const dryResp = await fetch("/cleanup-stale-branches", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo, branches, confirm: false })
      });
      if (!dryResp.ok) throw new Error("HTTP " + dryResp.status);
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
          if (log) log("Deleting merged branches\u2026", "step");
          const resp = await fetch("/cleanup-stale-branches", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ repo, branches: plan.branches, confirm: true })
          });
          if (!resp.ok) throw new Error("HTTP " + resp.status);
          const result = await resp.json();
          const deleted = (result.deleted || []).length;
          const failed = (result.failed || []).length;
          if (typeof _histScanStale === "function") await _histScanStale();
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

  // apps/dashboard/static/src/sprint-board/planning-insights.js
  function _smgmtStaleEstimateHtml(stale) {
    if (!stale || stale.length === 0) return "";
    const count = stale.length;
    const ids = escHtml(stale.join(", "));
    return `<div class="pi-stale-row" role="status" aria-live="polite">
    <i class="ti ti-clock-exclamation pi-stale-icon" aria-hidden="true"></i>
    <span class="pi-stale-label">${count} stale estimate${count !== 1 ? "s" : ""}:</span>
    <span class="pi-stale-ids">${ids}</span>
  </div>`;
  }
  async function _smgmtLoadPlanningInsights2(orderedLabels) {
    const repo = _smgmtRepo();
    if (!repo) return;
    await Promise.all(orderedLabels.map(async (label) => {
      if (_smgmtRunningLabels.has(label)) return;
      if (_smgmtFinishedLabels.has(label)) return;
      const slot = document.getElementById(`pi-stale-${label}`);
      if (!slot || slot.dataset.loaded) return;
      try {
        const resp = await fetch(
          `/api/sprints/${encodeURIComponent(label)}/preflight?project=${encodeURIComponent(repo)}`
        );
        if (!resp.ok) return;
        const data = await resp.json();
        const stale = (data.warnings || {}).stale_estimates || [];
        slot.dataset.loaded = "1";
        if (!stale.length) return;
        slot.innerHTML = _smgmtStaleEstimateHtml(stale);
      } catch (_) {
      }
    }));
  }

  // apps/dashboard/static/src/sprint-board/est-vs-actual.js
  function _fmtMin(minutes) {
    if (minutes == null) return "\u2014";
    const m = Math.round(minutes);
    return m >= 60 ? `${(Math.abs(minutes) / 60).toFixed(1)}h` : `${m}m`;
  }
  function _fmtDelta(delta) {
    if (delta == null) return { text: "\u2014", cls: "" };
    const abs = Math.abs(delta);
    const sign = delta < 0 ? "-" : "+";
    const text = `${sign}${abs >= 60 ? (abs / 60).toFixed(1) + "h" : Math.round(abs) + "m"}`;
    const cls = delta < -5 ? "pi-ev-under" : delta > 5 ? "pi-ev-over" : "pi-ev-on";
    return { text, cls };
  }
  function _smgmtEstVsActualSectionHtml(label, data) {
    const tickets = data && data.tickets || [];
    if (!tickets.length) return "";
    const safeLabel = escHtml(label);
    const jsLabel = "'" + String(label).replace(/\\/g, "\\\\").replace(/'/g, "\\'") + "'";
    const rows = tickets.map((t) => {
      const estStr = t.estimated_size ? `${_fmtMin(t.estimated_minutes)} (${escHtml(t.estimated_size)})` : _fmtMin(t.estimated_minutes);
      const actStr = _fmtMin(t.actual_elapsed_minutes);
      const { text: deltaText, cls: deltaCls } = _fmtDelta(t.delta_minutes);
      const statusCls = t.status === "done" ? "pi-ev-status-done" : t.status === "failed" ? "pi-ev-status-fail" : "pi-ev-status-skip";
      return `<div class="pi-ev-row">
      <span class="pi-ev-num">#${t.ticket_id}</span>
      <span class="pi-ev-title" title="${escHtml(t.title || "")}">${escHtml(t.title || "")}</span>
      <span class="pi-ev-est">${estStr}</span>
      <span class="pi-ev-act">${actStr}</span>
      <span class="pi-ev-delta ${deltaCls}">${escHtml(deltaText)}</span>
      <span class="pi-ev-status ${statusCls}">${escHtml(t.status || "\u2014")}</span>
    </div>`;
    }).join("");
    return `<div class="pi-ev-section" id="pi-ev-section-${safeLabel}">
    <button class="pi-ev-toggle-btn" type="button"
            onclick="_smgmtToggleEstVsActual(${jsLabel})"
            aria-expanded="false"
            aria-controls="pi-ev-content-${safeLabel}">
      <i class="ti ti-chart-bar" aria-hidden="true"></i>
      Estimate vs Actual
      <i class="ti ti-chevron-down pi-ev-chevron" aria-hidden="true"></i>
    </button>
    <div class="pi-ev-content" id="pi-ev-content-${safeLabel}" style="display:none">
      <div class="pi-ev-header-row">
        <span class="pi-ev-num">Ticket</span>
        <span class="pi-ev-title">Title</span>
        <span class="pi-ev-est">Est</span>
        <span class="pi-ev-act">Actual</span>
        <span class="pi-ev-delta">Delta</span>
        <span class="pi-ev-status">Status</span>
      </div>
      ${rows}
    </div>
  </div>`;
  }
  function _smgmtToggleEstVsActual(label) {
    const content = document.getElementById(`pi-ev-content-${label}`);
    const btn = document.querySelector(`#pi-ev-section-${label} .pi-ev-toggle-btn`);
    const chevron = document.querySelector(`#pi-ev-section-${label} .pi-ev-chevron`);
    if (!content) return;
    const isExpanded = content.style.display !== "none";
    content.style.display = isExpanded ? "none" : "block";
    if (btn) btn.setAttribute("aria-expanded", String(!isExpanded));
    if (chevron) {
      chevron.classList.toggle("ti-chevron-down", isExpanded);
      chevron.classList.toggle("ti-chevron-up", !isExpanded);
    }
  }
  async function _smgmtLoadEstVsActual2(orderedLabels) {
    const repo = _smgmtRepo();
    if (!repo) return;
    await Promise.all(orderedLabels.map(async (label) => {
      if (_smgmtRunningLabels.has(label)) return;
      if (!_smgmtFinishedLabels.has(label)) return;
      const slot = document.getElementById(`pi-ev-${label}`);
      if (!slot || slot.dataset.loaded) return;
      try {
        const resp = await fetch(
          `/api/sprints/${encodeURIComponent(label)}/estimate-vs-actual?project=${encodeURIComponent(repo)}`
        );
        slot.dataset.loaded = "1";
        if (!resp.ok) return;
        const data = await resp.json();
        const html = _smgmtEstVsActualSectionHtml(label, data);
        if (html) slot.innerHTML = html;
      } catch (_) {
      }
    }));
  }

  // apps/dashboard/static/src/sprint-board/health-strip.js
  function _fmtPct(rate) {
    return Math.round((rate || 0) * 100) + "%";
  }
  function _fmtDur(minutes) {
    minutes = minutes || 0;
    return minutes < 60 ? Math.round(minutes) + "m" : (minutes / 60).toFixed(1) + "h";
  }
  function _sHealthBuildHtml(data) {
    const fpr = data && data.first_pass_rate || {};
    const rwr = data && data.rework_rate || {};
    const thr = data && data.throughput || {};
    if ((fpr.total_completed || 0) <= 0) return null;
    const fprPct = _fmtPct(fpr.rate);
    const rwrPct = _fmtPct(rwr.rate);
    const durStr = _fmtDur(thr.avg_sprint_length_minutes);
    return '<span class="shs-stat"><span class="shs-val">' + fprPct + '</span><span class="shs-label">first-pass</span></span><span class="shs-stat"><span class="shs-val">' + rwrPct + '</span><span class="shs-label">rework</span></span><span class="shs-stat"><span class="shs-val">' + durStr + '</span><span class="shs-label">avg sprint</span></span>';
  }
  function _sHealthStripRender(data) {
    const el = document.getElementById("sprint-health-strip");
    if (!el) return;
    const html = _sHealthBuildHtml(data);
    if (html === null) {
      el.hidden = true;
      return;
    }
    el.innerHTML = html;
    el.hidden = false;
  }
  function sprintHealthStripInit2(slug) {
    if (!slug) return;
    if (typeof window !== "undefined" && window._anlHealthData) {
      _sHealthStripRender(window._anlHealthData);
      return;
    }
    if (typeof window !== "undefined" && window._anlHealthPromise) return;
    const url = "/api/projects/" + encodeURIComponent(slug) + "/analytics/metrics";
    const p = fetch(url).then(function(r) {
      return r.ok ? r.json() : null;
    }).then(function(d) {
      if (d) {
        if (typeof window !== "undefined") window._anlHealthData = d;
        _sHealthStripRender(d);
      }
      return d;
    }).catch(function() {
      return null;
    });
    if (typeof window !== "undefined") window._anlHealthPromise = p;
  }

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
    if (globalThis._commanderFeatures && globalThis._commanderFeatures.signoff !== true) return;
    const repo = _smgmtRepo();
    if (!repo) return;
    let labels = [];
    try {
      const res = await fetch(
        `/api/sprints/pending-signoff?project=${encodeURIComponent(repo)}`
      );
      if (!res.ok) return;
      const data = await res.json();
      labels = data.labels || [];
    } catch {
      return;
    }
    for (const label of labels) {
      const card = document.getElementById(`smgmt-card-${label}`);
      if (!card) continue;
      card.classList.add("smgmt-pending-signoff");
      if (card.querySelector(".smgmt-pending-signoff-badge")) continue;
      const header = card.querySelector(".smgmt-sprint-header, .sc-header");
      if (!header) continue;
      const badge = document.createElement("span");
      badge.className = "smgmt-pending-signoff-badge";
      badge.textContent = "Pending sign-off";
      badge.setAttribute("title", "Awaiting sign-off before this sprint goes live");
      header.appendChild(badge);
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
  function _histIsLoading() {
    return !!_histLedgerInflight;
  }
  function _histErrorHtml() {
    return `<div class="hist-ledger-error" role="alert">
    <i class="ti ti-alert-circle" aria-hidden="true"></i>
    <span>Could not load sprint history.</span>
    <button class="hist-ledger-retry" onclick="_histForceRefresh()">
      <i class="ti ti-refresh"></i> Retry
    </button>
  </div>`;
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
    if (secs == null || isNaN(secs)) return "\u2014";
    secs = Math.round(secs);
    if (secs < 60) return secs + "s";
    const m = Math.floor(secs / 60), s = secs % 60;
    if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
    const h = Math.floor(m / 60), mm = m % 60;
    return mm ? `${h}h ${mm}m` : `${h}h`;
  }
  function _histFmtTokens(n) {
    if (n == null || isNaN(n)) return "0";
    if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
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
    if (st === "closed") return { cls: "crashed", label: "CRASHED" };
    if (iss.time_spent != null) return { cls: "uat", label: "OPEN \xB7 UAT" };
    return { cls: "notrun", label: "NOT RUN" };
  }
  function _histSprintShowsBinaryIssues(s) {
    if (!s) return false;
    if ((s.end_reason || "").toLowerCase() === "queued") return false;
    if (_histSprintFailed(s)) return true;
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
    if (!issues.length) return "";
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
      if (n) return n;
    }
    const g = _histStaleBySprint[s.label];
    if (g && g.count) return g.count;
    return 0;
  }
  function _histHeadStatsHtml(s, group) {
    const parts = [];
    const progress = _histProgressText(s, group);
    if (progress) parts.push(progress);
    const stats = _histRunStats[s.label];
    const agentSecs = stats && stats.has_runs && stats.agent_total_seconds != null ? stats.agent_total_seconds : null;
    if (agentSecs != null) {
      parts.push(_histFmtSecs(agentSecs) + " agent");
    } else if (s.duration != null) {
      parts.push(_histFmtSecs(s.duration));
    }
    const looseN = _histLooseEndCount(s);
    if (looseN) parts.push(looseN + " loose end" + (looseN !== 1 ? "s" : ""));
    if (!parts.length) return "";
    return '<span class="hist-head-stats">' + parts.map((p) => '<span class="hist-head-stat">' + escHtml(p) + "</span>").join("") + "</span>";
  }
  function _histIssueLogUrl(s, issueNum) {
    const base = _histLogsUrl(s);
    if (issueNum == null) return base;
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
    if (iss.title) return String(iss.title);
    const tid = iss.ticket_id;
    if (titleMap && tid != null) {
      const hit = titleMap.get(tid) || titleMap.get(String(tid));
      if (hit) return String(hit);
    }
    try {
      const tickets = s && s.label && _smgmtBySprint[s.label] || [];
      const hit = tickets.find((t) => String(t.number) === String(tid));
      if (hit && hit.title) return String(hit.title);
    } catch (_) {
    }
    try {
      for (const row of _histLedgerData || []) {
        const hit = (row.issues || []).find(
          (i) => String(i.ticket_id) === String(tid) && i.title
        );
        if (hit) return String(hit.title);
      }
    } catch (_) {
    }
    return "";
  }
  function _histBuildLineageTitleMap(group) {
    const map = /* @__PURE__ */ new Map();
    if (!group) return map;
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
    if (!group || ticketId == null) return null;
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
    if (!group) return issues;
    return issues.filter((iss) => {
      if (iss.ticket_id == null) return true;
      const owner = _histCanonicalOwnerLabel(iss.ticket_id, group);
      return owner === s.label;
    });
  }
  function _histSprintFailed(s) {
    const st = (s.lifecycle_state || "").toLowerCase();
    if (st === "failed") return true;
    if (st !== "needs_rework") return false;
    const er = (s.end_reason || "").toLowerCase();
    if (er === "natural" || er === "merge_sprint") return false;
    if (er === "queued") return false;
    const failed = Array.isArray(s.failed_tickets) ? s.failed_tickets : [];
    if (failed.length) return true;
    const issues = Array.isArray(s.issues) ? s.issues : [];
    if (issues.length && issues.every(
      (i) => (i.state || "").toLowerCase() === "merged" || (i.agent_status || "").toLowerCase() === "completed"
    ))
      return false;
    return true;
  }
  function _histPartialChildrenHtml(s) {
    const state = (s.lifecycle_state || "").toLowerCase();
    if (state !== "partial_finished") return "";
    const children = Array.isArray(s.partial_children) ? s.partial_children : [];
    if (!children.length) return "";
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
    if (!label) return;
    _histExpanded.add(label);
    _histRenderLedger(_histLedgerData);
    const el = document.querySelector(
      `.hist-card[data-label="${CSS.escape(label)}"]`
    );
    if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  function _histRepo(s) {
    const cached = _cachedFullRepo[_slug];
    if (cached) return cached;
    const p = s && s.project ? String(s.project) : "";
    return p.includes("/") ? p : "";
  }
  function _histPrUrl(s) {
    if (s.pr_number == null) return "";
    const repo = _histRepo(s);
    if (!repo) return "";
    return `https://github.com/${repo}/pull/${s.pr_number}`;
  }
  function _histSummaryIssueUrl(s) {
    if (s.summary_issue_url) return s.summary_issue_url;
    if (s.summary_issue_num == null) return "";
    const repo = _histRepo(s);
    if (!repo) return "";
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
    if (!split.length) return "";
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
    if (!s || !s.label) return false;
    const st = (s.lifecycle_state || "").toLowerCase();
    if (_histIsLocked(st)) return false;
    return st === "needs_rework" || st === "failed" || st === "ready_to_merge" || st === "running" || st === "draft" || st === "planned" || st === "partial_finished";
  }
  var _histCollapseDefaultsApplied = /* @__PURE__ */ new Set();
  function _histAutoExpandRecent(groups) {
    const _expand = (s) => {
      if (!_histShouldAutoExpand(s)) return;
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
      if (i >= _histFoldSize) continue;
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
      chips.push(
        _histStatChip(
          "ti-robot",
          "agent time",
          _histFmtSecs(stats.agent_total_seconds)
        )
      );
    }
    if (tokens != null) {
      let tokVal = _histFmtTokens(tokens);
      if (hasRuns && stats.token_cost_usd != null)
        tokVal += " \u2248 $" + Number(stats.token_cost_usd).toFixed(2);
      else tokVal += " tok";
      chips.push(_histStatChip("ti-coin", "tokens", tokVal));
    }
    if (hasRuns) {
      if (stats.fix_round_count > 0) {
        const refs = (stats.fix_round_tickets || []).map((n) => "#" + n).join(", ");
        const word = stats.fix_round_count === 1 ? "fix round" : "fix rounds";
        chips.push(
          _histStatChip(
            "ti-refresh",
            "",
            stats.fix_round_count + " " + word + (refs ? " (" + refs + ")" : ""),
            "stat-chip--fix"
          )
        );
      }
      if (stats.slowest_ticket) {
        chips.push(
          _histStatChip(
            "ti-hourglass-low",
            "slowest",
            "#" + stats.slowest_ticket.ticket + " \xB7 " + _histFmtSecs(stats.slowest_ticket.seconds)
          )
        );
      }
      if (stats.parallel_saved_seconds != null) {
        chips.push(
          _histStatChip(
            "ti-arrows-split",
            "parallel saved",
            "~" + _histFmtSecs(stats.parallel_saved_seconds)
          )
        );
      }
      if (stats.coder_backend_split && stats.coder_backend_split.cline_count > 0) {
        const bs = stats.coder_backend_split;
        const parts = [];
        if (bs.cline_count > 0)
          parts.push(
            "cline: " + bs.cline_count + " \xB7 " + _histFmtSecs(bs.cline_seconds)
          );
        if (bs.claude_code_count > 0)
          parts.push(
            "claude-code: " + bs.claude_code_count + " \xB7 " + _histFmtSecs(bs.claude_code_seconds)
          );
        if (parts.length)
          chips.push(_histStatChip("ti-server", "backend", parts.join(" | ")));
      }
      if (sprintFailed && stats.crash) {
        const failed = (Array.isArray(s.failed_tickets) ? s.failed_tickets : []).find((ft) => ft.ticket_id === stats.crash.ticket);
        const reason = failed ? String(failed.failure_reason || "") : "";
        const crashAgent = /tester/i.test(reason) ? "tester" : "coder";
        const tail = reason ? " \xB7 " + reason.split("\n")[0].slice(0, 40) : "";
        chips.push(
          _histStatChip(
            "ti-alert-triangle",
            "crash",
            "#" + stats.crash.ticket + " \xB7 " + crashAgent + tail,
            "stat-chip--crash"
          )
        );
      }
    }
    const splitHtml = hasRuns ? _histSplitBarHtml(stats) : "";
    return `<div class="stats" data-stats-label="${escHtml(s.label || "")}">
    <div class="stat-chips">${chips.join("")}</div>
    ${splitHtml}
  </div>`;
  }
  function _histSeedRunStatsFromInline(sprints) {
    if (!Array.isArray(sprints)) return;
    for (const s of sprints) {
      if (s && s.label && s.run_stats != null && !(s.label in _histRunStats)) {
        _histRunStats[s.label] = s.run_stats;
      }
    }
  }
  async function _histLoadRunStats(label) {
    if (label in _histRunStats) return;
    const repo = _cachedFullRepo[_slug];
    try {
      const url = "/api/sprints/" + encodeURIComponent(label) + "/run-stats" + (repo ? "?project=" + encodeURIComponent(repo) : "");
      const resp = await fetch(url);
      if (!resp.ok) return;
      _histRunStats[label] = await resp.json();
      _histScheduleLedgerRender();
    } catch (_) {
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
    const el = document.getElementById("hist-ledger");
    if (!el || _histLedgerData && _histLedgerData.length) return;
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
    if (num == null) return "";
    const repo = _cachedFullRepo[_slug] || "";
    if (!repo) return "";
    return `https://github.com/${repo}/issues/${num}`;
  }
  function _histPostSprintHtml(s) {
    const ps = s.post_sprint;
    if (!ps) return "";
    const doc = ps.documenter;
    const rev = ps.reviewer;
    const docRan = doc && doc.status && doc.status !== "skipped";
    const revRan = rev && rev.status && rev.status !== "skipped";
    if (!docRan && !revRan) return "";
    let rows = "";
    if (doc) {
      let body = "";
      if (doc.status === "skipped") {
        body = '<span class="ps-skipped">Skipped \u2014 nothing merged</span>';
      } else if (doc.status === "failed") {
        body = '<span class="ps-skipped">Documenter failed</span>';
      } else if ((doc.files_touched || []).length) {
        body = (doc.files_touched || []).map((f) => `<code class="ps-file">${escHtml(String(f))}</code>`).join("");
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
          counts.push(
            rev.blockers + " blocker" + (rev.blockers !== 1 ? "s" : "")
          );
        if (rev.suggestions)
          counts.push(
            rev.suggestions + " suggestion" + (rev.suggestions !== 1 ? "s" : "")
          );
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
    if (!rows) return "";
    const note = ps.note || "Agents ran after ticket work finished";
    return `<div class="hist-post-sprint">
    <div class="ps-head"><i class="ti ti-clock-play"></i> ${escHtml(note)}</div>
    ${rows}
  </div>`;
  }
  function _histReconcileHtml(s) {
    const r = s.reconciliation;
    if (!r || !Array.isArray(r.checks) || !r.checks.length) return "";
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
    if (!stats) return 0;
    if (stats.fix_round_seconds != null)
      return Math.max(0, Number(stats.fix_round_seconds) || 0);
    let total = 0;
    for (const t of stats.tickets || []) {
      for (const seg of t.segments || []) {
        if (seg.fix_round) total += seg.duration || 0;
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
    if (!stats || !stats.has_runs) return [];
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
    if (secs == null) return "";
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
    if (!stats || !stats.has_runs) return "";
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
    if (!rows.length) return "";
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
      chips.push(
        `<span class="hist-metric-chip">wall ${_histFmtSecs(wall)}</span>`
      );
    if (hasRuns) {
      chips.push(
        `<span class="hist-metric-chip">agent time ${_histFmtSecs(stats.agent_total_seconds)}</span>`
      );
    }
    if (tokens != null) {
      chips.push(
        `<span class="hist-metric-chip">tokens ${_histFmtTokens(tokens)}</span>`
      );
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
    if (!tickets.length) return "";
    const scale = Math.max(1, stats.wall_seconds || 0);
    const rows = tickets.map((t) => {
      const dur = Math.max(0, (t.end || 0) - (t.start || 0));
      const fixN = (t.segments || []).filter((seg) => seg.fix_round).length;
      let durLabel = _histFmtSecs(
        dur || t.segments?.reduce((n, seg) => n + (seg.duration || 0), 0)
      );
      if (fixN) durLabel += " \xB7 fix";
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
    if (!stats || !stats.tickets) return 0;
    const hit = stats.tickets.find((t) => String(t.ticket) === String(issueNum));
    if (!hit) return 0;
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
    if (fixN) dur += ` \xB7 ${fixN} fix`;
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
    if (!issues.length) return "";
    const stats = _histRunStats[s.label];
    return `<div class="hist-issue-rows">${issues.map((i) => _histDoneIssueRowHtml(i, s, stats, titleMap)).join("")}</div>`;
  }
  function _histCardShowsDoneSummary(s, group) {
    const issues = group ? _histIssuesForDisplay(s, group) : Array.isArray(s.issues) ? s.issues : [];
    if (_histSprintFailed(s)) return issues.length > 0;
    if (issues.length) return true;
    const state = (s.lifecycle_state || "").toLowerCase();
    return state === "ready_to_merge" || state === "completed" || state === "running";
  }
  function _histCardOutcomeHtml(s, group) {
    const issues = group ? _histIssuesForDisplay(s, group) : Array.isArray(s.issues) ? s.issues : [];
    if (!_histCardShowsDoneSummary(s, group) && !issues.length) return "";
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
    if (!sub) return "";
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
    if (isLineageParent) cls.push("hist-lineage-parent");
    if (displayState === "ready_to_merge") cls.push("ready");
    if (displayState === "completed") cls.push("settled");
    if (expanded) cls.push("expanded");
    const display = sprintLabelDisplay(s.label);
    const fromLine = !isLineageParent && _histIsChild(s.label) ? _histParentFromLabel(s.label) : "";
    const chev = expanded ? "ti-chevron-down" : "ti-chevron-right";
    const recoveryBtn = _histRecoveryBtnHtml(s);
    const deleteBtn = _histDeleteBtnHtml(s);
    const secondaryLinks = _histSecondaryLinksHtml(s);
    const bulkBtn = opts.bulkCompleteBtn || "";
    const headRight = `<span class="hist-child-head-right">${secondaryLinks}${recoveryBtn}${deleteBtn}${bulkBtn}</span>`;
    if (expanded && !(s.label in _histRunStats)) _histLoadRunStats(s.label);
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
    if (_histIsLocked(s.lifecycle_state)) return "";
    const state = (s.lifecycle_state || "").toLowerCase();
    const displayIssues = group ? _histIssuesForDisplay(s, group) : Array.isArray(s.issues) ? s.issues : [];
    if (_histSprintFailed(s)) {
      const failed = Array.isArray(s.failed_tickets) ? s.failed_tickets : [];
      const issues = displayIssues;
      const sprintReason = s.failure_reason || s.end_reason;
      if (!failed.length && !sprintReason) return "";
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
      if (!unfinished.length) return "";
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
    if (!r || !Array.isArray(r.checks) || !r.checks.length) return "";
    const passed = r.checks.filter((c) => !!c.ok);
    if (!passed.length) return "";
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
    if (_histIsLocked(s.lifecycle_state)) return "";
    const state = (s.lifecycle_state || "").toLowerCase();
    const lbl = escHtml(s.label || "");
    const rawLabel = s.label || "";
    const reconcileBtn = `<button type="button" class="hist-head-btn hist-head-btn--reconcile"
      onclick="event.stopPropagation();smgmtReconcileSprint('${lbl}')"
      title="Reconcile this sprint's DB state against GitHub truth">
      <i class="ti ti-git-compare"></i> Reconcile</button>`;
    if (_histSprintFailed(s) || state === "needs_rework" || state === "failed" || state === "cancelled") {
      return reconcileBtn;
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
      return `${reconcileBtn}<button type="button" class="hist-head-btn hist-head-btn--bulk" ${runDisabled} ${runTitle}
      onclick="event.stopPropagation();smgmtRunSprint('${lbl}')">
      <i class="ti ti-player-play"></i> Run</button>`;
    }
    if (state === "running") return "";
    return reconcileBtn;
  }
  function _histDeleteBtnHtml(s) {
    if (_histIsLocked(s.lifecycle_state)) return "";
    const state = (s.lifecycle_state || "").toLowerCase();
    const actionable = /* @__PURE__ */ new Set([
      "needs_rework",
      "failed",
      "cancelled",
      "ready_to_merge",
      "completed",
      "partial_finished"
    ]);
    if (!actionable.has(state)) return "";
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
    if (locked) cls.push("locked");
    if (child) cls.push("child");
    if (expanded) cls.push("expanded");
    const lifecycle = (s.lifecycle_state || "").toLowerCase();
    if (lifecycle === "completed") cls.push("settled");
    if (lifecycle === "ready_to_merge") cls.push("ready");
    const display = typeof sprintLabelDisplay === "function" ? sprintLabelDisplay(s.label) : s.label || "";
    const chev = expanded ? "ti-chevron-down" : "ti-chevron-right";
    const lbl = escHtml(s.label || "");
    const headStatsHtml = _histHeadStatsHtml(s);
    const recoveryBtn = _histRecoveryBtnHtml(s);
    const bulkBtn = opts.bulkCompleteBtn || "";
    const deleteBtn = _histDeleteBtnHtml(s);
    const secondaryLinks = _histSecondaryLinksHtml(s);
    const headRight = secondaryLinks || deleteBtn || bulkBtn || recoveryBtn ? `<span class="hist-card-head-right">${secondaryLinks}${recoveryBtn}${deleteBtn}${bulkBtn}</span>` : "";
    if (expanded && !(s.label in _histRunStats)) _histLoadRunStats(s.label);
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
    if (!m) return { base: label || "", sub: 0, baseNum: 0 };
    return {
      base: `sprint-${m[1]}`,
      sub: m[2] ? parseInt(m[2], 10) : 0,
      baseNum: parseInt(m[1], 10)
    };
  }
  function _histGroupMembers(group) {
    const out = [];
    if (group.baseSprint) out.push(group.baseSprint);
    if (group.children) out.push(...group.children);
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
      if (sub === 0) g.baseSprint = s;
      else g.children.push(s);
      g.order = Math.min(g.order, i);
    }
    const _UNSETTLED = /* @__PURE__ */ new Set([
      "needs_rework",
      "failed",
      "cancelled",
      "ready_to_merge",
      "running"
    ]);
    const _groupUnsettled = (g) => [g.baseSprint, ...g.children || []].filter(Boolean).some((s) => _UNSETTLED.has((s.lifecycle_state || "").toLowerCase()));
    groupOrder.sort((a, b) => {
      const ua = _groupUnsettled(byBase.get(a)) ? 0 : 1;
      const ub = _groupUnsettled(byBase.get(b)) ? 0 : 1;
      if (ua !== ub) return ua - ub;
      return byBase.get(a).order - byBase.get(b).order;
    });
    return groupOrder.map((baseLabel) => {
      const g = byBase.get(baseLabel);
      g.children.sort(
        (a, b) => _histLabelParts(a.label).sub - _histLabelParts(b.label).sub
      );
      return { baseLabel, baseSprint: g.baseSprint, children: g.children };
    });
  }
  function _histGroupNeedsBulkComplete(group) {
    const children = group.children || [];
    if (!children.length || !group.baseSprint) return false;
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
    if (!children.length) return false;
    return children.every((s, i) => {
      if (_histChildRunFinished(s)) return true;
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
    if (!group.children?.length || !group.baseSprint) return "";
    if (!_histGroupNeedsBulkComplete(group)) return "";
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
      if (_fst === "needs_rework" || _fst === "failed") failed += 1;
      const acc = s.estimate_accuracy;
      if (acc != null && !isNaN(acc)) {
        accSum += Number(acc);
        accN += 1;
      }
    });
    return {
      done,
      failed,
      avgAcc: accN ? accSum / accN : null,
      count: group.length
    };
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
    if (_histFoldExpanded.has(id)) _histFoldExpanded.delete(id);
    else _histFoldExpanded.add(id);
    _histRenderLedger(_histLedgerData);
  }
  function _histToolbarHtml() {
    const note = _histShowClosed ? `<span class="hist-toolbar-note"><i class="ti ti-history"></i> Full archive \u2014 older sprint groups collapse into numbered folds; click to expand.</span>` : `<span class="hist-toolbar-note"><i class="ti ti-inbox"></i> Action inbox \u2014 sprints needing Complete, Re-run, or Bulk complete. Lineage groups stay together (e.g. Sprint 98 with 98.1).</span>`;
    const signOffBtn = _histShowClosed ? "" : `<button type="button" class="btn-ghost hist-bulk-signoff-btn" id="hist-bulk-signoff-btn" onclick="_histBulkSignOff()" title="Complete every ready-to-merge sprint in this inbox"><i class="ti ti-circle-check"></i> Sign off all ready</button>`;
    return `<div class="hist-toolbar">${note}${signOffBtn}</div>`;
  }
  async function _histScanStale2() {
    const repo = _cachedFullRepo[_slug];
    if (!repo) return;
    const btn = document.getElementById("ps-stale-scan-btn");
    if (btn) {
      btn.disabled = true;
    }
    try {
      const resp = await fetch(
        "/scan-stale-branches?repo=" + encodeURIComponent(repo)
      );
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
    if (!repo) return;
    const s = (_histLedgerData || []).find((x) => x.label === label);
    if (!s || !s.reconciliation) return;
    const staleCheck = (s.reconciliation.checks || []).find(
      (c) => !c.ok && c.name === "stale_labels"
    );
    if (!staleCheck) return;
    const tickets = Array.isArray(staleCheck.tickets) ? staleCheck.tickets : [];
    try {
      const resp = await fetch(
        "/api/sprints/" + encodeURIComponent(label) + "/clear-stale-labels",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ project: repo, tickets })
        }
      );
      if (resp.ok) {
        await _histLoadLedger2();
      }
    } catch (_) {
    }
  }
  async function _histCleanupStale(label) {
    const repo = _cachedFullRepo[_slug];
    const g = _histStaleBySprint[label];
    if (!repo || !g) return;
    const branches = g.branches || [];
    let plan;
    try {
      const resp = await fetch("/cleanup-stale-branches", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo, branches, confirm: false })
      });
      if (!resp.ok) return;
      plan = await resp.json();
    } catch (_) {
      return;
    }
    const toDelete = plan.to_delete || [];
    const skipped = plan.skipped_unmerged || [];
    if (!toDelete.length && !skipped.length) return;
    let msg = toDelete.length ? "Delete " + toDelete.length + " merged branch" + (toDelete.length !== 1 ? "es" : "") + "?\n\n" + toDelete.join("\n") : "No merged branches to delete for this sprint.";
    if (skipped.length) {
      msg += "\n\nSkipped (unmerged \u2014 never deleted):\n" + skipped.join("\n");
    }
    if (!confirm(msg)) return;
    if (!toDelete.length) return;
    try {
      const resp = await fetch("/cleanup-stale-branches", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo, branches, confirm: true })
      });
      if (!resp.ok) return;
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
    if (!el) return;
    if (!sprints || !sprints.length) {
      const emptyMsg = _histShowClosed ? "No sprint history yet \u2014 finished and deleted sprints appear here." : "Inbox clear \u2014 no sprints need action. Toggle Show completed for the archive.";
      el.innerHTML = `<div class="hist-ledger-empty" role="status">
      <i class="ti ti-inbox-off" aria-hidden="true"></i>
      <span>${emptyMsg}</span>
    </div>`;
      return;
    }
    let groups = _histGroupSprints(sprints);
    if (!_histShowClosed) {
      groups = groups.filter(_histGroupHasActionable);
      if (!groups.length) {
        el.innerHTML = `<div class="hist-ledger-empty" role="status">
        <i class="ti ti-inbox-off" aria-hidden="true"></i>
        <span>Inbox clear \u2014 no sprints need action. Toggle Show completed for the archive.</span>
      </div>`;
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
  function _histPrefetchLedger(repo) {
    if (!repo) return;
    const hasData = (_histLedgerData || []).length > 0;
    const fresh = repo === _histLedgerCacheRepo && Date.now() - _histLedgerCacheAt < _HIST_LEDGER_TTL_MS && hasData;
    if (fresh || _histLedgerInflight) return;
    _histLoadLedger2(repo, { background: true });
  }
  async function _histLoadLedger2(repo, opts) {
    opts = opts || {};
    const background = opts.background === true;
    if (!repo) {
      if (!background) _histRenderLedger([]);
      return;
    }
    const el = document.getElementById("hist-ledger");
    const force = opts.force === true;
    const hasCache = repo === _histLedgerCacheRepo && (_histLedgerData || []).length > 0;
    const fresh = !force && hasCache && Date.now() - _histLedgerCacheAt < _HIST_LEDGER_TTL_MS;
    if (fresh) {
      if (!background && el && !el.querySelector(".hist-card, .hist-sprint-group, .hist-fold")) {
        _histRenderLedger(_histLedgerData);
      }
      return;
    }
    if (_histLedgerInflight) {
      if (!background) {
        if (!hasCache) _histShowLedgerSkeleton();
        else _histRenderLedger(_histLedgerData);
      }
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
            if (!isNaN(fs) && fs > 0) _histFoldSize = fs;
            const ttlMin = parseFloat(settings.history_cache_ttl_min);
            if (!isNaN(ttlMin) && ttlMin > 0)
              _HIST_LEDGER_TTL_MS = ttlMin * 6e4;
          } catch (_) {
          }
        }
        if (!resp.ok) {
          if (!hasCache && el && !background) {
            el.innerHTML = _histErrorHtml();
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
          el.innerHTML = _histErrorHtml();
        }
      } finally {
        if (_histLedgerInflight === loadPromise) _histLedgerInflight = null;
      }
    })();
    _histLedgerInflight = loadPromise;
    await loadPromise;
  }
  function _histSyncShowClosedBtn() {
    const btn = document.getElementById("hist-show-closed-btn");
    if (!btn) return;
    btn.innerHTML = _histShowClosed ? '<i class="ti ti-eye-off"></i> Active only' : '<i class="ti ti-history"></i> Show completed';
    btn.title = _histShowClosed ? "Show only the action inbox (sprints needing you)" : "Load the full closed-sprint archive";
  }
  function _histToggleShowClosed() {
    _histShowClosed = !_histShowClosed;
    _histSyncShowClosedBtn();
    const repo = _cachedFullRepo[_slug];
    if (repo) _histLoadLedger2(repo, { force: true });
  }
  function _histSetTtlMin(min) {
    const m = parseFloat(min);
    if (!isNaN(m) && m > 0) _HIST_LEDGER_TTL_MS = m * 6e4;
  }
  function _histForceRefresh() {
    _histResetLedgerCache();
    for (const k of Object.keys(_histRunStats)) delete _histRunStats[k];
    _histStaleBySprint = {};
    const repo = _cachedFullRepo[_slug];
    if (repo) _histLoadLedger2(repo, { force: true });
  }
  function _histBulkSignOffTargets(sprints) {
    const groups = _histGroupSprints(sprints || []).filter(
      _histGroupHasActionable
    );
    const targets = [];
    const skipLabels = /* @__PURE__ */ new Set();
    for (const g of groups) {
      const members = _histGroupMembers(g);
      const rtm = members.filter(
        (s) => (s.lifecycle_state || "").toLowerCase() === "ready_to_merge"
      );
      if (!rtm.length) continue;
      const useBulk = (g.children || []).length && g.baseLabel && _histGroupNeedsBulkComplete(g) && _histChildSprintsAllCompleted(g) && !_histChildSprintsStillRunning(g);
      if (useBulk) {
        targets.push({ kind: "bulk", label: g.baseLabel });
        for (const s of members) skipLabels.add(s.label);
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
      if (pa.baseNum !== pb.baseNum) return pa.baseNum - pb.baseNum;
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
    if (!confirm(
      `Sign off ${targets.length} sprint(s)? Each will run Complete (merge + close UAT).

${listing}`
    )) {
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
            throw new Error(
              "Bulk complete helper unavailable \u2014 refresh the page."
            );
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
          _smgmtBoardLog(
            `\u2717 ${sprintLabelDisplay(label)}: ${failed.message}`,
            "err"
          );
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
          if (repo) _histLoadLedger2(repo, { force: true });
          else _histForceRefresh();
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
    if (cat === "UAT") return "rr-cat-uat";
    if (cat === "SIT") return "rr-cat-sit";
    if (cat === "needs-rework") return "rr-cat-rework";
    if (cat === "sprint-summary") return "rr-cat-summary";
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
    if (!loading) return;
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
    if (confirmBtn) confirmBtn.classList.add("hidden");
    if (cancelBtn) cancelBtn.textContent = "Close";
    if (retryBtn) retryBtn.classList.add("hidden");
  }
  function _fsUpdateProgress(snap) {
    const slot = _fsProgressSlot();
    if (!slot || slot.classList.contains("hidden")) return;
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
    if (cancelBtn) cancelBtn.textContent = "Close";
    if (retryBtn) retryBtn.classList.add("hidden");
    _fsActiveJob = null;
    setTimeout(() => loadSprintMgmt(), 1500);
  }
  function _fsHandleError(snap) {
    _fsUpdateProgress(snap);
    const cancelBtn = document.getElementById("fs-cancel-btn");
    const retryBtn = document.getElementById("fs-retry-btn");
    if (cancelBtn) cancelBtn.textContent = "Close";
    if (retryBtn) retryBtn.classList.remove("hidden");
  }
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
        if (preview.conflict_error) throw new Error(preview.conflict_error);
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
          if (snap.ping) return;
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
    if (!_fsActiveJob) return;
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
      if (retryBtn) retryBtn.classList.remove("hidden");
    }
  }
  async function smgmtFinishSprint(label) {
    const repo = _smgmtRepo();
    if (!repo) return;
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
    if (cancelBtn) cancelBtn.textContent = "Cancel";
    if (retryBtn) retryBtn.classList.add("hidden");
    const progSlot = _fsProgressSlot();
    if (progSlot) progSlot.classList.add("hidden");
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
        if (reworkCheckbox) reworkCheckbox.checked = false;
      } else {
        warningEl.classList.add("hidden");
      }
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
    if (!loading) return;
    loading.innerHTML = renderProgressActivity(
      {
        status: "running",
        mode: "indeterminate",
        current: current || "Loading preview\u2026"
      },
      {
        id: "bc-preview-pa",
        hideLog: true
      }
    );
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
    if (cat === "UAT") return "rr-cat-uat";
    if (cat === "SIT") return "rr-cat-sit";
    if (cat === "needs-rework") return "rr-cat-rework";
    if (cat === "sprint-summary") return "rr-cat-summary";
    return "rr-cat-queued";
  }
  function _bcSelectAll(checked) {
    document.querySelectorAll("#bc-ticket-list input[type=checkbox]").forEach((cb) => {
      cb.checked = checked;
    });
  }
  async function smgmtBulkCompleteSprint(label) {
    const repo = _smgmtRepo();
    if (!repo) return;
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
        listEl.innerHTML = groups.map(
          (g) => `<div style="font-size:11px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.04em;margin:8px 0 2px">${escHtml(sprintLabelDisplay(g.label))} \xB7 ${g.tickets.length}</div>` + g.tickets.map(_ticketRow).join("")
        ).join("");
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
      if (confirmBtn) confirmBtn.disabled = false;
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
    if (!repo) throw new Error("No project loaded");
    const parts = repo.split("/");
    const owner = parts[0];
    const repoName = parts.slice(1).join("/");
    const preview = await _bcFetchPreview(owner, repoName, label);
    const order = (preview.complete_order || []).slice();
    if (!order.length) throw new Error("Nothing to bulk complete");
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
        throw new Error(
          err.detail || `Failed completing ${sLabel} (HTTP ${res.status})`
        );
      }
    }
    return { label, steps: order.length };
  }
  async function _bcConfirm() {
    const repo = _smgmtRepo();
    if (!_bcLabel || !repo || !_bcPreview) return;
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
          throw new Error(
            err.detail || `Failed completing ${sLabel} (HTTP ${res.status})`
          );
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
      _smgmtBoardFinish({
        ok: false,
        message: "Stopped: " + e.message + "\n\nResolve the conflict manually, then re-run Bulk complete to resume (done steps are skipped).",
        onDone: _onDone
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
    if (bd) bd.remove();
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
    if (!body) return;
    if (!preview || preview.exists === false) {
      body.innerHTML = `<div style="font-size:13px;color:var(--text-muted)">
      This sprint has no lifecycle row in this dashboard's DB${preview && preview.wrong_project ? " for this project" : ""}, so Reconcile cannot change lifecycle here.
      ${preview && preview.exists === false ? '<div style="margin-top:8px">If git branches are already merged, use <b>Bulk complete</b> on the lineage parent \u2014 that seeds the DB row and marks each step completed.</div>' : ""}
    </div>`;
      if (applyBtn) applyBtn.classList.add("hidden");
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
    if (!repo) return;
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
      if (e.target === bd) _recClose();
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
      if (_recLabel !== label) return;
      _recRender(preview);
    } catch (e) {
      const errEl = document.getElementById("rec-error");
      if (errEl) {
        errEl.textContent = "Failed to load preview: " + e.message;
        errEl.classList.remove("hidden");
      }
      const bodyEl = document.getElementById("rec-body");
      if (bodyEl) bodyEl.innerHTML = "";
    }
  }
  async function _recApply() {
    const repo = typeof _smgmtRepo === "function" ? _smgmtRepo() : null;
    const label = _recLabel;
    if (!repo || !label) return;
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
      if (typeof _smgmtShowToast === "function") _smgmtShowToast(msg);
      if (typeof globalThis._histResetLedgerCache === "function") globalThis._histResetLedgerCache();
      if (typeof loadSprintMgmt === "function") loadSprintMgmt().catch(() => {
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

  // apps/dashboard/static/src/sprint-board/preflight-warnings.js
  function _pfBuildWarningsHtml(pfWarnings) {
    if (!pfWarnings) return "";
    const chips = [];
    const unestimated = pfWarnings.unestimated || [];
    const staleEstimates = pfWarnings.stale_estimates || [];
    const missingAc = pfWarnings.missing_ac || [];
    if (unestimated.length) {
      chips.push(`<span class="pf-warning-chip">${unestimated.length} unestimated: ${escHtml(unestimated.join(", "))}</span>`);
    }
    if (staleEstimates.length) {
      chips.push(`<span class="pf-warning-chip">${staleEstimates.length} stale estimate${staleEstimates.length > 1 ? "s" : ""}: ${escHtml(staleEstimates.join(", "))}</span>`);
    }
    if (missingAc.length) {
      chips.push(`<span class="pf-warning-chip">${missingAc.length} missing AC: ${escHtml(missingAc.join(", "))}</span>`);
    }
    if (!chips.length) return "";
    return `<div class="pf-warnings-section">
    <div class="pf-warnings-label">Warnings</div>
    <div class="pf-warning-chips">${chips.join("")}</div>
  </div>`;
  }
  function smgmtOpenPreflightWarnings(label) {
    const repo = _smgmtRepo();
    if (!repo) return;
    const backdrop = document.getElementById("pf-backdrop");
    const modal = document.getElementById("pf-modal");
    const titleEl = document.getElementById("pf-modal-title");
    const loading = document.getElementById("pf-loading");
    const content = document.getElementById("pf-content");
    const stepper = document.getElementById("pf-stepper");
    const footer = document.getElementById("pf-footer");
    const errEl = document.getElementById("pf-error");
    if (!backdrop || !modal) return;
    if (titleEl) titleEl.textContent = "Preflight Warnings";
    if (loading) loading.classList.remove("hidden");
    if (content) {
      content.innerHTML = "";
      content.classList.add("hidden");
    }
    if (stepper) stepper.classList.add("hidden");
    if (footer) footer.classList.add("hidden");
    if (errEl) errEl.classList.add("hidden");
    backdrop.classList.remove("hidden");
    modal.classList.remove("hidden");
    fetch(`/api/sprints/${encodeURIComponent(label)}/preflight?project=${encodeURIComponent(repo)}`).then((r) => {
      if (!r.ok) throw new Error(`${r.status}`);
      return r.json();
    }).then((data) => {
      if (loading) loading.classList.add("hidden");
      if (content) {
        const html = _pfBuildWarningsHtml(data.warnings);
        content.innerHTML = html || '<div class="pf-no-warnings">No warnings found.</div>';
        content.classList.remove("hidden");
      }
    }).catch((e) => {
      if (loading) loading.classList.add("hidden");
      if (errEl) {
        const msg = document.getElementById("pf-error-msg");
        if (msg) msg.textContent = `Failed to load: ${e.message}`;
        errEl.classList.remove("hidden");
      }
    });
  }
  function _pfClose() {
    const backdrop = document.getElementById("pf-backdrop");
    const modal = document.getElementById("pf-modal");
    if (backdrop) backdrop.classList.add("hidden");
    if (modal) modal.classList.add("hidden");
  }
  function _pfRetry() {
  }
  function _pfConfirm() {
  }
  function _pfBulkClose() {
    const overlay = document.getElementById("pf-bulk-overlay");
    if (overlay) overlay.classList.add("hidden");
  }

  // apps/dashboard/static/src/sprint-board/run-controls.js
  var _noop = () => {
  };
  var _noopStr = () => "";
  var smgmtRunBlockedToast = _noop;
  var smgmtRunSprint = _noop;
  var smgmtCancelSprint = _noop;
  var smgmtApproveSprint = _noop;
  var smgmtRejectSprint = _noop;
  var _pfOpen = _noop;
  var _pfReset = _noop;
  var _pfFetch = _noop;
  var _pfShowSuccess = _noop;
  var _pfUpdateConfirmBtn = _noop;
  var _pfBuildCycleHtml = _noopStr;
  var _pfBuildFlagsHtml = _noopStr;
  var _pfFlagShowSizePicker = _noop;
  var _pfFlagHidePicker = _noop;
  var _pfFlagAction = _noop;
  var _pfFlagReestimate = _noop;
  var _pfFlagAutoReestimate = _noop;
  var _pfApproveAll = _noop;
  var _pfReestimateAll = _noop;
  var _pfBuildDAGHtml = _noopStr;
  var _pfDrawDAGArrows = _noop;
  var _pfToggleTicket = _noop;
  var _pfGetSelectedTickets = () => [];
  var _pfComputeConflicts = () => [];
  var _pfBuildConflictsHtml = _noopStr;
  var _pfBuildOrderHtml = _noopStr;
  var _pfUpdateSections = _noop;
  var _pfShowError = _noop;
  var _pfStepperInit = _noop;
  var _pfStepState = _noop;
  var _pfStepperAnimate = _noop;
  var _pfStepperSummary = _noop;
  var smgmtKickoffRun = _noop;
  var smgmtKickoffRetry = _noop;

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
    if (msgEl) msgEl.textContent = text;
    if (overlay) {
      overlay.setAttribute("aria-label", text.replace(/…$/, "") + ", please wait");
      overlay.classList.add("active");
    }
    const showProgress = !!(opts && opts.progress);
    _smgmtBoardOverlayHasProgress = showProgress;
    if (progWrap) progWrap.hidden = true;
    if (logEl) {
      logEl.hidden = true;
      if (opts && opts.clearLog) logEl.innerHTML = "";
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
    if (fill) fill.style.width = pct + "%";
    if (pctEl) pctEl.textContent = pct + "%";
  }
  function _smgmtBoardLog2(line, kind) {
    if (_smgmtBoardOverlayHasProgress) {
      const mappedType = kind === "ok" ? "success" : kind === "err" ? "fail" : kind === "step" ? "dispatch" : "dispatch";
      appendProgressActivityLog2("smgmt-op-pa-host", line, mappedType, { id: BOARD_OVERLAY_PA_ID });
      return;
    }
    const logEl = document.getElementById("smgmt-op-log");
    if (!logEl) return;
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
    if (overlay) overlay.classList.remove("active");
    const paHost = document.getElementById("smgmt-op-pa-host");
    if (paHost) {
      unmountProgressActivity2(paHost);
      paHost.hidden = true;
    }
    const progWrap = document.getElementById("smgmt-op-progress-wrap");
    const logEl = document.getElementById("smgmt-op-log");
    if (progWrap) progWrap.hidden = true;
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
    if (spinner) spinner.style.display = "";
    _smgmtBoardProgress2(0, 1);
    if (_arInterval > 0) _smgmtArStartTicker();
  }
  function _smgmtBoardFinish2(opts) {
    opts = opts || {};
    const ok = opts.ok !== false;
    const message = opts.message || (ok ? "Done." : "Stopped.");
    const onDone = opts.onDone;
    _smgmtArStopTicker();
    const spinner = document.getElementById("smgmt-move-spinner");
    if (spinner) spinner.style.display = "none";
    const overlay = document.getElementById("smgmt-move-overlay");
    if (overlay) overlay.setAttribute("aria-busy", "false");
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
      if (msgEl) msgEl.textContent = message;
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
      if (card.label) idx[card.label] = card;
      for (const cl of card.chain || []) {
        if (!idx[cl]) idx[cl] = card;
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
    const _postRunSections = /* @__PURE__ */ new Set(["needs_rework", "ready_to_merge"]);
    const staleNoTicketLabels = /* @__PURE__ */ new Set();
    for (const [sectionName, cards] of sectionEntries) {
      for (const card of cards) {
        const label = card.label;
        if (!label) continue;
        sprintLabels.add(label);
        aggregateBuckets[label] = sectionName;
        sprint_has_run[label] = _ranStates.has(card.lifecycle_state);
        if (card.stale_no_tickets && _postRunSections.has(sectionName)) {
          staleNoTicketLabels.add(label);
        }
        for (const t of card.tickets || []) {
          issues.push({ ...t, sprint_label: label });
        }
        const chain = card.chain || [];
        for (let i = 0; i < chain.length; i++) {
          const cl = chain[i];
          sprintLabels.add(cl);
          if (!(cl in aggregateBuckets)) aggregateBuckets[cl] = "lineage";
          if (!(cl in sprint_has_run)) sprint_has_run[cl] = sprint_has_run[label];
        }
        for (let i = 1; i < chain.length; i++) {
          if (!sprint_parents[chain[i]]) sprint_parents[chain[i]] = chain[i - 1];
        }
      }
    }
    for (const t of (sections.backlog || {}).tickets || []) {
      issues.push({ ...t, sprint_label: null });
    }
    const order = [...sprintLabels].sort((a, b) => {
      const ma = String(a).match(/^sprint-(\d+)(?:\.(\d+))?$/);
      const mb = String(b).match(/^sprint-(\d+)(?:\.(\d+))?$/);
      if (!ma || !mb) return String(a).localeCompare(String(b));
      const na = parseInt(ma[1], 10);
      const nb = parseInt(mb[1], 10);
      if (na !== nb) return na - nb;
      return parseInt(ma[2] || 0, 10) - parseInt(mb[2] || 0, 10);
    });
    const sprintNumSet = /* @__PURE__ */ new Set();
    for (const l of order) {
      const m = String(l).match(/^sprint-(\d+)/);
      if (m) sprintNumSet.add(parseInt(m[1], 10));
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
      _aggregateBuckets: aggregateBuckets,
      _staleNoTicketLabels: staleNoTicketLabels
    };
  }
  function _smgmtSignoffState(label) {
    if (typeof globalThis !== "undefined" && globalThis._commanderFeatures && globalThis._commanderFeatures.signoff !== true) {
      return null;
    }
    return (_smgmtData && _smgmtData.sprint_signoff || {})[label] || null;
  }
  function _smgmtSignoffBadgeHtml(label) {
    if (_smgmtSignoffState(label) !== "pending") return "";
    return '<span class="sc-signoff-badge">Pending sign-off</span>';
  }
  function _smgmtSignoffActionsHtml(label) {
    if (_smgmtSignoffState(label) !== "pending") return "";
    const e = escHtml(label);
    return `<button class="smgmt-approve-btn" type="button" onclick="smgmtApproveSprint('${e}')"><i class="ti ti-check"></i> Approve</button><button class="smgmt-reject-btn" type="button" onclick="smgmtRejectSprint('${e}')"><i class="ti ti-x"></i> Reject</button>`;
  }
  function _smgmtGoalRequired() {
    const f = typeof globalThis !== "undefined" && globalThis._commanderFeatures;
    if (!f) return false;
    return f.goal_required === true;
  }
  async function loadSprintMgmt2(silent, optimisticRunningLabel) {
    const listEl = document.getElementById("smgmt-sprint-list");
    if (!listEl) return;
    const repo = _cachedFullRepo[_slug] || null;
    if (!repo) {
      listEl.innerHTML = '<div class="loading-msg">Project not found.</div>';
      return;
    }
    if (!silent) {
      listEl.innerHTML = '<div class="loading-msg">Loading sprints\u2026</div>';
      for (const k of Object.keys(_smgmtFinishCards)) delete _smgmtFinishCards[k];
    }
    try {
      if (typeof sprintHealthStripInit === "function") {
        sprintHealthStripInit(_slug);
      }
      if (typeof _smgmtEnsureCapData === "function") {
        _smgmtEnsureCapData();
      }
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
        for (const k of Object.keys(_smgmtLiveCache)) delete _smgmtLiveCache[k];
      }
      if (typeof _smgmtLingerRestore === "function") _smgmtLingerRestore(repo);
      const prevRunningAgg = new Set(_smgmtRunningLabels);
      _smgmtRunningLabels = /* @__PURE__ */ new Set();
      _smgmtAnySprintRunning = false;
      for (const card of (agg.sections || {}).running || []) {
        if (card.label) _smgmtRunningLabels.add(card.label);
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
      const data = _smgmtAggToRenderData(agg);
      _smgmtRender(data);
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
    if (!m) return [Infinity];
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
    const fromMeta = (order || []).filter(
      (l) => (parents || {})[l] === parentLabel
    );
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
      if (d !== 0) return d;
    }
    return 0;
  }
  function _smgmtChildSprintLabel(parentLabel, parents, rerunInto, order) {
    if (rerunInto && rerunInto[parentLabel]) return rerunInto[parentLabel];
    const children = _smgmtChildrenForParent(parentLabel, parents, order);
    if (!children.length) return null;
    return [...children].sort(_smgmtCompareSprintLabels)[children.length - 1];
  }
  function _smgmtLatestLineageLabel(baseLabel, parents, rerunInto, order) {
    const base = _smgmtSprintBaseLabel(baseLabel);
    const members = (order || []).filter(
      (l) => l === base || _smgmtSprintBaseLabel(l) === base && _smgmtSprintSubIndex(l) > 0
    );
    if (!members.length) return null;
    return [...members].sort(_smgmtCompareSprintLabels)[members.length - 1];
  }
  function _smgmtShouldCollapseParent(parentLabel, parents, rerunInto, order) {
    return Boolean(
      _smgmtChildSprintLabel(parentLabel, parents, rerunInto, order)
    );
  }
  function _smgmtShouldCollapseToLineage(label, parents, rerunInto, order) {
    if (_smgmtShouldCollapseParent(label, parents, rerunInto, order)) return true;
    const base = _smgmtSprintBaseLabel(label);
    const latest = _smgmtLatestLineageLabel(base, parents, rerunInto, order);
    if (!latest || label === latest) return false;
    return _smgmtCompareSprintLabels(label, latest) < 0;
  }
  function _smgmtComputeLeadingEmpty(orderedLabels, issues) {
    const labeled = new Set((issues || []).map((i) => i.sprint_label).filter(Boolean));
    const activeBases = /* @__PURE__ */ new Set();
    for (const lbl of labeled) {
      const m = /^sprint-(\d+)/.exec(lbl);
      if (m) activeBases.add(parseInt(m[1], 10));
    }
    const leadingEmpty = [];
    let foundActive = false;
    for (const lbl of orderedLabels) {
      if (!/^sprint-\d+$/.test(lbl)) continue;
      const base = parseInt(lbl.replace("sprint-", ""), 10);
      if (activeBases.has(base)) {
        foundActive = true;
        break;
      }
      leadingEmpty.push(lbl);
    }
    return foundActive ? leadingEmpty : [];
  }
  function _smgmtRender(data) {
    const listEl = document.getElementById("smgmt-sprint-list");
    if (!listEl) return;
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
        if (!bySprint[key]) bySprint[key] = [];
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
      if (_smgmtShouldCollapseToLineage(
        label,
        _sprintParents,
        _rerunInto,
        orderedLabelsRaw
      )) {
        const latest = _smgmtLatestLineageLabel(
          _smgmtSprintBaseLabel(label),
          _sprintParents,
          _rerunInto,
          orderedLabelsRaw
        );
        if (latest && _finishedSet.has(latest)) return false;
        _smgmtResolvedAncestors.add(label);
        return true;
      }
      if (_mergedSet.has(label)) return false;
      const tickets = bySprint[label] || [];
      const ticketCount = tickets.length;
      if (ticketCount > 0) return true;
      if (_finishedSet.has(label)) return false;
      if (_rerunInto[label]) return false;
      const hasChild = Object.values(_sprintParents).some(
        (parent) => parent === label
      );
      return !hasChild;
    });
    _smgmtOrderedLabels = orderedLabels;
    _smgmtFinishedLabels = _finishedSet;
    const focusGuideEl = document.getElementById("smgmt-focus-guide");
    if (focusGuideEl) {
      focusGuideEl.innerHTML = _smgmtFocusGuideHtml(
        data,
        orderedLabels,
        bySprint
      );
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
          if (latest && latest !== label) childLabel = latest;
        }
        const cachedOutcome = _smgmtOutcomeCache[label];
        return `<div class="smgmt-sprint-unit" id="smgmt-unit-${escHtml(label)}">` + _smgmtAncestorRowHtml(label, cachedOutcome, childLabel) + `</div>`;
      }
      if (_smgmtIsFreshRerunSprint(label)) delete _smgmtOutcomeCache[label];
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
    const lineageLabels = orderedLabels.filter(
      (l) => _smgmtResolvedAncestors.has(l)
    );
    const otherLabels = orderedLabels.filter(
      (l) => !_smgmtResolvedAncestors.has(l)
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
    const sectionLabel = (text, cls) => `<div class="smgmt-section-label ${cls}">${text}</div>`;
    const lineageRangeLabel = (labels) => {
      if (!labels.length) return "Lineage";
      const first = sprintLabelDisplay(labels[0]).replace("Sprint ", "");
      const last = sprintLabelDisplay(labels[labels.length - 1]).replace(
        "Sprint ",
        ""
      );
      return first === last ? `Lineage ${first}` : `Lineage ${first} \u2192 ${last}`;
    };
    let cards = "";
    if (lineageLabels.length > 0) {
      cards += sectionLabel(
        lineageRangeLabel(lineageLabels),
        "smgmt-section-lineage"
      );
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
      if (fc) _smgmtRenderFinishCard(lbl, fc.card, fc.branch, _smgmtRepo());
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
    if (typeof _smgmtLoadPlanningInsights === "function") {
      _smgmtLoadPlanningInsights(orderedLabels);
    }
    if (typeof _smgmtLoadEstVsActual === "function") {
      _smgmtLoadEstVsActual(orderedLabels);
    }
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
    if (!row) return;
    const seen = /* @__PURE__ */ new Set();
    (issues || []).forEach((iss) => {
      (iss.labels || []).forEach((l) => {
        seen.add(l.name);
        if (l.color) _smgmtLabelColors[l.name] = "#" + l.color;
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
    if (!parents[label]) return false;
    const planState = (_smgmtData && _smgmtData.sprint_plan_states || {})[label];
    return planState === "draft" || planState === "planning";
  }
  function _smgmtApplyRerunOptimistic(parentLabel, subLabel, ticketNumbers) {
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
      if (_smgmtRunningLabels.has(label)) return "running";
      const inLingerAgg = typeof _smgmtIsLinger === "function" && _smgmtIsLinger(label);
      if (inLingerAgg && !(_smgmtData.sprint_has_run || {})[label])
        return "running";
      const b = aggBuckets[label];
      if (b === "running" || b === "needs_rework" || b === "ready_to_merge" || b === "draft") {
        return b;
      }
    }
    if (_smgmtRunningLabels.has(label)) return "running";
    const inLinger = typeof _smgmtIsLinger === "function" && _smgmtIsLinger(label);
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
  function _smgmtHasLedgerRun(label) {
    return Boolean((_smgmtData?.sprint_has_run || {})[label]);
  }
  async function _smgmtFetchMissingOutcomes(orderedLabels, _bySprint) {
    const repo = _smgmtRepo();
    if (!repo) return;
    for (const label of orderedLabels) {
      if (_smgmtRunningLabels.has(label)) continue;
      if (_smgmtIsFreshRerunSprint(label)) continue;
      if (_smgmtOutcomeCache[label] !== void 0) continue;
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
  async function _smgmtLoadEstimates(orderedLabels, bySprint) {
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
          const display = Number.isInteger(h) ? `${h}h` : `${parseFloat(h.toFixed(1))}h`;
          estEl.textContent = `${display} estimated`;
        }
        _smgmtSetSprintTokenEl(label, {});
      })
    );
  }
  async function _smgmtLoadConflicts(orderedLabels, bySprint) {
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
          (t) => (t.status || "backlog") === "backlog"
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
            sharedFiles: c.shared_files
          });
          _smgmtConflictsByIssue[c.ticket2_id].push({
            partnerId: c.ticket1_id,
            partnerTitle: c.ticket1_title,
            sharedFiles: c.shared_files
          });
        }
        for (const t of pending) _smgmtUpdateConflictBadge(t.number);
      })
    );
  }
  async function _smgmtLoadDepOrder(orderedLabels, bySprint) {
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
          (t) => (t.status || "backlog") === "backlog"
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
                inCycle: true
              };
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
        for (const t of pending) _smgmtUpdateDepOrderBadge(t.number);
      })
    );
  }
  async function _smgmtLoadGoals(orderedLabels) {
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
        if (o === "done") blockClass = "seg-done";
        else if (o === "failed") blockClass = "seg-failed";
        else if (o === "skipped") blockClass = "seg-skipped";
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
    if (!issues || issues.length === 0) return "";
    const safeLabel = label ? escHtml(label) : "";
    const safeRepo = repo ? escHtml(repo) : "";
    return issues.map((iss) => {
      const o = iss.outcome || "skipped";
      let circle = "";
      if (o === "done")
        circle = '<div class="smgmt-ticket-circle done">\u2713</div>';
      else if (o === "failed")
        circle = '<div class="smgmt-ticket-circle failed">\u2715</div>';
      else circle = '<div class="smgmt-ticket-circle skipped">\u2212</div>';
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
    if (!repo || !_smgmtData) return;
    if (_smgmtAggregateCards) {
      for (const [label, card] of Object.entries(_smgmtAggregateCards)) {
        if (!card || !card.finish_card) continue;
        if (_smgmtIsFreshRerunSprint(label)) continue;
        const cardData = card.finish_card;
        if (cardData.state === "no_data") continue;
        const branchData = card.branch_status || { exists: false };
        _smgmtFinishCards[label] = { card: cardData, branch: branchData };
        _smgmtRenderFinishCard(label, cardData, branchData, repo);
      }
      return;
    }
    const order = _smgmtData.order && _smgmtData.order.length ? _smgmtData.order : (_smgmtData.sprints || []).map((n) => `sprint-${n}`);
    await Promise.allSettled(
      order.map(async (label) => {
        if (_smgmtIsFreshRerunSprint(label)) return;
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
          if (cardData.state === "no_data") return;
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
    if (cardData.state === "no_data") return;
    const cardEl = document.getElementById(`smgmt-finish-card-${label}`);
    const blockEl = document.getElementById(`smgmt-card-${label}`);
    if (!cardEl || !blockEl) return;
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
    if (state === "running") return _sfcRunningHtml(cardData, branchLink, n);
    if (state === "completed")
      return _sfcCompletedHtml(cardData, branchLink, n, branchData);
    if (state === "has_rework" || state === "cancelled") {
      return _sfcHasReworkHtml(cardData, branchLink, n, branchData);
    }
    return "";
  }
  function _smgmtCardActionBtnHtml(label, {
    isRunning,
    isLinger,
    isHasRework,
    isPostRun,
    rerunInto,
    rerunChildDisplay,
    canRun,
    tickets
  } = {}) {
    if (isRunning) {
      return `<button class="smgmt-cancel-btn" onclick="smgmtCancelSprint('${escHtml(label)}')">
                  <i class="ti ti-player-stop"></i> Cancel sprint</button>`;
    }
    if (isLinger) {
      return `<span class="smgmt-linger-note">Finished \u2014 snapshot kept 1h</span>`;
    }
    if (isHasRework && rerunInto && (tickets || []).length === 0) {
      const _rrDisabled = _smgmtAnySprintRunning ? "disabled" : "";
      const _rrTitle = _smgmtAnySprintRunning ? 'title="Cannot run: another sprint is currently running."' : "";
      return `<button class="smgmt-run-btn" ${_rrDisabled} ${_rrTitle}
                  onclick="smgmtRunSprint('${escHtml(rerunInto)}')">
                  <i class="ti ti-player-play"></i> Run \u2192 ${escHtml(rerunChildDisplay || "")}</button>`;
    }
    if (isHasRework || isPostRun) return "";
    if (_smgmtSignoffState(label) === "pending") return _smgmtSignoffActionsHtml(label);
    if (_smgmtAnySprintRunning) {
      return `<button class="smgmt-run-btn smgmt-run-btn--blocked"
                  title="Another sprint is running"
                  onclick="smgmtRunBlockedToast()">
                  <i class="ti ti-player-play"></i> Run Sprint</button>`;
    }
    const runDisabled = !canRun ? "disabled" : "";
    const runTitle = !canRun ? 'title="No dispatchable tickets \u2014 remaining items are already SIT/UAT or in progress"' : "";
    return `<button class="smgmt-run-btn" ${runDisabled} ${runTitle}
                  onclick="smgmtRunSprint('${escHtml(label)}')">
                  <i class="ti ti-player-play"></i> Run Sprint</button>`;
  }
  function _smgmtCardHtml(label, n, tickets, outcome, isNext, parent, finished) {
    const isRunning = _smgmtRunningLabels.has(label);
    const isLinger = false;
    const isRunningView = isRunning || isLinger;
    let isCollapsed = isRunning;
    try {
      const _pref = localStorage.getItem("sprintColumn_" + label + "_collapsed");
      if (_pref === "1") isCollapsed = true;
      else if (_pref === "0") isCollapsed = false;
    } catch (_) {
    }
    const isFreshRerun = _smgmtIsFreshRerunSprint(label);
    if (isFreshRerun) outcome = null;
    const planState = ((_smgmtData && _smgmtData.sprint_plan_states || {})[label] || "").toLowerCase();
    const planBlocksPostRun = ["planned", "draft", "planning"].includes(
      planState
    );
    const outcomeLifecycle = (outcome && outcome.lifecycle || "").toLowerCase();
    const outcomeState = outcome && (outcome.state || (outcome.sprint_status === "completed" ? "completed" : null));
    const hasLedgerRun = _smgmtHasLedgerRun(label);
    const _badgeState = outcome && typeof _smgmtStateMeta === "function" ? _smgmtStateMeta(outcome, (outcome.issues || []).length).state || "" : "";
    const isHasRework = hasLedgerRun && (outcomeLifecycle === "needs_rework" || _badgeState === "needs_rework" || outcomeState === "has_rework" || outcomeState === "cancelled");
    const isReadyToMerge = hasLedgerRun && _badgeState !== "needs_rework" && (outcomeLifecycle === "ready_to_merge" || outcomeLifecycle === "completed" && outcomeState === "completed");
    const isAwaitingMerge = isReadyToMerge || finished && !isRunning && !isHasRework && !planBlocksPostRun;
    const showRunningChrome = isRunningView && !isAwaitingMerge;
    const isPostRun = !isRunningView && !planBlocksPostRun && hasLedgerRun;
    const actionBtn = !isRunning && !isLinger && !isHasRework && !isPostRun && !finished ? `<button class="smgmt-preflight-warnings-btn" type="button"
              title="View preflight warnings for this sprint"
              onclick="smgmtOpenPreflightWarnings('${escHtml(label)}')">
         <i class="ti ti-alert-circle"></i> Preflight</button>` : "";
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
        if (_metaSecs != null) _metaParts.push(_fmtRunningTime(_metaSecs));
        if (_metaStopped) _metaParts.push(`stopped ${_metaStopped}`);
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
        if (tickets.length === 0 && (outcome.issues || []).length > 0) {
          rollupItems = outcome.issues.map((i) => ({ number: i.number }));
        }
      } else {
        outcomeBandHtml = _smgmtOutcomeBandHtml(label, outcome);
        const _movedToChild = /* @__PURE__ */ new Set();
        try {
          Object.keys(_smgmtBySprint || {}).forEach((cl) => {
            if (cl !== label && cl.startsWith(label + ".")) {
              (_smgmtBySprint[cl] || []).forEach(
                (t) => _movedToChild.add(t.number)
              );
            }
          });
        } catch (_) {
        }
        const issueList = (outcome.issues || []).filter(
          (i) => !_movedToChild.has(i.number)
        );
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
  <div class="sc-preview-slot" id="sc-preview-${escHtml(label)}"></div>
  <div class="pi-stale-slot" id="pi-stale-${escHtml(label)}"></div>
  <div class="pi-ev-slot" id="pi-ev-${escHtml(label)}"></div>`;
    const logHtml = "";
    const cancelBannerHtml = "";
    const plannedBadge = !finished && !isPostRun && !outcomeBadgeHtml && !isRunningView ? '<span class="sc-draft-badge">DRAFT</span>' : "";
    const signoffBadge = _smgmtSignoffBadgeHtml(label);
    const blockedHint = "";
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
    const isStaleNoTickets = !isRunning && !!(_smgmtData && _smgmtData._staleNoTicketLabels instanceof Set && _smgmtData._staleNoTicketLabels.has(label));
    const staleNoticeHtml = isStaleNoTickets ? `<span class="smgmt-stale-no-tickets-notice" title="No open tickets remain on this sprint"><i class="ti ti-alert-circle"></i> stale \u2014 no tickets</span>` : "";
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
          <button class="smgmt-finish-btn sc-merge-link ${finishHidden}" ${finishDisabled}
                  title="${finishDisabled ? "No open tickets" : "Merge sprint"}"
                  onclick="smgmtFinishSprint('${escHtml(label)}')">
            <i class="ti ti-flag-check"></i> Merge Sprint</button>`}
        </div>
      </div>
      ${(function() {
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
      if (!_ss) return "";
      return `<div class="sc-status-line"><i class="ti ti-clock sc-status-icon" aria-hidden="true"></i><span>${escHtml(_ss)}</span></div>`;
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
      if (ticketLevel > 0) prevLevel = ticketLevel;
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
    if (levelNums.length <= 1) return null;
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
      levelText
    ].filter(Boolean);
    textEl.textContent = parts.join(" \xB7 ");
  }
  function _smgmtRunningCardHtml(label, n, tickets) {
    let isCollapsed = true;
    try {
      const _pref = localStorage.getItem("sprintColumn_" + label + "_collapsed");
      if (_pref === "0") isCollapsed = false;
      else if (_pref === "1") isCollapsed = true;
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
      if (liveStatus === "done") blockClass = "seg-done";
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
          <!-- Cancel sprint button removed (issue #2251) -->
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
    if (count === 0) return "0 tickets";
    let totalMins = 0, unestimated = 0;
    for (const t of items) {
      const size = _smgmtTicketSize(t);
      const mins = size ? _sizeMinutes(size) : 0;
      if (mins > 0) totalMins += mins;
      else unestimated++;
    }
    const countStr = `${count} ticket${count !== 1 ? "s" : ""}`;
    if (unestimated === count) return countStr;
    const h = totalMins / 60;
    const timeStr = h < 1 ? `~${totalMins}m` : `~${parseFloat((Math.round(h * 10) / 10).toFixed(1))}h`;
    return `${countStr} \xB7 ${timeStr}`;
  }
  function _smgmtTicketSize(t) {
    if (!t) return null;
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
    if (isRunning) return "";
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
    if (isLinger) return "Sprint finished \u2014 snapshot kept 1 hour.";
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
    if (!_smgmtRunningLabels || _smgmtRunningLabels.size === 0) return "";
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
    if (el) el.textContent = _smgmtRollupText(items);
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
    if (!ticketsEl) return;
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
    const hasActiveSelection = typeof _smgmtSelectedIssues !== "undefined" && _smgmtSelectedIssues.size > 0;
    if (!hasActiveSelection) {
      if (sorted.length === 0) {
        const msg = _blBacklogAll.length === 0 ? "No backlog tickets \u2014 all caught up" : "No tickets match the active filters";
        ticketsEl.innerHTML = `<div style="padding:var(--space-3) var(--space-4);text-align:center;color:var(--text-sub);font-size:12px;">${msg}</div>`;
      } else {
        ticketsEl.innerHTML = sorted.map((t) => _smgmtBacklogTicketHtml(t, allSprintNums)).join("");
      }
    }
    _blSyncFilterPills();
    if (typeof _smgmtUpdateSelectionUI === "function") {
      _smgmtUpdateSelectionUI();
    } else {
      _blUpdateActions();
    }
  }
  var _BACKLOG_CHIP_LIMIT = 3;
  var _BACKLOG_CHIP_KEEP_RE = /^(uat|sit|in-progress|needs-rework|blocked|sprint-.+)$/i;
  function _smgmtBacklogLabelChipsHtml(labels) {
    if (!labels || !labels.length) return "";
    const kept = labels.map((l) => typeof l === "string" ? l : l.name || "").filter((n) => _BACKLOG_CHIP_KEEP_RE.test(n));
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
  function _smgmtBacklogTicketHtml(ticket, _sprintNums) {
    const hasEstimate = _smgmtTicketHasEstimate(ticket);
    const backlogLabelNames = (ticket.labels || []).map((l) => l.name).join(",");
    const schedDepHtml = _smgmtSchedDepHtml(ticket);
    const sizeValue = _smgmtTicketSize(ticket) || "";
    const sizeAttr = sizeValue ? ` data-size="${escHtml(sizeValue)}"` : "";
    const sizePillHtml = sizeValue ? `<span class="smgmt-ticket-size-pill">${escHtml(sizeValue)}</span>` : "";
    const estHtml = _smgmtTicketEstHtml(ticket);
    const labelChipsHtml = _smgmtBacklogLabelChipsHtml(ticket.labels);
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
      ${schedDepHtml}${sizePillHtml}${estHtml}${labelChipsHtml}
      ${addToSprintBtn}
      <button class="smgmt-row-menu-btn" tabindex="0" title="Ticket actions" aria-haspopup="true" aria-expanded="false"
              onclick="event.stopPropagation();_smgmtRowMenuOpen(event, ${ticket.number}, null, ${hasEstimate})"
              onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();_smgmtRowMenuOpen(event,${ticket.number},null,${hasEstimate});}">
        <i class="ti ti-menu-2"></i></button>
    </div>`;
  }
  function _smgmtAncestorMergeState(label, outcome) {
    if (!outcome) return "unknown";
    const counts = outcome.counts || {};
    const done = counts.done || 0;
    if (done === 0) return "failed";
    const meta = typeof _smgmtStateMeta === "function" ? _smgmtStateMeta(outcome, (outcome.issues || []).length) : { state: "unknown" };
    const state = meta.state;
    if (state === "ready_to_merge" || state === "partial_finished")
      return "needs_merge";
    if (state === "needs_rework") return "needs_merge";
    if (state === "completed") return "merged";
    if (_smgmtFinishedLabels && _smgmtFinishedLabels.has(label) && done > 0)
      return "merged";
    return "needs_merge";
  }
  function _smgmtAncestorStatsLine(outcome) {
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
    return parts.join(" \xB7 ");
  }
  function _smgmtAncestorCarrySummary(outcome, childLabel, mergeState) {
    if (!outcome) return "";
    const counts = outcome.counts || {};
    const done = counts.done || 0;
    const carried = (counts.failed || 0) + (counts.skipped || 0);
    const uat = counts.uat || 0;
    const childDisplay = childLabel ? sprintLabelDisplay(childLabel).replace("Sprint ", "") : "";
    if (mergeState === "failed") {
      if (carried > 0 && childDisplay) {
        return `${done} merged \xB7 ${carried} carried \u2192 ${childDisplay}`;
      }
      if (carried > 0) return `${done} merged \xB7 ${carried} carried`;
      return `${done} merged`;
    }
    if (mergeState === "needs_merge") {
      let summary2 = `${done} passed`;
      if (uat > 0) summary2 += ` \xB7 ${uat} awaiting UAT`;
      if (carried > 0 && childDisplay)
        summary2 += ` \xB7 ${carried} reworked \u2192 ${childDisplay}`;
      else if (carried > 0) summary2 += ` \xB7 ${carried} reworked`;
      return `${summary2} \xB7 not merged yet`;
    }
    let summary = `${done} merged`;
    if (uat > 0) summary += ` \xB7 ${uat} awaiting UAT`;
    if (carried > 0 && childDisplay)
      summary += ` \xB7 ${carried} reworked \u2192 ${childDisplay}`;
    else if (carried > 0) summary += ` \xB7 ${carried} reworked`;
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
    const carrySummary = _smgmtAncestorCarrySummary(
      outcome || null,
      rerunInto,
      mergeState
    );
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
    const actionsHtml = mergeState === "needs_merge" ? `<div class="slp-ancestor-actions">
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
    if (!body) return;
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
      if (_smgmtResolvedAncestors.has(l)) return false;
      if (_smgmtRunningLabels.has(l)) return false;
      if (l === draftLabel) return false;
      if (finishedSet.has(l)) return false;
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
      steps.push({
        text: "No draft sprint yet \u2014 create one to start planning.",
        priority: "low"
      });
    }
    const resolved = [];
    for (const label of lineageLabels) {
      const outcome = _smgmtOutcomeCache[label] || null;
      const mergeState = _smgmtAncestorMergeState(label, outcome);
      if (mergeState !== "merged" && mergeState !== "failed") continue;
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
    if (!draftLabel) return;
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
    if (!card || !card.classList.contains("slp-ancestor-row")) return;
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
        if (newBody) newBody.hidden = false;
        const newIcon = document.querySelector(
          `#smgmt-card-${CSS.escape(label)} .slp-ancestor-toggle i`
        );
        if (newIcon) newIcon.className = "ti ti-chevron-down";
      }
    }
  }
  var _boardSseTimer = null;
  var _boardSsePending = false;
  function _boardSseFireRefetch() {
    _boardSseTimer = null;
    loadSprintMgmt2(true);
  }
  function _boardSseOnInvalidated(_project) {
    if (typeof document !== "undefined" && document.hidden) {
      _boardSsePending = true;
      return;
    }
    if (_boardSseTimer !== null) clearTimeout(_boardSseTimer);
    _boardSseTimer = setTimeout(_boardSseFireRefetch, 2e3);
  }
  function _boardSseOnVisible() {
    if (!_boardSsePending) return;
    _boardSsePending = false;
    if (_boardSseTimer !== null) clearTimeout(_boardSseTimer);
    _boardSseTimer = setTimeout(_boardSseFireRefetch, 2e3);
  }

  // apps/dashboard/static/src/sprint-board/index.js
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
  globalThis.smgmtOpenPreflightWarnings = smgmtOpenPreflightWarnings;
  globalThis.smgmtRunBlockedToast = smgmtRunBlockedToast;
  globalThis.smgmtRunSprint = smgmtRunSprint;
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
  globalThis._smgmtCardActionBtnHtml = _smgmtCardActionBtnHtml;
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
  globalThis._smgmtAncestorMergeState = _smgmtAncestorMergeState;
  globalThis._smgmtAncestorCarrySummary = _smgmtAncestorCarrySummary;
  globalThis._smgmtAncestorTicketsHtml = _smgmtAncestorTicketsHtml;
  globalThis._smgmtAncestorRowHtml = _smgmtAncestorRowHtml;
  globalThis.smgmtToggleAncestor = smgmtToggleAncestor;
  globalThis._smgmtUpdateAncestorRow = _smgmtUpdateAncestorRow;
  globalThis.smgmtAddToDraft = smgmtAddToDraft;
  globalThis._boardSseOnInvalidated = _boardSseOnInvalidated;
  globalThis._boardSseOnVisible = _boardSseOnVisible;
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
  globalThis._histToggleAgentTime = _histToggleAgentTime;
  globalThis._histToggleMetrics = _histToggleMetrics;
  globalThis._histResetLedgerCache = _histResetLedgerCache;
  globalThis._histToggleShowClosed = _histToggleShowClosed;
  globalThis._histForceRefresh = _histForceRefresh;
  globalThis._histSetTtlMin = _histSetTtlMin;
  globalThis._histBulkSignOff = _histBulkSignOff;
  globalThis._histClearStaleLabels = _histClearStaleLabels;
  globalThis._histIsLoading = _histIsLoading;
  globalThis._sHealthBuildHtml = _sHealthBuildHtml;
  globalThis._sHealthStripRender = _sHealthStripRender;
  globalThis.sprintHealthStripInit = sprintHealthStripInit2;
  globalThis._smgmtComputeLeadingEmpty = _smgmtComputeLeadingEmpty;
  globalThis._smgmtStaleEstimateHtml = _smgmtStaleEstimateHtml;
  globalThis._smgmtLoadPlanningInsights = _smgmtLoadPlanningInsights2;
  globalThis._smgmtEstVsActualSectionHtml = _smgmtEstVsActualSectionHtml;
  globalThis._smgmtToggleEstVsActual = _smgmtToggleEstVsActual;
  globalThis._smgmtLoadEstVsActual = _smgmtLoadEstVsActual2;

  // apps/dashboard/static/src/failures/failures.js
  async function fetchFailures(project, category) {
    let url = "/api/failures?project=" + encodeURIComponent(project);
    if (category) url += "&category=" + encodeURIComponent(category);
    const resp = await fetch(url);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    return resp.json();
  }
  function _escHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function _normalizeTs(ts) {
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$/.test(ts)) {
      return ts + "Z";
    }
    return ts;
  }
  function _fmtTs(ts) {
    if (!ts) return "";
    try {
      const d = new Date(_normalizeTs(ts));
      return d.toLocaleString(void 0, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit"
      });
    } catch (_) {
      return String(ts);
    }
  }
  function _renderRows(rows) {
    if (!rows || rows.length === 0) {
      return '<tr><td colspan="7" class="fbox-empty">No failures</td></tr>';
    }
    return rows.map(function(r) {
      const issueLink = r.issue_number ? '<span class="fbox-issue">#' + _escHtml(r.issue_number) + "</span>" : "<span>\u2014</span>";
      const sprint = r.sprint_label ? _escHtml(r.sprint_label) : "\u2014";
      const agent = r.agent ? _escHtml(r.agent) : "\u2014";
      const category = r.category ? _escHtml(r.category) : "\u2014";
      const reason = r.reason ? '<span title="' + _escHtml(r.reason) + '">' + _escHtml(r.reason.length > 60 ? r.reason.slice(0, 60) + "\u2026" : r.reason) + "</span>" : "\u2014";
      const ts = _fmtTs(r.ts);
      const logCell = r.log_url ? '<a class="fbox-log-link" href="' + _escHtml(r.log_url) + '" target="_blank" rel="noopener">View log</a>' : "\u2014";
      return "<tr><td>" + issueLink + "</td><td>" + sprint + "</td><td>" + agent + '</td><td><span class="fbox-cat">' + category + "</span></td><td>" + reason + '</td><td class="fbox-ts">' + ts + "</td><td>" + logCell + "</td></tr>";
    }).join("");
  }
  function _setLoading(el, msg) {
    el.innerHTML = '<div class="fbox-state"><i class="ti ti-loader fbox-spinner"></i>' + _escHtml(msg) + "</div>";
  }
  function _setError(el, msg) {
    el.innerHTML = '<div class="fbox-state fbox-state-error"><i class="ti ti-alert-circle"></i>' + _escHtml(msg) + "</div>";
  }
  var _currentCategory = "";
  function _getProject() {
    return typeof _projectData !== "undefined" && _projectData ? _projectData.repo || "" : "";
  }
  function failuresInit2() {
    const root2 = document.getElementById("fbox-root");
    if (!root2) return;
    const project = _getProject();
    if (!project) {
      _setError(root2, "No project selected.");
      return;
    }
    _setLoading(root2, "Loading failures\u2026");
    const cat = _currentCategory;
    fetchFailures(project, cat).then(function(rows) {
      root2.innerHTML = '<div class="fbox-table-wrap"><table class="fbox-table"><thead><tr><th>Issue</th><th>Sprint</th><th>Agent</th><th>Category</th><th>Reason</th><th>Time</th><th>Log</th></tr></thead><tbody id="fbox-tbody">' + _renderRows(rows) + "</tbody></table></div>";
    }).catch(function(_err) {
      _setError(root2, "Failed to load failures");
    });
  }
  function failuresCategoryChange(value) {
    _currentCategory = value || "";
    failuresInit2();
  }

  // apps/dashboard/static/src/reasoning.js
  async function fetchRunReasoning(runId) {
    const resp = await fetch("/api/runs/" + encodeURIComponent(runId) + "/reasoning");
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    return resp.json();
  }

  // apps/dashboard/static/src/home/live-refresh.js
  var REPORT_REFRESH_INTERVAL_MS = 2e4;
  function startDevReportAutoRefresh({
    fetchFn,
    interval = REPORT_REFRESH_INTERVAL_MS,
    onUpdate
  } = {}) {
    const tick = async () => {
      try {
        await fetchFn();
        if (typeof onUpdate === "function") {
          onUpdate((/* @__PURE__ */ new Date()).toISOString());
        }
      } catch (_) {
      }
    };
    const handle = visibilityInterval(tick, interval);
    return () => clearInterval(handle);
  }

  // apps/dashboard/static/src/brain/brain.js
  async function fetchBrainSearch(q, project) {
    let url = "/api/brain/search?q=" + encodeURIComponent(q);
    if (project) url += "&project=" + encodeURIComponent(project);
    const resp = await fetch(url);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    return resp.json();
  }
  async function fetchBrainPanels(project) {
    let url = "/api/brain/panels";
    if (project) url += "?project=" + encodeURIComponent(project);
    const resp = await fetch(url);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    return resp.json();
  }
  async function fetchBrainDoc(slug, path) {
    const url = "/api/projects/" + encodeURIComponent(slug) + "/docs/" + path;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error("HTTP " + resp.status);
    return resp.json();
  }
  function _esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function _sourceLabel(source) {
    const map = {
      decisions: "Decision",
      "bulk-create": "Bulk create",
      retros: "Retro",
      docs: "Docs"
    };
    return map[source] || source;
  }
  function _sourceClass(source) {
    return "brain-badge brain-badge-" + source;
  }
  function _renderHits(hits) {
    if (!hits || hits.length === 0) {
      return '<div class="brain-empty">No results found.</div>';
    }
    return hits.map(function(h) {
      const badge = '<span class="' + _sourceClass(h.source) + '">' + _esc(_sourceLabel(h.source)) + "</span>";
      const path = _esc(h.path || "");
      const snippet = _esc(h.snippet || "");
      return '<div class="brain-hit brain-clickable" tabindex="0" role="button" aria-label="Open ' + path + '" data-doc-path="' + path + '"><div class="brain-hit-header">' + badge + '<span class="brain-hit-path">' + path + '</span></div><div class="brain-hit-snippet">' + snippet + "</div></div>";
    }).join("");
  }
  function _renderRecentDecisions(items) {
    if (!items || items.length === 0) {
      return "<p>No decisions found.</p>";
    }
    return items.map(function(d) {
      const path = _esc(d.path || "");
      return '<div class="brain-panel-item brain-clickable" tabindex="0" role="button" aria-label="Open ' + path + '" data-doc-path="' + path + '"><div class="brain-panel-item-title">' + _esc(d.title || d.path) + "</div>" + (d.decision ? '<div class="brain-panel-item-sub">' + _esc(d.decision) + "</div>" : "") + "</div>";
    }).join("");
  }
  function _renderOpenDecisions(items) {
    if (!items || items.length === 0) {
      return "<p>No open decision items found.</p>";
    }
    return items.map(function(d) {
      const path = _esc(d.path || "");
      return '<div class="brain-panel-item brain-clickable" tabindex="0" role="button" aria-label="Open ' + path + '" data-doc-path="' + path + '"><div class="brain-panel-item-path">' + path + '</div><div class="brain-panel-item-line">' + _esc(d.line) + "</div></div>";
    }).join("");
  }
  function _renderLastLearnings(items) {
    if (!items || items.length === 0) {
      return "<p>No learnings recorded.</p>";
    }
    return "<ul>" + items.map(function(l) {
      return "<li>" + _esc(l) + "</li>";
    }).join("") + "</ul>";
  }
  function _renderBacklogRationale(items) {
    if (!items || items.length === 0) {
      return "<p>No ADR entries found.</p>";
    }
    return items.map(function(d) {
      const path = _esc(d.path || "");
      return '<div class="brain-panel-item brain-clickable" tabindex="0" role="button" aria-label="Open ' + path + '" data-doc-path="' + path + '"><div class="brain-panel-item-title">' + _esc(d.title || d.path) + "</div></div>";
    }).join("");
  }
  var _panelsLoaded = false;
  var _delegationSetup = false;
  function _getProject2() {
    return typeof _projectData !== "undefined" && _projectData ? _projectData.repo || null : null;
  }
  function _getSlug() {
    const repo = _getProject2();
    return repo ? repo.split("/").pop() : null;
  }
  function openBrainDoc(path) {
    const viewerEl = document.getElementById("brain-doc-viewer");
    const contentEl = document.getElementById("brain-doc-content");
    const pathLabelEl = document.getElementById("brain-doc-path-label");
    const resultsEl = document.getElementById("brain-results");
    const rootEl = document.getElementById("brain-root");
    if (!viewerEl || !contentEl) return;
    if (resultsEl) resultsEl.style.display = "none";
    if (rootEl) rootEl.style.display = "none";
    viewerEl.style.display = "";
    if (pathLabelEl) pathLabelEl.textContent = path;
    contentEl.innerHTML = '<div class="brain-state"><i class="ti ti-loader brain-spinner"></i>Loading\u2026</div>';
    const slug = _getSlug();
    if (!slug) {
      contentEl.innerHTML = '<div class="brain-state brain-state-error"><i class="ti ti-alert-circle"></i>No project selected.</div>';
      return;
    }
    fetchBrainDoc(slug, path).then(function(data) {
      const content = data.content || "";
      const rendered = typeof _mdToHtml === "function" ? '<div class="md-body">' + _mdToHtml(content) + "</div>" : '<pre class="brain-doc-pre">' + _esc(content) + "</pre>";
      contentEl.innerHTML = rendered;
    }).catch(function() {
      contentEl.innerHTML = '<div class="brain-state brain-state-error"><i class="ti ti-alert-circle"></i>Failed to load document.</div>';
    });
  }
  function _handleDocActivation(e) {
    if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") return;
    const item = e.target && typeof e.target.closest === "function" ? e.target.closest("[data-doc-path]") : null;
    if (!item) return;
    if (e.type === "keydown") e.preventDefault();
    openBrainDoc(item.getAttribute("data-doc-path"));
  }
  function _setupDocDelegation(container) {
    if (!container) return;
    container.addEventListener("click", _handleDocActivation);
    container.addEventListener("keydown", _handleDocActivation);
  }
  function brainSearch() {
    const inputEl = document.getElementById("brain-search-input");
    const resultsEl = document.getElementById("brain-results");
    if (!inputEl || !resultsEl) return;
    const q = (inputEl.value || "").trim();
    if (!q) {
      resultsEl.innerHTML = "";
      return;
    }
    resultsEl.innerHTML = '<div class="brain-state"><i class="ti ti-loader brain-spinner"></i>Searching\u2026</div>';
    fetchBrainSearch(q, _getProject2()).then(function(hits) {
      resultsEl.innerHTML = _renderHits(hits);
    }).catch(function() {
      resultsEl.innerHTML = '<div class="brain-state brain-state-error"><i class="ti ti-alert-circle"></i>Search failed.</div>';
    });
  }
  function brainInit2() {
    const root2 = document.getElementById("brain-root");
    if (!root2) return;
    if (_panelsLoaded) return;
    _panelsLoaded = true;
    if (!_delegationSetup) {
      _delegationSetup = true;
      _setupDocDelegation(document.getElementById("brain-results"));
      _setupDocDelegation(document.getElementById("brain-panels"));
    }
    const panelsEl = document.getElementById("brain-panels");
    if (!panelsEl) return;
    panelsEl.innerHTML = '<div class="brain-state"><i class="ti ti-loader brain-spinner"></i>Loading panels\u2026</div>';
    fetchBrainPanels(_getProject2()).then(function(data) {
      panelsEl.innerHTML = '<div class="brain-panel-grid"><section class="brain-panel"><h3 class="brain-panel-title"><i class="ti ti-book"></i> Recent Decisions</h3><div class="brain-panel-body">' + _renderRecentDecisions(data.recent_decisions) + '</div></section><section class="brain-panel"><h3 class="brain-panel-title"><i class="ti ti-arrow-right"></i> Open \u27F6 DECISION Items</h3><div class="brain-panel-body">' + _renderOpenDecisions(data.open_decisions) + '</div></section><section class="brain-panel"><h3 class="brain-panel-title"><i class="ti ti-bulb"></i> Last Sprint Learnings</h3><div class="brain-panel-body">' + _renderLastLearnings(data.last_learnings) + '</div></section><section class="brain-panel"><h3 class="brain-panel-title"><i class="ti ti-list-check"></i> Backlog with Rationale</h3><div class="brain-panel-body">' + _renderBacklogRationale(data.backlog_rationale) + "</div></section></div>";
    }).catch(function() {
      panelsEl.innerHTML = '<div class="brain-state brain-state-error"><i class="ti ti-alert-circle"></i>Failed to load panels.</div>';
    });
  }

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
  root.parseUrl = parseUrl2;
  root.loadCommanderFeatures = loadCommanderFeatures;
  root.visibilityInterval = visibilityInterval;
  root.snavNavStatusFetch = snavNavStatusFetch;
  root.snavNavStatusCacheClear = snavNavStatusCacheClear;
  globalThis.switchTab = switchTab;
  globalThis.toggleStabDropdown = toggleStabDropdown;
  globalThis.closeAllStabDropdowns = closeAllStabDropdowns;
  globalThis.parseUrl = parseUrl2;
  globalThis.loadCommanderFeatures = loadCommanderFeatures;
  globalThis.visibilityInterval = visibilityInterval;
  globalThis.snavNavStatusFetch = snavNavStatusFetch;
  globalThis.snavNavStatusCacheClear = snavNavStatusCacheClear;
  installVisibilityGuard();
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
  root.getEnvironment = getEnvironment;
  root.getVersion = getVersion;
  root.getSettings = getSettings;
  root.invalidateSettings = invalidateSettings;
  globalThis.getEnvironment = getEnvironment;
  globalThis.getVersion = getVersion;
  globalThis.getSettings = getSettings;
  globalThis.invalidateSettings = invalidateSettings;
  root.GH_AUTH_POLL_INTERVAL_MS = GH_AUTH_POLL_INTERVAL_MS;
  root.startGhAuthPoll = startGhAuthPoll;
  root.stopGhAuthPoll = stopGhAuthPoll;
  globalThis.GH_AUTH_POLL_INTERVAL_MS = GH_AUTH_POLL_INTERVAL_MS;
  globalThis.startGhAuthPoll = startGhAuthPoll;
  globalThis.stopGhAuthPoll = stopGhAuthPoll;
  root.fetchFailures = fetchFailures;
  root.failuresInit = failuresInit2;
  root.failuresCategoryChange = failuresCategoryChange;
  globalThis.fetchFailures = fetchFailures;
  globalThis.failuresInit = failuresInit2;
  globalThis.failuresCategoryChange = failuresCategoryChange;
  root.fetchRunReasoning = fetchRunReasoning;
  globalThis.fetchRunReasoning = fetchRunReasoning;
  root.startDevReportAutoRefresh = startDevReportAutoRefresh;
  root.REPORT_REFRESH_INTERVAL_MS = REPORT_REFRESH_INTERVAL_MS;
  globalThis.startDevReportAutoRefresh = startDevReportAutoRefresh;
  globalThis.REPORT_REFRESH_INTERVAL_MS = REPORT_REFRESH_INTERVAL_MS;
  root.fetchBrainSearch = fetchBrainSearch;
  root.fetchBrainPanels = fetchBrainPanels;
  root.brainInit = brainInit2;
  root.brainSearch = brainSearch;
  globalThis.fetchBrainSearch = fetchBrainSearch;
  globalThis.fetchBrainPanels = fetchBrainPanels;
  globalThis.brainInit = brainInit2;
  globalThis.brainSearch = brainSearch;
})();
//# sourceMappingURL=bundle.js.map
