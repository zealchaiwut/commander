"""AC6: total runtime is under 10 seconds on a normally configured machine."""
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import doctor  # noqa: E402


def test_run_all_checks_under_10s():
    start = time.monotonic()
    doctor.run_all_checks()
    elapsed = time.monotonic() - start
    assert elapsed < 10, f"doctor took {elapsed:.1f}s, must be < 10s"


def test_per_subprocess_timeout_is_bounded():
    # Each external probe is bounded so the report can never hang indefinitely.
    assert doctor.DEFAULT_TIMEOUT <= 10
