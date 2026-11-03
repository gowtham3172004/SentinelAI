from __future__ import annotations

from fastapi.testclient import TestClient

from shop.orders.normalize import normalize_customer_note


def order(note: str = "Leave at the door") -> dict[str, object]:
    return {
        "customer_id": "c-42",
        "items": [{"sku": "SKU-MUG", "qty": 2}, {"sku": "SKU-TEE", "qty": 1}],
        "customer_note": note,
    }


def test_create_and_fetch_order(client: TestClient) -> None:
    created = client.post("/orders", json=order())
    assert created.status_code == 201
    body = created.json()
    assert body["total_cents"] == 2 * 1299 + 2499
    assert body["customer_note"] == "Leave at the door"
    fetched = client.get(f"/orders/{body['id']}")
    assert fetched.json() == body


def test_note_whitespace_is_collapsed(client: TestClient) -> None:
    created = client.post("/orders", json=order("  ring   twice  "))
    assert created.json()["customer_note"] == "ring twice"


def test_unknown_sku_is_rejected(client: TestClient) -> None:
    bad = {"customer_id": "c-1", "items": [{"sku": "SKU-NOPE", "qty": 1}], "customer_note": "x"}
    response = client.post("/orders", json=bad)
    assert response.status_code == 422


def test_list_orders(client: TestClient) -> None:
    for _ in range(3):
        client.post("/orders", json=order())
    assert len(client.get("/orders").json()) == 3


def test_missing_order_is_404(client: TestClient) -> None:
    assert client.get("/orders/999").status_code == 404


def test_pay_order_is_idempotent(client: TestClient) -> None:
    order_id = client.post("/orders", json=order()).json()["id"]
    first = client.post(f"/orders/{order_id}/pay")
    second = client.post(f"/orders/{order_id}/pay")
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == "paid"
    assert first.json()["charge_id"] == second.json()["charge_id"]


def test_health_and_readiness(client: TestClient) -> None:
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/readyz").json() == {"database": "up"}


def test_long_notes_are_capped() -> None:
    note = normalize_customer_note("x" * 600)
    assert note is not None
    assert len(note) == 500
