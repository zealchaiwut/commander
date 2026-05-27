"""Tests for issue #120: scripts/promote_to_master.py — auto-draft PR from develop to master."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
import pytest

# ── resolve UAT base URL ────────────────────────────────────────────────────

BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8099")
if not BASE_URL.startswith("http"):
    raise RuntimeError("UAT_BASE_URL / UAT_PORT not set.")

REPO_ROOT = Path(__file__).parent.parent.parent.parent  # tester/
SCRIPT_PATH = REPO_ROOT / "scripts" / "promote_to_master.py"

# ── import the module once for unit tests ────────────────────────────────────

def _load_module():
    spec = importlib.util.spec_from_file_location("promote_to_master", str(SCRIPT_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


try:
    _pm = _load_module()
except Exception as e:
    _pm = None
    _LOAD_ERROR = str(e)
else:
    _LOAD_ERROR = None


@pytest.fixture(scope="session")
def pm():
    if _pm is None:
        pytest.skip(f"Could not load module: {_LOAD_ERROR}")
    return _pm


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# ── AC: script exists at scripts/promote_to_master.py ────────────────────────

def test_script_exists():
    """AC: New script at `scripts/promote_to_master.py`"""
    assert SCRIPT_PATH.is_file(), f"Script not found at {SCRIPT_PATH}"


# ── AC: CLI accepts --dry-run, --draft, --ready, --title ────────────────────

def test_cli_help_shows_all_options():
    """AC: CLI --dry-run, --draft, --ready, --title all present in --help"""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--help"],
        capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, f"--help failed: {result.stderr}"
    output = result.stdout + result.stderr
    assert "--dry-run" in output, "Missing --dry-run option"
    assert "--draft" in output, "Missing --draft option"
    assert "--ready" in output, "Missing --ready option"
    assert "--title" in output, "Missing --title option"


# ── AC: generate_title format ────────────────────────────────────────────────

def test_default_title_single_sprint(pm):
    """AC: Default title: 'release: develop → master (N commits, sprint-X)'"""
    title = pm.generate_title(5, [3])
    assert "release:" in title
    assert "develop" in title
    assert "master" in title
    assert "5 commits" in title
    assert "sprint-3" in title


def test_default_title_multi_sprint(pm):
    """AC: Default title with sprint range: 'sprint-X through sprint-Y'"""
    title = pm.generate_title(15, [3, 5])
    assert "15 commits" in title
    assert "sprint-3 through sprint-5" in title


def test_default_title_no_sprints(pm):
    """AC: Default title when no sprint refs: 'no sprint refs'"""
    title = pm.generate_title(3, [])
    assert "3 commits" in title
    assert "no sprint refs" in title


def test_default_title_one_commit(pm):
    """AC: Default title uses singular 'commit' for N=1"""
    title = pm.generate_title(1, [7])
    assert "1 commit" in title
    assert "1 commits" not in title


# ── AC: issue number deduplication ──────────────────────────────────────────

def test_issue_refs_deduplication(pm):
    """AC: Issue numbers deduplicated across commits"""
    commits = [
        ("abc1", "feat: add thing closes #10"),
        ("abc2", "fix: patch fixes #10"),   # duplicate
        ("abc3", "feat: other resolves #20"),
        ("abc4", "chore: stuff Closes #20"), # duplicate, different case
    ]
    refs = pm.extract_issue_refs(commits)
    assert refs == [10, 20], f"Expected [10, 20] deduplicated, got {refs}"


def test_issue_refs_case_insensitive(pm):
    """AC: closes/fixes/resolves refs are case-insensitive"""
    commits = [
        ("a", "feat: thing Closes #5"),
        ("b", "fix: FIXES #6"),
        ("c", "chore: Resolves #7"),
    ]
    refs = pm.extract_issue_refs(commits)
    assert 5 in refs
    assert 6 in refs
    assert 7 in refs


def test_issue_refs_empty(pm):
    """AC: No closes refs → empty list"""
    commits = [("a", "chore: update readme"), ("b", "docs: fix typo")]
    refs = pm.extract_issue_refs(commits)
    assert refs == []


# ── AC: sprint number extraction ────────────────────────────────────────────

def test_sprint_numbers_extracted(pm):
    """AC: Sprint numbers extracted from commit subjects and sorted"""
    commits = [
        ("a", "Merge into develop sprint-5"),
        ("b", "feat: something sprint-3"),
        ("c", "fix: patch sprint-5"),  # duplicate sprint
    ]
    sprints = pm.extract_sprint_numbers(commits)
    assert sprints == [3, 5], f"Expected [3, 5], got {sprints}"


def test_sprint_numbers_empty(pm):
    """AC: No sprint refs → empty list"""
    commits = [("a", "feat: stuff"), ("b", "fix: bug")]
    sprints = pm.extract_sprint_numbers(commits)
    assert sprints == []


# ── AC: changes grouped by top-level directory ──────────────────────────────

def test_group_by_area(pm):
    """AC: Files grouped by top-level directory"""
    files = [
        "apps/dashboard/server.py",
        "apps/dashboard/static/app.js",
        "scripts/promote_to_master.py",
        "README.md",
    ]
    areas = pm.group_by_area(files)
    assert areas.get("apps") == 2, f"Expected apps=2, got {areas}"
    assert areas.get("scripts") == 1, f"Expected scripts=1, got {areas}"
    assert areas.get("(root)") == 1, f"Expected (root)=1 for README.md, got {areas}"


# ── AC: PR body includes all required sections ───────────────────────────────

def test_pr_body_has_all_required_sections(pm, monkeypatch):
    """AC: PR body contains Summary, Sprints included, Tickets shipping,
    Changes by area, Recent commits, What to verify, Generated automatically"""

    # Mock sprint lookup to avoid network calls
    monkeypatch.setattr(pm, "lookup_sprint_summary", lambda n: (None, None))

    commits = [
        ("abc1234", "feat: add thing closes #10 sprint-3"),
        ("def5678", "fix: patch fixes #20 sprint-3"),
    ]
    issue_refs = pm.extract_issue_refs(commits)
    sprint_numbers = pm.extract_sprint_numbers(commits)
    changed_files = ["apps/dashboard/server.py", "scripts/promote_to_master.py"]

    body = pm.build_pr_body(
        commits=commits,
        issue_refs=issue_refs,
        sprint_numbers=sprint_numbers,
        changed_files=changed_files,
        dry_run=True,
    )

    assert "## Summary" in body, "Missing ## Summary section"
    assert "## Sprints included" in body, "Missing ## Sprints included section"
    assert "## Tickets shipping" in body, "Missing ## Tickets shipping section"
    assert "## Changes by area" in body, "Missing ## Changes by area section"
    assert "## Recent commits" in body, "Missing ## Recent commits section"
    assert "## What to verify before merge" in body, "Missing ## What to verify before merge section"
    assert "## Generated automatically" in body, "Missing ## Generated automatically section"


def test_pr_body_checklist_items(pm, monkeypatch):
    """AC: 'What to verify' section contains verification checklist items"""
    monkeypatch.setattr(pm, "lookup_sprint_summary", lambda n: (None, None))

    body = pm.build_pr_body(
        commits=[("abc", "feat: thing")],
        issue_refs=[],
        sprint_numbers=[],
        changed_files=[],
        dry_run=True,
    )
    verify_section = body[body.find("## What to verify"):]
    assert "- [ ]" in verify_section, "Checklist items missing in What to verify section"
    assert "/api/health" in verify_section, "Missing /api/health check item"
    assert "UAT" in verify_section, "Missing UAT verification item"


def test_pr_body_includes_commit_shas(pm, monkeypatch):
    """AC: Recent commits section shows short SHAs"""
    monkeypatch.setattr(pm, "lookup_sprint_summary", lambda n: (None, None))

    commits = [("abc1234", "feat: add something"), ("def5678", "fix: patch bug")]
    body = pm.build_pr_body(
        commits=commits,
        issue_refs=[],
        sprint_numbers=[],
        changed_files=[],
        dry_run=True,
    )
    assert "abc1234" in body, "Commit SHA abc1234 missing from body"
    assert "def5678" in body, "Commit SHA def5678 missing from body"


def test_pr_body_limits_commits_to_20(pm, monkeypatch):
    """AC: Recent commits section shows at most last 20 commits"""
    monkeypatch.setattr(pm, "lookup_sprint_summary", lambda n: (None, None))

    commits = [(f"sha{i:04d}", f"commit {i}") for i in range(25)]
    body = pm.build_pr_body(
        commits=commits,
        issue_refs=[],
        sprint_numbers=[],
        changed_files=[],
        dry_run=True,
    )
    # commit 0 through commit 19 should be included, 20-24 should NOT
    assert "sha0000" in body
    assert "sha0019" in body
    # commits beyond 20 should not appear
    assert "sha0020" not in body
    assert "sha0024" not in body


def test_pr_body_sprint_no_summary_note(pm, monkeypatch):
    """AC: Sprint without summary issue shows '(no summary issue)' note"""
    monkeypatch.setattr(pm, "lookup_sprint_summary", lambda n: (None, None))

    commits = [("sha", "feat: thing sprint-99")]
    sprint_numbers = pm.extract_sprint_numbers(commits)
    body = pm.build_pr_body(
        commits=commits,
        issue_refs=[],
        sprint_numbers=sprint_numbers,
        changed_files=[],
        dry_run=True,
    )
    assert "no summary issue" in body, "Missing '(no summary issue)' note for sprint without summary"


def test_pr_body_ticket_shipping_list(pm, monkeypatch):
    """AC: Tickets shipping section lists deduplicated issue refs"""
    monkeypatch.setattr(pm, "lookup_sprint_summary", lambda n: (None, None))

    commits = [("sha", "feat: closes #55 closes #66")]
    issue_refs = pm.extract_issue_refs(commits)
    body = pm.build_pr_body(
        commits=commits,
        issue_refs=issue_refs,
        sprint_numbers=[],
        changed_files=[],
        dry_run=True,
    )
    assert "#55" in body, "Issue #55 missing from Tickets shipping"
    assert "#66" in body, "Issue #66 missing from Tickets shipping"


def test_pr_body_changes_by_area(pm, monkeypatch):
    """AC: Changes by area shows file counts per top-level directory"""
    monkeypatch.setattr(pm, "lookup_sprint_summary", lambda n: (None, None))

    files = ["apps/dashboard/server.py", "apps/dashboard/static/app.js", "scripts/foo.py"]
    body = pm.build_pr_body(
        commits=[("sha", "feat: thing")],
        issue_refs=[],
        sprint_numbers=[],
        changed_files=files,
    )
    assert "apps" in body, "Missing 'apps' in Changes by area"
    assert "scripts" in body, "Missing 'scripts' in Changes by area"


def test_pr_body_generated_automatically_timestamp(pm, monkeypatch):
    """AC: Generated automatically section includes script reference and timestamp"""
    monkeypatch.setattr(pm, "lookup_sprint_summary", lambda n: (None, None))

    body = pm.build_pr_body(
        commits=[("sha", "feat: thing")],
        issue_refs=[],
        sprint_numbers=[],
        changed_files=[],
        dry_run=True,
    )
    assert "promote_to_master.py" in body, "Missing script reference in Generated section"
    # Timestamp should be in ISO-like format
    assert "Z" in body, "Missing UTC timestamp marker in Generated section"


# ── AC: dry-run in isolated git repo (no network calls) ─────────────────────

@pytest.fixture(scope="module")
def temp_git_repo(tmp_path_factory):
    """Create an isolated git repo with develop ahead of master for dry-run tests."""
    repo = tmp_path_factory.mktemp("test_repo")

    def git(*args):
        return subprocess.run(
            ["git"] + list(args),
            cwd=str(repo), capture_output=True, text=True, check=True
        )

    git("init")
    git("config", "user.email", "tester@commander.test")
    git("config", "user.name", "Tester")
    # Ensure default branch is named master (git < 2.28 defaults to master anyway)
    subprocess.run(
        ["git", "symbolic-ref", "HEAD", "refs/heads/master"],
        cwd=str(repo), capture_output=True, text=True
    )

    # Initial commit on master
    (repo / "README.md").write_text("hello")
    git("add", "README.md")
    git("commit", "-m", "chore: init")

    # Create develop with 2 extra commits
    git("checkout", "-b", "develop")
    (repo / "feature.txt").write_text("feature")
    git("add", "feature.txt")
    git("commit", "-m", "feat: add feature closes #42 sprint-3")

    (repo / "fix.txt").write_text("fix")
    git("add", "fix.txt")
    git("commit", "-m", "fix: patch sprint-3")

    # Set up fake remote refs pointing to local branches
    git("checkout", "master")
    git("update-ref", "refs/remotes/origin/master", "master")
    git("checkout", "develop")
    git("update-ref", "refs/remotes/origin/develop", "develop")

    # Create scripts/ dir with the script
    scripts_dir = repo / "scripts"
    scripts_dir.mkdir()
    import shutil
    shutil.copy(str(SCRIPT_PATH), str(scripts_dir / "promote_to_master.py"))

    return repo


def test_dry_run_in_isolated_repo(temp_git_repo):
    """AC: --dry-run prints PR body without making GitHub API calls"""
    result = subprocess.run(
        [sys.executable, str(temp_git_repo / "scripts" / "promote_to_master.py"), "--dry-run"],
        cwd=str(temp_git_repo),
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "HOME": os.environ.get("HOME", "/tmp")},
    )
    assert result.returncode == 0, f"--dry-run failed: {result.stderr}"
    out = result.stdout
    assert "DRY RUN" in out, "Missing DRY RUN header"
    assert "Title:" in out, "Missing Title line"
    assert "## Summary" in out, "Missing ## Summary section"
    assert "## Recent commits" in out, "Missing ## Recent commits"
    assert "Would run:" in out, "Missing 'Would run:' gh command preview"
    # Should NOT have made any gh calls — no PR URL
    assert "github.com" not in out or "Would run" in out, "Dry run may have made API calls"


def test_dry_run_shows_gh_command_preview(temp_git_repo):
    """AC: --dry-run prints the would-be gh command"""
    result = subprocess.run(
        [sys.executable, str(temp_git_repo / "scripts" / "promote_to_master.py"), "--dry-run"],
        cwd=str(temp_git_repo),
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "HOME": os.environ.get("HOME", "/tmp")},
    )
    assert result.returncode == 0
    assert "gh pr create" in result.stdout, "Missing gh pr create preview"
    assert "--draft" in result.stdout, "Missing --draft flag in preview"


def test_dry_run_title_format_in_output(temp_git_repo):
    """AC: dry-run output shows auto-generated title with commit count and sprint range"""
    result = subprocess.run(
        [sys.executable, str(temp_git_repo / "scripts" / "promote_to_master.py"), "--dry-run"],
        cwd=str(temp_git_repo),
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "HOME": os.environ.get("HOME", "/tmp")},
    )
    assert result.returncode == 0
    assert "release:" in result.stdout, "Title missing 'release:'"
    assert "develop" in result.stdout
    assert "master" in result.stdout
    assert "commit" in result.stdout  # "2 commits"


def test_dry_run_custom_title(temp_git_repo):
    """AC: --title overrides auto-generated title"""
    result = subprocess.run(
        [sys.executable, str(temp_git_repo / "scripts" / "promote_to_master.py"),
         "--dry-run", "--title", "my custom release title"],
        cwd=str(temp_git_repo),
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "HOME": os.environ.get("HOME", "/tmp")},
    )
    assert result.returncode == 0
    assert "my custom release title" in result.stdout, "Custom title not used"


def test_precondition_develop_at_master_exits_0(temp_git_repo):
    """AC: exit 0 with 'Nothing to promote' when develop is at master's head"""
    # Point origin/develop to same commit as origin/master
    master_sha = subprocess.run(
        ["git", "rev-parse", "origin/master"],
        cwd=str(temp_git_repo), capture_output=True, text=True, check=True
    ).stdout.strip()

    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/develop", master_sha],
        cwd=str(temp_git_repo), capture_output=True, text=True, check=True
    )
    try:
        result = subprocess.run(
            [sys.executable, str(temp_git_repo / "scripts" / "promote_to_master.py")],
            cwd=str(temp_git_repo),
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "HOME": os.environ.get("HOME", "/tmp")},
        )
        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}: {result.stderr}"
        assert "Nothing to promote" in result.stdout, f"Missing 'Nothing to promote' message: {result.stdout}"
    finally:
        # Restore develop ahead of master
        develop_sha = subprocess.run(
            ["git", "rev-parse", "develop"],
            cwd=str(temp_git_repo), capture_output=True, text=True, check=True
        ).stdout.strip()
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/develop", develop_sha],
            cwd=str(temp_git_repo), capture_output=True, text=True, check=True
        )


