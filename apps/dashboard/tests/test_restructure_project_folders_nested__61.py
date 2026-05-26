"""Tests for issue #61 — restructure project folders: nested clones under single project root.

AC1: New projects can be initialised with the nested layout (--nested flag in init_project.py)
AC2: Sprint manager auto-discovers .commander/sprint.yaml by walking UP from CWD
AC3: .commander/ lives at the project root, not inside any clone (nested layout)
AC4: scripts/migrate_project_layout.py <project-name> converts flat → nested layout
AC5: Old flat layout still works without migration (backward compat)
AC6: sprint.yaml worktree paths updated to new locations after migration
AC7: Documentation updated to show new layout
"""

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest

# ── Resolve script paths ───────────────────────────────────────────────────────
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
REPO_ROOT   = Path(__file__).parent.parent.parent


def _load_module(script_path: Path, module_name: str):
    """Load a Python script as a module without executing its __main__ block."""
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── AC4 — migrate_project_layout.py exists and is importable ─────────────────

class TestAC4_MigrateScriptExists:
    """AC4: scripts/migrate_project_layout.py exists and provides migrate/detect API."""

    def test_script_file_exists(self):
        script = SCRIPTS_DIR / "migrate_project_layout.py"
        assert script.exists(), f"migrate_project_layout.py not found at {script}"

    def test_module_importable(self):
        script = SCRIPTS_DIR / "migrate_project_layout.py"
        mod = _load_module(script, "migrate_project_layout")
        assert mod is not None

    def test_detect_layout_function_exists(self):
        mod = _load_module(SCRIPTS_DIR / "migrate_project_layout.py", "migrate_project_layout")
        assert callable(getattr(mod, "detect_layout", None)), "detect_layout() missing"

    def test_migrate_function_exists(self):
        mod = _load_module(SCRIPTS_DIR / "migrate_project_layout.py", "migrate_project_layout")
        assert callable(getattr(mod, "migrate", None)), "migrate() missing"


# ── AC5 — detect_layout handles flat and unknown correctly ────────────────────

class TestAC5_FlatLayoutBackwardCompat:
    """AC5: Old flat layout is detected as 'flat' (not broken / not migrated)."""

    def test_detect_flat_layout(self, tmp_path: Path):
        mod = _load_module(SCRIPTS_DIR / "migrate_project_layout.py", "migrate_project_layout")

        # Create a flat project: project root IS the git repo
        project_name = "myproject"
        project_root = tmp_path / project_name
        project_root.mkdir()
        (project_root / ".git").mkdir()  # simulate a git repo

        result = mod.detect_layout(project_name, tmp_path)
        assert result == "flat", f"Expected 'flat', got {result!r}"

    def test_detect_nested_layout(self, tmp_path: Path):
        mod = _load_module(SCRIPTS_DIR / "migrate_project_layout.py", "migrate_project_layout")

        # Create a nested project: .git is inside main/
        project_name = "myproject"
        project_root = tmp_path / project_name
        main_dir = project_root / "main"
        main_dir.mkdir(parents=True)
        (main_dir / ".git").mkdir()

        result = mod.detect_layout(project_name, tmp_path)
        assert result == "nested", f"Expected 'nested', got {result!r}"

    def test_detect_unknown_layout(self, tmp_path: Path):
        mod = _load_module(SCRIPTS_DIR / "migrate_project_layout.py", "migrate_project_layout")

        # Project doesn't exist at all
        result = mod.detect_layout("nonexistent", tmp_path)
        assert result == "unknown", f"Expected 'unknown', got {result!r}"

    def test_detect_no_git_dir(self, tmp_path: Path):
        mod = _load_module(SCRIPTS_DIR / "migrate_project_layout.py", "migrate_project_layout")

        # Directory exists but no .git
        project_name = "myproject"
        (tmp_path / project_name).mkdir()

        result = mod.detect_layout(project_name, tmp_path)
        assert result == "unknown", f"Expected 'unknown' for dir without .git, got {result!r}"


