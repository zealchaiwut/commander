/* Sprint-board shared state (issue #797).
 *
 * The board's broadly-shared caches (`_smgmtData`, `_smgmtLiveCache`,
 * `_smgmtRunningLabels`, …) are still declared inline in project.html and are
 * reachable from these modules through the page's shared global lexical
 * environment (classic scripts share one). This module owns only the
 * MODAL/DRAG-local state that was extracted alongside its handlers, seeding it
 * on `window` so the (strict) bundle and the inline page resolve the same
 * bindings by bare name.
 *
 * Idempotent: uses `??=` so a reload/re-eval never clobbers live state.
 */

// Re-run Sprint modal (issue #512)
globalThis._rrLabel ??= null;
globalThis._rrVersionedLabel ??= null;

// Finish Sprint modal (issue #367 parity)
globalThis._fsLabel ??= null;
globalThis._fsPreview ??= null;

// Run preflight modal (issue #448)
globalThis._pfCurrentLabel ??= null;
globalThis._pfCurrentRepo ??= null;
globalThis._pfState ??= "idle";
globalThis._pfDagData ??= null;
globalThis._pfWarnings ??= null;
globalThis._pfCycle ??= null;
globalThis._pfFlags ??= null;
globalThis._pfSelectedIds ??= null;

// Drag/drop local locks
globalThis._smgmtMoveLock ??= false;
globalThis._smgmtGhostNextNum ??= null;

export const SPRINT_BOARD_STATE_KEYS = [
  "_rrLabel", "_rrVersionedLabel",
  "_fsLabel", "_fsPreview",
  "_pfCurrentLabel", "_pfCurrentRepo", "_pfState", "_pfDagData",
  "_pfWarnings", "_pfCycle", "_pfFlags", "_pfSelectedIds",
  "_smgmtMoveLock", "_smgmtGhostNextNum",
];
