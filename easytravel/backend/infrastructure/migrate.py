"""Explicit, atomic forward-only SQL migrations: python -m ...migrate."""
import asyncio
import hashlib
from pathlib import Path
import sys

from .database import connection, load_database_settings

MIGRATIONS = Path(__file__).with_name("migrations")


class MigrationError(RuntimeError):
    pass


async def upgrade(settings, *, directory=MIGRATIONS):
    files = sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))
    versions = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    if not files or len({p.name[:3] for p in files}) != len(files):
        raise MigrationError("Invalid migration sequence")
    applied = []
    async with connection(settings) as conn, conn.transaction():
        # Database-local lock serializes installers, including the first schema creation.
        await conn.execute("SELECT pg_advisory_xact_lock(68402002)")
        await conn.execute("CREATE SCHEMA IF NOT EXISTS easytravel")
        await conn.execute("""CREATE TABLE IF NOT EXISTS easytravel.schema_migrations (
            version text PRIMARY KEY, checksum text NOT NULL,
            applied_at timestamptz NOT NULL DEFAULT clock_timestamp())""")
        rows = await (await conn.execute(
            "SELECT version, checksum FROM easytravel.schema_migrations ORDER BY version")).fetchall()
        if [r["version"] for r in rows] != list(versions)[:len(rows)]:
            raise MigrationError("Database migration history is not a known prefix")
        for row in rows:
            if versions[row["version"]] != row["checksum"]:
                raise MigrationError("Applied migration checksum mismatch")
        for path in files[len(rows):]:
            await conn.execute(path.read_text(encoding="utf-8"))
            await conn.execute("INSERT INTO easytravel.schema_migrations(version, checksum) VALUES (%s,%s)",
                               (path.name, versions[path.name]))
            applied.append(path.name)
    return applied


def main():
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
            applied = runner.run(upgrade(load_database_settings()))
        print("Database migration succeeded; applied=" + ",".join(applied))
        return 0
    except Exception as error:
        print("Database migration failed: " + type(error).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
