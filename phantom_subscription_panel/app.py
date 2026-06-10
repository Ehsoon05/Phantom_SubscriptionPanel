from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import html
import json
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import httpx
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select

from .config import settings
from .database import Base, Config, async_session, engine
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
_cache_refresh_tasks: set[str] = set()


class ConfigSyncPayload(BaseModel):
    token: str
    upstream_url: str
    volume_gb: int
    category_key: str = "default"
    is_sold: bool = False
    service_name: str | None = None


@app.on_event("startup")
async def startup() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    settings.subscription_cache_dir.mkdir(parents=True, exist_ok=True)


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

    upstream = await _fetch_upstream(config.sub_link)
    if _wants_html(request):
        return HTMLResponse(_render_subscription_page(config, upstream))

    response_headers = {"Cache-Control": "no-store, no-cache, must-revalidate", "X-Content-Type-Options": "nosniff"}
    response_headers.update(upstream["forward_headers"])
    return Response(
        content=upstream["body"],
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
    v2rayng_button_text: str = Form(...),
    hiddify_button_text: str = Form(...),
    streisand_button_text: str = Form(...),
    happ_button_text: str = Form(...),
    channel_button_text: str = Form(...),
    copy_button_color: str = Form(...),
    qr_button_color: str = Form(...),
    v2rayng_button_color: str = Form(...),
    hiddify_button_color: str = Form(...),
    streisand_button_color: str = Form(...),
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
        v2rayng_button_text=v2rayng_button_text.strip() or "V2RayNG",
        hiddify_button_text=hiddify_button_text.strip() or "Hiddify",
        streisand_button_text=streisand_button_text.strip() or "Streisand",
        happ_button_text=happ_button_text.strip() or "HAPP",
        channel_button_text=channel_button_text.strip() or "کانال پشتیبانی",
        copy_button_color=_normalize_color(copy_button_color, "#426df8"),
        qr_button_color=_normalize_color(qr_button_color, "#334155"),
        v2rayng_button_color=_normalize_color(v2rayng_button_color, "#334155"),
        hiddify_button_color=_normalize_color(hiddify_button_color, "#334155"),
        streisand_button_color=_normalize_color(streisand_button_color, "#334155"),
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
        await session.commit()
    public_url = f"{settings.public_base_url}/token/{quote(token, safe='')}"
    return await _render_admin(load_panel_settings(), notice=f"لینک اختصاصی ساخته شد: {public_url}")


@app.post("/admin/subscriptions/{config_id}/delete")
async def admin_delete_subscription(config_id: int, _: str = Depends(_require_admin)) -> RedirectResponse:
    async with async_session() as session:
        config = await session.get(Config, config_id)
        if config:
            await session.delete(config)
            await session.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/internal/configs", response_class=PlainTextResponse)
async def sync_config(payload: ConfigSyncPayload, authorization: str | None = Header(default=None)) -> str:
    _require_sync_token(authorization)
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
        await session.commit()
    return "ok"


@app.get("/internal/configs/{token}/metadata", response_class=JSONResponse)
async def config_metadata(token: str, authorization: str | None = Header(default=None)) -> dict:
    _require_sync_token(authorization)
    config = await _config_for_token(token)
    if not config:
        raise HTTPException(status_code=404, detail="Subscription not found")
    upstream = await _fetch_upstream(config.sub_link)
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


def _require_sync_token(authorization: str | None) -> None:
    if not settings.sync_token:
        raise HTTPException(status_code=403, detail="PANEL_SYNC_TOKEN is not configured")
    expected = f"Bearer {settings.sync_token}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid sync token")


async def _config_for_token(token: str) -> Config | None:
    async with async_session() as session:
        result = await session.execute(select(Config).where(Config.public_sub_token == token))
        return result.scalar_one_or_none()


async def _fetch_upstream(url: str) -> dict:
    cached = _read_upstream_cache(url)
    if cached:
        if time.time() - cached["cached_at"] >= settings.subscription_cache_ttl_seconds:
            _schedule_cache_refresh(url)
        return cached

    return await _fetch_and_cache_upstream(url)


async def _fetch_and_cache_upstream(url: str) -> dict:
    headers = {
        "User-Agent": "v2rayNG/1.10 PhantomSubscriptionPanel/2.0",
        "Accept": "text/plain, application/octet-stream, */*",
        "Cache-Control": "no-cache",
    }
    last_error: httpx.HTTPError | None = None
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=settings.request_timeout_seconds,
        verify=settings.upstream_verify_tls,
    ) as client:
        for attempt in range(2):
            try:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                break
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < 1:
                    await asyncio.sleep(0.4 * (attempt + 1))
        else:
            raise HTTPException(
                status_code=502,
                detail=f"Upstream subscription is unavailable: {last_error}",
            ) from last_error

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


