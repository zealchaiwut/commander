"""Guard: sprint_repo.py / sync_projects_to_neon.py must stay export-only (issue #1695).

Neither module has a runtime caller — SQLite (apps/dashboard/db.py) is the
authoritative store for sprint lifecycle and the project registry lives in
projects.json (see docs/architecture/1_state-and-source-of-truth.md §1.4).
Their only legitimate caller is scripts/migrate_sprints_to_neon.py /
scripts/export_to_neon.py, run manually/offline.

This test statically scans dashboard/server runtime source (not tests/,
not scripts/) for an import of either module and fails if one appears,
so a future change doesn't silently reconnect runtime code against the
stale "Neon holds lifecycle" assumption these modules predate.

neon_db.py and models.py are NOT guarded here — both are legitimately
shared with the runtime settings KV path (settings_repo.py) when Neon is
enabled; see their module docstrings.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SPRINT_MANAGER_ORCHESTRATOR = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"

# Export-only modules — no runtime caller is allowed to import these.
FORBIDDEN_MODULES = frozenset({"sprint_repo", "sync_projects_to_neon"})


def _runtime_python_files() -> list[Path]:
    """Every .py file under apps/dashboard/ (server + routers), plus the
    sprint-manager orchestrator entrypoint that dispatches sprint runs."""
    files = sorted(DASHBOARD_DIR.rglob("*.py"))
    # Exclude anything living under a nested tests/ or venv-like directory,
    # should one ever appear inside apps/dashboard/.
    files = [f for f in files if "tests" not in f.parts and "venv" not in f.parts]
    files.append(SPRINT_MANAGER_ORCHESTRATOR)
    return files


def _imported_module_names(source: str) -> set[str]:
    """Return every top-level module name referenced by import/from-import
    statements in *source*, including dotted-path submodule imports
    (e.g. `services.sprint_manager.sprint_repo` -> {'sprint_repo', ...})."""
    names: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.update(alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.update(node.module.split("."))
            # `from services.sprint_manager import sprint_repo` — the forbidden
            # name can also show up as an imported attribute, not the module path.
            for alias in node.names:
                names.add(alias.name)
    return names


@pytest.mark.parametrize("path", _runtime_python_files(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_runtime_file_does_not_import_export_only_neon_modules(path: Path):
    source = path.read_text(encoding="utf-8")
    imported = _imported_module_names(source)
    hit = imported & FORBIDDEN_MODULES
    assert not hit, (
        f"{path.relative_to(REPO_ROOT)} imports export-only Neon module(s) {sorted(hit)} — "
        f"SQLite is the authoritative runtime store (see "
        f"docs/architecture/1_state-and-source-of-truth.md §1.4). If this is "
        f"intentional, update that doc and this test's scope, don't just delete it."
    )


def test_forbidden_modules_list_is_not_empty():
    # Guards against a future edit accidentally emptying the parametrize set.
    assert FORBIDDEN_MODULES == {"sprint_repo", "sync_projects_to_neon"}


def test_sprint_repo_still_has_no_runtime_caller_outside_migrate_script():
    """Sanity check on the exemption itself: sprint_repo's only legitimate
    caller is the migration script (plus tests), not another script that
    might run automatically."""
    hits: list[str] = []
    for path in sorted(REPO_ROOT.rglob("*.py")):
        if "tests" in path.parts or "venv" in path.parts:
            continue
        if path.name in ("sprint_repo.py",):
            continue
        source = path.read_text(encoding="utf-8")
        if "sprint_repo" in _imported_module_names(source):
            hits.append(str(path.relative_to(REPO_ROOT)))
    assert hits == ["scripts/migrate_sprints_to_neon.py"], (
        f"unexpected importers of sprint_repo: {hits}"
    )
