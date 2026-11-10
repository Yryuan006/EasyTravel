"""Persistence use cases; callers supply trusted identity, no HTTP or driver imports."""
from dataclasses import asdict
from datetime import datetime, timezone
import json
from uuid import uuid4

from pydantic import TypeAdapter

from ...contracts import ID, PlanDraftOutput, TripRequirements
from ...rules import assess_confirmation, disclosure_hash
from ..domain.hashing import digest
from ..domain.models import (ChangeProposal, ConfirmedSnapshot, ItemConfirmation,
                             SafeChangeOutput, SpecificAuthorization, WorkingDraft)
from ..domain.rules import ChangeError, check_constraints, construct_candidate, item_hash
from .ports import UnitOfWorkFactory


def budget_data(budget):
    return json.loads(json.dumps(asdict(budget)))


class PlanService:
    def __init__(self, uow: UnitOfWorkFactory, *, now=lambda: datetime.now(timezone.utc)):
        self.uow, self.now = uow, now

    @staticmethod
    def identifier(value):
        return TypeAdapter(ID).validate_python(value)

    async def create_trip(self, trip_id, payload: PlanDraftOutput, requirements: TripRequirements,
                          *, actor, confirmed_item_ids=()):
        """Persist a validated initial draft supplied by a trusted application caller.

        No generator, seed, confirmation, or booking side effects.
        Item confirmations may only come from a trusted upstream use case.
        """
        self.identifier(actor)
        self.identifier(trip_id)
        check_constraints(payload, requirements, now=self.now())
        items = {item.id: item for item in payload.items}
        if len(set(confirmed_item_ids)) != len(confirmed_item_ids) or not set(confirmed_item_ids) <= items.keys():
            raise ChangeError("invalid_confirmed_item_ids")
        draft = WorkingDraft(trip_id=trip_id, owner_id=actor, revision=1, requirements=requirements,
            payload=payload, latest_confirmed_version_id=None,
            item_confirmations=tuple(ItemConfirmation(item_id=i, item_hash=item_hash(items[i]))
                                     for i in confirmed_item_ids))
        async with self.uow(actor) as repo:
            await repo.create(draft)
        return draft

    async def get_working(self, trip_id, *, actor):
        self.identifier(actor)
        async with self.uow(actor) as repo:
            return await repo.working(trip_id)

    async def get_snapshot(self, snapshot_id, *, actor):
        self.identifier(actor)
        async with self.uow(actor) as repo:
            return await repo.snapshot(snapshot_id)

    @staticmethod
    def validate_proposal(proposal):
        if digest(proposal.model_dump(mode="json", exclude={"candidate_hash"})) != proposal.candidate_hash:
            raise ChangeError("proposal_integrity_error")

    async def propose_change(self, trip_id, output: SafeChangeOutput, *, actor):
        self.identifier(actor)
        async with self.uow(actor) as repo:
            working = await repo.working(trip_id)
            candidate = construct_candidate(working, output)
            data = dict(proposal_id=str(uuid4()), trip_id=trip_id, owner_id=actor,
                requirements_revision=working.requirements.requirements_revision,
                output=output.model_dump(mode="json"), candidate=candidate.payload.model_dump(mode="json"),
                level=candidate.level, touched_item_ids=list(candidate.touched))
            proposal = ChangeProposal.model_validate(data | dict(candidate_hash=digest(data)))
            await repo.save_proposal(proposal)
            return proposal

    async def authorize_change(self, proposal_id, *, seen_candidate_hash, item_ids,
                                explicit_authorization, actor):
        self.identifier(actor)
        if explicit_authorization is not True:
            raise ChangeError("explicit_authorization_required")
        async with self.uow(actor) as repo:
            proposal = await repo.proposal(proposal_id)
            self.validate_proposal(proposal)
            working = await repo.working(proposal.trip_id)
            if working.revision != proposal.output.base_working_revision:
                raise ChangeError("stale_proposal")
            if (proposal.level != "L3" or seen_candidate_hash != proposal.candidate_hash
                    or tuple(sorted(item_ids)) != proposal.touched_item_ids):
                raise ChangeError("authorization_scope_mismatch")
            grant = SpecificAuthorization(authorization_id=str(uuid4()), trip_id=proposal.trip_id,
                proposal_id=proposal_id, candidate_hash=proposal.candidate_hash,
                base_working_revision=working.revision, item_ids=proposal.touched_item_ids,
                granted_by=actor, granted_at=self.now())
            await repo.save_authorization(grant)
            return grant

    async def apply_working_change(self, proposal_id, *, operation_id, actor, authorization_id=None):
        self.identifier(actor)
        self.identifier(operation_id)
        async with self.uow(actor) as repo:
            await repo.lock_operation(operation_id)
            proposal = await repo.proposal(proposal_id)
            self.validate_proposal(proposal)
            request_hash = digest(dict(kind="apply_working_change", trip_id=proposal.trip_id, actor=actor,
                proposal_id=proposal_id, candidate_hash=proposal.candidate_hash, authorization_id=authorization_id))
            existing = await repo.receipt(operation_id, request_hash)
            if existing is not None:
                return existing
            working = await repo.working(proposal.trip_id)
            if proposal.requirements_revision != working.requirements.requirements_revision:
                raise ChangeError("stale_requirements")
            candidate = construct_candidate(working, proposal.output)
            if (candidate.payload != proposal.candidate or candidate.level != proposal.level
                    or candidate.touched != proposal.touched_item_ids):
                raise ChangeError("candidate_mismatch")
            if candidate.level == "L3":
                if authorization_id is None:
                    raise ChangeError("specific_authorization_required")
                grant, consumed = await repo.authorization(authorization_id)
                if (grant.trip_id != working.trip_id or grant.proposal_id != proposal_id
                        or grant.candidate_hash != proposal.candidate_hash or grant.granted_by != actor
                        or grant.base_working_revision != working.revision or grant.item_ids != candidate.touched
                        or consumed is not None):
                    raise ChangeError("authorization_scope_mismatch")
                await repo.consume(authorization_id, operation_id)
            elif authorization_id is not None:
                raise ChangeError("unexpected_authorization")
            budget = check_constraints(candidate.payload, working.requirements, now=self.now())
            updated = WorkingDraft(trip_id=working.trip_id, owner_id=actor, revision=working.revision+1,
                requirements=working.requirements, payload=candidate.payload,
                item_confirmations=candidate.remaining_confirmations,
                latest_confirmed_version_id=working.latest_confirmed_version_id)
            await repo.advance(working, updated)
            result = dict(operation_id=operation_id, operation_type="apply_working_change", trip_id=working.trip_id,
                working_revision=updated.revision, latest_confirmed_version_id=working.latest_confirmed_version_id,
                level=candidate.level, explanation=proposal.output.explanation, budget=budget_data(budget))
            await repo.save_receipt(operation_id, working.trip_id, "apply_working_change", request_hash, result)
            return result

    async def confirmation_preview(self, trip_id, *, actor):
        self.identifier(actor)
        async with self.uow(actor) as repo:
            working = await repo.working(trip_id)
            budget = check_constraints(working.payload, working.requirements, now=self.now())
            return dict(working_revision=working.revision, budget=budget_data(budget),
                disclosure_hash=disclosure_hash(working.payload, working.requirements, working.revision, budget),
                unknown_costs=[dict(id=c.id, reason=c.unknown_reason) for c in working.payload.costs if c.amount_cents is None])

    async def confirm_plan(self, trip_id, *, operation_id, expected_revision, seen_disclosure_hash,
                           explicit_confirmation, actor):
        self.identifier(actor)
        self.identifier(operation_id)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ChangeError("invalid_revision")
        async with self.uow(actor) as repo:
            await repo.lock_operation(operation_id)
            request_hash = digest(dict(kind="confirm_plan", trip_id=trip_id, actor=actor,
                expected_revision=expected_revision, seen_disclosure_hash=seen_disclosure_hash,
                explicit_confirmation=explicit_confirmation))
            existing = await repo.receipt(operation_id, request_hash)
            if existing is not None:
                return existing
            working = await repo.working(trip_id)
            if working.revision != expected_revision:
                raise ChangeError("stale_revision")
            check_constraints(working.payload, working.requirements, now=self.now())
            assessment = assess_confirmation(working.payload, working.requirements, {}, now=self.now(),
                working_revision=working.revision, seen_revision=expected_revision,
                seen_disclosure_hash=seen_disclosure_hash, explicit_confirmation=explicit_confirmation)
            if not assessment.allowed:
                raise ChangeError(assessment.reason)
            snapshot = ConfirmedSnapshot(snapshot_id=str(uuid4()), trip_id=trip_id,
                source_working_revision=working.revision, requirements=working.requirements,
                payload=working.payload, item_confirmations=working.item_confirmations,
                disclosure_hash=assessment.disclosure_hash, budget=assessment.budget,
                confirmed_by=actor, confirmed_at=self.now())
            snapshot = await repo.save_snapshot(snapshot)
            await repo.point_snapshot(working, snapshot.snapshot_id)
            result = dict(operation_id=operation_id, operation_type="confirm_plan", trip_id=trip_id,
                working_revision=working.revision, latest_confirmed_version_id=snapshot.snapshot_id,
                plan_confirmation="CONFIRMED", budget=budget_data(assessment.budget), readiness="NOT_ASSESSED", booked=False)
            await repo.save_receipt(operation_id, trip_id, "confirm_plan", request_hash, result)
            return result
