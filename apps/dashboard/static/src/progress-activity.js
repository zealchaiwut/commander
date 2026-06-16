/* ProgressActivity — reusable progress/activity component (issue #928).
 *
 * Renders one of three modes from a normalized payload:
 *   bar         — determinate fraction: fill-bar, current-item label, counts, est-remaining
 *   stepper     — fixed ordered steps cycling pending→checking→done/failed/fixed
 *   indeterminate — shimmer + spinner + current action label (total and steps absent)
 *
 * Optional live-log slot streams log_tail[] lines and is collapsible by the user.
 * Done and error end states are supported; error exposes a retry affordance.
 * Closing the container does NOT cancel the underlying operation — the component
 * is purely presentational with no fetch/SSE ownership.
 *
 * Normalized payload shape (AC7):
 *   { status, mode, current, done, total, steps[], log_tail[], result, error,
 *     est_remaining_minutes }
 *
 * Exported:
 *   renderProgressActivity(payload, opts?)  → HTML string
 *   updateProgressActivityLog(rootId, logTail, colorize?)  → DOM patch (log only)
 *   paToggleLog(rootId)  → toggle collapse state of mounted log stream
 *   PA_CSS  → string of component CSS (inject once via injectProgressActivityCss())
 *   injectProgressActivityCss()  → idempotent <style> injector
 */

// ── HTML escaping ─────────────────────────────────────────────────────────────

