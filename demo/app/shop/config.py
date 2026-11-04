"""Runtime configuration from environment variables (12-factor)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ShopSettings:
    database_url: str = "sqlite:///./shop.db"
    payment_url: str = "http://payment-stub:8000"
    payment_timeout_s: float = 2.0
    service_name: str = "shop-api"
    service_version: str = "dev"
    git_sha: str = "unknown"
    environment: str = "production"
    log_file: str | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ShopSettings:
        env = os.environ if environ is None else environ
        return cls(
            database_url=env.get("SHOP_DATABASE_URL", cls.database_url),
            payment_url=env.get("SHOP_PAYMENT_URL", cls.payment_url),
            payment_timeout_s=float(env.get("SHOP_PAYMENT_TIMEOUT_S", cls.payment_timeout_s)),
            service_name=env.get("SHOP_SERVICE_NAME", cls.service_name),
            service_version=env.get("SHOP_VERSION", cls.service_version),
            git_sha=env.get("SHOP_GIT_SHA", cls.git_sha),
            environment=env.get("SHOP_ENVIRONMENT", cls.environment),
            log_file=env.get("SHOP_LOG_FILE") or None,
        )
