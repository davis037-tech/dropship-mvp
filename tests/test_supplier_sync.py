import pytest
from backend.config.settings import ConfigError, Settings
from backend.models import products as repo
from backend.services import product_sync
from backend.suppliers import build_supplier_adapter
from backend.suppliers.base import SupplierNotConfigured, SupplierNotImplemented, SupplierUnavailable
from backend.suppliers.manual import ManualSupplierAdapter
from backend.suppliers.mock import MockSupplierAdapter
from backend.suppliers.platform_1688 import Alibaba1688Adapter

SID = "MOCK-1688-0001"


def prod(ctx):
    return repo.get_by_slug(ctx.db, "flower-garden-snuffle-mat")


def test_mock_adapter_interface(ctx):
    a = ctx.supplier
    assert isinstance(a, MockSupplierAdapter)
    assert a.get_price(SID).amount_minor == 1800 and a.get_price(SID).currency == "CNY"
    assert a.get_stock(SID) == 25 and a.get_product(SID).supplier_product_id == SID
    assert a.get_tracking("x").tracking_number is None
    a.set_offline(True)
    with pytest.raises(SupplierUnavailable):
        a.get_stock(SID)


def test_mock_adapter_refuses_production():
    with pytest.raises(RuntimeError):
        MockSupplierAdapter("production")


def test_1688_adapter_is_honest_about_not_working():
    a = Alibaba1688Adapter()
    for call in (lambda: a.get_price("1"), lambda: a.get_stock("1"), lambda: a.get_product("1"), lambda: a.get_tracking("1")):
        with pytest.raises(SupplierNotConfigured):
            call()
    b = Alibaba1688Adapter("key", "secret", "token")
    with pytest.raises(SupplierNotImplemented):
        b.get_price("1")


def test_factory_selects_adapter_by_mode(env):
    mk = lambda **o: build_supplier_adapter(Settings.from_env({**env, **o}))
    assert isinstance(mk(), MockSupplierAdapter)
    assert isinstance(mk(SUPPLIER_MODE="manual"), ManualSupplierAdapter)
    assert isinstance(mk(SUPPLIER_MODE="1688"), Alibaba1688Adapter)


def test_sync_updates_price_stock_and_recalculates_pricing(ctx):
    before = prod(ctx)
    ctx.supplier.set_price(SID, 1900)
    ctx.supplier.set_stock(SID, 12)
    r = product_sync.sync_product(ctx, before["id"])
    after = prod(ctx)
    assert not r.flagged and not r.error
    assert after["supplier_cost"] == 1900 and after["stock"] == 12 and after["landed_cost"] > before["landed_cost"]
    assert after["recommended_price"] >= before["recommended_price"] and after["status"] == "active"
    assert after["retail_price"] == before["retail_price"]                 # sync never silently reprices
    mirror = repo.get_supplier_product(ctx.db, "mock", SID)
    assert mirror["supplier_cost"] == 1900 and mirror["supplier_stock"] == 12 and mirror["status"] == "ok"


def test_sync_stock_zero_makes_product_unavailable_and_unbuyable(ctx, client):
    ctx.supplier.set_stock(SID, 0)
    product_sync.sync_product(ctx, prod(ctx)["id"])
    p = prod(ctx)
    assert p["status"] == "unavailable" and p["stock"] == 0 and not repo.is_purchasable(p)
    assert repo.get_supplier_product(ctx.db, "mock", SID)["status"] == "out_of_stock"
    assert client.get("/api/products/flower-garden-snuffle-mat").get_json()["available"] is False


def test_restock_reactivates_only_auto_deactivated_products(ctx):
    pid = prod(ctx)["id"]
    ctx.supplier.set_stock(SID, 0)
    product_sync.sync_product(ctx, pid)
    ctx.supplier.set_stock(SID, 7)
    product_sync.sync_product(ctx, pid)
    assert prod(ctx)["status"] == "active" and prod(ctx)["stock"] == 7
    repo.update(ctx.db, pid, {"status": "unavailable", "review_reason": "admin paused"})    # manual pause stays paused
    product_sync.sync_product(ctx, pid)
    assert prod(ctx)["status"] == "unavailable"


