"""The sprint subprocess gets GH_TOKEN so `gh` works without the macOS Keychain.

Root cause: the dashboard is launchd-spawned; its grandchild sprint subprocess
can't read the Keychain where `gh` stores auth, so `gh issue list` returns empty
and a sprint reports "No dispatchable issues" though the tickets exist. The env
builder injects a token (honoring a preset GH_TOKEN/GITHUB_TOKEN, else extracting
via `gh auth token`) so the subprocess's gh uses it directly.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_DS = Path(__file__).parent.parent / "apps" / "dashboard" / "routers" / "dispatch_service.py"


def _load():
    spec = importlib.util.spec_from_file_location("ds_under_test", _DS)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_honors_preset_gh_token_and_strips_anthropic(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_preset")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    ds = _load()
    env = ds.build_sprint_subprocess_env()
    assert env["GH_TOKEN"] == "ghp_preset"
    assert "ANTHROPIC_API_KEY" not in env


def test_falls_back_to_github_token(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_github")
    ds = _load()
    assert ds.build_sprint_subprocess_env()["GH_TOKEN"] == "ghp_github"


def test_extracts_via_gh_when_unset(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    ds = _load()

    class _R:
        returncode = 0
        stdout = "gho_extracted\n"
        stderr = ""

    monkeypatch.setattr(ds.subprocess, "run", lambda *a, **k: _R())
    assert ds.build_sprint_subprocess_env()["GH_TOKEN"] == "gho_extracted"


def test_no_token_when_gh_unavailable(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    ds = _load()

    def _boom(*a, **k):
        raise FileNotFoundError("gh not found")

    monkeypatch.setattr(ds.subprocess, "run", _boom)
    assert "GH_TOKEN" not in ds.build_sprint_subprocess_env()
