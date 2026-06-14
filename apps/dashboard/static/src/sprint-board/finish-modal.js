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
   renderProgressActivity */

export function _fsOpen() {
  _setBodyInert(["fs-backdrop", "fs-modal"]);
  document.getElementById("fs-backdrop").classList.remove("hidden");
  document.getElementById("fs-modal").classList.remove("hidden");
}

export function _fsClose() {
  document.getElementById("fs-backdrop").classList.add("hidden");
  document.getElementById("fs-modal").classList.remove("hidden");
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

  const logEl = document.getElementById("pa-log-stream-fs-pa");
  const atBottom =
    !logEl || logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 5;

  slot.innerHTML = renderProgressActivity(snap, {
    id: "fs-pa",
    retryFn: "_fsRetry",
  });

  if (atBottom) {
    const newLog = document.getElementById("pa-log-stream-fs-pa");
    if (newLog) newLog.scrollTop = newLog.scrollHeight;
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
  document.getElementById("fs-loading").classList.remove("hidden");
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
      '<div class="fs-action-row"><i class="ti ti-circle-check"></i> Close sprint tickets (labels kept)</div>',
    );
    actionsEl.innerHTML = actionRows.join("");

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

  // Collect selected tickets (number + title for progress labels — AC3).
  const checkboxes = Array.from(
    document.querySelectorAll("#fs-ticket-list input[type=checkbox]"),
  );
  const selectedTickets = checkboxes
    .filter((c) => c.checked)
    .map((c) => ({
      number: parseInt(c.dataset.issue, 10),
      title: c.dataset.title || `#${c.dataset.issue}`,
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
      throw new Error(err.detail || `HTTP ${res.status}`);
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
