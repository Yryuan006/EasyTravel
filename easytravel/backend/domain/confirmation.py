"""Confirmation contracts and deterministic, version-bound risk disclosure."""
from typing import Annotated, Literal
from pydantic import Field, StrictBool

from ...contracts import ID, Revision
from ...rules import BudgetResult, evaluate_budget, disclosure_hash
from .intake import InputContract, Risk, DraftContext, Text
from .models import ConfirmedSnapshot
from .hashing import digest
from .rules import ChangeError, check_constraints

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
RISK_RULES_VERSION = "confirmation-v1"


class ConfirmRequest(InputContract):
    expected_intake_revision: Revision
    expected_working_revision: Revision
    seen_disclosure_hash: Hash
    seen_risk_hash: Hash
    explicit_confirmation: StrictBool


class UnknownCost(InputContract):
    id: ID
    reason: Text


class ConfirmationPreview(InputContract):
    trip_id: ID
    intake_revision: Revision
    working_revision: Revision
    budget: BudgetResult
    pending_requirements_budget: BudgetResult | None
    disclosure_hash: Hash
    risk_hash: Hash
    risks: list[Risk]
    unknown_costs: list[UnknownCost]
    confirmation_allowed: bool
    blocking_reasons: list[Annotated[str, Field(min_length=1, max_length=80)]]
    readiness: Literal["NOT_ASSESSED"]
    booked: Literal[False]


class RiskDisclosure(InputContract):
    risk_hash: Hash
    risks: list[Risk]
    input_hash: Hash
    risk_rules_version: Annotated[str, Field(min_length=1, max_length=80)]


class SnapshotView(InputContract):
    snapshot: ConfirmedSnapshot
    context: DraftContext | None
    risk_disclosure: RiskDisclosure | None
    readiness: Literal["NOT_ASSESSED"]
    booked: Literal[False]


class ConfirmResult(InputContract):
    operation_id: ID
    snapshot: SnapshotView

    @property
    def trip_id(self):
        return self.snapshot.snapshot.trip_id


def preview(trip, *, now, rules_version=RISK_RULES_VERSION):
    w = trip.working
    if w is None:
        raise ChangeError("draft_not_ready")
    budget = evaluate_budget(w.payload, w.requirements, {}, now=now)
    reasons = []
    if trip.synchronization != "CURRENT":
        reasons.append("stale_intake")
    if w.context is None:
        reasons.append("draft_not_ready")
    pending = None
    if trip.synchronization == "STALE":
        i = trip.intake.input
        if (i.hard_limit_cents.state == "provided" and i.dates.state == "provided"
                and i.dates.value.mode == "fixed"
                and i.dates.value.start_date == w.requirements.start_date
                and i.dates.value.end_date == w.requirements.end_date):
            req = w.requirements.model_dump() | dict(hard_limit_cents=i.hard_limit_cents.value)
            pending = evaluate_budget(w.payload, type(w.requirements).model_validate(req), {}, now=now)
        else:
            reasons.append("pending_requirements_budget_unavailable")
    if budget.constraint == "OVER_LIMIT" or (pending and pending.constraint == "OVER_LIMIT"):
        reasons.append("known_costs_exceed_hard_limit")
    # Structural constraints remain mandatory even though a blocked GET retains its budget.
    try:
        check_constraints(w.payload, w.requirements, now=now)
    except ChangeError as error:
        if str(error) != "known_costs_exceed_hard_limit":
            reasons.append("draft_not_ready")
    risks = [Risk(code=code, severity="warning", message=message, item_ids=[]) for code, message in (
        ("not_booked", "尚未预订、付款或出票；整体确认不授予酒店具体授权，也不代表出行就绪。"),
        ("route_unverified", "路线、交通衔接和营业时间尚未核验。"),
    )]
    if any(c.is_mock for c in w.payload.costs):
        risks.append(Risk(code="mock_data", severity="warning", message="本行程使用 Mock 模拟资料，价格不能视为已核验。", item_ids=[]))
    if budget.verification != "VERIFIED":
        risks.append(Risk(code="prices_unverified", severity="warning", message="费用包含估算或未知价格，已知小计不代表已核验总价。", item_ids=[]))
    unknown = [UnknownCost(id=c.id, reason=c.unknown_reason) for c in w.payload.costs if c.amount_cents is None]
    if unknown:
        risks.append(Risk(code="unknown_costs", severity="warning", message="存在未知费用，未知金额没有计为零，总额暂无法确定。", item_ids=[]))
    if w.context:
        # Suggestions are additive. They cannot remove or overwrite required system risks.
        risks.extend(w.context.notes.risks)
    unique = {digest(r.model_dump(mode="json")): r for r in risks}
    risks = sorted(unique.values(), key=lambda r: (r.code, r.severity, r.message, tuple(r.item_ids)))
    if any(r.severity == "blocker" for r in risks):
        reasons.append("blocking_risks")
    input_hash = digest(w.context.input.model_dump(mode="json") if w.context else None)
    risk_hash = digest(dict(trip_id=trip.trip_id, working_revision=w.revision,
        intake_revision=trip.intake.intake_revision, latest_input=trip.intake.input.model_dump(mode="json"),
        context=w.context.model_dump(mode="json") if w.context else None, input_hash=input_hash,
        risk_rules_version=rules_version, risks=[r.model_dump(mode="json") for r in risks]))
    result = ConfirmationPreview(trip_id=trip.trip_id, intake_revision=trip.intake.intake_revision,
        working_revision=w.revision, budget=budget, pending_requirements_budget=pending,
        disclosure_hash=disclosure_hash(w.payload, w.requirements, w.revision, budget),
        risk_hash=risk_hash, risks=risks, unknown_costs=unknown,
        confirmation_allowed=not reasons, blocking_reasons=list(dict.fromkeys(reasons)),
        readiness="NOT_ASSESSED", booked=False)
    return result, RiskDisclosure(risk_hash=risk_hash, risks=risks, input_hash=input_hash,
                                  risk_rules_version=rules_version)
