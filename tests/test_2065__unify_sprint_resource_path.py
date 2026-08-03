"""Tests for issue #2065: Unify the sprint resource path.

Acceptance Criteria:
  AC1 - Canonical flat /api/sprints/{sprint_label}?project= routes exist for
        all 8 sprint_finish / finish_progress endpoints and respond (not 404).
  AC2 - Path parameter in canonical routes is sprint_label (not label).
  AC3 - Legacy /api/projects/{owner}/{repo_name}/sprints/{label}/... routes
        are marked deprecated in the OpenAPI schema.
  AC4 - Behavioral: invalid sprint_label → 400 on canonical AND alias routes;
        missing project= → 422 on canonical routes (FastAPI validation).
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

SPRINT_FINISH_PY = REPO_ROOT / "apps" / "dashboard" / "routers" / "sprint_finish.py"
FINISH_PROGRESS_PY = REPO_ROOT / "apps" / "dashboard" / "routers" / "finish_progress.py"

_CANONICAL_FLAT_PATHS = [
    "/api/sprints/{sprint_label}/finish-preview",
    "/api/sprints/{sprint_label}/bulk-complete-preview",
    "/api/sprints/{sprint_label}/finish",
    "/api/sprints/{sprint_label}/bulk-complete",
    "/api/sprints/{sprint_label}/complete-step",
    "/api/sprints/{sprint_label}/conflict-status",
    "/api/sprints/{sprint_label}/finish-bg",
    "/api/sprints/{sprint_label}/finish-stream",
]

_DEPRECATED_NESTED_PATHS = [
    "/api/projects/{owner}/{repo_name}/sprints/{label}/finish-preview",
    "/api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete-preview",
    "/api/projects/{owner}/{repo_name}/sprints/{label}/finish",
    "/api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete",
    "/api/projects/{owner}/{repo_name}/sprints/{label}/complete-step",
    "/api/projects/{owner}/{repo_name}/sprints/{label}/conflict-status",
    "/api/projects/{owner}/{repo_name}/sprints/{label}/finish-bg",
    "/api/projects/{owner}/{repo_name}/sprints/{label}/finish-stream",
]


def _dual(name, **kw):
    """Patch a name in both `server` and `startup` modules."""
    out = []
    for mod_name in ("server", "startup"):
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        if hasattr(mod, name):
            out.append(patch(f"{mod_name}.{name}", **kw))
    return out


def _make_client():
    import server as srv
    from fastapi.testclient import TestClient
    return TestClient(srv.app, raise_server_exceptions=False)


# ── AC1: Canonical routes exist and respond (not 404) ────────────────────────

def test_canonical_finish_preview_rejects_invalid_label():
    """AC1/AC4: GET canonical finish-preview returns 400 for bad sprint_label."""
    with _make_client() as client:
        r = client.get("/api/sprints/not-a-sprint/finish-preview?project=zealchaiwut/commander")
    assert r.status_code == 400, (
        f"Expected 400 for invalid sprint label, got {r.status_code}: {r.text}"
    )


def test_canonical_bulk_complete_preview_rejects_invalid_label():
    """AC1/AC4: GET canonical bulk-complete-preview returns 400 for bad sprint_label."""
    with _make_client() as client:
        r = client.get("/api/sprints/not-a-sprint/bulk-complete-preview?project=zealchaiwut/commander")
    assert r.status_code == 400, (
        f"Expected 400 for invalid sprint label, got {r.status_code}: {r.text}"
    )


def test_canonical_finish_post_rejects_invalid_label():
    """AC1/AC4: POST canonical finish returns 400 for bad sprint_label."""
    with _make_client() as client:
        r = client.post(
            "/api/sprints/not-a-sprint/finish?project=zealchaiwut/commander",
            json={"confirmed": True},
        )
    assert r.status_code == 400, (
        f"Expected 400 for invalid sprint label, got {r.status_code}: {r.text}"
    )


def test_canonical_bulk_complete_post_rejects_invalid_label():
    """AC1/AC4: POST canonical bulk-complete returns 400 for bad sprint_label."""
    with _make_client() as client:
        r = client.post(
            "/api/sprints/not-a-sprint/bulk-complete?project=zealchaiwut/commander",
            json={"confirmed": True},
        )
    assert r.status_code == 400, (
        f"Expected 400 for invalid sprint label, got {r.status_code}: {r.text}"
    )


def test_canonical_complete_step_rejects_invalid_label():
    """AC1/AC4: POST canonical complete-step returns 400 for bad sprint_label."""
    with _make_client() as client:
        r = client.post(
            "/api/sprints/not-a-sprint/complete-step?project=zealchaiwut/commander",
            json={"confirmed": True},
        )
    assert r.status_code == 400, (
        f"Expected 400 for invalid sprint label, got {r.status_code}: {r.text}"
    )


def test_canonical_conflict_status_rejects_invalid_label():
    """AC1/AC4: GET canonical conflict-status returns 400 for bad sprint_label."""
    with _make_client() as client:
        r = client.get("/api/sprints/not-a-sprint/conflict-status?project=zealchaiwut/commander")
    assert r.status_code == 400, (
        f"Expected 400 for invalid sprint label, got {r.status_code}: {r.text}"
    )


def test_canonical_finish_bg_rejects_invalid_label():
    """AC1/AC4: POST canonical finish-bg returns 400 for bad sprint_label."""
    with _make_client() as client:
        r = client.post(
            "/api/sprints/not-a-sprint/finish-bg?project=zealchaiwut/commander",
            json={"confirmed": True},
        )
    assert r.status_code == 400, (
        f"Expected 400 for invalid sprint label, got {r.status_code}: {r.text}"
    )


# ── AC2: sprint_label as path param in canonical routes ──────────────────────

def test_canonical_conflict_status_e2e_with_mocks():
    """AC4: Canonical conflict-status responds 200 when server functions succeed."""
    patches = [
        *_dual("_project_root_path", return_value=REPO_ROOT),
        *_dual("_sprint_get_conflict_blocked", return_value=None),
    ]
    for p in patches:
        p.start()
    try:
        with _make_client() as client:
            r = client.get(
                "/api/sprints/sprint-85/conflict-status?project=zealchaiwut/commander"
            )
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert data.get("blocked") is False, f"Expected blocked=False, got {data}"
        assert "label" in data, f"Expected label in response: {data}"
        assert data["label"] == "sprint-85", f"Expected label=sprint-85, got {data['label']}"
    finally:
        for p in patches:
            p.stop()


def test_openapi_canonical_routes_use_sprint_label_param():
    """AC2: OpenAPI schema canonical routes reference sprint_label path parameter."""
    with _make_client() as client:
        r = client.get("/openapi.json")
    assert r.status_code == 200, f"Failed to fetch openapi.json: {r.status_code}"
    schema = r.json()
    paths = schema.get("paths", {})

    canonical_paths_with_sprint_label = [
        path for path in paths
        if path.startswith("/api/sprints/") and "{sprint_label}" in path
        and any(
            suffix in path
            for suffix in [
                "/finish-preview", "/bulk-complete-preview", "/finish",
                "/bulk-complete", "/complete-step", "/conflict-status",
                "/finish-bg", "/finish-stream",
            ]
        )
    ]
    assert len(canonical_paths_with_sprint_label) >= 6, (
        f"Expected at least 6 canonical flat routes with {{sprint_label}}, "
        f"found {len(canonical_paths_with_sprint_label)}: {canonical_paths_with_sprint_label}"
    )


# ── AC3: Deprecated flag on legacy nested routes ──────────────────────────────

def test_openapi_nested_finish_preview_is_deprecated():
    """AC3: /api/projects/.../finish-preview is marked deprecated in OpenAPI."""
    with _make_client() as client:
        r = client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    paths = schema.get("paths", {})

    nested_path = "/api/projects/{owner}/{repo_name}/sprints/{label}/finish-preview"
    assert nested_path in paths, f"Nested path not found in schema: {nested_path}"
    for method, op in paths[nested_path].items():
        assert op.get("deprecated") is True, (
            f"Expected deprecated=true on {method.upper()} {nested_path}, got: {op}"
        )


def test_openapi_nested_bulk_complete_preview_is_deprecated():
    """AC3: /api/projects/.../bulk-complete-preview is marked deprecated in OpenAPI."""
    with _make_client() as client:
        r = client.get("/openapi.json")
    schema = r.json()
    nested_path = "/api/projects/{owner}/{repo_name}/sprints/{label}/bulk-complete-preview"
    assert nested_path in schema["paths"], f"Path not found: {nested_path}"
    for method, op in schema["paths"][nested_path].items():
        assert op.get("deprecated") is True, (
            f"Expected deprecated=true on {method.upper()} {nested_path}"
        )


def test_openapi_nested_finish_post_is_deprecated():
    """AC3: /api/projects/.../finish POST is marked deprecated in OpenAPI."""
    with _make_client() as client:
        r = client.get("/openapi.json")
    schema = r.json()
    nested_path = "/api/projects/{owner}/{repo_name}/sprints/{label}/finish"
    assert nested_path in schema["paths"], f"Path not found: {nested_path}"
    for method, op in schema["paths"][nested_path].items():
        assert op.get("deprecated") is True, (
            f"Expected deprecated=true on {method.upper()} {nested_path}"
        )


def test_openapi_nested_complete_step_is_deprecated():
    """AC3: /api/projects/.../complete-step is marked deprecated in OpenAPI."""
    with _make_client() as client:
        r = client.get("/openapi.json")
    schema = r.json()
    nested_path = "/api/projects/{owner}/{repo_name}/sprints/{label}/complete-step"
    assert nested_path in schema["paths"], f"Path not found: {nested_path}"
    for method, op in schema["paths"][nested_path].items():
        assert op.get("deprecated") is True, (
            f"Expected deprecated=true on {method.upper()} {nested_path}"
        )


def test_openapi_nested_conflict_status_is_deprecated():
    """AC3: /api/projects/.../conflict-status is marked deprecated in OpenAPI."""
    with _make_client() as client:
        r = client.get("/openapi.json")
    schema = r.json()
    nested_path = "/api/projects/{owner}/{repo_name}/sprints/{label}/conflict-status"
    assert nested_path in schema["paths"], f"Path not found: {nested_path}"
    for method, op in schema["paths"][nested_path].items():
        assert op.get("deprecated") is True, (
            f"Expected deprecated=true on {method.upper()} {nested_path}"
        )


def test_openapi_nested_finish_bg_is_deprecated():
    """AC3: /api/projects/.../finish-bg is marked deprecated in OpenAPI."""
    with _make_client() as client:
        r = client.get("/openapi.json")
    schema = r.json()
    nested_path = "/api/projects/{owner}/{repo_name}/sprints/{label}/finish-bg"
    assert nested_path in schema["paths"], f"Path not found: {nested_path}"
    for method, op in schema["paths"][nested_path].items():
        assert op.get("deprecated") is True, (
            f"Expected deprecated=true on {method.upper()} {nested_path}"
        )


# ── AC4: Missing project= → 422 on canonical routes ──────────────────────────

def test_canonical_finish_preview_requires_project_param():
    """AC4: GET canonical finish-preview without project= returns 422."""
    with _make_client() as client:
        r = client.get("/api/sprints/sprint-85/finish-preview")
    assert r.status_code == 422, (
        f"Expected 422 for missing project=, got {r.status_code}: {r.text}"
    )


def test_canonical_conflict_status_requires_project_param():
    """AC4: GET canonical conflict-status without project= returns 422."""
    with _make_client() as client:
        r = client.get("/api/sprints/sprint-85/conflict-status")
    assert r.status_code == 422, (
        f"Expected 422 for missing project=, got {r.status_code}: {r.text}"
    )


def test_canonical_complete_step_requires_project_param():
    """AC4: POST canonical complete-step without project= returns 422."""
    with _make_client() as client:
        r = client.post("/api/sprints/sprint-85/complete-step", json={"confirmed": True})
    assert r.status_code == 422, (
        f"Expected 422 for missing project=, got {r.status_code}: {r.text}"
    )


# ── AC4: Alias routes still work (not 404) ────────────────────────────────────

def test_alias_finish_preview_rejects_invalid_label():
    """AC4: Legacy alias GET finish-preview returns 400 (not 404) for invalid label."""
    with _make_client() as client:
        r = client.get(
            "/api/projects/zealchaiwut/commander/sprints/not-a-sprint/finish-preview"
        )
    assert r.status_code == 400, (
        f"Expected 400 from alias route, got {r.status_code}: {r.text}"
    )


def test_alias_conflict_status_rejects_invalid_label():
    """AC4: Legacy alias GET conflict-status returns 400 (not 404) for invalid label."""
    with _make_client() as client:
        r = client.get(
            "/api/projects/zealchaiwut/commander/sprints/not-a-sprint/conflict-status"
        )
    assert r.status_code == 400, (
        f"Expected 400 from alias route, got {r.status_code}: {r.text}"
    )


def test_alias_conflict_status_e2e_with_mocks():
    """AC4: Legacy alias conflict-status responds 200 with mocked server functions."""
    patches = [
        *_dual("_project_root_path", return_value=REPO_ROOT),
        *_dual("_sprint_get_conflict_blocked", return_value=None),
    ]
    for p in patches:
        p.start()
    try:
        with _make_client() as client:
            r = client.get(
                "/api/projects/zealchaiwut/commander/sprints/sprint-85/conflict-status"
            )
        assert r.status_code == 200, f"Expected 200 from alias, got {r.status_code}: {r.text}"
        data = r.json()
        assert data.get("blocked") is False
        assert data.get("label") == "sprint-85"
    finally:
        for p in patches:
            p.stop()


# ── AC4: Canonical and alias return identical data for conflict-status ────────

def test_canonical_and_alias_conflict_status_return_same_structure():
    """AC4: Canonical and alias conflict-status return the same response structure."""
    patches = [
        *_dual("_project_root_path", return_value=REPO_ROOT),
        *_dual("_sprint_get_conflict_blocked", return_value=None),
    ]
    for p in patches:
        p.start()
    try:
        with _make_client() as client:
            canonical = client.get(
                "/api/sprints/sprint-85/conflict-status?project=zealchaiwut/commander"
            )
            alias = client.get(
                "/api/projects/zealchaiwut/commander/sprints/sprint-85/conflict-status"
            )
        assert canonical.status_code == alias.status_code == 200, (
            f"Status mismatch: canonical={canonical.status_code}, alias={alias.status_code}"
        )
        c_data = canonical.json()
        a_data = alias.json()
        assert c_data.get("blocked") == a_data.get("blocked"), (
            f"blocked mismatch: {c_data} vs {a_data}"
        )
        assert c_data.get("label") == a_data.get("label") == "sprint-85", (
            f"label mismatch: {c_data} vs {a_data}"
        )
    finally:
        for p in patches:
            p.stop()
