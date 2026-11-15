import json
from tests.conftest import checkout_payload, place_order


def test_security_headers(client):
    r = client.get("/api/products")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["Cache-Control"] == "no-store"
    assert client.get("/api/products").headers["X-Frame-Options"] == "DENY"


def test_cors_only_for_allowed_origins(env, tmp_path):
    from backend.app import create_app
    app = create_app({**env, "CORS_ALLOWED_ORIGINS": "https://good.example", "DATABASE_URL": f"sqlite:///{tmp_path}/c.db"})
    c = app.test_client()
    ok = c.get("/api/products", headers={"Origin": "https://good.example"})
    bad = c.get("/api/products", headers={"Origin": "https://evil.example"})
    assert ok.headers.get("Access-Control-Allow-Origin") == "https://good.example"
    assert "Access-Control-Allow-Origin" not in bad.headers
    pre = c.options("/api/admin/orders", headers={"Origin": "https://good.example"})
    assert pre.status_code == 204


def test_no_secrets_in_any_public_response(client, ctx, env):
    order = place_order(client)
    token = order["status_url"].split("t=")[1]
    secrets_ = [env["SECRET_KEY"], env["PAYMENT_WEBHOOK_SECRET"], env["ADMIN_PASSWORD_HASH"]]
    urls = ["/api/config", "/api/products", "/api/products/flower-garden-snuffle-mat", "/api/shipping/countries",
            "/api/policies", f"/api/orders/{order['order_reference']}/status?token={token}", "/", "/product/flower-garden-snuffle-mat",
            "/robots.txt", "/sitemap.xml"]
    for u in urls:
        body = client.get(u).get_data(as_text=True)
        for s in secrets_:
            assert s not in body, (u, s[:6])
        for word in ("SUPPLIER_1688", "api_secret", "PAYMENT_SECRET_KEY"):
            assert word not in body


def test_checkout_response_contains_no_internal_data(client):
    body = client.post("/api/checkout", json=checkout_payload()).get_data(as_text=True)
    for leak in ("supplier", "landed", "profit", "est_"):
        assert leak not in body.lower().replace("redirect_url", "")


def test_sql_injection_attempts_are_inert(client, ctx):
    assert client.get("/api/products/x'%20OR%20'1'='1").status_code == 404
    r = client.post("/api/checkout", json=checkout_payload(customer={"name": "Robert'); DROP TABLE orders;--", "email": "bob@example.com"}))
    assert r.status_code == 201
    assert ctx.db.fetch_one("SELECT COUNT(*) AS n FROM orders")["n"] == 1
    assert client.get("/api/orders/x' OR '1'='1/status?email=a@b.com").status_code == 404


def test_error_responses_do_not_leak_internals(client):
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404 and r.is_json and "Traceback" not in r.get_data(as_text=True)
    assert client.delete("/api/products").status_code == 405


def test_xss_payloads_are_stored_verbatim_but_json_encoded(client, ctx):
    client.post("/api/checkout", json=checkout_payload(customer={"name": "<img src=x onerror=alert(1)>", "email": "x@example.com"}))
    # stored as plain text; the frontend renders with textContent only (covered in e2e)
    assert ctx.db.fetch_one("SELECT name FROM customers")["name"].startswith("<img")


def test_oversized_body_rejected(client):
    r = client.post("/api/checkout", data=b"{" + b"a" * 300_000, content_type="application/json")
    assert r.status_code == 413


def test_sandbox_endpoints_absent_in_production_like_config(env, tmp_path):
    from backend.app import create_app
    app = create_app({**env, "PAYMENT_PROVIDER": "stripe", "PAYMENT_SECRET_KEY": "sk_test_x", "DATABASE_URL": f"sqlite:///{tmp_path}/p.db"})
    assert app.test_client().get("/dev/pay/anything").status_code == 404
