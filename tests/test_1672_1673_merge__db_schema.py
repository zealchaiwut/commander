"""Regression: #1672 ICA usage columns coexist with #1673 provider column in db.py."""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent
DB_PY = (REPO_ROOT / "apps" / "dashboard" / "db.py").read_text(encoding="utf-8")


def test_agent_runs_has_provider_and_ica_columns():
    """Merged schema must include provider (#1673) and is_ica/cost_usd (#1672)."""
    assert '("provider", "TEXT")' in DB_PY
    assert '("is_ica", "INTEGER DEFAULT 0")' in DB_PY
    assert '("cost_usd", "REAL")' in DB_PY


def test_record_agent_start_accepts_both_provider_and_is_ica():
    body_start = DB_PY.split("def record_agent_start(", 1)[1].split("\ndef ", 1)[0]
    assert "provider: str | None = None" in body_start
    assert "is_ica: bool = False" in body_start
    assert "provider, is_ica" in body_start
    assert "1 if is_ica else 0" in body_start
