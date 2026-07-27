from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy a validated HAProxy config to a relay host.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--identity-file", type=Path, required=True)
    parser.add_argument("--known-hosts-file", type=Path)
    args = parser.parse_args()
    known_hosts_file = args.known_hosts_file or args.identity_file.with_name("known_hosts")

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
            "-o",
            f"UserKnownHostsFile={known_hosts_file}",
            args.host,
        ],
        input=args.config.read_bytes(),
        check=True,
    )


if __name__ == "__main__":
    main()
