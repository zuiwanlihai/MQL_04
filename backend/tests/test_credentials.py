"""Unit tests for the credential encryption helpers (plan step 4)."""

import pytest

from app.services.credentials import (
    DECRYPT_ERROR_MESSAGE,
    CredentialDecryptionError,
    decrypt_secret,
    encrypt_secret,
)


def test_encrypt_decrypt_roundtrip() -> None:
    secret = "s3cret-P@ss/中文+符号"
    ciphertext = encrypt_secret(secret)
    assert ciphertext != secret
    assert decrypt_secret(ciphertext) == secret


def test_ciphertext_carries_v1_prefix() -> None:
    ciphertext = encrypt_secret("hunter2")
    assert ciphertext.startswith("v1:")


def test_encryption_is_nondeterministic() -> None:
    # Fernet uses a random IV — same plaintext must not produce same blob.
    assert encrypt_secret("same") != encrypt_secret("same")


def test_decrypt_rejects_garbage_with_chinese_message() -> None:
    with pytest.raises(CredentialDecryptionError) as exc_info:
        decrypt_secret("v1:AAAAAAAAAAAAAAAAAAAAgAAAAA")
    assert str(exc_info.value) == DECRYPT_ERROR_MESSAGE


def test_decrypt_rejects_blob_without_version_prefix() -> None:
    with pytest.raises(CredentialDecryptionError) as exc_info:
        decrypt_secret("not-a-versioned-blob")
    assert str(exc_info.value) == DECRYPT_ERROR_MESSAGE
