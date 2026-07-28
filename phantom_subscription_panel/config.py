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
    subscription_cache_dir = Path(
        os.getenv(
            "SUBSCRIPTION_CACHE_DIR",
            str(settings_file.parent / "subscription-cache"),
        )
    ).expanduser()
    subscription_cache_ttl_seconds = int(os.getenv("SUBSCRIPTION_CACHE_TTL_SECONDS", "60"))
    device_limit_warning_bot_token = os.getenv("DEVICE_LIMIT_WARNING_BOT_TOKEN", "").strip()
    device_limit_warning_cooldown_seconds = int(os.getenv("DEVICE_LIMIT_WARNING_COOLDOWN_SECONDS", "21600"))
    svn_country_source_suffix = os.getenv("SVN_COUNTRY_SOURCE_SUFFIX", "sv.temas-bor.ir").strip().lower().strip(".")
    svn_upstream_host = os.getenv(
        "SVN_UPSTREAM_HOST",
        "sub.svnteam-max.com",
    ).strip().lower().strip(".")
    svn_relay_target_suffix = os.getenv(
        "SVN_RELAY_TARGET_SUFFIX",
        "api.bahrevari01.shop",
    ).strip().lower().strip(".")
    svn_automatic_address_rewrites_enabled = _bool_env(
        "SVN_AUTOMATIC_ADDRESS_REWRITES_ENABLED",
        False,
    )
    svn_direct_host_rewrites = os.getenv(
        "SVN_DIRECT_HOST_REWRITES",
        "\n".join(
            (
                "tun.temas-bor.ir=tun.api.bahrevari01.shop",
                "white-mt.jorzel.ir=white-mt.api.bahrevari01.shop",
                "white-mtp.jorzel.ir=white-mtp.api.bahrevari01.shop",
                "mmi.jorzel.ir=mmi.api.bahrevari01.shop",
                "mmip.jorzel.ir=mmip.api.bahrevari01.shop",
                "koper.jorzel.ir=koper.api.bahrevari01.shop",
            )
        ),
    ).strip()
    svn_fallback_endpoint_rewrites = os.getenv(
        "SVN_FALLBACK_ENDPOINT_REWRITES",
        ",".join(
            (
                "mmi.api.bahrevari01.shop:19302=koper.api.bahrevari01.shop:19302",
                "mmip.api.bahrevari01.shop:19302=koper.api.bahrevari01.shop:19302",
            )
        ),
    ).strip()
    svn_ws_origin_host = os.getenv(
        "SVN_WS_ORIGIN_HOST",
        "bankmelat.global.ssl.fastly.net",
    ).strip().lower().strip(".")
    svn_ws_alias = os.getenv(
        "SVN_WS_ALIAS",
        "wsr.api.bahrevari01.shop",
    ).strip().lower().strip(".")
    svn_ws_alias_port = int(os.getenv("SVN_WS_ALIAS_PORT", "443"))


settings = Settings()
