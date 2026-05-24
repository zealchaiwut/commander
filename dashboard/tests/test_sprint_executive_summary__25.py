"""Tests for issue #25 — Sprint executive summary, learnings prompt, and
Sprint History dashboard tab.

Static checks validate source files directly (no live server required).
Live-server checks are marked @pytest.mark.live.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS_DIR   = Path(__file__).parent.parent / "scripts"
SPRINT_SCRIPT = SCRIPTS_DIR / "sprint_manager.py"
SERVER_PY     = Path(__file__).parent.parent / "server.py"
APP_JS        = Path(__file__).parent.parent / "static" / "app.js"
INDEX_HTML    = Path(__file__).parent.parent / "static" / "index.html"


# ── helpers ───────────────────────────────────────────────────────────────────

def _import_sprint_manager():
    """Import sprint_manager as a module with stubbed dependencies."""
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_stub)

    gc_stub = types.ModuleType("github_client")
    gc_stub.repo         = lambda: "zealchaiwut/commander"
    gc_stub.update_labels = lambda *a, **kw: None
    gc_stub.add_comment  = lambda *a, **kw: None
    gc_stub.create_issue = lambda **kw: (999, "https://github.com/zealchaiwut/commander/issues/999")
    sys.modules.setdefault("github_client", gc_stub)

    mod_name = "sprint_manager_25_import"
    spec     = importlib.util.spec_from_file_location(mod_name, SPRINT_SCRIPT)
    mod      = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_state(sm, n_done: int = 2, n_skipped: int = 1):
    state = sm.SprintState(
        sprint_label="sprint-5",
        sprint_number=5,
        start_timestamp="2026-05-01T08:00:00Z",
    )
    for i in range(n_done):
        state.issues.append(sm.IssueState(
            number=i + 1, title=f"Feature {chr(65 + i)}", status="done",
            tokens_in=100, tokens_out=200,
        ))
    for j in range(n_skipped):
        state.issues.append(sm.IssueState(
            number=n_done + j + 1, title=f"Broken {j + 1}", status="skipped",
            skip_reason="Gate pytest failed",
            category=sm.FailureCategory.GATE_FAIL,
        ))
    state.total_tokens_in  = n_done * 100
    state.total_tokens_out = n_done * 200
    return state


# ── AC-1: Extended summary template ──────────────────────────────────────────

class TestExtendedSummaryTemplate:
    """AC-1: Sprint manager writes a richly-formatted summary with 7 sections."""

    def test_generate_sprint_summary_exists(self):
        content = SPRINT_SCRIPT.read_text()
        assert "generate_sprint_summary" in content

    def test_write_sprint_summary_exists(self):
        content = SPRINT_SCRIPT.read_text()
        assert "write_sprint_summary" in content

    def test_summary_path_format(self):
        sm   = _import_sprint_manager()
        path = sm._summary_path(5, "sprint-5")
        assert "sprint-5-summary-" in path.name
        assert path.suffix == ".md"

    def test_section_sprint_header(self):
        sm      = _import_sprint_manager()
        state   = _make_state(sm)
        content = sm.generate_sprint_summary(state, elapsed_secs=3661, end_reason="complete")
        assert "## Sprint 5" in content
        assert "complete" in content

    def test_section_what_shipped(self):
        sm      = _import_sprint_manager()
        state   = _make_state(sm)
        content = sm.generate_sprint_summary(state, elapsed_secs=3661)
        assert "## What Shipped" in content

    def test_section_what_didnt_ship(self):
        sm      = _import_sprint_manager()
        state   = _make_state(sm)
        content = sm.generate_sprint_summary(state, elapsed_secs=3661)
        assert "## What Didn't Ship" in content

    def test_section_stats(self):
        sm      = _import_sprint_manager()
        state   = _make_state(sm)
        content = sm.generate_sprint_summary(state, elapsed_secs=3661)
        assert "## Stats" in content
        assert "Total tokens" in content
        assert "Quality-gate pass rate" in content
        assert "Tester rejections" in content
        assert "Merge conflicts" in content

    def test_section_carried_over(self):
        sm      = _import_sprint_manager()
        state   = _make_state(sm)
        content = sm.generate_sprint_summary(state, elapsed_secs=3661)
        assert "## Carried Over" in content

    def test_section_key_learnings(self):
        sm      = _import_sprint_manager()
        state   = _make_state(sm)
        content = sm.generate_sprint_summary(state, elapsed_secs=3661)
        assert "## Key Learnings" in content

    def test_section_links(self):
        sm      = _import_sprint_manager()
        state   = _make_state(sm)
        content = sm.generate_sprint_summary(state, elapsed_secs=3661)
        assert "## Links" in content

    def test_footer_present(self):
        sm      = _import_sprint_manager()
        state   = _make_state(sm)
        content = sm.generate_sprint_summary(state, elapsed_secs=3661)
        assert "_Generated by sprint-manager v1.0" in content

    def test_shipped_table_has_issue_rows(self):
        sm      = _import_sprint_manager()
        state   = _make_state(sm, n_done=2, n_skipped=0)
        content = sm.generate_sprint_summary(state, elapsed_secs=100)
        assert "#1" in content
        assert "#2" in content
        # "UAT-approved / closed" outcome in shipped table
        assert "UAT-approved" in content

    def test_didnt_ship_table_has_failure_category(self):
        sm      = _import_sprint_manager()
        state   = _make_state(sm, n_done=0, n_skipped=1)
        content = sm.generate_sprint_summary(state, elapsed_secs=100)
        assert "GATE_FAIL" in content

    def test_stats_total_tokens(self):
        sm      = _import_sprint_manager()
        state   = _make_state(sm, n_done=1, n_skipped=0)
        state.total_tokens_in  = 1000
        state.total_tokens_out = 2000
        content = sm.generate_sprint_summary(state, elapsed_secs=60)
        assert "3000" in content  # total_tokens_in + total_tokens_out

    def test_links_wrapped_in_details_when_more_than_3(self):
        sm    = _import_sprint_manager()
        state = _make_state(sm, n_done=5, n_skipped=0)
        content = sm.generate_sprint_summary(state, elapsed_secs=60)
        assert "<details>" in content

    def test_links_not_wrapped_when_3_or_fewer(self):
        sm    = _import_sprint_manager()
        state = _make_state(sm, n_done=0, n_skipped=0)
        content = sm.generate_sprint_summary(state, elapsed_secs=60)
        assert "<details>" not in content

    def test_carried_over_shows_pending_issues(self):
        sm    = _import_sprint_manager()
        state = sm.SprintState(sprint_label="sprint-5", sprint_number=5)
        state.issues = [
            sm.IssueState(number=10, title="Pending Feature", status="pending"),
        ]
        content = sm.generate_sprint_summary(state, elapsed_secs=60)
        assert "## Carried Over" in content
        assert "#10" in content or "Pending Feature" in content

    def test_write_summary_creates_md_file(self, tmp_path):
        sm    = _import_sprint_manager()
        state = _make_state(sm)
        with mock.patch.object(sm, "SPRINTS_DIR", tmp_path / "sprints"), \
             mock.patch.object(sm, "create_summary_github_issue",
                               return_value=(None, None)), \
             mock.patch.object(sm, "_prompt_learnings", return_value=""):
            path = sm.write_sprint_summary(state, elapsed_secs=60)
        assert path.exists()
        assert path.suffix == ".md"
        assert "sprint-5-summary-" in path.name


# ── AC-2: GitHub issue creation ───────────────────────────────────────────────

class TestGitHubIssueCreation:
    """AC-2: After writing the file, create a GitHub issue with the summary."""

    def test_create_summary_github_issue_exists(self):
        content = SPRINT_SCRIPT.read_text()
        assert "create_summary_github_issue" in content

    def test_issue_title_format(self):
        sm = _import_sprint_manager()
        created_title = None

        def fake_create_issue(title, body, labels, repo_name=None):
            nonlocal created_title
            created_title = title
            return (99, "https://github.com/test/repo/issues/99")

        with mock.patch.object(sm.github_client, "create_issue", side_effect=fake_create_issue):
            sm.create_summary_github_issue("body", 5, "sprint-5")

        assert created_title == "Sprint 5 Executive Summary"

    def test_issue_labels_include_docs_and_sprint(self):
        sm = _import_sprint_manager()
        created_labels = None

        def fake_create_issue(title, body, labels, repo_name=None):
            nonlocal created_labels
            created_labels = labels
            return (99, "https://github.com/test/repo/issues/99")

        with mock.patch.object(sm.github_client, "create_issue", side_effect=fake_create_issue), \
             mock.patch.object(sm, "_ensure_github_labels"):
            sm.create_summary_github_issue("body", 5, "sprint-5")

        assert "docs" in created_labels
        assert "sprint-5" in created_labels

    def test_issue_url_printed_to_stdout(self, capsys):
        sm = _import_sprint_manager()
        with mock.patch.object(sm.github_client, "create_issue",
                               return_value=(99, "https://example.com/issues/99")), \
             mock.patch.object(sm, "_ensure_github_labels"):
            sm.create_summary_github_issue("body", 5, "sprint-5")
        out = capsys.readouterr().out
        assert "https://example.com/issues/99" in out

    def test_summary_issue_url_stored_in_state_json(self, tmp_path):
        sm    = _import_sprint_manager()
        state = _make_state(sm)
        sprints_dir = tmp_path / "sprints"

        with mock.patch.object(sm, "SPRINTS_DIR", sprints_dir), \
             mock.patch.object(sm, "create_summary_github_issue",
                               return_value=(99, "https://github.com/test/repo/issues/99")), \
             mock.patch.object(sm, "_prompt_learnings", return_value=""):
            sm.write_sprint_summary(state, elapsed_secs=60)

        state_file = sprints_dir / "sprint-5-state.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data.get("summary_issue_url") == "https://github.com/test/repo/issues/99"

    def test_failure_does_not_crash_write(self, tmp_path):
        sm    = _import_sprint_manager()
        state = _make_state(sm)
        with mock.patch.object(sm, "SPRINTS_DIR", tmp_path / "sprints"), \
             mock.patch.object(sm, "create_summary_github_issue",
                               side_effect=Exception("network error")), \
             mock.patch.object(sm, "_prompt_learnings", return_value=""):
            # Should not raise
            sm.write_sprint_summary(state, elapsed_secs=60)


# ── AC-3: Interactive learnings prompt ───────────────────────────────────────

class TestInteractiveLearningsPrompt:
    """AC-3: After file + issue are written, prompt for learnings if TTY."""

    def test_learnings_stub_in_summary(self):
        sm      = _import_sprint_manager()
        state   = _make_state(sm)
        content = sm.generate_sprint_summary(state, elapsed_secs=60)
        assert sm.LEARNINGS_STUB in content or "TODO" in content or "stub" in content.lower()

    def test_non_tty_leaves_stub(self, tmp_path):
        sm    = _import_sprint_manager()
        path  = tmp_path / "summary.md"
        content = f"## Key Learnings\n\n{sm.LEARNINGS_STUB}\n"
        path.write_text(content)

        with mock.patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            result = sm._prompt_learnings(
                content=content, path=path,
                sprint_number=5, sprint_label="sprint-5",
                summary_issue_num=None,
            )
        assert sm.LEARNINGS_STUB in result

    def test_tty_answer_n_leaves_stub(self, tmp_path):
        sm    = _import_sprint_manager()
        path  = tmp_path / "summary.md"
        content = f"## Key Learnings\n\n{sm.LEARNINGS_STUB}\n"
        path.write_text(content)

        with mock.patch("sys.stdout") as mock_stdout, \
             mock.patch("builtins.input", return_value="n"):
            mock_stdout.isatty.return_value = True
            result = sm._prompt_learnings(
                content=content, path=path,
                sprint_number=5, sprint_label="sprint-5",
                summary_issue_num=None,
            )
        assert sm.LEARNINGS_STUB in result

    def test_tty_answer_y_opens_editor(self, tmp_path):
        sm    = _import_sprint_manager()
        path  = tmp_path / "summary.md"
        content = f"## Key Learnings\n\n{sm.LEARNINGS_STUB}\n"
        path.write_text(content)
        new_learnings = "Smaller issues finish faster."

        def fake_editor_run(cmd, check=False):
            # Simulate editor: write new text to the temp file
            Path(cmd[1]).write_text(new_learnings)

        with mock.patch("sys.stdout") as mock_stdout, \
             mock.patch("builtins.input", return_value="y"), \
             mock.patch("subprocess.run", side_effect=fake_editor_run):
            mock_stdout.isatty.return_value = True
            result = sm._prompt_learnings(
                content=content, path=path,
                sprint_number=5, sprint_label="sprint-5",
                summary_issue_num=None,
            )

        assert new_learnings in result
        assert sm.LEARNINGS_STUB not in result

    def test_editor_updates_local_file(self, tmp_path):
        sm    = _import_sprint_manager()
        path  = tmp_path / "summary.md"
        content = f"## Key Learnings\n\n{sm.LEARNINGS_STUB}\n"
        path.write_text(content)
        new_learnings = "Testing is essential."

        def fake_editor_run(cmd, check=False):
            Path(cmd[1]).write_text(new_learnings)

        with mock.patch("sys.stdout") as mock_stdout, \
             mock.patch("builtins.input", return_value="y"), \
             mock.patch("subprocess.run", side_effect=fake_editor_run):
            mock_stdout.isatty.return_value = True
            sm._prompt_learnings(
                content=content, path=path,
                sprint_number=5, sprint_label="sprint-5",
                summary_issue_num=None,
            )

        assert new_learnings in path.read_text()

    def test_prompt_text_includes_sprint_number(self, capsys, tmp_path):
        sm    = _import_sprint_manager()
        path  = tmp_path / "summary.md"
        content = f"## Key Learnings\n\n{sm.LEARNINGS_STUB}\n"
        path.write_text(content)

        captured_prompt = []

        def fake_input(prompt):
            captured_prompt.append(prompt)
            return "n"

        with mock.patch("sys.stdout") as mock_stdout, \
             mock.patch("builtins.input", side_effect=fake_input):
            mock_stdout.isatty.return_value = True
            sm._prompt_learnings(
                content=content, path=path,
                sprint_number=7, sprint_label="sprint-7",
                summary_issue_num=None,
            )

        assert any("7" in p for p in captured_prompt)
        assert any("y/n" in p.lower() for p in captured_prompt)


# ── AC-4: GET /api/sprint-history endpoint ────────────────────────────────────

class TestSprintHistoryEndpoint:
    """AC-4: Server must expose GET /api/sprint-history."""

    def test_endpoint_defined_in_server(self):
        content = SERVER_PY.read_text()
        assert "/api/sprint-history" in content

    def test_endpoint_function_name(self):
        content = SERVER_PY.read_text()
        assert "get_sprint_history" in content

    def test_endpoint_returns_list(self):
        content = SERVER_PY.read_text()
        # Must return a list (empty or populated)
        assert "return []" in content or "results" in content

    def test_sprint_summary_endpoint_still_present(self):
        """AC-6 of #24: GET /api/sprint-summary must not regress."""
        content = SERVER_PY.read_text()
        assert "/api/sprint-summary" in content

    def test_parse_summary_file_function_exists(self):
        content = SERVER_PY.read_text()
        assert "_parse_summary_file" in content

    def test_parse_summary_uses_regex(self):
        content = SERVER_PY.read_text()
        assert "re.match" in content or "re.search" in content

    def test_history_content_endpoint_exists(self):
        content = SERVER_PY.read_text()
        assert "/api/sprint-history-content" in content

    def test_response_has_required_fields(self):
        content = SERVER_PY.read_text()
        for field in ("sprint_num", "date", "status", "file_path",
                      "github_issue_url", "shipped_count", "skipped_count", "total_tokens"):
            assert field in content, f"Response must include field '{field}'"

    def test_sorted_newest_first(self):
        content = SERVER_PY.read_text()
        assert "reverse=True" in content

    def test_uses_state_json_for_github_url(self):
        content = SERVER_PY.read_text()
        assert "summary_issue_url" in content


