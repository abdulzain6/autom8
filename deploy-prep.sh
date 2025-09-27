b#!/bin/bash
# Exit immediately if a command exits with a non-zero status.
set -e

# ==============================================================================
# Swarm Pre-Deployment Script
# ==============================================================================
# This script prepares your environment for a 'docker stack deploy' by:
# 1. Building all custom images defined in the 'images' array.
# 2. Tagging and pushing them to the local registry.
# 3. Removing and re-creating all Docker configs from the latest files.
#
# USAGE:
# 1. Customize the 'images' and 'configs' arrays below if needed.
# 2. Make the script executable: chmod +x deploy-prep.sh
# 3. Run it: ./deploy-prep.sh
# ==============================================================================

# --- Configuration ---
LOCAL_REGISTRY="localhost:5000"

# Associative array for images. Format: ["image_name"]="path/to/build/context"
declare -A images
images=(
    ["caddy"]="caddy"
    ["aci-app"]="aci"
    ["code-executor"]="code_executor"
    ["cycletls-server"]="cycletls-server"
)

# Associative array for configs. Format: ["config_name"]="path/to/file"
declare -A configs
configs=(
    ["caddy_file"]="caddy/Caddyfile"
    ["livekit_yaml"]="livekit/livekit.yaml"
    ["searxng_settings"]="searxng/settings.yml"
    ["loki_config"]="monitoring/loki-config.yml"      # <-- Added Loki config
    ["promtail_config"]="monitoring/promtail-config.yml" # <-- Added Promtail config
)

# --- Helper for colored output ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# --- 1. Build and Push Docker Images ---
echo -e "${GREEN}--- Building and Pushing Docker Images ---${NC}"
for name in "${!images[@]}"; do
    context="${images[$name]}"
    full_image_name="${LOCAL_REGISTRY}/${name}:latest"

    echo -e "${YELLOW}Processing image: ${name}${NC}"

    echo " > Building from context: ./${context}"
    sudo docker build -t "${full_image_name}" "./${context}"

    echo " > Pushing to ${LOCAL_REGISTRY}..."
    sudo docker push "${full_image_name}"
    echo "   Done."
    echo ""
done

# --- 2. Upsert Docker Configs ---
echo -e "${GREEN}--- Updating Docker Configs ---${NC}"
for name in "${!configs[@]}"; do
    path="${configs[$name]}"

    echo -e "${YELLOW}Processing config: ${name}${NC}"

    # Upsert logic: Remove the config if it already exists
    if sudo docker config inspect "$name" &>/dev/null; then
        echo " > Config exists. Removing old version."
        sudo docker config rm "$name"
    fi

    # Create the new config from the file
    echo " > Creating new version from: ./${path}"
    sudo docker config create "$name" "./${path}"
    echo "   Done."
    echo ""
done


echo -e "${GREEN}===================================================${NC}"
echo -e "${GREEN}✅ Prep complete! Images and configs are updated.${NC}"
echo "You can now safely deploy or update your stack:"
echo ""
echo "   docker stack deploy -c docker-stack.yml myapp"
echo ""
echo -e "${GREEN}===================================================${NC}"