#!/usr/bin/env bash
# install_service.sh — Install carousel-generator and ngrok as systemd services.
#
# Run once:  sudo bash install_service.sh
#
# After this, the web UI starts automatically on boot.
# Open your phone browser to the ngrok URL printed at the end.

set -euo pipefail

# ---------------------------------------------------------------------------
# Config — edit these if needed
# ---------------------------------------------------------------------------
SERVICE_USER="${SUDO_USER:-$(whoami)}"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(which python3)"
VENV_DIR="$PROJECT_DIR/.venv"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()  { echo "  ✔  $*"; }
warn()  { echo "  ⚠  $*"; }
die()   { echo "  ✖  $*" >&2; exit 1; }
hr()    { echo; echo "────────────────────────────────────────"; echo; }

[[ $EUID -ne 0 ]] && die "Re-run with sudo:  sudo bash install_service.sh"

hr
echo "  Carousel Generator — Service Installer"
echo "  Project: $PROJECT_DIR"
echo "  User:    $SERVICE_USER"
hr

# ---------------------------------------------------------------------------
# 1. Python venv + dependencies
# ---------------------------------------------------------------------------
echo "[1/5] Setting up Python virtual environment…"

if [[ ! -d "$VENV_DIR" ]]; then
  sudo -u "$SERVICE_USER" "$PYTHON" -m venv "$VENV_DIR"
  info "Created venv at $VENV_DIR"
fi

sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install -q --upgrade pip
sudo -u "$SERVICE_USER" "$VENV_DIR/bin/pip" install -q -r "$PROJECT_DIR/requirements.txt"
info "Python dependencies installed"

# Install Playwright browsers
sudo -u "$SERVICE_USER" "$VENV_DIR/bin/playwright" install chromium --with-deps 2>/dev/null || \
  sudo -u "$SERVICE_USER" "$VENV_DIR/bin/playwright" install chromium
info "Playwright chromium installed"

# ---------------------------------------------------------------------------
# 2. .env guard
# ---------------------------------------------------------------------------
echo
echo "[2/5] Checking .env…"

ENV_FILE="$PROJECT_DIR/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
  warn ".env created from example — edit it before starting the service:"
  warn "  nano $ENV_FILE"
fi

# ---------------------------------------------------------------------------
# 3. Install ngrok (if not already present)
# ---------------------------------------------------------------------------
echo
echo "[3/5] Checking ngrok…"

if ! command -v ngrok &>/dev/null; then
  ARCH=$(uname -m)
  case "$ARCH" in
    x86_64)  NGROK_PKG="ngrok-v3-stable-linux-amd64.tgz" ;;
    aarch64) NGROK_PKG="ngrok-v3-stable-linux-arm64.tgz" ;;
    armv7l)  NGROK_PKG="ngrok-v3-stable-linux-arm.tgz" ;;
    *)       die "Unknown arch $ARCH — install ngrok manually from https://ngrok.com/download" ;;
  esac

  TMP=$(mktemp -d)
  curl -sSL "https://bin.equinox.io/c/bNyj1mQVY4c/$NGROK_PKG" -o "$TMP/ngrok.tgz"
  tar -xzf "$TMP/ngrok.tgz" -C "$TMP"
  mv "$TMP/ngrok" /usr/local/bin/ngrok
  chmod +x /usr/local/bin/ngrok
  rm -rf "$TMP"
  info "ngrok installed to /usr/local/bin/ngrok"
else
  info "ngrok already installed: $(ngrok version 2>/dev/null | head -1)"
fi

# Prompt for ngrok auth token
NGROK_TOKEN=""
if [[ -f "$ENV_FILE" ]]; then
  NGROK_TOKEN=$(grep -E "^NGROK_AUTHTOKEN=" "$ENV_FILE" | cut -d= -f2- | tr -d '"' | tr -d "'" || true)
fi

if [[ -z "$NGROK_TOKEN" ]]; then
  echo
  echo "  ngrok auth token required (free at https://dashboard.ngrok.com/get-started/your-authtoken)"
  read -rp "  Paste your ngrok authtoken: " NGROK_TOKEN
  echo "NGROK_AUTHTOKEN=$NGROK_TOKEN" >> "$ENV_FILE"
