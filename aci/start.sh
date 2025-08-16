#!/bin/sh

echo "Database is up. Running migrations..."

alembic upgrade head
echo "Database migrations completed."

echo "Starting Uvicorn server..."
exec uvicorn aci.server.main:app \
  --proxy-headers \
  --forwarded-allow-ips='*' \
  --host 0.0.0.0 \
  --port 8000 \
