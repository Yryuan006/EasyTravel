"""Read-only ENV-00 checks. This does not create tables or run an Agent graph."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import struct
import sys
from importlib.metadata import version


def emit(event: str, **details: object) -> None:
    print(json.dumps({"event": event, **details}, ensure_ascii=False), flush=True)


def check_imports() -> None:
    if sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
        raise RuntimeError("Python 3.12 x64 is required")
    packages = {
        "fastapi": "fastapi",
        "pydantic": "pydantic",
        "langgraph": "langgraph.graph",
        "langgraph-checkpoint": "langgraph.checkpoint.base",
        "langgraph-checkpoint-postgres": "langgraph.checkpoint.postgres.aio",
        "psycopg": "psycopg",
        "psycopg-binary": "psycopg_binary",
        "pytest": "pytest",
        "pytest-asyncio": "pytest_asyncio",
        "httpx": "httpx",
    }
    for module in packages.values():
        importlib.import_module(module)
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.types import Command, interrupt
    from pydantic import BaseModel

    assert all((AsyncPostgresSaver, Command, interrupt, BaseModel))
    emit(
        "imports_ok",
        python=sys.version.split()[0],
        executable=sys.executable,
        bits=struct.calcsize("P") * 8,
        versions={package: version(package) for package in packages},
    )


async def check_database() -> None:
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    dsn = os.environ.get("EASYTRAVEL_TEST_DATABASE_URL")
    if not dsn:
        emit("database_not_run", reason="EASYTRAVEL_TEST_DATABASE_URL is unset")
        raise SystemExit(2)
    settings = conninfo_to_dict(dsn)
    if (
        settings.get("dbname") != "easytravel_env00_test"
        or settings.get("user") != "easytravel_env00"
        or settings.get("host") not in {"127.0.0.1", "localhost", "::1"}
        or settings.get("port") != "55432"
        or settings.get("service")
    ):
        emit("database_refused", reason="Only the dedicated local ENV-00 target is allowed")
        raise SystemExit(2)
    connection = await psycopg.AsyncConnection.connect(
        dsn,
        connect_timeout=5,
        autocommit=True,
        options="-c default_transaction_read_only=on",
        application_name="easytravel-env00-verify",
    )
    try:
        async with connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT current_database(), current_user, "
                    "current_setting('server_version'), current_setting('TimeZone'), "
                    "current_setting('transaction_read_only'), "
                    "has_schema_privilege(current_user, 'public', 'CREATE'), 1"
                )
                row = await cursor.fetchone()
            if row is None or row[0:2] != ("easytravel_env00_test", "easytravel_env00"):
                raise RuntimeError("Unexpected database identity")
            if row[4:] != ("on", True, 1):
                raise RuntimeError("Read-only SELECT check failed")
            emit(
                "database_select_ok",
                database=row[0],
                user=row[1],
                server_version=row[2],
                timezone=row[3],
                read_only=row[4],
                can_create_in_public=row[5],
                select_value=row[6],
                event_loop=type(asyncio.get_running_loop()).__name__,
            )
    finally:
        await connection.close()
        emit("database_connection_closed", closed=connection.closed)
    if not connection.closed:
        raise RuntimeError("Connection was not closed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", action="store_true", help="Also run the dedicated read-only DB check")
    args = parser.parse_args()
    try:
        check_imports()
        if args.database:
            loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
            with asyncio.Runner(loop_factory=loop_factory) as runner:
                runner.run(asyncio.wait_for(check_database(), timeout=15))
        return 0
    except Exception as error:
        # Never echo exception messages, the DSN, or server details containing credentials.
        emit("verification_failed", error_type=type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
