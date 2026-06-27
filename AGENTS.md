# Agent Guide: Themarr

This repository is prepared for AI coding agents. Use this guide for safe, consistent changes.

## Project Overview

- **Language**: Python 3.11
- **Runtime**: Docker / Docker Compose
- **Web UI entry point**: `web_app.py` (Flask, port 8080)
- **CLI entry point**: `plex_theme_downloader.py`
- **Goal**: Manage Plex theme music (`theme.mp3`) for TV shows and movies via Web UI or batch CLI

## Setup and Validation Commands

Run from repo root:

```bash
# Syntax check
python3 -m py_compile plex_theme_downloader.py
python3 -m py_compile web_app.py

# Validate compose file
docker compose config

# Run tests (78 tests, must all pass)
python3 -m pytest tests/ -v

# Build container image
docker build -t themarr:test .
```

## Configuration Surface

Primary environment variables are defined in `.env.example`:

- `PLEX_URL`, `PLEX_TOKEN` — Plex server credentials
- `TV_SHOWS_HOST_PATH`, `MOVIES_HOST_PATH` — host paths mounted into container
- `TV_SHOWS_PATH` / `TV_PATH` — container path for TV shows (default `/tv`)
- `MOVIES_PATH` — container path for movies (default `/movies`)
- `WEB_PORT` — web UI port (default `8080`)
- `FLASK_DEBUG` — Flask debug mode
- `DEFAULT_THEME` — default UI theme: `dark` or `light`
- `VERBOSE`, `VERBOSE_MATCHING`, `OVERWRITE` — CLI flags
- `PUSHOVER_APP_TOKEN`, `PUSHOVER_USER_KEY` — optional Pushover notifications
- `WEBHOOK_USERNAME`, `WEBHOOK_PASSWORD` — optional webhook Basic Auth
- `PLEX_RETRY_ATTEMPTS`, `PLEX_RETRY_DELAY` — webhook retry tuning

## Key Files

| File | Purpose |
|---|---|
| `web_app.py` | Flask REST API + Web UI backend |
| `plex_theme_downloader.py` | CLI batch downloader (TV + movies) |
| `templates/index.html` | Single-page web UI shell |
| `static/css/style.css` | Sonarr-inspired dark/light theme CSS |
| `static/js/app.js` | Frontend JS (library browser, modals, multi-select, settings) |
| `tests/test_web_app.py` | Web app unit tests |
| `tests/test_plex_theme_downloader.py` | CLI unit tests |
| `.github/workflows/docker-publish.yml` | CI: build + push to ghcr.io on main push |
| `.github/workflows/screenshots.yml` | CI: screenshot artifacts on UI PRs; auto-update on main |
| `.github/workflows/sanitize-screenshot-changes.yml` | CI: auto-removes direct screenshots/ changes from branches/PRs |

## Editing Rules for Agents

- Do not commit secrets or real Plex tokens.
- Keep changes minimal and directly related to the user request.
- Preserve Docker-first workflow and existing environment variable names.
- Update `README.md` when behavior, setup, or configuration changes.
- Re-run validation commands after edits.

## Screenshot Rule

**Do not generate or commit screenshots during normal agent coding/testing
sessions.**

When modifying `templates/index.html`, `static/css/style.css`, or
`static/js/app.js`, rely on CI:

- PRs: screenshot artifact only (no branch commit)
- main: screenshots regenerated and committed automatically after merge

### Why

The `screenshots/` directory in the README serves as the primary visual
documentation of the UI.  Stale screenshots mislead users and reviewers.

### Automation

`.github/workflows/screenshots.yml` runs automatically when UI files change:

- **Any PR** — uploads screenshots as a downloadable workflow artifact.
- **Pushes to `main`** — commits updated screenshots directly to `main`.

If screenshot files are accidentally committed in a branch, CI sanitizes those
changes automatically for same-repo branches/PRs.
