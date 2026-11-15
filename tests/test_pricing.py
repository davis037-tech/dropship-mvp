from decimal import Decimal
import pytest
from backend.config.settings import ConfigError, Settings
from backend.services import pricing


def cfg(**kw):
    env = {"APP_ENV": "test", "DATABASE_URL": "sqlite:////tmp/x.db", "SUPPLIER_FX_RATE": "0.14", **kw}
    return Settings.from_env(env).pricing


def test_landed_cost_converts_currency_and_adds_costs():
    c = cfg()
    assert pricing.landed_cost(supplier_cost=1800, supplier_currency="CNY", store_currency="USD", fx_rate=c.supplier_fx_rate,
                               estimated_shipping=650, agent_fee=50, other_costs=30) == 252 + 650 + 50 + 30


def test_missing_fx_rate_is_an_error_not_a_guess():
    with pytest.raises(pricing.PricingError):
        pricing.landed_cost(supplier_cost=1800, supplier_currency="CNY", store_currency="USD", fx_rate=None)


def test_recommended_price_hits_target_margin_and_is_not_a_flat_multiple():
    c = cfg(PRICE_ENDING_CENTS="")
    landed = 982
    price = pricing.recommended_price(landed, c)
    e = pricing.economics_at_price(price, landed, c)
    assert e.margin >= c.target_margin                      # never below target
    assert e.margin - c.target_margin < Decimal("0.005")    # ...and tight
    assert price != landed * 4


def test_price_ending_rounds_up_never_down():
    c = cfg(PRICE_ENDING_CENTS="99")
    price = pricing.recommended_price(982, c)
    assert price % 100 == 99
    raw = pricing.recommended_price(982, cfg(PRICE_ENDING_CENTS=""))
    assert price >= raw and price - raw < 100


def test_pricing_is_configurable():
    low = pricing.recommended_price(1000, cfg(TARGET_MARGIN="20", PRICE_ENDING_CENTS=""))
    high = pricing.recommended_price(1000, cfg(TARGET_MARGIN="50", PRICE_ENDING_CENTS=""))
    assert high > low
    assert pricing.recommended_price(1000, cfg(AD_COST_ALLOWANCE="10", PRICE_ENDING_CENTS="")) > \
           pricing.recommended_price(1000, cfg(AD_COST_ALLOWANCE="0", PRICE_ENDING_CENTS=""))


def test_impossible_percentages_rejected_at_startup():
    with pytest.raises(ConfigError):
        cfg(TARGET_MARGIN="60", PAYMENT_FEE_PERCENT="30", REFUND_RESERVE_PERCENT="20")


def test_is_profitable_threshold():
    c = cfg()
    assert pricing.is_profitable(5000, 982, c)
    assert not pricing.is_profitable(1100, 982, c)


def test_price_change_significance():
    c = cfg()
    assert not pricing.price_change_is_significant(1800, 1900, c)   # 5.5 %
    assert pricing.price_change_is_significant(1800, 2100, c)       # 16.7 %
