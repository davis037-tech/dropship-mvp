import pytest
from tests.conftest import checkout_payload, place_order
from backend.models import orders as order_repo, products as product_repo


def product(ctx):
    return product_repo.get_by_slug(ctx.db, "flower-garden-snuffle-mat")


def test_checkout_creates_pending_order_with_server_side_prices(client, ctx):
    p = product(ctx)
    data = place_order(client)
    o = order_repo.get_by_reference(ctx.db, data["order_reference"])
    assert o["status"] == "pending_payment" and o["payment_status"] == "pending"
    assert o["subtotal"] == p["retail_price"] and o["total"] == p["retail_price"] + 499
    assert o["payment_reference"].startswith("sbx_") and "/dev/pay/" in data["redirect_url"]
    assert o["est_profit"] != 0 and o["est_cost"] > 0


def test_client_cannot_manipulate_price_or_totals(client, ctx):
    p = product(ctx)
    payload = checkout_payload()
    payload["items"][0].update(price=0.01, unit_price=1, retail_price=1, total=1)
    payload.update(total=0.01, subtotal=0.01, shipping_cost=0, amount=1, price=1)
    data = client.post("/api/checkout", json=payload).get_json()
    o = order_repo.get_by_reference(ctx.db, data["order_reference"])
    assert o["subtotal"] == p["retail_price"] and o["shipping_cost"] == 499
    items = order_repo.items(ctx.db, o["id"])
    assert items[0]["unit_price"] == p["retail_price"]


def test_quote_matches_server_totals(client, ctx):
    p = product(ctx)
    q = client.post("/api/checkout/quote", json={"items": [{"slug": p["slug"], "quantity": 2}], "country": "gb"}).get_json()
    assert q["subtotal"] == pytest.approx(p["retail_price"] * 2 / 100) and q["shipping_cost"] == 4.99


def test_unsupported_country_rejected(client):
    r = client.post("/api/checkout", json=checkout_payload(country="KP"))
    assert r.status_code == 422 and r.get_json()["error"]["code"] == "shipping_unsupported"


def test_disabled_zone_rejected(client, ctx):
    ctx.db.execute("UPDATE shipping_zones SET enabled = 0 WHERE country = 'US'")
    assert client.post("/api/checkout", json=checkout_payload()).status_code == 422


@pytest.mark.parametrize("mutate", [
    lambda p: p["customer"].update(email="not-an-email"),
    lambda p: p["customer"].update(name=""),
    lambda p: p["address"].pop("line1"),
    lambda p: p["items"][0].update(quantity=0),
    lambda p: p["items"][0].update(quantity="two"),
    lambda p: p.update(country="USA"),
    lambda p: p.update(items=[]),
    lambda p: p["address"].update(city="Bad\x00City"),
])
def test_validation_errors(client, mutate):
    payload = checkout_payload()
    mutate(payload)
    r = client.post("/api/checkout", json=payload)
    assert r.status_code == 422 and r.get_json()["error"]["code"] == "validation_error"


def test_non_json_and_bad_json(client):
    assert client.post("/api/checkout", data="x=1").status_code == 415
    assert client.post("/api/checkout", data="{bad", content_type="application/json").status_code == 400


def test_quantity_limits(client, ctx):
    assert client.post("/api/checkout", json=checkout_payload(items=[{"slug": "flower-garden-snuffle-mat", "quantity": 6}])).status_code == 422
    ctx.supplier.set_stock("MOCK-1688-0001", 1)
    r = client.post("/api/checkout", json=checkout_payload(items=[{"slug": "flower-garden-snuffle-mat", "quantity": 2}]))
    assert r.status_code == 409


def test_unknown_product_404(client):
    r = client.post("/api/checkout", json=checkout_payload(items=[{"slug": "ghost", "quantity": 1}]))
    assert r.status_code == 404


