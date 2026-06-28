FROM python:3.14-slim

# Install system dependencies:
#  - ffmpeg: for yt-dlp audio extraction
#  - nodejs (≥22): yt-dlp requires Node.js 22+ as its JavaScript runtime for YouTube extraction
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends ffmpeg nodejs && \
    rm -rf /var/lib/apt/lists/* && \
    rm -rf /var/cache/apt/*

WORKDIR /app

# Create non-root user for running the application
RUN groupadd -r themarr && useradd -r -g themarr themarr

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY web_app.py .
COPY app/ app/
COPY templates/ templates/
COPY static/ static/

# Fix permissions for the non-root user
RUN chown -R themarr:themarr /app

# Expose Web UI port
EXPOSE 8080

# Switch to non-root user
USER themarr

# Default: run the Web UI
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--timeout", "120", "web_app:app"]
