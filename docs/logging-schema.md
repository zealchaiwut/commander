# Commander Logging Schema

Structured logging schema for Commander's sprint orchestration layer.
Every line written to a run's log file is a JSON-Lines record; no bare text.

## Structured Event Record (emit)

Written by `log.emit()` in `services/logging.py`.
These records capture sprint lifecycle transitions.

| Field        | Type     | Description                                              |
|--------------|----------|----------------------------------------------------------|
| `ts`         | ISO-8601 | UTC timestamp of the event                               |
| `event_type` | string   | One of the `EventType` enum values (see below)           |
| `run_id`     | string   | Unique run identifier (constant across one run)          |
| `sprint`     | string   | Sprint label, e.g. `sprint-59`                           |
| `issue`      | integer  | GitHub issue number                                      |

Additional context fields (`agent_role`, `project`, etc.) may be present.

### EventType values

All lifecycle events use the `EventType` enum defined in `services/logging.py`.
No string literals are used at call sites.

| Value          | Meaning                                               |
|----------------|-------------------------------------------------------|
| `dispatched`   | Agent subprocess spawned for a ticket                 |
| `started`      | Agent has begun substantive work (first log line)     |
| `finished`     | Agent subprocess exited successfully                  |
| `failed`       | Agent subprocess exited with a non-zero code          |
| `killed`       | Agent subprocess was killed (hang / timeout)          |
| `resumed`      | Agent was re-dispatched with continuation context     |
| `transitioned` | GitHub label changed (e.g. in-progress → sit → uat)  |

## Subprocess Envelope (agent output lines)

Written by `envelope_subprocess_line()` in `services/logging.py`.
Each line of agent (coder/tester) subprocess stdout/stderr is wrapped in this
envelope so the log file remains pure JSON-Lines.

| Field    | Type     | Description                                          |
|----------|----------|------------------------------------------------------|
| `run_id` | string   | Run identifier — matches all other lines in this run |
| `sprint` | string   | Sprint label                                         |
| `issue`  | integer  | GitHub issue number                                  |
| `agent`  | string   | Agent role: `coder`, `tester`, `reviewer`, etc.      |
| `ts`     | ISO-8601 | UTC timestamp of the captured line                   |
| `raw`    | string   | Original agent output text, preserved verbatim       |

### Example

```json
{"run_id": "sprint59-20260611T120000-ab3c", "sprint": "sprint-59", "issue": 784, "agent": "coder", "ts": "2026-06-11T12:00:01.234Z", "raw": "  Creating feature branch feature/784-..."}
```

## run_id format

```
<source>[hint]-<YYYYMMDDTHHmm>-<4rand>
```

Examples:
- `sprint59-20260611T1200-ab3c` — sprint dispatch, sprint number 59
- `manual-20260611T1200-x7z2`  — manual invocation

`run_id` is constant across **all** log lines within a single run.  Two separate
runs always have distinct `run_id` values (different random suffix and/or
timestamp).

## Run Browser Rendering

The run browser (`apps/dashboard/static/log-colorize.js`) extracts the `.raw`
field from envelope lines via `extractRaw(text)` before colorizing.  Plain text
lines (old log files) are rendered identically to pre-migration behavior.
