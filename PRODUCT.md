# Product Context

## What Commander Is

Commander is a personal AI agent platform for solo development with Claude Code.
It automates the BA → Coder → Tester → UAT workflow using GitHub Issues as a
sprint board, with a live dashboard at `localhost:8000`.

## Target Users

Solo developer using Claude Code agents to manage feature development cycles.
Mobile-accessible via Tailscale for monitoring sprints on the go.

## Core User Flows

1. **Sprint planning** — select backlog issues from the dashboard, assign sprint label
2. **Sprint execution** — run sprint manager; watch agents work issue by issue
3. **UAT sign-off** — review merged features in the dashboard, approve or reject
4. **Sprint summary** — auto-generated report filed as a GitHub issue

## Design Principles

- Zero friction: agents act immediately; humans only intervene at UAT
- Visibility: every agent action appears on the live dashboard in real time
- Simplicity: plain HTML + vanilla JS, no build steps, no frameworks
- Mobile-first secondary access via Tailscale