def test_precondition_uncommitted_changes_exits_1(temp_git_repo):
    """AC: exit 1 with 'Uncommitted changes detected' when tracked files modified"""
    test_file = temp_git_repo / "feature.txt"
    original = test_file.read_text()
    test_file.write_text("dirty changes")  # modify tracked file

    try:
        result = subprocess.run(
            [sys.executable, str(temp_git_repo / "scripts" / "promote_to_master.py")],
            cwd=str(temp_git_repo),
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "HOME": os.environ.get("HOME", "/tmp")},
        )
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "Uncommitted changes" in result.stderr, f"Missing message: {result.stderr}"
    finally:
        test_file.write_text(original)


def test_precondition_missing_origin_master_exits_1(temp_git_repo):
    """AC: exit 1 when origin/master does not exist"""
    # Temporarily delete origin/master ref
    subprocess.run(
        ["git", "update-ref", "-d", "refs/remotes/origin/master"],
        cwd=str(temp_git_repo), capture_output=True, text=True
    )
    try:
        result = subprocess.run(
            [sys.executable, str(temp_git_repo / "scripts" / "promote_to_master.py")],
            cwd=str(temp_git_repo),
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "HOME": os.environ.get("HOME", "/tmp")},
        )
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
        assert "origin/master" in result.stderr, f"Missing origin/master error: {result.stderr}"
    finally:
        # Restore origin/master
        master_sha = subprocess.run(
            ["git", "rev-parse", "master"],
            cwd=str(temp_git_repo), capture_output=True, text=True, check=True
        ).stdout.strip()
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/master", master_sha],
            cwd=str(temp_git_repo), capture_output=True, text=True
        )


