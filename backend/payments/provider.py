"""Payment provider factory. The ONLY place a concrete provider is chosen."""
from __future__ import annotations

from backend.config.settings import ConfigError, Settings
from backend.payments.base import PaymentProvider


def build_payment_provider(settings: Settings, base_url_getter) -> PaymentProvider:
    if settings.payment_provider == "sandbox":
        if settings.is_production:
            raise ConfigError("sandbox payments are not allowed in production")
        from backend.payments.sandbox import SandboxPaymentProvider

        return SandboxPaymentProvider(settings.payment_webhook_secret, base_url_getter)
    if settings.payment_provider == "stripe":
        from backend.payments.stripe_provider import StripePaymentProvider

        return StripePaymentProvider(settings.payment_secret_key, settings.payment_webhook_secret)
    raise ConfigError(f"unknown PAYMENT_PROVIDER {settings.payment_provider}")
