# Themarr

<p align="center">
  <img src="static/logo-themarr.svg" alt="Themarr logo" width="420">
</p>

Themarr is a web app for managing theme songs for TV shows and movies.  
It supports **Plex and Jellyfin libraries side-by-side** and can save themes as local `theme.mp3` files next to your media.

Theme download sources in the web UI:

- **Plex** (provider theme, where available)
- **ThemerrDB** (matched via IMDB/TMDB/TVDB IDs for Plex and Jellyfin items)
- **YouTube** (via `yt-dlp`)

You can also upload custom MP3 files and copy existing local themes between items.

## Features

- **Dual media-server support**  
  Browse **Plex Libraries** and **Jellyfin Libraries** in separate sidebar groups in one UI.

- **Three download sources**  
  Download from **Plex** (Plex items), **ThemerrDB** (Plex + Jellyfin), or **YouTube**.

- **Upload and copy local themes**  
  Upload custom MP3 files or copy `theme.mp3` between items/libraries.

- **Bulk actions**  
  Multi-select and bulk download themes from provider source for Plex libraries.

- **Plex + Jellyfin webhooks**  
  Automatically process new library items from webhook events.

- **Pushover notifications (optional)**  
  Receive push notifications when downloads complete.

- **Dark/light UI + poster/list views**  
  Configurable defaults with in-browser preference switching.

### Gallery

| Scene | Dark | Light |
|---|---|---|
| Login screen | ![Login screen dark](screenshots/06_login_screen_dark.png) | ![Login screen light](screenshots/06_login_screen_light.png) |
| Welcome screen | ![Welcome screen dark](screenshots/07_welcome_screen_dark.png) | ![Welcome screen light](screenshots/07_welcome_screen_light.png) |
| Poster view | ![Poster view dark](screenshots/01_poster_view_dark.png) | ![Poster view light](screenshots/01_poster_view_light.png) |
| List view | ![List view dark](screenshots/02_list_view_dark.png) | ![List view light](screenshots/02_list_view_light.png) |
| YouTube downloader | ![YouTube downloader dark](screenshots/03_youtube_downloader_dark.png) | ![YouTube downloader light](screenshots/03_youtube_downloader_light.png) |
| Copy theme | ![Copy theme dark](screenshots/04_copy_theme_dark.png) | ![Copy theme light](screenshots/04_copy_theme_light.png) |
| Plex download | ![Plex download dark](screenshots/05_plex_download_dark.png) | ![Plex download light](screenshots/05_plex_download_light.png) |

## Quick start (Docker Compose)

Themarr is designed to run with the `docker-compose.yml` in this repository.

1. Copy the example environment file:

   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and set your Plex and/or Jellyfin settings.

3. Start Themarr:

   ```bash
   docker compose up --build
   ```

4. Open:

   ```text
   http://localhost:8080
   ```

### Important: path mounts must match your media-server container paths

Themarr writes `theme.mp3` files directly to the media library on disk. To locate the right folder, it uses the file-system path that Plex or Jellyfin reports for each item — so Themarr's container must mount the **same host directories** at the **same container paths** that your media-server container uses.

Edit the `volumes` section of `docker-compose.yml` to match your actual library paths:

```yaml
volumes:
  # Use the SAME path you use to mount your media in the plex/jellyfin container so that the absolute path
  # inside Themarr matches the path reported by your Plex/Jellyfin container.
  - /<path to tvshows>:/tvshows
  - /<path to movies>:/movies
```

