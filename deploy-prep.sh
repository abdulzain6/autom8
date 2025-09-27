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

# --- 2. Gracefully Update Docker Configs (Zero-Downtime) ---
echo -e "${GREEN}--- Updating Docker Configs ---${NC}"
for config_base_name in "${!configs[@]}"; do
    service_and_path="${configs[$config_base_name]}"
    service_name="${service_and_path%%:*}"
    file_path="${service_and_path#*:}"
    
    echo -e "${YELLOW}Processing config '${config_base_name}' for service '${service_name}'${NC}"

    # Create a new, versioned config with a timestamp
    new_config_name="${config_base_name}_$(date +%s)"
    echo " > Creating new version: ${new_config_name}"
    sudo docker config create "${new_config_name}" "${file_path}"

    # Get the target path from the running service
    target_path=$(sudo docker service inspect --format '{{ range .Spec.TaskTemplate.ContainerSpec.Configs }}{{ if eq .ConfigName "'${config_base_name}'" }}{{ .File.Name }}{{ end }}{{ end }}' "${service_name}")

    if [ -z "$target_path" ]; then
        echo "   ! Warning: Could not find config '${config_base_name}' on service '${service_name}'. Skipping update."
        sudo docker config rm "${new_config_name}" # Clean up the new config
        continue
    fi

    # Perform the rolling update by adding the new config
    echo " > Applying new config to ${service_name}..."
    sudo docker service update --config-add source="${new_config_name}",target="${target_path}" "${service_name}"

    # Wait for the service to update before removing the old config
    echo " > Waiting for service to update..."
    sleep 10 

    # Detach the old config from the service
    echo " > Detaching old config from ${service_name}..."
    sudo docker service update --config-rm "${config_base_name}" "${service_name}"
    
    # Finally, delete the old, now-unused config
    echo " > Deleting old config: ${config_base_name}"
    sudo docker config rm "${config_base_name}"

    # Rename the new config to the original name for the next run
    echo " > Renaming new config to original name for consistency."
    sudo docker config create "${config_base_name}" "${file_path}"
    sudo docker config rm "${new_config_name}"
    
    echo "   Done."
    echo ""
done

echo -e "${GREEN}===================================================${NC}"
echo -e "${GREEN}✅ Prep complete! Images and configs are updated.${NC}"
echo -e "${GREEN}===================================================${NC}"