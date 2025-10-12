#!/bin/bash
set -e

# Go to your project folder
cd ~/ansible_project

echo ">>> Creating Python virtual environment (.venv)"
python3 -m venv .venv

echo ">>> Activating virtual environment"
source .venv/bin/activate

echo ">>> Upgrading pip"
pip install --upgrade pip

echo ">>> Installing Ansible + OpenStack SDK"
pip install --upgrade ansible openstacksdk

echo ">>> Installing Ansible collections (OpenStack + Docker)"
ansible-galaxy collection install -f openstack.cloud community.docker

echo ">>> Writing requirements.txt"
cat > requirements.txt <<EOF
ansible
openstacksdk
EOF

echo ">>> Writing requirements.yml (Ansible collections)"
cat > requirements.yml <<EOF
---
collections:
  - name: openstack.cloud
  - name: community.docker
EOF

echo ">>> Done! Activate your environment with:"
echo "    source ~/ansible_project/.venv/bin/activate"