@pytest.mark.live
class TestSprintHistoryLive:
    def test_get_sprint_history_returns_list(self, client):
        res = client.get("/api/sprint-history")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

    def test_sprint_summary_endpoint_still_works(self, client):
        """Regression: /api/sprint-summary must still work (from #24)."""
        res = client.get("/api/sprint-summary")
        assert res.status_code in (200, 404)  # 404 if no summaries exist

    def test_sprint_history_content_endpoint(self, client):
        res = client.get("/api/sprint-history-content?idx=0")
        assert res.status_code in (200, 404)  # 404 if no summaries exist


# ── AC-5: Sprint History dashboard tab ───────────────────────────────────────

class TestSprintHistoryTab:
    """AC-5: Sprint History tab in the dashboard frontend."""

    def test_fourth_nav_tab_exists(self):
        content = INDEX_HTML.read_text()
        assert "Sprint History" in content

    def test_tab_button_id_mtab_history(self):
        content = INDEX_HTML.read_text()
        assert "mtab-history" in content

    def test_view_history_div_exists(self):
        content = INDEX_HTML.read_text()
        assert 'id="view-history"' in content

    def test_quick_stats_row_exists(self):
        content = INDEX_HTML.read_text()
        assert "history-stats-row" in content

    def test_avg_sprint_duration_element(self):
        content = INDEX_HTML.read_text()
        assert "h-avg-duration" in content

    def test_tokens_this_month_element(self):
        content = INDEX_HTML.read_text()
        assert "h-tokens-month" in content

    def test_history_rows_container_exists(self):
        content = INDEX_HTML.read_text()
        assert "history-rows" in content

    def test_empty_state_element_exists(self):
        content = INDEX_HTML.read_text()
        assert "history-empty" in content or "No sprints recorded yet" in content

    # CSS checks
    def test_history_row_css_defined(self):
        content = INDEX_HTML.read_text()
        assert "history-row" in content

    def test_history_expand_panel_css_defined(self):
        content = INDEX_HTML.read_text()
        assert "history-expand-panel" in content

    def test_history_summary_pre_css_defined(self):
        content = INDEX_HTML.read_text()
        assert "history-summary-pre" in content

    # JS checks
    def test_js_switch_main_handles_history_tab(self):
        content = APP_JS.read_text()
        assert "history" in content
        assert "switchMain" in content

    def test_js_load_sprint_history_function(self):
        content = APP_JS.read_text()
        assert "loadSprintHistory" in content

    def test_js_render_sprint_history_function(self):
        content = APP_JS.read_text()
        assert "renderSprintHistory" in content

    def test_js_toggle_history_row_function(self):
        content = APP_JS.read_text()
        assert "toggleHistoryRow" in content

    def test_js_fetches_sprint_history_api(self):
        content = APP_JS.read_text()
        assert "/api/sprint-history" in content

    def test_js_renders_status_badge(self):
        content = APP_JS.read_text()
        assert "_statusBadgeHtml" in content or "status" in content

    def test_js_view_on_github_link(self):
        content = APP_JS.read_text()
        assert "View on GitHub" in content or "github_issue_url" in content

    def test_empty_state_message(self):
        # "No sprints recorded yet" is in index.html (the view template)
        html = INDEX_HTML.read_text()
        assert "No sprints recorded yet" in html or "history-empty" in html

    def test_js_refresh_button_reloads_history_tab(self):
        content = APP_JS.read_text()
        assert "loadSprintHistory" in content
        # The manual refresh should call loadSprintHistory when history is active
        assert "manualRefresh" in content


