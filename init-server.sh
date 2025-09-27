#!/bin/bash

# ==============================================================================
# VPS Initialization Script for Docker Swarm & Local Registry
# ==============================================================================
# This script automates the setup of a new VM (Oracle Linux / CentOS / RHEL)
# to run applications as a single-node Docker Swarm cluster.
#
# It performs the following steps:
# 1. Updates system packages.
# 2. Installs Docker Engine.
# 3. Starts and enables the Docker service.
# 4. Initializes Docker Swarm mode.
# 5. Deploys a persistent, local Docker registry as a Swarm service.
# 6. Configures the firewall (firewalld) for Swarm, the registry, and apps.
# 7. Adds the current user to the 'docker' group.
#
# USAGE:
# 1. Save this script to a file on your new VM (e.g., init_swarm_vm.sh).
# 2. Make it executable: chmod +x init_swarm_vm.sh
# 3. Run the script: ./init_swarm_vm.sh
# 4. IMPORTANT: Log out and log back in for the user group change to take effect.
# ==============================================================================

# --- Step 1: Update System Packages ---
echo "Updating system packages..."
sudo dnf update -y

# --- Step 2: Install Docker Engine ---
echo "Installing Docker Engine and dependencies..."
sudo dnf install -y dnf-utils zip unzip
sudo dnf config-manager --add-repo=https://download.docker.com/linux/centos/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io

# --- Step 3: Start and Enable Docker Service ---
echo "Starting and enabling Docker service..."
sudo systemctl start docker
sudo systemctl enable docker

# --- Step 4: Initialize Docker Swarm Mode ---
echo "Initializing single-node Docker Swarm cluster..."
sudo docker swarm init

# --- Step 5: Deploy Local Docker Registry as a Service ---
# We run the registry as a Swarm service so it's resilient and managed by Swarm.
echo "Deploying local Docker registry on port 5000..."
sudo docker service create --name registry --publish published=5000,target=5000 registry:2
sudo docker service update --env-add REGISTRY_STORAGE_DELETE_ENABLED=true registry

# --- Step 6: Configure Firewall (firewalld) ---
echo "Configuring firewall rules..."
# Allow standard web and SSH traffic
sudo firewall-cmd --permanent --add-service=ssh
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https

# Allow the local registry port
sudo firewall-cmd --permanent --add-port=5000/tcp

# Allow ports for your LiveKit application
sudo firewall-cmd --permanent --add-port=7881/tcp
sudo firewall-cmd --permanent --add-port=3478/udp
sudo firewall-cmd --permanent --add-port=20000-21000/udp

# Allow ports required for Docker Swarm inter-node communication
# (Good practice even for a single-node cluster)
sudo firewall-cmd --permanent --add-port=2377/tcp  # Cluster management
sudo firewall-cmd --permanent --add-port=7946/tcp  # Node communication
sudo firewall-cmd --permanent --add-port=7946/udp  # Node communication
sudo firewall-cmd --permanent --add-port=4789/udp  # Overlay network traffic

# Reload the firewall to apply all the new rules
echo "Reloading firewall..."
sudo firewall-cmd --reload

# --- Step 7: Add Current User to Docker Group ---
echo "Adding current user to the 'docker' group..."
sudo usermod -aG docker ${USER}

# --- Final Instructions ---
echo ""
echo "======================================================================"
echo "✅ Swarm Environment Ready!"
echo ""
echo "IMPORTANT: You must log out and log back in for the user group"
echo "changes to take effect. This will allow you to run 'docker' commands"
echo "without using 'sudo'."
echo ""
echo "------------------- NEXT STEPS FOR DEPLOYMENT --------------------"
echo "Your server is now a Swarm cluster with its own private registry."
echo "To deploy your app:"
echo ""
echo "1. Build your custom image:"
echo "   docker build -t my-app:latest ."
echo ""
echo "2. Tag the image for your local registry:"
echo "   docker tag my-app:latest localhost:5000/my-app:latest"
echo ""
echo "3. Push the image to your local registry:"
echo "   docker push localhost:5000/my-app:latest"
echo ""
echo "4. In your 'docker-stack.yml', use the local image name:"
echo "   image: localhost:5000/my-app:latest"
echo ""
echo "5. Deploy your stack:"
echo "   docker stack deploy -c docker-stack.yml myapp"
echo "======================================================================"