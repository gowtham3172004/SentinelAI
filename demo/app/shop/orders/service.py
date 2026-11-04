"""Order use cases."""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from shop.catalog import PRICES_CENTS
from shop.db import Order, now
from shop.orders.normalize import normalize_customer_note
from shop.payments import PaymentClient


class LineItem(BaseModel):
    sku: str
    qty: int = Field(gt=0, le=100)


class OrderRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=64)
    items: list[LineItem] = Field(min_length=1, max_length=20)
    customer_note: str | None = None  # optional in the API contract


class UnknownSkuError(ValueError):
    pass


class OrderNotFoundError(LookupError):
    pass


def create_order(session: Session, request: OrderRequest) -> Order:
    unknown = sorted({item.sku for item in request.items} - PRICES_CENTS.keys())
    if unknown:
        raise UnknownSkuError(f"unknown SKU(s): {', '.join(unknown)}")
    order = Order(
        customer_id=request.customer_id,
        items=[item.model_dump() for item in request.items],
        total_cents=sum(PRICES_CENTS[item.sku] * item.qty for item in request.items),
        customer_note=normalize_customer_note(request.customer_note),
        status="created",
        created_at=now(),
    )
    session.add(order)
    session.commit()
    return order


def get_order(session: Session, order_id: int) -> Order:
    order = session.get(Order, order_id)
    if order is None:
        raise OrderNotFoundError(order_id)
    return order


def list_orders(session: Session, limit: int = 50) -> list[Order]:
    return list(session.scalars(select(Order).order_by(Order.id.desc()).limit(limit)))


def pay_order(session: Session, payments: PaymentClient, order_id: int) -> Order:
    order = get_order(session, order_id)
    if order.status == "paid":
        return order  # idempotent: paying twice never charges twice
    order.charge_id = payments.charge(order.id, order.total_cents)
    order.status = "paid"
    session.commit()
    return order
