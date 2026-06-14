"""Sprint-mgmt count unification — one canonical "done" across every pill.

Vector-1 fix: the nav pill, sidebar badge, and board running badge must all use
the SAME definition of "done" = settled work past SIT (uat + done + needs-rework)
= total - backlog - in-progress - sit. Previously the GitHub-label fallback used
`total - backlog` (overcounting in-progress + SIT as done) while the live/board
path used the settled count, so the pill and board disagreed mid-sprint.

Also covers the two companion fixes shipped together:
  - outcome endpoint lazy-ingest (collapse the disk-vs-DB dual read path)
  - lint gate auto-fix-and-commit (stop lint churn burning coder fix-rounds)
"""
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_PROJECT_HTML = _REPO / "apps" / "dashboard" / "static" / "project.html"
_SERVER = _REPO / "apps" / "dashboard" / "server.py"
_SPRINT_MGR = _REPO / "services" / "sprint_manager" / "sprint_manager.py"


# ── Backend canonical formula (pure unit test) ──────────────────────────────

def _settled():
    """Compile just `_settled_done_from_columns` from server.py source.

    The function is pure (uses only ``max`` + dict.get), so we exec it in
    isolation rather than importing the whole server module — that pulls FastAPI
    routers with optional deps (sse_starlette) not always present in a bare venv.
    """
    src = _SERVER.read_text(encoding="utf-8")
    start = src.index("def _settled_done_from_columns(")
    nxt = src.index("\ndef ", start + 1)
    ns: dict = {}
    exec(compile(src[start:nxt], "<settled>", "exec"), ns)  # noqa: S102
    return ns["_settled_done_from_columns"]


@pytest.mark.parametrize("total,columns,expected", [
    # uat=3 only settled past SIT → 11 - 5 - 2 - 1 = 3
    (11, {"backlog": 5, "in-progress": 2, "sit": 1, "uat": 3, "done": 0, "needs-rework": 0}, 3),
    # done + needs-rework both count as settled → 6 - 0 - 0 - 0 = 6
    (6, {"backlog": 0, "in-progress": 0, "sit": 0, "uat": 0, "done": 4, "needs-rework": 2}, 6),
    # nothing dispatched yet → 0 settled
    (5, {"backlog": 5}, 0),
    # empty / missing columns → 0, never crashes
    (0, {}, 0),
    # never goes negative even if columns exceed total
    (2, {"backlog": 5}, 0),
])
def test_settled_done_formula(total, columns, expected):
    assert _settled()(total, columns) == expected


def test_settled_done_excludes_in_progress_and_sit():
    """Regression: in-progress + SIT tickets must NOT count as done.

    The old `total - backlog` formula returned 5 here; the settled formula must
    return 0 because every non-backlog ticket is still mid-flight.
    """
    cols = {"backlog": 0, "in-progress": 3, "sit": 2, "uat": 0, "done": 0}
    assert _settled()(5, cols) == 0


# ── Frontend uses the shared helper (no divergent copies) ───────────────────

def test_frontend_defines_and_uses_settled_helper():
    src = _PROJECT_HTML.read_text(encoding="utf-8")
    assert "function _snavSettledDone(" in src, "frontend canonical helper missing"
    # All three pill fallbacks route through the helper.
    assert src.count("_snavSettledDone(") >= 4, "helper not used at all fallback sites"


def test_frontend_no_raw_total_minus_backlog_fallback():
    """The buggy `(d.total || 0) - (cols.backlog || 0)` running fallback must be
    gone from the pill/badge code — it overcounted in-progress + SIT as done."""
    src = _PROJECT_HTML.read_text(encoding="utf-8")
    bad = re.findall(r"\(\s*\w+\.total\s*\|\|\s*0\s*\)\s*-\s*\(\s*cols\.backlog\s*\|\|\s*0\s*\)", src)
    assert not bad, f"raw total-minus-backlog fallback still present: {bad}"


# ── Outcome endpoint lazy-ingest (collapse disk/DB dual path) ───────────────

def test_outcome_lazy_ingests_when_not_yet_ingested():
    src = _SERVER.read_text(encoding="utf-8")
    # The outcome handler must persist disk state on read when run_ingested_at is
    # null and a sprints row exists, so the next read takes the DB path.
    assert "if ingested and not ingested.get(\"run_ingested_at\"):" in src
    assert "db.ingest_sprint_run_artifact(sprint_label, state_data, project=project)" in src


# ── Lint gate auto-fix-and-commit ───────────────────────────────────────────

def test_lint_autofix_helper_defined_and_wired():
    src = _SPRINT_MGR.read_text(encoding="utf-8")
    assert "def _lint_autofix_commit(" in src, "lint auto-fix helper missing"
    # Wired into the lint gate before the check runs.
    gate = src[src.index("def _gate_lint("):]
    assert "_lint_autofix_commit(issue_num, worktester_dashboard, base_branch," in gate
