from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from .config import settings


@dataclass
class PanelSettings:
    brand_name: str = "Phantom Hubs"
    primary_color: str = "#426df8"
    channel_handle: str = "@PhantomHubs"
    hero_text: str = "اشتراک شما آماده است. این لینک را داخل اپلیکیشن کلاینت خود وارد کنید."
    support_text: str = "برای آموزش‌ها و اطلاعیه‌ها عضو کانال شوید."


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
