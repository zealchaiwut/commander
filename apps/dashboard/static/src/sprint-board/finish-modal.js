/* Finish Sprint modal (issue #367 parity) — extracted from project.html (#797).
 *
 * Opens the finish-sprint modal, previews which tickets close vs. carry forward
 * (and whether a sprint PR will be merged), and confirms the finish. Page
 * helpers and broadly-shared board caches resolve through the page's global
 * scope; modal-local state (`_fsLabel`, `_fsPreview`) is seeded on `window` by
 * ./state.js.
 *
 * Issue #929: After confirm, the modal switches to ProgressActivity bar mode
 * and streams live progress via SSE. Closing the modal does not cancel the
 * background task; reopening reconnects to the in-flight stream.
 */

/* global _setBodyInert, _clearBodyInert, _smgmtRepo, sprintLabelDisplay,
   escHtml, loadSprintMgmt,
   _fsLabel:writable, _fsPreview:writable, _fsActiveJob:writable,
   renderProgressActivity, patchProgressActivityInPlace */

export function _fsOpen() {
  _setBodyInert(["fs-backdrop", "fs-modal"]);
  document.getElementById("fs-backdrop").classList.remove("hidden");
  document.getElementById("fs-modal").classList.remove("hidden");
}

export function _fsClose() {
  document.getElementById("fs-backdrop").classList.add("hidden");
  document.getElementById("fs-modal").classList.add("hidden");
  _clearBodyInert();
  // Close the EventSource so we don't leak connections, but keep _fsActiveJob
  // so the modal can reconnect on reopen (AC5 — job continues in background).
  if (_fsActiveJob && _fsActiveJob.es) {
    _fsActiveJob.es.close();
    _fsActiveJob.es = null;
  }
  // Clear preview state; active-job state is preserved for reconnect.
  const snap = _fsActiveJob && _fsActiveJob.snapshot;
  if (!snap || snap.status === "done" || snap.status === "error") {
    _fsActiveJob = null;
  }
  _fsLabel = null;
  _fsPreview = null;
}

export function _fsCatClass(cat) {
  if (cat === "UAT") return "rr-cat-uat";
  if (cat === "SIT") return "rr-cat-sit";
  if (cat === "needs-rework") return "rr-cat-rework";
  if (cat === "sprint-summary") return "rr-cat-summary";
  return "rr-cat-queued";
}

export function _fsSelectAll(checked) {
  document
    .querySelectorAll("#fs-ticket-list input[type=checkbox]")
    .forEach((cb) => {
      cb.checked = checked;
    });
}

// ── Progress view helpers (issue #929) ────────────────────────────────────────

function _fsProgressSlot() {
  return document.getElementById("fs-progress");
}
function _fsPreviewSlot() {
  return document.getElementById("fs-content");
}

function _fsRenderPreviewLoading(current) {
  const loading = document.getElementById("fs-loading");
  if (!loading) return;
  loading.innerHTML = renderProgressActivity({
    status: "running",
    mode: "indeterminate",
    current: current || "Loading preview…",
  }, {
    id: "fs-preview-pa",
    hideLog: true,
  });
  loading.classList.remove("hidden");
}

/** Switch modal body to ProgressActivity bar mode and set footer buttons. */
function _fsEnterProgressView(snap) {
  document.getElementById("fs-loading").classList.add("hidden");
  _fsPreviewSlot() && _fsPreviewSlot().classList.add("hidden");
  document.getElementById("fs-error").classList.add("hidden");

  const slot = _fsProgressSlot();
  if (slot) {
    slot.innerHTML = renderProgressActivity(snap, {
      id: "fs-pa",
      retryFn: "_fsRetry",
    });
    slot.classList.remove("hidden");
  }

  // Footer: hide "Merge Sprint", change Cancel → Close
  const confirmBtn = document.getElementById("fs-confirm-btn");
  const cancelBtn = document.getElementById("fs-cancel-btn");
  const retryBtn = document.getElementById("fs-retry-btn");
  if (confirmBtn) confirmBtn.classList.add("hidden");
  if (cancelBtn) cancelBtn.textContent = "Close";
  if (retryBtn) retryBtn.classList.add("hidden");
}

/** Update the ProgressActivity component in the progress slot on each SSE event. */
function _fsUpdateProgress(snap) {
  const slot = _fsProgressSlot();
  if (!slot || slot.classList.contains("hidden")) return;

  const patched = patchProgressActivityInPlace("fs-pa", snap, {
    retryFn: "_fsRetry",
  });
  if (!patched) {
    slot.innerHTML = renderProgressActivity(snap, {
      id: "fs-pa",
      retryFn: "_fsRetry",
    });
  }
}

