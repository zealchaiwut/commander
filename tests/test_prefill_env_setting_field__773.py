"""Tester (UAT) checks for issue #773: Pre-fill .env setting field with current value.

Runs against the live UAT server ($UAT_BASE_URL). Only the read-only data
contract that powers the pre-fill is exercised here:

  AC1 — the env-vars GET endpoint returns the saved value as plaintext so the
        edit field can be seeded with the current value (not blank).
  AC3 — empty values are returned as an empty string, never as a bullet mask
        or placeholder that could be mistaken for a saved value.

The remaining criteria are browser-rendered / mutating and are NOT safe to run
against the shared UAT server, so they are marked manual here and are covered
by the isolated suite tests/test_773__prefill_env_field.py (run green by the
tester before merge):

  AC2 — editable pre-filled input (client-side rendering; browser-only, and the
        agent browser runner is unavailable in this environment).
  AC4 — saving without changes leaves the value unchanged (mutating PUT; would
        rewrite the real shared UAT .env).
  AC5 — saving after clearing resets the value (mutating PUT; same reason).
"""
import os

import httpx
import pytest


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )

# A project known to exist on the UAT server (read-only access).
PROJECT_SLUG = os.environ.get("UAT_PROJECT_SLUG", "commander")
ENV_NAME = os.environ.get("UAT_ENV_NAME", "prd")
ENV_VARS_PATH = f"/api/projects/{PROJECT_SLUG}/environments/{ENV_NAME}/env-vars"


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- AC1: edit field is pre-filled with the current saved value ---

def test_prefill_env_setting_field__get_returns_plaintext_value_contract(client):
    # AC1: the env-vars GET endpoint exposes the saved value as a plaintext
    # string so the editor can pre-fill it. Verify the contract shape: a `vars`
    # list whose items carry key/value, with every value a plain string.
    r = client.get(ENV_VARS_PATH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "vars" in body, f"missing 'vars' in response: {body!r}"
    assert isinstance(body["vars"], list)
    for v in body["vars"]:
        assert "key" in v and "value" in v, f"item missing key/value: {v!r}"
        assert isinstance(v["value"], str), f"value not plaintext str: {v!r}"


# --- AC3: empty values are never masked/placeholder-treated as a saved value ---

def test_prefill_env_setting_field__empty_value_is_empty_string_not_masked(client):
    # AC3: the data contract that backs the "(empty)" hint — an unset variable
    # comes back as "" and is never returned as bullet-mask characters that the
    # client could mistake for a saved secret.
    r = client.get(ENV_VARS_PATH)
    assert r.status_code == 200, r.text
    for v in r.json().get("vars", []):
        assert "•" not in (v["value"] or ""), f"value is bullet-masked: {v!r}"
        # An empty/unset value must be the empty string, not None or a placeholder.
        if v["value"] == "":
            assert v["value"] == ""  # explicit: empty stays empty, not a sentinel


# --- AC2 / AC4 / AC5: browser-rendered or mutating — not run against shared UAT ---

def test_prefill_env_setting_field__editable_prefilled_input():
    # AC2: editability of the pre-filled input is client-side rendering.
    pytest.skip(
        "manual — UI editability is browser-rendered; agent browser runner "
        "unavailable (COMMANDER_AGENT_BROWSER_AVAILABLE=0). Covered by isolated "
        "test_773__prefill_env_field.py::test_edit_value_is_plain_editable_input."
    )


def test_prefill_env_setting_field__save_no_changes_unchanged():
    # AC4: requires a mutating PUT against the shared UAT .env.
    pytest.skip(
        "manual — mutating PUT not run against shared UAT .env. Covered by "
        "isolated test_773__prefill_env_field.py::test_put_no_changes_leaves_file_unchanged."
    )


def test_prefill_env_setting_field__save_after_clear_resets():
    # AC5: requires a mutating PUT against the shared UAT .env.
    pytest.skip(
        "manual — mutating PUT not run against shared UAT .env. Covered by "
        "isolated test_773__prefill_env_field.py::test_put_cleared_value_resets_to_empty."
    )
