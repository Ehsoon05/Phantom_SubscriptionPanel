#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/phantom-subscription-panel}"
REPO_URL="${REPO_URL:-https://github.com/Ehsoon05/Phantom_SubscriptionPanel.git}"
DOMAIN="${DOMAIN:-api.phantomhubs.shop}"
PORT="${PORT:-8090}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root."
  exit 1
fi

if [ ! -d "$APP_DIR/.git" ]; then
  git clone "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" pull origin main
fi

python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
  SYNC_TOKEN="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
  ADMIN_PASSWORD="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(18))
PY
)"
  cat > "$APP_DIR/.env" <<EOF
PANEL_DB_URL=sqlite+aiosqlite:///$APP_DIR/panel.db
PUBLIC_BASE_URL=https://$DOMAIN
PANEL_SYNC_TOKEN=$SYNC_TOKEN
PANEL_ADMIN_USERNAME=admin
PANEL_ADMIN_PASSWORD=$ADMIN_PASSWORD
PANEL_SETTINGS_FILE=$APP_DIR/panel-settings.json
UPSTREAM_VERIFY_TLS=false
REQUEST_TIMEOUT_SECONDS=20
EOF
  echo "Created $APP_DIR/.env"
  echo "Panel admin username: admin"
  echo "Panel admin password: $ADMIN_PASSWORD"
  echo "Panel sync token: $SYNC_TOKEN"
fi

cp "$APP_DIR/deploy/systemd/phantom-subscription-panel.service" /etc/systemd/system/
sed -i "s#--port 8090#--port $PORT#g" /etc/systemd/system/phantom-subscription-panel.service
systemctl daemon-reload
systemctl enable --now phantom-subscription-panel.service

if command -v nginx >/dev/null 2>&1; then
  mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled
  cp "$APP_DIR/deploy/nginx/phantom-subscription-panel.conf" /etc/nginx/sites-available/phantom-subscription-panel.conf
  sed -i "s/api.phantomhubs.shop/$DOMAIN/g" /etc/nginx/sites-available/phantom-subscription-panel.conf
  sed -i "s/127.0.0.1:8090/127.0.0.1:$PORT/g" /etc/nginx/sites-available/phantom-subscription-panel.conf
  ln -sf /etc/nginx/sites-available/phantom-subscription-panel.conf /etc/nginx/sites-enabled/phantom-subscription-panel.conf
  nginx -t
  systemctl reload nginx
fi

systemctl status phantom-subscription-panel.service --no-pager
