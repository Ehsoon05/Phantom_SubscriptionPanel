from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy a validated HAProxy config to a relay host.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--identity-file", type=Path, required=True)
    args = parser.parse_args()

    subprocess.run(
        [
            "ssh",
            "-i",
            str(args.identity_file),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=yes",
            args.host,
        ],
        input=args.config.read_bytes(),
        check=True,
    )


if __name__ == "__main__":
    main()
