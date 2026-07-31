/* Brain tab — FTS5 search over docs + panel data (issue #2028).
 *
 * Fetches GET /api/brain/search?q=<query> and renders keyword-search hits.
 * On tab init, loads panel data from GET /api/brain/panels for the four
 * pre-built panels: recent decisions, open DECISION items, last sprint's
 * learnings, backlog-with-rationale.
 *
 * Exported pure functions (importable by the Node --test fetch-spy tester):
 *   fetchBrainSearch(q)   — network boundary for search
 *   fetchBrainPanels()    — network boundary for panels
 *
 * AC4 compliance: exported functions are the sole network boundaries; callers
 * (brainInit, brainSearch) pass only primitives so tests can intercept fetch.
 */

/* global _projectData */

// ── Pure network helpers (exported for tester) ────────────────────────────────

/**
 * Fetch brain search results from the API.
 *
 * @param {string} q - keyword query
 * @param {string|null} project - optional owner/repo string
 * @returns {Promise<Array>} array of hit objects {path, source, snippet}
 */
export async function fetchBrainSearch(q, project) {
  let url = "/api/brain/search?q=" + encodeURIComponent(q);
  if (project) url += "&project=" + encodeURIComponent(project);
  const resp = await fetch(url);
  if (!resp.ok) throw new Error("HTTP " + resp.status);
  return resp.json();
}

/**
 * Fetch pre-built panel data from the API.
 *
 * @param {string|null} project - optional owner/repo string
 * @returns {Promise<Object>} {recent_decisions, open_decisions, last_learnings, backlog_rationale}
 */
export async function fetchBrainPanels(project) {
  let url = "/api/brain/panels";
  if (project) url += "?project=" + encodeURIComponent(project);
  const resp = await fetch(url);
  if (!resp.ok) throw new Error("HTTP " + resp.status);
  return resp.json();
}

// ── HTML helpers ──────────────────────────────────────────────────────────────

