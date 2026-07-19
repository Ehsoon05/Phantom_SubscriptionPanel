from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, UniqueConstraint
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
    telegram_user_id = Column(BigInteger, nullable=True)
    profile_title = Column(String, nullable=True)
    device_limit = Column(Integer, nullable=True)
    device_limit_warning_count = Column(Integer, nullable=False, default=0)
    device_limit_last_warning_at = Column(DateTime, nullable=True)
    show_header = Column(Boolean, nullable=True)
    channel_handle = Column(String, nullable=True)
    show_config_preview = Column(Boolean, nullable=True)
    info_proxies_enabled = Column(Boolean, nullable=True)


class SubscriptionDevice(Base):
    __tablename__ = "subscription_devices"
    __table_args__ = (
        UniqueConstraint("public_sub_token", "fingerprint", name="uq_subscription_device_fingerprint"),
    )

    id = Column(Integer, primary_key=True)
    public_sub_token = Column(String, nullable=False, index=True)
    fingerprint = Column(String, nullable=False)
    user_agent = Column(String, nullable=True)
    ip_hint = Column(String, nullable=True)
    first_seen_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, nullable=False)


engine = create_async_engine(settings.panel_db_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
