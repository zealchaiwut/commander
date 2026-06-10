# Product Context

## What Commander Is

Commander is a personal AI agent platform for solo development with Claude Code.
It automates the BA → Coder → Tester → UAT workflow using GitHub Issues as a
sprint board, with a live dashboard at `localhost:8000`. The dashboard is mission
control: plan a sprint, watch agents work it ticket by ticket, sign off on UAT,
and read the results.

## Who It's For

One developer (the operator) running Claude Code agents to manage feature work.
They live in the terminal and value speed, precision, and signal over chrome. The
dashboard is their cockpit at the desk, and a monitoring surface on an iPad over
Tailscale when away. Single-user — no multi-tenant, no auth.

## Emotional Goal

Using Commander should feel like driving a precise, fast developer tool — the
calm confidence of Vercel/Render, not a busy admin panel. The operator should
always know, at a glance: what's running, what needs them, and what just happened.
Trust comes from clarity and consistency.

## Jobs To Be Done

The operator opens the dashboard to:
1. **See state fast** — which sprint is running, what's awaiting UAT, what failed.
2. **Plan** — bulk-create tickets, estimate, assign to a sprint.
3. **Run & watch** — start a sprint, follow agents (coder/tester) live, ticket by ticket.
4. **Sign off** — review UAT tickets, approve or send back.
5. **Understand** — analytics (calibration, throughput, cost), logs, sprint history.
6. **Operate** — deploy/restart environments, edit config and env vars.

## Core User Flows

1. **Sprint planning** — select/bulk-create backlog issues, assign a sprint label.
2. **Sprint execution** — run the sprint manager; watch agents work each issue live.
3. **UAT sign-off** — review merged features in the dashboard, approve or reject.
4. **Sprint summary** — auto-generated report filed as a GitHub issue.

## Design Principles

- **Signal first.** The most important thing on any screen is the most prominent.
  Status (running / needs-you / failed) reads instantly.
- **Sharp & technical.** Precise, high-contrast, confident — a serious dev tool.
  Restraint over decoration. (Full visual contract in `DESIGN.md`.)
- **Zero friction.** Agents act immediately; the human only intervenes at UAT.
- **Visibility.** Every agent action appears on the live dashboard in real time.
- **Simplicity of build.** Plain HTML + vanilla JS, no build step, no frameworks.
- **Desktop-first, mobile usable.** Optimized for the desk; works on iPad via Tailscale.

## What Commander Is Not

- Not multi-user; no auth, roles, or sharing.
- Not a generic project-management tool — it is purpose-built for the Claude Code
  agent loop.
- Not a marketing surface — no landing-page flourish; this is an operator console.
