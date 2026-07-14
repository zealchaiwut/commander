# Commander Test Suite

## Structure

```
tests/
  conftest.py              — shared fixtures (call_budget_fixture, static_dashboard_url, …)
  test_<N>__<slug>.py      — per-ticket acceptance-criteria tests
  frontend/                — Node.js harness tests (run: node --test tests/frontend/*.test.mjs)
    call-budget-helpers.mjs — shared fetch-spy helpers for HTML-harness tests
    board-aggregate-flag.test.mjs
    sse-parser.test.mjs
    …
  integration/             — end-to-end integration tests (require live DB / network)
  reconciler/              — reconciler-specific unit tests
```

## Running tests

```bash
# Run all pytest tests (from repo root)
pytest tests/ -x

# Run a single ticket suite
pytest tests/test_1788__call_budgets.py -v

# Run Node.js frontend harness tests
node --test tests/frontend/board-aggregate-flag.test.mjs
node --test tests/frontend/call-budget-helpers.mjs  # (if test runner present)
```

## Call-count-budget harness (issue #1788)

The `call_budget_fixture` in `conftest.py` provides a reusable test harness
for enforcing per-page/per-endpoint call-count limits ("budgets").

### Pytest one-liner

```python
def test_board_zero_gh(call_budget_fixture):
    # ... drive endpoint via TestClient ...
    call_budget_fixture.assert_zero_gh()
    call_budget_fixture.record_http("/api/board?project=owner/repo")
    call_budget_fixture.assert_call_budget("/api/board", 1)
```

### Node.js / HTML-harness one-liner

```js
import { installFetchSpy, assertFetchBudget } from '../frontend/call-budget-helpers.mjs';

const spy = installFetchSpy({ '/api/board': myFakeBoardResponse() });
await loadSprintMgmt(true, null);
assertFetchBudget(spy, '/api/board', 1);  // exactly 1 call
```

### Arc targets (documented call budgets)

| Page / Endpoint               | Budget           | Flag required                     | Test location                          |
|-------------------------------|------------------|-----------------------------------|----------------------------------------|
| Board load                    | = 1 call         | `COMMANDER_BOARD_AGGREGATE=1`     | `test_call_budgets.py::TestBoardLoadBudget` |
| Home load                     | ≤ 4 calls        | (always)                          | `test_call_budgets.py::TestHomeLoadBudget` |
| History feed load             | = 1 call         | `COMMANDER_HISTORY_AGGREGATE=1`   | `test_call_budgets.py::TestHistoryFeedBudget` |
| Running tab first paint       | = 1 call         | `COMMANDER_RUNNING_AGGREGATE=1`   | `test_call_budgets.py::TestRunningTabBudget` |
| `GET /api/board`              | 0 gh subprocess  | (always)                          | `test_call_budgets.py::TestZeroGhSubprocessBudget` |
| `GET /api/home` *(aspirational)* | 0 gh subprocess | pending optimization           | `test_call_budgets.py::test_zero_gh_subprocess_in_home_endpoint_source` *(xfail)* |

### xfail-to-pass flip

Budgets that depend on unshipped optimizations are marked `@pytest.mark.xfail`.
When the blocking ticket lands:

1. Remove the `@pytest.mark.xfail(...)` decorator from the test.
2. Apply the relevant code change (see the `reason=` string for which file/pattern to fix).
3. Run `pytest tests/test_call_budgets.py` — the test passes.

## Writing new AC tests

- Name: `tests/test_<issue-N>__<slug>.py`
- Each test must exercise behavior, not source text (see CLAUDE.md §AC tests).
- Forbidden: `assert "symbol" in src` alone; required: `TestClient` or function call.
- Use `call_budget_fixture` when the AC specifies a call-count constraint.
