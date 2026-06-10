/* Log line colorization (issue #765).
 *
 * Shared by every log surface — the live log panel, the Activity-tab run rows,
 * and the post-run summary log excerpt — so all three render identical chips.
 *
 * colorizeLogLine() always escapes the raw log text FIRST (AC-4: no injected
 * markup is ever executed), then tokenizes the escaped string in a single pass:
 *   - `#NNN` issue refs  → clickable GitHub issue chips
 *   - the six known agent names (word-bounded, case-insensitive) → per-agent
 *     color chips (coder=blue, tester=green, reviewer=purple, documenter=amber,
 *     estimator=teal, BA=pink — colors live in CSS classes in project.html).
 *
 * A single combined regex pass guarantees the chip markup we inject is never
 * itself re-tokenized, and keeps tokenization O(n) over a 5 000-line log (AC-5).
 */
(function (root) {
  'use strict';

  var AGENT_NAMES = ['coder', 'tester', 'reviewer', 'documenter', 'estimator', 'BA'];

  function escapeLogHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // #NNN issue ref OR a word-bounded, case-insensitive agent name. Run over the
  // ALREADY-ESCAPED string; replace() does not re-scan its own output, so the
  // chip HTML we emit is never re-matched.
  var TOKEN_RE = /(#\d+)|\b(coder|tester|reviewer|documenter|estimator|BA)\b/gi;

  function colorizeLogLine(text, repo) {
    var escaped = escapeLogHtml(text);
    return escaped.replace(TOKEN_RE, function (match, issue, agent) {
      if (issue) {
        var num = issue.slice(1);
        var href = repo ? 'https://github.com/' + repo + '/issues/' + num : '#';
        return '<a class="log-chip log-chip--issue" href="' + escapeLogHtml(href) +
          '" target="_blank" rel="noopener">' + issue + '</a>';
      }
      var key = agent.toLowerCase();
      return '<span class="log-chip log-chip--agent log-agent-' + key + '">' +
        agent + '</span>';
    });
  }

  root.colorizeLogLine = colorizeLogLine;
  root.escapeLogHtml = escapeLogHtml;

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      colorizeLogLine: colorizeLogLine,
      escapeLogHtml: escapeLogHtml,
      AGENT_NAMES: AGENT_NAMES,
    };
  }
})(typeof window !== 'undefined' ? window : this);