def _cache_path(url: str) -> Path:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return settings.subscription_cache_dir / f"{key}.json"


def _read_upstream_cache(url: str) -> dict | None:
    try:
        payload = json.loads(_cache_path(url).read_text(encoding="utf-8"))
        body = base64.b64decode(payload["body"])
        return {
            "body": body,
            "content_type": payload["content_type"],
            "forward_headers": payload["forward_headers"],
            "lines": _subscription_lines(body),
            "usage": payload["usage"],
            "title": payload["title"],
            "cached_at": float(payload["cached_at"]),
        }
    except (OSError, KeyError, TypeError, ValueError, binascii.Error, json.JSONDecodeError):
        return None


def _write_upstream_cache(url: str, upstream: dict) -> None:
    path = _cache_path(url)
    temporary_path = path.with_suffix(".tmp")
    payload = {
        "body": base64.b64encode(upstream["body"]).decode("ascii"),
        "content_type": upstream["content_type"],
        "forward_headers": upstream["forward_headers"],
        "usage": upstream["usage"],
        "title": upstream["title"],
        "cached_at": time.time(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary_path.replace(path)
    except OSError:
        temporary_path.unlink(missing_ok=True)


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
    if profile_title and profile_title.casefold() not in {"subscription", "sub"}:
        return profile_title
    return disposition_title or profile_title or "Subscription"


def _config_name(line: str, index: int) -> str:
    fragment = unquote(urlparse(line).fragment).strip()
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


def _render_subscription_page(config: Config, upstream: dict) -> str:
    panel = load_panel_settings()
    usage = upstream["usage"]
    used = usage.get("upload", 0) + usage.get("download", 0)
    total = usage.get("total", 0) or max(config.volume_gb, 0) * 1024**3
    remaining = max(total - used, 0) if total else 0
    percent = min(round(used / total * 100), 100) if total else 0
    expire = usage.get("expire")
    expire_text = datetime.fromtimestamp(expire, timezone.utc).strftime("%Y-%m-%d") if expire else "نامحدود"
    public_url = f"{settings.public_base_url}/token/{quote(config.public_sub_token, safe='')}"
    config_rows = ""
    for index, line in enumerate(upstream["lines"][:20], 1):
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
        if panel.show_config_preview
        else ""
    )
    channel_url = f"https://t.me/{panel.channel_handle.lstrip('@')}"
    title = html.escape(upstream["title"])
    upstream_total = usage.get("total", 0)
    purchased_volume = _format_compact_gb(upstream_total) if upstream_total else (
        f"{config.volume_gb}GB" if config.volume_gb else "نامشخص"
    )
    quick_connect = ""
    if panel.show_quick_connect:
        encoded_url = quote(public_url, safe="")
        encoded_title = quote(upstream["title"], safe="")
        quick_connect = (
            f"<div class='section-title spaced'>{html.escape(panel.apps_title)}</div>"
            f"<p class='apps-help'>{html.escape(panel.apps_help_text)}</p><div class='btn-grid'>"
            f"<a class='link-btn' style='background:{panel.v2rayng_button_color}' href='v2rayng://install-sub?url={encoded_url}#{encoded_title}'>{html.escape(panel.v2rayng_button_text)}</a>"
            f"<a class='link-btn' style='background:{panel.hiddify_button_color}' href='hiddify://import/?url={encoded_url}&name={encoded_title}'>{html.escape(panel.hiddify_button_text)}</a>"
            f"<a class='link-btn' style='background:{panel.streisand_button_color}' href='streisand://import/{public_url}#{encoded_title}'>{html.escape(panel.streisand_button_text)}</a>"
            f"<a class='link-btn' style='background:{panel.happ_button_color}' href='/connect/happ/{quote(config.public_sub_token, safe='')}'>{html.escape(panel.happ_button_text)}</a></div>"
        )
    channel_button = (
        f"<a class='link-btn channel-btn' style='background:{panel.channel_button_color}' href='{html.escape(channel_url)}'>{html.escape(panel.channel_button_text)}</a>"
        if panel.show_channel_button else ""
    )
    return f"""<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<style>
*{{box-sizing:border-box;letter-spacing:0}}:root{{--primary:{panel.primary_color};--accent:{panel.accent_color};--bg:{panel.background_color};--card:{panel.card_color};--text:{panel.text_color};--muted:{panel.muted_text_color};--secondary:{panel.secondary_button_color};--border:color-mix(in srgb,var(--text) 18%,transparent)}}body{{margin:0;min-height:100vh;background:var(--bg);color:var(--text);font-family:Vazirmatn,Tahoma,sans-serif}}.background{{position:fixed;inset:0;z-index:-1;background:linear-gradient(145deg,var(--bg),color-mix(in srgb,var(--primary) 16%,var(--bg)))}}.container{{max-width:800px;margin:auto;padding:28px 16px 48px}}.brand-header{{display:flex;justify-content:center;align-items:center;width:100%;margin:0 auto 24px}}.brand-header img{{display:block;width:min(100%,680px);height:auto;aspect-ratio:1080/267;object-fit:contain}}.glass-card{{background:color-mix(in srgb,var(--card) 92%,transparent);border:1px solid var(--border);backdrop-filter:blur(14px);border-radius:8px;padding:20px;margin-bottom:18px;box-shadow:0 20px 50px rgba(0,0,0,.2)}}.header{{display:flex;justify-content:space-between;gap:16px;align-items:center}}.header-copy{{min-width:0}}.header-labels{{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px}}h1{{font-size:24px;margin:0 0 6px;overflow-wrap:anywhere}}p{{color:var(--muted);line-height:1.9;margin:0}}.status,.volume-badge{{padding:8px 12px;border-radius:8px;white-space:nowrap;flex:0 0 auto}}.status{{background:color-mix(in srgb,var(--accent) 15%,transparent);color:var(--accent);border:1px solid color-mix(in srgb,var(--accent) 40%,transparent)}}.volume-badge{{background:color-mix(in srgb,var(--primary) 18%,transparent);color:color-mix(in srgb,var(--primary) 65%,white);border:1px solid color-mix(in srgb,var(--primary) 48%,transparent)}}.stats-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:18px}}.stat-card{{background:color-mix(in srgb,var(--text) 6%,transparent);border:1px solid var(--border);border-radius:8px;padding:16px}}.stat-label{{color:var(--muted);font-size:13px}}.stat-value{{font-size:19px;font-weight:800;margin-top:6px}}.progress{{height:8px;background:color-mix(in srgb,var(--text) 10%,transparent);border-radius:4px;overflow:hidden;margin-top:12px}}.progress i{{display:block;height:100%;width:{percent}%;background:var(--primary)}}.subscription-container{{display:flex;gap:10px;align-items:stretch}}.subscription-url{{direction:ltr;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;padding:13px;background:color-mix(in srgb,var(--text) 6%,transparent);border:1px solid var(--border);border-radius:8px;color:var(--muted)}}button,.link-btn{{border:0;border-radius:8px;padding:12px 15px;background:var(--primary);color:#fff;font:inherit;font-weight:700;cursor:pointer;text-decoration:none;text-align:center}}.btn-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:14px}}.secondary{{background:var(--secondary);border:1px solid var(--border)}}.channel-btn{{display:block;margin-top:10px}}.section-title{{font-weight:800;margin-bottom:12px}}.spaced{{margin-top:20px;margin-bottom:4px}}.apps-help{{font-size:13px;margin-bottom:12px}}.proxy-list{{display:grid;gap:8px}}.proxy-item{{direction:ltr;text-align:left;background:color-mix(in srgb,var(--text) 5%,transparent);padding:10px;border-radius:8px;display:flex;gap:10px;align-items:center;overflow:hidden}}.proxy-copy{{min-width:0;flex:1}}.proxy-item strong{{direction:rtl;text-align:right;display:block;margin-bottom:4px}}.proxy-item span{{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted);font-family:monospace}}.proxy-actions{{display:flex;gap:6px}}.mini-btn{{padding:7px 10px;font-size:12px;white-space:nowrap}}.empty,.foot{{color:var(--muted);text-align:center}}#toast{{position:fixed;left:50%;bottom:24px;transform:translate(-50%,20px);background:var(--text);color:var(--bg);padding:10px 16px;border-radius:8px;font-weight:700;opacity:0;visibility:hidden;transition:.2s;z-index:10;box-shadow:0 10px 30px rgba(0,0,0,.3);white-space:nowrap}}#toast.show{{opacity:1;visibility:visible;transform:translate(-50%,0)}}#qr-modal{{display:none;position:fixed;inset:0;background:rgba(2,6,23,.9);align-items:center;justify-content:center;z-index:5}}#qr-modal.open{{display:flex}}#qrcode{{background:#fff;padding:16px;border-radius:8px}}@media(max-width:600px){{.container{{padding-top:20px}}.brand-header{{margin-bottom:18px}}.header{{flex-direction:column;align-items:flex-start;gap:10px}}.header-copy{{width:100%}}.header-labels{{justify-content:flex-start}}.status,.volume-badge{{padding:6px 10px}}.subscription-container{{flex-direction:column;align-items:stretch}}.stats-grid,.btn-grid{{grid-template-columns:1fr}}.proxy-item{{align-items:stretch;flex-direction:column}}.proxy-actions{{direction:rtl}}}}
</style></head><body><div class="background"></div><main class="container"><div class="brand-header"><img src="/static/header.png" alt="Phantom Hubs"></div>
<section class="glass-card"><div class="header"><div class="header-copy"><h1>{title}</h1><p>{html.escape(panel.hero_text)}</p></div><div class="header-labels"><div class="status">{html.escape(panel.active_status_text)}</div><div class="volume-badge">{purchased_volume}</div></div></div>
<div class="stats-grid"><div class="stat-card"><div class="stat-label">{html.escape(panel.used_label)}</div><div class="stat-value">{_format_bytes(used)}</div><div class="progress"><i></i></div></div><div class="stat-card"><div class="stat-label">{html.escape(panel.remaining_label)}</div><div class="stat-value">{_format_bytes(remaining)}</div></div><div class="stat-card"><div class="stat-label">{html.escape(panel.expiry_label)}</div><div class="stat-value">{expire_text}</div></div><div class="stat-card"><div class="stat-label">{html.escape(panel.config_count_label)}</div><div class="stat-value">{len(upstream['lines'])}</div></div></div></section>
<section class="glass-card"><div class="section-title">{html.escape(panel.subscription_title)}</div><div class="subscription-container"><div class="subscription-url">{html.escape(public_url)}</div><button style="background:{panel.copy_button_color}" onclick="copyText(link)">{html.escape(panel.copy_button_text)}</button><button style="background:{panel.qr_button_color}" onclick="showQR(link)">{html.escape(panel.qr_button_text)}</button></div>
{quick_connect}{channel_button}</section>
{preview}<div class="foot">{html.escape(panel.support_text)}</div></main><div id="toast" role="status">{html.escape(panel.copy_success_text)}</div><div id="qr-modal" onclick="this.classList.remove('open')"><div id="qrcode"></div></div>
<script>const link={json.dumps(public_url)};let toastTimer;async function copyText(value){{try{{await navigator.clipboard.writeText(value)}}catch(error){{const area=document.createElement('textarea');area.value=value;document.body.appendChild(area);area.select();document.execCommand('copy');area.remove()}}const toast=document.getElementById('toast');toast.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>toast.classList.remove('show'),1800)}}function showQR(value){{const modal=document.getElementById('qr-modal');const box=document.getElementById('qrcode');box.innerHTML='';new QRCode(box,{{text:value,width:220,height:220}});modal.classList.add('open')}}</script></body></html>"""


async def _render_admin(panel: PanelSettings, notice: str = "", error: str = "") -> str:
    async with async_session() as session:
        result = await session.execute(select(Config).order_by(Config.id.desc()))
        configs = list(result.scalars().all())
    rows = "".join(
        f"""<tr><td>{html.escape(config.service_name or "-")}</td><td>{config.volume_gb or "-"}</td><td><a href="{settings.public_base_url}/token/{quote(config.public_sub_token, safe='')}" target="_blank">بازکردن</a></td><td class="ltr">{html.escape(config.sub_link)}</td><td><form method="post" action="/admin/subscriptions/{config.id}/delete"><button class="danger">حذف</button></form></td></tr>"""
        for config in configs
    ) or "<tr><td colspan='5'>هنوز لینکی ثبت نشده است.</td></tr>"
    flash = f"<div class='notice'>{html.escape(notice)}</div>" if notice else f"<div class='error'>{html.escape(error)}</div>" if error else ""
    checked = {
        "quick": "checked" if panel.show_quick_connect else "",
        "channel": "checked" if panel.show_channel_button else "",
        "preview": "checked" if panel.show_config_preview else "",
        "copy": "checked" if panel.show_config_copy else "",
        "qr": "checked" if panel.show_config_qr else "",
    }
    return f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>مدیریت پنل اشتراک</title><link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet"><style>
*{{box-sizing:border-box;letter-spacing:0}}body{{margin:0;background:#f4f7fb;color:#172033;font-family:Vazirmatn,Tahoma,sans-serif}}main{{max-width:1100px;margin:auto;padding:24px 16px 50px}}header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}}h1{{font-size:25px;margin:0}}h2{{font-size:18px;margin:0 0 16px}}.card{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:20px;margin-bottom:16px;box-shadow:0 8px 24px rgba(15,23,42,.05)}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}}label{{display:grid;gap:6px;color:#64748b;font-size:13px}}input,textarea{{border:1px solid #cbd5e1;border-radius:8px;padding:11px;font:inherit;color:#172033}}textarea{{min-height:88px;resize:vertical}}button{{border:0;border-radius:8px;background:{panel.primary_color};color:white;padding:11px 16px;font:inherit;font-weight:700;cursor:pointer}}.danger{{background:#dc2626;padding:7px 10px}}.wide{{grid-column:1/-1}}.notice,.error{{padding:11px;border-radius:8px;margin-bottom:16px;overflow-wrap:anywhere}}.notice{{background:#dcfce7;color:#166534}}.error{{background:#fee2e2;color:#991b1b}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:10px;border-bottom:1px solid #e2e8f0;text-align:right;vertical-align:middle}}td.ltr{{direction:ltr;text-align:left;max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}a{{color:{panel.primary_color};font-weight:700}}.actions{{display:flex;justify-content:flex-end;margin-top:14px}}.toggle{{display:flex;align-items:center;gap:8px}}@media(max-width:700px){{.grid{{grid-template-columns:1fr}}.wide{{grid-column:auto}}.table-wrap{{overflow:auto}}}}</style></head><body><main><header><div><h1>مدیریت Phantom Subscription</h1><span>ساخته‌شده بر پایه ظاهر marzban-template</span></div><a href="{settings.public_base_url}/health">وضعیت سرویس</a></header>{flash}
<section class="card"><h2>تبدیل دستی لینک ساب</h2><form method="post" action="/admin/subscriptions"><div class="grid"><label class="wide">لینک اصلی سابسکریپشن<input name="upstream_url" type="url" required placeholder="https://example.com/token/..."></label><label>توکن دلخواه، اختیاری<input name="token" placeholder="اگر خالی باشد خودکار ساخته می‌شود"></label><label>نام سرویس<input name="service_name"></label><label>حجم گیگ<input name="volume_gb" type="number" min="0" value="0"></label><label>دسته‌بندی<input name="category_key" value="manual"></label></div><div class="actions"><button>ساخت لینک اختصاصی</button></div></form></section>
<section class="card"><h2>تنظیمات کامل قالب</h2><form method="post" action="/admin/settings"><div class="grid">
<label>نام برند<input name="brand_name" value="{html.escape(panel.brand_name)}"></label><label>آیدی کانال<input name="channel_handle" value="{html.escape(panel.channel_handle)}"></label>
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
<label>متن V2RayNG<input name="v2rayng_button_text" value="{html.escape(panel.v2rayng_button_text)}"></label><label>متن Hiddify<input name="hiddify_button_text" value="{html.escape(panel.hiddify_button_text)}"></label>
<label>متن Streisand<input name="streisand_button_text" value="{html.escape(panel.streisand_button_text)}"></label><label>متن HAPP<input name="happ_button_text" value="{html.escape(panel.happ_button_text)}"></label>
<label>رنگ V2RayNG<input name="v2rayng_button_color" type="color" value="{panel.v2rayng_button_color}"></label><label>رنگ Hiddify<input name="hiddify_button_color" type="color" value="{panel.hiddify_button_color}"></label>
<label>رنگ Streisand<input name="streisand_button_color" type="color" value="{panel.streisand_button_color}"></label><label>رنگ HAPP<input name="happ_button_color" type="color" value="{panel.happ_button_color}"></label>
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
</div><div class="actions"><button>ذخیره تنظیمات</button></div></form></section>
<section class="card"><h2>لینک‌های ثبت‌شده</h2><div class="table-wrap"><table><thead><tr><th>نام</th><th>حجم</th><th>لینک اختصاصی</th><th>لینک اصلی</th><th></th></tr></thead><tbody>{rows}</tbody></table></div></section></main></body></html>"""