If your Plex or Jellyfin container mounts `/data/media/tv`, use `/data/media/tv:/data/media/tv` here.

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `PLEX_URL` | No* | — | Plex server URL (example: `http://192.168.1.100:32400`) |
| `PLEX_TOKEN` | No* | — | Plex API token |
| `JELLYFIN_URL` | No* | — | Jellyfin server URL (example: `http://192.168.1.50:8096`) |
| `JELLYFIN_API_KEY` | No* | — | Jellyfin API key |
| `JELLYFIN_USER_ID` | No | first Jellyfin user | Optional explicit Jellyfin user ID |
| `FLASK_DEBUG` | No | `false` | Enables Flask debug mode |
| `DEFAULT_THEME` | No | `dark` | Default UI theme: `dark` or `light` |
| `DEFAULT_VIEW` | No | `list` | Default library view: `list` or `grid` |
| `AUTH_USERNAME` | No | — | Username for the login screen. Set together with `AUTH_PASSWORD` when `DISABLE_AUTH=false`. |
| `AUTH_PASSWORD` | No | — | Password for the login screen (used with `AUTH_USERNAME` when `DISABLE_AUTH=false`). |
| `DISABLE_AUTH` | No | `false` | Set to `true` to disable all UI authentication. Only use this when a trusted reverse proxy already handles auth (see [Disable auth](#disable-auth-reverse-proxy)). |
| `API_KEY` | No | auto-generated | API key for programmatic/webhook access. If unset, Themarr generates one at startup and logs it. |
| `WEBHOOK_USERNAME` | No | — | Optional Basic Auth username for Plex/Jellyfin webhook endpoints |
| `WEBHOOK_PASSWORD` | No | — | Optional Basic Auth password for Plex/Jellyfin webhook endpoints |
| `PUSHOVER_APP_TOKEN` | No | — | Pushover app token (required together with `PUSHOVER_USER_KEY`) |
| `PUSHOVER_USER_KEY` | No | — | Pushover user/group key (required together with `PUSHOVER_APP_TOKEN`) |

\* Configure at least one provider (Plex and/or Jellyfin).

## Authentication

Themarr has two Web UI authentication modes plus always-on API key support:

### Credentials (recommended)

Set both `AUTH_USERNAME` and `AUTH_PASSWORD` in your `.env`.  
A login screen is shown on first visit asking for username and password.

```env
AUTH_USERNAME=admin
AUTH_PASSWORD=yourpassword
```

### Missing credentials while auth is enabled

If `DISABLE_AUTH=false` and either `AUTH_USERNAME` or `AUTH_PASSWORD` is missing, the Web UI only shows a warning page until both are set.

### API key (automation)

`API_KEY` is independent from UI auth mode and is intended for scripts, API clients, and webhook callers.
If `API_KEY` is not set, Themarr generates an API key at startup and logs it:

```bash
docker logs <container> 2>&1 | grep "startup API key"
```

### Disable auth (reverse-proxy)

Set `DISABLE_AUTH=true` to bypass all UI authentication.  
Use this **only** when a trusted reverse proxy (e.g. Authelia, Authentik, nginx Basic Auth) already handles authentication in front of Themarr.

```env
DISABLE_AUTH=true
```

> **Note:** `API_KEY` is still used for programmatic API access (webhooks, API clients) regardless of the UI auth mode.

## Source behavior by provider

| Action | Plex items | Jellyfin items |
|---|---|---|
| Download from Plex source | ✅ | ❌ |
| Preview Plex source theme | ✅ | ❌ |
| Download from ThemerrDB | ✅ (via IMDB/TMDB/TVDB) | ✅ (via IMDB/TMDB/TVDB) |
| Preview ThemerrDB source theme | ✅ | ✅ |
| Download from YouTube | ✅ | ✅ |
| Upload local MP3 | ✅ | ✅ |
| Copy local theme | ✅ | ✅ |
| Delete local theme | ✅ | ✅ |

## Webhooks

### Plex Webhooks
Themarr can process Plex webhook events for newly added library items.

#### Setup

1. In Plex, go to **Settings > Webhooks**
2. Click **Add Webhook**
3. Enter your Themarr webhook URL:
   ```
   http://<themarr-host>:8080/api/webhooks/plex
   ```
4. Click **Save**

### Jellyfin Webhooks

Themarr can process Jellyfin webhook events for newly added library items.

Unlike Plex webhooks, Jellyfin webhook automation only downloads from **ThemerrDB**:

- If local `theme.mp3` already exists, Themarr skips the item.
- If ThemerrDB has no matching theme for the item, nothing is downloaded.

#### Setup

1. In Jellyfin, install/enable the **Webhook** plugin.
2. In the Webhook plugin UI, create a new generic webhook destination.
3. Set the destination URL to:
   ```
   http://<themarr-host>:8080/api/webhooks/jellyfin
   ```
4. In the destination's event/notification checkboxes, enable **Item Added**.
5. Keep the request body format as JSON and leave the payload template at its default plugin values (no custom template required).
6. Save and test the webhook.

## Optional webhook hardening

- Reuse `WEBHOOK_USERNAME` + `WEBHOOK_PASSWORD` to protect both Plex and Jellyfin webhook endpoints with Basic Auth.

## License

This project is licensed under the [MIT License](LICENSE).
