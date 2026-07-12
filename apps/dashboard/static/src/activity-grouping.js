/**
 * Groups Activity feed events by sprint run (issue #1853).
 *
 * Pure data-transformation — no DOM access, no project.html globals.
 * Exposed on window.evlGroupEventsByRun via bundle entry (index.js).
 */

const _SPRINT_TYPE_PREFIX = 'sprint_';

/** Extract the primary sprint label from an event, or null if none. */
function _evlGetSprintLabel(ev) {
  // Top-level field set by JSONL lifecycle events
  if (ev.sprint_label) return ev.sprint_label;
  const d = ev.detail || {};
  if (d.sprint_label) return d.sprint_label;
  if (d.sprint_id) return d.sprint_id;
  // For sprint lifecycle events (sprint_run, sprint_started, …) the target IS
  // the sprint label. Guard to avoid treating PR/issue numbers as sprint labels.
  if ((ev.type || '').startsWith(_SPRINT_TYPE_PREFIX) && ev.target) return ev.target;
  return null;
}

function _isRunError(ev) {
  const type = ev.type || '';
  if (type === 'ticket_failed') return true;
  if (type === 'agent_finished') {
    const st = (ev.detail || {}).status || '';
    return st === 'error' || st === 'timed_out';
  }
  return false;
}

/**
 * Groups events by sprint run (or "Dashboard / other" per day for unassociated
 * events). Expects events sorted newest-first (as returned by the API).
 *
 * Returns an array of group objects in first-seen order (newest run first):
 *   {
 *     key:          string   — unique group key
 *     sprint_label: string   — sprint label ('' for other groups)
 *     isOther:      boolean  — true for "Dashboard / other" groups
 *     dayKey:       string   — YYYY-MM-DD (only populated for isOther groups)
 *     events:       array    — all events in this group (unfiltered)
 *     hasErrors:    boolean  — true if any event in group is an error
 *     errorCount:   number   — count of error events
 *     outcome:      string|null — 'completed' | 'failed' | null (unknown/running)
 *   }
 */
export function evlGroupEventsByRun(events) {
  if (!events || events.length === 0) return [];

  const order = [];
  const map = {};

  for (const ev of events) {
    const sl = _evlGetSprintLabel(ev);

    if (sl) {
      const key = 'sprint:' + sl;
      if (!map[key]) {
        map[key] = {
          key,
          sprint_label: sl,
          isOther: false,
          dayKey: null,
          events: [],
          hasErrors: false,
          errorCount: 0,
          outcome: null,
        };
        order.push(key);
      }
      const g = map[key];
      g.events.push(ev);
      if (_isRunError(ev)) {
        g.hasErrors = true;
        g.errorCount += 1;
      }
      if (ev.type === 'sprint_finished') {
        const d = ev.detail || {};
        const failed = d.failed || 0;
        g.outcome = failed > 0 ? 'failed' : 'completed';
      }
    } else {
      const ts = ev.timestamp || '';
      const dayKey = ts.substring(0, 10) || 'unknown';
      const key = 'other:' + dayKey;
      if (!map[key]) {
        map[key] = {
          key,
          sprint_label: '',
          isOther: true,
          dayKey,
          events: [],
          hasErrors: false,
          errorCount: 0,
          outcome: null,
        };
        order.push(key);
      }
      map[key].events.push(ev);
    }
  }

  return order.map(k => map[k]);
}
