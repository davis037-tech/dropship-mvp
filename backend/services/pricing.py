"""Pricing engine. Pure functions, all money in integer minor units.

    landed_cost = supplier_cost * fx + estimated_shipping + agent_fee + other_supplier_costs

    profit(price)  = price - landed - payment_fee(price) - refund_reserve(price) - ad_allowance
    payment_fee    = price * PAYMENT_FEE_PERCENT + PAYMENT_FIXED_FEE
    refund_reserve = price * REFUND_RESERVE_PERCENT
    margin         = profit / price

The recommended price is the smallest price whose margin equals TARGET_MARGIN:

    price = (landed + fixed_fee + ad_allowance) / (1 - fee% - refund% - target_margin)

It is deliberately NOT "supplier price x 4". If you want to land near a 4x landed-cost
multiple, tune TARGET_MARGIN / AD_COST_ALLOWANCE; the admin product view shows the
resulting multiple. Shipping revenue charged to customers is treated as a bonus and is
not used to justify the product price.

Nothing here is ever serialised to the public API.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Optional

from backend.config.settings import PricingConfig


class PricingError(ValueError):
    pass


def _round(d: Decimal) -> int:
    return int(d.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def landed_cost(
    *,
    supplier_cost: int,
    supplier_currency: str,
    store_currency: str,
    fx_rate: Optional[Decimal],
    estimated_shipping: int = 0,
    agent_fee: int = 0,
    other_costs: int = 0,
) -> int:
    if supplier_currency.upper() == store_currency.upper():
        converted = Decimal(supplier_cost)
    else:
        if fx_rate is None:
            raise PricingError(
                f"SUPPLIER_FX_RATE is not configured ({supplier_currency}->{store_currency})"
            )
        converted = Decimal(supplier_cost) * fx_rate
    return _round(converted) + estimated_shipping + agent_fee + other_costs


def recommended_price(landed: int, cfg: PricingConfig) -> int:
    denom = Decimal(1) - cfg.payment_fee_rate - cfg.refund_reserve_rate - cfg.target_margin
    if denom <= 0:
        raise PricingError("Pricing percentages leave no room for profit")
    raw = (Decimal(landed) + cfg.payment_fixed_fee + cfg.ad_cost_allowance) / denom
    price = int(raw.quantize(Decimal(1), rounding=ROUND_CEILING))
    if cfg.price_ending_minor is not None:
        # round UP to the next price ending in .NN (never down: margin is protected)
        base = (price // 100) * 100 + cfg.price_ending_minor
        price = base if base >= price else base + 100
    return price


@dataclass(frozen=True)
class Economics:
    price: int
    landed_cost: int
    payment_fee: int
    refund_reserve: int
    ad_allowance: int
    profit: int
    margin: Decimal            # fraction of price (can be negative)
    landed_multiple: Decimal   # price / landed_cost

    def as_admin_dict(self) -> dict:
        return {
            "price": self.price / 100,
            "landed_cost": self.landed_cost / 100,
            "payment_fee": self.payment_fee / 100,
            "refund_reserve": self.refund_reserve / 100,
            "ad_allowance": self.ad_allowance / 100,
            "profit": self.profit / 100,
            "margin_percent": float(round(self.margin * 100, 2)),
            "landed_multiple": float(round(self.landed_multiple, 2)),
        }


def economics_at_price(price: int, landed: int, cfg: PricingConfig) -> Economics:
    fee = _round(Decimal(price) * cfg.payment_fee_rate) + cfg.payment_fixed_fee
    reserve = _round(Decimal(price) * cfg.refund_reserve_rate)
    profit = price - landed - fee - reserve - cfg.ad_cost_allowance
    margin = Decimal(profit) / Decimal(price) if price > 0 else Decimal(-1)
    multiple = Decimal(price) / Decimal(landed) if landed > 0 else Decimal(0)
    return Economics(price, landed, fee, reserve, cfg.ad_cost_allowance, profit, margin, multiple)


def is_profitable(price: int, landed: int, cfg: PricingConfig) -> bool:
    return economics_at_price(price, landed, cfg).margin >= cfg.min_margin


def order_estimate(lines, shipping_charge: int, cfg: PricingConfig) -> dict:
    """Internal estimate for a whole order. `lines` = [(unit_price, qty, unit_landed)]."""
    subtotal = sum(p * q for p, q, _ in lines)
    total = subtotal + shipping_charge
    cost = sum(l * q for _, q, l in lines)
    fees = _round(Decimal(total) * cfg.payment_fee_rate) + cfg.payment_fixed_fee
    reserve = _round(Decimal(subtotal) * cfg.refund_reserve_rate)
    ads = cfg.ad_cost_allowance * sum(q for _, q, _ in lines)
    return {
        "subtotal": subtotal,
        "total": total,
        "cost": cost,
        "fees": fees + reserve + ads,
        "profit": total - cost - fees - reserve - ads,
    }


def price_change_is_significant(old_cost: int, new_cost: int, cfg: PricingConfig) -> bool:
    if old_cost <= 0:
        return new_cost > 0 and old_cost != new_cost
    return abs(Decimal(new_cost - old_cost)) / Decimal(old_cost) > cfg.price_review_threshold
