#!/usr/bin/env bash
set -euo pipefail

PORT="${FLASK_PORT:-5000}"
WORKERS="${GUNICORN_WORKERS:-2}"
ACCESS_LOG="${GUNICORN_ACCESS_LOG:--}"
ERROR_LOG="${GUNICORN_ERROR_LOG:--}"

exec gunicorn \
  --bind "0.0.0.0:${PORT}" \
  --workers "${WORKERS}" \
  --access-logfile "${ACCESS_LOG}" \
  --error-logfile "${ERROR_LOG}" \
  flask_server:app
