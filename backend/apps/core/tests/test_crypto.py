from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from apps.core.crypto import EncryptionError, decrypt, encrypt, get_fernet


def test_round_trip() -> None:
    token = encrypt("ghp_a_github_token")

    assert token != "ghp_a_github_token"
    assert decrypt(token) == "ghp_a_github_token"


def test_ciphertext_is_not_deterministic() -> None:
    assert encrypt("same input") != encrypt("same input")


def test_unicode_round_trip() -> None:
    assert decrypt(encrypt("支付模块 token")) == "支付模块 token"


def test_other_key_cannot_decrypt() -> None:
    token = encrypt("value")

    with (
        override_settings(FIELD_ENCRYPTION_KEY=Fernet.generate_key().decode()),
        pytest.raises(EncryptionError),
    ):
        decrypt(token)


def test_missing_key_is_rejected() -> None:
    with (
        override_settings(FIELD_ENCRYPTION_KEY=""),
        pytest.raises(ImproperlyConfigured),
    ):
        encrypt("value")


def test_malformed_key_is_rejected() -> None:
    with (
        override_settings(FIELD_ENCRYPTION_KEY="not-a-fernet-key"),
        pytest.raises(ImproperlyConfigured),
    ):
        encrypt("value")


def test_one_key_reuses_one_cipher() -> None:
    """Reading N encrypted rows must not derive the key schedule N times."""
    assert get_fernet() is get_fernet()


def test_a_different_key_gets_its_own_cipher() -> None:
    """Caching must not outlive an override, or tests would share state."""
    original = get_fernet()

    with override_settings(FIELD_ENCRYPTION_KEY=Fernet.generate_key().decode()):
        assert get_fernet() is not original

    assert get_fernet() is original
