import { renderProgressActivity } from "./progress-activity.js";

export const BOARD_OVERLAY_PA_ID = "board-overlay-pa";

const _payloadById = new Map();
const _MAX_LOG_LINES = 200;

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

function _renderIntoHost(hostEl, payload, opts) {
  if (!hostEl) return;
  const renderOpts = opts || {};
  hostEl.innerHTML = renderProgressActivity(payload, renderOpts);
}

export function mountProgressActivity(host, payload, opts) {
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

export function getProgressActivityPayload(host) {
  const hostEl = _resolveHost(host);
  const paId = hostEl ? _resolvePaId(hostEl) : typeof host === "string" ? host : null;
  if (!paId) return null;
  const payload = _payloadById.get(paId);
  return payload ? _snapshot(payload) : null;
}

export function patchProgressActivity(host, patch, opts) {
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

export function patchProgressActivityStep(host, stepKey, state, note, opts) {
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
    Object.assign({}, opts || {}, { id: paId }),
  );
}

export function appendProgressActivityLog(host, line, type, opts) {
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
        timestamp: new Date().toLocaleTimeString(),
      });
    } else {
      nextTail.push(line);
    }
  }
  const logTail = nextTail.slice(-_MAX_LOG_LINES);
  return patchProgressActivity(
    hostEl,
    { log_tail: logTail },
    Object.assign({}, opts || {}, { id: paId }),
  );
}

export function unmountProgressActivity(host) {
  const hostEl = _resolveHost(host);
  if (!hostEl) return;
  const paId = _resolvePaId(hostEl);
  hostEl.innerHTML = "";
  hostEl.hidden = true;
  if (hostEl.dataset) delete hostEl.dataset.paId;
  _payloadById.delete(paId);
}
