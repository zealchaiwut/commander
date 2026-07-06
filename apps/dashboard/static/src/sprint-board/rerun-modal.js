/* Re-run Sprint modal (issue #512) — extracted from project.html (issue #797).
 *
 * Opens the re-run modal for a finished sprint, previews which tickets will be
 * carried into a new versioned sub-sprint (e.g. sprint-5.1), and confirms the
 * re-run. Shared page helpers and the broadly-shared board caches are reached
 * through the page's global scope; modal-local state (`_rrLabel`,
 * `_rrVersionedLabel`) is seeded on `window` by ./state.js.
 */

/* global _setBodyInert, _clearBodyInert, _smgmtRepo, sprintLabelDisplay,
   escHtml, _smgmtShowToast, loadSprintMgmt,
   _smgmtApplyRerunOptimistic, smgmtRunSprint, renderProgressActivity,
   _rrLabel:writable, _rrVersionedLabel:writable */

function _rrShowPreviewLoading(current) {
  const loading = document.getElementById("rr-loading");
  if (!loading) return;
  loading.innerHTML = renderProgressActivity(
    {
      status: "running",
      mode: "indeterminate",
      current: current || "Loading preview…",
    },
    {
      id: "rr-preview-pa",
      hideLog: true,
    },
  );
  loading.classList.remove("hidden");
}

function _rrShowCreateProgress(done, total, current, status, error) {
  const loading = document.getElementById("rr-loading");
  if (!loading) return;
  loading.innerHTML = renderProgressActivity(
    {
      status: status || "running",
      mode: "bar",
      done: done || 0,
      total: total || 3,
      current: current || "",
      error: error || "",
      result: status === "done" ? "Sub-sprint created" : "",
    },
    {
      id: "rr-create-pa",
      hideLog: true,
    },
  );
  loading.classList.remove("hidden");
}

export function _rrOpen() {
  _setBodyInert(["rr-backdrop", "rr-modal"]);
  document.getElementById("rr-backdrop").classList.remove("hidden");
  document.getElementById("rr-modal").classList.remove("hidden");
}

export function _rrClose() {
  document.getElementById("rr-backdrop").classList.add("hidden");
  document.getElementById("rr-modal").classList.add("hidden");
  _clearBodyInert();
  _rrLabel = null;
  _rrVersionedLabel = null;
}

export function _rrCatClass(cat) {
  if (cat === "UAT") return "rr-cat-uat";
  if (cat === "SIT") return "rr-cat-sit";
  if (cat === "needs-rework") return "rr-cat-rework";
  return "rr-cat-queued";
}

export function _rrUpdateState() {
  const checkboxes = document.querySelectorAll(
    "#rr-ticket-list input[type=checkbox]",
  );
  const checked = Array.from(checkboxes).filter((c) => c.checked);
  const uatChecked = Array.from(checkboxes).filter(
    (c) => c.checked && c.dataset.cat === "UAT",
  ).length;

  const confirmBtn = document.getElementById("rr-confirm-btn");
  if (confirmBtn) confirmBtn.disabled = checked.length === 0;

  const warnEl = document.getElementById("rr-uat-warning");
  if (warnEl) {
    if (uatChecked > 0) {
      warnEl.textContent = `${uatChecked} ticket${uatChecked !== 1 ? "s" : ""} in UAT will be re-tested from scratch.`;
    } else {
      warnEl.textContent = "";
    }
  }
}

export function _rrSelectAll(checked) {
  document
    .querySelectorAll("#rr-ticket-list input[type=checkbox]")
    .forEach((cb) => {
      cb.checked = checked;
    });
  _rrUpdateState();
}

