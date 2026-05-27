"""Tests for issue #147: Auto-install pip deps and move requirements.txt to repo root.

Verifies all 7 acceptance criteria:
  AC1 - requirements.txt moved to repo root; apps/dashboard/requirements.txt removed
  AC2 - start_prd.sh syncs pip from repo-root requirements.txt before uvicorn
  AC3 - start_uat.sh syncs pip from repo-root requirements.txt before uvicorn
  AC4 - setup_uat_env.sh references root-level path, not $UAT_DASHBOARD path
  AC5 - CLAUDE.md no longer references apps/dashboard/requirements.txt
  AC6 - README.md and .commander/README.md pip install lines use root path
  AC7 - No file in the repo still references apps/dashboard/requirements.txt
"""
import subprocess
from pathlib import Path

import pytest

DASHBOARD_DIR = Path(__file__).parent.parent
REPO_ROOT = DASHBOARD_DIR.parent.parent


# ---------------------------------------------------------------------------
# AC1 — requirements.txt moved to repo root
# ---------------------------------------------------------------------------

class TestAC1RequirementsMoved:
    def test_requirements_exists_at_repo_root(self):
        """AC1: requirements.txt must exist at the repo root."""
        assert (REPO_ROOT / "requirements.txt").is_file(), \
            "requirements.txt not found at repo root"

    def test_requirements_removed_from_dashboard(self):
        """AC1: requirements.txt must NOT exist at apps/dashboard/requirements.txt."""
        assert not (REPO_ROOT / "apps" / "dashboard" / "requirements.txt").exists(), \
            "requirements.txt still found at apps/dashboard/requirements.txt — should be removed"


# ---------------------------------------------------------------------------
# AC2 — start_prd.sh syncs pip before launching uvicorn
# ---------------------------------------------------------------------------

class TestAC2StartPrdSync:
    @pytest.fixture(scope="class")
    def prd_script(self):
        return (REPO_ROOT / "scripts" / "start_prd.sh").read_text()

    def test_prd_prints_syncing_message(self, prd_script):
        """AC2: start_prd.sh must print a 'Syncing pip requirements' status line."""
        assert "Syncing pip requirements" in prd_script, \
            "start_prd.sh missing 'Syncing pip requirements' status message"

    def test_prd_runs_pip_install_quiet(self, prd_script):
        """AC2: start_prd.sh must run pip install --quiet."""
        assert "pip" in prd_script and "install" in prd_script and "--quiet" in prd_script, \
            "start_prd.sh missing 'pip install --quiet'"

    def test_prd_references_repo_root_requirements(self, prd_script):
        """AC2: start_prd.sh pip install must reference a root-level requirements.txt."""
        # Should contain $REPO_ROOT/requirements.txt (not apps/dashboard)
        assert "apps/dashboard/requirements" not in prd_script, \
            "start_prd.sh still references apps/dashboard/requirements.txt"
        assert "requirements.txt" in prd_script, \
            "start_prd.sh does not reference requirements.txt at all"

    def test_prd_pip_before_uvicorn(self, prd_script):
        """AC2: pip install must appear before uvicorn in start_prd.sh."""
        pip_pos = prd_script.find("pip")
        uvicorn_pos = prd_script.find("uvicorn")
        assert pip_pos != -1 and uvicorn_pos != -1, \
            "start_prd.sh must contain both pip and uvicorn invocations"
        assert pip_pos < uvicorn_pos, \
            "pip install must appear before uvicorn launch in start_prd.sh"


# ---------------------------------------------------------------------------
# AC3 — start_uat.sh syncs pip before launching uvicorn
# ---------------------------------------------------------------------------

class TestAC3StartUatSync:
    @pytest.fixture(scope="class")
    def uat_script(self):
        return (REPO_ROOT / "scripts" / "start_uat.sh").read_text()

    def test_uat_prints_syncing_message(self, uat_script):
        """AC3: start_uat.sh must print a 'Syncing pip requirements' status line."""
        assert "Syncing pip requirements" in uat_script, \
            "start_uat.sh missing 'Syncing pip requirements' status message"

    def test_uat_runs_pip_install_quiet(self, uat_script):
        """AC3: start_uat.sh must run pip install --quiet."""
        assert "pip" in uat_script and "install" in uat_script and "--quiet" in uat_script, \
            "start_uat.sh missing 'pip install --quiet'"

    def test_uat_references_repo_root_requirements(self, uat_script):
        """AC3: start_uat.sh pip install must reference a root-level requirements.txt, not apps/dashboard."""
        assert "apps/dashboard/requirements" not in uat_script, \
            "start_uat.sh still references apps/dashboard/requirements.txt"
        assert "requirements.txt" in uat_script, \
            "start_uat.sh does not reference requirements.txt at all"

    def test_uat_pip_before_uvicorn(self, uat_script):
        """AC3: pip install must appear before uvicorn in start_uat.sh."""
        pip_pos = uat_script.find("pip")
        uvicorn_pos = uat_script.find("uvicorn")
        assert pip_pos != -1 and uvicorn_pos != -1, \
            "start_uat.sh must contain both pip and uvicorn invocations"
        assert pip_pos < uvicorn_pos, \
            "pip install must appear before uvicorn launch in start_uat.sh"