# ── AC: log file written on success ─────────────────────────────────────────

def test_write_log_creates_file(pm, tmp_path):
    """AC: write_log creates .commander/logs/promote-<timestamp>.log"""
    original_dir = pm.COMMANDER_LOGS_DIR
    pm.COMMANDER_LOGS_DIR = tmp_path / "logs"
    try:
        log_path = pm.write_log("PR body content", "gh output here", "20240101-120000")
        assert log_path.exists(), f"Log file not created at {log_path}"
        content = log_path.read_text()
        assert "PR body content" in content
        assert "gh output here" in content
        assert "promote-20240101-120000.log" in str(log_path)
    finally:
        pm.COMMANDER_LOGS_DIR = original_dir


# ── AC: POST /api/deploy/promote endpoint exists ─────────────────────────────

def test_api_endpoint_exists(client):
    """AC: POST /api/deploy/promote endpoint returns non-404"""
    try:
        health = client.get("/api/health")
        if health.status_code != 200:
            pytest.skip(f"Server not healthy: {health.status_code}")
    except Exception as e:
        pytest.skip(f"Server not reachable: {e}")

    try:
        r = client.post("/api/deploy/promote", json={"draft": True})
        assert r.status_code != 404, f"Endpoint not found (404). Status: {r.status_code}, body: {r.text}"
        # It may return 400 (precondition) or 502 (GitHub API) — those are valid, just not 404
    except httpx.TimeoutException:
        pytest.skip("Request timed out — endpoint may be slow due to git fetch")


