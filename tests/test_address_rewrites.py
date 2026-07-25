from __future__ import annotations

import base64
import unittest

from phantom_subscription_panel.app import (
    _automatic_svn_country_rewrites,
    _rewritten_subscription_lines,
    _rewrite_config_line_address,
    _rewrite_svn_fallback_endpoint,
    _rewrite_svn_ws_address,
    _serialize_address_rewrites,
    _subscription_body_with_info_proxies,
    _subscription_body_without_branded_suffixes,
)
from phantom_subscription_panel.database import Config


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

    def test_country_rewrites_are_discovered_from_subscription_content(self) -> None:
        body = base64.b64encode(
            (
                "vless://user@es.sv.temas-bor.ir:22009?security=reality\n"
                "vless://user@de.sv.temas-bor.ir:22006?security=reality\n"
            ).encode()
        )

        self.assertEqual(
            _automatic_svn_country_rewrites(body),
            {
                "de.sv.temas-bor.ir": "de.api.phantomhubs.shop",
                "es.sv.temas-bor.ir": "es.api.phantomhubs.shop",
            },
        )

    def test_direct_svn_hosts_use_dns_only_aliases(self) -> None:
        body = base64.b64encode(
            (
                "vless://user@tun.temas-bor.ir:443?security=reality&type=xhttp\n"
                "trojan://user@WHITE-MTP.jorzel.ir:19302?security=reality&type=xhttp\n"
                "trojan://user@white-mt.jorzel.ir:19302?security=reality&type=xhttp\n"
            ).encode()
        )

        self.assertEqual(
            _automatic_svn_country_rewrites(body),
            {
                "tun.temas-bor.ir": "tun.api.phantomhubs.shop",
                "white-mt.jorzel.ir": "white-mt.api.phantomhubs.shop",
                "white-mtp.jorzel.ir": "white-mtp.api.phantomhubs.shop",
            },
        )

    def test_direct_fastly_ws_ip_uses_stable_alias(self) -> None:
        source = (
            "vless://user-id@151.101.193.54:80"
            "?security=none&type=ws&path=&host=BankMelat.glObal.ssl.faStly.nEt.#Fastly"
        )

        rewritten = _rewrite_svn_ws_address(source)

        self.assertEqual(
            rewritten,
            "vless://user-id@wsr.api.phantomhubs.shop:8080"
            "?security=none&type=ws&path=&host=BankMelat.glObal.ssl.faStly.nEt.#Fastly",
        )

    def test_svn_fallback_relays_rewrite_host_and_port(self) -> None:
        cases = {
            "fi.api.phantomhubs.shop:22010": "fir.api.phantomhubs.shop:2053",
            "tun.api.phantomhubs.shop:443": "dyr.api.phantomhubs.shop:8443",
            "tun.api.phantomhubs.shop:1963": "xhr.api.phantomhubs.shop:2096",
            "tun.api.phantomhubs.shop:2087": "fnr.api.phantomhubs.shop:8880",
            "white-mt.api.phantomhubs.shop:19302": "mtd.api.phantomhubs.shop:2083",
            "white-mtp.api.phantomhubs.shop:19302": "mtpd.api.phantomhubs.shop:2087",
        }

        for source, target in cases.items():
            with self.subTest(source=source):
                line = f"vless://user@{source}?security=reality&type=xhttp#Test"
                self.assertEqual(
                    _rewrite_svn_fallback_endpoint(line),
                    f"vless://user@{target}?security=reality&type=xhttp#Test",
                )

    def test_non_matching_ws_host_is_not_rewritten(self) -> None:
        source = (
            "vless://user-id@192.0.2.10:80"
            "?security=none&type=ws&host=unrelated.example#Other"
        )

        self.assertEqual(_rewrite_svn_ws_address(source), source)

    def test_svn_source_rewrites_ws_even_without_country_nodes(self) -> None:
        source = (
            "vless://user-id@151.101.193.54:80"
            "?security=none&type=ws&host=bankmelat.global.ssl.fastly.net#Fastly\n"
        )
        config = Config(
            sub_link="https://sub.svnteam-max.com:2053/sub/example",
            info_proxies_enabled=False,
        )

        rewritten = _subscription_body_with_info_proxies(
            config,
            {"body": base64.b64encode(source.encode())},
        )

        self.assertIn(
            "@wsr.api.phantomhubs.shop:8080",
            base64.b64decode(rewritten).decode(),
        )

    def test_web_preview_lines_use_the_same_svn_aliases(self) -> None:
        source = (
            "vless://user@es.sv.temas-bor.ir:22009?security=reality\n"
            "vless://user@tun.temas-bor.ir:443?security=reality&type=xhttp\n"
            "vless://user@151.101.193.54:80"
            "?security=none&type=ws&host=bankmelat.global.ssl.fastly.net\n"
        )
        config = Config(sub_link="https://sub.svnteam-max.com/sub/example")
        upstream = {
            "body": base64.b64encode(source.encode()),
            "lines": source.splitlines(),
        }

        lines = _rewritten_subscription_lines(config, upstream)

        self.assertIn("@es.api.phantomhubs.shop:22009", lines[0])
        self.assertIn("@dyr.api.phantomhubs.shop:8443", lines[1])
        self.assertIn("@wsr.api.phantomhubs.shop:8080", lines[2])
