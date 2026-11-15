"""1688 adapter - PLACEHOLDER. NOT CONNECTED TO ANYTHING.

(The file is not called `1688.py` because Python modules cannot start with a digit.)

We do NOT currently have official 1688 API access, so every method below either
raises `SupplierNotConfigured` (no credentials) or `SupplierNotImplemented` (credentials
present, integration not written). Nothing here pretends to work.

HOW TO PLUG THE REAL THING IN LATER
-----------------------------------
1. Obtain API access through the 1688 / Alibaba open platform (https://open.1688.com) or
   an authorised third-party data/ordering provider, and put the credentials in `.env`:
       SUPPLIER_1688_API_KEY=...
       SUPPLIER_1688_API_SECRET=...
       SUPPLIER_1688_ACCESS_TOKEN=...
2. Implement each `TODO[1688]` method below. Keep the return types from
   `backend/suppliers/base.py`; prices must be integer minor units of the supplier
   currency (CNY), and stock an integer.
3. Set SUPPLIER_MODE=1688 (and, only once purchasing is proven, optionally
   FULFILLMENT_MODE=automatic - admin approval will still be required).

Credentials are read from the server environment only and are never sent to the browser.
"""
from __future__ import annotations

from backend.suppliers.base import (
    SupplierAdapter,
    SupplierNotConfigured,
    SupplierNotImplemented,
    SupplierOrderRequest,
)


class Alibaba1688Adapter(SupplierAdapter):
    name = "1688"
    supports_live_checks = True

    def __init__(self, api_key: str = "", api_secret: str = "", access_token: str = ""):
        self._api_key = api_key
        self._api_secret = api_secret
        self._access_token = access_token

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._api_secret)

    def _require(self, what: str):
        if not self.configured:
            raise SupplierNotConfigured(
                "1688 API credentials are not configured "
                "(SUPPLIER_1688_API_KEY / SUPPLIER_1688_API_SECRET)"
            )
        raise SupplierNotImplemented(f"TODO[1688]: {what} is not implemented yet")

    def get_product(self, supplier_product_id):
        self._require("get_product (fetch title, images, price, stock)")

    def get_price(self, supplier_product_id):
        self._require("get_price (current unit price, minor units of CNY)")

    def get_stock(self, supplier_product_id):
        self._require("get_stock (available quantity)")

    def create_supplier_order(self, request: SupplierOrderRequest):
        self._require("create_supplier_order (place purchase order, ship to customer)")

    def get_tracking(self, supplier_order_id):
        self._require("get_tracking (logistics number + carrier)")
