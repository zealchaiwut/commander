# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

```
commander/
├── dashboard/   # FastAPI web application (Python 3.12)
├── projects/    # Project data / configs
└── venv/        # Root-level Python 3.14 venv (separate from dashboard)
```

## Dashboard (FastAPI)

The `dashboard/` service uses its own venv at `dashboard/venv/` with Python 3.12.

**Activate the dashboard venv:**
```bash
source dashboard/venv/bin/activate
```

**Run the dev server (from repo root):**
```bash
cd dashboard && uvicorn main:app --reload
```

**Key installed packages:** FastAPI 0.136, Pydantic v2, uvicorn + uvloop, websockets, python-dotenv, PyYAML.

## Root venv

The root `venv/` uses Python 3.14. Activate with:
```bash
source venv/bin/activate
```
