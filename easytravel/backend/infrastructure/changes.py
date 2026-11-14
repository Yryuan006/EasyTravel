"""Proposal persistence composes existing run, intake and plan repositories in one UoW."""
from contextlib import asynccontextmanager
import json
import psycopg
from psycopg.types.json import Jsonb
from .generations import PostgresGenerationRepository
from .repository import PostgresPlanRepository
from .database import connection, DatabaseUnavailable
from ..domain.changes import ProposalContext, DecisionResult
from ..domain.generation import GenerationRun
from ..domain.models import WorkingDraft
from ..domain.errors import IdempotencyKeyReused, ResourceNotFound
from ..domain.rules import ChangeError


class PostgresChangeRepository(PostgresGenerationRepository, PostgresPlanRepository):
    async def read_only(self):
        await self.conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')

    async def replay_change(self, key, request_hash, path, *, intent=False):
        row = await (await self.conn.execute("""SELECT request_hash,response_status,result FROM easytravel.api_commands
            WHERE owner_id=%s AND method='POST' AND path=%s AND idempotency_key=%s""",(self.actor,path,key))).fetchone()
        if row is None:
            return None
        if row['request_hash'] != request_hash:
            raise IdempotencyKeyReused()
        if row['response_status'] != (202 if intent else 200):
            raise RuntimeError('Invalid command receipt')
        return (GenerationRun if intent else DecisionResult).model_validate(row['result'])

    async def save_change_command(self, key, request_hash, result, trip_id, operation_id, path, status):
        from uuid import uuid4
        await self.conn.execute("""INSERT INTO easytravel.api_commands
            (command_id,operation_id,owner_id,method,path,idempotency_key,request_hash,trip_id,response_status,result)
            VALUES (%s,%s,%s,'POST',%s,%s,%s,%s,%s,%s)""",
            (str(uuid4()),operation_id,self.actor,path,key,request_hash,trip_id,status,Jsonb(result.model_dump(mode='json'))))

    async def require_current_intake(self, trip_id):
        trip = await self.read_trip(trip_id)
        if trip.synchronization != 'CURRENT' or trip.working.context is None:
            raise ChangeError('scope_replan_not_supported')

    async def accept_change(self, trip, request, run_id, attempt):
        await self.conn.execute("""INSERT INTO easytravel.generation_runs
            (run_id,trip_id,owner_id,intake_revision,base_working_revision,kind,status,attempt,retry_of)
            VALUES (%s,%s,%s,%s,%s,'CHANGE_INTENT','ACCEPTED',%s,%s)""",
            (run_id,trip.trip_id,self.actor,trip.intake.intake_revision,trip.working.revision,attempt,request.retry_of))
        await self.conn.execute("""INSERT INTO easytravel.change_run_inputs
            (run_id,trip_id,owner_id,base_working_revision,intent,source) VALUES (%s,%s,%s,%s,%s,%s)""",
            (run_id,trip.trip_id,self.actor,trip.working.revision,request.intent,Jsonb(trip.working.model_dump(mode='json'))))
        return await self.run(trip.trip_id,run_id)

    async def frozen_change(self, run):
        from ..domain.intake import WorkingView
        row = await self.one('SELECT source,intent FROM easytravel.change_run_inputs WHERE run_id=%s AND trip_id=%s AND owner_id=%s',
                             (run.run_id,run.trip_id,self.actor))
        return WorkingView.model_validate(row['source']),row['intent']

    async def finish_proposal(self, run, proposal, context):
        await self.save_proposal(proposal)
        await self.conn.execute('INSERT INTO easytravel.proposal_contexts(proposal_id,trip_id,owner_id,run_id,context) VALUES (%s,%s,%s,%s,%s)',
            (proposal.proposal_id,run.trip_id,self.actor,run.run_id,Jsonb(context.model_dump(mode='json'))))
        await self.conn.execute("""UPDATE easytravel.generation_runs SET status='SUCCEEDED',result=%s,diagnostics_budget=%s,updated_at=clock_timestamp()
            WHERE run_id=%s AND owner_id=%s AND status='RUNNING'""",
            (Jsonb(dict(working_revision=None,proposal_id=proposal.proposal_id)),Jsonb(context.model_dump(mode='json')['candidate_budget']),run.run_id,self.actor))

    async def proposal_context(self, trip_id, proposal_id):
        row = await self.one('SELECT context,run_id FROM easytravel.proposal_contexts WHERE trip_id=%s AND proposal_id=%s AND owner_id=%s',
                             (trip_id,proposal_id,self.actor))
        return ProposalContext.model_validate_json(json.dumps(row['context'])),row['run_id']

    async def decision(self, trip_id, proposal_id):
        row = await (await self.conn.execute('SELECT result FROM easytravel.proposal_decisions WHERE trip_id=%s AND proposal_id=%s AND owner_id=%s',
                          (trip_id,proposal_id,self.actor))).fetchone()
        return DecisionResult.model_validate(row['result']) if row else None

    async def save_decision(self, trip_id, result, operation_id, request_hash):
        await self.conn.execute('INSERT INTO easytravel.proposal_decisions(proposal_id,trip_id,owner_id,decision,operation_id,request_hash,result) VALUES (%s,%s,%s,%s,%s,%s,%s)',
            (result.proposal_id,trip_id,self.actor,result.decision,operation_id,request_hash,Jsonb(result.model_dump(mode='json'))))

    async def attach_change_context(self, trip_id, revision, context, run_id):
        await self.conn.execute('INSERT INTO easytravel.draft_contexts(trip_id,owner_id,working_revision,intake_revision,run_id,context) VALUES (%s,%s,%s,%s,%s,%s)',
            (trip_id,self.actor,revision,context.intake_revision,run_id,Jsonb(context.model_dump(mode='json'))))


def unit_of_work(settings):
    @asynccontextmanager
    async def scope(actor):
        try:
            async with connection(settings) as conn, conn.transaction():
                yield PostgresChangeRepository(conn,actor)
        except (psycopg.errors.UndefinedTable,psycopg.errors.UndefinedColumn,psycopg.errors.InvalidSchemaName):
            raise DatabaseUnavailable('Change migration is required') from None
    return scope
