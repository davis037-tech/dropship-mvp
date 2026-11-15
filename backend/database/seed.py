"""DEVELOPMENT seed:  python -m backend.database.seed

Creates the sample product (pointing at the MOCK supplier id) and SAMPLE shipping zones
so you can click through the whole flow locally. Refuses to run in production: in
production you create the real product and shipping rates in the admin dashboard.

All numbers here are made-up samples - replace them with real quotes before selling.
"""
from __future__ import annotations

import sys
from decimal import Decimal

from backend.app import build_context
from backend.models import products as product_repo
from backend.models import shipping as shipping_repo
from backend.models.common import major_to_minor
from backend.services import pricing, product_sync

SAMPLE = {
    "name": "Flower Garden Snuffle Mat for Dogs",
    "slug": "flower-garden-snuffle-mat",
    "description": (
        "A snuffle mat for dogs with a flower-garden look. Scatter a little kibble or a few treats "
        "into the mat and let your dog sniff, search and forage for them.\n\n"
        "It turns an everyday meal into a short indoor activity that encourages natural sniffing and "
        "exploration. Always supervise your dog while they use it."
    ),
    "specifications": [
        {"label": "Product type", "value": "Snuffle (foraging) feeding mat for dogs"},
        {"label": "Intended use", "value": "Supervised indoor feeding and sniffing activity"},
    ],
    "images": [],     # add real product photos in the admin dashboard
}

# SAMPLE zones - costs are placeholders; delivery estimates intentionally left empty.
SAMPLE_ZONES = ["US", "CA", "GB", "AU", "DE", "FR"]


def seed_sample(ctx) -> None:
    if ctx.settings.is_production:
        raise RuntimeError("Refusing to seed sample data in production.")
    db = ctx.db
    if product_repo.get_by_slug(db, SAMPLE["slug"]) is None:
        sid = "MOCK-1688-0001" if ctx.settings.supplier_mode == "mock" else None
        base = {"supplier_product_id": sid, "supplier_platform": "1688", "supplier_currency": "CNY",
                "supplier_cost": 1800, "estimated_shipping_cost": 650, "agent_fee": 50, "other_supplier_costs": 30,
                "stock": 25, "currency": ctx.settings.store_currency, "status": "draft", "retail_price": 0}
        costs = product_sync.compute_costs(ctx, {**base})
        pid = product_repo.create(db, {**SAMPLE, **base, **costs, "retail_price": costs["recommended_price"]})
        product_repo.update(db, pid, {"status": "active"})
        econ = pricing.economics_at_price(costs["recommended_price"], costs["landed_cost"], ctx.settings.pricing)
        print(f"Created sample product, price {costs['recommended_price']/100:.2f}, margin {float(econ.margin)*100:.1f}%")
    for cc in SAMPLE_ZONES:
        if shipping_repo.get_zone(db, cc, enabled_only=False) is None:
            shipping_repo.upsert_zone(db, cc, shipping_cost=major_to_minor(Decimal("4.99")), delivery_days_min=None,
                                      delivery_days_max=None, tracking_available=True, enabled=True)
    print("Seeded sample shipping zones (placeholder rates, no delivery estimates).")


def main() -> int:
    ctx = build_context()
    try:
        seed_sample(ctx)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
