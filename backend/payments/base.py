"""Payment provider interface.

Providers are *hosted-checkout* style: the customer is redirected to the provider's
page, so raw card data never touches our servers. Provider-specific code (including
refunds) lives only in this package.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping, Optional


class PaymentError(Exception):
    pass


class InvalidWebhookSignature(PaymentError):
    pass


@dataclass(frozen=True)
class CheckoutSession:
    reference: str        # provider's id for this payment attempt (stored on the order)
    redirect_url: str     # hosted payment page


@dataclass(frozen=True)
class PaymentEvent:
    """Provider-neutral view of a verified notification."""
    event_id: str                    # unique per notification, used for idempotency
    type: str                        # payment_succeeded | payment_failed | refunded | ignored
    reference: Optional[str]         # provider payment/session reference
    order_reference: Optional[str]   # our order reference, if the provider echoes it
    amount_minor: Optional[int]
    currency: Optional[str]


@dataclass(frozen=True)
class PaymentStatus:
    status: str                      # pending | paid | failed
    amount_minor: Optional[int] = None
    currency: Optional[str] = None


@dataclass(frozen=True)
class RefundResult:
    refund_id: str
    status: str


class PaymentProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def create_checkout_session(
        self, *, order_reference: str, amount_minor: int, currency: str,
        customer_email: str, description: str, success_url: str, cancel_url: str,
    ) -> CheckoutSession: ...

    @abstractmethod
    def get_payment_status(self, reference: str) -> PaymentStatus:
        """Ask the PROVIDER (server-to-server) what happened. Never trust the browser."""

    @abstractmethod
    def parse_webhook(self, headers: Mapping[str, str], raw_body: bytes) -> PaymentEvent:
        """Verify the signature and parse. Raise InvalidWebhookSignature on any doubt."""

    @abstractmethod
    def refund(self, *, reference: str, amount_minor: int, currency: str, reason: str) -> RefundResult: ...
