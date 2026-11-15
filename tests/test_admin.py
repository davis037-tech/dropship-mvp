import time
import jwt
import pytest
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, checkout_payload, pay, place_order
from backend.models import orders as order_repo, products as product_repo


def test_every_admin_endpoint_rejects_anonymous_requests(app, client):
    rules = [r for r in app.url_map.iter_rules() if r.rule.startswith("/api/admin") and r.rule != "/api/admin/login"]
    assert len(rules) >= 20
    for rule in rules:
        path = rule.rule.replace("<int:oid>", "1").replace("<int:pid>", "1").replace("<country>", "US")
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            r = client.open(path, method=method, json={})
            assert r.status_code == 401, (method, path, r.status_code)


def test_bad_tokens_rejected(client, env):
    now = int(time.time())
    good = dict(sub=ADMIN_EMAIL, iat=now, exp=now + 600, iss="dropship-mvp", aud="admin")
    cases = {
        "garbage": "abc.def.ghi",
        "wrong_secret": jwt.encode(good, "z" * 48, algorithm="HS256"),
        "expired": jwt.encode({**good, "exp": now - 10}, env["SECRET_KEY"], algorithm="HS256"),
        "wrong_audience": jwt.encode({**good, "aud": "customer"}, env["SECRET_KEY"], algorithm="HS256"),
        "wrong_subject": jwt.encode({**good, "sub": "evil@example.com"}, env["SECRET_KEY"], algorithm="HS256"),
        "alg_none": jwt.encode(good, None, algorithm="none"),
    }
    for name, tok in cases.items():
        assert client.get("/api/admin/orders", headers={"Authorization": f"Bearer {tok}"}).status_code == 401, name
    ok = jwt.encode(good, env["SECRET_KEY"], algorithm="HS256")
    assert client.get("/api/admin/orders", headers={"Authorization": f"Bearer {ok}"}).status_code == 200


def test_login(client):
    assert client.post("/api/admin/login", json={"email": ADMIN_EMAIL, "password": "wrong"}).status_code == 401
    assert client.post("/api/admin/login", json={"email": "x@y.com", "password": ADMIN_PASSWORD}).status_code == 401
    r = client.post("/api/admin/login", json={"email": ADMIN_EMAIL.upper(), "password": ADMIN_PASSWORD})
    assert r.status_code == 200 and r.get_json()["token"]
    assert ADMIN_PASSWORD not in r.get_data(as_text=True)


def test_login_disabled_when_admin_not_configured(env, tmp_path):
    from backend.app import create_app
    app = create_app({**env, "ADMIN_EMAIL": "", "ADMIN_PASSWORD_HASH": "", "DATABASE_URL": f"sqlite:///{tmp_path}/n.db"})
    c = app.test_client()
    assert c.post("/api/admin/login", json={"email": "", "password": "x"}).status_code in (422, 503)
    assert c.post("/api/admin/login", json={"email": "a@b.com", "password": "x"}).status_code == 503


def test_login_is_rate_limited(env, tmp_path):
    from backend.app import create_app
    app = create_app({**env, "RATE_LIMIT_ENABLED": "true", "DATABASE_URL": f"sqlite:///{tmp_path}/r.db"})
    c = app.test_client()
    codes = [c.post("/api/admin/login", json={"email": ADMIN_EMAIL, "password": "bad"}).status_code for _ in range(8)]
    assert codes[:5] == [401] * 5 and 429 in codes[5:]


def test_dashboard_numbers(client, ctx, admin_headers, paid_order):
    d = client.get("/api/admin/dashboard", headers=admin_headers).get_json()
    assert d["total_orders"] == 1 and d["paid_orders"] == 1 and d["awaiting_supplier_approval"] == 1
    assert d["shipped"] == 0 and d["delivered"] == 0
    assert d["revenue"] == pytest.approx(paid_order["total"] / 100) and d["estimated_profit"] != 0


def test_admin_sees_supplier_cost_and_profit_customers_do_not(client, admin_headers, paid_order):
    rows = client.get("/api/admin/orders", headers=admin_headers).get_json()["orders"]
    assert rows[0]["supplier_cost"] > 0 and "estimated_profit" in rows[0] and rows[0]["customer"] == "Jane Buyer"
    detail = client.get(f"/api/admin/orders/{paid_order['id']}", headers=admin_headers).get_json()
    assert detail["ship_to"]["address"]["line1"] == "1 Test Street" and detail["items"][0]["unit_landed_cost"] > 0


