/* Bulk-move modal handler (issue #1924) — extracted from project.html.
 *
 * Owns _smgmtMoveModalPickBulk: the batch API call that moves selected
 * issues to a sprint label, creating the sprint first when isNew=true.
 * Exported for unit tests; exposed on globalThis via index.js.
 */

/* eslint-disable no-unused-vars */
/* global _smgmtRepo, _smgmtData, _smgmtClearSelection, _smgmtRender,
   _smgmtBoardLock, _smgmtBoardUnlock, _smgmtShowInlineError, _smgmtShowToast,
   loadSprintMgmt */
/* eslint-enable no-unused-vars */

/** Bulk move — same batch API as the former selection-bar dropdown. */
export async function _smgmtMoveModalPickBulk(nums, targetLabel, isNew) {
  const repo = _smgmtRepo();
  if (!repo || nums.length === 0) return;

  // sprintLabel / dest may be updated after the backend creates the sprint.
  let sprintLabel = targetLabel || 'backlog';
  let dest = sprintLabel === 'backlog'
    ? 'Backlog'
    : `Sprint ${sprintLabel.split('-')[1]}`;

  // Optimistic update only for moves to existing sprints — skip for isNew
  // because the real sprint label is not yet known.
  if (!isNew && _smgmtData) {
    const targetNum = sprintLabel === 'backlog'
      ? null
      : parseInt(sprintLabel.split('-')[1], 10);
    nums.forEach(n => {
      const iss = _smgmtData.issues.find(i => i.number === n);
      if (iss) iss.sprint = targetNum;
    });
    _smgmtClearSelection();
    _smgmtRender(_smgmtData);
  }

  _smgmtBoardLock(`Moving ${nums.length} issue${nums.length !== 1 ? 's' : ''} to ${dest}…`);
  try {
    if (isNew) {
      // Let the backend assign the correct next sprint number — do NOT send
      // sprint_number so stale labels from old lineages don't cause a 409
      // that was previously swallowed silently.
      const createRes = await fetch('/api/sprints/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project: repo }),
      });
      if (!createRes.ok) {
        const d = await createRes.json().catch(() => ({}));
        throw new Error(d.detail || 'HTTP ' + createRes.status);
      }
      const createData = await createRes.json();
      sprintLabel = createData.sprint_label;
      dest = `Sprint ${sprintLabel.split('-')[1]}`;
    }
    const changes = nums.map(n => ({ issue_num: n, sprint_label: sprintLabel }));
    const res = await fetch('/api/sprints/batch-labels', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ changes, project: repo }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    if (data.failed > 0 && data.errors?.length) {
      _smgmtShowInlineError(`${data.failed} issue${data.failed !== 1 ? 's' : ''} failed to move:\n${data.errors.join('\n')}`);
    } else if (data.applied > 0) {
      _smgmtShowToast(`Moved ${data.applied} issue${data.applied !== 1 ? 's' : ''} to ${dest}.`);
    }
    if (isNew) _smgmtClearSelection();
    await loadSprintMgmt();
  } catch (e) {
    _smgmtShowToast('Failed to move issues: ' + e.message);
    await loadSprintMgmt();
  } finally {
    _smgmtBoardUnlock();
  }
}
