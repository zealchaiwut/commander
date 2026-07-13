/**
 * Logs view control helpers (issue #1858).
 *
 * Pure functions — no DOM access, no globals.
 * Exposed on window via index.js so project.html inline scripts can call them.
 */

/**
 * Returns true when logsFetchRuns should trigger logsLoadRawView automatically.
 * Fires when raw view is active, nothing has been rendered yet, and runs exist.
 *
 * @param {string}     viewMode  _logsState.viewMode ('raw'|'activity')
 * @param {Array|null} rawLines  _logsState.rawLines (null = nothing loaded)
 * @param {number}     runCount  _logsState.runs.length
 */
export function shouldAutoLoadRaw(viewMode, rawLines, runCount) {
  return viewMode === 'raw' && rawLines === null && runCount > 0;
}

/**
 * Returns the sprint label to load in raw view.
 * Respects an explicit filter; falls back to runs[0] only when no filter is set.
 *
 * @param {Array<{sprint_label: string}>} runs         _logsState.runs
 * @param {string|null}                  filterSprint  _logsState.filter.sprint
 * @returns {string|null}
 */
export function pickAutoSprintLabel(runs, filterSprint) {
  if (filterSprint) return filterSprint;
  return (runs[0] && runs[0].sprint_label) || null;
}

/**
 * Returns a visibility descriptor for toolbar controls given the active view mode.
 * true = visible, false = hidden.
 *
 * @param {'activity'|'raw'} mode
 */
export function logsToolbarVisibility(mode) {
  const isActivity = mode === 'activity';
  const isRaw = mode === 'raw';
  return {
    agentSelect:    isActivity,
    sourceSelect:   isActivity,
    severitySeg:    isActivity,
    rawLevelSelect: isRaw,
    sprintSelect:   true,
    searchInput:    true,
  };
}
