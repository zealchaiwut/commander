"""Behavioral AC tests for issue #2345 (Part C): whole-graph module guard + env guard.

Part B's ``_guard_services_modules`` restored ``services.*``/``server``/
``projects`` (later ``routers*``) — but not ``db``, ``github_client``,
``env_file``, ... A *partial* restore is worse than none: after a test that
purges-and-reimports ``db``+``server``+``routers*`` (test_1163, test_631,
test_634, test_2232, ...), ``server`` went back to the original object while
``sys.modules["routers.analytics"]``/``sys.modules["db"]`` kept the fresh ones,
so tests patching one object drove requests through the other. That split
produced the ~100 "new" analytics/cost/metrics failures in the first
baseline-delta refusal on this ticket.

Separately, fixtures in test_2041/2042/2066 ``os.environ.pop("DB_PATH")`` at
teardown; ``db.py`` ``sys.exit(1)``s on import when ``DB_PATH`` is blank, so
every later fresh ``import db`` errored at fixture setup (632 ERRORs in one
full run).

These tests use the same step1-pollute / step2-assert pattern as the tester's
Part B file: the pollution happens in one test, the assertion in the next, so
the autouse guards in root ``conftest.py`` are what is under test.

AC coverage: AC3 (root cause + fix for the order-dependent failure class),
AC4 (the mechanism that made two runs of one commit differ is closed).
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(REPO_ROOT), str(REPO_ROOT / "apps" / "dashboard")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Loaded at collection time, as the real suite does (hundreds of test modules
# import server at module scope), so these objects are in the guard's pre-test
# snapshot. A module first imported *inside* a test is deliberately dropped by
# the guard afterwards — it was bound against that test's fresh graph.
for _name in ("db", "routers.analytics", "server"):
    importlib.import_module(_name)

_PROBE_VAR = "COMMANDER_2345_ENV_PROBE"
_ORIGINAL: dict = {}


def _first_party_ids() -> dict:
    import db
    import routers.analytics
    import server

    return {
        "db": id(db),
        "server": id(server),
        "routers.analytics": id(routers.analytics),
    }


# ── step 1: pollute exactly the way the real fixtures do ─────────────────────

def test_step1_pollute_db_server_routers_and_environ(tmp_path):
    """Mimics test_1163's fixture (purge db+server+routers*) and test_2042's
    teardown (pop DB_PATH), plus a probe variable set without restore."""
    _ORIGINAL.update(_first_party_ids())
    _ORIGINAL["DB_PATH"] = os.environ.get("DB_PATH")
    assert _ORIGINAL["DB_PATH"], "conftest must have set DB_PATH before any test"

    os.environ[_PROBE_VAR] = "leaked"
    os.environ["DB_PATH"] = str(tmp_path / "polluter.db")
    for mod in list(sys.modules):
        if mod in ("db", "server") or mod.startswith("routers"):
            del sys.modules[mod]

    fresh = _first_party_ids()
    assert all(fresh[k] != _ORIGINAL[k] for k in fresh), (
        "sanity check: purge+reimport did not create distinct module objects, "
        "so this repro could not detect pollution either way"
    )
    # Leave DB_PATH popped, exactly like test_2041/2042/2066's teardown.
    os.environ.pop("DB_PATH", None)


# ── step 2: identity restored for the WHOLE graph, not just server ───────────

def test_step2_db_server_routers_identity_restored():
    now = _first_party_ids()
    assert now == {k: _ORIGINAL[k] for k in now}, (
        f"first-party module identity not restored after the polluting test: "
        f"{ {k: (now[k] == _ORIGINAL[k]) for k in now} } — the "
        "_guard_services_modules fixture in conftest.py must cover db and "
        "routers.* as well as server (issue #2345)"
    )


def test_step2b_server_and_router_reference_the_same_db_as_sys_modules():
    """The split that produced the 105-test refusal: server/routers restored to
    the original objects while sys.modules['db'] kept the fresh one."""
    import db
    import server

    analytics = importlib.import_module("routers.analytics")  # resolves via sys.modules

    assert server.db is db, "server.db is not the db in sys.modules (split identity)"
    assert analytics._db is db, (
        "routers.analytics._db is not the db in sys.modules — a test patching "
        "db.DB_PATH would never reach the handler (issue #2345)"
    )


# ── step 3: os.environ restored ──────────────────────────────────────────────

def test_step3_environ_restored_after_polluting_test():
    assert _PROBE_VAR not in os.environ, (
        f"{_PROBE_VAR} leaked out of the polluting test — _guard_os_environ in "
        "conftest.py is not restoring os.environ (issue #2345)"
    )
    assert os.environ.get("DB_PATH") == _ORIGINAL["DB_PATH"], (
        "DB_PATH was not restored after a test popped it; the next fresh "
        "`import db` would sys.exit(1) at fixture setup (issue #2345)"
    )


def test_step3b_fresh_db_import_still_works_after_polluter(tmp_path):
    """The concrete symptom: a later purge-and-reimport of db must not die."""
    import db as before

    del sys.modules["db"]
    import db as fresh  # would raise SystemExit if DB_PATH had stayed popped

    assert fresh is not before
    assert isinstance(fresh.DB_PATH, Path)


# ── step 4: a fixture that dies before `yield` cannot leak db.DB_PATH ────────
#
# test_1160's autouse fixture does ``db.DB_PATH = str(db_file); db.init_db()``;
# init_db now calls ``DB_PATH.exists()``, so the fixture raises *before* yield
# and its post-yield restore never runs. The str stayed on the shared db module
# for the rest of the session and ~200 later tests errored at setup with
# "'str' object has no attribute 'exists'". Simulate exactly that.

def test_step4_pollute_db_path_like_a_fixture_that_errors_before_yield():
    import db

    _ORIGINAL["DB_PATH_ATTR"] = db.DB_PATH
    db.DB_PATH = "/nonexistent/leaked-by-a-fixture-that-never-reached-yield.db"
    assert isinstance(db.DB_PATH, str)


def test_step4b_db_path_attribute_restored():
    import db

    assert db.DB_PATH == _ORIGINAL["DB_PATH_ATTR"] and isinstance(db.DB_PATH, Path), (
        f"db.DB_PATH={db.DB_PATH!r} leaked out of the previous test — the guard "
        "in conftest.py must restore db.DB_PATH per test (issue #2345)"
    )
    db.init_db()  # the call that errored ~200 times in a full run


# ── step 5: github_client's TTL cache cannot carry over between tests ────────
#
# ``sprints:``/``labels:`` entries live 300s, so a test that mocks
# ``subprocess.run`` and expects a gh call got a cache hit instead whenever an
# earlier test had warmed the same key recently enough — a wall-clock-dependent
# outcome (test_1783, test_github_client).

def test_step5_warm_github_client_cache():
    import github_client as gc

    gc._cache["sprints:owner/repo-2345-probe"] = (float("inf"), ["sprint-1"])
    assert gc._cached("sprints:owner/repo-2345-probe", lambda: ["fresh"]) == ["sprint-1"]


def test_step5b_github_client_cache_is_empty_for_the_next_test():
    import github_client as gc

    assert "sprints:owner/repo-2345-probe" not in gc._cache, (
        "github_client._cache carried over from the previous test — the guard "
        "in conftest.py must empty it per test (issue #2345)"
    )
    calls = []
    assert gc._cached("sprints:owner/repo-2345-probe", lambda: calls.append(1) or ["fresh"]) == ["fresh"]
    assert calls == [1], "expected a cache miss (the underlying fetch must run)"
