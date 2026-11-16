"""Product repository + serializers.

`to_public()` is the ONLY serializer used by public endpoints and is an explicit
allow-list: supplier cost, landed cost, stock counts and review reasons never leave it.
"""
from __future__ import annotations

from typing import Optional

from backend.database.db import Database
from backend.models.common import jdumps, jloads, minor_to_major, utcnow

PURCHASABLE_STATUS = "active"
PUBLIC_STATUSES = ("active", "unavailable", "needs_review")  # shown on the storefront


def _decode(row: Optional[dict]) -> Optional[dict]:
    if row is None:
        return None
    row["images"] = jloads(row.get("images"), [])
    row["specifications"] = jloads(row.get("specifications"), [])
    return row


def get(db: Database, product_id: int) -> Optional[dict]:
    return _decode(db.fetch_one("SELECT * FROM products WHERE id = ?", [product_id]))


def get_by_slug(db: Database, slug: str) -> Optional[dict]:
    return _decode(db.fetch_one("SELECT * FROM products WHERE slug = ?", [slug]))


def list_all(db: Database, *, public_only: bool = False) -> list:
    if public_only:
        marks = ",".join("?" for _ in PUBLIC_STATUSES)
        rows = db.fetch_all(f"SELECT * FROM products WHERE status IN ({marks}) ORDER BY id", list(PUBLIC_STATUSES))
    else:
        rows = db.fetch_all("SELECT * FROM products ORDER BY id")
    return [_decode(r) for r in rows]


def create(db: Database, data: dict) -> int:
    now = utcnow()
    row = {**data, "created_at": now, "updated_at": now}
    row["images"] = jdumps(row.get("images", []))
    row["specifications"] = jdumps(row.get("specifications", []))
    return db.insert_row("products", row)


def update(db: Database, product_id: int, data: dict) -> None:
    row = {**data, "updated_at": utcnow()}
    if "images" in row:
        row["images"] = jdumps(row["images"])
    if "specifications" in row:
        row["specifications"] = jdumps(row["specifications"])
    db.update_row("products", product_id, row)


def is_purchasable(product: dict, quantity: int = 1) -> bool:
    return product["status"] == PURCHASABLE_STATUS and product["stock"] >= quantity and product["retail_price"] > 0


# ---------------------------------------------------------------- serializers
def to_public(p: dict, max_quantity: int) -> dict:
    available = is_purchasable(p)
    return {
        "id": p["id"],
        "name": p["name"],
        "slug": p["slug"],
        "description": p["description"],
        "price": minor_to_major(p["retail_price"]),
        "currency": p["currency"],
        "images": [{"url": i.get("url", ""), "alt": i.get("alt", p["name"])} for i in p["images"]],
        "specifications": p["specifications"],
        "available": available,
        "status": "available" if available else "unavailable",
        "max_quantity": max(0, min(p["stock"], max_quantity)) if available else 0,
        "created_at": p["created_at"],
        "updated_at": p["updated_at"],
    }


def to_admin(p: dict, economics: Optional[dict] = None) -> dict:
    return {
        "id": p["id"], "name": p["name"], "slug": p["slug"], "description": p["description"],
        "supplier_product_id": p["supplier_product_id"], "supplier_platform": p["supplier_platform"],
        "supplier_currency": p["supplier_currency"],
        "supplier_cost": minor_to_major(p["supplier_cost"]),
        "estimated_shipping_cost": minor_to_major(p["estimated_shipping_cost"]),
        "agent_fee": minor_to_major(p["agent_fee"]),
        "other_supplier_costs": minor_to_major(p["other_supplier_costs"]),
        "landed_cost": minor_to_major(p["landed_cost"]),
        "recommended_price": minor_to_major(p["recommended_price"]),
        "market_price": minor_to_major(p.get("market_price")) if p.get("market_price") is not None else None,
        "retail_price": minor_to_major(p["retail_price"]),
        "currency": p["currency"], "stock": p["stock"], "status": p["status"],
        "review_reason": p["review_reason"], "images": p["images"], "specifications": p["specifications"],
        "last_synced_at": p["last_synced_at"], "created_at": p["created_at"], "updated_at": p["updated_at"],
        "economics": economics,
    }


# ---------------------------------------------------------------- supplier mirror
def upsert_supplier_product(db: Database, *, supplier: str, supplier_product_id: str, currency: str,
                            cost: Optional[int], stock: Optional[int], status: str,
                            error: Optional[str] = None, url: Optional[str] = None) -> None:
    db.execute(
        """INSERT INTO supplier_products
               (supplier, supplier_product_id, product_url, supplier_currency, supplier_cost,
                supplier_stock, last_checked, status, last_error)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT (supplier, supplier_product_id) DO UPDATE SET
               supplier_currency = excluded.supplier_currency,
               supplier_cost = COALESCE(excluded.supplier_cost, supplier_products.supplier_cost),
               supplier_stock = COALESCE(excluded.supplier_stock, supplier_products.supplier_stock),
               last_checked = excluded.last_checked,
               status = excluded.status,
               last_error = excluded.last_error,
               product_url = COALESCE(excluded.product_url, supplier_products.product_url)""",
        [supplier, supplier_product_id, url, currency, cost, stock, utcnow(), status, error],
    )


def get_supplier_product(db: Database, supplier: str, supplier_product_id: str) -> Optional[dict]:
    return db.fetch_one(
        "SELECT * FROM supplier_products WHERE supplier = ? AND supplier_product_id = ?",
        [supplier, supplier_product_id],
    )
