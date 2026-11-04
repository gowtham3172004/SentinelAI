from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sentinelai.domain.redaction import redact_text, redact_value

SECRETS = {
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
    "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "bearer": "Authorization: Bearer abcdef0123456789xyz",
    "api_key": "key gsk_" + "A" * 40,
    "email": "contact jane.doe@example.com now",
    "url_credentials": "postgresql://shop:hunter2pass@db:5432/shop",
    "secret_assignment": "login failed password=s3cr3t-Value user=bob",
    "card": "paid with 4111 1111 1111 1111 yesterday",
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAK\n-----END RSA PRIVATE KEY-----",
}  # fmt: skip
LEAKED = {
    "jwt": "dozjgNryP4J3",
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
