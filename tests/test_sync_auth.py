from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from phantom_subscription_panel.app import _require_sync_token


class SyncAuthenticationTests(unittest.TestCase):
    def test_primary_token_is_accepted_for_internal_operations(self) -> None:
        with patch("phantom_subscription_panel.app.settings.sync_token", "primary-token"), patch(
            "phantom_subscription_panel.app.settings.integration_sync_token", "integration-token"
        ):
            _require_sync_token("Bearer primary-token")

    def test_integration_token_only_has_sync_access(self) -> None:
        with patch("phantom_subscription_panel.app.settings.sync_token", "primary-token"), patch(
            "phantom_subscription_panel.app.settings.integration_sync_token", "integration-token"
        ):
            _require_sync_token("Bearer integration-token", allow_integration=True)
            with self.assertRaises(HTTPException) as rejected:
                _require_sync_token("Bearer integration-token")

        self.assertEqual(rejected.exception.status_code, 401)

    def test_blank_integration_token_is_not_accepted(self) -> None:
        with patch("phantom_subscription_panel.app.settings.sync_token", "primary-token"), patch(
            "phantom_subscription_panel.app.settings.integration_sync_token", ""
        ):
            with self.assertRaises(HTTPException) as rejected:
                _require_sync_token("Bearer integration-token", allow_integration=True)

        self.assertEqual(rejected.exception.status_code, 401)
