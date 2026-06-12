"""Tester verification for issue #840 — LLM brief summary, cache + fallback
(runs against a LIVE server).

The shared UAT instance (port 8001) is pinned to a stale branch and 404s on the
brief-summary routes, so this suite boots the *feature branch* code as a real
uvicorn subprocess against a seeded temp SQLite DB and drives it over HTTP with
httpx — exactly as a deployed UAT box would be exercised. This proves the new
routes are actually mounted/reachable at runtime (the include_router wiring +
brief_summary import work, no startup errors), which an in-process TestClient
cannot prove. It complements the coder's in-process suite (test_840_*), which
monkeypatches the ``_call_model`` subprocess seam to drive the model path.

Strategy for a real subprocess (where we cannot monkeypatch ``_call_model``):

* The server is booted with ``COMMANDER_DISABLE_BRIEF_LLM=1`` so the model is
  never invoked and every freshly-generated summary is the deterministic
  templated fallback — reproducible, no dependency on the ``claude`` CLI.
* The *cache-read* path (AC3) is proven by pre-seeding a row with
  ``source="model"`` directly in the shared temp DB: with the LLM disabled, the
  only way the endpoint can return that pinned model text is by reading the
  cache rather than regenerating — a positive proof the model is not re-called.
* Regenerate (AC4/AC9) clears that pinned row and, with the LLM disabled,
  returns an uncached fallback — proving the clear happened and that fallbacks
  are not cached.

Model-only criteria that a live box cannot prove over HTTP without spawning the
real CLI (AC1 fixed prompt, AC2 no web/file context) are verified by static
inspection of the router source — mirroring the #839 live-suite approach.

Risk: MEDIUM (enhancement; additive cache table; non-destructive).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"

SLUG = "briefsum840"            # not a real tracked project → isolated rows
REPO_KEY = SLUG                 # rows are seeded keyed by the slug itself
DATE = "2026-06-10"
WIN_START = f"{DATE}T08:00:00"

# --- temp DB wired in before importing db (db reads DB_PATH at import) ---------
_TMPDIR = tempfile.mkdtemp(prefix="brief_uat_840_")
DB_FILE = str(Path(_TMPDIR) / "brief_840.db")
os.environ["DB_PATH"] = DB_FILE
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

import db  # noqa: E402  (import after DB_PATH is set)


# ── seeding (setup only — never the assertion path) ───────────────────────────
# A shipped sprint-50 (2 done + 1 skipped), a running sprint-51 (1 of 4 → 25%),
# and a blocked (rework) + needs-you (uat) ticket — same shape as the #839/#840
# in-process fixtures so the structured counts are deterministic.

def _seed():
    db.init_db()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO sprints (label, project, state, created_at, started_at, "
            "ended_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("sprint-50", REPO_KEY, "completed", f"{DATE}T07:00:00",
             f"{DATE}T08:00:00", f"{DATE}T08:45:00"),
        )
        conn.execute(
            "INSERT INTO sprints (label, project, state, created_at, started_at, "
            "ended_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("sprint-51", REPO_KEY, "running", f"{DATE}T08:50:00",
             f"{DATE}T09:00:00", None),
        )
        conn.commit()

    db.upsert_issues(REPO_KEY, [
        {"number": 100, "title": "Add login", "state": "closed",
         "labels": [{"name": "done"}], "updatedAt": f"{DATE}T08:00:00Z"},
        {"number": 101, "title": "Fix bug", "state": "closed",
         "labels": [{"name": "done"}], "updatedAt": f"{DATE}T08:00:00Z"},
        {"number": 102, "title": "Defer cache", "state": "closed",
         "labels": [{"name": "skipped"}], "updatedAt": f"{DATE}T08:00:00Z"},
        {"number": 200, "title": "Build dashboard", "state": "closed",
         "labels": [{"name": "done"}], "updatedAt": f"{DATE}T08:00:00Z"},
        {"number": 201, "title": "Wire API", "state": "open",
         "labels": [], "updatedAt": f"{DATE}T08:00:00Z"},
        {"number": 202, "title": "Write docs", "state": "open",
         "labels": [], "updatedAt": f"{DATE}T08:00:00Z"},
        {"number": 203, "title": "Polish UI", "state": "open",
         "labels": [], "updatedAt": f"{DATE}T08:00:00Z"},
        {"number": 300, "title": "Broken thing", "state": "open",
         "labels": [{"name": "rework"}], "updatedAt": f"{DATE}T08:00:00Z"},
        {"number": 301, "title": "Awaiting signoff", "state": "open",
         "labels": [{"name": "uat"}], "updatedAt": f"{DATE}T08:00:00Z"},
    ])
    db.set_sprint_ticket_order("sprint-50", [100, 101, 102])
    db.set_sprint_ticket_order("sprint-51", [200, 201, 202, 203])


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def base_url():
    _seed()
    port = _free_port()
    env = dict(os.environ)
    env.update({
        "DB_PATH": DB_FILE,
        "ENVIRONMENT": "uat",
        "COMMANDER_DISABLE_NEON": "1",
        # Disable the model so every fresh summary is the deterministic fallback;
        # the cache-read test pre-seeds a model row to prove the no-recall path.
        "COMMANDER_DISABLE_BRIEF_LLM": "1",
        "PYTHONPATH": str(DASHBOARD_DIR),
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(DASHBOARD_DIR), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 40
        ready = False
        while time.time() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
                raise RuntimeError(f"server exited early (code {proc.returncode}):\n{out[-3000:]}")
            try:
                r = httpx.get(f"{url}/api/projects/{SLUG}/brief/summary?date={DATE}", timeout=2.0)
                if r.status_code == 200:
                    ready = True
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        if not ready:
            raise RuntimeError("server did not become ready within 40s")
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture()
def client(base_url):
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        yield c


# ══ AC1 — fixed Haiku "chief of staff" prompt (static; live can't prove) ══════

def test_brief_summary__model_called_with_fixed_chief_of_staff_prompt(client):
    # AC1: model is invoked with a fixed prompt requesting a 2–4 sentence,
    # chief-of-staff summary covering shipped / in-progress / most-worth-attention.
    # The live box runs with the model disabled, so this is proven by inspecting
    # the prompt constant + the fixed model the subprocess seam targets.
    import routers.brief_summary as bs  # noqa: PLC0415
    assert bs.MODEL == "claude-haiku-4-5"
    low = bs._PROJECT_PROMPT.lower()
    assert "chief of staff" in low
    assert "sentence" in low
    for facet in ("shipped", "progress", "attention"):
        assert facet in low, f"prompt missing facet: {facet}"
    # The payload is embedded in the built prompt and nothing else is appended.
    payload = {"project": SLUG, "date": DATE, "kpis": {"tickets_done": 2}}
    built = bs.build_project_prompt(payload)
    import json as _json  # noqa: PLC0415
    assert _json.dumps(payload, indent=2) in built


# ══ AC2 — model sees ONLY structured data (static, mirrors #839) ══════════════

def test_brief_summary__no_llm_or_network_imports_and_clean_payload(client):
    # AC2: the summary module talks to the model through a single subprocess seam
    # and feeds it only the structured payload — no HTTP client, no file reads,
    # no extra context. Prove statically that no network/LLM SDK is imported and
    # that the built payload carries no file paths or URLs.
    import json as _json  # noqa: PLC0415
    import routers.brief_summary as bs  # noqa: PLC0415

    src = (ROUTERS_DIR / "brief_summary.py").read_text(encoding="utf-8")
    forbidden = ("import anthropic", "from anthropic", "import openai",
                 "from openai", "import requests", "import urllib.request",
                 "import httpx")
    for token in forbidden:
        assert token not in src, f"brief_summary.py unexpectedly references {token!r}"

    # Build the payload off the live structured brief and confirm it is pure
    # structured data (counts/labels/titles) with no filesystem or web hints.
    brief = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE}).json()
    payload = bs.build_project_payload(brief)
    blob = _json.dumps(payload)
    assert "/Users/" not in blob
    assert "http://" not in blob and "https://" not in blob
    # Faithful structured counts are present.
    assert payload["kpis"]["tickets_done"] == 2


# ══ AC3 — generated summary cached and returned without re-calling the model ══

def test_brief_summary__cached_model_summary_returned_without_recall(client):
    # AC3: a model-generated summary stored against (project, date) is returned
    # on subsequent loads without re-invoking the model. We pre-seed a row with
    # source="model"; with the LLM disabled on the server, the only way the
    # endpoint can return that pinned text is by reading the cache (a regenerate
    # would yield the templated fallback instead).
    pinned = "PINNED model summary — must come from the cache, not regeneration."
    db.set_brief_summary("project", SLUG, DATE, pinned, "model")
    try:
        r = client.get(f"/api/projects/{SLUG}/brief/summary", params={"date": DATE})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["summary"] == pinned
        assert body["source"] == "model"
        # A second load is identical and still served from cache.
        r2 = client.get(f"/api/projects/{SLUG}/brief/summary", params={"date": DATE})
        assert r2.json()["summary"] == pinned
    finally:
        db.delete_brief_summary("project", SLUG, DATE)


# ══ AC4 — Regenerate clears the stored summary and re-invokes ═════════════════

def test_brief_summary__regenerate_clears_stored_summary(client):
    # AC4: Regenerate clears the stored summary for (project, date) and
    # re-invokes the model. With the LLM disabled, regenerating a pinned model
    # row must (a) not return the pinned text and (b) leave no cached model row —
    # proving the clear + re-invoke happened and that fallbacks are not cached.
    pinned = "PINNED stale summary that regenerate must discard."
    db.set_brief_summary("project", SLUG, DATE, pinned, "model")
    try:
        r = client.post(f"/api/projects/{SLUG}/brief/summary/regenerate",
                        params={"date": DATE})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["summary"] != pinned, "regenerate returned the stale cached text"
        assert body["source"] == "fallback"  # model disabled → fresh fallback
        # The stale model row was cleared; fallbacks are not re-cached.
        assert db.get_brief_summary("project", SLUG, DATE) is None
        # A subsequent GET also recomputes the fallback (still uncached).
        again = client.get(f"/api/projects/{SLUG}/brief/summary", params={"date": DATE})
        assert again.json()["source"] == "fallback"
    finally:
        db.delete_brief_summary("project", SLUG, DATE)


# ══ AC5 — templated fallback when the model is disabled/fails ═════════════════

def test_brief_summary__templated_fallback_when_model_disabled(client):
    # AC5: with generation disabled via config, the endpoint returns a
    # deterministic templated summary built entirely from the structured fields.
    r = client.get(f"/api/projects/{SLUG}/brief/summary", params={"date": DATE})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "fallback"
    s = body["summary"]
    assert "Sprint 50" in s          # pretty label of the shipped sprint
    assert "2 features" in s         # tickets_done
    assert "25% complete" in s       # running sprint progress
    assert "need attention" in s


# ══ AC6 — brief summary renders (never 5xx), incl. empty project ══════════════

def test_brief_summary__renders_200_for_populated_and_empty(client):
    # AC6: the summary path never blocks rendering — 200 for a populated project
    # and 200 (with a graceful no-activity fallback) for an unknown one.
    r = client.get(f"/api/projects/{SLUG}/brief/summary", params={"date": DATE})
    assert r.status_code == 200, r.text
    assert r.json()["summary"]

    empty = client.get("/api/projects/no-such-project-xyz/brief/summary",
                       params={"date": DATE})
    assert empty.status_code == 200, empty.text
    assert empty.json()["source"] == "fallback"
    assert empty.json()["summary"]   # deterministic "No activity…" string


# ══ AC7 — one-line home recap, same storage + fallback rules ══════════════════

def test_brief_summary__home_recap_is_one_line_fallback(client):
    # AC7: the home recap endpoint returns a single-sentence fallback (model
    # disabled), renders 200, and exposes the same {date, summary, source} shape.
    r = client.get("/api/brief/summary", params={"date": DATE})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(("date", "summary", "source")) <= set(body)
    assert body["source"] == "fallback"
    assert body["summary"]
    # One line — the recap is a single sentence, not a multi-line block.
    assert "\n" not in body["summary"].strip()


def test_brief_summary__home_recap_regenerate_renders(client):
    # AC7 (regenerate parity): the home recap supports Regenerate and still
    # renders a fallback one-liner with the model disabled.
    r = client.post("/api/brief/summary/regenerate", params={"date": DATE})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "fallback"
    assert body["summary"]
    assert "\n" not in body["summary"].strip()


# ══ AC8 — summary numbers match the underlying structured counts ══════════════

def test_brief_summary__numbers_match_structured_counts(client):
    # AC8: the summary reflects the real counts — no hallucinated/fabricated
    # numbers. Cross-check the fallback summary against the structured brief that
    # feeds it: tickets_done and the in-progress percent must appear verbatim.
    brief = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE}).json()
    summary = client.get(f"/api/projects/{SLUG}/brief/summary",
                        params={"date": DATE}).json()["summary"]

    tickets_done = brief["kpis"]["tickets_done"]
    percent = brief["in_progress"]["progress"]["percent"]
    assert tickets_done == 2 and percent == 25, "precondition: seeded counts"
    assert f"{tickets_done} features" in summary
    assert f"{percent}% complete" in summary


# ══ AC9 — Regenerate is scoped to the summary only ════════════════════════════

def test_brief_summary__regenerate_scoped_to_summary_only(client):
    # AC9: Regenerate touches only the summary — it does not re-fetch or mutate
    # the structured brief. Capture the structured brief, regenerate the summary,
    # and confirm the structured brief is byte-for-byte unchanged. Also confirm
    # the regenerate response carries only summary fields (no structured brief).
    before = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE}).json()

    regen = client.post(f"/api/projects/{SLUG}/brief/summary/regenerate",
                       params={"date": DATE})
    assert regen.status_code == 200, regen.text
    assert set(regen.json().keys()) == {"project", "date", "summary", "source"}

    after = client.get(f"/api/projects/{SLUG}/brief", params={"date": DATE}).json()
    assert after == before, "regenerate must not recompute/alter the structured brief"
