"""DEVELOPMENT / TEST ONLY mock supplier.

Nothing in this file talks to any real platform and the data is fictional. The
application refuses to start with SUPPLIER_MODE=mock when APP_ENV=production
(see config/settings.py), and this class refuses to be constructed there as well.
"""
from __future__ import annotations

import itertools

from backend.suppliers.base import (
    SupplierAdapter,
    SupplierError,
    SupplierPrice,
    SupplierProduct,
    SupplierOrderRequest,
    SupplierOrderResult,
    SupplierUnavailable,
    TrackingInfo,
)

SAMPLE_SUPPLIER_PRODUCT_ID = "MOCK-1688-0001"


class MockSupplierAdapter(SupplierAdapter):
    name = "mock"
    supports_live_checks = True

    def __init__(self, app_env: str = "development"):
        if app_env == "production":
            raise RuntimeError("MockSupplierAdapter must never be used in production")
        self.offline = False
        self._seq = itertools.count(1)
        self._catalog = {
            SAMPLE_SUPPLIER_PRODUCT_ID: {
                "title": "[MOCK] Flower garden snuffle mat",
                "url": "https://example.invalid/mock/MOCK-1688-0001",
                "price_minor": 1800,   # 18.00 CNY (fictional)
                "currency": "CNY",
                "stock": 25,
            }
        }

    # -- test/dev controls ---------------------------------------------------
    def set_price(self, supplier_product_id: str, price_minor: int) -> None:
        self._catalog[supplier_product_id]["price_minor"] = price_minor

    def set_stock(self, supplier_product_id: str, stock: int) -> None:
        self._catalog[supplier_product_id]["stock"] = stock

    def set_offline(self, offline: bool) -> None:
        self.offline = offline

    # -- interface -------------------------------------------------------------
    def _item(self, supplier_product_id: str) -> dict:
        if self.offline:
            raise SupplierUnavailable("mock supplier offline")
        try:
            return self._catalog[supplier_product_id]
        except KeyError:
            raise SupplierError(f"unknown mock product {supplier_product_id}")

    def get_product(self, supplier_product_id: str) -> SupplierProduct:
        i = self._item(supplier_product_id)
        return SupplierProduct(supplier_product_id, i["title"], i["url"], i["price_minor"], i["currency"], i["stock"])

    def get_price(self, supplier_product_id: str) -> SupplierPrice:
        i = self._item(supplier_product_id)
        return SupplierPrice(i["price_minor"], i["currency"])

    def get_stock(self, supplier_product_id: str) -> int:
        return int(self._item(supplier_product_id)["stock"])

    def create_supplier_order(self, request: SupplierOrderRequest) -> SupplierOrderResult:
        self._item(request.supplier_product_id)
        return SupplierOrderResult(f"MOCK-ORDER-{next(self._seq):05d}", "placed", {"mock": True})

    def get_tracking(self, supplier_order_id: str) -> TrackingInfo:
        return TrackingInfo(None, None, "no_tracking_yet_mock")
