import json
from tests.conftest import WEBHOOK_SECRET, pay, place_order, signed_event
from backend.models import orders as order_repo, products as product_repo
from backend.payments import sandbox
from backend.payments.base import InvalidWebhookSignature
import pytest


def get_order(ctx, ref):
    return order_repo.get_by_reference(ctx.db, ref)


def test_valid_signed_webhook_marks_paid_and_awaits_supplier_approval(client, ctx):
    order = place_order(client)
    r = pay(client, ctx, order)
    assert r.status_code == 200 and r.get_json()["outcome"] == "paid"
    o = get_order(ctx, order["order_reference"])
    assert o["payment_status"] == "paid" and o["status"] == "awaiting_supplier_approval"
    assert o["supplier_status"] == "pending_approval" and o["paid_at"]


def test_webhook_with_bad_or_missing_signature_rejected(client, ctx):
    order = place_order(client)
    o = get_order(ctx, order["order_reference"])
    body = sandbox.build_event_body(type_="payment.succeeded", reference=o["payment_reference"],
                                    order_reference=o["reference"], amount_minor=o["total"], currency="USD")
    for headers in ({}, {sandbox.SIGNATURE_HEADER: "deadbeef"},
                    {sandbox.SIGNATURE_HEADER: sandbox.sign("wrong-secret", body)}):
        r = client.post("/api/webhooks/payment", data=body, headers=headers)
        assert r.status_code == 400
    assert get_order(ctx, o["reference"])["payment_status"] == "pending"


def test_tampered_body_fails_signature(client, ctx):
    order = place_order(client)
    o = get_order(ctx, order["order_reference"])
    body, headers = signed_event(ctx, reference=o["payment_reference"], order_reference=o["reference"], amount=o["total"])
    forged = body.replace(str(o["total"]).encode(), b"1")
    assert client.post("/api/webhooks/payment", data=forged, headers=headers).status_code == 400


def test_webhook_is_idempotent_same_event_id(client, ctx):
    order = place_order(client)
    o = get_order(ctx, order["order_reference"])
    stock_before = product_repo.get_by_slug(ctx.db, "flower-garden-snuffle-mat")["stock"]
    kw = dict(reference=o["payment_reference"], order_reference=o["reference"], amount=o["total"], event_id="evt_same")
    outcomes = [client.post("/api/webhooks/payment", data=b, headers=h).get_json()["outcome"]
                for b, h in (signed_event(ctx, **kw) for _ in range(3))]
    assert outcomes == ["paid", "duplicate", "duplicate"]
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM payment_events WHERE event_id='evt_same'")["n"] == 1
    assert product_repo.get_by_slug(ctx.db, "flower-garden-snuffle-mat")["stock"] == stock_before - 1   # decremented ONCE
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM orders")["n"] == 1                               # no duplicate orders


def test_replayed_payment_with_new_event_id_does_not_double_apply(client, ctx):
    order = place_order(client)
    assert pay(client, ctx, order).get_json()["outcome"] == "paid"
    assert pay(client, ctx, order).get_json()["outcome"] == "already_paid"
    assert ctx.db.fetch_one("SELECT stock FROM products")["stock"] == 24


def test_amount_mismatch_is_not_treated_as_paid(client, ctx):
    order = place_order(client)
    r = pay(client, ctx, order, amount=100)
    assert r.get_json()["outcome"] == "amount_mismatch"
    o = get_order(ctx, order["order_reference"])
    assert o["payment_status"] == "review" and o["needs_review"] == 1 and o["status"] == "pending_payment"


def test_currency_mismatch_not_paid(client, ctx):
    order = place_order(client)
    assert pay(client, ctx, order, currency="EUR").get_json()["outcome"] == "amount_mismatch"


def test_failed_payment_event(client, ctx):
    order = place_order(client)
    o = get_order(ctx, order["order_reference"])
    body, headers = signed_event(ctx, type_="payment.failed", reference=o["payment_reference"],
                                 order_reference=o["reference"], amount=o["total"])
    assert client.post("/api/webhooks/payment", data=body, headers=headers).get_json()["outcome"] == "failed"
    assert get_order(ctx, o["reference"])["payment_status"] == "failed"
    assert get_order(ctx, o["reference"])["status"] == "pending_payment"


def test_unknown_order_event_is_recorded_not_applied(client, ctx):
    body, headers = signed_event(ctx, reference="sbx_nope", order_reference="ORD-NOPE", amount=1)
    assert client.post("/api/webhooks/payment", data=body, headers=headers).get_json()["outcome"] == "unknown_order"


