"""Owner-scoped SQL access. No commit, schema initialization, or experiment imports."""
from contextlib import asynccontextmanager

from psycopg.types.json import Jsonb

from ..domain.errors import ResourceNotFound
from ..domain.models import ChangeProposal, ConfirmedSnapshot, SpecificAuthorization, Trip, WorkingDraft
from ..domain.rules import ChangeError, validate_confirmations
from .database import connection


class PostgresPlanRepository:
    def __init__(self, conn, actor: str):
        self.conn, self.actor = conn, actor

    async def one(self, query, params):
        row = await (await self.conn.execute(query, params)).fetchone()
        if row is None:
            raise ResourceNotFound("resource_not_found")
        return row

    async def trip(self, trip_id):
        row = await self.one("SELECT * FROM easytravel.trips WHERE trip_id=%s AND owner_id=%s FOR UPDATE",
                             (trip_id, self.actor))
        return Trip.model_validate(row)

    async def create(self, draft):
        if draft.owner_id != self.actor:
            raise ChangeError("wrong_actor")
        await self.conn.execute("INSERT INTO easytravel.trips(trip_id,owner_id) VALUES (%s,%s)",
                                (draft.trip_id, self.actor))
        await self.insert_version(draft)
        await self.conn.execute("INSERT INTO easytravel.working_drafts(trip_id,owner_id,revision) VALUES (%s,%s,%s)",
                                (draft.trip_id, self.actor, draft.revision))
        return await self.trip(draft.trip_id)

    async def working(self, trip_id):
        await self.trip(trip_id)  # Serialize short business transactions per trip, across connections.
        row = await self.one("""SELECT w.trip_id,w.owner_id,w.revision,w.latest_confirmed_version_id,
            v.requirements,v.payload,v.item_confirmations,v.requirements_revision
            FROM easytravel.working_drafts w JOIN easytravel.draft_versions v
            ON (w.trip_id,w.owner_id,w.revision)=(v.trip_id,v.owner_id,v.revision)
            WHERE w.trip_id=%s AND w.owner_id=%s""", (trip_id, self.actor))
        revision = row.pop("requirements_revision")
        draft = WorkingDraft.model_validate(row)
        if draft.requirements.requirements_revision != revision:
            raise ChangeError("requirements_revision_mismatch")
        validate_confirmations(draft)
        return draft

    async def insert_version(self, draft):
        await self.conn.execute("""INSERT INTO easytravel.draft_versions
            (trip_id,owner_id,revision,requirements_revision,requirements,payload,item_confirmations)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""", (draft.trip_id, self.actor, draft.revision,
                draft.requirements.requirements_revision, Jsonb(draft.requirements.model_dump(mode="json")),
                Jsonb(draft.payload.model_dump(mode="json")),
                Jsonb([c.model_dump(mode="json") for c in draft.item_confirmations])))

    async def advance(self, current, updated):
        if (current.owner_id != self.actor or updated.owner_id != self.actor
                or current.trip_id != updated.trip_id or updated.revision != current.revision + 1
                or updated.latest_confirmed_version_id != current.latest_confirmed_version_id):
            raise ChangeError("invalid_revision_transition")
        # CAS first: stale callers fail before a duplicate version INSERT.
        # The FK is deferred explicitly until the new immutable version exists.
        await self.conn.execute("SET CONSTRAINTS easytravel.working_version_fk DEFERRED")
        cursor = await self.conn.execute("""UPDATE easytravel.working_drafts SET revision=%s
            WHERE trip_id=%s AND owner_id=%s AND revision=%s""",
            (updated.revision, current.trip_id, self.actor, current.revision))
        if cursor.rowcount != 1:
            raise ChangeError("revision_conflict")
        await self.insert_version(updated)

    async def snapshot(self, snapshot_id):
        row = await self.one("""SELECT s.*,v.requirements,v.payload,v.item_confirmations
            FROM easytravel.confirmed_snapshots s JOIN easytravel.draft_versions v
              ON (s.trip_id,s.confirmed_by,s.source_working_revision)=(v.trip_id,v.owner_id,v.revision)
            WHERE s.snapshot_id=%s AND s.confirmed_by=%s""", (snapshot_id, self.actor))
        return ConfirmedSnapshot.model_validate(row)

    async def save_snapshot(self, snapshot):
        if snapshot.confirmed_by != self.actor:
            raise ChangeError("wrong_actor")
        row = await (await self.conn.execute("""SELECT snapshot_id FROM easytravel.confirmed_snapshots
            WHERE trip_id=%s AND source_working_revision=%s AND confirmed_by=%s""",
            (snapshot.trip_id, snapshot.source_working_revision, self.actor))).fetchone()
        if row:
            existing = await self.snapshot(row["snapshot_id"])
            if existing.disclosure_hash != snapshot.disclosure_hash:
                raise ChangeError("snapshot_disclosure_conflict")
            return existing
        await self.conn.execute("""INSERT INTO easytravel.confirmed_snapshots
            (snapshot_id,trip_id,confirmed_by,source_working_revision,disclosure_hash,budget,confirmed_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""", (snapshot.snapshot_id, snapshot.trip_id, self.actor,
                snapshot.source_working_revision, snapshot.disclosure_hash,
                Jsonb(snapshot.model_dump(mode="json")["budget"]), snapshot.confirmed_at))
        return snapshot

    async def point_snapshot(self, draft, snapshot_id):
        cursor = await self.conn.execute("""UPDATE easytravel.working_drafts SET latest_confirmed_version_id=%s
            WHERE trip_id=%s AND owner_id=%s AND revision=%s""",
            (snapshot_id, draft.trip_id, self.actor, draft.revision))
        if cursor.rowcount != 1:
            raise ChangeError("revision_conflict")

    async def save_proposal(self, proposal):
        if proposal.owner_id != self.actor:
            raise ChangeError("wrong_actor")
        await self.conn.execute("""INSERT INTO easytravel.change_proposals
            (proposal_id,trip_id,owner_id,base_working_revision,requirements_revision,output,candidate,
             level,touched_item_ids,candidate_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (proposal.proposal_id, proposal.trip_id, self.actor, proposal.output.base_working_revision,
             proposal.requirements_revision, Jsonb(proposal.output.model_dump(mode="json")),
             Jsonb(proposal.candidate.model_dump(mode="json")), proposal.level,
             list(proposal.touched_item_ids), proposal.candidate_hash))

    async def proposal(self, proposal_id):
        row = await self.one("SELECT * FROM easytravel.change_proposals WHERE proposal_id=%s AND owner_id=%s",
                             (proposal_id, self.actor))
        base = row.pop("base_working_revision")
        proposal = ChangeProposal.model_validate(row)
        if base != proposal.output.base_working_revision:
            raise ChangeError("proposal_integrity_error")
        return proposal

    async def save_authorization(self, grant):
        if grant.granted_by != self.actor:
            raise ChangeError("wrong_actor")
        await self.conn.execute("""INSERT INTO easytravel.specific_authorizations
            (authorization_id,proposal_id,trip_id,granted_by,candidate_hash,base_working_revision,item_ids,granted_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", (grant.authorization_id, grant.proposal_id, grant.trip_id,
                self.actor, grant.candidate_hash, grant.base_working_revision, list(grant.item_ids), grant.granted_at))

    async def authorization(self, authorization_id):
        row = await self.one("SELECT * FROM easytravel.specific_authorizations WHERE authorization_id=%s AND granted_by=%s",
                             (authorization_id, self.actor))
        consumed = row.pop("consumed_by_operation_id")
        return SpecificAuthorization.model_validate(row), consumed

    async def consume(self, authorization_id, operation_id):
        cursor = await self.conn.execute("""UPDATE easytravel.specific_authorizations SET consumed_by_operation_id=%s
            WHERE authorization_id=%s AND granted_by=%s AND consumed_by_operation_id IS NULL""",
            (operation_id, authorization_id, self.actor))
        if cursor.rowcount != 1:
            raise ChangeError("authorization_scope_mismatch")

    async def lock_operation(self, operation_id):
        await self.conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (operation_id,))

    async def receipt(self, operation_id, request_hash):
        row = await (await self.conn.execute("SELECT * FROM easytravel.operation_receipts WHERE operation_id=%s",
                                             (operation_id,))).fetchone()
        if row is None:
            return None
        if row["owner_id"] != self.actor or row["request_hash"] != request_hash:
            raise ChangeError("operation_id_conflict")
        return row["result"]

    async def save_receipt(self, operation_id, trip_id, kind, request_hash, result):
        await self.conn.execute("""INSERT INTO easytravel.operation_receipts
            (operation_id,trip_id,owner_id,operation_type,request_hash,result) VALUES (%s,%s,%s,%s,%s,%s)""",
            (operation_id, trip_id, self.actor, kind, request_hash, Jsonb(result)))


def unit_of_work(settings):
    @asynccontextmanager
    async def scope(actor):
        async with connection(settings) as conn, conn.transaction():
            yield PostgresPlanRepository(conn, actor)
    return scope