# ---------------------------------------------------------------------------
# AC4 — setup_uat_env.sh references root-level path
# ---------------------------------------------------------------------------

class TestAC4SetupUatEnv:
    @pytest.fixture(scope="class")
    def setup_script(self):
        return (REPO_ROOT / "scripts" / "setup_uat_env.sh").read_text()

    def test_setup_not_reference_uat_dashboard_path(self, setup_script):
        """AC4: setup_uat_env.sh must not install from $UAT_DASHBOARD/requirements.txt."""
        assert "UAT_DASHBOARD/requirements.txt" not in setup_script and \
               "uat_dashboard/requirements.txt" not in setup_script.lower(), \
            "setup_uat_env.sh still references $UAT_DASHBOARD/requirements.txt"

    def test_setup_references_uat_dir_requirements(self, setup_script):
        """AC4: setup_uat_env.sh must install from the UAT repo root requirements.txt."""
        # Should reference $UAT_DIR/requirements.txt where UAT_DIR is the clone root
        assert "UAT_DIR/requirements.txt" in setup_script, \
            "setup_uat_env.sh does not reference $UAT_DIR/requirements.txt (repo root)"


# ---------------------------------------------------------------------------
# AC5 — CLAUDE.md updated to root path
# ---------------------------------------------------------------------------

class TestAC5ClaudeMd:
    @pytest.fixture(scope="class")
    def claude_md(self):
        return (REPO_ROOT / "CLAUDE.md").read_text()

    def test_claude_md_no_old_requirements_path(self, claude_md):
        """AC5: CLAUDE.md must not reference apps/dashboard/requirements.txt."""
        assert "apps/dashboard/requirements.txt" not in claude_md, \
            "CLAUDE.md still contains 'apps/dashboard/requirements.txt'"

    def test_claude_md_references_root_requirements(self, claude_md):
        """AC5: CLAUDE.md pip install instruction must use root-level requirements.txt."""
        assert "pip install -r requirements.txt" in claude_md, \
            "CLAUDE.md does not contain root-level 'pip install -r requirements.txt'"


# ---------------------------------------------------------------------------
# AC6 — README.md and .commander/README.md updated
# ---------------------------------------------------------------------------

class TestAC6ReadmesUpdated:
    @pytest.fixture(scope="class")
    def readme_text(self):
        return (REPO_ROOT / "README.md").read_text()

    @pytest.fixture(scope="class")
    def commander_readme_text(self):
        path = REPO_ROOT / ".commander" / "README.md"
        if not path.exists():
            pytest.skip(".commander/README.md does not exist")
        return path.read_text()

    def test_readme_no_old_requirements_path(self, readme_text):
        """AC6: README.md must not reference apps/dashboard/requirements.txt."""
        assert "apps/dashboard/requirements.txt" not in readme_text, \
            "README.md still references apps/dashboard/requirements.txt"

    def test_readme_references_root_requirements(self, readme_text):
        """AC6: README.md pip install line must reference root-level requirements.txt."""
        assert "requirements.txt" in readme_text, \
            "README.md does not reference requirements.txt"

    def test_commander_readme_no_old_requirements_path(self, commander_readme_text):
        """AC6: .commander/README.md must not reference apps/dashboard/requirements.txt."""
        assert "apps/dashboard/requirements.txt" not in commander_readme_text, \
            ".commander/README.md still references apps/dashboard/requirements.txt"

    def test_commander_readme_references_root_requirements(self, commander_readme_text):
        """AC6: .commander/README.md pip install line must reference root-level requirements.txt."""
        assert "requirements.txt" in commander_readme_text, \
            ".commander/README.md does not reference requirements.txt"


# ---------------------------------------------------------------------------
# AC7 — No file in repo still references apps/dashboard/requirements.txt
# ---------------------------------------------------------------------------

class TestAC7NoStaleReferences:
    def test_no_stale_references_in_repo(self):
        """AC7: grep across repo must find zero references to apps/dashboard/requirements.txt."""
        this_test_file = Path(__file__).name
        result = subprocess.run(
            ["grep", "-r", "apps/dashboard/requirements", str(REPO_ROOT),
             "--include=*.sh", "--include=*.md", "--include=*.py",
             "--include=*.txt", "--include=*.yaml", "--include=*.yml",
             "--include=*.json", "--exclude-dir=.git", "--exclude-dir=__pycache__",
             f"--exclude={this_test_file}"],
            capture_output=True, text=True
        )
        matches = [line for line in result.stdout.splitlines()
                   if "apps/dashboard/requirements" in line and ".pyc" not in line]
        assert len(matches) == 0, \
            f"Found {len(matches)} stale reference(s) to apps/dashboard/requirements:\n" + \
            "\n".join(matches)
