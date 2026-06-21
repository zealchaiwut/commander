# Commander Product Overview

Commander is a personal AI agent platform for solo development with Claude Code. It tracks feature lifecycle (BA → Coder → Tester → UAT) via GitHub issues and sprints.

## Core Features

- Sprint management with composite-key invariant: `(label, project)` uniqueness
- Agent event tracking and live dashboard
- UAT environment separation from production
- Automated testing and merge gating

## Design Principles

- Single-user, local-first
- GitHub-native issue tracking
- No auth (trusted network only)
- Separation of concerns: product/design docs guide architecture decisions
