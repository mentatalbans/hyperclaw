#!/usr/bin/env bash
# ============================================================
# HyperClaw — AWS EC2 Deployment Script
# Usage: ./deploy-aws.sh <SERVER_IP> <SSH_KEY_PATH> [ENV_FILE]
#
# Example:
#   ./deploy-aws.sh 54.210.100.50 ~/.ssh/hyperclaw-aws.pem ../.env
#
# Prerequisites:
#   - EC2 instance running (Ubuntu 22.04, t3.small+ recommended)
#   - Security group: inbound TCP 22, 80, 443, 8000 open
#   - SSH key pair (.pem) already associated with the instance
#   - Default user: ubuntu (or set SSH_USER=ec2-user for Amazon Linux)
# ============================================================

set -euo pipefail

# ── Args ──────────────────────────────────────────────────────────────────────
SERVER_IP="${1:?Usage: $0 <SERVER_IP> <SSH_KEY_PATH> [ENV_FILE]}"
SSH_KEY="${2:?Usage: $0 <SERVER_IP> <SSH_KEY_PATH> [ENV_FILE]}"
ENV_FILE="${3:-../.env}"
SSH_USER="${SSH_USER:-ubuntu}"
REMOTE_DIR="/home/${SSH_USER}/hyperclaw"
APP_PORT="${APP_PORT:-8000}"

echo "
╔══════════════════════════════════════════════════════╗
║  HyperClaw → AWS EC2 Deployment                      ║
║  Target: ${SSH_USER}@${SERVER_IP}:${REMOTE_DIR}
╚══════════════════════════════════════════════════════╝
"

SSH_CMD="ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no ${SSH_USER}@${SERVER_IP}"
SCP_CMD="scp -i ${SSH_KEY} -o StrictHostKeyChecking=no"

# ── Step 1: Install Docker on EC2 ────────────────────────────────────────────
echo "→ [1/7] Installing Docker on EC2..."
$SSH_CMD "which docker >/dev/null 2>&1 && echo 'Docker already installed' || (
  sudo apt-get update -qq &&
  sudo apt-get install -y -qq ca-certificates curl gnupg &&
  sudo install -m 0755 -d /etc/apt/keyrings &&
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg &&
  sudo chmod a+r /etc/apt/keyrings/docker.gpg &&
  echo \"deb [arch=\$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \$(. /etc/os-release && echo \"\$VERSION_CODENAME\") stable\" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null &&
  sudo apt-get update -qq &&
  sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin &&
  sudo usermod -aG docker ${SSH_USER} &&
  sudo systemctl enable --now docker &&
  echo 'Docker installed successfully'
)"

# ── Step 2: (Optional) Install Nginx reverse proxy ────────────────────────────
echo "→ [2/7] Setting up Nginx (optional — comment out if not needed)..."
$SSH_CMD "which nginx >/dev/null 2>&1 || sudo apt-get install -y -qq nginx" || true

# ── Step 3: Create remote directory ──────────────────────────────────────────
echo "→ [3/7] Creating remote directory..."
$SSH_CMD "mkdir -p ${REMOTE_DIR}"

# ── Step 4: Rsync codebase ────────────────────────────────────────────────────
echo "→ [4/7] Syncing codebase to EC2..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

rsync -az --exclude='.venv' --exclude='__pycache__' \
  --exclude='*.pyc' --exclude='.git' --exclude='node_modules' \
  --exclude='*.egg-info' --exclude='.pytest_cache' \
  -e "ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no" \
  "${PROJECT_DIR}/" "${SSH_USER}@${SERVER_IP}:${REMOTE_DIR}/"

# ── Step 5: Upload .env ───────────────────────────────────────────────────────
echo "→ [5/7] Uploading environment config..."
if [[ -f "${ENV_FILE}" ]]; then
  $SCP_CMD "${ENV_FILE}" "${SSH_USER}@${SERVER_IP}:${REMOTE_DIR}/.env"
  $SSH_CMD "chmod 600 ${REMOTE_DIR}/.env"
  echo "  .env uploaded and secured (chmod 600)"
else
  echo "  ⚠ No .env at ${ENV_FILE} — create ${REMOTE_DIR}/.env manually before starting"
fi

# ── Step 6: Build and launch with docker compose ─────────────────────────────
echo "→ [6/7] Launching HyperClaw stack..."
$SSH_CMD "cd ${REMOTE_DIR} && sudo docker compose pull redis 2>/dev/null; sudo docker compose up -d --build 2>&1"

# ── Step 7: Health check + Nginx config ──────────────────────────────────────
echo "→ [7/7] Final health check..."
sleep 8

HEALTH=$($SSH_CMD "curl -sf http://localhost:${APP_PORT}/health" 2>/dev/null || echo "FAILED")

if echo "${HEALTH}" | grep -q '"status"'; then
  # Optional: write Nginx config to proxy port 80 → 8000
  $SSH_CMD "sudo tee /etc/nginx/sites-available/hyperclaw > /dev/null << 'NGINX'
server {
    listen 80;
    server_name hyperclaw.ai www.hyperclaw.ai _;

    location / {
        proxy_pass http://127.0.0.1:${APP_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
    }
}
NGINX
sudo ln -sf /etc/nginx/sites-available/hyperclaw /etc/nginx/sites-enabled/ 2>/dev/null
sudo nginx -t && sudo systemctl reload nginx || true" 2>/dev/null || true

  echo "
✅ HyperClaw is LIVE on AWS!
   Dashboard:  http://${SERVER_IP}
   Direct:     http://${SERVER_IP}:${APP_PORT}
   Health:     http://${SERVER_IP}:${APP_PORT}/health
   API Docs:   http://${SERVER_IP}:${APP_PORT}/docs

   SSL (recommended): sudo certbot --nginx -d hyperclaw.ai -d www.hyperclaw.ai
   DNS: Point hyperclaw.ai A record → ${SERVER_IP}

   To watch logs: ssh -i ${SSH_KEY} ${SSH_USER}@${SERVER_IP}
                  cd ${REMOTE_DIR} && docker compose logs -f
  "
else
  echo "
⚠ Stack may still be starting. Check:
   ${SSH_CMD} 'cd ${REMOTE_DIR} && docker compose logs --tail=50'
  "
fi
