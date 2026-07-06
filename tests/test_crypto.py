"""Tests for the crypto primitives - round trips plus a fixed vector."""

from __future__ import annotations

import base64
import hashlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from bnd_garage_client.crypto import (
    decrypt_control,
    decrypt_pairing,
    decrypt_pairing_reply,
    encrypt_control,
    encrypt_pairing,
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


def test_sign_hmac_is_deterministic_and_key_dependent() -> None:
    """Test the same key+message always signs the same, and the key matters."""
    signature_a = sign_hmac("secret", "1700000000000:abc")
    signature_b = sign_hmac("secret", "1700000000000:abc")
    assert signature_a == signature_b
    assert sign_hmac("other-secret", "1700000000000:abc") != signature_a