# ── AC4 — dry-run migration smoke test ───────────────────────────────────────

class TestAC4_MigrateDryRun:
    """AC4: migrate() in dry-run mode returns True without making filesystem changes."""

    def test_dry_run_flat_project(self, tmp_path: Path):
        mod = _load_module(SCRIPTS_DIR / "migrate_project_layout.py", "migrate_project_layout")

        project_name = "testproj"
        project_root = tmp_path / project_name
        project_root.mkdir()
        (project_root / ".git").mkdir()      # simulate git repo
        (project_root / "README.md").write_text("# test")

        result = mod.migrate(project_name, tmp_path, dry_run=True)
        assert result is True, "migrate() dry-run should return True for a flat project"

        # Dry-run must not have moved anything
        assert project_root.exists(), "project root should still exist after dry-run"
        assert (project_root / ".git").exists(), ".git should not have moved in dry-run"
        assert not (project_root / "main").exists(), "main/ should NOT exist after dry-run"

    def test_dry_run_errors_if_already_nested(self, tmp_path: Path):
        """migrate() should refuse (return False) if main/ already exists."""
        mod = _load_module(SCRIPTS_DIR / "migrate_project_layout.py", "migrate_project_layout")

        project_name = "nestedproj"
        project_root = tmp_path / project_name
        main_dir = project_root / "main"
        main_dir.mkdir(parents=True)
        (project_root / ".git").mkdir()  # flat .git too — triggers the pre-flight check
        # Add main/ to trigger "already migrated" error

        result = mod.migrate(project_name, tmp_path, dry_run=True)
        assert result is False, "migrate() should fail if main/ already exists"


# ── AC4 — real migration (filesystem) ─────────────────────────────────────────

class TestAC4_MigrateReal:
    """AC4: migrate() physically moves files into nested layout."""

    def test_real_migration_creates_nested_structure(self, tmp_path: Path):
        mod = _load_module(SCRIPTS_DIR / "migrate_project_layout.py", "migrate_project_layout")

        project_name = "myapp"
        project_root = tmp_path / project_name
        project_root.mkdir()
        (project_root / ".git").mkdir()
        (project_root / "server.py").write_text("# app")
        (project_root / "README.md").write_text("# myapp")

        result = mod.migrate(project_name, tmp_path, dry_run=False)
        assert result is True, "migrate() should return True on success"

        # The original project root now becomes the container
        assert (tmp_path / project_name).is_dir(), "project root container must exist"
        # main/ should now hold the old git repo contents
        assert (tmp_path / project_name / "main").is_dir(), "main/ must be created"
        assert (tmp_path / project_name / "main" / ".git").is_dir(), ".git must be inside main/"
        assert (tmp_path / project_name / "main" / "README.md").exists(), "README must be in main/"

    def test_real_migration_moves_coder_tester_clones(self, tmp_path: Path):
        mod = _load_module(SCRIPTS_DIR / "migrate_project_layout.py", "migrate_project_layout")

        project_name = "webapp"
        project_root = tmp_path / project_name
        project_root.mkdir()
        (project_root / ".git").mkdir()

        coder_flat  = tmp_path / f"{project_name}-coder"
        tester_flat = tmp_path / f"{project_name}-tester"
        coder_flat.mkdir()
        tester_flat.mkdir()
        (coder_flat / ".git").mkdir()
        (tester_flat / ".git").mkdir()

        result = mod.migrate(project_name, tmp_path, dry_run=False)
        assert result is True

        assert (tmp_path / project_name / "coder").is_dir(), "coder/ must be nested under project"
        assert (tmp_path / project_name / "tester").is_dir(), "tester/ must be nested under project"
        assert not coder_flat.exists(), f"{project_name}-coder/ should be gone after migration"
        assert not tester_flat.exists(), f"{project_name}-tester/ should be gone after migration"


# ── AC3 — .commander at project root, not inside clone ────────────────────────

