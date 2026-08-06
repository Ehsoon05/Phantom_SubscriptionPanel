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
PANEL_EXTRA_SYNC_TOKENS=توکن-داخلی-اضافی-اختیاری,توکن-داخلی-دیگر
PANEL_ADMIN_USERNAME=admin
PANEL_ADMIN_PASSWORD=change-this-password
PANEL_SETTINGS_FILE=/opt/phantom-subscription-panel/panel-settings.json
UPSTREAM_VERIFY_TLS=false
REQUEST_TIMEOUT_SECONDS=20
DEVICE_LAST_SEEN_WRITE_INTERVAL_SECONDS=900
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

SVN relay rewriting is disabled by default for fresh manual installations.
Production deployment enables it only after the DNS-only relay records have
passed connectivity checks. Rewriting changes the server address only: the
provider port, Reality `serverName`/SNI, WebSocket `Host`, keys, and transport
parameters remain unchanged. Country hostnames use the two-letter prefix under
the DNS-only `*.api.bahrevari01.shop` record.

For a dedicated relay host, install
`scripts/install_haproxy_from_stdin.py` as a forced SSH command for a restricted
deployment key. Then copy
`deploy/systemd/phantom-svn-relay-sync-remote.conf.example` to a systemd drop-in,
replace `RELAY_IP`, and restart the timer. Every successful sync will validate
and deploy the current HAProxy configuration to the relay without giving the
sync service an unrestricted remote shell.

When relay rewriting is enabled, direct Fastly WebSocket addresses from SVN are
rewritten to the DNS-only `wsr.api.bahrevari01.shop` relay.

When relay rewriting is enabled, other domain-based SVN endpoints are rewritten
through `SVN_DIRECT_HOST_REWRITES`. Port-changing fallback listeners are
disabled by default; `SVN_FALLBACK_ENDPOINT_REWRITES` is reserved for
individually tested emergency routes. The two unhealthy SVN Trojan/xHTTP
endpoints on `mmi` and `mmip` port `19302` use the working `koper` route while
preserving their credentials, Reality parameters, and port.

Admin page:

```text
https://api.phantomhubs.shop/admin
```
