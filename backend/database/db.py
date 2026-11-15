"""Tiny database abstraction over sqlite3 (dev/tests) and psycopg 3 (PostgreSQL).

All values go through bound parameters (`?` placeholders, converted to `%s` for
PostgreSQL). Table/column names that are interpolated come only from code-level
whitelists and are additionally validated against a strict identifier regex.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterable, Optional

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"unsafe identifier: {name!r}")
    return name


class Database:
    def __init__(self, url: str):
        self.url = url
        self._local = threading.local()
        if url.startswith(("postgres://", "postgresql://")):
            self.dialect = "postgres"
        elif url.startswith("sqlite:///"):
            self.dialect = "sqlite"
            self.path = url[len("sqlite:///"):]
            if self.path in ("", ":memory:"):
                raise ValueError("Use a file path for SQLite (connections are per-request)")
        else:
            raise ValueError("DATABASE_URL must start with postgresql:// or sqlite:///")

    # ------------------------------------------------------------------ connection
    @property
    def conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._connect()
            self._local.conn = c
            self._local.depth = 0
        return c

    def _connect(self):
        if self.dialect == "sqlite":
            import os
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            c = sqlite3.connect(self.path, isolation_level=None, timeout=15)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys = ON")
            c.execute("PRAGMA journal_mode = WAL")
            return c
        import psycopg  # type: ignore  (listed in requirements.txt)
        from psycopg.rows import dict_row  # type: ignore

        return psycopg.connect(self.url, autocommit=True, row_factory=dict_row)

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            try:
                c.close()
            finally:
                self._local.conn = None

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.dialect == "postgres" else sql

    # ------------------------------------------------------------------ queries
    def _run(self, sql: str, params: Iterable[Any] = ()):
        return self.conn.execute(self._sql(sql), tuple(params))

    def fetch_all(self, sql: str, params: Iterable[Any] = ()) -> list:
        return [dict(r) for r in self._run(sql, params).fetchall()]

    def fetch_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
        row = self._run(sql, params).fetchone()
        return dict(row) if row is not None else None

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Run a statement and return the affected row count."""
        return self._run(sql, params).rowcount

    def insert(self, sql: str, params: Iterable[Any] = ()) -> int:
        """INSERT ... and return the new primary key (adds RETURNING id)."""
        if "returning" not in sql.lower():
            sql = sql.rstrip().rstrip(";") + " RETURNING id"
        row = self._run(sql, params).fetchone()
        return int(dict(row)["id"])

    def insert_row(self, table: str, data: dict) -> int:
        cols = [_ident(c) for c in data]
        sql = f"INSERT INTO {_ident(table)} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
        return self.insert(sql, list(data.values()))

    def update_row(self, table: str, row_id: int, data: dict) -> int:
        if not data:
            return 0
        sets = ", ".join(f"{_ident(c)} = ?" for c in data)
        return self.execute(f"UPDATE {_ident(table)} SET {sets} WHERE id = ?", [*data.values(), row_id])

    # ------------------------------------------------------------------ transactions
    @contextmanager
    def transaction(self):
        """Atomic block. Nested use joins the outer transaction."""
        conn = self.conn
        if self._local.depth > 0:
            self._local.depth += 1
            try:
                yield
            finally:
                self._local.depth -= 1
            return
        conn.execute("BEGIN IMMEDIATE" if self.dialect == "sqlite" else "BEGIN")
        self._local.depth = 1
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
        finally:
            self._local.depth = 0
