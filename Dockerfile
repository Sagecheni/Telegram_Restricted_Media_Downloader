FROM python:3.13-slim

# Environment
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

# System deps for pymediainfo and timezone handling
RUN apt-get update && \
    apt-get install -y --no-install-recommends libmediainfo0v5 tzdata build-essential && \
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

# Declare common mount points
VOLUME ["/opt/trmd/sessions", "/opt/trmd/temp", "/root/.config/TRMD", "/data", "/links"]

# Default command: run the app (interactive prompts will appear on first run)
CMD ["python", "main.py"]