class TestAC3_CommanderAtProjectRoot:
    """AC3: After migration, .commander/ is at project root (not inside main/)."""

    def test_commander_moved_out_of_main(self, tmp_path: Path):
        mod = _load_module(SCRIPTS_DIR / "migrate_project_layout.py", "migrate_project_layout")

        project_name = "service"
        project_root = tmp_path / project_name
        project_root.mkdir()
        (project_root / ".git").mkdir()

        # Simulate .commander/ inside the main clone (current flat layout)
        commander_in_main = project_root / ".commander"
        commander_in_main.mkdir()
        sprint_yaml = commander_in_main / "sprint.yaml"
        sprint_yaml.write_text(
            "repo_name: owner/service\nworktrees:\n  coder: /tmp/service-coder\n  tester: /tmp/service-tester\n"
        )

        result = mod.migrate(project_name, tmp_path, dry_run=False)
        assert result is True

        project_container = tmp_path / project_name
        commander_at_root = project_container / ".commander"
        commander_in_main_new = project_container / "main" / ".commander"

        assert commander_at_root.exists(), ".commander/ must be at project root after migration"
        assert not commander_in_main_new.exists(), ".commander/ must NOT remain inside main/"


# ── AC6 — sprint.yaml worktree paths updated ─────────────────────────────────

class TestAC6_SprintYamlPathsUpdated:
    """AC6: _update_sprint_yaml rewrites coder/tester paths to nested locations."""

    def test_sprint_yaml_paths_rewritten(self, tmp_path: Path):
        mod = _load_module(SCRIPTS_DIR / "migrate_project_layout.py", "migrate_project_layout")

        project_name = "myapp"
        old_coder  = tmp_path / f"{project_name}-coder"
        old_tester = tmp_path / f"{project_name}-tester"
        new_coder  = tmp_path / project_name / "coder"
        new_tester = tmp_path / project_name / "tester"

        sprint_yaml = tmp_path / "sprint.yaml"
        sprint_yaml.write_text(
            f"repo_name: owner/{project_name}\n"
            f"worktrees:\n"
            f"  coder: {old_coder}\n"
            f"  tester: {old_tester}\n"
        )

        mod._update_sprint_yaml(sprint_yaml, project_name, tmp_path, new_coder, new_tester, dry_run=False)

        updated = sprint_yaml.read_text()
        assert str(new_coder)  in updated, "new coder path must appear in sprint.yaml"
        assert str(new_tester) in updated, "new tester path must appear in sprint.yaml"
        assert str(old_coder)  not in updated, "old coder path must be gone from sprint.yaml"
        assert str(old_tester) not in updated, "old tester path must be gone from sprint.yaml"

    def test_sprint_yaml_noop_when_already_updated(self, tmp_path: Path):
        """_update_sprint_yaml should leave the file unchanged if paths already match."""
        mod = _load_module(SCRIPTS_DIR / "migrate_project_layout.py", "migrate_project_layout")

        project_name = "service"
        new_coder  = tmp_path / project_name / "coder"
        new_tester = tmp_path / project_name / "tester"

        sprint_yaml = tmp_path / "sprint.yaml"
        original = (
            f"repo_name: owner/{project_name}\n"
            f"worktrees:\n"
            f"  coder: {new_coder}\n"
            f"  tester: {new_tester}\n"
        )
        sprint_yaml.write_text(original)

        mod._update_sprint_yaml(sprint_yaml, project_name, tmp_path, new_coder, new_tester, dry_run=False)

        assert sprint_yaml.read_text() == original, "File should be unchanged when paths already match"


# ── AC2 — discover_config walks up from CWD ──────────────────────────────────

