"""Cryptographic primitives the hub's two APIs require.

Organized by which API a function serves rather than by algorithm name: the
control API (port 8989) uses AES-128-CBC with MD5-derived key material, and
the pairing API (port 8991) uses AES-256-CBC with SHA-256-derived key
material plus RSA/ECDH for the one-time handshake. Both choices come from the
hub's own firmware, not from this client.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


def _cbc_encrypt(key: bytes, iv: bytes, plaintext: str) -> str:
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext.encode()) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(cipher.update(padded) + cipher.finalize()).decode()


def _cbc_decrypt(key: bytes, iv: bytes, ciphertext_b64: str) -> str:
    raw = base64.b64decode(ciphertext_b64)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = cipher.update(raw) + cipher.finalize()
    unpadder = PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode()


def encrypt_control(secret: str, iv_seed: str, plaintext: str) -> str:
    """AES-128-CBC encrypt for the control API: key=MD5(secret), iv=MD5(iv_seed)."""
    key = hashlib.md5(secret.encode()).digest()  # noqa: S324
    iv = hashlib.md5(iv_seed.encode()).digest()  # noqa: S324
    return _cbc_encrypt(key, iv, plaintext)


def decrypt_control(secret: str, iv_seed: str, ciphertext_b64: str) -> str:
    """AES-128-CBC decrypt for the control API - inverse of encrypt_control()."""
    key = hashlib.md5(secret.encode()).digest()  # noqa: S324
    iv = hashlib.md5(iv_seed.encode()).digest()  # noqa: S324
    return _cbc_decrypt(key, iv, ciphertext_b64)


def encrypt_pairing(secret: str, iv_seed: str, plaintext: str) -> str:
    """AES-256-CBC encrypt for the pairing API: key=SHA256(secret), iv=SHA256(seed)."""
    key = hashlib.sha256(secret.encode()).digest()
    iv = hashlib.sha256(iv_seed.encode()).digest()[:16]
    return _cbc_encrypt(key, iv, plaintext)


def decrypt_pairing(secret: str, iv_seed: str, ciphertext_b64: str) -> str:
    """AES-256-CBC decrypt for the pairing API - inverse of encrypt_pairing()."""
    key = hashlib.sha256(secret.encode()).digest()
    iv = hashlib.sha256(iv_seed.encode()).digest()[:16]
    return _cbc_decrypt(key, iv, ciphertext_b64)


def decrypt_pairing_reply(secret: str, ciphertext_b64: str) -> str:
    """Decrypt a hub-originated pairing-API reply, which always uses a zero IV.

    The hub never sends the true IV back, so with a zero IV the first
    plaintext block comes out as noise (XORed against the wrong value) while
    every later block is still correct - CBC chains each block off the
    *previous ciphertext* block, not the IV. The caller gets back everything
    after that first garbled 16-byte block.
    """
    key = hashlib.sha256(secret.encode()).digest()
    raw = base64.b64decode(ciphertext_b64)
    cipher = Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16)).decryptor()
    plaintext = cipher.update(raw) + cipher.finalize()
    pad_len = plaintext[-1]
    if 1 <= pad_len <= 16:
        plaintext = plaintext[:-pad_len]
    return plaintext[16:].decode("utf-8", errors="replace")


_DATA_VALUE_OPENERS = ('{"', '[{"', '"')
"""Candidate openings for the `data` value's true type: object, array of
objects (every list-returning RPC path returns a list of objects), or a
bare string. Tried in this order against `repair_sdk_reply`'s input."""


def repair_sdk_reply(tail: str) -> dict[str, Any] | None:
    """Reconstruct the full `{"data": ..., "errorCode": ..., ...}` wrapper
    from a reply already run through `decrypt_pairing_reply`.

    That function discards the garbled first AES block, but for a *reply*
    (as opposed to a request this client sends) that block always contains
    the wrapper's opening `{"data":` plus the first few characters of the
    `data` value itself - so `tail` starts mid-way through data's first
    field name (object/array) or its content (a bare string), never at a
    clean boundary. Everything else in the reply decrypted correctly,
    including data's own closing brace/bracket and the trailing
    `"appTimeout":...,"errorCode":...,"state":...}` fields - so restoring
    valid JSON only requires guessing which of those three shapes `data`
    had. The wrong guesses simply fail to parse (unbalanced brackets or a
    stray token) rather than silently producing incorrect data.

    For object/array data, only the first field's *key name* is lost (every
    value survives intact). For a bare string value, it's the string's own
    leading characters that get eaten instead - so a recovered string is
    missing several characters at its start, and one short enough to be
    entirely consumed by the corrupted block can't be recovered at all.

    Returns None if no candidate parses - meaning `data` was `null`/absent
    (e.g. a Void-returning command), the value was too short to survive,
    or something unexpected happened; callers should fall back to
    salvaging individual fields by regex.
    """
    for opener in _DATA_VALUE_OPENERS:
        try:
            parsed = json.loads(f'{{"data":{opener}{tail}')
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def sign_hmac(key: str, message: str) -> str:
    """base64(HMAC-SHA256(key, message)) - used for every request signature field."""
    return base64.b64encode(
        hmac.new(key.encode(), message.encode(), hashlib.sha256).digest()
    ).decode()


def sign_rsa(private_key_der_b64: str, message: str) -> str:
    """RSA-SHA512-PKCS1v15 sign, using a base64 PKCS8 DER-encoded private key."""
    private_key = serialization.load_der_private_key(
        base64.b64decode(private_key_der_b64), password=None
    )
    signature = private_key.sign(
        message.encode(), rsa_padding.PKCS1v15(), hashes.SHA512()
    )
    return base64.b64encode(signature).decode()
