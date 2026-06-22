"""sprint_manager run as __main__ must register itself under 'sprint_manager' so
the extracted helper modules' lazy proxies (_lookup_in_sm) resolve.

Regression (#1289 decomposition): the dashboard launches
`python3 sprint_manager.py <label> …`, so the module lives in sys.modules as
'__main__', not 'sprint_manager'. The pipeline/post_sprint/… proxies look up
'sprint_manager' / 'services.sprint_manager.sprint_manager'; without an alias they
return None and proxied calls (e.g. list_backlog_issues → _list_labeled_open_issues)
fall back to empty — so EVERY sprint reported "No dispatchable issues found".
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SM = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"


def test_main_run_registers_sprint_manager_alias():
    # Run the file as __main__ (via runpy) with no args → argparse exits, but the
    # module-load self-registration runs first. Then assert the alias is present
    # and points at the same module object (so proxies resolve to the real funcs).
    code = (
        "import runpy, sys\n"
        "sys.argv = ['sprint_manager.py']\n"
        f"try:\n    runpy.run_path({str(SM)!r}, run_name='__main__')\n"
        "except SystemExit:\n    pass\n"
        "sm = sys.modules.get('sprint_manager')\n"
        "ok = sm is not None and hasattr(sm, '_list_labeled_open_issues')\n"
        "print('ALIAS_OK' if ok else 'ALIAS_MISSING')\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=str(REPO_ROOT), timeout=60)
    assert "ALIAS_OK" in r.stdout, f"stdout={r.stdout!r} stderr={r.stderr[-800:]!r}"
