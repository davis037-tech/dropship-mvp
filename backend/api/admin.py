"""Admin API. EVERY route except /login requires a valid admin bearer token."""
from __future__ import annotations

import hmac

from flask import Blueprint, current_app, g, jsonify, request

from backend.api.errors import ApiError
from backend.api.public import validated
from backend.api.schemas import (ApproveReviewBody, LoginBody, ProductBody, RefundBody, SettingsBody,
                                 ShippingZoneBody, StatusBody, TrackingBody)
from backend.api.security import rate_limit
from backend.auth.passwords import hash_password, verify_password
from backend.auth.tokens import issue_admin_token, verify_admin_token
from backend.models import orders as order_repo
from backend.models import products as product_repo
from backend.models import shipping as shipping_repo
from backend.models.common import major_to_minor, minor_to_major, utcnow
from backend.services import order_workflow, pricing, product_sync
from backend.services.pricing import PricingError

bp = Blueprint("admin", __name__, url_prefix="/api/admin")
_DUMMY_HASH = hash_password("dummy-password-for-timing", n=2**10)


def ctx():
    return current_app.extensions["shop"]


@bp.before_request
def require_admin():
    if request.method == "OPTIONS" or request.endpoint == "admin.login":
        return None
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise ApiError(401, "unauthorized", "Authentication required")
    email = verify_admin_token(ctx().settings.secret_key, header[7:].strip())
    if not email or not ctx().settings.admin_email or not hmac.compare_digest(email, ctx().settings.admin_email):
        raise ApiError(401, "unauthorized", "Invalid or expired token")
    g.admin_email = email


# ------------------------------------------------------------------ auth
@bp.post("/login")
@rate_limit("login", 5)
def login():
    s = ctx().settings
    body = validated(LoginBody)
    if not s.admin_email or not s.admin_password_hash:
        raise ApiError(503, "admin_not_configured", "Admin login is not configured on this server.")
    email_ok = hmac.compare_digest(body.email.strip().lower().encode(), s.admin_email.encode())
    pw_ok = verify_password(body.password, s.admin_password_hash if email_ok else _DUMMY_HASH)
    if not (email_ok and pw_ok):
        raise ApiError(401, "invalid_credentials", "Invalid email or password")
    return jsonify({"token": issue_admin_token(s.secret_key, s.admin_email, s.admin_token_ttl_minutes),
                    "expires_in": s.admin_token_ttl_minutes * 60})


@bp.get("/me")
def me():
    return jsonify({"email": g.admin_email})


# ------------------------------------------------------------------ dashboard
@bp.get("/dashboard")
def dashboard():
    db = ctx().db
    one = lambda sql, p=(): int(list(db.fetch_one(sql, p).values())[0] or 0)  # int(): PostgreSQL returns SUM() as Decimal  # noqa: E731
    live = "payment_status = 'paid' AND status NOT IN ('cancelled','refunded')"
    return jsonify({
        "currency": ctx().settings.store_currency,
        "total_orders": one("SELECT COUNT(*) FROM orders"),
        "paid_orders": one("SELECT COUNT(*) FROM orders WHERE payment_status = 'paid'"),
        "awaiting_supplier_approval": one("SELECT COUNT(*) FROM orders WHERE status = 'awaiting_supplier_approval'"),
        "shipped": one("SELECT COUNT(*) FROM orders WHERE status = 'shipped'"),
        "delivered": one("SELECT COUNT(*) FROM orders WHERE status = 'delivered'"),
        "revenue": minor_to_major(one(f"SELECT COALESCE(SUM(total),0) FROM orders WHERE {live}")),
        "estimated_profit": minor_to_major(one(f"SELECT COALESCE(SUM(est_profit),0) FROM orders WHERE {live}")),
        "orders_needing_review": one("SELECT COUNT(*) FROM orders WHERE needs_review = 1"),
        "products_needing_review": one("SELECT COUNT(*) FROM products WHERE status = 'needs_review'"),
    })


# ------------------------------------------------------------------ products
def _economics(p: dict):
    c = ctx()
    try:
        return pricing.economics_at_price(p["retail_price"], p["landed_cost"], c.settings.pricing).as_admin_dict()
    except PricingError:
        return None


def _admin_product(p: dict) -> dict:
    return product_repo.to_admin(p, _economics(p))


_MONEY = ("supplier_cost", "estimated_shipping_cost", "agent_fee", "other_supplier_costs", "retail_price")


