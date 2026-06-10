# Design Context

This is the design contract for Commander's dashboard. It is the brief impeccable
(and the coder agent) should follow when designing, auditing, or redesigning any
UI. Read it before touching `apps/dashboard/static/*.html`.

## Design Intent

**Sharp & technical** — the feel of Vercel and Render. A serious developer tool:
crisp, high-contrast, confident, fast. Restrained color over a neutral base, with
monospace for data/metrics. No decoration for its own sake; every element earns
its place. Precise alignment and spacing read as craft.

- **Personality:** sharp, technical, precise. Not playful, not corporate, not loud.
- **One accent:** blue `#2563eb` over a neutral base. Use it sparingly — for the
  primary action and active state, not everywhere. Status colors (green/amber/red)
  are reserved for state, never decoration.
- **Monospace for data:** sprint numbers, durations, counts, costs, shas, logs.
  Body/UI text stays in the sans stack.

## Themes — light and dark are both first-class

Both themes ship and both must look intentional and designed — neither is an
afterthought. Toggled via `data-theme` on `<html>`. Design and verify in both.

### Light tokens (default)
```
--bg:            #f9fafb    page background
--surface:       #ffffff    cards / panels
--surface-2:     #f3f4f6    inset / alt rows / track
--surface-hover: #f5f6f8
--border:        #e5e7eb
--text:          #111827    primary
--text-muted:    #6b7280    secondary
--text-sub:      #9ca3af    tertiary / captions  (keep ≥ 4.5:1 on surface)
--blue:   #2563eb   --blue-bg:  #dbeafe     accent / primary
--green:  #16a34a   --green-bg: #dcfce7     success / done
--amber:  #d97706   --amber-bg: #fef3c7     warning / pending
--red:    #dc2626   --red-bg:   #fee2e2     error / failed
--purple: #7c3aed   --purple-bg:#ede9fe     (sparingly)
```

### Dark tokens
```
--bg:            #0d0d0d
--surface:       #161616
--surface-2:     #1e1e1e
--surface-hover: #222222
--border:        #2a2a2a
--text:          #e5e7eb
--text-muted:    #9ca3af
--text-sub:      #6b7280
--blue:   #60a5fa   --blue-bg:  rgba(96,165,250,.13)
--green:  #22c55e   --green-bg: rgba(34,197,94,.13)
--amber:  #eab308   --amber-bg: rgba(234,179,8,.13)
--red:    #f87171   --red-bg:   rgba(239,68,68,.13)
--purple: #a78bfa   --purple-bg:rgba(167,139,250,.13)
```

Always reference these CSS variables — never hardcode hex in components, so both
themes stay correct.

## Typography

- **Body + UI:** `-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif`
- **Monospace (data, logs, code):** `'SF Mono', ui-monospace, 'Cascadia Code', monospace`
- **Scale:** 11 (micro) · 12 (small) · 13 (body-sm) · 14 (body) · 16/18 (heading) · 24+ (page title)
- **Weights:** 400 body · 600 headings/labels · 700 emphasized metrics
- **Hierarchy is mandatory** (see audit focus): a page must have clear levels —
  page title › section heading › card title › body › caption. Differentiate with
  size + weight + color, not size alone. Never a wall of same-weight text.

## Spacing & layout — balanced density

- **Scale (use only these):** 4 · 8 · 12 · 16 · 24 · 32 px. No off-scale values.
- **Balanced** — readable with breathing room, but no wasted space. Not airy, not
  cramped. Card padding 16; gaps between cards 16–24; section gaps 24–32.
- **Cards:** `border-radius: 10px; border: 1px solid var(--border); background: var(--surface)`.
  Depth from the border + subtle surface contrast, not heavy shadows.
- **Desktop-first, mobile usable:** design for a wide desktop screen; max content
  width ~1280px, centered. Must remain usable on iPad (Tailscale monitoring) —
  responsive down to tablet, 44×44px touch targets, no horizontal scroll.
- **Alignment is non-negotiable** (see audit focus): everything on a shared grid;
  consistent left edges; numbers right-aligned in tables; labels and values aligned.

## Component conventions

- CSS classes kebab-case, named by function not appearance: `status-pill`,
  `sprint-badge`, `agent-card` — never `green-circle`, `wrapper`, `box`.
- State modifiers prefixed by element: `btn--primary`, `card--active`, `badge--error`.
- Icons: Tabler (`ti ti-*`). Icon-only controls require `title` + `aria-label`.
- Charts: Chart.js; grid lines `rgba(0,0,0,.06)` light; tooltips dark, mono, rounded.
- Status as pills/badges using the `*-bg` token for fill and the solid token for text.

## Accessibility

- WCAG AA: 4.5:1 body contrast, 3:1 large/bold. `--text-sub` on `--surface` is the
  tight one — verify in both themes.
- Visible focus ring on every interactive element; semantic HTML (`<button>`,
  `<nav>`, `<table>`); `aria-label` on icon-only controls.

## Audit focus — fix these first

The current UI's three biggest weaknesses (impeccable should target these):

1. **Weak hierarchy** — too much same-weight text; nothing guides the eye. Establish
   clear typographic levels and use weight/color/size together so the important
   thing is obviously important.
2. **Inconsistent spacing & alignment** — padding/gaps vary between components and
   things don't line up. Snap everything to the 4–32 scale and a shared grid;
   audit every card/row for consistent padding and aligned edges.
3. **Looks plain / generic-AI** — reads like a default template. Add craft: precise
   borders, intentional mono/sans pairing, a confident single accent, considered
   empty/loading/error states. Make it feel deliberately Vercel-sharp, not auto-generated.

## Anti-patterns to avoid

- Same-weight, same-size text blocks with no hierarchy.
- Off-scale spacing; misaligned card edges; ragged number columns.
- Accent blue (or status colors) used as decoration instead of meaning.
- Heavy drop shadows, gradients, or rounded-everything that softens the sharp feel.
- Hardcoded hex that breaks dark mode.
- All-caps labels without letter-spacing; line-height < 1.5 on paragraphs.

## Key screens (each must earn its hierarchy)

Home (project cards + activity), Sprint Mgmt board, Analytics (Calibration/Metrics/
Status/Trends), Logs (activity + agent runs), Settings, Deploy, Bulk Create. Each
has one primary job — make that job's primary element the visual anchor.
