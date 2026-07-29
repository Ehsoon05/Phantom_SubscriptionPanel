from __future__ import annotations

import unittest

from phantom_subscription_panel.app import _apply_usage_carryover
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


if __name__ == "__main__":
    unittest.main()
