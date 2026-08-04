from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


class Config(Base):
    __tablename__ = "subscription_configs"

    id = Column(Integer, primary_key=True)
    volume_gb = Column(Integer, nullable=False)
    category_key = Column(String, nullable=False, default="default")
    sub_link = Column(String, nullable=False, unique=True)
    public_sub_token = Column(String, nullable=False, unique=True)
    is_sold = Column(Boolean, default=False)
    service_name = Column(String, nullable=True)
    panel_username = Column(String, nullable=True)
    source_panel_key = Column(String, nullable=True, index=True)
    telegram_user_id = Column(BigInteger, nullable=True)
    usage_offset_bytes = Column(BigInteger, nullable=False, default=0)
    display_total_bytes = Column(BigInteger, nullable=True)
    profile_title = Column(String, nullable=True)
    # A title explicitly chosen in the subscription admin must survive source
    # replacements and routine upstream synchronisation.
    profile_title_locked = Column(Boolean, nullable=False, default=False)
    device_limit = Column(Integer, nullable=True)
    device_limit_warning_count = Column(Integer, nullable=False, default=0)
    device_limit_last_warning_at = Column(DateTime, nullable=True)
    show_header = Column(Boolean, nullable=True)
    channel_handle = Column(String, nullable=True)
    show_config_preview = Column(Boolean, nullable=True)
    info_proxies_enabled = Column(Boolean, nullable=True)
    address_rewrites_json = Column(String, nullable=True)


class SubscriptionDevice(Base):
    __tablename__ = "subscription_devices"
    __table_args__ = (
        UniqueConstraint("public_sub_token", "fingerprint", name="uq_subscription_device_fingerprint"),
    )

    id = Column(Integer, primary_key=True)
    public_sub_token = Column(String, nullable=False, index=True)
    fingerprint = Column(String, nullable=False)
    fingerprint_aliases_json = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    ip_hint = Column(String, nullable=True)
    first_seen_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, nullable=False)


class ConfigSupplement(Base):
    __tablename__ = "subscription_config_supplements"
    __table_args__ = (
        UniqueConstraint("config_id", "source_key", name="uq_config_supplement_source"),
    )

    id = Column(Integer, primary_key=True)
    config_id = Column(Integer, ForeignKey("subscription_configs.id"), nullable=False, index=True)
    source_key = Column(String, nullable=False)
    label = Column(String, nullable=True)
    upstream_url = Column(String, nullable=False)
    allowed_ports_json = Column(String, nullable=True)


engine = create_async_engine(settings.panel_db_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
