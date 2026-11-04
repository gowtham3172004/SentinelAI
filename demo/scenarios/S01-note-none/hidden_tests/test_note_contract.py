"""Hidden fail-to-pass test for S01 (never copied into the target repository).

The API contract makes ``customer_note`` optional: orders without a note must succeed.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from shop.orders.normalize import normalize_customer_note


def test_order_without_note_succeeds(client: TestClient) -> None:
    response = client.post(
        "/orders", json={"customer_id": "c-7", "items": [{"sku": "SKU-CAP", "qty": 1}]}
    )
    assert response.status_code == 201
    assert response.json()["customer_note"] is None


def test_none_and_blank_notes_normalise_to_none() -> None:
    assert normalize_customer_note(None) is None
    assert normalize_customer_note("   ") is None
