from __future__ import annotations

import base64
import unittest

from phantom_subscription_panel.app import (
    _rewrite_config_line_address,
    _serialize_address_rewrites,
    _subscription_body_without_branded_suffixes,
)


class AddressRewriteTests(unittest.TestCase):
    def test_rewrites_only_the_exact_server_address(self) -> None:
        source = (
            "vless://user-id@es.sv.temas-bor.ir:22009"
            "?security=reality&sni=login.yahoo.com#Spain"
        )

        rewritten = _rewrite_config_line_address(
            source,
            {"es.sv.temas-bor.ir": "svn-es.api.phantomhubs.shop"},
        )

        self.assertEqual(
            rewritten,
            "vless://user-id@svn-es.api.phantomhubs.shop:22009"
            "?security=reality&sni=login.yahoo.com#Spain",
        )

    def test_does_not_rewrite_similar_or_unconfigured_hosts(self) -> None:
        source = "vless://user-id@not-es.sv.temas-bor.ir:22009?security=reality"

        self.assertEqual(
            _rewrite_config_line_address(
                source,
                {"es.sv.temas-bor.ir": "svn-es.api.phantomhubs.shop"},
            ),
            source,
        )

    def test_base64_subscription_remains_base64(self) -> None:
        source = (
            "vless://user-id@es.sv.temas-bor.ir:22009"
            "?security=reality&sni=login.yahoo.com#Spain\n"
        )
        body = base64.b64encode(source.encode())

        rewritten = _subscription_body_without_branded_suffixes(
            body,
            {"es.sv.temas-bor.ir": "svn-es.api.phantomhubs.shop"},
        )

        decoded = base64.b64decode(rewritten).decode()
        self.assertIn("@svn-es.api.phantomhubs.shop:22009", decoded)
        self.assertIn("sni=login.yahoo.com", decoded)

    def test_rule_serialization_ignores_invalid_lines(self) -> None:
        serialized = _serialize_address_rewrites(
            "es.sv.temas-bor.ir=svn-es.api.phantomhubs.shop\n"
            "this is invalid\n"
            "# disabled.example=target.example\n"
        )

        self.assertEqual(
            serialized,
            '{"es.sv.temas-bor.ir":"svn-es.api.phantomhubs.shop"}',
        )