def test_full_manual_workflow_to_delivered(client, ctx, admin_headers, paid_order):
    oid, ref = paid_order["id"], paid_order["reference"]
    put = lambda path, **body: client.put(f"/api/admin/orders/{oid}/{path}", json=body, headers=admin_headers)
    assert put("status", status="shipped").status_code == 409                       # cannot skip steps
    assert put("status", status="supplier_ordered", supplier_order_id="X1").status_code == 409
    r = put("status", status="supplier_approved")
    assert r.status_code == 200 and r.get_json()["supplier_status"] == "approved"
    assert put("status", status="supplier_ordered").status_code == 422              # needs 1688 order number
    r = put("status", status="supplier_ordered", supplier_order_id="1688-9988776655")
    assert r.get_json()["status"] == "supplier_ordered" and r.get_json()["supplier_order_id"] == "1688-9988776655"
    assert put("status", status="shipped").status_code == 409                       # tracking required (US zone has tracking)
    r = put("tracking", tracking_number="LX123456789CN", carrier="Test Carrier", url="https://track.example.com/LX123456789CN")
    assert r.status_code == 200
    assert put("status", status="shipped").get_json()["status"] == "shipped"
    cust = client.get(f"/api/orders/{ref}/status?email=jane@example.com").get_json()
    assert cust["status"] == "shipped" and cust["tracking"]["number"] == "LX123456789CN"
    assert put("status", status="delivered").get_json()["status"] == "delivered"
    cust = client.get(f"/api/orders/{ref}/status?email=jane@example.com").get_json()
    assert all(s["done"] for s in cust["timeline"])
    assert put("status", status="cancelled", refund=True).status_code == 409        # terminal
    events = client.get(f"/api/admin/orders/{oid}", headers=admin_headers).get_json()["events"]
    assert [e["event"] for e in events][-1] == "status:delivered" and all(e["actor"] for e in events)


def test_approval_blocked_when_supplier_stock_gone_unless_overridden(client, ctx, admin_headers, paid_order):
    ctx.supplier.set_stock("MOCK-1688-0001", 0)
    url = f"/api/admin/orders/{paid_order['id']}/status"
    r = client.put(url, json={"status": "supplier_approved"}, headers=admin_headers)
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "supplier_check_failed"
    assert client.put(url, json={"status": "supplier_approved", "override": True}, headers=admin_headers).status_code == 200


def test_cancel_paid_order_requires_refund_decision_and_refunds(client, ctx, admin_headers, paid_order):
    url = f"/api/admin/orders/{paid_order['id']}/status"
    assert client.put(url, json={"status": "cancelled"}, headers=admin_headers).status_code == 409
    r = client.put(url, json={"status": "cancelled", "refund": True}, headers=admin_headers)
    assert r.status_code == 200 and r.get_json()["status"] == "cancelled" and r.get_json()["payment_status"] == "refunded"
    assert any(e["event"] == "refunded" for e in r.get_json()["events"])


def test_cancel_after_supplier_order_needs_acknowledgement(client, ctx, admin_headers, paid_order):
    oid = paid_order["id"]
    h = admin_headers
    client.put(f"/api/admin/orders/{oid}/status", json={"status": "supplier_approved"}, headers=h)
    client.put(f"/api/admin/orders/{oid}/status", json={"status": "supplier_ordered", "supplier_order_id": "A1"}, headers=h)
    url = f"/api/admin/orders/{oid}/status"
    assert client.put(url, json={"status": "cancelled", "refund": True}, headers=h).status_code == 409
    assert client.put(url, json={"status": "cancelled", "refund": True, "acknowledge_supplier_order": True}, headers=h).status_code == 200


def test_standalone_refund_after_delivery(client, ctx, admin_headers, paid_order):
    r = client.post(f"/api/admin/orders/{paid_order['id']}/refund", json={"reason": "damaged"}, headers=admin_headers)
    assert r.status_code == 200 and r.get_json()["status"] == "refunded"
    d = client.get("/api/admin/dashboard", headers=admin_headers).get_json()
    assert d["revenue"] == 0
    assert client.post(f"/api/admin/orders/{paid_order['id']}/refund", json={}, headers=admin_headers).status_code == 409


def test_unpaid_order_cannot_enter_fulfilment(client, ctx, admin_headers):
    order = place_order(client)
    oid = order_repo.get_by_reference(ctx.db, order["order_reference"])["id"]
    r = client.put(f"/api/admin/orders/{oid}/status", json={"status": "supplier_approved"}, headers=admin_headers)
    assert r.status_code == 409


def test_tracking_validation(client, admin_headers, paid_order):
    url = f"/api/admin/orders/{paid_order['id']}/tracking"
    assert client.put(url, json={"tracking_number": "ABC123"}, headers=admin_headers).status_code == 409   # not ordered yet
    assert client.put(url, json={"tracking_number": "<script>"}, headers=admin_headers).status_code == 422
    assert client.put(url, json={"tracking_number": "ABC123", "url": "javascript:alert(1)"}, headers=admin_headers).status_code == 422


