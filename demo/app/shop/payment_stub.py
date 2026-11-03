"""Payment service stub: approves every charge after a fixed latency (deterministic)."""

from __future__ import annotations

import asyncio
import hashlib
import os

from fastapi import FastAPI
from pydantic import BaseModel

LATENCY_S = float(os.environ.get("PAYMENT_STUB_LATENCY_S", "0.05"))

app = FastAPI(title="payment-stub")


class ChargeRequest(BaseModel):
    order_id: int
    amount_cents: int


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/charges")
async def charge(request: ChargeRequest) -> dict[str, str]:
    await asyncio.sleep(LATENCY_S)
    digest = hashlib.sha256(f"{request.order_id}:{request.amount_cents}".encode()).hexdigest()
    return {"status": "approved", "charge_id": f"ch_{digest[:16]}"}
