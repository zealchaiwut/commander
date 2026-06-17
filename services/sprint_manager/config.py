"""Sprint config loading — SprintConfig dataclass and related helpers.

Extracted from sprint_manager.py (issue #1269) — pure move, no logic changes.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml  # PyYAML — already in requirements.txt
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

# ── path constants (mirrors sprint_manager.py path setup) ───────────────────
# This file lives at services/sprint_manager/config.py
# Repo root is three levels up: config.py → sprint_manager/ → services/ → root
_REPO_ROOT = Path(__file__).parent.parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
_SCRIPTS_DIR = _REPO_ROOT / "scripts"

_WORKTESTER_ROOT = Path(os.environ.get(
    "WORKTESTER_ROOT",
    Path.home() / "dev" / "commander" / "tester",
))
_DASHBOARD_API_URL = os.environ.get("DASHBOARD_API_URL", "http://localhost:8000")
_SPRINTS_DIR = _DASHBOARD_DIR / "sprints"
_ALERTS_DIR = _DASHBOARD_DIR / "alerts"

_DEFAULT_CODER_BY_SIZE: dict = {
    "S": "claude-haiku-4-5",
    "M": "claude-sonnet-4-6",
    "L": "claude-sonnet-4-6",
    "XL": "claude-sonnet-4-6",
}


# ── SprintConfig dataclass ───────────────────────────────────────────────────

@dataclass
class SprintConfig:
    """All runtime paths and settings for a sprint.

    Replaces the six module-level path constants.  When no config file is
    present the class is populated with the same env-var + hardcoded defaults
    that existed before this ticket, so backward compatibility is preserved.
    """
    repo_name: Optional[str] = None
    worktree_coder: Path = field(default_factory=lambda: Path.home() / "commander" / "work-coder")
    worktree_tester: Path = field(default_factory=lambda: _WORKTESTER_ROOT)
    # Dedicated clone for non-coding sprint agents (documenter, reviewer) so they
    # never check out feature branches in the coder/tester worktrees (which are
    # mid-sprint) or — worse — the serving uat clone. Optional; falls back to the
    # tester/coder worktree when unset.
    worktree_agents: Optional[Path] = None
    tester_app_subdir: str = "apps/dashboard"
    scripts_dir: Path = field(default_factory=lambda: _SCRIPTS_DIR)
    logs_dir: Path = field(default_factory=lambda: _DASHBOARD_DIR / "logs")
    sprints_dir: Path = field(default_factory=lambda: _SPRINTS_DIR)
    alerts_dir: Path = field(default_factory=lambda: _ALERTS_DIR)
    api_url: str = field(default_factory=lambda: _DASHBOARD_API_URL)
    coder_prompt_template: Optional[str] = None
    tester_prompt_template: Optional[str] = None
    # Port detection (issue #62)
    app_default_port: Optional[int] = None
    app_port_strategy: str = "prefer_default"
    # Documentor (issue #103)
    documentor_enabled: bool = False
    # Concurrent pipeline mode (issue #737) — per-project opt-in, default serial
    pipeline_mode: bool = False
    # Reviewer (issue #159)
    reviewer_prompt_template: Optional[str] = None
    # Documenter (issue #165)
    documenter_prompt_template: Optional[str] = None
    # Agent models (issue #700) — defaults match current hardcoded values
    coder_model: str = "claude-sonnet-4-6"
    tester_model: str = "claude-sonnet-4-6"
    reviewer_model: str = "claude-haiku-4-5"
    estimator_model: str = "claude-sonnet-4-6"
    documentor_model: str = "claude-sonnet-4-6"
    # Risk-tier model routing for tester (issue #790)
    tester_by_risk: dict = field(default_factory=lambda: {
        "LOW": "claude-haiku-4-5",
        "MEDIUM": "claude-haiku-4-5",
        "HIGH": "claude-sonnet-4-6",
    })
    # Size-tier model routing for coder (issue #789)
    coder_by_size: dict = field(default_factory=lambda: dict(_DEFAULT_CODER_BY_SIZE))
    # Alternate coder dispatch backend (issue #917) — default claude-code keeps existing behavior
    coder_backend: str = "claude-code"
    # Cline-specific model id (agent_config.cline.model) — separate namespace from coder_model
    cline_model: Optional[str] = None
    # Route follow-up tickets to Cline (issue #918) — default off; opt in per sprint
    use_cline_followups: bool = False

    @property
    def worktree_tester_app(self) -> Path:
        """Resolved path where tests/app lives inside the tester worktree."""
        if self.tester_app_subdir:
            return self.worktree_tester / self.tester_app_subdir
        return self.worktree_tester

    @property
    def finish_feature_script(self) -> Path:
        return self.scripts_dir / "finish_feature.py"


def _resolve_path(raw: str, base_dir: Path) -> Path:
    """Expand ~ and resolve relative paths against base_dir."""
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    return (base_dir / p).resolve()


def load_config(path: Path) -> "SprintConfig":
    """Parse .commander/sprint.yaml and return a SprintConfig.

    Relative paths in paths.* are resolved relative to the YAML file's
    directory.  Raises SystemExit on validation errors.
    """
    if yaml is None:
        sys.exit("PyYAML is not installed. Install it with: pip install pyyaml")

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        sys.exit(f"Cannot read config file {path}: {e}")

    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        sys.exit(f"YAML parse error in {path}: {e}")

    base_dir = path.parent  # directory containing sprint.yaml

    # ── required fields ───────────────────────────────────────────────────────
    missing = []
    repo_name = os.environ.get("COMMANDER_REPO", "").strip() or (data.get("repo_name") or "").strip()
    if not repo_name:
        missing.append("repo_name")

    wt = data.get("worktrees") or {}
    coder_raw = (wt.get("coder") or "").strip()
    tester_raw = (wt.get("tester") or "").strip()
    if not coder_raw:
        missing.append("worktrees.coder")
    if not tester_raw:
        missing.append("worktrees.tester")

    if missing:
        sys.exit(
            f"Config file {path} is missing required field(s): "
            + ", ".join(missing)
        )

    worktree_coder = _resolve_path(coder_raw, base_dir)
    worktree_tester = _resolve_path(tester_raw, base_dir)
    # Optional dedicated agents clone (documenter/reviewer). Falls back to None
    # (→ tester/coder worktree) when not configured.
    agents_raw = (wt.get("agents") or "").strip()
    worktree_agents = _resolve_path(agents_raw, base_dir) if agents_raw else None
    tester_app_subdir = (wt.get("tester_app_subdir") or "")

    # ── validate worktree paths ────────────────────────────────────────────────
    path_errors = []
    if not worktree_coder.exists():
        path_errors.append(f"worktrees.coder path does not exist: {worktree_coder}")
    if not worktree_tester.exists():
        path_errors.append(f"worktrees.tester path does not exist: {worktree_tester}")
    if path_errors:
        sys.exit("Config validation error:\n  " + "\n  ".join(path_errors))

    # ── optional paths ────────────────────────────────────────────────────────
    paths = data.get("paths") or {}

    scripts_raw = (paths.get("scripts_dir") or "").strip()
    scripts_dir = _resolve_path(scripts_raw, base_dir) if scripts_raw else _SCRIPTS_DIR

    logs_raw = (paths.get("logs_dir") or "").strip()
    logs_dir = _resolve_path(logs_raw, base_dir) if logs_raw else base_dir / "logs"

    sprints_raw = (paths.get("sprints_dir") or "").strip()
    sprints_dir = _resolve_path(sprints_raw, base_dir) if sprints_raw else base_dir / "sprints"

    alerts_raw = (paths.get("alerts_dir") or "").strip()
    alerts_dir = _resolve_path(alerts_raw, base_dir) if alerts_raw else base_dir / "alerts"

    # ── dashboard section ─────────────────────────────────────────────────────
    dashboard = data.get("dashboard") or {}
    # A clone-specific DASHBOARD_API_URL (set in that clone's apps/dashboard/.env,
    # loaded via load_dotenv at import) overrides the shared sprint.yaml value: the
    # project-root yaml can't tell PRD (8000) from UAT (8001), so when the running
    # clone's own .env declares the URL it wins. Falls back to yaml, then hardcoded.
    _env_api_url = os.environ.get("DASHBOARD_API_URL")
    api_url = (_env_api_url or dashboard.get("api_url") or _DASHBOARD_API_URL).strip()

    # ── agents section ────────────────────────────────────────────────────────
    agents = data.get("agents") or {}
    coder_prompt = agents.get("coder_prompt_template") or None
    tester_prompt = agents.get("tester_prompt_template") or None

    # ── app section (issue #62: per-project port detection) ───────────────────
    app_section = data.get("app") or {}
    app_default_port: Optional[int] = None
    app_port_strategy: str = "prefer_default"
    if app_section:
        raw_port = app_section.get("default_port")
        if raw_port is not None:
            try:
                app_default_port = int(raw_port)
            except (TypeError, ValueError):
                sys.exit(f"Config error: app.default_port must be an integer, got {raw_port!r}")
        raw_strategy = (app_section.get("port_strategy") or "prefer_default").strip()
        if raw_strategy not in ("prefer_default", "always_random"):
            sys.exit(
                f"Config error: app.port_strategy must be 'prefer_default' or 'always_random', "
                f"got {raw_strategy!r}"
            )
        app_port_strategy = raw_strategy

    # ── documentor section (issue #103) ──────────────────────────────────────
    documentor_enabled: bool = bool(data.get("documentor_enabled", False))

    # ── pipeline mode (issue #737) ───────────────────────────────────────────
    pipeline_mode: bool = bool(data.get("pipeline_mode", False))

    # ── reviewer section (issue #159) ────────────────────────────────────────
    reviewer_prompt = agents.get("reviewer_prompt_template") or None

    # ── documenter section (issue #165) ──────────────────────────────────────
    documenter_prompt = agents.get("documenter_prompt_template") or None

    # ── agent_config section (issue #700) ────────────────────────────────────
    agent_cfg = data.get("agent_config") or {}
    _default_model: Optional[str] = (
        (agent_cfg.get("default_model") or None) if isinstance(agent_cfg, dict) else None
    )

    def _resolve_model(key: str, hardcoded: str) -> str:
        """Return per-agent override → default_model → hardcoded, in that order."""
        if isinstance(agent_cfg, dict) and key in agent_cfg:
            return str(agent_cfg[key])
        if _default_model:
            return _default_model
        return hardcoded

    coder_model = _resolve_model("coder_model", "claude-sonnet-4-6")
    tester_model = _resolve_model("tester_model", "claude-sonnet-4-6")
    reviewer_model = _resolve_model("reviewer_model", "claude-haiku-4-5")
    estimator_model = _resolve_model("estimator_model", "claude-sonnet-4-6")
    documentor_model = _resolve_model("documentor_model", "claude-sonnet-4-6")

    # ── agent_config.tester.by_risk (issue #790) ─────────────────────────────
    _default_by_risk = {
        "LOW": "claude-haiku-4-5",
        "MEDIUM": "claude-haiku-4-5",
        "HIGH": "claude-sonnet-4-6",
    }
    tester_by_risk: dict = _default_by_risk.copy()
    if isinstance(agent_cfg, dict):
        _tester_sub = agent_cfg.get("tester") or {}
        if isinstance(_tester_sub, dict) and _tester_sub.get("by_risk"):
            _from_yaml = _tester_sub["by_risk"]
            if isinstance(_from_yaml, dict):
                tester_by_risk.update({str(k): str(v) for k, v in _from_yaml.items()})

    # ── agent_config.coder.by_size (issue #789) ──────────────────────────────
    coder_by_size: dict = dict(_DEFAULT_CODER_BY_SIZE)
    if isinstance(agent_cfg, dict):
        _coder_sub = agent_cfg.get("coder") or {}
        if isinstance(_coder_sub, dict) and _coder_sub.get("by_size"):
            _from_yaml_size = _coder_sub["by_size"]
            if isinstance(_from_yaml_size, dict):
                coder_by_size.update({str(k).upper(): str(v) for k, v in _from_yaml_size.items()})

    # ── agent_config.coder.backend (issue #917) ───────────────────────────────
    coder_backend: str = "claude-code"
    if isinstance(agent_cfg, dict):
        _coder_sub_b = agent_cfg.get("coder") or {}
        if isinstance(_coder_sub_b, dict) and _coder_sub_b.get("backend"):
            coder_backend = str(_coder_sub_b["backend"])

    # ── agent_config.use_cline_followups (issue #918) ────────────────────────
    use_cline_followups: bool = False
    if isinstance(agent_cfg, dict):
        _ucf = agent_cfg.get("use_cline_followups")
        if _ucf is not None:
            use_cline_followups = bool(_ucf)

    # ── agent_config.cline.model — Cline CLI model id (not Claude Code names) ─
    cline_model: Optional[str] = None
    if isinstance(agent_cfg, dict):
        _cline_sub = agent_cfg.get("cline") or {}
        if isinstance(_cline_sub, dict) and _cline_sub.get("model"):
            cline_model = str(_cline_sub["model"])

    return SprintConfig(
        repo_name=repo_name,
        worktree_coder=worktree_coder,
        worktree_tester=worktree_tester,
        worktree_agents=worktree_agents,
        tester_app_subdir=tester_app_subdir,
        scripts_dir=scripts_dir,
        logs_dir=logs_dir,
        sprints_dir=sprints_dir,
        alerts_dir=alerts_dir,
        api_url=api_url,
        coder_prompt_template=coder_prompt,
        tester_prompt_template=tester_prompt,
        app_default_port=app_default_port,
        app_port_strategy=app_port_strategy,
        documentor_enabled=documentor_enabled,
        pipeline_mode=pipeline_mode,
        reviewer_prompt_template=reviewer_prompt,
        documenter_prompt_template=documenter_prompt,
        coder_model=coder_model,
        tester_model=tester_model,
        reviewer_model=reviewer_model,
        estimator_model=estimator_model,
        documentor_model=documentor_model,
        tester_by_risk=tester_by_risk,
        coder_by_size=coder_by_size,
        coder_backend=coder_backend,
        cline_model=cline_model,
        use_cline_followups=use_cline_followups,
    )


def discover_config(start_dir: Optional[Path] = None) -> Optional[Path]:
    """Walk up from start_dir looking for .commander/sprint.yaml.

    Returns the Path if found, None otherwise.
    """
    if start_dir is None:
        start_dir = Path.cwd()
    current = start_dir.resolve()
    while True:
        candidate = current / ".commander" / "sprint.yaml"
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:  # reached filesystem root
            break
        current = parent
    return None


def _default_config() -> "SprintConfig":
    """Build a SprintConfig from env-vars + hardcoded defaults (backward compat)."""
    _dev = Path.home() / "dev" / "commander"
    return SprintConfig(
        repo_name=None,  # will use github_client.repo()
        worktree_coder=_dev / "coder",
        worktree_tester=_WORKTESTER_ROOT,
        tester_app_subdir="apps/dashboard",
        scripts_dir=_SCRIPTS_DIR,
        logs_dir=_DASHBOARD_DIR / "logs",
        sprints_dir=_SPRINTS_DIR,
        alerts_dir=_ALERTS_DIR,
        api_url=_DASHBOARD_API_URL,
        documentor_enabled=False,
    )
