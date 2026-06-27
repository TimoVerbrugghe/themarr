# Copilot Instructions for Themarr

## What this repo does

Themarr manages Plex theme music (`theme.mp3`) for TV shows and movies.
It includes a Flask-based **web UI** (`web_app.py`), a CLI batch downloader
(`plex_theme_downloader.py`), Sonarr/Radarr webhook support, and Pushover
notifications.

## Local validation before finishing a task

Always run **all** of these before pushing changes:

```bash
# Syntax check
python3 -m py_compile plex_theme_downloader.py
python3 -m py_compile web_app.py

# Unit tests (78 tests, must all pass)
python3 -m pytest tests/ -v

# Validate Docker Compose config
docker compose config

# Build container image
docker build -t themarr:test .
```

## Web UI change rule — screenshots MUST be updated

If you modify **any** of the following files you **must** regenerate the
screenshots and commit them alongside your code changes:

- `templates/index.html`
- `static/css/style.css`
- `static/js/app.js`

**How to regenerate screenshots locally (only when explicitly requested):**

```bash
pip install playwright
playwright install chromium
python3 scripts/take_screenshots.py
```

The script starts Flask with mocked API data (no real Plex needed), takes
Playwright screenshots of every UI state, and writes them to `screenshots/`.

The `.github/workflows/screenshots.yml` GitHub Actions workflow runs this
automatically on any PR that touches UI files (artifact only), then commits
the regenerated screenshots to `main` after merge.

During normal agent coding/testing sessions, do **not** run
`python3 scripts/take_screenshots.py` and do **not** commit `screenshots/**`
changes to feature branches.

## Implementation constraints

- Keep behavior Docker-compatible.
- Keep environment variable names stable unless explicitly asked to migrate them.
- Do not hardcode credentials, server URLs, or filesystem paths.
- Favor explicit logging and clear error messages.
- Update README when setup/behavior/config changes.
- Do not generate screenshots during normal agent coding/testing sessions; rely
  on CI unless explicitly asked.

## Files to check when changing configuration

- `.env.example`
- `docker-compose.yml`
- `Dockerfile`
- `README.md`

## Key web UI files

| File | Purpose |
|---|---|
| `templates/index.html` | Single-page app shell |
| `static/css/style.css` | Sonarr-inspired dark/light theme CSS |
| `static/js/app.js` | Frontend logic (library browser, modals, multi-select, settings) |
| `scripts/take_screenshots.py` | Playwright screenshot helper (mock Plex data) |
| `.github/workflows/screenshots.yml` | CI workflow — screenshot artifacts on UI PRs; auto-updates on main |
| `.github/workflows/sanitize-screenshot-changes.yml` | CI workflow — auto-removes direct screenshots/ changes in branches/PRs |
