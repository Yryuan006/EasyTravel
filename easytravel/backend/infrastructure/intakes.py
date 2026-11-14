"""Owner-scoped PostgreSQL intake adapter. The application owns the transaction."""
from contextlib import asynccontextmanager
import json

import psycopg
from psycopg.types.json import Jsonb

from ..domain.errors import CommandInProgress, IdempotencyKeyReused, ResourceNotFound, BusinessConflict
from ..domain.intake import RequirementsInput, TripView, trip_view, ClarificationQuestion, next_field
from .database import DatabaseUnavailable, connection


class PostgresIntakeRepository:
    def __init__(self, conn, actor):
        self.conn, self.actor = conn, actor

    async def lock_command(self, scope):
        try:
            await self.conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 10101))", (scope,))
        except psycopg.errors.LockNotAvailable:
            raise CommandInProgress() from None

    async def command(self, key, request_hash, method="POST", path="/api/v1/trips"):
        row = await (await self.conn.execute("""SELECT request_hash,result FROM easytravel.api_commands
            WHERE owner_id=%s AND method=%s AND path=%s AND idempotency_key=%s""",
            (self.actor, method, path, key))).fetchone()
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

    async def save_command(self, key, request_hash, result, command_id, operation_id,
                           method="POST", path="/api/v1/trips", status=201):
        await self.conn.execute("""INSERT INTO easytravel.api_commands
            (command_id,operation_id,owner_id,method,path,idempotency_key,request_hash,trip_id,response_status,result)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (command_id, operation_id, self.actor, method, path, key, request_hash, result.trip_id, status, Jsonb(result.model_dump(mode="json"))))

    async def read_trip(self, trip_id):
        row = await (await self.conn.execute("""SELECT t.trip_id,t.created_at,v.revision,v.input, w.revision AS working_revision,w.latest_confirmed_version_id,
                   d.requirements,d.payload,d.item_confirmations,s.source_working_revision AS confirmed_revision
            FROM easytravel.trips t JOIN easytravel.trip_intakes i
              ON (t.trip_id,t.owner_id)=(i.trip_id,i.owner_id)
            JOIN easytravel.trip_intake_versions v
              ON (i.trip_id,i.owner_id,i.revision)=(v.trip_id,v.owner_id,v.revision)
            LEFT JOIN easytravel.working_drafts w ON (w.trip_id,w.owner_id)=(t.trip_id,t.owner_id)
            LEFT JOIN easytravel.draft_versions d ON (d.trip_id,d.owner_id,d.revision)=(w.trip_id,w.owner_id,w.revision)
            LEFT JOIN easytravel.confirmed_snapshots s ON (s.snapshot_id,s.trip_id,s.confirmed_by)=(w.latest_confirmed_version_id,w.trip_id,w.owner_id)
            WHERE t.trip_id=%s AND t.owner_id=%s""",
            (trip_id, self.actor))).fetchone()
        if row is None:
            # Legacy Trips without explicit user intake have no supported HTTP read model.
            raise ResourceNotFound()
        working = None
        if row["working_revision"] is not None:
            from ..domain.models import WorkingDraft
            working = WorkingDraft.model_validate(dict(trip_id=trip_id,owner_id=self.actor,
                revision=row["working_revision"],requirements=row["requirements"],payload=row["payload"],
                item_confirmations=row["item_confirmations"],latest_confirmed_version_id=row["latest_confirmed_version_id"]))
        context, active = None, None
        schema = await (await self.conn.execute("SELECT to_regclass('easytravel.generation_runs') AS t")).fetchone()
        if schema["t"] is not None:
            from ..domain.intake import DraftContext
            ctx = await (await self.conn.execute("SELECT context FROM easytravel.draft_contexts WHERE trip_id=%s AND owner_id=%s AND working_revision=%s", (trip_id,self.actor,row["working_revision"]))).fetchone()
            context = DraftContext.model_validate(ctx["context"]) if ctx else None
            run = await (await self.conn.execute("SELECT run_id FROM easytravel.generation_runs WHERE trip_id=%s AND owner_id=%s AND status IN ('ACCEPTED','RUNNING')", (trip_id,self.actor))).fetchone()
            active = run["run_id"] if run else None
        return trip_view(row["trip_id"], row["created_at"], row["revision"],
                         RequirementsInput.model_validate(row["input"]), working, context, active, row["confirmed_revision"])


    async def lock_trip(self, trip_id):
        row = await (await self.conn.execute("SELECT trip_id FROM easytravel.trips WHERE trip_id=%s AND owner_id=%s FOR UPDATE",
                                            (trip_id,self.actor))).fetchone()
        if row is None:
            raise ResourceNotFound()

    async def next_question(self, trip):
        field = next_field(trip.intake.input)
        if field is None:
            return None
        row = await (await self.conn.execute("""SELECT question_id::text,intake_revision,field_key,prompt,allow_no_preference
            FROM easytravel.clarification_questions WHERE trip_id=%s AND owner_id=%s AND intake_revision=%s AND field_key=%s""",
            (trip.trip_id,self.actor,trip.intake.intake_revision,field))).fetchone()
        if row is None:
            raise DatabaseUnavailable("Clarification migration required")
        return ClarificationQuestion.model_validate(row)

    async def advance_intake(self, trip, requirements):
        revision = trip.intake.intake_revision
        await self.conn.execute("""INSERT INTO easytravel.trip_intake_versions(trip_id,owner_id,revision,input)
            VALUES (%s,%s,%s,%s)""",(trip.trip_id,self.actor,revision+1,Jsonb(requirements.model_dump(mode="json"))))
        cursor = await self.conn.execute("""UPDATE easytravel.trip_intakes SET revision=%s
            WHERE trip_id=%s AND owner_id=%s AND revision=%s""",(revision+1,trip.trip_id,self.actor,revision))
        if cursor.rowcount != 1:
            raise BusinessConflict()
        return await self.read_trip(trip.trip_id)

    async def save_answer(self, question, trip_id, answer, command_id):
        await self.conn.execute("""INSERT INTO easytravel.clarification_answers
            (question_id,trip_id,owner_id,intake_revision,field_key,resulting_revision,answer,command_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",(question.question_id,trip_id,self.actor,
                question.intake_revision,question.field_key,question.intake_revision+1,
                Jsonb(answer.model_dump(mode="json")),command_id))


def unit_of_work(settings):
    @asynccontextmanager
    async def scope(actor):
        try:
            async with connection(settings) as conn, conn.transaction():
                yield PostgresIntakeRepository(conn, actor)
        except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn, psycopg.errors.InvalidSchemaName):
            raise DatabaseUnavailable("Business schema is not ready") from None
    return scope
