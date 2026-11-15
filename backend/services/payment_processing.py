"""Turns verified payment notifications into order state. Idempotent by design.

Two entry points feed the same function:
  * the signed webhook (`POST /api/webhooks/payment`)
  * a server-to-server reconciliation call to the provider (`reconcile_order`), used when
    a customer returns to the site before / without the webhook arriving.
The browser is never a source of truth about payment.
"""
from __future__ import annotations

import logging

from backend.context import AppContext
from backend.models import orders as order_repo
from backend.models.common import utcnow
from backend.payments.base import PaymentError, PaymentEvent

log = logging.getLogger(__name__)


def process_payment_event(ctx: AppContext, event: PaymentEvent) -> str:
    """Returns an outcome string. The same event id can never be applied twice."""
    db = ctx.db
    with db.transaction():
        inserted = db.execute(
            """INSERT INTO payment_events (provider, event_id, event_type, payment_reference, created_at)
               VALUES (?,?,?,?,?) ON CONFLICT (provider, event_id) DO NOTHING""",
            [ctx.payments.name, event.event_id, event.type, event.reference, utcnow()])
        if inserted == 0:
            return "duplicate"

        order = None
        if event.reference:
            order = order_repo.get_by_payment_reference(db, event.reference)
        if order is None and event.order_reference:
            order = order_repo.get_by_reference(db, event.order_reference)

        if event.type == "ignored":
            outcome = "ignored"
        elif order is None:
            outcome = "unknown_order"
        elif event.type == "payment_succeeded":
            outcome = _apply_paid(ctx, order, event)
        elif event.type == "payment_failed":
            outcome = "failed"
            if order["payment_status"] == "pending":
                order_repo.update(db, order["id"], {"payment_status": "failed"})
                order_repo.add_event(db, order["id"], "payment_failed", "payment_provider")
            else:
                outcome = "ignored_not_pending"
        elif event.type == "refunded":
            outcome = "refunded"
            if order["payment_status"] in ("paid", "review"):
                order_repo.update(db, order["id"], {"payment_status": "refunded", "status": "refunded"})
                order_repo.add_event(db, order["id"], "refunded_by_provider", "payment_provider")
        else:
            outcome = "ignored"

        db.execute("UPDATE payment_events SET outcome = ? WHERE provider = ? AND event_id = ?",
                   [outcome, ctx.payments.name, event.event_id])
        return outcome


def _apply_paid(ctx: AppContext, order: dict, event: PaymentEvent) -> str:
    db = ctx.db
    if order["payment_status"] == "paid":
        return "already_paid"
    amount_ok = event.amount_minor is None or event.amount_minor == order["total"]
    currency_ok = event.currency is None or event.currency.upper() == order["currency"].upper()
    if not (amount_ok and currency_ok):
        order_repo.update(db, order["id"], {
            "payment_status": "review", "needs_review": 1,
            "review_note": f"Payment amount/currency mismatch: got {event.amount_minor} {event.currency}, "
                           f"expected {order['total']} {order['currency']}"})
        order_repo.add_event(db, order["id"], "payment_mismatch", "payment_provider")
        return "amount_mismatch"
    if order["status"] == "cancelled":
        order_repo.update(db, order["id"], {
            "payment_status": "paid", "paid_at": utcnow(), "needs_review": 1,
            "review_note": "Payment arrived after the order was cancelled - refund required"})
        order_repo.add_event(db, order["id"], "paid_after_cancel", "payment_provider")
        return "paid_after_cancel"

    notes = []
    for it in order_repo.items(db, order["id"]):
        changed = db.execute(
            "UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
            [it["quantity"], it["product_id"], it["quantity"]])
        if changed == 0:
            db.execute("UPDATE products SET stock = 0 WHERE id = ?", [it["product_id"]])
            notes.append(f"Insufficient local stock for {it['product_name']} at payment time")
    order_repo.update(db, order["id"], {
        "payment_status": "paid", "status": "awaiting_supplier_approval", "supplier_status": "pending_approval",
        "paid_at": utcnow(), "needs_review": 1 if notes else 0, "review_note": "; ".join(notes) or None})
    order_repo.add_event(db, order["id"], "payment_confirmed", "payment_provider")
    return "paid"


def reconcile_order(ctx: AppContext, order: dict) -> None:
    """Ask the provider directly about a still-pending order (server-side verification)."""
    if order["payment_status"] != "pending" or not order["payment_reference"]:
        return
    try:
        status = ctx.payments.get_payment_status(order["payment_reference"])
    except PaymentError as exc:
        log.warning("reconcile failed for %s: %s", order["reference"], exc)
        return
    if status.status == "paid":
        process_payment_event(ctx, PaymentEvent(
            event_id=f"reconcile:{order['payment_reference']}:paid", type="payment_succeeded",
            reference=order["payment_reference"], order_reference=order["reference"],
            amount_minor=status.amount_minor, currency=status.currency))
