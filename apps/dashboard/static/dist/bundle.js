"use strict";
(() => {
  // apps/dashboard/static/src/logpanel.js
  var AGENT_NAMES = ["coder", "tester", "reviewer", "documenter", "estimator", "BA"];
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
  function colorizeLogLine(text, repo) {
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

  // apps/dashboard/static/src/index.js
  const root = typeof window !== "undefined" ? window : globalThis;
  root.colorizeLogLine = colorizeLogLine;
  root.escapeLogHtml = escapeLogHtml;
  root.extractRaw = extractRaw;
  root.AGENT_NAMES = AGENT_NAMES;
})();
//# sourceMappingURL=bundle.js.map
