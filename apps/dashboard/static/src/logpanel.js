/* Log-panel tokenizer module (issue #796 — first esbuild module).
 *
 * Extracted from static/log-colorize.js (issue #765) as the first real module
 * behind the esbuild pipeline. Source-of-truth for log-line colorization lives
 * here now; the bundle (static/dist/bundle.js) re-exposes these helpers on
 * `window` so project.html keeps its historical global API unchanged.
 *
 * colorizeLogLine() always escapes the raw log text FIRST (no injected markup
 * is ever executed), then tokenizes the escaped string in a single pass:
 *   - `#NNN` issue refs  → clickable GitHub issue chips
 *   - the six known agent names (word-bounded, case-insensitive) → per-agent
 *     color chips (coder=blue, tester=green, reviewer=purple, documenter=amber,
 *     estimator=teal, BA=pink — colors live in CSS classes in project.html).
 *
 * A single combined regex pass guarantees the chip markup we inject is never
 * itself re-tokenized, and keeps tokenization O(n) over a 5 000-line log.
 */

export const AGENT_NAMES = ['coder', 'tester', 'reviewer', 'documenter', 'estimator', 'BA'];

export function escapeLogHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// #NNN issue ref OR a word-bounded, case-insensitive agent name. Run over the
// ALREADY-ESCAPED string; replace() does not re-scan its own output, so the
// chip HTML we emit is never re-matched.
const TOKEN_RE = /(#\d+)|\b(coder|tester|reviewer|documenter|estimator|BA)\b/gi;

/**
 * Extract the display text from a log line.
 *
 * Lines written by envelope_subprocess_line() are JSON objects with a `.raw`
 * field that contains the original agent output.  All other lines (plain text,
 * structured manager events) are returned as-is.  This preserves pre-migration
 * rendering for old log files while transparently unwrapping new envelopes.
 */
export function extractRaw(text) {
  const s = String(text == null ? '' : text).trim();
  if (s.length === 0 || s[0] !== '{') return s;
  try {
    const obj = JSON.parse(s);
    if (typeof obj.raw === 'string') return obj.raw;
  } catch (_) { /* not JSON — fall through */ }
  return s;
}

/**
 * Classify an event object's severity level.
 * Returns 'error', 'warn', or 'info'.
 */
export function evlSeverity(ev) {
  if (!ev) return 'info';
  const type = ev.type || '';
  const detail = ev.detail || {};
  if (type === 'ticket_failed') return 'error';
  if (type === 'agent_finished') {
    const st = detail.status || '';
    if (st === 'error' || st === 'timed_out') return 'error';
    if (st === 'partial') return 'warn';
  }
  return 'info';
}

/**
 * Render a severity chip HTML string.
 * @param {'error'|'warn'|'info'} sev
 * @param {function} [esc] - HTML escape function; defaults to escapeLogHtml
 * @returns {string}
 */
export function evlSevChipHtml(sev, esc) {
  const e = esc || escapeLogHtml;
  const label = sev === 'error' ? 'ERROR' : sev === 'warn' ? 'WARN' : 'INFO';
  return '<span class="evl-sev-chip evl-sev-chip--' + e(sev) + '">' + label + '</span>';
}

export function colorizeLogLine(text, repo) {
  const escaped = escapeLogHtml(extractRaw(text));
  return escaped.replace(TOKEN_RE, function (match, issue, agent) {
    if (issue) {
      const num = issue.slice(1);
      const href = repo ? 'https://github.com/' + repo + '/issues/' + num : '#';
      return '<a class="log-chip log-chip--issue" href="' + escapeLogHtml(href) +
        '" target="_blank" rel="noopener">' + issue + '</a>';
    }
    const key = agent.toLowerCase();
    return '<span class="log-chip log-chip--agent log-agent-' + key + '">' +
      agent + '</span>';
  });
}
