"""Definition-of-Ready (DoR) check for sprint preflight (issue #1486).

Derives readiness purely from parse_ticket_spec output and estimate-resolution
results — no GitHub API calls or side-effects.
"""
from __future__ import annotations


def check_ticket_readiness(
    spec: dict,
    resolved_estimate: dict,
) -> tuple[bool, list[str]]:
    """Return (is_ready, missing_reasons) for a single ticket.

    Args:
        spec: Output of parse_ticket_spec: {acceptance_criteria, design_refs,
              test_plan, out_of_scope}.
        resolved_estimate: Output of _resolve_issue_estimate: {estimated: bool,
                           size: str|None, files: list}.

    Missing reason strings (AC3):
        no_acceptance_criteria, no_design_ref, no_test_plan,
        unresolved_estimate, unsplit_xl
    """
    missing: list[str] = []

    if not spec.get("acceptance_criteria"):
        missing.append("no_acceptance_criteria")

    if not spec.get("design_refs"):
        missing.append("no_design_ref")

    if not (spec.get("test_plan") or "").strip():
        missing.append("no_test_plan")

    if not resolved_estimate.get("estimated"):
        missing.append("unresolved_estimate")

    if resolved_estimate.get("size") == "XL":
        missing.append("unsplit_xl")

    return (len(missing) == 0, missing)
