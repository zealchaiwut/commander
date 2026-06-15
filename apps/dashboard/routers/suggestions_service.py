"""Business logic for the Roadmap Suggestions panel (issue #882).

Provides build_accept_prompt() which converts a suggestion dict into a BA
seed prompt ready to pass to the bulk-create endpoint, along with attribution
metadata (suggestion_id, pitch, milestone_name).
"""
from __future__ import annotations


def build_accept_prompt(suggestion: dict) -> dict:
    """Build a BA seed prompt from a suggestion card and return attribution info."""
    pitch = suggestion.get("pitch", "")
    rationale = suggestion.get("rationale", "")
    milestone = suggestion.get("milestone", "")
    scope = suggestion.get("scope", "M")

    scope_label = {
        "S": "Small (< 1 day)",
        "M": "Medium (1-3 days)",
        "L": "Large (> 3 days)",
    }.get(scope, scope)

    seed_prompt = (
        f"Feature suggestion to expand into acceptance-criteria-backed tickets:\n\n"
        f"Pitch: {pitch}\n\n"
        f"Rationale: {rationale}\n\n"
        f"Proposed milestone: {milestone}\n\n"
        f"Estimated scope: {scope_label}\n\n"
        "Please create one or more detailed GitHub issue tickets with full acceptance "
        "criteria and UAT test steps based on this suggestion. Break the work into "
        "appropriately scoped tickets."
    )

    return {
        "seed_prompt": seed_prompt,
        "suggestion_id": suggestion.get("id"),
        "pitch": pitch,
        "milestone_name": milestone,
    }
