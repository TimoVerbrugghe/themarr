# Agent Guide: Themarr

This repository is prepared for AI coding agents. Use this guide for safe, consistent changes.

## Project Overview

- **Language**: Python 3.11
- **Runtime**: Docker / Docker Compose (base image: `python:3.11-slim`)
- **Web UI entry point**: `web_app.py` (Flask, port 8080)
- **Goal**: Manage theme music (`theme.mp3`) for Plex and Jellyfin libraries via Web UI (Plex, ThemerrDB, and YouTube download sources)

## Application Structure

The application is organized into a Flask entry point plus a supporting `app/` package:

| File/Directory | Purpose |
|---|---|
| `web_app.py` | Flask app — all HTTP routes, request handling, cache management |
| `app/media_utils.py` | Filesystem path validation, upload size/type constants, theme directory scanning |
| `app/external_ids.py` | IMDB/TMDB/TVDB ID extraction for Plex and Jellyfin items |
| `app/youtube_utils.py` | YouTube URL validation, yt-dlp option builders, stream helpers |
| `app/plex_utils.py` | Plex server connection (`get_plex()`), library path helpers |
| `app/jellyfin_utils.py` | Jellyfin connection helpers, media path resolution, provider normalization |
| `tests/test_web_app.py` | Unit tests (111 tests, all must pass) |

## Setup and Validation Commands

Run from repo root:

```bash
# Syntax check
python3 -m py_compile web_app.py
python3 -m py_compile app/*.py

# Validate compose file
docker compose config

# Run tests (must all pass)
python3 -m pytest tests/ -v

# Build container image
docker build -t themarr:test .
```

## Configuration Surface

Primary environment variables are defined in `.env.example`:

- `PLEX_URL`, `PLEX_TOKEN` — Plex server credentials
- `JELLYFIN_URL`, `JELLYFIN_API_KEY`, `JELLYFIN_USER_ID` — Jellyfin server credentials/user context
- `TV_SHOWS_HOST_PATH`, `MOVIES_HOST_PATH` — set automatically by the container's volume mounts in `docker-compose.yml` (**security boundary**: constrains filesystem write operations to the mounted library roots). Not user-facing env vars in `.env.example` — users edit the `volumes:` section of `docker-compose.yml` directly instead.
- `FLASK_DEBUG` — Flask debug mode (never enable in production)
- `DEFAULT_THEME` — default UI theme: `dark` or `light`
- `DEFAULT_VIEW` — default library view: `list` or `grid`
- `API_KEY` — API key protecting mutating API endpoints; auto-generated and logged at startup when not set
- `PUSHOVER_APP_TOKEN`, `PUSHOVER_USER_KEY` — optional Pushover notifications
- `WEBHOOK_USERNAME`, `WEBHOOK_PASSWORD` — optional webhook Basic Auth (both must be set)
- `PLEX_RETRY_ATTEMPTS`, `PLEX_RETRY_DELAY` — webhook retry tuning

## Key Files

| File | Purpose |
|---|---|
| `web_app.py` | Flask REST API + Web UI backend |
| `app/` | Supporting modules (see Application Structure above) |
| `templates/index.html` | Single-page web UI shell |
| `static/css/style.css` | Sonarr-inspired dark/light theme CSS |
| `static/js/app.js` | Frontend JS (library browser, modals, multi-select, settings) |
| `tests/test_web_app.py` | Web app unit tests |
| `.github/workflows/docker-publish.yml` | CI: build + push to ghcr.io on main push |
| `.github/workflows/screenshots.yml` | CI: screenshot artifacts on UI PRs; auto-update on main |
| `.github/workflows/sanitize-screenshot-changes.yml` | CI: auto-removes direct screenshots/ changes from branches/PRs |

## Editing Rules for Agents

- Do not commit secrets or real Plex tokens.
- Keep changes minimal and directly related to the user request.
- Preserve Docker-first workflow and existing environment variable names.
- Update `README.md` when behavior, setup, or configuration changes.
- Re-run validation commands after edits.
- When modifying `app/` modules, check that all imports in `web_app.py` still resolve correctly.

## Security Notes

- **API key**: `GET /api/settings/runtime` is an **authenticated** endpoint — it requires a valid session cookie or API key header and returns the actual key in the response. The key is never written to `localStorage`. Users log in via the Settings page; the server sets an httpOnly session cookie (`POST /api/auth/login`). The key is kept in JS memory (`apiKey`) for the lifetime of the tab.
- **Media root validation**: `TV_SHOWS_HOST_PATH` / `MOVIES_HOST_PATH` env vars are security controls read from the environment — they are populated via the `docker-compose.yml` volume mount paths, not from `.env`. Do not add them to `.env.example`.
- **yt-dlp**: `remote_components` must NOT be enabled (supply-chain risk — fetches and executes JS from GitHub at runtime).
- **ThemerrDB URLs**: always validate with `is_valid_youtube_url()` before passing to yt-dlp.

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
