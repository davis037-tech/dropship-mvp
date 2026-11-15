"""Checkout: validates a cart against the database (never against client prices),
runs the stock/price guard, creates a pending order and opens a hosted payment session.

The client may send only product slugs and quantities. Prices, shipping, totals, and
whether an order is valid are all decided here.
"""
from __future__ import annotations

import json
from collections import OrderedDict
from typing import List, Tuple

from backend.api.errors import ApiError
from backend.auth.tokens import order_access_token
from backend.context import AppContext
from backend.models import orders as order_repo
from backend.models import products as product_repo
from backend.models import shipping as shipping_repo
from backend.models.common import minor_to_major, utcnow
from backend.payments.base import PaymentError
from backend.services import pricing, product_sync, stock_guard


def _merge(items) -> "OrderedDict[str, int]":
    merged: "OrderedDict[str, int]" = OrderedDict()
    for it in items:
        merged[it.slug] = merged.get(it.slug, 0) + it.quantity
    return merged


def _zone_or_error(ctx: AppContext, country: str) -> dict:
    zone = shipping_repo.get_zone(ctx.db, country)
    if zone is None:
        raise ApiError(422, "shipping_unsupported", "Sorry, we do not currently ship to that country.",
                       {"country": country})
    return zone


def _load_lines(ctx: AppContext, items) -> List[Tuple[dict, int]]:
    lines = []
    for slug, qty in _merge(items).items():
        p = product_repo.get_by_slug(ctx.db, slug)
        if p is None or p["status"] in ("draft", "archived"):
            raise ApiError(404, "product_not_found", "That product could not be found.", {"slug": slug})
        if qty > ctx.settings.max_quantity:
            raise ApiError(422, "quantity_too_high", f"Maximum {ctx.settings.max_quantity} per item.", {"slug": slug})
        lines.append((p, qty))
    return lines


def _quote_body(ctx, lines, zone) -> dict:
    subtotal = sum(p["retail_price"] * q for p, q in lines)
    shipping = zone["shipping_cost"]
    return {
        "currency": ctx.settings.store_currency,
        "items": [{"slug": p["slug"], "name": p["name"], "quantity": q,
                   "unit_price": minor_to_major(p["retail_price"]),
                   "line_total": minor_to_major(p["retail_price"] * q)} for p, q in lines],
        "subtotal": minor_to_major(subtotal),
        "shipping_cost": minor_to_major(shipping),
        "total": minor_to_major(subtotal + shipping),
        "shipping": shipping_repo.zone_public(zone),
    }


def quote(ctx: AppContext, items, country: str) -> dict:
    """Cheap, DB-only quote used by the cart/checkout page for display."""
    zone = _zone_or_error(ctx, country)
    lines = _load_lines(ctx, items)
    for p, q in lines:
        if not product_repo.is_purchasable(p, q):
            raise ApiError(409, "product_unavailable", "Sorry, this item is currently unavailable.", {"slug": p["slug"]})
    return _quote_body(ctx, lines, zone)


def create_checkout(ctx: AppContext, req, base_url: str) -> dict:
    db, cfg = ctx.db, ctx.settings.pricing
    zone = _zone_or_error(ctx, req.country)
    lines = _load_lines(ctx, req.items)

    fresh = []
    for p, q in lines:                       # supplier + stock + profitability gate
        fresh.append((stock_guard.ensure_purchasable(ctx, p["id"], q), q))

    est_lines = [(p["retail_price"], q, product_sync.compute_costs(ctx, p)["landed_cost"]) for p, q in fresh]
    est = pricing.order_estimate(est_lines, zone["shipping_cost"], cfg)
    now = utcnow()
    address = {"line1": req.address.line1, "line2": req.address.line2, "city": req.address.city,
               "region": req.address.region, "postal_code": req.address.postal_code}

    with db.transaction():
        customer_id = db.insert(
            """INSERT INTO customers (email, name, created_at) VALUES (?,?,?)
               ON CONFLICT (email) DO UPDATE SET name = excluded.name RETURNING id""",
            [req.customer.email, req.customer.name, now])
        order_id, reference = None, None
        for _ in range(5):
            reference = order_repo.new_reference()
            if order_repo.get_by_reference(db, reference) is None:
                break
        order_id = db.insert_row("orders", {
            "reference": reference, "customer_id": customer_id, "status": "pending_payment",
            "payment_status": "pending", "subtotal": est["subtotal"], "shipping_cost": zone["shipping_cost"],
            "total": est["total"], "currency": ctx.settings.store_currency,
            "supplier_status": "none", "shipping_name": req.customer.name,
            "shipping_address": json.dumps(address), "shipping_country": req.country,
            "est_cost": est["cost"], "est_fees": est["fees"], "est_profit": est["profit"],
            "needs_review": 0, "created_at": now, "updated_at": now,
        })
        for (p, q), (_, _, landed) in zip(fresh, est_lines):
            db.insert_row("order_items", {
                "order_id": order_id, "product_id": p["id"], "product_name": p["name"], "quantity": q,
                "unit_price": p["retail_price"], "unit_landed_cost": landed, "created_at": now})
        order_repo.add_event(db, order_id, "order_created", "customer", f"total={est['total']}")

    token = order_access_token(ctx.settings.secret_key, reference)
    order_url = f"{base_url}/order?ref={reference}&t={token}"
    try:
        session = ctx.payments.create_checkout_session(
            order_reference=reference, amount_minor=est["total"], currency=ctx.settings.store_currency,
            customer_email=req.customer.email,
            description=f"Order {reference} - " + ", ".join(p["name"] for p, _ in fresh),
            success_url=order_url, cancel_url=f"{base_url}/cart")
    except PaymentError as exc:
        order_repo.update(db, order_id, {"status": "cancelled", "payment_status": "failed"})
        order_repo.add_event(db, order_id, "payment_session_failed", "system", str(exc)[:200])
        raise ApiError(502, "payment_unavailable", "We could not start the payment. Please try again shortly.")

    order_repo.update(db, order_id, {"payment_provider": ctx.payments.name, "payment_reference": session.reference})
    return {"order_reference": reference, "redirect_url": session.redirect_url, "status_url": order_url}
