from __future__ import annotations

import argparse
import base64
import binascii
import os
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx


CONFIG_SCHEMES = ("vless://", "trojan://")


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh HAProxy SVN country relays from a live subscription.")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--upstream-host", default="sub.svnteam-max.com")
    parser.add_argument("--source-suffix", default="sv.temas-bor.ir")
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    endpoints = _fetch_country_endpoints(
        args.db,
        upstream_host=args.upstream_host,
        source_suffix=args.source_suffix,
    )
    if not endpoints:
        raise SystemExit("No valid SVN country endpoints were discovered; last-good HAProxy config was preserved.")

    rendered = _render_haproxy(endpoints)
    changed = _validated_atomic_write(args.output, rendered)
    if changed and args.reload:
        action = "reload" if _service_is_active("haproxy.service") else "restart"
        subprocess.run(["systemctl", action, "haproxy.service"], check=True)

    print(f"Discovered {len(endpoints)} SVN country endpoints.")
    print("HAProxy config updated and reloaded." if changed and args.reload else "HAProxy config is already current.")


def _fetch_country_endpoints(
    db_path: Path,
    *,
    upstream_host: str,
    source_suffix: str,
) -> dict[int, str]:
    urls = _candidate_urls(db_path, upstream_host=upstream_host, source_suffix=source_suffix)
    pattern = re.compile(
        rf"@(?P<host>[a-z]{{2}}\.{re.escape(source_suffix.strip('.').lower())}):(?P<port>\d+)",
        flags=re.IGNORECASE,
    )
    headers = {
        "Accept": "text/plain",
        "User-Agent": "PhantomSVNRelaySync/1.0",
    }
    with httpx.Client(follow_redirects=True, timeout=20, verify=False, headers=headers) as client:
        for url in urls:
            try:
                response = client.get(url)
                response.raise_for_status()
            except httpx.HTTPError:
                continue
            text = _decode_subscription(response.content)
            endpoints: dict[int, str] = {}
            conflict = False
            for match in pattern.finditer(text):
                host = match.group("host").lower()
                port = int(match.group("port"))
                existing = endpoints.get(port)
                if existing and existing != host:
                    conflict = True
                    break
                endpoints[port] = host
            if endpoints and not conflict:
                return dict(sorted(endpoints.items()))
    return {}


def _candidate_urls(db_path: Path, *, upstream_host: str, source_suffix: str) -> list[str]:
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT sub_link, address_rewrites_json
            FROM subscription_configs
            ORDER BY id DESC
            """
        ).fetchall()
    finally:
        connection.close()

    candidates: list[str] = []
    seen: set[str] = set()
    for sub_link, rewrites in rows:
        host = (urlparse(sub_link or "").hostname or "").lower()
        if host != upstream_host.lower() and source_suffix.lower() not in str(rewrites or "").lower():
            continue
        if sub_link and sub_link not in seen:
            seen.add(sub_link)
            candidates.append(sub_link)
        if len(candidates) >= 20:
            break
    return candidates


def _decode_subscription(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace").strip()
    if any(scheme in text.lower() for scheme in CONFIG_SCHEMES):
        return text
    compact = re.sub(r"\s+", "", text)
    try:
        decoded = base64.b64decode(compact + "=" * (-len(compact) % 4), validate=False)
        candidate = decoded.decode("utf-8").strip()
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return ""
    return candidate if any(scheme in candidate.lower() for scheme in CONFIG_SCHEMES) else ""


def _render_haproxy(endpoints: dict[int, str]) -> str:
    sections = [
        """global
    maxconn 50000

defaults
    mode tcp
    option tcpka
    timeout connect 10s
    timeout client 1h
    timeout server 1h
    timeout tunnel 1h

resolvers public_dns
    nameserver cloudflare 1.1.1.1:53
    nameserver google 8.8.8.8:53
    resolve_retries 3
    timeout resolve 2s
    timeout retry 2s
    hold valid 30s
    hold nx 30s
"""
    ]
    for port, host in endpoints.items():
        label = host.split(".", 1)[0]
        sections.append(
            f"""
frontend svn_{label}_{port}
    bind 0.0.0.0:{port}
    default_backend svn_{label}_{port}_origin

backend svn_{label}_{port}_origin
    server origin {host}:{port} check inter 10s fall 3 rise 2 resolvers public_dns resolve-prefer ipv4 init-addr last,libc,none
"""
        )
    return "".join(sections).strip() + "\n"


def _validated_atomic_write(output: Path, content: str) -> bool:
    if output.exists() and output.read_text(encoding="utf-8") == content:
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix="haproxy-svn-", suffix=".cfg", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        subprocess.run(["haproxy", "-c", "-f", str(temporary)], check=True)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _service_is_active(service: str) -> bool:
    return subprocess.run(
        ["systemctl", "is-active", "--quiet", service],
        check=False,
    ).returncode == 0


if __name__ == "__main__":
    main()
