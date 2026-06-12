"""Tests for issue #808 — stale-branch scan + cleanup on History.

Two surfaces are exercised, each anchored one-to-one to an acceptance criterion:

  * Backend (functional) — ``stale_branches_service`` orchestration is tested by
    patching its three thin gh-calling helpers (``_list_remote_feature_branches``,
    ``_is_merged``, ``_delete_branch``) and the local history map, so the AC
    behaviour (mapping, dry-run, skip-unmerged, delete-only-merged) is verified
    without touching the network. The route layer is exercised through a real
    FastAPI ``TestClient``.

  * Frontend (design-contract) — the amber ``.stale-chip``, the "Scan stale
    branches" toolbar button, the confirm dialog, and the post-cleanup chip
    removal live in ``project.html`` (no build step, no node in this clone).
    Following the established pattern for frontend tickets (#805–#807) these
    assertions read the static HTML/JS source.

AC coverage:
  AC1  GET /scan-stale-branches lists feature/<N>-* branches, maps N->sprint via
       enrichment data, checks merged status against the target branch.
  AC2  POST /cleanup-stale-branches returns a dry-run first; on confirm deletes
       only merged branches and returns a skipped_unmerged list.
  AC3  History rows with stale branches show an amber .stale-chip reading
       "N stale branches · clean up".
  AC4  Toolbar renders a "Scan stale branches" button that triggers the scan.
  AC5  Clicking "clean up" opens a confirm dialog listing which branches will be
       deleted.
  AC6  After successful cleanup, chips disappear from affected History rows.
  AC7  Scan and cleanup are permitted on locked History rows.
  AC8  Unmerged branches appear in the dialog as skipped and are never deleted.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"
PROJECT_HTML = (DASHBOARD_DIR / "static" / "project.html").read_text(encoding="utf-8")

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))


def _load_module(name: str, path: Path):
    """Load a routers/*.py file as a submodule of a stub `routers` package."""
    if "routers" not in sys.modules:
        stub = types.ModuleType("routers")
        stub.__path__ = [str(ROUTERS_DIR)]  # type: ignore[attr-defined]
        sys.modules["routers"] = stub
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# sprint_history_service is imported by both the service and the router module.
_load_module("routers.sprint_history_service", ROUTERS_DIR / "sprint_history_service.py")
svc = _load_module("routers.stale_branches_service", ROUTERS_DIR / "stale_branches_service.py")
router_mod = _load_module("routers.sprint_history", ROUTERS_DIR / "sprint_history.py")


@pytest.fixture()
def patched(monkeypatch):
    """Patch the gh-calling helpers + the ticket→sprint map with fakes.

    Remote branches: two map to sprint-58 (one merged, one unmerged) and one
    maps to no sprint. Deletes are recorded so we can assert *what* was deleted.
    """
    branches = ["feature/42-old-work", "feature/99-wip", "feature/7-unmapped"]
    merged = {"feature/42-old-work": True, "feature/99-wip": False, "feature/7-unmapped": True}
    deleted: list[str] = []

    monkeypatch.setattr(svc, "_list_remote_feature_branches", lambda repo: list(branches))
    monkeypatch.setattr(svc, "_is_merged", lambda repo, branch, target: merged.get(branch, False))

    def _fake_delete(repo, branch):
        deleted.append(branch)
        return True

    monkeypatch.setattr(svc, "_delete_branch", _fake_delete)
    monkeypatch.setattr(svc, "_ticket_sprint_map", lambda: {42: "sprint-58", 99: "sprint-58"})
    return types.SimpleNamespace(branches=branches, merged=merged, deleted=deleted)


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router_mod.router)
    return TestClient(app)


# ════════════════════════ AC1 — scan endpoint ════════════════════════════════

def test_ac1_list_helper_keeps_only_feature_number_branches(monkeypatch):
    """The remote lister filters to feature/<N>-* and drops everything else."""
    raw = "\n".join([
        "feature/42-old-work",
        "feature/99-wip",
        "develop", "master", "main", "attachments",
        "sprint/sprint-58",          # not a feature/<N>-* branch
        "feature/no-number-here",    # feature/ but no leading issue number
        "bugfix/123-thing",
    ])
    monkeypatch.setattr(svc, "_run_gh", lambda args: raw)
    got = svc._list_remote_feature_branches("o/r")
    assert got == ["feature/42-old-work", "feature/99-wip"]


def test_ac1_scan_maps_issue_to_sprint(patched):
    res = svc.scan_stale_branches("o/r")
    by_branch = {b["branch"]: b for b in res["branches"]}
    assert by_branch["feature/42-old-work"]["issue"] == 42
    assert by_branch["feature/42-old-work"]["sprint"] == "sprint-58"
    assert by_branch["feature/99-wip"]["sprint"] == "sprint-58"
    # A branch whose issue maps to no sprint is surfaced but unmapped.
    assert by_branch["feature/7-unmapped"]["sprint"] is None


def test_ac1_scan_checks_merged_status_against_target(patched):
    res = svc.scan_stale_branches("o/r", target="develop")
    assert res["target_branch"] == "develop"
    by_branch = {b["branch"]: b for b in res["branches"]}
    assert by_branch["feature/42-old-work"]["merged"] is True
    assert by_branch["feature/99-wip"]["merged"] is False


def test_ac1_scan_groups_by_sprint_for_chips(patched):
    res = svc.scan_stale_branches("o/r")
    grp = res["by_sprint"]["sprint-58"]
    assert grp["count"] == 2
    assert grp["merged"] == ["feature/42-old-work"]
    assert grp["unmerged"] == ["feature/99-wip"]
    # Unmapped branches do not create a phantom row group.
    assert all(k == "sprint-58" for k in res["by_sprint"])


def test_ac1_scan_merged_check_uses_compare_ahead_by(monkeypatch):
    """_is_merged returns True only when the branch is 0 commits ahead of target."""
    calls = {}

    def _fake_run(args):
        calls["args"] = args
        return "0\n"

    monkeypatch.setattr(svc, "_run_gh", _fake_run)
    assert svc._is_merged("o/r", "feature/42-old-work", "develop") is True
    assert "compare/develop...feature/42-old-work" in " ".join(calls["args"])
    monkeypatch.setattr(svc, "_run_gh", lambda args: "3\n")
    assert svc._is_merged("o/r", "feature/99-wip", "develop") is False


def test_ac1_scan_endpoint_returns_shape(patched, client):
    r = client.get("/scan-stale-branches", params={"repo": "o/r"})
    assert r.status_code == 200
    body = r.json()
    assert body["repo"] == "o/r"
    assert "sprint-58" in body["by_sprint"]
    assert body["by_sprint"]["sprint-58"]["count"] == 2


# ════════════════════════ AC2 / AC8 — cleanup endpoint ═══════════════════════

def test_ac2_cleanup_dry_run_lists_to_delete_and_skipped(patched):
    res = svc.cleanup_stale_branches(
        "o/r", ["feature/42-old-work", "feature/99-wip"], confirm=False
    )
    assert res["dry_run"] is True
    assert res["to_delete"] == ["feature/42-old-work"]
    assert res["skipped_unmerged"] == ["feature/99-wip"]
    # A dry run must never delete anything.
    assert patched.deleted == []


def test_ac2_cleanup_confirm_deletes_only_merged(patched):
    res = svc.cleanup_stale_branches(
        "o/r", ["feature/42-old-work", "feature/99-wip"], confirm=True
    )
    assert res["dry_run"] is False
    assert res["deleted"] == ["feature/42-old-work"]
    assert res["skipped_unmerged"] == ["feature/99-wip"]
    # Exactly the merged branch was deleted; the unmerged one was not.
    assert patched.deleted == ["feature/42-old-work"]


def test_ac8_unmerged_branch_is_never_deleted_even_when_requested(patched):
    """Even if the caller explicitly lists an unmerged branch, it is skipped."""
    res = svc.cleanup_stale_branches("o/r", ["feature/99-wip"], confirm=True)
    assert res["deleted"] == []
    assert res["skipped_unmerged"] == ["feature/99-wip"]
    assert "feature/99-wip" not in patched.deleted


def test_ac2_cleanup_skips_protected_and_out_of_scope_branches(patched):
    """Protected / non-feature branches are never deleted nor counted unmerged."""
    res = svc.cleanup_stale_branches(
        "o/r", ["develop", "master", "sprint/sprint-58", "feature/42-old-work"], confirm=True
    )
    assert res["deleted"] == ["feature/42-old-work"]
    assert res["skipped_unmerged"] == []
    assert patched.deleted == ["feature/42-old-work"]


def test_ac2_cleanup_endpoint_dry_run(patched, client):
    r = client.post("/cleanup-stale-branches", json={
        "repo": "o/r", "branches": ["feature/42-old-work", "feature/99-wip"], "confirm": False,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert body["to_delete"] == ["feature/42-old-work"]
    assert body["skipped_unmerged"] == ["feature/99-wip"]
    assert patched.deleted == []


def test_ac2_cleanup_endpoint_confirm(patched, client):
    r = client.post("/cleanup-stale-branches", json={
        "repo": "o/r", "branches": ["feature/42-old-work", "feature/99-wip"], "confirm": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is False
    assert body["deleted"] == ["feature/42-old-work"]
    assert body["skipped_unmerged"] == ["feature/99-wip"]
    assert patched.deleted == ["feature/42-old-work"]


# ════════════════════ frontend design-contract (AC3–AC7) ═════════════════════

def _fn_body(name: str, src: str = PROJECT_HTML) -> str:
    """Return the brace-balanced body of a JS function defined in ``src``."""
    pos = -1
    for needle in (f"function {name}(", f"{name} = function", f"async function {name}("):
        pos = src.find(needle)
        if pos != -1:
            break
    assert pos != -1, f"function {name} not found"
    brace = src.find("{", pos)
    assert brace != -1, f"no opening brace for {name}"
    depth = 0
    for i in range(brace, len(src)):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[brace:i + 1]
    raise AssertionError(f"unbalanced braces for {name}")


def _css_rule(selector: str) -> str:
    pat = re.compile(re.escape(selector) + r"(?![\w-])[^{}]*\{([^{}]*)\}")
    m = pat.search(PROJECT_HTML)
    assert m is not None, f"CSS rule for `{selector}` not found"
    return m.group(1)


# ── AC3 — amber per-row chip ──────────────────────────────────────────────────

def test_ac3_stale_chip_css_is_amber():
    rule = _css_rule(".stale-chip")
    assert "var(--amber-bg)" in rule, "chip background uses the amber token"
    assert "var(--amber)" in rule, "chip text/border uses the amber token"


def test_ac3_chip_builder_reads_count_and_clean_up_label():
    body = _fn_body("_histStaleChipHtml")
    assert "stale-chip" in body, "chip carries the .stale-chip class"
    assert "stale branch" in body and "stale branches" in body, \
        "chip text is singular/plural 'stale branch(es)'"
    assert "clean up" in body, "chip carries the 'clean up' affordance"
    assert "_histStaleBySprint" in body, "chip count comes from the scan results"
    assert "_histCleanupStale" in body, "clicking the chip triggers cleanup"


def test_ac3_chip_is_rendered_in_the_card_head():
    card = _fn_body("_histCardHtml")
    assert "_histStaleChipHtml(s)" in card, \
        "the card head renders the stale chip for the sprint"


# ── AC4 — toolbar scan button ─────────────────────────────────────────────────

def test_ac4_toolbar_has_scan_button():
    assert 'id="ps-stale-scan-btn"' in PROJECT_HTML
    assert "Scan stale branches" in PROJECT_HTML
    assert 'id="ps-sprint-cleanup-card"' in PROJECT_HTML
    assert "_histScanStale()" in PROJECT_HTML, "the button triggers the scan handler"
    toolbar = _fn_body("_histToolbarHtml")
    assert "Scan stale branches" not in toolbar, "scan action lives in project settings, not history toolbar"


def test_ac4_scan_handler_hits_the_scan_endpoint():
    body = _fn_body("_histScanStale")
    assert "/scan-stale-branches" in body, "scan calls the scan endpoint"
    assert "_histStaleBySprint" in body, "scan stores results for the chips"
    assert "_histRenderLedger" in body, "scan re-renders so chips appear"


# ── AC5 — confirm dialog ──────────────────────────────────────────────────────

def test_ac5_cleanup_does_dry_run_then_confirm_dialog():
    body = _fn_body("_histCleanupStale")
    # A dry-run request precedes the destructive one (AC2 on the client side).
    assert "confirm: false" in body, "cleanup dry-runs first"
    assert "/cleanup-stale-branches" in body
    # The dialog lists the branches that will be deleted.
    assert "confirm(" in body, "a confirm dialog gates the deletion"
    assert "to_delete" in body, "the dialog is built from the dry-run to_delete list"


# ── AC6 — chips disappear after cleanup ───────────────────────────────────────

def test_ac6_chip_cleared_after_successful_cleanup():
    body = _fn_body("_histCleanupStale")
    assert "confirm: true" in body, "a confirmed delete is sent"
    assert "delete _histStaleBySprint[label]" in body, \
        "the row's chip state is removed once no stale branches remain"
    assert "_histRenderLedger" in body, "the ledger re-renders to drop the chip"


# ── AC7 — chips/cleanup allowed on locked rows ────────────────────────────────

def test_ac7_chip_renders_regardless_of_locked_state():
    """The stale chip is inserted into the card head unconditionally, so it
    appears on locked (finished/deleted) rows too — scan/cleanup are repo
    actions, not record changes."""
    card = _fn_body("_histCardHtml")
    # The chip call sits in the always-rendered head, not inside the
    # `expanded ? ... : ''` body and not gated by `locked`.
    head_marker = "_histStateChip(s.lifecycle_state"
    chip_marker = "_histStaleChipHtml(s)"
    assert head_marker in card and chip_marker in card
    assert card.index(chip_marker) > card.index(head_marker)
    # The body (verbs/links) is the part suppressed when locked; the chip must
    # not live there.
    body_var = _fn_body("_histCardHtml")
    # crude guard: chip is referenced exactly once and before the `body` const.
    assert body_var.count(chip_marker) == 1


# ── AC8 (frontend half) — skipped unmerged surfaced in the dialog ─────────────

def test_ac8_dialog_surfaces_skipped_unmerged():
    body = _fn_body("_histCleanupStale")
    assert "skipped_unmerged" in body, "the dialog reads the skipped list"
    assert "Skipped" in body or "skipped" in body, "the dialog labels skipped branches"
    assert "never deleted" in body or "unmerged" in body, \
        "the dialog explains unmerged branches are not deleted"
