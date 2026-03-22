#!/bin/bash
# Sports Picks VPS Setup Script
# Run on a fresh Ubuntu 22.04/24.04 VPS as root
# Usage: curl -sSL <raw-github-url>/deploy/setup.sh | bash

set -euo pipefail

APP_USER="sportspicks"
APP_DIR="/opt/sports-picks"
REPO_URL="https://github.com/mwilliams2733/sports_picks.git"

echo "=== Sports Picks VPS Setup ==="

# 1. System packages
echo "[1/7] Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv nodejs npm git nginx certbot python3-certbot-nginx ufw

# 2. Create app user
echo "[2/7] Creating app user..."
if ! id "$APP_USER" &>/dev/null; then
    useradd -m -s /bin/bash "$APP_USER"
fi

# 3. Clone repo
echo "[3/7] Cloning repository..."
if [ -d "$APP_DIR" ]; then
    cd "$APP_DIR" && git pull
else
    git clone "$REPO_URL" "$APP_DIR"
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# 4. Python setup
echo "[4/7] Setting up Python environment..."
cd "$APP_DIR"
sudo -u "$APP_USER" python3 -m venv .venv
sudo -u "$APP_USER" .venv/bin/pip install -q --upgrade pip
sudo -u "$APP_USER" .venv/bin/pip install -q -e ".[dev]"

# 5. Frontend build
echo "[5/7] Building frontend..."
cd "$APP_DIR/frontend"
sudo -u "$APP_USER" npm install --silent
sudo -u "$APP_USER" npm run build

# 6. Create .env if missing
echo "[6/7] Checking .env..."
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo ">>> IMPORTANT: Edit $APP_DIR/.env and add your ODDS_API_KEY"
fi

# 7. Install systemd services
echo "[7/7] Installing systemd services..."
cp "$APP_DIR/deploy/sports-picks-web.service" /etc/systemd/system/
cp "$APP_DIR/deploy/sports-picks-pipeline.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable sports-picks-web sports-picks-pipeline
systemctl start sports-picks-web sports-picks-pipeline

echo ""
echo "=== Setup Complete ==="
echo "Web server:  systemctl status sports-picks-web"
echo "Pipeline:    systemctl status sports-picks-pipeline"
echo "Logs:        journalctl -u sports-picks-web -f"
echo ""
echo "Next steps:"
echo "  1. Edit /opt/sports-picks/.env with your ODDS_API_KEY"
echo "  2. Set up nginx: cp /opt/sports-picks/deploy/nginx.conf /etc/nginx/sites-available/sports-picks"
echo "  3. ln -s /etc/nginx/sites-available/sports-picks /etc/nginx/sites-enabled/"
echo "  4. rm /etc/nginx/sites-enabled/default"
echo "  5. nginx -t && systemctl reload nginx"
echo "  6. Set up SSL: certbot --nginx -d yourdomain.com"
echo "  7. Open firewall: ufw allow 'Nginx Full' && ufw enable"
