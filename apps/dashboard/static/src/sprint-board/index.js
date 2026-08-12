/* Sprint-board barrel (issue #797).
 *
 * Imports every extracted concern module and re-attaches its public handlers to
 * the global object so project.html's inline HTML handlers (onclick / ondrag*)
 * keep resolving exactly as they did when the code was inline. Importing the
 * concern modules also runs their side effects; ./state.js seeds modal/drag
 * state on `window`.
 *
 * Concerns: board render · drag/drop · run-controls · finish modal
 * · bulk-complete modal · plan-next · scheduled-run.
 */

import './state.js';

import {
  _sHealthBuildHtml, _sHealthStripRender, sprintHealthStripInit,
} from './health-strip.js';
import {
  smgmtPlanNextSprint, _smgmtLoadPendingSignoff,
} from './plan-next.js';
import {
  _smgmtSchedToggleHtml, smgmtToggleRunOnSchedule, _smgmtHydrateSchedToggles,
} from './scheduled-run.js';
import {
  _histNeedsActionCount, _histLoadLedger, _histPrefetchLedger, _histScanStale, _histCleanupStale,
  _histToggleCard, _histToggleGroup, _histToggleFold, _histFocusLabel, _histStateChip,
  _histRenderLedger, _histToggleAgentTime, _histToggleMetrics, _histClearStaleLabels,
  _histResetLedgerCache, _histToggleShowClosed, _histForceRefresh, _histSetTtlMin,
  _histBulkSignOff, _histIsLoading,
} from './history.js';

import {
  _fsOpen, _fsClose, _fsCatClass, _fsSelectAll,
  smgmtFinishSprint, _fsConfirm, _fsRetry, finishSprintAndWait,
} from './finish-modal.js';
import {
  _bcOpen, _bcClose, _bcCatClass, _bcSelectAll,
  smgmtBulkCompleteSprint, _bcConfirm, bulkCompleteLineageAndWait,
} from './bulk-complete-modal.js';
import {
  smgmtReconcileSprint, _recApply, _recClose,
} from './reconcile-modal.js';
import {
  smgmtRunBlockedToast, smgmtRunSprint, smgmtCancelSprint,
  smgmtApproveSprint, smgmtRejectSprint,
  _pfOpen, _pfReset, _pfClose, _pfFetch, _pfShowSuccess, _pfUpdateConfirmBtn,
  _pfBuildWarningsHtml, _pfBuildCycleHtml, _pfBuildFlagsHtml,
  _pfFlagShowSizePicker, _pfFlagHidePicker, _pfFlagAction, _pfFlagReestimate,
  _pfFlagAutoReestimate, _pfFlagDefaultReestimateSize,
  _pfApproveAll, _pfReestimateAll, _pfBulkClose,
  _pfBuildDAGHtml, _pfDrawDAGArrows, _pfToggleTicket, _pfGetSelectedTickets,
  _pfComputeConflicts, _pfBuildConflictsHtml, _pfBuildOrderHtml,
  _pfUpdateSections, _pfShowError, _pfRetry, _pfConfirm,
  _pfStepperInit, _pfStepState, _pfStepperAnimate, _pfStepperSummary,
  smgmtKickoffRun, smgmtKickoffRetry,
} from './run-controls.js';
import {
  _smgmtBoardLock, _smgmtBoardUnlock, _smgmtBoardProgress,
  _smgmtBoardLog, _smgmtBoardFinish, _smgmtBoardHalt,
} from './board-overlay.js';
import {
  loadSprintMgmt, _smgmtSprintLabelSortKey, _smgmtRender,
  _smgmtLabelFilterRender, _smgmtLabelFilterApply,
  _smgmtFetchMissingOutcomes, _smgmtLoadEstimates, _smgmtLoadConflicts,
  _smgmtLoadDepOrder, _smgmtLoadGoals, _smgmtOutcomeBandHtml,
  _smgmtOutcomeTicketListHtml, _smgmtLoadFinishCards, _smgmtRenderFinishCard,
  _smgmtFinishCardInnerHtml, _smgmtCardHtml, _smgmtRunningCardHtml,
  _smgmtRunningBoardBannerHtml, _smgmtBoardBannerPatch, _smgmtRunningLevelText,
  _smgmtRollupText, _smgmtTicketSize, _smgmtTicketHasEstimate, _smgmtUpdateColRollup, _smgmtTicketRowHtml,
  _smgmtRenderBacklog, _smgmtBacklogTicketHtml,
  _smgmtApplyRerunOptimistic,
  // Ancestor row (issue #1043)
  _smgmtAncestorMergeState, _smgmtAncestorCarrySummary, _smgmtAncestorTicketsHtml,
  _smgmtAncestorRowHtml, smgmtToggleAncestor, _smgmtUpdateAncestorRow,
  smgmtAddToDraft,
  // SSE board_invalidated handler (issue #1785)
  _boardSseOnInvalidated, _boardSseOnVisible,
  // Clean-up-empty pure helper (issue #2089)
  _smgmtComputeLeadingEmpty,
} from './board-render.js';

// Finish modal (issue #367)
globalThis._fsOpen = _fsOpen;
globalThis._fsClose = _fsClose;
globalThis._fsCatClass = _fsCatClass;
globalThis._fsSelectAll = _fsSelectAll;
globalThis.smgmtFinishSprint = smgmtFinishSprint;
globalThis._fsConfirm = _fsConfirm;
globalThis._fsRetry = _fsRetry;
globalThis.finishSprintAndWait = finishSprintAndWait;

