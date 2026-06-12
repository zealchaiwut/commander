/* Bundle entry point (issue #796).
 *
 * esbuild bundles this into static/dist/bundle.js (IIFE format). The dashboard
 * is served from disk with no build step, so the emitted bundle is committed
 * and loaded directly by project.html.
 *
 * Modules: the log-panel tokenizer (#796) and the sprint-management board
 * (#797 — board render, drag/drop, run-controls, finish modal, rerun modal).
 * Follow-on tickets extract additional self-contained blocks into static/src/
 * and import them here.
 */
import { colorizeLogLine, escapeLogHtml, extractRaw, AGENT_NAMES } from './logpanel.js';
import './sprint-board/index.js';

// Preserve the historical global API. project.html and run_browser.html call
// these helpers on `window` (see static/AGENTS.md "What NOT to Touch"); the
// bundle keeps that contract intact so the page loads with no ReferenceError.
const root = typeof window !== 'undefined' ? window : globalThis;
root.colorizeLogLine = colorizeLogLine;
root.escapeLogHtml = escapeLogHtml;
root.extractRaw = extractRaw;
root.AGENT_NAMES = AGENT_NAMES;
