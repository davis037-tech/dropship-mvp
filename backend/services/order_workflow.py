"""Admin-driven order state machine.

pending_payment -> (payment verified) -> awaiting_supplier_approval
  -> supplier_approved -> supplier_ordered -> shipped -> delivered
Any pre-shipment state can be cancelled; paid orders can be refunded via the payment module.
Nothing here is reachable by customers.
"""
from __future__ import annotations

from typing import Optional

from backend.api.errors import ApiError
from backend.context import AppContext
from backend.models import orders as order_repo
from backend.models import shipping as shipping_repo
from backend.payments.base import PaymentError
from backend.services import fulfillment, stock_guard

TRANSITIONS = {
    "pending_payment": {"cancelled"},
    "awaiting_supplier_approval": {"supplier_approved", "cancelled"},
    "supplier_approved": {"supplier_ordered", "cancelled"},
    "supplier_ordered": {"shipped", "cancelled"},
    "shipped": {"delivered"},
}
SUPPLIER_STATUS = {"supplier_approved": "approved", "supplier_ordered": "ordered", "cancelled": "cancelled"}


def _refund(ctx: AppContext, order: dict, actor: str, reason: str) -> None:
    if order["payment_status"] not in ("paid", "review"):
        raise ApiError(409, "not_refundable", "Only paid orders can be refunded.")
    if not order["payment_reference"]:
        raise ApiError(409, "not_refundable", "Order has no payment reference.")
    try:
        r = ctx.payments.refund(reference=order["payment_reference"], amount_minor=order["total"],
                                currency=order["currency"], reason=reason)
    except PaymentError as exc:
        raise ApiError(502, "refund_failed", f"Payment provider refused the refund: {exc}")
    order_repo.add_event(ctx.db, order["id"], "refunded", actor, f"refund_id={r.refund_id}")


def change_status(ctx: AppContext, order_id: int, target: str, actor: str, *, supplier_order_id: Optional[str] = None,
                  override: bool = False, refund: bool = False, acknowledge_supplier_order: bool = False) -> dict:
    db = ctx.db
    order = order_repo.get(db, order_id)
    if order is None:
        raise ApiError(404, "not_found", "Order not found")
    current = order["status"]
    if target not in TRANSITIONS.get(current, set()):
        raise ApiError(409, "invalid_transition", f"Cannot move an order from {current} to {target}.")
    updates: dict = {"status": target}
    detail = None

    if target == "supplier_approved":
        items = order_repo.items(db, order_id)
        warnings = stock_guard.fulfilment_warnings(ctx, [i["product_id"] for i in items])
        if warnings and not override:
            raise ApiError(409, "supplier_check_failed", "Supplier check raised warnings.", {"warnings": warnings})
        updates["supplier_status"] = "approved"
        detail = "; ".join(warnings) if warnings else None
        result = fulfillment.get_fulfillment(ctx).after_approval(ctx, order)
        if result is not None:                       # automatic mode only
            updates.update(status="supplier_ordered", supplier_status="ordered", supplier_order_id=result.supplier_order_id)

    elif target == "supplier_ordered":
        if not supplier_order_id:
            raise ApiError(422, "supplier_order_id_required", "Enter the 1688 order number.")
        updates.update(supplier_status="ordered", supplier_order_id=supplier_order_id)

    elif target == "shipped":
        zone = shipping_repo.get_zone(db, order["shipping_country"], enabled_only=False)
        needs_tracking = zone is None or bool(zone["tracking_available"])
        if needs_tracking and not order["tracking_number"] and not override:
            raise ApiError(409, "tracking_required", "Add a tracking number before marking shipped.")

    elif target == "cancelled":
        if current == "supplier_ordered" and not acknowledge_supplier_order:
            raise ApiError(409, "supplier_order_exists",
                           "A 1688 order was already placed. Cancel it on 1688 first, then confirm.")
        updates["supplier_status"] = "cancelled" if order["supplier_status"] != "none" else "none"
        if order["payment_status"] in ("paid", "review"):
            if not refund:
                raise ApiError(409, "refund_decision_required",
                               "This order is paid. Cancel with refund=true (or refund it separately).")
            _refund(ctx, order, actor, "order cancelled")
            updates["payment_status"] = "refunded"

    with db.transaction():
        order_repo.update(db, order_id, updates)
        order_repo.add_event(db, order_id, f"status:{updates['status']}", actor, detail)
    return order_repo.get(db, order_id)


def set_tracking(ctx: AppContext, order_id: int, actor: str, number: str, carrier: Optional[str], url: Optional[str]) -> dict:
    order = order_repo.get(ctx.db, order_id)
    if order is None:
        raise ApiError(404, "not_found", "Order not found")
    if order["status"] not in ("supplier_ordered", "shipped"):
        raise ApiError(409, "invalid_state", "Tracking can be added once the supplier order is placed.")
    order_repo.update(ctx.db, order_id, {"tracking_number": number, "tracking_carrier": carrier, "tracking_url": url})
    order_repo.add_event(ctx.db, order_id, "tracking_set", actor, number)
    return order_repo.get(ctx.db, order_id)


def refund_order(ctx: AppContext, order_id: int, actor: str, reason: str = "admin refund") -> dict:
    order = order_repo.get(ctx.db, order_id)
    if order is None:
        raise ApiError(404, "not_found", "Order not found")
    _refund(ctx, order, actor, reason)
    order_repo.update(ctx.db, order_id, {"payment_status": "refunded", "status": "refunded"})
    order_repo.add_event(ctx.db, order_id, "status:refunded", actor, reason)
    return order_repo.get(ctx.db, order_id)