def test_large_price_change_flags_for_review(ctx):
    ctx.supplier.set_price(SID, 2400)    # +33 %
    r = product_sync.sync_product(ctx, prod(ctx)["id"])
    p = prod(ctx)
    assert r.flagged and p["status"] == "needs_review" and "Supplier price moved" in p["review_reason"]
    assert not repo.is_purchasable(p)


def test_supplier_failure_leaves_data_untouched_and_records_error(ctx):
    before = prod(ctx)
    ctx.supplier.set_offline(True)
    r = product_sync.sync_product(ctx, before["id"])
    after = prod(ctx)
    assert r.error and after["stock"] == before["stock"] and after["supplier_cost"] == before["supplier_cost"]
    assert repo.get_supplier_product(ctx.db, "mock", SID)["status"] == "error"


def test_sync_skipped_in_manual_mode(env, tmp_path):
    from backend.app import create_app
    from backend.database.seed import seed_sample
    app = create_app({**env, "SUPPLIER_MODE": "manual", "DATABASE_URL": f"sqlite:///{tmp_path}/s.db"})
    c = app.extensions["shop"]
    seed_sample(c)
    assert product_sync.sync_all(c)[0].skipped


def test_sync_all(ctx):
    ctx.supplier.set_stock(SID, 3)
    results = product_sync.sync_all(ctx)
    assert len(results) == 1 and prod(ctx)["stock"] == 3


# ------------------------------------------------------------------ production safety
PROD = {"APP_ENV": "production", "SECRET_KEY": "s" * 40, "DATABASE_URL": "postgresql://u:p@db/shop",
        "PUBLIC_BASE_URL": "https://shop.example.com", "ADMIN_EMAIL": "a@b.com", "ADMIN_PASSWORD_HASH": "scrypt:1:1:1:a:b",
        "PAYMENT_PROVIDER": "stripe", "PAYMENT_MODE": "live", "PAYMENT_SECRET_KEY": "sk_live_x",
        "PAYMENT_WEBHOOK_SECRET": "whsec_x", "SUPPLIER_MODE": "manual", "SUPPLIER_FX_RATE": "0.14"}


def test_production_config_accepts_valid_setup():
    assert Settings.from_env(PROD).is_production


@pytest.mark.parametrize("override,fragment", [
    ({"SUPPLIER_MODE": "mock"}, "mock"), ({"PAYMENT_PROVIDER": "sandbox", "PAYMENT_SECRET_KEY": ""}, "sandbox"),
    ({"PAYMENT_MODE": "test"}, "live"), ({"SECRET_KEY": "short"}, "SECRET_KEY"),
    ({"DATABASE_URL": "sqlite:///x.db"}, "PostgreSQL"), ({"ADMIN_PASSWORD_HASH": ""}, "ADMIN"),
    ({"PUBLIC_BASE_URL": "http://shop.example.com"}, "https"), ({"CORS_ALLOWED_ORIGINS": "*"}, "CORS"),
    ({"PAYMENT_SECRET_KEY": "sk_test_x"}, "live key"), ({"SUPPLIER_MODE": "1688"}, "1688"),
    ({"PAYMENT_WEBHOOK_SECRET": ""}, "WEBHOOK"),
])
def test_production_refuses_unsafe_config(override, fragment):
    with pytest.raises(ConfigError) as e:
        Settings.from_env({**PROD, **override})
    assert fragment.lower() in str(e.value).lower()


def test_automatic_fulfilment_requires_real_supplier_mode(env):
    with pytest.raises(ConfigError):
        Settings.from_env({**env, "FULFILLMENT_MODE": "automatic"})