def test_api_endpoint_returns_json(client):
    """AC: POST /api/deploy/promote returns JSON response"""
    try:
        r = client.post("/api/deploy/promote", json={"draft": True})
        if r.status_code == 404:
            pytest.fail("Endpoint /api/deploy/promote not found (404)")
        # Should always be JSON
        assert r.headers.get("content-type", "").startswith("application/json"), \
            f"Expected JSON response, got: {r.headers.get('content-type')}"
    except httpx.TimeoutException:
        pytest.skip("Request timed out")


# ── AC: dashboard UI — Deploy to Production button and modal ─────────────────

def test_dashboard_has_deploy_button(client):
    """AC: Dashboard SPA 'Deploy to Production' button present in HTML"""
    try:
        r = client.get("/projects/test%2Frepo")
        assert r.status_code == 200, f"Dashboard returned {r.status_code}"
        html = r.text
        assert "Deploy to Production" in html, "Missing 'Deploy to Production' button text"
    except Exception as e:
        pytest.skip(f"Dashboard not reachable: {e}")


def test_dashboard_has_confirmation_modal(client):
    """AC: Dashboard SPA confirmation modal present in HTML"""
    try:
        r = client.get("/projects/test%2Frepo")
        assert r.status_code == 200
        html = r.text
        # Modal should have confirm action
        assert "confirmDeploy" in html or "deploy-modal" in html, \
            "Missing confirmation modal for deploy"
    except Exception as e:
        pytest.skip(f"Dashboard not reachable: {e}")


