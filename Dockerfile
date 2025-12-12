FROM python:3.13-slim

# Environment
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

# System deps for pymediainfo, yt-dlp (ffmpeg) and timezone handling
RUN apt-get update && \
    apt-get install -y --no-install-recommends libmediainfo0v5 tzdata build-essential ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/trmd

# Copy dependency list first to leverage Docker layer caching
COPY requirements.txt ./

# Install Python deps; filter out Windows-only pyreadline3 on Linux
RUN set -eux; \
    grep -v '^pyreadline3$' requirements.txt > /tmp/requirements.linux.txt; \
    pip install --no-cache-dir -r /tmp/requirements.linux.txt

# Copy project files
COPY . .

# Install helper wrapper for yt-dlp that automatically uses mounted cookies.txt
RUN install -m 755 res/yt-dlp-x.sh /usr/local/bin/x-yt-dlp

# Declare common mount points
VOLUME ["/opt/trmd/sessions", "/opt/trmd/temp", "/root/.config/TRMD", "/data", "/links", "/cookies"]

# Default command: run the app (interactive prompts will appear on first run)
CMD ["python", "main.py"]
