"""One-time phone pairing.

Registers a new virtual phone with a hub and returns the long-lived
Credentials needed for all subsequent runtime control - that runtime control
itself lives in client.py and never touches any of this again afterwards.

The hub's pairing API doesn't document which signing key it expects for a
given step, so a couple of steps below try several candidate keys and keep
whichever one the hub accepts - determined empirically, not guesswork left
in by accident.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import aiohttp
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding

from .crypto import (
    decrypt_control,
    decrypt_pairing_reply,
    encrypt_control,
    encrypt_pairing,
    sign_hmac,
    sign_rsa,
)
from .errors import (
    AmbiguousDeviceError,
    AuthenticationError,
    HubUnreachableError,
    PairingError,
)
from .models import Credentials
from .transport import hub_ssl_context, read_hub_id

_CLOUD_URL = "https://version2.smartdoordevices.com"
_CONTROL_PORT = 8989
_PAIRING_PORT = 8991

_CLIENT_NAME = "Home Assistant"
_PAIRING_HEADERS = {
    "Content-Type": "application/json",
    "sdk": "3.7.0",
    "platform": "android",
}
_CONTROL_HEADERS = {
    "Content-Type": "application/json",
    "version": "2.21.1",
    "app-version": "1.2.3",
}

# DER SubjectPublicKeyInfo header for an uncompressed SECP256R1 point (a
# standard, publicly documented ASN.1 structure - not hub- or vendor-specific)
# - needed because the hub exchanges raw 65-byte EC points, not full SPKI DER.
_P256_SPKI_PREFIX = bytes.fromhex(
    "3059301306072a8648ce3d020106082a8648ce3d030107034200"
)


@dataclass
class _MigratedSecret:
    """Result of the RSA/ECDH key upgrade step: a fresh key pair and secret."""

    private_key_der_b64: str
    secret: str


class PairingSession:
    """Drives the multi-step handshake that pairs a new phone with a hub.

    Holds everything shared across steps as instance state, rather than
    threading a context object through free functions - each step is a
    method that reads/extends `self` as pairing progresses.
    """

    def __init__(self, session: aiohttp.ClientSession, host: str) -> None:
        """Initialize a session for pairing against the hub at `host`."""
        self._session = session
        self._host = host
        self._ssl_context = hub_ssl_context()
        self.hub_id: str = ""
        self.phone_id: str = ""

    def _url(self, port: int) -> str:
        return f"https://{self._host}:{port}"

    async def pair(self, activation_code: str, user_password: str) -> Credentials:
        """Run the full pairing handshake and return runtime Credentials."""
        self.hub_id = await read_hub_id(self._host)

        phone_password, control_secret, user_id = await self._register_with_cloud(
            activation_code, user_password
        )
        await self._verify_credentials(phone_password, user_password)

        pairing_password = secrets.token_urlsafe(24)
        migrated = await self._upgrade_signing_key(
            control_secret, phone_password, user_password, pairing_password
        )

        session_key, device_id = await self._authenticate_pairing_session(
            migrated, user_password, pairing_password
        )
        if session_key:
            _cleared, discovered = await self._clear_password_expiry(
                migrated, session_key, user_password, user_id, pairing_password
            )
            device_id = device_id or discovered

        if not device_id:
            device_id = await self._discover_device_id(
                phone_password, user_password, control_secret
            )
        if not device_id:
            raise PairingError("could not determine this hub's controllable device ID")

        return Credentials(
            hub_id=self.hub_id,
            phone_id=self.phone_id,
            phone_password=phone_password,
            control_secret=control_secret,
            user_password=user_password,
            device_id=device_id,
        )

    async def _register_with_cloud(
        self, activation_code: str, user_password: str
    ) -> tuple[str, str, str]:
        """Exchange an activation code for phone credentials via the vendor's cloud."""
        try:
            async with self._session.post(
                f"{_CLOUD_URL}/app/remoteregister",
                ssl=False,
                timeout=aiohttp.ClientTimeout(total=20),
                json={
                    "bsid": self.hub_id,
                    "remoteRegistrationCode": activation_code,
                    "userPassword": user_password,
                    "phoneName": _CLIENT_NAME,
                    "phoneModel": _CLIENT_NAME,
                },
            ) as response:
                if response.status in (401, 403):
                    raise AuthenticationError(
                        "cloud registration rejected the activation code or password"
                    )
                if response.status != 200:
                    body = await response.text()
                    raise PairingError(
                        f"cloud register failed: HTTP {response.status} {body[:120]}"
                    )
                reply = await response.json(content_type=None)
        except TimeoutError as err:
            raise HubUnreachableError(
                "timed out contacting vendor cloud registration"
            ) from err
        except aiohttp.ClientError as err:
            raise HubUnreachableError(
                f"could not reach vendor cloud registration: {err}"
            ) from err

        self.phone_id = reply["phoneId"]
        return (
            reply["phonePassword"],
            reply.get("phoneSecret", ""),
            str(reply.get("userId", "")),
        )

    async def _verify_credentials(
        self, phone_password: str, user_password: str
    ) -> None:
        """Confirm the hub accepts the freshly cloud-registered credentials."""
        try:
            async with self._session.post(
                f"{self._url(_CONTROL_PORT)}/app/connect",
                ssl=self._ssl_context,
                timeout=aiohttp.ClientTimeout(total=10),
                json={
                    "bsid": self.hub_id,
                    "phoneId": self.phone_id,
                    "phonePassword": phone_password,
                    "userPassword": user_password,
                    "communicationType": 3,
                },
            ) as response:
                if response.status in (401, 403):
                    raise AuthenticationError("hub rejected the newly registered phone")
                if response.status != 200:
                    body = await response.text()
                    raise PairingError(
                        f"connect failed: HTTP {response.status} {body[:200]}"
                    )
        except TimeoutError as err:
            raise HubUnreachableError(
                f"timed out connecting to hub at {self._host}"
            ) from err
        except aiohttp.ClientError as err:
            raise HubUnreachableError(
                f"could not reach hub at {self._host}: {err}"
            ) from err

    async def _upgrade_signing_key(
        self,
        control_secret: str,
        phone_password: str,
        user_password: str,
        pairing_password: str,
    ) -> _MigratedSecret:
        """Register an RSA key pair with the hub and derive a fresh secret via ECDH.

        The RSA key signs every pairing-API request from here on; the ECDH
        exchange with the hub's own EC key produces `secret`, used only for
        the pairing API for the remainder of this handshake.
        """
        rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        rsa_key_der_b64 = base64.b64encode(
            rsa_key.private_bytes(
                serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        ).decode()
        rsa_public_der = rsa_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        # The hub wants the raw RSA public key, not wrapped in a full SPKI structure.
        rsa_public_raw_b64 = base64.b64encode(rsa_public_der[-270:]).decode()

        ec_key = ec.generate_private_key(ec.SECP256R1())
        ec_point = ec_key.public_key().public_numbers()
        ec_public_raw_b64 = base64.b64encode(
            bytes([4]) + ec_point.x.to_bytes(32, "big") + ec_point.y.to_bytes(32, "big")
        ).decode()

        timestamp = int(time.time() * 1000)
        payload = json.dumps(
            {
                "phoneKey": rsa_public_raw_b64,
                "newPhoneSecretPhoneHalf": ec_public_raw_b64,
                "newPhonePassword": pairing_password,
            },
            separators=(",", ":"),
        )
        encrypted_payload = encrypt_control(control_secret, str(timestamp), payload)
        signature = base64.b64encode(
            rsa_key.sign(
                encrypted_payload.encode(), rsa_padding.PKCS1v15(), hashes.SHA512()
            )
        ).decode()

        try:
            async with self._session.post(
                f"{self._url(_CONTROL_PORT)}/app/v3migrate",
                ssl=self._ssl_context,
                headers=_PAIRING_HEADERS,
                timeout=aiohttp.ClientTimeout(total=20),
                json={
                    "bsid": self.hub_id,
                    "phoneId": self.phone_id,
                    "phoneKey": rsa_public_raw_b64,
                    "phonePassword": phone_password,
                    "userPassword": user_password,
                    "data": encrypted_payload,
                    "time": timestamp,
                    "signature": signature,
                },
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    raise PairingError(
                        f"key upgrade failed: HTTP {response.status} {body[:200]}"
                    )
                reply = await response.json(content_type=None)
        except TimeoutError as err:
            raise HubUnreachableError(
                f"timed out upgrading signing key on {self._host}"
            ) from err
        except aiohttp.ClientError as err:
            raise HubUnreachableError(
                f"could not reach hub at {self._host}: {err}"
            ) from err

        secret = control_secret
        migration_b64 = reply.get("migrationData", "")
        if migration_b64:
            try:
                decoded = decrypt_control(
                    control_secret, reply.get("phoneId", self.phone_id), migration_b64
                )
                hub_half_b64 = json.loads(decoded).get("newPhoneSecretHubHalf", "")
                if hub_half_b64:
                    hub_public_key = serialization.load_der_public_key(
                        _P256_SPKI_PREFIX + base64.b64decode(hub_half_b64)
                    )
                    shared_secret = ec_key.exchange(ec.ECDH(), hub_public_key)
                    secret = base64.b64encode(shared_secret).decode()
            except (ValueError, KeyError, TypeError):
                # The upgraded secret is a best-effort improvement; keep the
                # original one if the hub's reply can't be parsed.
                pass

        return _MigratedSecret(private_key_der_b64=rsa_key_der_b64, secret=secret)

    async def _pairing_api_timestamp(self) -> int:
        """Fetch the hub's own clock for the pairing API, falling back to ours."""
        try:
            async with self._session.post(
                f"{self._url(_PAIRING_PORT)}/sdk/info",
                ssl=self._ssl_context,
                headers=_PAIRING_HEADERS,
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

    async def _send_signed_command(
        self,
        migrated: _MigratedSecret,
        command_json: str,
        signing_key: str,
        timestamp: int,
    ) -> dict:
        request_id = "req" + uuid.uuid4().hex[:12]
        encrypted = encrypt_pairing(migrated.secret, str(timestamp), command_json)
        signing_input = (
            f"{self.hub_id}:{self.phone_id}:{timestamp}:{request_id}:{encrypted}"
        )
        mac = (
            "NOKEY" if signing_key == "NOKEY" else sign_hmac(signing_key, signing_input)
        )
        async with self._session.post(
            f"{self._url(_PAIRING_PORT)}/sdk/message",
            ssl=self._ssl_context,
            headers=_PAIRING_HEADERS,
            timeout=aiohttp.ClientTimeout(total=15),
            json={
                "hubId": self.hub_id,
                "phoneId": self.phone_id,
                "requestId": request_id,
                "time": timestamp,
                "request": encrypted,
                "signature": sign_rsa(migrated.private_key_der_b64, signing_input),
                "mac": mac,
            },
        ) as response:
            return await response.json(content_type=None)

    async def _try_signing_keys(
        self,
        migrated: _MigratedSecret,
        command_json: str,
        candidate_keys: Sequence[str],
    ) -> dict:
        """Try each candidate signing key until the hub accepts one.

        The pairing API doesn't document which key a given command expects,
        so this is a deliberate empirical fallback, not a guess left in.
        """
        for key in candidate_keys:
            if not key:
                continue
            try:
                timestamp = await self._pairing_api_timestamp()
                reply = await self._send_signed_command(
                    migrated, command_json, key, timestamp
                )
            except (TimeoutError, aiohttp.ClientError):
                continue
            if reply.get("mac") != "INVALID":
                return reply
        return {}

    async def _authenticate_pairing_session(
        self, migrated: _MigratedSecret, user_password: str, pairing_password: str
    ) -> tuple[str | None, str | None]:
        command = json.dumps(
            {
                "path": "auth",
                "data": {
                    "userPassword": user_password,
                    "phonePassword": pairing_password,
                    "temporary": False,
                },
            },
            separators=(",", ":"),
        )
        reply = await self._try_signing_keys(
            migrated, command, ("NOKEY", pairing_password, migrated.secret)
        )
        return _extract_session_key(reply, migrated.secret)

    async def _clear_password_expiry(
        self,
        migrated: _MigratedSecret,
        session_key: str,
        user_password: str,
        user_id: str,
        pairing_password: str,
    ) -> tuple[bool, str | None]:
        """Clear the hub's "password expired" flag for this user.

        The vendor app forces a password change on first login otherwise;
        this call short-circuits that for a headless client.
        """
        data: dict = {"oldPassword": user_password, "newPassword": user_password}
        if user_id:
            data["userId"] = user_id
        command = json.dumps(
            {"path": "setUserPassword", "data": data}, separators=(",", ":")
        )

        reply = await self._try_signing_keys(
            migrated, command, (session_key, pairing_password, migrated.secret, "NOKEY")
        )
        decoded = _decode_pairing_reply(reply, migrated.secret)
        if decoded.get("errorCode") == 0:
            return True, _device_id_from_payload(decoded)
        return False, None

    async def _discover_device_id(
        self, phone_password: str, user_password: str, control_secret: str
    ) -> str | None:
        """Falls back to listing devices when nothing else yielded a device id."""
        try:
            async with self._session.post(
                f"{self._url(_CONTROL_PORT)}/app/connect",
                ssl=self._ssl_context,
                timeout=aiohttp.ClientTimeout(total=10),
                json={
                    "bsid": self.hub_id,
                    "phoneId": self.phone_id,
                    "phonePassword": phone_password,
                    "userPassword": user_password,
                    "communicationType": 1,
                },
            ) as response:
                if response.status != 200:
                    return None
                connection = await response.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError):
            return None

        timestamp = int(time.time() * 1000)
        encrypted = encrypt_control(control_secret, str(timestamp), "{}")
        signing_input = f"{timestamp}:{encrypted}"
        try:
            async with self._session.post(
                f"{self._url(_CONTROL_PORT)}/app/res/devices/fetch",
                ssl=self._ssl_context,
                headers=_CONTROL_HEADERS,
                timeout=aiohttp.ClientTimeout(total=15),
                json={
                    "bsid": self.hub_id,
                    "sessionId": connection["sessionId"],
                    "time": timestamp,
                    "data": encrypted,
                    "processId": "0",
                    "sessionSig": sign_hmac(connection["sessionSecret"], signing_input),
                    "phoneSig": sign_hmac(control_secret, signing_input),
                    "isEncrypted": True,
                },
            ) as response:
                if response.status != 200:
                    return None
                messages = json.loads(
                    (await response.json(content_type=None)).get("messages", "[]")
                )
        except (TimeoutError, aiohttp.ClientError, ValueError):
            return None

        found: list[tuple[str, str]] = []
        for message in messages:
            if message.get("processState") != 0:
                continue
            try:
                body = json.loads(message.get("data", "{}"))
            except ValueError:
                continue
            for entry in body.get("devices", []):
                device = entry.get("device", {})
                name = entry.get("name") or device.get("name", "")
                device_id = (
                    entry.get("deviceId") or device.get("deviceId") or device.get("id")
                )
                if device_id:
                    found.append((name, str(device_id)))

        if not found:
            return None
        if len(found) > 1:
            raise AmbiguousDeviceError(found)
        return found[0][1]


def _device_id_from_payload(data: dict) -> str | None:
    """The device permissions map is keyed by device ID, e.g. {"cWepe5Rn": {...}}."""
    permissions = data.get("devicePermissions") or (data.get("data") or {}).get(
        "devicePermissions"
    )
    if isinstance(permissions, dict) and permissions:
        return next(iter(permissions))
    return None


def _decode_pairing_reply(reply: dict, secret: str) -> dict:
    """Decode a reply: plain JSON, AES-decrypted, or zero-IV-decrypted + regex."""
    raw = reply.get("response", "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        pass
    try:
        return json.loads(decrypt_pairing_reply(secret, raw))
    except (ValueError, TypeError):
        pass

    try:
        text = decrypt_pairing_reply(secret, raw)
    except (ValueError, TypeError):
        return {"_raw": raw}
    try:
        return json.loads(text)
    except ValueError:
        pass

    salvaged: dict = {}
    if m := re.search(r'"errorCode"\s*:\s*(-?\d+)', text):
        salvaged["errorCode"] = int(m.group(1))
    if m := re.search(r'"state"\s*:\s*(-?\d+)', text):
        salvaged["state"] = int(m.group(1))
    if m := re.search(r'"devicePermissions"\s*:\s*\{"([^"]+)"', text):
        salvaged["devicePermissions"] = {m.group(1): {}}
    return salvaged or {"_raw": raw}


def _extract_session_key(reply: dict, secret: str) -> tuple[str | None, str | None]:
    raw = reply.get("response", "")
    if not raw:
        return None, None

    def _scan(data: dict) -> tuple[str | None, str | None]:
        key = data.get("key") or (data.get("data") or {}).get("key")
        return key or None, _device_id_from_payload(data)

    try:
        key, device_id = _scan(json.loads(raw))
        if key:
            return key, device_id
    except ValueError:
        pass

    try:
        text = decrypt_pairing_reply(secret, raw)
    except (ValueError, TypeError):
        return None, None

    try:
        key, device_id = _scan(json.loads(text))
        if key:
            return key, device_id
    except ValueError:
        pass

    key_match = re.search(r'"key"\s*:\s*"([^"]+)"', text)
    device_match = re.search(r'"devicePermissions"\s*:\s*\{"([^"]+)"', text)
    if key_match:
        return key_match.group(1), (device_match.group(1) if device_match else None)
    return None, None


async def pair_new_phone(
    session: aiohttp.ClientSession, host: str, activation_code: str, user_password: str
) -> Credentials:
    """Pair a new virtual phone with the hub at `host` and return Credentials.

    `session` can be a plain aiohttp.ClientSession - certificate verification
    is disabled per-request for both the cloud call (the vendor's own
    intermediate/leaf certificates are currently expired) and hub calls (the
    hub's certificate is self-signed). Used only once, during this handshake;
    runtime control never talks to the cloud endpoint again.
    """
    return await PairingSession(session, host).pair(activation_code, user_password)
