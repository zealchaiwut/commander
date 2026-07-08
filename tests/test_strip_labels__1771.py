"""Tests for issue #1771: Remove orphan split_ticket_service, anchor test to live path.

Acceptance Criteria:
- Orphan split_ticket_service.py is deleted from the repository
- Test test_1454__strip_sit_uat_labels.py is re-anchored to import from live split_xl_service
- Child tickets produced by XL-split do not inherit SIT or UAT status labels
- No other file imports split_ticket_service after deletion

These tests verify the cleanup and label-strip behavior via the HTTP API.
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    import httpx
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# AC1 — Orphan split_ticket_service.py is deleted from the repository.
def test_orphan_split_ticket_service_deleted():
    """Verify the orphan module is not present in the codebase."""
    orphan_path = DASHBOARD_DIR / "routers" / "split_ticket_service.py"
    assert not orphan_path.exists(), f"Orphan {orphan_path} should be deleted but still exists"


# AC2 — Test test_1454__strip_sit_uat_labels.py is re-anchored to import from live split_xl_service.
def test_strip_labels_test_imports_live_split_xl_service():
    """Verify the test now imports from the live split_xl_service, not the orphan."""
    test_path = REPO_ROOT / "tests" / "test_1454__strip_sit_uat_labels.py"
    assert test_path.exists(), f"Test file {test_path} not found"

    content = test_path.read_text()
    assert "from routers.split_xl_service import _STRIP_LABELS, build_child_labels" in content, \
        "Test must import from live split_xl_service"

    # Ensure no import from the orphan module
    assert "split_ticket_service" not in content or "re-anchored" in content, \
        "Test must not import from orphan split_ticket_service"


# AC3 — Child tickets produced by XL-split do not inherit SIT status labels.
def test_build_child_labels_strips_sit():
    """Unit test: build_child_labels excludes SIT from children."""
    from routers.split_xl_service import build_child_labels

    parent = ["enhancement", "SIT", "sprint-99.1"]
    child = build_child_labels(parent, "sprint-99.1")
    assert "SIT" not in child, f"Child must not inherit SIT label; got {child}"
    assert "enhancement" in child
    assert "sprint-99.1" in child


# AC4 — Child tickets produced by XL-split do not inherit UAT status labels.
def test_build_child_labels_strips_uat():
    """Unit test: build_child_labels excludes UAT from children."""
    from routers.split_xl_service import build_child_labels

    parent = ["enhancement", "UAT", "sprint-99.1"]
    child = build_child_labels(parent, "sprint-99.1")
    assert "UAT" not in child, f"Child must not inherit UAT label; got {child}"
    assert "enhancement" in child
    assert "sprint-99.1" in child


# AC5 — Child tickets preserve non-lifecycle labels when parent has both SIT and UAT.
def test_build_child_labels_strips_both_sit_uat_preserves_other_labels():
    """Unit test: both SIT and UAT are stripped, custom labels preserved."""
    from routers.split_xl_service import build_child_labels

    parent = ["enhancement", "backend", "SIT", "UAT", "size-XL", "sprint-99.1"]
    child = build_child_labels(parent, "sprint-99.1")

    assert "SIT" not in child
    assert "UAT" not in child
    assert "size-XL" not in child  # size labels are also stripped
    assert "enhancement" in child
    assert "backend" in child
    assert "sprint-99.1" in child


# AC6 — No other file imports split_ticket_service after deletion.
def test_no_remaining_imports_of_split_ticket_service():
    """Verify no active imports of the deleted orphan module remain in the codebase."""
    # Search all Python files in the repo (excluding .git and __pycache__)
    import subprocess

    result = subprocess.run(
        ["grep", "-r", "^from.*split_ticket_service\\|^import.*split_ticket_service",
         "--include=*.py", "--exclude-dir=.git", "--exclude-dir=__pycache__", str(REPO_ROOT)],
        capture_output=True,
        text=True
    )

    # grep exits with 1 if no matches found (which is what we want)
    if result.returncode == 0:
        # Some matches were found; all are active imports (^ anchors at line start)
        lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
        assert not lines, f"Found active imports of split_ticket_service:\n" + "\n".join(lines)


# AC7 — _STRIP_LABELS constant includes SIT and UAT.
def test_strip_labels_constant_defined_in_split_xl_service():
    """Verify _STRIP_LABELS is defined in split_xl_service and includes SIT/UAT."""
    from routers.split_xl_service import _STRIP_LABELS

    expected_labels = {"size-S", "size-M", "size-L", "size-XL", "estimated", "in-progress", "SIT", "UAT"}
    assert expected_labels.issubset(set(_STRIP_LABELS)), \
        f"_STRIP_LABELS must include {expected_labels}; got {_STRIP_LABELS}"
