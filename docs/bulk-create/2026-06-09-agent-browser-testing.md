# Agent-Browser Testing + impeccable Skills

**Date:** 2026-06-09
**Sprint label:** NEW
**Default labels:** enhancement
**Status:** drafted

Bring live browser-based UAT into the test process with
[agent-browser](https://github.com/vercel-labs/agent-browser) so the tester runs
visual/interactive UAT steps instead of marking them MANUAL, captures
screenshots for human review, and lets the BA flag which steps are
agent-testable. Plus a separate ticket wiring impeccable *skills* into BA design
requirements and coder frontend work.

## Feasibility notes

- **agent-browser** — native Rust CLI, drives real Chrome (navigate, click, fill,
  screenshot). Install: `npm i -g agent-browser` then `agent-browser install`
  (downloads Chrome for Testing). No Playwright/Node needed at runtime.
- **impeccable detect** — static UI anti-pattern linter (current design gate). Does
  NOT render pages.
- **impeccable skills** — design-rule skill packs (`npx impeccable skills install`
  → `.github/skills/impeccable/`). Loadable by BA/coder for design guidance.
- **Screenshots** — agent-browser captures them natively; saved per issue and
  attached to the test report + sprint summary for visual sign-off.

Dependency order: ticket 1 (runner) is the foundation; tickets 2 and 5… build on
it; ticket 4 needs 1; ticket 3 (BA) and ticket 5 (impeccable skills) are
independent. The estimator DAG sequences them.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Install and wire agent-browser (https://github.com/vercel-labs/agent-browser) as a tester capability for live browser-based UAT. agent-browser is a native CLI that drives a real Chrome to navigate, click, fill forms, and take screenshots. Add a helper module in services/sprint_manager/ (e.g. agent_browser_runner.py) that: (1) detects whether agent-browser is installed (`which agent-browser`) and Chrome is set up (`agent-browser install` was run); (2) exposes a function run_browser_step(step_text, base_url) that translates a natural-language UAT step into agent-browser commands (navigate to the URL, perform the described interaction, assert the expected outcome) and returns a structured result {status: pass|fail|uncovered, detail, screenshot_path}; (3) returns "uncovered" when the step is not automatable by a browser (e.g. mobile-device-specific, requires external hardware) so the caller can fall back to MANUAL. Graceful skip when agent-browser is not installed: log a warning and report all browser steps as MANUAL, never crash the sprint. Acceptance: with agent-browser installed, run_browser_step navigates and returns pass/fail with a screenshot path; without it installed, the tester degrades to MANUAL with no error.
---
Update the tester to run agent-browser for browser-testable UAT steps instead of marking them MANUAL. In .claude/agents/tester.md Step 6 (Evaluate UAT steps), change the logic: for each UAT step, if it is flagged as agent-testable by the BA (see the BA ticket) OR clearly describes a browser interaction (open page, click, see, navigate), attempt it with the agent_browser_runner instead of marking MANUAL. Record the result as PASS or FAIL with the screenshot. Only mark MANUAL when agent-browser returns "uncovered" or is not installed. HTTP-only steps still use httpx as today. Update the overall-status rule so a FAIL from a browser step counts as NEEDS_FIXES, exactly like a failed AC. Also update the tester dispatch in services/sprint_manager/sprint_manager.py so the agent_browser_runner is available in the tester environment and its results feed the test report. Acceptance: a ticket whose UAT step is "open localhost:8000/project/x and click Run Sprint, expect a spinner" is executed by agent-browser and reported PASS/FAIL with a screenshot, not MANUAL.
---
Add BA support to flag which acceptance criteria / UAT steps are agent-browser testable when creating a ticket. In .claude/agents/ba.md, when writing UAT steps, the BA should mark each step that can be verified by driving a browser with an explicit tag the tester can parse — e.g. append "[agent-test]" to browser-verifiable UAT steps, and leave steps that need a human (subjective visual judgment, real device, external service) untagged so they stay MANUAL. Add a short rule explaining when to tag (any step expressible as navigate + interact + observe in a desktop browser) vs not. Ensure scripts/create_ticket.py and the issue template (.github/ISSUE_TEMPLATE/feature.md) carry the tag through to the created issue body unchanged. Acceptance: a newly created ticket shows "[agent-test]" on browser-verifiable UAT steps, and the tester reads those tags to decide which steps to run via agent-browser.
---
Capture and surface agent-browser screenshots as a sprint review artifact. Extend the agent_browser_runner to save a screenshot for every browser step to a per-issue directory (e.g. .commander/sprints/sprint-<N>/screenshots/issue-<N>/step-<k>.png). After the tester finishes a ticket, attach the screenshots to the test report comment posted on the GitHub issue so they render inline when the human reviews UAT, and list them in the sprint summary report under each ticket. Keep total size reasonable (cap resolution, skip duplicates). Graceful: if no browser steps ran, add nothing. Acceptance: after a sprint, each ticket that had browser UAT steps shows its screenshots inline in the issue's test report and is referenced in the sprint summary, so I can visually verify the UI without running anything myself.
---
Wire impeccable skills into BA design requirements and coder frontend work so UI tickets carry concrete design rules end to end, with an attached mock HTML as the visual source of truth. impeccable ships design-rule skill packs via `npx impeccable skills install`, which scaffold into `.github/skills/impeccable/` and are loaded by agents through `node .github/skills/impeccable/scripts/context.mjs`. (1) Ensure the skills are installed/scaffolded as part of project setup (extend .commander/setup.sh or the design-docs guard so `.github/skills/impeccable/` exists). (2) In .claude/agents/ba.md, when a ticket has frontend/UI scope: if a mock HTML file is attached to the issue (under references/issue-<N>/), the BA reads the mock and extracts its actual design tokens — colors, spacing, typography, component structure — cross-checks them against the impeccable skills, and bakes those concrete values into the acceptance criteria so the mock becomes the design contract; if no mock is attached, the BA falls back to the generic impeccable design rules (spacing scale, contrast, component naming, responsive breakpoints). Either way the AC must state explicit design expectations, not implied ones. (3) In .claude/agents/coder.md, when implementing frontend, the coder loads the same impeccable skills AND, when a mock HTML is attached, treats that mock as the pixel/visual target to reproduce, so its output matches the mock, follows the design system, and passes the existing impeccable `detect` design gate on the first try. Acceptance: for a UI ticket with an attached mock, the AC references concrete design tokens extracted from that mock and the coder reproduces the mock as the visual source of truth; for a UI ticket without a mock, the AC references impeccable design rules; in both cases the coder loads the skills before writing frontend code and the design gate pass rate on frontend tickets improves.
```

## Posted issues

| # | Title | Size |
|---|-------|------|
| _pending_ | | |
