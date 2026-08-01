"""Canonical sprint-label regex — single source of truth (issue #2059).

A sprint label takes one of two forms:
  sprint-N      base sprint (e.g. sprint-42)
  sprint-N.M    child / re-run (e.g. sprint-42.1)

One sub-level only: sprint-1.2 is valid; sprint-1.2.3 is not.

Exported constants
------------------
SPRINT_LABEL_RE       compiled pattern for the full sprint label (base or child)
SPRINT_LABEL_PATTERN  raw pattern string (for embedding in error messages)
SPRINT_BASE_LABEL_RE  compiled pattern for base-only labels (sprint-N, no child)
"""
import re

SPRINT_LABEL_PATTERN = r"^sprint-\d+(\.\d+)?$"
SPRINT_LABEL_RE = re.compile(SPRINT_LABEL_PATTERN)

SPRINT_BASE_LABEL_RE = re.compile(r"^sprint-\d+$")