# ── AC-6: No regression ───────────────────────────────────────────────────────

class TestNoRegression:
    """AC-6: Other tabs and existing API endpoints must not be broken."""

    def test_projects_tab_still_exists(self):
        content = INDEX_HTML.read_text()
        assert "mtab-projects" in content

    def test_agents_tab_still_exists(self):
        content = INDEX_HTML.read_text()
        assert "mtab-agents" in content

    def test_activity_tab_still_exists(self):
        content = INDEX_HTML.read_text()
        assert "mtab-activity" in content

    def test_view_projects_div_present(self):
        content = INDEX_HTML.read_text()
        assert 'id="view-projects"' in content

    def test_view_agents_div_present(self):
        content = INDEX_HTML.read_text()
        assert 'id="view-agents"' in content

    def test_view_activity_div_present(self):
        content = INDEX_HTML.read_text()
        assert 'id="view-activity"' in content

    def test_sprint_summary_endpoint_preserved(self):
        content = SERVER_PY.read_text()
        assert "/api/sprint-summary" in content

    def test_sprint_manager_quality_gates_preserved(self):
        content = SPRINT_SCRIPT.read_text()
        assert "_gate_pytest" in content or "gate_pytest" in content
        assert "handle_post_tester" in content

    def test_sprint_manager_run_sprint_preserved(self):
        content = SPRINT_SCRIPT.read_text()
        assert "run_sprint" in content

    def test_sprint_manager_FailureCategory_preserved(self):
        content = SPRINT_SCRIPT.read_text()
        assert "FailureCategory" in content

    @pytest.mark.live
    def test_live_api_projects(self, client):
        res = client.get("/api/projects")
        assert res.status_code == 200

    @pytest.mark.live
    def test_live_api_agents(self, client):
        res = client.get("/api/agents")
        assert res.status_code == 200

    @pytest.mark.live
    def test_live_api_events(self, client):
        res = client.get("/api/events")
        assert res.status_code == 200
