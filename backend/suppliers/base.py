"""Supplier integration interface.

Every supplier platform (1688 today, others later) implements `SupplierAdapter`.
The rest of the application only talks to this interface, so swapping the manual
workflow for automated 1688 ordering never touches checkout, pricing or the admin UI.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


class SupplierError(Exception):
    """Any supplier-side failure. Checkout treats these as 'cannot verify'."""


class SupplierNotConfigured(SupplierError):
    """Credentials / API access are missing."""


class SupplierNotImplemented(SupplierError):
    """The real integration has not been written yet."""


class SupplierUnavailable(SupplierError):
    """Live checks are not possible (manual mode, outage, ...)."""


@dataclass(frozen=True)
class SupplierProduct:
    supplier_product_id: str
    title: str
    url: str
    price_minor: int        # in `currency` minor units
    currency: str
    stock: int
    images: tuple = ()


@dataclass(frozen=True)
class SupplierPrice:
    amount_minor: int
    currency: str


@dataclass(frozen=True)
class SupplierOrderRequest:
    reference: str                  # our order reference
    supplier_product_id: str
    quantity: int
    ship_to_name: str
    ship_to_address: dict
    ship_to_country: str


@dataclass(frozen=True)
class SupplierOrderResult:
    supplier_order_id: str
    status: str
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TrackingInfo:
    carrier: Optional[str]
    tracking_number: Optional[str]
    status: str
    events: tuple = ()


class SupplierAdapter(ABC):
    name: str = "abstract"
    #: False when there is no API to ask (manual mode): the DB values are admin-maintained.
    supports_live_checks: bool = True

    @abstractmethod
    def get_product(self, supplier_product_id: str) -> SupplierProduct: ...

    @abstractmethod
    def get_price(self, supplier_product_id: str) -> SupplierPrice: ...

    @abstractmethod
    def get_stock(self, supplier_product_id: str) -> int: ...

    @abstractmethod
    def create_supplier_order(self, request: SupplierOrderRequest) -> SupplierOrderResult: ...

    @abstractmethod
    def get_tracking(self, supplier_order_id: str) -> TrackingInfo: ...
