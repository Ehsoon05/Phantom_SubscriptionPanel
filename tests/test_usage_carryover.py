from __future__ import annotations

import unittest

from phantom_subscription_panel.app import _apply_usage_carryover, _sync_profile_title
from phantom_subscription_panel.database import Config


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


class SyncPayloadTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
