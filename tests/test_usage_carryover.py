from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from phantom_subscription_panel.app import (
    _apply_usage_carryover,
    _app_title_for_subscription,
    _sync_profile_title,
    _web_title_for_subscription,
    _merge_supplemental_bodies,
    _panel_username_from_upstream,
    _subscription_response_headers,
    _subscription_metadata,
)
from phantom_subscription_panel.database import Config, ConfigSupplement


class UsageCarryoverTests(unittest.TestCase):
    def test_preserves_old_usage_and_total_while_new_usage_grows(self) -> None:
        config = Config(
            sub_link="https://new-panel.example/sub/user",
            usage_offset_bytes=7 * 1024**3,
            display_total_bytes=10 * 1024**3,
        )
        upstream = {
            "usage": {
                "upload": 256 * 1024**2,
                "download": 768 * 1024**2,
                "total": 3 * 1024**3,
                "expire": 1_800_000_000,
            },
            "forward_headers": {
                "subscription-userinfo": "upload=1; download=2; total=3",
            },
        }

        adjusted = _apply_usage_carryover(config, upstream)

        self.assertEqual(adjusted["usage"]["upload"], 7 * 1024**3 + 256 * 1024**2)
        self.assertEqual(adjusted["usage"]["download"], 768 * 1024**2)
        self.assertEqual(adjusted["usage"]["total"], 10 * 1024**3)
        self.assertIn("expire=1800000000", adjusted["forward_headers"]["subscription-userinfo"])
        self.assertEqual(upstream["usage"]["upload"], 256 * 1024**2)

    def test_zeroed_migration_fields_leave_upstream_unchanged(self) -> None:
        config = Config(
            sub_link="https://new-panel.example/sub/user",
            usage_offset_bytes=0,
            display_total_bytes=None,
        )
        upstream = {"usage": {"upload": 1}, "forward_headers": {}}

        self.assertIs(_apply_usage_carryover(config, upstream), upstream)

    def test_explicit_zero_display_total_hides_real_upstream_cap(self) -> None:
        config = Config(
            volume_gb=0,
            sub_link="https://provider.example/sub/unlimited",
            usage_offset_bytes=0,
            display_total_bytes=0,
        )
        upstream = {
            "body": b"",
            "lines": [],
            "title": "Unlimited",
            "usage": {
                "upload": 10 * 1024**3,
                "download": 5 * 1024**3,
                "total": 300 * 1024**3,
                "expire": 0,
            },
            "forward_headers": {},
        }

        adjusted = _apply_usage_carryover(config, upstream)
        metadata = _subscription_metadata(config, adjusted)

        self.assertEqual(adjusted["usage"]["total"], 0)
        self.assertEqual(metadata["total"], 0)
        self.assertEqual(metadata["status"], "active")


