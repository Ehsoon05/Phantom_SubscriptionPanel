#!/usr/bin/env python3

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


OUTPUT = Path("/etc/haproxy/haproxy.cfg")


def main() -> None:
    content = sys.stdin.buffer.read()
    if not content:
        raise SystemExit("No HAProxy configuration was received.")
    if OUTPUT.exists() and OUTPUT.read_bytes() == content:
        print("HAProxy configuration is already current.")
        return

    descriptor, temporary_name = tempfile.mkstemp(
        prefix="haproxy-remote-",
        suffix=".cfg",
        dir=OUTPUT.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        subprocess.run(["haproxy", "-c", "-f", str(temporary)], check=True)
        os.chmod(temporary, 0o644)
        os.replace(temporary, OUTPUT)
        action = "reload" if _service_is_active() else "restart"
        subprocess.run(["systemctl", action, "haproxy.service"], check=True)
    finally:
        temporary.unlink(missing_ok=True)

    print("HAProxy configuration updated and reloaded.")


def _service_is_active() -> bool:
    return subprocess.run(
        ["systemctl", "is-active", "--quiet", "haproxy.service"],
        check=False,
    ).returncode == 0


if __name__ == "__main__":
    main()
