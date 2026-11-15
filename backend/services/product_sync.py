"""Supplier -> database synchronisation.

For one product:
  1. fetch supplier price + stock through the adapter
  2. compare price with what we have; compare stock
  3. update `supplier_products` (mirror) and `products`
  4. recalculate landed cost and the recommended price
  5. supplier stock 0  -> product becomes `unavailable` (not purchasable)
  6. big price move or unprofitable at the current retail price -> `needs_review`
     (not purchasable until an admin approves)

Used by the scheduled/admin sync AND by the checkout stock guard (fresh check).
Run it periodically with:  python -m backend.services.product_sync
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from backend.context import AppContext
from backend.models import products as product_repo
from backend.models.common import utcnow
from backend.services import pricing
from backend.suppliers.base import SupplierError

log = logging.getLogger(__name__)

OUT_OF_STOCK_MARK = "supplier_out_of_stock"


@dataclass
class SyncResult:
    product_id: int
    skipped: bool = False
    error: Optional[str] = None
    flagged: bool = False
    reason: Optional[str] = None
    changes: list = field(default_factory=list)


def compute_costs(ctx: AppContext, p: dict, **override) -> dict:
    """Landed cost + recommended price for a product row (with optional field overrides)."""
    v = {**p, **override}
    cfg = ctx.settings.pricing
    landed = pricing.landed_cost(
        supplier_cost=v["supplier_cost"], supplier_currency=v["supplier_currency"],
        store_currency=ctx.settings.store_currency, fx_rate=cfg.supplier_fx_rate,
        estimated_shipping=v["estimated_shipping_cost"], agent_fee=v["agent_fee"],
        other_costs=v["other_supplier_costs"],
    )
    return {"landed_cost": landed, "recommended_price": pricing.recommended_price(landed, cfg)}


def sync_product(ctx: AppContext, product_id: int) -> SyncResult:
    db, adapter, cfg = ctx.db, ctx.supplier, ctx.settings.pricing
    p = product_repo.get(db, product_id)
    result = SyncResult(product_id)
    if p is None or p["status"] == "archived" or not p["supplier_product_id"]:
        result.skipped = True
        return result
    if not adapter.supports_live_checks:
        result.skipped = True  # manual mode: admin maintains values
        return result

    sid = p["supplier_product_id"]
    try:
        price = adapter.get_price(sid)
        stock = adapter.get_stock(sid)
    except SupplierError as exc:
        product_repo.upsert_supplier_product(
            db, supplier=adapter.name, supplier_product_id=sid, currency=p["supplier_currency"],
            cost=None, stock=None, status="error", error=str(exc)[:300])
        result.error = str(exc)
        log.warning("supplier sync failed for product %s: %s", product_id, exc)
        return result

    with db.transaction():
        product_repo.upsert_supplier_product(
            db, supplier=adapter.name, supplier_product_id=sid, currency=price.currency,
            cost=price.amount_minor, stock=stock, status="ok" if stock > 0 else "out_of_stock")

        updates: dict = {"stock": max(stock, 0), "last_synced_at": utcnow()}
        status, reason = p["status"], p["review_reason"]

        currency_changed = price.currency.upper() != p["supplier_currency"].upper()
        if price.amount_minor != p["supplier_cost"] or currency_changed:
            result.changes.append(f"supplier_cost {p['supplier_cost']} -> {price.amount_minor}")
        if stock != p["stock"]:
            result.changes.append(f"stock {p['stock']} -> {stock}")
        updates.update(supplier_cost=price.amount_minor, supplier_currency=price.currency.upper())

        costs = compute_costs(ctx, p, supplier_cost=price.amount_minor, supplier_currency=price.currency.upper())
        updates.update(costs)

        if p["status"] in ("active", "unavailable", "needs_review"):
            economics = pricing.economics_at_price(p["retail_price"], costs["landed_cost"], cfg)
            flag = None
            if currency_changed:
                flag = f"Supplier currency changed to {price.currency}"
            elif pricing.price_change_is_significant(p["supplier_cost"], price.amount_minor, cfg):
                flag = (f"Supplier price moved from {p['supplier_cost']/100:.2f} to "
                        f"{price.amount_minor/100:.2f} {price.currency}")
            elif p["status"] != "needs_review" and economics.margin < cfg.min_margin:
                flag = f"Margin {float(economics.margin)*100:.1f}% is below the minimum at the current retail price"

            if flag and p["status"] != "needs_review":
                status, reason = "needs_review", flag
                result.flagged, result.reason = True, flag
            elif p["status"] == "needs_review":
                reason = (p["review_reason"] or flag)
                if flag and flag not in (reason or ""):
                    reason = f"{reason}; {flag}" if reason else flag
            elif stock <= 0 and p["status"] == "active":
                status, reason = "unavailable", OUT_OF_STOCK_MARK
                result.changes.append("marked unavailable (supplier stock 0)")
            elif stock > 0 and p["status"] == "unavailable" and p["review_reason"] == OUT_OF_STOCK_MARK:
                status, reason = "active", None
                result.changes.append("restocked -> active")
            updates.update(status=status, review_reason=reason)

        product_repo.update(db, product_id, updates)
    return result


def sync_all(ctx: AppContext) -> list:
    return [sync_product(ctx, p["id"]) for p in product_repo.list_all(ctx.db) if p["status"] != "archived"]


def approve_review(ctx: AppContext, product_id: int, *, apply_recommended_price: bool = False) -> dict:
    """Admin accepted the supplier change. Re-activates the product if it is profitable."""
    from backend.api.errors import ApiError

    db, cfg = ctx.db, ctx.settings.pricing
    p = product_repo.get(db, product_id)
    if p is None:
        raise ApiError(404, "not_found", "Product not found")
    costs = compute_costs(ctx, p)
    price = costs["recommended_price"] if apply_recommended_price else p["retail_price"]
    econ = pricing.economics_at_price(price, costs["landed_cost"], cfg)
    if econ.margin < cfg.min_margin:
        raise ApiError(409, "margin_below_minimum",
                       "Margin is below the minimum. Raise the price or apply the recommended price.",
                       econ.as_admin_dict())
    status = "active" if p["stock"] > 0 else "unavailable"
    product_repo.update(db, product_id, {**costs, "retail_price": price, "status": status,
                                         "review_reason": OUT_OF_STOCK_MARK if status == "unavailable" else None})
    return product_repo.get(db, product_id)


if __name__ == "__main__":  # python -m backend.services.product_sync
    from backend.app import build_context

    context = build_context()
    for r in sync_all(context):
        print(r)
