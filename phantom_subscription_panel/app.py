from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import html
import ipaddress
import json
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx
import qrcode
import qrcode.constants
import qrcode.exceptions
import qrcode.image.svg
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .config import settings
from .database import Base, Config, SubscriptionDevice, async_session, engine
from .panel_settings import PanelSettings, load_panel_settings, save_panel_settings


app = FastAPI(title="Phantom Subscription Panel")
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent / "static"),
    name="static",
)
security = HTTPBasic()
CONFIG_SCHEMES = ("vmess://", "vless://", "trojan://", "ss://", "ssr://", "hysteria://", "hysteria2://", "tuic://", "wireguard://")
FORWARDED_HEADERS = (
    "subscription-userinfo",
    "profile-update-interval",
    "profile-title",
    "content-disposition",
    "etag",
    "last-modified",
)
GENERIC_SUBSCRIPTION_TITLES = {"subscription", "sub", "subscription information"}
UPSTREAM_BRAND_SUFFIXES = ("Upgo",)
QUICK_CONNECT_KEYS = ("v2rayng", "hiddify", "streisand", "singbox", "v2box", "happ")
_cache_refresh_tasks: set[str] = set()
_memory_cache: dict[str, dict] = {}
_fetch_locks: dict[str, asyncio.Lock] = {}
_upstream_client: httpx.AsyncClient | None = None


class ConfigSyncPayload(BaseModel):
    token: str
    upstream_url: str
    volume_gb: int
    category_key: str = "default"
    is_sold: bool = False
    service_name: str | None = None
    panel_username: str | None = None
    profile_title: str | None = None
    telegram_user_id: int | None = None
    usage_offset_bytes: int | None = None
    display_total_bytes: int | None = None
    device_limit: int | None = None
    show_config_preview: bool | None = None
    info_proxies_enabled: bool | None = None
    show_header: bool | None = None
    channel_handle: str | None = None
    address_rewrites: str | None = None


class PanelSettingsSyncPayload(BaseModel):
    subscription_profile_title: str = ""
    subscription_device_limit: int | None = None
    quick_connect_order: str | None = None


class TokenRevokePayload(BaseModel):
    new_token: str | None = None


class QRPayload(BaseModel):
    data: str


@app.on_event("startup")
async def startup() -> None:
    global _upstream_client
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        try:
            await conn.execute(text("ALTER TABLE subscription_configs ADD COLUMN profile_title VARCHAR"))
        except SQLAlchemyError:
            pass
        try:
            await conn.execute(text("ALTER TABLE subscription_configs ADD COLUMN device_limit INTEGER"))
        except SQLAlchemyError:
            pass
        try:
            await conn.execute(text("ALTER TABLE subscription_configs ADD COLUMN show_header BOOLEAN"))
        except SQLAlchemyError:
            pass
        try:
            await conn.execute(text("ALTER TABLE subscription_configs ADD COLUMN channel_handle VARCHAR"))
        except SQLAlchemyError:
            pass
        try:
            await conn.execute(text("ALTER TABLE subscription_configs ADD COLUMN show_config_preview BOOLEAN"))
        except SQLAlchemyError:
            pass
        try:
            await conn.execute(text("ALTER TABLE subscription_configs ADD COLUMN info_proxies_enabled BOOLEAN"))
        except SQLAlchemyError:
            pass
        try:
            await conn.execute(text("ALTER TABLE subscription_configs ADD COLUMN panel_username VARCHAR"))
        except SQLAlchemyError:
            pass
        try:
            await conn.execute(text("ALTER TABLE subscription_configs ADD COLUMN telegram_user_id INTEGER"))
        except SQLAlchemyError:
            pass
        try:
            await conn.execute(text("ALTER TABLE subscription_configs ADD COLUMN usage_offset_bytes BIGINT DEFAULT 0 NOT NULL"))
        except SQLAlchemyError:
            pass
        try:
            await conn.execute(text("ALTER TABLE subscription_configs ADD COLUMN display_total_bytes BIGINT"))
        except SQLAlchemyError:
            pass
        try:
            await conn.execute(text("ALTER TABLE subscription_configs ADD COLUMN device_limit_warning_count INTEGER DEFAULT 0 NOT NULL"))
        except SQLAlchemyError:
            pass
        try:
            await conn.execute(text("ALTER TABLE subscription_configs ADD COLUMN device_limit_last_warning_at DATETIME"))
        except SQLAlchemyError:
            pass
        try:
            await conn.execute(text("ALTER TABLE subscription_configs ADD COLUMN address_rewrites_json VARCHAR"))
        except SQLAlchemyError:
            pass
        try:
            await conn.execute(
                text("ALTER TABLE subscription_devices ADD COLUMN fingerprint_aliases_json VARCHAR")
            )
        except SQLAlchemyError:
            pass
    await _collapse_legacy_device_rows()
    settings.subscription_cache_dir.mkdir(parents=True, exist_ok=True)
    _upstream_client = httpx.AsyncClient(
        follow_redirects=True,
        timeout=settings.request_timeout_seconds,
        verify=settings.upstream_verify_tls,
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=30),
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    global _upstream_client
    if _upstream_client is not None:
        await _upstream_client.aclose()
        _upstream_client = None


@app.get("/")
async def index() -> RedirectResponse:
    return RedirectResponse("/admin")


@app.get("/health")
async def health() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.get("/sub/{token}")
async def sub_alias(token: str, request: Request) -> Response:
    return await subscription(token, request)


@app.get("/token/{token}")
async def subscription(token: str, request: Request) -> Response:
    config = await _config_for_token(token)
    if not config:
        raise HTTPException(status_code=404, detail="Subscription not found")

    upstream = _apply_usage_carryover(config, await _fetch_upstream(config.sub_link))
    if _wants_html(request):
        web_title = await _fetch_upstream_web_title(config.sub_link)
        return HTMLResponse(_render_subscription_page(config, upstream, web_title=web_title))

    await _enforce_device_limit(config, request)

    response_headers = {"Cache-Control": "no-store, no-cache, must-revalidate", "X-Content-Type-Options": "nosniff"}
    response_headers.update(upstream["forward_headers"])
    response_headers.update(_subscription_title_headers(_app_title_for_subscription(config, upstream)))
    body = _subscription_body_with_info_proxies(config, upstream)
    return Response(
        content=body,
        media_type=upstream["content_type"] or "text/plain; charset=utf-8",
        headers=response_headers,
    )


def _require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if not settings.admin_password:
        raise HTTPException(status_code=403, detail="PANEL_ADMIN_PASSWORD is not configured")
    username_ok = secrets.compare_digest(credentials.username, settings.admin_username)
    password_ok = secrets.compare_digest(credentials.password, settings.admin_password)
    if not (username_ok and password_ok):
        raise HTTPException(status_code=401, detail="Invalid admin credentials", headers={"WWW-Authenticate": "Basic"})
    return credentials.username


@app.get("/admin", response_class=HTMLResponse)
async def admin_form(_: str = Depends(_require_admin)) -> str:
    return await _render_admin(load_panel_settings())


@app.post("/admin/settings", response_class=HTMLResponse)
async def admin_save_settings(
    brand_name: str = Form(...),
    subscription_profile_title: str = Form(default=""),
    subscription_device_limit: str = Form(default="0"),
    primary_color: str = Form(...),
    accent_color: str = Form(...),
    background_color: str = Form(...),
    card_color: str = Form(...),
    text_color: str = Form(...),
    muted_text_color: str = Form(...),
    secondary_button_color: str = Form(...),
    channel_handle: str = Form(...),
    hero_text: str = Form(...),
    support_text: str = Form(...),
    active_status_text: str = Form(...),
    purchased_volume_label: str = Form(...),
    used_label: str = Form(...),
    remaining_label: str = Form(...),
    expiry_label: str = Form(...),
    config_count_label: str = Form(...),
    subscription_title: str = Form(...),
    copy_button_text: str = Form(...),
    copy_success_text: str = Form(...),
    qr_button_text: str = Form(...),
    apps_title: str = Form(...),
    apps_help_text: str = Form(...),
    quick_connect_order: str = Form(default="v2rayng,hiddify,streisand,singbox,v2box,happ"),
    v2rayng_button_text: str = Form(...),
    hiddify_button_text: str = Form(...),
    streisand_button_text: str = Form(...),
    singbox_button_text: str = Form(default="Sing-box"),
    v2box_button_text: str = Form(default="V2Box"),
    happ_button_text: str = Form(...),
    channel_button_text: str = Form(...),
    copy_button_color: str = Form(...),
    qr_button_color: str = Form(...),
    v2rayng_button_color: str = Form(...),
    hiddify_button_color: str = Form(...),
    streisand_button_color: str = Form(...),
    singbox_button_color: str = Form(default="#334155"),
    v2box_button_color: str = Form(default="#334155"),
    happ_button_color: str = Form(...),
    channel_button_color: str = Form(...),
    configs_title: str = Form(...),
    config_copy_button_text: str = Form(...),
    config_qr_button_text: str = Form(...),
    config_copy_button_color: str = Form(...),
    config_qr_button_color: str = Form(...),
    empty_configs_text: str = Form(...),
    show_quick_connect: str | None = Form(default=None),
    show_channel_button: str | None = Form(default=None),
    show_config_preview: str | None = Form(default=None),
    show_config_copy: str | None = Form(default=None),
    show_config_qr: str | None = Form(default=None),
    _: str = Depends(_require_admin),
) -> str:
    panel = PanelSettings(
        brand_name=brand_name.strip() or "Phantom Hubs",
        subscription_profile_title=subscription_profile_title.strip(),
        subscription_device_limit=_positive_int(subscription_device_limit),
        primary_color=_normalize_color(primary_color, "#426df8"),
        accent_color=_normalize_color(accent_color, "#22c55e"),
        background_color=_normalize_color(background_color, "#0f172a"),
        card_color=_normalize_color(card_color, "#1e293b"),
        text_color=_normalize_color(text_color, "#ffffff"),
        muted_text_color=_normalize_color(muted_text_color, "#cbd5e1"),
        secondary_button_color=_normalize_color(secondary_button_color, "#334155"),
        channel_handle=channel_handle.strip() or "@PhantomHubs",
        hero_text=hero_text.strip(),
        support_text=support_text.strip(),
        active_status_text=active_status_text.strip() or "فعال",
        purchased_volume_label=purchased_volume_label.strip() or "حجم خریداری‌شده",
        used_label=used_label.strip() or "حجم مصرف‌شده",
        remaining_label=remaining_label.strip() or "حجم باقی‌مانده",
        expiry_label=expiry_label.strip() or "تاریخ انقضا",
        config_count_label=config_count_label.strip() or "تعداد کانفیگ",
        subscription_title=subscription_title.strip() or "لینک اشتراک",
        copy_button_text=copy_button_text.strip() or "کپی لینک اشتراک",
        copy_success_text=copy_success_text.strip() or "با موفقیت کپی شد",
        qr_button_text=qr_button_text.strip() or "QR",
        apps_title=apps_title.strip() or "اتصال سریع",
        apps_help_text=apps_help_text.strip() or "بر روی اسم برنامه‌ای که نصب دارید بزنید تا به صورت خودکار داخل برنامه اضافه شود.",
        quick_connect_order=_normalize_quick_connect_order(quick_connect_order),
        v2rayng_button_text=v2rayng_button_text.strip() or "V2RayNG",
        hiddify_button_text=hiddify_button_text.strip() or "Hiddify",
        streisand_button_text=streisand_button_text.strip() or "Streisand",
        singbox_button_text=singbox_button_text.strip() or "Sing-box",
        v2box_button_text=v2box_button_text.strip() or "V2Box",
        happ_button_text=happ_button_text.strip() or "HAPP",
        channel_button_text=channel_button_text.strip() or "کانال پشتیبانی",
        copy_button_color=_normalize_color(copy_button_color, "#426df8"),
        qr_button_color=_normalize_color(qr_button_color, "#334155"),
        v2rayng_button_color=_normalize_color(v2rayng_button_color, "#334155"),
        hiddify_button_color=_normalize_color(hiddify_button_color, "#334155"),
        streisand_button_color=_normalize_color(streisand_button_color, "#334155"),
        singbox_button_color=_normalize_color(singbox_button_color, "#334155"),
        v2box_button_color=_normalize_color(v2box_button_color, "#334155"),
        happ_button_color=_normalize_color(happ_button_color, "#334155"),
        channel_button_color=_normalize_color(channel_button_color, "#426df8"),
        configs_title=configs_title.strip() or "کانفیگ‌های اشتراک",
        config_copy_button_text=config_copy_button_text.strip() or "کپی",
        config_qr_button_text=config_qr_button_text.strip() or "QR",
        config_copy_button_color=_normalize_color(config_copy_button_color, "#426df8"),
        config_qr_button_color=_normalize_color(config_qr_button_color, "#334155"),
        empty_configs_text=empty_configs_text.strip() or "کانفیگ قابل نمایش دریافت نشد.",
        show_quick_connect=show_quick_connect == "on",
        show_channel_button=show_channel_button == "on",
        show_config_preview=show_config_preview == "on",
        show_config_copy=show_config_copy == "on",
        show_config_qr=show_config_qr == "on",
    )
    save_panel_settings(panel)
    return await _render_admin(panel, notice="تنظیمات ظاهری ذخیره شد.")


