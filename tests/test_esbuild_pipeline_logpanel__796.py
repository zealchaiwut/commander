"""Tester acceptance tests for issue #796 — esbuild pipeline + log-panel module.

This is a build-pipeline ticket. The dashboard is served from disk with no build
step, so the committed `static/dist/bundle.js` is what the running app loads. The
Node/esbuild toolchain is NOT installed in this environment, and the feature
branch is not deployed to the UAT server (which still runs the prior sprint
code), so these tests verify the *pipeline definition and the committed build
artifacts* at the filesystem level on the checked-out feature branch — the
correct surface for this ticket. HTTP-against-UAT would yield false negatives.

Independent of the coder's own test file; anchored one test per acceptance
criterion (Risk: MEDIUM → up to 2 per criterion).
"""
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
SRC_DIR = STATIC_DIR / "src"
DIST_DIR = STATIC_DIR / "dist"
PROJECT_HTML = STATIC_DIR / "project.html"
PACKAGE_JSON = REPO_ROOT / "package.json"
SETUP_MACHINE = REPO_ROOT / "scripts" / "setup_machine.sh"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

FRAMEWORKS = ("react", "vue", "svelte", "angular", "preact", "solid-js")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pkg():
    assert PACKAGE_JSON.exists(), "package.json must exist at the repo root"
    return json.loads(_read(PACKAGE_JSON))


@pytest.fixture(scope="module")
def project_html():
    return _read(PROJECT_HTML)


@pytest.fixture(scope="module")
def ci_text():
    assert WORKFLOWS_DIR.is_dir(), ".github/workflows/ must exist"
    parts = [_read(p) for p in WORKFLOWS_DIR.glob("*.yml")]
    parts += [_read(p) for p in WORKFLOWS_DIR.glob("*.yaml")]
    return "\n".join(parts)


# AC1 — static/src is the source root; static/dist holds the bundle
def test_esbuild_pipeline_logpanel__src_and_dist_layout():
    assert SRC_DIR.is_dir(), "static/src/ must exist as the JS source root"
    assert DIST_DIR.is_dir(), "static/dist/ must exist for the emitted bundle"
    assert (DIST_DIR / "bundle.js").exists(), "static/dist/bundle.js must be committed"


# AC2 — npm run build produces the bundle via esbuild, no framework dependency
def test_esbuild_pipeline_logpanel__build_script_uses_esbuild(pkg):
    build = pkg.get("scripts", {}).get("build", "")
    assert "esbuild" in build, "build script must invoke esbuild"
    assert "apps/dashboard/static/dist/bundle.js" in build, "build must emit to static/dist/bundle.js"
    assert "apps/dashboard/static/src/index.js" in build, "build entry must be static/src/index.js"


def test_esbuild_pipeline_logpanel__no_framework_dependency(pkg):
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    assert "esbuild" in deps, "esbuild must be a declared dependency"
    for name in deps:
        assert not any(fw in name.lower() for fw in FRAMEWORKS), \
            f"framework dependency '{name}' is out of scope for #796"


# AC3 — npm run watch runs esbuild in watch mode
def test_esbuild_pipeline_logpanel__watch_script(pkg):
    watch = pkg.get("scripts", {}).get("watch", "")
    assert "esbuild" in watch and "--watch" in watch, \
        "watch script must run esbuild in --watch mode"


# AC4 — project.html loads the dist bundle; standalone log-panel script removed
def test_esbuild_pipeline_logpanel__html_loads_bundle(project_html):
    assert re.search(r'<script[^>]+src="[^"]*?/static/dist/bundle\.js"', project_html), \
        "project.html must load the dist bundle via a <script src>"


def test_esbuild_pipeline_logpanel__html_drops_standalone_script(project_html):
    assert not re.search(r'<script[^>]+src="[^"]*?/static/log-colorize\.js"', project_html), \
        "project.html must no longer load the standalone log-colorize.js script tag"


# AC5 — logpanel.js holds the tokenizer and is imported by the entry point
def test_esbuild_pipeline_logpanel__module_exists_and_exports():
    body = _read(SRC_DIR / "logpanel.js")
    assert "colorizeLogLine" in body and "escapeLogHtml" in body, \
        "logpanel.js must contain the tokenizer helpers"
    assert re.search(r'\bexport\b', body), "logpanel.js must use ES-module exports"


def test_esbuild_pipeline_logpanel__entry_imports_logpanel():
    entry = _read(SRC_DIR / "index.js")
    assert re.search(r'import .*from\s+[\'"]\./logpanel(\.js)?[\'"]', entry), \
        "entry point (index.js) must import ./logpanel.js"


# AC6 — source maps enabled; .map present and valid v3
def test_esbuild_pipeline_logpanel__sourcemap_enabled_and_present(pkg):
    assert "--sourcemap" in pkg["scripts"]["build"], "build must enable --sourcemap"
    mp = DIST_DIR / "bundle.js.map"
    assert mp.exists(), "static/dist/bundle.js.map must be present"
    data = json.loads(_read(mp))
    assert data.get("version") == 3, "source map must be v3"
    assert "mappings" in data, "source map must carry a mappings field"
    assert any("logpanel" in s for s in data.get("sources", [])), \
        "source map must reference the logpanel source"
    assert "sourceMappingURL=bundle.js.map" in _read(DIST_DIR / "bundle.js"), \
        "bundle.js must link its source map"


# AC7 — setup_machine docs updated with the build step
def test_esbuild_pipeline_logpanel__setup_machine_documents_build():
    body = _read(SETUP_MACHINE)
    assert "npm install" in body, "setup_machine.sh must document/run npm install"
    assert "npm run build" in body, "setup_machine.sh must document/run npm run build"


# AC8 — CI gate runs npm run build and fails on error
def test_esbuild_pipeline_logpanel__ci_runs_build(ci_text):
    assert "npm run build" in ci_text, "a CI workflow must run npm run build"


# AC9 — impeccable detect + frontend lint execute over static/src in CI
def test_esbuild_pipeline_logpanel__ci_runs_design_and_lint_gates(ci_text):
    assert "impeccable" in ci_text and "detect" in ci_text, "CI must run impeccable detect"
    assert "static/src" in ci_text, "design/lint gates must target static/src"
    assert "npm run lint" in ci_text or "eslint" in ci_text, "CI must run a frontend lint"


# AC10 — dashboard loads with zero JS console errors (static proxy: the committed
# bundle re-exposes exactly the globals project.html depends on).
def test_esbuild_pipeline_logpanel__bundle_wires_required_globals():
    bundle = _read(DIST_DIR / "bundle.js")
    for name in ("colorizeLogLine", "escapeLogHtml", "extractRaw"):
        assert name in bundle, f"bundle must define {name} for project.html"
    assert "root.colorizeLogLine" in bundle or "window" in bundle, \
        "bundle must attach helpers to the global object"


# Value-add: the committed dist artifact must genuinely reflect the source module
# (guards against a stale/stub bundle that diverges from static/src/logpanel.js).
def test_esbuild_pipeline_logpanel__bundle_matches_source_logic():
    src = _read(SRC_DIR / "logpanel.js")
    bundle = _read(DIST_DIR / "bundle.js")
    # Agent-name tokenization and chip markup must survive into the built bundle.
    for token in ("coder", "tester", "reviewer", "documenter", "estimator", "BA"):
        assert token in src and token in bundle, \
            f"agent token '{token}' must be present in both source and bundle"
    assert "log-chip" in bundle, "bundle must preserve chip markup from the source"
    assert "log-colorize" in bundle or "logpanel" in bundle, \
        "bundle must carry the module-origin comment from esbuild"
