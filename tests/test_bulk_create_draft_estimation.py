"""Tests for bulk-create pre-post draft estimation (Draft → Estimate → Sprint → Post).

Covers:
- _draft_body_hash: stable for the same text, changes when title/body changes
  (drives auto re-estimate on edit and the "already sized" short-circuit).
- estimate_issue.estimate_draft_file: sizes a draft from a {title, body} JSON
  file using the text-based estimator, prints the estimate to stdout, and does
  NOT carry an issue_number (no issue exists yet) or touch GitHub.
- BulkPostSelectedBody accepts an optional sprint_label (sprint chosen at the
  Sprint stage and sent at post time).
"""
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


@pytest.fixture()
def srv():
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv  # noqa: WPS433
    return srv


# ── _draft_body_hash ──────────────────────────────────────────────────────────

def test_body_hash_stable(srv):
    a = srv._draft_body_hash("Title", "Body text")
    b = srv._draft_body_hash("Title", "Body text")
    assert a == b


def test_body_hash_changes_with_title(srv):
    assert srv._draft_body_hash("Title A", "Body") != srv._draft_body_hash("Title B", "Body")


def test_body_hash_changes_with_body(srv):
    assert srv._draft_body_hash("Title", "Body A") != srv._draft_body_hash("Title", "Body B")


# ── BulkPostSelectedBody.sprint_label ─────────────────────────────────────────

def test_post_body_accepts_sprint_label(srv):
    body = srv.BulkPostSelectedBody(tickets=[{"index": 0}], sprint_label="sprint-7")
    assert body.sprint_label == "sprint-7"


def test_post_body_sprint_label_optional(srv):
    body = srv.BulkPostSelectedBody(tickets=[{"index": 0}])
    assert body.sprint_label is None


# ── estimate_issue.estimate_draft_file ────────────────────────────────────────

@pytest.fixture()
def estimate_issue():
    sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))
    import importlib
    import estimate_issue as ei  # noqa: WPS433
    return importlib.reload(ei)


def test_estimate_draft_file_prints_estimate(estimate_issue, tmp_path):
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps({"title": "Add a widget", "body": "Render a widget on the page."}))

    fake = {"size": "M", "minutes": 15, "confidence": "high", "issue_number": 0,
            "risk_flags": [], "files_likely_affected": []}
    buf = io.StringIO()
    with patch.object(estimate_issue, "run_estimator", return_value=(dict(fake), None)) as runner:
        with redirect_stdout(buf):
            estimate_issue.estimate_draft_file(draft)

    # Estimator is called with issue_num=0 and the draft's title/body.
    assert runner.call_args[0][0] == 0
    assert runner.call_args[0][1] == {"title": "Add a widget", "body": "Render a widget on the page."}

    out = json.loads(buf.getvalue().strip())
    assert out["size"] == "M"
    assert "issue_number" not in out  # no issue exists for a draft


def test_estimate_draft_file_exits_on_failure(estimate_issue, tmp_path):
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps({"title": "X", "body": "Y"}))
    with patch.object(estimate_issue, "run_estimator", return_value=(None, "model_error")):
        with pytest.raises(SystemExit) as exc:
            estimate_issue.estimate_draft_file(draft)
    assert exc.value.code == 1
