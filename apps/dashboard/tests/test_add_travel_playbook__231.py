"""
Tests for issue #231 — Add TRAVEL_PLAYBOOK.md with pre-travel checklist and remote recovery.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAYBOOK = REPO_ROOT / "docs" / "TRAVEL_PLAYBOOK.md"
README = REPO_ROOT / "README.md"


def _text():
    return PLAYBOOK.read_text()


def _readme():
    return README.read_text()


# --- File presence ---

def test_playbook_file_exists():
    assert PLAYBOOK.exists(), "docs/TRAVEL_PLAYBOOK.md must exist"


def test_readme_has_going_remote_section():
    text = _readme()
    assert "Going remote?" in text or "Going Remote?" in text, \
        "README must contain a 'Going remote?' section"


def test_readme_links_to_playbook():
    assert "TRAVEL_PLAYBOOK.md" in _readme(), \
        "README must link to docs/TRAVEL_PLAYBOOK.md"


def test_playbook_has_last_reviewed_line():
    text = _text()
    assert re.search(r"Last reviewed: \d{4}-\d{2}-\d{2}", text), \
        "Playbook must have a 'Last reviewed: YYYY-MM-DD' line"


def test_playbook_has_code_blocks():
    text = _text()
    assert "```" in text, "Playbook must contain fenced code blocks"


def test_playbook_has_markdown_headers():
    text = _text()
    assert "## " in text, "Playbook must contain section headers"


# --- Pre-travel checklist ---

def test_checklist_sleep_disabled():
    assert "disablesleep" in _text(), \
        "Checklist must include 'sudo pmset -a disablesleep 1'"


def test_checklist_auto_login():
    text = _text()
    assert "auto-login" in text.lower() or "autologin" in text.lower() or "Login Items" in text, \
        "Checklist must mention auto-login on boot"


def test_checklist_launchd():
    assert "install_launchd.sh" in _text(), \
        "Checklist must reference bash scripts/install_launchd.sh"


def test_checklist_tailscale():
    assert "tailscale" in _text().lower(), \
        "Checklist must mention Tailscale"


def test_checklist_magicdns():
    assert "MagicDNS" in _text() or "magicdns" in _text().lower(), \
        "Checklist must mention MagicDNS"


def test_checklist_cellular_test():
    assert "cellular" in _text().lower(), \
        "Checklist must mention testing on cellular (not WiFi)"


def test_checklist_ssh_enabled():
    assert "ssh" in _text().lower() and "authorized_keys" in _text(), \
        "Checklist must cover SSH key setup"


def test_checklist_gh_auth():
    assert "gh auth status" in _text(), \
        "Checklist must include 'gh auth status' check"


def test_checklist_claude_version():
    assert "claude" in _text() and ("--version" in _text() or "auth status" in _text()), \
        "Checklist must cover Claude Code auth check"


def test_checklist_api_health():
    assert "/api/health" in _text(), \
        "Checklist must include /api/health endpoint check"


def test_checklist_backup_status():
    assert "/api/backup/status" in _text(), \
        "Checklist must reference /api/backup/status"


def test_checklist_sprint_state():
    assert "sprint.yaml" in _text(), \
        "Checklist must mention clearing sprint state before departure"


# --- URLs section ---

def test_urls_section_present():
    text = _text()
    assert "URLs" in text or "urls" in text.lower(), \
        "Playbook must have a URLs section"


def test_urls_dashboard():
    assert "8000" in _text(), "URLs section must include dashboard port 8000"


def test_urls_diagnostics():
    assert "/diagnostics" in _text(), "URLs must include /diagnostics path"


def test_urls_backup_status():
    assert "/api/backup/status" in _text(), "URLs must include /api/backup/status"


def test_urls_github_repo():
    assert "github.com/zealchaiwut/commander" in _text(), \
        "URLs must include the GitHub repo URL"


def test_urls_tailscale_admin():
    assert "login.tailscale.com/admin" in _text(), \
        "URLs must include Tailscale admin console URL"


# --- Common failures ---

def test_failure_dashboard_not_responding():
    assert "Dashboard URL does not respond" in _text() or \
           "Dashboard URL doesn't respond" in _text() or \
           "does not respond" in _text() or "doesn't respond" in _text(), \
        "Must document 'Dashboard URL does not respond' failure"


def test_failure_health_shows_down():
    assert "/api/health" in _text() and "down" in _text(), \
        "Must document 'dashboard responds but /api/health shows down' failure"


def test_failure_github_auth_expired():
    assert "GitHub auth expired" in _text() or "GitHub auth" in _text(), \
        "Must document GitHub auth expiry failure"


def test_failure_claude_auth_expired():
    assert "Claude Code auth expired" in _text() or "Claude Code auth" in _text(), \
        "Must document Claude Code auth expiry failure"


def test_failure_sprint_stuck():
    assert "Sprint stuck" in _text() or "sprint stuck" in _text(), \
        "Must document sprint stuck failure"


def test_failure_config_restore():
    assert "projects.json" in _text() and "sprint.yaml" in _text() and \
           "restore" in _text().lower(), \
        "Must document projects.json/sprint.yaml restore procedure"


def test_failure_tester_rejected():
    assert "Tester rejected" in _text() or "rejected" in _text().lower(), \
        "Must document tester repeated rejection escalation path"


def test_failure_mac_unreachable():
    assert "unreachable" in _text().lower() or "offline" in _text().lower(), \
        "Must document Mac mini totally unreachable failure"


def test_failure_ipad_battery():
    assert "battery" in _text().lower(), \
        "Must document iPad battery dies mid-sprint failure"


def test_each_failure_has_symptom():
    text = _text()
    assert text.count("**Symptom:**") >= 8, \
        "Each major failure mode must have a **Symptom:** field"


def test_each_failure_has_likely_cause():
    text = _text()
    assert text.count("**Likely cause:**") >= 7, \
        "Each major failure mode must have a **Likely cause:** field"


def test_each_failure_has_ipad_recovery():
    text = _text()
    assert text.count("**Recovery from iPad:**") >= 7, \
        "Each major failure mode must have a **Recovery from iPad:** section"


def test_each_failure_has_ssh_recovery():
    text = _text()
    assert text.count("**Recovery via SSH:**") >= 7, \
        "Each major failure mode must have a **Recovery via SSH:** section"


# --- SSH commands section ---

def test_ssh_section_present():
    assert "SSH Commands" in _text() or "SSH commands" in _text(), \
        "Playbook must have an SSH Commands section"


def test_ssh_connect_command():
    assert "tail-xxxx.ts.net" in _text(), \
        "SSH section must include Tailscale SSH connect command"


def test_ssh_tmux_command():
    assert "tmux attach" in _text(), \
        "SSH section must include 'tmux attach -t commander'"


def test_ssh_tail_log():
    assert "tail -f" in _text() and "commander-dashboard.out.log" in _text(), \
        "SSH section must include tail command for dashboard log"


def test_ssh_launchctl_list():
    assert "launchctl list" in _text() and "commander" in _text(), \
        "SSH section must include launchctl list | grep commander"


def test_ssh_launchctl_kickstart():
    assert "launchctl kickstart" in _text(), \
        "SSH section must include launchctl kickstart force-restart command"


def test_ssh_backup_restore():
    assert "backup restore" in _text() and "gist-id" in _text(), \
        "SSH section must include backup restore command"


# --- Fallback paths ---

def test_fallback_ipad_fails():
    assert "iPad fails" in _text() or "If iPad fails" in _text(), \
        "Fallback paths must cover 'If iPad fails'"


def test_fallback_both_fail():
    assert "phone" in _text().lower(), \
        "Fallback paths must mention phone as backup device"


def test_fallback_mac_dies():
    assert "Mac mini truly dies" in _text() or "Mac mini" in _text(), \
        "Fallback paths must describe what is recoverable if Mac mini dies"


def test_fallback_recoverable_vs_lost():
    text = _text()
    assert "recoverable" in text.lower() and ("not recoverable" in text.lower() or
                                               "**not** recoverable" in text or
                                               "not recoverable" in text), \
        "Fallback section must distinguish recoverable vs non-recoverable data"


# --- What to verify on return ---

def test_return_section_present():
    text = _text()
    assert "return" in text.lower() or "Return" in text, \
        "Playbook must have a 'What to verify on return' section"


def test_return_checklist_items():
    text = _text()
    lines_with_checkbox = [l for l in text.splitlines() if "- [ ]" in l]
    # Playbook should have multiple checklist items across all sections
    assert len(lines_with_checkbox) >= 15, \
        f"Playbook must have at least 15 checkbox items, found {len(lines_with_checkbox)}"
