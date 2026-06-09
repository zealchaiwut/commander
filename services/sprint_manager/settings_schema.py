"""Defines the known settings fields, their defaults, and which are secrets.

Schema rules:
  - Non-secret fields are returned as-is in GET responses.
  - Secret fields are returned as a boolean presence flag (<field>_set: bool).
  - PUT endpoints reject unknown fields (400) and secret fields (422).
  - Secret fields with "env_var" key derive their presence flag from that env var
    instead of from stored KV data.
"""
from __future__ import annotations

import os
from typing import Any

# field_name -> {"secret": bool, "default": Any, "env_var": Optional[str]}
KNOWN_FIELDS: dict[str, dict[str, Any]] = {
    # Agent model assignments
    "default_model": {"secret": False, "default": "claude-sonnet-4-6"},
    "coder_model": {"secret": False, "default": "claude-sonnet-4-6"},
    "tester_model": {"secret": False, "default": "claude-haiku-4-5-20251001"},
    "reviewer_model": {"secret": False, "default": "claude-haiku-4-5"},
    "estimator_model": {"secret": False, "default": "claude-haiku-4-5-20251001"},
    "documentor_model": {"secret": False, "default": "claude-sonnet-4-6"},
    # Sprint / workflow defaults
    "sprint_duration_days": {"secret": False, "default": 14},
    "default_branch": {"secret": False, "default": "develop"},
    "bulk_create_concurrency": {"secret": False, "default": 3},
    "log_level": {"secret": False, "default": "INFO"},
    # Estimation defaults (size → minutes table + buffer)
    "estimation_s_minutes": {"secret": False, "default": 5},
    "estimation_m_minutes": {"secret": False, "default": 15},
    "estimation_l_minutes": {"secret": False, "default": 30},
    "estimation_xl_minutes": {"secret": False, "default": 60},
    "estimation_buffer_pct": {"secret": False, "default": 20},
    "estimation_thin_ac_buffer_pct": {"secret": False, "default": 30},
    # Legacy scalar kept for backward compatibility
    "estimation_default_points": {"secret": False, "default": 3},
    # Project identity / display (per-project overrides stored via settings API)
    "display_name": {"secret": False, "default": ""},
    "icon": {"secret": False, "default": "ti-folder"},
    "color": {"secret": False, "default": "gray"},
    "tracked": {"secret": False, "default": True},
    # Per-project tester repo override (GITHUB_ISSUE_TEST_REPO equivalent)
    "tester_test_repo": {"secret": False, "default": ""},
    # Secrets (presence-only; values never returned)
    "github_token": {"secret": True, "default": None},
    "database_url": {"secret": True, "default": None, "env_var": "DATABASE_URL"},
}

SECRET_FIELDS: frozenset[str] = frozenset(k for k, v in KNOWN_FIELDS.items() if v["secret"])
NON_SECRET_FIELDS: frozenset[str] = frozenset(k for k, v in KNOWN_FIELDS.items() if not v["secret"])

# The single key used to store all app config in the settings table.
APP_CONFIG_KEY = "app_config"


def build_effective_response(stored: dict[str, Any]) -> dict[str, Any]:
    """Return a settings dict ready for API response.

    - Fills in defaults for any missing non-secret field.
    - Replaces each secret field with a boolean <field>_set flag.
    - For secrets with env_var, presence is derived from the environment variable.
    """
    result: dict[str, Any] = {}
    for field, meta in KNOWN_FIELDS.items():
        if meta["secret"]:
            env_var = meta.get("env_var")
            if env_var is not None:
                result[f"{field}_set"] = bool(os.environ.get(env_var, "").strip())
            else:
                result[f"{field}_set"] = field in stored and stored[field] is not None
        else:
            result[field] = stored.get(field, meta["default"])
    return result