def test_supplier_out_of_stock_blocks_checkout_and_hides_product(client, ctx):
    ctx.supplier.set_stock("MOCK-1688-0001", 0)       # DB still says 25: must not be trusted
    assert product(ctx)["stock"] == 25
    r = client.post("/api/checkout", json=checkout_payload())
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "product_unavailable"
    p = product(ctx)
    assert p["status"] == "unavailable" and p["stock"] == 0
    assert client.get("/api/products/flower-garden-snuffle-mat").get_json()["available"] is False


def test_checkout_uses_fresh_supplier_stock_for_quantity(client, ctx):
    ctx.supplier.set_stock("MOCK-1688-0001", 1)
    r = client.post("/api/checkout", json=checkout_payload(items=[{"slug": "flower-garden-snuffle-mat", "quantity": 2}]))
    assert r.status_code == 409


def test_supplier_price_spike_blocks_and_flags_not_silently_accepts(client, ctx):
    ctx.supplier.set_price("MOCK-1688-0001", 9000)   # 18 CNY -> 90 CNY
    r = client.post("/api/checkout", json=checkout_payload())
    assert r.status_code == 409
    assert "price" not in r.get_data(as_text=True).lower().replace("unavailable", "")  # no internal details
    p = product(ctx)
    assert p["status"] == "needs_review" and "Supplier price moved" in p["review_reason"]
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM orders")["n"] == 0


def test_small_supplier_price_change_is_accepted_and_synced(client, ctx):
    ctx.supplier.set_price("MOCK-1688-0001", 1900)   # +5.5 % < 10 % threshold
    assert client.post("/api/checkout", json=checkout_payload()).status_code == 201
    assert product(ctx)["supplier_cost"] == 1900


def test_unprofitable_without_big_price_move_is_blocked(client, ctx):
    ctx.db.execute("UPDATE products SET retail_price = 1200")  # admin mistake: margin far below minimum
    r = client.post("/api/checkout", json=checkout_payload())
    assert r.status_code == 409 and product(ctx)["status"] == "needs_review"


def test_supplier_offline_strict_blocks(client, ctx):
    ctx.supplier.set_offline(True)
    assert client.post("/api/checkout", json=checkout_payload()).status_code == 409


def test_supplier_offline_cache_policy_allows_only_fresh_cache(env, tmp_path):
    from backend.app import create_app
    from backend.database.seed import seed_sample
    app = create_app({**env, "SUPPLIER_CHECK_POLICY": "cache", "DATABASE_URL": f"sqlite:///{tmp_path}/c.db"})
    ctx = app.extensions["shop"]
    seed_sample(ctx)
    c = app.test_client()
    c.post("/api/products")  # noop
    from backend.services import product_sync
    pid = product(ctx)["id"]
    product_sync.sync_product(ctx, pid)                    # fresh cache
    ctx.supplier.set_offline(True)
    assert c.post("/api/checkout", json=checkout_payload()).status_code == 201
    ctx.db.execute("UPDATE products SET last_synced_at = '2020-01-01T00:00:00.000000Z'")
    assert c.post("/api/checkout", json=checkout_payload()).status_code == 409


def test_manual_supplier_mode_uses_admin_maintained_db_values(env, tmp_path):
    from backend.app import create_app
    from backend.database.seed import seed_sample
    app = create_app({**env, "SUPPLIER_MODE": "manual", "DATABASE_URL": f"sqlite:///{tmp_path}/m.db"})
    seed_sample(app.extensions["shop"])
    c = app.test_client()
    assert c.post("/api/checkout", json=checkout_payload()).status_code == 201
    app.extensions["shop"].db.execute("UPDATE products SET stock = 0")
    assert c.post("/api/checkout", json=checkout_payload()).status_code == 409


def test_payment_provider_failure_cancels_order(client, ctx, monkeypatch):
    from backend.payments.base import PaymentError
    monkeypatch.setattr(ctx.payments, "create_checkout_session", lambda **k: (_ for _ in ()).throw(PaymentError("down")))
    r = client.post("/api/checkout", json=checkout_payload())
    assert r.status_code == 502
    assert ctx.db.fetch_one("SELECT status, payment_status FROM orders")["status"] == "cancelled"
