import json
import pytest

from backend.app import create_app
from backend.auth.passwords import hash_password
from backend.database.seed import seed_sample
from backend.payments import sandbox

ADMIN_EMAIL = "owner@example.com"
ADMIN_PASSWORD = "correct horse battery staple"
WEBHOOK_SECRET = "whsec_unit_test_secret"


@pytest.fixture
def env(tmp_path):
    return {
        "APP_ENV": "test", "DATABASE_URL": f"sqlite:///{tmp_path}/test.db", "SECRET_KEY": "k" * 48,
        "PAYMENT_WEBHOOK_SECRET": WEBHOOK_SECRET, "PAYMENT_PROVIDER": "sandbox", "SUPPLIER_MODE": "mock",
        "ADMIN_EMAIL": ADMIN_EMAIL, "ADMIN_PASSWORD_HASH": hash_password(ADMIN_PASSWORD, n=2**10),
        "SUPPLIER_FX_RATE": "0.14", "PRICE_ENDING_CENTS": "99", "RATE_LIMIT_ENABLED": "false",
        "PUBLIC_BASE_URL": "http://localhost:5000",
    }


@pytest.fixture
def app(env):
    app = create_app(env)
    seed_sample(app.extensions["shop"])
    return app


@pytest.fixture
def ctx(app):
    return app.extensions["shop"]


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin_headers(client):
    r = client.post("/api/admin/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.data
    return {"Authorization": "Bearer " + r.get_json()["token"]}


CHECKOUT = {
    "items": [{"slug": "flower-garden-snuffle-mat", "quantity": 1}],
    "country": "US",
    "customer": {"name": "Jane Buyer", "email": "jane@example.com"},
    "address": {"line1": "1 Test Street", "city": "Springfield", "postal_code": "12345"},
}


def checkout_payload(**over):
    return {**json.loads(json.dumps(CHECKOUT)), **over}


def place_order(client, **over):
    r = client.post("/api/checkout", json=checkout_payload(**over))
    assert r.status_code == 201, r.data
    return r.get_json()


def signed_event(ctx, *, type_="payment.succeeded", reference, order_reference, amount, currency="USD", event_id=None):
    body = sandbox.build_event_body(event_id=event_id, type_=type_, reference=reference,
                                    order_reference=order_reference, amount_minor=amount, currency=currency)
    return body, {sandbox.SIGNATURE_HEADER: sandbox.sign(WEBHOOK_SECRET, body), "Content-Type": "application/json"}


def pay(client, ctx, order, **over):
    """Deliver a correctly signed 'payment succeeded' webhook for an order dict from the DB."""
    from backend.models import orders as order_repo
    o = order_repo.get_by_reference(ctx.db, order["order_reference"])
    kw = dict(reference=o["payment_reference"], order_reference=o["reference"], amount=o["total"], currency=o["currency"])
    kw.update(over)
    body, headers = signed_event(ctx, **kw)
    return client.post("/api/webhooks/payment", data=body, headers=headers)


@pytest.fixture
def paid_order(client, ctx):
    order = place_order(client)
    assert pay(client, ctx, order).get_json()["outcome"] == "paid"
    from backend.models import orders as order_repo
    return order_repo.get_by_reference(ctx.db, order["order_reference"])
