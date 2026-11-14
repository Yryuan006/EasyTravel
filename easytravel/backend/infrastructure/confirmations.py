"""Confirmation adapter reuses existing plan persistence in the same connection."""
from contextlib import asynccontextmanager
import json
import psycopg
from psycopg.types.json import Jsonb
from .intakes import PostgresIntakeRepository
from .repository import PostgresPlanRepository
from .database import connection, DatabaseUnavailable
from ..domain.confirmation import ConfirmResult, RiskDisclosure
from ..domain.errors import IdempotencyKeyReused
from ..domain.rules import ChangeError


class PostgresConfirmationRepository(PostgresIntakeRepository, PostgresPlanRepository):
    async def read_only(self):
        await self.conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")

    async def replay(self, key, request_hash, path):
        row = await (await self.conn.execute("""SELECT request_hash,response_status,result FROM easytravel.api_commands
            WHERE owner_id=%s AND method='POST' AND path=%s AND idempotency_key=%s""",
            (self.actor, path, key))).fetchone()
        if row is None:
            return None
        if row['request_hash'] != request_hash:
            raise IdempotencyKeyReused()
        if row['response_status'] != 201:
            raise RuntimeError("Invalid confirmation receipt")
        return ConfirmResult.model_validate_json(json.dumps(row['result']))

    async def require_current_intake(self, trip_id):
        trip = await self.read_trip(trip_id)
        if trip.synchronization != 'CURRENT' or trip.working.context is None:
            raise ChangeError('stale_intake')

    async def source_snapshot(self, trip_id, revision):
        row = await (await self.conn.execute("""SELECT snapshot_id FROM easytravel.confirmed_snapshots
            WHERE trip_id=%s AND confirmed_by=%s AND source_working_revision=%s""",
            (trip_id, self.actor, revision))).fetchone()
        return await self.snapshot(row['snapshot_id']) if row else None

    async def disclosure(self, snapshot_id):
        row = await (await self.conn.execute("""SELECT disclosure FROM easytravel.confirmation_disclosures
            WHERE snapshot_id=%s AND owner_id=%s""", (snapshot_id, self.actor))).fetchone()
        return RiskDisclosure.model_validate(row['disclosure']) if row else None

    async def save_disclosure(self, snapshot, context, disclosure):
        await self.conn.execute("""INSERT INTO easytravel.confirmation_disclosures
            (snapshot_id,trip_id,owner_id,working_revision,intake_revision,input_hash,risk_hash,disclosure,context)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (snapshot.snapshot_id, snapshot.trip_id, self.actor, snapshot.source_working_revision,
             context.intake_revision, disclosure.input_hash, disclosure.risk_hash,
             Jsonb(disclosure.model_dump(mode='json')), Jsonb(context.model_dump(mode='json'))))


def unit_of_work(settings):
    @asynccontextmanager
    async def scope(actor):
        try:
            async with connection(settings) as conn, conn.transaction():
                yield PostgresConfirmationRepository(conn, actor)
        except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn, psycopg.errors.InvalidSchemaName):
            raise DatabaseUnavailable("Confirmation migration is required") from None
    return scope