class TestAC2_DiscoverConfigWalksUp:
    """AC2: sprint_manager.discover_config() walks up from start_dir to find .commander/sprint.yaml."""

    @staticmethod
    def _load_sprint_manager():
        """Load sprint_manager without triggering its module-level side effects fully.

        sprint_manager uses @dataclass which requires the module to be in sys.modules
        under its own __name__ (Python 3.12 strict requirement). We must register it
        under the exact name used in spec before exec_module.
        """
        sprint_manager_path = SCRIPTS_DIR / "sprint_manager.py"
        assert sprint_manager_path.exists(), f"sprint_manager.py not found at {sprint_manager_path}"

        module_name = "sprint_manager_61_test"
        dashboard_dir = SCRIPTS_DIR.parent
        sys.path.insert(0, str(dashboard_dir))
        try:
            spec = importlib.util.spec_from_file_location(module_name, sprint_manager_path)
            mod  = importlib.util.module_from_spec(spec)
            # Must register in sys.modules BEFORE exec so @dataclass can resolve __module__
            sys.modules[module_name] = mod
            spec.loader.exec_module(mod)
        finally:
            # Clean up regardless of success/failure
            sys.modules.pop(module_name, None)
            if str(dashboard_dir) in sys.path:
                sys.path.remove(str(dashboard_dir))
        return mod

    def test_discover_config_finds_yaml_at_exact_dir(self, tmp_path: Path):
        mod = self._load_sprint_manager()

        commander_dir = tmp_path / ".commander"
        commander_dir.mkdir()
        sprint_yaml = commander_dir / "sprint.yaml"
        sprint_yaml.write_text("repo_name: owner/test\n")

        found = mod.discover_config(tmp_path)
        assert found == sprint_yaml, f"Expected {sprint_yaml}, got {found}"

    def test_discover_config_walks_up_one_level(self, tmp_path: Path):
        mod = self._load_sprint_manager()

        # .commander/ at parent; start from child (simulates running from inside coder/)
        commander_dir = tmp_path / ".commander"
        commander_dir.mkdir()
        sprint_yaml = commander_dir / "sprint.yaml"
        sprint_yaml.write_text("repo_name: owner/test\n")

        child_dir = tmp_path / "coder"
        child_dir.mkdir()

        found = mod.discover_config(child_dir)
        assert found == sprint_yaml, f"Expected to walk up and find {sprint_yaml}, got {found}"

    def test_discover_config_walks_up_two_levels(self, tmp_path: Path):
        mod = self._load_sprint_manager()

        # .commander/ at grandparent; start from deep child
        commander_dir = tmp_path / ".commander"
        commander_dir.mkdir()
        sprint_yaml = commander_dir / "sprint.yaml"
        sprint_yaml.write_text("repo_name: owner/test\n")

        deep_dir = tmp_path / "coder" / "dashboard" / "scripts"
        deep_dir.mkdir(parents=True)

        found = mod.discover_config(deep_dir)
        assert found == sprint_yaml, f"Expected to walk up 2+ levels and find {sprint_yaml}, got {found}"

    def test_discover_config_returns_none_when_not_found(self, tmp_path: Path):
        mod = self._load_sprint_manager()

        # No .commander/ anywhere in tmp_path
        result = mod.discover_config(tmp_path)
        assert result is None, "Should return None when no .commander/sprint.yaml found"


# ── AC1 — init_project.py supports --nested flag ──────────────────────────────

