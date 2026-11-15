"""Application factory."""
from __future__ import annotations

import logging
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
from backend.suppliers import build_supplier_adapter

log = logging.getLogger(__name__)


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
