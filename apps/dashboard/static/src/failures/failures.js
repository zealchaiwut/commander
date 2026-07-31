/* Failures inbox tab (issue #2020).
 *
 * Fetches GET /api/failures?project=<repo>&category=<cat> and renders
 * a tabular inbox of failed agent runs.
 *
 * Exported pure function: fetchFailures(project, category)
 * — importable by the Node --test fetch-spy tester without DOM or globals.
 *
 * AC5 compliance: the exported function is the sole network boundary; the
 * caller (failuresInit) passes _projectData.repo and the selected category.
 */

/* global _projectData */

// ── Pure network helper (exported for tester) ─────────────────────────────────

/**
 * Fetch failures from the API.
 *
 * @param {string} project - full owner/repo string (e.g. "zealchaiwut/commander")
 * @param {string|null} category - optional category filter (e.g. "timed_out")
 * @returns {Promise<Array>} array of failure row objects
 */
export async function fetchFailures(project, category) {
  let url = "/api/failures?project=" + encodeURIComponent(project);
  if (category) url += "&category=" + encodeURIComponent(category);
  const resp = await fetch(url);
  if (!resp.ok) throw new Error("HTTP " + resp.status);
  return resp.json();
}

// ── Rendering helpers ─────────────────────────────────────────────────────────

function _escHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function _fmtTs(ts) {
  if (!ts) return "";
  try {
    const d = new Date(ts);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch (_) {
    return String(ts);
  }
}

function _renderRows(rows) {
  if (!rows || rows.length === 0) {
    return '<tr><td colspan="7" class="fbox-empty">No failures</td></tr>';
  }
  return rows
    .map(function (r) {
      const issueLink = r.issue_number
        ? '<span class="fbox-issue">#' + _escHtml(r.issue_number) + "</span>"
        : "<span>—</span>";
      const sprint = r.sprint_label ? _escHtml(r.sprint_label) : "—";
      const agent = r.agent ? _escHtml(r.agent) : "—";
      const category = r.category ? _escHtml(r.category) : "—";
      const reason = r.reason
        ? '<span title="' +
          _escHtml(r.reason) +
          '">' +
          _escHtml(r.reason.length > 60 ? r.reason.slice(0, 60) + "…" : r.reason) +
          "</span>"
        : "—";
      const ts = _fmtTs(r.ts);
      const logCell = r.log_url
        ? '<a class="fbox-log-link" href="' +
          _escHtml(r.log_url) +
          '" target="_blank" rel="noopener">View log</a>'
        : "—";
      return (
        "<tr>" +
        "<td>" + issueLink + "</td>" +
        "<td>" + sprint + "</td>" +
        "<td>" + agent + "</td>" +
        '<td><span class="fbox-cat">' + category + "</span></td>" +
        "<td>" + reason + "</td>" +
        '<td class="fbox-ts">' + ts + "</td>" +
        "<td>" + logCell + "</td>" +
        "</tr>"
      );
    })
    .join("");
}

function _setLoading(el, msg) {
  el.innerHTML =
    '<div class="fbox-state"><i class="ti ti-loader fbox-spinner"></i>' +
    _escHtml(msg) +
    "</div>";
}

function _setError(el, msg) {
  el.innerHTML =
    '<div class="fbox-state fbox-state-error"><i class="ti ti-alert-circle"></i>' +
    _escHtml(msg) +
    "</div>";
}

// ── Category filter ───────────────────────────────────────────────────────────

let _currentCategory = "";

function _getCategorySelect() {
  return document.getElementById("fbox-cat-select");
}

function _getProject() {
  return typeof _projectData !== "undefined" && _projectData
    ? _projectData.repo || ""
    : "";
}

// ── Public init (called by switchTab) ────────────────────────────────────────

/**
 * Initialise or refresh the Failures tab.
 * Called by switchTab('failures') — safe to call multiple times.
 */
export function failuresInit() {
  const root = document.getElementById("fbox-root");
  if (!root) return;

  const project = _getProject();
  if (!project) {
    _setError(root, "No project selected.");
    return;
  }

  _setLoading(root, "Loading failures…");

  const cat = _currentCategory;
  fetchFailures(project, cat)
    .then(function (rows) {
      root.innerHTML =
        '<div class="fbox-table-wrap"><table class="fbox-table">' +
        "<thead><tr>" +
        "<th>Issue</th>" +
        "<th>Sprint</th>" +
        "<th>Agent</th>" +
        "<th>Category</th>" +
        "<th>Reason</th>" +
        "<th>Time</th>" +
        "<th>Log</th>" +
        "</tr></thead>" +
        "<tbody id=\"fbox-tbody\">" +
        _renderRows(rows) +
        "</tbody>" +
        "</table></div>";
    })
    .catch(function (err) {
      _setError(root, "Failed to load failures");
    });
}

/**
 * Category filter change handler — called from project.html inline onclick.
 * Re-fetches (or re-filters) by the selected category.
 */
export function failuresCategoryChange(value) {
  _currentCategory = value || "";
  failuresInit();
}
