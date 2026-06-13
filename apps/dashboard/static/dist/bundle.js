(() => {
  // apps/dashboard/static/src/logpanel.js
  var AGENT_NAMES = ["coder", "tester", "reviewer", "documenter", "estimator", "BA"];
  function escapeLogHtml(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  var TOKEN_RE = /(#\d+)|\b(coder|tester|reviewer|documenter|estimator|BA)\b/gi;
  function extractRaw(text) {
    const s = String(text == null ? "" : text).trim();
    if (s.length === 0 || s[0] !== "{")
      return s;
    try {
      const obj = JSON.parse(s);
      if (typeof obj.raw === "string")
        return obj.raw;
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

  // apps/dashboard/static/src/sprint-board/state.js
  globalThis._rrLabel ??= null;
  globalThis._rrVersionedLabel ??= null;
  globalThis._fsLabel ??= null;
  globalThis._fsPreview ??= null;
  globalThis._bcLabel ??= null;
  globalThis._bcPreview ??= null;
  globalThis._pfCurrentLabel ??= null;
  globalThis._pfCurrentRepo ??= null;
  globalThis._pfState ??= "idle";
  globalThis._pfDagData ??= null;
  globalThis._pfWarnings ??= null;
  globalThis._pfCycle ??= null;
  globalThis._pfFlags ??= null;
  globalThis._pfSelectedIds ??= /* @__PURE__ */ new Set();
  globalThis._smgmtMoveLock ??= false;
  globalThis._smgmtGhostNextNum ??= null;

  // apps/dashboard/static/src/sprint-board/scheduled-run.js
  var _schedMap = {};
  function _smgmtSchedToggleHtml2(label) {
    const on = !!_schedMap[label];
    const id = `sched-toggle-${label}`;
    return `<label class="smgmt-sched-toggle" title="Auto-run this sprint at the project's scheduled time">
    <input type="checkbox" id="${escHtml(id)}" ${on ? "checked" : ""}
      onchange="smgmtToggleRunOnSchedule('${escHtml(label)}', this)"
      aria-label="Run sprint ${escHtml(label)} on schedule">
    <span>Run on schedule</span>
  </label>`;
  }
  async function smgmtToggleRunOnSchedule(label, el) {
    const repo = typeof _smgmtRepo === "function" ? _smgmtRepo() : null;
    if (!repo)
      return;
    const enabled = !!(el && el.checked);
    _schedMap[label] = enabled;
    try {
      const res = await fetch("/api/scheduler/sprints", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: repo, sprint_label: label, enabled })
      });
      if (!res.ok)
        throw new Error(`HTTP ${res.status}`);
    } catch (e) {
      _schedMap[label] = !enabled;
      if (el)
        el.checked = !enabled;
      if (typeof _smgmtShowToast === "function") {
        _smgmtShowToast("Could not update schedule: " + (e.message || e));
      }
    }
  }
  async function _smgmtHydrateSchedToggles2(repo) {
    if (!repo)
      return;
    try {
      const res = await fetch(`/api/scheduler/sprints?project=${encodeURIComponent(repo)}`);
      if (!res.ok)
        return;
      const data = await res.json();
      const map = data.run_on_schedule || {};
      for (const k of Object.keys(_schedMap))
        delete _schedMap[k];
      Object.keys(map).forEach((k) => {
        _schedMap[k] = !!map[k];
      });
      Object.keys(map).forEach((label) => {
        const cb = document.getElementById(`sched-toggle-${label}`);
        if (cb)
          cb.checked = !!map[label];
      });
    } catch (_) {
    }
  }

  // apps/dashboard/static/src/sprint-board/rerun-modal.js
  function _rrOpen() {
    _setBodyInert(["rr-backdrop", "rr-modal"]);
    document.getElementById("rr-backdrop").classList.remove("hidden");
    document.getElementById("rr-modal").classList.remove("hidden");
  }
  function _rrClose() {
    document.getElementById("rr-backdrop").classList.add("hidden");
    document.getElementById("rr-modal").classList.add("hidden");
    _clearBodyInert();
    _rrLabel = null;
    _rrVersionedLabel = null;
  }
  function _rrCatClass(cat) {
    if (cat === "UAT")
      return "rr-cat-uat";
    if (cat === "SIT")
      return "rr-cat-sit";
    if (cat === "needs-rework")
      return "rr-cat-rework";
    return "rr-cat-queued";
  }
  function _rrUpdateState() {
    const checkboxes = document.querySelectorAll("#rr-ticket-list input[type=checkbox]");
    const checked = Array.from(checkboxes).filter((c) => c.checked);
    const uatChecked = Array.from(checkboxes).filter((c) => c.checked && c.dataset.cat === "UAT").length;
    const confirmBtn = document.getElementById("rr-confirm-btn");
    if (confirmBtn)
      confirmBtn.disabled = checked.length === 0;
    const warnEl = document.getElementById("rr-uat-warning");
    if (warnEl) {
      if (uatChecked > 0) {
        warnEl.textContent = `${uatChecked} ticket${uatChecked !== 1 ? "s" : ""} in UAT will be re-tested from scratch.`;
      } else {
        warnEl.textContent = "";
      }
    }
  }
  function _rrSelectAll(checked) {
    document.querySelectorAll("#rr-ticket-list input[type=checkbox]").forEach((cb) => {
      cb.checked = checked;
    });
    _rrUpdateState();
  }
  async function smgmtRerunSprint(label) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    _rrLabel = label;
    _rrVersionedLabel = null;
    document.getElementById("rr-modal-title").textContent = `Re-run ${sprintLabelDisplay(label)}?`;
    document.getElementById("rr-loading").classList.remove("hidden");
    document.getElementById("rr-content").classList.add("hidden");
    document.getElementById("rr-error").classList.add("hidden");
    document.getElementById("rr-error").textContent = "";
    const confirmBtn = document.getElementById("rr-confirm-btn");
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Create sprint and run";
    }
    _rrOpen();
    try {
      const res = await fetch(
        `/api/sprints/${encodeURIComponent(label)}/rerun-preview?project=${encodeURIComponent(repo)}`
      );
      if (!res.ok)
        throw new Error(await res.text());
      const preview = await res.json();
      _rrVersionedLabel = preview.suggested_versioned_label;
      document.getElementById("rr-modal-title").textContent = `Re-run ${sprintLabelDisplay(label)} as ${sprintLabelDisplay(_rrVersionedLabel)}?`;
      if (confirmBtn)
        confirmBtn.textContent = `Create & run ${sprintLabelDisplay(_rrVersionedLabel)}`;
      const listEl = document.getElementById("rr-ticket-list");
      if ((preview.tickets || []).length === 0) {
        listEl.innerHTML = '<div style="padding:10px;color:var(--text-muted);font-size:13px">No tickets in this sprint.</div>';
      } else {
        listEl.innerHTML = (preview.tickets || []).map((t) => {
          const checked = t.checked ? "checked" : "";
          const catClass = _rrCatClass(t.category);
          return `<label class="rr-ticket-row">
          <input type="checkbox" ${checked} data-issue="${t.number}" data-cat="${escHtml(t.category)}" onchange="_rrUpdateState()">
          <span class="rr-ticket-num">#${t.number}</span>
          <span class="rr-ticket-title" title="${escHtml(t.title)}">${escHtml(t.title)}</span>
          <span class="rr-ticket-cat ${catClass}">${escHtml(t.category)}</span>
        </label>`;
        }).join("");
      }
      document.getElementById("rr-loading").classList.add("hidden");
      document.getElementById("rr-content").classList.remove("hidden");
      _rrUpdateState();
    } catch (e) {
      document.getElementById("rr-loading").classList.add("hidden");
      const errEl = document.getElementById("rr-error");
      errEl.textContent = "Failed to load preview: " + e.message;
      errEl.classList.remove("hidden");
    }
  }
  async function _rrConfirm() {
    const repo = _smgmtRepo();
    if (!_rrLabel || !repo)
      return;
    const parentLabel = _rrLabel;
    const checkboxes = Array.from(document.querySelectorAll("#rr-ticket-list input[type=checkbox]"));
    const ticketNumbers = checkboxes.filter((c) => c.checked).map((c) => parseInt(c.dataset.issue, 10));
    if (ticketNumbers.length === 0)
      return;
    const confirmBtn = document.getElementById("rr-confirm-btn");
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Creating\u2026";
    }
    try {
      const res = await fetch(
        `/api/sprints/${encodeURIComponent(parentLabel)}/rerun?project=${encodeURIComponent(repo)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ticket_numbers: ticketNumbers, auto_run: false })
        }
      );
      if (!res.ok) {
        let detail = await res.text();
        try {
          const parsed = JSON.parse(detail);
          detail = parsed.detail || detail;
        } catch (_) {
        }
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      const data = await res.json();
      const subLabel = data.sub_label;
      _rrClose();
      if (typeof _smgmtApplyRerunOptimistic === "function") {
        _smgmtApplyRerunOptimistic(parentLabel, subLabel, ticketNumbers);
      }
      await loadSprintMgmt(true);
      const subDisplay = subLabel ? sprintLabelDisplay(subLabel) : "Sub-sprint";
      if (data.errors && data.errors.length > 0) {
        _smgmtShowToast(`${subDisplay} created with label errors \u2014 check GitHub.`);
      } else {
        _smgmtShowToast(`${subDisplay} ready \u2014 confirm run`);
      }
      if (subLabel && typeof smgmtRunSprint === "function") {
        smgmtRunSprint(subLabel);
      }
    } catch (e) {
      const errEl = document.getElementById("rr-error");
      errEl.textContent = "Failed to re-run sprint: " + e.message;
      errEl.classList.remove("hidden");
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = _rrVersionedLabel ? `Create & run ${sprintLabelDisplay(_rrVersionedLabel)}` : "Create sprint and run";
      }
    }
  }

  // apps/dashboard/static/src/sprint-board/finish-modal.js
  function _fsOpen() {
    _setBodyInert(["fs-backdrop", "fs-modal"]);
    document.getElementById("fs-backdrop").classList.remove("hidden");
    document.getElementById("fs-modal").classList.remove("hidden");
  }
  function _fsClose() {
    document.getElementById("fs-backdrop").classList.add("hidden");
    document.getElementById("fs-modal").classList.add("hidden");
    _clearBodyInert();
    _fsLabel = null;
    _fsPreview = null;
  }
  function _fsCatClass(cat) {
    if (cat === "UAT")
      return "rr-cat-uat";
    if (cat === "SIT")
      return "rr-cat-sit";
    if (cat === "needs-rework")
      return "rr-cat-rework";
    if (cat === "sprint-summary")
      return "rr-cat-summary";
    return "rr-cat-queued";
  }
  function _fsSelectAll(checked) {
    document.querySelectorAll("#fs-ticket-list input[type=checkbox]").forEach((cb) => {
      cb.checked = checked;
    });
  }
  async function smgmtFinishSprint(label) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    _fsLabel = label;
    _fsPreview = null;
    const parts = repo.split("/");
    const owner = parts[0];
    const repoName = parts.slice(1).join("/");
    document.getElementById("fs-modal-title").textContent = `Merge ${sprintLabelDisplay(label)}?`;
    document.getElementById("fs-loading").classList.remove("hidden");
    document.getElementById("fs-content").classList.add("hidden");
    document.getElementById("fs-error").classList.add("hidden");
    document.getElementById("fs-error").textContent = "";
    const confirmBtn = document.getElementById("fs-confirm-btn");
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Merge Sprint";
    }
    _fsOpen();
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/finish-preview`
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const preview = await res.json();
      _fsPreview = preview;
      if (preview.conflict_error) {
        throw new Error(preview.conflict_error);
      }
      const listEl = document.getElementById("fs-ticket-list");
      const allTickets = preview.all_tickets || [];
      if (allTickets.length === 0) {
        listEl.innerHTML = '<div style="padding:10px;color:var(--text-muted);font-size:13px">No open tickets in this sprint.</div>';
      } else {
        listEl.innerHTML = allTickets.map((t) => {
          const catClass = _fsCatClass(t.category);
          const catLabel = t.category === "sprint-summary" ? "SUMMARY" : t.category.toUpperCase();
          return `<label class="rr-ticket-row">
          <input type="checkbox" checked data-issue="${t.number}" onchange="">
          <span class="rr-ticket-num">#${t.number}</span>
          <span class="rr-ticket-title" title="${escHtml(t.title)}">${escHtml(t.title)}</span>
          <span class="rr-ticket-cat ${catClass}">${escHtml(catLabel)}</span>
        </label>`;
        }).join("");
      }
      const actionsEl = document.getElementById("fs-actions");
      const actionRows = [];
      const mergeBranches = preview.merge_branches || [];
      for (const mb of mergeBranches) {
        actionRows.push(
          `<div class="fs-action-row"><i class="ti ti-git-merge"></i> Merge <code>${escHtml(mb.head)}</code> \u2192 <code>${escHtml(mb.base)}</code></div>`
        );
      }
      if (preview.sprint_pr) {
        actionRows.push(`<div class="fs-action-row"><i class="ti ti-git-merge"></i> Merge open PR
        <a href="${escHtml(preview.sprint_pr.url)}" target="_blank" rel="noopener">#${preview.sprint_pr.number}</a></div>`);
      }
      actionRows.push('<div class="fs-action-row"><i class="ti ti-circle-check"></i> Close sprint tickets (labels kept)</div>');
      actionsEl.innerHTML = actionRows.join("");
      document.getElementById("fs-loading").classList.add("hidden");
      document.getElementById("fs-content").classList.remove("hidden");
      if (confirmBtn)
        confirmBtn.disabled = false;
    } catch (e) {
      document.getElementById("fs-loading").classList.add("hidden");
      const errEl = document.getElementById("fs-error");
      errEl.textContent = "Failed to load preview: " + e.message;
      errEl.classList.remove("hidden");
    }
  }
  async function _fsConfirm() {
    const repo = _smgmtRepo();
    if (!_fsLabel || !repo || !_fsPreview)
      return;
    const parts = repo.split("/");
    const owner = parts[0];
    const repoName = parts.slice(1).join("/");
    const checkboxes = Array.from(document.querySelectorAll("#fs-ticket-list input[type=checkbox]"));
    const selectedNums = checkboxes.filter((c) => c.checked).map((c) => parseInt(c.dataset.issue, 10));
    const confirmBtn = document.getElementById("fs-confirm-btn");
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Merging\u2026";
    }
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(_fsLabel)}/finish`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            confirmed: true,
            move_non_uat_to: _fsPreview.next_sprint_label,
            selected_ticket_numbers: selectedNums,
            merge_pr: !!_fsPreview.sprint_pr,
            sprint_pr_url: _fsPreview.sprint_pr ? _fsPreview.sprint_pr.url : null
          })
        }
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      _fsClose();
      if (data.errors && data.errors.length > 0) {
        _smgmtShowToast(`Merged with errors \u2014 ${data.closed} closed.`);
      } else {
        let msg = `${sprintLabelDisplay(_fsLabel || "")} merged \u2014 ${data.closed} closed`;
        if (data.moved > 0)
          msg += `, ${data.moved} moved to ${data.next_sprint_label}`;
        _smgmtShowToast(msg + ".");
      }
      await loadSprintMgmt();
    } catch (e) {
      const errEl = document.getElementById("fs-error");
      errEl.textContent = "Failed to finish sprint: " + e.message;
      errEl.classList.remove("hidden");
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = "Merge Sprint";
      }
    }
  }

  // apps/dashboard/static/src/sprint-board/bulk-complete-modal.js
  function _bcOpen() {
    _setBodyInert(["bc-backdrop", "bc-modal"]);
    document.getElementById("bc-backdrop").classList.remove("hidden");
    document.getElementById("bc-modal").classList.remove("hidden");
  }
  function _bcClose() {
    document.getElementById("bc-backdrop").classList.add("hidden");
    document.getElementById("bc-modal").classList.add("hidden");
    _clearBodyInert();
    _bcLabel = null;
    _bcPreview = null;
  }
  function _bcCatClass(cat) {
    if (cat === "UAT")
      return "rr-cat-uat";
    if (cat === "SIT")
      return "rr-cat-sit";
    if (cat === "needs-rework")
      return "rr-cat-rework";
    if (cat === "sprint-summary")
      return "rr-cat-summary";
    return "rr-cat-queued";
  }
  function _bcSelectAll(checked) {
    document.querySelectorAll("#bc-ticket-list input[type=checkbox]").forEach((cb) => {
      cb.checked = checked;
    });
  }
  async function smgmtBulkCompleteSprint(label) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    _bcLabel = label;
    _bcPreview = null;
    const parts = repo.split("/");
    const owner = parts[0];
    const repoName = parts.slice(1).join("/");
    document.getElementById("bc-modal-title").textContent = `Bulk complete ${sprintLabelDisplay(label)}?`;
    document.getElementById("bc-loading").classList.remove("hidden");
    document.getElementById("bc-content").classList.add("hidden");
    document.getElementById("bc-error").classList.add("hidden");
    document.getElementById("bc-error").textContent = "";
    const confirmBtn = document.getElementById("bc-confirm-btn");
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Bulk complete";
    }
    _bcOpen();
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/bulk-complete-preview`
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const preview = await res.json();
      _bcPreview = preview;
      const listEl = document.getElementById("bc-ticket-list");
      const allTickets = preview.all_tickets || [];
      if (allTickets.length === 0) {
        listEl.innerHTML = '<div style="padding:10px;color:var(--text-muted);font-size:13px">No open tickets in this sprint lineage.</div>';
      } else {
        listEl.innerHTML = allTickets.map((t) => {
          const catClass = _bcCatClass(t.category);
          const catLabel = t.category === "sprint-summary" ? "SUMMARY" : t.category.toUpperCase();
          return `<label class="rr-ticket-row">
          <input type="checkbox" checked data-issue="${t.number}" onchange="">
          <span class="rr-ticket-num">#${t.number}</span>
          <span class="rr-ticket-title" title="${escHtml(t.title)}">${escHtml(t.title)}</span>
          <span class="rr-ticket-cat ${catClass}">${escHtml(catLabel)}</span>
        </label>`;
        }).join("");
      }
      const memberCount = (preview.member_labels || []).length;
      const actionsEl = document.getElementById("bc-actions");
      actionsEl.innerHTML = [
        `<div class="fs-action-row"><i class="ti ti-circle-check"></i> Close selected tickets (UAT + summary included)</div>`,
        `<div class="fs-action-row"><i class="ti ti-flag-check"></i> Mark ${memberCount} sprint${memberCount !== 1 ? "s" : ""} completed</div>`
      ].join("");
      document.getElementById("bc-loading").classList.add("hidden");
      document.getElementById("bc-content").classList.remove("hidden");
      if (confirmBtn)
        confirmBtn.disabled = false;
    } catch (e) {
      document.getElementById("bc-loading").classList.add("hidden");
      const errEl = document.getElementById("bc-error");
      errEl.textContent = "Failed to load preview: " + e.message;
      errEl.classList.remove("hidden");
    }
  }
  async function _bcConfirm() {
    const repo = _smgmtRepo();
    if (!_bcLabel || !repo || !_bcPreview)
      return;
    const parts = repo.split("/");
    const owner = parts[0];
    const repoName = parts.slice(1).join("/");
    const checkboxes = Array.from(document.querySelectorAll("#bc-ticket-list input[type=checkbox]"));
    const selectedNums = checkboxes.filter((c) => c.checked).map((c) => parseInt(c.dataset.issue, 10));
    const confirmBtn = document.getElementById("bc-confirm-btn");
    if (confirmBtn) {
      confirmBtn.disabled = true;
      confirmBtn.textContent = "Completing\u2026";
    }
    try {
      const res = await fetch(
        `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(_bcLabel)}/bulk-complete`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            confirmed: true,
            selected_ticket_numbers: selectedNums
          })
        }
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      _bcClose();
      if (data.errors && data.errors.length > 0) {
        _smgmtShowToast(`Bulk complete finished with errors \u2014 ${data.closed} closed.`);
      } else {
        _smgmtShowToast(
          `${sprintLabelDisplay(_bcLabel || "")} bulk completed \u2014 ${data.closed} closed, ${data.completed} marked completed.`
        );
      }
      await loadSprintMgmt();
    } catch (e) {
      const errEl = document.getElementById("bc-error");
      errEl.textContent = "Failed to bulk complete: " + e.message;
      errEl.classList.remove("hidden");
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = "Bulk complete";
      }
    }
  }

  // apps/dashboard/static/src/sprint-board/run-controls.js
  var _pfModels = null;
  function _pfModelShort(m) {
    const s = String(m || "");
    return s.replace(/^claude-/, "") || s;
  }
  function _pfBuildModelsHtml() {
    const m = _pfModels;
    if (!m)
      return "";
    const rows = [];
    rows.push(`<span class="pf-model-pill"><b>Coder</b> ${escHtml(_pfModelShort(m.coder))}</span>`);
    const br = m.tester_by_risk || {};
    const testerTxt = Object.keys(br).length ? Object.keys(br).map((k) => `${k.toLowerCase()}:${_pfModelShort(br[k])}`).join(" \xB7 ") : "risk-routed";
    rows.push(`<span class="pf-model-pill"><b>Tester</b> ${escHtml(testerTxt)}</span>`);
    rows.push(`<span class="pf-model-pill"><b>Estimator</b> ${escHtml(_pfModelShort(m.estimator))}</span>`);
    if (m.documentor) {
      rows.push(`<span class="pf-model-pill"><b>Documentor</b> ${escHtml(_pfModelShort(m.documentor))}</span>`);
    }
    return `<div class="pf-section">
      <div class="pf-section-label">Agent models <span class="pf-model-note">\u2014 confirm before run \xB7 edit in Settings \u2192 Agent Models</span></div>
      <div class="pf-section-body pf-model-pills">${rows.join("")}</div>
    </div>`;
  }
  function smgmtRunBlockedToast() {
    _smgmtShowToast("Another sprint is running \u2014 wait for it to finish or cancel it");
  }
  function smgmtRunSprint2(label) {
    _pfOpen(label);
  }
  async function smgmtCancelSprint(label) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    if (!confirm(`Cancel sprint ${sprintLabelDisplay(label)}? The sprint will stop and tickets will not be modified.`))
      return;
    try {
      const res = await fetch(`/api/sprints/run/${encodeURIComponent(label)}?project=${encodeURIComponent(repo)}`, { method: "DELETE" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        _smgmtShowToast(`Cancel failed: ${err.detail || res.status}`);
      } else {
        _smgmtShowToast(`Sprint ${sprintLabelDisplay(label)} cancel signal sent`);
        setTimeout(() => loadSprintMgmt(), 2e3);
      }
    } catch (e) {
      _smgmtShowToast(`Cancel failed: ${e.message}`);
    }
  }
  function _pfOpen(label) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    _pfCurrentLabel = label;
    _pfCurrentRepo = repo;
    _pfReset();
    document.getElementById("pf-backdrop").classList.remove("hidden");
    document.getElementById("pf-modal").classList.remove("hidden");
    document.getElementById("pf-close-btn").focus();
    _pfFetch();
  }
  function _pfReset() {
    document.getElementById("pf-loading").classList.remove("hidden");
    document.getElementById("pf-content").classList.add("hidden");
    document.getElementById("pf-error").classList.add("hidden");
    document.getElementById("pf-footer").classList.add("hidden");
    document.getElementById("pf-confirm-btn").disabled = true;
    document.getElementById("pf-confirm-btn").textContent = "Run Sprint";
    _pfDagData = null;
    _pfWarnings = null;
    _pfCycle = null;
    _pfFlags = null;
    _pfModels = null;
    _pfSelectedIds = /* @__PURE__ */ new Set();
  }
  function _pfClose() {
    document.getElementById("pf-backdrop").classList.add("hidden");
    document.getElementById("pf-modal").classList.add("hidden");
    _pfCurrentLabel = null;
    _pfCurrentRepo = null;
    _pfState = "idle";
    _pfDagData = null;
    _pfWarnings = null;
    _pfCycle = null;
    _pfFlags = null;
    _pfSelectedIds = /* @__PURE__ */ new Set();
  }
  async function _pfFetch() {
    _pfState = "loading";
    const label = _pfCurrentLabel;
    const repo = _pfCurrentRepo;
    try {
      const res = await fetch(
        `/api/sprints/${encodeURIComponent(label)}/preflight?project=${encodeURIComponent(repo)}`
      );
      if (!res.ok)
        throw new Error(await res.text());
      if (_pfCurrentLabel !== label)
        return;
      const data = await res.json();
      _pfDagData = data.dag || null;
      _pfWarnings = data.warnings || null;
      _pfCycle = data.cycle || null;
      _pfFlags = data.mis_sizing_flags || null;
      _pfModels = data.models || null;
      if (_pfDagData) {
        for (const t of _pfDagData.tickets || [])
          _pfSelectedIds.add(t.id);
      }
      _pfState = "success";
      _pfShowSuccess();
    } catch (e) {
      if (_pfCurrentLabel !== label)
        return;
      _pfState = "error";
      _pfShowError(e.message || "Preflight check failed.");
    }
  }
  function _pfShowSuccess() {
    document.getElementById("pf-loading").classList.add("hidden");
    document.getElementById("pf-error").classList.add("hidden");
    const n = parseInt((_pfCurrentLabel || "").split("-")[1], 10);
    const dagHtml = _pfDagData && (_pfDagData.tickets || []).length > 0 ? _pfBuildDAGHtml(_pfDagData) : "";
    const warningsHtml = _pfBuildWarningsHtml();
    const cycleHtml = _pfBuildCycleHtml();
    const flagsHtml = _pfBuildFlagsHtml();
    const conflictsHtml = _pfBuildConflictsHtml();
    const orderHtml = _pfBuildOrderHtml();
    const modelsHtml = _pfBuildModelsHtml();
    document.getElementById("pf-content").innerHTML = `<p style="font-size:13px;color:var(--text);margin:0;">Ready to run <strong>Sprint ${n}</strong>.</p>
     ${modelsHtml}
     ${warningsHtml}
     ${cycleHtml}
     ${flagsHtml}
     ${dagHtml}
     <div class="pf-section">
       <div class="pf-section-label">Conflicts</div>
       <div class="pf-section-body" id="pf-conflicts">${conflictsHtml}</div>
     </div>
     <div class="pf-section">
       <div class="pf-section-label">Proposed Execution Order</div>
       <div class="pf-section-body" id="pf-order">${orderHtml}</div>
     </div>`;
    document.getElementById("pf-content").classList.remove("hidden");
    document.getElementById("pf-footer").classList.remove("hidden");
    _pfUpdateConfirmBtn();
    document.getElementById("pf-cancel-btn").focus();
    if (_pfDagData && (_pfDagData.edges || []).length > 0) {
      requestAnimationFrame(() => _pfDrawDAGArrows(_pfDagData.edges));
    }
  }
  function _pfUpdateConfirmBtn() {
    const hasCycle = !!(_pfCycle && _pfCycle.length);
    const pendingFlags = _pfFlags && (_pfFlags.flags || []).filter((f) => f.status === "pending") || [];
    const hasPending = pendingFlags.length > 0;
    const confirmBtn = document.getElementById("pf-confirm-btn");
    if (!confirmBtn)
      return;
    confirmBtn.disabled = hasCycle || hasPending;
    if (hasCycle) {
      confirmBtn.title = "Cannot run: dependency cycle detected. Resolve the cycle first.";
      confirmBtn.setAttribute("aria-label", "Run Sprint \u2014 disabled: dependency cycle detected");
    } else if (hasPending) {
      confirmBtn.title = `Cannot run: ${pendingFlags.length} mis-sizing flag${pendingFlags.length > 1 ? "s" : ""} need review.`;
      confirmBtn.setAttribute("aria-label", "Run Sprint \u2014 disabled: mis-sizing flags need review");
    } else {
      confirmBtn.title = "";
      confirmBtn.setAttribute("aria-label", "Run Sprint");
    }
  }
  function _pfBuildWarningsHtml() {
    if (!_pfWarnings)
      return "";
    const chips = [];
    const unestimated = _pfWarnings.unestimated || [];
    const staleEstimates = _pfWarnings.stale_estimates || [];
    const missingAc = _pfWarnings.missing_ac || [];
    if (unestimated.length) {
      chips.push(`<span class="pf-warning-chip">${unestimated.length} unestimated: ${escHtml(unestimated.join(", "))}</span>`);
    }
    if (staleEstimates.length) {
      chips.push(`<span class="pf-warning-chip">${staleEstimates.length} stale estimate${staleEstimates.length > 1 ? "s" : ""}: ${escHtml(staleEstimates.join(", "))}</span>`);
    }
    if (missingAc.length) {
      chips.push(`<span class="pf-warning-chip">${missingAc.length} missing AC: ${escHtml(missingAc.join(", "))}</span>`);
    }
    if (!chips.length)
      return "";
    return `<div class="pf-warnings-section">
    <div class="pf-warnings-label">Warnings</div>
    <div class="pf-warning-chips">${chips.join("")}</div>
  </div>`;
  }
  function _pfBuildCycleHtml() {
    if (!_pfCycle || !_pfCycle.length)
      return "";
    return `<div class="pf-cycle-banner">
    <strong>Cycle detected:</strong> ${escHtml(_pfCycle.join(" \u2192 "))}
  </div>`;
  }
  function _pfBuildFlagsHtml() {
    const flags = _pfFlags && (_pfFlags.flags || []);
    if (!flags || !flags.length)
      return "";
    const label = _pfCurrentLabel;
    const repo = _pfCurrentRepo;
    const rows = flags.map((f) => {
      const num = f.issue_number;
      const resolved = f.status !== "pending";
      const itemClass = resolved ? "pf-flag-item resolved" : "pf-flag-item";
      const estLabel = f.current_estimate ? `${escHtml(f.current_estimate)} (${f.current_estimate_minutes ?? "?"} min)` : "unknown";
      const avgLabel = f.historical_avg_actual_size ? `${escHtml(f.historical_avg_actual_size)} (${f.historical_avg_actual_minutes ?? "?"} min avg)` : "unknown";
      const drivingLabels = (f.driving_labels || []).map((l) => `<code>${escHtml(l)}</code>`).join(", ");
      const eventCount = f.mis_sizing_event_count || 0;
      let badgeHtml = "";
      let actionsHtml = "";
      if (resolved) {
        const actionText = { approved: "Approved", reestimated: "Re-estimated", dismissed: "Dismissed" }[f.status] || f.status;
        badgeHtml = `<span class="pf-flag-badge pf-flag-badge-resolved">${escHtml(actionText)}</span>`;
        const noteText = f.action_note ? ` \u2014 ${escHtml(f.action_note)}` : "";
        const newSizeText = f.new_size ? ` New size: ${escHtml(f.new_size)}.` : "";
        actionsHtml = `<div class="pf-flag-resolved-note">${escHtml(actionText)}${newSizeText}${noteText}</div>`;
      } else {
        badgeHtml = `<span class="pf-flag-badge pf-flag-badge-pending">Review needed</span>`;
        actionsHtml = `
        <div class="pf-flag-actions" id="pf-flag-actions-${num}">
          <button class="pf-flag-action-btn approve" onclick="_pfFlagAction(${num}, 'approved')">Approve</button>
          <button class="pf-flag-action-btn" onclick="_pfFlagShowSizePicker(${num}, '${escHtml(f.current_estimate || "S")}')">Re-estimate</button>
          <button class="pf-flag-action-btn dismiss" onclick="_pfFlagAction(${num}, 'dismissed')">Dismiss</button>
        </div>
        <div id="pf-flag-picker-${num}" style="display:none">
          <div class="pf-flag-size-picker">
            <span style="font-size:12px;color:var(--text-muted);">New size:</span>
            ${["S", "M", "L", "XL"].map(
          (s) => `<button class="pf-flag-size-btn" onclick="_pfFlagReestimate(${num}, '${s}')">${s}</button>`
        ).join("")}
            <button class="pf-flag-size-cancel" onclick="_pfFlagHidePicker(${num})">Cancel</button>
          </div>
        </div>`;
      }
      return `<div class="${itemClass}" id="pf-flag-item-${num}">
      <div class="pf-flag-header">
        <span class="pf-flag-id">#${num}</span>
        <span class="pf-flag-title" title="${escHtml(f.title)}">${escHtml(f.title)}</span>
        ${badgeHtml}
      </div>
      <div class="pf-flag-details">
        Estimate: <strong>${estLabel}</strong> \xB7
        Historical avg: <strong>${avgLabel}</strong> \xB7
        ${eventCount} mis-sizing event${eventCount !== 1 ? "s" : ""} on: ${drivingLabels}
      </div>
      ${actionsHtml}
    </div>`;
    });
    const pending = flags.filter((f) => f.status === "pending").length;
    const subtitle = pending > 0 ? `${pending} ticket${pending > 1 ? "s" : ""} flagged for review` : "All flags resolved";
    return `<div class="pf-flags-section" id="pf-flags-section">
    <div class="pf-flags-label">Mis-sizing review \u2014 ${subtitle}</div>
    ${rows.join("")}
  </div>`;
  }
  function _pfFlagShowSizePicker(num, currentSize) {
    const actionsEl = document.getElementById(`pf-flag-actions-${num}`);
    const pickerEl = document.getElementById(`pf-flag-picker-${num}`);
    if (actionsEl)
      actionsEl.style.display = "none";
    if (pickerEl)
      pickerEl.style.display = "block";
  }
  function _pfFlagHidePicker(num) {
    const actionsEl = document.getElementById(`pf-flag-actions-${num}`);
    const pickerEl = document.getElementById(`pf-flag-picker-${num}`);
    if (actionsEl)
      actionsEl.style.display = "";
    if (pickerEl)
      pickerEl.style.display = "none";
  }
  async function _pfFlagAction(num, action, newSize) {
    const label = _pfCurrentLabel;
    const repo = _pfCurrentRepo;
    if (!label || !repo)
      return;
    const itemEl = document.getElementById(`pf-flag-item-${num}`);
    if (itemEl)
      itemEl.querySelectorAll("button").forEach((b) => {
        b.disabled = true;
      });
    try {
      const body = { action };
      if (newSize)
        body.new_size = newSize;
      const res = await fetch(
        `/api/sprints/${encodeURIComponent(label)}/mis-sizing-flags/${num}/action?project=${encodeURIComponent(repo)}`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
      );
      if (!res.ok) {
        const err = await res.text();
        _smgmtShowToast(`Flag action failed: ${err}`, "error");
        if (itemEl)
          itemEl.querySelectorAll("button").forEach((b) => {
            b.disabled = false;
          });
        return;
      }
      const data = await res.json();
      _pfFlags = data;
      const flagsSection = document.getElementById("pf-flags-section");
      if (flagsSection) {
        const newHtml = _pfBuildFlagsHtml();
        flagsSection.outerHTML = newHtml || '<div id="pf-flags-section"></div>';
      }
      _pfUpdateConfirmBtn();
    } catch (e) {
      _smgmtShowToast("Flag action failed: " + e.message, "error");
      if (itemEl)
        itemEl.querySelectorAll("button").forEach((b) => {
          b.disabled = false;
        });
    }
  }
  function _pfFlagReestimate(num, newSize) {
    _pfFlagHidePicker(num);
    _pfFlagAction(num, "reestimated", newSize);
  }
  function _pfBuildDAGHtml(dag) {
    const ticketMap = {};
    for (const t of dag.tickets || [])
      ticketMap[t.id] = t;
    const layers = dag.layers || [];
    if (!layers.length)
      return "";
    let colsHtml = "";
    for (let i = 0; i < layers.length; i++) {
      const layer = layers[i];
      let cardsHtml = "";
      for (const id of layer) {
        const t = ticketMap[id] || { id, number: id.replace("#", ""), title: id, state: "backlog", size: null, files_touched: [] };
        const stateClass = t.state || "backlog";
        const stateBadge = `<span class="ticket-status-pill ${escHtml(stateClass)}">${escHtml(stateClass)}</span>`;
        const sizeBadge = t.size ? `<span class="pf-dag-size-badge">${escHtml(t.size)}</span>` : "";
        const files = t.files_touched || [];
        const shown = files.slice(0, 3).map((f) => `<span>${escHtml(f.split("/").slice(-1)[0])}</span>`).join("");
        const more = files.length > 3 ? `<span>+${files.length - 3} more</span>` : "";
        const filesHtml = shown || more ? `<div class="pf-dag-card-files">${shown}${more}</div>` : "";
        cardsHtml += `<div class="pf-dag-card" id="pf-card-${escHtml(id)}" data-dag-id="${escHtml(id)}">
          <div class="pf-dag-card-header">
            <input type="checkbox" class="pf-dag-card-check" checked
              onchange="_pfToggleTicket('${escHtml(id)}')" aria-label="Include ticket ${escHtml(id)}">
            <span class="pf-dag-card-num">${escHtml(id)}</span>
          </div>
          <div class="pf-dag-card-title" title="${escHtml(t.title)}">${escHtml(t.title)}</div>
          <div class="pf-dag-card-meta">${stateBadge}${sizeBadge}</div>
          ${filesHtml}
        </div>`;
      }
      colsHtml += `<div class="pf-dag-col">
        <div class="pf-dag-col-label">Level ${i + 1}</div>
        ${cardsHtml}
      </div>`;
    }
    return `<div class="pf-dag-section">
    <div class="pf-dag-section-label">Execution Graph</div>
    <div class="pf-dag-wrap" id="pf-dag-wrap">
      <svg class="pf-dag-svg" id="pf-dag-svg" aria-hidden="true"></svg>
      <div class="pf-dag-levels" id="pf-dag-levels">${colsHtml}</div>
    </div>
  </div>`;
  }
  function _pfDrawDAGArrows(edges) {
    if (!edges || !edges.length)
      return;
    const wrap = document.getElementById("pf-dag-wrap");
    const svg = document.getElementById("pf-dag-svg");
    const levels = document.getElementById("pf-dag-levels");
    if (!wrap || !svg || !levels)
      return;
    const wrapRect = wrap.getBoundingClientRect();
    const h = levels.getBoundingClientRect().height;
    svg.setAttribute("width", String(wrapRect.width));
    svg.setAttribute("height", String(h));
    const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    const marker = document.createElementNS("http://www.w3.org/2000/svg", "marker");
    marker.setAttribute("id", "pf-arrow");
    marker.setAttribute("markerWidth", "7");
    marker.setAttribute("markerHeight", "7");
    marker.setAttribute("refX", "6");
    marker.setAttribute("refY", "3.5");
    marker.setAttribute("orient", "auto");
    const arrowPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
    arrowPath.setAttribute("d", "M0,0 L0,7 L7,3.5 z");
    arrowPath.setAttribute("fill", "var(--text-muted)");
    marker.appendChild(arrowPath);
    defs.appendChild(marker);
    svg.appendChild(defs);
    for (const [fromId, toId] of edges) {
      const fromEl = wrap.querySelector(`[data-dag-id="${fromId}"]`);
      const toEl = wrap.querySelector(`[data-dag-id="${toId}"]`);
      if (!fromEl || !toEl)
        continue;
      const fr = fromEl.getBoundingClientRect();
      const tr = toEl.getBoundingClientRect();
      const x1 = fr.right - wrapRect.left;
      const y1 = fr.top + fr.height / 2 - wrapRect.top;
      const x2 = tr.left - wrapRect.left - 7;
      const y2 = tr.top + tr.height / 2 - wrapRect.top;
      const mx = (x1 + x2) / 2;
      const line = document.createElementNS("http://www.w3.org/2000/svg", "path");
      line.setAttribute("d", `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`);
      line.setAttribute("stroke", "var(--text-muted)");
      line.setAttribute("stroke-width", "1.5");
      line.setAttribute("fill", "none");
      line.setAttribute("marker-end", "url(#pf-arrow)");
      svg.appendChild(line);
    }
  }
  function _pfToggleTicket(id) {
    if (_pfSelectedIds.has(id)) {
      _pfSelectedIds.delete(id);
    } else {
      _pfSelectedIds.add(id);
    }
    const card = document.getElementById(`pf-card-${id}`);
    if (card)
      card.classList.toggle("pf-deselected", !_pfSelectedIds.has(id));
    _pfUpdateSections();
  }
  function _pfGetSelectedTickets() {
    if (!_pfDagData)
      return [];
    return (_pfDagData.tickets || []).filter((t) => _pfSelectedIds.has(t.id));
  }
  function _pfComputeConflicts(tickets) {
    const conflicts = [];
    for (let i = 0; i < tickets.length; i++) {
      for (let j = i + 1; j < tickets.length; j++) {
        const filesA = tickets[i].files_touched || [];
        const filesB = tickets[j].files_touched || [];
        const shared = filesA.filter((f) => filesB.includes(f));
        for (const file of shared) {
          conflicts.push({ a: tickets[i], b: tickets[j], file });
        }
      }
    }
    return conflicts;
  }
  function _pfBuildConflictsHtml() {
    const selected = _pfGetSelectedTickets();
    const conflicts = _pfComputeConflicts(selected);
    if (!conflicts.length) {
      return '<p class="pf-no-conflict">No file conflicts detected.</p>';
    }
    return conflicts.map(
      (c) => `<p class="pf-conflict-item">Tickets #${c.a.number} and #${c.b.number} both touch <code>${escHtml(c.file)}</code></p>`
    ).join("");
  }
  function _pfBuildOrderHtml() {
    if (!_pfDagData)
      return '<p class="pf-no-conflict">No order data available.</p>';
    const layers = (_pfDagData.layers || []).map((layer) => layer.filter((id) => _pfSelectedIds.has(id))).filter((l) => l.length > 0);
    if (!layers.length)
      return '<p class="pf-no-conflict">No tickets selected.</p>';
    let html = '<ol class="pf-order-list">';
    for (let i = 0; i < layers.length; i++) {
      const nums = layers[i].map((id) => id);
      const descriptor = i === 0 ? "parallel-eligible" : `runs after Level ${i}`;
      html += `<li class="pf-order-item">Level ${i + 1}: ${escHtml(nums.join(", "))} \u2014 ${escHtml(descriptor)}.</li>`;
    }
    html += "</ol>";
    return html;
  }
  function _pfUpdateSections() {
    const conflictsEl = document.getElementById("pf-conflicts");
    const orderEl = document.getElementById("pf-order");
    if (conflictsEl)
      conflictsEl.innerHTML = _pfBuildConflictsHtml();
    if (orderEl)
      orderEl.innerHTML = _pfBuildOrderHtml();
  }
  function _pfShowError(msg) {
    document.getElementById("pf-loading").classList.add("hidden");
    document.getElementById("pf-content").classList.add("hidden");
    document.getElementById("pf-error-msg").textContent = msg;
    document.getElementById("pf-error").classList.remove("hidden");
    document.getElementById("pf-footer").classList.remove("hidden");
    document.getElementById("pf-confirm-btn").disabled = true;
    document.getElementById("pf-retry-btn").focus();
  }
  function _pfRetry() {
    _pfReset();
    _pfFetch();
  }
  async function _pfConfirm() {
    if (_pfState !== "success")
      return;
    const label = _pfCurrentLabel;
    const repo = _pfCurrentRepo;
    if (!label || !repo)
      return;
    const confirmBtn = document.getElementById("pf-confirm-btn");
    confirmBtn.disabled = true;
    confirmBtn.innerHTML = '<span class="pf-spinner" style="width:12px;height:12px;border-width:2px;"></span> Running\u2026';
    try {
      const res = await fetch("/api/sprints/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: repo, sprint_label: label })
      });
      if (!res.ok) {
        let detail = await res.text();
        try {
          const parsed = JSON.parse(detail);
          detail = parsed.detail || detail;
        } catch (_) {
        }
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
      }
      _pfClose();
      const n = parseInt(label.split("-")[1], 10);
      _smgmtShowToast(`Sprint ${n} dispatched.`);
      if (typeof _smgmtShowSubView === "function")
        _smgmtShowSubView("running");
      await loadSprintMgmt(true, label);
      if (typeof _smgmtLivePollRestart === "function")
        _smgmtLivePollRestart();
      for (let i = 0; i < 8; i++) {
        if (_smgmtRunningLabels && _smgmtRunningLabels.has(label))
          break;
        await new Promise((r) => setTimeout(r, 600));
        await loadSprintMgmt(true, label);
      }
    } catch (e) {
      _pfState = "error";
      _pfShowError("Failed to run sprint: " + e.message);
    }
  }

  // apps/dashboard/static/src/sprint-board/drag-drop.js
  function isDragBlocked(state) {
    return !!(state && state.moveLock);
  }
  function computeDropPlan(dragInfo, targetLabel) {
    if (!dragInfo)
      return { mode: "none", tickets: [], targetLabel, noop: true };
    if (dragInfo.multi && dragInfo.multi.length > 1) {
      return { mode: "multi", tickets: dragInfo.multi.slice(), targetLabel, noop: false };
    }
    const noop = dragInfo.fromSprint === targetLabel;
    return { mode: "single", tickets: noop ? [] : [dragInfo.number], targetLabel, noop };
  }
  function _smgmtUpdateSelectionUI2() {
    const count = _smgmtSelectedIssues.size;
    _blUpdateActions();
    let bar = document.getElementById("smgmt-selection-bar");
    const listEl = document.getElementById("smgmt-sprint-list");
    if (count > 0) {
      if (!bar) {
        bar = document.createElement("div");
        bar.id = "smgmt-selection-bar";
        bar.className = "smgmt-selection-bar";
        bar.setAttribute("role", "status");
        bar.setAttribute("aria-live", "polite");
        bar.innerHTML = `
        <i class="ti ti-checkbox"></i>
        <strong class="smgmt-selection-bar-count" id="smgmt-sel-count">0 tickets selected</strong>
        <span style="color:var(--text-muted)">\u2014 batch move to a sprint or backlog</span>
        <div class="smgmt-sel-bar-actions">
          <button class="smgmt-selection-bar-delete" id="smgmt-sel-delete-btn"
                  onclick="_smgmtDeleteSelected()">
            <i class="ti ti-trash"></i> Delete
          </button>
          <button class="smgmt-selection-bar-deselect" onclick="_smgmtClearSelection()">
            <i class="ti ti-x"></i> Deselect all
          </button>
          <button class="smgmt-move-to-btn" id="smgmt-move-to-btn"
                  onclick="_smgmtToggleMoveToMenu(event)">
            <i class="ti ti-send"></i> Move to Sprint &#9660;
          </button>
          <div class="smgmt-move-to-menu" id="smgmt-move-to-menu"></div>
        </div>`;
        if (listEl)
          listEl.insertBefore(bar, listEl.firstChild);
      }
      bar.classList.add("show");
      if (listEl)
        listEl.classList.add("has-selection");
      const countEl = document.getElementById("smgmt-sel-count");
      if (countEl)
        countEl.textContent = count === 1 ? "1 ticket selected" : `${count} tickets selected`;
      _smgmtPopulateMoveToMenu();
      const deleteBtn = document.getElementById("smgmt-sel-delete-btn");
      if (deleteBtn) {
        const showDelete = count === 1 && _smgmtIsDeletableIssue([..._smgmtSelectedIssues][0]);
        deleteBtn.classList.toggle("show", showDelete);
      }
    } else {
      if (bar)
        bar.classList.remove("show");
      if (listEl)
        listEl.classList.remove("has-selection");
    }
  }
  function _smgmtPopulateSelectionDropdown() {
  }
  function _smgmtPopulateMoveToMenu() {
    const menu = document.getElementById("smgmt-move-to-menu");
    if (!menu || !_smgmtData)
      return;
    const selectedNums = Array.from(_smgmtSelectedIssues);
    const occupiedSprints = new Set(
      selectedNums.map((n) => {
        const iss = (_smgmtData.issues || []).find((i) => i.number === n);
        return iss ? iss.sprint : void 0;
      }).filter((s) => s != null)
    );
    const sprints = (_smgmtData.sprints || []).sort((a, b) => a - b);
    let html = "";
    sprints.forEach((n) => {
      if (occupiedSprints.has(n))
        return;
      const label = `sprint-${n}`;
      html += `<button class="smgmt-move-to-item" onclick="_smgmtMoveSelectedTo('${label}');_smgmtCloseMoveToMenu()">Sprint ${n}</button>`;
      if (typeof _smgmtHotswapAvailableFor === "function" && _smgmtHotswapAvailableFor(label)) {
        html += `<button class="smgmt-move-to-item smgmt-move-to-item--hotswap" onclick="_smgmtHotswapModalOpen('${label}');_smgmtCloseMoveToMenu()">Sprint ${n} \u2014 replace (hotswap)</button>`;
      }
    });
    html += `<button class="smgmt-move-to-item" onclick="_smgmtMoveSelectedTo('backlog');_smgmtCloseMoveToMenu()">Backlog (no sprint)</button>`;
    menu.innerHTML = html || '<span style="display:block;padding:8px 14px;font-size:12px;color:var(--text-muted)">No other sprints available</span>';
  }
  function _smgmtToggleMoveToMenu(event) {
    event.stopPropagation();
    const menu = document.getElementById("smgmt-move-to-menu");
    if (!menu)
      return;
    _smgmtPopulateMoveToMenu();
    menu.classList.toggle("open");
  }
  function _smgmtCloseMoveToMenu() {
    const menu = document.getElementById("smgmt-move-to-menu");
    if (menu)
      menu.classList.remove("open");
  }
  function _smgmtClearSelection() {
    _smgmtSelectedIssues.forEach((num) => {
      const el = document.getElementById(`smgmt-ticket-${num}`);
      if (el) {
        el.classList.remove("is-selected");
        const cb = el.querySelector(".smgmt-ticket-cb");
        if (cb)
          cb.checked = false;
      }
    });
    _smgmtSelectedIssues.clear();
    _smgmtUpdateSelectionUI2();
  }
  function _smgmtSetSelected(number, selected) {
    if (selected)
      _smgmtSelectedIssues.add(number);
    else
      _smgmtSelectedIssues.delete(number);
    const el = document.getElementById(`smgmt-ticket-${number}`);
    if (el) {
      el.classList.toggle("is-selected", selected);
      const cb = el.querySelector(".smgmt-ticket-cb");
      if (cb)
        cb.checked = selected;
    }
  }
  function _smgmtTicketSprintKey(number) {
    const iss = (_smgmtData?.issues || []).find((i) => i.number === number);
    if (!iss)
      return void 0;
    return iss.sprint == null ? "backlog" : iss.sprint;
  }
  function _smgmtSelectionSprintKey() {
    const first = [..._smgmtSelectedIssues][0];
    return first == null ? void 0 : _smgmtTicketSprintKey(first);
  }
  function _smgmtEnforceSelectionScope(number) {
    if (_smgmtSelectedIssues.size === 0)
      return;
    const cur = _smgmtSelectionSprintKey();
    const next = _smgmtTicketSprintKey(number);
    if (cur !== void 0 && next !== void 0 && cur !== next) {
      _smgmtClearSelection();
    }
  }
  function _smgmtToggleSelect(number, checked) {
    if (checked)
      _smgmtEnforceSelectionScope(number);
    _smgmtSetSelected(number, checked);
    _smgmtLastSelectedNum = checked ? number : null;
    _smgmtUpdateSelectionUI2();
  }
  function _smgmtRowClick(event, number, label) {
    const container = label ? document.getElementById(`smgmt-tickets-${label}`) : document.getElementById("smgmt-backlog-tickets");
    if (event.shiftKey && _smgmtLastSelectedNum != null && container) {
      const nums = Array.from(container.querySelectorAll(".smgmt-ticket[data-issue]")).map((r) => parseInt(r.dataset.issue, 10));
      const a = nums.indexOf(_smgmtLastSelectedNum);
      const b = nums.indexOf(number);
      if (a !== -1 && b !== -1) {
        const [lo, hi] = a <= b ? [a, b] : [b, a];
        for (let i = lo; i <= hi; i++)
          _smgmtSetSelected(nums[i], true);
        _smgmtLastSelectedNum = number;
        _smgmtUpdateSelectionUI2();
        const sel = window.getSelection && window.getSelection();
        if (sel)
          sel.removeAllRanges();
        return;
      }
    }
    if (event.ctrlKey || event.metaKey) {
      const nowSelected2 = !_smgmtSelectedIssues.has(number);
      if (nowSelected2)
        _smgmtEnforceSelectionScope(number);
      _smgmtSetSelected(number, nowSelected2);
      _smgmtLastSelectedNum = nowSelected2 ? number : null;
      _smgmtUpdateSelectionUI2();
      return;
    }
    const nowSelected = !_smgmtSelectedIssues.has(number);
    if (nowSelected)
      _smgmtEnforceSelectionScope(number);
    _smgmtSetSelected(number, nowSelected);
    _smgmtLastSelectedNum = nowSelected ? number : null;
    _smgmtUpdateSelectionUI2();
  }
  function _smgmtIsDeletableIssue(num) {
    if (!_smgmtData)
      return false;
    const iss = _smgmtData.issues.find((i) => i.number === num);
    if (!iss)
      return false;
    return iss.status === "done" || iss.sprint === null;
  }
  async function _smgmtDeleteSelected() {
    if (_smgmtSelectedIssues.size !== 1)
      return;
    const num = [..._smgmtSelectedIssues][0];
    const repo = _smgmtRepo();
    if (!repo)
      return;
    const iss = _smgmtData?.issues.find((i) => i.number === num);
    const label = iss ? `#${num}: ${iss.title}` : `#${num}`;
    if (!confirm(`Delete ${label}?

This will close the issue on GitHub. This cannot be undone.`))
      return;
    if (_smgmtData)
      _smgmtData.issues = _smgmtData.issues.filter((i) => i.number !== num);
    _smgmtClearSelection();
    _smgmtRender(_smgmtData);
    _smgmtBoardLock(`Deleting #${num}\u2026`);
    try {
      const res = await fetch(`/api/issues/${num}/close?repo=${encodeURIComponent(repo)}`, {
        method: "POST"
      });
      if (!res.ok)
        throw new Error(await res.text());
      _smgmtShowToast(`Issue #${num} closed.`);
    } catch (e) {
      alert("Failed to delete issue: " + e.message);
      await loadSprintMgmt();
    } finally {
      _smgmtBoardUnlock();
    }
  }
  async function _smgmtMoveSelectedTo(targetLabel) {
    if (!targetLabel || _smgmtSelectedIssues.size === 0)
      return;
    const repo = _smgmtRepo();
    if (!repo)
      return;
    const nums = Array.from(_smgmtSelectedIssues);
    const changes = nums.map((n) => ({ issue_num: n, sprint_label: targetLabel }));
    const dest = targetLabel === "backlog" ? "Backlog" : `Sprint ${targetLabel.split("-")[1]}`;
    if (_smgmtData) {
      const targetNum = targetLabel === "backlog" ? null : parseInt(targetLabel.split("-")[1], 10);
      nums.forEach((n) => {
        const iss = _smgmtData.issues.find((i) => i.number === n);
        if (iss)
          iss.sprint = targetNum;
      });
      _smgmtClearSelection();
      _smgmtRender(_smgmtData);
    }
    _smgmtBoardLock(`Moving ${nums.length} ticket${nums.length !== 1 ? "s" : ""} to ${dest}\u2026`);
    try {
      const res = await fetch("/api/sprints/batch-labels", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ changes, project: repo })
      });
      if (!res.ok)
        throw new Error(await res.text());
      const data = await res.json();
      if (data.failed > 0 && data.errors && data.errors.length > 0) {
        _smgmtShowInlineError(`${data.failed} ticket${data.failed !== 1 ? "s" : ""} failed to move:
${data.errors.join("\n")}`);
      } else if (data.applied > 0) {
        _smgmtShowToast(`Moved ${data.applied} ticket${data.applied !== 1 ? "s" : ""} to ${dest}.`);
      }
      await loadSprintMgmt();
    } catch (e) {
      _smgmtShowToast("Failed to move tickets: " + e.message);
      await loadSprintMgmt();
    } finally {
      _smgmtBoardUnlock();
    }
  }
  function _smgmtTicketDragStart(event, issueNum, fromSprint) {
    if (fromSprint) {
      const card = document.getElementById(`smgmt-card-${fromSprint}`);
      if (card && card.querySelector(".smgmt-rename-wrap")) {
        event.preventDefault();
        return;
      }
    }
    const isChecked = _smgmtSelectedIssues.has(issueNum);
    if (isChecked && _smgmtSelectedIssues.size > 1) {
      const nums = Array.from(_smgmtSelectedIssues);
      const sprints = new Set(nums.map((n) => {
        const iss = (_smgmtData?.issues || []).find((i) => i.number === n);
        return iss ? iss.sprint : null;
      }));
      _smgmtDragTicket = {
        number: issueNum,
        fromSprint: fromSprint || null,
        multi: nums,
        multiSprints: sprints.size
      };
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", nums.join(","));
      const pill = document.getElementById("smgmt-drag-pill");
      if (pill) {
        const label = sprints.size > 1 ? `Moving ${nums.length} tickets from ${sprints.size} sprints` : `Moving ${nums.length} tickets`;
        pill.textContent = label;
        pill.style.top = event.clientY - 20 + "px";
        pill.style.left = event.clientX + 12 + "px";
      }
      setTimeout(() => {
        nums.forEach((n) => {
          const el = document.getElementById(`smgmt-ticket-${n}`);
          if (el)
            el.classList.add("dragging-ticket");
        });
      }, 0);
    } else {
      _smgmtDragTicket = { number: issueNum, fromSprint: fromSprint || null, multi: null };
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", String(issueNum));
      const el = document.getElementById(`smgmt-ticket-${issueNum}`);
      if (el)
        setTimeout(() => el.classList.add("dragging-ticket"), 0);
    }
    _smgmtGhostShow();
  }
  function _smgmtDragMovePill(event) {
    if (_smgmtDragTicket?.multi) {
      const pill = document.getElementById("smgmt-drag-pill");
      if (pill && pill.textContent) {
        pill.style.top = event.clientY - 20 + "px";
        pill.style.left = event.clientX + 12 + "px";
      }
    }
  }
  function _smgmtGhostComputeNextFree() {
    if (_smgmtData && Number.isInteger(_smgmtData.placeholder_sprint)) {
      return _smgmtData.placeholder_sprint;
    }
    const nums = (_smgmtData?.sprints || []).map(Number).filter((n) => !isNaN(n));
    return nums.length ? Math.max(...nums) + 1 : 1;
  }
  function _smgmtGhostShow() {
    if (_smgmtRunningLabels.size > 0) {
      showToast("Cannot create new sprint while one is running.", "warning");
      return;
    }
    _smgmtGhostNextNum = _smgmtGhostComputeNextFree();
    const ghost = document.getElementById("smgmt-ghost-pane");
    const titleEl = document.getElementById("smgmt-ghost-title");
    const subEl = document.getElementById("smgmt-ghost-sub");
    if (!ghost)
      return;
    titleEl.textContent = `Drop here to create Sprint ${_smgmtGhostNextNum}`;
    subEl.textContent = "next sprint number";
    ghost.classList.add("ghost-visible");
  }
  function _smgmtGhostHide() {
    const ghost = document.getElementById("smgmt-ghost-pane");
    if (!ghost)
      return;
    ghost.classList.remove("ghost-visible", "ghost-hot");
    _smgmtGhostNextNum = null;
  }
  function _smgmtGhostDragOver(event) {
    if (!_smgmtDragTicket)
      return;
    if (_smgmtRunningLabels.size > 0)
      return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    const ghost = document.getElementById("smgmt-ghost-pane");
    if (!ghost)
      return;
    const titleEl = document.getElementById("smgmt-ghost-title");
    const subEl = document.getElementById("smgmt-ghost-sub");
    ghost.classList.add("ghost-hot");
    if (titleEl)
      titleEl.textContent = `Release to create Sprint ${_smgmtGhostNextNum}`;
    if (subEl)
      subEl.textContent = "you'll be asked to confirm";
  }
  function _smgmtGhostDragLeave(event) {
    const ghost = document.getElementById("smgmt-ghost-pane");
    if (!ghost)
      return;
    if (!ghost.contains(event.relatedTarget)) {
      ghost.classList.remove("ghost-hot");
      const titleEl = document.getElementById("smgmt-ghost-title");
      const subEl = document.getElementById("smgmt-ghost-sub");
      if (titleEl)
        titleEl.textContent = `Drop here to create Sprint ${_smgmtGhostNextNum}`;
      const existing = new Set((_smgmtData?.sprints || []).map((n) => Number(n)));
      const skipped = [];
      for (let i = 1; i < _smgmtGhostNextNum; i++) {
        if (!existing.has(i))
          skipped.push(i);
      }
      if (subEl)
        subEl.textContent = skipped.length > 0 ? `next free number \xB7 skipped empty ${skipped.map((s) => `Sprint ${s}`).join(", ")}` : "next free number";
    }
  }
  async function _smgmtGhostDrop(event) {
    event.preventDefault();
    if (!_smgmtDragTicket)
      return;
    if (_smgmtRunningLabels.size > 0)
      return;
    const dragInfo = _smgmtDragTicket;
    const nextNum = _smgmtGhostNextNum;
    _smgmtGhostHide();
    if (dragInfo.multi && dragInfo.multi.length > 1) {
      return;
    }
    const dragEl = document.getElementById(`smgmt-ticket-${dragInfo.number}`);
    if (dragEl)
      dragEl.classList.remove("dragging-ticket");
    _smgmtDragTicket = null;
    if (nextNum == null)
      return;
    const repo = _smgmtRepo();
    if (!repo)
      return;
    const sprintLabel = `sprint-${nextNum}`;
    const issue = (_smgmtData?.issues || []).find((i) => i.number === dragInfo.number);
    const fromLabel = dragInfo.fromSprint || "backlog";
    document.getElementById("gc-sprint-name").textContent = sprintLabel;
    document.getElementById("gc-ticket-info").textContent = issue ? `#${issue.number} \u2014 ${issue.title}` : `#${dragInfo.number}`;
    document.getElementById("gc-source-pane").textContent = fromLabel === "backlog" ? "Backlog" : `Sprint ${fromLabel.replace("sprint-", "")}`;
    const confirmBtn = document.getElementById("gc-confirm-btn");
    confirmBtn.textContent = `Create ${sprintLabel} & move`;
    confirmBtn.disabled = false;
    const errEl = document.getElementById("gc-error");
    errEl.textContent = "";
    errEl.classList.add("hidden");
    document.getElementById("gc-modal").dataset.issueNum = String(dragInfo.number);
    document.getElementById("gc-modal").dataset.fromSprint = fromLabel;
    document.getElementById("gc-modal").dataset.sprintNum = String(nextNum);
    document.getElementById("gc-modal").dataset.repo = repo;
    document.getElementById("gc-backdrop").classList.remove("hidden");
    document.getElementById("gc-modal").classList.remove("hidden");
    confirmBtn.focus();
  }
  function _gcClose() {
    document.getElementById("gc-backdrop").classList.add("hidden");
    document.getElementById("gc-modal").classList.add("hidden");
  }
  async function _gcConfirm() {
    const modal = document.getElementById("gc-modal");
    const issueNum = parseInt(modal.dataset.issueNum, 10);
    const sprintNum = parseInt(modal.dataset.sprintNum, 10);
    const fromSprint = modal.dataset.fromSprint;
    const repo = modal.dataset.repo;
    const sprintLabel = `sprint-${sprintNum}`;
    const confirmBtn = document.getElementById("gc-confirm-btn");
    const errEl = document.getElementById("gc-error");
    confirmBtn.disabled = true;
    confirmBtn.textContent = "Creating\u2026";
    errEl.classList.add("hidden");
    try {
      const createRes = await fetch("/api/sprints/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project: repo, sprint_number: sprintNum })
      });
      if (!createRes.ok && createRes.status !== 409) {
        const d = await createRes.json().catch(() => ({}));
        throw new Error(d.detail || "HTTP " + createRes.status);
      }
      const moveRes = await fetch(`/api/issues/${issueNum}/sprint-label`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sprint_label: sprintLabel, project: repo })
      });
      if (!moveRes.ok) {
        const d = await moveRes.json().catch(() => ({}));
        throw new Error(d.detail || "HTTP " + moveRes.status);
      }
      _gcClose();
      await loadSprintMgmt();
    } catch (e) {
      errEl.textContent = `Failed: ${e.message}`;
      errEl.classList.remove("hidden");
      confirmBtn.disabled = false;
      confirmBtn.textContent = `Create ${sprintLabel} & move`;
    }
  }
  function _smgmtTicketDragEnd(event) {
    if (_smgmtDragTicket) {
      if (_smgmtDragTicket.multi) {
        _smgmtDragTicket.multi.forEach((n) => {
          const el = document.getElementById(`smgmt-ticket-${n}`);
          if (el)
            el.classList.remove("dragging-ticket");
        });
      } else {
        const el = document.getElementById(`smgmt-ticket-${_smgmtDragTicket.number}`);
        if (el)
          el.classList.remove("dragging-ticket");
      }
    }
    const pill = document.getElementById("smgmt-drag-pill");
    if (pill) {
      pill.style.top = "-100px";
      pill.style.left = "-100px";
      pill.textContent = "";
    }
    _smgmtGhostHide();
    _smgmtDragTicket = null;
    document.querySelectorAll(".smgmt-sprint-card").forEach((el) => el.classList.remove("drag-over-sprint"));
    document.getElementById("smgmt-backlog-pane")?.classList.remove("drag-over-backlog");
  }
  function _smgmtDragOver(event, sprintLabel) {
    if (_smgmtDragTicket) {
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      document.querySelectorAll(".smgmt-sprint-card").forEach((b) => b.classList.remove("drag-over-sprint"));
      document.getElementById("smgmt-backlog-pane")?.classList.remove("drag-over-backlog");
      const target = document.getElementById(`smgmt-card-${sprintLabel}`);
      if (target)
        target.classList.add("drag-over-sprint");
    }
  }
  function _smgmtDragLeave(event) {
    if (event.currentTarget && !event.currentTarget.contains(event.relatedTarget)) {
      event.currentTarget.classList.remove("drag-over-sprint");
    }
  }
  async function _smgmtDropOnSprint(event, targetLabel) {
    event.preventDefault();
    document.querySelectorAll(".smgmt-sprint-card").forEach((el) => el.classList.remove("drag-over-sprint"));
    document.getElementById("smgmt-backlog-pane")?.classList.remove("drag-over-backlog");
    if (isDragBlocked({ moveLock: _smgmtMoveLock }))
      return;
    if (!_smgmtDragTicket)
      return;
    const dragInfo = _smgmtDragTicket;
    _smgmtDragTicket = null;
    const repo = _smgmtRepo();
    if (!repo)
      return;
    if (dragInfo.multi && dragInfo.multi.length > 1) {
      const nums = dragInfo.multi;
      const targetNum = targetLabel ? parseInt(targetLabel.split("-")[1], 10) : null;
      if (_smgmtData) {
        nums.forEach((n) => {
          const iss = _smgmtData.issues.find((i) => i.number === n);
          if (iss)
            iss.sprint = targetNum;
        });
      }
      _smgmtClearSelection();
      if (_smgmtData)
        _smgmtRender(_smgmtData);
      const changes = nums.map((n) => ({ issue_num: n, sprint_label: targetLabel || "backlog" }));
      _smgmtBoardLock();
      try {
        const res = await fetch("/api/sprints/batch-labels", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ changes, project: repo })
        });
        if (!res.ok)
          throw new Error(await res.text());
        await loadSprintMgmt();
      } catch (e) {
        alert(`Failed to move tickets: ${e.message}`);
        await loadSprintMgmt();
      } finally {
        _smgmtBoardUnlock();
      }
    } else {
      const { number, fromSprint } = dragInfo;
      if (fromSprint === targetLabel)
        return;
      const targetNum = targetLabel ? parseInt(targetLabel.split("-")[1], 10) : null;
      if (_smgmtData) {
        const iss = _smgmtData.issues.find((i) => i.number === number);
        if (iss)
          iss.sprint = targetNum;
        _smgmtRender(_smgmtData);
      }
      _smgmtClearSelection();
      _smgmtBoardLock();
      try {
        const res = await fetch(`/api/issues/${number}/sprint-label`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sprint_label: targetLabel || "backlog", project: repo })
        });
        if (!res.ok)
          throw new Error(await res.text());
        await loadSprintMgmt();
      } catch (e) {
        if (_smgmtData) {
          const iss = _smgmtData.issues.find((i) => i.number === number);
          if (iss)
            iss.sprint = fromSprint ? parseInt(fromSprint.split("-")[1], 10) : null;
          _smgmtRender(_smgmtData);
        }
        alert(`Failed to move ticket #${number}: ${e.message}`);
      } finally {
        _smgmtBoardUnlock();
      }
    }
  }
  function _smgmtTicketReorderDragOver(event) {
    if (!_smgmtDragTicket || _smgmtDragTicket.multi && _smgmtDragTicket.multi.length > 1)
      return;
    const target = event.currentTarget;
    const targetSprint = target.dataset.sprint;
    const dragSprint = _smgmtDragTicket ? _smgmtDragTicket.fromSprint : null;
    if (targetSprint !== dragSprint)
      return;
    event.preventDefault();
    event.stopPropagation();
    const rect = target.getBoundingClientRect();
    const midY = rect.top + rect.height / 2;
    target.classList.remove("drag-before", "drag-after");
    target.classList.add(event.clientY < midY ? "drag-before" : "drag-after");
  }
  function _smgmtTicketReorderDragLeave(event) {
    event.currentTarget.classList.remove("drag-before", "drag-after");
  }
  async function _smgmtTicketReorderDrop(event, targetIssue, sprintLabel) {
    if (!_smgmtDragTicket || _smgmtDragTicket.multi && _smgmtDragTicket.multi.length > 1)
      return;
    const dragInfo = _smgmtDragTicket;
    if (dragInfo.fromSprint !== sprintLabel)
      return;
    const dragIssue = dragInfo.number;
    if (dragIssue === targetIssue) {
      event.currentTarget.classList.remove("drag-before", "drag-after");
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const rect = event.currentTarget.getBoundingClientRect();
    const insertAfter = event.clientY >= rect.top + rect.height / 2;
    event.currentTarget.classList.remove("drag-before", "drag-after");
    const repo = _smgmtRepo();
    if (!repo || !_smgmtData)
      return;
    const container = document.getElementById(`smgmt-tickets-${sprintLabel}`);
    if (!container)
      return;
    const rows = Array.from(container.querySelectorAll(".smgmt-ticket[data-issue]"));
    let order = rows.map((r) => parseInt(r.dataset.issue, 10)).filter((n) => !isNaN(n));
    order = order.filter((n) => n !== dragIssue);
    const insertIdx = order.indexOf(targetIssue) + (insertAfter ? 1 : 0);
    order.splice(insertIdx, 0, dragIssue);
    _smgmtDragTicket = null;
    try {
      const res = await fetch(
        `/api/sprints/${encodeURIComponent(sprintLabel)}/plan?project=${encodeURIComponent(repo)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(order)
        }
      );
      if (!res.ok)
        throw new Error(await res.text());
      await loadSprintMgmt();
    } catch (e) {
      alert(`Failed to reorder tickets: ${e.message}`);
      await loadSprintMgmt();
    }
  }
  function _smgmtBacklogTicketDragStart(event, issueNum) {
    const isChecked = _smgmtSelectedIssues.has(issueNum);
    if (isChecked && _smgmtSelectedIssues.size > 1) {
      const nums = Array.from(_smgmtSelectedIssues);
      _smgmtDragTicket = { number: issueNum, fromSprint: null, multi: nums, multiSprints: 1 };
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", nums.join(","));
      const pill = document.getElementById("smgmt-drag-pill");
      if (pill) {
        pill.textContent = `Moving ${nums.length} tickets`;
        pill.style.top = event.clientY - 20 + "px";
        pill.style.left = event.clientX + 12 + "px";
      }
      setTimeout(() => {
        nums.forEach((n) => {
          const el = document.getElementById(`smgmt-ticket-${n}`);
          if (el)
            el.classList.add("dragging-ticket");
        });
      }, 0);
    } else {
      _smgmtDragTicket = { number: issueNum, fromSprint: null, multi: null };
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", String(issueNum));
      const el = document.getElementById(`smgmt-ticket-${issueNum}`);
      if (el)
        setTimeout(() => el.classList.add("dragging-ticket"), 0);
    }
    _smgmtGhostShow();
  }
  function _smgmtBacklogDragOver(event) {
    if (_smgmtDragTicket && _smgmtDragTicket.fromSprint !== null) {
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      document.getElementById("smgmt-backlog-pane")?.classList.add("drag-over-backlog");
    }
  }
  function _smgmtBacklogDragLeave(event) {
    const pane = document.getElementById("smgmt-backlog-pane");
    if (pane && !pane.contains(event.relatedTarget)) {
      pane.classList.remove("drag-over-backlog");
    }
  }
  async function _smgmtDropOnBacklog(event) {
    event.preventDefault();
    document.getElementById("smgmt-backlog-pane")?.classList.remove("drag-over-backlog");
    if (isDragBlocked({ moveLock: _smgmtMoveLock }))
      return;
    if (!_smgmtDragTicket)
      return;
    const dragInfo = _smgmtDragTicket;
    _smgmtDragTicket = null;
    if (!dragInfo.fromSprint)
      return;
    const repo = _smgmtRepo();
    if (!repo)
      return;
    if (dragInfo.multi && dragInfo.multi.length > 1) {
      const nums = dragInfo.multi;
      if (_smgmtData) {
        nums.forEach((n) => {
          const iss = _smgmtData.issues.find((i) => i.number === n);
          if (iss)
            iss.sprint = null;
        });
      }
      _smgmtClearSelection();
      if (_smgmtData)
        _smgmtRender(_smgmtData);
      const changes = nums.map((n) => ({ issue_num: n, sprint_label: "backlog" }));
      _smgmtBoardLock();
      try {
        const res = await fetch("/api/sprints/batch-labels", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ changes, project: repo })
        });
        if (!res.ok)
          throw new Error(await res.text());
        await loadSprintMgmt();
      } catch (e) {
        alert(`Failed to move tickets to backlog: ${e.message}`);
        await loadSprintMgmt();
      } finally {
        _smgmtBoardUnlock();
      }
    } else {
      const { number, fromSprint } = dragInfo;
      if (_smgmtData) {
        const iss = _smgmtData.issues.find((i) => i.number === number);
        if (iss)
          iss.sprint = null;
        _smgmtRender(_smgmtData);
      }
      _smgmtClearSelection();
      _smgmtBoardLock();
      try {
        const res = await fetch(`/api/issues/${number}/sprint-label`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sprint_label: "backlog", project: repo })
        });
        if (!res.ok)
          throw new Error(await res.text());
        await loadSprintMgmt();
      } catch (e) {
        if (_smgmtData) {
          const iss = _smgmtData.issues.find((i) => i.number === number);
          if (iss)
            iss.sprint = fromSprint ? parseInt(fromSprint.split("-")[1], 10) : null;
          _smgmtRender(_smgmtData);
        }
        alert(`Failed to move ticket #${number} to backlog: ${e.message}`);
      } finally {
        _smgmtBoardUnlock();
      }
    }
  }
  function _smgmtBoardLock(message, opts) {
    _smgmtMoveLock = true;
    _smgmtArStopTicker();
    const overlay = document.getElementById("smgmt-move-overlay");
    const msgEl = document.getElementById("smgmt-move-overlay-msg");
    const progWrap = document.getElementById("smgmt-op-progress-wrap");
    const logEl = document.getElementById("smgmt-op-log");
    const text = message || "Moving\u2026";
    if (msgEl)
      msgEl.textContent = text;
    if (overlay) {
      overlay.setAttribute("aria-label", text.replace(/…$/, "") + ", please wait");
      overlay.classList.add("active");
    }
    const showProgress = !!(opts && opts.progress);
    if (progWrap)
      progWrap.hidden = !showProgress;
    if (logEl) {
      logEl.hidden = !showProgress;
      if (showProgress && opts.clearLog)
        logEl.innerHTML = "";
    }
    if (showProgress && opts.total != null) {
      _smgmtBoardProgress(0, opts.total);
    } else if (!showProgress) {
      _smgmtBoardProgress(0, 1);
    }
  }
  function _smgmtBoardProgress(done, total) {
    const fill = document.getElementById("smgmt-op-progress-fill");
    const pctEl = document.getElementById("smgmt-op-progress-pct");
    const pct = total > 0 ? Math.round(done / total * 100) : 0;
    if (fill)
      fill.style.width = pct + "%";
    if (pctEl)
      pctEl.textContent = pct + "%";
  }
  function _smgmtBoardLog(line, kind) {
    const logEl = document.getElementById("smgmt-op-log");
    if (!logEl)
      return;
    const row = document.createElement("div");
    row.className = "smgmt-op-log-line" + (kind ? ` smgmt-op-log-line--${kind}` : "");
    row.textContent = line;
    logEl.appendChild(row);
    logEl.scrollTop = logEl.scrollHeight;
  }
  function _smgmtBoardUnlock() {
    _smgmtMoveLock = false;
    const overlay = document.getElementById("smgmt-move-overlay");
    if (overlay)
      overlay.classList.remove("active");
    const progWrap = document.getElementById("smgmt-op-progress-wrap");
    const logEl = document.getElementById("smgmt-op-log");
    if (progWrap)
      progWrap.hidden = true;
    if (logEl) {
      logEl.hidden = true;
      logEl.innerHTML = "";
    }
    _smgmtBoardProgress(0, 1);
    if (_arInterval > 0)
      _smgmtArStartTicker();
  }

  // apps/dashboard/static/src/sprint-board/board-render.js
  async function loadSprintMgmt2(silent, optimisticRunningLabel) {
    const listEl = document.getElementById("smgmt-sprint-list");
    if (!listEl)
      return;
    const repo = _cachedFullRepo[_slug] || null;
    if (!repo) {
      listEl.innerHTML = '<div class="loading-msg">Project not found.</div>';
      return;
    }
    if (!silent) {
      listEl.innerHTML = '<div class="loading-msg">Loading sprints\u2026</div>';
      for (const k of Object.keys(_smgmtFinishCards))
        delete _smgmtFinishCards[k];
    }
    try {
      if (typeof _smgmtEnsureCapData === "function") {
        await _smgmtEnsureCapData();
      }
      const [resp, runningResp] = await Promise.all([
        fetch("/api/sprint-management/issues?repo=" + encodeURIComponent(repo)),
        fetch("/api/sprints/running-all").catch(() => null)
      ]);
      if (!resp.ok) {
        let msg = "Failed to load sprints.";
        const d = await resp.json().catch(() => null);
        const detail = d && typeof d.detail === "string" ? d.detail : "";
        if (resp.status === 429 || /rate limit/i.test(detail)) {
          msg = detail || "GitHub API rate limit reached \u2014 retry shortly.";
        }
        throw new Error(msg);
      }
      const data = await resp.json();
      if (typeof _smgmtLingerRestore === "function")
        _smgmtLingerRestore();
      const prevRunning = new Set(_smgmtRunningLabels);
      _smgmtRunningLabels = /* @__PURE__ */ new Set();
      _smgmtAnySprintRunning = false;
      if (runningResp && runningResp.ok) {
        const runningData = await runningResp.json();
        const running = runningData.running || [];
        running.forEach((r) => {
          if (r.project === repo) {
            _smgmtRunningLabels.add(r.sprint_label);
          }
        });
        _smgmtAnySprintRunning = _smgmtRunningLabels.size > 0;
      }
      for (const label of prevRunning) {
        if (!_smgmtRunningLabels.has(label) && typeof _smgmtLingerStart === "function") {
          _smgmtLingerStart(label);
        }
      }
      if (optimisticRunningLabel) {
        _smgmtRunningLabels.add(optimisticRunningLabel);
        _smgmtAnySprintRunning = true;
      }
      _smgmtRender2(data);
      if (typeof _smgmtHydrateSchedToggles === "function") {
        _smgmtHydrateSchedToggles(repo);
      }
      _smgmtLivePollRestart();
      const lingerLbl = typeof _smgmtPrimaryRunningLabel === "function" ? _smgmtPrimaryRunningLabel() : null;
      if (lingerLbl && typeof _smgmtRunningViewUpdate === "function") {
        const live = typeof _smgmtLingerLive === "function" ? _smgmtLingerLive(lingerLbl) : _smgmtLiveCache[lingerLbl] || null;
        _smgmtRunningViewUpdate(lingerLbl, live);
      }
    } catch (err) {
      if (!silent) {
        const msg = err && err.message ? err.message : "Failed to load sprints.";
        listEl.innerHTML = `<div class="loading-msg">${escHtml(msg)}</div>`;
      }
    }
  }
  function _smgmtSprintLabelSortKey(label) {
    const m = String(label).match(/^sprint-(\d+(?:\.\d+)*)$/);
    if (!m)
      return [Infinity];
    return m[1].split(".").map((n) => parseInt(n, 10));
  }
  function _smgmtRender2(data) {
    const listEl = document.getElementById("smgmt-sprint-list");
    if (!listEl)
      return;
    _smgmtData = data;
    _smgmtUpdateSubnav();
    const sprints = data.sprints || [];
    const order = data.order || [];
    const issues = data.issues || [];
    const bySprint = {};
    const unassigned = [];
    issues.forEach((iss) => {
      const key = iss.sprint_label || null;
      if (key != null) {
        if (!bySprint[key])
          bySprint[key] = [];
        bySprint[key].push(iss);
      } else {
        unassigned.push(iss);
      }
    });
    _smgmtBySprint = bySprint;
    _smgmtRenderBacklog(unassigned);
    if (order.length === 0 && sprints.length === 0) {
      listEl.innerHTML = '<div class="loading-msg">No sprints yet. Create one with + New Sprint.</div>';
      return;
    }
    const orderedLabelsRaw = order.length > 0 ? order.filter((l) => /^sprint-\d+(\.\d+)*$/.test(l)) : [...sprints].sort((a, b) => a - b).map((n) => `sprint-${n}`);
    const _sprintParents = data.sprint_parents || {};
    const _rerunInto = data.sprint_rerun_into || {};
    const orderedLabels = orderedLabelsRaw.filter((label) => {
      const ticketCount = (bySprint[label] || []).length;
      if (ticketCount > 0)
        return true;
      if (_rerunInto[label])
        return false;
      const hasChild = Object.values(_sprintParents).some((parent) => parent === label);
      return !hasChild;
    });
    _smgmtFinishedLabels = new Set(data.finished_sprints || []);
    let _smgmtNextUpLabel = null;
    const sortedForNext = [...orderedLabels].sort((a, b) => {
      const ka = _smgmtSprintLabelSortKey(a);
      const kb = _smgmtSprintLabelSortKey(b);
      for (let i = 0; i < Math.max(ka.length, kb.length); i++) {
        const d = (ka[i] ?? Infinity) - (kb[i] ?? Infinity);
        if (d !== 0)
          return d;
      }
      return 0;
    });
    for (const lbl of sortedForNext) {
      if (_smgmtRunningLabels.has(lbl))
        continue;
      if (typeof _smgmtIsLinger === "function" && _smgmtIsLinger(lbl))
        continue;
      if (_smgmtFinishedLabels.has(lbl))
        continue;
      if ((bySprint[lbl] || []).length >= 1) {
        _smgmtNextUpLabel = lbl;
        break;
      }
    }
    const cards = orderedLabels.map((label) => {
      const tickets = bySprint[label] || [];
      if (_smgmtIsFreshRerunSprint(label))
        delete _smgmtOutcomeCache[label];
      const inLinger = typeof _smgmtIsLinger === "function" && _smgmtIsLinger(label);
      const outcome = _smgmtRunningLabels.has(label) || inLinger ? null : _smgmtOutcomeCache[label] || null;
      const parent = _sprintParents[label] || null;
      const cardHtml = _smgmtCardHtml(label, null, tickets, outcome, label === _smgmtNextUpLabel, parent, _smgmtFinishedLabels.has(label));
      return `<div class="smgmt-sprint-unit" id="smgmt-unit-${escHtml(label)}">` + cardHtml + `</div>`;
    }).join("");
    listEl.innerHTML = cards || '<div class="loading-msg">No sprints found.</div>';
    _smgmtInitCapacityGauges(orderedLabels);
    _smgmtRenderAllCapBars();
    _smgmtEnsureCapData(false);
    if (_smgmtSelectedIssues.size > 0)
      _smgmtUpdateSelectionUI();
    for (const [lbl, fc] of Object.entries(_smgmtFinishCards)) {
      if (fc)
        _smgmtRenderFinishCard(lbl, fc.card, fc.branch, _smgmtRepo());
    }
    _smgmtLoadFinishCards();
    _smgmtFetchMissingOutcomes(orderedLabels, bySprint);
    _smgmtLoadEstimates(orderedLabels, bySprint);
    _smgmtCheckEstimatorHealth();
    _smgmtLoadGoals(orderedLabels);
    _preflightLoadBanners(orderedLabels, bySprint);
    _smgmtLoadConflicts(orderedLabels, bySprint);
    _smgmtLoadDepOrder(orderedLabels, bySprint);
    _smgmtLoadMiniRail(orderedLabels, bySprint);
    _smgmtUpdateCleanupBtn(data);
    _smgmtLabelFilterRender(issues);
    _smgmtLabelFilterApply();
    _smgmtKbRestoreFocus();
    orderedLabels.forEach((lbl) => _smgmtApplySort(lbl));
    _smgmtFilterApply();
  }
  function _smgmtLabelFilterRender(issues) {
    _smgmtLastLabelIssues = issues || [];
    const row = document.getElementById("smgmt-label-filter-row");
    if (!row)
      return;
    const seen = /* @__PURE__ */ new Set();
    (issues || []).forEach((iss) => {
      (iss.labels || []).forEach((l) => {
        seen.add(l.name);
        if (l.color)
          _smgmtLabelColors[l.name] = "#" + l.color;
      });
    });
    const priority = _SMGMT_FILTER_PRIORITY.filter((n) => seen.has(n));
    const rest = [...seen].filter((n) => !_SMGMT_FILTER_PRIORITY.includes(n)).sort();
    const allLabels = [...priority, ...rest];
    if (allLabels.length === 0) {
      row.classList.add("is-empty");
      row.innerHTML = "";
      return;
    }
    const _SMGMT_LABEL_VISIBLE = 5;
    const expanded = row.dataset.expanded === "true";
    const visible = expanded ? allLabels : allLabels.slice(0, _SMGMT_LABEL_VISIBLE);
    const hidden = allLabels.length - _SMGMT_LABEL_VISIBLE;
    row.classList.remove("is-empty");
    row.innerHTML = visible.map((name) => {
      const active = !_smgmtDeactivatedLabels.has(name);
      const color = _smgmtLabelColors[name] || "var(--text-muted)";
      return `<button class="smgmt-lf-chip ${active ? "is-active" : "is-inactive"}"
               data-label="${escHtml(name)}"
               aria-pressed="${active}"
               title="${active ? "Hide" : "Show"} tickets labeled &quot;${escHtml(name)}&quot;"
               onclick="_smgmtLabelFilterToggle('${escHtml(name)}')">
              <span class="smgmt-lf-chip-dot" style="background:${color}"></span>
              ${escHtml(name)}
            </button>`;
    }).join("") + (hidden > 0 && !expanded ? `<button class="smgmt-lf-show-more" onclick="_smgmtLabelFilterToggleExpand(true)">+${hidden} more</button>` : hidden > 0 && expanded ? `<button class="smgmt-lf-show-more" onclick="_smgmtLabelFilterToggleExpand(false)">Show less</button>` : "");
  }
  function _smgmtLabelFilterApply() {
    if (_smgmtDeactivatedLabels.size === 0) {
      document.querySelectorAll(".smgmt-ticket[data-labels]").forEach((el) => {
        el.style.display = "";
      });
      return;
    }
    document.querySelectorAll(".smgmt-ticket[data-labels]").forEach((el) => {
      const raw = el.getAttribute("data-labels") || "";
      const ticketLabels = raw ? raw.split(",") : [];
      if (ticketLabels.length === 0) {
        el.style.display = "";
        return;
      }
      const allDeactivated = ticketLabels.every((n) => _smgmtDeactivatedLabels.has(n));
      el.style.display = allDeactivated ? "none" : "";
    });
  }
  function _smgmtIsFreshRerunSprint(label) {
    const parents = _smgmtData && _smgmtData.sprint_parents || {};
    if (!parents[label])
      return false;
    const planState = (_smgmtData && _smgmtData.sprint_plan_states || {})[label];
    return planState === "draft" || planState === "planning";
  }
  function _smgmtApplyRerunOptimistic2(parentLabel, subLabel, ticketNumbers) {
    if (!_smgmtData || !parentLabel || !subLabel)
      return;
    const nums = new Set(ticketNumbers || []);
    const issues = _smgmtData.issues || [];
    for (const iss of issues) {
      if (nums.has(iss.number))
        iss.sprint_label = subLabel;
    }
    if (!_smgmtData.order)
      _smgmtData.order = [];
    if (!_smgmtData.order.includes(subLabel)) {
      const parentIdx = _smgmtData.order.indexOf(parentLabel);
      if (parentIdx >= 0)
        _smgmtData.order.splice(parentIdx + 1, 0, subLabel);
      else
        _smgmtData.order.push(subLabel);
    }
    if (!_smgmtData.sprint_parents)
      _smgmtData.sprint_parents = {};
    _smgmtData.sprint_parents[subLabel] = parentLabel;
    if (!_smgmtData.sprint_rerun_into)
      _smgmtData.sprint_rerun_into = {};
    _smgmtData.sprint_rerun_into[parentLabel] = subLabel;
    if (!_smgmtData.sprint_plan_states)
      _smgmtData.sprint_plan_states = {};
    _smgmtData.sprint_plan_states[subLabel] = "draft";
    delete _smgmtOutcomeCache[parentLabel];
    delete _smgmtOutcomeCache[subLabel];
    if (_smgmtBySprint) {
      const moved = (_smgmtBySprint[parentLabel] || []).filter((t) => nums.has(t.number));
      _smgmtBySprint[subLabel] = [..._smgmtBySprint[subLabel] || [], ...moved];
      _smgmtBySprint[parentLabel] = (_smgmtBySprint[parentLabel] || []).filter((t) => !nums.has(t.number));
    }
  }
  async function _smgmtFetchMissingOutcomes(orderedLabels, bySprint) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    const toFetch = [];
    for (const label of orderedLabels) {
      if (_smgmtRunningLabels.has(label))
        continue;
      if (_smgmtIsFreshRerunSprint(label))
        continue;
      if (_smgmtOutcomeCache[label] !== void 0)
        continue;
      const tickets = bySprint[label] || [];
      const hasRework = tickets.some((t) => (t.labels || []).some((l) => l.name === "need-rework" || l.name === "needs-rework"));
      const hasCompleted = _smgmtHasCompletedTickets(tickets);
      if (tickets.length > 0 && !hasRework && !hasCompleted)
        continue;
      toFetch.push(label);
    }
    await Promise.all(toFetch.map(async (label) => {
      try {
        const resp = await fetch(
          `/api/sprints/${encodeURIComponent(label)}/outcome?project=${encodeURIComponent(repo)}`
        );
        if (resp.ok) {
          const outcome = await resp.json();
          _smgmtOutcomeCache[label] = outcome;
          _smgmtInjectOutcomeBand(label, outcome);
        } else {
          _smgmtOutcomeCache[label] = null;
        }
      } catch (_) {
        _smgmtOutcomeCache[label] = null;
      }
    }));
  }
  async function _smgmtLoadEstimates(orderedLabels, bySprint) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    for (const label of orderedLabels) {
      const tickets = bySprint[label] || [];
      if (tickets.length === 0)
        continue;
      for (const t of tickets)
        _smgmtTicketToSprint[t.number] = label;
      const issueNums = tickets.map((t) => t.number).join(",");
      try {
        const resp = await fetch(
          `/api/estimates/batch?project=${encodeURIComponent(repo)}&issues=${issueNums}`
        );
        if (!resp.ok)
          continue;
        const data = await resp.json();
        const estEl = document.getElementById(`smgmt-est-${label}`);
        if (estEl && data.complete && data.total_hours !== null) {
          const h = data.total_hours;
          const display = Number.isInteger(h) ? `${h}h` : `${parseFloat(h.toFixed(1))}h`;
          estEl.textContent = `${display} estimated`;
        }
        _smgmtSetSprintTokenEl(label, data);
        if (data.issues) {
          for (const [numStr, est] of Object.entries(data.issues)) {
            _estDataCache[parseInt(numStr, 10)] = est;
          }
          for (const t of tickets) {
            _smgmtUpdateEstimateBadge(t.number);
          }
          _smgmtUpdateColRollup(label, tickets);
          _smgmtUpdateCapacityGauge(label);
        }
      } catch (_) {
      }
    }
  }
  async function _smgmtLoadConflicts(orderedLabels, bySprint) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    for (const label of orderedLabels) {
      if (_smgmtRunningLabels.has(label))
        continue;
      if (_smgmtFinishedLabels.has(label))
        continue;
      const tickets = bySprint[label] || [];
      const pending = tickets.filter((t) => (t.status || "backlog") === "backlog");
      if (pending.length < 2)
        continue;
      for (const t of pending)
        delete _smgmtConflictsByIssue[t.number];
      try {
        const resp = await fetch(
          `/api/sprints/${encodeURIComponent(label)}/conflicts?project=${encodeURIComponent(repo)}`
        );
        if (!resp.ok)
          continue;
        const data = await resp.json();
        for (const c of data.conflicts || []) {
          if (!_smgmtConflictsByIssue[c.ticket1_id])
            _smgmtConflictsByIssue[c.ticket1_id] = [];
          if (!_smgmtConflictsByIssue[c.ticket2_id])
            _smgmtConflictsByIssue[c.ticket2_id] = [];
          _smgmtConflictsByIssue[c.ticket1_id].push({ partnerId: c.ticket2_id, partnerTitle: c.ticket2_title, sharedFiles: c.shared_files });
          _smgmtConflictsByIssue[c.ticket2_id].push({ partnerId: c.ticket1_id, partnerTitle: c.ticket1_title, sharedFiles: c.shared_files });
        }
        for (const t of pending)
          _smgmtUpdateConflictBadge(t.number);
      } catch (_) {
      }
    }
  }
  async function _smgmtLoadDepOrder(orderedLabels, bySprint) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    for (const label of orderedLabels) {
      if (_smgmtRunningLabels.has(label))
        continue;
      if (_smgmtFinishedLabels.has(label))
        continue;
      const tickets = bySprint[label] || [];
      const pending = tickets.filter((t) => (t.status || "backlog") === "backlog");
      if (pending.length < 2)
        continue;
      for (const t of pending)
        delete _smgmtDepOrderByIssue[t.number];
      try {
        const resp = await fetch(
          `/api/sprints/${encodeURIComponent(label)}/dep-order?project=${encodeURIComponent(repo)}`
        );
        if (!resp.ok)
          continue;
        const data = await resp.json();
        if (data.has_cycle) {
          const cycleSet = new Set((data.in_cycle_tickets || []).map(String));
          for (const t of pending) {
            if (cycleSet.has(String(t.number))) {
              _smgmtDepOrderByIssue[t.number] = { upstream: [], downstream: [], inCycle: true };
            }
          }
        } else {
          for (const [idStr, hint] of Object.entries(data.dep_hints || {})) {
            const num = parseInt(idStr, 10);
            _smgmtDepOrderByIssue[num] = {
              upstream: hint.upstream || [],
              downstream: hint.downstream || [],
              inCycle: false
            };
          }
        }
        for (const t of pending)
          _smgmtUpdateDepOrderBadge(t.number);
      } catch (_) {
      }
    }
  }
  async function _smgmtLoadGoals(orderedLabels) {
    const repo = _smgmtRepo();
    if (!repo)
      return;
    for (const label of orderedLabels) {
      const goalEl = document.getElementById(`smgmt-goal-${label}`);
      if (!goalEl)
        continue;
      try {
        const resp = await fetch(
          `/api/sprints/goal?project=${encodeURIComponent(repo)}&sprint=${encodeURIComponent(label)}`
        );
        if (!resp.ok)
          continue;
        const data = await resp.json();
        const goal = (data.goal || "").trim();
        if (goal) {
          goalEl.textContent = goal;
          goalEl.title = goal;
          goalEl.style.display = "";
        }
      } catch (_) {
      }
    }
  }
  function _smgmtOutcomeBandHtml(label, outcome) {
    const st = outcome.sprint_status;
    const paneState = outcome.state || "";
    const c = outcome.counts || {};
    const dur = _fmtWallClock(outcome.wall_clock_secs);
    const ts = outcome.ended_at ? st === "completed" ? `ended ${outcome.ended_at}` : `stopped ${outcome.ended_at}` : "";
    const issues = outcome.issues || [];
    let segBarHtml = "";
    if (issues.length > 0) {
      const blocks = issues.map((iss) => {
        const o = iss.outcome || "skipped";
        let blockClass = "seg-pending";
        if (o === "done")
          blockClass = "seg-done";
        else if (o === "failed")
          blockClass = "seg-failed";
        else if (o === "skipped")
          blockClass = "seg-skipped";
        return `<div class="seg-block ${blockClass}"></div>`;
      }).join("");
      segBarHtml = `<div class="smgmt-seg-bar">${blocks}</div>`;
    }
    let linksHtml = "";
    if (paneState === "completed" || st === "completed") {
      const prNum = outcome.pr_number;
      const prUrl = outcome.pr_url;
      const sumNum = outcome.summary_issue_num;
      const sumUrl = outcome.summary_issue_url;
      const prLink = prNum && prUrl ? `<a href="${escHtml(prUrl)}" target="_blank" rel="noopener" class="oc-pr-link"><i class="ti ti-git-pull-request"></i> PR #${prNum}</a>` : "";
      const sumLink = sumNum && sumUrl ? `<a href="${escHtml(sumUrl)}" target="_blank" rel="noopener" class="oc-summary-link"><i class="ti ti-file-description"></i> #${sumNum} Sprint Summary</a>` : sumNum ? `<span class="oc-summary-link"><i class="ti ti-file-description"></i> #${sumNum} Sprint Summary</span>` : "";
      if (prLink || sumLink) {
        linksHtml = `<div class="oc-band-links">${prLink}${sumLink}</div>`;
      }
    }
    return `<div class="smgmt-outcome-band ${escHtml(st || "")}">
    <div class="smgmt-outcome-stat"><span class="onum green">${c.done || 0}</span><span class="olbl">Completed</span></div>
    <div class="smgmt-outcome-stat"><span class="onum ${c.failed ? "red" : "muted"}">${c.failed || 0}</span><span class="olbl">Failed</span></div>
    <div class="smgmt-outcome-stat"><span class="onum muted">${c.skipped || 0}</span><span class="olbl">Skipped</span></div>
    <span class="oc-spacer"></span>
    ${segBarHtml}
    <div class="smgmt-outcome-dur"><i class="ti ti-clock" style="vertical-align:-1px;"></i> ${escHtml(dur)}${ts ? " \xB7 " + escHtml(ts) : ""}</div>
    ${linksHtml}
  </div>`;
  }
  function _smgmtOutcomeTicketListHtml(issues, label, repo) {
    if (!issues || issues.length === 0)
      return "";
    const safeLabel = label ? escHtml(label) : "";
    const safeRepo = repo ? escHtml(repo) : "";
    return issues.map((iss) => {
      const o = iss.outcome || "skipped";
      let circle = "";
      if (o === "done")
        circle = '<div class="smgmt-ticket-circle done">\u2713</div>';
      else if (o === "failed")
        circle = '<div class="smgmt-ticket-circle failed">\u2715</div>';
      else
        circle = '<div class="smgmt-ticket-circle skipped">\u2212</div>';
      const elapsed = `<span class="smgmt-ticket-elapsed">${escHtml(_fmtElapsed(iss.elapsed_secs))}</span>`;
      const rejLabel = o === "failed" ? '<span class="smgmt-lbl-rejected">TESTER REJECTED</span>' : "";
      const viewLogBtn = safeLabel && safeRepo ? `<button class="btn-view-log" title="View issue log"
              onclick="event.stopPropagation();openLvIssueLog(${iss.number},'${safeLabel}','${safeRepo}')">
           <i class="ti ti-file-text"></i></button>` : "";
      return `<div class="smgmt-ticket" data-issue="${iss.number}" data-labels="" draggable="false">
      ${circle}
      <a class="smgmt-ticket-num" href="${safeRepo ? `https://github.com/${safeRepo}/issues/${iss.number}` : "#"}" target="_blank" rel="noopener">#${iss.number}</a>
      <span class="smgmt-ticket-title" title="${escHtml(iss.title)}">${escHtml(iss.title)}</span>
      ${rejLabel}
      ${viewLogBtn}
      ${elapsed}
      <button class="t-details-btn" onclick="event.stopPropagation();toggleTicketRow('${safeLabel}',${iss.number})">
        <span class="t-dbtn-label">Details</span> <span id="caret-${safeLabel}-${iss.number}">\u25BC</span>
      </button>
    </div>
    <div class="ticket-expand" id="ex-${safeLabel}-${iss.number}" style="display:none">
      <div class="ex-row">
        <span class="ex-label">Conflicts</span>
        <span class="ex-conflicts-val">\u2014</span>
      </div>
      <div class="ex-row">
        <span class="ex-label">Execution</span>
        <span class="ex-exec-val">\u2014</span>
      </div>
      <div class="ex-actions">
        <button class="ex-btn" onclick="event.stopPropagation();_smgmtReEstimate(${iss.number},this)"><i class="ti ti-sparkles" style="font-size:12px"></i> Re-estimate</button>
        <button class="ex-btn" onclick="event.stopPropagation();_smgmtRowMenuOpen(event,${iss.number},'${safeLabel}',false)"><i class="ti ti-arrow-right" style="font-size:12px"></i> Move to sprint</button>
        <button class="ex-btn ex-btn-danger" onclick="event.stopPropagation();_smgmtCloseIssueOpen(${iss.number})"><i class="ti ti-x" style="font-size:12px"></i> Close ticket</button>
      </div>
    </div>`;
    }).join("");
  }
  async function _smgmtLoadFinishCards() {
    const repo = _smgmtRepo();
    if (!repo || !_smgmtData)
      return;
    const order = _smgmtData.order && _smgmtData.order.length ? _smgmtData.order : (_smgmtData.sprints || []).map((n) => `sprint-${n}`);
    await Promise.allSettled(order.map(async (label) => {
      if (_smgmtIsFreshRerunSprint(label))
        return;
      try {
        const [cardRes, branchRes] = await Promise.all([
          fetch(`/api/sprints/${encodeURIComponent(label)}/finish-card?project=${encodeURIComponent(repo)}`),
          fetch(`/api/sprints/${encodeURIComponent(label)}/branch-status?project=${encodeURIComponent(repo)}`).catch(() => null)
        ]);
        if (!cardRes.ok) {
          console.warn(`finish-card: unexpected ${cardRes.status} for ${label}`);
          return;
        }
        const cardData = await cardRes.json();
        if (cardData.state === "no_data")
          return;
        const branchData = branchRes && branchRes.ok ? await branchRes.json() : { exists: false };
        _smgmtFinishCards[label] = { card: cardData, branch: branchData };
        _smgmtRenderFinishCard(label, cardData, branchData, repo);
      } catch (e) {
        console.warn("finish-card load error for", label, e);
      }
    }));
  }
  function _smgmtRenderFinishCard(label, cardData, branchData, repo) {
    if (branchData && branchData.pr_url && branchData.pr_number) {
      const sprintCard = document.getElementById(`smgmt-card-${label}`);
      if (sprintCard) {
        let linksEl = sprintCard.querySelector(".oc-band-links");
        if (!linksEl) {
          const band = sprintCard.querySelector(".smgmt-outcome-band");
          if (band) {
            linksEl = document.createElement("div");
            linksEl.className = "oc-band-links";
            band.appendChild(linksEl);
          }
        }
        if (linksEl && !linksEl.querySelector(".oc-pr-link")) {
          const prLink = document.createElement("a");
          prLink.href = branchData.pr_url;
          prLink.target = "_blank";
          prLink.rel = "noopener";
          prLink.className = "oc-pr-link";
          prLink.innerHTML = `<i class="ti ti-git-pull-request"></i> PR #${branchData.pr_number}`;
          linksEl.insertBefore(prLink, linksEl.firstChild);
        }
      }
    }
    if (cardData.state === "no_data")
      return;
    const cardEl = document.getElementById(`smgmt-finish-card-${label}`);
    const blockEl = document.getElementById(`smgmt-card-${label}`);
    if (!cardEl || !blockEl)
      return;
    const isFinished = cardData.state === "completed" || cardData.state === "has_rework";
    const hasPr = !!(branchData && branchData.pr_url);
    const hasSummary = !!cardData.summary_issue_num;
    if (isFinished && !(hasPr && hasSummary)) {
      cardEl.style.display = "none";
      return;
    }
    cardEl.style.display = "";
    cardEl.className = `smgmt-finish-card sfc-${cardData.state}`;
    cardEl.innerHTML = _smgmtFinishCardInnerHtml(cardData, branchData, repo);
    blockEl.classList.add("smgmt-has-card");
  }
  function _smgmtFinishCardInnerHtml(cardData, branchData, repo) {
    const state = cardData.state;
    const n = cardData.sprint_number;
    const branchName = `sprint/sprint-${n}`;
    const branchUrl = `https://github.com/${escHtml(repo)}/tree/${branchName}`;
    const branchLink = branchData && branchData.exists ? `<a href="${branchUrl}" target="_blank" rel="noopener" class="sfc-branch-link"><i class="ti ti-git-branch"></i> ${escHtml(branchName)}</a>` : `<a href="${branchUrl}" target="_blank" rel="noopener" class="sfc-branch-link sfc-branch-link--warn" title="Could not verify branch exists on GitHub"><i class="ti ti-alert-triangle"></i> ${escHtml(branchName)}</a>`;
    if (state === "running")
      return _sfcRunningHtml(cardData, branchLink, n);
    if (state === "completed")
      return _sfcCompletedHtml(cardData, branchLink, n, branchData);
    if (state === "has_rework" || state === "cancelled") {
      return _sfcHasReworkHtml(cardData, branchLink, n, branchData);
    }
    return "";
  }
  function _smgmtCardHtml(label, n, tickets, outcome, isNext, parent, finished) {
    const isRunning = _smgmtRunningLabels.has(label);
    const isLinger = !isRunning && typeof _smgmtIsLinger === "function" && _smgmtIsLinger(label);
    const isRunningView = isRunning || isLinger;
    let isCollapsed = false;
    try {
      isCollapsed = localStorage.getItem("sprintColumn_" + label + "_collapsed") === "1";
    } catch (_) {
    }
    const isFreshRerun = _smgmtIsFreshRerunSprint(label);
    if (isFreshRerun)
      outcome = null;
    const outcomeLifecycle = (outcome && outcome.lifecycle || "").toLowerCase();
    const outcomeState = outcome && (outcome.state || (outcome.sprint_status === "completed" ? "completed" : null));
    const isHasRework = outcomeLifecycle === "needs_rework" || outcomeState === "has_rework" || outcomeState === "cancelled";
    const isReadyToMerge = outcomeLifecycle === "ready_to_merge" || outcomeLifecycle === "completed" && outcomeState === "completed";
    const hasCompleted = isFreshRerun ? false : _smgmtHasCompletedTickets(tickets);
    const isPostRun = !isRunningView && !!(outcome && (outcome.sprint_status || outcome.state) || hasCompleted);
    const canRun = tickets.length >= 1 && !hasCompleted;
    const rerunDisabled = _smgmtAnySprintRunning ? "disabled" : "";
    const rerunTitle = _smgmtAnySprintRunning ? 'title="Cannot re-run: another sprint is currently running."' : "";
    const childLabel = _smgmtNextChildLabel(label);
    const childDisplay = sprintLabelDisplay(childLabel).replace("Sprint ", "");
    const rerunBtn = `<button class="smgmt-run-btn smgmt-run-btn--rerun" ${rerunDisabled} ${rerunTitle}
                    onclick="smgmtRerunSprint('${escHtml(label)}')">
                    <i class="ti ti-refresh"></i> Re-run \u2192 ${escHtml(childDisplay)}</button>`;
    const rerunInto = (_smgmtData?.sprint_rerun_into || {})[label];
    const rerunChildDisplay = rerunInto ? sprintLabelDisplay(rerunInto).replace("Sprint ", "") : "";
    let actionBtn;
    if (isRunning) {
      actionBtn = `<button class="smgmt-cancel-btn" onclick="smgmtCancelSprint('${escHtml(label)}')">
                  <i class="ti ti-player-stop"></i> Cancel sprint</button>`;
    } else if (isLinger) {
      actionBtn = `<span class="smgmt-linger-note">Finished \u2014 snapshot kept 1h</span>`;
    } else if (isHasRework && rerunInto && tickets.length === 0) {
      actionBtn = `<button class="smgmt-run-btn" ${rerunDisabled} ${rerunTitle}
                  onclick="smgmtRunSprint('${escHtml(rerunInto)}')">
                  <i class="ti ti-player-play"></i> Run \u2192 ${escHtml(rerunChildDisplay)}</button>`;
    } else if (isHasRework || isPostRun) {
      actionBtn = rerunBtn;
    } else if (_smgmtAnySprintRunning) {
      actionBtn = `<button class="smgmt-run-btn smgmt-run-btn--blocked"
                  title="Another sprint is running"
                  onclick="smgmtRunBlockedToast()">
                  <i class="ti ti-player-play"></i> Run Sprint</button>`;
    } else {
      const runDisabled = !canRun ? "disabled" : "";
      const runTitle = !canRun ? 'title="Add at least one ticket first"' : "";
      const schedToggle = typeof _smgmtSchedToggleHtml === "function" ? _smgmtSchedToggleHtml(label) : "";
      actionBtn = `<button class="smgmt-run-btn" ${runDisabled} ${runTitle}
                  onclick="smgmtRunSprint('${label}')">
                  <i class="ti ti-player-play"></i> Run Sprint</button>${schedToggle}`;
    }
    const isOutcomeCompleted = isReadyToMerge || isHasRework || outcomeState === "completed";
    const finishHidden = isOutcomeCompleted || isPostRun && !outcome ? "" : "hidden";
    const finishDisabled = isReadyToMerge && tickets.length === 0 ? "disabled" : "";
    let outcomeBandHtml = "";
    let outcomeCardClass = "";
    let outcomeBadgeHtml = "";
    let headerMetaHtml = "";
    let ticketCount = tickets.length;
    let ticketsContainerHtml = "";
    let isOutcomeView = false;
    let rollupItems = tickets;
    if (outcome && (outcome.sprint_status || outcome.state)) {
      const meta = _smgmtStateMeta(outcome, (outcome.issues || []).length);
      outcomeCardClass = " " + meta.cardClass;
      outcomeBadgeHtml = `<span class="smgmt-state-badge ${meta.badgeCls}">${escHtml(meta.badge)}</span>`;
      if (meta.state === "needs_rework") {
        const _metaSecs = outcome.wall_clock_secs;
        const _metaStopped = outcome.ended_at ? _fmtStoppedAt(outcome.ended_at) : null;
        const _metaParts = [];
        if (_metaSecs != null)
          _metaParts.push(_fmtRunningTime(_metaSecs));
        if (_metaStopped)
          _metaParts.push(`stopped ${_metaStopped}`);
        if (_metaParts.length)
          headerMetaHtml = `<span class="smgmt-sprint-meta">${escHtml(_metaParts.join(" \xB7 "))}</span>`;
        const _elapsedByNum = {};
        if (outcome.issues) {
          for (const _oi of outcome.issues) {
            if (_oi.elapsed_secs != null)
              _elapsedByNum[_oi.number] = _oi.elapsed_secs;
          }
        }
        ticketsContainerHtml = tickets.length > 0 ? tickets.map((t) => _smgmtTicketRowHtml(t, label, _elapsedByNum[t.number] ?? null)).join("") : '<div class="smgmt-drop-hint">Drop tickets here</div>';
      } else {
        outcomeBandHtml = _smgmtOutcomeBandHtml(label, outcome);
        const issueList = outcome.issues || [];
        ticketCount = issueList.length;
        ticketsContainerHtml = _smgmtOutcomeTicketListHtml(issueList, label, _smgmtRepo());
        isOutcomeView = true;
        rollupItems = issueList.map((i) => ({ number: i.number }));
      }
    } else if (isRunningView) {
      ticketsContainerHtml = _smgmtRunningTicketRowsHtml(label, tickets);
    } else {
      ticketsContainerHtml = tickets.length > 0 ? tickets.map((t) => _smgmtTicketRowHtml(t, label)).join("") : '<div class="smgmt-drop-hint">Drop tickets here</div>';
      if (finished) {
        outcomeBadgeHtml = `<span class="smgmt-state-badge state-finished">READY TO MERGE</span>`;
      }
    }
    const summaryHtml = `<div class="sc-budget-section">
    <div class="sc-budget-head">
      <span class="sc-budget-eyebrow">SPRINT BUDGET</span>
      <span class="sc-budget-forecast" id="sc-budget-forecast-${escHtml(label)}"></span>
    </div>
    <div class="cap" id="smgmt-cap-${escHtml(label)}"></div>
    <div class="smgmt-sprint-goal-text" id="smgmt-goal-${escHtml(label)}" style="display:none"></div>
  </div>
  <div class="sc-preview-slot" id="sc-preview-${escHtml(label)}"></div>`;
    const logHtml = "";
    const cancelBannerHtml = "";
    const hasUnsizedTickets = tickets.length > 0 && tickets.some((t) => !_smgmtTicketHasEstimate(t));
    const bulkEstBtnHtml = `<button class="smgmt-bulk-est-btn${hasUnsizedTickets ? "" : " hidden"}"
                    onclick="event.stopPropagation();_smgmtBulkEstimate('${escHtml(label)}',this)"
                    title="Estimate all unsized tickets in this sprint">
                    <i class="ti ti-calculator"></i> Estimate all unsized</button>
                   <span class="smgmt-bulk-est-progress"></span>`;
    const plannedBadge = !isNext && !finished && !isPostRun && !outcomeBadgeHtml ? '<span class="sc-planned-badge">PLANNED</span>' : "";
    const blockedHint = _smgmtAnySprintRunning && !isPostRun && !isRunningView ? `<span class="sc-blocked-hint">blocked: ${_smgmtRunningBlockerShort()} running</span>` : "";
    const parentLineage = parent && !isFreshRerun ? `<span class="smgmt-sprint-lineage" title="Child sprint spawned from ${escHtml(parent)}">\u2190 from ${escHtml(sprintLabelDisplay(parent))}</span>` : "";
    const live = isRunningView ? (typeof _smgmtLingerLive === "function" ? _smgmtLingerLive(label) : null) || _smgmtLiveCache[label] || null : null;
    const runningComplete = live ? (live.done_count || 0) + (live.failed_count || 0) + (live.skipped_count || 0) : 0;
    const runningTotal = live ? live.total_count || tickets.length : tickets.length;
    const runningRatio = runningTotal > 0 ? `${runningComplete}/${runningTotal}` : "\u2014";
    const runningElapsed = live && live.time_spent_sec > 0 ? `<span class="smgmt-sprint-meta" id="smgmt-elapsed-${escHtml(label)}">elapsed ${_fmtRunningTime(live.time_spent_sec)}</span>` : `<span class="smgmt-sprint-meta" id="smgmt-elapsed-${escHtml(label)}"></span>`;
    const runningBadgeHtml = isRunningView ? `<span class="smgmt-running-badge" id="smgmt-running-badge-${escHtml(label)}"><span class="smgmt-running-badge-dot"></span>${isLinger ? "done" : runningRatio}</span>` : "";
    const runningStripeHtml = isRunningView ? '<div class="smgmt-running-stripe"></div>' : "";
    const runningClass = isRunning ? " smgmt-running" : isLinger ? " smgmt-linger" : "";
    const collapsedClass = isCollapsed ? " smgmt-collapsed" : "";
    const collapseLabel = (isCollapsed ? "Expand " : "Collapse ") + escHtml(sprintLabelDisplay(label));
    return `
    <div class="smgmt-sprint-card sc-v5${outcomeCardClass}${runningClass}${collapsedClass}" id="smgmt-card-${escHtml(label)}"
         ondragover="${isRunning ? "" : `_smgmtDragOver(event, '${escHtml(label)}')`}"
         ondragleave="${isRunning ? "" : `_smgmtDragLeave(event)`}"
         ondrop="${isRunning ? "" : `_smgmtDropOnSprint(event, '${escHtml(label)}')`}">
      ${runningStripeHtml}
      <div class="sc-header smgmt-sprint-header">
        <div class="smgmt-sprint-header-left sc-header-left">
          <button class="smgmt-collapse-btn" id="smgmt-collapse-btn-${escHtml(label)}"
                  onclick="smgmtToggleCollapse('${escHtml(label)}')"
                  aria-label="${collapseLabel}"
                  title="${collapseLabel}"
                  onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();smgmtToggleCollapse('${escHtml(label)}');}">
            <i class="ti ti-chevron-down"></i></button>
          <span class="smgmt-sprint-name sc-name">${escHtml(sprintLabelDisplay(label))}</span>
          ${runningBadgeHtml}
          ${isNext && !isRunning ? '<span class="smgmt-next-badge">NEXT UP</span>' : ""}
          ${plannedBadge}
          ${outcomeBadgeHtml}
          ${headerMetaHtml}
          ${parentLineage}
          <span class="sc-meta smgmt-sprint-count" id="smgmt-col-rollup-${escHtml(label)}">${_smgmtRollupText(rollupItems)}</span>
        </div>
        <div class="smgmt-sprint-header-right sc-header-right">
          <button class="smgmt-delete-btn"
                  onclick="smgmtDeleteSprint('${escHtml(label)}')">
            <i class="ti ti-trash"></i> Delete</button>
          ${actionBtn}
          ${blockedHint}
          ${isRunning ? runningElapsed : ""}
          <button class="smgmt-finish-btn ${finishHidden}" ${finishDisabled}
                  title="${finishDisabled ? "No open tickets" : "Finish sprint"}"
                  onclick="smgmtFinishSprint('${escHtml(label)}')">
            <i class="ti ti-flag-check"></i> Merge Sprint</button>
        </div>
      </div>
      ${cancelBannerHtml}
      ${outcomeBandHtml}
      ${summaryHtml}
      <div class="smgmt-sprint-tickets sc-tickets" id="smgmt-tickets-${escHtml(label)}">
        ${ticketsContainerHtml}
      </div>
      ${logHtml}
    </div>`;
  }
  function _smgmtRunningTicketRowsHtml(label, tickets) {
    const live = _smgmtLiveCache[label] || null;
    const currentTicket = live ? live.current_ticket : null;
    const liveIssues = live && live.issues && live.issues.length > 0 ? live.issues : [];
    const liveByNum = {};
    liveIssues.forEach((i) => {
      liveByNum[i.number] = i;
    });
    const sourceTickets = (liveIssues.length > 0 ? liveIssues : tickets).slice().sort((a, b) => (a.dispatch_level || 0) - (b.dispatch_level || 0));
    const cardRepo = _smgmtRepo();
    if (sourceTickets.length === 0) {
      return '<div class="smgmt-drop-hint">No tickets in this sprint</div>';
    }
    let prevLevel = 0;
    return sourceTickets.map((t) => {
      const liveIss = liveByNum[t.number];
      const liveStatus = liveIss ? liveIss.status : null;
      const agentStatus = liveIss ? liveIss.agent_status : null;
      const ticketLevel = liveIss && liveIss.dispatch_level || t.dispatch_level || 0;
      let sepHtml = "";
      if (ticketLevel > 0 && ticketLevel > prevLevel) {
        sepHtml = `<div class="level-sep">
        <span class="level-sep-num">Level ${ticketLevel}</span>
        <span class="level-sep-desc">\xB7 runs after level ${prevLevel} completes</span>
      </div>`;
      }
      if (ticketLevel > 0)
        prevLevel = ticketLevel;
      const isActiveAgent = agentStatus && (agentStatus.endsWith("_running") || agentStatus.endsWith("_dispatched"));
      let indicator = "";
      if (liveStatus === "done") {
        indicator = '<div class="smgmt-ticket-indicator"><div class="circle-done">&#10003;</div></div>';
      } else if (agentStatus === "failed" || liveStatus === "skipped") {
        indicator = '<div class="smgmt-ticket-indicator"><div class="circle-failed">&#10005;</div></div>';
      } else if (liveStatus === "in-progress" || isActiveAgent || currentTicket && t.number === currentTicket.number) {
        indicator = '<div class="smgmt-ticket-indicator"><div class="ring"></div></div>';
      } else {
        indicator = '<div class="smgmt-ticket-indicator"><div class="circle-pending"></div></div>';
      }
      const issueUrl = t.url || (cardRepo ? `https://github.com/${cardRepo}/issues/${t.number}` : "#");
      const sizeVal = liveIss && liveIss.size || t.size || "";
      const sizePillHtml = sizeVal ? `<span class="smgmt-ticket-size-pill" title="\u2248${liveIss && liveIss.minutes || _sizeMinutes(sizeVal)} min">${escHtml(sizeVal)}</span>` : "";
      const runSizeAttr = sizeVal ? ` data-size="${escHtml(sizeVal)}"` : "";
      const agentTagHtml = liveIss && liveIss.agent ? `<span class="smgmt-ticket-agent-tag ${_smgmtAgentTagClass(liveIss.agent)}">${escHtml(liveIss.agent.toUpperCase())}</span>` : "";
      const elapsedStr = liveIss ? _fmtTicketElapsed(liveIss.elapsed_secs) : null;
      const elapsedHtml = elapsedStr ? `<span class="smgmt-ticket-elapsed">${elapsedStr}</span>` : "";
      const runTicketLabels = escHtml((t.labels || []).map((l) => l.name).join(","));
      return sepHtml + `<div class="smgmt-ticket" data-issue="${t.number}" data-labels="${runTicketLabels}" draggable="false"${runSizeAttr}>
      ${indicator}
      <a class="smgmt-ticket-num" href="${escHtml(issueUrl)}" target="_blank"
         rel="noopener">#${t.number}</a>
      <span class="smgmt-ticket-title" title="${escHtml(t.title)}">${escHtml(t.title)}</span>
      ${sizePillHtml}${agentTagHtml}${elapsedHtml}
    </div>`;
    }).join("");
  }
  function _smgmtRunningLevelText(live) {
    const levels = live && live.levels || [];
    if (levels.length > 1) {
      const active = levels.find((l) => l.state === "active");
      const cur = active ? active.level : levels[levels.length - 1].level;
      return `level ${cur} of ${levels.length}`;
    }
    const issues = live && live.issues || [];
    const levelNums = [...new Set(
      issues.map((i) => i.dispatch_level || 0 || 1)
    )].filter((l) => l > 0).sort((a, b) => a - b);
    if (levelNums.length <= 1)
      return null;
    let current = levelNums[0];
    for (const lvl of levelNums) {
      const group = issues.filter((i) => (i.dispatch_level || 0 || 1) === lvl);
      const allDone = group.length > 0 && group.every(
        (i) => i.status === "done" || i.status === "skipped" || i.agent_status === "failed"
      );
      if (!allDone) {
        current = lvl;
        break;
      }
      current = lvl;
    }
    return `level ${current} of ${levelNums.length}`;
  }
  function _smgmtRunningBoardBannerHtml(label, tickets) {
    const isLinger = typeof _smgmtIsLinger === "function" && _smgmtIsLinger(label);
    const live = (typeof _smgmtLingerLive === "function" ? _smgmtLingerLive(label) : null) || _smgmtLiveCache[label] || null;
    const doneCount = live ? live.done_count || 0 : 0;
    const failedCount = live ? live.failed_count || 0 : 0;
    const skippedCount = live ? live.skipped_count || 0 : 0;
    const totalCount = live ? live.total_count || tickets.length : tickets.length;
    const completeCount = doneCount + failedCount + skippedCount;
    const timeSpentSec = live ? live.time_spent_sec || 0 : 0;
    const levelText = _smgmtRunningLevelText(live);
    const parts = [
      isLinger ? `${escHtml(sprintLabelDisplay(label))} finished (snapshot)` : `${escHtml(sprintLabelDisplay(label))} is running`,
      `${completeCount}/${totalCount} done`,
      timeSpentSec > 0 ? _fmtRunningTime(timeSpentSec) : null,
      levelText
    ].filter(Boolean);
    const safeLabel = escHtml(label);
    const lingerCls = isLinger ? " linger" : "";
    return `<div class="smgmt-board-running-banner${lingerCls}" id="smgmt-board-banner-${safeLabel}" data-label="${safeLabel}">
    <span class="smgmt-board-running-banner-dot" aria-hidden="true"></span>
    <span class="smgmt-board-running-banner-text" id="smgmt-board-banner-text-${safeLabel}">${parts.join(" \xB7 ")}</span>
    <button type="button" class="smgmt-board-running-banner-link"
            onclick="_smgmtShowSubView('running')">Watch in Running \u2192</button>
  </div>`;
  }
  function _smgmtBoardBannerPatch(label, live) {
    const textEl = document.getElementById(`smgmt-board-banner-text-${label}`);
    if (!textEl)
      return;
    const doneCount = live.done_count || 0;
    const failedCount = live.failed_count || 0;
    const skippedCount = live.skipped_count || 0;
    const totalCount = live.total_count || 0;
    const completeCount = doneCount + failedCount + skippedCount;
    const timeSpentSec = live.time_spent_sec || 0;
    const levelText = _smgmtRunningLevelText(live);
    const parts = [
      `${sprintLabelDisplay(label)} is running`,
      `${completeCount}/${totalCount} done`,
      timeSpentSec > 0 ? _fmtRunningTime(timeSpentSec) : null,
      levelText
    ].filter(Boolean);
    textEl.textContent = parts.join(" \xB7 ");
  }
  function _smgmtRunningCardHtml(label, n, tickets) {
    let isCollapsed = false;
    try {
      isCollapsed = localStorage.getItem("sprintColumn_" + label + "_collapsed") === "1";
    } catch (_) {
    }
    const live = _smgmtLiveCache[label] || null;
    const doneCount = live ? live.done_count || 0 : 0;
    const failedCount = live ? live.failed_count || 0 : 0;
    const skippedCount = live ? live.skipped_count || 0 : 0;
    const totalCount = live ? live.total_count || tickets.length : tickets.length;
    const completeCount = doneCount + failedCount + skippedCount;
    const estRemMins = live ? live.est_remaining_minutes : null;
    const timeSpentSec = live ? live.time_spent_sec || 0 : 0;
    const currentTicket = live ? live.current_ticket : null;
    const activeAgent = live ? live.active_agent : null;
    const recentLogLines = live ? live.recent_log_lines || [] : [];
    const pct = totalCount > 0 ? Math.round(completeCount / totalCount * 100) : 0;
    const liveIssues = live && live.issues && live.issues.length > 0 ? live.issues : [];
    const liveByNum = {};
    liveIssues.forEach((i) => {
      liveByNum[i.number] = i;
    });
    const sourceTickets = (liveIssues.length > 0 ? liveIssues : tickets).slice().sort((a, b) => (a.dispatch_level || 0) - (b.dispatch_level || 0));
    const segBarHtml = sourceTickets.length > 0 ? `<div class="smgmt-seg-bar" id="smgmt-seg-${escHtml(label)}">${sourceTickets.map((t) => {
      const liveIss = liveByNum[t.number];
      const liveStatus = liveIss ? liveIss.status : null;
      const agentStatus = liveIss ? liveIss.agent_status : null;
      let blockClass = "seg-pending";
      if (liveStatus === "done")
        blockClass = "seg-done";
      else if (agentStatus === "failed" || liveStatus === "skipped")
        blockClass = "seg-failed";
      else if (liveStatus === "in-progress" || agentStatus === "running" || currentTicket && t.number === currentTicket.number)
        blockClass = "seg-running";
      return `<div class="seg-block ${blockClass}" data-issue="${t.number}"></div>`;
    }).join("")}</div>` : "";
    const ticketRowsHtml = _smgmtRunningTicketRowsHtml(label, tickets);
    const runCollapsedClass = isCollapsed ? " smgmt-collapsed" : "";
    const runCollapseLabel = (isCollapsed ? "Expand " : "Collapse ") + escHtml(sprintLabelDisplay(label));
    return `
    <div class="smgmt-sprint-card smgmt-running${runCollapsedClass}" id="smgmt-card-${escHtml(label)}">
      <div class="smgmt-running-stripe"></div>
      <div class="smgmt-sprint-header">
        <div class="smgmt-sprint-header-left">
          <button class="smgmt-collapse-btn" id="smgmt-collapse-btn-${escHtml(label)}"
                  onclick="smgmtToggleCollapse('${escHtml(label)}')"
                  aria-label="${runCollapseLabel}"
                  title="${runCollapseLabel}"
                  onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();smgmtToggleCollapse('${escHtml(label)}');}">
            <i class="ti ti-chevron-down"></i></button>
          <i class="ti ti-layout-kanban" style="font-size:14px;color:var(--green)"></i>
          <span class="smgmt-sprint-name" style="font-size:15px;font-weight:700;">${escHtml(sprintLabelDisplay(label))}</span>
          <span class="smgmt-running-badge" id="smgmt-running-badge-${escHtml(label)}">
            <span class="smgmt-running-badge-dot"></span>${totalCount > 0 ? `${completeCount}/${totalCount}` : "\u2014"}
          </span>
        </div>
        <div class="smgmt-sprint-header-right">
          <span class="smgmt-sprint-meta" id="smgmt-elapsed-${escHtml(label)}">${timeSpentSec > 0 ? `elapsed ${_fmtRunningTime(timeSpentSec)}` : ""}</span>
          <button class="smgmt-cancel-btn" onclick="smgmtCancelSprint('${escHtml(label)}')">
            <i class="ti ti-player-stop"></i> Cancel sprint</button>
        </div>
      </div>
      <div class="smgmt-outcome-band" id="smgmt-running-stats-${escHtml(label)}">
        <span class="oc-done" id="smgmt-rs-done-${escHtml(label)}">${doneCount} DONE</span>
        <span class="oc-fail ${failedCount > 0 ? "" : "muted"}" id="smgmt-rs-failed-${escHtml(label)}">${failedCount} FAILED</span>
        <span class="oc-skip" id="smgmt-rs-skipped-${escHtml(label)}">${skippedCount} SKIPPED</span>
        <span class="oc-est" id="smgmt-rs-est-${escHtml(label)}">${estRemMins != null ? `\u21A9 ${estRemMins}m EST. REMAINING` : "\u21A9 EST. REMAINING"}</span>
        <span class="oc-spacer"></span>
        ${segBarHtml}
        <span class="smgmt-outcome-dur" id="smgmt-rs-time-${escHtml(label)}">${_fmtRunningTime(timeSpentSec)}</span>
      </div>
      <div id="smgmt-active-agents-wrap-${escHtml(label)}">${_smgmtActiveAgentsHtml(live, label)}</div>
      <div id="smgmt-levels-wrap-${escHtml(label)}">${_smgmtLevelsHtml(live, label)}</div>
      <div class="smgmt-sprint-tickets" id="smgmt-tickets-${escHtml(label)}">
        ${ticketRowsHtml || '<div class="smgmt-drop-hint">No tickets in this sprint</div>'}
      </div>
      <div class="smgmt-live-log" id="smgmt-live-log-${escHtml(label)}">
        <div class="smgmt-live-log-bar">
          <span class="smgmt-live-indicator"></span>
          <span>live</span>
          <span class="smgmt-live-log-agent" id="smgmt-live-agent-${escHtml(label)}">${_smgmtLiveAgentBadgesHtml(live)}</span>
        </div>
        <div class="smgmt-live-log-stream" id="smgmt-live-log-stream-${escHtml(label)}">${_smgmtLiveLogLinesHtml(recentLogLines)}</div>
      </div>
    </div>`;
  }
  function _smgmtRollupText(items) {
    const count = items.length;
    if (count === 0)
      return "0 tickets";
    let totalMins = 0, unestimated = 0;
    for (const t of items) {
      const size = _smgmtTicketSize(t);
      const mins = size ? _sizeMinutes(size) : 0;
      if (mins > 0)
        totalMins += mins;
      else
        unestimated++;
    }
    const countStr = `${count} ticket${count !== 1 ? "s" : ""}`;
    if (unestimated === count)
      return countStr;
    const h = totalMins / 60;
    const timeStr = h < 1 ? `~${totalMins}m` : `~${parseFloat((Math.round(h * 10) / 10).toFixed(1))}h`;
    return `${countStr} \xB7 ${timeStr}`;
  }
  function _smgmtTicketSize(t) {
    if (!t)
      return null;
    const cached = Object.prototype.hasOwnProperty.call(_estDataCache, t.number) ? _estDataCache[t.number] : void 0;
    let size = cached && cached.size ? cached.size : t.size || null;
    if (!size && t.labels) {
      for (const lbl of t.labels) {
        const m = /^size-([SMLX]+)$/.exec(lbl.name || "");
        if (m) {
          size = m[1];
          break;
        }
      }
    }
    return size || null;
  }
  function _smgmtTicketHasEstimate(t) {
    return _smgmtTicketSize(t) !== null;
  }
  function _smgmtRunningBlockerShort() {
    if (!_smgmtRunningLabels || _smgmtRunningLabels.size === 0)
      return "";
    const lbl = [..._smgmtRunningLabels][0];
    const m = String(lbl).match(/sprint-(\d+(?:\.\d+)?)/);
    return m ? `S${m[1]}` : sprintLabelDisplay(lbl);
  }
  function _smgmtTicketEstHtml(ticket) {
    const activity = typeof globalThis !== "undefined" && globalThis._smgmtRowActivity ? globalThis._smgmtRowActivity[ticket.number] : null;
    if (activity) {
      const label = activity === "fixing-ac" ? "fixing AC\u2026" : "estimating\u2026";
      return `<span class="smgmt-ticket-est smgmt-ticket-est--pending" id="smgmt-ticket-est-${ticket.number}" aria-label="${label}"><span class="smgmt-estimating-dot" aria-hidden="true"></span></span>`;
    }
    const size = _smgmtTicketSize(ticket);
    if (!size) {
      return `<span class="smgmt-ticket-est" id="smgmt-ticket-est-${ticket.number}"></span>`;
    }
    const mins = _sizeMinutes(size);
    return `<span class="smgmt-ticket-est" id="smgmt-ticket-est-${ticket.number}">${mins}m</span>`;
  }
  function _smgmtUpdateColRollup(label, items) {
    const el = document.getElementById(`smgmt-col-rollup-${label}`);
    if (el)
      el.textContent = _smgmtRollupText(items);
  }
  function _smgmtTicketRowHtml(ticket, label, elapsedSecs = null) {
    const hasRework = (ticket.labels || []).some((l) => l.name === "need-rework" || l.name === "needs-rework");
    const statusClass = hasRework ? "smgmt-status-need-rework" : {
      "backlog": "smgmt-status-backlog",
      "in-progress": "smgmt-status-in-progress",
      "sit": "smgmt-status-sit",
      "uat": "smgmt-status-uat",
      "done": "smgmt-status-done"
    }[ticket.status] || "smgmt-status-backlog";
    const statusLabel = hasRework ? "needs rework" : ticket.status || "backlog";
    const isSelected = _smgmtSelectedIssues.has(ticket.number);
    const _outcomeMap = {
      "done": ["ti-circle-check", "outcome-success"],
      "uat": ["ti-circle-check", "outcome-success"],
      "needs-rework": ["ti-circle-x", "outcome-rework"],
      "in-progress": ["ti-circle-dot", "outcome-active"],
      "sit": ["ti-circle-dot", "outcome-active"]
    };
    const _oc = hasRework ? ["ti-circle-x", "outcome-rework"] : _outcomeMap[ticket.status] || ["ti-circle", "outcome-backlog"];
    const outcomeIconHtml = `<i class="ti ${_oc[0]} smgmt-outcome-icon ${_oc[1]}" title="${escHtml(statusLabel)}"></i>`;
    const sizeValue = _smgmtTicketSize(ticket) || "";
    const hasEstimate = sizeValue !== "";
    const sizeAttr = sizeValue ? ` data-size="${escHtml(sizeValue)}"` : "";
    const estimateBadgeHtml = _smgmtEstimateBadgeHtml(ticket.number);
    const _cachedEst = Object.prototype.hasOwnProperty.call(_estDataCache, ticket.number) ? _estDataCache[ticket.number] : void 0;
    const sizePillHtml = sizeValue && !(_cachedEst && _cachedEst.size) ? `<span class="smgmt-ticket-size-pill" title="\u2248${_sizeMinutes(sizeValue)} min">${escHtml(sizeValue)}</span>` : "";
    const staleBadgeHtml = ticket.estimate_stale && hasEstimate ? `<button class="smgmt-stale-badge" data-stale="true" tabindex="0"
         title="Estimate may be outdated \u2014 issue body changed since last estimate"
         onclick="event.stopPropagation();_smgmtReEstimate(${ticket.number},this)"
         onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();_smgmtReEstimate(${ticket.number},this);}">stale</button>` : "";
    const reEstBtnHtml = _smgmtEstimatorAvailable && !ticket.estimate_stale ? `<button class="smgmt-reestimate-btn" tabindex="0" title="Re-estimate this ticket"
         onclick="event.stopPropagation();_smgmtReEstimate(${ticket.number},this)"
         onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();_smgmtReEstimate(${ticket.number},this);}">Re-estimate</button>` : "";
    const riskFlagIconsHtml = _smgmtRiskFlagIconsHtml(ticket.number);
    const schedDepHtml = _smgmtSchedDepHtml(ticket);
    const ticketLabelNames = (ticket.labels || []).map((l) => l.name).join(",");
    const sk = escHtml(label);
    return `
    <div class="smgmt-ticket${isSelected ? " is-selected" : ""}" id="smgmt-ticket-${ticket.number}"
         tabindex="-1"
         draggable="true"
         data-issue="${ticket.number}"
         data-sprint="${sk}"${sizeAttr}
         data-labels="${escHtml(ticketLabelNames)}"
         ondragstart="_smgmtTicketDragStart(event, ${ticket.number}, '${sk}')"
         ondragend="_smgmtTicketDragEnd(event)"
         ondragover="_smgmtTicketReorderDragOver(event)"
         ondragleave="_smgmtTicketReorderDragLeave(event)"
         ondrop="_smgmtTicketReorderDrop(event, ${ticket.number}, '${sk}')"
         onclick="_smgmtRowClick(event, ${ticket.number}, '${sk}')"
         oncontextmenu="_smgmtCtxMenuOpen(event,${ticket.number})">
      <input type="checkbox" class="smgmt-ticket-cb"
             ${isSelected ? "checked" : ""}
             onclick="event.stopPropagation()"
             onchange="_smgmtToggleSelect(${ticket.number}, this.checked)">
      <i class="ti ti-grip-vertical smgmt-ticket-grip"></i>
      ${outcomeIconHtml}
      <a class="smgmt-ticket-num" href="${escHtml(ticket.url || "#")}" target="_blank"
         rel="noopener" draggable="false" onclick="event.stopPropagation()">#${ticket.number}</a>
      <span class="smgmt-ticket-title" title="${escHtml(ticket.title)}">${escHtml(ticket.title)}</span>
      ${sizePillHtml}${staleBadgeHtml}${estimateBadgeHtml}${riskFlagIconsHtml}${schedDepHtml}${reEstBtnHtml}
      ${hasRework ? '<span class="smgmt-lbl-rejected">TESTER REJECTED</span>' : ""}
      ${elapsedSecs != null ? `<span class="smgmt-ticket-elapsed">${_fmtRunningTime(elapsedSecs)}</span>` : ""}
      ${_smgmtTicketEstHtml(ticket)}
      <span class="smgmt-ticket-status ${statusClass}">${escHtml(statusLabel)}</span>
      <button class="btn-view-log" tabindex="0" title="View issue log"
              onclick="event.stopPropagation();openLvIssueLog(${ticket.number},'${sk}',_smgmtRepo()||'')">
        <i class="ti ti-file-text"></i></button>
      <button class="smgmt-row-menu-btn" tabindex="0" title="Ticket actions" aria-haspopup="true" aria-expanded="false"
              onclick="event.stopPropagation();_smgmtRowMenuOpen(event, ${ticket.number}, '${sk}', ${hasEstimate})"
              onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();_smgmtRowMenuOpen(event,${ticket.number},'${sk}',${hasEstimate});}">
        <i class="ti ti-menu-2"></i></button>
      <button class="t-details-btn" onclick="event.stopPropagation();toggleTicketRow('${sk}',${ticket.number})">
        <span class="t-dbtn-label">Details</span> <span id="caret-${sk}-${ticket.number}">\u25BC</span>
      </button>
    </div>
    <div class="ticket-expand" id="ex-${sk}-${ticket.number}" style="display:none">
      <div class="ex-row">
        <span class="ex-label">Conflicts</span>
        <span class="ex-conflicts-val">\u2014</span>
      </div>
      <div class="ex-row">
        <span class="ex-label">Execution</span>
        <span class="ex-exec-val">\u2014</span>
      </div>
      <div class="ex-actions">
        <button class="ex-btn" onclick="event.stopPropagation();_smgmtReEstimate(${ticket.number},this)"><i class="ti ti-sparkles" style="font-size:12px"></i> Re-estimate</button>
        <button class="ex-btn" onclick="event.stopPropagation();_smgmtRowMenuOpen(event,${ticket.number},'${sk}',${hasEstimate})"><i class="ti ti-arrow-right" style="font-size:12px"></i> Move to sprint</button>
        <button class="ex-btn ex-btn-danger" onclick="event.stopPropagation();_smgmtCloseIssueOpen(${ticket.number})"><i class="ti ti-x" style="font-size:12px"></i> Close ticket</button>
      </div>
    </div>`;
  }
  function _smgmtRenderBacklog(tickets) {
    _blBacklogAll = tickets || [];
    const countEl = document.getElementById("smgmt-backlog-count");
    const ticketsEl = document.getElementById("smgmt-backlog-tickets");
    if (!ticketsEl)
      return;
    const filtered = _blApplyFilters(_blBacklogAll);
    if (countEl) {
      const total = _blBacklogAll.length, shown = filtered.length;
      countEl.textContent = total > 0 ? `${shown === total ? total : `${shown} of ${total}`} ticket${total !== 1 ? "s" : ""}` : "0 tickets";
    }
    const backlogBulkBtn = document.getElementById("smgmt-backlog-bulk-est-btn");
    if (backlogBulkBtn) {
      const hasUnsized = _blBacklogAll.some((t) => !_smgmtTicketHasEstimate(t));
      backlogBulkBtn.classList.toggle("hidden", !hasUnsized);
    }
    const sorted = [...filtered].sort((a, b) => b.number - a.number);
    const allSprintNums = (_smgmtData?.sprints || []).sort((a, b) => a - b);
    if (sorted.length === 0) {
      const msg = _blBacklogAll.length === 0 ? "No backlog tickets \u2014 all caught up" : "No tickets match the active filters";
      ticketsEl.innerHTML = `<div class="smgmt-drop-hint" style="padding:14px 18px;text-align:center;">${msg}</div>`;
    } else {
      ticketsEl.innerHTML = sorted.map((t) => _smgmtBacklogTicketHtml(t, allSprintNums)).join("");
    }
    _blSyncFilterPills();
    _blUpdateActions();
  }
  function _smgmtBacklogTicketHtml(ticket, sprintNums) {
    const isSelected = _smgmtSelectedIssues.has(ticket.number);
    const hasEstimate = _smgmtTicketHasEstimate(ticket);
    const backlogLabelNames = (ticket.labels || []).map((l) => l.name).join(",");
    const schedDepHtml = _smgmtSchedDepHtml(ticket);
    const sizeValue = _smgmtTicketSize(ticket) || "";
    const sizeAttr = sizeValue ? ` data-size="${escHtml(sizeValue)}"` : "";
    const sizePillHtml = sizeValue ? `<span class="smgmt-ticket-size-pill">${escHtml(sizeValue)}</span>` : "";
    const ageDays = ticket.created_at ? Math.floor((Date.now() - Date.parse(ticket.created_at)) / 864e5) : null;
    const ageHtml = ageDays != null && !isNaN(ageDays) ? `<span class="bl-row-age">${ageDays}d</span>` : "";
    return `
    <div class="smgmt-ticket bl-row${isSelected ? " is-selected" : ""}" id="smgmt-ticket-${ticket.number}"
         draggable="true"
         data-issue="${ticket.number}"
         data-sprint=""${sizeAttr}
         data-labels="${escHtml(backlogLabelNames)}"
         ondragstart="_smgmtBacklogTicketDragStart(event, ${ticket.number})"
         ondragend="_smgmtTicketDragEnd(event)"
         onclick="_smgmtRowClick(event, ${ticket.number}, null)"
         oncontextmenu="_smgmtCtxMenuOpen(event,${ticket.number})">
      <input type="checkbox" class="smgmt-ticket-cb" draggable="false"
             ${isSelected ? "checked" : ""}
             onclick="event.stopPropagation()"
             onchange="_smgmtToggleSelect(${ticket.number}, this.checked)">
      <a class="smgmt-ticket-num" href="${escHtml(ticket.url || "#")}" target="_blank"
         rel="noopener" draggable="false" onclick="event.stopPropagation()">#${ticket.number}</a>
      <span class="smgmt-ticket-title" title="${escHtml(ticket.title)}">${escHtml(ticket.title)}</span>
      ${schedDepHtml}${sizePillHtml}${ageHtml}
      <button class="smgmt-row-menu-btn" tabindex="0" title="Ticket actions" aria-haspopup="true" aria-expanded="false"
              onclick="event.stopPropagation();_smgmtRowMenuOpen(event, ${ticket.number}, null, ${hasEstimate})"
              onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();_smgmtRowMenuOpen(event,${ticket.number},null,${hasEstimate});}">
        <i class="ti ti-menu-2"></i></button>
    </div>`;
  }

  // apps/dashboard/static/src/sprint-board/index.js
  globalThis._rrOpen = _rrOpen;
  globalThis._rrClose = _rrClose;
  globalThis._rrCatClass = _rrCatClass;
  globalThis._rrUpdateState = _rrUpdateState;
  globalThis._rrSelectAll = _rrSelectAll;
  globalThis.smgmtRerunSprint = smgmtRerunSprint;
  globalThis._rrConfirm = _rrConfirm;
  globalThis._fsOpen = _fsOpen;
  globalThis._fsClose = _fsClose;
  globalThis._fsCatClass = _fsCatClass;
  globalThis._fsSelectAll = _fsSelectAll;
  globalThis.smgmtFinishSprint = smgmtFinishSprint;
  globalThis._fsConfirm = _fsConfirm;
  globalThis._bcOpen = _bcOpen;
  globalThis._bcClose = _bcClose;
  globalThis._bcCatClass = _bcCatClass;
  globalThis._bcSelectAll = _bcSelectAll;
  globalThis.smgmtBulkCompleteSprint = smgmtBulkCompleteSprint;
  globalThis._bcConfirm = _bcConfirm;
  globalThis.smgmtRunBlockedToast = smgmtRunBlockedToast;
  globalThis.smgmtRunSprint = smgmtRunSprint2;
  globalThis.smgmtCancelSprint = smgmtCancelSprint;
  globalThis._pfOpen = _pfOpen;
  globalThis._pfReset = _pfReset;
  globalThis._pfClose = _pfClose;
  globalThis._pfFetch = _pfFetch;
  globalThis._pfShowSuccess = _pfShowSuccess;
  globalThis._pfUpdateConfirmBtn = _pfUpdateConfirmBtn;
  globalThis._pfBuildWarningsHtml = _pfBuildWarningsHtml;
  globalThis._pfBuildCycleHtml = _pfBuildCycleHtml;
  globalThis._pfBuildFlagsHtml = _pfBuildFlagsHtml;
  globalThis._pfFlagShowSizePicker = _pfFlagShowSizePicker;
  globalThis._pfFlagHidePicker = _pfFlagHidePicker;
  globalThis._pfFlagAction = _pfFlagAction;
  globalThis._pfFlagReestimate = _pfFlagReestimate;
  globalThis._pfBuildDAGHtml = _pfBuildDAGHtml;
  globalThis._pfDrawDAGArrows = _pfDrawDAGArrows;
  globalThis._pfToggleTicket = _pfToggleTicket;
  globalThis._pfGetSelectedTickets = _pfGetSelectedTickets;
  globalThis._pfComputeConflicts = _pfComputeConflicts;
  globalThis._pfBuildConflictsHtml = _pfBuildConflictsHtml;
  globalThis._pfBuildOrderHtml = _pfBuildOrderHtml;
  globalThis._pfUpdateSections = _pfUpdateSections;
  globalThis._pfShowError = _pfShowError;
  globalThis._pfRetry = _pfRetry;
  globalThis._pfConfirm = _pfConfirm;
  globalThis.computeDropPlan = computeDropPlan;
  globalThis._smgmtUpdateSelectionUI = _smgmtUpdateSelectionUI2;
  globalThis._smgmtPopulateSelectionDropdown = _smgmtPopulateSelectionDropdown;
  globalThis._smgmtPopulateMoveToMenu = _smgmtPopulateMoveToMenu;
  globalThis._smgmtToggleMoveToMenu = _smgmtToggleMoveToMenu;
  globalThis._smgmtCloseMoveToMenu = _smgmtCloseMoveToMenu;
  globalThis._smgmtClearSelection = _smgmtClearSelection;
  globalThis._smgmtSetSelected = _smgmtSetSelected;
  globalThis._smgmtToggleSelect = _smgmtToggleSelect;
  globalThis._smgmtRowClick = _smgmtRowClick;
  globalThis._smgmtIsDeletableIssue = _smgmtIsDeletableIssue;
  globalThis._smgmtDeleteSelected = _smgmtDeleteSelected;
  globalThis._smgmtMoveSelectedTo = _smgmtMoveSelectedTo;
  globalThis._smgmtTicketDragStart = _smgmtTicketDragStart;
  globalThis._smgmtDragMovePill = _smgmtDragMovePill;
  globalThis._smgmtGhostComputeNextFree = _smgmtGhostComputeNextFree;
  globalThis._smgmtGhostShow = _smgmtGhostShow;
  globalThis._smgmtGhostHide = _smgmtGhostHide;
  globalThis._smgmtGhostDragOver = _smgmtGhostDragOver;
  globalThis._smgmtGhostDragLeave = _smgmtGhostDragLeave;
  globalThis._smgmtGhostDrop = _smgmtGhostDrop;
  globalThis._gcClose = _gcClose;
  globalThis._gcConfirm = _gcConfirm;
  globalThis._smgmtTicketDragEnd = _smgmtTicketDragEnd;
  globalThis._smgmtDragOver = _smgmtDragOver;
  globalThis._smgmtDragLeave = _smgmtDragLeave;
  globalThis._smgmtDropOnSprint = _smgmtDropOnSprint;
  globalThis._smgmtTicketReorderDragOver = _smgmtTicketReorderDragOver;
  globalThis._smgmtTicketReorderDragLeave = _smgmtTicketReorderDragLeave;
  globalThis._smgmtTicketReorderDrop = _smgmtTicketReorderDrop;
  globalThis._smgmtBacklogTicketDragStart = _smgmtBacklogTicketDragStart;
  globalThis._smgmtBacklogDragOver = _smgmtBacklogDragOver;
  globalThis._smgmtBacklogDragLeave = _smgmtBacklogDragLeave;
  globalThis._smgmtDropOnBacklog = _smgmtDropOnBacklog;
  globalThis._smgmtBoardLock = _smgmtBoardLock;
  globalThis._smgmtBoardUnlock = _smgmtBoardUnlock;
  globalThis._smgmtBoardProgress = _smgmtBoardProgress;
  globalThis._smgmtBoardLog = _smgmtBoardLog;
  globalThis.loadSprintMgmt = loadSprintMgmt2;
  globalThis._smgmtSprintLabelSortKey = _smgmtSprintLabelSortKey;
  globalThis._smgmtRender = _smgmtRender2;
  globalThis._smgmtLabelFilterRender = _smgmtLabelFilterRender;
  globalThis._smgmtLabelFilterApply = _smgmtLabelFilterApply;
  globalThis._smgmtFetchMissingOutcomes = _smgmtFetchMissingOutcomes;
  globalThis._smgmtLoadEstimates = _smgmtLoadEstimates;
  globalThis._smgmtLoadConflicts = _smgmtLoadConflicts;
  globalThis._smgmtLoadDepOrder = _smgmtLoadDepOrder;
  globalThis._smgmtLoadGoals = _smgmtLoadGoals;
  globalThis._smgmtOutcomeBandHtml = _smgmtOutcomeBandHtml;
  globalThis._smgmtOutcomeTicketListHtml = _smgmtOutcomeTicketListHtml;
  globalThis._smgmtLoadFinishCards = _smgmtLoadFinishCards;
  globalThis._smgmtRenderFinishCard = _smgmtRenderFinishCard;
  globalThis._smgmtFinishCardInnerHtml = _smgmtFinishCardInnerHtml;
  globalThis._smgmtCardHtml = _smgmtCardHtml;
  globalThis._smgmtRunningCardHtml = _smgmtRunningCardHtml;
  globalThis._smgmtRunningBoardBannerHtml = _smgmtRunningBoardBannerHtml;
  globalThis._smgmtBoardBannerPatch = _smgmtBoardBannerPatch;
  globalThis._smgmtRunningLevelText = _smgmtRunningLevelText;
  globalThis._smgmtRollupText = _smgmtRollupText;
  globalThis._smgmtTicketSize = _smgmtTicketSize;
  globalThis._smgmtTicketHasEstimate = _smgmtTicketHasEstimate;
  globalThis._smgmtUpdateColRollup = _smgmtUpdateColRollup;
  globalThis._smgmtTicketRowHtml = _smgmtTicketRowHtml;
  globalThis._smgmtRenderBacklog = _smgmtRenderBacklog;
  globalThis._smgmtBacklogTicketHtml = _smgmtBacklogTicketHtml;
  globalThis._smgmtApplyRerunOptimistic = _smgmtApplyRerunOptimistic2;
  globalThis._smgmtSchedToggleHtml = _smgmtSchedToggleHtml2;
  globalThis.smgmtToggleRunOnSchedule = smgmtToggleRunOnSchedule;
  globalThis._smgmtHydrateSchedToggles = _smgmtHydrateSchedToggles2;

  // apps/dashboard/static/src/index.js
  var root = typeof window !== "undefined" ? window : globalThis;
  root.colorizeLogLine = colorizeLogLine;
  root.escapeLogHtml = escapeLogHtml;
  root.extractRaw = extractRaw;
  root.AGENT_NAMES = AGENT_NAMES;
})();
//# sourceMappingURL=bundle.js.map
