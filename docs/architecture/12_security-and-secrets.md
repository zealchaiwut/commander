# 12. Security & secrets

[← Contents](0_content.md) · [← Prev: Remote work](11_remote-work.md) · [Next: Observability & cost →](13_observability-and-cost.md)

## 12.1 Secrets handling

`.env`, gist redaction.

- `.env` and `.env.*` are gitignored (gap from the architecture review was fixed — README claim now accurate).
- Per-project `.env` files live inside clone dirs; never commit secrets.
- Gist backup must redact tokens before upload.
- Config vs state vs secrets boundary: [section 8.3](8_database-and-local-env.md).

## 12.2 Auth posture

Single-user, local-only — what changes if remote/multi-user.

_TODO_

## 12.3 Token scope

GitHub token, Neon credentials, blast radius.

_TODO_

## 12.4 Public-repo exposure

What's safe to commit.

_TODO_