# ------------------------------------------------------------------ products
def test_product_crud_and_margin_guard(client, ctx, admin_headers):
    h = admin_headers
    body = {"name": "Second Mat", "slug": "second-mat", "supplier_cost": 20, "supplier_currency": "cny",
            "estimated_shipping_cost": 6.5, "stock": 10, "use_recommended_price": True}
    r = client.post("/api/admin/products", json=body, headers=h)
    assert r.status_code == 201, r.data
    p = r.get_json()
    assert p["status"] == "draft" and p["retail_price"] == p["recommended_price"] and p["landed_cost"] > 0
    assert p["economics"]["margin_percent"] >= 30
    assert client.post("/api/admin/products", json=body, headers=h).status_code == 409
    # activation below the minimum margin is refused
    r = client.put(f"/api/admin/products/{p['id']}", json={"status": "active", "retail_price": 9.99}, headers=h)
    assert r.status_code == 422 and r.get_json()["error"]["code"] == "margin_below_minimum"
    assert client.put(f"/api/admin/products/{p['id']}", json={"status": "active"}, headers=h).status_code == 200
    assert client.get("/api/products/second-mat").status_code == 200
    # changing supplier cost recalculates landed cost
    r = client.put(f"/api/admin/products/{p['id']}", json={"supplier_cost": 40, "retail_price": 80}, headers=h)
    assert r.get_json()["landed_cost"] > p["landed_cost"]
    assert client.delete(f"/api/admin/products/{p['id']}", headers=h).get_json()["archived"] is True
    assert client.get("/api/products/second-mat").status_code == 404


def test_admin_product_validation(client, admin_headers):
    h = admin_headers
    for bad in ({"name": "x", "slug": "Bad Slug"}, {"name": "x", "slug": "ok", "supplier_cost": -1},
                {"name": "x", "slug": "ok", "images": [{"url": "javascript:alert(1)"}]},
                {"name": "x", "slug": "ok", "status": "needs_review"}):
        assert client.post("/api/admin/products", json=bad, headers=h).status_code == 422, bad


def test_admin_product_list_shows_internal_fields(client, admin_headers):
    p = client.get("/api/admin/products", headers=admin_headers).get_json()["products"][0]
    assert p["supplier_cost"] == 18.0 and p["landed_cost"] > 0 and p["economics"]["margin_percent"] > 0


def test_review_flow_approve_and_apply_recommended_price(client, ctx, admin_headers):
    ctx.supplier.set_price("MOCK-1688-0001", 6000)
    pid = product_repo.get_by_slug(ctx.db, "flower-garden-snuffle-mat")["id"]
    r = client.post(f"/api/admin/products/{pid}/sync", headers=admin_headers).get_json()
    assert r["result"]["flagged"] and r["product"]["status"] == "needs_review"
    bad = client.post(f"/api/admin/products/{pid}/approve-review", json={}, headers=admin_headers)
    assert bad.status_code == 409                                                    # price too low for new cost
    ok = client.post(f"/api/admin/products/{pid}/approve-review", json={"apply_recommended_price": True}, headers=admin_headers)
    assert ok.status_code == 200 and ok.get_json()["status"] == "active" and ok.get_json()["retail_price"] > 21.99


def test_shipping_zone_and_settings_admin(client, admin_headers):
    h = admin_headers
    r = client.put("/api/admin/shipping/nz", json={"shipping_cost": 9.5, "delivery_days_min": 10, "delivery_days_max": 20,
                                                   "tracking_available": True, "enabled": True}, headers=h)
    assert r.status_code == 200 and r.get_json()["country"] == "NZ"
    nz = [c for c in client.get("/api/shipping/countries").get_json()["countries"] if c["country"] == "NZ"][0]
    assert nz["delivery_days_min"] == 10 and nz["shipping_cost"] == 9.5
    assert client.put("/api/admin/shipping/NZ", json={"shipping_cost": 1, "delivery_days_min": 9, "delivery_days_max": 3}, headers=h).status_code == 422
    assert client.put("/api/admin/shipping/NZL", json={"shipping_cost": 1}, headers=h).status_code == 422
    s = client.put("/api/admin/settings", json={"returns_enabled": True, "returns_window_days": 14,
                                                "returns_policy_text": "Draft policy text"}, headers=h).get_json()
    assert s["returns.window_days"] == "14"
    pub = client.get("/api/policies").get_json()["returns"]
    assert pub["enabled"] is True and pub["window_days"] == 14 and pub["policy_text"] == "Draft policy text"
