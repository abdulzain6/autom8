#!/bin/bash

# ==============================================================================
# LiveKit Server Initialization Script for Oracle Linux / CentOS / RHEL
# ==============================================================================
# This script automates the setup of a new VM to run a LiveKit server using
# Docker and Docker Compose. It performs the following steps:
#
# 1. Updates the system packages.
# 2. Installs Docker Engine and Docker Compose.
# 3. Starts and enables the Docker service.
# 4. Configures the system firewall (firewalld) with the necessary rules
#    for LiveKit's web, media, and TURN traffic.
# 5. Adds the current user to the 'docker' group to allow running Docker
#    commands without 'sudo'.
#
# USAGE:
# 1. Save this script to a file on your new VM (e.g., init_vm.sh).
# 2. Make it executable: chmod +x init_vm.sh
# 3. Run the script: ./init_vm.sh
# 4. IMPORTANT: Log out and log back in for the user group change to take effect.
# ==============================================================================

# --- Step 1: Update System Packages ---
echo "Updating system packages..."
sudo dnf update -y

# --- Step 2: Install Docker Engine & Dependencies ---
echo "Installing Docker Engine and dependencies..."
sudo dnf install -y dnf-utils zip unzip
sudo dnf config-manager --add-repo=https://download.docker.com/linux/centos/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io

# --- Step 3: Install Docker Compose ---
echo "Installing Docker Compose..."
DOCKER_COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep -Po '"tag_name": "\K.*\d')
sudo curl -L "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# --- Step 4: Start and Enable Docker Service ---
echo "Starting and enabling Docker service..."
sudo systemctl start docker
sudo systemctl enable docker

# --- Step 5: Configure Firewall (firewalld) ---
echo "Configuring firewall for LiveKit..."
# Allow standard web and SSH traffic
sudo firewall-cmd --permanent --add-service=ssh
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https

# Allow the LiveKit media and TURN ports
sudo firewall-cmd --permanent --add-port=7881/tcp
sudo firewall-cmd --permanent --add-port=3478/udp
sudo firewall-cmd --permanent --add-port=50000-60000/udp

# Reload the firewall to apply all the new rules
echo "Reloading firewall..."
sudo firewall-cmd --reload

# --- Step 6: Add Current User to Docker Group ---
echo "Adding current user to the 'docker' group..."
sudo usermod -aG docker ${USER}

# --- Final Instructions ---
echo ""
echo "========================================================"
echo "✅ VM Initialization Complete!"
echo ""
echo "IMPORTANT: You must log out and log back in for the"
echo "user group changes to take effect. This will allow you"
echo "to run 'docker' commands without using 'sudo'."
echo "========================================================"

