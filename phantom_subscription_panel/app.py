from __future__ import annotations

import html
import secrets

import httpx
from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy import select

from .config import settings
from .database import Config, async_session
from .panel_settings import PanelSettings, load_panel_settings, save_panel_settings


app = FastAPI(title="Phantom Subscription Panel")
security = HTTPBasic()


@app.get("/")
async def index() -> RedirectResponse:
    panel = load_panel_settings()
    return RedirectResponse(f"https://t.me/{panel.channel_handle.lstrip('@')}")


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

    upstream = await _fetch_upstream(config.sub_link, request)
    if _wants_html(request):
        return HTMLResponse(_render_subscription_page(config, upstream))

    return Response(
        content=upstream["body"],
        media_type=upstream["content_type"] or "text/plain; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/admin", response_class=HTMLResponse)
async def admin_form(_: str = Depends(_require_admin)) -> str:
    return _render_admin(load_panel_settings())


@app.post("/admin", response_class=HTMLResponse)
async def admin_save(
    brand_name: str = Form(...),
    primary_color: str = Form(...),
    channel_handle: str = Form(...),
    hero_text: str = Form(...),
    support_text: str = Form(...),
    _: str = Depends(_require_admin),
) -> str:
    panel = PanelSettings(
        brand_name=brand_name.strip() or "Phantom Hubs",
        primary_color=_normalize_color(primary_color),
        channel_handle=channel_handle.strip() or "@PhantomHubs",
        hero_text=hero_text.strip(),
        support_text=support_text.strip(),
    )
    save_panel_settings(panel)
    return _render_admin(panel, saved=True)


async def _config_for_token(token: str) -> Config | None:
    async with async_session() as session:
        result = await session.execute(select(Config).where(Config.public_sub_token == token))
        return result.scalar_one_or_none()


async def _fetch_upstream(url: str, request: Request) -> dict[str, str]:
    headers = {
        "User-Agent": request.headers.get("user-agent", "PhantomSubscriptionPanel/1.0"),
        "Accept": request.headers.get("accept", "*/*"),
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

    return {
        "body": response.text,
        "content_type": response.headers.get("content-type", "text/plain; charset=utf-8"),
    }


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    user_agent = request.headers.get("user-agent", "").lower()
    if "text/html" not in accept.lower():
        return False
    vpn_clients = ("v2ray", "clash", "sing-box", "hiddify", "streisand", "shadowrocket", "nekobox", "v2rayng")
    return not any(client in user_agent for client in vpn_clients)


def _require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if not settings.admin_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Set PANEL_ADMIN_PASSWORD before using the admin panel",
        )
    username_ok = secrets.compare_digest(credentials.username, settings.admin_username)
    password_ok = secrets.compare_digest(credentials.password, settings.admin_password)
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _normalize_color(value: str) -> str:
    value = value.strip()
    if len(value) == 6 and not value.startswith("#"):
        value = f"#{value}"
    if len(value) == 7 and value.startswith("#") and all(char in "0123456789abcdefABCDEF" for char in value[1:]):
        return value.lower()
    return "#426df8"


def _render_subscription_page(config: Config, upstream: dict[str, str]) -> str:
    panel = load_panel_settings()
    primary = panel.primary_color
    channel_url = f"https://t.me/{panel.channel_handle.lstrip('@')}"
    configs = _subscription_lines(upstream["body"])
    escaped_title = html.escape(f"{panel.brand_name} {config.volume_gb}GB")
    escaped_channel = html.escape(panel.channel_handle)
    preview_rows = "\n".join(
        f"<li><code>{html.escape(line[:120])}{'...' if len(line) > 120 else ''}</code></li>"
        for line in configs[:8]
    )
    if not preview_rows:
        preview_rows = "<li>لینک را داخل اپلیکیشن کلاینت وارد کنید.</li>"

    return _page_shell(
        title=escaped_title,
        primary=primary,
        body=f"""
        <section class="hero">
          <h1>{escaped_title}</h1>
          <p>{html.escape(panel.hero_text)}</p>
          <div class="actions">
            <a class="btn primary" href="{html.escape(channel_url)}">عضویت در {escaped_channel}</a>
            <a class="btn ghost" href="javascript:navigator.clipboard.writeText(location.href)">کپی لینک اشتراک</a>
          </div>
        </section>
        <section class="grid">
          <div class="card"><div class="metric">{config.volume_gb}</div><div class="label">حجم سرویس / گیگ</div></div>
          <div class="card"><div class="metric">{len(configs)}</div><div class="label">تعداد کانفیگ دریافتی</div></div>
          <div class="card"><div class="metric">فعال</div><div class="label">وضعیت لینک اشتراک</div></div>
        </section>
        <section class="card section">
          <h2>پیش‌نمایش کانفیگ‌ها</h2>
          <ul>{preview_rows}</ul>
        </section>
        <p class="foot">{html.escape(panel.support_text)}</p>
        """,
    )


