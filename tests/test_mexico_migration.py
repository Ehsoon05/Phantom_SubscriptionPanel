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
    _pasarguard_user_id_from_url,
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
        self.assertEqual(
            _normalize_upstream_url("https://my.api.novexgroup.ir:8000/sub/old-token"),
            f"{MEXICO_PANEL_URL}/sub/old-token",
        )

    def test_extracts_pasarguard_user_id_from_subscription_url(self) -> None:
        self.assertEqual(
            _pasarguard_user_id_from_url(
                "https://my.litegames.ir:8000/sub/djMsNTIxMDEsMTc4NzQ5NTE3OA.M9n7I5eCCKXk7P-U2KjT0"
            ),
            52101,
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

    def test_rebinds_panel_db_using_credentials_from_bot_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot_db_path = Path(tmp) / "bot.db"
            panel_db_path = Path(tmp) / "panel.db"
            bot_connection = sqlite3.connect(bot_db_path)
            bot_connection.executescript(
                """
                CREATE TABLE provision_panels (
                    key TEXT PRIMARY KEY,
                    base_url TEXT NOT NULL,
                    username TEXT,
                    password TEXT
                );
                INSERT INTO provision_panels VALUES
                    ('mexico_hajmi', 'https://my.litegames.ir', 'hajmi-user', 'hajmi-pass'),
                    ('mexico_namahdod', 'https://my.litegames.ir', 'namahdod-user', 'namahdod-pass');
                """
            )
            bot_connection.commit()
            bot_connection.close()

            panel_connection = sqlite3.connect(panel_db_path)
            panel_connection.executescript(
                """
                CREATE TABLE subscription_configs (
                    id INTEGER PRIMARY KEY,
                    sub_link TEXT UNIQUE NOT NULL,
                    public_sub_token TEXT UNIQUE NOT NULL,
                    panel_username TEXT,
                    source_panel_key TEXT
                );
                INSERT INTO subscription_configs VALUES
                    (20, 'https://panel.mexicosenter.ir/sub/old-hajmi', 'public-hajmi', 'Ali', 'mexico_hajmi');
                """
            )
            panel_connection.commit()
            panel_connection.close()

            def fake_fetch(username: str, password: str) -> dict[str, dict]:
                if username == "hajmi-user":
                    return {"Ali": {"username": "Ali", "subscription_url": "/sub/new-hajmi"}}
                return {}

            with (
                patch("phantom_subscription_panel.app.PHANTOM_PANEL_DATABASES", (bot_db_path,)),
                patch("phantom_subscription_panel.app.settings.panel_db_url", f"sqlite+aiosqlite:///{panel_db_path}"),
                patch("phantom_subscription_panel.app._fetch_mexico_users", side_effect=fake_fetch),
            ):
                _migrate_mexico_panel_urls()

            panel_connection = sqlite3.connect(panel_db_path)
            try:
                sub_link = panel_connection.execute(
                    "SELECT sub_link FROM subscription_configs WHERE panel_username = 'Ali'"
                ).fetchone()[0]
            finally:
                panel_connection.close()

        self.assertEqual(sub_link, f"{MEXICO_PANEL_URL}/sub/new-hajmi")

    def test_rebinds_panel_subscription_by_old_pasarguard_user_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot_db_path = Path(tmp) / "bot.db"
            panel_db_path = Path(tmp) / "panel.db"
            bot_connection = sqlite3.connect(bot_db_path)
            bot_connection.executescript(
                """
                CREATE TABLE provision_panels (
                    key TEXT PRIMARY KEY,
                    base_url TEXT NOT NULL,
                    username TEXT,
                    password TEXT
                );
                INSERT INTO provision_panels VALUES
                    ('mexico_hajmi', 'https://my.litegames.ir', 'hajmi-user', 'hajmi-pass'),
                    ('mexico_namahdod', 'https://my.litegames.ir', 'namahdod-user', 'namahdod-pass');
                """
            )
            bot_connection.commit()
            bot_connection.close()

            panel_connection = sqlite3.connect(panel_db_path)
            panel_connection.executescript(
                """
                CREATE TABLE subscription_configs (
                    id INTEGER PRIMARY KEY,
                    sub_link TEXT UNIQUE NOT NULL,
                    public_sub_token TEXT UNIQUE NOT NULL,
                    panel_username TEXT,
                    source_panel_key TEXT
                );
                INSERT INTO subscription_configs VALUES
                    (
                        20,
                        'https://my.litegames.ir:8000/sub/djMsNTIxMDEsMTc4NzQ5NTE3OA.M9n7I5eCCKXk7P-U2KjT0',
                        'public-hajmi',
                        NULL,
                        'mexico_hajmi'
                    );
                """
            )
            panel_connection.commit()
            panel_connection.close()

            def fake_fetch_users(username: str, password: str) -> dict[str, dict]:
                if username == "hajmi-user":
                    return {"Ali": {"id": 52101, "username": "Ali", "subscription_url": ""}}
                return {}

            def fake_fetch_user(username: str, panel_keys: tuple[str, ...]) -> tuple[str, str] | None:
                if username == "Ali" and panel_keys == ("mexico_hajmi",):
                    return "mexico_hajmi", f"{MEXICO_PANEL_URL}/sub/new-hajmi"
                return None

            with (
                patch("phantom_subscription_panel.app.PHANTOM_PANEL_DATABASES", (bot_db_path,)),
                patch("phantom_subscription_panel.app.settings.panel_db_url", f"sqlite+aiosqlite:///{panel_db_path}"),
                patch("phantom_subscription_panel.app._fetch_mexico_users", side_effect=fake_fetch_users),
                patch("phantom_subscription_panel.app._fetch_mexico_user", side_effect=fake_fetch_user),
            ):
                _migrate_mexico_panel_urls()

            panel_connection = sqlite3.connect(panel_db_path)
            try:
                sub_link = panel_connection.execute(
                    "SELECT sub_link FROM subscription_configs WHERE public_sub_token = 'public-hajmi'"
                ).fetchone()[0]
            finally:
                panel_connection.close()

        self.assertEqual(sub_link, f"{MEXICO_PANEL_URL}/sub/new-hajmi")


if __name__ == "__main__":
    unittest.main()
