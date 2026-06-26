# Themarr

A Docker-based utility that synchronizes TV show theme files from your Plex server to your local media library. Downloads and manages `theme.mp3` files for all shows that have themes available in Plex.

## Features

- 📻 **Automatic Theme Syncing**: Downloads themes directly from Plex for all TV shows
- 🎯 **Smart Matching**: Case-insensitive folder matching using Plex metadata and direct API paths
- 🔄 **Overwrite Mode**: Re-download and replace existing theme files to keep them current
- 📊 **High Coverage**: 98.7% folder matching accuracy using `show.locations` API
- 🐳 **Fully Containerized**: Docker & Docker Compose setup for easy deployment
- 📡 **NFS Support**: Seamless integration with NFS-mounted media volumes
- 🔍 **Detailed Logging**: Verbose output for troubleshooting and monitoring

## How It Works

1. **Connects to Plex**: Authenticates with your Plex server
2. **Retrieves Show Data**: Fetches complete TV library metadata including theme URLs and filesystem locations
3. **Scans Local Storage**: Reads local folder structure
4. **Matches Shows**: Uses Plex's `show.locations[0]` API property for accurate path extraction
5. **Downloads Themes**: Streams MP3 files directly from Plex (8KB chunks for efficiency)
6. **Saves Files**: Creates/overwrites `theme.mp3` in matched show folders

## Prerequisites

