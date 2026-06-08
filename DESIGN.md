# Design Context

Commander's frontend uses plain HTML + vanilla JS with an inline dark-mode aesthetic.
No build step, no framework. Pages are served from `apps/dashboard/static/`.

## Design Tokens

```
Background:    #0f172a  (slate-900)
Surface:       #1e293b  (slate-800)
Border:        #334155  (slate-700)
Text primary:  #f1f5f9  (slate-100)
Text secondary:#94a3b8  (slate-400)  — keep contrast ≥ 4.5:1 on surface
Accent:        #6366f1  (indigo-500)
Success:       #22c55e  (green-500)
Warning:       #f59e0b  (amber-500)
Error:         #ef4444  (red-500)
```

## Typography

- Body + UI: system-ui, -apple-system, sans-serif
- Monospace (agent logs, code): "SF Mono", ui-monospace, monospace
- Pair display headings with body text — avoid single-font pages

## Layout Conventions

- Max content width: 1280px, centered with auto margins
- Cards: rounded-lg (8px), 1px slate-700 border, slate-800 background
- Spacing rhythm: 4 / 8 / 16 / 24 / 32 px multiples

## Accessibility Targets

- WCAG AA minimum: 4.5:1 contrast for body text, 3:1 for large/bold text
- Interactive elements: visible focus ring, min 44×44 px touch target
- Semantic HTML: use `<button>`, `<nav>`, ARIA labels on icon-only controls

## Known Issues (from impeccable detect)

- `diagnostics.html`: low-contrast #6b7280 on #f3f4f6 (4.4:1, needs 4.5:1) — TODO fix
- `diagnostics.html`: single-font (SF Mono only) — TODO pair with body font
- Several pages use a single font family throughout — add typographic hierarchy

## Component Naming Conventions

- Use kebab-case for CSS classes and custom HTML attributes: `agent-card`, `sprint-badge`, `data-issue-num`
- Prefix state classes with the element type: `btn--primary`, `card--active`, `badge--error`
- Interactive containers: `<section>`, `<article>`, or `<div role="region">` + descriptive `aria-label`
- Icon-only controls: always include a `title` attribute and `aria-label`
- Reusable micro-components named by function, not appearance: `status-pill` not `green-circle`
- Avoid generic class names like `container`, `wrapper`, `inner` — be specific: `sprint-header`, `issue-list`

## Design Anti-Patterns to Avoid

- Flat monochrome cards with no depth or border separation
- All-caps labels without letter-spacing
- Tight line-height on paragraph text (use ≥ 1.5)
- Overuse of a single accent color with no neutrals
