"""Symmetric encryption for secrets stored in the database.

Git access tokens and AI provider keys must never be readable from a database
dump, so they are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) using a key
that lives only in the environment.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class EncryptionError(RuntimeError):
    """Raised when a stored ciphertext cannot be decrypted."""


@lru_cache(maxsize=8)
def _fernet_for_key(key: str) -> Fernet:
    """Build (once) the cipher for a key.

    Keyed on the key rather than on nothing so ``override_settings`` in tests still
    takes effect, and cached because constructing a Fernet derives a key schedule —
    doing it per row means listing N connections pays for it N times, since
    ``EncryptedTextField.from_db_value`` decrypts on every read.
    """
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEY is not a valid Fernet key (expected 32 url-safe "
            "base64-encoded bytes)."
        ) from exc


def get_fernet() -> Fernet:
    """The Fernet instance for the configured key."""
    key = str(getattr(settings, "FIELD_ENCRYPTION_KEY", "") or "")
    if not key:
        raise ImproperlyConfigured(
            "FIELD_ENCRYPTION_KEY is not set. Generate one with: "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    return _fernet_for_key(key)


def encrypt(plaintext: str) -> str:
    """Encrypt ``plaintext`` and return the ciphertext as ASCII text."""
    return get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """Decrypt a token produced by :func:`encrypt`."""
    try:
        return get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise EncryptionError(
            "Ciphertext could not be decrypted with the configured FIELD_ENCRYPTION_KEY."
        ) from exc
