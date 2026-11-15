"""Minimal SQL migration runner.   Usage:  python -m backend.database.migrate"""
from __future__ import annotations

import sys
from pathlib import Path

from backend.config.settings import PROJECT_ROOT, Settings
from backend.database.db import Database
from backend.models.common import utcnow

MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
PK = {
    "sqlite": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "postgres": "BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY",
}


def _statements(sql: str):
    cleaned = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in cleaned.split(";") if s.strip()]


def run_migrations(db: Database, directory: Path = MIGRATIONS_DIR) -> list:
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    done = {r["name"] for r in db.fetch_all("SELECT name FROM schema_migrations")}
    applied = []
    for path in sorted(directory.glob("*.sql")):
        if path.name in done:
            continue
        sql = path.read_text(encoding="utf-8").replace("{PK}", PK[db.dialect])
        with db.transaction():
            for stmt in _statements(sql):
                db.execute(stmt)
            db.execute(
                "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                [path.name, utcnow()],
            )
        applied.append(path.name)
    return applied


def main() -> int:
    settings = Settings.from_env()
    db = Database(settings.database_url)
    applied = run_migrations(db)
    print("Applied:", ", ".join(applied) if applied else "nothing (up to date)")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
