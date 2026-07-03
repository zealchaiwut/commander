"""Child-label inheritance for the split-ticket flow (issue #1423, #1454).

When a parent ticket is split, each child inherits the parent's labels minus the
per-ticket lifecycle/size/state labels in `_STRIP_LABELS`. Stripping those keeps
children in a clean backlog/planning state so they pick up their own size and
stage labels through the normal estimate/workflow flow rather than starting in a
downstream column they were never worked in.

Issue #1454: the strip set originally omitted the downstream workflow-state
labels ``SIT`` and ``UAT``. Splitting a parent already carrying a stage label
therefore copied that stage onto a brand-new, unimplemented child. ``SIT`` and
``UAT`` are now stripped alongside the size/estimate/in-progress labels.
"""
from __future__ import annotations

# Per-ticket lifecycle state — never inherited by children. Covers size buckets,
# the estimate marker, and every workflow-stage label (in-progress → SIT → UAT)
# so a child always starts clean and earns its own stage via the re-estimate flow.
_STRIP_LABELS = frozenset({
    "size-S", "size-M", "size-L", "size-XL", "estimated", "in-progress", "SIT", "UAT"
})


def build_child_labels(parent_labels: list[str], sprint_label: str) -> list[str]:
    """Return labels for a child issue: parent labels minus lifecycle/size/state.

    Keeps the sprint label, enhancement, and any custom labels. Always strips
    size-*, estimated, in-progress, SIT, and UAT so children get fresh estimates
    and start in a clean backlog/planning state.
    """
    result = []
    for lbl in parent_labels:
        if lbl in _STRIP_LABELS:
            continue
        result.append(lbl)
    # Ensure the sprint label is present (it should already be in parent_labels).
    if sprint_label and sprint_label not in result:
        result.append(sprint_label)
    return result
