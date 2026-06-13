# Deck deep-link URLs

Stable URLs a Stream Deck (or any monitoring board) opens to jump straight to a
pane. Each key opens the **canonical deck form** below; the dashboard resolves it
on a cold load — selects the project + view, applies the errors filter where
given — and survives refresh and back/forward.

`<P>` is the **project slug** (the short name, e.g. `commander`), as used in
`/project/<slug>/…` and returned by `/api/home`.

## Keys

| Deck key | Open this URL | Lands on |
|---|---|---|
| Running | `/?project=<P>&view=running` | Sprint → **Running** sub-view |
| Activity | `/?project=<P>&view=activity` | **Activity** (Logs) view |
| Activity · errors | `/?project=<P>&view=activity&filter=errors` | Activity pre-filtered to **errors only** |
| Summary | `/?project=<P>&view=summary` | Sprint → **History** (reviewable sprint summaries) |
| Board | `/?project=<P>&view=board` | Sprint → **Board** |
| History | `/?project=<P>&view=history` | Sprint → **History** |
| Tickets | `/?project=<P>&view=tickets` | **Tickets** tab |

## How it resolves (frontend-only, no backend changes)

`/` serves the home page. When it sees `?project=`, it redirects to the canonical
project URL, mapping `view` → tab segment and carrying `view`/`filter` in the
query for the project page to apply:

```
/?project=<P>&view=running                 ->  /project/<P>/sprint?view=running
/?project=<P>&view=board                    ->  /project/<P>/sprint?view=board
/?project=<P>&view=history                  ->  /project/<P>/sprint?view=history
/?project=<P>&view=summary                  ->  /project/<P>/sprint?view=summary   (History sub-view)
/?project=<P>&view=activity                 ->  /project/<P>/logs?view=activity
/?project=<P>&view=activity&filter=errors   ->  /project/<P>/logs?view=activity&filter=errors
/?project=<P>&view=tickets                  ->  /project/<P>/tickets?view=tickets
```

The project page reads `?view=` to pick the sub-view (board / running / history;
`summary` → History) and `?filter=errors` to switch the Activity log to
errors-only. The query string is preserved in the address bar, so a refresh
re-lands on the same pane and back/forward re-applies it.

## Notes

- These URLs carry **no deck-specific fields** (no button ids / jump targets) —
  they're plain app URLs. The deck owns its own key→URL mapping.
- `view=summary` opens the History sub-view, which lists each sprint's reviewable
  Executive Summary. (It does not auto-open one summary; tap the one to review.)
- The errors filter is also a clickable **Errors** pill in the Activity filter
  bar, so it can be toggled off after a deck jump.
