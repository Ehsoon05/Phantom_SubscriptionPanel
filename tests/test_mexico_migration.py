from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phantom_subscription_panel.app import (
    MEXICO_PANEL_URL,
    _migrate_mexico_panel_urls,
    _normalize_upstream_url,
)


class MexicoMigrationTests(unittest.TestCase):
    def test_normalizes_legacy_mexico_hosts(self) -> None:
        self.assertEqual(
            _normalize_upstream_url("https://my.litegames.ir:8000/sub/old-token"),
            f"{MEXICO_PANEL_URL}/sub/old-token",
        )
        self.assertEqual(
            _normalize_upstream_url("https://panel.mexicosenter.ir/sub/old-token"),
            f"{MEXICO_PANEL_URL}/sub/old-token",
        )

    def test_rebinds_bot_and_panel_subscriptions_to_new_panel_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "phantom.db"
            connection = sqlite3.connect(db_path)
            connection.executescript(
                """
                CREATE TABLE provision_panels (
                    key TEXT PRIMARY KEY,
                    base_url TEXT NOT NULL,
                    username TEXT,
                    password TEXT
                );
                CREATE TABLE configs (
                    id INTEGER PRIMARY KEY,
                    sub_link TEXT UNIQUE NOT NULL,
                    panel_username TEXT,
                    panel_key TEXT
                );
                CREATE TABLE subscription_configs (
                    id INTEGER PRIMARY KEY,
                    sub_link TEXT UNIQUE NOT NULL,
                    public_sub_token TEXT UNIQUE NOT NULL,
                    panel_username TEXT,
                    source_panel_key TEXT
                );
                INSERT INTO provision_panels VALUES
                    ('mexico_hajmi', 'https://my.litegames.ir', 'hajmi-user', 'hajmi-pass'),
                    ('mexico_namahdod', 'https://my.litegames.ir', 'namahdod-user', 'namahdod-pass');
                INSERT INTO configs VALUES
                    (10, 'https://my.litegames.ir/sub/old-hajmi', 'Ali', 'mexico_hajmi'),
                    (11, 'https://my.litegames.ir/sub/old-unlimited', 'Sara', 'mexico_namahdod');
                INSERT INTO subscription_configs VALUES
                    (20, 'https://panel.mexicosenter.ir/sub/old-hajmi', 'public-hajmi', 'Ali', 'mexico_hajmi'),
                    (21, 'https://panel.mexicosenter.ir/sub/old-unlimited', 'public-unlimited', 'Sara', 'mexico_namahdod');
                """
            )
            connection.commit()
            connection.close()

            def fake_fetch(username: str, password: str) -> dict[str, dict]:
                if username == "hajmi-user":
                    return {"Ali": {"username": "Ali", "subscription_url": "/sub/new-hajmi"}}
                if username == "namahdod-user":
                    return {"Sara": {"username": "Sara", "subscription_url": "/sub/new-unlimited"}}
                return {}

            with (
                patch("phantom_subscription_panel.app.PHANTOM_PANEL_DATABASES", (db_path,)),
                patch("phantom_subscription_panel.app.settings.panel_db_url", "sqlite+aiosqlite:////missing/panel.db"),
                patch("phantom_subscription_panel.app._fetch_mexico_users", side_effect=fake_fetch),
            ):
                _migrate_mexico_panel_urls()

            connection = sqlite3.connect(db_path)
            try:
                panel_urls = dict(connection.execute("SELECT key, base_url FROM provision_panels").fetchall())
                bot_urls = dict(connection.execute("SELECT panel_username, sub_link FROM configs").fetchall())
                panel_sub_urls = dict(
                    connection.execute("SELECT panel_username, sub_link FROM subscription_configs").fetchall()
                )
            finally:
                connection.close()

        self.assertEqual(panel_urls["mexico_hajmi"], MEXICO_PANEL_URL)
        self.assertEqual(panel_urls["mexico_namahdod"], MEXICO_PANEL_URL)
        self.assertEqual(bot_urls["Ali"], f"{MEXICO_PANEL_URL}/sub/new-hajmi")
        self.assertEqual(bot_urls["Sara"], f"{MEXICO_PANEL_URL}/sub/new-unlimited")
        self.assertEqual(panel_sub_urls["Ali"], f"{MEXICO_PANEL_URL}/sub/new-hajmi")
        self.assertEqual(panel_sub_urls["Sara"], f"{MEXICO_PANEL_URL}/sub/new-unlimited")


if __name__ == "__main__":
    unittest.main()
