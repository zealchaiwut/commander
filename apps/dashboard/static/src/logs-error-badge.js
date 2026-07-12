/**
 * Logs error badge helpers (issue #1857).
 *
 * Pure functions — no DOM access, no globals.
 * Exposed on window via index.js so project.html inline scripts can call them.
 */

/** localStorage key for the last-visit timestamp for a given project slug. */
export function logsErrorBadgeKey(slug) {
  return "commander_logs_last_visit_" + slug;
}

/** Read last-visit ISO timestamp from localStorage. Returns null if unset or unavailable. */
export function logsReadLastVisit(slug) {
  try {
    return (
      (typeof localStorage !== "undefined" &&
        localStorage.getItem(logsErrorBadgeKey(slug))) ||
      null
    );
  } catch (_) {
    return null;
  }
}

/** Write current time as last-visit timestamp to localStorage. */
export function logsWriteLastVisit(slug) {
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(logsErrorBadgeKey(slug), new Date().toISOString());
    }
  } catch (_) {}
}

/**
 * Returns true when the event is error-severity.
 * Mirrors the inline _evlIsError() in project.html.
 */
export function evlIsErrorEvent(ev) {
  const type = ev.type || "";
  if (type === "ticket_failed") return true;
  if (type === "agent_finished") {
    const st = ((ev.detail || {}).status) || "";
    return st === "error" || st === "timed_out";
  }
  return false;
}

/** Count the number of error-severity events in an array. */
export function logsCountNewErrors(events) {
  return events.filter(evlIsErrorEvent).length;
}

/**
 * Build the URL for /api/projects/{slug}/events.
 * When sinceTs is provided, adds ?since= for a bounded window; otherwise falls
 * back to a flat ?limit=200 window.
 */
export function buildEvlFetchUrl(slug, sinceTs) {
  const base = "/api/projects/" + encodeURIComponent(slug) + "/events";
  if (sinceTs) {
    return base + "?since=" + encodeURIComponent(sinceTs) + "&limit=200";
  }
  return base + "?limit=200";
}
