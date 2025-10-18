#!/bin/bash
set -e
REGISTRY_URL="http://localhost:5000"

echo "🧹 Running Docker Registry Garbage Collection..."
# This command runs garbage collection *inside* the container, which is correct
docker exec registry bin/registry garbage-collect /etc/docker/registry/config.yml --delete-untagged=true

# --- THIS IS THE FIX ---
# Update this path to match the volume you created in Step 3
UPLOADS_DIR="/opt/registry/docker/registry/v2/repositories" 
# --- END FIX ---

if [ -d "$UPLOADS_DIR" ]; then
  echo "🗑️ Removing orphaned _uploads directories..."
  find "$UPLOADS_DIR" -type d -name "_uploads" -exec rm -rf {} + || true
fi

echo "✅ Cleanup done — registry storage optimized."