@app.post("/admin/subscriptions", response_class=HTMLResponse)
async def admin_create_subscription(
    upstream_url: str = Form(...),
    token: str = Form(default=""),
    service_name: str = Form(default=""),
    panel_username: str = Form(default=""),
    profile_title: str = Form(default=""),
    device_limit: str = Form(default="0"),
    show_header: str | None = Form(default=None),
    channel_handle: str = Form(default=""),
    show_config_preview: str | None = Form(default=None),
    info_proxies_enabled: str | None = Form(default=None),
    address_rewrites: str = Form(default=""),
    volume_gb: int = Form(default=0),
    category_key: str = Form(default="manual"),
    _: str = Depends(_require_admin),
) -> str:
    upstream_url = upstream_url.strip()
    parsed = urlparse(upstream_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return await _render_admin(load_panel_settings(), error="لینک اصلی معتبر نیست.")
    token = _clean_token(token) or _token_from_url(upstream_url) or secrets.token_urlsafe(18)
    async with async_session() as session:
        result = await session.execute(select(Config).where(Config.public_sub_token == token))
        config = result.scalar_one_or_none()
        if config is None:
            config = Config(public_sub_token=token, sub_link=upstream_url, volume_gb=max(volume_gb, 0))
            session.add(config)
        config.sub_link = upstream_url
        config.volume_gb = max(volume_gb, 0)
        config.category_key = category_key.strip() or "manual"
        config.service_name = service_name.strip() or None
        config.panel_username = panel_username.strip() or service_name.strip() or None
        config.profile_title = profile_title.strip() or None
        config.device_limit = _positive_int(device_limit)
        config.show_header = show_header == "on"
        config.channel_handle = channel_handle.strip() or None
        config.show_config_preview = show_config_preview == "on"
        config.info_proxies_enabled = info_proxies_enabled == "on"
        config.address_rewrites_json = _serialize_address_rewrites(address_rewrites)
        await session.commit()
    public_url = f"{settings.public_base_url}/token/{quote(token, safe='')}"
    return await _render_admin(load_panel_settings(), notice=f"لینک اختصاصی ساخته شد: {public_url}")


@app.post("/admin/subscriptions/{config_id}/delete")
async def admin_delete_subscription(config_id: int, _: str = Depends(_require_admin)) -> RedirectResponse:
    async with async_session() as session:
        config = await session.get(Config, config_id)
        if config:
            await _reset_devices_for_token(session, config.public_sub_token)
            await session.delete(config)
            await session.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/subscriptions/{config_id}/device-limit")
async def admin_update_subscription_device_limit(
    config_id: int,
    device_limit: str = Form(default="0"),
    _: str = Depends(_require_admin),
) -> RedirectResponse:
    async with async_session() as session:
        config = await session.get(Config, config_id)
        if config:
            config.device_limit = _positive_int(device_limit)
            await session.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/subscriptions/{config_id}/upstream", response_class=HTMLResponse)
async def admin_update_subscription_upstream(
    config_id: int,
    upstream_url: str = Form(...),
    _: str = Depends(_require_admin),
) -> str:
    upstream_url = upstream_url.strip()
    parsed = urlparse(upstream_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return await _render_admin(load_panel_settings(), error="لینک اصلی جدید معتبر نیست.")

    async with async_session() as session:
        config = await session.get(Config, config_id)
        if not config:
            return await _render_admin(load_panel_settings(), error="لینک موردنظر پیدا نشد.")
        old_url = config.sub_link
        if old_url == upstream_url:
            return await _render_admin(load_panel_settings(), notice="لینک اصلی تغییری نکرد.")

        existing = (
            await session.execute(
                select(Config.id).where(Config.sub_link == upstream_url, Config.id != config.id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return await _render_admin(load_panel_settings(), error="این لینک اصلی قبلاً برای یک لینک دیگر ثبت شده است.")

        config.sub_link = upstream_url
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return await _render_admin(load_panel_settings(), error="این لینک اصلی قبلاً ثبت شده است.")

    _clear_upstream_cache(old_url)
    _clear_upstream_cache(upstream_url)
    _schedule_cache_refresh(upstream_url)
    return await _render_admin(load_panel_settings(), notice="لینک اصلی با موفقیت جایگزین شد.")


@app.post("/admin/subscriptions/{config_id}/devices/reset")
async def admin_reset_subscription_devices(config_id: int, _: str = Depends(_require_admin)) -> RedirectResponse:
    async with async_session() as session:
        config = await session.get(Config, config_id)
        if config:
            await _reset_devices_for_token(session, config.public_sub_token)
            config.device_limit_warning_count = 0
            config.device_limit_last_warning_at = None
            await session.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/subscriptions/{config_id}/revoke")
async def admin_revoke_subscription(config_id: int, _: str = Depends(_require_admin)) -> RedirectResponse:
    async with async_session() as session:
        config = await session.get(Config, config_id)
        if config:
            old_token = config.public_sub_token
            config.public_sub_token = await _unique_token(session)
            await _reset_devices_for_token(session, old_token)
            config.device_limit_warning_count = 0
            config.device_limit_last_warning_at = None
            await session.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/subscriptions/{config_id}/display")
async def admin_update_subscription_display(
    config_id: int,
    show_header: str | None = Form(default=None),
    profile_title: str = Form(default=""),
    panel_username: str = Form(default=""),
    channel_handle: str = Form(default=""),
    show_config_preview: str | None = Form(default=None),
    info_proxies_enabled: str | None = Form(default=None),
    address_rewrites: str = Form(default=""),
    _: str = Depends(_require_admin),
) -> RedirectResponse:
    async with async_session() as session:
        config = await session.get(Config, config_id)
        if config:
            config.profile_title = profile_title.strip() or None
            config.panel_username = panel_username.strip() or None
            config.show_header = show_header == "on"
            config.channel_handle = channel_handle.strip() or None
            config.show_config_preview = show_config_preview == "on"
            config.info_proxies_enabled = info_proxies_enabled == "on"
            config.address_rewrites_json = _serialize_address_rewrites(address_rewrites)
            await session.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/internal/configs", response_class=PlainTextResponse)
async def sync_config(payload: ConfigSyncPayload, authorization: str | None = Header(default=None)) -> str:
    _require_sync_token(authorization, allow_integration=True)
    async with async_session() as session:
        result = await session.execute(select(Config).where(Config.public_sub_token == payload.token))
        config = result.scalar_one_or_none()
        if config is None:
            config = Config(public_sub_token=payload.token, sub_link=payload.upstream_url, volume_gb=payload.volume_gb)
            session.add(config)
        config.sub_link = payload.upstream_url
        config.volume_gb = payload.volume_gb
        config.category_key = payload.category_key
        config.is_sold = payload.is_sold
        config.service_name = payload.service_name
        if payload.profile_title is not None:
            config.profile_title = payload.profile_title.strip() or None
        if payload.panel_username is not None:
            config.panel_username = payload.panel_username.strip() or None
        if payload.telegram_user_id is not None:
            config.telegram_user_id = int(payload.telegram_user_id)
        if payload.usage_offset_bytes is not None:
            config.usage_offset_bytes = max(0, int(payload.usage_offset_bytes))
        if payload.display_total_bytes is not None:
            total = max(0, int(payload.display_total_bytes))
            config.display_total_bytes = total or None
        if payload.device_limit is not None:
            config.device_limit = max(0, int(payload.device_limit))
        if payload.show_config_preview is not None:
            config.show_config_preview = bool(payload.show_config_preview)
        if payload.info_proxies_enabled is not None:
            config.info_proxies_enabled = bool(payload.info_proxies_enabled)
        if payload.show_header is not None:
            config.show_header = bool(payload.show_header)
        if payload.channel_handle is not None:
            config.channel_handle = payload.channel_handle.strip() or None
        if payload.address_rewrites is not None:
            config.address_rewrites_json = _serialize_address_rewrites(
                payload.address_rewrites
            )
        await session.commit()
    _schedule_cache_refresh(payload.upstream_url)
    return "ok"


@app.post("/internal/configs/{token}/devices/reset", response_class=PlainTextResponse)
async def reset_config_devices(token: str, authorization: str | None = Header(default=None)) -> str:
    _require_sync_token(authorization)
    async with async_session() as session:
        config = await _config_for_token_in_session(session, token)
        if not config:
            raise HTTPException(status_code=404, detail="Subscription not found")
        await _reset_devices_for_token(session, config.public_sub_token)
        config.device_limit_warning_count = 0
        config.device_limit_last_warning_at = None
        await session.commit()
    return "ok"


@app.delete("/internal/configs/{token}", response_class=PlainTextResponse)
async def delete_synced_config(
    token: str,
    authorization: str | None = Header(default=None),
) -> str:
    _require_sync_token(authorization)
    upstream_url = ""
    async with async_session() as session:
        config = await _config_for_token_in_session(session, token)
        if not config:
            raise HTTPException(status_code=404, detail="Subscription not found")
        upstream_url = config.sub_link
        await _reset_devices_for_token(session, config.public_sub_token)
        await session.delete(config)
        await session.commit()
    if upstream_url:
        _clear_upstream_cache(upstream_url)
    return "ok"


@app.post("/internal/configs/{token}/revoke", response_class=JSONResponse)
async def revoke_config_token(
    token: str,
    payload: TokenRevokePayload,
    authorization: str | None = Header(default=None),
) -> dict:
    _require_sync_token(authorization)
    async with async_session() as session:
        config = await _config_for_token_in_session(session, token)
        if not config:
            raise HTTPException(status_code=404, detail="Subscription not found")
        new_token = _clean_token(payload.new_token or "") or await _unique_token(session)
        duplicate = await _config_for_token_in_session(session, new_token)
        if duplicate is not None and duplicate.id != config.id:
            raise HTTPException(status_code=409, detail="New token already exists")
        old_token = config.public_sub_token
        config.public_sub_token = new_token
        await _reset_devices_for_token(session, old_token)
        await _reset_devices_for_token(session, new_token)
        config.device_limit_warning_count = 0
        config.device_limit_last_warning_at = None
        await session.commit()
    return {
        "token": new_token,
        "public_url": f"{settings.public_base_url}/token/{quote(new_token, safe='')}",
    }


@app.post("/internal/settings", response_class=PlainTextResponse)
async def sync_panel_settings(payload: PanelSettingsSyncPayload, authorization: str | None = Header(default=None)) -> str:
    _require_sync_token(authorization)
    panel = load_panel_settings()
    panel.subscription_profile_title = payload.subscription_profile_title.strip()
    if payload.subscription_device_limit is not None:
        panel.subscription_device_limit = max(0, int(payload.subscription_device_limit or 0))
    if payload.quick_connect_order is not None:
        panel.quick_connect_order = _normalize_quick_connect_order(payload.quick_connect_order)
    save_panel_settings(panel)
    return "ok"


@app.get("/internal/configs/{token}/metadata", response_class=JSONResponse)
async def config_metadata(token: str, authorization: str | None = Header(default=None)) -> dict:
    _require_sync_token(authorization, allow_integration=True)
    config = await _config_for_token(token)
    if not config:
        raise HTTPException(status_code=404, detail="Subscription not found")
    upstream = _apply_usage_carryover(config, await _fetch_upstream(config.sub_link))
    usage = upstream["usage"]
    used = usage.get("upload", 0) + usage.get("download", 0)
    total = usage.get("total", 0) or max(config.volume_gb, 0) * 1024**3
    return {
        "title": upstream["title"],
        "upload": usage.get("upload", 0),
        "download": usage.get("download", 0),
        "used": used,
        "total": total,
        "remaining": max(total - used, 0) if total else 0,
        "expire": usage.get("expire"),
        "config_count": len(upstream["lines"]),
        "status": "active",
        "public_url": f"{settings.public_base_url}/token/{quote(token, safe='')}",
    }


@app.get("/connect/happ/{token}")
async def connect_happ(token: str) -> RedirectResponse:
    config = await _config_for_token(token)
    if not config:
        raise HTTPException(status_code=404, detail="Subscription not found")
    public_url = f"{settings.public_base_url}/token/{quote(token, safe='')}"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post("https://crypto.happ.su/api-v2.php", json={"url": public_url})
            response.raise_for_status()
            encrypted_link = response.json().get("encrypted_link", "")
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="HAPP quick connect is unavailable") from exc
    if not isinstance(encrypted_link, str) or not encrypted_link.startswith("happ://"):
        raise HTTPException(status_code=502, detail="HAPP returned an invalid link")
    return RedirectResponse(encrypted_link, status_code=302)


@app.post("/qr", response_class=Response)
async def make_qr(payload: QRPayload) -> Response:
    data = payload.data.strip()
    if not data:
        raise HTTPException(status_code=400, detail="QR data is empty")
    if len(data) > 8192:
        raise HTTPException(status_code=413, detail="QR data is too large")
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=8,
        border=3,
        image_factory=qrcode.image.svg.SvgPathImage,
    )
    try:
        qr.add_data(data, optimize=20)
        qr.make(fit=True)
    except qrcode.exceptions.DataOverflowError as exc:
        raise HTTPException(status_code=413, detail="QR data exceeds QR capacity") from exc
    image = qr.make_image()
    return Response(content=image.to_string(encoding="unicode"), media_type="image/svg+xml")


def _require_sync_token(authorization: str | None, *, allow_integration: bool = False) -> None:
    accepted_tokens = [settings.sync_token]
    if allow_integration:
        accepted_tokens.append(settings.integration_sync_token)
    accepted_tokens = [token for token in accepted_tokens if token]
    if not accepted_tokens:
        raise HTTPException(status_code=403, detail="PANEL_SYNC_TOKEN is not configured")
    if not authorization or not any(
        secrets.compare_digest(authorization, f"Bearer {token}") for token in accepted_tokens
    ):
        raise HTTPException(status_code=401, detail="Invalid sync token")


async def _config_for_token(token: str) -> Config | None:
    async with async_session() as session:
        return await _config_for_token_in_session(session, token)


async def _config_for_token_in_session(session, token: str) -> Config | None:
    result = await session.execute(select(Config).where(Config.public_sub_token == token))
    return result.scalar_one_or_none()


async def _reset_devices_for_token(session, token: str) -> int:
    result = await session.execute(
        delete(SubscriptionDevice).where(SubscriptionDevice.public_sub_token == token)
    )
    return int(result.rowcount or 0)


async def _unique_token(session) -> str:
    while True:
        token = secrets.token_urlsafe(24)
        if await _config_for_token_in_session(session, token) is None:
            return token


async def _fetch_upstream(url: str) -> dict:
    cached = _read_upstream_cache(url)
    if cached:
        if time.time() - cached["cached_at"] >= settings.subscription_cache_ttl_seconds:
            _schedule_cache_refresh(url)
        return cached

    lock = _fetch_locks.setdefault(url, asyncio.Lock())
    async with lock:
        cached = _read_upstream_cache(url)
        if cached:
            return cached
        return await _fetch_and_cache_upstream(url)


async def _fetch_and_cache_upstream(url: str) -> dict:
    headers = {
        "User-Agent": "v2rayNG/1.10 PhantomSubscriptionPanel/2.0",
        "Accept": "text/plain, application/octet-stream, */*",
        "Cache-Control": "no-cache",
    }
    last_error: httpx.HTTPError | None = None
    client = _upstream_client
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=settings.request_timeout_seconds,
            verify=settings.upstream_verify_tls,
        )
    try:
        for attempt in range(2):
            try:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                break
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < 1:
                    await asyncio.sleep(0.25 * (attempt + 1))
        else:
            raise HTTPException(
                status_code=502,
                detail=f"Upstream subscription is unavailable: {last_error}",
            ) from last_error
    finally:
        if owns_client:
            await client.aclose()

    body = response.content
    if _looks_like_html(body):
        raise HTTPException(status_code=502, detail="Upstream returned an HTML page instead of subscription data")
    upstream = {
        "body": body,
        "content_type": response.headers.get("content-type", "text/plain; charset=utf-8"),
        "forward_headers": {name: response.headers[name] for name in FORWARDED_HEADERS if name in response.headers},
        "lines": _subscription_lines(body),
        "usage": _parse_subscription_userinfo(response.headers.get("subscription-userinfo", "")),
        "title": _upstream_title(response.headers),
    }
    _write_upstream_cache(url, upstream)
    return upstream


async def _fetch_upstream_web_title(url: str) -> str:
    cache_key = f"web-title:{url}"
    cached = _memory_cache.get(cache_key)
    if cached and time.time() - cached.get("cached_at", 0) < settings.subscription_cache_ttl_seconds:
        return str(cached.get("title") or "")

    headers = {
        "User-Agent": "Mozilla/5.0 PhantomSubscriptionPanel/2.1",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    }
    client = _upstream_client
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=settings.request_timeout_seconds,
            verify=settings.upstream_verify_tls,
        )
    try:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if "html" not in content_type:
            return ""
        match = re.search(r"<title[^>]*>(.*?)</title>", response.text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        title = _clean_web_title(html.unescape(re.sub(r"\s+", " ", match.group(1))).strip())
        _memory_cache[cache_key] = {"cached_at": time.time(), "title": title}
        return title
    except (httpx.HTTPError, UnicodeDecodeError):
        return ""
    finally:
        if owns_client:
            await client.aclose()


def _clean_web_title(value: str) -> str:
    title = value.strip()
    generic_suffixes = (
        "Sub Info",
        "Subscription Information",
        "Subscription",
    )
    separators = (" - ", " | ", " — ", " – ")
    for generic in generic_suffixes:
        if title.casefold() == generic.casefold():
            return ""
        for separator in separators:
            suffix = f"{separator}{generic}"
            if title.casefold().endswith(suffix.casefold()):
                return _clean_upstream_brand_suffix(title[: -len(suffix)].strip())
    for suffix in (" - Sub Info", " | Sub Info", " — Sub Info"):
        if title.endswith(suffix):
            return _clean_upstream_brand_suffix(title[: -len(suffix)].strip())
    return _clean_upstream_brand_suffix(title)


def _cache_path(url: str) -> Path:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return settings.subscription_cache_dir / f"{key}.json"


def _read_upstream_cache(url: str) -> dict | None:
    memory = _memory_cache.get(url)
    if memory is not None:
        return memory
    try:
        payload = json.loads(_cache_path(url).read_text(encoding="utf-8"))
        body = base64.b64decode(payload["body"])
        cached = {
            "body": body,
            "content_type": payload["content_type"],
            "forward_headers": payload["forward_headers"],
            "lines": _subscription_lines(body),
            "usage": payload["usage"],
            "title": payload["title"],
            "cached_at": float(payload["cached_at"]),
        }
        _memory_cache[url] = cached
        return cached
    except (OSError, KeyError, TypeError, ValueError, binascii.Error, json.JSONDecodeError):
        return None


def _write_upstream_cache(url: str, upstream: dict) -> None:
    path = _cache_path(url)
    temporary_path = path.with_suffix(".tmp")
    cached_at = time.time()
    payload = {
        "body": base64.b64encode(upstream["body"]).decode("ascii"),
        "content_type": upstream["content_type"],
        "forward_headers": upstream["forward_headers"],
        "usage": upstream["usage"],
        "title": upstream["title"],
        "cached_at": cached_at,
    }
    _memory_cache[url] = {
        **upstream,
        "lines": list(upstream["lines"]),
        "usage": dict(upstream["usage"]),
        "forward_headers": dict(upstream["forward_headers"]),
        "cached_at": cached_at,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary_path.replace(path)
    except OSError:
        temporary_path.unlink(missing_ok=True)


def _clear_upstream_cache(url: str) -> None:
    _memory_cache.pop(url, None)
    _memory_cache.pop(f"web-title:{url}", None)
    try:
        _cache_path(url).unlink(missing_ok=True)
    except OSError:
        pass


def _schedule_cache_refresh(url: str) -> None:
    if url in _cache_refresh_tasks:
        return
    _cache_refresh_tasks.add(url)

    async def refresh() -> None:
        try:
            await _fetch_and_cache_upstream(url)
        except HTTPException:
            pass
        finally:
            _cache_refresh_tasks.discard(url)

    asyncio.create_task(refresh())


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "").lower()
    user_agent = request.headers.get("user-agent", "").lower()
    vpn_clients = (
        "v2ray",
        "clash",
        "sing-box",
        "hiddify",
        "streisand",
        "shadowrocket",
        "nekobox",
        "v2box",
        "foxray",
        "happ",
    )
    return "text/html" in accept and not any(client in user_agent for client in vpn_clients)


def _normalize_color(value: str, fallback: str) -> str:
    value = value.strip()
    if len(value) == 6 and not value.startswith("#"):
        value = f"#{value}"
    if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return value.lower()
    return fallback


def _normalize_quick_connect_order(value: str) -> str:
    requested = [part.strip().lower() for part in (value or "").split(",")]
    ordered = []
    for key in requested:
        if key in QUICK_CONNECT_KEYS and key not in ordered:
            ordered.append(key)
    ordered.extend(key for key in QUICK_CONNECT_KEYS if key not in ordered)
    return ",".join(ordered)


def _quick_connect_button_html(
    panel: PanelSettings,
    key: str,
    public_url: str,
    encoded_url: str,
    encoded_title: str,
    token: str,
) -> str:
    specs = {
        "v2rayng": (
            panel.v2rayng_button_text,
            panel.v2rayng_button_color,
            f"v2rayng://install-sub?url={encoded_url}#{encoded_title}",
        ),
        "hiddify": (
            panel.hiddify_button_text,
            panel.hiddify_button_color,
            f"hiddify://import/?url={encoded_url}&name={encoded_title}",
        ),
        "streisand": (
            panel.streisand_button_text,
            panel.streisand_button_color,
            f"streisand://import/{public_url}#{encoded_title}",
        ),
        "singbox": (
            panel.singbox_button_text,
            panel.singbox_button_color,
            f"sing-box://import-remote-profile?url={encoded_url}#{encoded_title}",
        ),
        "v2box": (
            panel.v2box_button_text,
            panel.v2box_button_color,
            f"v2box://install-sub?url={encoded_url}&name={encoded_title}",
        ),
        "happ": (
            panel.happ_button_text,
            panel.happ_button_color,
            f"/connect/happ/{quote(token, safe='')}",
        ),
    }
    text_value, color, url = specs[key]
    return f"<a class='link-btn' style='background:{color}' href='{html.escape(url, quote=True)}'>{html.escape(text_value)}</a>"


def _positive_int(value: str | int | None) -> int:
    try:
        return max(0, int(str(value or "").strip() or 0))
    except (TypeError, ValueError):
        return 0


def _clean_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", value.strip())[:160]


def _token_from_url(url: str) -> str:
    return _clean_token(urlparse(url).path.rstrip("/").split("/")[-1])


def _looks_like_html(body: bytes) -> bool:
    sample = body.lstrip()[:500].lower()
    return sample.startswith(b"<!doctype html") or sample.startswith(b"<html") or b"<head" in sample


def _subscription_lines(body: bytes) -> list[str]:
    text = body.decode("utf-8", errors="replace").strip()
    decoded = text
    if text and not any(scheme in text.lower() for scheme in CONFIG_SCHEMES):
        compact = re.sub(r"\s+", "", text)
        try:
            raw = base64.b64decode(compact + "=" * (-len(compact) % 4), validate=False)
            candidate = raw.decode("utf-8").strip()
            if any(scheme in candidate.lower() for scheme in CONFIG_SCHEMES):
                decoded = candidate
        except (binascii.Error, UnicodeDecodeError, ValueError):
            pass
    if _looks_like_html(decoded.encode()):
        return []
    return [line.strip() for line in decoded.splitlines() if line.strip() and any(line.lower().startswith(s) for s in CONFIG_SCHEMES)]


def _decode_subscription_text(body: bytes) -> tuple[str, bool]:
    text = body.decode("utf-8", errors="replace").strip()
    if text and any(scheme in text.lower() for scheme in CONFIG_SCHEMES):
        return text, False
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return text, False
    try:
        raw = base64.b64decode(compact + "=" * (-len(compact) % 4), validate=False)
        candidate = raw.decode("utf-8").strip()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return text, False
    if any(scheme in candidate.lower() for scheme in CONFIG_SCHEMES):
        return candidate, True
    return text, False


def _subscription_body_with_info_proxies(config: Config, upstream: dict) -> bytes:
    address_rewrites, rewrite_svn_ws = _subscription_rewrite_context(config, upstream["body"])
    if not bool(config.info_proxies_enabled):
        return _subscription_body_without_branded_suffixes(
            upstream["body"],
            address_rewrites,
            rewrite_svn_ws=rewrite_svn_ws,
        )
    text, was_base64 = _decode_subscription_text(upstream["body"])
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines = [_clean_config_line_display_name(line) for line in lines]
    lines = [_rewrite_config_line_address(line, address_rewrites) for line in lines]
    if rewrite_svn_ws:
        lines = [_rewrite_svn_fallback_endpoint(line) for line in lines]
        lines = [_rewrite_svn_ws_address(line) for line in lines]
    info_lines = _info_proxy_lines(config, upstream)
    if not info_lines:
        return _subscription_body_without_branded_suffixes(
            upstream["body"],
            address_rewrites,
            rewrite_svn_ws=rewrite_svn_ws,
        )
    # Put status entries first so apps show them at the top of the profile.
    content = "\n".join([*info_lines, *lines]).strip() + "\n"
    if was_base64:
        return base64.b64encode(content.encode("utf-8"))
    return content.encode("utf-8")


def _subscription_rewrite_context(config: Config, body: bytes) -> tuple[dict[str, str], bool]:
    automatic_rewrites = _automatic_svn_country_rewrites(body)
    source_host = (urlparse(config.sub_link or "").hostname or "").lower().rstrip(".")
    is_svn_subscription = (
        bool(automatic_rewrites)
        or source_host == settings.svn_upstream_host
        or source_host == "sub.svnteam-max.com"
    )
    if is_svn_subscription and not settings.svn_automatic_address_rewrites_enabled:
        # Preserve the provider's working endpoints. Relay rewrites are opt-in
        # because a dead relay would otherwise break every SVN subscription.
        return {}, False
    return (
        {
            **automatic_rewrites,
            **_config_address_rewrites(config),
        },
        is_svn_subscription,
    )


def _rewritten_subscription_lines(config: Config, upstream: dict) -> list[str]:
    address_rewrites, rewrite_svn_ws = _subscription_rewrite_context(config, upstream["body"])
    lines = [
        _rewrite_config_line_address(line, address_rewrites)
        for line in upstream.get("lines", [])
    ]
    if rewrite_svn_ws:
        lines = [_rewrite_svn_fallback_endpoint(line) for line in lines]
        lines = [_rewrite_svn_ws_address(line) for line in lines]
    return lines


def _info_proxy_lines(config: Config, upstream: dict) -> list[str]:
    usage = upstream.get("usage") or {}
    used = int(usage.get("upload", 0) or 0) + int(usage.get("download", 0) or 0)
    total = int(usage.get("total", 0) or 0) or max(int(config.volume_gb or 0), 0) * 1024**3
    remaining_bytes = max(total - used, 0) if total > 0 else 0
    remaining_label = "نامحدود" if total <= 0 else _format_gb_compact(remaining_bytes)
    days_label = _remaining_days_label(usage.get("expire"))
    title = _info_title_for_subscription(config, upstream)
    return [
        _vless_info_proxy("00000000-0000-4000-8000-000000000001", f"👤 {title}"),
        _vless_info_proxy("00000000-0000-4000-8000-000000000002", f"⏳ روزهای باقی مانده {days_label}"),
        _vless_info_proxy("00000000-0000-4000-8000-000000000003", f"📊 حجم باقی مانده {remaining_label}"),
    ]


def _vless_info_proxy(uuid: str, label: str) -> str:
    return f"vless://{uuid}@info.phantomhubs.local:443?type=tcp&security=none#{quote(label, safe='')}"


def _format_gb_compact(value: int) -> str:
    gb = max(value, 0) / 1024**3
    if gb >= 100:
        formatted = f"{gb:.0f}"
    elif gb >= 10:
        formatted = f"{gb:.1f}"
    else:
        formatted = f"{gb:.2f}"
    formatted = formatted.rstrip("0").rstrip(".")
    return f"{formatted} GB"


def _remaining_days_label(expire: int | None) -> str:
    if not expire:
        return "نامحدود"
    remaining_seconds = max(int(expire) - int(time.time()), 0)
    days = remaining_seconds // 86400
    return f"{days} day"


def _clean_upstream_brand_suffix(value: str) -> str:
    cleaned = (value or "").strip()
    separators = (" · ", " - ", " | ", " — ", " – ")
    changed = True
    while changed and cleaned:
        changed = False
        for brand in UPSTREAM_BRAND_SUFFIXES:
            if cleaned.casefold() == brand.casefold():
                return ""
            for separator in separators:
                suffix = f"{separator}{brand}"
                if cleaned.casefold().endswith(suffix.casefold()):
                    cleaned = cleaned[: -len(suffix)].strip()
                    changed = True
                    break
            if changed:
                break
    return cleaned


def _clean_config_line_display_name(line: str) -> str:
    if "#" not in line:
        return line
    base, fragment = line.rsplit("#", 1)
    title = unquote(fragment).strip()
    cleaned = _clean_upstream_brand_suffix(title)
    if cleaned == title:
        return line
    return f"{base}#{quote(cleaned, safe='')}"


def _subscription_body_without_branded_suffixes(
    body: bytes,
    address_rewrites: dict[str, str] | None = None,
    *,
    rewrite_svn_ws: bool = False,
) -> bytes:
    text, was_base64 = _decode_subscription_text(body)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return body
    cleaned_lines = [_clean_config_line_display_name(line) for line in lines]
    if address_rewrites:
        cleaned_lines = [_rewrite_config_line_address(line, address_rewrites) for line in cleaned_lines]
    if rewrite_svn_ws:
        cleaned_lines = [_rewrite_svn_fallback_endpoint(line) for line in cleaned_lines]
        cleaned_lines = [_rewrite_svn_ws_address(line) for line in cleaned_lines]
    if cleaned_lines == lines:
        return body
    content = "\n".join(cleaned_lines).strip() + "\n"
    if was_base64:
        return base64.b64encode(content.encode("utf-8"))
    return content.encode("utf-8")


def _serialize_address_rewrites(value: str) -> str | None:
    rules: dict[str, str] = {}
    for raw_line in (value or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        source, target = (part.strip().lower().rstrip(".") for part in line.split("=", 1))
        if _valid_rewrite_host(source) and _valid_rewrite_host(target) and source != target:
            rules[source] = target
    if not rules:
        return None
    return json.dumps(rules, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _config_address_rewrites(config: Config) -> dict[str, str]:
    raw = getattr(config, "address_rewrites_json", None)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(source).strip().lower().rstrip("."): str(target).strip().lower().rstrip(".")
        for source, target in data.items()
        if _valid_rewrite_host(str(source)) and _valid_rewrite_host(str(target))
    }


def _display_address_rewrites(config: Config) -> str:
    return "\n".join(f"{source}={target}" for source, target in _config_address_rewrites(config).items())


def _valid_rewrite_host(value: str) -> bool:
    host = (value or "").strip().lower().rstrip(".")
    return bool(host) and len(host) <= 253 and bool(re.fullmatch(r"[a-z0-9.-]+", host))


def _rewrite_config_line_address(line: str, rules: dict[str, str]) -> str:
    if not rules:
        return line
    match = re.match(
        r"^(?P<prefix>[a-z][a-z0-9+.-]*://[^@\s]+@)(?P<host>\[[^\]]+\]|[^:/?#\s]+)(?P<suffix>:\d+.*)$",
        line,
        flags=re.IGNORECASE,
    )
    if not match:
        return line
    source = match.group("host").strip("[]").lower().rstrip(".")
    target = rules.get(source)
    if not target:
        return line
    return f"{match.group('prefix')}{target}{match.group('suffix')}"


def _automatic_svn_country_rewrites(body: bytes) -> dict[str, str]:
    source_suffix = settings.svn_country_source_suffix
    target_suffix = settings.svn_relay_target_suffix
    text, _ = _decode_subscription_text(body)
    rules: dict[str, str] = {}
    if source_suffix and target_suffix:
        pattern = re.compile(
            rf"@(?P<code>[a-z]{{2}})\.{re.escape(source_suffix)}:\d+",
            flags=re.IGNORECASE,
        )
        codes = {match.group("code").lower() for match in pattern.finditer(text)}
        rules.update(
            {
                f"{code}.{source_suffix}": f"{code}.{target_suffix}"
                for code in sorted(codes)
            }
        )
    for raw_rule in re.split(r"[\n,]+", settings.svn_direct_host_rewrites):
        if "=" not in raw_rule:
            continue
        source, target = (part.strip().lower().rstrip(".") for part in raw_rule.split("=", 1))
        if not _valid_rewrite_host(source) or not _valid_rewrite_host(target):
            continue
        if re.search(rf"@{re.escape(source)}:\d+", text, flags=re.IGNORECASE):
            rules[source] = target
    return rules


def _rewrite_svn_fallback_endpoint(line: str) -> str:
    match = re.match(
        r"^(?P<prefix>[a-z][a-z0-9+.-]*://[^@\s]+@)"
        r"(?P<host>\[[^\]]+\]|[^:/?#\s]+):(?P<port>\d+)(?P<suffix>.*)$",
        line,
        flags=re.IGNORECASE,
    )
    if not match:
        return line
    source = (match.group("host").strip("[]").lower().rstrip("."), int(match.group("port")))
    rules: dict[tuple[str, int], tuple[str, int]] = {}
    for raw_rule in re.split(r"[\n,]+", settings.svn_fallback_endpoint_rewrites):
        if "=" not in raw_rule:
            continue
        raw_source, raw_target = (part.strip().lower().rstrip(".") for part in raw_rule.split("=", 1))
        try:
            source_host, source_port = raw_source.rsplit(":", 1)
            target_host, target_port = raw_target.rsplit(":", 1)
            parsed_source_port = int(source_port)
            parsed_target_port = int(target_port)
        except (TypeError, ValueError):
            continue
        if not _valid_rewrite_host(source_host) or not _valid_rewrite_host(target_host):
            continue
        if not (1 <= parsed_source_port <= 65535 and 1 <= parsed_target_port <= 65535):
            continue
        rules[(source_host, parsed_source_port)] = (target_host, parsed_target_port)
    target = rules.get(source)
    if not target:
        return line
    return f"{match.group('prefix')}{target[0]}:{target[1]}{match.group('suffix')}"


def _rewrite_svn_ws_address(line: str) -> str:
    alias = settings.svn_ws_alias
    alias_port = settings.svn_ws_alias_port
    origin_host = settings.svn_ws_origin_host
    if not alias or not origin_host or not (1 <= alias_port <= 65535):
        return line
    match = re.match(
        r"^(?P<prefix>[a-z][a-z0-9+.-]*://[^@\s]+@)"
        r"(?P<host>\[[^\]]+\]|[^:/?#\s]+):\d+(?P<suffix>.*)$",
        line,
        flags=re.IGNORECASE,
    )
    if not match:
        return line
    try:
        source_address = ipaddress.ip_address(match.group("host").strip("[]"))
    except ValueError:
        return line
    if source_address.version != 4:
        return line
    query = parse_qs(urlparse(line).query)
    transport = str((query.get("type") or [""])[0]).strip().lower()
    ws_host = str((query.get("host") or [""])[0]).strip().lower().strip(".")
    if transport != "ws" or ws_host != origin_host:
        return line
    return f"{match.group('prefix')}{alias}:{alias_port}{match.group('suffix')}"


def _parse_subscription_userinfo(value: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for item in value.split(";"):
        if "=" not in item:
            continue
        key, raw = item.strip().split("=", 1)
        try:
            values[key.lower()] = int(raw)
        except ValueError:
            continue
    return values


def _apply_usage_carryover(config: Config, upstream: dict) -> dict:
    offset = max(0, int(config.usage_offset_bytes or 0))
    display_total = (
        max(0, int(config.display_total_bytes))
        if config.display_total_bytes is not None
        else None
    )
    if not offset and display_total is None:
        return upstream

    adjusted = {
        **upstream,
        "usage": dict(upstream.get("usage") or {}),
        "forward_headers": dict(upstream.get("forward_headers") or {}),
    }
    usage = adjusted["usage"]
    usage["upload"] = max(0, int(usage.get("upload", 0) or 0)) + offset
    usage["download"] = max(0, int(usage.get("download", 0) or 0))
    if display_total is not None:
        usage["total"] = display_total

    fields = [
        f"upload={usage['upload']}",
        f"download={usage['download']}",
        f"total={max(0, int(usage.get('total', 0) or 0))}",
    ]
    if usage.get("expire"):
        fields.append(f"expire={max(0, int(usage['expire']))}")
    adjusted["forward_headers"]["subscription-userinfo"] = "; ".join(fields)
    return adjusted


def _decode_profile_title(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.lower().startswith("base64:"):
        encoded = value.split(":", 1)[1].strip()
        try:
            return base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8").strip()
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return ""
    return unquote(value).strip().strip("\"'")


def _content_disposition_title(value: str) -> str:
    match = re.search(r"filename\*?=(?:UTF-8''|)(?:\"([^\"]+)\"|([^;]+))", value, flags=re.IGNORECASE)
    if not match:
        return ""
    return unquote((match.group(1) or match.group(2) or "").strip()).strip("\"'")


def _upstream_title(headers: httpx.Headers) -> str:
    profile_title = _decode_profile_title(headers.get("profile-title", ""))
    disposition_title = _content_disposition_title(headers.get("content-disposition", ""))
    if _usable_subscription_title(profile_title):
        return _clean_upstream_brand_suffix(profile_title.strip())
    return _clean_upstream_brand_suffix(_usable_subscription_title(disposition_title)) or "Subscription"


def _usable_subscription_title(value: str | None) -> str:
    title = _clean_upstream_brand_suffix(value or "")
    if not title:
        return ""
    if title.casefold() in GENERIC_SUBSCRIPTION_TITLES:
        return ""
    return title


def _app_title_for_subscription(config: Config, upstream: dict) -> str:
    panel = load_panel_settings()
    return (
        _usable_subscription_title(config.profile_title)
        or _usable_subscription_title(panel.subscription_profile_title)
        or _usable_subscription_title(config.service_name)
        or _usable_subscription_title(upstream["title"])
        or panel.brand_name
        or "Phantom Hubs"
    ).strip()


def _info_title_for_subscription(config: Config, upstream: dict) -> str:
    return (
        _usable_subscription_title(config.panel_username)
        or _usable_subscription_title(config.service_name)
        or _app_title_for_subscription(config, upstream)
    ).strip()


def _web_title_for_subscription(config: Config, upstream: dict) -> str:
    panel = load_panel_settings()
    return (
        _usable_subscription_title(upstream["title"])
        or _usable_subscription_title(config.service_name)
        or _usable_subscription_title(config.profile_title)
        or panel.brand_name
        or "Phantom Hubs"
    ).strip()


def _device_limit_for_subscription(config: Config) -> int:
    if config.device_limit is not None:
        return max(0, int(config.device_limit))
    return max(0, int(load_panel_settings().subscription_device_limit or 0))


def _client_ip_hint(request: Request) -> str:
    for header in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for"):
        value = request.headers.get(header, "").split(",", 1)[0].strip()
        if value:
            return value
    return request.client.host if request.client else ""


def _normalized_device_user_agent(user_agent: str) -> str:
    value = user_agent.strip().lower()
    # Strip short app/OS version tokens while preserving long installation IDs
    # such as Happ's per-device numeric identifier.
    value = re.sub(r"([/\s])v?\d{1,4}(?:\.\d+){0,4}\b", r"\1{version}", value)
    value = re.sub(r"\s+", " ", value)
    return value[:300]


def _device_client_family(user_agent: str) -> str:
    value = user_agent.strip().casefold()
    aliases = (
        ("v2rayng", ("v2rayng",)),
        ("v2box", ("v2box",)),
        ("hiddify", ("hiddify",)),
        ("happ", ("happ/", "happ ")),
        ("streisand", ("streisand",)),
        ("sing-box", ("sing-box", "singbox", "sfa/")),
        ("nekobox", ("nekobox",)),
        ("clash", ("clash", "stash/")),
        ("shadowrocket", ("shadowrocket",)),
        ("wireguard", ("wireguard",)),
    )
    for family, markers in aliases:
        if any(marker in value for marker in markers):
            return family
    product = re.match(r"\s*([a-z][a-z0-9._-]{2,40})", value)
    return product.group(1) if product else ""


def _explicit_device_identifier(request: Request) -> str:
    for header in ("x-hwid", "hwid", "x-device-id", "device-id", "x-client-id", "x-install-id"):
        value = request.headers.get(header, "").strip()
        if 6 <= len(value) <= 300:
            return f"{header}:{value.casefold()}"
    return ""


def _is_trackable_device_request(user_agent: str) -> bool:
    value = user_agent.strip().casefold()
    if not value:
        return False
    ignored_clients = (
        "bot",
        "crawler",
        "spider",
        "preview",
        "google-read-aloud",
        "facebookexternalhit",
        "telegrambot",
        "whatsapp",
    )
    return not any(client in value for client in ignored_clients)


def _device_fingerprints(request: Request) -> tuple[str, str, str, str, str]:
    user_agent = request.headers.get("user-agent", "").strip()[:300]
    accept_language = request.headers.get("accept-language", "").strip()[:120]
    ip_hint = _client_ip_hint(request)[:120]
    explicit_identifier = _explicit_device_identifier(request)
    identity_kind = "explicit" if explicit_identifier else "user-agent"
    stable_seed = explicit_identifier or _normalized_device_user_agent(user_agent)
    legacy_seed = "\n".join([user_agent, accept_language, ip_hint])
    return (
        f"v2:{identity_kind}:{hashlib.sha256(stable_seed.encode('utf-8')).hexdigest()}",
        hashlib.sha256(legacy_seed.encode("utf-8")).hexdigest(),
        user_agent,
        ip_hint,
        identity_kind,
    )


def _device_fingerprint_aliases(device: SubscriptionDevice) -> set[str]:
    try:
        values = json.loads(device.fingerprint_aliases_json or "[]")
    except (TypeError, ValueError):
        return set()
    return {
        str(value)
        for value in values
        if isinstance(value, str) and value
    }


def _set_device_fingerprint_aliases(
    device: SubscriptionDevice,
    fingerprints: set[str],
) -> None:
    fingerprints.discard("")
    fingerprints.discard(device.fingerprint or "")
    device.fingerprint_aliases_json = json.dumps(
        sorted(fingerprints)[-32:],
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _device_platform(user_agent: str) -> str:
    value = user_agent.strip().casefold()
    if any(marker in value for marker in ("macos", "mac os", "darwin")):
        return "macos"
    if any(marker in value for marker in ("iphone", "ipad", "/ios", ";ios")):
        return "ios"
    if "android" in value:
        return "android"
    if "windows" in value:
        return "windows"
    if "linux" in value:
        return "linux"
    return ""


def _same_device_pairing_candidate(
    device: SubscriptionDevice,
    *,
    user_agent: str,
    ip_hint: str,
    now: datetime,
) -> bool:
    if not ip_hint or not device.ip_hint or device.ip_hint != ip_hint:
        return False
    last_seen_at = _as_aware(device.last_seen_at)
    if last_seen_at is None or (now - last_seen_at).total_seconds() > 7 * 86400:
        return False
    existing_platform = _device_platform(device.user_agent or "")
    current_platform = _device_platform(user_agent)
    if existing_platform and current_platform and existing_platform != current_platform:
        return False
    return bool(
        _device_client_family(device.user_agent or "")
        and _device_client_family(user_agent)
    )


async def _collapse_legacy_device_rows() -> int:
    async with async_session() as session:
        result = await session.execute(
            select(SubscriptionDevice)
        )
        all_devices = list(result.scalars().all())
        ignored_ids = {
            device.id
            for device in all_devices
            if not _is_trackable_device_request(device.user_agent or "")
        }
        devices = [
            device
            for device in all_devices
            if device.id not in ignored_ids
            and not (device.fingerprint or "").startswith("v2:")
        ]
        grouped: dict[tuple[str, str], list[SubscriptionDevice]] = {}
        for device in devices:
            family = _device_client_family(device.user_agent or "")
            if family:
                grouped.setdefault((device.public_sub_token, family), []).append(device)

        duplicate_ids: set[int] = set()
        for matches in grouped.values():
            matches.sort(
                key=lambda device: (
                    _as_aware(device.last_seen_at) or datetime.min.replace(tzinfo=timezone.utc),
                    device.id,
                ),
                reverse=True,
            )
            duplicate_ids.update(device.id for device in matches[1:])
        delete_ids = duplicate_ids | ignored_ids
        if not delete_ids:
            return 0

        await session.execute(
            delete(SubscriptionDevice).where(SubscriptionDevice.id.in_(delete_ids))
        )
        await session.commit()
        return len(delete_ids)


async def _enforce_device_limit(config: Config, request: Request) -> None:
    limit = _device_limit_for_subscription(config)
    if limit <= 0:
        return
    fingerprint, legacy_fingerprint, user_agent, ip_hint, identity_kind = _device_fingerprints(request)
    if not _is_trackable_device_request(user_agent):
        return
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        device_result = await session.execute(
            select(SubscriptionDevice).where(
                SubscriptionDevice.public_sub_token == config.public_sub_token
            )
        )
        devices = list(device_result.scalars().all())
        direct_matches = [
            device for device in devices if device.fingerprint in {fingerprint, legacy_fingerprint}
        ]
        alias_matches = [
            device
            for device in devices
            if fingerprint in _device_fingerprint_aliases(device)
        ]
        ua_matches = [
            device
            for device in devices
            if _normalized_device_user_agent(device.user_agent or "")
            == _normalized_device_user_agent(user_agent)
        ]
        client_family = _device_client_family(user_agent)
        legacy_family_matches = [
            device
            for device in devices
            if not (device.fingerprint or "").startswith("v2:")
            and client_family
            and _device_client_family(device.user_agent or "") == client_family
        ]
        # Older fingerprints included language and IP. A single matching client
        # family is migrated once, and its duplicate IP/version records are removed.
        has_v2_family_identity = any(
            (device.fingerprint or "").startswith("v2:")
            and _device_client_family(device.user_agent or "") == client_family
            for device in devices
        )
        if identity_kind == "user-agent":
            ua_migration_matches = ua_matches
        else:
            ua_migration_matches = [
                device
                for device in ua_matches
                if not (device.fingerprint or "").startswith("v2:explicit:")
            ]
        legacy_migration_matches = (
            legacy_family_matches
            if identity_kind == "user-agent" or not has_v2_family_identity
            else []
        )
        network_pairing_matches = [
            device
            for device in devices
            if _same_device_pairing_candidate(
                device,
                user_agent=user_agent,
                ip_hint=ip_hint,
                now=now,
            )
        ]
        if len(network_pairing_matches) != 1:
            network_pairing_matches = []
        matches = (
            direct_matches
            or alias_matches
            or ua_migration_matches
            or legacy_migration_matches
            or network_pairing_matches
        )
        existing = matches[0] if matches else None
        if existing is not None:
            cleanup_matches = {
                device.id: device
                for device in [*matches, *legacy_family_matches, *ua_migration_matches]
            }
            duplicate_ids = {
                device_id for device_id in cleanup_matches if device_id != existing.id
            }
            if duplicate_ids:
                await session.execute(
                    delete(SubscriptionDevice).where(SubscriptionDevice.id.in_(duplicate_ids))
                )
            aliases = _device_fingerprint_aliases(existing)
            for matched_device in cleanup_matches.values():
                aliases.add(matched_device.fingerprint or "")
                aliases.update(_device_fingerprint_aliases(matched_device))
            aliases.add(fingerprint)
            if not (existing.fingerprint or "").startswith("v2:"):
                existing.fingerprint = fingerprint
            _set_device_fingerprint_aliases(existing, aliases)
            existing.user_agent = user_agent
            existing.ip_hint = ip_hint
            existing.last_seen_at = now
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
            return

        count = (
            await session.execute(
                select(func.count(SubscriptionDevice.id)).where(
                    SubscriptionDevice.public_sub_token == config.public_sub_token
                )
            )
        ).scalar_one()
        if int(count or 0) >= limit:
            db_config = await session.get(Config, config.id)
            if db_config is not None:
                warning_text = _device_limit_warning_to_send(db_config, now)
                await session.commit()
                if warning_text:
                    asyncio.create_task(_send_device_limit_warning(db_config.telegram_user_id, warning_text))
            raise HTTPException(
                status_code=403,
                detail=f"Device limit reached for this subscription ({limit}).",
            )

        session.add(
            SubscriptionDevice(
                public_sub_token=config.public_sub_token,
                fingerprint=fingerprint,
                user_agent=user_agent,
                ip_hint=ip_hint,
                first_seen_at=now,
                last_seen_at=now,
            )
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()


def _device_limit_warning_to_send(config: Config, now: datetime) -> str | None:
    if not settings.device_limit_warning_bot_token:
        return None
    if not config.telegram_user_id:
        return None
    last_warning_at = _as_aware(config.device_limit_last_warning_at)
    cooldown = max(300, int(settings.device_limit_warning_cooldown_seconds or 21600))
    if last_warning_at and (now - last_warning_at).total_seconds() < cooldown:
        return None

    config.device_limit_warning_count = max(0, int(config.device_limit_warning_count or 0)) + 1
    config.device_limit_last_warning_at = now
    service_name = (config.service_name or config.profile_title or "اشتراک شما").strip()
    return (
        f"🔴 کاربر گرامی اتصال کانکشن شما به سرویس {service_name} بیشتر از حد مجاز می‌باشد.\n\n"
        f"⭕️ تعداد اخطار : {config.device_limit_warning_count}\n\n"
        "⚠️ در صورتی که تعداد تلاش‌های غیرمجاز شما بیشتر شود، سرویس شما برای 6 ساعت مسدود خواهد شد."
    )


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _send_device_limit_warning(telegram_user_id: int | None, text: str) -> None:
    if not settings.device_limit_warning_bot_token or not telegram_user_id:
        return
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.post(
                f"https://api.telegram.org/bot{settings.device_limit_warning_bot_token}/sendMessage",
                json={
                    "chat_id": int(telegram_user_id),
                    "text": text,
                    "disable_web_page_preview": True,
                },
            )
    except (httpx.HTTPError, ValueError, TypeError):
        return


def _subscription_title_headers(title: str) -> dict[str, str]:
    safe_title = title.strip() or "Subscription"
    quoted_title = quote(safe_title, safe="")
    encoded_title = base64.b64encode(safe_title.encode("utf-8")).decode("ascii")
    ascii_filename = "".join(ch if 32 <= ord(ch) < 127 and ch not in {'"', "\\", ";"} else "_" for ch in safe_title)
    ascii_filename = (ascii_filename.strip() or "Subscription")[:80]
    return {
        "profile-title": f"base64:{encoded_title}",
        "content-disposition": f"inline; filename=\"{ascii_filename}.txt\"; filename*=UTF-8''{quoted_title}.txt",
    }


def _config_name(line: str, index: int) -> str:
    fragment = _clean_upstream_brand_suffix(unquote(urlparse(line).fragment).strip())
    return fragment or f"کانفیگ {index}"


def _format_bytes(value: int | None) -> str:
    if not value:
        return "نامحدود"
    units = ("بایت", "کیلوبایت", "مگابایت", "گیگابایت", "ترابایت")
    size = float(value)
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    return f"{size:.1f} {units[index]}"


def _format_compact_gb(value: int) -> str:
    size_gb = max(value, 0) / 1024**3
    return f"{size_gb:g}GB"


def _config_bool(value: bool | None, default: bool) -> bool:
    return default if value is None else bool(value)


def _render_subscription_page(config: Config, upstream: dict, web_title: str = "") -> str:
    panel = load_panel_settings()
    show_header = _config_bool(config.show_header, True)
    show_config_preview = _config_bool(config.show_config_preview, panel.show_config_preview)
    channel_handle = (config.channel_handle or panel.channel_handle).strip()
    usage = upstream["usage"]
    used = usage.get("upload", 0) + usage.get("download", 0)
    total = usage.get("total", 0) or max(config.volume_gb, 0) * 1024**3
    remaining = max(total - used, 0) if total else 0
    percent = min(round(used / total * 100), 100) if total else 0
    expire = usage.get("expire")
    expire_text = datetime.fromtimestamp(expire, timezone.utc).strftime("%Y-%m-%d") if expire else "نامحدود"
    public_url = f"{settings.public_base_url}/token/{quote(config.public_sub_token, safe='')}"
    config_rows = ""
    for index, line in enumerate(_rewritten_subscription_lines(config, upstream)[:20], 1):
        copy_button = (
            f"<button class='mini-btn' style='background:{panel.config_copy_button_color}' onclick='copyText({html.escape(json.dumps(line), quote=True)});event.stopPropagation()'>{html.escape(panel.config_copy_button_text)}</button>"
            if panel.show_config_copy else ""
        )
        qr_button = (
            f"<button class='mini-btn' style='background:{panel.config_qr_button_color}' onclick='showQR({html.escape(json.dumps(line), quote=True)});event.stopPropagation()'>{html.escape(panel.config_qr_button_text)}</button>"
            if panel.show_config_qr else ""
        )
        config_rows += (
            "<div class='proxy-item'><div class='proxy-copy'>"
            f"<strong>{html.escape(_config_name(line, index))}</strong><span>{html.escape(line)}</span></div>"
            f"<div class='proxy-actions'>{copy_button}{qr_button}</div></div>"
        )
    empty_configs = f"<div class='empty'>{html.escape(panel.empty_configs_text)}</div>"
    preview_content = config_rows or empty_configs
    preview = (
        f"<section class='glass-card'><div class='section-title'>{html.escape(panel.configs_title)}</div>"
        f"<div class='proxy-list'>{preview_content}</div></section>"
        if show_config_preview
        else ""
    )
    channel_url = f"https://t.me/{channel_handle.lstrip('@')}"
    app_title = _app_title_for_subscription(config, upstream)
    title = html.escape(web_title or _web_title_for_subscription(config, upstream))
    upstream_total = usage.get("total", 0)
    purchased_volume = _format_compact_gb(upstream_total) if upstream_total else (
        f"{config.volume_gb}GB" if config.volume_gb else "نامحدود"
    )
    quick_connect = ""
    if panel.show_quick_connect:
        encoded_url = quote(public_url, safe="")
        encoded_title = quote(app_title, safe="")
        buttons = "".join(
            _quick_connect_button_html(panel, key, public_url, encoded_url, encoded_title, config.public_sub_token)
            for key in _normalize_quick_connect_order(panel.quick_connect_order).split(",")
        )
        quick_connect = (
            f"<div class='section-title spaced'>{html.escape(panel.apps_title)}</div>"
            f"<p class='apps-help'>{html.escape(panel.apps_help_text)}</p><div class='btn-grid'>"
            f"{buttons}</div>"
        )
    channel_button = (
        f"<a class='link-btn channel-btn' style='background:{panel.channel_button_color}' href='{html.escape(channel_url)}'>{html.escape(panel.channel_button_text)}</a>"
        if panel.show_channel_button else ""
    )
    brand_header = (
        '<div class="brand-header"><img src="/static/header.png" alt="Phantom Hubs"></div>'
        if show_header
        else ""
    )
    return f"""<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin>
<link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet" media="print" onload="this.media='all'">
<noscript><link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet"></noscript>
<style>
*{{box-sizing:border-box;letter-spacing:0}}:root{{--primary:{panel.primary_color};--accent:{panel.accent_color};--bg:{panel.background_color};--card:{panel.card_color};--text:{panel.text_color};--muted:{panel.muted_text_color};--secondary:{panel.secondary_button_color};--border:color-mix(in srgb,var(--text) 18%,transparent)}}body{{margin:0;min-height:100vh;background:var(--bg);color:var(--text);font-family:Vazirmatn,Tahoma,sans-serif}}.background{{position:fixed;inset:0;z-index:-1;background:linear-gradient(145deg,var(--bg),color-mix(in srgb,var(--primary) 16%,var(--bg)))}}.container{{max-width:800px;margin:auto;padding:28px 16px 48px}}.brand-header{{display:flex;justify-content:center;align-items:center;width:100%;margin:0 auto 24px}}.brand-header img{{display:block;width:min(100%,680px);height:auto;aspect-ratio:1080/267;object-fit:contain}}.glass-card{{background:color-mix(in srgb,var(--card) 92%,transparent);border:1px solid var(--border);backdrop-filter:blur(14px);border-radius:8px;padding:20px;margin-bottom:18px;box-shadow:0 20px 50px rgba(0,0,0,.2);content-visibility:auto;contain-intrinsic-size:260px}}.header{{display:flex;justify-content:space-between;gap:16px;align-items:center}}.header-copy{{min-width:0}}.header-labels{{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px}}h1{{font-size:24px;margin:0 0 6px;overflow-wrap:anywhere}}p{{color:var(--muted);line-height:1.9;margin:0}}.status,.volume-badge{{padding:8px 12px;border-radius:8px;white-space:nowrap;flex:0 0 auto}}.status{{background:color-mix(in srgb,var(--accent) 15%,transparent);color:var(--accent);border:1px solid color-mix(in srgb,var(--accent) 40%,transparent)}}.volume-badge{{background:color-mix(in srgb,var(--primary) 18%,transparent);color:color-mix(in srgb,var(--primary) 65%,white);border:1px solid color-mix(in srgb,var(--primary) 48%,transparent)}}.stats-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:18px}}.stat-card{{background:color-mix(in srgb,var(--text) 6%,transparent);border:1px solid var(--border);border-radius:8px;padding:16px}}.stat-label{{color:var(--muted);font-size:13px}}.stat-value{{font-size:19px;font-weight:800;margin-top:6px}}.progress{{height:8px;background:color-mix(in srgb,var(--text) 10%,transparent);border-radius:4px;overflow:hidden;margin-top:12px}}.progress i{{display:block;height:100%;width:{percent}%;background:var(--primary)}}.subscription-container{{display:flex;gap:10px;align-items:stretch}}.subscription-url{{direction:ltr;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;padding:13px;background:color-mix(in srgb,var(--text) 6%,transparent);border:1px solid var(--border);border-radius:8px;color:var(--muted)}}button,.link-btn{{border:0;border-radius:8px;padding:12px 15px;background:var(--primary);color:#fff;font:inherit;font-weight:700;cursor:pointer;text-decoration:none;text-align:center}}.btn-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:14px}}.secondary{{background:var(--secondary);border:1px solid var(--border)}}.channel-btn{{display:block;margin-top:10px}}.section-title{{font-weight:800;margin-bottom:12px}}.spaced{{margin-top:20px;margin-bottom:4px}}.apps-help{{font-size:13px;margin-bottom:12px}}.proxy-list{{display:grid;gap:8px}}.proxy-item{{direction:ltr;text-align:left;background:color-mix(in srgb,var(--text) 5%,transparent);padding:10px;border-radius:8px;display:flex;gap:10px;align-items:center;overflow:hidden}}.proxy-copy{{min-width:0;flex:1}}.proxy-item strong{{direction:rtl;text-align:right;display:block;margin-bottom:4px}}.proxy-item span{{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);font-family:monospace}}.proxy-actions{{display:flex;gap:6px}}.mini-btn{{padding:7px 10px;font-size:12px;white-space:nowrap}}.empty,.foot{{color:var(--muted);text-align:center}}#toast{{position:fixed;left:50%;bottom:24px;transform:translate(-50%,20px);background:var(--text);color:var(--bg);padding:10px 16px;border-radius:8px;font-weight:700;opacity:0;visibility:hidden;transition:.2s;z-index:10;box-shadow:0 10px 30px rgba(0,0,0,.3);white-space:nowrap}}#toast.show{{opacity:1;visibility:visible;transform:translate(-50%,0)}}#qr-modal{{display:none;position:fixed;inset:0;background:rgba(2,6,23,.9);align-items:center;justify-content:center;z-index:5;padding:16px}}#qr-modal.open{{display:flex}}#qrcode{{background:#fff;padding:16px;border-radius:8px;max-width:min(92vw,360px)}}#qrcode svg{{display:block;width:min(76vw,300px);height:auto}}.qr-error{{background:#fff;color:#0f172a;max-width:320px;line-height:1.8;text-align:center;padding:16px;border-radius:8px}}@media(max-width:600px){{.container{{padding-top:20px}}.brand-header{{margin-bottom:18px}}.header{{flex-direction:column;align-items:flex-start;gap:10px}}.header-copy{{width:100%}}.header-labels{{justify-content:flex-start}}.status,.volume-badge{{padding:6px 10px}}.subscription-container{{flex-direction:column;align-items:stretch}}.stats-grid,.btn-grid{{grid-template-columns:1fr}}.proxy-item{{align-items:stretch;flex-direction:column}}.proxy-actions{{direction:rtl}}}}
</style></head><body><div class="background"></div><main class="container">{brand_header}
<section class="glass-card"><div class="header"><div class="header-copy"><h1>{title}</h1><p>{html.escape(panel.hero_text)}</p></div><div class="header-labels"><div class="status">{html.escape(panel.active_status_text)}</div><div class="volume-badge">{purchased_volume}</div></div></div>
<div class="stats-grid"><div class="stat-card"><div class="stat-label">{html.escape(panel.used_label)}</div><div class="stat-value">{_format_bytes(used)}</div><div class="progress"><i></i></div></div><div class="stat-card"><div class="stat-label">{html.escape(panel.remaining_label)}</div><div class="stat-value">{_format_bytes(remaining)}</div></div><div class="stat-card"><div class="stat-label">{html.escape(panel.expiry_label)}</div><div class="stat-value">{expire_text}</div></div><div class="stat-card"><div class="stat-label">{html.escape(panel.config_count_label)}</div><div class="stat-value">{len(upstream['lines'])}</div></div></div></section>
<section class="glass-card"><div class="section-title">{html.escape(panel.subscription_title)}</div><div class="subscription-container"><div class="subscription-url">{html.escape(public_url)}</div><button style="background:{panel.copy_button_color}" onclick="copyText(link)">{html.escape(panel.copy_button_text)}</button><button style="background:{panel.qr_button_color}" onclick="showQR(link)">{html.escape(panel.qr_button_text)}</button></div>
{quick_connect}{channel_button}</section>
{preview}<div class="foot">{html.escape(panel.support_text)}</div></main><div id="toast" role="status">{html.escape(panel.copy_success_text)}</div><div id="qr-modal" onclick="this.classList.remove('open')"><div id="qrcode"></div></div>
<script>const link={json.dumps(public_url)};let toastTimer;async function copyText(value){{try{{await navigator.clipboard.writeText(value)}}catch(error){{const area=document.createElement('textarea');area.value=value;document.body.appendChild(area);area.select();document.execCommand('copy');area.remove()}}const toast=document.getElementById('toast');toast.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>toast.classList.remove('show'),1800)}}async function showQR(value){{const modal=document.getElementById('qr-modal');const box=document.getElementById('qrcode');box.innerHTML='';modal.classList.add('open');try{{const response=await fetch('/qr',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{data:value}})}});if(!response.ok){{throw new Error(response.status===413?'too-long':'qr')}}box.innerHTML=await response.text()}}catch(error){{const message=error.message==='too-long'?'این کانفیگ برای QR بیش از حد طولانی است. از دکمه کپی استفاده کنید.':'ساخت QR برای این کانفیگ ممکن نشد. از دکمه کپی استفاده کنید.';box.innerHTML='<div class="qr-error">'+message+'</div>'}}}}</script></body></html>"""


async def _render_admin(panel: PanelSettings, notice: str = "", error: str = "") -> str:
    async with async_session() as session:
        result = await session.execute(select(Config).order_by(Config.id.desc()))
        configs = list(result.scalars().all())
        count_result = await session.execute(
            select(SubscriptionDevice.public_sub_token, func.count(SubscriptionDevice.id))
            .group_by(SubscriptionDevice.public_sub_token)
        )
        device_counts = {str(token): int(count or 0) for token, count in count_result.all()}

    def row(config: Config) -> str:
        public_url = f"{settings.public_base_url}/token/{quote(config.public_sub_token, safe='')}"
        header_checked = "checked" if _config_bool(config.show_header, True) else ""
        preview_checked = "checked" if _config_bool(config.show_config_preview, panel.show_config_preview) else ""
        info_checked = "checked" if bool(config.info_proxies_enabled) else ""
        channel_value = html.escape(config.channel_handle or "")
        address_rewrites_value = html.escape(_display_address_rewrites(config))
        device_count = device_counts.get(config.public_sub_token, 0)
        search_text = " ".join(
            [
                config.service_name or "",
                config.profile_title or "",
                config.public_sub_token or "",
                config.sub_link or "",
                public_url,
            ]
        )
        volume_text = "نامحدود" if not config.volume_gb else f"{config.volume_gb} GB"
        return f"""<article class="sub-card" data-search="{html.escape(search_text.casefold(), quote=True)}"><div class="sub-card-head"><div><strong>{html.escape(config.service_name or "-")}</strong><span>{html.escape(config.profile_title or "نام اختصاصی ندارد")}</span></div><b>{html.escape(volume_text)}</b></div><div class="link-panel"><div><span>لینک ساخته‌شده</span><a class="ltr break" href="{public_url}" target="_blank">{html.escape(public_url)}</a></div><button type="button" class="copy-admin" onclick="copyAdminLink({html.escape(json.dumps(public_url), quote=True)})">کپی لینک</button></div><div class="sub-grid"><form class="inline-form" method="post" action="/admin/subscriptions/{config.id}/device-limit"><label>محدودیت کاربر/دستگاه<input name="device_limit" type="number" min="0" value="{config.device_limit if config.device_limit is not None else 0}" title="0 یعنی نامحدود"></label><button>ثبت</button></form><form class="stack-form" method="post" action="/admin/subscriptions/{config.id}/display"><div class="toggle-row"><label class="tiny-toggle"><input name="show_header" type="checkbox" {header_checked}> هدر</label><label class="tiny-toggle"><input name="show_config_preview" type="checkbox" {preview_checked}> کانفیگ‌ها</label><label class="tiny-toggle"><input name="info_proxies_enabled" type="checkbox" {info_checked}> کانفیگ‌های اطلاعاتی</label></div><label>نام نمایشی داخل برنامه‌ها<input name="profile_title" value="{html.escape(config.profile_title or '')}" placeholder="مثلا PhantomHubs VIP"></label><label>Username پنل برای کانفیگ آدمک<input name="panel_username" value="{html.escape(config.panel_username or '')}" placeholder="مثلا PhantomExpress10GB-VIP1"></label><label>کانال اختصاصی<input name="channel_handle" value="{channel_value}" placeholder="@SupportChannel"></label><label>بازنویسی آدرس کانفیگ<textarea class="ltr" name="address_rewrites" placeholder="es.sv.temas-bor.ir=es.api.bahrevari01.shop">{address_rewrites_value}</textarea><span>هر خط: آدرس فعلی=آدرس جدید؛ پورت و تنظیمات اتصال دست‌نخورده می‌مانند.</span></label><button>ذخیره نمایش</button></form></div><div class="device-panel"><span>دستگاه‌های ثبت‌شده: <b>{device_count}</b></span><form method="post" action="/admin/subscriptions/{config.id}/devices/reset"><button type="submit">ریست شمارش</button></form><form method="post" action="/admin/subscriptions/{config.id}/revoke" onsubmit="return confirm('لینک قبلی باطل و لینک جدید ساخته شود؟')"><button type="submit" class="danger">Revoke لینک</button></form></div><details><summary>لینک اصلی</summary><p class="ltr break">{html.escape(config.sub_link)}</p><form class="replace-form" method="post" action="/admin/subscriptions/{config.id}/upstream" onsubmit="return confirm('لینک اصلی این اشتراک جایگزین شود؟ لینک ساخته‌شده و توکن فعلی حفظ می‌شود.')"><label>جایگزینی لینک اصلی<input name="upstream_url" type="url" required dir="ltr" value="{html.escape(config.sub_link, quote=True)}"></label><button type="submit">جایگزینی لینک اصلی</button></form></details><form class="delete-form" method="post" action="/admin/subscriptions/{config.id}/delete"><button class="danger">حذف</button></form></article>"""

    rows = "".join(row(config) for config in configs) or "<div class='empty-admin'>هنوز لینکی ثبت نشده است.</div>"
    flash = f"<div class='notice'>{html.escape(notice)}</div>" if notice else f"<div class='error'>{html.escape(error)}</div>" if error else ""
    checked = {
        "quick": "checked" if panel.show_quick_connect else "",
        "channel": "checked" if panel.show_channel_button else "",
        "preview": "checked" if panel.show_config_preview else "",
        "copy": "checked" if panel.show_config_copy else "",
        "qr": "checked" if panel.show_config_qr else "",
    }
    return f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>مدیریت پنل اشتراک</title><link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet"><style>
*{{box-sizing:border-box;letter-spacing:0}}body{{margin:0;background:#f4f7fb;color:#172033;font-family:Vazirmatn,Tahoma,sans-serif}}main{{max-width:1100px;margin:auto;padding:24px 16px 50px}}header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}}h1{{font-size:25px;margin:0}}h2{{font-size:18px;margin:0 0 16px}}.card{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:20px;margin-bottom:16px;box-shadow:0 8px 24px rgba(15,23,42,.05)}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}}label{{display:grid;gap:6px;color:#64748b;font-size:13px}}input,textarea{{border:1px solid #cbd5e1;border-radius:8px;padding:11px;font:inherit;color:#172033;min-width:0}}textarea{{min-height:88px;resize:vertical}}button{{border:0;border-radius:8px;background:{panel.primary_color};color:white;padding:11px 16px;font:inherit;font-weight:700;cursor:pointer}}.danger{{background:#dc2626;padding:7px 10px}}.wide{{grid-column:1/-1}}.notice,.error{{padding:11px;border-radius:8px;margin-bottom:16px;overflow-wrap:anywhere}}.notice{{background:#dcfce7;color:#166534}}.error{{background:#fee2e2;color:#991b1b}}a{{color:{panel.primary_color};font-weight:700}}.actions{{display:flex;justify-content:flex-end;margin-top:14px}}.toggle,.tiny-toggle{{display:flex;align-items:center;gap:8px}}.template-settings{{overflow:hidden}}.template-summary{{display:flex;align-items:center;justify-content:space-between;gap:12px;list-style:none;color:#172033}}.template-summary::-webkit-details-marker{{display:none}}.template-title{{display:grid;gap:4px}}.template-title h2{{margin:0}}.template-title span{{color:#64748b;font-size:13px}}.summary-pill{{background:#eef2ff;color:{panel.primary_color};border:1px solid #c7d2fe;border-radius:8px;padding:8px 12px;font-weight:800;white-space:nowrap}}.template-settings[open] .summary-pill{{background:#f1f5f9;color:#334155;border-color:#cbd5e1}}.template-settings form{{margin-top:18px;padding-top:18px;border-top:1px solid #e2e8f0}}.sub-tools{{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:end;margin-bottom:14px}}.sub-tools label{{font-size:13px}}.search-count{{color:#64748b;font-size:13px;padding:11px 0;white-space:nowrap}}.sub-list{{display:grid;grid-template-columns:repeat(auto-fit,minmax(440px,1fr));gap:14px;align-items:start}}.sub-card{{border:1px solid #e2e8f0;border-radius:8px;padding:14px;background:#f8fafc;display:grid;gap:12px;min-width:0}}.sub-card[hidden]{{display:none}}.sub-card-head{{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;padding-bottom:10px;border-bottom:1px solid #e2e8f0}}.sub-card-head div{{display:grid;gap:4px;min-width:0}}.sub-card-head strong{{overflow-wrap:anywhere}}.sub-card-head span{{color:#64748b;font-size:12px;overflow-wrap:anywhere}}.sub-card-head b{{white-space:nowrap;color:{panel.primary_color};background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:6px 9px}}.link-panel{{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:center;background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:10px}}.link-panel span{{display:block;color:#64748b;font-size:12px;margin-bottom:4px}}.sub-grid{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.25fr);gap:10px}}.inline-form,.stack-form{{display:grid;gap:8px;align-content:start;background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:10px}}.inline-form{{grid-template-columns:minmax(0,1fr) auto;align-items:end}}.inline-form button,.stack-form button,.copy-admin{{padding:8px 11px}}.copy-admin{{background:#334155;white-space:nowrap}}.toggle-row{{display:flex;flex-wrap:wrap;gap:12px}}.break{{overflow-wrap:anywhere;white-space:normal}}.ltr{{direction:ltr;text-align:left}}details{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:8px}}summary{{cursor:pointer;color:#64748b}}.delete-form{{display:flex;justify-content:flex-end}}.empty-admin{{padding:16px;color:#64748b;text-align:center}}#empty-search{{display:none}}@media(max-width:700px){{main{{padding:18px 10px 40px}}header{{display:grid;gap:10px}}.grid,.sub-grid,.inline-form,.sub-tools,.link-panel{{grid-template-columns:1fr}}.wide{{grid-column:auto}}.sub-list{{grid-template-columns:1fr}}.sub-card-head{{display:grid}}.template-summary{{align-items:flex-start;display:grid}}.summary-pill{{justify-self:start}}}}</style></head><body><main><header><div><h1>مدیریت Phantom Subscription</h1><span>ساخته‌شده بر پایه ظاهر marzban-template</span></div><a href="{settings.public_base_url}/health">وضعیت سرویس</a></header>{flash}
<section class="card"><h2>تبدیل دستی لینک ساب</h2><form method="post" action="/admin/subscriptions"><div class="grid"><label class="wide">لینک اصلی سابسکریپشن<input name="upstream_url" type="url" required placeholder="https://example.com/token/..."></label><label>توکن دلخواه، اختیاری<input name="token" placeholder="اگر خالی باشد خودکار ساخته می‌شود"></label><label>نام سرویس<input name="service_name"></label><label>Username پنل برای کانفیگ آدمک<input name="panel_username" placeholder="اگر خالی باشد از نام سرویس استفاده می‌شود"></label><label>نام نمایشی اختصاصی داخل برنامه‌ها<input name="profile_title" placeholder="فقط برای همین لینک"></label><label>محدودیت کاربر/دستگاه همین لینک<input name="device_limit" type="number" min="0" value="0" placeholder="0 یعنی نامحدود"></label><label>کانال/پشتیبانی اختصاصی<input name="channel_handle" placeholder="@PhantomHubsSupport"></label><label>حجم گیگ<input name="volume_gb" type="number" min="0" value="0"></label><label>دسته‌بندی<input name="category_key" value="manual"></label><label class="wide">بازنویسی آدرس کانفیگ<textarea class="ltr" name="address_rewrites" placeholder="es.sv.temas-bor.ir=es.api.bahrevari01.shop"></textarea></label><label class="toggle"><input name="show_header" type="checkbox" checked> نمایش هدر سایت</label><label class="toggle"><input name="show_config_preview" type="checkbox" checked> نمایش کانفیگ‌های اشتراک</label><label class="toggle"><input name="info_proxies_enabled" type="checkbox"> افزودن کانفیگ‌های اطلاعاتی</label></div><div class="actions"><button>ساخت لینک اختصاصی</button></div></form></section>
<details class="card template-settings"><summary class="template-summary"><div class="template-title"><h2>تنظیمات کامل قالب</h2><span>رنگ‌ها، متن‌ها، دکمه‌ها و نمایش بخش‌های صفحه اشتراک</span></div><span class="summary-pill">باز/بستن تنظیمات</span></summary><form method="post" action="/admin/settings"><div class="grid">
<label>نام برند<input name="brand_name" value="{html.escape(panel.brand_name)}"></label><label>آیدی کانال<input name="channel_handle" value="{html.escape(panel.channel_handle)}"></label>
<label class="wide">نام نمایشی سابسکریپشن داخل برنامه‌ها<input name="subscription_profile_title" value="{html.escape(panel.subscription_profile_title)}" placeholder="خالی باشد، نام لینک اصلی یا نام سرویس استفاده می‌شود"></label>
<label>محدودیت کاربر/دستگاه پیش‌فرض لینک‌های ساب<input name="subscription_device_limit" type="number" min="0" value="{panel.subscription_device_limit}"><span>0 یعنی نامحدود؛ لینک‌هایی که محدودیت اختصاصی دارند از عدد خودشان استفاده می‌کنند.</span></label>
<label>رنگ اصلی<input name="primary_color" type="color" value="{panel.primary_color}"></label><label>رنگ وضعیت<input name="accent_color" type="color" value="{panel.accent_color}"></label>
<label>رنگ پس‌زمینه<input name="background_color" type="color" value="{panel.background_color}"></label><label>رنگ کارت‌ها<input name="card_color" type="color" value="{panel.card_color}"></label>
<label>رنگ متن اصلی<input name="text_color" type="color" value="{panel.text_color}"></label><label>رنگ متن فرعی<input name="muted_text_color" type="color" value="{panel.muted_text_color}"></label>
<label>رنگ دکمه فرعی<input name="secondary_button_color" type="color" value="{panel.secondary_button_color}"></label><label>متن وضعیت<input name="active_status_text" value="{html.escape(panel.active_status_text)}"></label>
<label>عنوان حجم خریداری‌شده<input name="purchased_volume_label" value="{html.escape(panel.purchased_volume_label)}"></label>
<label class="wide">متن بالای صفحه<textarea name="hero_text">{html.escape(panel.hero_text)}</textarea></label><label class="wide">متن پشتیبانی<textarea name="support_text">{html.escape(panel.support_text)}</textarea></label>
<label>عنوان مصرف‌شده<input name="used_label" value="{html.escape(panel.used_label)}"></label><label>عنوان باقی‌مانده<input name="remaining_label" value="{html.escape(panel.remaining_label)}"></label>
<label>عنوان انقضا<input name="expiry_label" value="{html.escape(panel.expiry_label)}"></label><label>عنوان تعداد کانفیگ<input name="config_count_label" value="{html.escape(panel.config_count_label)}"></label>
<label>عنوان لینک اشتراک<input name="subscription_title" value="{html.escape(panel.subscription_title)}"></label><label>متن دکمه کپی لینک<input name="copy_button_text" value="{html.escape(panel.copy_button_text)}"></label>
<label>پیام موفقیت کپی<input name="copy_success_text" value="{html.escape(panel.copy_success_text)}"></label><label>متن دکمه QR لینک<input name="qr_button_text" value="{html.escape(panel.qr_button_text)}"></label>
<label>رنگ دکمه کپی لینک<input name="copy_button_color" type="color" value="{panel.copy_button_color}"></label><label>رنگ دکمه QR لینک<input name="qr_button_color" type="color" value="{panel.qr_button_color}"></label>
<label>عنوان اتصال سریع<input name="apps_title" value="{html.escape(panel.apps_title)}"></label><label class="wide">متن راهنمای اتصال سریع<input name="apps_help_text" value="{html.escape(panel.apps_help_text)}"></label>
<label class="wide">ترتیب دکمه‌های اتصال سریع<input name="quick_connect_order" dir="ltr" value="{html.escape(_normalize_quick_connect_order(panel.quick_connect_order))}"><span>کلیدها را با کاما جدا کنید: v2rayng, hiddify, streisand, singbox, v2box, happ</span></label>
<label>متن V2RayNG<input name="v2rayng_button_text" value="{html.escape(panel.v2rayng_button_text)}"></label><label>متن Hiddify<input name="hiddify_button_text" value="{html.escape(panel.hiddify_button_text)}"></label>
<label>متن Streisand<input name="streisand_button_text" value="{html.escape(panel.streisand_button_text)}"></label><label>متن Sing-box<input name="singbox_button_text" value="{html.escape(panel.singbox_button_text)}"></label>
<label>متن V2Box<input name="v2box_button_text" value="{html.escape(panel.v2box_button_text)}"></label><label>متن HAPP<input name="happ_button_text" value="{html.escape(panel.happ_button_text)}"></label>
<label>رنگ V2RayNG<input name="v2rayng_button_color" type="color" value="{panel.v2rayng_button_color}"></label><label>رنگ Hiddify<input name="hiddify_button_color" type="color" value="{panel.hiddify_button_color}"></label>
<label>رنگ Streisand<input name="streisand_button_color" type="color" value="{panel.streisand_button_color}"></label><label>رنگ Sing-box<input name="singbox_button_color" type="color" value="{panel.singbox_button_color}"></label>
<label>رنگ V2Box<input name="v2box_button_color" type="color" value="{panel.v2box_button_color}"></label><label>رنگ HAPP<input name="happ_button_color" type="color" value="{panel.happ_button_color}"></label>
<label>متن دکمه کانال<input name="channel_button_text" value="{html.escape(panel.channel_button_text)}"></label><label>رنگ دکمه کانال<input name="channel_button_color" type="color" value="{panel.channel_button_color}"></label>
<label>عنوان فهرست کانفیگ‌ها<input name="configs_title" value="{html.escape(panel.configs_title)}"></label><label>رنگ کپی هر کانفیگ<input name="config_copy_button_color" type="color" value="{panel.config_copy_button_color}"></label>
<label>متن کپی هر کانفیگ<input name="config_copy_button_text" value="{html.escape(panel.config_copy_button_text)}"></label><label>متن QR هر کانفیگ<input name="config_qr_button_text" value="{html.escape(panel.config_qr_button_text)}"></label>
<label>رنگ QR هر کانفیگ<input name="config_qr_button_color" type="color" value="{panel.config_qr_button_color}"></label>
<label class="wide">متن نبود کانفیگ<input name="empty_configs_text" value="{html.escape(panel.empty_configs_text)}"></label>
<label class="toggle"><input name="show_quick_connect" type="checkbox" {checked['quick']}> نمایش اتصال سریع</label>
<label class="toggle"><input name="show_channel_button" type="checkbox" {checked['channel']}> نمایش دکمه کانال</label>
<label class="toggle"><input name="show_config_preview" type="checkbox" {checked['preview']}> نمایش کانفیگ‌ها</label>
<label class="toggle"><input name="show_config_copy" type="checkbox" {checked['copy']}> نمایش کپی هر کانفیگ</label>
<label class="toggle"><input name="show_config_qr" type="checkbox" {checked['qr']}> نمایش QR هر کانفیگ</label>
</div><div class="actions"><button>ذخیره تنظیمات</button></div></form></details>
<section class="card"><h2>لینک‌های ثبت‌شده</h2><div class="sub-tools"><label>جستجو بین اسم، لینک اصلی و لینک ساخته‌شده<input id="subscription-search" placeholder="مثلا Phantom، token، یا بخشی از لینک"></label><div class="search-count"><span id="visible-count">{len(configs)}</span> / {len(configs)} لینک</div></div><div class="sub-list" id="subscription-list">{rows}</div><div class="empty-admin" id="empty-search">موردی با این جستجو پیدا نشد.</div></section></main><style>.replace-form{{display:grid;gap:9px;margin-top:10px;padding-top:10px;border-top:1px solid #e2e8f0}}.replace-form button{{justify-self:start;background:#0f766e}}</style><script>async function copyAdminLink(value){{try{{await navigator.clipboard.writeText(value);alert('لینک کپی شد.')}}catch(error){{prompt('برای کپی لینک:',value)}}}}const searchInput=document.getElementById('subscription-search');const cards=[...document.querySelectorAll('.sub-card')];const visibleCount=document.getElementById('visible-count');const emptySearch=document.getElementById('empty-search');function filterSubscriptions(){{const term=(searchInput.value||'').trim().toLocaleLowerCase('fa-IR');let shown=0;cards.forEach(card=>{{const match=!term||card.dataset.search.includes(term);card.hidden=!match;if(match)shown+=1}});visibleCount.textContent=shown;emptySearch.style.display=cards.length&&shown===0?'block':'none'}}searchInput.addEventListener('input',filterSubscriptions);</script></body></html>"""
