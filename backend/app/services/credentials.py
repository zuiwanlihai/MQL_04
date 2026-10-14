"""Fernet encryption for MT5 account passwords (M1).

The encryption key is derived from ``CREDENTIALS_KEY`` (falling back to
``JWT_SECRET_KEY`` in development) via SHA-256 → urlsafe-base64, so the
stored key material is always a valid 32-byte Fernet key.

Ciphertext is versioned with a ``v1:`` prefix to allow future key
rotation.  Plaintext secrets must never be logged.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

_VERSION_PREFIX = "v1:"
DECRYPT_ERROR_MESSAGE = "凭据解密失败，请重新绑定"


class CredentialDecryptionError(Exception):
    """Raised when a stored credential blob cannot be decrypted."""


def _fernet() -> Fernet:
    seed = settings.CREDENTIALS_KEY or settings.JWT_SECRET_KEY
    key = base64.urlsafe_b64encode(hashlib.sha256(seed.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a plaintext secret into a versioned ciphertext string."""
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _VERSION_PREFIX + token


def decrypt_secret(ciphertext: str) -> str:
    """Reverse :func:`encrypt_secret`.

    Raises :class:`CredentialDecryptionError` with an actionable Chinese
    message when the blob is malformed or was encrypted with another key.
    """
    if not ciphertext.startswith(_VERSION_PREFIX):
        raise CredentialDecryptionError(DECRYPT_ERROR_MESSAGE)
    try:
        raw = _fernet().decrypt(ciphertext[len(_VERSION_PREFIX):].encode("ascii"))
    except InvalidToken as exc:
        raise CredentialDecryptionError(DECRYPT_ERROR_MESSAGE) from exc
    return raw.decode("utf-8")
