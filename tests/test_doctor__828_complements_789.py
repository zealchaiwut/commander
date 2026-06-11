"""AC: the install-time doctor complements (does not duplicate) the per-dispatch
doctor of issue #789 — install-time vs dispatch-time concerns are clearly
separated."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import doctor  # noqa: E402

SCRIPT = REPO_ROOT / "scripts" / "doctor.py"


def test_module_documents_install_vs_dispatch_separation():
    text = SCRIPT.read_text()
    assert "#789" in text or "789" in text, "must reference the dispatch-time doctor (#789)"
    lowered = text.lower()
    assert "install-time" in lowered
    assert "dispatch-time" in lowered or "dispatch time" in lowered


def test_docstring_explains_the_distinction():
    assert doctor.__doc__ is not None
    doc = doctor.__doc__.lower()
    assert "install-time" in doc
    assert "789" in doc