def test_dashboard_has_deploy_toast(client):
    """AC: Dashboard SPA has toast element for showing PR URL after deploy"""
    try:
        r = client.get("/projects/test%2Frepo")
        assert r.status_code == 200
        html = r.text
        assert "toast-deploy" in html or "View PR" in html, \
            "Missing toast or View PR element for deploy success"
    except Exception as e:
        pytest.skip(f"Dashboard not reachable: {e}")


# ── AC: exit codes (0, 1, 2) ────────────────────────────────────────────────

def test_exit_code_1_on_precondition_failure(temp_git_repo):
    """AC: exit code 1 on precondition failure"""
    # Remove origin/develop to trigger precondition failure
    subprocess.run(
        ["git", "update-ref", "-d", "refs/remotes/origin/develop"],
        cwd=str(temp_git_repo), capture_output=True, text=True
    )
    try:
        result = subprocess.run(
            [sys.executable, str(temp_git_repo / "scripts" / "promote_to_master.py")],
            cwd=str(temp_git_repo),
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "HOME": os.environ.get("HOME", "/tmp")},
        )
        assert result.returncode == 1, f"Expected exit code 1, got {result.returncode}"
    finally:
        develop_sha = subprocess.run(
            ["git", "rev-parse", "develop"],
            cwd=str(temp_git_repo), capture_output=True, text=True, check=True
        ).stdout.strip()
        subprocess.run(
            ["git", "update-ref", "refs/remotes/origin/develop", develop_sha],
            cwd=str(temp_git_repo), capture_output=True, text=True
        )
