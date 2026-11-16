"""Customer account repository."""
from __future__ import annotations

from typing import Optional

from backend.database.db import Database


def get_by_email(db: Database, email: str) -> Optional[dict]:
    return db.fetch_one("SELECT * FROM customers WHERE email = ?", [email.lower()])


def get(db: Database, customer_id: int) -> Optional[dict]:
    return db.fetch_one("SELECT * FROM customers WHERE id = ?", [customer_id])