/** Handle done snapshot: show summary, update footer, refresh board. */
function _fsDone(snap) {
  _fsUpdateProgress(snap);
  const cancelBtn = document.getElementById("fs-cancel-btn");
  const retryBtn = document.getElementById("fs-retry-btn");
  if (cancelBtn) cancelBtn.textContent = "Close";
  if (retryBtn) retryBtn.classList.add("hidden");
  _fsActiveJob = null;
  // Refresh the sprint board after a brief pause to let GitHub settle.
  setTimeout(() => loadSprintMgmt(), 1500);
}

/** Handle error snapshot: show error state and reveal retry button. */
function _fsHandleError(snap) {
  _fsUpdateProgress(snap);
  const cancelBtn = document.getElementById("fs-cancel-btn");
  const retryBtn = document.getElementById("fs-retry-btn");
  if (cancelBtn) cancelBtn.textContent = "Close";
  if (retryBtn) retryBtn.classList.remove("hidden");
}

/** Connect an EventSource to /finish-stream and drive the progress view. */
function _fsConnectStream(owner, repoName, label) {
  const url = `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/finish-stream`;
  const es = new EventSource(url);
  if (_fsActiveJob) _fsActiveJob.es = es;

  es.onmessage = (e) => {
    let snap;
    try {
      snap = JSON.parse(e.data);
    } catch {
      return;
    }
    if (snap.ping) return;
    if (_fsActiveJob) _fsActiveJob.snapshot = snap;
    if (snap.status === "done") {
      es.close();
      if (_fsActiveJob) _fsActiveJob.es = null;
      _fsDone(snap);
    } else if (snap.status === "error") {
      es.close();
      if (_fsActiveJob) _fsActiveJob.es = null;
      _fsHandleError(snap);
    } else {
      _fsUpdateProgress(snap);
    }
  };

  es.onerror = () => {
    es.close();
    if (_fsActiveJob) _fsActiveJob.es = null;
  };
}

/** Run Complete (finish-bg + stream) for one sprint; resolves when done. */
export function finishSprintAndWait(label) {
  return new Promise(async (resolve, reject) => {
    const repo = _smgmtRepo();
    if (!repo) {
      reject(new Error('No project loaded'));
      return;
    }
    const parts = repo.split('/');
    const owner = parts[0];
    const repoName = parts.slice(1).join('/');
    try {
      const prevRes = await fetch(
        `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/finish-preview`,
      );
      if (!prevRes.ok) {
        const err = await prevRes.json().catch(() => ({}));
        throw new Error(err.detail || `Preview failed (HTTP ${prevRes.status})`);
      }
      const preview = await prevRes.json();
      if (preview.conflict_error) throw new Error(preview.conflict_error);
      const allTickets = preview.all_tickets || [];
      const bgParams = {
        confirmed: true,
        move_non_uat_to: preview.next_sprint_label || '',
        selected_ticket_numbers: allTickets.map((t) => t.number),
        selected_tickets: allTickets.map((t) => ({
          number: t.number,
          title: t.title || `#${t.number}`,
        })),
        merge_pr: !!preview.sprint_pr,
        sprint_pr_url: preview.sprint_pr ? preview.sprint_pr.url : null,
        total: allTickets.length + 2,
      };
      const res = await fetch(
        `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/finish-bg`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(bgParams),
        },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const url = `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/finish-stream`;
      const es = new EventSource(url);
      es.onmessage = (e) => {
        let snap;
        try {
          snap = JSON.parse(e.data);
        } catch {
          return;
        }
        if (snap.ping) return;
        if (snap.status === 'done') {
          es.close();
          resolve(snap);
        } else if (snap.status === 'error') {
          es.close();
          reject(new Error(snap.error || 'Finish failed'));
        }
      };
      es.onerror = () => {
        es.close();
        reject(new Error('Finish stream disconnected'));
      };
    } catch (e) {
      reject(e);
    }
  });
}

