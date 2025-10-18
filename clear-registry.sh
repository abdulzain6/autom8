#!/bin/bash
set -e
REGISTRY_URL="http://localhost:5000"

echo "🧹 Running Docker Registry Garbage Collection..."
docker exec registry bin/registry garbage-collect /etc/docker/registry/config.yml --delete-untagged=true

UPLOADS_DIR=$(docker inspect registry -f '{{range .Mounts}}{{if eq .Destination "/var/lib/registry"}}{{.Source}}{{end}}{{end}}')/docker/registry/v2/repositories
if [ -d "$UPLOADS_DIR" ]; then
  echo "🗑️ Removing orphaned _uploads directories..."
  find "$UPLOADS_DIR" -type d -name "_uploads" -exec rm -rf {} + || true
fi

echo "✅ Cleanup done — registry storage optimized."
