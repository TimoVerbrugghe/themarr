# Agent Guide: Themarr

This repository is prepared for AI coding agents. Use this guide for safe, consistent changes.

## Project Overview

- **Language**: Python 3.11
- **Runtime**: Docker / Docker Compose
- **Entry point**: `plex_theme_downloader.py`
- **Goal**: Sync Plex TV show theme files (`theme.mp3`) into local show folders

## Setup and Validation Commands

Run from repo root:

```bash
# Syntax check
python3 -m py_compile plex_theme_downloader.py

# Validate compose file
docker compose config

# Build container image
docker build -t themarr:test .
```

## Configuration Surface

Primary environment variables are defined in `.env.example`:

- `PLEX_URL`
- `PLEX_TOKEN`
- `PLEX_LIBRARY_NAME`
- `TV_SHOWS_PATH`
- `NFS_MOUNT_OPTIONS`
- `NFS_DEVICE`
- `VERBOSE`
- `VERBOSE_MATCHING`
- `OVERWRITE`

## Editing Rules for Agents

- Do not commit secrets or real Plex tokens.
- Keep changes minimal and directly related to the user request.
- Preserve Docker-first workflow and existing environment variable names.
- Update `README.md` when behavior, setup, or configuration changes.
- Re-run validation commands after edits.

