"""Defines the known settings fields, their defaults, and which are secrets.

Schema rules:
  - Non-secret fields are returned as-is in GET responses.
  - Secret fields are returned as a boolean presence flag (<field>_set: bool).
  - PUT endpoints reject unknown fields (400) and secret fields (422).
"""
from __future__ import annotations

from typing import Any

# field_name -> {"secret": bool, "default": Any}
KNOWN_FIELDS: dict[str, dict[str, Any]] = {
    "default_model": {"secret": False, "default": "claude-sonnet-4-6"},
    "coder_model": {"secret": False, "default": "claude-sonnet-4-6"},
    "tester_model": {"secret": False, "default": "claude-haiku-4-5-20251001"},
    "estimator_model": {"secret": False, "default": "claude-haiku-4-5-20251001"},
    "estimation_default_points": {"secret": False, "default": 3},
    "github_token": {"secret": True, "default": None},
}

SECRET_FIELDS: frozenset[str] = frozenset(k for k, v in KNOWN_FIELDS.items() if v["secret"])
NON_SECRET_FIELDS: frozenset[str] = frozenset(k for k, v in KNOWN_FIELDS.items() if not v["secret"])

# The single key used to store all app config in the settings table.
APP_CONFIG_KEY = "app_config"


def build_effective_response(stored: dict[str, Any]) -> dict[str, Any]:
    """Return a settings dict ready for API response.

    - Fills in defaults for any missing non-secret field.
    - Replaces each secret field with a boolean <field>_set flag.
    """
    result: dict[str, Any] = {}
    for field, meta in KNOWN_FIELDS.items():
        if meta["secret"]:
            result[f"{field}_set"] = field in stored and stored[field] is not None
        else:
            result[field] = stored.get(field, meta["default"])
    return result
