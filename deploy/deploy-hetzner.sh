#!/usr/bin/env bash
# ============================================================
# HyperClaw — Hetzner Cloud Deployment Script
# Usage: ./deploy-hetzner.sh <SERVER_IP> <SSH_KEY_PATH> [ENV_FILE]
#
# Example:
#   ./deploy-hetzner.sh 65.21.100.200 ~/.ssh/id_ed25519 ../.env
#
# Prerequisites:
#   - Hetzner server (Ubuntu 22.04) already provisioned
#   - Docker + docker-compose installed on server (script installs if missing)
#   - SSH access to root or a sudo user
# ============================================================

set -euo pipefail

# ── Args ──────────────────────────────────────────────────────────────────────
SERVER_IP="${1:?Usage: $0 <SERVER_IP> <SSH_KEY_PATH> [ENV_FILE]}"
SSH_KEY="${2:?Usage: $0 <SERVER_IP> <SSH_KEY_PATH> [ENV_FILE]}"
ENV_FILE="${3:-../.env}"
SSH_USER="${SSH_USER:-root}"
REMOTE_DIR="/opt/hyperclaw"
APP_PORT="${APP_PORT:-8000}"

echo "
╔══════════════════════════════════════════════════════╗
║  HyperClaw → Hetzner Deployment                      ║
║  Target: ${SSH_USER}@${SERVER_IP}:${REMOTE_DIR}
╚══════════════════════════════════════════════════════╝
"

SSH_CMD="ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no ${SSH_USER}@${SERVER_IP}"
SCP_CMD="scp -i ${SSH_KEY} -o StrictHostKeyChecking=no"

# ── Step 1: Install Docker on server if missing ───────────────────────────────
echo "→ [1/6] Checking Docker on server..."
$SSH_CMD "which docker >/dev/null 2>&1 || (
  apt-get update -qq &&
  apt-get install -y -qq docker.io docker-compose-plugin &&
  systemctl enable --now docker &&
  echo 'Docker installed'
) && docker --version"

# ── Step 2: Create remote directory ──────────────────────────────────────────
echo "→ [2/6] Creating remote directory ${REMOTE_DIR}..."
$SSH_CMD "mkdir -p ${REMOTE_DIR}"

# ── Step 3: Rsync codebase ────────────────────────────────────────────────────
echo "→ [3/6] Syncing codebase..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

rsync -az --exclude='.venv' --exclude='__pycache__' \
  --exclude='*.pyc' --exclude='.git' --exclude='node_modules' \
  --exclude='*.egg-info' --exclude='.pytest_cache' \
  -e "ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no" \
  "${PROJECT_DIR}/" "${SSH_USER}@${SERVER_IP}:${REMOTE_DIR}/"

# ── Step 4: Copy .env file ────────────────────────────────────────────────────
echo "→ [4/6] Uploading .env..."
if [[ -f "${ENV_FILE}" ]]; then
  $SCP_CMD "${ENV_FILE}" "${SSH_USER}@${SERVER_IP}:${REMOTE_DIR}/.env"
  echo "  .env uploaded"
else
  echo "  ⚠ No .env found at ${ENV_FILE} — you must create ${REMOTE_DIR}/.env manually"
fi

# ── Step 5: Build & start stack ──────────────────────────────────────────────
echo "→ [5/6] Building and starting HyperClaw stack..."
$SSH_CMD "cd ${REMOTE_DIR} && docker compose up -d --build 2>&1"

# ── Step 6: Health check ──────────────────────────────────────────────────────
echo "→ [6/6] Health check..."
sleep 5
HEALTH=$($SSH_CMD "curl -sf http://localhost:${APP_PORT}/health" 2>/dev/null || echo "FAILED")

if echo "${HEALTH}" | grep -q '"status": *"ok"'; then
  echo "
✅ HyperClaw is LIVE on Hetzner!
   Dashboard:  http://${SERVER_IP}:${APP_PORT}
   Health:     http://${SERVER_IP}:${APP_PORT}/health
   API Docs:   http://${SERVER_IP}:${APP_PORT}/docs

   Next: point hyperclaw.ai DNS A record → ${SERVER_IP}
  "
else
  echo "
⚠ Health check inconclusive. Check logs:
   ${SSH_CMD} 'cd ${REMOTE_DIR} && docker compose logs --tail=50'
  "
fi
