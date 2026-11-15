"""Fulfilment strategy: how an APPROVED order becomes a supplier purchase.

Today:  ManualFulfillment  - an admin buys on 1688 by hand, then records the supplier
                             order number in the dashboard.
Later:  AutomaticFulfillment - calls `SupplierAdapter.create_supplier_order()`.
Switching is a configuration change (FULFILLMENT_MODE=automatic, SUPPLIER_MODE=1688);
admin APPROVAL stays mandatory in both: customers can never trigger a purchase.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from backend.context import AppContext
from backend.models import orders as order_repo
from backend.models.common import jloads
from backend.suppliers.base import SupplierOrderRequest, SupplierOrderResult


class FulfillmentStrategy(ABC):
    @abstractmethod
    def after_approval(self, ctx: AppContext, order: dict) -> Optional[SupplierOrderResult]:
        """Called once an admin has approved the order. Return a result if a supplier
        order was placed automatically, else None (a human will do it)."""


class ManualFulfillment(FulfillmentStrategy):
    def after_approval(self, ctx, order):
        return None


class AutomaticFulfillment(FulfillmentStrategy):
    def after_approval(self, ctx, order):
        items = order_repo.items(ctx.db, order["id"])
        if len(items) != 1:
            raise NotImplementedError("Automatic fulfilment currently supports single-line orders only")
        it = items[0]
        return ctx.supplier.create_supplier_order(SupplierOrderRequest(
            reference=order["reference"], supplier_product_id=it["supplier_product_id"],
            quantity=it["quantity"], ship_to_name=order["shipping_name"],
            ship_to_address=jloads(order["shipping_address"], {}), ship_to_country=order["shipping_country"]))


def get_fulfillment(ctx: AppContext) -> FulfillmentStrategy:
    return AutomaticFulfillment() if ctx.settings.fulfillment_mode == "automatic" else ManualFulfillment()