- Docker and Docker Compose
- Running Plex Media Server with TV library
- Plex API token (see [Getting Your Plex Token](#getting-your-plex-token))
- TV shows organized in local folder structure
- Network access from Docker container to Plex server

## Quick Start

### 1. Clone Repository

```bash
git clone https://github.com/yourusername/themarr.git
cd themarr
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your Plex credentials
```

### 3. Run

```bash
# Download themes once
docker-compose --env-file .env run --rm themarr

# Or run continuously
docker-compose --env-file .env up -d
```

## AI Ready

This repo is configured for AI coding assistants:

- `AGENTS.md` contains project-level agent guidance and validation commands.
- `.github/copilot-instructions.md` provides GitHub Copilot-specific instructions.

If you use an AI assistant to change code, validate with:

```bash
python3 -m py_compile plex_theme_downloader.py
docker compose config
docker build -t themarr:test .
```

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PLEX_URL` | ✅ | - | Plex server URL (e.g., `http://192.168.1.100:32400`) |
| `PLEX_TOKEN` | ✅ | - | Plex API authentication token |
| `TV_SHOWS_PATH` | ✅ | `/tv` | Container path to TV shows directory |
| `PLEX_LIBRARY_NAME` | ❌ | `TV Shows` | Name of Plex TV library |
| `VERBOSE` | ❌ | `false` | Enable detailed debug logging |
| `VERBOSE_MATCHING` | ❌ | `false` | Show detailed matching information |
| `OVERWRITE` | ❌ | `false` | Re-download themes even if they exist |

### .env.example

```env
PLEX_URL=http://plex.local.timo.be:32400
PLEX_TOKEN=your_plex_token_here
PLEX_LIBRARY_NAME=TV Shows
TV_SHOWS_PATH=/tv

# Optional flags
VERBOSE=false
VERBOSE_MATCHING=false
OVERWRITE=false
```

### NFS Volume Configuration

If using NFS mount, update `docker-compose.yml`:

```yaml
volumes:
  tv-shows:
    driver: local
    driver_opts:
      type: nfs
      o: addr=10.10.10.2,vers=4,soft,timeo=180,retrans=2,noresvport
      device: ":/mnt/path/to/tvshows"
```

## Usage

### One-Time Download

```bash
docker-compose --env-file .env run --rm themarr
```

### Continuous Service

```bash
# Start
docker-compose --env-file .env up -d

# View logs
docker-compose logs -f themarr

# Stop
docker-compose down
```

### Overwrite Existing Themes

```bash
OVERWRITE=true docker-compose --env-file .env run --rm themarr
```

### Verbose Output

```bash
VERBOSE=true VERBOSE_MATCHING=true docker-compose --env-file .env run --rm themarr
```

## Getting Your Plex Token

### Browser Developer Console

1. Open https://www.plex.tv/ and sign in
2. Open browser DevTools (F12)
3. Go to **Network** tab
4. Refresh page
5. Look for any Plex server request
6. Copy the `X-Plex-Token` header value

### cURL Command

```bash
curl -X GET https://plex.tv/api/v2/user \
  -H "Accept: application/json" \
  -u "your-email@example.com:your-password"
```

### Plex Direct URL Method

1. Go to https://www.plex.tv/your-account/
2. Click "Authorized Devices & Applications"
3. Find your token in the authorization headers

## Architecture

### Core Components

- **PlexThemeDownloader**: Plex API client for authentication and theme retrieval
- **TVShowScanner**: Local filesystem scanner with theme.mp3 detection
- **match_shows()**: Intelligent show matching using Plex metadata
- **download_theme()**: Streaming MP3 download with error handling

### Key Implementation Details

- **Path Extraction**: Uses `show.locations[0]` directly from Plex API for 98.7% accuracy
- **Chunk Streaming**: 8KB chunks prevent memory bloat on large files
- **Case-Insensitive Matching**: Normalizes folder names for matching
- **Graceful Error Handling**: Continues on individual download failures
- **Comprehensive Logging**: Debug and info levels for troubleshooting

## Performance Metrics

- **Accuracy**: 98.7% folder-to-show matching
- **Speed**: ~30 seconds for 101 themes (varies by file sizes and network)
- **Coverage**: Successfully handles 149 shows with 151 local folders
- **Success Rate**: 100% download success with proper Plex access
- **Memory Usage**: Efficient streaming prevents large buffers

## Troubleshooting

### No Themes Downloaded

**Symptoms**: Script runs but shows "0 themes downloaded"

**Solutions**:
- Verify Plex token is valid: `curl -H "X-Plex-Token: YOUR_TOKEN" http://plex-url:32400/library/sections`
- Check Plex library name matches configuration exactly
- Ensure TV shows in Plex actually have themes available
- Enable `VERBOSE=true` for detailed logging

### Connection Refused

**Symptoms**: "Cannot connect to Plex server"

**Solutions**:
- Verify `PLEX_URL` is correct and accessible from container
- Test connectivity: `docker-compose run --rm themarr curl http://plex-url:32400`
- For local networks, use IP address instead of hostname
- Check firewall rules on port 32400

### Permission Denied

**Symptoms**: "Permission denied" when creating theme.mp3

**Solutions**:
- Verify NFS mount has `rw` permissions
- Check folder permissions: `chmod 755 /path/to/tvshows/`
- Ensure Docker container can write to mounted volume

### Shows Not Matching

**Symptoms**: Only partial match, some shows skipped

**Solutions**:
- Enable `VERBOSE_MATCHING=true` to see matching logic
- Verify local folder names correspond to Plex show titles
- Check `show.locations` output in logs
- Ensure no leading/trailing spaces in folder names

## Development

### Local Setup

```bash
pip install -r requirements.txt
python3 plex_theme_downloader.py
```

### Docker Build

```bash
docker build -t themarr:latest .
docker-compose up --build
```

### Dependencies

- `plexapi==4.15.1` - Official Plex API wrapper
- `requests==2.31.0` - HTTP client library
- `Python 3.11+`

## File Structure

```
.
├── plex_theme_downloader.py  # Main application
├── docker-compose.yml         # Docker Compose configuration
├── Dockerfile                 # Container image definition
├── requirements.txt           # Python dependencies
├── .env.example              # Environment variables template
├── .gitignore                # Git ignore rules
└── README.md                 # This file
```

## Contributing

Contributions welcome! Please ensure:
- Code follows existing style
- Docker builds without errors
- No hardcoded credentials
- Comprehensive logging statements

## License

MIT

## Support

For issues or questions, please open an issue on GitHub.
