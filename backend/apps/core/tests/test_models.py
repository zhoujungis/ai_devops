from __future__ import annotations

from apps.core.crypto import decrypt
from apps.core.models import EncryptedTextField


def test_write_path_encrypts() -> None:
    field = EncryptedTextField()

    prepared = field.get_prep_value("ghp_token")

    assert prepared != "ghp_token"
    assert decrypt(prepared) == "ghp_token"


def test_load_path_decrypts() -> None:
    field = EncryptedTextField()

    prepared = field.get_prep_value("ghp_token")

    assert field.from_db_value(prepared, None, None) == "ghp_token"


def test_empty_values_pass_through() -> None:
    field = EncryptedTextField()

    assert field.get_prep_value(None) is None
    assert field.get_prep_value("") == ""
    assert field.from_db_value(None, None, None) is None
    assert field.from_db_value("", None, None) == ""
