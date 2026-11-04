from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from shop.api import create_app
from shop.config import ShopSettings


def approving_payments(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"status": "approved", "charge_id": "ch_test"})


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = ShopSettings(database_url=f"sqlite:///{tmp_path / 'shop.db'}")
    app = create_app(settings, payment_transport=httpx.MockTransport(approving_payments))
    with TestClient(app) as test_client:
        yield test_client
