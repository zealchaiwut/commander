"""Shared run_id minting utility for Commander.

Format: <source>[hint]-<utc-ts>-<rand>
Examples:
  sprint12-20260530T0413-a3f7   (sprint dispatch, sprint number 12)
  manual-20260530T0413-b2e9     (standalone manual invocation)

Child processes that detect COMMANDER_RUN_ID in the environment adopt the
parent's run_id unchanged instead of minting a new one.
"""
from __future__ import annotations

import datetime
import os
import random

# Excludes ambiguous characters: 0, O, 1, l, I
_RAND_ALPHABET = (
    "23456789"
    "abcdefghjkmnpqrstuvwxyz"
    "ABCDEFGHJKLMNPQRSTUVWXYZ"
)


def mint_run_id(source: str, label_or_hint: str | None = None) -> str:
    """Return a run_id for this process.

    If COMMANDER_RUN_ID is already set in the environment the caller is a child
    process that should share the parent's trace — return the parent id as-is.

    Otherwise mint a new id: <source>[hint]-<utc-ts>-<rand>.
    """
    parent_id = os.environ.get("COMMANDER_RUN_ID", "").strip()
    if parent_id:
        return parent_id

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M")
    rand = "".join(random.choices(_RAND_ALPHABET, k=4))
    prefix = f"{source}{label_or_hint}" if label_or_hint else source
    return f"{prefix}-{ts}-{rand}"
