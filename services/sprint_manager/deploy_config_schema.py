"""Per-environment deploy config schema (issue #722).

A project's deploy config is an object keyed by environment name (``prd``,
``uat``). Each entry declares a ``host`` of ``"local"`` or ``"render"``:

  - ``host=local``  → working_dir, branch, optional launchd_label, optional
    restart_script. Drives a launchd / script-based restart.
  - ``host=render`` → render_service_id, render_api_key (a secret, never
    returned in cleartext).

Storage rules:
  - Config persists via ``settings_repo`` under the ``deploy_config`` key,
    scope ``project``; it falls back to the local JSON store when Neon is
    unavailable (the repo handles that transparently).
  - GET responses NEVER include ``render_api_key`` in cleartext. Instead each
    render entry carries ``render_api_key_set: bool`` and a masked preview
    (``render_api_key_masked``, e.g. ``rnd_...cret``).
  - PUT merges per environment; a PUT that omits ``render_api_key`` (or sends
    it null/empty) leaves the stored secret unchanged. A non-empty value
    replaces it.
"""
from __future__ import annotations

import copy
from typing import Any, Optional

# The settings key under which deploy config is stored (scope='project').
DEPLOY_CONFIG_KEY = "deploy_config"

# Only these two environments are in scope.
SUPPORTED_ENVS: tuple[str, ...] = ("prd", "uat")

# Valid host values for an environment entry.
SUPPORTED_HOSTS: tuple[str, ...] = ("local", "render")

# Default branch per environment for local hosts.
_BRANCH_DEFAULTS: dict[str, str] = {"prd": "master", "uat": "develop"}

# Seed defaults keyed by project slug (the repo's last path component).
# Returned by GET when no stored override exists for that environment.
SEED_DEFAULTS: dict[str, dict[str, dict[str, Any]]] = {
    "commander": {
        "prd": {
            "host": "local",
            "launchd_label": "com.commander.dashboard",
            "branch": "master",
        },
        "uat": {"host": "local", "branch": "develop"},
    },
    "perf-coach": {
        "prd": {"host": "render"},
        "uat": {
            "host": "local",
            "branch": "develop",
            "launchd_label": "com.perfcoach.uat",
        },
    },
}


def seed_for(slug: str) -> dict[str, dict[str, Any]]:
    """Return a deep copy of the seed defaults for *slug* (empty if none)."""
    return copy.deepcopy(SEED_DEFAULTS.get(slug, {}))


def known_deploy_slugs() -> tuple[str, ...]:
    """Return the project slugs that ship seed deploy config (commander, perf-coach).

    The Deploy tab aggregates environments across these projects, so both are
    always visible even when only one is listed in ``projects.json``.
    """
    return tuple(SEED_DEFAULTS.keys())


def overview_entries_for(
    slug: str, merged: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build secret-safe Deploy-tab card entries for one project's merged config.

    One entry per configured environment (``prd``, ``uat``) whose host is
    ``local`` or ``render``. Each entry carries ``project``, ``env``, ``host``
    plus host-specific fields:

      - local  → ``branch`` (falling back to the per-env default)
      - render → ``render_service_id`` and ``render_api_key_set`` (bool)

    ``render_api_key`` is NEVER included in cleartext.
    """
    out: list[dict[str, Any]] = []
    for env in SUPPORTED_ENVS:
        entry = merged.get(env)
        if not entry:
            continue
        host = entry.get("host")
        if host not in SUPPORTED_HOSTS:
            continue
        e: dict[str, Any] = {"project": slug, "env": env, "host": host}
        if host == "local":
            e["branch"] = entry.get("branch") or branch_default(env)
        elif host == "render":
            e["render_service_id"] = entry.get("render_service_id") or None
            e["render_api_key_set"] = bool(entry.get("render_api_key"))
        out.append(e)
    return out


def branch_default(env: str) -> Optional[str]:
    """Return the default branch for *env* (``prd``→master, ``uat``→develop)."""
    return _BRANCH_DEFAULTS.get(env)


def mask_secret(value: Optional[str]) -> Optional[str]:
    """Mask a secret to a short preview, e.g. ``rnd_live_secret`` → ``rnd_...cret``.

    Returns None for an empty/absent value. Short values (<= 8 chars) are fully
    masked so no meaningful portion of the secret leaks.
    """
    if not value:
        return None
    s = str(value)
    if len(s) <= 8:
        return "..."
    return f"{s[:4]}...{s[-4:]}"


def merge_seed(
    seed: dict[str, dict[str, Any]], stored: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Merge stored overrides over seed defaults, per environment.

    Stored fields win; environments present only in the seed are preserved.
    """
    out: dict[str, dict[str, Any]] = {
        env: dict(entry) for env, entry in seed.items()
    }
    for env, entry in (stored or {}).items():
        base = dict(out.get(env, {}))
        base.update(entry)
        out[env] = base
    return out


def merge_for_put(
    current: dict[str, dict[str, Any]], incoming: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Merge an incoming PUT body over the currently stored overrides.

    Per environment, incoming fields overwrite stored fields. The
    ``render_api_key`` secret is sticky: when the incoming entry omits it (or
    sends null/empty) the existing stored key is preserved; a non-empty value
    replaces it.
    """
    out: dict[str, dict[str, Any]] = {
        env: dict(entry) for env, entry in (current or {}).items()
    }
    for env, entry in incoming.items():
        base = dict(out.get(env, {}))
        new = dict(entry)
        incoming_key = new.get("render_api_key")
        if incoming_key in (None, ""):
            # Treat null/empty/absent as "no change" — preserve stored secret.
            new.pop("render_api_key", None)
            preserved = base.get("render_api_key")
            base.update(new)
            if preserved is not None:
                base["render_api_key"] = preserved
        else:
            base.update(new)
        out[env] = base
    return out


def build_deploy_config_response(
    merged: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Build a GET-safe deploy config response from a merged config.

    - Strips raw ``render_api_key`` from every entry.
    - For render entries (or any entry that had a stored key), adds
      ``render_api_key_set: bool`` and ``render_api_key_masked``.
    - For local entries, fills the default branch when none is set.
    """
    out: dict[str, dict[str, Any]] = {}
    for env, entry in merged.items():
        e = dict(entry)
        raw = e.pop("render_api_key", None)
        host = e.get("host")
        if host == "local" and not e.get("branch"):
            bd = branch_default(env)
            if bd:
                e["branch"] = bd
        if host == "render" or raw not in (None, ""):
            e["render_api_key_set"] = raw not in (None, "")
            e["render_api_key_masked"] = mask_secret(raw)
        out[env] = e
    return out
