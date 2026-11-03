"""Client for the payment service."""

from __future__ import annotations

import httpx

from shop.telemetry import current_traceparent


class PaymentTimeoutError(Exception):
    """The payment service did not answer within the configured timeout."""


class PaymentUnavailableError(Exception):
    """The payment service could not be reached or failed."""


class PaymentClient:
    def __init__(
        self, base_url: str, timeout_s: float, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._timeout_s = timeout_s
        self._client = httpx.Client(base_url=base_url, transport=transport)

    def charge(self, order_id: int, amount_cents: int) -> str:
        headers = {}
        if (traceparent := current_traceparent()) is not None:
            headers["traceparent"] = traceparent
        try:
            response = self._client.post(
                "/charges",
                json={"order_id": order_id, "amount_cents": amount_cents},
                headers=headers,
                timeout=self._timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise PaymentTimeoutError(
                f"payment service did not respond within {self._timeout_s}s"
            ) from exc
        except httpx.TransportError as exc:
            raise PaymentUnavailableError(f"payment service unreachable: {exc}") from exc
        if response.status_code >= 500:
            raise PaymentUnavailableError(f"payment service returned {response.status_code}")
        response.raise_for_status()
        return str(response.json()["charge_id"])
