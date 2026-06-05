from __future__ import annotations

from sqlalchemy import Boolean, Column, Integer, String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


class Config(Base):
    __tablename__ = "configs"

    id = Column(Integer, primary_key=True)
    volume_gb = Column(Integer, nullable=False)
    category_key = Column(String, nullable=False, default="default")
    sub_link = Column(String, nullable=False, unique=True)
    public_sub_token = Column(String, nullable=True, unique=True)
    is_sold = Column(Boolean, default=False)


engine = create_async_engine(settings.phantom_db_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
