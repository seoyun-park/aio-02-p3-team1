"""Explicit, checksummed migrations; dry-run unless --apply is supplied."""
import argparse
import hashlib
import os
from pathlib import Path
import re

import psycopg


def migrate(connection, directory: Path, apply: bool = False):
    connection.execute("SELECT pg_advisory_lock(92741602)")
    try:
        ledger_exists = connection.execute(
            "SELECT to_regclass('public.schema_migrations')"
        ).fetchone()[0] is not None
        if not ledger_exists:
            tables = connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ).fetchall()
            if tables:
                raise RuntimeError("Untracked nonempty database: inspect and baseline it before migration.")
        applied = dict(connection.execute(
            "SELECT name, sha256 FROM schema_migrations"
        ).fetchall()) if ledger_exists else {}
        pending = []
        for path in sorted(directory.glob("*.sql")):
            content = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if path.name in applied:
                if applied[path.name] != checksum:
                    raise RuntimeError(f"Previously applied migration changed: {path.name}")
                continue
            pending.append((path.name, checksum, content))
        if not apply:
            print("Pending migrations:", ", ".join(item[0] for item in pending) or "none")
            return
        with connection.transaction():
            connection.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        for name, checksum, content in pending:
            # Existing files wrap their whole body in BEGIN/COMMIT. Move that
            # boundary here so schema changes and ledger insertion are atomic.
            content = re.sub(r"^BEGIN;\s*$", "", content, count=1, flags=re.MULTILINE)
            content = re.sub(r"^COMMIT;\s*$", "", content, count=1, flags=re.MULTILINE)
            with connection.transaction():
                connection.execute(content, prepare=False)
                connection.execute("INSERT INTO schema_migrations (name, sha256) VALUES (%s, %s)", (name, checksum))
            print("Applied:", name)
    finally:
        connection.execute("SELECT pg_advisory_unlock(92741602)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with psycopg.connect(
        host=os.environ["DATABASE_HOST"], port=os.environ["DATABASE_PORT"],
        dbname=os.environ["DATABASE_NAME"], user=os.environ["DATABASE_USER"],
        password=os.environ["DATABASE_PASSWORD"], autocommit=True,
    ) as connection:
        migrate(connection, Path(__file__).resolve().parents[1] / "migrations", args.apply)
