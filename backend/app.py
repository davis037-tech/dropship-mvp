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


def _bootstrap_cj_product(ctx: AppContext) -> None:
    """Import the first usable CJ product and a real CJ variant into the storefront."""
    if ctx.settings.supplier_mode != "cj" or not getattr(ctx.supplier, "configured", False):
        return
    if product_repo.list_all(ctx.db, public_only=True):
        return
    try:
        rows = ctx.supplier.list_products(page=1, size=20)
        for row in rows:
            pid = str(row.get("pid") or row.get("productId") or row.get("id") or "").strip()
            if not pid:
                continue
            try:
                detail = ctx.supplier.get_product(pid)
            except Exception as exc:
                log.warning("Skipping CJ product %s: detail lookup failed: %s", pid, exc)
                continue
            sid = str(detail.supplier_product_id or "").strip()
            name = str(detail.title or row.get("nameEn") or row.get("productNameEn") or "CJ Product").strip()
            price_minor = int(detail.price_minor or 0)
            stock = int(detail.stock or 0)
            image = (detail.images[0] if detail.images else "") or row.get("bigImage") or row.get("productImage") or ""
            if not sid or price_minor <= 0 or stock <= 0:
                continue
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:90] or ("cj-" + sid[:12].lower())
            base_slug, n = slug, 2
            while product_repo.get_by_slug(ctx.db, slug):
                slug = f"{base_slug}-{n}"
                n += 1
            images = [{"url": image, "alt": name}] if isinstance(image, str) and image.startswith("https://") else []
            base = {
                "name": name, "slug": slug,
                "description": "Product fulfilled by our supplier. Shipping times and final price are shown at checkout.",
                "supplier_product_id": sid, "supplier_platform": "cj", "supplier_currency": "USD",
                "supplier_cost": price_minor, "estimated_shipping_cost": 0, "agent_fee": 0,
                "other_supplier_costs": 0, "stock": stock, "currency": ctx.settings.store_currency,
                "status": "active", "retail_price": 0, "images": images, "specifications": []
            }
            costs = product_sync.compute_costs(ctx, base)
            product_repo.create(ctx.db, {**base, **costs, "retail_price": costs["recommended_price"]})
            log.info("Bootstrapped CJ product %s (variant %s) into storefront", pid, sid)
            return
        log.warning("CJ bootstrap found no usable in-stock variant")
    except Exception as exc:
        log.error("CJ product bootstrap failed: %s", exc)


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
    _bootstrap_cj_product(ctx)
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
