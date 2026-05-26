---
description: Run the documentor agent for a GitHub issue — commits minimal README/CLAUDE.md updates to the feature branch and posts a plain-language UAT comment. Usage: /document <issue-url>
---

Extract the issue number from the provided URL (e.g. `https://github.com/zealchaiwut/commander/issues/42` → `42`).

Then run:

```bash
# Discover git root
GIT_ROOT="$(git rev-parse --show-toplevel)"

# Extract repo from git remote (or use default)
REPO="$(cd "$GIT_ROOT" && python3 -c "import sys; sys.path.insert(0, 'apps/dashboard'); import github_client; print(github_client.repo())" 2>/dev/null || echo "zealchaiwut/commander")"

# Run the documentor
python3 "$GIT_ROOT/services/sprint_manager/document_issue.py" \
  --issue <N> \
  --repo "$REPO" \
  --mode both
```

Report back:
- Whether README.md was updated (and what was changed)
- Whether CLAUDE.md was updated (and what was changed)
- Whether the UAT comment was posted (and a preview of the comment)
- The path to the cached output JSON
