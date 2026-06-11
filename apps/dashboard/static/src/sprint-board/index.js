/* Sprint-board barrel (issue #797).
 *
 * Imports every extracted concern module and re-attaches its public handlers to
 * the global object so project.html's inline HTML handlers (onclick / ondrag*)
 * keep resolving exactly as they did when the code was inline. Importing the
 * concern modules also runs their side effects; ./state.js seeds modal/drag
 * state on `window`.
 *
 * Concerns: board render · drag/drop · run-controls · finish modal · rerun modal.
 */

import './state.js';

import {
  _rrOpen, _rrClose, _rrCatClass, _rrUpdateState, _rrSelectAll,
  smgmtRerunSprint, _rrConfirm,
} from './rerun-modal.js';
import {
  _fsOpen, _fsClose, _fsCatClass, _fsSelectAll,
  smgmtFinishSprint, _fsConfirm,
} from './finish-modal.js';
import {
  smgmtRunBlockedToast, smgmtRunSprint, smgmtCancelSprint,
  _pfOpen, _pfReset, _pfClose, _pfFetch, _pfShowSuccess, _pfUpdateConfirmBtn,
  _pfBuildWarningsHtml, _pfBuildCycleHtml, _pfBuildFlagsHtml,
  _pfFlagShowSizePicker, _pfFlagHidePicker, _pfFlagAction, _pfFlagReestimate,
  _pfBuildDAGHtml, _pfDrawDAGArrows, _pfToggleTicket, _pfGetSelectedTickets,
  _pfComputeConflicts, _pfBuildConflictsHtml, _pfBuildOrderHtml,
  _pfUpdateSections, _pfShowError, _pfRetry, _pfConfirm,
} from './run-controls.js';

// Re-run modal (issue #512)
globalThis._rrOpen = _rrOpen;
globalThis._rrClose = _rrClose;
globalThis._rrCatClass = _rrCatClass;
globalThis._rrUpdateState = _rrUpdateState;
globalThis._rrSelectAll = _rrSelectAll;
globalThis.smgmtRerunSprint = smgmtRerunSprint;
globalThis._rrConfirm = _rrConfirm;

// Finish modal (issue #367)
globalThis._fsOpen = _fsOpen;
globalThis._fsClose = _fsClose;
globalThis._fsCatClass = _fsCatClass;
globalThis._fsSelectAll = _fsSelectAll;
globalThis.smgmtFinishSprint = smgmtFinishSprint;
globalThis._fsConfirm = _fsConfirm;

// Run controls + preflight modal (issue #448)
globalThis.smgmtRunBlockedToast = smgmtRunBlockedToast;
globalThis.smgmtRunSprint = smgmtRunSprint;
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