function _esc(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function _sourceLabel(source) {
  const map = {
    decisions: "Decision",
    "bulk-create": "Bulk create",
    retros: "Retro",
    docs: "Docs",
  };
  return map[source] || source;
}

function _sourceClass(source) {
  return "brain-badge brain-badge-" + source;
}

// ── Search result rendering ───────────────────────────────────────────────────

function _renderHits(hits) {
  if (!hits || hits.length === 0) {
    return '<div class="brain-empty">No results found.</div>';
  }
  return hits
    .map(function (h) {
      const badge =
        '<span class="' +
        _sourceClass(h.source) +
        '">' +
        _esc(_sourceLabel(h.source)) +
        "</span>";
      const path = _esc(h.path || "");
      const snippet = _esc(h.snippet || "");
      return (
        '<div class="brain-hit">' +
        '<div class="brain-hit-header">' +
        badge +
        '<span class="brain-hit-path">' +
        path +
        "</span>" +
        "</div>" +
        '<div class="brain-hit-snippet">' +
        snippet +
        "</div>" +
        "</div>"
      );
    })
    .join("");
}

// ── Panel rendering ───────────────────────────────────────────────────────────

function _renderRecentDecisions(items) {
  if (!items || items.length === 0) {
    return "<p>No decisions found.</p>";
  }
  return items
    .map(function (d) {
      return (
        '<div class="brain-panel-item">' +
        '<div class="brain-panel-item-title">' +
        _esc(d.title || d.path) +
        "</div>" +
        (d.decision
          ? '<div class="brain-panel-item-sub">' + _esc(d.decision) + "</div>"
          : "") +
        "</div>"
      );
    })
    .join("");
}

function _renderOpenDecisions(items) {
  if (!items || items.length === 0) {
    return "<p>No open decision items found.</p>";
  }
  return items
    .map(function (d) {
      return (
        '<div class="brain-panel-item">' +
        '<div class="brain-panel-item-path">' +
        _esc(d.path) +
        "</div>" +
        '<div class="brain-panel-item-line">' +
        _esc(d.line) +
        "</div>" +
        "</div>"
      );
    })
    .join("");
}

function _renderLastLearnings(items) {
  if (!items || items.length === 0) {
    return "<p>No learnings recorded.</p>";
  }
  return (
    "<ul>" +
    items.map(function (l) {
      return "<li>" + _esc(l) + "</li>";
    }).join("") +
    "</ul>"
  );
}

function _renderBacklogRationale(items) {
  if (!items || items.length === 0) {
    return "<p>No ADR entries found.</p>";
  }
  return items
    .map(function (d) {
      return (
        '<div class="brain-panel-item">' +
        '<div class="brain-panel-item-title">' +
        _esc(d.title || d.path) +
        "</div>" +
        "</div>"
      );
    })
    .join("");
}

// ── State ─────────────────────────────────────────────────────────────────────

let _panelsLoaded = false;

function _getProject() {
  return typeof _projectData !== "undefined" && _projectData
    ? _projectData.repo || null
    : null;
}

// ── Search handler ────────────────────────────────────────────────────────────

/**
 * Run a search and render results into the brain-results container.
 * Called from the search form submit handler in project.html.
 */
export function brainSearch() {
  const inputEl = document.getElementById("brain-search-input");
  const resultsEl = document.getElementById("brain-results");
  if (!inputEl || !resultsEl) return;

  const q = (inputEl.value || "").trim();
  if (!q) {
    resultsEl.innerHTML = "";
    return;
  }

  resultsEl.innerHTML =
    '<div class="brain-state"><i class="ti ti-loader brain-spinner"></i>Searching…</div>';

  fetchBrainSearch(q, _getProject())
    .then(function (hits) {
      resultsEl.innerHTML = _renderHits(hits);
    })
    .catch(function () {
      resultsEl.innerHTML =
        '<div class="brain-state brain-state-error"><i class="ti ti-alert-circle"></i>Search failed.</div>';
    });
}

// ── Public init ───────────────────────────────────────────────────────────────

/**
 * Initialise or refresh the Brain tab.
 * Called by switchTab('brain') — safe to call multiple times.
 */
export function brainInit() {
  const root = document.getElementById("brain-root");
  if (!root) return;

  // Only load panels once; search results are driven by the search box
  if (_panelsLoaded) return;
  _panelsLoaded = true;

  const panelsEl = document.getElementById("brain-panels");
  if (!panelsEl) return;

  panelsEl.innerHTML =
    '<div class="brain-state"><i class="ti ti-loader brain-spinner"></i>Loading panels…</div>';

  fetchBrainPanels(_getProject())
    .then(function (data) {
      panelsEl.innerHTML =
        '<div class="brain-panel-grid">' +
        '<section class="brain-panel">' +
        '<h3 class="brain-panel-title"><i class="ti ti-book"></i> Recent Decisions</h3>' +
        '<div class="brain-panel-body">' +
        _renderRecentDecisions(data.recent_decisions) +
        "</div>" +
        "</section>" +
        '<section class="brain-panel">' +
        '<h3 class="brain-panel-title"><i class="ti ti-arrow-right"></i> Open ⟶ DECISION Items</h3>' +
        '<div class="brain-panel-body">' +
        _renderOpenDecisions(data.open_decisions) +
        "</div>" +
        "</section>" +
        '<section class="brain-panel">' +
        '<h3 class="brain-panel-title"><i class="ti ti-bulb"></i> Last Sprint Learnings</h3>' +
        '<div class="brain-panel-body">' +
        _renderLastLearnings(data.last_learnings) +
        "</div>" +
        "</section>" +
        '<section class="brain-panel">' +
        '<h3 class="brain-panel-title"><i class="ti ti-list-check"></i> Backlog with Rationale</h3>' +
        '<div class="brain-panel-body">' +
        _renderBacklogRationale(data.backlog_rationale) +
        "</div>" +
        "</section>" +
        "</div>";
    })
    .catch(function () {
      panelsEl.innerHTML =
        '<div class="brain-state brain-state-error"><i class="ti ti-alert-circle"></i>Failed to load panels.</div>';
    });
}