def _apply_body(body: ProductBody, existing: dict | None) -> dict:
    c = ctx()
    data = body.model_dump(exclude_none=True, exclude={"use_recommended_price"})
    for k in _MONEY:
        if k in data:
            data[k] = major_to_minor(data[k])
    if "supplier_currency" in data:
        data["supplier_currency"] = data["supplier_currency"].upper()
    if "images" in data:
        data["images"] = [i for i in data["images"]]
    merged = {**(existing or {"supplier_cost": 0, "supplier_currency": "CNY", "estimated_shipping_cost": 0,
                              "agent_fee": 0, "other_supplier_costs": 0, "retail_price": 0, "stock": 0,
                              "status": "draft"}), **data}
    try:
        costs = product_sync.compute_costs(c, merged)
    except PricingError as exc:
        raise ApiError(422, "pricing_not_configured", str(exc))
    data.update(costs)
    if body.use_recommended_price:
        data["retail_price"] = costs["recommended_price"]
        merged["retail_price"] = costs["recommended_price"]
    if merged.get("status") == "active":
        if merged["retail_price"] <= 0:
            raise ApiError(422, "price_required", "Set a retail price before activating a product.")
        econ = pricing.economics_at_price(merged["retail_price"], costs["landed_cost"], c.settings.pricing)
        if econ.margin < c.settings.pricing.min_margin:
            raise ApiError(422, "margin_below_minimum", "Retail price is below the minimum acceptable margin.", econ.as_admin_dict())
        if (existing or {}).get("status") != "active":
            data["review_reason"] = None
    return data


def _mirror_supplier(p_id: int):
    p = product_repo.get(ctx().db, p_id)
    if p["supplier_product_id"] and not ctx().supplier.supports_live_checks:
        product_repo.upsert_supplier_product(
            ctx().db, supplier=p["supplier_platform"], supplier_product_id=p["supplier_product_id"],
            currency=p["supplier_currency"], cost=p["supplier_cost"], stock=p["stock"],
            status="ok" if p["stock"] > 0 else "out_of_stock")
        product_repo.update(ctx().db, p_id, {"last_synced_at": utcnow()})


@bp.get("/cj/products")
def cj_products():
    """Search the live CJ catalog without changing the storefront."""
    supplier = ctx().supplier
    if supplier.name != "cj" or not hasattr(supplier, "list_products"):
        raise ApiError(409, "cj_not_enabled", "CJ supplier mode is not enabled.")
    keyword = (request.args.get("keyword") or "").strip()
    country = (request.args.get("country") or "").strip().upper()
    try:
        size = min(max(int(request.args.get("size", 20)), 1), 50)
        rows = supplier.list_products(keyword=keyword, page=1, size=size, country_code=country)
    except Exception as exc:
        raise ApiError(502, "cj_fetch_failed", str(exc)[:300])
    out = []
    for row in rows:
        pid = row.get("pid") or row.get("productId") or row.get("id") or ""
        title = row.get("productNameEn") or row.get("nameEn") or row.get("productName") or row.get("name") or ""
        image = row.get("bigImage") or row.get("productImage") or ""
        images = row.get("productImageSet") or ([image] if image else [])
        price = row.get("sellPrice") or row.get("nowPrice") or row.get("discountPrice") or row.get("price") or 0
        stock = row.get("inventory") or row.get("warehouseInventoryNum") or row.get("totalVerifiedInventory") or row.get("availableInventory") or 0
        out.append({"supplier_product_id": str(pid), "name": str(title), "image": image,
                    "images": images[:12] if isinstance(images, list) else [],
                    "supplier_cost": float(price or 0), "supplier_currency": "USD",
                    "stock": int(float(stock or 0))})
    return jsonify({"products": out, "count": len(out)})


