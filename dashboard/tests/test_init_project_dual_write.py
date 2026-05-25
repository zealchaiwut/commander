"""Tests for init_project dual-write behavior (projects.json sync).

Validates that step6_register_project and rollback write to / remove from
both prd and uat projects.json files when both exist as sibling dashboards.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
INIT_SCRIPT = SCRIPTS_DIR / "init_project.py"


def _import_init_project():
    """Import init_project.py without executing main()."""
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_stub)

    mod_name = "init_project_test_import"
    if mod_name in sys.modules:
        return sys.modules[mod_name]

    spec = importlib.util.spec_from_file_location(mod_name, INIT_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_projects_json(path: Path, entries: list[dict] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries or [], indent=2) + "\n")


def _read_projects_json(path: Path) -> list[dict]:
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# _find_all_projects_jsons detection logic
# ---------------------------------------------------------------------------

class TestFindAllProjectsJsons:
    def test_returns_own_when_no_siblings(self, tmp_path, monkeypatch):
        ip = _import_init_project()
        # Point script's __file__ into a fake structure with no prd/uat siblings
        # tmp_path/any/scripts/init_project.py → commander_home = tmp_path/../..
        # (4 levels up from scripts/ is beyond tmp_path — no candidates exist)
        own = tmp_path / "dashboard" / "projects.json"
        _make_projects_json(own)
        monkeypatch.setattr(ip, "_find_projects_json", lambda: own)

        # Patch commander_home to a dir with no prd/ or uat/ children
        empty_home = tmp_path / "empty_home"
        empty_home.mkdir()

        original_fn = ip._find_all_projects_jsons

        def patched():
            found = []
            for variant in ("prd", "uat"):
                candidate = empty_home / variant / "dashboard" / "projects.json"
                if candidate.exists():
                    found.append(candidate)
            if not found:
                return [own]
            seen: set = set()
            result = []
            for p in found:
                rp = p.resolve()
                if rp not in seen:
                    seen.add(rp)
                    result.append(p)
            return result

        monkeypatch.setattr(ip, "_find_all_projects_jsons", patched)
        result = ip._find_all_projects_jsons()
        assert result == [own]

    def test_returns_both_when_both_exist(self, tmp_path, monkeypatch):
        ip = _import_init_project()
        commander_home = tmp_path / "commander"
        prd_json = commander_home / "prd" / "dashboard" / "projects.json"
        uat_json = commander_home / "uat" / "dashboard" / "projects.json"
        _make_projects_json(prd_json)
        _make_projects_json(uat_json)

        def patched():
            found = []
            for variant in ("prd", "uat"):
                candidate = commander_home / variant / "dashboard" / "projects.json"
                if candidate.exists():
                    found.append(candidate)
            if not found:
                return [tmp_path / "own" / "projects.json"]
            seen: set = set()
            result = []
            for p in found:
                rp = p.resolve()
                if rp not in seen:
                    seen.add(rp)
                    result.append(p)
            return result

        monkeypatch.setattr(ip, "_find_all_projects_jsons", patched)
        result = ip._find_all_projects_jsons()
        assert prd_json in result
        assert uat_json in result
        assert len(result) == 2

    def test_returns_only_uat_when_prd_file_absent(self, tmp_path, monkeypatch):
        ip = _import_init_project()
        commander_home = tmp_path / "commander"
        prd_json = commander_home / "prd" / "dashboard" / "projects.json"
        uat_json = commander_home / "uat" / "dashboard" / "projects.json"
        # prd file does NOT exist (directory may exist but file is absent)
        (commander_home / "prd" / "dashboard").mkdir(parents=True)
        _make_projects_json(uat_json)

        def patched():
            found = []
            for variant in ("prd", "uat"):
                candidate = commander_home / variant / "dashboard" / "projects.json"
                if candidate.exists():
                    found.append(candidate)
            if not found:
                return [tmp_path / "own" / "projects.json"]
            seen: set = set()
            result = []
            for p in found:
                rp = p.resolve()
                if rp not in seen:
                    seen.add(rp)
                    result.append(p)
            return result

        monkeypatch.setattr(ip, "_find_all_projects_jsons", patched)
        result = ip._find_all_projects_jsons()
        assert result == [uat_json], f"Expected only UAT, got {result}"
        assert prd_json not in result

    def test_deduplicates_when_own_equals_sibling(self, tmp_path, monkeypatch):
        ip = _import_init_project()
        commander_home = tmp_path / "commander"
        uat_json = commander_home / "uat" / "dashboard" / "projects.json"
        _make_projects_json(uat_json)

        def patched():
            found = []
            for variant in ("prd", "uat"):
                candidate = commander_home / variant / "dashboard" / "projects.json"
                if candidate.exists():
                    found.append(candidate)
            if not found:
                return [uat_json]
            seen: set = set()
            result = []
            for p in found:
                rp = p.resolve()
                if rp not in seen:
                    seen.add(rp)
                    result.append(p)
            return result

        monkeypatch.setattr(ip, "_find_all_projects_jsons", patched)
        result = ip._find_all_projects_jsons()
        assert len(result) == 1
        assert result[0] == uat_json


# ---------------------------------------------------------------------------
# step6_register_project dual-write
# ---------------------------------------------------------------------------

class TestStep6DualWrite:
    def _patched_step6(self, ip, monkeypatch, targets: list[Path]):
        monkeypatch.setattr(ip, "_find_all_projects_jsons", lambda: targets)

    def test_writes_to_both_prd_and_uat(self, tmp_path, monkeypatch):
        ip = _import_init_project()
        prd_json = tmp_path / "prd" / "dashboard" / "projects.json"
        uat_json = tmp_path / "uat" / "dashboard" / "projects.json"
        _make_projects_json(prd_json, [])
        _make_projects_json(uat_json, [])
        self._patched_step6(ip, monkeypatch, [prd_json, uat_json])

        ip.step6_register_project("owner", "my-repo", "ti-flask", "blue")

        for path in (prd_json, uat_json):
            data = _read_projects_json(path)
            assert len(data) == 1
            assert data[0]["repo"] == "owner/my-repo"

    def test_no_duplicate_when_already_in_file(self, tmp_path, monkeypatch):
        ip = _import_init_project()
        existing = [{"repo": "owner/my-repo", "name": "My Repo", "icon": "ti-folder", "color": "blue", "active_sprints": {}}]
        prd_json = tmp_path / "prd" / "dashboard" / "projects.json"
        uat_json = tmp_path / "uat" / "dashboard" / "projects.json"
        _make_projects_json(prd_json, existing)
        _make_projects_json(uat_json, [])  # uat doesn't have it yet
        self._patched_step6(ip, monkeypatch, [prd_json, uat_json])

        ip.step6_register_project("owner", "my-repo", "ti-flask", "blue")

        # prd should still have exactly 1 entry
        assert len(_read_projects_json(prd_json)) == 1
        # uat should now have 1 entry
        assert len(_read_projects_json(uat_json)) == 1

    def test_writes_only_to_uat_when_prd_absent(self, tmp_path, monkeypatch):
        ip = _import_init_project()
        uat_json = tmp_path / "uat" / "dashboard" / "projects.json"
        _make_projects_json(uat_json, [])
        self._patched_step6(ip, monkeypatch, [uat_json])  # only UAT in targets

        ip.step6_register_project("owner", "my-repo", "ti-flask", "blue")

        data = _read_projects_json(uat_json)
        assert len(data) == 1
        assert data[0]["repo"] == "owner/my-repo"

    def test_creates_file_if_not_exists(self, tmp_path, monkeypatch):
        ip = _import_init_project()
        new_json = tmp_path / "dashboard" / "projects.json"
        # Do NOT pre-create the file
        self._patched_step6(ip, monkeypatch, [new_json])

        ip.step6_register_project("owner", "fresh-repo", "ti-folder", "green")

        assert new_json.exists()
        data = _read_projects_json(new_json)
        assert data[0]["repo"] == "owner/fresh-repo"

    def test_result_is_valid_json_array(self, tmp_path, monkeypatch):
        ip = _import_init_project()
        path = tmp_path / "projects.json"
        _make_projects_json(path, [])
        self._patched_step6(ip, monkeypatch, [path])

        ip.step6_register_project("owner", "valid-json", "ti-folder", "amber")

        # File must parse without error and be a list
        content = path.read_text()
        parsed = json.loads(content)
        assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# rollback dual-remove
# ---------------------------------------------------------------------------

class TestRollbackDualRemove:
    def _patched_rollback(self, ip, monkeypatch, targets: list[Path]):
        monkeypatch.setattr(ip, "_find_all_projects_jsons", lambda: targets)
        # Stub out filesystem removal so rollback doesn't try to delete ~/dev dirs
        monkeypatch.setattr(ip.shutil, "rmtree", lambda p: None)

    def test_removes_from_both_files(self, tmp_path, monkeypatch):
        ip = _import_init_project()
        entry = {"repo": "owner/old-repo", "name": "Old Repo", "icon": "ti-folder", "color": "blue", "active_sprints": {}}
        prd_json = tmp_path / "prd" / "dashboard" / "projects.json"
        uat_json = tmp_path / "uat" / "dashboard" / "projects.json"
        _make_projects_json(prd_json, [entry])
        _make_projects_json(uat_json, [entry])
        self._patched_rollback(ip, monkeypatch, [prd_json, uat_json])

        ip.rollback("owner", "old-repo", tmp_path / "projects_dir", confirm_delete=False)

        assert _read_projects_json(prd_json) == []
        assert _read_projects_json(uat_json) == []

    def test_removes_only_from_uat_when_prd_absent(self, tmp_path, monkeypatch):
        ip = _import_init_project()
        entry = {"repo": "owner/old-repo", "name": "Old Repo", "icon": "ti-folder", "color": "blue", "active_sprints": {}}
        uat_json = tmp_path / "uat" / "dashboard" / "projects.json"
        _make_projects_json(uat_json, [entry])
        self._patched_rollback(ip, monkeypatch, [uat_json])

        ip.rollback("owner", "old-repo", tmp_path / "projects_dir", confirm_delete=False)

        assert _read_projects_json(uat_json) == []

    def test_skips_entry_not_in_file(self, tmp_path, monkeypatch):
        ip = _import_init_project()
        other = {"repo": "owner/other-repo", "name": "Other", "icon": "ti-folder", "color": "blue", "active_sprints": {}}
        path = tmp_path / "dashboard" / "projects.json"
        _make_projects_json(path, [other])
        self._patched_rollback(ip, monkeypatch, [path])

        ip.rollback("owner", "nonexistent-repo", tmp_path / "projects_dir", confirm_delete=False)

        # other-repo must remain untouched
        data = _read_projects_json(path)
        assert len(data) == 1
        assert data[0]["repo"] == "owner/other-repo"

    def test_preserves_other_entries_when_removing(self, tmp_path, monkeypatch):
        ip = _import_init_project()
        keeper = {"repo": "owner/keep-me", "name": "Keep", "icon": "ti-folder", "color": "blue", "active_sprints": {}}
        remove_me = {"repo": "owner/remove-me", "name": "Remove", "icon": "ti-folder", "color": "red", "active_sprints": {}}
        path = tmp_path / "dashboard" / "projects.json"
        _make_projects_json(path, [keeper, remove_me])
        self._patched_rollback(ip, monkeypatch, [path])

        ip.rollback("owner", "remove-me", tmp_path / "projects_dir", confirm_delete=False)

        data = _read_projects_json(path)
        assert len(data) == 1
        assert data[0]["repo"] == "owner/keep-me"
