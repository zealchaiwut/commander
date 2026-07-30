/* reasoning.js — Run reasoning panel network helper (issue #2022).
 *
 * Exported pure function: fetchRunReasoning(runId)
 * — importable by the Node --test fetch-spy tester without DOM or globals.
 *
 * The panel itself lives in run_browser.html (rendered via renderRunReasoning).
 * This module is the sole network boundary for the reasoning surface; keeping
 * it exported satisfies AC4 (behaviorally testable via fetch spy).
 */

/**
 * Fetch reasoning data for a completed agent run.
 *
 * @param {number|string} runId - agent_runs row id
 * @returns {Promise<{final_message: string|null, transcript_path: string|null, log_tail: string|null}>}
 * @throws {Error} on non-200 HTTP response (includes 404 for unknown run ids)
 */
export async function fetchRunReasoning(runId) {
  const resp = await fetch('/api/runs/' + encodeURIComponent(runId) + '/reasoning');
  if (!resp.ok) throw new Error('HTTP ' + resp.status);
  return resp.json();
}
