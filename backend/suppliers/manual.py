"""Manual supplier mode: there is no supplier API.

Stock and supplier cost are maintained by an admin in the dashboard; the backend
cannot verify them live. Orders are placed on 1688 by a human. This is a legitimate
production mode until real 1688 API access exists, but freshness of stock/price is then
the admin's responsibility (see README, "Supplier modes").
"""
from __future__ import annotations

from backend.suppliers.base import SupplierAdapter, SupplierUnavailable

_MSG = "SUPPLIER_MODE=manual: no supplier API is connected"


class ManualSupplierAdapter(SupplierAdapter):
    name = "manual"
    supports_live_checks = False

    def get_product(self, supplier_product_id):
        raise SupplierUnavailable(_MSG)

    def get_price(self, supplier_product_id):
        raise SupplierUnavailable(_MSG)

    def get_stock(self, supplier_product_id):
        raise SupplierUnavailable(_MSG)

    def create_supplier_order(self, request):
        raise SupplierUnavailable(_MSG + " - place the order on 1688 by hand")

    def get_tracking(self, supplier_order_id):
        raise SupplierUnavailable(_MSG)
