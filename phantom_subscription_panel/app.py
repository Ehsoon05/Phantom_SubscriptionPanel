from __future__ import annotations

import base64
import binascii
import html
import json
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import httpx
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from sqlalchemy import select

from .config import settings
from .database import Base, Config, async_session, engine
from .panel_settings import PanelSettings, load_panel_settings, save_panel_settings


app = FastAPI(title="Phantom Subscription Panel")
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
    channel_handle: str = Form(...),
    hero_text: str = Form(...),
    support_text: str = Form(...),
    copy_button_text: str = Form(...),
    apps_title: str = Form(...),
    show_config_preview: str | None = Form(default=None),
    _: str = Depends(_require_admin),
) -> str:
    panel = PanelSettings(
        brand_name=brand_name.strip() or "Phantom Hubs",
        primary_color=_normalize_color(primary_color, "#426df8"),
        accent_color=_normalize_color(accent_color, "#22c55e"),
        background_color=_normalize_color(background_color, "#0f172a"),
        channel_handle=channel_handle.strip() or "@PhantomHubs",
        hero_text=hero_text.strip(),
        support_text=support_text.strip(),
        copy_button_text=copy_button_text.strip() or "کپی لینک اشتراک",
        apps_title=apps_title.strip() or "اتصال سریع",
        show_config_preview=show_config_preview == "on",
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
    if not settings.sync_token:
        raise HTTPException(status_code=403, detail="PANEL_SYNC_TOKEN is not configured")
    expected = f"Bearer {settings.sync_token}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="Invalid sync token")
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


async def _config_for_token(token: str) -> Config | None:
    async with async_session() as session:
        result = await session.execute(select(Config).where(Config.public_sub_token == token))
        return result.scalar_one_or_none()


async def _fetch_upstream(url: str) -> dict:
    headers = {
        "User-Agent": "v2rayNG/1.10 PhantomSubscriptionPanel/2.0",
        "Accept": "text/plain, application/octet-stream, */*",
        "Cache-Control": "no-cache",
    }
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=settings.request_timeout_seconds,
            verify=settings.upstream_verify_tls,
        ) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Upstream subscription is unavailable: {exc}") from exc

    body = response.content
    if _looks_like_html(body):
        raise HTTPException(status_code=502, detail="Upstream returned an HTML page instead of subscription data")
    return {
        "body": body,
        "content_type": response.headers.get("content-type", "text/plain; charset=utf-8"),
        "forward_headers": {name: response.headers[name] for name in FORWARDED_HEADERS if name in response.headers},
        "lines": _subscription_lines(body),
        "usage": _parse_subscription_userinfo(response.headers.get("subscription-userinfo", "")),
    }


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "").lower()
    user_agent = request.headers.get("user-agent", "").lower()
    vpn_clients = ("v2ray", "clash", "sing-box", "hiddify", "streisand", "shadowrocket", "nekobox", "v2box", "foxray")
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
    config_rows = "".join(
        f"<div class='proxy-item'><span>{html.escape(line[:180])}</span></div>" for line in upstream["lines"][:10]
    )
    preview = (
        f"<section class='glass-card'><div class='section-title'>کانفیگ‌های اشتراک</div><div class='proxy-list'>{config_rows or '<div class=\"empty\">کانفیگ قابل نمایش دریافت نشد.</div>'}</div></section>"
        if panel.show_config_preview else ""
    )
    channel_url = f"https://t.me/{panel.channel_handle.lstrip('@')}"
    title = html.escape(config.service_name or f"{panel.brand_name} {config.volume_gb}GB")
    return f"""<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<style>
*{{box-sizing:border-box;letter-spacing:0}}:root{{--primary:{panel.primary_color};--accent:{panel.accent_color};--bg:{panel.background_color};--glass:rgba(255,255,255,.09);--border:rgba(255,255,255,.18)}}body{{margin:0;min-height:100vh;background:var(--bg);color:#fff;font-family:Vazirmatn,Tahoma,sans-serif}}.background{{position:fixed;inset:0;z-index:-1;background:radial-gradient(circle at 10% 10%,color-mix(in srgb,var(--primary) 35%,transparent),transparent 35%),radial-gradient(circle at 90% 90%,rgba(34,197,94,.18),transparent 36%)}}.container{{max-width:800px;margin:auto;padding:28px 16px 48px}}.glass-card{{background:var(--glass);border:1px solid var(--border);backdrop-filter:blur(14px);border-radius:8px;padding:20px;margin-bottom:18px;box-shadow:0 20px 50px rgba(0,0,0,.2)}}.header{{display:flex;justify-content:space-between;gap:16px;align-items:center}}h1{{font-size:24px;margin:0 0 6px}}p{{color:#cbd5e1;line-height:1.9;margin:0}}.status{{background:rgba(34,197,94,.15);color:#86efac;border:1px solid rgba(34,197,94,.35);padding:8px 12px;border-radius:8px;white-space:nowrap}}.stats-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:18px}}.stat-card{{background:rgba(255,255,255,.06);border:1px solid var(--border);border-radius:8px;padding:16px}}.stat-label{{color:#94a3b8;font-size:13px}}.stat-value{{font-size:19px;font-weight:800;margin-top:6px}}.progress{{height:8px;background:rgba(255,255,255,.1);border-radius:4px;overflow:hidden;margin-top:12px}}.progress i{{display:block;height:100%;width:{percent}%;background:var(--primary)}}.subscription-container{{display:flex;gap:10px;align-items:stretch}}.subscription-url{{direction:ltr;text-align:left;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;padding:13px;background:rgba(255,255,255,.06);border:1px solid var(--border);border-radius:8px;color:#cbd5e1}}button,.link-btn{{border:0;border-radius:8px;padding:12px 15px;background:var(--primary);color:#fff;font:inherit;font-weight:700;cursor:pointer;text-decoration:none;text-align:center}}.btn-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:14px}}.secondary{{background:rgba(255,255,255,.08);border:1px solid var(--border)}}.section-title{{font-weight:800;margin-bottom:12px}}.proxy-list{{display:grid;gap:8px}}.proxy-item{{direction:ltr;text-align:left;background:rgba(255,255,255,.05);padding:10px;border-radius:8px;overflow:hidden}}.proxy-item span{{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#cbd5e1;font-family:monospace}}.empty,.foot{{color:#94a3b8;text-align:center}}#qr-modal{{display:none;position:fixed;inset:0;background:rgba(2,6,23,.9);align-items:center;justify-content:center;z-index:5}}#qr-modal.open{{display:flex}}#qrcode{{background:#fff;padding:16px;border-radius:8px}}@media(max-width:600px){{.header,.subscription-container{{flex-direction:column;align-items:stretch}}.stats-grid,.btn-grid{{grid-template-columns:1fr}}}}
</style></head><body><div class="background"></div><main class="container">
<section class="glass-card"><div class="header"><div><h1>{title}</h1><p>{html.escape(panel.hero_text)}</p></div><div class="status">فعال</div></div>
<div class="stats-grid"><div class="stat-card"><div class="stat-label">حجم مصرف‌شده</div><div class="stat-value">{_format_bytes(used)}</div><div class="progress"><i></i></div></div><div class="stat-card"><div class="stat-label">حجم باقی‌مانده</div><div class="stat-value">{_format_bytes(remaining)}</div></div><div class="stat-card"><div class="stat-label">تاریخ انقضا</div><div class="stat-value">{expire_text}</div></div><div class="stat-card"><div class="stat-label">تعداد کانفیگ</div><div class="stat-value">{len(upstream['lines'])}</div></div></div></section>
<section class="glass-card"><div class="section-title">لینک اشتراک</div><div class="subscription-container"><div class="subscription-url">{html.escape(public_url)}</div><button onclick="copyLink()">{html.escape(panel.copy_button_text)}</button><button class="secondary" onclick="showQR()">QR</button></div>
<div class="section-title" style="margin-top:20px">{html.escape(panel.apps_title)}</div><div class="btn-grid"><a class="link-btn" href="v2rayng://install-config?url={quote(public_url, safe='')}">V2RayNG</a><a class="link-btn secondary" href="hiddify://import/{quote(public_url, safe='')}">Hiddify</a><a class="link-btn secondary" href="streisand://import/{quote(public_url, safe='')}">Streisand</a><a class="link-btn" href="{html.escape(channel_url)}">کانال {html.escape(panel.channel_handle)}</a></div></section>
{preview}<div class="foot">{html.escape(panel.support_text)}</div></main><div id="qr-modal" onclick="this.classList.remove('open')"><div id="qrcode"></div></div>
<script>const link={json.dumps(public_url)};function copyLink(){{navigator.clipboard.writeText(link)}}function showQR(){{const modal=document.getElementById('qr-modal');const box=document.getElementById('qrcode');box.innerHTML='';new QRCode(box,{{text:link,width:220,height:220}});modal.classList.add('open')}}</script></body></html>"""