def test_customer_cannot_mark_payment_successful(client, ctx):
    order = place_order(client)
    ref, token = order["order_reference"], order["status_url"].split("t=")[1]
    for url in (f"/api/orders/{ref}/status?token={token}&payment_success=true&status=paid&payment_status=paid",):
        r = client.get(url)
        assert r.get_json()["payment_status"] == "pending"
    for path in ("/api/orders/%s/status" % ref, "/api/checkout", "/api/orders/%s" % ref):
        r = client.post(path, json={"payment_success": True, "payment_status": "paid", "status": "paid"})
        assert r.status_code in (404, 405, 415, 422)
    assert get_order(ctx, ref)["payment_status"] == "pending"
    assert client.put(f"/api/orders/{ref}/status", json={"payment_success": True}).status_code in (404, 405)


def test_return_to_site_reconciles_with_provider_server_side(client, ctx):
    """Customer comes back before the webhook arrives: backend asks the provider itself."""
    order = place_order(client)
    token = order["status_url"].split("t=")[1]
    assert client.get(f"/api/orders/{order['order_reference']}/status?token={token}").get_json()["status"] == "awaiting_payment"
    o = get_order(ctx, order["order_reference"])
    ctx.payments.set_status(o["payment_reference"], "paid")             # provider says paid
    r = client.get(f"/api/orders/{order['order_reference']}/status?token={token}").get_json()
    assert r["status"] == "preparing" and r["payment_status"] == "paid"


def test_order_status_needs_email_or_token(client, ctx):
    order = place_order(client)
    ref = order["order_reference"]
    assert client.get(f"/api/orders/{ref}/status").status_code == 404
    assert client.get(f"/api/orders/{ref}/status?email=other@example.com").status_code == 404
    assert client.get(f"/api/orders/{ref}/status?token=wrong").status_code == 404
    assert client.get("/api/orders/ORD-DOESNOTEXIST/status?email=jane@example.com").status_code == 404
    ok = client.get(f"/api/orders/{ref}/status?email=JANE@example.com")
    assert ok.status_code == 200


def test_customer_status_view_hides_internal_data(client, ctx, paid_order):
    text = client.get(f"/api/orders/{paid_order['reference']}/status?email=jane@example.com").get_data(as_text=True)
    for leak in ("est_cost", "est_profit", "supplier", "landed", "profit", "needs_review", "review_note", "payment_reference", "sbx_"):
        assert leak not in text, leak


def test_customer_timeline_progresses(client, ctx, paid_order):
    v = client.get(f"/api/orders/{paid_order['reference']}/status?email=jane@example.com").get_json()
    done = {s["key"]: s["done"] for s in v["timeline"]}
    assert done == {"received": True, "paid": True, "preparing": True, "shipped": False, "delivered": False}


def test_dev_payment_page_goes_through_signed_webhook(client, ctx):
    order = place_order(client)
    from urllib.parse import urlparse
    path = urlparse(order["redirect_url"]).path
    page = client.get(path)
    assert page.status_code == 200 and b"SANDBOX" in page.data
    r = client.post(path + "/succeeded")
    assert r.status_code == 303
    assert get_order(ctx, order["order_reference"])["payment_status"] == "paid"


def test_stripe_signature_verification():
    import hashlib, hmac, time
    from backend.payments.stripe_provider import StripePaymentProvider
    p = StripePaymentProvider("sk_test_x", "whsec_abc")
    body = json.dumps({"id": "evt_1", "type": "checkout.session.completed", "data": {"object": {
        "id": "cs_1", "payment_status": "paid", "amount_total": 2499, "currency": "usd", "client_reference_id": "ORD-1"}}}).encode()
    ts = str(int(time.time()))
    sig = hmac.new(b"whsec_abc", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    ev = p.parse_webhook({"Stripe-Signature": f"t={ts},v1={sig}"}, body)
    assert ev.type == "payment_succeeded" and ev.amount_minor == 2499 and ev.currency == "USD" and ev.order_reference == "ORD-1"
    with pytest.raises(InvalidWebhookSignature):
        p.parse_webhook({"Stripe-Signature": f"t={ts},v1=00"}, body)
    with pytest.raises(InvalidWebhookSignature):
        p.parse_webhook({}, body)
    old = str(int(time.time()) - 10_000)
    old_sig = hmac.new(b"whsec_abc", f"{old}.".encode() + body, hashlib.sha256).hexdigest()
    with pytest.raises(InvalidWebhookSignature):
        p.parse_webhook({"Stripe-Signature": f"t={old},v1={old_sig}"}, body)
