"""Application context passed explicitly to services (keeps them testable)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.config.settings import Settings
from backend.database.db import Database
from backend.payments.base import PaymentProvider
from backend.suppliers.base import SupplierAdapter


@dataclass
class AppContext:
    settings: Settings
    db: Database
    supplier: SupplierAdapter
    payments: PaymentProvider
    limiter: Any = None