// Bulk Complete modal (parent + child lineage)
globalThis._bcOpen = _bcOpen;
globalThis._bcClose = _bcClose;
globalThis._bcCatClass = _bcCatClass;
globalThis._bcSelectAll = _bcSelectAll;
globalThis.smgmtBulkCompleteSprint = smgmtBulkCompleteSprint;
globalThis._bcConfirm = _bcConfirm;
globalThis.bulkCompleteLineageAndWait = bulkCompleteLineageAndWait;

globalThis.smgmtReconcileSprint = smgmtReconcileSprint;
globalThis._recApply = _recApply;
globalThis._recClose = _recClose;

// Run controls + preflight modal (issue #448)
globalThis.smgmtRunBlockedToast = smgmtRunBlockedToast;
globalThis.smgmtRunSprint = smgmtRunSprint;
globalThis.smgmtCancelSprint = smgmtCancelSprint;
globalThis.smgmtApproveSprint = smgmtApproveSprint;
globalThis.smgmtRejectSprint = smgmtRejectSprint;
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
globalThis._pfFlagAutoReestimate = _pfFlagAutoReestimate;
globalThis._pfApproveAll = _pfApproveAll;
globalThis._pfReestimateAll = _pfReestimateAll;
globalThis._pfBulkClose = _pfBulkClose;
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
// Stepper functions (issue #933)
globalThis._pfStepperInit = _pfStepperInit;
globalThis._pfStepState = _pfStepState;
globalThis._pfStepperAnimate = _pfStepperAnimate;
globalThis._pfStepperSummary = _pfStepperSummary;
// Kickoff stepper (issue #932)
globalThis.smgmtKickoffRun = smgmtKickoffRun;
globalThis.smgmtKickoffRetry = smgmtKickoffRetry;

// Board move-lock overlay (issue #276)
globalThis._smgmtBoardLock = _smgmtBoardLock;
globalThis._smgmtBoardUnlock = _smgmtBoardUnlock;
globalThis._smgmtBoardProgress = _smgmtBoardProgress;
globalThis._smgmtBoardLog = _smgmtBoardLog;
globalThis._smgmtBoardFinish = _smgmtBoardFinish;
globalThis._smgmtBoardHalt = _smgmtBoardHalt;

// Board render pipeline (issue #797)
globalThis.loadSprintMgmt = loadSprintMgmt;
globalThis._smgmtSprintLabelSortKey = _smgmtSprintLabelSortKey;
globalThis._smgmtRender = _smgmtRender;
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
globalThis._smgmtApplyRerunOptimistic = _smgmtApplyRerunOptimistic;

// Ancestor sprint rows (issue #1043)
globalThis._smgmtAncestorMergeState = _smgmtAncestorMergeState;
globalThis._smgmtAncestorCarrySummary = _smgmtAncestorCarrySummary;
globalThis._smgmtAncestorTicketsHtml = _smgmtAncestorTicketsHtml;
globalThis._smgmtAncestorRowHtml = _smgmtAncestorRowHtml;
globalThis.smgmtToggleAncestor = smgmtToggleAncestor;
globalThis._smgmtUpdateAncestorRow = _smgmtUpdateAncestorRow;

globalThis.smgmtAddToDraft = smgmtAddToDraft;

// SSE board_invalidated handler (issue #1785)
globalThis._boardSseOnInvalidated = _boardSseOnInvalidated;
globalThis._boardSseOnVisible = _boardSseOnVisible;

// Run-on-schedule toggle (issue #863)
globalThis._smgmtSchedToggleHtml = _smgmtSchedToggleHtml;
globalThis.smgmtToggleRunOnSchedule = smgmtToggleRunOnSchedule;
globalThis._smgmtHydrateSchedToggles = _smgmtHydrateSchedToggles;

// Plan next sprint + pending-sign-off decoration (issue #861)
globalThis.smgmtPlanNextSprint = smgmtPlanNextSprint;
globalThis._smgmtLoadPendingSignoff = _smgmtLoadPendingSignoff;

// History ledger (issue #806 / #797 extraction)
globalThis._histNeedsActionCount = _histNeedsActionCount;
globalThis._histLoadLedger = _histLoadLedger;
globalThis._histPrefetchLedger = _histPrefetchLedger;
globalThis._histScanStale = _histScanStale;
globalThis._histCleanupStale = _histCleanupStale;
globalThis._histToggleCard = _histToggleCard;
globalThis._histToggleGroup = _histToggleGroup;
globalThis._histToggleFold = _histToggleFold;
globalThis._histFocusLabel = _histFocusLabel;
globalThis._histStateChip = _histStateChip;
globalThis._histRenderLedger = _histRenderLedger;
globalThis._histToggleAgentTime = _histToggleAgentTime;
globalThis._histToggleMetrics = _histToggleMetrics;
globalThis._histResetLedgerCache = _histResetLedgerCache;
globalThis._histToggleShowClosed = _histToggleShowClosed;
globalThis._histForceRefresh = _histForceRefresh;
globalThis._histSetTtlMin = _histSetTtlMin;
globalThis._histBulkSignOff = _histBulkSignOff;
globalThis._histClearStaleLabels = _histClearStaleLabels;
globalThis._histIsLoading = _histIsLoading;

// Delivery-health stat strip (issue #1849)
globalThis._sHealthBuildHtml = _sHealthBuildHtml;
globalThis._sHealthStripRender = _sHealthStripRender;
globalThis.sprintHealthStripInit = sprintHealthStripInit;

// Clean-up-empty pure helper (issue #2089)
globalThis._smgmtComputeLeadingEmpty = _smgmtComputeLeadingEmpty;
