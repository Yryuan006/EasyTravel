"""Dedicated ENV-00 database guard and explicit experiment initialization."""
from contextlib import asynccontextmanager
import os
from pathlib import Path

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row


def connection_string(*, checkpoint: bool = False) -> str:
    dsn = os.environ.get("EASYTRAVEL_TEST_DATABASE_URL", "")
    settings = conninfo_to_dict(dsn)
    if (settings.get("dbname") != "easytravel_env00_test"
            or settings.get("user") != "easytravel_env00"
            or settings.get("host") not in {"127.0.0.1", "localhost", "::1"}
            or settings.get("hostaddr", "127.0.0.1") not in {"127.0.0.1", "::1"}
            or settings.get("port") != "55432" or settings.get("service")):
        raise ValueError("only the dedicated ENV-00 database is permitted")
    schema = "spike02_checkpoint" if checkpoint else "spike02_business"
    return make_conninfo(dsn, connect_timeout=5, application_name="easytravel-spike02",
                         options=f"-c search_path={schema} -c statement_timeout=10000 -c lock_timeout=3000")


@asynccontextmanager
async def business_connection():
    async with await psycopg.AsyncConnection.connect(
        connection_string(), autocommit=True, row_factory=dict_row,
    ) as conn:
        row = await (await conn.execute("SELECT current_database() AS db, current_user AS usr")).fetchone()
        if row != {"db": "easytravel_env00_test", "usr": "easytravel_env00"}:
            raise ValueError("unexpected database identity")
        yield conn


async def initialize() -> None:
    """Run once before workers, never once per decision. No drops or data cleanup."""
    async with business_connection() as conn:
        async with conn.transaction():
            await conn.execute("CREATE SCHEMA IF NOT EXISTS spike02_business")
            await conn.execute("CREATE SCHEMA IF NOT EXISTS spike02_checkpoint")
            await conn.execute(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
    async with AsyncPostgresSaver.from_conn_string(connection_string(checkpoint=True)) as saver:
        await saver.setup()
