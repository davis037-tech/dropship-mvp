"""Order repository + serializers (public status view vs admin view)."""
from __future__ import annotations

import secrets
from typing import Optional

from backend.database.db import Database
from backend.models.common import jloads, minor_to_major, utcnow

_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no look-alike characters


def new_reference() -> str:
    return "ORD-" + "".join(secrets.choice(_ALPHABET) for _ in range(10))


def get(db: Database, order_id: int) -> Optional[dict]:
    return db.fetch_one("SELECT * FROM orders WHERE id = ?", [order_id])


def get_by_reference(db: Database, reference: str) -> Optional[dict]:
    return db.fetch_one("SELECT * FROM orders WHERE reference = ?", [reference])


def get_by_payment_reference(db: Database, ref: str) -> Optional[dict]:
    return db.fetch_one("SELECT * FROM orders WHERE payment_reference = ?", [ref])


def items(db: Database, order_id: int) -> list:
    return db.fetch_all(
        """SELECT oi.*, p.slug AS product_slug, p.supplier_product_id AS supplier_product_id
           FROM order_items oi JOIN products p ON p.id = oi.product_id
           WHERE oi.order_id = ? ORDER BY oi.id""",
        [order_id],
    )


def customer(db: Database, customer_id: int) -> dict:
    return db.fetch_one("SELECT * FROM customers WHERE id = ?", [customer_id])


def add_event(db: Database, order_id: int, event: str, actor: str, detail: Optional[str] = None) -> None:
    db.execute(
        "INSERT INTO order_events (order_id, event, actor, detail, created_at) VALUES (?,?,?,?,?)",
        [order_id, event, actor, detail, utcnow()],
    )


def events(db: Database, order_id: int) -> list:
    return db.fetch_all("SELECT event, actor, detail, created_at FROM order_events WHERE order_id = ? ORDER BY id", [order_id])


def update(db: Database, order_id: int, data: dict) -> None:
    db.update_row("orders", order_id, {**data, "updated_at": utcnow()})


def list_orders(db: Database, status: Optional[str] = None, limit: int = 200) -> list:
    sql = """SELECT o.*, c.name AS customer_name, c.email AS customer_email,
                    (SELECT CAST(COALESCE(SUM(quantity),0) AS INTEGER) FROM order_items WHERE order_id = o.id) AS quantity,
                    (SELECT MIN(product_name) FROM order_items WHERE order_id = o.id) AS product_name
             FROM orders o JOIN customers c ON c.id = o.customer_id"""
    params: list = []
    if status:
        sql += " WHERE o.status = ?"
        params.append(status)
    sql += " ORDER BY o.id DESC LIMIT ?"
    params.append(limit)
    return db.fetch_all(sql, params)


# ---------------------------------------------------------------- serializers
SHIPPING_STATUS = {
    "shipped": "shipped", "delivered": "delivered",
}


def customer_timeline(o: dict) -> list:
    """Customer-facing steps. Contains no internal supplier information."""
    paid = o["payment_status"] == "paid" or o["status"] in ("shipped", "delivered")
    preparing = o["status"] in ("awaiting_supplier_approval", "supplier_approved", "supplier_ordered", "shipped", "delivered")
    return [
        {"key": "received", "label": "Order received", "done": True},
        {"key": "paid", "label": "Payment confirmed", "done": paid},
        {"key": "preparing", "label": "Preparing order", "done": preparing and paid},
        {"key": "shipped", "label": "Shipped", "done": o["status"] in ("shipped", "delivered")},
        {"key": "delivered", "label": "Delivered", "done": o["status"] == "delivered"},
    ]


def to_customer_view(db: Database, o: dict) -> dict:
    """What the BUYER may see. No supplier cost, profit, supplier ids or internal notes."""
    status = o["status"]
    public_status = {
        "pending_payment": "awaiting_payment", "awaiting_supplier_approval": "preparing",
        "supplier_approved": "preparing", "supplier_ordered": "preparing",
    }.get(status, status)
    tracking = None
    if o["tracking_number"]:
        tracking = {"carrier": o["tracking_carrier"], "number": o["tracking_number"], "url": o["tracking_url"]}
    return {
        "reference": o["reference"],
        "status": public_status,
        "payment_status": o["payment_status"] if o["payment_status"] in ("paid", "refunded", "pending", "failed") else "pending",
        "total": minor_to_major(o["total"]),
        "currency": o["currency"],
        "created_at": o["created_at"],
        "items": [{"name": i["product_name"], "quantity": i["quantity"], "unit_price": minor_to_major(i["unit_price"])} for i in items(db, o["id"])],
        "timeline": customer_timeline(o),
        "tracking": tracking,
    }


def to_admin_row(o: dict) -> dict:
    return {
        "id": o["id"], "reference": o["reference"],
        "customer": o.get("customer_name"), "email": o.get("customer_email"),
        "product": o.get("product_name"), "quantity": o.get("quantity"),
        "total": minor_to_major(o["total"]), "currency": o["currency"],
        "supplier_cost": minor_to_major(o["est_cost"]),
        "estimated_profit": minor_to_major(o["est_profit"]),
        "status": o["status"], "payment_status": o["payment_status"],
        "supplier_status": o["supplier_status"],
        "shipping_status": "delivered" if o["status"] == "delivered" else "shipped" if o["status"] == "shipped" else "not_shipped",
        "tracking_number": o["tracking_number"], "needs_review": bool(o["needs_review"]),
        "created_at": o["created_at"],
    }


def to_admin_detail(db: Database, o: dict) -> dict:
    c = customer(db, o["customer_id"])
    row = to_admin_row({**o, "customer_name": c["name"], "customer_email": c["email"],
                        "quantity": sum(i["quantity"] for i in items(db, o["id"])),
                        "product_name": ", ".join(i["product_name"] for i in items(db, o["id"]))})
    row.update({
        "subtotal": minor_to_major(o["subtotal"]), "shipping_cost": minor_to_major(o["shipping_cost"]),
        "fees_and_reserves": minor_to_major(o["est_fees"]),
        "payment_provider": o["payment_provider"], "payment_reference": o["payment_reference"],
        "supplier_order_id": o["supplier_order_id"],
        "tracking_carrier": o["tracking_carrier"], "tracking_url": o["tracking_url"],
        "review_note": o["review_note"], "paid_at": o["paid_at"], "updated_at": o["updated_at"],
        "ship_to": {"name": o["shipping_name"], "country": o["shipping_country"],
                    "address": jloads(o["shipping_address"], {})},
        "items": [{"product_id": i["product_id"], "name": i["product_name"], "quantity": i["quantity"],
                   "unit_price": minor_to_major(i["unit_price"]), "unit_landed_cost": minor_to_major(i["unit_landed_cost"]),
                   "supplier_product_id": i["supplier_product_id"]} for i in items(db, o["id"])],
        "events": events(db, o["id"]),
    })
    return row
