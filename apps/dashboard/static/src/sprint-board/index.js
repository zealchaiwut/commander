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

import "./state.js";

import {
  _smgmtSchedToggleHtml,
  smgmtToggleRunOnSchedule,
  _smgmtHydrateSchedToggles,
} from "./scheduled-run.js";

import {
  _rrOpen,
  _rrClose,
  _rrCatClass,
  _rrUpdateState,
  _rrSelectAll,
  smgmtRerunSprint,
  _rrConfirm,
} from "./rerun-modal.js";
import {
  _fsOpen,
  _fsClose,
  _fsCatClass,
  _fsSelectAll,
  smgmtFinishSprint,
  _fsConfirm,
  _fsRetry,
} from "./finish-modal.js";
import {
  _bcOpen,
  _bcClose,
  _bcCatClass,
  _bcSelectAll,
  smgmtBulkCompleteSprint,
  _bcConfirm,
} from "./bulk-complete-modal.js";
import {
  smgmtRunBlockedToast,
  smgmtRunSprint,
  smgmtCancelSprint,
  _pfOpen,
  _pfReset,
  _pfClose,
  _pfFetch,
  _pfShowSuccess,
  _pfUpdateConfirmBtn,
  _pfBuildWarningsHtml,
  _pfBuildCycleHtml,
  _pfBuildFlagsHtml,
  _pfFlagShowSizePicker,
  _pfFlagHidePicker,
  _pfFlagAction,
  _pfFlagReestimate,
  _pfBuildDAGHtml,
  _pfDrawDAGArrows,
  _pfToggleTicket,
  _pfGetSelectedTickets,
  _pfComputeConflicts,
  _pfBuildConflictsHtml,
  _pfBuildOrderHtml,
  _pfUpdateSections,
  _pfShowError,
  _pfRetry,
  _pfConfirm,
  _pfStepperInit,
  _pfStepState,
  _pfStepperAnimate,
  _pfStepperSummary,
} from "./run-controls.js";
import {
  computeDropPlan,
  _smgmtUpdateSelectionUI,
  _smgmtPopulateSelectionDropdown,
  _smgmtPopulateMoveToMenu,
  _smgmtToggleMoveToMenu,
  _smgmtCloseMoveToMenu,
  _smgmtClearSelection,
  _smgmtSetSelected,
  _smgmtToggleSelect,
  _smgmtRowClick,
  _smgmtIsDeletableIssue,
  _smgmtDeleteSelected,
  _smgmtMoveSelectedTo,
  _smgmtTicketDragStart,
  _smgmtDragMovePill,
  _smgmtGhostComputeNextFree,
  _smgmtGhostShow,
  _smgmtGhostHide,
  _smgmtGhostDragOver,
  _smgmtGhostDragLeave,
  _smgmtGhostDrop,
  _gcClose,
  _gcConfirm,
  _smgmtTicketDragEnd,
  _smgmtDragOver,
  _smgmtDragLeave,
  _smgmtDropOnSprint,
  _smgmtTicketReorderDragOver,
  _smgmtTicketReorderDragLeave,
  _smgmtTicketReorderDrop,
  _smgmtBacklogTicketDragStart,
  _smgmtBacklogDragOver,
  _smgmtBacklogDragLeave,
  _smgmtDropOnBacklog,
  _smgmtBoardLock,
  _smgmtBoardUnlock,
  _smgmtBoardProgress,
  _smgmtBoardLog,
} from "./drag-drop.js";
import {
  loadSprintMgmt,
  _smgmtSprintLabelSortKey,
  _smgmtRender,
  _smgmtLabelFilterRender,
  _smgmtLabelFilterApply,
  _smgmtFetchMissingOutcomes,
  _smgmtLoadEstimates,
  _smgmtLoadConflicts,
  _smgmtLoadDepOrder,
  _smgmtLoadGoals,
  _smgmtOutcomeBandHtml,
  _smgmtOutcomeTicketListHtml,
  _smgmtLoadFinishCards,
  _smgmtRenderFinishCard,
  _smgmtFinishCardInnerHtml,
  _smgmtCardHtml,
  _smgmtRunningCardHtml,
  _smgmtRunningBoardBannerHtml,
  _smgmtBoardBannerPatch,
  _smgmtRunningLevelText,
  _smgmtRollupText,
  _smgmtTicketSize,
  _smgmtTicketHasEstimate,
  _smgmtUpdateColRollup,
  _smgmtTicketRowHtml,
  _smgmtRenderBacklog,
  _smgmtBacklogTicketHtml,
  _smgmtApplyRerunOptimistic,
} from "./board-render.js";

// Re-run modal (issue #512)
globalThis._rrOpen = _rrOpen;
globalThis._rrClose = _rrClose;
globalThis._rrCatClass = _rrCatClass;
globalThis._rrUpdateState = _rrUpdateState;
globalThis._rrSelectAll = _rrSelectAll;
globalThis.smgmtRerunSprint = smgmtRerunSprint;
globalThis._rrConfirm = _rrConfirm;

// Finish modal (issue #367 / #929)
globalThis._fsOpen = _fsOpen;
globalThis._fsClose = _fsClose;
globalThis._fsCatClass = _fsCatClass;
globalThis._fsSelectAll = _fsSelectAll;
globalThis.smgmtFinishSprint = smgmtFinishSprint;
globalThis._fsConfirm = _fsConfirm;
globalThis._fsRetry = _fsRetry;

// Bulk Complete modal (parent + child lineage)
globalThis._bcOpen = _bcOpen;
globalThis._bcClose = _bcClose;
globalThis._bcCatClass = _bcCatClass;
globalThis._bcSelectAll = _bcSelectAll;
globalThis.smgmtBulkCompleteSprint = smgmtBulkCompleteSprint;
globalThis._bcConfirm = _bcConfirm;

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
// Stepper functions (issue #933)
globalThis._pfStepperInit = _pfStepperInit;
globalThis._pfStepState = _pfStepState;
globalThis._pfStepperAnimate = _pfStepperAnimate;
globalThis._pfStepperSummary = _pfStepperSummary;

// Drag & drop + multi-select + ghost pane + board lock (issues #247/#276/#660)
// computeDropPlan is a DOM-free decision helper kept on the global (and thus in
// the bundle) for the drag/drop smoke-test contract — see test_..__797.py.
globalThis.computeDropPlan = computeDropPlan;
globalThis._smgmtUpdateSelectionUI = _smgmtUpdateSelectionUI;
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

// Run-on-schedule toggle (issue #863)
globalThis._smgmtSchedToggleHtml = _smgmtSchedToggleHtml;
globalThis.smgmtToggleRunOnSchedule = smgmtToggleRunOnSchedule;
globalThis._smgmtHydrateSchedToggles = _smgmtHydrateSchedToggles;
