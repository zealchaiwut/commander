# Roadmap phase 1 — GitHub-native milestones + Roadmap tab

**Date:** 2026-06-11
**Sprint label:** NEW
**Default labels:** enhancement
**Status:** drafted

CEO-loop foundation (vision: milestones always visible above the day-to-day).
Decision: use GitHub's native Milestones API — tickets attach naturally and
milestones flow through the existing issues mirror (zero extra quota).
Phase 2 (planner + sign-off) and phase 3 (advisor) build on this.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Add GitHub milestone support to the backend and the issues mirror. Use GitHub's native Milestones via the REST API: list, create, edit (title, description, due date, state), and close milestones for a project repo, exposed as dashboard endpoints. Extend the issues-mirror sync so each mirrored issue records its milestone (number and title) — the REST issues payload already includes it — and add a mirrored milestones read so milestone lists cost zero GitHub quota after sync. Writes go to GitHub; reads come from the mirror with a gh fallback before first sync. Acceptance: I can create and edit a milestone from an API endpoint, see it on GitHub, and the mirror returns each issue's milestone without extra API calls.
---
Add a Roadmap tab to the project view showing ordered milestone cards. New top-level project tab "Roadmap": one card per open milestone showing title, description, ticket progress (done plus UAT over total, from the mirror), and due date if set. One milestone can be marked Active (stored in project settings); the active one is visually dominant. Cards support inline create, edit, reorder (order persisted in project settings), and close-when-done. Closed milestones collapse into a small history row. Follow the existing sharp/technical design system (tokens, 5-6px radii, no side-stripes). Acceptance: I can manage my project's milestones entirely from the Roadmap tab and see per-milestone ticket progress at a glance.
---
Let tickets carry a milestone from creation through the board. The BA bulk-create flow and the single new-ticket dialog get a milestone selector (defaulting to the project's Active milestone); posting assigns the GitHub milestone on the issue. The sprint board ticket rows and the ticket detail panel show the milestone as a small chip; the backlog panel can filter by milestone. Acceptance: a ticket created from bulk-create lands on GitHub with the chosen milestone, the board shows its milestone chip, and I can filter backlog by milestone.
---
Show milestone progress in the sprint nav and home project cards. The project's Active milestone appears with a compact progress indicator (done plus UAT over total) on the home page project card and in the project header area, so the high-level goal is always visible while working the day-to-day board. Clicking it opens the Roadmap tab. Acceptance: from home I can see each project's active milestone and progress without opening the project.
```

## Notes

- Mirror note: REST issues payload carries `milestone`; `_normalise_issue`
  needs to keep it and `upsert_issues`/`_row_to_issue` pass it through (raw
  column already preserves extras — verify the read path).
- Active-milestone + card order live in project settings (existing settings
  store), not on GitHub.
- Phase 2 planner consumes: active milestone + its open tickets + estimates.

## Posted issues

| # | Title | Size |
|---|-------|------|
| _pending_ | | |
