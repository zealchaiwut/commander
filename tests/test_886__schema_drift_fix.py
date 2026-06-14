"""Tests for issue #886: Fix schema-drift by pointing fixtures at real DDL.

AC coverage:
  AC3 - no hand-rolled inline-DDL for sprint_tickets in tests/
  AC4 - shared make_sprint_db helper exists in conftest and creates tables from real models
  AC5 - make_sprint_db creates sprint_tickets with all canonical columns (no new hand-rolled DDL)
"""
import subprocess
from pathlib import Path

def test_no_hand_rolled_sprint_tickets_ddl():
    """AC3: no inline DDL for sprint_tickets remains in any test file."""
    tests_dir = Path(__file__).parent
    # Exclude this file so the grep command text doesn't self-match.
    this_file = Path(__file__).name
    result = subprocess.run(
        ["grep", "-rn", "--include=*.py", f"--exclude={this_file}",
         "CREATE TABLE.*sprint_tickets", str(tests_dir)],
        capture_output=True,
        text=True,
    )
    assert result.stdout == "", (
        f"Found hand-rolled sprint_tickets DDL in:\n{result.stdout}"
    )


def test_make_sprint_db_helper_exists():
    """AC4: shared helper make_sprint_db is importable from conftest (re-exported from services)."""
    from conftest import make_sprint_db
    assert callable(make_sprint_db)
    # The real implementation lives in services/sprint_manager/testing.py (not in tests/)
    # so the grep check for hand-rolled DDL in tests/ still passes.
    from services.sprint_manager.testing import make_sprint_db as svc_fn
    assert make_sprint_db is svc_fn


def test_make_sprint_db_creates_canonical_columns():
    """AC4/AC5: make_sprint_db creates sprint_tickets with all columns from models.SprintTicket."""
    from sqlalchemy import create_engine, inspect
    from services.sprint_manager.models import SprintTicket
    from conftest import make_sprint_db

    engine = create_engine("sqlite:///:memory:")
    make_sprint_db(engine)

    insp = inspect(engine)
    actual_cols = {c["name"] for c in insp.get_columns("sprint_tickets")}

    # Derive required columns from the canonical ORM model — drift is structurally impossible.
    required = {col.name for col in SprintTicket.__table__.columns}
    assert required <= actual_cols, f"Missing columns: {required - actual_cols}"


def test_make_sprint_db_creates_sprints_table():
    """AC4: make_sprint_db also creates the sprints table."""
    from sqlalchemy import create_engine, inspect
    from conftest import make_sprint_db

    engine = create_engine("sqlite:///:memory:")
    make_sprint_db(engine)

    insp = inspect(engine)
    assert "sprints" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("sprints")}
    assert {"id", "label", "goal", "status", "project"} <= cols
