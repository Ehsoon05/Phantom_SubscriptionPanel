# Phantom Subscription Panel

Standalone subscription gateway for Phantom.

It reads Phantom bot `configs` rows by `public_sub_token`, fetches the original
Marzban subscription URL, and serves:

- raw subscription content for VPN clients
- a branded browser page for normal web visits
- a small `/admin` page for visual settings

## Environment

```dotenv
PHANTOM_DB_URL=sqlite+aiosqlite:////opt/phantom/vpn_shop.db
PUBLIC_BASE_URL=https://api.phantomhubs.shop
PANEL_ADMIN_USERNAME=admin
PANEL_ADMIN_PASSWORD=change-this-password
PANEL_SETTINGS_FILE=/opt/phantom-subscription-panel/panel-settings.json
UPSTREAM_VERIFY_TLS=false
REQUEST_TIMEOUT_SECONDS=20
```

## Run

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn phantom_subscription_panel.app:app --host 127.0.0.1 --port 8090
```

## Deploy

```bash
cp deploy/systemd/phantom-subscription-panel.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now phantom-subscription-panel.service

cp deploy/nginx/phantom-subscription-panel.conf /etc/nginx/sites-available/
ln -sf /etc/nginx/sites-available/phantom-subscription-panel.conf /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
certbot --nginx -d api.phantomhubs.shop
```

Admin page:

```text
https://api.phantomhubs.shop/admin
```
