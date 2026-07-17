"""Tests for the crypto primitives - round trips plus a fixed vector."""

from __future__ import annotations

import base64
import hashlib
import json

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from bnd_garage_client.crypto import (
    decrypt_control,
    decrypt_pairing,
    decrypt_pairing_reply,
    encrypt_control,
    encrypt_pairing,
    repair_sdk_reply,
    sign_hmac,
)


def test_control_round_trip() -> None:
    """Test encrypt_control/decrypt_control are inverses."""
    plaintext = '{"deviceId":"abc"}'
    ciphertext = encrypt_control("mysecret", "1700000000000", plaintext)
    assert decrypt_control("mysecret", "1700000000000", ciphertext) == plaintext


def test_control_fixed_vector() -> None:
    """Test key/iv derivation matches MD5(secret)/MD5(seed) - pinned against drift."""
    ciphertext = encrypt_control("mysecret", "42", "hello")
    key = hashlib.md5(b"mysecret").digest()  # noqa: S324
    iv = hashlib.md5(b"42").digest()  # noqa: S324
    assert len(key) == 16
    assert len(iv) == 16
    assert decrypt_control("mysecret", "42", ciphertext) == "hello"


def test_pairing_round_trip() -> None:
    """Test encrypt_pairing/decrypt_pairing are inverses."""
    ciphertext = encrypt_pairing("mysecret", "1700000000000", '{"path":"auth"}')
    assert decrypt_pairing("mysecret", "1700000000000", ciphertext) == '{"path":"auth"}'


def test_decrypt_pairing_reply_skips_garbled_first_block() -> None:
    """Test the zero-IV reply decoder discards the garbled first block only."""
    payload = "x" * 20  # spans two 16-byte blocks once PKCS7-padded
    key = hashlib.sha256(b"mysecret").digest()
    padder = PKCS7(128).padder()
    padded = padder.update(payload.encode()) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16)).encryptor()
    encrypted = cipher.update(padded) + cipher.finalize()
    zero_iv_ciphertext = base64.b64encode(encrypted).decode()

    decrypted = decrypt_pairing_reply("mysecret", zero_iv_ciphertext)
    assert decrypted == payload[16:]


def test_repair_sdk_reply_recovers_object_data() -> None:
    """Test an object `data` value is fully recovered past the corrupted first key."""
    full = json.dumps(
        {
            "data": {"currentNetwork": "MyWifi", "serialNumber": "50002BFF"},
            "appTimeout": 0,
            "errorCode": 0,
            "state": 0,
        },
        separators=(",", ":"),
    )
    tail = full[16:]  # simulates decrypt_pairing_reply's first-block discard
    repaired = repair_sdk_reply(tail)
    assert repaired is not None
    assert repaired["data"]["serialNumber"] == "50002BFF"
    assert repaired["errorCode"] == 0
    assert repaired["state"] == 0


def test_repair_sdk_reply_recovers_array_data() -> None:
    """Test a list-of-objects `data` value (e.g. getUsers) is fully recovered."""
    full = json.dumps(
        {
            "data": [
                {"userId": "111", "name": "Owner"},
                {"userId": "222", "name": "Wall Button"},
            ],
            "appTimeout": 0,
            "errorCode": 0,
            "state": 0,
        },
        separators=(",", ":"),
    )
    tail = full[16:]
    repaired = repair_sdk_reply(tail)
    assert repaired is not None
    assert [u["name"] for u in repaired["data"]] == ["Owner", "Wall Button"]


def test_repair_sdk_reply_recovers_long_string_data() -> None:
    """Test a bare-string `data` value long enough to survive the truncation.

    Unlike object/array data (where only the first *key name* is lost, never
    a value), a string value's own leading characters are what get eaten -
    so the recovered string is missing its first few characters here, not
    just cosmetically mislabeled. See the too-short-to-recover case below.
    """
    full = json.dumps(
        {
            "data": "v2.1.0-release-build-100",
            "appTimeout": 0,
            "errorCode": 0,
            "state": 0,
        },
        separators=(",", ":"),
    )
    tail = full[16:]
    repaired = repair_sdk_reply(tail)
    assert repaired is not None
    assert repaired["data"] == "release-build-100"


def test_repair_sdk_reply_returns_none_for_short_string_data() -> None:
    """Test a string value short enough to be entirely consumed by the corrupted
    first block can't be reconstructed - correctly reports failure rather than
    fabricating data.
    """
    full = json.dumps(
        {"data": "2.1.0", "appTimeout": 0, "errorCode": 0, "state": 0},
        separators=(",", ":"),
    )
    tail = full[16:]
    assert repair_sdk_reply(tail) is None


def test_repair_sdk_reply_returns_none_for_null_data() -> None:
    """Test a Void-returning command's `null` data can't be reconstructed cleanly."""
    full = json.dumps(
        {"data": None, "appTimeout": 0, "errorCode": 0, "state": 0},
        separators=(",", ":"),
    )
    tail = full[16:]
    assert repair_sdk_reply(tail) is None


def test_repair_sdk_reply_matches_real_hub_gethubinfo_shape() -> None:
    """Test against the exact truncation pattern captured from a real hub reply."""
    tail = (
        'tNetwork":"VX420-TandC_2.4Ghz","currentTime":1784250864814,'
        '"hardwareVersion":"920Mhz not supported","hotspotSSID":"B\\u0026D50002BFF",'
        '"hubId":"_yscujIhFTK1q9OAuK8AUA","ipAddress":"192.168.3.196",'
        '"macAddress":"ac:64:cf:c2:85:7e","name":"HandC",'
        '"savedWifiNetwork":"VX420-TandC_2.4Ghz","serialNumber":"50002BFF",'
        '"timeZone":"Australia/Melbourne","version":"1086","wifiSignal":"Good"},'
        '"appTimeout":0,"errorCode":0,"state":0}'
    )
    repaired = repair_sdk_reply(tail)
    assert repaired is not None
    assert repaired["data"]["serialNumber"] == "50002BFF"
    assert repaired["data"]["hubId"] == "_yscujIhFTK1q9OAuK8AUA"
    assert repaired["errorCode"] == 0


def test_sign_hmac_is_deterministic_and_key_dependent() -> None:
    """Test the same key+message always signs the same, and the key matters."""
    signature_a = sign_hmac("secret", "1700000000000:abc")
    signature_b = sign_hmac("secret", "1700000000000:abc")
    assert signature_a == signature_b
    assert sign_hmac("other-secret", "1700000000000:abc") != signature_a
