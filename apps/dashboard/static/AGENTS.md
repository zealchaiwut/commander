# AGENTS.md — apps/dashboard/static

## Purpose

Vanilla HTML + JavaScript frontend for the Commander dashboard. No build
step — files are served directly from disk by uvicorn. Changes take effect
on the **next page refresh** without a server restart. The running production
launchd instance picks up static changes immediately.

## Key Files

- `project.html` — main project view: sprint board, activity/logs tabs, SSE live updates
- `analytics.html` — token usage and cost analytics dashboard
- `diagnostics.html` — system diagnostics page (DB state, GitHub connectivity)
- `home-preview.html` — aggregated home page showing all projects
- `add-project-modal.js` — shared modal component for adding a new project
- `log-colorize.js` — log entry colorization utilities used by multiple pages

## Conventions

- **No build step** — plain HTML + vanilla JS only; no React, Vue, Svelte, or bundler.
- **No external CSS frameworks** — inline styles or `<style>` blocks only.
- **No JS modules** — no `import`/`export`; all code in `<script>` tags or plain `.js` files.
- **Live updates via SSE** — use `EventSource` and follow existing patterns in `project.html`.
- **Design** — follow impeccable rules; run `npx impeccable detect <file>` before committing UI changes.
- **Mobile layout** — test on Tailscale-connected mobile; the dashboard is mobile-accessible.

## Danger Zones

- `project.html` is very large — surgical edits only; always search for the relevant section before editing.
- SSE event names and payload shapes — changing them requires coordinating with the matching `server.py` SSE endpoint.
- Global JS variable names — check for collisions before adding new globals; there is no module scope.
- SSE reconnection logic — fragile; do not modify without testing real disconnect/reconnect scenarios.

## What NOT to Touch

- `log-colorize.js` public API — `project.html` and other pages depend on its function signatures.
- Inline CSS that controls mobile/Tailscale layout — do not remove without verifying on a mobile device.
- Sprint board column ordering and CSS classes — the sprint manager and tests reference these class names.

<!-- needs-review: hotfix/board-history-running-ux — directory had changes; review and update this file -->
