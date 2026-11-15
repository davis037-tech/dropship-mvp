"""Central configuration.

Everything configurable comes from environment variables (optionally loaded from a
local `.env` file that is git-ignored). Nothing secret is ever hard-coded.

Percentages are written in PERCENT units (30 = 30 %). Money values are written in
MAJOR units (3.50 = 3.50 in the store currency) and converted to integer minor units
(cents) internally. Zero-decimal currencies (e.g. JPY) are not supported in this MVP.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    """Raised at start-up when configuration is invalid or unsafe."""


# --------------------------------------------------------------------------- helpers
def parse_dotenv(text: str) -> dict:
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def load_env(dotenv_path: Optional[Path] = None) -> dict:
    """`.env` values first, real environment variables win."""
    env: dict = {}
    path = dotenv_path or PROJECT_ROOT / ".env"
    if path.exists():
        env.update(parse_dotenv(path.read_text(encoding="utf-8")))
    env.update(os.environ)
    return env


def _get(env: Mapping, key: str, default: str = "") -> str:
    value = env.get(key)
    return default if value is None or str(value).strip() == "" else str(value).strip()


def _decimal(env, key, default) -> Decimal:
    raw = _get(env, key, str(default))
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise ConfigError(f"{key} must be a number, got {raw!r}")


def _percent(env, key, default) -> Decimal:
    value = _decimal(env, key, default)
    if not (Decimal(0) <= value < Decimal(100)):
        raise ConfigError(f"{key} must be >= 0 and < 100 (percent units)")
    return value / Decimal(100)


def _money(env, key, default) -> int:
    value = _decimal(env, key, default)
    if value < 0:
        raise ConfigError(f"{key} must not be negative")
    return int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _int(env, key, default, lo, hi) -> int:
    raw = _get(env, key, str(default))
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be an integer")
    if not lo <= value <= hi:
        raise ConfigError(f"{key} must be between {lo} and {hi}")
    return value


def _bool(env, key, default: bool) -> bool:
    raw = _get(env, key, "true" if default else "false").lower()
    return raw in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- config
@dataclass(frozen=True)
class PricingConfig:
    """Internal pricing economics. NEVER serialise this to a public response."""

    target_margin: Decimal          # fraction of selling price kept as profit
    payment_fee_rate: Decimal       # fraction of order total
    payment_fixed_fee: int          # minor units per order
    refund_reserve_rate: Decimal    # fraction of selling price
    ad_cost_allowance: int          # minor units per unit sold
    min_margin: Decimal             # below this fraction a sale is "unprofitable"
    price_review_threshold: Decimal  # supplier price move that triggers admin review
    price_ending_minor: Optional[int]  # e.g. 99 -> prices end in .99 ; None = off
    supplier_fx_rate: Optional[Decimal]  # store-currency units per 1 supplier-currency unit


@dataclass(frozen=True)
class Settings:
    app_env: str
    secret_key: str
    database_url: str
    auto_migrate: bool
    public_base_url: str
    cors_allowed_origins: tuple
    trust_proxy: bool
    rate_limit_enabled: bool
    frontend_dir: Path

    store_name: str
    store_currency: str
    max_quantity: int

    supplier_mode: str              # mock | manual | 1688 | cj
    supplier_1688_api_key: str
    supplier_1688_api_secret: str
    supplier_1688_access_token: str
    cj_api_key: str
    cj_access_token: str
    cj_refresh_token: str
    supplier_check_policy: str      # strict | cache
    stock_cache_max_age_minutes: int
    fulfillment_mode: str           # manual | automatic

    payment_provider: str           # sandbox | stripe
    payment_mode: str               # test | live
    payment_secret_key: str
    payment_webhook_secret: str

    admin_email: str
    admin_password_hash: str
    admin_token_ttl_minutes: int

    pricing: PricingConfig

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    # ------------------------------------------------------------------ factory
    @classmethod
    def from_env(cls, env: Optional[Mapping] = None) -> "Settings":
        env = load_env() if env is None else env
        app_env = _get(env, "APP_ENV", "development").lower()
        if app_env not in ("development", "test", "production"):
            raise ConfigError("APP_ENV must be development, test or production")
        prod = app_env == "production"

        secret_key = _get(env, "SECRET_KEY")
        webhook_secret = _get(env, "PAYMENT_WEBHOOK_SECRET")
        if not prod:
            # Ephemeral per-process secrets are fine for local development only.
            secret_key = secret_key or secrets.token_urlsafe(48)
            webhook_secret = webhook_secret or secrets.token_urlsafe(32)

        db_default = "" if prod else f"sqlite:///{PROJECT_ROOT / 'data' / 'dev.db'}"
        origins = tuple(o.strip().rstrip("/") for o in _get(env, "CORS_ALLOWED_ORIGINS").split(",") if o.strip())

        fx_raw = _get(env, "SUPPLIER_FX_RATE")
        fx = None
        if fx_raw:
            fx = _decimal(env, "SUPPLIER_FX_RATE", fx_raw)
            if fx <= 0:
                raise ConfigError("SUPPLIER_FX_RATE must be > 0")
        ending_raw = _get(env, "PRICE_ENDING_CENTS")
        ending = int(ending_raw) if ending_raw else None
        if ending is not None and not 0 <= ending <= 99:
            raise ConfigError("PRICE_ENDING_CENTS must be between 0 and 99 (or empty to disable)")

        pricing = PricingConfig(
            target_margin=_percent(env, "TARGET_MARGIN", 30),
            payment_fee_rate=_percent(env, "PAYMENT_FEE_PERCENT", 2.9),
            payment_fixed_fee=_money(env, "PAYMENT_FIXED_FEE", "0.30"),
            refund_reserve_rate=_percent(env, "REFUND_RESERVE_PERCENT", 5),
            ad_cost_allowance=_money(env, "AD_COST_ALLOWANCE", "3.00"),
            min_margin=_percent(env, "MIN_ACCEPTABLE_MARGIN", 10),
            price_review_threshold=_percent(env, "PRICE_REVIEW_THRESHOLD_PERCENT", 10),
            price_ending_minor=ending,
            supplier_fx_rate=fx,
        )
        if pricing.target_margin + pricing.payment_fee_rate + pricing.refund_reserve_rate >= 1:
            raise ConfigError("TARGET_MARGIN + PAYMENT_FEE_PERCENT + REFUND_RESERVE_PERCENT must be < 100")

        s = cls(
            app_env=app_env,
            secret_key=secret_key,
            database_url=_get(env, "DATABASE_URL", db_default),
            auto_migrate=_bool(env, "AUTO_MIGRATE", not prod),
            public_base_url=_get(env, "PUBLIC_BASE_URL").rstrip("/"),
            cors_allowed_origins=origins,
            trust_proxy=_bool(env, "TRUST_PROXY", False),
            rate_limit_enabled=_bool(env, "RATE_LIMIT_ENABLED", True),
            frontend_dir=Path(_get(env, "FRONTEND_DIR", str(PROJECT_ROOT / "frontend"))),
            store_name=_get(env, "STORE_NAME", "Sniff Garden"),
            store_currency=_get(env, "STORE_CURRENCY", "USD").upper(),
            max_quantity=_int(env, "MAX_QUANTITY_PER_ORDER_LINE", 5, 1, 50),
            supplier_mode=_get(env, "SUPPLIER_MODE", "mock").lower(),
            supplier_1688_api_key=_get(env, "SUPPLIER_1688_API_KEY"),
            supplier_1688_api_secret=_get(env, "SUPPLIER_1688_API_SECRET"),
            supplier_1688_access_token=_get(env, "SUPPLIER_1688_ACCESS_TOKEN"),
            cj_api_key=_get(env, "CJ_API_KEY"),
            cj_access_token=_get(env, "CJ_ACCESS_TOKEN"),
            cj_refresh_token=_get(env, "CJ_REFRESH_TOKEN"),
            supplier_check_policy=_get(env, "SUPPLIER_CHECK_POLICY", "strict").lower(),
            stock_cache_max_age_minutes=_int(env, "STOCK_CACHE_MAX_AGE_MINUTES", 15, 0, 1440),
            fulfillment_mode=_get(env, "FULFILLMENT_MODE", "manual").lower(),
            payment_provider=_get(env, "PAYMENT_PROVIDER", "sandbox").lower(),
            payment_mode=_get(env, "PAYMENT_MODE", "test").lower(),
            payment_secret_key=_get(env, "PAYMENT_SECRET_KEY"),
            payment_webhook_secret=webhook_secret,
            admin_email=_get(env, "ADMIN_EMAIL").lower(),
            admin_password_hash=_get(env, "ADMIN_PASSWORD_HASH"),
            admin_token_ttl_minutes=_int(env, "ADMIN_TOKEN_TTL_MINUTES", 60, 5, 1440),
            pricing=pricing,
        )
        s._validate()
        return s

    # ------------------------------------------------------------------ validation
    def _validate(self) -> None:
        if self.supplier_mode not in ("mock", "manual", "1688", "cj"):
            raise ConfigError("SUPPLIER_MODE must be mock, manual, 1688 or cj")
        if self.payment_provider not in ("sandbox", "stripe"):
            raise ConfigError("PAYMENT_PROVIDER must be sandbox or stripe")
        if self.payment_mode not in ("test", "live"):
            raise ConfigError("PAYMENT_MODE must be test or live")
        if self.supplier_check_policy not in ("strict", "cache"):
            raise ConfigError("SUPPLIER_CHECK_POLICY must be strict or cache")
        if self.fulfillment_mode not in ("manual", "automatic"):
            raise ConfigError("FULFILLMENT_MODE must be manual or automatic")
        if self.fulfillment_mode == "automatic" and self.supplier_mode not in ("1688", "cj"):
            raise ConfigError("FULFILLMENT_MODE=automatic requires SUPPLIER_MODE=1688 or cj")
        if not (len(self.store_currency) == 3 and self.store_currency.isalpha()):
            raise ConfigError("STORE_CURRENCY must be a 3-letter ISO code")
        if not self.database_url:
            raise ConfigError("DATABASE_URL is required")
        if self.payment_provider == "stripe" and not self.payment_secret_key:
            raise ConfigError("PAYMENT_PROVIDER=stripe requires PAYMENT_SECRET_KEY")

        if self.is_production:
            problems = []
            if self.supplier_mode == "mock":
                problems.append("SUPPLIER_MODE=mock is not allowed in production (use manual or 1688)")
            if self.payment_provider == "sandbox":
                problems.append("PAYMENT_PROVIDER=sandbox is not allowed in production")
            if self.payment_mode != "live":
                problems.append("PAYMENT_MODE must be live in production")
            if len(self.secret_key) < 32:
                problems.append("SECRET_KEY must be set and at least 32 characters")
            if not self.database_url.startswith(("postgres://", "postgresql://")):
                problems.append("DATABASE_URL must be a PostgreSQL URL in production")
            if not self.admin_email or not self.admin_password_hash:
                problems.append("ADMIN_EMAIL and ADMIN_PASSWORD_HASH must be set")
            if not self.public_base_url.startswith("https://"):
                problems.append("PUBLIC_BASE_URL must be an https:// URL")
            if not self.payment_webhook_secret:
                problems.append("PAYMENT_WEBHOOK_SECRET must be set")
            if "*" in self.cors_allowed_origins:
                problems.append("CORS_ALLOWED_ORIGINS must not contain *")
            if self.supplier_mode == "cj" and not self.cj_api_key:
                problems.append("SUPPLIER_MODE=cj requires CJ_API_KEY")
            if self.supplier_mode == "1688" and not (
                self.supplier_1688_api_key and self.supplier_1688_api_secret
            ):
                problems.append("SUPPLIER_MODE=1688 requires SUPPLIER_1688_API_KEY / _SECRET")
            if self.payment_provider == "stripe" and not self.payment_secret_key.startswith(("sk_live_", "rk_live_")):
                problems.append("PAYMENT_SECRET_KEY must be a live key in production")
            if problems:
                raise ConfigError("Unsafe production configuration:\n - " + "\n - ".join(problems))