@bp.post("/products/import-cj")
def cj_import_products():
    """Import current CJ catalog results as draft products."""
    supplier = ctx().supplier
    if supplier.name != "cj" or not hasattr(supplier, "list_products"):
        raise ApiError(409, "cj_not_enabled", "CJ supplier mode is not enabled.")
    body = request.get_json(silent=True) or {}
    keyword = str(body.get("keyword") or "").strip()
    country = str(body.get("country") or "").strip().upper()
    size = min(max(int(body.get("size", 20)), 1), 50)
    rows = supplier.list_products(keyword=keyword, page=1, size=size, country_code=country)
    created = []
    for row in rows:
        sid = str(row.get("pid") or row.get("productId") or row.get("id") or "")
        if not sid:
            continue
        if ctx().db.fetch_one("SELECT id FROM products WHERE supplier_platform = 'cj' AND supplier_product_id = ?", [sid]):
            continue
        name = str(row.get("productNameEn") or row.get("nameEn") or row.get("productName") or row.get("name") or "").strip()
        if not name:
            continue
        import re
        slug_base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:90] or ("cj-" + sid[:12].lower())
        slug = slug_base
        n = 2
        while product_repo.get_by_slug(ctx().db, slug):
            slug = f"{slug_base}-{n}"
            n += 1
        price = float(row.get("sellPrice") or row.get("nowPrice") or row.get("discountPrice") or row.get("price") or 0)
        stock = int(float(row.get("inventory") or row.get("warehouseInventoryNum") or row.get("totalVerifiedInventory") or row.get("availableInventory") or 0))
        image = row.get("bigImage") or row.get("productImage") or ""
        raw_images = row.get("productImageSet") or ([image] if image else [])
        images = [{"url": u, "alt": name} for u in raw_images if isinstance(u, str) and u.startswith("https://")][:12]
        base = {"name": name, "slug": slug, "description": "Imported from CJ. Review the description, shipping cost and retail price before publishing.",
                "supplier_product_id": sid, "supplier_platform": "cj", "supplier_currency": "USD",
                "supplier_cost": major_to_minor(price), "estimated_shipping_cost": 0, "agent_fee": 0,
                "other_supplier_costs": 0, "stock": max(stock, 0), "currency": ctx().settings.store_currency,
                "status": "draft", "retail_price": 0, "images": images, "specifications": []}
        costs = product_sync.compute_costs(ctx(), base)
        # Make the first real CJ import immediately visible when it has stock
        # and a valid recommended price. This is the MVP storefront path.
        retail = costs["recommended_price"]
        status = "active" if stock > 0 and retail > 0 else "draft"
        pid = product_repo.create(ctx().db, {**base, **costs, "retail_price": retail, "status": status})
        created.append({"id": pid, "name": name, "supplier_product_id": sid, "status": status})
    return jsonify({"created": created, "count": len(created)}), 201


@bp.get("/products")
def products_list():
    return jsonify({"products": [_admin_product(p) for p in product_repo.list_all(ctx().db)]})


@bp.get("/products/<int:pid>")
def products_get(pid):
    p = product_repo.get(ctx().db, pid)
    if not p:
        raise ApiError(404, "not_found", "Product not found")
    return jsonify(_admin_product(p))


@bp.post("/products")
def products_create():
    c = ctx()
    body = validated(ProductBody)
    if not body.name or not body.slug:
        raise ApiError(422, "validation_error", "name and slug are required")
    if product_repo.get_by_slug(c.db, body.slug):
        raise ApiError(409, "slug_taken", "A product with that slug already exists")
    data = _apply_body(body, None)
    data.setdefault("currency", c.settings.store_currency)
    data["currency"] = c.settings.store_currency
    pid = product_repo.create(c.db, data)
    _mirror_supplier(pid)
    return jsonify(_admin_product(product_repo.get(c.db, pid))), 201


@bp.put("/products/<int:pid>")
def products_update(pid):
    c = ctx()
    existing = product_repo.get(c.db, pid)
    if not existing:
        raise ApiError(404, "not_found", "Product not found")
    body = validated(ProductBody)
    if body.slug and body.slug != existing["slug"] and product_repo.get_by_slug(c.db, body.slug):
        raise ApiError(409, "slug_taken", "A product with that slug already exists")
    data = _apply_body(body, existing)
    if data.get("status") == "active" and existing["status"] == "needs_review":
        data["review_reason"] = None
    product_repo.update(c.db, pid, data)
    _mirror_supplier(pid)
    return jsonify(_admin_product(product_repo.get(c.db, pid)))


@bp.delete("/products/<int:pid>")
def products_delete(pid):
    """Soft delete: orders keep referencing the row, so it is archived (hidden + unpurchasable)."""
    if not product_repo.get(ctx().db, pid):
        raise ApiError(404, "not_found", "Product not found")
    product_repo.update(ctx().db, pid, {"status": "archived"})
    return jsonify({"archived": True})


@bp.post("/products/<int:pid>/sync")
def products_sync(pid):
    r = product_sync.sync_product(ctx(), pid)
    p = product_repo.get(ctx().db, pid)
    return jsonify({"result": {"skipped": r.skipped, "error": r.error, "flagged": r.flagged,
                               "reason": r.reason, "changes": r.changes},
                    "product": _admin_product(p) if p else None})


@bp.post("/products/sync-all")
def products_sync_all():
    results = product_sync.sync_all(ctx())
    return jsonify({"results": [{"product_id": r.product_id, "skipped": r.skipped, "error": r.error,
                                 "flagged": r.flagged, "changes": r.changes} for r in results]})


@bp.post("/products/<int:pid>/approve-review")
def products_approve(pid):
    body = validated(ApproveReviewBody)
    p = product_sync.approve_review(ctx(), pid, apply_recommended_price=body.apply_recommended_price)
    return jsonify(_admin_product(p))


