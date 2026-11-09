"""New schemas: never reinterpret or migrate a Spike checkpoint."""
from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.conninfo import make_conninfo

from ..spike02.database import business_connection, connection_string


def checkpoint_dsn():
    return make_conninfo(connection_string(checkpoint=True),
        options="-c search_path=m000_checkpoint_v1 -c statement_timeout=10000 -c lock_timeout=3000")


@asynccontextmanager
async def connection():
    async with business_connection() as conn:
        await conn.execute("SET search_path TO m000_business_v1")
        yield conn


async def initialize():
    async with connection() as conn:
        async with conn.transaction():
            await conn.execute("CREATE SCHEMA IF NOT EXISTS m000_business_v1")
            await conn.execute("CREATE SCHEMA IF NOT EXISTS m000_checkpoint_v1")
            await conn.execute(Path(__file__).parents[1].joinpath("spike03/schema.sql").read_text(encoding="utf-8"))
            await conn.execute(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
    async with AsyncPostgresSaver.from_conn_string(checkpoint_dsn()) as saver:
        await saver.setup()
