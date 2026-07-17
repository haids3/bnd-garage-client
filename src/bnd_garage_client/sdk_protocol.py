"""Shared machinery for the hub's SDK-protocol RPC calls (port 8991).

Used by both `pairing.py` (the one-time handshake) and `sdk_client.py`
(post-pairing runtime calls) - the hub doesn't distinguish between them
once a phone is paired, so both send the same signed+encrypted envelope to
the same `sdk/message` endpoint.
"""

from __future__ import annotations

import ssl
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import aiohttp

from .crypto import encrypt_pairing, sign_hmac, sign_rsa

SDK_PORT = 8991
SDK_HEADERS = {
    "Content-Type": "application/json",
    "sdk": "3.7.0",
    "platform": "android",
}


@dataclass
class SdkConnection:
    """Bundles what every SDK-protocol call needs to reach, address, and sign
    a request against a hub for the lifetime of a pairing or runtime session.
    """

    session: aiohttp.ClientSession
    ssl_context: ssl.SSLContext
    host: str
    hub_id: str
    phone_id: str
    rsa_key_der_b64: str
    """Base64 PKCS8 DER-encoded RSA private key signing every request's
    `signature` field - registered with the hub during pairing."""
    secret: str
    """AES key material for `encrypt_pairing`/`decrypt_pairing_reply` - the
    ECDH-upgraded secret from pairing's key-upgrade step."""

    def url(self) -> str:
        """Build the base `sdk/message`-protocol URL for this hub."""
        return f"https://{self.host}:{SDK_PORT}"


async def fetch_hub_timestamp(conn: SdkConnection) -> int:
    """Fetch the hub's own clock for signing, falling back to local time."""
    try:
        async with conn.session.post(
            f"{conn.url()}/sdk/info",
            ssl=conn.ssl_context,
            headers=SDK_HEADERS,
            data="",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            if response.status == 200:
                hub_clock = (await response.json(content_type=None)).get("mono", 0)
                if hub_clock > 0:
                    return int(hub_clock)
    except (TimeoutError, aiohttp.ClientError):
        pass
    return int(time.time() * 1000)


async def send_signed_command(
    conn: SdkConnection, command_json: str, signing_key: str, timestamp: int
) -> dict[str, Any]:
    """POST one signed+encrypted `sdk/message` call and return the raw reply."""
    request_id = "req" + uuid.uuid4().hex[:12]
    encrypted = encrypt_pairing(conn.secret, str(timestamp), command_json)
    signing_input = (
        f"{conn.hub_id}:{conn.phone_id}:{timestamp}:{request_id}:{encrypted}"
    )
    mac = "NOKEY" if signing_key == "NOKEY" else sign_hmac(signing_key, signing_input)
    async with conn.session.post(
        f"{conn.url()}/sdk/message",
        ssl=conn.ssl_context,
        headers=SDK_HEADERS,
        timeout=aiohttp.ClientTimeout(total=15),
        json={
            "hubId": conn.hub_id,
            "phoneId": conn.phone_id,
            "requestId": request_id,
            "time": timestamp,
            "request": encrypted,
            "signature": sign_rsa(conn.rsa_key_der_b64, signing_input),
            "mac": mac,
        },
    ) as response:
        return await response.json(content_type=None)  # type: ignore[no-any-return]


async def try_signing_keys(
    conn: SdkConnection, command_json: str, candidate_keys: Sequence[str]
) -> dict[str, Any]:
    """Try each candidate signing key until the hub accepts one.

    The SDK protocol doesn't document which key a given command expects, so
    this is a deliberate empirical fallback - determined by trial against a
    real hub, not guesswork left in by accident.
    """
    for key in candidate_keys:
        if not key:
            continue
        try:
            timestamp = await fetch_hub_timestamp(conn)
            reply = await send_signed_command(conn, command_json, key, timestamp)
        except (TimeoutError, aiohttp.ClientError):
            continue
        if reply.get("mac") != "INVALID":
            return reply
    return {}
