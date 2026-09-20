"""Signature verification is pure logic, so it needs no database."""

from __future__ import annotations

from apps.integrations.webhooks import compute_signature, verify_signature


def test_a_correct_signature_verifies() -> None:
    body = b'{"hello":"world"}'

    assert verify_signature(
        secret="s3cret", body=body, signature_header=compute_signature("s3cret", body)
    )


def test_the_signature_covers_the_exact_body() -> None:
    signature = compute_signature("s3cret", b'{"hello":"world"}')

    assert not verify_signature(
        secret="s3cret", body=b'{"hello":"tampered"}', signature_header=signature
    )


def test_a_different_secret_does_not_verify() -> None:
    body = b'{"hello":"world"}'

    assert not verify_signature(
        secret="other", body=body, signature_header=compute_signature("s3cret", body)
    )


def test_missing_secret_or_header_never_verifies() -> None:
    body = b"{}"

    assert not verify_signature(
        secret="", body=body, signature_header=compute_signature("s3cret", body)
    )
    assert not verify_signature(secret="s3cret", body=body, signature_header="")
    assert not verify_signature(secret="s3cret", body=body, signature_header="sha256=deadbeef")


def test_the_signature_matches_rfc_4231() -> None:
    """Known-answer test, so a change of algorithm cannot slip through unnoticed."""
    assert compute_signature("\x0b" * 20, b"Hi There") == (
        "sha256=b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"
    )
