from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from .config import settings


@dataclass
class PanelSettings:
    brand_name: str = "Phantom Hubs"
    primary_color: str = "#426df8"
    accent_color: str = "#22c55e"
    background_color: str = "#0f172a"
    card_color: str = "#1e293b"
    text_color: str = "#ffffff"
    muted_text_color: str = "#cbd5e1"
    secondary_button_color: str = "#334155"
    channel_handle: str = "@PhantomHubs"
    hero_text: str = "اشتراک شما آماده است. این لینک را داخل اپلیکیشن کلاینت خود وارد کنید."
    support_text: str = "برای آموزش‌ها و اطلاعیه‌ها عضو کانال شوید."
    active_status_text: str = "فعال"
    used_label: str = "حجم مصرف‌شده"
    remaining_label: str = "حجم باقی‌مانده"
    expiry_label: str = "تاریخ انقضا"
    config_count_label: str = "تعداد کانفیگ"
    subscription_title: str = "لینک اشتراک"
    copy_button_text: str = "کپی لینک اشتراک"
    copy_success_text: str = "با موفقیت کپی شد"
    qr_button_text: str = "QR"
    apps_title: str = "اتصال سریع"
    apps_help_text: str = "بر روی اسم برنامه‌ای که نصب دارید بزنید تا به صورت خودکار داخل برنامه اضافه شود."
    v2rayng_button_text: str = "V2RayNG"
    hiddify_button_text: str = "Hiddify"
    streisand_button_text: str = "Streisand"
    happ_button_text: str = "HAPP"
    channel_button_text: str = "کانال پشتیبانی"
    copy_button_color: str = "#426df8"
    qr_button_color: str = "#334155"
    v2rayng_button_color: str = "#334155"
    hiddify_button_color: str = "#334155"
    streisand_button_color: str = "#334155"
    happ_button_color: str = "#334155"
    channel_button_color: str = "#426df8"
    configs_title: str = "کانفیگ‌های اشتراک"
    config_copy_button_text: str = "کپی"
    config_qr_button_text: str = "QR"
    config_copy_button_color: str = "#426df8"
    config_qr_button_color: str = "#334155"
    empty_configs_text: str = "کانفیگ قابل نمایش دریافت نشد."
    show_quick_connect: bool = True
    show_channel_button: bool = True
    show_config_preview: bool = True
    show_config_copy: bool = True
    show_config_qr: bool = True


def load_panel_settings() -> PanelSettings:
    path = settings.settings_file
    if not path.exists():
        return PanelSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return PanelSettings()
    defaults = asdict(PanelSettings())
    defaults.update({key: value for key, value in data.items() if key in defaults})
    return PanelSettings(**defaults)


def save_panel_settings(panel: PanelSettings) -> None:
    path = settings.settings_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(panel), ensure_ascii=False, indent=2), encoding="utf-8")
