/* Commander feature flags — loaded from /api/environment (config.py). */

/** @type {{ signoff?: boolean, planning?: boolean } | null} */
let _features = null;

export function commanderFeatures() {
  return _features || {};
}

export function signoffEnabled() {
  return commanderFeatures().signoff === true;
}

export function planningEnabled() {
  return commanderFeatures().planning === true;
}

/** Fetch flags and hide disabled UI surfaces. */
export async function loadCommanderFeatures() {
  try {
    const res = await fetch("/api/environment", { cache: "no-store" });
    if (!res.ok) throw new Error(String(res.status));
    const data = await res.json();
    _features = data.features || {};
  } catch {
    _features = { signoff: false, planning: false };
  }
  const root = typeof window !== "undefined" ? window : globalThis;
  root._commanderFeatures = _features;
  applyFeatureFlags();
  return _features;
}

function _hide(el) {
  if (!el) return;
  el.classList.add("hidden");
  el.setAttribute("aria-hidden", "true");
}

export function applyFeatureFlags() {
  if (!planningEnabled()) {
    _hide(document.getElementById("smgmt-plan-next-btn"));
  }
  if (!signoffEnabled()) {
    _hide(document.getElementById("snav-signoff"));
  }
}
