FROM python:3.14-slim

# Install system dependencies:
#  - ffmpeg: for yt-dlp audio extraction
#  - nodejs (≥22): yt-dlp requires Node.js 22+ as its JavaScript runtime for YouTube extraction
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends ffmpeg nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY web_app.py .
COPY templates/ templates/
COPY static/ static/

# Expose Web UI port
EXPOSE 8080

# Default: run the Web UI
CMD ["python", "web_app.py"]
