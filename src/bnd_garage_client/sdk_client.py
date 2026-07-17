"""Runtime access to the hub's wider SDK-protocol RPC surface (port 8991).

This is a second, optional client alongside `client.py`'s control-API
`HubClient` - use it only for RPCs the control API doesn't cover (e.g.
notification history), never for door open/close/stop/status, which stay on
the control API. Requires `Credentials` paired with SDK-protocol support
(`rsa_key_der_b64`/`sdk_secret` populated) - re-pair to obtain them if a
given set of credentials predates that support.
"""

from __future__ import annotations

import json
import re
import time
from types import TracebackType
from typing import Any, Self

import aiohttp

from .crypto import decrypt_pairing_reply, repair_sdk_reply
from .errors import AuthenticationError, HubCommandError
from .models import Credentials, NotificationEntry
from .sdk_protocol import SdkConnection, try_signing_keys
from .transport import hub_ssl_context

_SESSION_MARGIN_SECONDS = 10
"""Re-authenticate this many seconds before the hub's own `expiresIn` would
lapse, to avoid a request racing against expiry mid-flight."""
_MIN_SESSION_SECONDS = 5
"""Floor for the cached-session lifetime, in case the hub ever returns an
`expiresIn` shorter than the margin above."""


class SdkClient:
    """Async client for the hub's SDK-protocol RPC surface, post-pairing."""

    def __init__(
        self,
        host: str,
        credentials: Credentials,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the client for `host`, authenticating with `credentials`.

        Raises AuthenticationError immediately if `credentials` predate
        SDK-protocol support (no RSA key/secret to sign or encrypt with).
        Owns and creates its own `aiohttp.ClientSession` (with the TLS
        settings the hub requires) unless one is passed in, in which case
        the caller is responsible for closing it.
        """
        if not (credentials.rsa_key_der_b64 and credentials.sdk_secret):
            raise AuthenticationError(
                "credentials have no SDK-protocol key material - re-pair to "
                "obtain them"
            )
        self._host = host
        self._creds = credentials
        self._ssl_context = hub_ssl_context()
        self._owns_http_session = session is None
        self._http = session or aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=self._ssl_context)
        )
        self._session_key = ""
        self._session_expires_at = 0.0  # time.monotonic() timestamp

    async def close(self) -> None:
        """Close the underlying HTTP session, if this client owns it."""
        if self._owns_http_session:
            await self._http.close()

    async def __aenter__(self) -> Self:
        """Enter the async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Exit the async context manager, closing an owned HTTP session."""
        await self.close()

    def _conn(self) -> SdkConnection:
        return SdkConnection(
            session=self._http,
            ssl_context=self._ssl_context,
            host=self._host,
            hub_id=self._creds.hub_id,
            phone_id=self._creds.phone_id,
            rsa_key_der_b64=self._creds.rsa_key_der_b64,
            secret=self._creds.sdk_secret,
        )

    def _decode(self, reply: dict[str, Any]) -> dict[str, Any] | None:
        """Decrypt+repair a raw sdk/message reply into its full wrapper dict.

        Falls back to salvaging just `errorCode`/`state` by regex when
        `data` can't be reconstructed (e.g. a Void response) - still enough
        to tell a caller whether a command succeeded.
        """
        raw = reply.get("response", "")
        if not raw:
            return None
        try:
            tail = decrypt_pairing_reply(self._creds.sdk_secret, raw)
        except (ValueError, TypeError):
            return None

        repaired = repair_sdk_reply(tail)
        if repaired is not None:
            return repaired

        salvaged: dict[str, Any] = {}
        if m := re.search(r'"errorCode"\s*:\s*(-?\d+)', tail):
            salvaged["errorCode"] = int(m.group(1))
        if m := re.search(r'"state"\s*:\s*(-?\d+)', tail):
            salvaged["state"] = int(m.group(1))
        return salvaged or None

    async def _ensure_session(self) -> str:
        """Return a valid SDK-protocol session key, authenticating if needed."""
        if self._session_key and time.monotonic() < self._session_expires_at:
            return self._session_key

        command = json.dumps(
            {
                "path": "auth",
                "data": {
                    "userPassword": self._creds.user_password,
                    "phonePassword": self._creds.sdk_phone_password,
                    "temporary": False,
                },
            },
            separators=(",", ":"),
        )
        reply = await try_signing_keys(
            self._conn(), command, (self._creds.sdk_secret,)
        )
        decoded = self._decode(reply)
        if decoded is None or decoded.get("errorCode") != 0:
            raise AuthenticationError("hub rejected the SDK-protocol auth call")

        data = decoded.get("data")
        key = data.get("key") if isinstance(data, dict) else None
        if not key:
            raise AuthenticationError("hub accepted auth but returned no session key")

        expires_in = data.get("expiresIn", 300) if isinstance(data, dict) else 300
        lifetime = max(expires_in - _SESSION_MARGIN_SECONDS, _MIN_SESSION_SECONDS)
        self._session_key = key
        self._session_expires_at = time.monotonic() + lifetime
        return key

    async def call(self, path: str, data: dict[str, Any]) -> Any:
        """Call any SDK-protocol RPC `path` and return its `data` field.

        Raises HubCommandError if the hub reports a nonzero errorCode, or if
        the reply couldn't be decoded at all. Raises AuthenticationError if
        a session couldn't be established. The catalog of valid paths and
        their request/response shapes lives outside this client - the hub
        exposes roughly 80 of them, this method doesn't validate `path`.
        """
        session_key = await self._ensure_session()
        command = json.dumps({"path": path, "data": data}, separators=(",", ":"))
        reply = await try_signing_keys(self._conn(), command, (session_key,))
        decoded = self._decode(reply)
        if decoded is None:
            raise HubCommandError("?", f"could not decode hub reply for {path!r}")

        error_code = decoded.get("errorCode", -1)
        if error_code != 0:
            raise HubCommandError(error_code, f"{path!r} failed")
        return decoded.get("data")

    async def get_notification_history(self) -> list[NotificationEntry]:
        """Fetch this account's full notification history from the hub."""
        if not self._creds.user_id:
            raise AuthenticationError(
                "credentials have no user_id - re-pair to obtain one"
            )
        data = await self.call(
            "getNotificationHistory",
            {"userId": self._creds.user_id, "phoneId": self._creds.phone_id},
        )
        return _notifications_from_data(data)


def _notifications_from_data(data: Any) -> list[NotificationEntry]:
    """Parse getNotificationHistory's `data` field into NotificationEntry list."""
    if not isinstance(data, list):
        return []
    return [
        NotificationEntry(
            sent=bool(entry.get("sent")),
            text=entry.get("text", ""),
            time=entry.get("time", 0),
        )
        for entry in data
    ]
