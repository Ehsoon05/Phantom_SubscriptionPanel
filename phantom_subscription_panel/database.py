from __future__ import annotations

from sqlalchemy import Boolean, Column, Integer, String
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


engine = create_async_engine(settings.panel_db_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
