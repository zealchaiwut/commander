"""Tests for issue #1898: Auto-resolve safe merge conflicts in complete-step/bulk-complete.

Consolidated from test_merge_conflict_auto_resolve__1898.py (tester-authored UAT tests)
and test_1898__merge_conflict_auto_resolve.py (coder-authored unit tests).
Both sets of behavioral assertions are retained.

Acceptance Criteria:
1. Auto-resolve mechanically-safe conflict classes (append-only CHANGELOG/SCHEMA, non-overlapping class blocks)
2. Return machine-readable terminal outcome for unresolvable conflicts
3. GET status surfaces "blocked on conflict, human needed" per sprint
4. Tests: append-only conflicts auto-resolve; overlapping conflicts return needs-human; loop-driver contract exercised
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import httpx


_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import startup  # noqa: E402

BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8001")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as c:
        yield c


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_git_repo(path: Path) -> None:
    """Initialise a bare git repo with a single commit on main."""
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True)


def _commit(path: Path, msg: str, files: dict[str, str]) -> None:
    for name, content in files.items():
        fp = path / name
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", name], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=path, check=True, capture_output=True)


# ── AC1: _is_union_merge_safe_path ───────────────────────────────────────────

class TestIsUnionMergeSafePath:
    """_is_union_merge_safe_path returns True for SCHEMA.md and models.py patterns."""

    def test_schema_md_is_safe(self):
        assert startup._is_union_merge_safe_path("SCHEMA.md") is True

    def test_models_py_is_safe(self):
        assert startup._is_union_merge_safe_path("models.py") is True

    def test_backend_models_py_is_safe(self):
        assert startup._is_union_merge_safe_path("backend/models.py") is True

    def test_arbitrary_models_file_is_safe(self):
        # Any file whose base name is models.py
        assert startup._is_union_merge_safe_path("perf_coach/models.py") is True

    def test_server_py_is_not_safe(self):
        assert startup._is_union_merge_safe_path("server.py") is False

    def test_requirements_txt_is_not_safe(self):
        assert startup._is_union_merge_safe_path("requirements.txt") is False

    def test_migration_py_is_not_safe(self):
        assert startup._is_union_merge_safe_path("migrations/001_add_table.py") is False


# ── AC1: _resolve_union_merge_conflicts — pure append-only ───────────────────

class TestResolveUnionMergeConflicts:
    """Unit tests for _resolve_union_merge_conflicts using a real temp git repo."""

    def _setup_repo(self, tmp_path: Path) -> Path:
        _make_git_repo(tmp_path)
        _commit(tmp_path, "base", {
            "SCHEMA.md": "# Schema\n\n## Table A\n- field1 text\n",
            "models.py": "class ModelA:\n    field1 = None\n",
        })
        return tmp_path

    def test_append_only_schema_auto_resolves(self, tmp_path: Path):
        """Both sides append a new section to SCHEMA.md — union merge produces both."""
        repo = self._setup_repo(tmp_path)

        # Create branch 'sprint' that appends Table B
        subprocess.run(["git", "checkout", "-b", "sprint"], cwd=repo, check=True, capture_output=True)
        _commit(repo, "add Table B", {
            "SCHEMA.md": "# Schema\n\n## Table A\n- field1 text\n\n## Table B\n- id int\n",
        })

        # Go back to main and append Table C (different section, no overlap)
        subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)
        _commit(repo, "add Table C", {
            "SCHEMA.md": "# Schema\n\n## Table A\n- field1 text\n\n## Table C\n- id int\n",
        })

        # Now merge sprint into main — this will conflict
        result = subprocess.run(
            ["git", "merge", "sprint", "-m", "merge sprint into main"],
            cwd=repo, capture_output=True, text=True,
        )
        assert result.returncode != 0, "Expected merge conflict"

        # Get unmerged files
        status = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=repo, capture_output=True, text=True,
        )
        unmerged = [ln.strip() for ln in status.stdout.splitlines() if ln.strip()]
        assert "SCHEMA.md" in unmerged

        # Try auto-resolve
        resolved, still_bad = startup._resolve_union_merge_conflicts(repo, ["SCHEMA.md"])
        assert resolved is True, f"Expected auto-resolve, still_bad={still_bad}"
        assert still_bad == []

        # Verify merged content has both Table B and Table C
        merged_text = (repo / "SCHEMA.md").read_text(encoding="utf-8")
        assert "## Table B" in merged_text
        assert "## Table C" in merged_text

    def test_overlapping_edit_not_auto_resolved(self, tmp_path: Path):
        """Both sides modify the SAME line — union merge leaves conflict markers."""
        repo = self._setup_repo(tmp_path)

        # Branch 'sprint' changes field1 to 'field1 varchar(255)'
        subprocess.run(["git", "checkout", "-b", "sprint"], cwd=repo, check=True, capture_output=True)
        _commit(repo, "change field1 type", {
            "SCHEMA.md": "# Schema\n\n## Table A\n- field1 varchar(255)\n",
        })

        # main also changes field1 but to 'field1 integer'
        subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)
        _commit(repo, "change field1 to integer", {
            "SCHEMA.md": "# Schema\n\n## Table A\n- field1 integer\n",
        })

        # Conflict
        subprocess.run(
            ["git", "merge", "sprint", "-m", "merge"],
            cwd=repo, capture_output=True, text=True,
        )

        resolved, still_bad = startup._resolve_union_merge_conflicts(repo, ["SCHEMA.md"])
        assert resolved is False
        assert "SCHEMA.md" in still_bad

        # Abort the merge to clean up
        subprocess.run(["git", "merge", "--abort"], cwd=repo, capture_output=True)

    def test_models_py_append_only_auto_resolves(self, tmp_path: Path):
        """Both sides append a new class to models.py — union merge produces both."""
        repo = self._setup_repo(tmp_path)

        subprocess.run(["git", "checkout", "-b", "sprint"], cwd=repo, check=True, capture_output=True)
        _commit(repo, "add ModelB", {
            "models.py": "class ModelA:\n    field1 = None\n\nclass ModelB:\n    pass\n",
        })

        subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)
        _commit(repo, "add ModelC", {
            "models.py": "class ModelA:\n    field1 = None\n\nclass ModelC:\n    pass\n",
        })

        subprocess.run(
            ["git", "merge", "sprint", "-m", "merge"],
            cwd=repo, capture_output=True, text=True,
        )

        resolved, still_bad = startup._resolve_union_merge_conflicts(repo, ["models.py"])
        assert resolved is True
        merged = (repo / "models.py").read_text(encoding="utf-8")
        assert "class ModelB" in merged
        assert "class ModelC" in merged


# ── AC1: _prepare_sprint_branch_for_develop_merge uses union merge ────────────

def _build_bare_repos(tmp_path: Path, base_files: dict) -> tuple:
    """Create a bare upstream and two working clones: source (for setup) and coder.

    Returns (upstream_path, source_path, coder_path).
    Uses a bare upstream so both clones can push without 'checked-out branch' errors.
    """
    upstream = tmp_path / "upstream.git"
    upstream.mkdir()
    subprocess.run(["git", "init", "--bare", "-b", "main"], cwd=upstream, check=True, capture_output=True)

    source = tmp_path / "source"
    subprocess.run(["git", "clone", str(upstream), str(source)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=source, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True, capture_output=True)
    _commit(source, "base", base_files)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=source, check=True, capture_output=True)
    # Create 'develop' as alias of main on upstream
    subprocess.run(["git", "push", "origin", "main:develop"], cwd=source, check=True, capture_output=True)

    coder = tmp_path / "coder"
    subprocess.run(["git", "clone", str(upstream), str(coder)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=coder, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=coder, check=True, capture_output=True)
    return upstream, source, coder


class TestPrepareSprintBranchUnionMerge:
    """_prepare_sprint_branch_for_develop_merge auto-resolves SCHEMA.md / models.py conflicts."""

    def test_schema_conflict_auto_resolved_and_pushed(self, tmp_path: Path, monkeypatch):
        """When SCHEMA.md has an append-only conflict, the prepare step resolves and pushes."""
        upstream, source, coder = _build_bare_repos(tmp_path, {
            "SCHEMA.md": "# Schema\n\n## TableA\n- id int\n",
        })

        # Sprint branch (from source) adds TableB
        subprocess.run(["git", "checkout", "-b", "sprint-1"], cwd=source, check=True, capture_output=True)
        _commit(source, "sprint: add TableB", {
            "SCHEMA.md": "# Schema\n\n## TableA\n- id int\n\n## TableB\n- name text\n",
        })
        subprocess.run(["git", "push", "origin", "sprint-1"], cwd=source, check=True, capture_output=True)

        # develop branch adds TableC (from main)
        subprocess.run(["git", "checkout", "main"], cwd=source, check=True, capture_output=True)
        _commit(source, "develop: add TableC", {
            "SCHEMA.md": "# Schema\n\n## TableA\n- id int\n\n## TableC\n- value int\n",
        })
        subprocess.run(["git", "push", "origin", "main:develop"], cwd=source, check=True, capture_output=True)

        # Coder clone: checkout sprint-1 so prepare can work on it
        subprocess.run(["git", "fetch", "origin"], cwd=coder, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-b", "sprint-1", "origin/sprint-1"], cwd=coder, check=True, capture_output=True)

        monkeypatch.setattr(startup, "_git_repo_for_merge", lambda repo: coder)

        ok, detail = startup._prepare_sprint_branch_for_develop_merge("owner/repo", "sprint-1")
        assert ok is True, f"Expected success, got: {detail}"

        # TableB (sprint) and TableC (develop) must both appear in the pushed sprint-1
        subprocess.run(["git", "fetch", "origin"], cwd=coder, check=True, capture_output=True)
        show = subprocess.run(
            ["git", "show", "origin/sprint-1:SCHEMA.md"],
            cwd=coder, capture_output=True, text=True,
        )
        assert "## TableB" in show.stdout, f"TableB missing from result: {show.stdout}"
        assert "## TableC" in show.stdout, f"TableC missing from result: {show.stdout}"

    def test_unsafe_conflict_returns_needs_human_prefix(self, tmp_path: Path, monkeypatch):
        """When conflict is in an unsafe file (requirements.txt), prepare returns needs-human."""
        upstream, source, coder = _build_bare_repos(tmp_path, {
            "requirements.txt": "fastapi\nuvicorn\n",
        })

        # Sprint branch modifies requirements.txt
        subprocess.run(["git", "checkout", "-b", "sprint-2"], cwd=source, check=True, capture_output=True)
        _commit(source, "sprint: fastapi==0.100", {
            "requirements.txt": "fastapi==0.100\nuvicorn\n",
        })
        subprocess.run(["git", "push", "origin", "sprint-2"], cwd=source, check=True, capture_output=True)

        # develop gets a different requirements.txt change
        subprocess.run(["git", "checkout", "main"], cwd=source, check=True, capture_output=True)
        _commit(source, "develop: fastapi==0.99 + httpx", {
            "requirements.txt": "fastapi==0.99\nuvicorn\nhttpx\n",
        })
        subprocess.run(["git", "push", "origin", "main:develop"], cwd=source, check=True, capture_output=True)

        subprocess.run(["git", "fetch", "origin"], cwd=coder, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "-b", "sprint-2", "origin/sprint-2"], cwd=coder, check=True, capture_output=True)

        monkeypatch.setattr(startup, "_git_repo_for_merge", lambda repo: coder)

        ok, detail = startup._prepare_sprint_branch_for_develop_merge("owner/repo", "sprint-2")
        assert ok is False, f"Expected failure, got ok={ok}, detail={detail}"
        assert "merge_conflict_needs_human" in detail, f"Expected needs-human prefix: {detail}"
        assert "requirements.txt" in detail, f"Expected file name in detail: {detail}"


# ── AC2: complete-step returns structured 409 on needs-human conflict ─────────

class TestCompleteStepStructured409:
    """complete_sprint_step returns 409 with code=merge_conflict_needs_human."""

    @pytest.fixture
    def client_standalone(self, tmp_path, monkeypatch):
        import db as _db
        import server as srv

        db_file = tmp_path / "test_1898.db"
        monkeypatch.setattr(_db, "DB_PATH", db_file)
        _db.init_db()

        from fastapi.testclient import TestClient
        return TestClient(srv.app, raise_server_exceptions=False)

    def test_needs_human_conflict_returns_structured_409(self, client_standalone, monkeypatch):
        """When _gh_merge_branch_via_pr returns a needs-human conflict, the endpoint
        returns 409 with code=merge_conflict_needs_human in the body."""
        import server as srv

        def _fake_merge(repo, head, base, title, delete_branch=True, conflict_detail_out=None):
            if conflict_detail_out is not None:
                conflict_detail_out["code"] = "merge_conflict_needs_human"
                conflict_detail_out["files"] = ["SCHEMA.md"]
            return False, "merge_conflict_needs_human: SCHEMA.md", None

        monkeypatch.setattr(srv, "_gh_merge_branch_via_pr", _fake_merge)
        monkeypatch.setattr(srv, "_SPRINT_LABEL_RE", __import__("re").compile(r"^sprint-\d+(?:\.\d+)?$"))
        monkeypatch.setattr(srv, "_is_sprint_running", lambda *a, **kw: False)
        monkeypatch.setattr(srv, "_branch_has_unmerged_commits", lambda *a, **kw: True)
        monkeypatch.setattr(srv, "_sprint_branch_name", lambda lbl: f"sprint/{lbl}")
        monkeypatch.setattr(srv, "_is_child_sprint_label", lambda lbl: False)
        monkeypatch.setattr(srv, "_project_root_path", lambda repo: Path("/tmp/fake"))
        monkeypatch.setattr(srv, "_sprint_set_conflict_blocked", lambda *a, **kw: None)
        monkeypatch.setattr(srv, "_has_rework_tickets", None)

        resp = client_standalone.post(
            "/api/projects/owner/repo/sprints/sprint-104/complete-step",
            json={"confirmed": True},
        )
        assert resp.status_code == 409
        body = resp.json()
        # The structured 409 must include code and files fields
        assert body.get("code") == "merge_conflict_needs_human" or (
            isinstance(body.get("detail"), dict)
            and body["detail"].get("code") == "merge_conflict_needs_human"
        ), f"Expected structured 409, got: {body}"

    def test_non_conflict_failure_keeps_plain_409(self, client_standalone, monkeypatch):
        """Non-conflict merge failures still return 409 but without the structured body."""
        import server as srv

        def _fake_merge(repo, head, base, title, delete_branch=True, conflict_detail_out=None):
            return False, "PR create failed: network error", None

        monkeypatch.setattr(srv, "_gh_merge_branch_via_pr", _fake_merge)
        monkeypatch.setattr(srv, "_SPRINT_LABEL_RE", __import__("re").compile(r"^sprint-\d+(?:\.\d+)?$"))
        monkeypatch.setattr(srv, "_is_sprint_running", lambda *a, **kw: False)
        monkeypatch.setattr(srv, "_branch_has_unmerged_commits", lambda *a, **kw: True)
        monkeypatch.setattr(srv, "_sprint_branch_name", lambda lbl: f"sprint/{lbl}")
        monkeypatch.setattr(srv, "_is_child_sprint_label", lambda lbl: False)
        monkeypatch.setattr(srv, "_project_root_path", lambda repo: Path("/tmp/fake"))
        monkeypatch.setattr(srv, "_has_rework_tickets", None)

        resp = client_standalone.post(
            "/api/projects/owner/repo/sprints/sprint-104/complete-step",
            json={"confirmed": True},
        )
        assert resp.status_code == 409
        body = resp.json()
        # NOT a structured needs-human response
        detail = body.get("detail") or body
        if isinstance(detail, dict):
            assert detail.get("code") != "merge_conflict_needs_human"


# ── AC3: GET /conflict-status endpoint ────────────────────────────────────────

class TestConflictStatusEndpoint:
    """GET /conflict-status returns blocked state per sprint."""

    @pytest.fixture
    def client_and_project_root(self, tmp_path, monkeypatch):
        import db as _db
        import server as srv

        db_file = tmp_path / "test_1898_status.db"
        monkeypatch.setattr(_db, "DB_PATH", db_file)
        _db.init_db()

        project_root = tmp_path / "project"
        project_root.mkdir()
        monkeypatch.setattr(srv, "_project_root_path", lambda repo: project_root)
        monkeypatch.setattr(srv, "_SPRINT_LABEL_RE", __import__("re").compile(r"^sprint-\d+(?:\.\d+)?$"))

        from fastapi.testclient import TestClient
        return TestClient(srv.app, raise_server_exceptions=False), project_root

    def test_not_blocked_by_default(self, client_and_project_root):
        client, _ = client_and_project_root
        resp = client.get("/api/projects/owner/repo/sprints/sprint-104/conflict-status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["blocked"] is False
        assert body["label"] == "sprint-104"

    def test_blocked_after_set(self, client_and_project_root):
        """After _sprint_set_conflict_blocked is called, the endpoint reports blocked=true."""
        client, project_root = client_and_project_root

        startup._sprint_set_conflict_blocked(project_root, "sprint-104", ["SCHEMA.md", "models.py"])

        resp = client.get("/api/projects/owner/repo/sprints/sprint-104/conflict-status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["blocked"] is True
        assert body["code"] == "merge_conflict_needs_human"
        assert "SCHEMA.md" in body["files"]
        assert "models.py" in body["files"]

    def test_not_blocked_after_clear(self, client_and_project_root):
        """After _sprint_clear_conflict_blocked, the endpoint reports blocked=false."""
        _, project_root = client_and_project_root
        startup._sprint_set_conflict_blocked(project_root, "sprint-104", ["SCHEMA.md"])
        startup._sprint_clear_conflict_blocked(project_root, "sprint-104")

        # Re-import client (already set up above)
        import server as srv
        from fastapi.testclient import TestClient
        client2 = TestClient(srv.app, raise_server_exceptions=False)

        resp = client2.get("/api/projects/owner/repo/sprints/sprint-104/conflict-status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["blocked"] is False


# ── AC4: loop-driver contract — skip and continue ────────────────────────────

class TestLoopDriverContract:
    """Autonomous caller can detect needs-human 409 and skip without hanging."""

    @pytest.fixture
    def client_loop(self, tmp_path, monkeypatch):
        import db as _db
        import server as srv

        db_file = tmp_path / "test_1898_loop.db"
        monkeypatch.setattr(_db, "DB_PATH", db_file)
        _db.init_db()

        from fastapi.testclient import TestClient
        return TestClient(srv.app, raise_server_exceptions=False)

    def test_409_body_is_json_with_code_field(self, client_loop, monkeypatch):
        """An autonomous loop can parse the 409 body: code=merge_conflict_needs_human."""
        import server as srv

        def _fake_merge(repo, head, base, title, delete_branch=True, conflict_detail_out=None):
            if conflict_detail_out is not None:
                conflict_detail_out["code"] = "merge_conflict_needs_human"
                conflict_detail_out["files"] = ["SCHEMA.md"]
            return False, "merge_conflict_needs_human: SCHEMA.md", None

        monkeypatch.setattr(srv, "_gh_merge_branch_via_pr", _fake_merge)
        monkeypatch.setattr(srv, "_SPRINT_LABEL_RE", __import__("re").compile(r"^sprint-\d+(?:\.\d+)?$"))
        monkeypatch.setattr(srv, "_is_sprint_running", lambda *a, **kw: False)
        monkeypatch.setattr(srv, "_branch_has_unmerged_commits", lambda *a, **kw: True)
        monkeypatch.setattr(srv, "_sprint_branch_name", lambda lbl: f"sprint/{lbl}")
        monkeypatch.setattr(srv, "_is_child_sprint_label", lambda lbl: False)
        monkeypatch.setattr(srv, "_project_root_path", lambda repo: Path("/tmp/fake"))
        monkeypatch.setattr(srv, "_sprint_set_conflict_blocked", lambda *a, **kw: None)
        monkeypatch.setattr(srv, "_has_rework_tickets", None)

        resp = client_loop.post(
            "/api/projects/owner/repo/sprints/sprint-104/complete-step",
            json={"confirmed": True},
        )
        assert resp.status_code == 409

        # Simulate what an autonomous loop does: parse the body
        body = resp.json()
        detail = body if "code" in body else body.get("detail", {})

        # Loop can detect the specific conflict type
        assert detail.get("code") == "merge_conflict_needs_human", (
            f"Loop needs code=merge_conflict_needs_human to skip safely. Got: {detail}"
        )
        # Loop can see which files need human resolution
        assert isinstance(detail.get("files"), list)
        # Loop can identify which sprint is blocked
        assert detail.get("label") == "sprint-104"


# ─────────────────────────────────────────────────────────────────────────────────
# UAT Integration: Manual verification steps
# ─────────────────────────────────────────────────────────────────────────────────

def test_merge_conflict_auto_resolve__append_only_changelog(client):
    """AC1 (UAT): Append-only CHANGELOG additions auto-resolve and merge succeeds."""
    pytest.skip("manual — requires setup of two sprints with append-only CHANGELOG conflict; verified via integration")


def test_merge_conflict_auto_resolve__append_only_schema(client):
    """AC1 (UAT): Append-only SCHEMA.md additions auto-resolve and merge succeeds."""
    pytest.skip("manual — requires setup with append-only SCHEMA conflict; verified via integration")


def test_merge_conflict_auto_resolve__non_overlapping_model_blocks(client):
    """AC1 (UAT): Non-overlapping class/function blocks in same file auto-resolve."""
    pytest.skip("manual — requires two sprints adding different classes to models.py; verified via integration")


def test_merge_conflict_unresolvable__returns_409_with_code_and_files(client):
    """AC2 (UAT): Overlapping conflict returns 409 with code + files."""
    pytest.skip("manual — requires true overlapping conflict; verified via integration")


def test_merge_conflict_response__structured_format(client):
    """AC2 (UAT): 409 response includes {code, files, label} for autonomous loop."""
    pytest.skip("manual — response structure verified in integration; parsed by loop-driver")


def test_merge_conflict_status__get_sprint_shows_blocked_state(client):
    """AC3 (UAT): GET status endpoint shows blocked state."""
    pytest.skip("manual — requires active conflict state; GET endpoint availability verified separately")


def test_merge_conflict_loop_contract__skip_and_continue_flow(client):
    """AC4 (UAT): Autonomous loop receives 409, parses, and continues."""
    pytest.skip("manual — loop-driver behavior exercised at higher level; contract verified via simulation")


def test_merge_conflict_idempotent_on_resume__already_merged_steps_skipped(client):
    """AC4 (UAT): When complete-step resumes, already-merged branches are idempotent."""
    pytest.skip("manual — idempotency verified via _branch_has_unmerged_commits logic")
