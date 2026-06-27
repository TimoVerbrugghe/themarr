# Themarr

<p align="center">
  <img src="static/logo-themarr.svg" alt="Themarr logo" width="420">
</p>

Themarr is a web app for managing theme songs for both TV shows and movies.  
It helps you download theme songs from Plex or YouTube with options to upload your own custom audio and/or copy existing themes between items, then stores them as `theme.mp3` files next to your media. 

With theme songs in place, Plex can play that theme music while you browse your library items.

## Features

- **Plex library integration**  
  Browse your Plex TV and movie libraries directly in Themarr, including poster/list views and quick playback of current themes.

- **Download theme songs from Plex**  
  Save a Plex-provided theme as local `theme.mp3` in one click.

- **Download theme songs from YouTube**  
  Paste a YouTube URL and download audio as `theme.mp3`.

- **Upload your own custom theme files**  
  Upload an MP3 from your device and use it as the show/movie theme.

- **Copy themes between TV shows and movies**  
  Reuse an existing `theme.mp3` from one item on another item (including cross-library copy).

- **Bulk actions**  
  Multi-select multiple items and download themes in batch.

- **Plex webhooks**  
  Automatically download themes when new items are added to your Plex library.

- **Pushover notifications (optional)**  
  Get push notifications when theme downloads complete.

- **Dark and light UI themes**  
  Choose your preferred interface theme and default library view.

## Quick start (Docker Compose)

Themarr is designed to run with the `docker-compose.yml` in this repository.

1. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and set your Plex URL/token and media paths.

3. Start Themarr:

   ```bash
   docker compose up --build
   ```

4. Open:

   ```text
   http://localhost:8080
   ```

### Important: path mounts must match Plex exactly

Your TV/movies (and any other Plex library) mount paths in Themarr **must be the same inside-container paths used by your Plex container**.  
Themarr uses the exact paths reported by Plex.

Example:

```yaml
volumes:
  - /media/tvshows:/media/tvshows
  - /media/movies:/media/movies
```

If Plex uses `/media/tvshows` and `/media/movies`, Themarr must use those same container paths too.

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `PLEX_URL` | Yes | — | Plex server URL (example: `http://192.168.1.100:32400`) |
| `PLEX_TOKEN` | Yes | — | Plex API token |
| `TV_SHOWS_HOST_PATH` | Usually | `/mnt/tv` | TV library host path mounted into container at the same path |
| `MOVIES_HOST_PATH` | Optional | `/mnt/movies` | Movies library host path mounted into container at the same path |
| `FLASK_DEBUG` | No | `false` | Enables Flask debug mode |
| `DEFAULT_THEME` | No | `dark` | Default UI theme: `dark` or `light` |
| `DEFAULT_VIEW` | No | `list` | Default library view: `list` or `grid` |
| `VERBOSE` | No | `false` | Enables verbose logging |
| `PUSHOVER_APP_TOKEN` | No | — | Pushover app token (required together with `PUSHOVER_USER_KEY`) |
| `PUSHOVER_USER_KEY` | No | — | Pushover user/group key (required together with `PUSHOVER_APP_TOKEN`) |

## Plex Webhooks

Themarr can automatically download themes when new items are added to your Plex library using Plex webhooks.

### Setup

1. In Plex, go to **Settings > Webhooks**
2. Click **Add Webhook**
3. Enter your Themarr webhook URL:
   ```
   http://<themarr-host>:8080/api/webhooks/plex
   ```
   Replace `<themarr-host>` with your Themarr server's IP or hostname

4. Click **Save**

### How it works

- When you add a new item to your Plex library, Plex sends a webhook event to Themarr
- Themarr checks if the item already has a `theme.mp3` file
- If not, and if the item has a theme in Plex, Themarr downloads it automatically
- If Pushover notifications are configured, you'll receive a notification when the download completes

## Screenshots

### Dark theme

| Poster view | List view |
|---|---|
| ![Poster view dark](screenshots/01_poster_view_dark.png) | ![List view dark](screenshots/02_list_view_dark.png) |

| YouTube downloader | Copy theme from |
|---|---|
| ![YouTube downloader dark](screenshots/03_youtube_downloader_dark.png) | ![Copy theme dark](screenshots/04_copy_theme_dark.png) |

| Plex download |
|---|
| ![Plex download dark](screenshots/05_plex_download_dark.png) |

### Light theme

| Poster view | List view |
|---|---|
| ![Poster view light](screenshots/06_poster_view_light.png) | ![List view light](screenshots/07_list_view_light.png) |

| YouTube downloader | Copy theme from |
|---|---|
| ![YouTube downloader light](screenshots/08_youtube_downloader_light.png) | ![Copy theme light](screenshots/09_copy_theme_light.png) |

| Plex download |
|---|
| ![Plex download light](screenshots/10_plex_download_light.png) |
