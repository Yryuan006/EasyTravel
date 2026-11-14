"""Short PostgreSQL transactions for durable runs and atomic initial attachment."""
from contextlib import asynccontextmanager
import json
import psycopg
from psycopg.types.json import Jsonb
from .intakes import PostgresIntakeRepository
from .repository import PostgresPlanRepository
from .database import connection, DatabaseUnavailable
from ..domain.generation import GenerationRun
from ..domain.errors import ResourceNotFound, IdempotencyKeyReused

class PostgresGenerationRepository(PostgresIntakeRepository):
    async def replay(self, key, request_hash, path):
        row = await (await self.conn.execute("SELECT request_hash,result FROM easytravel.api_commands WHERE owner_id=%s AND method='POST' AND path=%s AND idempotency_key=%s", (self.actor,path,key))).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise IdempotencyKeyReused()
        return GenerationRun.model_validate_json(json.dumps(row["result"]))

    async def run(self, trip_id, run_id, *, lock=False):
        row = await (await self.conn.execute("SELECT * FROM easytravel.generation_runs WHERE trip_id=%s AND owner_id=%s AND run_id=%s" + (" FOR UPDATE" if lock else ""), (trip_id,self.actor,run_id))).fetchone()
        if row is None:
            raise ResourceNotFound()
        row.pop("owner_id")
        return GenerationRun.model_validate(row)

    async def state(self, trip_id):
        from ..domain.generation import GenerationState
        trip = await self.read_trip(trip_id)
        source = None
        if trip.working:
            row = await (await self.conn.execute("""SELECT g.run_id FROM easytravel.draft_contexts c
                JOIN easytravel.generation_runs g ON g.run_id=c.run_id AND g.trip_id=c.trip_id AND g.owner_id=c.owner_id
                WHERE c.trip_id=%s AND c.owner_id=%s AND c.working_revision=%s AND g.status='SUCCEEDED' AND g.intake_revision=c.intake_revision
                AND ((g.kind='INITIAL_PLAN' AND (g.result->>'working_revision')::integer=c.working_revision)
                     OR (g.kind='CHANGE_INTENT' AND g.base_working_revision+1=c.working_revision))""",
                (trip_id,self.actor,trip.working.revision))).fetchone()
            if row:
                source = await self.run(trip_id,row['run_id'])
        latest = await (await self.conn.execute("SELECT run_id FROM easytravel.generation_runs WHERE trip_id=%s AND owner_id=%s ORDER BY created_at DESC,run_id DESC LIMIT 1",(trip_id,self.actor))).fetchone()
        return GenerationState(trip_id=trip_id,working_revision=trip.working.revision if trip.working else None,
            source_run=source,latest_run=await self.run(trip_id,latest['run_id']) if latest else None)

    async def failed_for_input(self, trip_id, revision):
        return await (await self.conn.execute("SELECT 1 FROM easytravel.generation_runs WHERE trip_id=%s AND owner_id=%s AND intake_revision=%s AND status='FAILED' LIMIT 1", (trip_id,self.actor,revision))).fetchone() is not None

    async def frozen_input(self, run):
        from ..domain.intake import RequirementsInput
        row = await (await self.conn.execute("SELECT input FROM easytravel.trip_intake_versions WHERE trip_id=%s AND owner_id=%s AND revision=%s", (run.trip_id,self.actor,run.intake_revision))).fetchone()
        if row is None:
            raise ResourceNotFound()
        return RequirementsInput.model_validate(row["input"])

    async def accept(self, trip, run_id, attempt, retry_of):
        await self.conn.execute("""INSERT INTO easytravel.generation_runs(run_id,trip_id,owner_id,intake_revision,kind,status,attempt,retry_of)
            VALUES (%s,%s,%s,%s,'INITIAL_PLAN','ACCEPTED',%s,%s)""", (run_id,trip.trip_id,self.actor,trip.intake.intake_revision,attempt,retry_of))
        return await self.run(trip.trip_id,run_id)

    async def finish(self, run, *, error=None, budget=None):
        await self.conn.execute("""UPDATE easytravel.generation_runs SET status=%s,updated_at=clock_timestamp(),result=%s,error=%s,diagnostics_budget=%s
            WHERE run_id=%s AND owner_id=%s AND status='RUNNING'""",
            ('FAILED' if error else 'SUCCEEDED', None if error else Jsonb(dict(working_revision=1,proposal_id=None)),
             Jsonb(error) if error else None, Jsonb(budget) if budget else None,run.run_id,self.actor))

    async def attach(self, draft, context, run):
        await PostgresPlanRepository(self.conn,self.actor).insert_version(draft)
        await self.conn.execute("INSERT INTO easytravel.working_drafts(trip_id,owner_id,revision) VALUES (%s,%s,1)", (draft.trip_id,self.actor))
        await self.conn.execute("INSERT INTO easytravel.draft_contexts(trip_id,owner_id,working_revision,intake_revision,run_id,context) VALUES (%s,%s,1,%s,%s,%s)", (draft.trip_id,self.actor,context.intake_revision,run.run_id,Jsonb(context.model_dump(mode='json'))))

def unit_of_work(settings):
    @asynccontextmanager
    async def scope(actor):
        try:
            async with connection(settings) as conn, conn.transaction():
                yield PostgresGenerationRepository(conn,actor)
        except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn, psycopg.errors.InvalidSchemaName):
            raise DatabaseUnavailable("Generation migration is required") from None
    return scope
