"""Acceptance tests for issue #796 — esbuild pipeline + log-panel module.

Each test is anchored to a specific acceptance criterion from the issue. The
build toolchain (Node/esbuild) is not assumed to be installed in the test
environment, so these tests verify the *pipeline definition and emitted
artifacts* at the file level — the dashboard is served from disk with no build
step, so the committed `static/dist/bundle.js` is what production actually runs.

AC map:
  AC1  static/src/ is the JS source root; static/dist/ holds the bundle
  AC2  `npm run build` produces dist/bundle.js (+.map) via esbuild, no framework
  AC3  `npm run watch` runs esbuild in watch mode
  AC4  project.html references dist/bundle.js, not a standalone log-panel script
  AC5  static/src/logpanel.js holds the tokenizer and is imported by the entry
  AC6  source maps enabled; .map present in static/dist/
  AC7  setup_machine docs updated with the build step (npm install / build)
  AC8  CI gate runs `npm run build` and fails on error
  AC9  impeccable detect + frontend lint execute over static/src/ in CI
  AC10 dashboard loads with zero JS console errors (static proxy: globals wired)
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

FRAMEWORK_TOKENS = ("react", "vue", "svelte", "angular", "preact", "solid-js")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def package_json():
    assert PACKAGE_JSON.exists(), "package.json must exist at the repo root"
    return json.loads(_read(PACKAGE_JSON))


@pytest.fixture(scope="module")
def project_html():
    return _read(PROJECT_HTML)


# ── AC1: static/src is the source root; static/dist holds the bundle ──────────

class TestSourceAndDistLayout:
    def test_src_directory_exists(self):
        assert SRC_DIR.is_dir(), "static/src/ must exist as the JS source root"

    def test_dist_directory_exists(self):
        assert DIST_DIR.is_dir(), "static/dist/ must exist for emitted bundles"

    def test_dist_holds_bundle(self):
        assert (DIST_DIR / "bundle.js").exists(), "static/dist/bundle.js missing"


# ── AC2: npm run build produces dist/bundle.js via esbuild, no framework ──────

class TestBuildScript:
    def test_build_script_defined(self, package_json):
        scripts = package_json.get("scripts", {})
        assert "build" in scripts, "package.json needs a 'build' script"

    def test_build_uses_esbuild(self, package_json):
        assert "esbuild" in package_json["scripts"]["build"], \
            "build script must invoke esbuild"

    def test_esbuild_is_a_dependency(self, package_json):
        deps = {**package_json.get("dependencies", {}),
                **package_json.get("devDependencies", {})}
        assert "esbuild" in deps, "esbuild must be a declared dependency"

    def test_no_framework_dependency(self, package_json):
        deps = {**package_json.get("dependencies", {}),
                **package_json.get("devDependencies", {})}
        for name in deps:
            assert not any(fw in name.lower() for fw in FRAMEWORK_TOKENS), \
                f"framework dependency '{name}' is out of scope for #796"

    def test_build_emits_bundle_to_dist(self, package_json):
        build = package_json["scripts"]["build"]
        assert "static/dist/bundle.js" in build, \
            "build must emit to static/dist/bundle.js"

    def test_bundle_artifact_committed(self):
        assert (DIST_DIR / "bundle.js").exists(), \
            "dist/bundle.js must be committed (production serves static from disk)"


# ── AC3: npm run watch runs esbuild in watch mode ─────────────────────────────

class TestWatchScript:
    def test_watch_script_defined(self, package_json):
        assert "watch" in package_json.get("scripts", {}), \
            "package.json needs a 'watch' script"

    def test_watch_uses_esbuild_watch_mode(self, package_json):
        watch = package_json["scripts"]["watch"]
        assert "esbuild" in watch and "--watch" in watch, \
            "watch script must run esbuild in --watch mode"


# ── AC4: project.html references the dist bundle, not a standalone script ──────

class TestProjectHtmlWiring:
    def test_references_dist_bundle(self, project_html):
        assert "/static/dist/bundle.js" in project_html, \
            "project.html must load the dist bundle"

    def test_loads_bundle_via_script_tag(self, project_html):
        assert re.search(r'<script[^>]+src="[^"]*?/static/dist/bundle\.js"',
                         project_html), \
            "project.html must include a <script src> for the dist bundle"

    def test_no_standalone_logcolorize_script_tag(self, project_html):
        # The standalone log-panel script load is replaced by the bundle.
        assert not re.search(r'<script[^>]+src="[^"]*?/static/log-colorize\.js"',
                             project_html), \
            "project.html must not load the standalone log-colorize.js script"


# ── AC5: logpanel.js holds the tokenizer and is imported by the entry ─────────

class TestLogpanelModule:
    def test_logpanel_module_exists(self):
        assert (SRC_DIR / "logpanel.js").exists(), "static/src/logpanel.js missing"

    def test_logpanel_contains_tokenizer(self):
        body = _read(SRC_DIR / "logpanel.js")
        assert "colorizeLogLine" in body, "logpanel.js must contain colorizeLogLine"
        assert "escapeLogHtml" in body, "logpanel.js must contain escapeLogHtml"

    def test_logpanel_uses_es_module_exports(self):
        body = _read(SRC_DIR / "logpanel.js")
        assert re.search(r'\bexport\b', body), \
            "logpanel.js must use ES-module export(s)"

    def test_entry_point_exists(self):
        assert (SRC_DIR / "index.js").exists(), \
            "static/src/index.js entry point missing"

    def test_entry_imports_logpanel(self):
        entry = _read(SRC_DIR / "index.js")
        assert re.search(r'import .*from\s+[\'"]\./logpanel(\.js)?[\'"]', entry, re.DOTALL), \
            "entry point must import ./logpanel.js"

    def test_build_entry_is_index(self, package_json):
        build = package_json["scripts"]["build"]
        assert "static/src/index.js" in build, \
            "build must use static/src/index.js as the entry point"


# ── AC6: source maps enabled; .map present in static/dist/ ────────────────────

class TestSourceMaps:
    def test_build_enables_sourcemap(self, package_json):
        assert "--sourcemap" in package_json["scripts"]["build"], \
            "build script must enable --sourcemap"

    def test_map_file_present(self):
        assert (DIST_DIR / "bundle.js.map").exists(), \
            "static/dist/bundle.js.map must be present"

    def test_map_is_valid_sourcemap(self):
        data = json.loads(_read(DIST_DIR / "bundle.js.map"))
        assert data.get("version") == 3, "source map must be v3"
        assert "mappings" in data, "source map must carry a mappings field"
        assert any("logpanel" in s for s in data.get("sources", [])), \
            "source map must reference the logpanel source"

    def test_bundle_links_to_map(self):
        bundle = _read(DIST_DIR / "bundle.js")
        assert "sourceMappingURL=bundle.js.map" in bundle, \
            "bundle.js must link its source map"


# ── AC7: setup_machine docs updated with the build step ───────────────────────

class TestSetupMachineDocs:
    def test_setup_machine_mentions_npm_install(self):
        body = _read(SETUP_MACHINE)
        assert "npm install" in body, \
            "setup_machine.sh must document/run `npm install`"

    def test_setup_machine_mentions_npm_build(self):
        body = _read(SETUP_MACHINE)
        assert "npm run build" in body, \
            "setup_machine.sh must document/run `npm run build`"


# ── AC8 / AC9: CI gate runs build + impeccable detect + lint over static/src ──

def _frontend_ci_text() -> str:
    assert WORKFLOWS_DIR.is_dir(), ".github/workflows must exist"
    texts = [_read(p) for p in WORKFLOWS_DIR.glob("*.yml")]
    texts += [_read(p) for p in WORKFLOWS_DIR.glob("*.yaml")]
    return "\n".join(texts)


class TestCiGate:
    def test_ci_runs_npm_build(self):
        assert "npm run build" in _frontend_ci_text(), \
            "a CI workflow must run `npm run build`"

    def test_ci_runs_impeccable_detect_over_src(self):
        ci = _frontend_ci_text()
        assert "impeccable" in ci and "detect" in ci, \
            "CI must run impeccable detect"
        assert "static/src" in ci, \
            "impeccable detect / lint must target static/src"

    def test_ci_runs_frontend_lint(self):
        ci = _frontend_ci_text()
        assert "npm run lint" in ci or "eslint" in ci, \
            "CI must run a frontend lint over static/src"


# ── AC10: dashboard loads with zero JS console errors (static proxy) ──────────
#
# A pytest run cannot open a browser; the proxy is that the emitted bundle wires
# the exact globals project.html depends on, so no ReferenceError fires at load.

class TestNoConsoleErrorsProxy:
    REQUIRED_GLOBALS = ("colorizeLogLine", "escapeLogHtml", "extractRaw")

    def test_bundle_defines_required_globals(self):
        bundle = _read(DIST_DIR / "bundle.js")
        for name in self.REQUIRED_GLOBALS:
            assert name in bundle, f"bundle must define {name} for project.html"

    def test_entry_attaches_globals_to_window(self):
        entry = _read(SRC_DIR / "index.js")
        for name in self.REQUIRED_GLOBALS:
            assert name in entry, f"entry must expose {name} on the global object"
        assert "window" in entry, "entry must attach to window for the page"

    def test_bundle_preserves_chip_tokenization(self):
        # The page renders agent/issue chips; the tokenizer regex must survive.
        bundle = _read(DIST_DIR / "bundle.js")
        assert "log-chip" in bundle, "bundle must preserve chip markup"
        assert "coder" in bundle and "tester" in bundle, \
            "bundle must preserve agent-name tokenization"