def _render_admin(panel: PanelSettings, saved: bool = False) -> str:
    saved_box = "<div class='notice'>ذخیره شد.</div>" if saved else ""
    return _page_shell(
        title="Phantom Panel Admin",
        primary=panel.primary_color,
        body=f"""
        <section class="card section admin">
          <h1>تنظیمات ظاهری پنل</h1>
          {saved_box}
          <form method="post">
            <label>نام برند<input name="brand_name" value="{html.escape(panel.brand_name)}"></label>
            <label>رنگ اصلی<input name="primary_color" value="{html.escape(panel.primary_color)}"></label>
            <label>آیدی کانال<input name="channel_handle" value="{html.escape(panel.channel_handle)}"></label>
            <label>متن اصلی<textarea name="hero_text">{html.escape(panel.hero_text)}</textarea></label>
            <label>متن پشتیبانی<textarea name="support_text">{html.escape(panel.support_text)}</textarea></label>
            <button class="submit" type="submit">ذخیره تغییرات</button>
          </form>
        </section>
        """,
    )


def _page_shell(title: str, primary: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{ --primary: {primary}; --ink: #172033; --muted: #667085; --bg: #f6f8ff; --card: #fff; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Tahoma, Arial, sans-serif; background: var(--bg); color: var(--ink); }}
    .shell {{ max-width: 940px; margin: 0 auto; padding: 28px 16px 44px; }}
    .hero {{ padding: 28px; border-radius: 8px; background: linear-gradient(135deg, var(--primary), #2446b8); color: white; }}
    .hero h1, .admin h1 {{ margin: 0 0 12px; font-size: 28px; letter-spacing: 0; }}
    .hero p {{ margin: 0; line-height: 1.9; opacity: .94; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 20px; }}
    .btn {{ display: inline-flex; align-items: center; justify-content: center; min-height: 42px; padding: 0 16px; border-radius: 8px; text-decoration: none; font-weight: 700; }}
    .btn.primary {{ background: white; color: var(--primary); }}
    .btn.ghost {{ color: white; border: 1px solid rgba(255,255,255,.45); }}
    .grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 18px 0; }}
    .card {{ background: var(--card); border: 1px solid #e5e9f5; border-radius: 8px; padding: 16px; box-shadow: 0 10px 28px rgba(23,32,51,.06); }}
    .metric {{ font-size: 24px; font-weight: 800; color: var(--primary); margin-bottom: 6px; }}
    .label, .foot {{ color: var(--muted); font-size: 13px; }}
    .section h2 {{ font-size: 18px; margin: 0 0 12px; }}
    ul {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 8px; }}
    li {{ background: #f8faff; border: 1px solid #edf1ff; border-radius: 8px; padding: 10px; overflow-wrap: anywhere; direction: ltr; text-align: left; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 12px; color: #28344f; }}
    form {{ display: grid; gap: 14px; }}
    label {{ display: grid; gap: 6px; color: var(--muted); }}
    input, textarea {{ width: 100%; border: 1px solid #d8def0; border-radius: 8px; padding: 11px; font: inherit; color: var(--ink); }}
    textarea {{ min-height: 96px; resize: vertical; }}
    .submit {{ border: 0; border-radius: 8px; min-height: 44px; background: var(--primary); color: white; font-weight: 800; cursor: pointer; }}
    .notice {{ background: #eaf8ef; color: #176b35; border-radius: 8px; padding: 10px; margin: 10px 0 16px; }}
    @media (max-width: 720px) {{ .grid {{ grid-template-columns: 1fr; }} .hero h1 {{ font-size: 23px; }} }}
  </style>
</head>
<body><main class="shell">{body}</main></body>
</html>"""


def _subscription_lines(body: str) -> list[str]:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if len(lines) <= 1 and body.strip():
        return [body.strip()]
    return lines
