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

// Re-run modal (issue #512)
globalThis._rrOpen = _rrOpen;
globalThis._rrClose = _rrClose;
globalThis._rrCatClass = _rrCatClass;
globalThis._rrUpdateState = _rrUpdateState;
globalThis._rrSelectAll = _rrSelectAll;
globalThis.smgmtRerunSprint = smgmtRerunSprint;
globalThis._rrConfirm = _rrConfirm;