/** Retry a failed finish operation using the stored params. */
export async function _fsRetry() {
  if (!_fsActiveJob) return;
  const { owner, repoName, label, params } = _fsActiveJob;
  const emptySnap = {
    status: "running",
    mode: "bar",
    done: 0,
    total: params.total || 2,
    current: "Retrying…",
    log_tail: [],
  };
  _fsEnterProgressView(emptySnap);
  _fsActiveJob.snapshot = emptySnap;

  try {
    const res = await fetch(
      `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/finish-bg`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...params, confirmed: true }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    _fsConnectStream(owner, repoName, label);
  } catch (e) {
    const slot = _fsProgressSlot();
    if (slot) {
      slot.innerHTML = renderProgressActivity(
        {
          status: "error",
          mode: "bar",
          error: "Retry failed: " + e.message,
          log_tail: [],
        },
        { id: "fs-pa", retryFn: "_fsRetry" },
      );
    }
    const retryBtn = document.getElementById("fs-retry-btn");
    if (retryBtn) retryBtn.classList.remove("hidden");
  }
}

// ── Main entry points ─────────────────────────────────────────────────────────

export async function smgmtFinishSprint(label) {
  const repo = _smgmtRepo();
  if (!repo) return;

  const parts = repo.split("/");
  const owner = parts[0];
  const repoName = parts.slice(1).join("/");

  // If a finish job for this label is still running, reconnect instead of
  // reloading the preview (AC6 — modal shows live progress on reopen).
  if (_fsActiveJob && _fsActiveJob.label === label) {
    _fsLabel = label;
    document.getElementById("fs-modal-title").textContent =
      `Merging ${sprintLabelDisplay(label)}…`;
    _fsOpen();
    const snap = _fsActiveJob.snapshot;
    if (snap) {
      _fsEnterProgressView(snap);
      if (snap.status === "done") {
        _fsDone(snap);
      } else if (snap.status === "error") {
        _fsHandleError(snap);
      } else {
        _fsConnectStream(owner, repoName, label);
      }
    }
    return;
  }

  // Normal preview flow
  _fsLabel = label;
  _fsPreview = null;

  document.getElementById("fs-modal-title").textContent =
    `Merge ${sprintLabelDisplay(label)}?`;
  _fsRenderPreviewLoading("Loading preview…");
  document.getElementById("fs-content").classList.add("hidden");
  document.getElementById("fs-error").classList.add("hidden");
  document.getElementById("fs-error").textContent = "";

  // Reset footer to preview state
  const confirmBtn = document.getElementById("fs-confirm-btn");
  const cancelBtn = document.getElementById("fs-cancel-btn");
  const retryBtn = document.getElementById("fs-retry-btn");
  if (confirmBtn) {
    confirmBtn.classList.remove("hidden");
    confirmBtn.disabled = true;
    confirmBtn.textContent = "Merge Sprint";
  }
  if (cancelBtn) cancelBtn.textContent = "Cancel";
  if (retryBtn) retryBtn.classList.add("hidden");

  const progSlot = _fsProgressSlot();
  if (progSlot) progSlot.classList.add("hidden");

  _fsOpen();

  try {
    const res = await fetch(
      `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(label)}/finish-preview`,
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
      listEl.innerHTML =
        '<div style="padding:10px;color:var(--text-muted);font-size:13px">No open tickets in this sprint.</div>';
    } else {
      listEl.innerHTML = allTickets
        .map((t) => {
          const catClass = _fsCatClass(t.category);
          const catLabel =
            t.category === "sprint-summary"
              ? "SUMMARY"
              : t.category.toUpperCase();
          return `<label class="rr-ticket-row">
          <input type="checkbox" checked data-issue="${t.number}" data-title="${escHtml(t.title)}" onchange="">
          <span class="rr-ticket-num">#${t.number}</span>
          <span class="rr-ticket-title" title="${escHtml(t.title)}">${escHtml(t.title)}</span>
          <span class="rr-ticket-cat ${catClass}">${escHtml(catLabel)}</span>
        </label>`;
        })
        .join("");
    }

    const actionsEl = document.getElementById("fs-actions");
    const actionRows = [];
    const mergeBranches = preview.merge_branches || [];
    for (const mb of mergeBranches) {
      actionRows.push(
        `<div class="fs-action-row"><i class="ti ti-git-merge"></i> Merge ` +
          `<code>${escHtml(mb.head)}</code> → <code>${escHtml(mb.base)}</code></div>`,
      );
    }
    if (preview.sprint_pr) {
      actionRows.push(`<div class="fs-action-row"><i class="ti ti-git-merge"></i> Merge open PR
        <a href="${escHtml(preview.sprint_pr.url)}" target="_blank" rel="noopener">#${preview.sprint_pr.number}</a></div>`);
    }
    actionRows.push(
      `<div class="fs-action-row"><i class="ti ti-circle-check"></i> Close all ${allTickets.length} sprint ticket${allTickets.length !== 1 ? 's' : ''}</div>`,
    );
    actionsEl.innerHTML = actionRows.join("");

    // issue #1696: soft rework guard — warn when tickets haven't reached UAT.
    const reworkTickets = preview.rework_tickets || [];
    const warningEl = document.getElementById("fs-rework-warning");
    const warningTextEl = document.getElementById("fs-rework-warning-text");
    const reworkCheckbox = document.getElementById("fs-confirm-rework-checkbox");
    if (reworkTickets.length > 0) {
      warningTextEl.textContent =
        `${reworkTickets.length} ticket${reworkTickets.length !== 1 ? "s" : ""} ` +
        `will be closed unfinished: ` +
        reworkTickets.map((t) => `#${t.number}`).join(", ");
      warningEl.classList.remove("hidden");
      if (reworkCheckbox) reworkCheckbox.checked = false;
    } else {
      warningEl.classList.add("hidden");
    }

    document.getElementById("fs-loading").classList.add("hidden");
    document.getElementById("fs-content").classList.remove("hidden");
    if (confirmBtn) confirmBtn.disabled = false;
  } catch (e) {
    document.getElementById("fs-loading").classList.add("hidden");
    const errEl = document.getElementById("fs-error");
    errEl.textContent = "Failed to load preview: " + e.message;
    errEl.classList.remove("hidden");
  }
}

