"""Root pytest configuration for Commander tester.

Ensures sys.path includes repo root so services/* modules can be imported
from tests anywhere in the project.
"""
import sys
from pathlib import Path

# Add repo root to sys.path
_REPO_ROOT = Path(__file__).parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
