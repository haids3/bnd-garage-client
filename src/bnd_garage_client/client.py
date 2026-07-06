"""Runtime control over the hub's session-based control API (port 8989).

Everything here runs after pairing (see pairing.py) has produced Credentials
- this never talks to the vendor's cloud service, only the hub itself on the
LAN.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import TracebackType
from typing import Any, Self

import aiohttp

from .const import (
    CMD_AUXILIARY_RELAY,
    CMD_CLOSE,
    CMD_LIGHT_TOGGLE,
    CMD_OPEN,
    CMD_STOP,
    CONTROL_HEADERS,
    CONTROL_PORT,
    SESSION_LIFETIME_SECONDS,
)
from .crypto import encrypt_control, sign_hmac
from .errors import AuthenticationError, HubCommandError, HubUnreachableError
from .models import (
    ActivityLogEntry,
    Credentials,
    HubStatus,
    PresetAction,
    ToggleState,
    status_from_raw,
)
from .transport import hub_ssl_context

_NON_PRESET_COMMANDS = frozenset((*CMD_LIGHT_TOGGLE, *CMD_AUXILIARY_RELAY))


def _split_features(
    actions: list[dict[str, Any]],
) -> tuple[tuple[PresetAction, ...], ToggleState | None]:
    """Split the hub's feature list into named position presets and the light toggle.

    An "auxiliary" relay slot (a separate pair of toggle commands) also
    appears in this list on hubs that expose it, but is deliberately not
    surfaced here: every hub tested so far accepts commands for it without
    error yet shows no observable effect, so there's nothing to build a
    feature around yet.
    """
    presets: list[PresetAction] = []
    light: ToggleState | None = None
    for action in actions:
        command = action.get("action", {}).get("cmd")
        if command is None:
            continue
        if command in CMD_LIGHT_TOGGLE:
            light = ToggleState(command=command, is_on=command == CMD_LIGHT_TOGGLE[1])
        elif command not in _NON_PRESET_COMMANDS:
            presets.append(PresetAction(command=command, label=action.get("title", "")))
    return tuple(presets), light


def _parse_activity(log: dict[str, Any]) -> ActivityLogEntry | None:
    """Parse the hub's own last-action log entry, if it reported one."""
    if not log:
        return None
    return ActivityLogEntry(
        text=log.get("text", ""),
        log_id=log.get("logId", 0),
        logged_at=log.get("time", 0),
        alert=log.get("alert", 0),
    )


