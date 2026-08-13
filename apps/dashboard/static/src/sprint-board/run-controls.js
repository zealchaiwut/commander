/* Run controls — REMOVED (issue #2251).
 *
 * Dispatch controls stripped. This file is retained as a stub so that
 * index.js imports resolve without changes. Real preflight-warnings code
 * lives in preflight-warnings.js. All dispatch functions are no-ops.
 */

export {
  _pfBuildWarningsHtml,
  smgmtOpenPreflightWarnings,
  _pfClose,
  _pfRetry,
  _pfConfirm,
  _pfBulkClose,
} from './preflight-warnings.js';

const _noop = () => {};
const _noopStr = () => '';

export const smgmtRunBlockedToast = _noop;
export const smgmtRunSprint = _noop;
export const smgmtCancelSprint = _noop;
export const smgmtApproveSprint = _noop;
export const smgmtRejectSprint = _noop;
export const _pfOpen = _noop;
export const _pfReset = _noop;
export const _pfFetch = _noop;
export const _pfShowSuccess = _noop;
export const _pfUpdateConfirmBtn = _noop;
export const _pfBuildCycleHtml = _noopStr;
export const _pfBuildFlagsHtml = _noopStr;
export const _pfFlagShowSizePicker = _noop;
export const _pfFlagHidePicker = _noop;
export const _pfFlagAction = _noop;
export const _pfFlagReestimate = _noop;
export const _pfFlagAutoReestimate = _noop;
export const _pfFlagDefaultReestimateSize = _noopStr;
export const _pfApproveAll = _noop;
export const _pfReestimateAll = _noop;
export const _pfBuildDAGHtml = _noopStr;
export const _pfDrawDAGArrows = _noop;
export const _pfToggleTicket = _noop;
export const _pfGetSelectedTickets = () => [];
export const _pfComputeConflicts = () => [];
export const _pfBuildConflictsHtml = _noopStr;
export const _pfBuildOrderHtml = _noopStr;
export const _pfUpdateSections = _noop;
export const _pfShowError = _noop;
export const _pfStepperInit = _noop;
export const _pfStepState = _noop;
export const _pfStepperAnimate = _noop;
export const _pfStepperSummary = _noop;
export const smgmtKickoffRun = _noop;
export const smgmtKickoffRetry = _noop;