# ------------------------------------------------------------------ orders
@bp.get("/orders")
def orders_list():
    rows = order_repo.list_orders(ctx().db, request.args.get("status") or None)
    return jsonify({"orders": [order_repo.to_admin_row(o) for o in rows]})


@bp.get("/orders/<int:oid>")
def orders_get(oid):
    o = order_repo.get(ctx().db, oid)
    if not o:
        raise ApiError(404, "not_found", "Order not found")
    return jsonify(order_repo.to_admin_detail(ctx().db, o))


@bp.put("/orders/<int:oid>/status")
def orders_status(oid):
    b = validated(StatusBody)
    order_workflow.change_status(ctx(), oid, b.status, g.admin_email, supplier_order_id=b.supplier_order_id,
                                 override=b.override, refund=b.refund,
                                 acknowledge_supplier_order=b.acknowledge_supplier_order)
    return jsonify(order_repo.to_admin_detail(ctx().db, order_repo.get(ctx().db, oid)))


@bp.put("/orders/<int:oid>/tracking")
def orders_tracking(oid):
    b = validated(TrackingBody)
    order_workflow.set_tracking(ctx(), oid, g.admin_email, b.tracking_number, b.carrier, b.url)
    return jsonify(order_repo.to_admin_detail(ctx().db, order_repo.get(ctx().db, oid)))


@bp.post("/orders/<int:oid>/refund")
def orders_refund(oid):
    b = validated(RefundBody)
    order_workflow.refund_order(ctx(), oid, g.admin_email, b.reason)
    return jsonify(order_repo.to_admin_detail(ctx().db, order_repo.get(ctx().db, oid)))


# ------------------------------------------------------------------ shipping + settings
@bp.get("/shipping")
def shipping_list():
    return jsonify({"zones": [shipping_repo.zone_admin(z) for z in shipping_repo.list_zones(ctx().db)]})


@bp.put("/shipping/<country>")
def shipping_upsert(country):
    if len(country) != 2 or not country.isalpha():
        raise ApiError(422, "validation_error", "country must be a 2-letter ISO code")
    b = validated(ShippingZoneBody)
    shipping_repo.upsert_zone(ctx().db, country, shipping_cost=major_to_minor(b.shipping_cost),
                              delivery_days_min=b.delivery_days_min, delivery_days_max=b.delivery_days_max,
                              tracking_available=b.tracking_available, enabled=b.enabled)
    return jsonify(shipping_repo.zone_admin(shipping_repo.get_zone(ctx().db, country, enabled_only=False)))


@bp.delete("/shipping/<country>")
def shipping_delete(country):
    shipping_repo.delete_zone(ctx().db, country)
    return jsonify({"deleted": True})


@bp.get("/settings")
def settings_get():
    return jsonify(shipping_repo.get_settings(ctx().db))


@bp.put("/settings")
def settings_put():
    b = validated(SettingsBody)
    mapping = {"returns_enabled": "returns.enabled", "returns_window_days": "returns.window_days",
               "returns_return_shipping_paid_by": "returns.return_shipping_paid_by",
               "returns_refund_method": "returns.refund_method", "returns_policy_text": "returns.policy_text"}
    for field, key in mapping.items():
        v = getattr(b, field)
        if v is not None:
            shipping_repo.set_setting(ctx().db, key, ("true" if v else "false") if isinstance(v, bool) else str(v))
    return jsonify(shipping_repo.get_settings(ctx().db))


@bp.post("/pricing/preview")
def pricing_preview():
    """Calculator: what would the engine recommend for these inputs? (admin only)"""
    b = validated(ProductBody)
    merged = {"supplier_cost": major_to_minor(b.supplier_cost or 0), "supplier_currency": (b.supplier_currency or "CNY").upper(),
              "estimated_shipping_cost": major_to_minor(b.estimated_shipping_cost or 0),
              "agent_fee": major_to_minor(b.agent_fee or 0), "other_supplier_costs": major_to_minor(b.other_supplier_costs or 0)}
    try:
        costs = product_sync.compute_costs(ctx(), merged)
    except PricingError as exc:
        raise ApiError(422, "pricing_not_configured", str(exc))
    price = major_to_minor(b.retail_price) if b.retail_price else costs["recommended_price"]
    return jsonify({"landed_cost": minor_to_major(costs["landed_cost"]),
                    "recommended_price": minor_to_major(costs["recommended_price"]),
                    "economics": pricing.economics_at_price(price, costs["landed_cost"], ctx().settings.pricing).as_admin_dict()})
