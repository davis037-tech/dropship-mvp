"""Stock + profitability protection. The backend is the source of truth.

Before a customer may pay we:
  1. check our own database stock / status
  2. ask the supplier for a FRESH price and stock (when the adapter supports it)
  3. refuse if the product is unavailable, flagged for review, or no longer profitable
If the supplier cannot be reached we only fall back to cached data when
SUPPLIER_CHECK_POLICY=cache AND the cache is younger than STOCK_CACHE_MAX_AGE_MINUTES.
Customers only ever see a generic "unavailable" message; the real reason stays internal.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List

from backend.api.errors import ApiError
from backend.context import AppContext
from backend.models import products as product_repo
from backend.models.common import parse_ts
from backend.services import pricing, product_sync

log = logging.getLogger(__name__)


def _unavailable(slug: str, internal_reason: str) -> ApiError:
    log.info("checkout blocked for %s: %s", slug, internal_reason)
    return ApiError(409, "product_unavailable",
                    "Sorry, this item is currently unavailable. Please check back soon.", {"slug": slug})


def _cache_is_fresh(ctx: AppContext, product: dict) -> bool:
    if ctx.settings.supplier_check_policy != "cache":
        return False
    checked = parse_ts(product.get("last_synced_at"))
    if checked is None:
        return False
    return datetime.now(timezone.utc) - checked <= timedelta(minutes=ctx.settings.stock_cache_max_age_minutes)


def ensure_purchasable(ctx: AppContext, product_id: int, quantity: int) -> dict:
    """Returns the *fresh* product row or raises ApiError(409)."""
    db = ctx.db
    product = product_repo.get(db, product_id)
    if product is None or product["status"] in ("draft", "archived"):
        raise _unavailable("?", "not found / not public")
    slug = product["slug"]

    if ctx.supplier.supports_live_checks:
        result = product_sync.sync_product(ctx, product_id)
        if result.error:
            product = product_repo.get(db, product_id)
            if not _cache_is_fresh(ctx, product):
                raise _unavailable(slug, f"supplier check failed: {result.error}")
        product = product_repo.get(db, product_id)

    if product["status"] != "active":
        raise _unavailable(slug, f"status={product['status']} reason={product['review_reason']}")
    if product["stock"] < quantity:
        raise _unavailable(slug, f"insufficient stock ({product['stock']} < {quantity})")

    # Recompute economics from current numbers; never rely on a stale stored margin.
    costs = product_sync.compute_costs(ctx, product)
    if not pricing.is_profitable(product["retail_price"], costs["landed_cost"], ctx.settings.pricing):
        product_repo.update(db, product_id, {**costs, "status": "needs_review",
                                             "review_reason": "Checkout blocked: order would be below minimum margin"})
        raise _unavailable(slug, "unprofitable at current supplier price")
    return product


def fulfilment_warnings(ctx: AppContext, product_ids: List[int]) -> List[str]:
    """Used when an admin approves a supplier order: advisory fresh check."""
    warnings: List[str] = []
    for pid in product_ids:
        p = product_repo.get(ctx.db, pid)
        if not ctx.supplier.supports_live_checks:
            warnings.append(f"{p['name']}: supplier cannot be verified automatically (manual mode) - check 1688 yourself")
            continue
        r = product_sync.sync_product(ctx, pid)
        p = product_repo.get(ctx.db, pid)
        if r.error:
            warnings.append(f"{p['name']}: supplier check failed ({r.error})")
        elif p["stock"] <= 0:
            warnings.append(f"{p['name']}: supplier stock is 0")
        elif p["status"] == "needs_review":
            warnings.append(f"{p['name']}: supplier price changed / unprofitable ({p['review_reason']})")
    return warnings
