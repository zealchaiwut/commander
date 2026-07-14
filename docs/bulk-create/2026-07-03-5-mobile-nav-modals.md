# Mobile Compatibility — Nav Tab Strip, Sprint/Deploy/Settings Reach, Modal Overflow

**Date:** 2026-07-03
**Sprint label:** sprint-108
**Default labels:** frontend, bug
**Status:** posted

Source: 2026-07-03 mobile review — live browser test at a true 390×844 viewport
against the running dashboard (project page, Tailscale) plus a code audit of
develop @ 4043bd1b. Both converge. Verified live: the page itself scrolls
sideways (documentElement.scrollWidth 410 vs 390 viewport); the sub-tab strip
overflows with the Settings tab at x=325 w=86 → right edge 411px, off-screen
with `overflow: visible` and no scroll affordance; the Manage dropdown itself
renders fine (Logs/Deploy/Analytics reachable when Manage is on-screen).
Viewport meta is present on both pages — this is real layout overflow, not
desktop scaling. There is NO breakpoint below 480px anywhere in project.html;
home.html's lowest is 560px.

All fixes are project.html inline CSS/HTML (live on refresh, no bundle
rebuild) unless a prompt says otherwise.

## Prompts

```
Make the project-page sub-tab strip reachable at 390px: swipeable overflow + tighter header padding.

Verified live at 390×844: .sub-tabs (project.html:583, redesign override :9603) is display:flex, overflow:visible, with .stab { flex-shrink:0; white-space:nowrap; margin-right:16px } (:9613). Below 768px the visible tabs (Sprint · Tickets · Manage▾ · Planning▾ · Settings, :10182-10238) total ≈410px while the available width inside .proj-header padding (var(--space-8)=32px each side, :457) is ≈326px — the Settings tab lands at right edge 411px on a 390px viewport: clipped, unreachable, no scroll. Deploy is a menuitem inside Manage▾ (stab-deploy :10203) so it is only reachable while Manage▾ is on-screen. This is the "Settings button unclickable on mobile" report.

Fix: below 768px give the strip real horizontal scrolling — .sub-tabs { overflow-x:auto; overflow-y:visible; flex-wrap:nowrap; -webkit-overflow-scrolling:touch; scrollbar-width:none } — and add a ≤430px breakpoint that reduces .proj-header horizontal padding to var(--space-4) and hides the "Commander" wordmark (brand mark stays). CAUTION: the comment at :580 says the strip keeps overflow visible so the Manage/Planning dropdown menus are not clipped — those menus are position:absolute children of .stab-group. With overflow-x:auto they WILL clip. Either (a) reposition the open dropdown with position:fixed anchored to the trigger's getBoundingClientRect (small JS change in the existing toggle), or (b) render the menus into a body-level container. Verify both dropdowns open fully at 390px after the change.

AC must cover: at 390px every tab including Settings is reachable by horizontal swipe; Manage and Planning dropdowns open unclipped at 390px and at desktop widths; no regression at ≥768px (strip unchanged); documentElement.scrollWidth == 390 on the Sprint tab (no page-level sideways scroll from the strip).
---
Cap modal height globally: Finish/Merge and Bulk-complete modals currently grow past the viewport with the confirm button unreachable.

Base .modal (project.html:3829) is position:fixed; top:50%; left:50%; transform:translate(-50%,-50%) with NO max-height and NO overflow. .modal-body { overflow-y:auto } (:3854) never engages because the modal itself is unbounded. Consequence at 390×844: #fs-modal (Finish/Merge Sprint, :29487) and #bc-modal (Bulk complete, :29526) — both bare class="modal" with long ticket-list previews — grow past 100vh, pushing the header off the top and the Confirm/Cancel footer off the bottom, with no way to scroll. This is the "pop-up modal always breaking" report. Six more width-only variants share the latent defect: .ns-modal (:3887), .ct-modal (:1824), .cl-modal (:1826), .bcm-modal (:1861), .re-est-modal (:5754), .hs-modal (:4522).

Fix: on base .modal add max-height:calc(100dvh - 32px); display:flex; flex-direction:column; overflow:hidden, make .modal-body { flex:1 1 auto; overflow-y:auto; min-height:0 } and keep header/footer fixed within the modal. Normalize width on the width-only variants to width:min(<current>px, calc(100vw - 32px)) — .hs-modal/.mt-modal still use fixed min-width:320-360px (:4521-4522) which exceeds 390−32=358px. Follow the already-correct patterns in .apm-modal (:3790-3792) and reconcile-modal.js:111 (width:min(520px,92vw); max-height:80vh). Do NOT touch the modals that are already capped (.nt-modal :3890, .lv-modal :6866, .pf-modal :7053, .apm-modal).

AC must cover: fs-modal and bc-modal with a 50-item ticket list at 390×844 show header + scrollable body + reachable footer; every .modal variant fits within 100dvh−32 and 100vw−32; backdrop click-to-close still works (:23000-23014); desktop rendering unchanged for short content; screenshots (or DOM-rect assertions) at 390×844 for fs-modal and ns-modal attached to the PR.
---
Touch targets to 44px under @media (hover:none): run-sprint button, modal close, icon buttons.

Verified sub-44px targets in project.html: .smgmt-run-btn ≈24px tall (padding:4px 12px; font-size:11px, :3088) — this is the primary "run sprint" action; .modal-close ≈24px hit area (font-size:20px; padding:0 2px, :3848); .btn-icon 32px (:88). The existing @media (hover:none) block (:7930) already enlarges table row actions to ~44px but skips these three.

Fix: extend the (hover:none) block: .smgmt-run-btn { min-height:44px; padding:10px 16px } (or an equivalent tap-area pseudo-element if visual size must stay compact), .modal-close { min-width:44px; min-height:44px; display:grid; place-items:center }, .btn-icon { min-width:44px; min-height:44px }. Keep desktop/hover rendering unchanged.

AC must cover: the three controls measure ≥44×44 CSS px under (hover:none) emulation; no layout shift at desktop widths; run button remains aligned in the sprint-card header at 390px (that header already goes column at max-480, :3084).
---
Eliminate page-level horizontal overflow at ≤430px: sticky chrome, subnav, and sprint-card header leak past the viewport.

Verified live at 390px: documentElement.scrollWidth = 410. Overflowing containers measured in the live DOM: .proj-sticky-chrome clientWidth 378 / scrollWidth 411, .proj-header 378/411 (header), .sub-tabs 314/379 (fixed by the tab-strip ticket), .smgmt-sticky-area and .subnav 378/391, .sc-header.smgmt-sprint-header 321/361, .smgmt-sprint-header-right 321/345 — all overflow:visible, so the whole page pans sideways and taps land unpredictably (contributes to "buttons not clickable"). There is no breakpoint below 480px in the file.

Fix: add a @media (max-width:430px) block that (a) allows the subnav pills row (Board/Running/History + New Sprint, .subnav :~9640) to wrap or horizontally scroll; (b) lets .smgmt-sprint-header-right wrap under the sprint title (the max-480 block :3084 already columns the header — extend it to the right-side cluster and the Open-in-Running/tickets meta); (c) audits remaining fixed min-widths inside .proj-sticky-chrome (search wrap max-width:320 :198 is fine; check the create/auto-refresh cluster :540 flex-shrink:0). Acceptance is mechanical: no element's border-box right edge may exceed 390.

AC must cover: documentElement.scrollWidth == 390 on Sprint (board + running + history), Issues, Manage>Deploy, and Settings tabs at 390×844 with a running sprint rendered; assert via a DOM-walk test or documented live measurement; no visual regression at ≥768px.
---
Deploy tab at mobile: investigate stuck 'Loading environments…' first paint and make deploy cards single-column at ≤430px.

Two parts. (1) INVESTIGATE-FIRST (do not assume): in a 390px live session the Deploy pane sat on 'Loading environments…' (#deploy-grid-empty, project.html:10768) indefinitely, while the backing endpoints answered fast when curled directly (/api/deploy/overview 200 in ~113ms, /api/projects/{slug}/environments 200 in ~17ms). The grid is populated by fetch('/api/deploy/overview') → _deployRenderGrid (:32190-32217) which replaces the placeholder; its .catch writes an error message — a permanently stuck placeholder means the fetch promise neither resolved nor rejected in that session, or _deployInit never ran when the tab was entered via the Manage▾ menuitem (stab-deploy :10203). Reproduce: open the project page at ≤430px, enter Deploy via Manage▾, and trace whether _deployInit fires and the overview fetch settles. Fix whatever the trace shows (likely a tab-activation path that skips init when arriving from the dropdown, or a guard keyed to desktop-only layout). If it cannot be reproduced after an honest attempt, document that with the trace and close this half.

(2) Deploy cards responsive: the deploy grid is a desktop multi-column layout; per the overview payload each card carries host/branch/SHA/status plus Deploy/Restart/Start/Stop actions. At ≤430px make the grid single-column (repeat(auto-fit, minmax(280px, 1fr)) or explicit 1fr) with action buttons ≥44px and full-width, and confirm the confirm-dialogs those actions open comply with the modal height-cap ticket.

AC must cover: entering Deploy via the Manage▾ dropdown at 390px renders environment cards (or a reproduced-and-fixed init bug with regression test); cards single-column at ≤430px; action buttons ≥44px; deploy/restart confirm flows usable end-to-end at 390×844 (no off-screen confirm).
```

## Posted issues

| # | Title | Size |
|---|-------|------|
| 1765 | Fix sub-tab strip scroll on mobile (390px) | M |
| 1766 | Cap modal height to prevent off-screen overflow on mobile | M |
| 1767 | Expand touch targets to 44px on mobile for key controls | M |
| 1768 | Fix horizontal overflow on mobile viewports ≤430px | M |
| 1769 | Fix stuck Deploy loader and mobile-responsive cards at 430px | L |
