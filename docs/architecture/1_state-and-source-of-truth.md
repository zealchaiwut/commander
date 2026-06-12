# 1. State & source-of-truth model

*The foundation — every other section depends on this.*

[← Contents](0_content.md) · [Next: App / Dashboard architecture →](2_app-dashboard-architecture.md)

## 1.1 The stores today

GitHub labels, JSON files, SQLite, runtime PID state — inventory of every place state lives.

_TODO_

## 1.2 Who is authoritative for what

For each piece of state (ticket status, sprint status, project registry, run state), which store wins on conflict.

_TODO_

## 1.3 Reconciliation

Startup restore, orphan sweeps, and the drift bugs we have hit when stores disagree.

_TODO_

## 1.4 Target model — what Neon changes about authority

How the Neon migration (section 8.2) shifts authority and removes reconciliation cases.

_TODO_
