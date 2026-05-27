# PRD Changelog — Sprint N (YYYY-MM-DD)

**Sprint(s) included:** sprint-N
**Date merged to master:** YYYY-MM-DD
**PR:** #N — [link](https://github.com/zealchaiwut/commander/pull/N)
**Environment:** PRD (port 8000, `master` branch)

---

## What Changed

Brief summary of everything that moved from `develop` to `master` in this merge.

### Features & Fixes

| Issue | Title | Type |
|---|---|---|
| #N | Short title | feature / fix / chore |

---

## What to Test on PRD

Smoke-test the items below on `http://localhost:8000` after restarting PRD
(`restart-prd`).

### Checklist

- [ ] **#N — Title**
  - Where to look: _UI location or API endpoint_
  - Verify: _what should work in production_

- [ ] **Regression check**
  - Dashboard loads without errors
  - Agent event feed is live (trigger a hook call and confirm it appears)
  - Sprint Mgmt tab opens and shows correct sprint state
  - UAT approve/reject buttons work on any UAT-labelled ticket

---

## Rollback

If something is broken on PRD and can't be hot-fixed immediately:

```bash
git checkout master
git revert HEAD --no-edit
git push origin master
restart-prd
```

---

## Notes

Post-merge observations, follow-up tickets, or deferred items.
