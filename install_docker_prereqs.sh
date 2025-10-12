#!/usr/bin/env bash
set -euo pipefail
sudo mkdir -p /etc/apt/keyrings
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg lsb-release
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o "$tmpdir/docker.gpg"
gpg --dearmor "$tmpdir/docker.gpg"
sudo install -m 0644 "$tmpdir/docker.gpg.gpg" /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
curl -fsSL https://pkgs.k8s.io/core:/stable:/v1.29/deb/Release.key -o "$tmpdir/k8s.key"
gpg --dearmor "$tmpdir/k8s.key"
sudo install -m 0644 "$tmpdir/k8s.key.gpg" /etc/apt/keyrings/kubernetes-apt-keyring.gpg
echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v1.29/deb/ /" | sudo tee /etc/apt/sources.list.d/kubernetes.list > /dev/null
sudo apt-get update