class HubClient:
    """Async client for a paired hub's runtime control API."""

    def __init__(
        self,
        host: str,
        credentials: Credentials,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize the client for `host`, authenticating with `credentials`.

        Owns and creates its own `aiohttp.ClientSession` (with the TLS
        settings the hub requires) unless one is passed in, in which case the
        caller is responsible for closing it.
        """
        self._host = host
        self._credentials = credentials
        self._owns_http_session = session is None
        self._http = session or aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=hub_ssl_context())
        )
        self._session_token = ""
        self._session_secret = ""
        self._session_established_at = 0.0

    @property
    def _base_url(self) -> str:
        return f"https://{self._host}:{CONTROL_PORT}"

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

    async def connect(self) -> None:
        """Establish a control-API session, validating the stored credentials."""
        await self._establish_session()

    async def get_status(self) -> HubStatus:
        """Fetch the hub's current status: door position/rate plus any features."""
        request = json.dumps(
            {"deviceId": self._credentials.device_id}, separators=(",", ":")
        )
        for message in await self._call("app/res/devices/fetch", request):
            if message.get("processState") != 0:
                continue
            body = json.loads(message.get("data", "{}"))
            devices = body.get("devices", [])
            if not devices:
                continue
            device = devices[0].get("device", {})
            presets, light = _split_features(devices[0].get("aux", []))
            activity = _parse_activity(devices[0].get("log", {}))
            return status_from_raw(
                position=device.get("position", -1),
                rate=device.get("rate", 0),
                name=devices[0].get("name", ""),
                presets=presets,
                light=light,
                activity=activity,
            )
        return status_from_raw(position=-1, rate=0)

    async def open_door(self) -> None:
        """Open the garage door."""
        await self.send_command(CMD_OPEN)

    async def close_door(self) -> None:
        """Close the garage door."""
        await self.send_command(CMD_CLOSE)

    async def stop_door(self) -> None:
        """Stop the garage door mid-travel."""
        await self.send_command(CMD_STOP)

    async def send_command(self, command: int) -> None:
        """Send a raw command code - used for the light toggle and any preset.

        `command` values for presets/light come from a prior `get_status()`
        call's `HubStatus.presets`/`.light`, not from anything fixed here.
        """
        request = json.dumps(
            {"deviceId": self._credentials.device_id, "action": {"cmd": command}},
            separators=(",", ":"),
        )
        messages = await self._call("app/res/action", request)
        for message in messages:
            state = message.get("processState", -1)
            if state == 1:
                # Accepted but still processing - the result lands in a
                # follow-up poll rather than this response.
                await asyncio.sleep(1.5)
                for polled in await self._call("app/res/messages", ""):
                    if polled.get("processState") == -1:
                        _raise_for_error(polled)
                return
            if state == -1:
                _raise_for_error(message)
            # state == 0: completed synchronously, nothing further to do.

    async def _establish_session(self) -> None:
        try:
            async with self._http.post(
                f"{self._base_url}/app/connect",
                json={
                    "bsid": self._credentials.hub_id,
                    "phoneId": self._credentials.phone_id,
                    "phonePassword": self._credentials.phone_password,
                    "userPassword": self._credentials.user_password,
                    "communicationType": 1,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status in (401, 403):
                    raise AuthenticationError("hub rejected the stored credentials")
                if response.status != 200:
                    body = await response.text()
                    raise HubUnreachableError(
                        f"connect failed: HTTP {response.status} {body[:150]}"
                    )
                reply = await response.json(content_type=None)
        except TimeoutError as err:
            raise HubUnreachableError(
                f"timed out connecting to hub at {self._host}"
            ) from err
        except aiohttp.ClientError as err:
            raise HubUnreachableError(
                f"could not reach hub at {self._host}: {err}"
            ) from err

        self._session_token = reply["sessionId"]
        self._session_secret = reply["sessionSecret"]
        self._session_established_at = time.monotonic()
        await self._respect_rate_limit(reply.get("data", {}))

    @staticmethod
    async def _respect_rate_limit(data_field: Any) -> None:
        if isinstance(data_field, str):
            try:
                data_field = json.loads(data_field)
            except ValueError:
                data_field = {}
        retry_after_ms = (data_field or {}).get("userAccess", {}).get("nextAccess", 0)
        remaining = retry_after_ms - int(time.time() * 1000)
        if remaining > 0:
            await asyncio.sleep(remaining / 1000.0 + 0.1)

    async def _active_session(self) -> tuple[str, str]:
        age = time.monotonic() - self._session_established_at
        if self._session_token and age < SESSION_LIFETIME_SECONDS:
            return self._session_token, self._session_secret
        await self._establish_session()
        return self._session_token, self._session_secret

    async def _call(self, endpoint: str, request_json: str) -> list[dict[str, Any]]:
        """POST an encrypted, signed request; retry once if the session was rejected."""
        secret = self._credentials.control_secret

        for attempt_is_retry in (False, True):
            session_token, session_secret = await self._active_session()
            timestamp = int(time.time() * 1000)
            encrypted = encrypt_control(secret, str(timestamp), request_json)
            signing_input = f"{timestamp}:{encrypted}"
            body = {
                "bsid": self._credentials.hub_id,
                "sessionId": session_token,
                "time": timestamp,
                "data": encrypted,
                "processId": "0",
                "sessionSig": sign_hmac(session_secret, signing_input),
                "phoneSig": sign_hmac(secret, signing_input),
                "isEncrypted": True,
            }
            try:
                async with self._http.post(
                    f"{self._base_url}/{endpoint}",
                    headers=CONTROL_HEADERS,
                    json=body,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as response:
                    if response.status == 403 and not attempt_is_retry:
                        self._session_token = ""
                        self._session_established_at = 0.0
                        continue
                    if response.status != 200:
                        body_text = await response.text()
                        raise HubUnreachableError(
                            f"{endpoint} HTTP {response.status}: {body_text[:120]}"
                        )
                    reply = await response.json(content_type=None)
            except TimeoutError as err:
                raise HubUnreachableError(f"timed out calling {endpoint}") from err
            except aiohttp.ClientError as err:
                raise HubUnreachableError(
                    f"could not reach hub at {self._host}: {err}"
                ) from err

            return json.loads(reply.get("messages", "[]"))

        raise AuthenticationError(
            f"{endpoint} rejected even after re-establishing a session"
        )


def _raise_for_error(message: dict[str, Any]) -> None:
    try:
        body = json.loads(message.get("data", "{}"))
    except ValueError:
        body = {}
    raise HubCommandError(
        body.get("code", "?"), body.get("description", "unknown error")
    )