fi

sudo -u "$SERVICE_USER" ngrok config add-authtoken "$NGROK_TOKEN" 2>/dev/null || true

# Optional static domain
NGROK_DOMAIN=""
if [[ -f "$ENV_FILE" ]]; then
  NGROK_DOMAIN=$(grep -E "^NGROK_DOMAIN=" "$ENV_FILE" | cut -d= -f2- | tr -d '"' | tr -d "'" || true)
fi

if [[ -z "$NGROK_DOMAIN" ]]; then
  echo
  echo "  (Optional) Free static ngrok domain — get one at https://dashboard.ngrok.com/domains"
  read -rp "  Paste static domain or press Enter to skip: " NGROK_DOMAIN
  if [[ -n "$NGROK_DOMAIN" ]]; then
    echo "NGROK_DOMAIN=$NGROK_DOMAIN" >> "$ENV_FILE"
  fi
fi

# Build ngrok tunnel flags
if [[ -n "$NGROK_DOMAIN" ]]; then
  NGROK_FLAGS="http --domain=$NGROK_DOMAIN 5000"
else
  NGROK_FLAGS="http 5000"
fi

# ---------------------------------------------------------------------------
# 4. Write systemd unit files
# ---------------------------------------------------------------------------
echo
echo "[4/5] Installing systemd services…"

# --- carousel-generator.service ---
cat > /etc/systemd/system/carousel-generator.service <<EOF
[Unit]
Description=Instagram Carousel Generator Web UI
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$VENV_DIR/bin/python $PROJECT_DIR/server.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
info "Written: /etc/systemd/system/carousel-generator.service"

# --- carousel-ngrok.service ---
cat > /etc/systemd/system/carousel-ngrok.service <<EOF
[Unit]
Description=ngrok tunnel for Carousel Generator
After=network.target carousel-generator.service
Requires=carousel-generator.service

[Service]
Type=simple
User=$SERVICE_USER
EnvironmentFile=$ENV_FILE
ExecStart=/usr/local/bin/ngrok $NGROK_FLAGS
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
info "Written: /etc/systemd/system/carousel-ngrok.service"

# ---------------------------------------------------------------------------
# 5. Enable + start services
# ---------------------------------------------------------------------------
echo
echo "[5/5] Enabling and starting services…"

systemctl daemon-reload
systemctl enable carousel-generator.service carousel-ngrok.service
systemctl restart carousel-generator.service
sleep 2
systemctl restart carousel-ngrok.service
sleep 3

info "carousel-generator: $(systemctl is-active carousel-generator.service)"
info "carousel-ngrok:     $(systemctl is-active carousel-ngrok.service)"

# ---------------------------------------------------------------------------
# Done — print URL
# ---------------------------------------------------------------------------
hr
echo "  Installation complete!"
echo
echo "  The web UI starts automatically on every boot."
echo

if [[ -n "$NGROK_DOMAIN" ]]; then
  echo "  Open on your phone:"
  echo "    https://$NGROK_DOMAIN"
else
  # Grab the dynamic URL from the ngrok API
  sleep 2
  NGROK_URL=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null \
    | python3 -c "import sys,json; t=json.load(sys.stdin)['tunnels']; print(t[0]['public_url'])" 2>/dev/null || true)

  if [[ -n "$NGROK_URL" ]]; then
    echo "  Open on your phone:"
    echo "    $NGROK_URL"
    echo
    warn "Dynamic URL — changes on restart. Get a free static domain at:"
    warn "  https://dashboard.ngrok.com/domains"
  else
    echo "  Check your ngrok URL:"
    echo "    curl http://localhost:4040/api/tunnels"
  fi
fi

echo
echo "  Manage services:"
echo "    sudo systemctl status  carousel-generator"
echo "    sudo systemctl restart carousel-generator"
echo "    sudo systemctl stop    carousel-generator carousel-ngrok"
hr
