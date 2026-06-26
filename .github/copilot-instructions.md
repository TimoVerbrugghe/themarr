# Copilot Instructions for Themarr

## What this repo does

Themarr downloads TV show theme music from Plex and writes `theme.mp3` files into local show folders.

Main script: `plex_theme_downloader.py`

## Local validation before finishing a task

Always run:

```bash
python3 -m py_compile plex_theme_downloader.py
docker compose config
docker build -t themarr:test .
```

## Implementation constraints

- Keep behavior Docker-compatible.
- Keep environment variable names stable unless explicitly asked to migrate them.
- Do not hardcode credentials, server URLs, or filesystem paths.
- Favor explicit logging and clear error messages.
- Update README when setup/behavior/config changes.

## Files to check when changing configuration

- `.env.example`
- `docker-compose.yml`
- `Dockerfile`
- `README.md`

