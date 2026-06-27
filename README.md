# Themarr

Themarr manages Plex theme music for TV shows and movies. It now includes both the original CLI downloader and a Flask-based web UI for browsing libraries, previewing themes, and downloading, uploading, or deleting `theme.mp3` files.

## Features

- Web UI for TV and movie libraries
- Plex theme preview and one-click download
- Manual MP3 upload support
- YouTube audio import via `yt-dlp`
- Existing CLI downloader for TV show batch sync
- Docker-first deployment

## Requirements

- Docker / Docker Compose
- Plex server URL and token
- Writable media folders mounted into the container
- `ffmpeg` available when using YouTube downloads (included in Docker image)

## Quick start

```bash
cp .env.example .env
# edit .env

docker compose up --build
```

Open `http://localhost:8080`.

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `PLEX_URL` | Yes | - | Plex server URL |
| `PLEX_TOKEN` | Yes | - | Plex API token |
| `TV_SHOWS_HOST_PATH` | Yes | `/mnt/tv` | Host TV library path mounted to `/tv` |
| `MOVIES_HOST_PATH` | No | `/mnt/movies` | Host movie library path mounted to `/movies` |
| `TV_SHOWS_PATH` | No | `/tv` | CLI-compatible container TV path |
| `TV_PATH` | No | `/tv` | Web UI alias for `TV_SHOWS_PATH` |
| `MOVIES_PATH` | No | `/movies` | Container movie path |
| `WEB_PORT` | No | `8080` | Published web port |
| `FLASK_DEBUG` | No | `false` | Flask debug mode |
| `VERBOSE` | No | `false` | CLI logging flag |
| `VERBOSE_MATCHING` | No | `false` | CLI match logging flag |
| `OVERWRITE` | No | `false` | Overwrite existing themes |

## Migration note

`TV_SHOWS_PATH` remains supported for the original CLI flow. The new web app also accepts `TV_PATH`, and the provided compose file exports both to avoid breaking older setups.

## Web app

Run locally:

```bash
pip install -r requirements.txt
python3 web_app.py
```

API endpoints include:

- `GET /api/status`
- `GET /api/libraries`
- `GET /api/libraries/<id>/items`
- `GET /api/items/<ratingKey>/theme`
- `GET /api/items/<ratingKey>/theme/preview`
- `POST /api/items/<ratingKey>/theme/download`
- `POST /api/items/<ratingKey>/theme/upload`
- `POST /api/items/<ratingKey>/theme/youtube`
- `DELETE /api/items/<ratingKey>/theme`

## CLI downloader

The original downloader remains available:

```bash
python3 plex_theme_downloader.py
```

Use `TV_SHOWS_PATH` for the CLI's TV root.

## Validation

```bash
python3 -m py_compile plex_theme_downloader.py
python3 -m py_compile web_app.py
docker compose config
docker build -t themarr:test .
```
