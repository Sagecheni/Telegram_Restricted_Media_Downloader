#!/usr/bin/env bash
set -euo pipefail

# Default cookies file path inside container; can be overridden via YTDLP_COOKIES_FILE
COOKIES_FILE="${YTDLP_COOKIES_FILE:-/cookies/cookies.txt}"

if [[ ! -r "$COOKIES_FILE" ]]; then
  echo "Error: cookies file '$COOKIES_FILE' not found or not readable." >&2
  echo "Hint: mount your exported cookies.txt to ${COOKIES_FILE} or set YTDLP_COOKIES_FILE." >&2
  exit 1
fi

# Optional extra arguments for yt-dlp (e.g. '-vUS res,proto')
EXTRA_ARGS="${YTDLP_EXTRA_ARGS:-}"

exec yt-dlp ${EXTRA_ARGS} --cookies "$COOKIES_FILE" "$@"

