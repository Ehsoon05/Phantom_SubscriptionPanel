from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    panel_db_url = os.getenv("PANEL_DB_URL", "sqlite+aiosqlite:////opt/phantom-subscription-panel/panel.db").strip()
    public_base_url = os.getenv("PUBLIC_BASE_URL", "https://api.phantomhubs.shop").strip().rstrip("/")
    sync_token = os.getenv("PANEL_SYNC_TOKEN", "").strip()
    upstream_verify_tls = _bool_env("UPSTREAM_VERIFY_TLS", False)
    request_timeout_seconds = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))
    admin_username = os.getenv("PANEL_ADMIN_USERNAME", "admin").strip()
    admin_password = os.getenv("PANEL_ADMIN_PASSWORD", "").strip()
    settings_file = Path(os.getenv("PANEL_SETTINGS_FILE", "panel-settings.json")).expanduser()


settings = Settings()