async def _render_admin(panel: PanelSettings, notice: str = "", error: str = "") -> str:
    async with async_session() as session:
        result = await session.execute(select(Config).order_by(Config.id.desc()))
        configs = list(result.scalars().all())
    rows = "".join(
        f"""<tr><td>{html.escape(config.service_name or "-")}</td><td>{config.volume_gb or "-"}</td><td><a href="{settings.public_base_url}/token/{quote(config.public_sub_token, safe='')}" target="_blank">بازکردن</a></td><td class="ltr">{html.escape(config.sub_link)}</td><td><form method="post" action="/admin/subscriptions/{config.id}/delete"><button class="danger">حذف</button></form></td></tr>"""
        for config in configs
    ) or "<tr><td colspan='5'>هنوز لینکی ثبت نشده است.</td></tr>"
    flash = f"<div class='notice'>{html.escape(notice)}</div>" if notice else f"<div class='error'>{html.escape(error)}</div>" if error else ""
    checked = "checked" if panel.show_config_preview else ""
    return f"""<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>مدیریت پنل اشتراک</title><link href="https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css" rel="stylesheet"><style>
*{{box-sizing:border-box;letter-spacing:0}}body{{margin:0;background:#f4f7fb;color:#172033;font-family:Vazirmatn,Tahoma,sans-serif}}main{{max-width:1100px;margin:auto;padding:24px 16px 50px}}header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}}h1{{font-size:25px;margin:0}}h2{{font-size:18px;margin:0 0 16px}}.card{{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:20px;margin-bottom:16px;box-shadow:0 8px 24px rgba(15,23,42,.05)}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px}}label{{display:grid;gap:6px;color:#64748b;font-size:13px}}input,textarea{{border:1px solid #cbd5e1;border-radius:8px;padding:11px;font:inherit;color:#172033}}textarea{{min-height:88px;resize:vertical}}button{{border:0;border-radius:8px;background:{panel.primary_color};color:white;padding:11px 16px;font:inherit;font-weight:700;cursor:pointer}}.danger{{background:#dc2626;padding:7px 10px}}.wide{{grid-column:1/-1}}.notice,.error{{padding:11px;border-radius:8px;margin-bottom:16px;overflow-wrap:anywhere}}.notice{{background:#dcfce7;color:#166534}}.error{{background:#fee2e2;color:#991b1b}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:10px;border-bottom:1px solid #e2e8f0;text-align:right;vertical-align:middle}}td.ltr{{direction:ltr;text-align:left;max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}a{{color:{panel.primary_color};font-weight:700}}.actions{{display:flex;justify-content:flex-end;margin-top:14px}}.toggle{{display:flex;align-items:center;gap:8px}}@media(max-width:700px){{.grid{{grid-template-columns:1fr}}.wide{{grid-column:auto}}.table-wrap{{overflow:auto}}}}</style></head><body><main><header><div><h1>مدیریت Phantom Subscription</h1><span>ساخته‌شده بر پایه ظاهر marzban-template</span></div><a href="{settings.public_base_url}/health">وضعیت سرویس</a></header>{flash}
<section class="card"><h2>تبدیل دستی لینک ساب</h2><form method="post" action="/admin/subscriptions"><div class="grid"><label class="wide">لینک اصلی سابسکریپشن<input name="upstream_url" type="url" required placeholder="https://example.com/token/..."></label><label>توکن دلخواه، اختیاری<input name="token" placeholder="اگر خالی باشد خودکار ساخته می‌شود"></label><label>نام سرویس<input name="service_name"></label><label>حجم گیگ<input name="volume_gb" type="number" min="0" value="0"></label><label>دسته‌بندی<input name="category_key" value="manual"></label></div><div class="actions"><button>ساخت لینک اختصاصی</button></div></form></section>
<section class="card"><h2>تنظیمات قالب</h2><form method="post" action="/admin/settings"><div class="grid"><label>نام برند<input name="brand_name" value="{html.escape(panel.brand_name)}"></label><label>آیدی کانال<input name="channel_handle" value="{html.escape(panel.channel_handle)}"></label><label>رنگ اصلی<input name="primary_color" type="color" value="{panel.primary_color}"></label><label>رنگ وضعیت<input name="accent_color" type="color" value="{panel.accent_color}"></label><label>رنگ پس‌زمینه<input name="background_color" type="color" value="{panel.background_color}"></label><label>متن دکمه کپی<input name="copy_button_text" value="{html.escape(panel.copy_button_text)}"></label><label class="wide">متن بالای صفحه<textarea name="hero_text">{html.escape(panel.hero_text)}</textarea></label><label class="wide">متن پشتیبانی<textarea name="support_text">{html.escape(panel.support_text)}</textarea></label><label>عنوان برنامه‌ها<input name="apps_title" value="{html.escape(panel.apps_title)}"></label><label class="toggle"><input name="show_config_preview" type="checkbox" {checked}> نمایش پیش‌نمایش کانفیگ‌ها</label></div><div class="actions"><button>ذخیره تنظیمات</button></div></form></section>
<section class="card"><h2>لینک‌های ثبت‌شده</h2><div class="table-wrap"><table><thead><tr><th>نام</th><th>حجم</th><th>لینک اختصاصی</th><th>لینک اصلی</th><th></th></tr></thead><tbody>{rows}</tbody></table></div></section></main></body></html>"""
