#!/bin/bash
# Quick update script — pull latest code, rebuild, restart
# Run as root on the VPS: bash /opt/sports-picks/deploy/update.sh

set -euo pipefail

APP_DIR="/opt/sports-picks"
APP_USER="sportspicks"

echo "=== Updating Sports Picks ==="

cd "$APP_DIR"

echo "[1/4] Pulling latest code..."
sudo -u "$APP_USER" git pull

echo "[2/4] Updating Python deps..."
sudo -u "$APP_USER" .venv/bin/pip install -q -e ".[dev]"

echo "[3/4] Rebuilding frontend..."
cd "$APP_DIR/frontend"
sudo -u "$APP_USER" npm install --silent
sudo -u "$APP_USER" npm run build

echo "[4/4] Restarting services..."
systemctl restart sports-picks-web sports-picks-pipeline

echo "=== Update complete ==="
systemctl status sports-picks-web --no-pager -l | head -5