export async function _fsConfirm() {
  const repo = _smgmtRepo();
  if (!_fsLabel || !repo || !_fsPreview) return;
  const parts = repo.split("/");
  const owner = parts[0];
  const repoName = parts.slice(1).join("/");

  // issue #1696: soft rework guard — require the override checkbox when the
  // preview flagged unfinished tickets.
  const reworkTickets = _fsPreview.rework_tickets || [];
  const reworkCheckbox = document.getElementById("fs-confirm-rework-checkbox");
  if (reworkTickets.length > 0 && !(reworkCheckbox && reworkCheckbox.checked)) {
    const errEl = document.getElementById("fs-error");
    errEl.textContent = "Check the box to confirm closing unfinished tickets, or cancel and re-run them first.";
    errEl.classList.remove("hidden");
    return;
  }

  // Always close every open ticket in the sprint preview (operator can uncheck
  // for visibility, but confirm sends the full set).
  const allTickets = _fsPreview.all_tickets || [];
  const selectedTickets = allTickets.map((t) => ({
    number: t.number,
    title: t.title || `#${t.number}`,
  }));
  const selectedNums = selectedTickets.map((t) => t.number);

  const confirmBtn = document.getElementById("fs-confirm-btn");
  if (confirmBtn) {
    confirmBtn.disabled = true;
    confirmBtn.textContent = "Starting…";
  }

  const bgParams = {
    move_non_uat_to: _fsPreview.next_sprint_label || "",
    selected_ticket_numbers: selectedNums,
    selected_tickets: selectedTickets,
    merge_pr: !!_fsPreview.sprint_pr,
    sprint_pr_url: _fsPreview.sprint_pr ? _fsPreview.sprint_pr.url : null,
    total: selectedNums.length + 2,
    confirm_rework: reworkTickets.length > 0 && !!(reworkCheckbox && reworkCheckbox.checked),
  };

  try {
    const res = await fetch(
      `/api/projects/${encodeURIComponent(owner)}/${encodeURIComponent(repoName)}/sprints/${encodeURIComponent(_fsLabel)}/finish-bg`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmed: true, ...bgParams }),
      },
    );
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      const detail = err.detail;
      const msg = detail && typeof detail === "object" ? detail.message : detail;
      throw new Error(msg || `HTTP ${res.status}`);
    }
    await res.json();

    // Register active job for reconnect support (AC6)
    const initialSnap = {
      status: "running",
      mode: "bar",
      done: 0,
      total: bgParams.total,
      current: "Starting…",
      log_tail: [],
    };
    _fsActiveJob = {
      label: _fsLabel,
      owner,
      repoName,
      params: bgParams,
      snapshot: initialSnap,
      es: null,
    };

    document.getElementById("fs-modal-title").textContent =
      `Merging ${sprintLabelDisplay(_fsLabel)}…`;
    _fsEnterProgressView(initialSnap);
    _fsConnectStream(owner, repoName, _fsLabel);
  } catch (e) {
    const errEl = document.getElementById("fs-error");
    errEl.textContent = "Failed to start finish: " + e.message;
    errEl.classList.remove("hidden");
    if (confirmBtn) {
      confirmBtn.classList.remove("hidden");
      confirmBtn.disabled = false;
      confirmBtn.textContent = "Merge Sprint";
    }
  }
}
