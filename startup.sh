#!/bin/bash
# Azure App Service startup: use PORT from environment (Azure sets this)
set -e
PORT="${PORT:-8000}"

# Use virtualenv if present (antenv on Azure Oryx, .venv or Poetry cache locally)
if [ -d "antenv/bin" ]; then
  source antenv/bin/activate
elif [ -d ".venv/bin" ]; then
  source .venv/bin/activate
elif command -v poetry >/dev/null 2>&1; then
  POETRY_VENV=$(poetry env info -p 2>/dev/null)
  if [ -n "$POETRY_VENV" ] && [ -d "$POETRY_VENV/bin" ]; then
    source "$POETRY_VENV/bin/activate"
  fi
fi

echo "Starting gunicorn on 0.0.0.0:${PORT}"
exec gunicorn -k uvicorn.workers.UvicornWorker \
  --bind "0.0.0.0:${PORT}" \
  --workers 2 \
  --timeout 120 \
  --access-logfile - \
  --error-logfile - \
  application:app
