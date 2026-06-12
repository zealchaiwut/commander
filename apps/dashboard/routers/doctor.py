"""Doctor endpoint (issue #828).

Exposes the install-time host doctor (``scripts/doctor.py``) so it can be
triggered from the dashboard as a button / status check, returning the same
PASS/FAIL checks the CLI prints. Service logic lives in the sibling
``doctor_service`` module.
"""
from fastapi import APIRouter

from . import doctor_service

router = APIRouter(tags=["doctor"])


@router.get("/api/doctor")
def get_doctor():
    """Run the pre-sprint host doctor and return its structured report."""
    return doctor_service.run_doctor()
