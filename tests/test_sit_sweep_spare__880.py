"""The run-start SIT sweep must spare tickets the re-run will send straight to
the tester (legitimate SIT, coded in a prior run). Wiping their SIT demoted them
to a coder re-dispatch so the tester never ran (incident: sprint-66.3 #880 was
re-coded, then SIGKILLed, instead of being tested).
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "sprint_manager"))

import sprint_manager as sm


def _list_then_edit(list_nums):
    """gh stub: `issue list` returns list_nums; `issue edit` records the cleared #."""
    cleared = []

    def fake_run(cmd, *a, **kw):
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        if "list" in cmd:
            r.stdout = json.dumps([{"number": n} for n in list_nums])
        elif "edit" in cmd:
            cleared.append(int(cmd[3]))  # ["gh","issue","edit",<n>,...]
        return r

    return fake_run, cleared


def test_sit_sweep_spares_tester_bound_tickets():
    fake_run, cleared = _list_then_edit([880, 999])
    with patch.object(sm.subprocess, "run", side_effect=fake_run):
        sm._sweep_stale_status("SIT", "sprint-66.3", "owner/repo", spare_issues={880})
    assert 880 not in cleared, "tester-bound SIT ticket must be spared"
    assert 999 in cleared, "a true ghost SIT label is still cleared"


def test_sit_sweep_clears_all_when_no_spare():
    fake_run, cleared = _list_then_edit([880, 999])
    with patch.object(sm.subprocess, "run", side_effect=fake_run):
        sm._sweep_stale_status("SIT", "sprint-66.3", "owner/repo")
    assert set(cleared) == {880, 999}


def test_sit_sweep_active_issue_still_spared():
    fake_run, cleared = _list_then_edit([880, 999])
    with patch.object(sm.subprocess, "run", side_effect=fake_run):
        sm._sweep_stale_status("SIT", "sprint-66.3", "owner/repo", active_issue=999)
    assert 999 not in cleared
    assert 880 in cleared
