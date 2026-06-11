"""AC1: scripts/doctor.py exists and is executable (issue #828)."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "doctor.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))


def test_doctor_script_exists():
    assert SCRIPT.exists(), "scripts/doctor.py must exist"


def test_doctor_script_is_executable():
    mode = SCRIPT.stat().st_mode
    assert mode & 0o111, "scripts/doctor.py must have an executable bit set"


def test_doctor_has_python_shebang():
    first_line = SCRIPT.read_text().splitlines()[0]
    assert first_line.startswith("#!") and "python" in first_line, (
        "scripts/doctor.py must start with a python shebang so it can be run "
        "directly"
    )


def test_doctor_module_importable_and_has_entrypoint():
    import doctor

    assert callable(doctor.main)
    assert callable(doctor.run_all_checks)
