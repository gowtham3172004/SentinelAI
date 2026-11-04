"""HTTP API. Run with: uvicorn shop.api:create_app --factory

No ``from __future__ import annotations`` here: FastAPI must resolve the request-scoped
dependency alias defined inside ``create_app`` at definition time.
"""

import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from shop.config import ShopSettings
from shop.db import Order, create_schema, database_ready, make_engine, session_factory, sessions
from shop.orders import service
from shop.payments import PaymentClient, PaymentTimeoutError, PaymentUnavailableError
from shop.telemetry import configure_logging, start_trace

log = logging.getLogger("shop.api")
access_log = logging.getLogger("shop.access")


def _order_view(order: Order) -> dict[str, Any]:
    return {
        "id": order.id,
        "customer_id": order.customer_id,
        "items": order.items,
        "total_cents": order.total_cents,
        "customer_note": order.customer_note,
        "status": order.status,
        "charge_id": order.charge_id,
    }


def create_app(
    settings: ShopSettings | None = None, *, payment_transport: httpx.BaseTransport | None = None
) -> FastAPI:
    settings = settings or ShopSettings.from_env()
    configure_logging(settings)
    engine = make_engine(settings.database_url)
    create_schema(engine)
    factory = session_factory(engine)
    payments = PaymentClient(settings.payment_url, settings.payment_timeout_s, payment_transport)

    app = FastAPI(title="shop-api", version=settings.service_version)

    def db() -> Iterator[Session]:
        yield from sessions(factory)

    DbSession = Annotated[Session, Depends(db)]

    @app.middleware("http")
    async def telemetry(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start_trace(request.headers.get("traceparent"))
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error")
            response = JSONResponse({"detail": "internal server error"}, status_code=500)
        route = request.scope.get("route")
        access_log.info(
            "%s %s %s",
            request.method,
            request.url.path,
            response.status_code,
            extra={
                "attributes": {
                    "http.method": request.method,
                    "http.route": getattr(route, "path", request.url.path),
                    "http.target": request.url.path,
                    "http.status_code": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            },
        )
        return response

    @app.exception_handler(OperationalError)
    async def database_down(_request: Request, exc: OperationalError) -> JSONResponse:
        log.error("database unavailable", exc_info=exc)
        return JSONResponse({"detail": "database unavailable"}, status_code=503)

    @app.exception_handler(PaymentTimeoutError)
    async def payment_timeout(_request: Request, exc: PaymentTimeoutError) -> JSONResponse:
        log.error("payment timed out", exc_info=exc)
        return JSONResponse({"detail": "payment timed out"}, status_code=504)

    @app.exception_handler(PaymentUnavailableError)
    async def payment_down(_request: Request, exc: PaymentUnavailableError) -> JSONResponse:
        log.error("payment unavailable", exc_info=exc)
        return JSONResponse({"detail": "payment unavailable"}, status_code=502)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": settings.service_version}

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        ready = database_ready(engine)
        return JSONResponse(
            {"database": "up" if ready else "down"}, status_code=200 if ready else 503
        )

    @app.post("/orders", status_code=201)
    def create_order(request: service.OrderRequest, session: DbSession) -> dict[str, Any]:
        try:
            order = service.create_order(session, request)
        except service.UnknownSkuError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        log.info("order created", extra={"attributes": {"order.id": order.id}})
        return _order_view(order)

    @app.get("/orders")
    def list_orders(session: DbSession) -> list[dict[str, Any]]:
        return [_order_view(order) for order in service.list_orders(session)]

    @app.get("/orders/{order_id}")
    def get_order(order_id: int, session: DbSession) -> dict[str, Any]:
        try:
            return _order_view(service.get_order(session, order_id))
        except service.OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail="order not found") from exc

    @app.post("/orders/{order_id}/pay")
    def pay_order(order_id: int, session: DbSession) -> dict[str, Any]:
        try:
            order = service.pay_order(session, payments, order_id)
        except service.OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail="order not found") from exc
        log.info("order paid", extra={"attributes": {"order.id": order.id}})
        return _order_view(order)

    return app
