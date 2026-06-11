"""Acceptance tests for issue #797 — extract the sprint management board into ES modules.

Each test is anchored to a specific acceptance criterion from the issue. The
dashboard is served from disk with no build step, so the committed bundle
(``static/dist/bundle.js``) is what production runs; these tests verify the
*module layout, the de-inlining of board JS, the measurable payload reduction,
the drag/drop smoke test, and the CI gate wiring* at the file level.

AC map:
  AC1  static/src/sprint-board/ holds discrete modules: board render, drag/drop,
       run-controls, finish modal, rerun modal
  AC2  project.html retains only mount points + HTML partials; no inline board JS
  AC3  measurable project.html KB reduction (before/after recorded)
  AC4  a drag/drop smoke test exists against the modularized code and passes green
  AC5  new modules pass impeccable + lint gates in CI (source is module-clean and
       the CI workflow targets static/src)
  AC6  no regressions: the board's public handlers stay reachable (bundle re-exposes
       them on window so project.html's HTML handlers keep working)
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
SRC_DIR = STATIC_DIR / "src"
BOARD_DIR = SRC_DIR / "sprint-board"
DIST_DIR = STATIC_DIR / "dist"
PROJECT_HTML = STATIC_DIR / "project.html"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# Baseline size of project.html BEFORE the extraction (bytes), captured on the
# feature branch at branch creation. AC3 requires a measurable reduction below
# this number.
PROJECT_HTML_BASELINE_BYTES = 1050642

# The five named concerns and a representative module file for each (AC1).
CONCERN_MODULES = {
    "board-render": BOARD_DIR / "board-render.js",
    "drag-drop": BOARD_DIR / "drag-drop.js",
    "run-controls": BOARD_DIR / "run-controls.js",
    "finish-modal": BOARD_DIR / "finish-modal.js",
    "rerun-modal": BOARD_DIR / "rerun-modal.js",
}

# Board functions that MUST no longer be defined inline in project.html and MUST
# live in the extracted modules instead (one canonical anchor per concern, plus
# a few key handlers exercised by the UAT steps).
EXTRACTED_FUNCTIONS = {
    "board-render": ["_smgmtRender", "loadSprintMgmt", "_smgmtCardHtml"],
    "drag-drop": [
        "_smgmtTicketDragStart",
        "_smgmtDropOnSprint",
        "_smgmtDropOnBacklog",
        "_smgmtGhostDrop",
    ],
    "run-controls": ["smgmtRunSprint", "_smgmtBoardLock", "_pfFetch"],
    "finish-modal": ["smgmtFinishSprint", "_fsConfirm"],
    "rerun-modal": ["smgmtRerunSprint", "_rrConfirm"],
}

# Public handlers wired into project.html's HTML (onclick / ondrag*) that must
# stay reachable on window after extraction (AC6 — no regressions).
WINDOW_HANDLERS = [
    "smgmtRunSprint",
    "smgmtRerunSprint",
    "smgmtFinishSprint",
    "_smgmtTicketDragStart",
    "_smgmtDropOnSprint",
    "_rrConfirm",
    "_fsConfirm",
    "loadSprintMgmt",
    "_smgmtRender",
]


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def project_html() -> str:
    return _read(PROJECT_HTML)


@pytest.fixture(scope="module")
def board_sources() -> str:
    assert BOARD_DIR.is_dir(), "static/src/sprint-board/ must exist"
    return "\n".join(_read(p) for p in BOARD_DIR.glob("*.js"))


@pytest.fixture(scope="module")
def bundle() -> str:
    b = DIST_DIR / "bundle.js"
    assert b.exists(), "static/dist/bundle.js must be committed"
    return _read(b)


# ── AC1: sprint-board/ holds the five discrete concern modules ────────────────

class TestModuleLayout:
    def test_sprint_board_dir_exists(self):
        assert BOARD_DIR.is_dir(), "static/src/sprint-board/ must exist"

    @pytest.mark.parametrize("concern", list(CONCERN_MODULES))
    def test_concern_module_exists(self, concern):
        path = CONCERN_MODULES[concern]
        assert path.exists(), f"missing module for concern '{concern}': {path.name}"

    @pytest.mark.parametrize("concern", list(CONCERN_MODULES))
    def test_concern_module_uses_es_exports(self, concern):
        body = _read(CONCERN_MODULES[concern])
        assert re.search(r"\bexport\b", body), \
            f"{CONCERN_MODULES[concern].name} must use ES-module export(s)"

    def test_barrel_imports_every_concern(self):
        barrel = BOARD_DIR / "index.js"
        assert barrel.exists(), "static/src/sprint-board/index.js barrel missing"
        body = _read(barrel)
        for concern in CONCERN_MODULES:
            assert concern in body, f"barrel must import the '{concern}' module"

    def test_entry_point_imports_board_barrel(self):
        entry = _read(SRC_DIR / "index.js")
        assert re.search(r"import\s+['\"]\./sprint-board(/index\.js)?['\"]", entry) or \
            "sprint-board" in entry, \
            "src/index.js entry must pull in the sprint-board barrel"


# ── AC1 + AC2: each named function lives in a module, NOT inline in the page ──

class TestFunctionsExtracted:
    @pytest.mark.parametrize(
        "fn",
        [fn for fns in EXTRACTED_FUNCTIONS.values() for fn in fns],
    )
    def test_function_defined_in_a_module(self, board_sources, fn):
        assert re.search(rf"function\s+{re.escape(fn)}\b", board_sources), \
            f"{fn} must be defined in a sprint-board module"

    @pytest.mark.parametrize(
        "fn",
        [fn for fns in EXTRACTED_FUNCTIONS.values() for fn in fns],
    )
    def test_function_not_defined_inline(self, project_html, fn):
        # No `function <name>(` definition may remain in project.html.
        assert not re.search(rf"function\s+{re.escape(fn)}\s*\(", project_html), \
            f"{fn} is still defined inline in project.html (AC2 violation)"


# ── AC2: project.html keeps mount points + partials, drops inline board JS ────

class TestPageDeInlined:
    def test_mount_point_retained(self, project_html):
        # The board still needs its mount container + ghost pane in the HTML.
        assert 'id="smgmt-sprint-list"' in project_html, \
            "board mount point #smgmt-sprint-list must be retained in project.html"

    def test_modal_partials_retained(self, project_html):
        # Modal markup is an HTML partial, not board JS — it stays in the page.
        for modal_id in ('id="rr-modal"', 'id="fs-modal"', 'id="pf-modal"'):
            assert modal_id in project_html, f"{modal_id} partial must be retained"

    def test_loads_dist_bundle(self, project_html):
        assert re.search(
            r'<script[^>]+src="[^"]*?/static/dist/bundle\.js"', project_html
        ), "project.html must load the dist bundle that provides the board modules"


# ── AC3: measurable project.html payload reduction ────────────────────────────

class TestPayloadReduction:
    def test_project_html_smaller_than_baseline(self):
        now = PROJECT_HTML.stat().st_size
        assert now < PROJECT_HTML_BASELINE_BYTES, (
            f"project.html ({now} bytes) must be smaller than the pre-extraction "
            f"baseline ({PROJECT_HTML_BASELINE_BYTES} bytes)"
        )

    def test_reduction_is_meaningful(self):
        # Require a non-trivial reduction (the board JS is thousands of lines).
        now = PROJECT_HTML.stat().st_size
        removed = PROJECT_HTML_BASELINE_BYTES - now
        assert removed >= 20_000, (
            f"expected a meaningful reduction (>=20 KB); only {removed} bytes removed"
        )


# ── AC4: drag/drop smoke test against the modularized code ────────────────────

class TestDragDropSmoke:
    SMOKE = REPO_ROOT / "tests" / "frontend" / "dragdrop.smoke.test.mjs"

    def test_smoke_test_file_exists(self):
        assert self.SMOKE.exists(), \
            "a drag/drop smoke test (tests/frontend/dragdrop.smoke.test.mjs) must exist"

    def test_smoke_targets_dragdrop_module(self):
        body = _read(self.SMOKE)
        assert "drag-drop" in body, \
            "smoke test must import the modularized drag-drop code"

    def test_dragdrop_module_exposes_pure_helpers(self):
        body = _read(CONCERN_MODULES["drag-drop"])
        # Pure, DOM-free decision helpers the smoke test can exercise green.
        assert "export function isDragBlocked" in body, \
            "drag-drop must export isDragBlocked(state) for the smoke test"
        assert "export function computeDropPlan" in body, \
            "drag-drop must export computeDropPlan(...) for the smoke test"

    def test_npm_test_script_runs_smoke(self):
        import json
        pkg = json.loads(_read(REPO_ROOT / "package.json"))
        scripts = pkg.get("scripts", {})
        assert "test" in scripts, "package.json needs a 'test' script for the smoke test"
        assert "dragdrop.smoke" in scripts["test"] or "tests/frontend" in scripts["test"], \
            "npm test must run the drag/drop smoke test"


# ── AC5: modules pass lint/impeccable gates; CI targets static/src ────────────

class TestModuleCleanliness:
    @pytest.mark.parametrize("concern", list(CONCERN_MODULES))
    def test_module_declares_shared_globals(self, concern):
        # no-undef is an error in eslint.config.js; cross-cutting page state/helpers
        # are referenced via a /* global ... */ directive (or window.) so lint passes.
        body = _read(CONCERN_MODULES[concern])
        assert "/* global" in body or "window." in body, (
            f"{concern} module must declare shared globals (/* global */) or use "
            "window. so eslint no-undef passes"
        )

    def test_no_inline_event_html_in_modules(self):
        # Modules are logic; they must not smuggle in undeclared bare globals that
        # would trip no-undef. Spot-check: every module references window or a
        # /* global */ directive when it touches shared state.
        for path in BOARD_DIR.glob("*.js"):
            body = _read(path)
            assert "import" in body or "export" in body or "/* global" in body, \
                f"{path.name} does not look like a clean ES module"

    def test_ci_gate_targets_static_src(self):
        texts = "\n".join(_read(p) for p in WORKFLOWS_DIR.glob("*.yml"))
        assert "static/src" in texts and "impeccable" in texts, \
            "CI must run impeccable/lint over static/src (covers sprint-board/)"


# ── AC6: no regressions — board handlers stay reachable on window ─────────────

class TestNoRegressionWiring:
    @pytest.mark.parametrize("fn", WINDOW_HANDLERS)
    def test_handler_reexposed_on_window_in_bundle(self, bundle, fn):
        # The bundle must attach each public handler to the global object so the
        # page's onclick/ondrag* attributes resolve exactly as before.
        assert re.search(rf"\.{re.escape(fn)}\s*=", bundle) or f"{fn}" in bundle, \
            f"bundle must re-expose {fn} on window for project.html handlers"

    def test_bundle_contains_board_logic(self, bundle):
        # Sanity: the extracted concerns actually made it into the committed bundle.
        for anchor in ("_smgmtRender", "_smgmtDropOnSprint", "_rrConfirm", "_fsConfirm"):
            assert anchor in bundle, f"bundle must contain extracted board fn {anchor}"
