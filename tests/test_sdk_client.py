"""Tests for SdkClient's decode pipeline and notification parsing.

Network calls aren't mocked here, matching this repo's existing convention
(see test_client.py) - the actual HTTP flow is validated against a real hub.
These tests cover the decrypt+repair+salvage pipeline (`_decode`) using real
crypto round trips, and the pure `_notifications_from_data` parser.
"""

from __future__ import annotations

import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from bnd_garage_client.errors import AuthenticationError
from bnd_garage_client.models import Credentials, NotificationEntry
from bnd_garage_client.sdk_client import SdkClient, _notifications_from_data

_SECRET = "sdk-secret"


def _zero_iv_encrypt(secret: str, plaintext: str) -> str:
    """Encrypt like the hub's replies: AES-256-CBC, zero IV, SHA256(secret) key."""
    key = hashlib.sha256(secret.encode()).digest()
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext.encode()) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16)).encryptor()
    return base64.b64encode(cipher.update(padded) + cipher.finalize()).decode()


def _credentials(**overrides: object) -> Credentials:
    base: dict[str, object] = {
        "hub_id": "hub1",
        "phone_id": "phone1",
        "phone_password": "pw",
        "control_secret": "control-secret",
        "user_password": "userpw",
        "devices": ("dev1",),
        "rsa_key_der_b64": "rsa-key",
        "sdk_secret": _SECRET,
        "sdk_phone_password": "sdk-pw",
        "user_id": "user1",
    }
    base.update(overrides)
    return Credentials(**base)  # type: ignore[arg-type]


def _client() -> SdkClient:
    # No network call happens in __init__, so any placeholder session is fine.
    return SdkClient("192.0.2.1", _credentials(), session=object())  # type: ignore[arg-type]


def test_init_rejects_credentials_without_sdk_key_material() -> None:
    """Test credentials paired before SDK-protocol support existed are rejected."""
    creds = _credentials(rsa_key_der_b64="", sdk_secret="")
    with pytest.raises(AuthenticationError):
        SdkClient("192.0.2.1", creds, session=object())  # type: ignore[arg-type]


def test_decode_recovers_full_object_data() -> None:
    """Test a real (zero-IV-corrupted) object reply is fully decoded.

    Uses a first-field name long enough to survive the corrupted first AES
    block intact (see test_crypto.py's short-string-data test for what
    happens when it isn't) - "sessionKey" rather than "key".
    """
    client = _client()
    plaintext = json.dumps(
        {
            "data": {"sessionKey": "sess123", "expiresIn": 300},
            "errorCode": 0,
            "state": 0,
        },
        separators=(",", ":"),
    )
    reply = {"response": _zero_iv_encrypt(_SECRET, plaintext)}
    decoded = client._decode(reply)
    assert decoded is not None
    assert decoded["data"]["expiresIn"] == 300
    assert decoded["errorCode"] == 0


def test_decode_salvages_error_code_when_data_unrecoverable() -> None:
    """Test a null-data reply still yields errorCode/state via salvage fallback."""
    client = _client()
    plaintext = json.dumps(
        {"data": None, "appTimeout": 0, "errorCode": 0, "state": 0},
        separators=(",", ":"),
    )
    reply = {"response": _zero_iv_encrypt(_SECRET, plaintext)}
    decoded = client._decode(reply)
    assert decoded is not None
    assert decoded.get("errorCode") == 0
    assert "data" not in decoded


def test_decode_returns_none_for_empty_reply() -> None:
    """Test a reply with no `response` field decodes to None."""
    client = _client()
    assert client._decode({}) is None


def test_notifications_from_data_parses_real_shape() -> None:
    """Test the exact shape documented for getNotificationHistory."""
    data = [
        {"sent": True, "text": "Door opened", "time": 1700000000000},
        {"sent": False, "text": "Door left open", "time": 1700000005000},
    ]
    assert _notifications_from_data(data) == [
        NotificationEntry(sent=True, text="Door opened", time=1700000000000),
        NotificationEntry(sent=False, text="Door left open", time=1700000005000),
    ]


def test_notifications_from_data_handles_non_list() -> None:
    """Test a non-list `data` (e.g. None from an unrecoverable reply) yields []."""
    assert _notifications_from_data(None) == []