export async function smgmtRerunSprint(label) {
  const repo = _smgmtRepo();
  if (!repo) {
    _smgmtShowToast(
      "No project loaded — please refresh and try again.",
      "warning",
    );
    return;
  }

  _rrLabel = label;
  _rrVersionedLabel = null;

  document.getElementById("rr-modal-title").textContent =
    `Re-run ${sprintLabelDisplay(label)}?`;
  _rrShowPreviewLoading("Loading preview…");
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
      `/api/sprints/${encodeURIComponent(label)}/rerun-preview?project=${encodeURIComponent(repo)}`,
    );
    if (!res.ok) throw new Error(await res.text());
    const preview = await res.json();

    _rrVersionedLabel = preview.suggested_versioned_label;
    document.getElementById("rr-modal-title").textContent =
      `Re-run ${sprintLabelDisplay(label)} as ${sprintLabelDisplay(_rrVersionedLabel)}?`;
    if (confirmBtn)
      confirmBtn.textContent = `Create & run ${sprintLabelDisplay(_rrVersionedLabel)}`;

    const listEl = document.getElementById("rr-ticket-list");
    if ((preview.tickets || []).length === 0) {
      listEl.innerHTML =
        '<div style="padding:10px;color:var(--text-muted);font-size:13px">No tickets in this sprint.</div>';
    } else {
      listEl.innerHTML = (preview.tickets || [])
        .map((t) => {
          const checked = t.checked ? "checked" : "";
          const catClass = _rrCatClass(t.category);
          return `<label class="rr-ticket-row">
          <input type="checkbox" ${checked} data-issue="${t.number}" data-cat="${escHtml(t.category)}" onchange="_rrUpdateState()">
          <span class="rr-ticket-num">#${t.number}</span>
          <span class="rr-ticket-title" title="${escHtml(t.title)}">${escHtml(t.title)}</span>
          <span class="rr-ticket-cat ${catClass}">${escHtml(t.category)}</span>
        </label>`;
        })
        .join("");
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

export async function _rrConfirm() {
  const repo = _smgmtRepo();
  if (!_rrLabel || !repo) return;

  const parentLabel = _rrLabel;
  const checkboxes = Array.from(
    document.querySelectorAll("#rr-ticket-list input[type=checkbox]"),
  );
  const ticketNumbers = checkboxes
    .filter((c) => c.checked)
    .map((c) => parseInt(c.dataset.issue, 10));
  if (ticketNumbers.length === 0) return;

  const confirmBtn = document.getElementById("rr-confirm-btn");
  if (confirmBtn) {
    confirmBtn.disabled = true;
    confirmBtn.textContent = "Creating…";
  }
  _rrShowCreateProgress(0, 3, "Creating sprint…", "running", "");
  document.getElementById("rr-content").classList.add("hidden");

  try {
    const res = await fetch(
      `/api/sprints/${encodeURIComponent(parentLabel)}/rerun?project=${encodeURIComponent(repo)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticket_numbers: ticketNumbers,
          auto_run: false,
        }),
      },
    );
    if (!res.ok) {
      let detail = await res.text();
      try {
        const parsed = JSON.parse(detail);
        detail = parsed.detail || detail;
      } catch (_) {
        /* plain-text error body */
      }
      throw new Error(
        typeof detail === "string" ? detail : JSON.stringify(detail),
      );
    }
    const data = await res.json();
    const subLabel = data.sub_label;
    _rrShowCreateProgress(1, 3, "Applying local updates…", "running", "");
    if (typeof _smgmtApplyRerunOptimistic === "function") {
      _smgmtApplyRerunOptimistic(parentLabel, subLabel, ticketNumbers);
    }
    // The optimistic update above already shows the new sub-sprint — a full
    // loadSprintMgmt (the board's per-sprint API fan-out, ~5+3N calls) and
    // _histLoadLedger (triggers the History reconcile sweep) are NOT needed
    // before proceeding, and awaiting them on a project with many sprints made
    // this step hang for minutes. Refresh in the background instead; the
    // periodic poll / next tab visit picks up any drift.
    loadSprintMgmt(true).catch(() => {});
    if (typeof globalThis._histLoadLedger === "function") {
      globalThis._histLoadLedger(repo).catch(() => {});
    }
    _rrShowCreateProgress(2, 3, "Queueing sprint run…", "running", "");
    const subDisplay = subLabel ? sprintLabelDisplay(subLabel) : "Sub-sprint";
    if (data.errors && data.errors.length > 0) {
      _smgmtShowToast(
        `${subDisplay} created with label errors — check GitHub.`,
      );
    } else {
      _smgmtShowToast(`${subDisplay} ready — confirm run`);
    }
    if (subLabel && typeof smgmtRunSprint === "function") {
      _rrShowCreateProgress(3, 3, "Done", "done", "");
      smgmtRunSprint(subLabel);
    }
    _rrClose();
  } catch (e) {
    const errMsg = e.message || "Failed to create re-run sprint";
    _rrShowCreateProgress(0, 3, "", "error", errMsg);
    const errEl = document.getElementById("rr-error");
    errEl.textContent = "Failed to re-run sprint: " + errMsg;
    errEl.classList.remove("hidden");
    document.getElementById("rr-loading").classList.add("hidden");
    document.getElementById("rr-content").classList.remove("hidden");
    _smgmtShowToast("Re-run failed: " + errMsg, "error");
    if (confirmBtn) {
      confirmBtn.disabled = false;
      confirmBtn.textContent = _rrVersionedLabel
        ? `Create & run ${sprintLabelDisplay(_rrVersionedLabel)}`
        : "Create sprint and run";
    }
  }
}
