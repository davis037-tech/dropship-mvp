"""Supplier adapter factory. The ONLY place a concrete adapter is chosen."""
from __future__ import annotations

from backend.config.settings import ConfigError, Settings
from backend.suppliers.base import SupplierAdapter


def build_supplier_adapter(settings: Settings) -> SupplierAdapter:
    mode = settings.supplier_mode
    if mode == "mock":
        if settings.is_production:  # belt and braces; Settings already refuses this
            raise ConfigError("mock supplier is not allowed in production")
        from backend.suppliers.mock import MockSupplierAdapter

        return MockSupplierAdapter(settings.app_env)
    if mode == "manual":
        from backend.suppliers.manual import ManualSupplierAdapter

        return ManualSupplierAdapter()
    if mode == "1688":
        from backend.suppliers.platform_1688 import Alibaba1688Adapter

        return Alibaba1688Adapter(
            settings.supplier_1688_api_key,
            settings.supplier_1688_api_secret,
            settings.supplier_1688_access_token,
        )
    if mode == "cj":
        from backend.suppliers.cj import CJDropshippingAdapter

        return CJDropshippingAdapter(settings.cj_api_key)
    raise ConfigError(f"unknown SUPPLIER_MODE {mode}")
