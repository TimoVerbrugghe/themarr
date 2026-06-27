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

# Run tests
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
- `VERBOSE`, `VERBOSE_MATCHING`, `OVERWRITE` — CLI flags
- `PUSHOVER_APP_TOKEN`, `PUSHOVER_USER_KEY` — optional Pushover notifications
- `WEBHOOK_USERNAME`, `WEBHOOK_PASSWORD` — optional webhook Basic Auth
- `PLEX_RETRY_ATTEMPTS`, `PLEX_RETRY_DELAY` — webhook retry tuning

## Key Files

| File | Purpose |
|---|---|
| `web_app.py` | Flask REST API + Web UI backend |
| `plex_theme_downloader.py` | CLI batch downloader (TV + movies) |
| `templates/index.html` | Single-page web UI |
| `static/css/style.css` | Sonarr-inspired dark theme CSS |
| `static/js/app.js` | Frontend JS (library browser, modals, multi-select) |
| `tests/test_web_app.py` | Web app unit tests |
| `tests/test_plex_theme_downloader.py` | CLI unit tests |
| `.github/workflows/docker-publish.yml` | CI: build + push to ghcr.io on main push |

## Editing Rules for Agents

- Do not commit secrets or real Plex tokens.
- Keep changes minimal and directly related to the user request.
- Preserve Docker-first workflow and existing environment variable names.
- Update `README.md` when behavior, setup, or configuration changes.
- Re-run validation commands after edits.
