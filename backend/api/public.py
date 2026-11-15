"""Public (customer-facing) API. Only allow-listed fields are ever returned."""
from __future__ import annotations

import hmac

from flask import Blueprint, current_app, jsonify, request
from pydantic import ValidationError
from werkzeug.exceptions import HTTPException

from backend.api.errors import ApiError
from backend.api.schemas import CheckoutRequest, QuoteRequest
from backend.api.security import rate_limit
from backend.auth.tokens import order_access_token
from backend.models import orders as order_repo
from backend.models import products as product_repo
from backend.models import shipping as shipping_repo
from backend.payments.base import InvalidWebhookSignature
from backend.services import checkout, payment_processing

bp = Blueprint("public", __name__, url_prefix="/api")


def ctx():
    return current_app.extensions["shop"]


def validated(model):
    if not request.is_json:
        raise ApiError(415, "json_required", "Send a JSON body with Content-Type: application/json")
    try:
        return model.model_validate(request.get_json(silent=False))
    except ValidationError as exc:
        raise ApiError(422, "validation_error", "Some fields are invalid.", [
            {"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]} for e in exc.errors()])
    except HTTPException:
        raise
    except Exception:
        raise ApiError(400, "bad_json", "Malformed JSON body")


def base_url() -> str:
    return ctx().settings.public_base_url or request.host_url.rstrip("/")


@bp.get("/config")
def public_config():
    s = ctx().settings
    return jsonify({
        "store_name": s.store_name, "currency": s.store_currency, "max_quantity": s.max_quantity,
        "payment_test_mode": s.payment_provider == "sandbox",
    })


@bp.get("/products")
def list_products():
    c = ctx()
    rows = product_repo.list_all(c.db, public_only=True)
    return jsonify({"products": [product_repo.to_public(p, c.settings.max_quantity) for p in rows]})


@bp.get("/products/<slug>")
def get_product(slug):
    c = ctx()
    p = product_repo.get_by_slug(c.db, slug)
    if p is None or p["status"] not in product_repo.PUBLIC_STATUSES:
        raise ApiError(404, "not_found", "Product not found")
    return jsonify(product_repo.to_public(p, c.settings.max_quantity))


@bp.get("/shipping/countries")
def shipping_countries():
    zones = shipping_repo.list_zones(ctx().db, enabled_only=True)
    return jsonify({"countries": [shipping_repo.zone_public(z) for z in zones]})


@bp.get("/policies")
def policies():
    s = shipping_repo.get_settings(ctx().db)
    return jsonify({
        "returns": {
            "enabled": s["returns.enabled"] == "true",
            "window_days": int(s["returns.window_days"]) if s["returns.window_days"].isdigit() else None,
            "return_shipping_paid_by": s["returns.return_shipping_paid_by"] or None,
            "refund_method": s["returns.refund_method"] or None,
            "policy_text": s["returns.policy_text"],
        }
    })


@bp.post("/checkout/quote")
@rate_limit("quote", 60)
def checkout_quote():
    body = validated(QuoteRequest)
    return jsonify(checkout.quote(ctx(), body.items, body.country))


@bp.post("/checkout")
@rate_limit("checkout", 10)
def create_checkout():
    body = validated(CheckoutRequest)
    return jsonify(checkout.create_checkout(ctx(), body, base_url())), 201


@bp.get("/orders/<reference>/status")
@rate_limit("order_status", 30)
def order_status(reference):
    """Look up with the order reference plus EITHER the buyer's email or the link token.
    Any other query parameter (e.g. payment_success=true) is ignored: payment state is
    only ever changed by verified provider notifications."""
    c = ctx()
    order = order_repo.get_by_reference(c.db, reference.upper())
    email = (request.args.get("email") or "").strip().lower()
    token = request.args.get("token") or ""
    authorised = False
    if order is not None:
        cust = order_repo.customer(c.db, order["customer_id"])
        authorised = (bool(email) and hmac.compare_digest(email, cust["email"])) or (
            bool(token) and hmac.compare_digest(token, order_access_token(c.settings.secret_key, order["reference"])))
    if not authorised:
        raise ApiError(404, "not_found", "We could not find an order with those details.")
    payment_processing.reconcile_order(c, order)
    order = order_repo.get(c.db, order["id"])
    return jsonify(order_repo.to_customer_view(c.db, order))


@bp.post("/webhooks/payment")
def payment_webhook():
    c = ctx()
    raw = request.get_data(cache=False)
    try:
        event = c.payments.parse_webhook(request.headers, raw)
    except InvalidWebhookSignature:
        raise ApiError(400, "invalid_signature", "Invalid webhook signature")
    outcome = payment_processing.process_payment_event(c, event)
    return jsonify({"received": True, "outcome": outcome})