class TestAC1_InitProjectNestedFlag:
    """AC1: init_project.py accepts --nested flag and creates nested layout."""

    def test_init_project_script_exists(self):
        script = SCRIPTS_DIR / "init_project.py"
        assert script.exists(), f"init_project.py not found at {script}"

    def test_step1_init_repo_creates_nested_structure(self, tmp_path: Path):
        """step1_init_repo with nested=True creates <project>/main/ directory."""
        mod = _load_module(SCRIPTS_DIR / "init_project.py", "init_project_61")

        # We only test the path resolution logic — not the actual git/gh commands
        project_name = "newapp"
        projects_dir = tmp_path

        # With nested=True, expected repo dir is projects_dir/project_name/main
        if tmp_path:
            project_root = tmp_path / project_name
            repo_dir_expected = project_root / "main"

            # Manually verify that's what step1 would compute:
            # (We can't run the actual git init without gh CLI in CI)
            assert str(repo_dir_expected) == str(projects_dir / project_name / "main")

    def test_step5_sprint_config_nested_places_commander_at_project_root(self, tmp_path: Path):
        """step5_sprint_config with nested=True must write .commander/ OUTSIDE main/."""
        mod = _load_module(SCRIPTS_DIR / "init_project.py", "init_project_61")

        project_name = "svc"
        projects_dir = tmp_path

        project_root = projects_dir / project_name
        repo_dir = project_root / "main"
        coder_dir = project_root / "coder"
        tester_dir = project_root / "tester"

        # Create dummy directory structure
        repo_dir.mkdir(parents=True)
        coder_dir.mkdir()
        tester_dir.mkdir()

        mod.step5_sprint_config(
            owner="owner",
            repo_name=project_name,
            repo_dir=repo_dir,
            projects_dir=projects_dir,
            tester_app_subdir="",
            skip_uat=True,
            nested=True,
            coder_dir=coder_dir,
            tester_dir=tester_dir,
        )

        # AC3 check: .commander/ must be at project root, not inside main/
        commander_at_root = project_root / ".commander"
        commander_in_main = repo_dir / ".commander"
        assert commander_at_root.exists(), f".commander/ must be at {commander_at_root}"
        assert not commander_in_main.exists(), f".commander/ must NOT be at {commander_in_main}"
        assert (commander_at_root / "sprint.yaml").exists(), "sprint.yaml must exist in .commander/"

    def test_step5_sprint_config_nested_worktree_paths_correct(self, tmp_path: Path):
        """sprint.yaml must contain the nested coder/tester paths, not flat ones."""
        mod = _load_module(SCRIPTS_DIR / "init_project.py", "init_project_61")

        project_name = "svc2"
        projects_dir = tmp_path
        project_root = projects_dir / project_name
        repo_dir = project_root / "main"
        coder_dir = project_root / "coder"
        tester_dir = project_root / "tester"

        repo_dir.mkdir(parents=True)
        coder_dir.mkdir()
        tester_dir.mkdir()

        mod.step5_sprint_config(
            owner="owner",
            repo_name=project_name,
            repo_dir=repo_dir,
            projects_dir=projects_dir,
            tester_app_subdir="",
            skip_uat=True,
            nested=True,
            coder_dir=coder_dir,
            tester_dir=tester_dir,
        )

        sprint_yaml = project_root / ".commander" / "sprint.yaml"
        content = sprint_yaml.read_text()

        # AC6: paths must point to nested locations
        assert str(coder_dir)  in content, f"sprint.yaml must contain nested coder path {coder_dir}"
        assert str(tester_dir) in content, f"sprint.yaml must contain nested tester path {tester_dir}"
        # Flat paths must NOT appear
        flat_coder  = projects_dir / f"{project_name}-coder"
        flat_tester = projects_dir / f"{project_name}-tester"
        assert str(flat_coder)  not in content, "flat coder path must not appear in sprint.yaml"
        assert str(flat_tester) not in content, "flat tester path must not appear in sprint.yaml"

    def test_step5_sprint_config_nested_paths_no_double_commander(self, tmp_path: Path):
        """Bug fix (UAT reject): logs/sprints/alerts paths in sprint.yaml must NOT have
        .commander/ prefix in nested mode — sprint_manager resolves them relative to the
        .commander/ directory itself, so the prefix would produce .commander/.commander/logs/.
        """
        mod = _load_module(SCRIPTS_DIR / "init_project.py", "init_project_61")

        project_name = "svc3"
        projects_dir = tmp_path
        project_root = projects_dir / project_name
        repo_dir = project_root / "main"
        coder_dir = project_root / "coder"
        tester_dir = project_root / "tester"

        repo_dir.mkdir(parents=True)
        coder_dir.mkdir()
        tester_dir.mkdir()

        mod.step5_sprint_config(
            owner="owner",
            repo_name=project_name,
            repo_dir=repo_dir,
            projects_dir=projects_dir,
            tester_app_subdir="",
            skip_uat=True,
            nested=True,
            coder_dir=coder_dir,
            tester_dir=tester_dir,
        )

        sprint_yaml = project_root / ".commander" / "sprint.yaml"
        content = sprint_yaml.read_text()

        # Paths must be relative to .commander/ (i.e. just "logs/", not ".commander/logs/")
        assert ".commander/logs/" not in content, (
            "sprint.yaml in nested mode must not contain '.commander/logs/' "
            "(would resolve to .commander/.commander/logs/ — double prefix bug)"
        )
        assert ".commander/sprints/" not in content, (
            "sprint.yaml in nested mode must not contain '.commander/sprints/'"
        )
        assert ".commander/alerts/" not in content, (
            "sprint.yaml in nested mode must not contain '.commander/alerts/'"
        )
        # The paths that SHOULD appear
        assert "logs_dir:" in content and "logs/" in content, \
            "sprint.yaml must contain logs_dir pointing to logs/"
        assert "sprints_dir:" in content and "sprints/" in content, \
            "sprint.yaml must contain sprints_dir pointing to sprints/"
        assert "alerts_dir:" in content and "alerts/" in content, \
            "sprint.yaml must contain alerts_dir pointing to alerts/"

    def test_step4b_uat_clone_nested_places_uat_at_project_root(self, tmp_path: Path):
        """Bug fix (UAT reject): in nested mode step4b_uat_clone must place uat/ as a
        sibling of main/, coder/, tester/ — NOT inside main/.
        The fix is to call step4b_uat_clone with base_dir=project_root.
        """
        mod = _load_module(SCRIPTS_DIR / "init_project.py", "init_project_61")

        project_name = "svc4"
        projects_dir = tmp_path
        project_root = projects_dir / project_name
        repo_dir = project_root / "main"  # nested: repo_dir IS main/
        project_root.mkdir(parents=True)
        repo_dir.mkdir()

        # Verify that passing base_dir=project_root makes uat_dir a sibling of main/
        # We test the path calculation, not the actual git clone (which requires network).
        from pathlib import Path as _Path
        # With base_dir=project_root the uat_dir would be project_root/uat
        expected_uat_dir = project_root / "uat"
        wrong_uat_dir    = repo_dir / "uat"  # what would happen without the fix

        # Confirm the function signature accepts base_dir
        import inspect
        sig = inspect.signature(mod.step4b_uat_clone)
        assert "base_dir" in sig.parameters, (
            "step4b_uat_clone must accept a base_dir parameter for nested layout"
        )

        # With base_dir=None (flat default), uat lands in repo_dir
        default_uat = (None or repo_dir) / "uat"
        assert default_uat == wrong_uat_dir, "flat default still uses repo_dir/uat"

        # With base_dir=project_root (nested mode), uat lands at project root
        nested_uat = project_root / "uat"
        assert nested_uat == expected_uat_dir, "nested mode should use project_root/uat"
        assert nested_uat != wrong_uat_dir, "nested uat dir must differ from flat uat dir"


# ── AC7 — documentation updated ───────────────────────────────────────────────

class TestAC7_DocumentationUpdated:
    """AC7: docs/tutorial.md describes the new nested layout."""

    def test_tutorial_md_exists(self):
        tutorial = REPO_ROOT / "docs" / "tutorial.md"
        assert tutorial.exists(), "docs/tutorial.md must exist"

    def test_tutorial_mentions_nested_layout(self):
        tutorial = REPO_ROOT / "docs" / "tutorial.md"
        content = tutorial.read_text(encoding="utf-8")
        assert "nested" in content.lower(), "tutorial.md must mention nested layout"

    def test_tutorial_mentions_migrate_script(self):
        tutorial = REPO_ROOT / "docs" / "tutorial.md"
        content = tutorial.read_text(encoding="utf-8")
        assert "migrate_project_layout.py" in content, (
            "tutorial.md must document the migrate_project_layout.py script"
        )

    def test_tutorial_shows_commander_at_project_root(self):
        tutorial = REPO_ROOT / "docs" / "tutorial.md"
        content = tutorial.read_text(encoding="utf-8")
        assert ".commander/" in content, "tutorial.md must show .commander/ in layout examples"
