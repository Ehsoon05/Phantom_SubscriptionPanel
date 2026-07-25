from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


COUNTRY_CODES = (
    "at",
    "be",
    "bg",
    "ca",
    "cz",
    "de",
    "dk",
    "es",
    "fi",
    "fr",
    "ie",
    "in",
    "nl",
    "no",
    "pl",
    "ro",
    "rs",
    "se",
    "sg",
    "us",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Set per-subscription SVN country relay rewrites.")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--target-suffix", default="api.phantomhubs.shop")
    args = parser.parse_args()

    target_suffix = args.target_suffix.strip().lower().strip(".")
    rewrites = {
        f"{code}.sv.temas-bor.ir": f"{code}.{target_suffix}"
        for code in COUNTRY_CODES
    }
    payload = json.dumps(rewrites, ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    connection = sqlite3.connect(args.db)
    try:
        row = connection.execute(
            """
            SELECT id, service_name, panel_username
            FROM subscription_configs
            WHERE public_sub_token = ?
            """,
            (args.token,),
        ).fetchone()
        if row is None:
            raise SystemExit("Subscription token was not found; no changes were made.")

        cursor = connection.execute(
            """
            UPDATE subscription_configs
            SET address_rewrites_json = ?
            WHERE public_sub_token = ?
            """,
            (payload, args.token),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            raise SystemExit(f"Expected one updated row, got {cursor.rowcount}; no changes were committed.")
        connection.commit()
    finally:
        connection.close()

    print(f"Updated subscription id={row[0]} service={row[1]!r} panel_username={row[2]!r}")
    print(f"Configured {len(rewrites)} country address rewrites.")


if __name__ == "__main__":
    main()
