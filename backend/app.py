"""Application factory."""
from __future__ import annotations

import logging
import re
from typing import Mapping, Optional

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from backend.api import admin, dev, pages, public, security
from backend.api.errors import ApiError
from backend.config.settings import Settings
from backend.context import AppContext
from backend.database.db import Database
from backend.database.migrate import run_migrations
from backend.payments.provider import build_payment_provider
from backend.models import products as product_repo
from backend.models.common import major_to_minor
from backend.services import product_sync
from backend.suppliers import build_supplier_adapter

log = logging.getLogger(__name__)
\n\ndef _bootstrap_cj_product(ctx: AppContext) -> None:\n    """Put one in-stock CJ product on the storefront when the catalog is empty.\n\n    This is intentionally a one-product bootstrap for the MVP; it does nothing\n    once an active product already exists.\n    """\n    if ctx.settings.supplier_mode != "cj" or not getattr(ctx.supplier, "configured", False):\n        return\n    if product_repo.list_all(ctx.db, public_only=True):\n        return\n    if ctx.db.fetch_one("SELECT id FROM products WHERE supplier_platform = 'cj' LIMIT 1"):\n        return\n    try:\n        rows = ctx.supplier.list_products(page=1, size=10)\n        for row in rows:\n            sid = str(row.get("pid") or row.get("productId") or row.get("id") or "")\n            name = str(row.get("productNameEn") or row.get("nameEn") or row.get("productName") or row.get("name") or "").strip()\n            price = float(row.get("sellPrice") or row.get("nowPrice") or row.get("discountPrice") or row.get("price") or 0)\n            stock = int(float(row.get("inventory") or row.get("warehouseInventoryNum") or row.get("totalVerifiedInventory") or row.get("availableInventory") or 0))\n            image = row.get("bigImage") or row.get("productImage") or ""\n            if not sid or not name or price <= 0 or stock <= 0:\n                continue\n            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:90] or ("cj-" + sid[:12].lower())\n            base_slug, n = slug, 2\n            while product_repo.get_by_slug(ctx.db, slug):\n                slug = f"{base_slug}-{n}"\n                n += 1\n            images = [{"url": image, "alt": name}] if isinstance(image, str) and image.startswith("https://") else []\n            base = {"name": name, "slug": slug,\n                    "description": "Product fulfilled by our supplier. Shipping times and final price are shown at checkout.",\n                    "supplier_product_id": sid, "supplier_platform": "cj", "supplier_currency": "USD",\n                    "supplier_cost": major_to_minor(price), "estimated_shipping_cost": 0, "agent_fee": 0,\n                    "other_supplier_costs": 0, "stock": stock, "currency": ctx.settings.store_currency,\n                    "status": "active", "retail_price": 0, "images": images, "specifications": []}\n            costs = product_sync.compute_costs(ctx, base)\n            product_repo.create(ctx.db, {**base, **costs, "retail_price": costs["recommended_price"]})\n            log.info("Bootstrapped CJ product %s (%s) into storefront", sid, name[:80])\n            return\n        log.warning("CJ bootstrap found no in-stock priced product")\n    except Exception as exc:\n        log.error("CJ product bootstrap failed: %s", exc)\n

def build_context(env: Optional[Mapping] = None) -> AppContext:
    settings = Settings.from_env(env)
    db = Database(settings.database_url)
    if settings.auto_migrate:
        run_migrations(db)
    holder = {}

    def base_url():
        from flask import has_request_context, request
        if settings.public_base_url:
            return settings.public_base_url
        return request.host_url.rstrip("/") if has_request_context() else "http://localhost:5000"

    supplier = build_supplier_adapter(settings)
    if settings.supplier_mode == "cj" and getattr(supplier, "configured", False):
        try:
            supplier.authentication_check()
            log.info("CJ API authentication succeeded")
        except Exception as exc:
            log.error("CJ API authentication failed: %s", exc)
    ctx = AppContext(settings=settings, db=db, supplier=supplier,
                     payments=build_payment_provider(settings, base_url), limiter=security.RateLimiter())
    holder["ctx"] = ctx
    return ctx


def create_app(env: Optional[Mapping] = None) -> Flask:
    ctx = build_context(env)
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 256 * 1024
    app.config["JSON_SORT_KEYS"] = False
    app.extensions["shop"] = ctx
    if ctx.settings.trust_proxy:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    security.install(app, ctx.settings.cors_allowed_origins, ctx.settings.is_production)
    app.register_blueprint(public.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(pages.bp)
    if ctx.settings.payment_provider == "sandbox" and not ctx.settings.is_production:
        app.register_blueprint(dev.bp)

    @app.get("/health")
    def _health():
        return jsonify({"status": "ok", "service": "dropship-mvp"})

    @app.errorhandler(ApiError)
    def _api_error(e: ApiError):
        return jsonify(e.to_dict()), e.status

    @app.errorhandler(HTTPException)
    def _http_error(e: HTTPException):
        from flask import request
        if request.path.startswith("/api/"):
            return jsonify({"error": {"code": e.name.lower().replace(" ", "_"), "message": e.description}}), e.code
        return e

    @app.errorhandler(Exception)
    def _unhandled(e: Exception):
        log.exception("unhandled error")
        return jsonify({"error": {"code": "server_error", "message": "Something went wrong on our side."}}), 500

    @app.teardown_appcontext
    def _close_db(_exc):
        ctx.db.close()

    return app
