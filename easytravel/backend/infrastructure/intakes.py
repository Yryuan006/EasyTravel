"""Owner-scoped PostgreSQL intake adapter. The application owns the transaction."""
from contextlib import asynccontextmanager
import json

import psycopg
from psycopg.types.json import Jsonb

from ..domain.errors import CommandInProgress, IdempotencyKeyReused, ResourceNotFound
from ..domain.intake import RequirementsInput, TripView, trip_view
from .database import DatabaseUnavailable, connection


class PostgresIntakeRepository:
    def __init__(self, conn, actor):
        self.conn, self.actor = conn, actor

    async def lock_command(self, scope):
        try:
            await self.conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 10101))", (scope,))
        except psycopg.errors.LockNotAvailable:
            raise CommandInProgress() from None

    async def command(self, key, request_hash):
        row = await (await self.conn.execute("""SELECT request_hash,result FROM easytravel.api_commands
            WHERE owner_id=%s AND method='POST' AND path='/api/v1/trips' AND idempotency_key=%s""",
            (self.actor, key))).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise IdempotencyKeyReused()
        return TripView.model_validate_json(json.dumps(row["result"]))

    async def create_intake(self, trip_id, request):
        row = await (await self.conn.execute("""INSERT INTO easytravel.trips(trip_id,owner_id)
            VALUES (%s,%s) RETURNING created_at""", (trip_id, self.actor))).fetchone()
        await self.conn.execute("""INSERT INTO easytravel.trip_intake_versions(trip_id,owner_id,revision,input)
            VALUES (%s,%s,1,%s)""", (trip_id, self.actor, Jsonb(request.requirements.model_dump(mode="json"))))
        await self.conn.execute("INSERT INTO easytravel.trip_intakes(trip_id,owner_id,revision) VALUES (%s,%s,1)",
                                (trip_id, self.actor))
        return trip_view(trip_id, row["created_at"], 1, request.requirements)

    async def save_command(self, key, request_hash, result, command_id, operation_id):
        await self.conn.execute("""INSERT INTO easytravel.api_commands
            (command_id,operation_id,owner_id,method,path,idempotency_key,request_hash,trip_id,response_status,result)
            VALUES (%s,%s,%s,'POST','/api/v1/trips',%s,%s,%s,201,%s)""",
            (command_id, operation_id, self.actor, key, request_hash, result.trip_id, Jsonb(result.model_dump(mode="json"))))

    async def read_trip(self, trip_id):
        row = await (await self.conn.execute("""SELECT t.trip_id,t.created_at,v.revision,v.input
            FROM easytravel.trips t JOIN easytravel.trip_intakes i
              ON (t.trip_id,t.owner_id)=(i.trip_id,i.owner_id)
            JOIN easytravel.trip_intake_versions v
              ON (i.trip_id,i.owner_id,i.revision)=(v.trip_id,v.owner_id,v.revision)
            WHERE t.trip_id=%s AND t.owner_id=%s
              AND NOT EXISTS (SELECT 1 FROM easytravel.working_drafts w WHERE w.trip_id=t.trip_id)""",
            (trip_id, self.actor))).fetchone()
        if row is None:
            # Legacy Trips without explicit user intake have no supported HTTP read model.
            raise ResourceNotFound()
        return trip_view(row["trip_id"], row["created_at"], row["revision"],
                         RequirementsInput.model_validate(row["input"]))


def unit_of_work(settings):
    @asynccontextmanager
    async def scope(actor):
        try:
            async with connection(settings) as conn, conn.transaction():
                yield PostgresIntakeRepository(conn, actor)
        except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn, psycopg.errors.InvalidSchemaName):
            raise DatabaseUnavailable("Business schema is not ready") from None
    return scope
