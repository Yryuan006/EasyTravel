"""Working updates and whole-plan confirmation are separate short transactions."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
from uuid import uuid4

from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from ..contracts import ID, PlanDraftOutput, TripRequirements
from ..model_adapter import generate_output
from ..rules import assess_confirmation, disclosure_hash
from ..spike02.repository import OWNER, digest
from .database import connection
from .models import ChangeProposal, ConfirmedSnapshot, ItemConfirmation, SafeChangeOutput, SpecificAuthorization
from .repository import Repository
from .rules import ChangeError, check_constraints, construct_candidate, item_hash


def budget_data(budget):
    return json.loads(json.dumps(asdict(budget)))


class PlanService:
    def __init__(self, conn, *, fault=lambda _: None, now=lambda: datetime.now(timezone.utc)):
        self.conn, self.repository = conn, Repository(conn)
        self.fault, self.now, self.lock = fault, now, asyncio.Lock()

    @staticmethod
    def actor(actor):
        if actor != OWNER:
            raise ChangeError("wrong_actor")

    @staticmethod
    def identifier(value):
        return TypeAdapter(ID).validate_python(value)

    @staticmethod
    def validate_proposal(proposal):
        if digest(proposal.model_dump(mode="json", exclude={"candidate_hash"})) != proposal.candidate_hash:
            raise ChangeError("proposal_integrity_error")

    async def seed_working(self, trip_id, payload: PlanDraftOutput, requirements: TripRequirements,
                           *, revision=1, confirmed_item_ids=(), actor=OWNER):
        """Server-owned test bootstrap, not a model action or production confirmation API."""
        self.actor(actor)
        self.identifier(trip_id)
        if type(revision) is not int or revision < 1:
            raise ChangeError("invalid_revision")
        check_constraints(payload, requirements, now=self.now())
        items = {item.id: item for item in payload.items}
        if len(set(confirmed_item_ids)) != len(confirmed_item_ids) or not set(confirmed_item_ids) <= items.keys():
            raise ChangeError("invalid_confirmed_item_ids")
        confirmations = [ItemConfirmation(item_id=i, item_hash=item_hash(items[i])).model_dump(mode="json")
                         for i in confirmed_item_ids]
        async with self.lock, self.conn.transaction():
            await self.conn.execute(
                "INSERT INTO working_drafts(trip_id,owner_id,revision,requirements_revision,requirements,payload,item_confirmations) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (trip_id, actor, revision, requirements.requirements_revision,
                 Jsonb(requirements.model_dump(mode="json")), Jsonb(payload.model_dump(mode="json")), Jsonb(confirmations)))
        return await self.repository.working(trip_id)

    async def get_working(self, trip_id, *, actor=OWNER):
        self.actor(actor)
        async with self.lock:
            return await self.repository.working(trip_id)

    async def get_snapshot(self, snapshot_id, *, actor=OWNER):
        self.actor(actor)
        async with self.lock:
            return await self.repository.snapshot(snapshot_id)

    async def propose_change(self, trip_id, adapter, *, actor=OWNER):
        self.actor(actor)
        async with self.lock:
            working = await self.repository.working(trip_id)
            output = await generate_output(adapter, SafeChangeOutput)
            candidate = construct_candidate(working, output)
            data = dict(proposal_id=str(uuid4()), trip_id=trip_id, owner_id=actor,
                requirements_revision=working.requirements.requirements_revision,
                output=output.model_dump(mode="json"), candidate=candidate.payload.model_dump(mode="json"),
                level=candidate.level, touched_item_ids=list(candidate.touched))
            proposal = ChangeProposal.model_validate(data | dict(candidate_hash=digest(data)))
            async with self.conn.transaction():
                await self.conn.execute("INSERT INTO change_proposals(proposal_id,trip_id,data) VALUES (%s,%s,%s)",
                    (proposal.proposal_id, trip_id, Jsonb(proposal.model_dump(mode="json"))))
            return proposal

    async def authorize_change(self, proposal_id, *, seen_candidate_hash, item_ids,
                               explicit_authorization, actor=OWNER):
        self.actor(actor)
        if explicit_authorization is not True:
            raise ChangeError("explicit_authorization_required")
        async with self.lock, self.conn.transaction():
            proposal = await self.repository.proposal(proposal_id)
            self.validate_proposal(proposal)
            working = await self.repository.working(proposal.trip_id)
            if working.revision != proposal.output.base_working_revision:
                raise ChangeError("stale_proposal")
            if (proposal.level != "L3" or seen_candidate_hash != proposal.candidate_hash
                    or tuple(sorted(item_ids)) != proposal.touched_item_ids):
                raise ChangeError("authorization_scope_mismatch")
            authorization = SpecificAuthorization(authorization_id=str(uuid4()), trip_id=proposal.trip_id,
                proposal_id=proposal.proposal_id, candidate_hash=proposal.candidate_hash,
                base_working_revision=working.revision, item_ids=proposal.touched_item_ids,
                granted_by=actor, granted_at=self.now())
            await self.conn.execute(
                "INSERT INTO specific_authorizations(authorization_id,proposal_id,data) VALUES (%s,%s,%s)",
                (authorization.authorization_id, proposal_id, Jsonb(authorization.model_dump(mode="json"))))
            return authorization

    async def apply_working_change(self, proposal_id, *, operation_id, authorization_id=None, actor=OWNER):
        self.actor(actor)
        self.identifier(operation_id)
        async with self.lock, self.conn.transaction():
            proposal = await self.repository.proposal(proposal_id)
            self.validate_proposal(proposal)
            request_hash = digest(dict(kind="apply_working_change", trip_id=proposal.trip_id, actor=actor,
                proposal_id=proposal_id, candidate_hash=proposal.candidate_hash, authorization_id=authorization_id))
            existing = await self.repository.receipt(operation_id, request_hash)
            if existing is not None:
                return existing  # Before stale checks: retries after later changes return original result.
            working = await self.repository.working(proposal.trip_id)
            if proposal.requirements_revision != working.requirements.requirements_revision:
                raise ChangeError("stale_requirements")
            candidate = construct_candidate(working, proposal.output)
            if (candidate.payload != proposal.candidate or candidate.level != proposal.level
                    or candidate.touched != proposal.touched_item_ids):
                raise ChangeError("candidate_mismatch")
            if candidate.level == "L3":
                if authorization_id is None:
                    raise ChangeError("specific_authorization_required")
                grant, consumed = await self.repository.authorization(authorization_id)
                if (grant.trip_id != working.trip_id or grant.proposal_id != proposal_id
                        or grant.candidate_hash != proposal.candidate_hash or grant.granted_by != actor
                        or grant.base_working_revision != working.revision or grant.item_ids != candidate.touched
                        or consumed is not None):
                    raise ChangeError("authorization_scope_mismatch")
                await self.conn.execute(
                    "UPDATE specific_authorizations SET consumed_by_operation_id=%s WHERE authorization_id=%s",
                    (operation_id, authorization_id))
            elif authorization_id is not None:
                raise ChangeError("unexpected_authorization")
            budget = check_constraints(candidate.payload, working.requirements, now=self.now())
            cursor = await self.conn.execute(
                "UPDATE working_drafts SET payload=%s,item_confirmations=%s,revision=revision+1 "
                "WHERE trip_id=%s AND revision=%s AND requirements_revision=%s",
                (Jsonb(candidate.payload.model_dump(mode="json")),
                 Jsonb([c.model_dump(mode="json") for c in candidate.remaining_confirmations]),
                 working.trip_id, working.revision, working.requirements.requirements_revision))
            if cursor.rowcount != 1:
                raise ChangeError("revision_conflict")
            self.fault("after_working_update")
            result = dict(operation_id=operation_id, operation_type="apply_working_change", trip_id=working.trip_id,
                working_revision=working.revision+1, latest_confirmed_version_id=working.latest_confirmed_version_id,
                level=candidate.level, explanation=proposal.output.explanation, budget=budget_data(budget))
            await self.repository.save_receipt(operation_id, working.trip_id, "apply_working_change", request_hash, result)
            self.fault("before_change_commit")
        self.fault("after_change_commit")
        return result

    async def confirmation_preview(self, trip_id, *, actor=OWNER):
        self.actor(actor)
        async with self.lock:
            working = await self.repository.working(trip_id)
            budget = check_constraints(working.payload, working.requirements, now=self.now())
            return dict(working_revision=working.revision, budget=budget_data(budget),
                disclosure_hash=disclosure_hash(working.payload, working.requirements, working.revision, budget),
                unknown_costs=[dict(id=c.id, reason=c.unknown_reason) for c in working.payload.costs if c.amount_cents is None])

    async def confirm_plan(self, trip_id, *, operation_id, expected_revision, seen_disclosure_hash,
                           explicit_confirmation, actor=OWNER):
        self.actor(actor)
        self.identifier(operation_id)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ChangeError("invalid_revision")
        async with self.lock, self.conn.transaction():
            request_hash = digest(dict(kind="confirm_plan", trip_id=trip_id, actor=actor,
                expected_revision=expected_revision, seen_disclosure_hash=seen_disclosure_hash,
                explicit_confirmation=explicit_confirmation))
            existing = await self.repository.receipt(operation_id, request_hash)
            if existing is not None:
                return existing
            working = await self.repository.working(trip_id)
            if working.revision != expected_revision:
                raise ChangeError("stale_revision")
            check_constraints(working.payload, working.requirements, now=self.now())
            assessment = assess_confirmation(working.payload, working.requirements, {}, now=self.now(),
                working_revision=working.revision, seen_revision=expected_revision,
                seen_disclosure_hash=seen_disclosure_hash, explicit_confirmation=explicit_confirmation)
            if not assessment.allowed:
                raise ChangeError(assessment.reason)
            row = await (await self.conn.execute(
                "SELECT snapshot_id FROM confirmed_snapshots WHERE trip_id=%s AND source_working_revision=%s",
                (trip_id, working.revision))).fetchone()
            if row:
                snapshot = await self.repository.snapshot(row["snapshot_id"])
                if snapshot.disclosure_hash != assessment.disclosure_hash:
                    raise ChangeError("snapshot_disclosure_conflict")
            else:
                snapshot = ConfirmedSnapshot(snapshot_id=str(uuid4()), trip_id=trip_id,
                    source_working_revision=working.revision, requirements=working.requirements,
                    payload=working.payload, item_confirmations=working.item_confirmations,
                    disclosure_hash=assessment.disclosure_hash, budget=assessment.budget,
                    confirmed_by=actor, confirmed_at=self.now())
                await self.conn.execute(
                    "INSERT INTO confirmed_snapshots(snapshot_id,trip_id,source_working_revision,data) VALUES (%s,%s,%s,%s)",
                    (snapshot.snapshot_id, trip_id, working.revision, Jsonb(snapshot.model_dump(mode="json"))))
            self.fault("after_snapshot_insert")
            cursor = await self.conn.execute(
                "UPDATE working_drafts SET latest_confirmed_version_id=%s WHERE trip_id=%s AND revision=%s AND requirements_revision=%s",
                (snapshot.snapshot_id, trip_id, working.revision, working.requirements.requirements_revision))
            if cursor.rowcount != 1:
                raise ChangeError("revision_conflict")
            self.fault("after_confirmation_pointer")
            result = dict(operation_id=operation_id, operation_type="confirm_plan", trip_id=trip_id,
                working_revision=working.revision, latest_confirmed_version_id=snapshot.snapshot_id,
                plan_confirmation="CONFIRMED", budget=budget_data(assessment.budget),
                readiness="NOT_ASSESSED", booked=False)
            await self.repository.save_receipt(operation_id, trip_id, "confirm_plan", request_hash, result)
            self.fault("before_confirmation_commit")
        self.fault("after_confirmation_commit")
        return result

    async def status(self, trip_id, *, actor=OWNER):
        self.actor(actor)
        async with self.lock:
            working = await self.repository.working(trip_id)
            state = "UNCONFIRMED"
            if working.latest_confirmed_version_id:
                snapshot = await self.repository.snapshot(working.latest_confirmed_version_id)
                state = "CONFIRMED" if snapshot.source_working_revision == working.revision else "HAS_UNCONFIRMED_CHANGES"
            budget = check_constraints(working.payload, working.requirements, now=self.now())
            return dict(working_revision=working.revision, latest_confirmed_version_id=working.latest_confirmed_version_id,
                plan_confirmation=state, budget=budget_data(budget), readiness="NOT_ASSESSED", booked=False,
                confirmed_item_ids=[c.item_id for c in working.item_confirmations])


@asynccontextmanager
async def open_service(*, fault=lambda _: None):
    async with connection() as conn:
        yield PlanService(conn, fault=fault)
