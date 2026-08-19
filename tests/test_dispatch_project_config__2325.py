"""AC tests for reading the project sprint.yaml at dispatch (issue #2325).

Run 94914e4a8e47 sent `/coder <url>` to an agent running in the viral-radar
clone. That slash command exists only in commander's `.claude/commands/`, so
the agent replied "Unknown command: /coder" and did nothing, across five
tickets. The project's own config had the answer all along.

No live HTTP and no agents: configs are written to tmp_path and `spawn` is
injected.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.sprint_manager.dispatch_runner import (  # noqa: E402
    DispatchConfigError,
    DispatchRun,
    ProjectDispatchConfig,
    default_spawn,
    execute_run,
    load_project_config,
)

SPRINT_YAML = """\
repo_name: zealchaiwut/viral-radar
agent_config:
  coder_model: claude-sonnet-4-6
agents:
  coder_prompt_template: 'Read the issue at {issue_url} and implement it.'
  tester_prompt_template: 'Read the issue at {issue_url} and verify it.'
worktrees:
  coder: /tmp/vr/coder
  tester: /tmp/vr/tester
"""


def _write_config(tmp_path: Path, body: str = SPRINT_YAML) -> Path:
    project = tmp_path / "viral-radar"
    (project / ".commander").mkdir(parents=True)
    (project / ".commander" / "sprint.yaml").write_text(body, encoding="utf-8")
    clone = project / "coder"
    clone.mkdir()
    return clone


# --- Loading ---------------------------------------------------------------

def test_config_is_loaded_from_the_project_sprint_yaml(tmp_path):
    clone = _write_config(tmp_path)
    cfg = load_project_config(clone)

    assert cfg.repo_name == "zealchaiwut/viral-radar"
    assert cfg.coder_model == "claude-sonnet-4-6"
    assert cfg.coder_worktree == Path("/tmp/vr/coder")
    assert cfg.tester_worktree == Path("/tmp/vr/tester")


def test_discovery_walks_up_from_the_clone(tmp_path):
    """sprint.yaml sits at the project root, outside the clone being dispatched."""
    clone = _write_config(tmp_path)
    assert load_project_config(clone).repo_name == "zealchaiwut/viral-radar"


def test_prompt_template_substitutes_the_issue_url(tmp_path):
    cfg = load_project_config(_write_config(tmp_path))
    prompt = cfg.prompt_for("coder", "https://github.com/o/r/issues/80")

    assert "https://github.com/o/r/issues/80" in prompt
    assert "{issue_url}" not in prompt
    # And it must NOT be a slash command.
    assert not prompt.lstrip().startswith("/coder")


def test_each_step_gets_its_own_template_and_worktree(tmp_path):
    cfg = load_project_config(_write_config(tmp_path))

    assert "implement it" in cfg.prompt_for("coder", "u")
    assert "verify it" in cfg.prompt_for("tester", "u")
    assert cfg.worktree_for("coder") != cfg.worktree_for("tester")


# --- Failing loudly --------------------------------------------------------

def test_missing_sprint_yaml_raises(tmp_path):
    lonely = tmp_path / "nothing-here"
    lonely.mkdir()
    with pytest.raises(DispatchConfigError) as exc:
        load_project_config(lonely)
    assert "sprint.yaml" in str(exc.value)


@pytest.mark.parametrize(
    "missing_key",
    ["coder_prompt_template", "tester_prompt_template"],
)
def test_missing_prompt_template_raises(tmp_path, missing_key):
    body = SPRINT_YAML.replace(f"  {missing_key}:", "  unused_key:")
    clone = _write_config(tmp_path, body)
    with pytest.raises(DispatchConfigError) as exc:
        load_project_config(clone)
    assert missing_key in str(exc.value)


def test_missing_worktree_raises(tmp_path):
    body = SPRINT_YAML.replace("  tester: /tmp/vr/tester", "")
    clone = _write_config(tmp_path, body)
    with pytest.raises(DispatchConfigError) as exc:
        load_project_config(clone)
    assert "worktrees.tester" in str(exc.value)


def test_unparseable_yaml_raises(tmp_path):
    clone = _write_config(tmp_path, "{{{ not yaml")
    with pytest.raises(DispatchConfigError):
        load_project_config(clone)


# --- No silent fallback to a slash command --------------------------------

def test_default_spawn_refuses_without_a_prompt():
    """The old behaviour built `/coder <url>`; that must not come back."""
    with pytest.raises(DispatchConfigError) as exc:
        default_spawn("coder", 80, "o/r", cwd=Path("/tmp"), baseline_note="x")
    assert "refusing to guess" in str(exc.value)


def test_source_no_longer_builds_a_slash_command():
    src = (REPO_ROOT / "services" / "sprint_manager" / "dispatch_runner.py").read_text()
    assert 'f"/{step} {url}' not in src, "slash-command prompt must not return"


# --- Wiring through execute_run -------------------------------------------

def test_execute_run_uses_per_step_prompt_and_worktree(tmp_path):
    cfg = ProjectDispatchConfig(
        repo_name="o/r",
        coder_prompt="CODE {issue_url}",
        tester_prompt="TEST {issue_url}",
        coder_worktree=Path("/tmp/wt-coder"),
        tester_worktree=Path("/tmp/wt-tester"),
        coder_model="claude-sonnet-4-6",
    )
    seen = []

    def spawn(step, issue, repo, *, cwd, baseline_note, prompt=None, model=None):
        seen.append({"step": step, "cwd": cwd, "prompt": prompt, "model": model})
        return True, "ok"

    execute_run(
        DispatchRun(run_id="r1", sprint_label="sprint-7", tickets=[80], repo="o/r"),
        repo_root=tmp_path, cwd=tmp_path, spawn=spawn, config=cfg,
    )

    coder, tester = seen
    assert coder["cwd"] == Path("/tmp/wt-coder")
    assert tester["cwd"] == Path("/tmp/wt-tester")
    assert coder["prompt"].startswith("CODE https://github.com/o/r/issues/80")
    assert tester["prompt"].startswith("TEST https://github.com/o/r/issues/80")
    # Model comes from the project config for the coder step.
    assert coder["model"] == "claude-sonnet-4-6"


def test_preamble_still_reaches_the_agent(tmp_path):
    """The #2315 quality-bar contract must survive the prompt change."""
    cfg = ProjectDispatchConfig(
        repo_name="o/r", coder_prompt="CODE {issue_url}", tester_prompt="TEST {issue_url}",
        coder_worktree=tmp_path, tester_worktree=tmp_path,
    )
    captured = {}

    def spawn(step, issue, repo, *, cwd, baseline_note, prompt=None, model=None):
        # Keyed by step — a single dict would hold only the tester's call.
        captured[step] = {"prompt": prompt, "note": baseline_note}
        return True, "ok"

    execute_run(
        DispatchRun(run_id="r2", sprint_label="s", tickets=[1], repo="o/r"),
        repo_root=tmp_path, cwd=tmp_path, spawn=spawn, config=cfg,
        baseline_note="75 failed / 954 passed",
    )
    # execute_run passes the raw template; default_spawn appends the preamble.
    assert captured["coder"]["note"] == "75 failed / 954 passed"
    assert captured["tester"]["note"] == "75 failed / 954 passed"
    assert "CODE" in captured["coder"]["prompt"]
    assert "TEST" in captured["tester"]["prompt"]
