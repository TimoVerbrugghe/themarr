FROM python:3.14-slim

# Install system dependencies (ffmpeg for yt-dlp audio extraction)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY plex_theme_downloader.py .
COPY web_app.py .
COPY templates/ templates/
COPY static/ static/

# Expose Web UI port
EXPOSE 8080

# Default: run the Web UI
CMD ["python", "web_app.py"]
