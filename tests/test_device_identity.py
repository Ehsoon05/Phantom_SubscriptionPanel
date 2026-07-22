from __future__ import annotations

import unittest
from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from phantom_subscription_panel.app import (
    _device_fingerprints,
    _enforce_device_limit,
    _is_trackable_device_request,
)
from phantom_subscription_panel.database import Base, Config, SubscriptionDevice


def _request(*, user_agent: str, language: str = "", ip: str = "127.0.0.1", hwid: str = "") -> Request:
    headers = [(b"user-agent", user_agent.encode())]
    if language:
        headers.append((b"accept-language", language.encode()))
    if hwid:
        headers.append((b"x-hwid", hwid.encode()))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/token/test",
            "headers": headers,
            "client": (ip, 12345),
        }
    )


class DeviceIdentityTests(unittest.TestCase):
    def test_language_ip_and_app_version_do_not_create_a_new_device(self) -> None:
        first = _device_fingerprints(
            _request(user_agent="V2Box 10.1.4/iOS 26.0", language="fa-IR", ip="192.0.2.1")
        )
        second = _device_fingerprints(
            _request(user_agent="V2Box 10.1.5/iOS 26.1", language="", ip="198.51.100.2")
        )

        self.assertEqual(first[0], second[0])
        self.assertNotEqual(first[1], second[1])

    def test_explicit_hardware_ids_remain_distinct(self) -> None:
        first = _device_fingerprints(_request(user_agent="Hiddify/4.0", hwid="device-000001"))
        second = _device_fingerprints(_request(user_agent="Hiddify/4.0", hwid="device-000002"))

        self.assertNotEqual(first[0], second[0])
        self.assertEqual(first[4], "explicit")

    def test_happ_install_identifier_remains_distinct(self) -> None:
        first = _device_fingerprints(
            _request(user_agent="Happ/3.26.3/Android/17839452147361875676")
        )
        second = _device_fingerprints(
            _request(user_agent="Happ/3.26.4/Android/17839452147361875677")
        )

        self.assertNotEqual(first[0], second[0])

    def test_preview_bots_are_not_counted_as_devices(self) -> None:
        self.assertFalse(_is_trackable_device_request("TelegramBot (like TwitterBot)"))
        self.assertFalse(_is_trackable_device_request("Google-Read-Aloud"))
        self.assertTrue(_is_trackable_device_request("v2rayNG/1.10.31"))


class DeviceLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.temp_dir.name}/test.db")
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self.temp_dir.cleanup()

    async def _config(self, *, token: str, limit: int) -> Config:
        config = Config(
            volume_gb=10,
            category_key="test",
            sub_link=f"https://example.com/{token}",
            public_sub_token=token,
            device_limit=limit,
        )
        async with self.session_factory() as session:
            session.add(config)
            await session.commit()
        return config

    async def test_legacy_same_client_is_migrated_instead_of_blocked(self) -> None:
        config = await self._config(token="legacy", limit=1)
        old_request = _request(
            user_agent="Streisand/47 CFNetwork/3860.600.12 Darwin/25.5.0",
            language="fa-IR",
            ip="192.0.2.1",
        )
        old_fingerprints = _device_fingerprints(old_request)
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session:
            session.add(
                SubscriptionDevice(
                    public_sub_token=config.public_sub_token,
                    fingerprint=old_fingerprints[1],
                    user_agent=old_fingerprints[2],
                    ip_hint=old_fingerprints[3],
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
            await session.commit()

        new_request = _request(
            user_agent="Streisand/48 CFNetwork/3860.700.1 Darwin/25.6.0",
            language="",
            ip="198.51.100.2",
        )
        with patch("phantom_subscription_panel.app.async_session", self.session_factory):
            await _enforce_device_limit(config, new_request)

        async with self.session_factory() as session:
            devices = list((await session.execute(select(SubscriptionDevice))).scalars().all())
        self.assertEqual(len(devices), 1)
        self.assertTrue(devices[0].fingerprint.startswith("v2:user-agent:"))

    async def test_distinct_explicit_hardware_id_hits_the_limit(self) -> None:
        config = await self._config(token="explicit", limit=1)
        first = _request(user_agent="Hiddify/4.0", hwid="device-000001")
        second = _request(user_agent="Hiddify/4.0", hwid="device-000002")

        with patch("phantom_subscription_panel.app.async_session", self.session_factory):
            await _enforce_device_limit(config, first)
            with self.assertRaises(HTTPException) as raised:
                await _enforce_device_limit(config, second)

        self.assertEqual(raised.exception.status_code, 403)
        async with self.session_factory() as session:
            count = await session.scalar(select(func.count(SubscriptionDevice.id)))
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
