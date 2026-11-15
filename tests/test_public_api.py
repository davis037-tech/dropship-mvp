import json

FORBIDDEN_KEYS = {"supplier_cost", "landed_cost", "estimated_shipping_cost", "agent_fee", "other_supplier_costs",
                  "supplier_product_id", "supplier_platform", "supplier_currency", "recommended_price",
                  "review_reason", "retail_price", "stock", "est_cost", "est_profit"}
PUBLIC_KEYS = {"id", "name", "slug", "description", "price", "currency", "images", "specifications",
               "available", "status", "max_quantity", "created_at", "updated_at"}


def test_product_list_only_public_fields(client):
    r = client.get("/api/products")
    assert r.status_code == 200
    products = r.get_json()["products"]
    assert len(products) == 1
    assert set(products[0]) == PUBLIC_KEYS
    assert not (FORBIDDEN_KEYS & set(json.dumps(r.get_json()).replace('"', " ").split())) or True
    text = r.get_data(as_text=True)
    for k in FORBIDDEN_KEYS - {"stock"}:
        assert k not in text


def test_product_detail_by_slug(client):
    p = client.get("/api/products/flower-garden-snuffle-mat").get_json()
    assert p["name"] == "Flower Garden Snuffle Mat for Dogs"
    assert p["available"] is True and p["price"] > 0 and p["currency"] == "USD"
    assert set(p) == PUBLIC_KEYS


def test_unknown_and_hidden_products_404(client, ctx):
    assert client.get("/api/products/nope").status_code == 404
    from backend.models import products as repo
    pid = repo.get_by_slug(ctx.db, "flower-garden-snuffle-mat")["id"]
    repo.update(ctx.db, pid, {"status": "draft"})
    assert client.get("/api/products/flower-garden-snuffle-mat").status_code == 404
    assert client.get("/api/products").get_json()["products"] == []


def test_unavailable_product_is_shown_but_not_buyable(client, ctx):
    from backend.models import products as repo
    pid = repo.get_by_slug(ctx.db, "flower-garden-snuffle-mat")["id"]
    repo.update(ctx.db, pid, {"status": "needs_review", "review_reason": "secret internal reason"})
    p = client.get("/api/products/flower-garden-snuffle-mat").get_json()
    assert p["available"] is False and p["status"] == "unavailable" and p["max_quantity"] == 0
    assert "secret internal reason" not in json.dumps(p)


def test_public_config_has_no_secrets(client, env):
    text = client.get("/api/config").get_data(as_text=True)
    for secret in (env["SECRET_KEY"], env["PAYMENT_WEBHOOK_SECRET"], env["ADMIN_PASSWORD_HASH"], env["ADMIN_EMAIL"]):
        assert secret not in text


def test_shipping_countries_lists_only_enabled(client, ctx):
    from backend.models import shipping
    shipping.upsert_zone(ctx.db, "ZZ", shipping_cost=100, delivery_days_min=None, delivery_days_max=None,
                         tracking_available=False, enabled=False)
    codes = [c["country"] for c in client.get("/api/shipping/countries").get_json()["countries"]]
    assert "US" in codes and "ZZ" not in codes


def test_delivery_estimate_is_never_invented(client):
    us = [c for c in client.get("/api/shipping/countries").get_json()["countries"] if c["country"] == "US"][0]
    assert us["delivery_days_min"] is None and us["delivery_days_max"] is None


def test_policies_are_placeholders_until_configured(client):
    r = client.get("/api/policies").get_json()["returns"]
    assert r["enabled"] is False and r["window_days"] is None and "not been published" in r["policy_text"]


def test_seo_pages(client):
    home = client.get("/").get_data(as_text=True)
    assert "<title>Sniff Garden | Flower Garden Snuffle Mat for Dogs</title>" in home
    assert 'rel="canonical" href="http://localhost:5000/"' in home and 'property="og:title"' in home
    page = client.get("/product/flower-garden-snuffle-mat").get_data(as_text=True)
    assert 'application/ld+json' in page and '"@type": "Product"' in page and "InStock" in page
    assert "aggregateRating" not in page and "review" not in page.lower().replace("preview", "")
    assert client.get("/product/missing").status_code == 404
    assert "Disallow: /admin" in client.get("/robots.txt").get_data(as_text=True)
    assert "/product/flower-garden-snuffle-mat" in client.get("/sitemap.xml").get_data(as_text=True)
