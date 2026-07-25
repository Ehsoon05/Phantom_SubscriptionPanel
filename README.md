# Phantom Subscription Panel

Standalone subscription gateway for Phantom.

The Phantom bot syncs subscription links into this panel through
`POST /internal/configs`. The panel stores those links in its own database, fetches
the original Marzban subscription URL, and serves:

- raw subscription content for VPN clients
- a branded browser page for normal web visits
- a full `/admin` page for visual settings, manual URL conversion, and link management
- optional per-subscription server-address rewrites for isolated relay tests

The public DNS record should be proxied through Cloudflare. For VPN subscription
paths, configure a Cache Rule matching `/token/*` and `/sub/*` with cache bypass,
and do not enable a JavaScript challenge on those paths.

## Environment

```dotenv
PANEL_DB_URL=sqlite+aiosqlite:////opt/phantom-subscription-panel/panel.db
PUBLIC_BASE_URL=https://api.phantomhubs.shop
PANEL_SYNC_TOKEN=یک-توکن-خیلی-قوی-و-تصادفی
PANEL_ADMIN_USERNAME=admin
PANEL_ADMIN_PASSWORD=change-this-password
PANEL_SETTINGS_FILE=/opt/phantom-subscription-panel/panel-settings.json
UPSTREAM_VERIFY_TLS=false
REQUEST_TIMEOUT_SECONDS=20
SUBSCRIPTION_CACHE_DIR=/opt/phantom-subscription-panel/subscription-cache
SUBSCRIPTION_CACHE_TTL_SECONDS=60
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
chmod +x scripts/install.sh
./scripts/install.sh
```

Manual deploy:

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

SVN country Reality endpoints are served through HAProxy with dynamic DNS
resolution. `phantom-svn-relay-sync.timer` refreshes the country host/port map
from a live SVN subscription every five minutes and preserves the last-good
configuration when the upstream is temporarily unavailable. Country hostnames
use the two-letter prefix under the DNS-only `*.api.phantomhubs.shop` record.

Direct Fastly WebSocket addresses from SVN are rewritten to the DNS-only
`ws.api.phantomhubs.shop` CNAME. This keeps Fastly traffic off the relay server
while allowing Fastly IP changes to propagate through DNS.

Other domain-based SVN endpoints use explicit DNS-only CNAME aliases configured
through `SVN_DIRECT_HOST_REWRITES`. The default aliases cover `tun.temas-bor.ir`
and both `white-mt.jorzel.ir` variants without routing their traffic through the
subscription server.

Admin page:

```text
https://api.phantomhubs.shop/admin
```
