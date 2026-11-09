"""Reuse the ENV-00 target guard; isolate business tables from earlier Spikes."""
from contextlib import asynccontextmanager
from pathlib import Path

from ..spike02.database import business_connection


@asynccontextmanager
async def connection():
    async with business_connection() as conn:
        await conn.execute("SET search_path TO spike03_business")
        yield conn


async def initialize():
    async with connection() as conn:
        async with conn.transaction():
            await conn.execute("CREATE SCHEMA IF NOT EXISTS spike03_business")
            await conn.execute(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
