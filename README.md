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
use the two-letter prefix under the DNS-only `*.api.bahrevari01.shop` record.

For a dedicated relay host, install
`scripts/install_haproxy_from_stdin.py` as a forced SSH command for a restricted
deployment key. Then copy
`deploy/systemd/phantom-svn-relay-sync-remote.conf.example` to a systemd drop-in,
replace `RELAY_IP`, and restart the timer. Every successful sync will validate
and deploy the current HAProxy configuration to the relay without giving the
sync service an unrestricted remote shell.

Direct Fastly WebSocket addresses from SVN are rewritten to the DNS-only
`wsr.api.bahrevari01.shop` relay. This keeps the public subscription domain
separate from transport traffic while allowing Fastly IP changes to propagate
through DNS.

Other domain-based SVN endpoints are first rewritten through
`SVN_DIRECT_HOST_REWRITES`, then selected blocked transports are routed through
the tested HAProxy fallback listeners configured by
`SVN_FALLBACK_ENDPOINT_REWRITES`.

Admin page:

```text
https://api.phantomhubs.shop/admin
```
