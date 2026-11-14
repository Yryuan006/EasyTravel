"""One transaction: command -> operation -> Trip -> snapshot/disclosure/receipts."""
from uuid import uuid4
from ...rules import assess_confirmation
from ..domain.confirmation import ConfirmResult, SnapshotView, preview, RISK_RULES_VERSION
from ..domain.hashing import digest
from ..domain.rules import ChangeError, check_constraints
from .plans import PlanService


class ConfirmationService(PlanService):
    def __init__(self, uow, *, rules_version=RISK_RULES_VERSION, **kwargs):
        super().__init__(uow, **kwargs)
        self.rules_version = rules_version

    async def preview(self, trip_id, *, actor):
        self.identifier(actor)
        async with self.uow(actor) as repo:
            # Read-only transaction supplies a consistent view across all SELECTs.
            await repo.read_only()
            trip = await repo.read_trip(trip_id)
            p, disclosure = preview(trip, now=self.now(), rules_version=self.rules_version)
            existing = await repo.source_snapshot(trip_id, p.working_revision)
            if existing and (existing.disclosure_hash != p.disclosure_hash
                    or await repo.disclosure(existing.snapshot_id) != disclosure):
                # A legacy or differently disclosed fact cannot be silently reconfirmed.
                data = {name: getattr(p, name) for name in type(p).model_fields}
                p = type(p)(**(data | dict(confirmation_allowed=False,
                    blocking_reasons=[*p.blocking_reasons, "snapshot_disclosure_conflict"])))
            return p

    async def confirm(self, trip_id, request, *, key, actor):
        self.identifier(actor)
        path = f"/api/v1/trips/{trip_id}/confirmations"
        request_hash = digest(request.model_dump(mode="json"))
        async with self.uow(actor) as repo:
            await repo.lock_command(digest(dict(actor=actor, method="POST", path=path, key=key)))
            replay = await repo.replay(key, request_hash, path)
            if replay is not None:
                return replay
            operation_id = str(uuid4())
            await repo.lock_operation(operation_id)
            await repo.lock_trip(trip_id)
            trip = await repo.read_trip(trip_id)
            if request.explicit_confirmation is not True:
                raise ChangeError("explicit_confirmation_required")
            if request.expected_intake_revision != trip.intake.intake_revision:
                raise ChangeError("stale_intake")
            if trip.working is None:
                raise ChangeError("draft_not_ready")
            if request.expected_working_revision != trip.working.revision:
                raise ChangeError("stale_revision")
            p, disclosure = preview(trip, now=self.now(), rules_version=self.rules_version)
            if p.blocking_reasons:
                raise ChangeError(p.blocking_reasons[0])
            if request.seen_risk_hash != p.risk_hash:
                raise ChangeError("risk_not_acknowledged")
            w = trip.working
            check_constraints(w.payload, w.requirements, now=self.now())
            assessment = assess_confirmation(w.payload, w.requirements, {}, now=self.now(),
                working_revision=w.revision, seen_revision=request.expected_working_revision,
                seen_disclosure_hash=request.seen_disclosure_hash,
                explicit_confirmation=request.explicit_confirmation)
            if not assessment.allowed:
                raise ChangeError(assessment.reason)
            existing = await repo.source_snapshot(trip_id, w.revision)
            if existing:
                saved = await repo.disclosure(existing.snapshot_id)
                # Missing legacy disclosure is never backfilled as an acknowledgement.
                if (saved != disclosure or existing.disclosure_hash != p.disclosure_hash):
                    raise ChangeError("snapshot_disclosure_conflict")
                snapshot = existing
            else:
                snapshot = await repo.save_snapshot(self.confirmed_snapshot(w, assessment, actor=actor))
                await repo.save_disclosure(snapshot, w.context, disclosure)
            await repo.point_snapshot(w, snapshot.snapshot_id)
            result = ConfirmResult(operation_id=operation_id, snapshot=SnapshotView(
                snapshot=snapshot, context=w.context, risk_disclosure=disclosure,
                readiness="NOT_ASSESSED", booked=False))
            await repo.save_receipt(operation_id, trip_id, "confirm_plan", request_hash, result.model_dump(mode="json"))
            await repo.save_command(key, request_hash, result, str(uuid4()), operation_id, "POST", path, 201)
            return result