function _e(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Mode detection ────────────────────────────────────────────────────────────

function _detectMode(payload) {
  if (payload.mode) return payload.mode;
  if (Array.isArray(payload.steps) && payload.steps.length > 0)
    return "stepper";
  if (payload.total != null) return "bar";
  return "indeterminate";
}

// ── Step icon glyphs ──────────────────────────────────────────────────────────

const _STEP_ICON = {
  pending: "○",
  checking: "…",
  running: "●",
  done: "✓",
  pass: "✓",
  fixed: "✓",
  failed: "✗",
};

// ── Log type → CSS class ──────────────────────────────────────────────────────

const _LOG_CLASS = {
  dispatch: "pa-log-dispatch",
  success: "pa-log-success",
  warn: "pa-log-warn",
  fail: "pa-log-fail",
};

// ── Section builders ──────────────────────────────────────────────────────────

function _barHtml(p) {
  const done = Number(p.done ?? 0);
  const total = Number(p.total ?? 0);
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
  const shimmer = pct < 100 ? '<div class="pa-bar-shimmer"></div>' : "";
  const countsHtml =
    total > 0 ? `<span class="pa-counts">${done} of ${total}</span>` : "";
  const estHtml =
    p.est_remaining_minutes != null
      ? `<span class="pa-est-rem">~${_e(p.est_remaining_minutes)}m remaining</span>`
      : "";
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
  const rows = steps
    .map((s) => {
      const state = _e(s.state || "pending");
      const icon = _STEP_ICON[s.state] || "○";
      const note = s.note
        ? `<span class="pa-step-note">${_e(s.note)}</span>`
        : "";
      return `<div class="pa-step pa-step--${state}" id="pa-step-${_e(s.key || "")}">
      <span class="pa-step-icon" aria-hidden="true">${icon}</span>
      <div class="pa-step-content">
        <span class="pa-step-name">${_e(s.label || s.key || "")}</span>
        ${note}
      </div>
    </div>`;
    })
    .join("");
  return `<div class="pa-steps">${rows}</div>`;
}

function _indeterminateHtml(p) {
  const cur = p.current
    ? `<span class="pa-current">${_e(p.current)}</span>`
    : "";
  return `<div class="pa-indeterminate">
    <div class="pa-spinner"></div>
    ${cur}
    <div class="pa-indet-shimmer"></div>
  </div>`;
}

function _doneHtml(p) {
  const txt = p.result ? _e(p.result) : "Done";
  return `<div class="pa-done">
    <span class="pa-done-icon" aria-hidden="true">✓</span>
    <span class="pa-result">${txt}</span>
  </div>`;
}

function _errorHtml(p, opts) {
  const msg = p.error || "An error occurred.";
  const fn = opts.retryFn || "";
  const retryBtn = fn
    ? `<button class="pa-retry-btn" type="button" onclick="${_e(fn)}()">Retry</button>`
    : `<button class="pa-retry-btn" type="button">Retry</button>`;
  return `<div class="pa-error">
    <div class="pa-error-msg">${_e(msg)}</div>
    ${retryBtn}
  </div>`;
}

function _logLineHtml(line, colorize) {
  if (!line) return "";
  if (typeof line === "string") {
    const msg = colorize ? colorize(line, "") : _e(line);
    return `<div class="pa-log-line"><span class="pa-log-msg">${msg}</span></div>`;
  }
  const cls = _LOG_CLASS[line.type] || "";
  const tsHtml =
    line.timestamp && line.timestamp !== "—"
      ? `<span class="pa-log-time">${_e(line.timestamp)}</span>`
      : "";
  const msg = colorize
    ? colorize(String(line.message || ""), "")
    : _e(line.message || "");
  return `<div class="pa-log-line${cls ? " " + cls : ""}">${tsHtml}<span class="pa-log-msg">${msg}</span></div>`;
}

function _logSectionHtml(p, rootId, opts) {
  const lines = Array.isArray(p.log_tail) ? p.log_tail : [];
  const colorize = opts.colorize || null;
  const collapsed = opts.logCollapsed ? " pa-log-collapsed" : "";
  const streamId = rootId ? ` id="pa-log-stream-${_e(rootId)}"` : "";
  const toggleArg = rootId ? `'${_e(rootId)}'` : "''";
  const agentSlot = opts.logHeaderAgentHtml || "";

  const emptyMsg =
    '<div class="pa-log-line" style="color:var(--text-sub)">Waiting for log…</div>';
  const linesHtml = lines.length
    ? lines.map((l) => _logLineHtml(l, colorize)).join("")
    : emptyMsg;

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

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Render a ProgressActivity component to an HTML string.
 *
 * @param {Object}  payload
 * @param {Object}  [opts]
 * @param {string}  [opts.id]                Root element id (for live patching and log toggle).
 * @param {Function}[opts.colorize]          Log colorizer: (message, repo) → html string.
 * @param {string}  [opts.retryFn]           Global fn name called when retry button is clicked.
 * @param {boolean} [opts.logCollapsed]      Start log stream in collapsed state.
 * @param {boolean} [opts.hideLog]           Suppress the log slot entirely.
 * @param {string}  [opts.logHeaderAgentHtml] Extra HTML injected into the log header (agent badges).
 * @returns {string}
 */
export function renderProgressActivity(payload, opts) {
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

  const showLog =
    !opts.hideLog &&
    status !== "done" &&
    status !== "error" &&
    (status === "running" || Array.isArray(payload.log_tail));
  const logHtml = showLog ? _logSectionHtml(payload, rootId, opts) : "";

  return `<div class="pa-root pa-mode-${_e(mode)} pa-status-${_e(status)}"${idAttr}>${bodyHtml}${logHtml}</div>`;
}

/**
 * Patch only the log-stream section of a mounted ProgressActivity.
 * Call this on every live-poll tick instead of a full re-render.
 *
 * @param {string}   rootId    The id passed to renderProgressActivity({ id: rootId }).
 * @param {Array}    logTail   Fresh log_tail array.
 * @param {Function} [colorize] Log colorizer: (message, repo) → html.
 */
export function updateProgressActivityLog(rootId, logTail, colorize) {
  if (typeof document === "undefined") return;
  const streamEl = document.getElementById("pa-log-stream-" + rootId);
  if (!streamEl) return;
  const lines = Array.isArray(logTail) ? logTail : [];
  const emptyMsg =
    '<div class="pa-log-line" style="color:var(--text-sub)">Waiting for log…</div>';
  streamEl.innerHTML = lines.length
    ? lines.map((l) => _logLineHtml(l, colorize || null)).join("")
    : emptyMsg;
  streamEl.scrollTop = streamEl.scrollHeight;
}

/**
 * Toggle the collapsed state of the log stream.
 * Wired to onclick in the rendered log-header; also usable programmatically.
 *
 * @param {string} rootId
 */
export function paToggleLog(rootId) {
  if (typeof document === "undefined") return;
  const el = document.getElementById("pa-log-stream-" + rootId);
  if (el) el.classList.toggle("pa-log-collapsed");
}

// ── Component CSS (AC8: all colors via design tokens — no hardcoded hex) ─────

export const PA_CSS = `
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

/* ── Bar mode ── */
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

/* ── Stepper mode ── */
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

/* ── Indeterminate mode ── */
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

/* ── Done end state ── */
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

/* ── Error end state ── */
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

/* ── Live-log slot ── */
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

/** Inject PA_CSS into the document head (idempotent). */
let _cssInjected = false;
export function injectProgressActivityCss() {
  if (_cssInjected || typeof document === "undefined") return;
  _cssInjected = true;
  const style = document.createElement("style");
  style.dataset.paStyle = "1";
  style.textContent = PA_CSS;
  document.head.appendChild(style);
}
