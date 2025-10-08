#!/bin/bash
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
STACK_NAME="autom8"

# Associative array for images. Format: ["image_name"]="path/to/build/context"
declare -A images
images=(
    ["caddy"]="caddy"
    ["aci-app"]="aci"
    ["code-executor"]="code_executor"
    ["cycletls-server"]="cycletls-server"
    ["headless-browser"]="headless-browser"
)

# --- Helper for colored output ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}--- Building and Pushing Docker Images ---${NC}"
for name in "${!images[@]}"; do
    context="${images[$name]}"
    full_image_name="${LOCAL_REGISTRY}/${name}:latest"

    echo -e "${YELLOW}Processing image: ${name}${NC}"

    echo " > Building from context: ./${context}"
    if [ "$name" = "headless-browser" ]; then
        sudo docker build -t "${full_image_name}" --file "./${context}/docker/Dockerfile" "./${context}"
    else
        sudo docker build -t "${full_image_name}" "./${context}"
    fi

    echo " > Pushing to ${LOCAL_REGISTRY}..."
    sudo docker push "${full_image_name}"
    echo "   Done."
    echo ""
done


echo -e "${GREEN}===================================================${NC}"
echo -e "${GREEN}✅ Prep complete! Images and configs are updated.${NC}"
echo -e "${GREEN}===================================================${NC}"