class SyncPayloadTests(unittest.TestCase):
    def test_extracts_pasarguard_username_from_information_config(self) -> None:
        username = _panel_username_from_upstream(
            {
                "lines": [
                    "vless://id@example.com:443#%F0%9F%8C%9F%20PhantomHubs-Unlimited%40ameireza%20%2026.9%20GB%2F%E2%88%9E%20%F0%9F%93%8A"
                ]
            }
        )

        self.assertEqual(username, "PhantomHubs-Unlimited@ameireza")

    def test_sync_payload_accepts_explicit_empty_address_rewrites(self) -> None:
        from phantom_subscription_panel.app import ConfigSyncPayload

        payload = ConfigSyncPayload(
            token="public-token",
            upstream_url="https://example.com/sub/user",
            volume_gb=10,
            address_rewrites="",
        )

        self.assertEqual(payload.address_rewrites, "")

    def test_upstream_sync_does_not_overwrite_a_local_profile_title(self) -> None:
        config = Config(profile_title="PhantomHubs VIP", profile_title_locked=True)

        _sync_profile_title(config, "Provider default")

        self.assertEqual(config.profile_title, "PhantomHubs VIP")

    def test_upstream_sync_updates_an_unlocked_profile_title(self) -> None:
        config = Config(profile_title="Old provider title", profile_title_locked=False)

        _sync_profile_title(config, "New provider title")

        self.assertEqual(config.profile_title, "New provider title")

    def test_seller_web_title_uses_the_created_service_name(self) -> None:
        config = Config(
            category_key="seller",
            service_name="Heydari",
            panel_username="Heydari",
            profile_title="Phantom Hubs",
        )

        title = _web_title_for_subscription(
            config,
            {"title": "@Ehsoon05"},
            upstream_web_title="@Ehsoon05",
        )

        self.assertEqual(title, "Heydari")

    def test_regular_web_title_uses_panel_username_before_upstream_page_name(self) -> None:
        config = Config(
            category_key="manual",
            service_name="Local name",
            panel_username="Panel username",
            profile_title="App name",
        )

        title = _web_title_for_subscription(
            config,
            {"title": "Header name"},
            upstream_web_title="Original subscription",
        )

        self.assertEqual(title, "Panel username")

    def test_regular_web_title_ignores_an_upstream_telegram_handle(self) -> None:
        config = Config(category_key="manual", service_name="Express 30GB")

        title = _web_title_for_subscription(
            config,
            {"title": "@Ehsoon05"},
            upstream_web_title="@Ehsoon05",
        )

        self.assertEqual(title, "Express 30GB")

    def test_app_title_ignores_a_telegram_handle(self) -> None:
        config = Config(profile_title="@Ehsoon05", service_name="Express 30GB")

        with patch(
            "phantom_subscription_panel.app.load_panel_settings",
            return_value=SimpleNamespace(subscription_profile_title="", brand_name="Phantom Hubs"),
        ):
            self.assertEqual(
                _app_title_for_subscription(config, {"title": "@Ehsoon05"}),
                "Express 30GB",
            )

    def test_app_title_keeps_an_explicit_locked_handle(self) -> None:
        config = Config(
            profile_title="@LidsoNet",
            profile_title_locked=True,
            service_name="Express 30GB",
        )

        self.assertEqual(
            _app_title_for_subscription(config, {"title": "Provider title"}),
            "@LidsoNet",
        )

    def test_app_title_keeps_the_explicit_panel_default(self) -> None:
        config = Config(service_name="Express 30GB")

        with patch(
            "phantom_subscription_panel.app.load_panel_settings",
            return_value=SimpleNamespace(
                subscription_profile_title="@PhantomHubs",
                brand_name="Phantom Hubs",
            ),
        ):
            self.assertEqual(
                _app_title_for_subscription(config, {"title": "Provider title"}),
                "@PhantomHubs",
            )

    def test_supplement_merge_only_adds_selected_inbound_ports(self) -> None:
        primary = b"vless://primary@example.com:443#Primary\n"
        supplement = ConfigSupplement(
            source_key="rule:1",
            upstream_url="https://panel.example/sub/user",
            allowed_ports_json=json.dumps([8880]),
        )
        upstream = {
            "lines": [
                "vless://info@nasa.com:4241#Info",
                "vless://temp-a@example.net:8880#Temporary",
                "vless://temp-b@example.org:8880#Temporary",
                "vless://other@example.net:7190#Other",
            ]
        }

        merged = _merge_supplemental_bodies(primary, [(supplement, upstream)]).decode()

        self.assertIn("primary@example.com:443", merged)
        self.assertIn("temp-a@example.net:8880", merged)
        self.assertIn("temp-b@example.org:8880", merged)
        self.assertNotIn("nasa.com:4241", merged)
        self.assertNotIn("other@example.net:7190", merged)

    def test_supplement_is_returned_when_primary_subscription_is_empty(self) -> None:
        supplement = ConfigSupplement(
            source_key="rule:1",
            upstream_url="https://panel.example/sub/user",
            allowed_ports_json=json.dumps([8880]),
        )
        upstream = {"lines": ["vless://temp@example.net:8880#Temporary"]}

        merged = _merge_supplemental_bodies(b"", [(supplement, upstream)]).decode()

        self.assertIn("temp@example.net:8880", merged)

    def test_combined_body_gets_its_own_etag(self) -> None:
        config = Config(profile_title="Phantom Hubs")
        upstream = {
            "forward_headers": {
                "etag": '"upstream-etag"',
                "last-modified": "Tue, 04 Aug 2026 12:00:00 GMT",
            },
            "title": "Provider",
        }

        headers = _subscription_response_headers(config, upstream, b"combined-body")

        self.assertNotEqual(headers["ETag"], '"upstream-etag"')
        self.assertNotIn("etag", headers)
        self.assertNotIn("last-modified", headers)

    def test_cached_metadata_marks_expired_volume_as_expired(self) -> None:
        config = Config(volume_gb=10)
        upstream = {
            "usage": {"upload": 4 * 1024**3, "download": 6 * 1024**3, "total": 0},
            "lines": ["vless://example"],
            "title": "Example",
        }

        metadata = _subscription_metadata(config, upstream)

        self.assertEqual(metadata["status"], "expired")
        self.assertEqual(metadata["total"], 10 * 1024**3)


if __name__ == "__main__":
    unittest.main()
