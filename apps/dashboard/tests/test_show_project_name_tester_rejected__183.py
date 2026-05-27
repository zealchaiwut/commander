"""Tests for issue #183 — Show project name in tester-rejected alerts and banners.

AC-1: _alert_dashboard_banner includes `repo` field in the JSON payload.
AC-2: dispatch_alerts accepts and forwards `repo` to _alert_dashboard_banner.
AC-3: Every dispatch_alerts call in sprint_manager.py passes the correct repo value.
AC-4: Frontend repoPrefix renders [projectname] when a.repo is set.
AC-5: AlertPayload model includes repo; GET handler returns it; missing repo is null/omitted.
AC-6: TESTER_REJECTED banner displays [<project-name>] prefix.
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

SPRINT_SCRIPT = (
    Path(__file__).parent.parent.parent.parent
    / "services" / "sprint_manager" / "sprint_manager.py"
)
SERVER_PY = Path(__file__).parent.parent / "server.py"
APP_JS    = Path(__file__).parent.parent / "static" / "app.js"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_sprint_manager():
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_stub)

    gc_stub = types.ModuleType("github_client")
    gc_stub.repo = lambda: "zealchaiwut/commander"
    gc_stub.update_labels = lambda *a, **kw: None
    gc_stub.add_comment   = lambda *a, **kw: None
    sys.modules.setdefault("github_client", gc_stub)

    mod_name = "sprint_manager_183_test_import"
    spec = importlib.util.spec_from_file_location(mod_name, SPRINT_SCRIPT)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# AC-1: _alert_dashboard_banner includes repo in JSON payload
# ---------------------------------------------------------------------------

class TestAC1AlertPayloadIncludesRepo:
    def test_alert_dashboard_banner_sends_repo_field(self):
        """_alert_dashboard_banner must include repo in the JSON body."""
        sm = _import_sprint_manager()
        captured = {}

        def fake_urlopen(req, timeout=3):
            import json as _json
            body = req.data.decode()
            captured.update(_json.loads(body))

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            sm._alert_dashboard_banner(
                "Test title", "Test body", 42, "TESTER_REJECTED",
                api_url="http://localhost:8000",
                repo="zealchaiwut/commander",
            )

        assert "repo" in captured, "_alert_dashboard_banner must include 'repo' in JSON payload"
        assert captured["repo"] == "zealchaiwut/commander"

    def test_alert_dashboard_banner_repo_none_is_ok(self):
        """_alert_dashboard_banner with repo=None sends null without error."""
        sm = _import_sprint_manager()
        captured = {}

        def fake_urlopen(req, timeout=3):
            import json as _json
            body = req.data.decode()
            captured.update(_json.loads(body))

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            sm._alert_dashboard_banner(
                "Title", "Body", 1, "CRASH",
                api_url="http://localhost:8000",
                repo=None,
            )

        assert "repo" in captured
        assert captured["repo"] is None


# ---------------------------------------------------------------------------
# AC-2: dispatch_alerts accepts optional repo and forwards it
# ---------------------------------------------------------------------------

class TestAC2DispatchAlertsAcceptsRepo:
    def test_dispatch_alerts_has_repo_parameter(self):
        """dispatch_alerts must declare an optional repo parameter."""
        sm = _import_sprint_manager()
        sig = inspect.signature(sm.dispatch_alerts)
        assert "repo" in sig.parameters, \
            "dispatch_alerts must have a 'repo' parameter"
        param = sig.parameters["repo"]
        assert param.default is None, \
            "dispatch_alerts 'repo' parameter must default to None"

    def test_dispatch_alerts_forwards_repo_to_banner(self):
        """dispatch_alerts must forward repo to _alert_dashboard_banner."""
        sm = _import_sprint_manager()
        with mock.patch.object(sm, "_alert_dashboard_banner") as mock_banner:
            sm.dispatch_alerts(
                ["dashboard-banner"],
                title="T",
                body="B",
                repo="zealchaiwut/commander",
            )
        mock_banner.assert_called_once()
        call_kwargs = mock_banner.call_args[1]
        assert call_kwargs.get("repo") == "zealchaiwut/commander", \
            "_alert_dashboard_banner must be called with repo='zealchaiwut/commander'"

    def test_dispatch_alerts_no_repo_passes_none(self):
        """dispatch_alerts called without repo still forwards repo=None."""
        sm = _import_sprint_manager()
        with mock.patch.object(sm, "_alert_dashboard_banner") as mock_banner:
            sm.dispatch_alerts(
                ["dashboard-banner"],
                title="T",
                body="B",
            )
        mock_banner.assert_called_once()
        call_kwargs = mock_banner.call_args[1]
        assert "repo" in call_kwargs, \
            "_alert_dashboard_banner must receive repo kwarg even when None"
        assert call_kwargs["repo"] is None


# ---------------------------------------------------------------------------
# AC-3: Every dispatch_alerts call in sprint_manager.py passes repo
# ---------------------------------------------------------------------------

class TestAC3AllCallSitesPassRepo:
    def _get_dispatch_call_lines(self):
        """Return source lines of all dispatch_alerts(...) blocks."""
        content = SPRINT_SCRIPT.read_text(encoding="utf-8")
        lines = content.splitlines()
        call_blocks = []
        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            if "dispatch_alerts(" in stripped and not stripped.startswith("def "):
                # Collect the multi-line call
                block_lines = [lines[i]]
                depth = stripped.count("(") - stripped.count(")")
                j = i + 1
                while depth > 0 and j < len(lines):
                    block_lines.append(lines[j])
                    depth += lines[j].count("(") - lines[j].count(")")
                    j += 1
                call_blocks.append((i + 1, "\n".join(block_lines)))
            i += 1
        return call_blocks

    def test_all_dispatch_alerts_calls_pass_repo(self):
        """Every dispatch_alerts call (excluding the def itself) must include repo=."""
        blocks = self._get_dispatch_call_lines()
        assert len(blocks) >= 4, \
            f"Expected at least 4 dispatch_alerts call sites, found {len(blocks)}"
        missing = []
        for lineno, block in blocks:
            if "repo=" not in block:
                missing.append(f"Line ~{lineno}: {block[:120]!r}")
        assert not missing, (
            f"dispatch_alerts calls missing repo= parameter:\n" +
            "\n".join(missing)
        )

    def test_sprint_summary_dispatch_alerts_passes_repo(self):
        """The sprint summary dispatch_alerts call must include repo=eff_repo."""
        content = SPRINT_SCRIPT.read_text(encoding="utf-8")
        lines = content.splitlines()
        # Find write_sprint_summary function body
        in_fn = False
        fn_lines = []
        for line in lines:
            if "def write_sprint_summary(" in line:
                in_fn = True
            if in_fn:
                fn_lines.append(line)
                if line.strip().startswith("def ") and "write_sprint_summary" not in line:
                    break
        fn_body = "\n".join(fn_lines)
        if "dispatch_alerts(" in fn_body:
            # Find the specific call
            idx = fn_body.find("dispatch_alerts(")
            call_segment = fn_body[idx:idx+200]
            assert "repo=" in call_segment, \
                "write_sprint_summary dispatch_alerts call must pass repo="

    def test_run_sprint_coder_failure_dispatch_passes_repo(self):
        """The coder-failure dispatch_alerts call must include repo=eff_repo."""
        content = SPRINT_SCRIPT.read_text(encoding="utf-8")
        lines = content.splitlines()
        # Look for the coder failure block: "coder failed" context
        for i, line in enumerate(lines):
            if "coder failed" in line.lower() and "dispatch_alerts" not in line:
                # Look forward for dispatch_alerts within 20 lines
                block = "\n".join(lines[i:i+25])
                if "dispatch_alerts(" in block:
                    assert "repo=" in block, \
                        f"Coder-failure dispatch_alerts call near line {i+1} must pass repo="
                    return
        # If we didn't find it via context, let the all-calls test cover it

    def test_run_sprint_gate_failure_dispatch_passes_repo(self):
        """The gate/tester failure dispatch_alerts call must include repo=eff_repo."""
        content = SPRINT_SCRIPT.read_text(encoding="utf-8")
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if "gate failed" in line.lower() and "dispatch_alerts" not in line:
                block = "\n".join(lines[i:i+25])
                if "dispatch_alerts(" in block:
                    assert "repo=" in block, \
                        f"Gate-failure dispatch_alerts call near line {i+1} must pass repo="
                    return


# ---------------------------------------------------------------------------
# AC-4: Frontend repoPrefix renders [projectname]
# ---------------------------------------------------------------------------

class TestAC4FrontendRepoPrefixLogic:
    def test_repo_prefix_logic_exists_in_app_js(self):
        content = APP_JS.read_text(encoding="utf-8")
        assert "repoPrefix" in content, \
            "app.js must contain repoPrefix logic"

    def test_repo_prefix_uses_last_segment(self):
        """repoPrefix must split on '/' and take the last segment (repo name, not owner)."""
        content = APP_JS.read_text(encoding="utf-8")
        assert "split('/')" in content or 'split("/")' in content, \
            "repoPrefix must call split('/') to extract repo name from owner/repo"
        # Also verify it uses .pop() or similar to get the last segment
        assert ".pop()" in content or "slice(-1)" in content or "[1]" in content, \
            "repoPrefix must extract the repo name (last segment after split)"

    def test_repo_prefix_conditional_empty_when_no_repo(self):
        content = APP_JS.read_text(encoding="utf-8")
        # The prefix should be empty string when a.repo is falsy
        assert "a.repo ?" in content or "a.repo?" in content, \
            "repoPrefix must use ternary to be empty when a.repo is falsy"

    def test_repo_prefix_brackets_format(self):
        content = APP_JS.read_text(encoding="utf-8")
        # Should produce [reponame] prefix
        assert "[${" in content or "[ " in content or "`[`" in content or '`[' in content, \
            "repoPrefix must wrap the repo name in square brackets"


# ---------------------------------------------------------------------------
# AC-5: server.py AlertPayload has repo field; GET returns it
# ---------------------------------------------------------------------------

class TestAC5ServerRepoField:
    def test_alert_payload_model_has_repo_field(self):
        content = SERVER_PY.read_text(encoding="utf-8")
        assert "repo" in content, \
            "AlertPayload in server.py must define a 'repo' field"

    def test_alert_payload_repo_is_optional(self):
        content = SERVER_PY.read_text(encoding="utf-8")
        # Should have Optional[str] = None for repo
        assert "Optional[str]" in content, \
            "AlertPayload.repo must be Optional[str]"

    def test_get_alerts_returns_all_fields_including_repo(self):
        """GET /api/alerts must return full alert dict (including repo)."""
        content = SERVER_PY.read_text(encoding="utf-8")
        # model_dump() serialises all fields including repo
        assert "model_dump()" in content, \
            "receive_alert must store full model_dump() so repo is preserved"

    @pytest.mark.live
    def test_post_alert_with_repo_returns_201(self, client):
        r = client.post("/api/alerts", json={
            "title": "Issue #42 skipped: TESTER_REJECTED",
            "body": "smoke test",
            "repo": "zealchaiwut/commander",
            "category": "TESTER_REJECTED",
        })
        assert r.status_code == 201

    @pytest.mark.live
    def test_get_alerts_returns_repo_field(self, client):
        client.post("/api/alerts", json={
            "title": "Issue #42 skipped: TESTER_REJECTED",
            "body": "smoke test",
            "repo": "zealchaiwut/commander",
            "category": "TESTER_REJECTED",
        })
        alerts = client.get("/api/alerts").json()
        assert isinstance(alerts, list)
        # Find our alert
        matching = [a for a in alerts if "42 skipped" in a.get("title", "")]
        assert matching, "Alert just posted must appear in GET /api/alerts"
        assert matching[0].get("repo") == "zealchaiwut/commander"

    @pytest.mark.live
    def test_get_alerts_without_repo_returns_none(self, client):
        client.post("/api/alerts", json={
            "title": "Old alert without repo",
            "body": "no repo field",
        })
        alerts = client.get("/api/alerts").json()
        matching = [a for a in alerts if "Old alert without repo" in a.get("title", "")]
        assert matching, "Alert without repo must still appear in GET /api/alerts"
        assert matching[0].get("repo") is None


# ---------------------------------------------------------------------------
# AC-6: TESTER_REJECTED alert fires with repo via handle_post_tester
# ---------------------------------------------------------------------------

class TestAC6TesterRejectedAlertIncludesRepo:
    def test_handle_post_tester_passes_repo_to_dispatch_alerts(self):
        """handle_post_tester fires dispatch_alerts with repo=eff_repo on TESTER_REJECTED."""
        sm = _import_sprint_manager()

        with mock.patch.object(sm, "_get_issue_labels", return_value={"sprint-15", "backlog"}), \
             mock.patch.object(sm.github_client, "add_comment"), \
             mock.patch.object(sm, "dispatch_alerts") as mock_dispatch:

            sm.handle_post_tester(
                issue_num=42,
                tester_exit_code=0,
                skip_gates=False,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
                alert_modes=["dashboard-banner"],
                repo_name="zealchaiwut/commander",
            )

        mock_dispatch.assert_called_once()
        kwargs = mock_dispatch.call_args[1]
        assert kwargs.get("repo") == "zealchaiwut/commander", (
            "dispatch_alerts must be called with repo='zealchaiwut/commander' "
            f"in TESTER_REJECTED path; got kwargs={kwargs}"
        )

    def test_tester_rejected_alert_category_is_tester_rejected(self):
        """TESTER_REJECTED path must fire alert with category=TESTER_REJECTED."""
        sm = _import_sprint_manager()

        with mock.patch.object(sm, "_get_issue_labels", return_value={"backlog"}), \
             mock.patch.object(sm.github_client, "add_comment"), \
             mock.patch.object(sm, "dispatch_alerts") as mock_dispatch:

            merged, summary_line, category = sm.handle_post_tester(
                issue_num=42,
                tester_exit_code=0,
                skip_gates=False,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
                alert_modes=["dashboard-banner"],
                repo_name="zealchaiwut/commander",
            )

        assert category == sm.FailureCategory.TESTER_REJECTED
        kwargs = mock_dispatch.call_args[1]
        assert kwargs.get("category") == sm.FailureCategory.TESTER_REJECTED

    def test_tester_rejected_alert_title_contains_issue_num(self):
        """TESTER_REJECTED alert title must reference the issue number."""
        sm = _import_sprint_manager()

        with mock.patch.object(sm, "_get_issue_labels", return_value={"backlog"}), \
             mock.patch.object(sm.github_client, "add_comment"), \
             mock.patch.object(sm, "dispatch_alerts") as mock_dispatch:

            sm.handle_post_tester(
                issue_num=42,
                tester_exit_code=0,
                skip_gates=False,
                gate_pytest=True,
                gate_lint=True,
                gate_merge_preview=True,
                alert_modes=["dashboard-banner"],
                repo_name="zealchaiwut/commander",
            )

        kwargs = mock_dispatch.call_args[1]
        assert "42" in kwargs.get("title", ""), \
            "Alert title must contain the issue number"
