"""Tests for issue #1415: populate slot capacity in live sprint snapshot payload.

Each test is anchored to a specific acceptance criterion (AC).

AC1  Sprint manager resolves max_coder_slots and max_tester_slots from project
     settings (SprintConfig) and sprint-level overrides (run_sprint params) at
     run start; sprint-level override takes precedence over project settings.
AC2  Resolved slot values are written into the status payload posted to the
     running pane AT run start — i.e. _post_sprint_status is called with the
     correct max_coder_slots / max_tester_slots BEFORE the first ticket
     dispatches a coder.
AC3  Concurrent run with slots=3 shows max_coder_slots=3 in the status payload.
AC4  Serial run (slots=1 or concurrent mode off) shows max_coder_slots=1.
AC5  When max_coder_slots or max_tester_slots is absent from project settings
     (cfg returns None for the field) the runner defaults to 1.
AC6  Status payload always contains both max_coder_slots and max_tester_slots
     regardless of configuration — no separate layout path.
"""
from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from unittest import mock


# ── sys.path setup ────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).parent.parent
_SM_DIR = _REPO_ROOT / "services" / "sprint_manager"
for _p in (str(_REPO_ROOT), str(_SM_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── minimal SprintConfig stub ─────────────────────────────────────────────────

@dataclass
class _FakeConfig:
    """Minimal SprintConfig stand-in covering all attrs accessed by run_sprint."""
    max_coder_slots:          Optional[int]  = None
    max_tester_slots:         Optional[int]  = None
    pipeline_mode:            bool           = False
    repo_name:                str            = "owner/repo"
    api_url:                  Optional[str]  = None
    worktree_coder:           Path           = field(default_factory=lambda: Path("/tmp/coder"))
    worktree_tester:          Path           = field(default_factory=lambda: Path("/tmp/tester"))
    sprints_dir:              Path           = field(default_factory=lambda: Path("/tmp/sprints"))
    logs_dir:                 Path           = field(default_factory=lambda: Path("/tmp/logs"))
    alerts_dir:               Path           = field(default_factory=lambda: Path("/tmp/alerts"))
    scripts_dir:              Path           = field(default_factory=lambda: Path("/tmp/scripts"))
    app_default_port:         Optional[int]  = None
    app_port_strategy:        Optional[str]  = None
    documentor_enabled:       bool           = False
    coder_model:              Optional[str]  = None
    tester_model:             Optional[str]  = None
    reviewer_model:           Optional[str]  = None
    documentor_model:         Optional[str]  = None
    coder_prompt_template:    Optional[str]  = None
    tester_prompt_template:   Optional[str]  = None
    reviewer_prompt_template: Optional[str]  = None
    documenter_prompt_template: Optional[str] = None
    tester_by_risk:           bool           = False
    coder_by_size:            Optional[dict] = None
    coder_backend:            Optional[str]  = None
    cline_model:              Optional[str]  = None
    tester_app_subdir:        Optional[str]  = None

    @property
    def worktree_tester_app(self) -> Path:
        return self.worktree_tester

    @property
    def finish_feature_script(self) -> Path:
        return self.scripts_dir / "finish_feature.py"


# ── helpers ───────────────────────────────────────────────────────────────────

def _import_sm():
    """Import sprint_manager with stubs for heavy optional deps."""
    import importlib.util

    for stub_name in ("dotenv", "yaml", "neondb_client"):
        if stub_name not in sys.modules:
            stub = types.ModuleType(stub_name)
            stub.load_dotenv = lambda *a, **kw: None  # type: ignore[attr-defined]
            sys.modules[stub_name] = stub

    gc = types.ModuleType("github_client")
    gc.repo = lambda: "owner/repo"  # type: ignore[attr-defined]
    gc.update_labels = lambda *a, **kw: None  # type: ignore[attr-defined]
    gc.add_comment = lambda *a, **kw: None  # type: ignore[attr-defined]
    gc.get_issue = lambda *a, **kw: {"title": "test issue"}  # type: ignore[attr-defined]
    sys.modules.setdefault("github_client", gc)

    sm_path = _REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
    mod_name = "_sm_1415_test"
    if mod_name in sys.modules:
        return sys.modules[mod_name]

    spec = importlib.util.spec_from_file_location(mod_name, sm_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _build_patches(sm, status_posts, dispatch_order, tmp_path):
    """Return a list of mock.patch objects for run_sprint side-effects."""
    def _fake_post(state, api_url=None, project=None):
        status_posts.append({
            "max_coder_slots": state.max_coder_slots,
            "max_tester_slots": state.max_tester_slots,
        })
        dispatch_order.append(f"post:{state.max_coder_slots}")

    def _fake_dispatch_coder(issue_num, *a, **kw):
        dispatch_order.append(f"dispatch:{issue_num}")
        return True, None

    return [
        mock.patch.object(sm, "_post_sprint_status", _fake_post),
        mock.patch.object(sm, "_dispatch_coder", _fake_dispatch_coder),
        mock.patch.object(sm, "_dispatch_tester", lambda num, *a, **kw: (0, None)),
        mock.patch.object(sm, "handle_post_tester",
                          lambda num, ec, sg, gp, gl, gm, target_branch="develop", **kw:
                          (True, "done", None)),
        mock.patch.object(sm, "list_backlog_issues",
                          lambda label, repo_name=None: [{"number": 1, "title": "ticket one"}]),
        mock.patch.object(sm, "_create_sprint_branch", lambda *a, **kw: None),
        mock.patch.object(sm, "_setup_pid_file", lambda *a, **kw: None),
        mock.patch.object(sm, "_release_pid_lock", lambda *a, **kw: None),
        mock.patch.object(sm, "_acquire_pid_lock", lambda *a, **kw: tmp_path / "pid"),
        mock.patch.object(sm, "_warn_file_conflicts", lambda *a, **kw: None),
        mock.patch.object(sm, "_sweep_stale_in_progress", lambda *a, **kw: None),
        mock.patch.object(sm, "_sweep_stale_status", lambda *a, **kw: None),
        mock.patch.object(sm, "_neon_sprint_init", lambda *a, **kw: None),
        mock.patch.object(sm, "_build_sprint_dag_layers", lambda *a, **kw: None),
        mock.patch.object(sm, "_load_sprint_plan", lambda *a, **kw: None),
        mock.patch.object(sm, "_compute_dispatch_levels",
                          lambda issues, *a, **kw: [list(issues)]),
        mock.patch.object(sm, "_sprint_db_set_ticket_order_sm", lambda *a, **kw: None),
        mock.patch.object(sm, "_plan_json_set_state_sm", lambda *a, **kw: None),
        mock.patch.object(sm, "_sprint_db_set_state_sm", lambda *a, **kw: None),
        mock.patch.object(sm, "_emit_sprint_lifecycle_event", lambda *a, **kw: None),
        mock.patch.object(sm, "_WorktreePool", _FakeWorktreePool),
        mock.patch.object(sm, "_prune_stale_local_feature_branch", lambda *a, **kw: None),
        mock.patch.object(sm, "_neon_ticket_status", lambda *a, **kw: None),
        mock.patch("atexit.register", lambda *a, **kw: None),
    ]


def _run_sprint_stub(sm, cfg, max_coder_slots=None, max_tester_slots=None, tmp_path=None):
    """Call run_sprint with all side-effects stubbed out.

    Returns (status_posts, dispatch_order) where:
    - status_posts: list of {max_coder_slots, max_tester_slots} dicts from each post
    - dispatch_order: ordered list of "post:<slots>" and "dispatch:<num>" events
    """
    import tempfile
    if tmp_path is None:
        tmp_path = Path(tempfile.mkdtemp())

    status_posts: list[dict] = []
    dispatch_order: list[str] = []

    patches = _build_patches(sm, status_posts, dispatch_order, tmp_path)

    run_kwargs = dict(
        label="sprint-99",
        skip_gates=True,
        gate_pytest=False,
        gate_lint=False,
        gate_merge_preview=False,
        cfg=cfg,
        dry_run=False,
        skip_estimator=True,
    )
    if max_coder_slots is not None:
        run_kwargs["max_coder_slots"] = max_coder_slots
    if max_tester_slots is not None:
        run_kwargs["max_tester_slots"] = max_tester_slots

    from contextlib import ExitStack
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        sm.run_sprint(**run_kwargs)

    return status_posts, dispatch_order


class _FakeWorktreePool:
    """Stub for _WorktreePool — does nothing."""
    def __init__(self, *a, **kw):
        pass

    @staticmethod
    def reconcile_orphans(*a, **kw):
        pass

    def create(self):
        pass

    def acquire(self):
        return None

    def release(self, slot):
        pass


# ── AC1: Resolution precedence ────────────────────────────────────────────────

class TestAC1ResolutionPrecedence:
    def test_sprint_override_beats_project_config(self, tmp_path):
        """Sprint-level max_coder_slots=1 overrides project config max_coder_slots=3."""
        sm = _import_sm()
        cfg = _FakeConfig(max_coder_slots=3, max_tester_slots=2)
        posts, _ = _run_sprint_stub(sm, cfg,
                                     max_coder_slots=1, max_tester_slots=1,
                                     tmp_path=tmp_path)
        assert posts, "run_sprint must post at least one status"
        first = posts[0]
        assert first["max_coder_slots"] == 1, (
            "Sprint-level override (1) must beat project config (3)"
        )
        assert first["max_tester_slots"] == 1, (
            "Sprint-level tester override (1) must beat project config (2)"
        )

    def test_project_config_used_when_no_sprint_override(self, tmp_path):
        """Project config max_coder_slots=3 is used when no sprint-level override."""
        sm = _import_sm()
        cfg = _FakeConfig(max_coder_slots=3, max_tester_slots=2)
        posts, _ = _run_sprint_stub(sm, cfg, tmp_path=tmp_path)
        assert posts, "run_sprint must post at least one status"
        first = posts[0]
        assert first["max_coder_slots"] == 3, (
            "Project config (3) must be used when no sprint override"
        )
        assert first["max_tester_slots"] == 2, (
            "Project config tester slots (2) must be used when no sprint override"
        )


# ── AC2: Slots posted before first coder dispatch ─────────────────────────────

class TestAC2SlotsPostedAtRunStart:
    def test_status_posted_before_first_dispatch(self, tmp_path):
        """A status post with the correct slot count must precede the first dispatch."""
        sm = _import_sm()
        cfg = _FakeConfig(max_coder_slots=3, max_tester_slots=2)
        _, order = _run_sprint_stub(sm, cfg, tmp_path=tmp_path)

        # Find the first "post" event and the first "dispatch" event
        first_post_idx = next(
            (i for i, ev in enumerate(order) if ev.startswith("post:")), None
        )
        first_dispatch_idx = next(
            (i for i, ev in enumerate(order) if ev.startswith("dispatch:")), None
        )

        assert first_post_idx is not None, (
            "_post_sprint_status must be called at least once"
        )
        assert first_dispatch_idx is not None, (
            "_dispatch_coder must be called for the test ticket"
        )
        assert first_post_idx < first_dispatch_idx, (
            "_post_sprint_status with slot counts must fire BEFORE first _dispatch_coder"
        )

    def test_first_status_post_has_correct_slots(self, tmp_path):
        """The first status post must carry the resolved slot values, not defaults."""
        sm = _import_sm()
        cfg = _FakeConfig(max_coder_slots=3, max_tester_slots=2)
        posts, order = _run_sprint_stub(sm, cfg, tmp_path=tmp_path)

        # The FIRST post must already have the correct slots
        assert posts[0]["max_coder_slots"] == 3, (
            "First _post_sprint_status call must have max_coder_slots=3, not the default 1"
        )


# ── AC3: Concurrent run shows correct slot count ──────────────────────────────

class TestAC3ConcurrentSlots:
    def test_slots_3_in_status_payload(self, tmp_path):
        """Status payload contains max_coder_slots=3 for a concurrent run."""
        sm = _import_sm()
        cfg = _FakeConfig(max_coder_slots=3, max_tester_slots=2)
        posts, _ = _run_sprint_stub(sm, cfg, tmp_path=tmp_path)
        assert any(p["max_coder_slots"] == 3 for p in posts), (
            "At least one status post must have max_coder_slots=3"
        )
        assert any(p["max_tester_slots"] == 2 for p in posts), (
            "At least one status post must have max_tester_slots=2"
        )


# ── AC4: Serial run shows 1 ───────────────────────────────────────────────────

class TestAC4SerialSlots:
    def test_slots_1_in_status_payload(self, tmp_path):
        """Status payload contains max_coder_slots=1 for a serial run."""
        sm = _import_sm()
        cfg = _FakeConfig(max_coder_slots=1, max_tester_slots=1)
        posts, _ = _run_sprint_stub(sm, cfg, tmp_path=tmp_path)
        assert posts, "run_sprint must post at least one status"
        assert posts[0]["max_coder_slots"] == 1, (
            "Serial run must show max_coder_slots=1"
        )
        assert posts[0]["max_tester_slots"] == 1, (
            "Serial run must show max_tester_slots=1"
        )


# ── AC5: Absent settings default to 1 ────────────────────────────────────────

class TestAC5DefaultToOne:
    def test_no_config_defaults_to_1(self, tmp_path):
        """When cfg is None, run_sprint defaults max_coder_slots and max_tester_slots to 1."""
        sm = _import_sm()
        posts, _ = _run_sprint_stub(sm, cfg=None, tmp_path=tmp_path)
        assert posts, "run_sprint must post at least one status"
        assert posts[0]["max_coder_slots"] == 1, (
            "No config → max_coder_slots must default to 1"
        )
        assert posts[0]["max_tester_slots"] == 1, (
            "No config → max_tester_slots must default to 1"
        )

    def test_config_with_none_slots_defaults_to_1(self, tmp_path):
        """When cfg exists but max_coder_slots / max_tester_slots are None (absent from yaml),
        run_sprint must default to 1 for the status payload."""
        sm = _import_sm()
        # max_coder_slots=None means not set in sprint.yaml
        cfg = _FakeConfig(max_coder_slots=None, max_tester_slots=None)
        posts, _ = _run_sprint_stub(sm, cfg, tmp_path=tmp_path)
        assert posts, "run_sprint must post at least one status"
        assert posts[0]["max_coder_slots"] == 1, (
            "Absent max_coder_slots (None in cfg) → must default to 1"
        )
        assert posts[0]["max_tester_slots"] == 1, (
            "Absent max_tester_slots (None in cfg) → must default to 1"
        )


# ── AC6: Both fields always present in status payload ─────────────────────────

class TestAC6BothFieldsAlwaysPresent:
    def test_both_slot_fields_in_payload(self, tmp_path):
        """Status payload must always contain both max_coder_slots and max_tester_slots."""
        sm = _import_sm()
        cfg = _FakeConfig(max_coder_slots=2, max_tester_slots=1)
        posts, _ = _run_sprint_stub(sm, cfg, tmp_path=tmp_path)
        assert posts, "run_sprint must post at least one status"
        for i, post in enumerate(posts):
            assert "max_coder_slots" in post, (
                f"Status post #{i} missing max_coder_slots"
            )
            assert "max_tester_slots" in post, (
                f"Status post #{i} missing max_tester_slots"
            )

    def test_no_layout_branching_single_path(self, tmp_path):
        """Slot values are passed through state.to_dict() — no separate code path for serial vs concurrent."""
        from services.sprint_manager.state import SprintState
        state = SprintState(sprint_label="sprint-99", sprint_number=99)
        state.max_coder_slots = 3
        state.max_tester_slots = 2
        payload = state.to_dict()
        assert payload["max_coder_slots"] == 3
        assert payload["max_tester_slots"] == 2

        # Same state with serial slots
        state.max_coder_slots = 1
        state.max_tester_slots = 1
        payload = state.to_dict()
        assert payload["max_coder_slots"] == 1
        assert payload["max_tester_slots"] == 1
