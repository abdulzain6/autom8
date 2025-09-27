#!/bin/bash
# This script deletes all images from the local Docker registry and reclaims space.
set -e

REGISTRY_URL="http://localhost:5000"

echo "--- Starting Registry Cleanup ---"

# Get a list of all repositories (image names)
REPOS=$(curl -s "${REGISTRY_URL}/v2/_catalog" | jq -r '.repositories[]' || true)

if [ -z "$REPOS" ]; then
    echo "✅ No repositories found. Registry is already empty."
    exit 0
fi

echo "Found repositories to delete: ${REPOS}"

# Loop through each repository
for repo in $REPOS; do
    echo " > Processing repository: ${repo}"
    
    # Get a list of all tags for the repository
    TAGS=$(curl -s "${REGISTRY_URL}/v2/${repo}/tags/list" | jq -r '.tags[]' || true)

    if [ -z "$TAGS" ]; then
        echo "   - No tags found for ${repo}."
        continue
    fi

    # Loop through each tag and delete its manifest
    for tag in $TAGS; do
        echo "   - Deleting tag: ${tag}"
        
        # Get the manifest digest required for deletion
        DIGEST=$(curl -s -I \
            -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
            "${REGISTRY_URL}/v2/${repo}/manifests/${tag}" \
            | awk '/^docker-content-digest:/ {print $2}' | tr -d '\r')

        # Send the DELETE request
        if [ -n "$DIGEST" ]; then
            curl -s -X DELETE "${REGISTRY_URL}/v2/${repo}/manifests/${DIGEST}" > /dev/null
        else
            echo "     ! Warning: Could not find digest for tag ${tag}. Skipping."
        fi
    done
done

echo ""
echo "--- Running Garbage Collection to Reclaim Disk Space ---"

# Find the registry container's ID
REGISTRY_CONTAINER_ID=$(sudo docker ps -q --filter "name=registry.")

if [ -z "$REGISTRY_CONTAINER_ID" ]; then
    echo "Error: Could not find the registry container."
    exit 1
fi

# Execute the garbage collection command inside the container
sudo docker exec "$REGISTRY_CONTAINER_ID" \
    registry garbage-collect /etc/docker/registry/config.yml

echo ""
echo "✅ All images have been deleted and space has been reclaimed."