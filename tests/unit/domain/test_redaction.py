from __future__ import annotations

import base64
import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sentinelai.domain.redaction import redact_text, redact_value


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


# Fixtures are assembled at runtime so the repository never contains secret-shaped literals
# (keeps the secret scanner meaningful: any finding in the tree is a real problem).
_JWT = ".".join((_b64url(json.dumps({"alg": "HS256"}).encode()),
                 _b64url(json.dumps({"sub": "test-user"}).encode()),
                 _b64url(b"not-a-real-signature-0123456789")))  # fmt: skip
_KEY_HEADER = "-----BEGIN " + "RSA PRIVATE KEY-----"
_KEY_FOOTER = "-----END " + "RSA PRIVATE KEY-----"

SECRETS = {
    "jwt": f"session {_JWT} expired",
    "bearer": "Authorization: Bearer abcdef0123456789xyz",
    "api_key": "key gsk_" + "A" * 40,
    "email": "contact jane.doe@example.com now",
    "url_credentials": "postgresql://shop:hunter2pass@db:5432/shop",
    "secret_assignment": "login failed password=s3cr3t-Value user=bob",
    "card": "paid with 4111 1111 1111 1111 yesterday",
    "private_key": f"{_KEY_HEADER}\nMIIBOgIBAAJBAK\n{_KEY_FOOTER}",
}  # fmt: skip
LEAKED = {
    "jwt": _JWT.rsplit(".", 1)[1][:12],
    "bearer": "abcdef0123456789xyz",
    "api_key": "A" * 40,
    "email": "jane.doe@example.com",
    "url_credentials": "hunter2pass",
    "secret_assignment": "s3cr3t-Value",
    "card": "4111 1111 1111 1111",
    "private_key": "MIIBOgIBAAJBAK",
}


@pytest.mark.at("AT-ING-03")
@pytest.mark.parametrize("kind", sorted(SECRETS))
def test_each_secret_kind_is_redacted(kind: str) -> None:
    out = redact_text(SECRETS[kind])
    assert LEAKED[kind] not in out
    assert f"[REDACTED:{kind}]" in out


def test_non_secrets_survive() -> None:
    text = "order 12345 failed: 'NoneType' object has no attribute 'strip' (route /orders)"
    assert redact_text(text) == text
    assert redact_text("phone 4111 1111 1111 1112") == "phone 4111 1111 1111 1112"  # Luhn fails


def test_structured_values_and_sensitive_keys() -> None:
    value = {"user": "jane@example.com", "headers": {"Authorization": "Bearer x"}, "n": 3,
             "items": ["password=abc123", {"api_key": "k"}]}  # fmt: skip
    out = redact_value(value)
    assert out["user"] == "[REDACTED:email]"
    assert out["headers"]["Authorization"] == "[REDACTED:key]"
    assert out["n"] == 3
    assert "abc123" not in str(out)
    assert out["items"][1]["api_key"] == "[REDACTED:key]"


@pytest.mark.at("AT-ING-05")
@given(st.text(max_size=200), st.sampled_from(sorted(SECRETS)))
def test_redaction_is_idempotent_and_never_leaks(noise: str, kind: str) -> None:
    text = f"{noise} {SECRETS[kind]} {noise}"
    once = redact_text(text)
    assert redact_text(once) == once
    assert LEAKED[kind] not in once
