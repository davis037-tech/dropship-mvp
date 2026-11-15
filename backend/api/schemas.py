"""Request validation (pydantic). Anything not described here is rejected or ignored."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")
_SLUG = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


def _clean(v: str) -> str:
    v = v.strip()
    if _CTRL.search(v):
        raise ValueError("contains control characters")
    return v


class Base(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# ------------------------------------------------------------------ public
class CartItem(Base):
    slug: str = Field(pattern=_SLUG, max_length=120)
    quantity: int = Field(ge=1, le=50)


class QuoteRequest(Base):
    items: List[CartItem] = Field(min_length=1, max_length=10)
    country: str = Field(pattern=r"^[A-Za-z]{2}$")

    @field_validator("country")
    @classmethod
    def up(cls, v):
        return v.upper()


class CustomerIn(Base):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=254)

    @field_validator("name")
    @classmethod
    def n(cls, v):
        return _clean(v)

    @field_validator("email")
    @classmethod
    def e(cls, v):
        v = _clean(v).lower()
        if not _EMAIL.match(v):
            raise ValueError("invalid email address")
        return v


class AddressIn(Base):
    line1: str = Field(min_length=1, max_length=120)
    line2: Optional[str] = Field(default=None, max_length=120)
    city: str = Field(min_length=1, max_length=80)
    region: Optional[str] = Field(default=None, max_length=80)
    postal_code: str = Field(min_length=1, max_length=20)

    @field_validator("line1", "line2", "city", "region", "postal_code")
    @classmethod
    def c(cls, v):
        return _clean(v) if v is not None else v


class CheckoutRequest(QuoteRequest):
    customer: CustomerIn
    address: AddressIn


# ------------------------------------------------------------------ admin
class LoginBody(Base):
    email: str = Field(max_length=254)
    password: str = Field(min_length=1, max_length=200)


class ImageIn(Base):
    url: str = Field(max_length=500)
    alt: str = Field(default="", max_length=200)

    @field_validator("url")
    @classmethod
    def u(cls, v):
        if not (v.startswith("https://") or (v.startswith("/") and not v.startswith("//"))):
            raise ValueError("image url must be https:// or a site-relative path")
        return v


class SpecIn(Base):
    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=300)


Money = Decimal


class ProductBody(Base):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    slug: Optional[str] = Field(default=None, pattern=_SLUG, max_length=120)
    description: Optional[str] = Field(default=None, max_length=5000)
    supplier_product_id: Optional[str] = Field(default=None, max_length=100)
    supplier_platform: Optional[str] = Field(default=None, max_length=30)
    supplier_currency: Optional[str] = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    supplier_cost: Optional[Money] = Field(default=None, ge=0, le=1_000_000)
    estimated_shipping_cost: Optional[Money] = Field(default=None, ge=0, le=100_000)
    agent_fee: Optional[Money] = Field(default=None, ge=0, le=100_000)
    other_supplier_costs: Optional[Money] = Field(default=None, ge=0, le=100_000)
    retail_price: Optional[Money] = Field(default=None, ge=0, le=1_000_000)
    use_recommended_price: bool = False
    stock: Optional[int] = Field(default=None, ge=0, le=1_000_000)
    status: Optional[str] = Field(default=None, pattern=r"^(draft|active|unavailable|archived)$")
    images: Optional[List[ImageIn]] = Field(default=None, max_length=12)
    specifications: Optional[List[SpecIn]] = Field(default=None, max_length=30)


class StatusBody(Base):
    status: str = Field(pattern=r"^(supplier_approved|supplier_ordered|shipped|delivered|cancelled)$")
    supplier_order_id: Optional[str] = Field(default=None, max_length=64)
    override: bool = False
    refund: bool = False
    acknowledge_supplier_order: bool = False


class TrackingBody(Base):
    tracking_number: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9\-_. ]+$")
    carrier: Optional[str] = Field(default=None, max_length=60)
    url: Optional[str] = Field(default=None, max_length=300)

    @field_validator("url")
    @classmethod
    def u(cls, v):
        if v and not v.startswith("https://"):
            raise ValueError("tracking url must be https://")
        return v or None


class RefundBody(Base):
    reason: str = Field(default="admin refund", max_length=200)


class ShippingZoneBody(Base):
    shipping_cost: Money = Field(ge=0, le=10_000)
    delivery_days_min: Optional[int] = Field(default=None, ge=1, le=365)
    delivery_days_max: Optional[int] = Field(default=None, ge=1, le=365)
    tracking_available: bool = False
    enabled: bool = False

    @field_validator("delivery_days_max")
    @classmethod
    def order(cls, v, info):
        mn = info.data.get("delivery_days_min")
        if v is not None and mn is not None and v < mn:
            raise ValueError("max must be >= min")
        return v


class SettingsBody(Base):
    returns_enabled: Optional[bool] = None
    returns_window_days: Optional[int] = Field(default=None, ge=0, le=365)
    returns_return_shipping_paid_by: Optional[str] = Field(default=None, pattern=r"^(customer|store|)$")
    returns_refund_method: Optional[str] = Field(default=None, max_length=200)
    returns_policy_text: Optional[str] = Field(default=None, max_length=10000)


class ApproveReviewBody(Base):
    apply_recommended_price: bool = False
